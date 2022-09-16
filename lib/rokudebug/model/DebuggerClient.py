########################################################################
# Copyright 2019-2022 Roku, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
########################################################################
# File: DebuggerClient.py
# Requires python v3.5.3 or later
#
# NAMING CONVENTIONS:
#
# Type identifiers are CamelCase
# All other identifiers are snake_case
# Protected members begin with a single underscore '_'
# Private members begin with double underscore: '__'
#
# python more or less enfores the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import socket, sys, threading, time, traceback

from .DebuggerRequest import CmdCode
from .DebuggerResponse import ErrCode
from .DebuggerResponse import UpdateType
from .DebuggerControlListener import DebuggerControlListener
from .DebuggerIOListener import DebuggerIOListener
from .DebugUtils import do_exit, do_print, revision_timestamp_to_str
from .ProtocolVersion import ProtocolFeature
from .ProtocolVersion import ProtocolVersion
from .StackReferenceIDManager import StackReferenceIDManager
from .StreamUtils import StreamUtils
from .Verbosity import Verbosity

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level, do_exit()

DEBUGGER_PORT = 8081
DEBUGGER_CONNECTION_TIMEOUT_SECONDS = 60
DEBUGGER_MAGIC = 0x0067756265647362 # 64-bit = [b'bsdebug\0' little-endian]
DEBUGGER_MAGIC_BYTES_LE = b'bsdebug\0'

class DebuggerClient(object):

    # Attribute protocol_version is None until successful call is made
    # to connect_control(), which performs the handshake to the debuggee.
    # Updates from the debuggee are sent to function update_handler(),
    # except CONNECT_IO_PORT update(s) which are handled by this object.
    # @param debuggee_out the file where output from debuggee will be sent
    def __init__(self, target_ip_addr, update_handler, debuggee_out):
        assert target_ip_addr
        assert update_handler
        assert debuggee_out
        self._debug_level = 0
        self.is_fake = False            # This is not a debugging stub

        self.protocol_version = None    # ProtocolVersion, set during handshake

        # Private
        self.__stack_ref_id_mgr = StackReferenceIDManager()
        self.__features = set()         # populated during handshake
        self.__io_listener = None
        self.__control_socket = None
        self.__next_request_id = 1 # start with 1 b/c 0 is confused with None
        self.__target_ip_addr = target_ip_addr
        self.__request_id_lock = threading.Lock()
        self.__caller_update_handler = update_handler
        self.__debuggee_out = debuggee_out
        self.__control_listener = None  # populated during handshake

        # Cached data
        self.__cached_threads_lock = threading.RLock()
        self.__cached_threads = None           # [thr_idx] -> Latest THREADS response
        self.__cached_thread_stacktraces = None     # [thr_idx] -> Latest STACKTRACE reponse
        self.__cached_thread_stack_variables = None # see __make_cached_variables_key()

    # only valid after connect_control() successfully completes
    # @param feature: enum ProtocolFeature
    def has_feature(self, feature):
        assert self.protocol_version
        return feature in self.__features

    def get_protocol_version(self):
        return self.protocol_version

    # @return None if connect_control() has not been called
    def get_protocol_version_str(self):
        if self.protocol_version == None:
            return None
        s = ''
        for v in self.protocol_version:
            if len(s):
                s = s + '.'
            s += str(v)
        return s

    def get_next_request_id(self):
        with self.__request_id_lock:
            id = self.__next_request_id
            self.__next_request_id += 1
        return id

    # @return None or int stack reference ID
    def get_stack_ref_id(self, thread_index, frame_index,
            variable_path=None, allow_create=True):
        return self.__stack_ref_id_mgr.get_stack_ref_id(thread_index,
                frame_index, variable_path, allow_create=allow_create)

    # @raise KeyError if stack_ref_id is unknown and !allow_create
    def get_child_stack_ref_id(self, stack_ref_id, child_name,
                allow_create=True):
        return self.__stack_ref_id_mgr.get_child_stack_ref_id(
                        stack_ref_id, child_name, allow_create=allow_create)

    # Get the thread_index,frame_index,var_path defined by stack_ref_id
    # @raise KeyError if stack_ref_id is unknown
    def decode_stack_ref_id(self, stack_ref_id):
        return self.__stack_ref_id_mgr.get_indexes(stack_ref_id)

    # Get the most recent response to a THREADS request, which may
    # be an error. Returns None if an operation has invalidated the
    # cached response.
    def get_threads(self):
        with self.__cached_threads_lock:
            return self.__cached_threads

    # Get the most recent response to a STACKTRACE request, which
    # may be an error. Returns None if an operation has invalidated the
    # cached response.
    def get_thread_stacktrace(self, thread_index):
        with self.__cached_threads_lock:
            frames = None
            if self.__cached_thread_stacktraces and \
                        len(self.__cached_thread_stacktraces) > thread_index:
                frames = self.__cached_thread_stacktraces[thread_index]
            return frames

    # Get the latest response from the debuggee for thread,frame,var_path,
    # which may be an error. Returns NOne if an operation has invalidated
    # the cache.
    # @param variable_path None or iterable of strings
    def get_thread_stack_variables(self, thread_index, frame_index,
                    variable_path, get_child_keys):
        assert thread_index != None
        assert frame_index != None
        assert get_child_keys != None
        # variable_path can be None

        vars_response = None
        with self.__cached_threads_lock:
            if self.__cached_thread_stack_variables:
                vars_key = self.__make_cached_variables_key(thread_index,
                    frame_index, variable_path, get_child_keys,
                    allow_create=False)
                if vars_key:
                    vars_response = self.__cached_thread_stack_variables.get(
                        vars_key, None)
        return vars_response

    # Connect to the debugger's control port
    # MODIFIES: Sets self.protocol_version (array of 3 ints, each in the range of 0.999)
    # MODIFIES: Sets self.has_stop_line_number_bug
    # @see get_protocol_version_int()
    def connect(self):
        self.__connect_control()

    def __connect_control(self):
        print('info: connecting to debug target {}:{} ...'.format(
                self.__target_ip_addr, DEBUGGER_PORT))
        # If we attempt to connect, prior to the target listening, there
        # is a lag of several seconds between the target listening and the
        # connection being established. To speed things, up, we attempt
        # a connection repeatedly with a short timeout.
        timeout = DEBUGGER_CONNECTION_TIMEOUT_SECONDS
        connected = False
        try_count = 0
        now = global_config.get_monotonic_time()
        retryEndTime = now + DEBUGGER_CONNECTION_TIMEOUT_SECONDS
        sleepSeconds = 0.1
        while ((not connected) and (now < retryEndTime)):
            try_count += 1
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            if self.__check_debug(2):
                print('debug: attempting connection {} (timeout={}s,remaining={}s)...'.format(
                    try_count, timeout, (retryEndTime-now)))
            try:
                sock.connect((self.__target_ip_addr, DEBUGGER_PORT))
                connected = True
            except ConnectionRefusedError:
                # port not open yet?
                if self.__check_debug(5):
                    print('debug: socket connection refused')
            except TimeoutError:
                if self.__check_debug(5):
                    print('debug: socket connect timeout')
                pass
            now = global_config.get_monotonic_time()
            timeout = min((1.1 * timeout), (retryEndTime - now))
            time.sleep(sleepSeconds)
            sleepSeconds = min(1.0, 1.1 * sleepSeconds)

        if not connected:
            global_config.do_exit(1, 'Could not connect to {}:{}'.format(
                self.__target_ip_addr, DEBUGGER_PORT))

        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(1e+6) # normal state is blocked waiting for event
        self.__control_socket = sock
        self.__do_handshake()
        self.__control_listener = DebuggerControlListener(self,
            self.__general_update_handler, self.__io_update_handler)

        print('info: connected to debug target, protocol version={} software_revision={}'.format(
            self.protocol_version.to_user_str(), 
            revision_timestamp_to_str(self.protocol_version.get_platform_revision())))
        if self.__check_debug(2):
            strs = []
            for f in self.__features:
                strs.append(f.to_user_string())
            strs.sort()
            print('debug: target features: {}'.format(','.join(strs)))

    # END: connect_control()

    # Connect to the debugger's I/O port. This happens when a message
    # comes over an existing connection to the debugger's control port,
    # which tells this client to connect to another port to retrieve
    # output from the script being debugged.
    def __connect_io_port(self, port, out):
        if self.__check_debug(2):
            print('debug:dclient: connect_io_port(port={})'.format(port))
        self.__io_listener = DebuggerIOListener(
            self.__target_ip_addr, port, out)

    def __io_update_handler(self, update):
        assert update.update_type == UpdateType.CONNECT_IO_PORT
        self.__connect_io_port(update.io_port, self.__debuggee_out)
        return True

    def __general_update_handler(self, update):

        #
        # FIRST: UPDATE CACHES
        #
        if update.update_type == UpdateType.THREAD_ATTACHED or \
                update.update_type == UpdateType.ALL_THREADS_STOPPED:
            self.__invalidate_thread_cache()
        elif update.update_type == UpdateType.COMMAND_RESPONSE:
            assert update.request
            request = update.request
            cmd = request.cmd_code
            if cmd == CmdCode.THREADS:
                self.__cache_threads(update)
            elif cmd == CmdCode.STACKTRACE:
                self.__cache_thread_stacktrace(request.thread_index,
                    update)
            elif cmd == CmdCode.VARIABLES:
                self.__cache_thread_stack_variables(request.thread_index,
                    request.frame_index, request.variable_path,
                    update)

        #
        # NEXT: HANDLE THE RESPONSE
        #
        return self.__caller_update_handler(update)

    # Send the request and keep track of it as "pending", until a
    # response is received from the debuggee.
    # @param request: DebuggerRequest
    # @return number of bytes written
    def send(self, request, allow_update=False, allowed_update_types=None):
        assert not request.request_id
        request.request_id = self.get_next_request_id()
        self.__control_listener.add_pending_request(request)
        if allow_update:
            self.__control_listener.add_pending_request(request, allow_update,
                            allowed_update_types)

        # Invalidate cached thread info on some commands
        if request.cmd_code == CmdCode.CONTINUE or \
                request.cmd_code == CmdCode.STEP or \
                request.cmd_code == CmdCode.EXIT_CHANNEL:
            self.__invalidate_thread_cache()

        request._send(self)

    def has_pending_request(self):
        return self.__control_listener.has_pending_request()

    def get_pending_request_count(self):
        return self.__control_listener.get_pending_request_count()

    ####################################################################
    # RECEIVE DATA
    ####################################################################

    def recv_double(self, counter):
        return StreamUtils.read_ieee754binary64_le(self.__control_socket, counter)

    def recv_float(self, counter):
        return StreamUtils.read_ieee754binary32_le(self.__control_socket, counter)

    def recv_bool(self, counter):
        return self.recv_uint8(counter) != 0

    def recv_uint8(self, counter):
        return StreamUtils.read_uint8(self.__control_socket, counter)

    def recv_int32(self, counter):
        return StreamUtils.read_int32_le(self.__control_socket, counter)

    def recv_uint32(self, counter):
        return StreamUtils.read_uint32_le(self.__control_socket, counter)

    def recv_int64(self, counter):
        return StreamUtils.read_int64_le(self.__control_socket, counter)

    def recv_str(self, counter):
        s = StreamUtils.read_utf8(self.__control_socket, counter)
        if self.__check_debug(10):
            print('debug: dclient.recv_str() s={}'.format(s))
        return s

    ####################################################################
    # SEND DATA
    ####################################################################

    def send_bool(self, bool_val):
        int_val = 1 if bool_val else 0
        return StreamUtils.write_uint8(self.__control_socket, int_val)

    def send_byte(self, byte_val):
        return StreamUtils.write_uint8(self.__control_socket, byte_val)

    def send_uint(self, val):
        return StreamUtils.write_uint32_le(self.__control_socket, val)

    def send_str(self, val):
        return StreamUtils.write_utf8(self.__control_socket, val)

    # Shuts down the connection to the debugging target.
    # This should only be called after the response to the
    # final request is received, because unsent data will
    # be discarded (at least on some platforms).
    def shutdown(self):
        if self.__control_socket:
            if self.__check_debug(2):
                print('debug: closing socket')
            try:
                self.__control_socket.shutdown(socket.SHUT_RDWR)
            except Exception:
                if self.__check_debug(2):
                    print('debug: exception:')
                    traceback.print_exc(file=sys.stdout)
            try:
                self.__control_socket.close()
            except Exception:
                if self.__check_debug(2):
                    traceback.print_exc(file=sys.stdout)
            self.__control_socket = None

    # Initial handshake with debug server
    # REQUIRES: self.__control_socket is a connected socket
    # MODIFIES: sets self.protocol_version
    def __do_handshake(self):
        sock = self.__control_socket
        if self.__check_debug(2):
            print('debug: socket connected, attempting handshake')

        # Exchange magic number
        class Counter:
            def __init__(self):
                self.byte_read_count = 0
        counter = Counter()
        StreamUtils.write_uint64_le(self.__control_socket, DEBUGGER_MAGIC)
        readMagic = StreamUtils.read_uint64_le(sock, counter)
        if readMagic != DEBUGGER_MAGIC:
            global_config.do_exit(1, "Bad magic number from debug target")

        # Get protocol version from target

        major = StreamUtils.read_uint32_le(sock, counter)
        minor = StreamUtils.read_uint32_le(sock, counter)
        patch = StreamUtils.read_uint32_le(sock, counter)
        platform_revision = None
        packet_length = None

        counter.byte_read_count = 0
        if major >= 3:
            if self.__check_debug(2):
                print('debug: dclient: reading packet length and platform revision')
            packet_length = StreamUtils.read_uint32_le(sock, counter);
            platform_revision = StreamUtils.read_int64_le(sock, counter)
        else:
            if self.__check_debug(2):
                print('debug: dclient: NOT reading platform revision')
        if self.__check_debug(3):
            print('debug: dclient: read protocol version={}.{}.{} platform_revision={}'.format(
                major, minor, patch, platform_revision))
        if packet_length != None:
            assert counter.byte_read_count == packet_length

        self.protocol_version = ProtocolVersion(major, minor, patch)
        self.protocol_version.set_platform_revision(platform_revision)
        v = self.protocol_version
        if not v.is_valid():
            global_config.do_exit(1,
                'Invalid protocol version from target: {}'.format(
                    v.to_user_str()))

        # Infer the target's feature set from information in the handshake
        for feature in ProtocolFeature:
            if self.protocol_version.has_feature(feature):
                self.__features.add(feature)

    def __invalidate_thread_cache(self):
        self.__cache_threads(None)

    # Cache the response. If the response is None or has err_code!=ErrCode.OK,
    # the cache entry is erased.
    def __cache_threads(self, response):
        with self.__cached_threads_lock:
            self.__cached_threads = response
            self.__cached_thread_stacktraces = None
            self.__cached_thread_stack_variables = None

    # Cache the response. If the response is None or has err_code!=ErrCode.OK,
    # the cache entry is erased.
    def __cache_thread_stacktrace(self, thread_index, response):
        assert thread_index >= 0
        with self.__cached_threads_lock:
            if not self.__cached_thread_stacktraces:
                self.__cached_thread_stacktraces = list()
            while len(self.__cached_thread_stacktraces) <= thread_index:
                self.__cached_thread_stacktraces.append(None)
            self.__cached_thread_stacktraces[thread_index] = response

            # Invalidate all cached variables. If this is too aggressive
            # and causes unnecessary round trips, the cache entries could
            # be cleared for just this thread.
            self.__cached_thread_stack_variables = None

    # Cache the response. If the response is None or has err_code!=ErrCode.OK,
    # the cache entry is erased.
    def __cache_thread_stack_variables(self, thread_index, frame_index,
                variable_path, response):
        assert response
        assert response.request
        request = response.request

        get_child_keys = request.get_child_keys
        assert request
        with self.__cached_threads_lock:
            if not self.__cached_thread_stack_variables:
                self.__cached_thread_stack_variables = dict()
            vars_key = self.__make_cached_variables_key(thread_index,
                frame_index, variable_path, get_child_keys, allow_create=True)
            self.__cached_thread_stack_variables[vars_key] = response

    # key is str: '<stack_ref_id>-<get_child_keys>', e.g., '1-True'
    # @return key or None if allow_create is False and no stack_ref_id exists
    def __make_cached_variables_key(self, thread_index, frame_index,
        variable_path, get_child_keys, allow_create):
        key = None
        stack_ref_id = self.__stack_ref_id_mgr.get_stack_ref_id(
            thread_index, frame_index, variable_path, allow_create)
        if stack_ref_id:
            key = '{}-{}'.format(stack_ref_id, get_child_keys)
        return key

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level

#END class DebuggerClient
