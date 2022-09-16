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
# File: DebuggerResponse.py
# Requires python v3.5.3 or later
#
# NAMING CONVENTIONS:
#
# TypeIdentifiers are CamelCase
# CONSTANTS are UPPER_SNAKE_CASE
# all_other_identifiers are lower_snake_case
# _protected members begin with a single underscore '_' (subclasses can access)
# __private members begin with double underscore: '__'
#
# python more or less enfores the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import enum, sys, traceback

from .DebuggerRequest import CmdCode
from .DebugUtils import do_exit, get_enum_name, get_file_name
from .ProtocolVersion import ProtocolFeature


global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

@enum.unique
class ErrCode(enum.IntEnum):
    OK = 0,
    OTHER_ERR = 1,
    INVALID_PROTOCOL = 2,   # fatal
    CANT_CONTINUE = 3,
    NOT_STOPPED = 4,
    INVALID_ARGS = 5,
    THREAD_DETACHED = 6,
    EXECUTION_TIMEOUT = 7

    # enums do not follow the normal rules, regarding str.format().
    # Specifically, str.format() does not call str() for enums and
    # always includes the int value only, in the formatted string.
    # str(obj) does call this
    def __str__(self):
        return repr(self)

    # string displayable to an end user
    def to_user_str(self):
        return '{}({})'.format(self.name, self.value)


# Starting with protocol 3.1, error responses include a 32-bit bitmap of
# error flags and potentially extra data.
@enum.unique
class ErrFlag(enum.IntEnum):
    INVALID_VALUE_IN_PATH  = 0x0001
    MISSING_KEY_IN_PATH    = 0x0002

    @staticmethod
    def flags_to_str(flags):
        s = ''
        needBar = False
        for flag in ErrFlag:
            if flags & flag:
                if needBar:
                    s += '|'
                else:
                    needBar = True
                s += flag.name
        if not len(s):
            s = "<none>"
        return s


@enum.unique
class UpdateType(enum.IntEnum):
    CONNECT_IO_PORT = 1,        # connect to the debugger's I/O port
    ALL_THREADS_STOPPED = 2,
    THREAD_ATTACHED = 3,
    BREAKPOINT_ERROR = 4,
    COMPILE_ERROR = 5,

    COMMAND_RESPONSE = 99,      # Not part of protocol

    # enums do not follow the normal rules, regarding str.format().
    # Specifically, str.format() does not call str() for enums and
    # always includes the int value only, in the formatted string.
    # str(obj) does call this
    def __str__(self):
        return repr(self)


@enum.unique
class VariableType(enum.IntEnum):
    # There is no 0 value, largely because it gets confused with None
    AA = 1,
    ARRAY = 2,
    BOOLEAN = 3,
    DOUBLE = 4,
    FLOAT = 5,
    FUNCTION = 6,
    INTEGER = 7,
    INTERFACE = 8,
    INVALID = 9,
    LIST = 10,
    LONG_INTEGER = 11,
    OBJECT = 12,
    STRING = 13,
    SUBROUTINE = 14,
    SUBTYPED_OBJECT = 15,
    UNINITIALIZED = 16, # variable has name, but no type, no value
    UNKNOWN = 17,   # var/key/value is valid, but type is unknown

    # enums do not follow the normal rules, regarding str.format().
    # Specifically, str.format() does not call str() for enums and
    # always includes the int value only, in the formatted string.
    # str(obj) does call this
    def __str__(self):
        return repr(self)


@enum.unique
class ThreadStopReason(enum.IntEnum):
    UNDEFINED       = 0,	# uninitialized stopReason
    NOT_STOPPED     = 1,	# thread is running
    NORMAL_EXIT     = 2,	# thread exited
    STOP_STATEMENT  = 3,	# STOP statement executed
    BREAK           = 4,	# Stopped because of reasons beyond this thread
    ERROR           = 5		# Thread stopped because of an error during execution

    # enums do not follow the normal rules, regarding str.format().
    # Specifically, str.format() does not call str() for enums and
    # always includes the int value only, in the formatted string.
    # str(obj) does call this
    def __str__(self):
        return repr(self)

    def to_str_for_user(self):
        TSR = ThreadStopReason
        s = '<UNKNOWN>'
        if self.value == TSR.NOT_STOPPED:
            s = 'Not Stopped'
        elif self.value == TSR.NORMAL_EXIT:
            s = 'Normal Exit'
        elif self.value == TSR.STOP_STATEMENT:
            s = 'STOP Statement'
        elif self.value == TSR.BREAK:
            s = 'Break'
        elif self.value == TSR.ERROR:
            s = 'Crash'
        else:
            raise AssertionError('Bad value for ThreadStopReason: {}'.format(
                self.value))
        return s


# Set of types that are always containers (those that have sub-elements)
_g_container_types = {
    VariableType.AA,
    VariableType.ARRAY,
    VariableType.LIST,
}
# Set of types that may be containers, though not always
_g_maybe_container_types = {
    VariableType.OBJECT,
    VariableType.SUBTYPED_OBJECT
}


# A DebuggerUpdate can be an asynchronous event (e.g., script crashed)
# or a response to a request. Unrequested updates have requestID=0,
# and responses have requestID>0
class DebuggerUpdate(object):
    def __init__(self):
        super(DebuggerUpdate,self).__init__()
        self._debug_level = 0
        self.is_error = False
        self.err_code = None
        self.packet_length = None
        self.byte_read_count = 0
        self.request_id = None
        self.request = None  # the request that caused this update
        self.update_type = None     # one of enum UpdateType

    def dump(self, out):
        print(str(self), file=out)

    # raises an AssertionError if things are not right
    # subclasses are encouraged to override this and invoke super._validate()
    def _validate(self):    # class DebuggerUpdate
        # 0 is a valid valid for some fields so 'not field' won't work
        assert self.err_code != None
        assert self.request_id != None
        if self.request_id:     # 0 is valid
            assert self.request
        assert self.update_type != None
        # protocol < 3.0 does not send packet_length. If we have it, check it
        if self.packet_length != None:
            assert self.byte_read_count == self.packet_length

    def _copy_from(self, other):
        self._debug_level = other._debug_level
        self.packet_length = other.packet_length
        self.byte_read_count = other.byte_read_count
        self.is_error = other.is_error          # True if err_code != ErrCode.OK
        self.err_code = other.err_code
        self.request_id = other.request_id
        self.request = other.request
        self.update_type = other.update_type

    # Returns true if this message is in response to a specific request
    def is_response(self):
        isIt = (self.request_id != 0)
        if isIt and self.update_type:
            do_exit(1, 'INTERNAL ERROR: request has update type')
        return isIt

    # An update is a message from the debugger sent without a request
    # from this client.
    # Return True if this is an update without a request
    def is_update(self):
        isIt = (self.request_id == 0)
        if isIt and not self.update_type:
            do_exit(1, 'INTERNAL ERROR: update does not have upateType')
        return isIt

    # If the update from the debugger is in response to a request,
    # the returned response's getRequestID() will return non-zero
    # and the request can be retrieved by invoking getRequest() on
    # the returned response.
    # If the request is not related to a request, getRequestID()
    # on the response will return 0 and getRequest() will return None.
    # Upon return, the matching request will have been removed from
    # the debuggerListener's pending request list.
    @staticmethod
    def read_update(debugger_client, debuggerListener):
        debug_level = global_config.debug_level
        dclient = debugger_client

        update = DebuggerUpdate()
        if debug_level >= 3:
            print('debug: dresp: waiting for update...')
        if dclient.has_feature(ProtocolFeature.UPDATES_HAVE_PACKET_LENGTH):
            update.packet_length = dclient.recv_uint32(update)
        update.request_id = dclient.recv_uint32(update)
        errInt = dclient.recv_uint32(update)
        try:
            update.err_code = ErrCode(errInt)
            update.is_error = update.err_code != ErrCode.OK
        except Exception:
            do_exit(1, 'Unknown err code from target: {}'.format(errInt))

        if debug_level >= 5:
            print('debug: dresp: read update header: {}, err={}'.format(update, errInt))
        # Infer the type of the response, from the type of the request
        request = None
        if update.request_id:
            update.update_type = UpdateType.COMMAND_RESPONSE
            request = debuggerListener.get_pending_request(
                                        update.request_id, True)
        update.request = request  # Could be None

        # Validate the update

        if update.request_id and \
                    (update.update_type != UpdateType.COMMAND_RESPONSE):
            do_exit(1, 'Update is both request and not-request: {}'.format(
                update))
        if update.request_id and not update.request:
            do_exit(1, 'Request not found for response, requestID={}'.format(
                update.request_id))


        if (update.err_code != ErrCode.OK):
            # The error payload is different from a successful response
            update = DebuggerResponse_Error(dclient, update)
        else:
            # Read the remainder of the update or response, based on the
            # request type and/or update type.
            if request:
                # Message is a response to a specific request
                if request.cmd_code == CmdCode.ADD_BREAKPOINTS:
                    update = DebuggerResponse_AddBreakpoints(dclient, update)
                elif request.cmd_code == CmdCode.ADD_CONDITIONAL_BREAKPOINTS:
                    update = DebuggerResponse_AddConditionalBreakpoints(dclient, update)
                elif request.cmd_code == CmdCode.CONTINUE:
                    pass
                elif request.cmd_code == CmdCode.EXECUTE:
                    update = DebuggerResponse_Execute(dclient, update)
                elif request.cmd_code == CmdCode.EXIT_CHANNEL:
                    pass
                elif request.cmd_code == CmdCode.LIST_BREAKPOINTS:
                    update = DebuggerResponse_ListBreakpoints(dclient, update)
                elif request.cmd_code == CmdCode.REMOVE_BREAKPOINTS:
                    update = DebuggerResponse_RemoveBreakpoints(dclient, update)
                elif request.cmd_code == CmdCode.STACKTRACE:
                    update = DebuggerResponse_Stacktrace(debugger_client, update)
                elif request.cmd_code == CmdCode.STEP:
                    pass
                elif request.cmd_code == CmdCode.STOP:
                    pass
                elif request.cmd_code == CmdCode.THREADS:
                    update = DebuggerResponse_Threads(debugger_client, update)
                elif request.cmd_code == CmdCode.VARIABLES:
                    update = DebuggerResponse_Variables(debugger_client, update)
                else:
                    do_exit(1, 'INTERNAL ERROR: response for unknown cmd_code={}'.format(
                        request.cmd_code.to_user_str()))
            else:
                # Message is an update without a request
                update_type_raw = dclient.recv_uint32(update)
                update.update_type = None
                try:
                    update.update_type = UpdateType(update_type_raw)
                except Exception: pass
                if update.update_type == UpdateType.ALL_THREADS_STOPPED:
                    update = DebuggerUpdate_AllThreadsStopped(
                        debugger_client, update)
                elif update.update_type == UpdateType.BREAKPOINT_ERROR:
                    update = DebuggerUpdate_BreakpointError(
                        debugger_client, update)
                elif update.update_type == UpdateType.COMPILE_ERROR:
                    update = DebuggerUpdate_CompileError(
                        debugger_client, update)
                elif update.update_type == UpdateType.CONNECT_IO_PORT:
                    update = DebuggerUpdate_ConnectIoPort(
                        debugger_client, update)
                elif update.update_type == UpdateType.THREAD_ATTACHED:
                    update = DebuggerUpdate_ThreadAttached(
                        debugger_client, update)
                else:
                    do_exit(1, 'Bad update_type from target: {}'.format(
                        update_type_raw))

            # If protocol provides packet_length, read remainder
            if update.packet_length != None:
                pad_count = 0;
                while update.byte_read_count < update.packet_length:
                    debugger_client.recv_uint8(update)
                    pad_count += 1
                if update.__check_debug(5) and pad_count:
                    print('debug: dresp: read {} padding bytes'.format(pad_count))

        # There are some commands that cause an asynchronous update to
        # happen, such as 'STEP' which gets an immediate "OK" but will
        # cause a THREAD_ATTACHED or ALL_THREADS_STOPPED update later.
        if update.update_type and not update.request_id:
            update.request = \
                debuggerListener.get_pending_request_by_update_type(
                    update.update_type, True)

        if global_config.debug_level >= 1: # 1 = validation
            DebuggerUpdate.__validate_update(update)

        if debug_level >= 2:
            print('debug: dresp: update received: {}'.format(update))
        return update

    # Throws an AssertionError if validation fails
    @staticmethod
    def __validate_update(update):
        assert update
        if update.err_code == ErrCode.OK:
            assert not isinstance(update, DebuggerResponse_Error)
        else:
            assert isinstance(update, DebuggerResponse_Error)
        update._validate()

    def __str__(self):
        s = '{}[{}]'.format(type(self).__name__, self.str_params())
        if self.request:
            s = s + ',request={}'.format(self.request)
        return s

    # parameters inside the response to __str__()
    def str_params(self):
        s = 'len={}/{},reqid={},errcode={}'.format(
            self.byte_read_count,
            self.packet_length,
            self.request_id,
            get_enum_name(self.err_code))
        return s

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level


#END class DebuggerUpdate

#######################################################################
#######################################################################
## DEBUGGER RESPONSES                                                ##
##                                                                   ##
## These are in response to specific requests, made by this client.  ##
#######################################################################
#######################################################################

# Generic error response to any command
class DebuggerResponse_Error(DebuggerUpdate):

    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from baseResponse
    def __init__(self, debugger_client, baseResponse):
        # If protocol version < 3.1, there is nothing else to read
        # If protocol version >= 3.1, additional data is expected:
        # uint32: err_flags
        # ... various data, depending upon the flags ...
        super(DebuggerResponse_Error, self).__init__()
        d = debugger_client
        self._copy_from(baseResponse)
        self.err_flags = 0                      # 32-bit flags
        self.invalid_value_path_index = None
        self.missing_key_path_index = None

        # Read additional fields
        if d.has_feature(ProtocolFeature.ERROR_FLAGS):
            self.err_flags = d.recv_uint32(self)
        if self.__check_debug(5):
            print('debug: dresp: read errflags: {}'.format(ErrFlag.flags_to_str(self.err_flags)))
        if self.err_flags & ErrFlag.INVALID_VALUE_IN_PATH:
            self.invalid_value_path_index = d.recv_int32(self)
        if self.err_flags & ErrFlag.MISSING_KEY_IN_PATH:
            self.missing_key_path_index = d.recv_int32(self)

        if self.__check_debug(1): # 1 = validate
            self._validate()

    # parameters inside the response to __str__()
    def str_params(self):
        s = super(DebuggerResponse_Error, self).str_params()
        s += ',errflags=0x{:X}={}'.format(self.err_flags,
            ErrFlag.flags_to_str(self.err_flags))
        if self.invalid_value_path_index != None:   # 0 is valid
            s += f',invalid_idx={self.invalid_value_path_index}'
        if self.missing_key_path_index != None:     # 0 is valid
            s += f',missing_idx={self.missing_key_path_index}'
        return s

    def _validate(self):
        self.err_flags != None
        # only one of invalid path entry or missing entry can be present
        if self.invalid_value_path_index != None:
            assert self.missing_key_path_index == None
        if self.missing_key_path_index != None:
            assert self.invalid_value_path_index == None

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level


# Response to ADD_BREAKPOINTS command
class DebuggerResponse_AddBreakpoints(DebuggerUpdate):

    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from baseResponse
    def __init__(self, debugger_client, baseResponse):
        # The response has this format:
        # uint32 numBreakpoints
        # breakpointInfo[]:
        #     uint32 breakpoint_id  # 0 is invalid
        #     uint32 err_code     # ErrorCode enum: OK if valid
        #     uint32 ignore_count   # only present if breakpoint_id is valid
        # ...breakpointInfo repeated numBreakpoint times
        super(DebuggerResponse_AddBreakpoints, self).__init__()
        d = debugger_client
        self._copy_from(baseResponse)
        numBreakpoints = d.recv_uint32(self)
        if self.__check_debug(5):
            print('debug: dresp: reading {} breakpoint infos'.format(
                numBreakpoints))
        self.breakpoints = list()
        for _ in range(numBreakpoints):
            brkpt_info = _BreakpointInfo(d, self)
            self.breakpoints.append(brkpt_info)
            if self.__check_debug(3):
                print('debug: dresp: read breakinfo: {}'.format(brkpt_info))

    # parameters inside the response to __str__()
    def str_params(self):
        s = '{},nbrkpts={}'.format(
            super(DebuggerResponse_AddBreakpoints, self).str_params(),
            len(self.breakpoints))
        return s

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level


# Response to ADD_CONDITIONAL_BREAKPOINTS command
# Currently, this is identical to the ADD_BREAKPOINTS response
class DebuggerResponse_AddConditionalBreakpoints(DebuggerResponse_AddBreakpoints):
    def __init__(self, debugger_client, baseResponse):
        super(DebuggerResponse_AddConditionalBreakpoints, self).__init__(
            debugger_client, baseResponse)


# Response to EXECUTE command
class DebuggerResponse_Execute(DebuggerUpdate):

    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from baseResponse
    def __init__(self, debugger_client, baseResponse):
        # Prior to protocol 3.0.0, this command had no payload.
        # Protocol 3.0.0+ has this format:
        #   uint8 success;  // bool
        #   uint8 runtime_stop_code;    // _UNDEF if code not run
        #   uint32 num_compile_errs;
        #   utf8z[num_compile_errs] compile_errs;
        #   uint32 num_runtime_errs;
        #   utf8z[num_runtime_errs] runtime_errs;
        #   uint32 num_other_errs;
        #   utf8z[num_other_errs] other_errs;
        super(DebuggerResponse_Execute, self).__init__()
        d = debugger_client
        self._copy_from(baseResponse)

        if d.has_feature(ProtocolFeature.EXECUTE_RETURNS_ERRORS):
            self.run_success = d.recv_bool(self)
            self.run_stop_code = d.recv_uint8(self)

            # Compile errors
            errCount = d.recv_uint32(self)
            if self.__check_debug(2):
                print('debug: dresp: reading {} compile errs'.format(
                    errCount))
            self.compile_errors = list()
            for _ in range(errCount):
                self.compile_errors.append(d.recv_str(self))
                if self.__check_debug(3):
                    print('debug: dresp: read compile err: {}'.format(self.compile_errors[-1]))

            # Runtime errors
            errCount = d.recv_uint32(self)
            if self.__check_debug(2):
                print('debug: dresp: reading {} runtime errs'.format(
                    errCount))
            self.runtime_errors = list()
            for _ in range(errCount):
                self.runtime_errors.append(d.recv_str(self))
                if self.__check_debug(3):
                    print('debug: dresp: read runtime err: {}'.format(self.runtime_errors[-1]))

            # Other errors
            errCount = d.recv_uint32(self)
            if self.__check_debug(2):
                print('debug: dresp: reading {} other errs'.format(
                    errCount))
            self.other_errors = list()
            for _ in range(errCount):
                self.other_errors.append(d.recv_str(self))
                if self.__check_debug(3):
                    print('debug: dresp: read other err: {}'.format(self.other_errors[-1]))

    # parameters inside the response to __str__()
    def str_params(self):
        s = '{},runsuccess={},runstopcode={},numcompileerrs={},numrunerrs={},numothererrs={}'.format(
                super(DebuggerResponse_Execute, self).str_params(),
                get_enum_name(self.run_success), get_enum_name(self.run_stop_code),
                len(self.compile_errors), len(self.runtime_errors), len(self.other_errors))
        return s

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level

#END class DebuggerResponse_Execute


# Response to a 'list breakpoints' command
class DebuggerResponse_ListBreakpoints(DebuggerUpdate):

    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from baseResponse
    def __init__(self, debugger_client, baseResponse):
        # From the protocol spec:
        # list breakpoints response data looks like this:
        # uint32 num_breakpoints
        #     uint32 breakpoint_id   # >0 if valid, 0 = invalid
        #     uint32 err_code
        #     uint32 ignore_count    # only present if breakpoint_id is valid
        #     breakpoint_id,err_code,ignore_count repeated num_breakpoints times
        super(DebuggerResponse_ListBreakpoints, self).__init__()
        if self.__check_debug(5):
            print('debug: dresp: reading list breakpoints response')
        d = debugger_client
        self._copy_from(baseResponse)
        self.breakpoint_infos = list()
        num_breakpoints = debugger_client.recv_uint32(self)
        if self.__check_debug(5):
            print('debug: dresp: reading {} breakpoints'.format(num_breakpoints))
        for _ in range(num_breakpoints):   # pylint: disable=unused-variable
            info = _BreakpointInfo(d, self)
            if self.__check_debug(5):
                print('debug: dresp: read breakpoint info: {}'.format(info))
            self.breakpoint_infos.append(info)

    def str_params(self):
        s = '{},nbreaks={}'.format(
            super(DebuggerResponse_ListBreakpoints, self).str_params(),
            len(self.breakpoint_infos))
        return s

    def dump(self, out):
        num_breakpoints = len(self.breakpoint_infos)
        print('{} ({} brkpts):'.format(
            self.__class__.__name__, num_breakpoints), file=out)
        for i_breakpoint in range(num_breakpoints):
            info = self.breakpoint_infos[i_breakpoint]
            print('    {}: {}'.format(i_breakpoint, info), file=out)

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level

# END class DebuggerResponse_ListBreakpoints


# Response to a 'remove breakpoints' command
class DebuggerResponse_RemoveBreakpoints(DebuggerUpdate):

    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from baseResponse
    def __init__(self, debugger_client, baseResponse):
        # From the protocol spec:
        # remove breakpoints response data looks like this:
        # uint32 num_breakpoints
        #     uint32 breakpoint_id   # >0 if valid, 0 = invalid
        #     uint32 err_code
        #     uint32 ignore_count    # only present if breakpoint_id is valid
        #     breakpoint_id,err_code,ignore_count repeated num_breakpoints times
        super(DebuggerResponse_RemoveBreakpoints, self).__init__()
        if self.__check_debug(5):
            print('debug: dresp: reading remove breakpoints response')
        d = debugger_client
        self._copy_from(baseResponse)
        self.breakpoint_infos = list()
        num_breakpoints = debugger_client.recv_uint32(self)
        if self.__check_debug(5):
            print('debug: dresp: reading {} breakpoints'.format(num_breakpoints))
        for _ in range(num_breakpoints):   # pylint: disable=unused-variable
            info = _BreakpointInfo(d, self)
            if self.__check_debug(5):
                print('debug: dresp: read breakpoint info: {}'.format(info))
            self.breakpoint_infos.append(info)

    def str_params(self):
        s = super(DebuggerResponse_RemoveBreakpoints, self).str_params()
        s += ',bkpt_infos=['
        is_first = True
        for info in self.breakpoint_infos:
            if is_first:
                is_first = False
            else:
                s += ','
            s += info.str_params()
        s += ']'
        return s

    def dump(self, out):
        num_breakpoints = len(self.breakpoint_infos)
        print('{} ({} brkpts):'.format(
            self.__class__.__name__, num_breakpoints), file=out)
        for i_breakpoint in range(num_breakpoints):
            info = self.breakpoint_infos[i_breakpoint]
            print('    {}: {}'.format(i_breakpoint, info), file=out)

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level

# END class DebuggerResponse_RemoveBreakpoints

class _BreakpointInfo(object):
    def __init__(self, debugger_client, io_counter):
        d = debugger_client
        self.remote_id = d.recv_uint32(io_counter)
        raw_err_code = d.recv_uint32(io_counter)
        try:
            self.err_code = ErrCode(raw_err_code)
            self.is_error = self.err_code != ErrCode.OK
        except Exception:
            do_exit(1, 'Unknown err_code from target: remote_id={}, err_code={}'.\
                format(self.remote_id, raw_err_code))
        self.ignore_count = None
        if self.remote_id:
            self.ignore_count = d.recv_uint32(io_counter)

    def __str__(self):
        s = '{}[{}]'.format(self.__class__.__name__, self.str_params())
        return s

    def str_params(self):
        s = 'rmt_id={},err={},ign_cnt={}'.format(
                self.remote_id, str(self.err_code), self.ignore_count)
        return s


# Response to 'stacktrace' command
# Stack frames are in this order:
#     frames[0] = first function called, frames[nframes-1] = last function
class DebuggerResponse_Stacktrace(DebuggerUpdate):
    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from baseResponse
    def __init__(self, debugger_client, baseResponse):
        # From the spec:
        # stacktrace response looks like this:
        # uint32 numStackFrames
        # stackFrames[]:
        #     uint32 lineNumber
        #     utf8   fileName
        #     utf8   functionName
        #     utf8   codeSnippet
        # [ stack frame info repeated numStackFrames times ]
        super(DebuggerResponse_Stacktrace, self).__init__()
        self.frames = []

        d = debugger_client
        self._copy_from(baseResponse)
        numFrames = d.recv_uint32(self)
        if self.__check_debug(5):
            print('debug: dresp: reading {} stack frames'.format(numFrames))
        for _ in range(numFrames):
            frame = DebuggerStackFrame(d, self)
            self.frames.append(frame)
            if self.__check_debug(3):
                print('debug: dresp: read frame: {}'.format(frame))
        # The debugger protocol 1.x specifies the stack frames
        # come in reverse order (last function...first function)
        # reverse 'em
        for i_frame in range(int(numFrames / 2)):
            pair_idx = numFrames - i_frame - 1
            tmp = self.frames[i_frame]
            self.frames[i_frame] = self.frames[pair_idx]
            self.frames[pair_idx] = tmp

    # parameters inside the response to __str__()
    def str_params(self):
        s = super(DebuggerResponse_Stacktrace, self).str_params()
        s += ",threads=["
        needComma = False
        for frame in self.frames:
            if needComma: s += ','
            else: needComma = True
            s += frame.str_params()
        s += ']'
        return s

    def dump(self, out):
        numFrames = self.get_num_frames()
        print('Stacktrace ({} frames):'.format(numFrames), file=out)
        for iFrame in range(numFrames):
            frame = self.frames[iFrame]
            print('    {}: {}'.format(iFrame, frame), file=out)

    def get_num_frames(self):
        return len(self.frames)

    def get_frames(self):
        return self.frames

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level


class DebuggerStackFrame(object):
    def __init__(self, debugger_client, io_counter):
        d = debugger_client
        self.line_num = d.recv_uint32(io_counter)
        self.func_name = d.recv_str(io_counter)
        self.file_path = d.recv_str(io_counter)

    # Copies known attributes (file_name, line_num, func_name) from
    # other, which can be of any type. Attributes that do not exist
    # in other will be set to None in this object.
    def copy_from(self, other):
        self.line_num = getattr(other, 'line_num', None)
        self.file_path = getattr(other, 'file_name', None)
        self.func_name = getattr(other, 'func_name', None)

    def __str__(self):
        return 'StackFrame[{}]'.format(self.str_params())

    def str_params(self):
        return '{}(),{}:{}'.format(self.func_name, get_file_name(self.file_path), self.line_num)


########################################################################
# THREADS
########################################################################

# Bitwise mask flags that fit in 8 bits
@enum.unique
class _ThreadInfoFlags(enum.IntEnum):
    IS_PRIMARY  = 0x01
    IS_DETACHED = 0x02

    # enums do not follow the normal rules, regarding str.format().
    # Specifically, str.format() does not call str() for enums and
    # always includes the int value only, in the formatted string.
    # str(obj) does call this
    def __str__(self):
        return repr(self)


# Response to the 'threads' command
class DebuggerResponse_Threads(DebuggerUpdate):

    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from baseResponse
    def __init__(self, debugger_client, baseResponse):
        # From the protocol spec:
        # thread response looks like this:
        # uint32 num_threads
        # uint32 line_number
        # utf8   function_name
        # utf8   file_name
        # utf8   code_snippet
        # [ thread info repeated num_threads times ]
        super(DebuggerResponse_Threads, self).__init__()
        self._debug_level = 0
        if self.__check_debug(5):
            print('debug: dresp: reading threads response')
        d = debugger_client
        self._copy_from(baseResponse)
        self.threads = []
        num_threads = debugger_client.recv_uint32(self)
        if self.__check_debug(5):
            print('debug: dresp: reading {} threads'.format(num_threads))
        primary_count = 0
        for i_thread in range(num_threads):   # pylint: disable=unused-variable
            thread_info = ThreadInfo(d, self)
            if self.__check_debug(5):
                print('debug: dresp: read thrinfo: {}'.format(thread_info))
            self.threads.append(thread_info)
            if thread_info.is_primary:
                primary_count += 1
        if self.__check_debug(1):
            if primary_count != 1:
                do_exit(1, 'error: primary count should be 1, but is {}'.
                    format(primary_count))

    def str_params(self):
        s = super(DebuggerResponse_Threads, self).str_params()
        s += ',threads=['
        need_comma = False
        for info in self.threads:
            if need_comma:
                s += ','
            else:
                need_comma = True
            s += '[' + info.str_params(False) + ']'
        s += ']'
        return s

    def dump(self, out):
        num_threads = len(self.threads)
        print('ThreadInfo ({} threads):'.format(num_threads), file=out)
        for i_thread in range(num_threads):
            thread = self.threads[i_thread]
            print('    {}: {}'.format(i_thread, thread), file=out)

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level


class ThreadInfo(object):
    def __init__(self, debugger_client, io_counter):
        d = debugger_client
        flags = d.recv_uint8(io_counter)
        self.is_primary = False
        if flags & _ThreadInfoFlags.IS_PRIMARY:
            self.is_primary = True
        try:
            stop_int = d.recv_uint32(io_counter)
            self.stop_reason = ThreadStopReason(stop_int)
        except ValueError:
            do_exit(1, 'Bad thread stop reason from target: {}'.format(
                stop_int))

        self.stop_reason_detail = d.recv_str(io_counter)
        self.line_num = d.recv_uint32(io_counter)
        self.func_name = d.recv_str(io_counter)
        self.file_name = d.recv_str(io_counter)
        self.code_snippet = d.recv_str(io_counter)

    def __str__(self):
        s = '{}[{}]'.format(self.__class__.__name__, self.str_params())
        return self.str_params(True)

    def str_params(self, truncateSnippet=True):
        s = ''
        if self.is_primary:
            s += 'primary,'
        s += 'stopcode={},stopdetail={}'.format(
                get_enum_name(self.stop_reason), self.stop_reason_detail)
        s += ',{}(),{}:{}'.format(self.func_name, get_file_name(self.file_name), self.line_num)
        code_snippet = self.code_snippet
        if code_snippet:
            if len(code_snippet) > 40:
                code_snippet = f'{code_snippet[0:37]}...'
            s += f',snippet={code_snippet}'
        return s


########################################################################
# VARIABLES
########################################################################

# VARINFO flags fit in one byte
# This is a private enum
@enum.unique
class _VarInfoFlag(enum.IntEnum):
    IS_CHILD_KEY            = 0x01,
    IS_CONST                = 0x02,
    IS_CONTAINER            = 0x04,
    IS_NAME_HERE            = 0x08,
    IS_REF_COUNTED          = 0x10,
    IS_VALUE_HERE           = 0x20,
    IS_KEYS_CASE_SENSITIVE  = 0x40      # valid for container types

    # enums do not follow the normal rules, regarding str.format().
    # Specifically, str.format() does not call str() for enums and
    # always includes the int value only, in the formatted string.
    # str(obj) does call this
    def __str__(self):
        return repr(self)


# Response to 'variables' command
class DebuggerResponse_Variables(DebuggerUpdate):
    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from baseResponse
    def __init__(self, debugger_client, base_response):
        # From the protocol spec:
        # variables response looks like this:
        # uint32 num_variables
        # variableInfo[]:
        #   uint8 flags;
        #   uint8 variable_type;    // enum _ValueType
        #   char*  name;            // present iff VARINFO_IS_NAME_HERE in flags
        #   uint32 ref_count;       // present iff VARINFO_IS_REF_COUNTED in flags
        #   uint32 key_type;        // present iff VARINFO_IS_CONTAINER in flags
        #   uint32 element_count;   // present iff VARINFO_IS_CONTAINER in flags
        #   void*  value;           // present iff VARINFO_IS_VALUE_HERE in flags
        #                           // value data is dependent upon var_type
        # [ variable info repeated num_variables times ]
        super(DebuggerResponse_Variables, self).__init__()
        self._debug_level = 0
        d = debugger_client
        self._copy_from(base_response)
        num_vars = d.recv_uint32(self)
        if self.__check_debug(5):
            print('debug: dresp: reading {} vars'.format(num_vars))
        self.variables = []
        for _ in range(num_vars):
            var = DebuggerVariable(d, self)
            self.variables.append(var)
            if self.__check_debug(3):
                print('debug: dresp: read var: {}'.format(var))

    # parameters inside the response to __str__()
    def str_params(self):
        s = '{},nvars={}'.format(
            super(DebuggerResponse_Variables, self).str_params(),
            len(self.variables))
        return s

    def get_parent_var(self):
        parent_var = None
        for var in self.variables:
            if not var.is_child_key:
                parent_var = var
                break
        return parent_var

    # Get a description of the parent variable, and optionally all
    # child variables.
    # class DebuggerResponse_Variables
    # @param default_parent_name only used if parent var's name not present in this object
    def get_description_for_user(self, default_parent_name=None,
            include_parent_type=False, include_children=False):
        parent_var = self.get_parent_var()
        if self.__check_debug(1):
            assert parent_var
        if not parent_var:
            return 'No variables'

        s = ''
        s += parent_var.get_description_for_user(
            default_name=default_parent_name,
            include_type=include_parent_type)
        if include_children:
            for var in self.variables:
                if var.is_child_key:
                    s += '\r\n    {}'.format(var.get_description_for_user(
                        include_type=True))
        return s

    # @return array of strings, may be empty or None
    def get_child_keys_as_strs_sorted(self):
        keys = None

        # If no parent_var is found, then this should be a list of all
        # variables in a local scope
        parent_var = self.get_parent_var()
        keys_are_strings = (parent_var == None) or \
                            (parent_var.key_type == VariableType.STRING)
        keys = list()
        if self.variables:
            child_index = -1
            for var in self.variables:
                if var.is_child_key:
                    child_index += 1
                    if keys_are_strings:
                        keys.append(var.name)
                    else:
                        keys.append(str(child_index))
        if keys_are_strings:
            keys.sort()
        return keys

    def dump(self, fout, line_prefix=None):
        if not line_prefix:
            line_prefix = ''
        if not (self.variables and len(self.variables)):
            print('{}Variables: <NONE>'.format(line_prefix))
        else:
            print('{}Variables ({} vars):'.format(
                line_prefix, len(self.variables)), file=fout)
            for i_var in range(len(self.variables)):
                var = self.variables[i_var]
                print('{}    {}: {}'.format(line_prefix, i_var, var), file=fout)

    def _validate(self): # class DebuggerResponse_Variables
        super(DebuggerResponse_Variables,self)._validate()
        if self.variables:
            if len(self.variables):
                parent_count = 0
                for var in self.variables:
                    if not var.is_child_key:
                        parent_count += 1
                # If request is stack frame, parent_count==0
                # If request is variable path, parent_count==1
                assert parent_count <= 1
            for var in self.variables:
                var._validate()

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level


class DebuggerVariable(object):
    def __init__(self, debugger_client, io_counter):
        # See DebuggerResponse_Variables.__init__() for details on the
        # data received ( ^ it's immediately above ^ )
        self._debug_level = 0
        d = debugger_client

        # Set default values
        self.name = None
        self.__variable_type = None

        # examples: OBJECT: roMessagePort, roSGScreen
        #           SUBTYPED_OBJECT: roSGNode:Node
        self.__subtype = None  # types: OBJECT, INTERFACE, SUBTYPED_OBJECT
        self.__subsubtype = None # types: SUBTYPED_OBJECT
        self.__io_counter = io_counter
        self.ref_count = None
        self.key_type = None
        self.element_count = None
        self.name = None
        self.value = None
        self.is_child_key = False
        self.is_container_type = False
        self.is_keys_case_sensitive = False     # valid iff is_container_type
        self.is_const = False
        self.is_ref_counted = False

        # Start reading
        flags = d.recv_uint8(io_counter)
        self.__variable_type = self.__read_value_type(d)

        # NOTE: It would be a good idea to add a flag IS_INDEXED_VALUE
        # to the BrightScript debugging protocol, to better support arrays.
        # If that is set, then the numeric index would be transmitted, in
        # place of the name. That would also support paging of array
        # contents, should that be desirable.

        if flags & _VarInfoFlag.IS_NAME_HERE:
            self.name = d.recv_str(io_counter)
        if self.__check_debug(5):
            print('debug: dresp: reading var: flags={},name={},type={}'.format(
                _format_var_info_flags(flags), self.name, self.__variable_type))

        if flags & _VarInfoFlag.IS_CHILD_KEY:
            self.is_child_key = True
        if flags & _VarInfoFlag.IS_CONST:
            self.is_const = True
        if flags & _VarInfoFlag.IS_REF_COUNTED:
            self.is_ref_counted = True
            self.ref_count = d.recv_uint32(io_counter)
            if self.__check_debug(8):
                print('debug: dresp: read ref_count={}'.format(self.ref_count))

        # Container metadata
        if flags & _VarInfoFlag.IS_CONTAINER:
            self.is_container_type = True
            self.is_keys_case_sensitive = \
                 True if flags & _VarInfoFlag.IS_KEYS_CASE_SENSITIVE else False
            self.key_type = self.__read_value_type(d)
            if self.__check_debug(8):
                print('debug: dresp: read key_type={}'.format(str(self.key_type)))
            self.element_count = d.recv_uint32(io_counter)
            if self.__check_debug(8):
                print('debug: dresp: read element_count={}'.format(self.element_count))
        else:
            self.is_keys_case_sensitive = False
            if flags & _VarInfoFlag.IS_KEYS_CASE_SENSITIVE:
                do_exit(1, "Bad data from target: case-sensitive for non-container") 

        if flags & _VarInfoFlag.IS_VALUE_HERE:
            self.__read_value(d)

        self._validate()

    def get_value_str_for_user(self, use_type_if_no_value=True):
        VT = VariableType
        value_str = None
        self_type = self.__variable_type

        has_no_value = False

        if self_type == VT.BOOLEAN:
            if self.value:
                value_str = 'true'
            else:
                value_str = 'false'
        elif self_type == VT.INVALID:
            value_str = "invalid"
        elif self_type == VT.OBJECT or self_type == VT.SUBTYPED_OBJECT:
            has_no_value = True
        elif self_type == VT.STRING:
            value_str = '"{}"'.format(self.value)
        elif self_type == VT.UNINITIALIZED:
            value_str = '<unitialized>'
        elif self.value == None:
            # An opaque type, such as BrightScript interface ifGlobal
            has_no_value = True
        else:
            value_str = str(self.value)

        if has_no_value and use_type_if_no_value:
            value_str = self.get_type_name_for_user()

        # Other decorations
        if self.is_keys_case_sensitive:
            if not value_str: value_str = ''
            value_str += " casesensitive"
        if self.is_container_type:
            if not value_str: value_str = ''
            if len(value_str): value_str += ' '
            value_str += 'el_cnt={}'.format(self.element_count)
        if self.is_ref_counted:
            if not value_str: value_str = ''
            if len(value_str): value_str += ' '
            value_str += 'ref_cnt={}'.format(self.ref_count)

        return value_str

    def get_type_name_for_user(self):
        tcode = self.__variable_type
        VT = VariableType
        if tcode == VT.AA:
            return "roAssociativeArray"
        elif tcode == VT.ARRAY:
            return 'roArray'
        elif tcode == VT.BOOLEAN:
            return 'Boolean'
        elif tcode == VT.DOUBLE:
            return 'Double'
        elif tcode == VT.FLOAT:
            return 'Float'
        elif tcode == VT.FUNCTION:
            return 'Function'
        elif tcode == VT.INTEGER:
            return 'Integer'
        elif tcode == VT.INTERFACE:
            return 'Interface:{}'.format(self.__subtype)
        elif tcode == VT.INVALID:
            return 'Invalid'
        elif tcode == VT.LIST:
            return 'roList'
        elif tcode == VT.LONG_INTEGER:
            return 'LongInteger'
        elif tcode == VT.OBJECT:
            return self.__subtype
        elif tcode == VT.STRING:
            if self.is_const:
                return 'String (VT_STR_CONST)'
            else:
                return 'roString'
        elif tcode == VT.SUBROUTINE:
            return 'Subroutine'
        elif tcode == VT.SUBTYPED_OBJECT:
            return '{}:{}'.format(self.__subtype, self.__subsubtype)
        elif tcode == VT.UNINITIALIZED:
            return '<uninitialized>'
        elif tcode == VT.UNKNOWN:
            return '<UNKNOWN>'
        else:
            raise AssertionError('Bad value for type: {}'.format(tcode))

    # class DebuggerVariable
    # @param default_name used only if var.name is None
    def get_description_for_user(self, default_name=None, include_type=True):
        s = ''
        if self.name:
            s += self.name + ' '
        elif default_name:
            s += default_name + ' '
        if include_type:
            s += self.get_type_name_for_user() + ' '
        s += self.get_value_str_for_user(use_type_if_no_value=not include_type)
        return s

    def __str__(self):
        var_type_name = None
        if self.__variable_type:
            var_type_name = self.__variable_type.name
        key_type_name = None
        if self.key_type:
            key_type_name = self.key_type.name
        s = 'name={},type={}'.format(self.name, var_type_name)
        if self.ref_count:
            s += ',ref_count={}'.format(self.ref_count)
        if self.is_container_type:
            s += ',iscontainer'
            if self.is_keys_case_sensitive:
                s += ',casesensitive'
            s += ',key_type={}'.format(key_type_name)
            s += ',el_count={}'.format(self.element_count)
        if self.is_child_key:
            s += ',ischildkey'
        return s

    # raises an AssertError if this variable not internally consistent
    def _validate(self): # class DebuggerVariable
        # python asserts can take two expressions: expr1,expr2 .
        # this is equivent to: if not expr1 raise AssertionError(expr2)
        VT = VariableType
        assert self.__variable_type
        if self.is_container_type:
            # element_count can be 0, but not None
            assert (self.element_count != None),\
                        'INTERNAL ERROR: container type has null elcount: {}'.format(self)
            key_type_ok = ((self.key_type == VariableType.STRING) or
                           (self.key_type == VariableType.INTEGER))
            assert key_type_ok, 'bad key type for {}: {}'.format(
                self.name, str(self.key_type))
        else:
            # scalar or string type
            # element_count=0 is valid, must be None
            assert (self.element_count == None), \
                'INTERNAL ERROR: scalar type has element count: {}'.format(self)
        # Verify __subtype
        if (self.__variable_type == VT.OBJECT) or \
            (self.__variable_type == VT.INTERFACE) or \
            (self.__variable_type == VT.SUBTYPED_OBJECT):
            assert self.__subtype, \
                    'No subtype found for type {}'.format(
                        str(self.__variable_type))
        else:
            assert (not self.__subtype), \
                    'Subtype found for type {}'.format(
                        str(self.__variable_type))

        # Verify __subsubtype
        if self.__variable_type == VT.SUBTYPED_OBJECT:
            assert self.__subsubtype, \
                    'No subsubtype found for type {}:{}'.format(
                        str(self.__variable_type), self.__subtype)
        else:
            assert (not self.__subsubtype), \
                    'Subsubtype found for type {}'.format(
                        str(self.__variable_type))

        # Do some extra validation, when debugging
        if self.__check_debug(1):
            if self.is_container_type:
                assert self.__variable_type in _g_container_types or \
                        self.__variable_type in _g_maybe_container_types
            else:
                assert self.__variable_type not in _g_container_types

    def __read_value_type(self, debugger_client):
        raw_var_type = debugger_client.recv_uint8(self.__io_counter)
        try:
            var_type = VariableType(raw_var_type)
        except ValueError:
            if self.__check_debug(2):
                print('debug: exception:')
                traceback.print_exc(file=sys.stdout)

            do_exit(1, 'Bad variable or key type from target: {}'.format(
                            raw_var_type))
        return var_type

    def __read_value(self, debugger_client):
        d = debugger_client
        tcode = self.__variable_type
        VT = VariableType
        if self.__check_debug(5):
            print('debug: dresp: reading var value, type={}'.format(str(tcode)))
        if tcode == VT.AA:
            raise AssertionError('AA should not have a value')
        elif tcode == VT.ARRAY:
            raise AssertionError('Array should not have a value')
        elif tcode == VT.BOOLEAN:
            self.__read_value_boolean(d)
        elif tcode == VT.DOUBLE:
            self.__read_value_double(d)
        elif tcode == VT.FLOAT:
            self.__read_value_float(d)
        elif tcode == VT.FUNCTION:
            self.__read_value_function(d)
        elif tcode == VT.INTEGER:
            self.__read_value_integer(d)
        elif tcode == VT.INTERFACE:
            self.__read_value_interface(d)
        elif tcode == VT.INVALID:
            self.__read_value_invalid(d)
        elif tcode == VT.LIST:
            raise AssertionError('list should not have a value')
        elif tcode == VT.LONG_INTEGER:
            self.__read_value_long_integer(d)
        elif tcode == VT.OBJECT:
            self.__read_value_object(d)
        elif tcode == VT.STRING:
            self.__read_value_string(d)
        elif tcode == VT.SUBROUTINE:
            self.__read_value_subroutine(d)
        elif tcode == VT.SUBTYPED_OBJECT:
            self.__read_value_subtyped_object(d)
        else:
            do_exit(1,
                'INTERNAL ERROR: Variable type has a value but shoud not: {}'.\
                    format(repr(tcode)))

    def __read_value_boolean(self, debugger_client):
        if self.__check_debug(5):
            print('debug: dresp: reading var type=Boolean')
        int_val = debugger_client.recv_uint8(self.__io_counter)
        if int_val:
            self.value = True
        else:
            self.value = False

    def __read_value_double(self, debugger_client):
        if self.__check_debug(5):
            print('debug: dresp: reading var type=Double')
        self.value = debugger_client.recv_double(self.__io_counter)

    def __read_value_float(self, debugger_client):
        if self.__check_debug(5):
            print('debug: dresp: reading var type=Float')
        self.value = debugger_client.recv_float(self.__io_counter)

    def __read_value_function(self, debugger_client):
        if self.__check_debug(5):
            print('debug: dresp: reading var type=Function')
        self.value = debugger_client.recv_str(self.__io_counter)

    def __read_value_integer(self, debugger_client):
        if self.__check_debug(5):
            print('debug: dresp: reading var type=Integer')
        self.value = debugger_client.recv_int32(self.__io_counter)

    def __read_value_interface(self, debugger_client):
        if self.__check_debug(5):
            print('debug: dresp: reading var type=Interface')
        self.__subtype = debugger_client.recv_str(self.__io_counter)

    def __read_value_invalid(self, debugger_client):
        if self.__check_debug(5):
            print('debug: dresp: reading var type=Invalid')
        self.value = None

    def __read_value_long_integer(self, debugger_client):
        if self.__check_debug(5):
            print('debug: dresp: reading var type=LongInteger')
        self.value = debugger_client.recv_long(self.__io_counter)

    def __read_value_object(self, debugger_client):
        if self.__check_debug(5):
            print('debug: dresp: reading var type=Object')
        self.__subtype = debugger_client.recv_str(self.__io_counter)

    def __read_value_string(self, debugger_client):
        if self.__check_debug(5):
            print('debug: dresp: reading var type=String')
        self.value = debugger_client.recv_str(self.__io_counter)

    def __read_value_subroutine(self, debugger_client):
        if self.__check_debug(5):
            print('debug: dresp: reading var type=Subroutine')
        self.value = debugger_client.recv_str(self.__io_counter)

    def __read_value_subtyped_object(self, debugger_client):
        if self.__check_debug(5):
            print('debug: dresp: reading var type=SubtypedObject')
        self.__subtype = debugger_client.recv_str(self.__io_counter)
        self.__subsubtype = debugger_client.recv_str(self.__io_counter)

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level

#END class DebuggerVariable


#######################################################################
#######################################################################
## DEBUGGER UPDATES                                                  ##
##                                                                   ##
## These are in sent by the debugger without a request being sent    ##
#######################################################################
#######################################################################


# The debugger is telling this client to connect to another port,
# to receive the output from the running script.
class DebuggerUpdate_ConnectIoPort(DebuggerUpdate):
    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from baseResponse
    def __init__(self, debugger_client, baseResponse):
        super(DebuggerUpdate_ConnectIoPort, self).__init__()
        d = debugger_client
        self._copy_from(baseResponse)
        self.io_port = d.recv_uint32(self)

    # parameters inside the response to __str__()
    def str_params(self):
        s = '{},port={}'.format(
            super(DebuggerUpdate_ConnectIoPort, self).str_params(),
            self.io_port)
        return s


class DebuggerUpdate_AllThreadsStopped(DebuggerUpdate):
    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from baseResponse
    def __init__(self, debugger_client, baseResponse):
        super(DebuggerUpdate_AllThreadsStopped, self).__init__()
        dc = debugger_client
        self._copy_from(baseResponse)
        self.primary_thread_index = dc.recv_int32(self)
        stop_int = dc.recv_uint8(self)
        try:
            self.stop_reason = ThreadStopReason(stop_int)
        except ValueError:
            do_exit(1, 'Bad value for stop_reason from target: {}'.format(stop_int))
        self.stop_reason_detail = dc.recv_str(self)

    # raises AssertionError if things are not right
    def _validate(self):    # class DebuggerUpdate_AllThreadsstopped
        super(DebuggerUpdate_AllThreadsStopped, self)._validate()
        assert self.stop_reason_detail

    def str_params(self):
        s = '{},primarythridx={},stopreason={},stopdetail="{}"'.format(
            super(DebuggerUpdate_AllThreadsStopped, self).str_params(),
            self.primary_thread_index,
            self.stop_reason,
            self.stop_reason_detail)
        return s


class DebuggerUpdate_BreakpointError(DebuggerUpdate):
    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from baseResponse
    def __init__(self, debugger_client, baseResponse):
        super(DebuggerUpdate_BreakpointError, self).__init__()
        dc = debugger_client
        self._copy_from(baseResponse)
        self.flags = dc.recv_uint32(self)
        self.breakpoint_id = dc.recv_uint32(self)

        num_compile_errors = dc.recv_uint32(self)
        self.compile_errors = []
        for i in range(num_compile_errors):
            self.compile_errors.append(dc.recv_str(self))

        num_runtime_errors = dc.recv_uint32(self)
        self.runtime_errors = []
        for i in range(num_runtime_errors):
            self.runtime_errors.append(dc.recv_str(self))

        num_other_errors = dc.recv_uint32(self)
        self.other_errors = []
        for i in range(num_other_errors):
            self.other_errors.append(dc.recv_str(self))

        if self.__check_debug(1):
            self._validate()

    def str_params(self):
        s = super(DebuggerUpdate_BreakpointError, self).str_params()
        s += ',compile_errs={},run_errs={},other_errs={}'.format(\
            self.compile_errors, self.runtime_errors, self.other_errors)
        return s

    def _validate(self):
        super(DebuggerUpdate_BreakpointError, self)._validate()
        assert self.compile_errors
        assert self.runtime_errors
        assert self.other_errors
        # There must be at least one error
        assert len(self.compile_errors) + len(self.runtime_errors) + \
                 len(self.other_errors) > 0

    def __check_debug(self, min_level):
        return global_config.debug_level >= min_level


class DebuggerUpdate_CompileError(DebuggerUpdate):
    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from baseResponse
    def __init__(self, debugger_client, baseResponse):
        super(DebuggerUpdate_CompileError, self).__init__()
        dc = debugger_client
        self._copy_from(baseResponse)
        self.flags = dc.recv_uint32(self)
        self.err_str = dc.recv_str(self)
        self.file_spec = dc.recv_str(self)
        self.line_num = dc.recv_uint32(self)
        self.lib_name = dc.recv_str(self)
        if len(self.lib_name) == 0:
            self.lib_name = None
        if self.__check_debug(1):
            self._validate()

    def __check_debug(self, min_level):
        return global_config.debug_level >= min_level

    # raises AssertionError if things are not right
    def _validate(self):    # class DebuggerUpdate_AllThreadsstopped
        super(DebuggerUpdate_CompileError, self)._validate()
        assert self.flags != None
        assert self.err_str and len(self.err_str)
        assert self.file_spec and len(self.file_spec)
        assert self.line_num != None and self.line_num >= 0
        assert self.lib_name == None or len(self.lib_name) # None or populated string

    def format_for_user(self):
        if self.__check_debug(1):
            self._validate()
        s = self.err_str + ': '
        s += self.file_spec
        if self.line_num > 0:
            s += '({})'.format(self.line_num)
        if self.lib_name:
            s += ' (lib {})'.format(self.lib_name)

        return s

    def str_params(self):
        s = '{},errstr={},file={},line={}'.format(
            super(DebuggerUpdate_CompileError, self).str_params(),
            self.err_str,
            self.file_spec,
            self.line_num)
        return s


class DebuggerUpdate_ThreadAttached(DebuggerUpdate):
    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from base_response
    def __init__(self, debugger_client, base_response):
        super(DebuggerUpdate_ThreadAttached, self).__init__()
        dc = debugger_client
        self._copy_from(base_response)
        self.thread_index = dc.recv_int32(self)
        stop_int = dc.recv_uint8(self)
        try:
            self.stop_reason = ThreadStopReason(stop_int)
        except ValueError:
            do_exit(1, 'Bad value for stop_reason from target: {}'.format(
                stop_int))
        self.stop_reason_detail = dc.recv_str(self)

    # raises AssertionError if things are not right
    def _validate(self):    # class DebuggerUpdate_ThreadAttached
        super(DebuggerUpdate_ThreadAttached, self)._validate()
        assert self.stop_reason
        assert self.stop_reason_detail

    def str_params(self):
        s = '{},thridx={},stopreason={},stopdetail={}'.format(
            super(DebuggerUpdate_ThreadAttached, self).str_params(),
            self.thread_index,
            str(self.stop_reason),  # str.format() does not call str() for enum
            self.stop_reason_detail)
        return s

def _format_var_info_flags(info_flags):
    assert (info_flags == None) or isinstance(info_flags, int)
    if not info_flags:
        info_flags = 0
    s = 'VarInfoFlags[0x{:02x}'.format(info_flags)
    first_flag = True
    for one_flag in _VarInfoFlag:
        if info_flags & one_flag.value:
            if first_flag:
                s += '='
                first_flag = False
            else:
                s += ','
            s += one_flag.name
    s += ']'
    return s

def get_stop_reason_str_for_user(stop_reason, stop_reason_detail):
    s = stop_reason.to_str_for_user()
    if stop_reason_detail and len(stop_reason_detail):
        s += ': '
        s += stop_reason_detail
    return s
