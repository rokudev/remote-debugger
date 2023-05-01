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
# File: FakeDebuggerClients.py
# Requires python v3.5.3 or later
#
# This file defines minimal stub classes, used for debugging. Primarily,
# these classes are used to debug this script's external interfaces,
# without requiring a target device.
#
# NAMING CONVENTIONS:
#
# TypeNames are CamelCase
# CONSTANT_VALUES are CAPITAL_SNAKE_CASE
# all_other_identifiers are snake_case
# _protected members begin with a single underscore '_' (subclasses can access)
# __private members begin with double underscore: '__'
#
# python more or less enforces the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import sys, threading

from .ProtocolVersion import ProtocolVersion
from .DebuggerClient import AbstractDebuggerClient
from .DebuggerRequest import CmdCode
from .DebuggerResponse import ErrCode, UpdateType
from .Verbosity import Verbosity

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

_DEFAULT_PROTOCOL_VERSION = ProtocolVersion(3,2,0)

# Fake debugger client that does not make a connection to a device
# and can be used to test features of this remote debugger. This
# client is far from comprehensive and more features are added on
# an ad hoc basis to test.
#
# Currently, this emulates only one protocol version, but could be
# modified to support multiple versions. That would be helpful to
# test for backward compatibility of the remote debugger.
#
# Developers should fee free to modify to this class, but please try
# to maintain existing functionality.
class FakeDebuggerClient(AbstractDebuggerClient):

    _THREADS_MAX = 500
    _STACK_FRAMES_MAX = 500
    _REQUEST_SIZE_MAX = int(1e+6)   # bytes
    _STRING_SIZE_MAX = 10000        # size in bytes (not chars)

    _BYTE_SIZE = 1
    _UINT_SIZE = 4

    def __init__(self, update_handler, protocol_version = _DEFAULT_PROTOCOL_VERSION):
        super().__init__(is_fake=True)
        self.protocol_version = protocol_version
        self.__sending_pendreq = None   # _FakePendingRequest currently being sent
        self.__next_request_id = 1      # start with 1 b/c 0 is confused with None
        self.__request_id_lock = threading.Lock()
        self.__pending_requests = []
        self.__pending_lock = threading.Lock()
        self.__pending_cond_var = threading.Condition(lock=self.__pending_lock)
        self.__pending_handler = _FakePendingRequestHandler(self.protocol_version,
            self.__pending_requests, self.__pending_lock, self.__pending_cond_var,
            update_handler)

    # If suppress==True, connection errors are not reported to the user,
    # may be changed at any time.
    # This is useful during shutdown and for tests that test failure modes
    def set_suppress_connection_errors(self, suppress) -> None:
        pass

    def is_connected(self) -> bool:
        return True

    def get_protocol_version(self) -> ProtocolVersion:
        return self.protocol_version

    # @param feature: enum ProtocolFeature
    def has_feature(self, feature) -> bool:
        assert feature
        return self.protocol_version.has_feature(feature)

    def __add_pending_request(self, request):
        with self.__pending_lock:
            self.__pending_requests.append(request)
            self.__pending_cond_var.notify_all()

    def get_pending_request_count(self) -> int:
        with self.__pending_lock:
            return len(self.__pending_requests)

    def has_pending_request(self) -> bool:
        with self.__pending_lock:
            return len(self.__pending_requests)

    def shutdown(self) -> None:
        pass

    def __get_next_request_id(self):
        with self.__request_id_lock:
            id = self.__next_request_id
            self.__next_request_id += 1
        return id

    # @param request instance of DebuggerRequest or subclass
    def send(self, request):
        assert not request.request_id
        assert not self.__sending_pendreq
        request.request_id = self.__get_next_request_id()
        pendreq = _FakePendingRequest(request)
        self.__sending_pendreq = pendreq
        rc = 0
        try:
            rc = request._send(self)
        finally:
            self.__sending_pendreq = None
        self.__add_pending_request(pendreq)
        return rc

    def send_byte(self, val):
        size = self._BYTE_SIZE
        self.__sending_pendreq.write_count += size
        return size

    def send_str(self, val):
        pending = self.__sending_pendreq
        size = len(val.encode('utf-8')) + 1
        pending.max_str_size_written = max(pending.max_str_size_written, size)
        pending.write_count += size
        return size

    def send_uint(self, val):
        size = self._UINT_SIZE
        self.__sending_pendreq.write_count += size
        return size


########################################################################
##### FAKE REQUEST HANDLER
########################################################################


class _FakePendingRequest(object):

    def __init__(self, request):
        self.request = request
        self.max_str_size_written = 0
        self.write_count = 0              # count of bytes written

# Creates a thread to read pending requests and send responses
class _FakePendingRequestHandler(object):

    def __init__(self, protocol_version, pending_list, pending_lock,
            pending_cond_var, update_handler):
        self._debug_level = 0
        self.__protocol_version = protocol_version
        self.__shutdown_now = False
        self.__update_handler = update_handler
        self.__pending_list = pending_list
        self.__pending_lock = pending_lock
        self.__pending_cond_var = pending_cond_var
        self.__thread = threading.Thread(
                    name='FakePendingHandler', target=self, daemon=True)
        self.__thread.start()

    def __call__(self):
        if self.__check_debug(2):
            print('debug: fake: request handler started')
        done = False

        # target always sends all threads stopped
        self.__update_handler(FakeDebuggerUpdate_AllThreadsStopped())

        while not done:
            pending = None
            with self.__pending_cond_var:
                if not self.__shutdown_now:
                    self.__pending_cond_var.wait()
                done = self.__shutdown_now
                if not done:
                    if len(self.__pending_list):
                        pending = self.__pending_list.pop(0)
            if pending:
                self.__handle_pending(pending)

    def shutdown(self):
        with self.__pending_cond_var:
            self.__shutdown_now = True
            self.__pending_cond_var.notify_all()

    def __handle_pending(self, pending):
        assert pending
        assert pending.request
        request = pending.request
        request_size = request.get_packet_size(self.__protocol_version)
        sent_size = pending.write_count
        response = None

        if request_size > FakeDebuggerClient._REQUEST_SIZE_MAX:
            response = FakeDebuggerResponse_Error(ErrCode.INVALID_PROTOCOL, request)
        elif pending.max_str_size_written >= FakeDebuggerClient._STRING_SIZE_MAX:
            response = FakeDebuggerResponse_Error(ErrCode.INVALID_PROTOCOL, request)
        elif request_size != sent_size:
            response = FakeDebuggerResponse_Error(ErrCode.INVALID_PROTOCOL, request)
        elif request.cmd_code == CmdCode.STOP:
            pass
        elif request.cmd_code == CmdCode.CONTINUE:
            pass
        elif request.cmd_code == CmdCode.THREADS:
            pass
        elif request.cmd_code == CmdCode.STACKTRACE:
            response = self.__handle_cmd_stacktrace(request)
        elif request.cmd_code == CmdCode.VARIABLES:
            pass
        elif request.cmd_code == CmdCode.STEP:
            pass
        elif request.cmd_code == CmdCode.ADD_BREAKPOINTS:
            pass
        elif request.cmd_code == CmdCode.LIST_BREAKPOINTS:
            pass
        elif request.cmd_code == CmdCode.REMOVE_BREAKPOINTS:
            pass
        elif request.cmd_code == CmdCode.EXECUTE:
            response = self.__handle_cmd_execute(request)
        elif request.cmd_code == CmdCode.ADD_CONDITIONAL_BREAKPOINTS:
            pass
        elif request.cmd_code == CmdCode.EXIT_CHANNEL:
            pass
        else:
            response = FakeDebuggerResponse_Error(ErrCode.INVALID_PROTOCOL, request)

        if response:
            if self.__check_debug(3):
                print('debug: fake: send response: {}'.format(response))
            self.__update_handler(response)

    # Ideally this will handle running/stopped states, but this is a FAKE client...
    # @return a DebuggerResponse instance
    def __handle_cmd_execute(self, request):
        response = None
        src_len = len(request.source_code) if request.source_code else 0
        if not self.__thread_index_ok(request.thread_index) or \
                not self.__stack_frame_index_ok(request.frame_index) or \
                not src_len:
            response = FakeDebuggerResponse_Error(ErrCode.INVALID_ARGS, request)
        else:
            response = FakeDebuggerResponse(ErrCode.OK, request)
        return response

    def __check_debug(self, lvl):
        return lvl <= max(global_config.debug_level, self._debug_level)

    # Ideally this will handle running/stopped states, but this is a FAKE client...
    # @return a DebuggerResponse instance
    def __handle_cmd_stacktrace(self, request):
        response = None
        if not self.__thread_index_ok(request.thread_index):
            response = FakeDebuggerResponse_Error(ErrCode.INVALID_ARGS, request)
        else:
            pass   # create _FakeStacktraceResponse?
        return response

    def __thread_index_ok(self, thread_index):
        return thread_index != None and thread_index >= 0 and \
            thread_index <= FakeDebuggerClient._THREADS_MAX

    def __stack_frame_index_ok(self, frame_index):
        return frame_index != None and frame_index >= 0 and \
            frame_index <= FakeDebuggerClient._STACK_FRAMES_MAX

    def __check_debug(self, lvl):
        return lvl <= max(global_config.debug_level, self._debug_level)


########################################################################
##### FAKE RESPONSES
########################################################################

#-------------- 
# Updates (no associated request)
#---------------

class FakeDebuggerUpdate(object):

    # @param update_type enum DebuggerResponse.UpdateType
    def __init__(self, err_code, update_type):
        super().__init__()
        self._debug_level = 0
        self.err_code = err_code
        self.is_error = err_code != ErrCode.OK
        self.update_type = update_type
        self.request = None
        self.request_id = None

    def __str__(self):
        s = '{}[{}]'.format(type(self).__name__, self.str_params())
        return s

    # parameters inside a larger string, such as the return from __str__()
    def str_params(self):
        s = 'errcode={},update_type={}'.format(self.err_code,
                self.update_type)
        return s


class FakeDebuggerUpdate_AllThreadsStopped(FakeDebuggerUpdate):
    def __init__(self):
        super().__init__(ErrCode.OK, UpdateType.ALL_THREADS_STOPPED)
        self.primary_thread_index = 0


#-------------- --------------------------------------------------------
# Responses (update that is a response to a specific request)
#---------------

# A response is an update that is in response to a specific request
class FakeDebuggerResponse(FakeDebuggerUpdate):
    def __init__(self, err_code, request):
        super().__init__(err_code, UpdateType.COMMAND_RESPONSE)

    def __str__(self):
        s = '{}[{}]'.format(type(self).__name__, self.str_params())
        return s

    # parameters inside a larger string, such as the return from __str__()
    def str_params(self):
        s = super().str_params()
        s = s + ',reqid={}'.format(self.request_id)
        if self.request:
            s = s + ',request={}'.format(self.request)
        return s


class FakeDebuggerResponse_Error(FakeDebuggerResponse):
    def __init__(self, err_code, request):
        super().__init__(err_code, request)
        self.update_type = UpdateType.COMMAND_RESPONSE

        # Additional fields
        self.err_flags = 0                      # 32-bit flags
        self.invalid_value_path_index = None
        self.missing_key_path_index = None
