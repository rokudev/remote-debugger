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
# File: DebuggerControlListener.py
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

from .DebuggerResponse import DebuggerUpdate
from .DebuggerResponse import UpdateType
from .Verbosity import Verbosity

import sys, threading, traceback

# SystemExit only exits the current thread, so call it by its real name
ThreadExit = SystemExit

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

BITS_PER_BYTE = 8
UINT32_NUM_BYTES = 4
UINT64_NUM_BYTES = 8

class _PendingRequest(object):
    def __init__(self, request, allow_update, allowed_update_types):
        if allow_update:
            assert allowed_update_types != None
        else:
            assert allowed_update_types == None
        self.request = request
        self.allow_update =  allow_update
        self.allowed_update_types = allowed_update_types

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        s = '_PendingRequest['
        if self.allow_update:
            if s[len(s)-1] != '[':
                s += ','
            s = s + 'allowupdate'
        if self.allowed_update_types != None:
            if s[len(s)-1] != '[':
                s += ','
            s += 'allowedupdatetypes=['
            for one_type in self.allowed_update_types:
                if s[len(s)-1] != '[':
                    s += ','
                s += one_type.name
            s += ']'
        if s[len(s)-1] != '[':
            s += ','
        s += 'request={}'.format(self.request)
        s += ']'
        return s


# Uses a separate thread to listen to the debugger control
# port for responses and updates.
class DebuggerControlListener(object):

    # Starts a thread to listen to the debuggerClient and immediately
    # starts accepting messages and sending them to
    # general_update_handler(), except for CONNECT_IO messages, which
    # are sent to io_update_handler(). If any update handler returns
    # True, then processing continues. If the handler returns False,
    # the listening thread exits and no further updates will be
    # processed.
    # @param suppress_connection_errors if True do not log connection errors
    def __init__(self, debugger_client, general_update_handler,
                    io_update_handler, suppress_connection_errors=False):
        self._debug_level = 0
        self._debugger_client = debugger_client
        self._general_update_handler = general_update_handler
        self._io_update_handler = io_update_handler

        # private
        self.__pending_requests = []    # list of _PendingRequest
        self.__thread = _ListenerThread(self, suppress_connection_errors)
        self.__pending_lock = threading.Lock()

        self.__thread.start()

    # If suppress==True, connection errors are not reported to the user,
    # may be changed at any time.
    # This is useful during shutdown and for tests that test failure modes
    def set_suppress_connection_errors(self, suppress) -> None:
        self.__thread.set_suppress_connection_errors(suppress)

    def get_suppress_connection_errors(self) -> bool:
        return self.__thread.get_suppress_connection_errors()

    def has_pending_request(self):
        with self.__pending_lock:
            return (len(self.__pending_requests) > 0)

    def get_pending_request_count(self):
        with self.__pending_lock:
            return len(self.__pending_requests)

    # A pending request is any request that is waiting for a response
    # from the debugging target.
    def add_pending_request(self, request, allow_update=False,
                            allowed_update_types=None):
        with self.__pending_lock:
            assert request
            assert request.request_id
            entry = _PendingRequest(request, allow_update, allowed_update_types)
            self.__pending_requests.append(entry)
            if self.__check_debug(3):
                print('debug: ctl_lis: add pending request, count={},req={}'.format(
                    len(self.__pending_requests), entry))

    def get_pending_request(self, request_id, remove=False):
        pending_list = self.__pending_requests
        request = None
        with self.__pending_lock:
            for i in range(len(pending_list)):
                one_pending = pending_list[i]
                if one_pending.request.request_id == request_id:
                    request = one_pending.request
                    if remove:
                        del pending_list[i]
                    break
        if self.__check_debug(3):
            print('debug: ctl_lis: find pending by ID({})->{}'.format(
                                            request_id, request))
        return request

    def get_pending_request_by_update_type(self, update_type, remove=False):
        assert update_type
        assert isinstance(update_type, UpdateType)
        pending_list = self.__pending_requests
        request = None
        with self.__pending_lock:
            for i in range(len(pending_list)):
                one_pending = pending_list[i]
                if one_pending.allowed_update_types:
                    for one_type in one_pending.allowed_update_types:
                        if one_type == update_type:
                            request = one_pending.request
                            if remove:
                                del pending_list[i]
                            break
                        if request:
                            break

        if self.__check_debug(3):
            print('debug: ctl_lis: find pending by update_type({})->{}'.format(
                update_type.name, request))
        return request

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level

#END class DebuggerControlListener


class _ListenerThread(threading.Thread):

    def __init__(self, listener, suppress_connection_errors):
        super(_ListenerThread, self).__init__(daemon=True)
        self.name = 'DebuggerListener'      # Used by superclass
        self._debug_level = 0
        self.__listener = listener

        # members below are protected with __lock
        self.__lock = threading.Lock()
        self.__suppress_connection_errors = suppress_connection_errors

    def set_suppress_connection_errors(self, suppress):
        with self.__lock:
            self.__suppress_connection_errors = suppress

    def get_suppress_connection_errors(self) -> bool:
        with self.__lock:
            return self.__suppress_connection_errors

    def run(self):
        try:
            self.__run_impl()
        except Exception as e:
            if self.__check_debug(2):
                print('debug: ctl_lis: control connection closed: {}'.format(e))
            is_connection_err = isinstance(e, OSError)
            if not is_connection_err or not self.get_suppress_connection_errors():
                # Don't show connection exceptions when the are suppressed
                # Always show other exceptions
                traceback.print_exc(file=sys.stderr)
                global_config.do_exit(1, 'INTERNAL ERR: uncaught exception')

        if not self.get_suppress_connection_errors():
            if self.__check_debug(2):
                print('debug: unexpected termination of control listener')
            global_config.do_exit(1, 'INTERNAL ERROR: '\
                    'unexpected termination of control listener')

    def __run_impl(self) -> None:
        if self.__check_debug(2):
            print('debug: ctl_lis: thread running')
        listener = self.__listener
        dclient = listener._debugger_client
        general_update_handler = listener._general_update_handler
        io_update_handler = listener._io_update_handler
        done = False
        while not done:
            update = DebuggerUpdate.read_update(dclient, listener)
            if not update:
                if self.__check_debug(2):
                    print('debug: ctl_lis: EOF on socket, suppressioerrs={}',
                          self.__suppress_connection_errors)
                if not self.__suppress_connection_errors:
                    if global_config.verbosity >= Verbosity.NORMAL:
                        print('info: unexpected EOF on control socket')
                        global_config.do_exit(1, 'Unexpected EOF on control socket')
                done = True
                break

            if self.__check_debug(5):
                print('debug: ctl_lis: recvd msg: {}'.format(update))
            if update.update_type == UpdateType.CONNECT_IO_PORT:
                done = not io_update_handler(update)
            else:
                done = not general_update_handler(update)
            if done:
                self.set_suppress_connection_errors(True)
                if self.__check_debug(2):
                    print('debug: ctl_lis: update handler says "quit"')

        if self.__check_debug(2):
            print('debug: ctl_lis: thread exiting')

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level
