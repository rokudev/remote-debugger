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
# File: DAPTypes.py
# Requires python 3.5.3 or later
#
# Data types used by Debug Adapter Protocol (DAP) messages,
# requests, and responses. The data types defined here are sparse, in
# that they only define types, fields, and values used by this adapter,
# and not the full types defined in the DAP spec.
#
# Types that are specific to events should be defined in DAPEvent.py,
# and not in this file. Types specific to requests should be in
# DAPRequest.py. Similarly, for DAPResponse.py, and so on.
#
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

import enum, os, sys

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level, do_exit()


# LITERAL strings (typos are bad news with lazy bindings)
class LITERAL(object):
    arguments = 'arguments'
    breakpoints = 'breakpoints'
    cancelled = 'cancelled'
    channel = 'channel'
    command = 'command'
    configurationDone = 'configurationDone'
    console = 'console'
    context = 'context'
    continue_ = 'continue'      # python reserved word
    data = 'data'
    disconnect = 'disconnect'
    error = 'error'
    evaluate = 'evaluate'
    event = 'event'
    exited = 'exited'
    expression = 'expression'
    filter = 'filter'
    frameId = 'frameId'
    hitCondition = 'hitCondition'
    initialize = 'initialize'
    initialized = 'initialized'
    invalid = 'invalid'
    launch = 'launch'
    line = 'line'
    manifest = 'manifest'
    name = 'name'
    next = 'next'
    outFolder = 'outFolder'
    output = 'output'
    path = 'path'
    pause = 'pause'
    projectRootFolder = 'projectRootFolder'
    rawString = 'rawString'
    request_seq = 'request_seq'
    response = 'response'
    rokuDeviceIP = 'rokuDeviceIP'
    rokuDevicePassword = 'rokuDevicePassword'
    scopes = 'scopes'
    seq = 'seq'
    setBreakpoints = 'setBreakpoints'
    setExceptionBreakpoints = 'setExceptionBreakpoints'
    source = 'source'
    stackTrace = 'stackTrace'
    started = 'started'
    stdout = 'stdout'
    stepIn = 'stepIn'
    stepOut = 'stepOut'
    stopped = 'stopped'
    terminate = 'terminate'
    thread = 'thread'
    threadId = 'threadId'
    threads = 'threads'
    value = 'value'
    variables = 'variables'
    variablesReference = 'variablesReference'


class DAPBreakpoint(object):
    # @param dap_source is a DAPSource file specification
    def __init__(self, dap_source, line):
        assert dap_source
        assert type(dap_source) == DAPSource

        # Required fields
        self.verified = False

        # Optional fields
        self.id = None
        self.message = None     # displayed to the user, usually if !verified
        self.source = dap_source
        self.line = line
        self.endLine = None
        self.endColumn = None


# Defines the capabilities of this debugger, than can be used
# by the DAP client (typically an IDE).
class DAPDebuggerCapabilities(object):
    def __init__(self):
        self.supportsConfigurationDoneRequest = True
        self.supportsFunctionBreakpoints = False
        self.supportsConditionalBreakpoints = True
        self.supportsHitConditionalBreakpoints = True
        self.supportsEvaluateForHovers = True
        self.exceptionBreakpointFilters = None
        self.supportsStepBack = False
        self.supportsSetVariable = False
        self.supportsRestartFrame = False
        self.supportsGotoTargetsRequest = False
        self.supportsStepInTargetsRequest = True
        self.supportsCompletionsRequest = False
        self.completionTriggerCharacters = None
        self.supportsModulesRequest = False
        self.additionalModuleColumns = None
        self.supportedChecksumAlgorithms = None
        self.supportsRestartRequest = False
        self.supportsExceptionOptions = False
        self.supportsValueFormattingOptions = False
        self.supportsExceptionInfoRequest = False
        self.supportTerminateDebuggee = True
        self.supportsDelayedStackTraceLoading = True
        # REMIND: what's the advantage to implementing loadedSources?
        self.supportsLoadedSourcesRequest = False
        self.supportsLogPoints = False
        self.supportsTerminateThreadsRequest = False
        self.supportsSetExpression = False
        self.supportsTerminateRequest = True
        self.supportsDataBreakpoints = False
        self.supportsReadMemoryRequest = False
        self.supportsDisassembleRequest = False
        self.supportsCancelRequest = False
        self.supportsBreakpointLocationsRequest = False


# An output category that is used with DAPOutputEvent(s)
# Numeric values are only used internally within this script.
# Use to_dap_str() to get string that can be passed in a DAP message.
@enum.unique
class DAPOutputCategory(enum.IntEnum):
    UNDEF = 0
    CONSOLE = enum.auto()
    STDERR = enum.auto()
    STDOUT = enum.auto()
    TELEMETRY = enum.auto()

    # Return a string that can be part of a DAP message. Messages always
    # use these strings, and never integer values.
    def to_dap_str(self):
        if not self.value:
            return None
        return self.name.lower()        # pylint: disable=no-member


# A DAPScope is a named container for variables.
class DAPScope(object):
    # @param scope_id defined in this adapter and used in DAP requests
    # @param scope_name displayed to user, e.g., 'Locals', 'Arguments'
    def __init__(self, scope_id, scope_name, func_name, file_path, line_num, num_vars):
        assert type(scope_id) == int and scope_id >= 0
        assert scope_name and len(scope_name)
        assert func_name and len(func_name)
        assert file_path and len(file_path)
        assert line_num >= 0
        assert num_vars >= 0

        self.variablesReference = scope_id
        self.name = scope_name
        self.source = DAPSource(func_name, file_path)
        self.line = line_num
        self.namedVariables = num_vars
        self.expensive = False


# Descriptor for source code (does not include line number)
# Source file paths from debuggee/adapter -> IDE:
#     Paths sent to the IDE should always be an absolute file path on the
#     IDE's host. The DAP spec does not make it clear whether the path can
#     be relative, so it depends upon the IDE, and an absolute path is
#     probably the most consistent option. The launch message from the
#     IDE should provide enough implementation-specific information to
#     build proper paths.
# Source file paths from IDE -> debuggee:
#     These will depend upon the debuggee's native debugger. For the
#     BrightScript debugger, they should be relative to the pkg: root.
class DAPSource(object):
    def __init__(self, func_name, path):
        if global_config.debug_level >= 1:
            assert path
            assert not path.startswith('pkg:')

        self.name = func_name
        self.path = path
        self.sourceReference = None
        self.presentationHint = None
        self.origin = None
        self.sources = None
        self.adapterData = None
        self.checksums = None


class DAPStackFrame(object):

    # Stack frames always go from debuggee to IDE, so file_path must
    # always be an absolute file path on the IDE's host. See comments
    # on class DAPSource for a bit more detail.
    # @param frame_id must be globally unique among all threads
    # @param name is typically the name of the executing function
    def __init__(self, frame_id, name, file_path, line_num):
        if global_config.debug_level >= 1:
            assert isinstance(frame_id,int) and frame_id >= 0, str(frame_id)
            assert name and len(name), str(name)
            assert file_path and len(file_path), str(file_path)
            assert line_num >= 0, str(line_num)

            assert os.path.exists(file_path), 'does not exist: {}'.format(file_path)

        # REQUIRED attributes
        self.id = frame_id
        self.name = name
        self.line = line_num
        self.column = 1                 # BrightScript never stops mid-line

        # Optional attributes
        self.source = DAPSource(os.path.basename(file_path), file_path)

        self.presentationHint = 'label'


class DAPThread(object):
    def __init__(self, id, name):
        self.id = id
        self.name = name

# A DAPVariable can have children (e.g., elements of a linear array or
# associative array)
class DAPVariable(object):

    # variables_ref is > 0, if this variable has children
    # See DAP specification for details
    def __init__(self, name, value, variables_ref=0, type_name=None,
                named_child_count=None, indexed_child_count=None):
        if global_config.debug_level >= 1:
            assert name
            assert value != None        # Required by DAP spec (0 valid)

        if value != None:
            value = str(value)          # DAP requires strings

        # Required fields
        self.name = name
        self.value = value
        self.variablesReference = variables_ref

        # Optional fields
        self.type = type_name
        self.namedVariables = named_child_count
        self.indexedVariables = indexed_child_count
