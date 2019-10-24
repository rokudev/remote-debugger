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
# File: DebuggerResponse.py
# Requires python v3.5.3 or later
#
# NAMING CONVENTIONS:
#
# Type identifiers are CamelCase
# all_other_identifiers are snake_case
# _protected members begin with a single underscore '_' (available to friends)
# __private members begin with double underscore: '__'
#
# python more or less enfores the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import enum, sys

from .DebuggerRequest import CmdCode

@enum.unique
class ErrCode(enum.IntEnum):
    OK = 0,
    OTHER_ERR = 1,
    INVALID_PROTOCOL = 2,   # fatal
    CANT_CONTINUE = 3,
    NOT_STOPPED = 4,
    INVALID_ARGS = 5,

    # enums do not follow the normal rules, regarding str.format().
    # Specifically, str.format() does not call str() for enums and
    # always includes the int value only, in the formatted string.
    # str(obj) does call this
    def __str__(self):
        return repr(self)


@enum.unique
class UpdateType(enum.IntEnum):
    CONNECT_IO_PORT = 1,        # connect to the debugger's I/O port
    ALL_THREADS_STOPPED = 2,
    THREAD_ATTACHED = 3,
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


# Set of container types (those that have sub-elements)
_g_container_types = {
    VariableType.AA,
    VariableType.ARRAY,
    VariableType.LIST,
    VariableType.SUBTYPED_OBJECT
}


# A DebuggerUpdate can be an asynchronous event (e.g., script crashed)
# or a response to a request. Unrequested updates have requestID=0,
# and responses have requestID>0
class DebuggerUpdate(object):
    def __init__(self):
        super(DebuggerUpdate,self).__init__()
        global gMain
        gMain = sys.modules['__main__'].gMain
        self._debug = max(gMain.gDebugLevel, 0)
        self.err_code = None
        self.request_id = None
        self.request = None  # the request that caused this update
        self.update_type = None     # one of enum UpdateType

    def dump(self, out):
        print(str(self), file=out)

    # raises an AssertionError if things are not right
    # subclasses are encouraged to override this and invoke super._validate()
    def _validate(self):
        # 0 is a valid valid for some fields so 'not field' won't work
        assert self.err_code != None
        assert self.request_id != None
        if self.request_id:     # 0 is valid
            assert self.request
        assert self.update_type != None

    def _copy_from(self, other):
        self._debug = other._debug
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
        global gMain
        gMain = sys.modules['__main__'].gMain
        debug_level = max(0, gMain.gDebugLevel)
        d = debugger_client

        update = DebuggerUpdate()
        if debug_level >= 3:
            print('debug: dresp: waiting for update...')
        update.request_id = d.recv_uint()
        if debug_level >= 2:
            print('debug: dresp: reading update/response, requestid={}...'.format(
                update.request_id))
        errInt = d.recv_uint()
        try:
            update.err_code = ErrCode(errInt)
        except:
            do_exit(1, 'Bad err code from target: {}'.format(errInt))

        if debug_level >= 5:
            print('debug: dresp: read update header: {}'.format(update))
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

        # Read the remainder of the update or response, based on the
        # request type and/or update type.
        # However, if the response is an error, there will be no payload
        if (update.err_code == ErrCode.OK):
            if request:
                # Message is a response to a specific request
                if request.cmd_code == CmdCode.THREADS:
                    update = DebuggerResponse_Threads(debugger_client, update)
                elif request.cmd_code == CmdCode.STACKTRACE:
                    update = DebuggerResponse_Stacktrace(debugger_client, update)
                elif request.cmd_code == CmdCode.VARIABLES:
                    update = DebuggerResponse_Variables(debugger_client, update)
            else:
                # Message is an update without a request
                update_type_raw = d.recv_uint()
                update.update_type = None
                try:
                    update.update_type = UpdateType(update_type_raw)
                except:
                    pass
                if update.update_type == UpdateType.CONNECT_IO_PORT:
                    update = DebuggerUpdate_ConnectIoPort(
                        debugger_client, update)
                elif update.update_type == UpdateType.ALL_THREADS_STOPPED:
                    update = DebuggerUpdate_AllThreadsStopped(
                        debugger_client, update)
                elif update.update_type == UpdateType.THREAD_ATTACHED:
                    update = DebuggerUpdate_ThreadAttached(
                        debugger_client, update)
                else:
                    do_exit(1, 'Bad update_type from target: {}'.format(
                        update_type_raw))

        # There are some commands that cause an asynchronous update to
        # happen, such as 'STEP' which gets an immediate "OK" but will
        # cause a THREAD_ATTACHED or ALL_THREADS_STOPPED update later.
        if update.update_type and not update.request_id:
            update.request = \
                debuggerListener.get_pending_request_by_update_type(
                    update.update_type, True)

        if debug_level >= 2:
            print('debug: dresp: read update done: {}'.format(update))
        return update

    def __str__(self):
        s = '{}[{}]'.format(type(self).__name__, self._str_params())
        if self.request:
            s = s + ',request={}'.format(self.request)
        return s

    # parameters inside the response to __str__()
    def _str_params(self):
        s = 'reqid={},errcode={}'.format(
            self.request_id,
            repr(self.err_code))
        return s


#######################################################################
#######################################################################
## DEBUGGER RESPONSES                                                ##
##                                                                   ##
## These are in response to specific requests, made by this client.  ##
#######################################################################
#######################################################################


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
        d = debugger_client
        self._copy_from(baseResponse)
        numFrames = d.recv_uint()
        if self._debug >= 2:
            print('debug: dresp: reading {} stack frames'.format(numFrames))
        self.frames = []
        for _ in range(numFrames):
            frame = DebuggerStackFrame(d)
            self.frames.append(frame)
            if self._debug >= 3:
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
    def _str_params(self):
        s = '{},nframes={}'.format(
            super(DebuggerResponse_Stacktrace, self)._str_params(),
            len(self.frames))
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


class DebuggerStackFrame(object):
    def __init__(self, debugger_client):
        d = debugger_client
        self.line_num = d.recv_uint()
        self.file_name = d.recv_str()
        self.func_name = d.recv_str()

    # Copies known attributes (file_name, line_num, func_name) from
    # other, which can be of any type. Attributes that do not exist
    # in other will be set to None in this object.
    def copy_from(self, other):
        self.line_num = getattr(other, 'line_num', None)
        self.file_name = getattr(other, 'file_name', None)
        self.func_name = getattr(other, 'func_name', None)

    def __str__(self):
        return 'StackFrame[{}(),{}:{}]'.format(
            self.func_name, self.file_name, self.line_num)


########################################################################
# THREADS
########################################################################

# Bitwise mask flags that fit in 8 bits
@enum.unique
class _ThreadInfoFlags(enum.IntEnum):
    IS_PRIMARY = 0x01

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
        if self._debug >= 5:
            print('debug: dresp: reading threads response')
        d = debugger_client
        self._copy_from(baseResponse)
        self.threads = []
        num_threads = debugger_client.recv_uint()
        if self._debug >= 5:
            print('debug: dresp: reading {} threads'.format(num_threads))
        for i_thread in range(num_threads):   # pylint: disable=unused-variable
            thread_info = ThreadInfo(d)
            if self._debug >= 5:
                print('debug: dresp: read thrinfo: {}'.format(thread_info))
            self.threads.append(thread_info)

    def _str_params(self):
        s = '{},nthreads={}'.format(
            super(DebuggerResponse_Threads, self)._str_params(),
            len(self.threads))
        return s

    def dump(self, out):
        num_threads = len(self.threads)
        print('ThreadInfo ({} threads):'.format(num_threads), file=out)
        for i_thread in range(num_threads):
            thread = self.threads[i_thread]
            print('    {}: {}'.format(i_thread, thread), file=out)

class ThreadInfo(object):
    def __init__(self, debugger_client):
        d = debugger_client
        flags = d.recv_byte()
        self.is_primary = False
        if flags & _ThreadInfoFlags.IS_PRIMARY:
            self.is_primary = True
        try:
            stop_int = d.recv_uint()
            self.stop_reason = ThreadStopReason(stop_int)
        except ValueError:
            do_exit(1, 'Bad thread stop reason from target: {}'.format(
                stop_int))

        self.stop_reason_detail = d.recv_str()
        self.line_num = d.recv_uint()
        self.func_name = d.recv_str()
        self.file_name = d.recv_str()
        self.code_snippet = d.recv_str()

    def __str__(self):
        s = ''
        if self.is_primary:
            s += 'primary,'
        s += 'stopcode={},stopdetail={}'.format(
                str(self.stop_reason), self.stop_reason_detail)
        s += ',{}(),{}:{},snippet={}'.format(
            self.func_name, self.file_name, self.line_num,
            self.code_snippet)
        return s


########################################################################
# VARIABLES
########################################################################

# VARINFO flags fit in one byte
# This is a private enum
@enum.unique
class _VarInfoFlag(enum.IntEnum):
    IS_CHILD_KEY   = 0x01,
    IS_CONST       = 0x02,
    IS_CONTAINER   = 0x04,
    IS_NAME_HERE   = 0x08,
    IS_REF_COUNTED = 0x10,
    IS_VALUE_HERE  = 0x20

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
        global gMain
        gMain = sys.modules['__main__'].gMain
        self._debug = max(gMain.gDebugLevel, 0)
        d = debugger_client
        self._copy_from(base_response)
        num_vars = d.recv_uint()
        if self._debug >= 2:
            print('debug: dresp: reading {} vars'.format(num_vars))
        self.variables = []
        for _ in range(num_vars):
            var = DebuggerVariable(d)
            self.variables.append(var)
            if self._debug >= 3:
                print('debug: dresp: read variable: {}'.format(var))

    # parameters inside the response to __str__()
    def _str_params(self):
        s = '{},nvars={}'.format(
            super(DebuggerResponse_Variables, self)._str_params(),
            len(self.variables))
        return s

    def get_parent_var(self):
        parent_var = None
        for var in self.variables:
            if not var.is_child_key:
                parent_var = var
                break
        return parent_var

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


class DebuggerVariable(object):
    def __init__(self, debugger_client):
        # See DebuggerResponse_Variables.__init__() for details on the
        # data received ( ^ it's immediately above ^ )
        self._debug = max(gMain.gDebugLevel, 0)
        d = debugger_client

        # Set default values
        self.name = None
        self.__variable_type = None

        # examples: OBJECT: roMessagePort, roSGScreen
        #           SUBTYPED_OBJECT: roSGNode:Node
        self.__subtype = None  # types: OBJECT, INTERFACE, SUBTYPED_OBJECT
        self.__subsubtype = None # types: SUBTYPED_OBJECT
        self.ref_count = None
        self.key_type = None
        self.element_count = None
        self.name = None
        self.value = None
        self.is_child_key = False
        self.is_container_type = False
        self.is_const = False

        # Start reading
        flags = d.recv_byte()
        self.__variable_type = self.__read_value_type(d)
        if flags & _VarInfoFlag.IS_NAME_HERE:
            self.name = d.recv_str()
        if self._debug >= 5:
            print('debug: dresp: reading var: flags={},name={},type={}'.format(
                _format_var_info_flags(flags), self.name, self.__variable_type))

        if flags & _VarInfoFlag.IS_CHILD_KEY:
            self.is_child_key = True
        if flags & _VarInfoFlag.IS_CONST:
            self.is_const = True
        if flags & _VarInfoFlag.IS_REF_COUNTED:
            self.ref_count = d.recv_uint()
            if self._debug >= 8:
                print('debug: dresp: read ref_count={}'.format(self.ref_count))
        if flags & _VarInfoFlag.IS_CONTAINER:
            self.is_container_type = True
            self.key_type = self.__read_value_type(d)
            if self._debug >= 8:
                print('debug: dresp: read key_type={}'.format(str(self.key_type)))
            self.element_count = d.recv_uint()
            if self._debug >= 8:
                print('debug: dresp: read element_count={}'.format(self.element_count))

        if flags & _VarInfoFlag.IS_VALUE_HERE:
            self.__read_value(d)

        self._validate()

    def get_value_str_for_user(self):
        VT = VariableType
        vartype = self.__variable_type
        if vartype == VT.BOOLEAN:
            if self.value:
                return 'true'
            else:
                return 'false'
        elif vartype == VT.STRING:
            return '"{}"'.format(self.value)
        return '{}'.format(self.value)

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
            s += ',key_type={}'.format(key_type_name)
            s += ',el_count={}'.format(self.element_count)
        if self.is_child_key:
            s += ',ischildkey'
        return s

    # raises an AssertError if this variable not internally consistent
    def _validate(self):
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
        if self._debug >= 1:
            if self.is_container_type:
                assert self.__variable_type in _g_container_types
            else:
                assert self.__variable_type not in _g_container_types

    def __read_value_type(self, debugger_client):
        raw_var_type = debugger_client.recv_byte()
        try:
            var_type = VariableType(raw_var_type)
        except ValueError:
            do_exit(1, 'Bad variable or key type from target: {}'.format(
                            raw_var_type))
        return var_type

    def __read_value(self, debugger_client):
        d = debugger_client
        tcode = self.__variable_type
        VT = VariableType
        if self._debug > 5:
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
        if self._debug >= 5:
            print('debug: dresp: reading var type=Boolean')
        int_val = debugger_client.recv_byte()
        if int_val:
            self.value = True
        else:
            self.value = False

    def __read_value_double(self, debugger_client):
        if self._debug >= 5:
            print('debug: dresp: reading var type=Double')
        self.value = debugger_client.recv_double()

    def __read_value_float(self, debugger_client):
        if self._debug >= 5:
            print('debug: dresp: reading var type=Float')
        self.value = debugger_client.recv_float()

    def __read_value_function(self, debugger_client):
        if self._debug >= 5:
            print('debug: dresp: reading var type=Function')
        self.value = debugger_client.recv_str()

    def __read_value_integer(self, debugger_client):
        if self._debug >= 5:
            print('debug: dresp: reading var type=Integer')
        self.value = debugger_client.recv_int()

    def __read_value_interface(self, debugger_client):
        if self._debug >= 5:
            print('debug: dresp: reading var type=Interface')
        self.__subtype = debugger_client.recv_str()

    def __read_value_invalid(self, debugger_client):
        if self._debug >= 5:
            print('debug: dresp: reading var type=Invalid')
        self.value = None

    def __read_value_long_integer(self, debugger_client):
        if self._debug >= 5:
            print('debug: dresp: reading var type=LongInteger')
        self.value = debugger_client.recv_long()

    def __read_value_object(self, debugger_client):
        if self._debug >= 5:
            print('debug: dresp: reading var type=Object')
        self.__subtype = debugger_client.recv_str()

    def __read_value_string(self, debugger_client):
        if self._debug >= 5:
            print('debug: dresp: reading var type=String')
        self.value = debugger_client.recv_str()

    def __read_value_subroutine(self, debugger_client):
        if self._debug >= 5:
            print('debug: dresp: reading var type=Subroutine')
        self.value = debugger_client.recv_str()

    def __read_value_subtyped_object(self, debugger_client):
        if self._debug >= 5:
            print('debug: dresp: reading var type=SubtypedObject')
        self.__subtype = debugger_client.recv_str()
        self.__subsubtype = debugger_client.recv_str()


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
        self.io_port = d.recv_uint()

    # parameters inside the response to __str__()
    def _str_params(self):
        s = '{},port={}'.format(
            super(DebuggerUpdate_ConnectIoPort, self)._str_params(),
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
        self.primary_thread_index = dc.recv_int()
        stop_int = dc.recv_byte()
        try:
            self.stop_reason = ThreadStopReason(stop_int)
        except ValueError:
            do_exit(1, 'Bad value for stop_reason from target: {}'.format(stop_int))
        self.stop_reason_detail = dc.recv_str()
        self._validate()

    # raises AssertionError if things are not right
    def _validate(self):
        super(DebuggerUpdate_AllThreadsStopped, self)._validate()
        assert self.stop_reason_detail

    def _str_params(self):
        s = '{},primarythridx={},stopreason={},stopdetail={}'.format(
            super(DebuggerUpdate_AllThreadsStopped, self)._str_params(),
            self.primary_thread_index,
            str(self.stop_reason),
            str(self.stop_reason_detail))
        return s


class DebuggerUpdate_ThreadAttached(DebuggerUpdate):
    # Finish reading the response that was started in baseResponse
    # The returned response is a new object that has a copy of all
    # relevent information from base_response
    def __init__(self, debugger_client, base_response):
        super(DebuggerUpdate_ThreadAttached, self).__init__()
        dc = debugger_client
        self._copy_from(base_response)
        self.thread_index = dc.recv_int()
        stop_int = dc.recv_byte()
        try:
            self.stop_reason = ThreadStopReason(stop_int)
        except ValueError:
            do_exit(1, 'Bad value for stop_reason from target: {}'.format(
                stop_int))
        self.stop_reason_detail = dc.recv_str()
        self._validate()

    # raises AssertionError if things are not right
    def _validate(self):
        super(DebuggerUpdate_ThreadAttached, self)._validate()
        assert self.stop_reason
        assert self.stop_reason_detail

    def _str_params(self):
        s = '{},thridx={},stopreason={},stopdetail={}'.format(
            super(DebuggerUpdate_ThreadAttached, self)._str_params(),
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

def do_exit(err_code, msg=None):
    sys.modules['__main__'].do_exit(err_code, msg)
