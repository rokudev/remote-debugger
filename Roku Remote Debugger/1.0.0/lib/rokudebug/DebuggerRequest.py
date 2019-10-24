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
# File: DebuggerRequest.py
# Requires python v3.5.3 or later
#
# NAMING CONVENTIONS:
#
# TypeIdentifiers are CamelCase
# CONSTANTS are CAPITAL_CASE
# all_other_identifiers are snake_case
# _protected members begin with a single underscore '_' (subclasses can access)
# __private members begin with double underscore: '__'
#
# python more or less enfores the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import sys
import enum, sys

UINT8_SIZE = 1
UINT32_SIZE = 4

# Size in bytes of a simple request with no parameters:
#    - packetSize,requestID,cmdCode
NO_PARAMS_REQUEST_SIZE = (3 * UINT32_SIZE)

@enum.unique
class CmdCode(enum.IntEnum):
    # Skip value 0 because it is confused with None
    STOP = 1,
    CONTINUE = 2,
    THREADS = 3,
    STACKTRACE = 4,
    VARIABLES = 5,
    STEP = 6,

    EXIT_CHANNEL = 122,

@enum.unique
class StepType(enum.IntEnum):
    NONE = 0,
    LINE = 1,
    OUT = 2,
    OVER = 3,

@enum.unique
class _VariablesRequestFlags(enum.IntEnum):
    # These values must fit in 8 bits
    GET_CHILD_KEYS = 0x01


# Abstract base class of all debugger requests
class DebuggerRequest(object):
    # All debugger requests have a caller_data attribute. caller_data
    # is an opaque value that is ignored by the debugger client, and the
    # caller can manipulate that data at will
    def __init__(self, cmdCode, caller_data=None):
        global gMain
        gMain = sys.modules['__main__'].gMain
        self._debug = max(gMain.gDebugLevel, 0)
        self.cmd_code = cmdCode
        self.request_id = None
        self.caller_data = caller_data

    def __str__(self):
        s = '{}[{}]'.format(type(self).__name__, self._str_params())
        return s

    # parameters inside the response to __str__()
    def _str_params(self):
        s = 'cmdcode={},reqid={}'.format(repr(self.cmd_code), self.request_id)
        if self.caller_data:
            s += ',cdata={}'.format(self.caller_data)
        return s

    # python makes some whacky decision when choosing repr() vs. str()
    # let's just make 'em the same
    def __repr__(self):
        return self.__str__()

    # Send the fields common to all requests: packetSize,requestID,cmdCode
    # @return number of bytes written
    def _send_base_fields(self, debugger_client, packet_size):
        debugger = debugger_client
        self.request_id = debugger.get_next_request_id()
        if self._debug >= 5:
            print('debug: send base fields {}({}), packet_size={},requestID={}'.\
                format(
                    self.cmd_code.name,
                    self.cmd_code.value,
                    packet_size,
                    self.request_id))
        count = 0
        count += debugger.send_uint(packet_size)
        count += debugger.send_uint(self.request_id)
        count += debugger.send_uint(self.cmd_code)
        self.__verify_num_written(NO_PARAMS_REQUEST_SIZE, count)
        return count

    # raise an exception if the counts don't match
    # @return actual value, if it matches expectations
    def __verify_num_written(self, expected, actual):
        if expected == actual:
            return actual
        raise AssertionError(
            'INTERNAL ERROR: bad size written expected={},actual={}'.format(
                expected, actual))

# Private subclass
class _DebuggerRequest_NoParams(DebuggerRequest):
    def __init__(self, cmd_code, caller_data=None):
        super(_DebuggerRequest_NoParams, self).\
                            __init__(cmd_code, caller_data)

    # @return number of bytes written
    def send(self, debugger_client):
        if self._debug >= 1:
            print('debug: send {}'.format(self))
        return self._send_base_fields(debugger_client, NO_PARAMS_REQUEST_SIZE)

class DebuggerRequest_Continue(_DebuggerRequest_NoParams):
    def __init__(self):
        super(DebuggerRequest_Continue, self).__init__(CmdCode.CONTINUE, False)

class DebuggerRequest_ExitChannel(_DebuggerRequest_NoParams):
    def __init__(self):
        super(DebuggerRequest_ExitChannel, self).\
                    __init__(CmdCode.EXIT_CHANNEL, False)

# Get stack trace of one stopped thread
class DebuggerRequest_Stacktrace(DebuggerRequest):
    def __init__(self, thread_index, caller_data=None):
        super(DebuggerRequest_Stacktrace, self).\
                                __init__(CmdCode.STACKTRACE, caller_data)
        self._request_size = NO_PARAMS_REQUEST_SIZE + UINT32_SIZE
        self.thread_index = thread_index
        return

    def send(self, debugger_client):
        self._send_base_fields(debugger_client, self._request_size)
        debugger_client.send_uint(self.thread_index)

    def _str_params(self):
        s = super(DebuggerRequest_Stacktrace, self)._str_params()
        s += ',thidx={}'.format(self.thread_index)
        return s


# Step (briefly execute) one thread
# @param step_type enum StepType
class DebuggerRequest_Step(_DebuggerRequest_NoParams):
    def __init__(self, thread_index, step_type):
        assert isinstance(step_type, StepType)
        super(DebuggerRequest_Step,self).__init__(CmdCode.STEP)
        self.__thread_index = thread_index
        self.__step_type = step_type
        self._request_size = \
                NO_PARAMS_REQUEST_SIZE + UINT32_SIZE + UINT8_SIZE

    def send(self, debugger_client):
        self._send_base_fields(debugger_client, self._request_size)
        debugger_client.send_uint(self.__thread_index)
        debugger_client.send_byte(self.__step_type.value)

    def _str_params(self):
        s = super(DebuggerRequest_Step, self)._str_params()
        s += ',thidx={}'.format(self.__thread_index)
        s += ',steptype={}'.format(str(self.__step_type))
        return s


# Stop all threads
class DebuggerRequest_Stop(_DebuggerRequest_NoParams):
    def __init__(self):
        super(DebuggerRequest_Stop,self).__init__(CmdCode.STOP)

# Enumerate all threads
class DebuggerRequest_Threads(_DebuggerRequest_NoParams):
    def __init__(self, caller_data=None):
        super(DebuggerRequest_Threads, self).\
                        __init__(CmdCode.THREADS, caller_data)


########################################################################
# VARIABLES
########################################################################

# Get variables accessible from a given stack frame
class DebuggerRequest_Variables(DebuggerRequest):

    # Get the value of a variable, referenced from the specified
    # stack frame. The path may be None or an empty array, which
    # specifies the local variables in the specified frame.
    # @param thread_index index of the thread to be examined
    # @param stack_index index of the stack frame on the specified thread
    # @param variable_path array of strings, path to variable to inspect
    # @param get_keys if True get the keys in the container variable
    def __init__(self, thread_index, stack_index, variable_path,
                    get_child_keys, caller_data=None):
        super(DebuggerRequest_Variables, self).\
                __init__(CmdCode.VARIABLES, caller_data)

        assert (thread_index != None) and (int(thread_index) >= 0)
        assert (stack_index != None) and (int(stack_index) >= 0)
        assert ((get_child_keys == True) or (get_child_keys == False))
        assert ((variable_path == None) or (len(variable_path) >= 0))

        self.__get_child_keys = get_child_keys
        self.thread_index = thread_index
        self.stack_index = stack_index
        if not variable_path:
            self.__variable_path = []
        else:
            self.__variable_path = variable_path
        # request is base +
        #    uint8: request flags (see enum _VariableRequestFlags)
        #    uint32:thread_index,
        #    uint32:stack_index
        #    uint32:variable_path_len,
        #    char*[]:variable_path
        request_size = NO_PARAMS_REQUEST_SIZE + UINT8_SIZE + (3 * UINT32_SIZE)
        for elem in self.__variable_path:
            # encode() does not include trailing 0
            request_size += len(elem.encode('utf-8')) + 1
        self._request_size = request_size

    # parameters inside the result of __str__()
    def _str_params(self):
        return '{},thridx={},stkidx={},getchildkeys={},varpath={}'.format(
            super(DebuggerRequest_Variables, self)._str_params(),
            self.thread_index,
            self.stack_index,
            self.__get_child_keys,
            self.__variable_path)

    def send(self, debugger_client):
        self._send_base_fields(debugger_client, self._request_size)
        dc = debugger_client
        flags = 0
        if self.__get_child_keys:
            flags |= _VariablesRequestFlags.GET_CHILD_KEYS
        dc.send_byte(flags)
        dc.send_uint(self.thread_index)
        dc.send_uint(self.stack_index)
        dc.send_uint(len(self.__variable_path))
        for elem in self.__variable_path:
            dc.send_str(elem)
        if self._debug >= 5:
            print('debug: sent VARIABLES cmd: {}'.format(self))


def do_exit(errCode, msg=None):
    sys.modules['__main__'].do_exit(errCode, msg)
