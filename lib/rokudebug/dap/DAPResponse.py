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
# File: DAPResponse.py
# Requires python 3.5.3 or later
#
# Responses via Debug Adapter AProtocol
#
# NAMING CONVENTIONS:
#
# TypeNames are CamelCase
# CONSTANT_VALUES are CAPITAL_CASE
# all_other_identifiers are snake_case
# _protected members begin with a single underscore '_' (subclasses can access)
# __private members begin with double underscore: '__'
#
# python more or less enforces the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import sys, threading

from .DAPProtocolMessage import DAPProtocolMessage
from .DAPTypes import DAPDebuggerCapabilities
from .DAPTypes import LITERAL

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level, do_exit()


########################################################################
# DAP RESPONSES
# Responses from this adapter via DAP, in response to a request from
# the DAP client (typically an IDE).
########################################################################

class _DAPBody(object):
    pass

# A structured message object. Used to return errors from requests.
class _DAPMessage(object):
    _next_message_lock = threading.Lock()
    _next_message_id = 0

    # message_str should not contain {}, because those are used
    # for variable substitution by the DAP client
    def __init__(self, message_str):
        with _DAPMessage._next_message_lock:
            message_id = _DAPMessage._next_message_id
            _DAPMessage._next_message_id += 1
        self.id = message_id
        self.format = message_str
        self.showUser = True


class DAPVariablePresentationHint(object):
    def __init__(self, is_raw_string=None):

        # This class has no 'body' element.
        # This class has no required attributes

        # Optional attributes
        if is_raw_string:
            self.kind = LITERAL.data
            self.attributes = list()
            self.attributes.append(LITERAL.rawString)


class DAPResponse(DAPProtocolMessage):
    def __init__(self, request_seq, cmd_str, success):
        super(DAPResponse,self).__init__(LITERAL.response)
        assert type(request_seq) == int and request_seq >= 0
        assert type(cmd_str) == str and len(cmd_str)
        assert type(success) == bool

        # Required fields, should not change
        self.command = cmd_str
        self.request_seq = request_seq
        self.success = success

        # Fields set by subclass
        self.message = None             # optional: should be set if !success
        self.body = None                # optional: set by subclass


class DAPErrorResponse(DAPResponse):
    # @param machine_err_str is an enum: 'canceled', etc (definition from spec)
    # @param human_err_str may be displayed to the user
    def __init__(self, request_seq, cmd_str, machine_err_str=None,
            human_err_str=None):
        super(DAPErrorResponse,self).__init__(request_seq, cmd_str, False)
        self.message = machine_err_str
        if human_err_str:
            self.body = _DAPBody()
            self.body.error = _DAPMessage(human_err_str)


class DAPContinueResponse(DAPResponse):
    def __init__(self, request_seq, cmd_str, success):
        super(DAPContinueResponse,self).__init__(request_seq, cmd_str, success)
        self.body = _DAPBody()
        self.body.allThreadsContinued = True    # BRS debugger always continues all


class DAPEvaluateResponse(DAPResponse):

    # if variables_reference > 0, the result is a cointainer variable
    # whose children can be retrieved.
    def __init__(self, request_seq, cmd_str, variables_reference, result,
            type_str):
        super(DAPEvaluateResponse,self).__init__(request_seq, cmd_str, True)
        assert isinstance(result, str)
        assert variables_reference >= 0
        assert isinstance(type_str, str)

        self.body = _DAPBody()
        body = self.body


        # REQUIRED fields
        body.result = str(result)  # DAP spec requires string
        body.variablesReference = variables_reference

        # Optional fields
        body.type = type_str
        body.presentationHint = DAPVariablePresentationHint(
            is_raw_string=True)

class DAPInitializeResponse(DAPResponse):
    def __init__(self, request_seq, cmd_str, success):
        super(DAPInitializeResponse, self).__init__(
                                request_seq, cmd_str, success)
        self.body = DAPDebuggerCapabilities()


class DAPSetBreakpointsResponse(DAPResponse):
    def __init__(self, request_seq, cmd_str, success, breakpoints):
        super(DAPSetBreakpointsResponse, self).__init__(
                        request_seq, cmd_str, success)
        if success: assert breakpoints
        if breakpoints:
            self.body = _DAPBody()
            self.body.breakpoints = breakpoints


class DAPScopesResponse(DAPResponse):
    # @param scopes list of DAPScope
    def __init__(self, request_seq, cmd_str, scopes):
        super(DAPScopesResponse, self).__init__(request_seq, cmd_str, True)
        self.body = _DAPBody()
        self.body.scopes = scopes


class DAPStackTraceResponse(DAPResponse):
    # @param stack_frames list of DAPStackFrame
    def __init__(self, request_seq, cmd_str, stack_frames):
        super(DAPStackTraceResponse, self).__init__(request_seq, cmd_str, True)
        assert stack_frames
        self.body = _DAPBody()
        self.body.stackFrames = stack_frames
        self.body.totalFrames = len(stack_frames)


class DAPThreadsResponse(DAPResponse):
    def __init__(self, request_seq, cmd_str, threads):
        assert threads
        super(DAPThreadsResponse, self).__init__(request_seq, cmd_str, True)
        self.body = _DAPBody()
        self.body.threads = threads

class DAPVariablesResponse(DAPResponse):
    # @param variables iterable of DAPVariable
    def __init__(self, request_seq, cmd_str, variables):
        super(DAPVariablesResponse,self).__init__(request_seq, cmd_str, True)
        if not variables:
            variables = list()
        self.body = _DAPBody()
        self.body.variables = variables
