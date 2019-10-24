########################################################################
# Copyright 2019 Roku, Inc.
#
#Licensed under the Apache License, Version 2.0 (the "License");
#you may not use this file except in compliance with the License.
#You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#Unless required by applicable law or agreed to in writing, software
#distributed under the License is distributed on an "AS IS" BASIS,
#WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#See the License for the specific language governing permissions and
#limitations under the License.
########################################################################
# File: DebuggerListener.py
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

import sys, threading, traceback

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
    # starts accepting messages and sending them to updateHandler.
    # @param updateHandler.updateReceived(update)
    def __init__(self, debugger_client, update_handler):
        global gMain
        gMain = sys.modules['__main__'].gMain
        self.__debug = max(gMain.gDebugLevel, 0)
        self._debugger_client = debugger_client
        self._update_handler = update_handler
        self.__pending_requests = []    # list of _PendingRequest
        self.__thread = _ListenerThread(listener=self)
        self.__pending_lock = threading.Lock()

        self.__thread.start()

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
            entry = _PendingRequest(request, allow_update, allowed_update_types)
            self.__pending_requests.append(entry)
            if self.__debug >= 3:
                print('debug: add pending request, count={},req={}'.format(
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
        if self.__debug >= 3:
            print('debug: dlis: find pending by ID({})->{}'.format(
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

        if self.__debug >= 3:
            print('debug: dlis: find pending by update_type({})->{}'.format(
                update_type.name, request))
        return request


class _ListenerThread(threading.Thread):

    def __init__(self, listener):
        super(_ListenerThread, self).__init__(daemon=True)
        self.name = 'DebuggerListener'      # Used by superclass
        self.__debug = max(gMain.gDebugLevel, 0)
        self.__listener = listener

    def run(self):
        if self.__debug >= 1:
            print('debug: listener: thread running')
        listener = self.__listener
        __debugger_client = listener._debugger_client
        __update_handler = listener._update_handler

        try:
            done = False
            while not done:
                update = DebuggerUpdate.read_update(__debugger_client, listener)
                if (self.__debug >= 5):
                    print('debug: recvd msg: {}'.format(update))
                done = not __update_handler.update_received(update)
        except:
            sys.stdout.flush()
            traceback.print_exc(file=sys.stderr)
            print('ERROR: Failed with exception', file=sys.stderr)
            do_exit(1, 'Uncaught exception in listener thread')

        if self.__debug >= 1:
            print('debug: listener: thread exiting')

import sys
def do_exit(exitCode, msg=None):
    sys.modules['__main__'].do_exit(exitCode, msg)
