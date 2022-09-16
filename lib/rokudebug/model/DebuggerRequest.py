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

import copy, enum, re, sys, traceback

from .ProtocolVersion import ProtocolFeature

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

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
    ADD_BREAKPOINTS = 7,
    LIST_BREAKPOINTS = 8,
    REMOVE_BREAKPOINTS = 9,
    EXECUTE = 10,
    ADD_CONDITIONAL_BREAKPOINTS = 11,

    EXIT_CHANNEL = 122,

    # string displayable to an end user
    def to_user_str(self):
        return '{}({})'.format(self.name, self.value)


@enum.unique
class StepType(enum.IntEnum):
    UNDEF = 0,      # Uninitialized value, should not be sent over protocol
    LINE = 1,
    OUT = 2,
    OVER = 3,

@enum.unique
class _VariablesRequestFlags(enum.IntEnum):
    # These values must fit in 8 bits
    GET_CHILD_KEYS              = 0x01,
    CASE_SENSITIVITY_OPTIONS    = 0x02  # can force identifier case insensitivity


# Abstract base class of all debugger requests
class DebuggerRequest(object):

    # All debugger requests have a caller_data attribute. caller_data
    # is an opaque value that is ignored by the debugger client, and the
    # caller can manipulate that data at will
    def __init__(self, cmd_code, caller_data=None):
        self.__local_debug_level = 0
        self.__packet_size = None
        self._protocol_version = None
        self.cmd_code = cmd_code
        self.request_id = None          # Set when sent to debuggee
        self.caller_data = caller_data

    def __str__(self):
        s = '{}[{}]'.format(type(self).__name__, self._str_params())
        return s

    def _get_packet_size(self, protocol_version):
        assert protocol_version
        if self.__packet_size == None or protocol_version != self._protocol_version:
            self._protocol_version = protocol_version
            self.__packet_size = self._calc_packet_size(protocol_version)
        return self.__packet_size

    def _calc_packet_size(self, protocol_version):
        # protocol version must be the same for calculate size and send
        self._protocol_version = protocol_version
        return NO_PARAMS_REQUEST_SIZE

    # parameters inside the response to __str__()
    def _str_params(self):
        s = 'cmdcode={},size={},reqid={}'.format(
                repr(self.cmd_code), self.__packet_size, self.request_id)
        if self.caller_data:
            s += ',cdata={}'.format(self.caller_data)
        return s

    # python makes some whacky decisions when choosing repr() vs. str()
    # let's just make 'em the same
    def __repr__(self):
        return self.__str__()

    # Send the fields common to all requests: packetSize,requestID,cmdCode
    # @return number of bytes written
    def _send_base_fields(self, debugger_client):
        assert self.cmd_code
        assert self.request_id
        dclient = debugger_client
        packet_size = self._get_packet_size(debugger_client.get_protocol_version())
        if self._debug_level() >= 5:
            print('debug: drqst: send base fields {}({}), packet_size={},requestID={}'.\
                format(
                    self.cmd_code.name,
                    self.cmd_code.value,
                    packet_size,
                    self.request_id))
        count = 0
        count += dclient.send_uint(packet_size)
        count += dclient.send_uint(self.request_id)
        count += dclient.send_uint(self.cmd_code)

        # data validation
        # protocol_version set in _get_packet_size()
        self.__verify_num_written(NO_PARAMS_REQUEST_SIZE, count)
        assert self._protocol_version == debugger_client.get_protocol_version()
        return count

    # @param validate if true, raise an exception if the counts don't match
    # @return actual value
    def __verify_num_written(self, expected, actual, validate=True):
        if validate and expected != actual:
            s = 'INTERNAL ERROR: bad size written expected={},actual={}'.format(
                expected, actual)
            print('{}'.format(s))
            raise AssertionError(s)
        return actual

    def _debug_command_sent(self, debugger_client, wr_count, validate=True):
        if (self._debug_level() >= 2):
            print('debug: drqst: command sent: {}'.format(self))
        # protocol_version determines size, member vars set in get_packet_size()
        assert self._protocol_version == debugger_client.get_protocol_version()
        self.__verify_num_written(self.__packet_size, wr_count, validate)

    def _debug_level(self):
        return max(global_config.debug_level, self.__local_debug_level)


# Private subclass
class _DebuggerRequest_NoParams(DebuggerRequest):
    def __init__(self, cmd_code, caller_data=None):
        super(_DebuggerRequest_NoParams, self).\
                __init__(cmd_code, caller_data=caller_data)
        self.__local_debug_level = 0

    def _debug_level(self):
        return max(global_config.debug_level, self.__local_debug_level)

    # Intended for use only within this package (e.g., from DebuggerClient)
    # @return number of bytes written
    def _send(self, debugger_client):
        if self._debug_level() >= 5:
            print('debug: drqst: send {}'.format(self))
        wrcnt = self._send_base_fields(debugger_client)
        self._debug_command_sent(debugger_client, wrcnt)
        return wrcnt


class DebuggerRequest_AddBreakpoints(DebuggerRequest):

    def __init__(self, breakpoints, caller_data=None):
        super(DebuggerRequest_AddBreakpoints, self).\
                __init__(CmdCode.ADD_BREAKPOINTS, caller_data=caller_data)
        if not (breakpoints and len(breakpoints)):
            raise ValueError
        self.__lib_uri_regex = re.compile('^lib:/([a-zA-Z0-9_]+)/(.*)', re.IGNORECASE)
        self.__local_debug_level = 0
        self.breakpoints = copy.deepcopy(breakpoints)
        self.__adjusted_breakpoints = None  # adjusted to meet target's requirements

    def _calc_packet_size(self, protocol_version):
        packet_size = super(DebuggerRequest_AddBreakpoints, self).\
            _calc_packet_size(protocol_version)
        self.__adjust_breakpoints_for_protocol()

        # Adjust the packet size, for the additional fields
        # request is base +
        #    uint32:num_breakpoints,
        #    breakpoint_spec[]:
        #        utf8z:file_path_or_uri
        #        uint32:line_num
        #        uint32:ignore_count
        #    ... breakpoint_spec repeated num_breakpoints times
        packet_size += UINT32_SIZE # num_breakpoints
        try:
            for one_break in self.__adjusted_breakpoints:
                # encode() does not include trailing 0
                packet_size += len(one_break.file_uri.encode('utf-8')) + 1
                packet_size += (2*UINT32_SIZE) # line_num, ignore_count
        except Exception:
            if self._debug_level() >= 5:
                print('debug: drqst: exception:')
                traceback.print_exc(file=sys.stdout)
            raise ValueError

        return packet_size

    # Intended for use only within this package
    def _send(self, debugger_client):
        if self._debug_level() >= 1:
            assert self.__adjusted_breakpoints == None or \
                    len(self.breakpoints) == len(self.__adjusted_breakpoints)
        dclient = debugger_client
        wrcnt = self._send_base_fields(dclient)
        wrcnt += dclient.send_uint(len(self.__adjusted_breakpoints))
        for one_break in self.__adjusted_breakpoints:
            wrcnt += dclient.send_str(one_break.file_uri)
            wrcnt += dclient.send_uint(one_break.line_num)
            wrcnt += dclient.send_uint(one_break.ignore_count)
        self._debug_command_sent(debugger_client, wrcnt)

    def _str_params(self):
        if self._debug_level() >= 1:
            assert self.__adjusted_breakpoints == None or \
                    len(self.breakpoints) == len(self.__adjusted_breakpoints)
        s = super(DebuggerRequest_AddBreakpoints, self)._str_params()
        s += ',breakpoints=['
        need_comma = False
        for i in range(len(self.breakpoints)):
            orig = self.breakpoints[i]
            adjusted = None
            if self.__adjusted_breakpoints:
                adjusted = self.__adjusted_breakpoints[i]
            if need_comma:
                s += ','
            need_comma = True
            if adjusted:
                s += '[{}]'.format(adjusted.str_params())
            else:
                s += 'None'
            s += ',orig=[{}]'.format(orig.str_params())
        return s

    def _debug_level(self):
        return max(global_config.debug_level, self.__local_debug_level)

    # Creates the list self.__adjusted_breakpoints, which has file specs that
    # meet the requirements of the debug target.
    def __adjust_breakpoints_for_protocol(self):
        send_uris = self._protocol_version.has_feature(ProtocolFeature.BREAKPOINTS_URI_SUPPORT)
        self.__adjusted_breakpoints = copy.deepcopy(self.breakpoints)
        for breakpoint in self.__adjusted_breakpoints:
            # This script stores URIs as pkg:/<path> and lib:/<libname>/<path>
            file_spec = None
            if send_uris:
                # target accepts URIs as this script uses them
                file_spec = breakpoint.file_uri
            else:
                # target wants only <path>
                lib_match = self.__lib_uri_regex.match(breakpoint.file_uri)
                if lib_match:
                    file_spec = lib_match.group(2)
                elif breakpoint.file_uri.startswith('pkg:/'):
                    file_spec = breakpoint.file_uri[5:]
                else:
                    file_spec = breakpoint.file_uri
            if self._debug_level() >= 1:
                assert file_spec

            breakpoint.file_uri = file_spec

# END class DebuggerRequest_AddBreakpoints


# Conditional breakpoints always support pkg: and lib: file URIs
class DebuggerRequest_AddConditionalBreakpoints(DebuggerRequest):

    def __init__(self, breakpoints, caller_data=None):
        super(DebuggerRequest_AddConditionalBreakpoints, self).\
                __init__(CmdCode.ADD_CONDITIONAL_BREAKPOINTS, caller_data=caller_data)
        if not (breakpoints and len(breakpoints)):
            raise ValueError
        self.__local_debug_level = 0
        self.__flags = 0    # unused, reserved for future use
        self.breakpoints = copy.deepcopy(breakpoints)
        for breakpoint in self.breakpoints:
            if not breakpoint.cond_expr:
                breakpoint.cond_expr = ''

    def _calc_packet_size(self, protocol_version):
        packet_size = super(DebuggerRequest_AddConditionalBreakpoints, self).\
            _calc_packet_size(protocol_version)

        # Adjust the packet size, for fields beyond the base
        # request is base +
        #    uint32:flags
        #    uint32:num_breakpoints,
        #    breakpoint_spec[]:
        #        utf8z:file_uri    (always URI, not a simple path)
        #        uint32:line_num
        #        uint32:ignore_count
        #        utf8z:cond_expr
        #    ... breakpoint_spec repeated num_breakpoints times
        packet_size += UINT32_SIZE  # flags
        packet_size += UINT32_SIZE  # num_breakpoints
        try:
            for one_break in self.breakpoints:
                # encode() does not include trailing 0
                packet_size += len(one_break.file_uri.encode('utf-8')) + 1
                packet_size += UINT32_SIZE # line_num
                packet_size += UINT32_SIZE # ignore_count
                cond_expr = one_break.cond_expr if one_break.cond_expr else ''
                packet_size += len(cond_expr.encode('utf-8')) + 1
        except Exception:
            if self._debug_level() >= 5:
                print('debug: drqst: exception:')
                traceback.print_exc(file=sys.stdout)
            raise ValueError

        return packet_size

    # Intended for use only within this package
    def _send(self, debugger_client):
        if self._debug_level() >= 1:
            assert self.breakpoints != None and len(self.breakpoints)
        dclient = debugger_client
        wrcnt = self._send_base_fields(dclient)
        wrcnt += dclient.send_uint(self.__flags)
        wrcnt += dclient.send_uint(len(self.breakpoints))
        for one_break in self.breakpoints:
            wrcnt += dclient.send_str(one_break.file_uri)
            wrcnt += dclient.send_uint(one_break.line_num)
            wrcnt += dclient.send_uint(one_break.ignore_count)
            cond_expr = one_break.cond_expr if one_break.cond_expr else ''
            wrcnt += dclient.send_str(cond_expr)
        self._debug_command_sent(debugger_client, wrcnt)

    def _str_params(self):
        s = super(DebuggerRequest_AddConditionalBreakpoints, self)._str_params()
        s += ',breakpoints=['
        need_comma = False
        for breakpoint in self.breakpoints:
            if need_comma:
                s += ','
            need_comma = True
            s += '[{}]'.format(breakpoint.str_params())
        s += ']'
        return s

    def _debug_level(self):
        return max(global_config.debug_level, self.__local_debug_level)

# END class DebuggerRequest_AddConditionalBreakpoints


class DebuggerRequest_Continue(_DebuggerRequest_NoParams):
    def __init__(self, caller_data=None):
        super(DebuggerRequest_Continue, self).\
            __init__(CmdCode.CONTINUE, caller_data=caller_data)


class DebuggerRequest_ExitChannel(_DebuggerRequest_NoParams):
    def __init__(self, caller_data=None):
        super(DebuggerRequest_ExitChannel, self).\
                    __init__(CmdCode.EXIT_CHANNEL, caller_data=caller_data)


class DebuggerRequest_ListBreakpoints(_DebuggerRequest_NoParams):
    def __init__(self, caller_data=None):
        super(DebuggerRequest_ListBreakpoints, self).__init__(
                CmdCode.LIST_BREAKPOINTS, caller_data=caller_data)


class DebuggerRequest_RemoveBreakpoints(DebuggerRequest):

    # @param breakpoint_ids remote breakpoint IDs as defined on target
    def __init__(self, breakpoint_ids, caller_data=None):
        super(DebuggerRequest_RemoveBreakpoints, self).\
                __init__(CmdCode.REMOVE_BREAKPOINTS, caller_data=caller_data)
        if not (breakpoint_ids and len(breakpoint_ids)):
            raise ValueError
        self.breakpoint_ids = copy.copy(breakpoint_ids)

    def _calc_packet_size(self, protocol_version):
        packet_size = super(DebuggerRequest_RemoveBreakpoints, self).\
            _calc_packet_size(protocol_version)
        # Adjust the packet size, for the additional fields
        # request is base +
        #    uint32:num_breakpoints,
        #        uint32 breakpoint_id
        #        ... breakpoint_id repeated num_breakpoints times
        packet_size += ((1+len(self.breakpoint_ids)) * UINT32_SIZE)
        return packet_size

    # Intended for use only within this package
    def _send(self, debugger_client):
        dclient = debugger_client
        wrcnt = self._send_base_fields(dclient)
        wrcnt += dclient.send_uint(len(self.breakpoint_ids))
        for one_id in self.breakpoint_ids:
            wrcnt += dclient.send_uint(one_id)
        self._debug_command_sent(debugger_client, wrcnt)

    def _str_params(self):
        s = super(DebuggerRequest_RemoveBreakpoints, self)._str_params()
        s += ',bkpt_ids=['
        for i in range(len(self.breakpoint_ids)):
            if i > 0:
                s += ','
            s += str(self.breakpoint_ids[i])
        s += ']'
        return s

# END class DebuggerRequest_RemoveBreakpoints

# Get stack trace of one stopped thread
class DebuggerRequest_Stacktrace(DebuggerRequest):
    def __init__(self, thread_index, caller_data=None):
        super(DebuggerRequest_Stacktrace, self).\
                        __init__(CmdCode.STACKTRACE, caller_data=caller_data)
        self.thread_index = thread_index
        return

    def _calc_packet_size(self, protocol_version):
        packet_size = super(DebuggerRequest_Stacktrace, self).\
            _calc_packet_size(protocol_version)
        packet_size += UINT32_SIZE
        return packet_size;

    # Intended for use only within this package
    def _send(self, debugger_client):
        wrcnt = self._send_base_fields(debugger_client)
        wrcnt += debugger_client.send_uint(self.thread_index)
        self._debug_command_sent(debugger_client, wrcnt)

    def _str_params(self):
        s = super(DebuggerRequest_Stacktrace, self)._str_params()
        s += ',thidx={}'.format(self.thread_index)
        return s


# Step (briefly execute) one thread
# @param step_type enum StepType
class DebuggerRequest_Step(_DebuggerRequest_NoParams):

    def __init__(self, thread_index, step_type, caller_data=None):
        assert isinstance(step_type, StepType)
        super(DebuggerRequest_Step,self).__init__(CmdCode.STEP,
            caller_data=caller_data)
        self.__thread_index = thread_index
        self.__step_type = step_type

    def _calc_packet_size(self, protocol_version):
        packet_size = super(DebuggerRequest_Step, self).\
            _calc_packet_size(protocol_version);
        packet_size += (UINT32_SIZE + UINT8_SIZE)
        return packet_size

    # Intended for use only within this package
    def _send(self, debugger_client):
        wrcnt = self._send_base_fields(debugger_client)
        wrcnt += debugger_client.send_uint(self.__thread_index)
        wrcnt += debugger_client.send_byte(self.__step_type.value)
        self._debug_command_sent(debugger_client, wrcnt)


    def _str_params(self):
        s = super(DebuggerRequest_Step, self)._str_params()
        s += ',thidx={}'.format(self.__thread_index)
        s += ',steptype={}'.format(str(self.__step_type))
        return s


# Stop all threads
class DebuggerRequest_Stop(_DebuggerRequest_NoParams):
    def __init__(self, caller_data=None):
        super(DebuggerRequest_Stop,self).__init__(CmdCode.STOP,
                    caller_data=caller_data)

# Enumerate all threads
class DebuggerRequest_Threads(_DebuggerRequest_NoParams):
    def __init__(self, caller_data=None):
        super(DebuggerRequest_Threads, self).\
                        __init__(CmdCode.THREADS, caller_data=caller_data)


########################################################################
# VARIABLES
########################################################################

# Get variables accessible from a given stack frame
class DebuggerRequest_Variables(DebuggerRequest):

    # Get the value of a variable, referenced from the specified
    # stack frame. The path may be None or an empty array, which
    # specifies the local variables in the specified frame.
    # @param thread_index index of the thread to be examined
    # @param frame_index index of the stack frame on the specified thread
    # @param variable_path array of strings, path to variable to inspect
    # @param path_force_case_insensitive bool array, may force each
    #        element of variable_path to be case-insensitive, may be None
    # @param get_keys if True get the keys in the container variable
    def __init__(self, thread_index, frame_index, variable_path,
            path_force_case_insensitive, get_child_keys, caller_data=None):
        super(DebuggerRequest_Variables, self).\
                __init__(CmdCode.VARIABLES, caller_data=caller_data)

        assert (thread_index != None) and (int(thread_index) >= 0)
        assert (frame_index != None) and (int(frame_index) >= 0)
        assert ((get_child_keys == True) or (get_child_keys == False))
        assert ((variable_path == None and path_force_case_insensitive == None) or
                    (len(variable_path) == len(path_force_case_insensitive)))

        self.get_child_keys = get_child_keys
        self.thread_index = thread_index
        self.frame_index = frame_index
        if not variable_path:
            self.variable_path = []
            self.path_force_case_insensitive = []
        else:
            self.variable_path = variable_path
            self.path_force_case_insensitive = path_force_case_insensitive

    def _calc_packet_size(self, protocol_version):
        packet_size = super(DebuggerRequest_Variables, self).\
            _calc_packet_size(protocol_version)
        # request is base +
        #    uint8: request flags (see enum _VariableRequestFlags)
        #    uint32:thread_index,
        #    uint32:frame_index
        #    uint32:variable_path_len,
        #    char*[]:variable_path
        packet_size += (UINT8_SIZE + (3 * UINT32_SIZE))
        for elem in self.variable_path:
            # encode() does not include trailing 0
            packet_size += len(elem.encode('utf-8')) + 1
        if protocol_version.has_feature(ProtocolFeature.CASE_SENSITIVITY):
            packet_size += UINT8_SIZE * len(self.path_force_case_insensitive)
        return packet_size

    # parameters inside the result of __str__()
    def _str_params(self):
        return '{},thridx={},frmidx={},getchildkeys={},varpath={},force_ci={}'.format(
            super(DebuggerRequest_Variables, self)._str_params(),
            self.thread_index,
            self.frame_index,
            self.get_child_keys,
            self.variable_path,
            self.path_force_case_insensitive)

    # Intended for use only within this package
    def _send(self, debugger_client, validate=True):
        dc = debugger_client
        supports_ci = dc.has_feature(ProtocolFeature.CASE_SENSITIVITY)
        flags = 0
        if self.get_child_keys:
            flags |= _VariablesRequestFlags.GET_CHILD_KEYS
        if supports_ci:
            flags |= _VariablesRequestFlags.CASE_SENSITIVITY_OPTIONS

        wrcnt = self._send_base_fields(debugger_client)
        wrcnt += dc.send_byte(flags)
        wrcnt += dc.send_uint(self.thread_index)
        wrcnt += dc.send_uint(self.frame_index)
        wrcnt += dc.send_uint(len(self.variable_path))
        for i in range(len(self.variable_path)):
            elem = self.variable_path[i]
            if self.path_force_case_insensitive[i] and not supports_ci:
                # protocol does not support case-sensitivity options, so use lower
                # case, which is effectively BrightScript's canonical form.
                elem = elem.lower()
            wrcnt += dc.send_str(elem)
        if supports_ci:
            for force_ci in self.path_force_case_insensitive:
                wrcnt += dc.send_byte( 1 if force_ci else 0 )
        self._debug_command_sent(debugger_client, wrcnt, validate)


########################################################################
# EXECUTE
########################################################################

class DebuggerRequest_Execute(DebuggerRequest):

    def __init__(self, thread_index, frame_index, source_code,
                    caller_data=None):
        super(DebuggerRequest_Execute, self).\
                __init__(CmdCode.EXECUTE, caller_data=caller_data)

        assert (thread_index != None) and (int(thread_index) >= 0)
        assert (frame_index != None) and (int(frame_index) >= 0)
        assert ((source_code == None) or (len(source_code) >= 0))

        self.thread_index = thread_index
        self.frame_index = frame_index
        self.source_code = source_code

    def _calc_packet_size(self, protocol_version):
        packet_size = super(DebuggerRequest_Execute, self).\
            _calc_packet_size(protocol_version)
        # request is base +
        #    uint32:thread_index,
        #    uint32:frame_index,
        #    char*:source_code
        packet_size += (2 * UINT32_SIZE)
        packet_size += len(self.source_code.encode('utf-8')) + 1
        return packet_size

    # parameters inside the result of __str__()
    def _str_params(self):
        return '{},thridx={},frmidx={},srccode={}'.format(
            super(DebuggerRequest_Execute, self)._str_params(),
            self.thread_index,
            self.frame_index,
            self.source_code)

    # Intended for use only within this package
    def _send(self, debugger_client, validate=True):
        wrcnt = self._send_base_fields(debugger_client)
        dc = debugger_client
        wrcnt += dc.send_uint(self.thread_index)
        wrcnt += dc.send_uint(self.frame_index)
        wrcnt += dc.send_str(self.source_code)
        self._debug_command_sent(debugger_client, wrcnt, validate);


def do_exit(errCode, msg=None):
    sys.modules['__main__'].do_exit(errCode, msg)
