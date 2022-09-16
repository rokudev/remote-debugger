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
# File: DebugAdapterProtocol.py
# Requires python 3.5.3 or later
#
# Implements the Debug Adapter Protocol (DAP), for integration
# with IDEs, such as Visual Studio Code.
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

import http.client, os, json, pathlib, re, sys, threading, time
import traceback, zipfile

# Classes from other packages
from rokudebug.model import AppInstallerClient
from rokudebug.model import Breakpoint
from rokudebug.model import CmdCode
from rokudebug.model import DebuggerClient
from rokudebug.model import DebuggerRequest_AddBreakpoints
from rokudebug.model import DebuggerRequest_Continue
from rokudebug.model import DebuggerRequest_ExitChannel
from rokudebug.model import DebuggerRequest_ListBreakpoints
from rokudebug.model import DebuggerRequest_RemoveBreakpoints
from rokudebug.model import DebuggerRequest_Stacktrace
from rokudebug.model import DebuggerRequest_Step
from rokudebug.model import DebuggerRequest_Stop
from rokudebug.model import DebuggerRequest_Threads
from rokudebug.model import DebuggerRequest_Variables
from rokudebug.model import ErrCode
from rokudebug.model import ProtocolFeature
from rokudebug.model import ProtocolVersion
from rokudebug.model import StackReferenceIDManager
from rokudebug.model import StepType
from rokudebug.model import UpdateType
from rokudebug.model import Verbosity

# Functions from other packages
from rokudebug.model import check_debuggee_protocol_version

# Classes from this package
from .DAPEvent import DAPInitializedEvent
from .DAPEvent import DAPOutputCategory
from .DAPEvent import DAPOutputEvent
from .DAPEvent import DAPStoppedEvent
from .DAPEvent import DAPStopReason
from .DAPEvent import DAPThreadEvent
from .DAPEvent import DAPThreadEventReason
from .DAPOutputPacketizer import DAPOutputPacketizer
from .DAPRequest import DAPEvaluateContext
from .DAPResponse import DAPContinueResponse
from .DAPResponse import DAPErrorResponse
from .DAPResponse import DAPEvaluateResponse
from .DAPResponse import DAPResponse
from .DAPResponse import DAPInitializeResponse
from .DAPResponse import DAPScopesResponse
from .DAPResponse import DAPSetBreakpointsResponse
from .DAPResponse import DAPStackTraceResponse
from .DAPResponse import DAPThreadsResponse
from .DAPResponse import DAPVariablesResponse
from .DAPTypes import DAPBreakpoint
from .DAPTypes import DAPDebuggerCapabilities
from .DAPTypes import DAPOutputCategory
from .DAPTypes import DAPScope
from .DAPTypes import DAPSource
from .DAPTypes import DAPStackFrame
from .DAPTypes import DAPThread
from .DAPTypes import DAPVariable
from .DAPTypes import LITERAL
from .DAPUtils import do_exit, do_print, to_dap_dict, get_dap_seq_cmd, \
     get_dap_seq_cmd_args, to_debug_str

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level, do_exit()

# Message Headers
_HEADER_NAME_CONTENT_LENGTH = 'Content-Length'
_KNOWN_HEADERS = set((
    _HEADER_NAME_CONTENT_LENGTH,
))

# DAP Commands
_DAP_CMD_CONFIGURATION_DONE = LITERAL.configurationDone
_DAP_CMD_CONTINUE = LITERAL.continue_
_DAP_CMD_DISCONNECT = LITERAL.disconnect
_DAP_CMD_EVALUATE = LITERAL.evaluate
_DAP_CMD_INITIALIZE = LITERAL.initialize
_DAP_CMD_LAUNCH = LITERAL.launch
_DAP_CMD_NEXT = LITERAL.next
_DAP_CMD_PAUSE = LITERAL.pause
_DAP_CMD_SCOPES = LITERAL.scopes
_DAP_CMD_SET_BREAKPOINTS = LITERAL.setBreakpoints
_DAP_CMD_SET_EXCEPTION_BREAKPOINTS = LITERAL.setExceptionBreakpoints
_DAP_CMD_STACK_TRACE = LITERAL.stackTrace
_DAP_CMD_STEP_IN = LITERAL.stepIn
_DAP_CMD_STEP_OUT = LITERAL.stepOut
_DAP_CMD_TERMINATE = LITERAL.terminate
_DAP_CMD_THREADS = LITERAL.threads
_DAP_CMD_VARIABLES = LITERAL.variables


# Implements the Debug Adapter Protocol, which is JSON-based
# but does not fully conform to JSON-RPC.
class DebugAdapterProtocol(object):
    _class_lock = threading.Lock()

    def __init__(self, fin, fout):
        self._debug_level = 0
        if self.__check_debug(1):
            do_print('debug:dap: test stdout redirect')
            do_print('debug:dap: test stderr redirect')
        if self.__check_debug(5):
            do_print('debug:dap:DebugAdaptorProtocol()')
        self.__fin = fin
        self.__fout = fout
        self.__thread = None
        self.__debugger_client = None

        self.__self_lock = threading.Lock()
        self.__dap_send_lock = threading.RLock()
        self.__dap_project_root_dir_path = None
        self.__dap_project_output_dir_path = None
        self.__channel_file_path = None
        self.__dap_known_thread_ids = set()     # thread IDs sent to DAP
                                                # protected with dap_send_lock

        self.__debuggee_all_stopped_received = False

        # Only needed for ProtocolFeature.ATTACH_MESSAGE_DURING_STEP bug
        self.__ignore_next_bs_attached_message = True

    def start(self):
        with DebugAdapterProtocol._class_lock:
            if self.__thread:
                raise RuntimeError('Already started')
        self.__thread = threading.Thread(target=self,
            name='DebugAdapterProtocol-0', daemon=True)

        self.__thread.start()

    # Invoked by runner thread
    def __call__(self):
        try:
            self.__call_impl()
        except SystemExit: raise
        except: # Yes, catch EVERYTHING
            traceback.print_exc(file=sys.stderr)
            global_config.do_exit(1,
                'Uncaught exception in Debug Adapter Protocol')

    def __call_impl(self):
        if self.__check_debug(5):
            do_print('debug:dap:run()')
        done = False

        do_print('info: DAP execution started at {}'.format(
            time.asctime(time.localtime())))

        while not done:
            msg = self.__read_dap_message()
            if not msg:
                done = True
                continue
            handled = self.__handle_dap_message(msg)
            if self.__check_debug(1):
                # Normally, we can just continue, protocol should survive
                assert handled, 'DAP message not handled'

        if self.__check_debug(1):
            do_print('debug:dap: EOF on DAP stream')

    # Returns None on EOF, exits this script if an error occurs
    def __read_dap_message(self):
        if self.__check_debug(5):
            do_print('debug:dap: read_dap_message()')
        fin = self.__fin
        msg = None

        # Format is similar to HTTP : sequences of headers, then data
        headers = self.__read_dap_headers()
        if not headers:
            return None

        msg_str = None
        content_len_str = headers.get(_HEADER_NAME_CONTENT_LENGTH)
        content_len = -1
        try:
            content_len = int(content_len_str)
        except Exception: pass
        if content_len < 1:
            do_exit(1, 'Bad {} in message from DAP: {}'.format(
                _HEADER_NAME_CONTENT_LENGTH, content_len_str))
        try:
            # NB: char encoding should already be correct for the platform
            msg_str = fin.read(content_len)
            if self.__check_debug(9):
                do_print('debug:dap: from dap: {}'.format(msg_str))
            msg = json.loads(msg_str)
        except Exception as e:
            do_exit(1, 'Error reading message from DAP: {}'.format(e))

        if self.__check_debug(3):
            do_print('debug:dap: dap_msg_received: {}'.format(msg_str))
        return msg
    # END __read_dap_message()

    # @return None at EOF
    def __read_dap_headers(self):
        if self.__check_debug(9):
            do_print('debug:dap: read_dap_headers()')
        # The DAP spec says that each line should be terminated with a CRLF, but
        # on Linux, it seems only LF is received. Without CRLF, the standard
        # http and email parsing functions cannot be used (required by RFC 2822)
        headers = None
        line = '_'
        while len(line):
            try:
                # char encoding should already be correct for the platform
                line = self.__fin.readline().strip()
            except Exception:
                do_exit(1, 'Error reading from DAP')
            try:
                if len(line):
                    if not headers:
                        headers = dict()
                    parts = line.split(sep=': ', maxsplit=1)
                    headers[parts[0]] = parts[1]
            except Exception:
                do_exit(1, 'Bad header in message from DAP: {}'.format(line))

        if headers:
            self.__check_known_headers(headers)
        return headers

    # headers:dict
    # @return True if all headers known, False otherwise
    def __check_known_headers(self, headers):
        all_known = True
        for key in headers.keys():
            if not (key in _KNOWN_HEADERS):
                all_known = False
                msg = 'warn: unknown header from DAP: {}'.format(key)
                do_print(msg)
                if self.__check_debug(1):
                    do_exit(1, msg)
        return all_known

    # Exits this process, if an error occurs
    # msg:dict
    # @return True if message handled (regardless of success), False if not
    def __handle_dap_message(self, dap_msg):
        handled = False
        dap_seq, dap_cmd_str = get_dap_seq_cmd(dap_msg)
        if self.__check_debug(5):
            do_print('debug:dap: handle_dap_message()')
        if self.__check_debug(8):
            do_print('debug:dap: handle_dap_message(), msg={}'.format(dap_msg))

        if dap_cmd_str == _DAP_CMD_CONFIGURATION_DONE:
            handled = self.__handle_dap_configuration_done(dap_msg)
        elif dap_cmd_str == _DAP_CMD_CONTINUE:
            handled = self.__handle_dap_continue(dap_msg)
        elif dap_cmd_str == _DAP_CMD_DISCONNECT:
            self.__handle_dap_disconnect(dap_msg)  # exits process, never returns
        elif dap_cmd_str == _DAP_CMD_EVALUATE:
            handled = self.__handle_dap_evaluate(dap_msg)
        elif dap_cmd_str == _DAP_CMD_INITIALIZE:
            handled = self.__handle_dap_initialize(dap_msg)
        elif dap_cmd_str == _DAP_CMD_LAUNCH:
            handled = self.__handle_dap_launch(dap_msg)
        elif dap_cmd_str == _DAP_CMD_NEXT:
            handled = self.__handle_dap_step(dap_msg)
        elif dap_cmd_str == _DAP_CMD_PAUSE:
            handled = self.__handle_dap_pause(dap_msg)
        elif dap_cmd_str == _DAP_CMD_SCOPES:
            handled = self.__handle_dap_scopes(dap_msg)
        elif dap_cmd_str == _DAP_CMD_SET_BREAKPOINTS:
            handled = self.__handle_dap_set_breakpoints(dap_msg)
        elif dap_cmd_str == _DAP_CMD_SET_EXCEPTION_BREAKPOINTS:
            handled = self.__handle_dap_set_exception_breakpoints(dap_msg)
        elif dap_cmd_str == _DAP_CMD_STACK_TRACE:
            handled = self.__handle_dap_stack_trace(dap_msg)
        elif dap_cmd_str == _DAP_CMD_STEP_IN:
            handled = self.__handle_dap_step(dap_msg)
        elif dap_cmd_str == _DAP_CMD_STEP_OUT:
            handled = self.__handle_dap_step(dap_msg)
        elif dap_cmd_str == _DAP_CMD_TERMINATE:
            handled = self.__handle_dap_terminate(dap_msg)
        elif dap_cmd_str == _DAP_CMD_THREADS:
            handled = self.__handle_dap_threads(dap_msg)
        elif dap_cmd_str == _DAP_CMD_VARIABLES:
            handled = self.__handle_dap_variables(dap_msg)
        else:
            dap_msg = 'warn: unknown command from DAP: {}'.format(dap_cmd_str)
            do_print(dap_msg)
            self._send_dap_msg(DAPResponse(dap_seq, dap_cmd_str, False))
            handled = True
            if self.__check_debug(1):
                do_exit(1, dap_msg)

        return handled

    def __check_protocol_version(self):
        assert self.__debugger_client
        assert self.__debugger_client.protocol_version
        dclient = self.__debugger_client
        # check_debuggee exits on incompatible version
        check_debuggee_protocol_version(dclient.protocol_version)


    ####################################################################
    # DAP COMMAND HANDLERS
    ####################################################################

    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_configuration_done(self, dap_msg):
        dclient = self.__debugger_client
        if self.__check_debug(2):
            do_print('debug:dap: __handle_dap_configuration_done(),msg={}'.format(
                dap_msg))
        dap_seq, dap_cmd_str = get_dap_seq_cmd(dap_msg)
        self._send_dap_msg(DAPResponse(dap_seq, dap_cmd_str, True))
        if dclient.has_feature(ProtocolFeature.STOP_ON_LAUNCH_ALWAYS):
            dclient.send(DebuggerRequest_Continue())
        return True

    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_continue(self, dap_msg):
        dclient = self.__debugger_client
        if self.__check_debug(2):
            do_print('debug:dap: handle_dap_continue(),msg={}'.format(
                dap_msg))
        dclient.send(DebuggerRequest_Continue(caller_data=dap_msg))
        return True

    # disconnect = disconnect from this adapter. Since this adapter
    # always performs a "launch" and not an "attach," this also
    # kills the debuggee and this adapter.
    # @return never returns
    def __handle_dap_disconnect(self, dap_msg):
        if self.__check_debug(2):
            do_print('debug:dap: handle_dap_disconnect()')
        dap_seq, dap_cmd_str = get_dap_seq_cmd(dap_msg)
        dclient = self.__debugger_client

        # connections may be dropped, causing I/O errors. Set the exit
        # code, so those relatively normal shutdown errors don't cause
        # this adapter to exit with an error.
        global_config.set_exit_code(0)

        try:
            dclient.send(DebuggerRequest_ExitChannel(caller_data=dap_msg))
        except Exception:
            if self.__check_debug(1):
                do_print('debug: exception ignored')
                traceback.print_exc(file=sys.stdout)
        self._send_dap_msg(DAPResponse(dap_seq, dap_cmd_str, True))
        do_exit(0)
        raise AssertionError('exit failed')

    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_evaluate(self, dap_msg):
        if self.__check_debug(2):
            do_print('debug:dap: handle_dap_evaluate()')
        dap_seq, dap_cmd_str, dap_args = get_dap_seq_cmd_args(dap_msg)
        dclient = self.__debugger_client
        if self.__check_debug(1):
            assert dap_cmd_str == _DAP_CMD_EVALUATE
            assert dap_seq >= 0
            assert dap_args

        # Get the evaluation context, to determine what information to
        # to return.

        eval_ctx = DAPEvaluateContext.from_dap_str(
                    dap_args.get(LITERAL.context, None))
        if eval_ctx == DAPEvaluateContext.UNDEF:
            # Hover provides the simplest response
            eval_ctx = DAPEvaluateContext.HOVER

        # Validate and normalize the request. Currently, only variable
        # references are evaluated

        err_msg = None
        expr = dap_args.get(LITERAL.expression, None)
        if not expr:
            err_msg = 'No expression found in request'
        if not err_msg:
            frame_id = dap_args.get(LITERAL.frameId, None)
            if not frame_id:    # 0 is invalid
                err_msg = 'No stack frame specified'
        if not err_msg:
            thread_index, frame_index, var_path = \
                dclient.decode_stack_ref_id(frame_id)
            if thread_index == None or frame_index == None: # 0 is valid
                err_msg = 'Bad stack frameId in DAP request: {}'.format(
                    frame_id)
        if err_msg:
            with self.__dap_send_lock: # re-entrant
                if global_config.verbosity >= Verbosity.NORMAL:
                    print('info: {}'.format(err_msg))
                self._send_dap_msg(DAPErrorResponse(dap_seq, dap_cmd_str,
                    machine_err_str=LITERAL.error, human_err_str=err_msg))
            return True
        expr = re.sub('^\\s*print\\s+', '', expr)

        # Send the request
        # See DAPEvaluationContext enum definition for meaning of the context

        get_child_keys = eval_ctx == DAPEvaluateContext.REPL
        expr_var_path = var_path
        if not expr_var_path:
            expr_var_path = list()
        expr_var_path.extend(expr.lower().split('.'))

        path_force_case_insensitive = None
        if expr_var_path:
            # Do we need to check the context for appropriate case sensitivity?
            path_force_case_insensitive = [False] * len(var_path)

        dclient.send(DebuggerRequest_Variables(thread_index, frame_index,
            expr_var_path, path_force_case_insensitive,
            get_child_keys=get_child_keys, caller_data=dap_msg))
        return True

    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_initialize(self, dap_msg):
        if self.__check_debug(2):
            do_print('debug:dap: handle_dap_initialize()')
        dap_seq, dap_cmd_str = get_dap_seq_cmd(dap_msg)

        # initialize request and response primarily provide static
        # info about the capabilities of this debugger.
        self._send_dap_msg(DAPInitializeResponse(dap_seq, dap_cmd_str, True))
        return True

    # A scopes requests is a request for variables scopes visible
    # from a specified stack frame. That can require multiple chained
    # requests to the debuggee.
    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_scopes(self, dap_msg):
        return self.__continue_dap_scopes_request(dap_msg)

    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_launch(self, dap_cmd):
        if self.__check_debug(2):
            do_print('debug:dap: handle_dap_launch()')
        dap_seq, dap_cmd_str, dap_args = get_dap_seq_cmd_args(dap_cmd)

        if not self.__parse_launch_args(dap_cmd):
            return True
        if not self.__package_channel_file(dap_seq, dap_cmd_str):
            return True

        ip = dap_args[LITERAL.rokuDeviceIP] # pylint: disable=unsubscriptable-object
        password = dap_args[LITERAL.rokuDevicePassword] # pylint: disable=unsubscriptable-object
        channel_file_path = self.__channel_file_path
        installer = AppInstallerClient(ip, password)
        installer.remove()
        installer.install(channel_file_path, remote_debug=True)
        output_packetizer = DAPOutputPacketizer(self, DAPOutputCategory.STDOUT)
        self.__debugger_client = DebuggerClient(ip, self.debuggee_update_received,
            output_packetizer)
        dclient = self.__debugger_client
        dclient.connect()
        self.__check_protocol_version()
        self._send_dap_msg(DAPResponse(dap_seq, dap_cmd_str, True))

        # The initialized (with a 'd') event is sent, when this adapter
        # and the debuggee are ready to accept configuration commands,
        # such as setBreakpoints. If the debuggee always stops on launch,
        # we wait for the initial stop to accept requests. Versions that
        # don't stop on launch don't support breakpoints, anyway.
        if not dclient.has_feature(ProtocolFeature.STOP_ON_LAUNCH_ALWAYS):
            self._send_dap_msg(DAPInitializedEvent())

        if global_config.verbosity >= Verbosity.HIGH:
            do_print('info: connected, protocol version: {}'.format(
                dclient.protocol_version.to_user_str()))

        return True

    # Sets root dir, output dir, channel zip path instance variables
    # On failure, sends the DAP response and returns False
    # @return True on success, False if launch must be aborted
    def __parse_launch_args(self, dap_cmd):
        dap_seq, dap_cmd_str, dap_args = get_dap_seq_cmd_args(dap_cmd)
        assert dap_cmd_str == _DAP_CMD_LAUNCH

        # Directory paths stored without trailing '/' or '\'
        # These paths are implementation-specific attributes

        # Project root dir
        proj_root_dir = dap_args.get(LITERAL.projectRootFolder,None)
        if not proj_root_dir:
            self._send_dap_msg(DAPErrorResponse(dap_seq, dap_cmd_str, 'cancelled',
                'IDE did not send {}'.format(LITERAL.projectRootFolder)))
            return False
        proj_root_dir = os.path.normpath(proj_root_dir)
        self.__dap_project_root_dir_path = proj_root_dir  # store w/o trailing path sep
        if self.__check_debug(3):
            print('debug:dap: launch root dir={}'.format(proj_root_dir))

        # Output dir
        proj_out_dir = dap_args.get(LITERAL.outFolder, None)
        if not proj_out_dir:
            self._send_dap_msg(DAPErrorResponse(dap_seq, dap_cmd_str, 'cancelled',
                'IDE did not send {}'.format(LITERAL.outFolder)))
            return False
        proj_out_dir = os.path.normpath(proj_out_dir)
        self.__dap_project_output_dir_path = proj_out_dir
        if self.__check_debug(3):
            print('debug:dap: launch out dir={}'.format(proj_out_dir))

        return True

    # On error, sends DAP response and returns False
    # @return True if successful, False if launch should be aborted
    def __package_channel_file(self, dap_seq, dap_cmd_str):
        err_msg = None
        root_dir = self.__dap_project_root_dir_path
        out_dir = self.__dap_project_output_dir_path
        if self.__check_debug(5):
            do_print('debug:dap: package_channel_file(),root={},out={}'.format(
                root_dir, out_dir))

        # Determine channel file name and path
        err_msg = self.__determine_channel_file_path()
        if not err_msg:
            channel_file_path = self.__channel_file_path
            assert channel_file_path and len(channel_file_path)

        # Create output directory

        if not err_msg:
            if os.path.exists(out_dir):
                if not os.path.isdir(out_dir):
                    err_msg = 'Path exists, but is not a directory: {}'.\
                        format(out_dir)
            else:
                try:
                    pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    err_msg = 'ERROR: {}: {}'.format(out_dir, str(e))

        # Delete old file(s)
        if not err_msg:
            zombie_path = channel_file_path
            try:
                os.unlink(zombie_path)
            except Exception as e:
                if os.path.exists(zombie_path):
                    err_msg = 'Could not remove {}: {}'.format(
                                zombie_path, e)

        # Write zip file

        if not err_msg:
            channel_zip = None
            try:
                # Create the zip file
                # Note: python <3.8 does not support compresslevel
                if self.__check_debug(5):
                    do_print('debug:dap: writing channel zip: {}'.format(
                        channel_file_path))
                try:
                    channel_zip = zipfile.ZipFile(channel_file_path, 'w',
                        compression=zipfile.ZIP_DEFLATED)
                except Exception: pass
                if not channel_zip:
                    # compression package 'zlib' not accessible?
                    try:
                        channel_zip = zipfile.ZipFile(channel_file_path, 'w')
                        if self.__check_debug(2):
                            do_print('debug:dap: creating zip file with no'
                                ' compression (zlib not loaded?)')
                    except Exception as e:
                        err_msg = 'Could not create zip file {}: {}'.format(
                            channel_file_path, e)

                # Write the zip file
                try:
                    if channel_zip:
                        vscode_dir = os.path.join(root_dir, '.vscode')
                        excludes=[out_dir, vscode_dir]
                        self.__zip_dir_hierarchy(channel_zip, root_dir,
                                archive_path='', excludes=excludes)
                except Exception as e:
                    err_msg = 'Failed writing zip file {}: {}'.format(
                        channel_file_path, e)
            finally:
                try:
                    if channel_zip:
                        channel_zip.close()
                except Exception as e:
                    if self.__check_debug(1):
                        do_print('debug:dap: error closing zip file {}: {}'.format(
                            channel_file_path, e))
                channel_zip = None

        if err_msg:
            self._send_dap_msg(DAPErrorResponse(dap_seq, dap_cmd_str,
                LITERAL.cancelled, err_msg))
            if self.__check_debug(2):
                do_print('debug:dap: {}'.format(err_msg))
            return False
        else:
            if global_config.verbosity >= Verbosity.HIGH:
                do_print('info: packaged channel: {}'.format(
                    channel_file_path))
        return True

    # Sets self.__channel_file_path
    # @return None on success, a str err_msg on failure
    def __determine_channel_file_path(self):
        err_msg = None
        root_dir_path = self.__dap_project_root_dir_path
        out_dir_path = self.__dap_project_output_dir_path

        if not err_msg:
            manifest_path = os.path.join(root_dir_path, LITERAL.manifest)
            channel_file_name = None
            title = None
            major_version = None
            minor_version = None
            build_version = None
            try:
                with open(manifest_path) as manifest:
                    for line in manifest:
                        match = re.search(r'title\s*=\s*(.*)', line)
                        if match:
                            title = match.group(1)
                        match = re.search(r'major_version\s*=\s*(.*)', line)
                        if match:
                            major_version = match.group(1)
                        match = re.search(r'minor_version\s*=\s*(.*)', line)
                        if match:
                            minor_version = match.group(1)
                        match = re.search(r'build_version\s*=\s*(.*)', line)
                        if match:
                            build_version = match.group(1)
            except Exception as e:
                err_msg = 'Could not read channel manifest: {}: {}'.format(
                    manifest_path, str(e))
            if not err_msg:
                if not title:
                    err_msg = '\'title\' not found in manifest: {}'.format(
                        manifest_path)
                else:
                    s = re.sub(r'[^a-zA-Z0-9]', '', title.strip())[:20]
                    if not len(s):
                        s = LITERAL.channel
                    if major_version:
                        s += '-{}'.format(major_version)
                        if minor_version:
                            s += '.{}'.format(minor_version)
                            if build_version:
                                s += '.{}'.format(build_version)
                    s = re.sub(r'[^a-zA-Z0-9_\-\.]', '', s)
                    channel_file_name = '{}-dev.zip'.format(s)
                    channel_file_path = os.path.join(out_dir_path,
                            channel_file_name)
                    self.__channel_file_path = channel_file_path
        return err_msg
    #END __determine_channel_file_path()

    # @param excluded_files iterable of file paths (os-specific)
    # @raise an exception on any errors
    # @return None
    def __zip_dir_hierarchy(self, zip_file, dir_path, archive_path, excludes):
        if self.__check_debug(5):
            do_print('debug:dap: zip_dir_hierarchy(dir={},arpath={},excludes={}'\
                .format(dir_path, archive_path, repr(excludes)))
        for entry_name in sorted(os.listdir(dir_path)):
            entry_path = os.path.join(dir_path, entry_name)
            entry_archive_path = os.path.join(archive_path, entry_name)
            excluded = False
            for exclude in excludes:
                try:
                    if os.path.samefile(entry_path, exclude):
                        excluded = True
                        break
                except Exception: pass
            if excluded:
                continue
            if os.path.isfile(entry_path):
                zip_file.write(entry_path, arcname=entry_archive_path)
            elif os.path.isdir(entry_path):
                self.__zip_dir_hierarchy(zip_file,
                    dir_path=os.path.join(dir_path, entry_name),
                    archive_path=os.path.join(archive_path, entry_name),
                    excludes=excludes)

    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_pause(self, dap_cmd):
        if self.__check_debug(2):
            do_print('debug:dap: handle_dap_pause()')
        dap_seq, dap_cmd_str = get_dap_seq_cmd(dap_cmd)
        dclient = self.__debugger_client

        # Acknowledge command without waiting for actual stop
        self._send_dap_msg(DAPResponse(dap_seq, dap_cmd_str, True))

        # The DAP client (typically an IDE) is expecting a STOPPED
        # event when the threads actually stop
        dclient.send(DebuggerRequest_Stop(caller_data=dap_cmd))
        return True

    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_set_breakpoints(self, dap_msg):
        dclient = self.__debugger_client
        dap_seq, dap_cmd_str = get_dap_seq_cmd(dap_msg)
        dap_args = dap_msg[LITERAL.arguments]
        dap_source_desc = dap_args[LITERAL.source]
        dap_source_path = dap_source_desc[LITERAL.path]
        dap_breakpoints = dap_args[LITERAL.breakpoints]
        dap_source_root = self.__dap_project_root_dir_path
        if self.__check_debug(2):
            do_print('debug:dap: __handle_dap_set_breakpoints(path={})'.format(
                dap_source_path))

        bs_breakpoints = list()
        if not dap_source_path.startswith(dap_source_root):
            if global_config.verbosity >= Verbosity.NORMAL:
                do_print('warn: breakpoint ignored'
                    ', path "{}" not under project root "{}"'.format(
                        dap_source_path, self.__dap_project_root_dir_path))
        else:
            debuggee_source_path = dap_source_path[len(dap_source_root):]
            for dap_breakpoint in dap_breakpoints:
                ignore_count = 0
                try:
                    ignore_count = \
                        max(0,int(dap_breakpoint[LITERAL.hitCondition]))
                except Exception: pass
                bs_breakpoints.append(Breakpoint(debuggee_source_path,
                    int(dap_breakpoint[LITERAL.line]), ignore_count))

        # REMIND: DAP spec says that any pre-existing breapoints in the
        # source_spec in the message should be cleared.

        if len(bs_breakpoints):
            dclient.send(DebuggerRequest_AddBreakpoints(bs_breakpoints,
                caller_data=dap_msg))
        else:
            if self.__check_debug(2):
                do_print('debug:dap: no breakpoints in set_breakpoints message')
            self._send_dap_msg(DAPResponse(dap_seq, dap_cmd_str, True))
        return True

    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_set_exception_breakpoints(self, dap_msg):
        # BrightScript has no exceptions, so this request always fails
        dap_cmd_str = dap_msg[LITERAL.command]
        dap_request_seq = dap_msg[LITERAL.seq]
        self._send_dap_msg(DAPResponse(dap_request_seq, dap_cmd_str, False))
        return True

    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_stack_trace(self, dap_cmd):
        assert dap_cmd[LITERAL.command] == _DAP_CMD_STACK_TRACE
        dclient = self.__debugger_client
        dap_args = dap_cmd[LITERAL.arguments]
        thread_index = dap_args[LITERAL.threadId]
        if self.__check_debug(2):
            do_print('debug:dap: handle_dap_stack_trace(thread_index={})'.
                    format(thread_index))
        dclient.send(DebuggerRequest_Stacktrace(
            thread_index, caller_data=dap_cmd))
        return True

    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_step(self, dap_cmd):
        dap_seq, dap_cmd_str, dap_args = get_dap_seq_cmd_args(dap_cmd)
        dclient = self.__debugger_client
        thread_index = dap_args[LITERAL.threadId] # pylint: disable=unsubscriptable-object
        if self.__check_debug(2):
            do_print('debug:dap: handle_dap_step(cmd={},thread_index={})'.
                    format(dap_cmd_str, thread_index))

        bs_step_type = None
        if dap_cmd_str == _DAP_CMD_NEXT:
            bs_step_type = StepType.OVER
        elif dap_cmd_str == _DAP_CMD_STEP_IN:
            bs_step_type = StepType.LINE
        elif dap_cmd_str == _DAP_CMD_STEP_OUT:
            bs_step_type = StepType.OUT
        assert bs_step_type, 'internal error bad step type' # unrecoverable

        if dclient.has_feature(
                    ProtocolFeature.ATTACHED_MESSAGE_DURING_STEP_BUG):
            with self.__self_lock:
                self.__ignore_next_bs_attached_message = True

        # Respond to DAP client (IDE) immediately with acknowledgement,
        # will send another message when the debuggee responds.
        self._send_dap_msg(DAPResponse(dap_seq, dap_cmd_str, True))
        dclient.send(DebuggerRequest_Step(thread_index, bs_step_type,
            caller_data=dap_cmd))
        return True

    # terminate == terminate the debuggee
    # This adapter always performs a "launch" and not an "attach," so
    # this kills the debuggee and this adapter.
    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_terminate(self, dap_msg):
        if self.__check_debug(2):
            do_print('debug:dap: __handle_dap_terminate()')
        dclient = self.__debugger_client

        # The debuggee often kills the channel, and closes the connection
        # without sending a success response. That results in an I/O error
        # waiting for the reponse. Set the exit code so that does not
        # cause this adapter to exit with an error.
        global_config.set_exit_code(0)

        dclient.send(DebuggerRequest_ExitChannel(caller_data=dap_msg))
        return True

    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_threads(self, dap_msg):
        if self.__check_debug(2):
            do_print('debug:dap: __handle_dap_threads()')
        dclient = self.__debugger_client
        dclient.send(DebuggerRequest_Threads(caller_data=dap_msg))
        return True

    # @return True if message handled (regardless of success), False otherwise
    def __handle_dap_variables(self, dap_msg):
        if self.__check_debug(2):
            do_print('debug:dap: __handle_dap_variables()')
        # dap_seq, dap_cmd_str = get_dap_request_seq_and_cmd(dap_msg)
        dap_args = dap_msg[LITERAL.arguments]
        stack_ref_id = dap_args[LITERAL.variablesReference]
        get_child_keys = True
        dclient = self.__debugger_client
        thread_index, frame_index, var_path = \
                        dclient.decode_stack_ref_id(stack_ref_id)
        vars_response = dclient.get_thread_stack_variables(
                            thread_index, frame_index, var_path,
                            get_child_keys=get_child_keys)

        path_force_case_insensitive = None
        if var_path:
            # Do we need to check the context for appropriate case sensitivity?
            path_force_case_insensitive = [False] * len(var_path)

        vars = None
        if vars_response:
            vars = vars_response.variables
        if vars:
            self.__respond_to_dap_variables_request(dap_msg)
        else:
            dclient.send(DebuggerRequest_Variables(thread_index, frame_index,
                var_path, path_force_case_insensitive,
                get_child_keys=get_child_keys, caller_data=dap_msg))
        return True

    # A scopes requests is a request for variables scopes visible
    # from a specified stack frame. That can require multiple chained
    # requests to the debuggee.
    # @return True if the sequence was successfully continued, False otherwise
    def __continue_dap_scopes_request(self, dap_request,
            debuggee_response=None):
        dap_args = dap_request[LITERAL.arguments]
        frame_id = dap_args[LITERAL.frameId]
        dclient = self.__debugger_client
        thread_index, frame_index, _ = dclient.decode_stack_ref_id(frame_id)
        get_child_keys = False
        if self.__check_debug(1):
            if debuggee_response:
                assert debuggee_response.request.caller_data == dap_request

        if not dclient.get_threads():
            dclient.send(DebuggerRequest_Threads(caller_data=dap_request))
        elif not dclient.get_thread_stacktrace(thread_index):
            dclient.send(DebuggerRequest_Stacktrace(
                thread_index, caller_data=dap_request))
        elif not dclient.get_thread_stack_variables(thread_index, frame_index,
                    variable_path=None, get_child_keys=get_child_keys):
            dclient.send(DebuggerRequest_Variables(thread_index, frame_index,
                variable_path=None, path_force_case_insensitive=None,
                get_child_keys=get_child_keys, caller_data=dap_request))
        else:
            self.__respond_to_dap_scopes_request(dap_request)
        return True


    ######################################################################
    # Responses to DAP messages.
    # ften, a DAP request requires multiple requests to the debuggee.
    # These methods are called when all of the necessary actions have
    # been completed.
    ######################################################################

    # REQUIRES: self.__debugger_client has cached the stack frame's variables
    def __respond_to_dap_variables_request(self, dap_msg):
        if self.__check_debug(3):
            print('debug:dap: respond_to_dap_variables_request()')

        dap_seq, dap_cmd_str = get_dap_seq_cmd(dap_msg)
        dap_args = dap_msg[LITERAL.arguments]
        want_child_keys = True               # always want child keys
        dclient = self.__debugger_client

        # REMIND: should check 'filter' argument in dap_msg

        parent_stack_ref_id = dap_args[LITERAL.variablesReference]
        thread_index, frame_index, var_path = dclient.decode_stack_ref_id(
                                                parent_stack_ref_id)

        indexed_count = 0
        dap_vars = list()
        vars_response = dclient.get_thread_stack_variables(thread_index,
                    frame_index, var_path, get_child_keys=want_child_keys)
        vars = vars_response.variables
        for var in vars:
            if not var.is_child_key:
                # Info about the parent object -- DAP only wants contents
                continue

            var_name = var.name
            if var_name:
                if self.__check_debug(1):
                    assert not indexed_count  # should not mix named,indexed
            else:
                # This is a bit dicey -- we assume that unnamed children
                # are numerically indexed (e.g., an array). Currently, the
                # BrightScript protocol always sends the full list of
                # children, so this should be OK.
                indexed_count += 1
                var_name = str(indexed_count-1)


            # If this item is a container, variableReferenceId must
            # uniquely identify this item, so that the DAP client can
            # query its contents. variableReferenceId must be included
            # with all variable responses and may not be null/None.
            var_value_str = var.get_value_str_for_user()
            var_type_str = var.get_type_name_for_user()
            vars_reference_id = 0
            if var.is_container_type:
                vars_reference_id = dclient.get_child_stack_ref_id(
                    parent_stack_ref_id, var_name)

            dap_vars.append(DAPVariable(var_name, var_value_str,
                    type_name=var_type_str,
                    variables_ref=vars_reference_id))
        self._send_dap_msg(DAPVariablesResponse(dap_seq, dap_cmd_str, dap_vars))

    # Currently, evaluate only supports variable values (e.g., for hovering
    # over a variable in the IDE UI).
    # ASSUMES: self.__debugger_client has cached the variables
    # @return None
    def __respond_to_dap_evaluate_request(self, dap_request,
            debuggee_response):
        dap_seq, dap_cmd_str, dap_args = get_dap_seq_cmd_args(dap_request)
        dclient = self.__debugger_client
        if self.__check_debug(1):
            assert dap_cmd_str == _DAP_CMD_EVALUATE

        var_path = debuggee_response.request.variable_path
        var_path_str = None
        if var_path and len(var_path):
            var_path_str = '.'.join(var_path)

        if debuggee_response.is_error:
            human_err_str = 'Error'
            if debuggee_response.err_code == ErrCode.INVALID_ARGS:
                err_path = var_path_str
                if not err_path and len(err_path):
                    err_path = '<empty>'
                human_err_str = 'Unknown variable: {}'.format(err_path)
            self._send_dap_msg(DAPErrorResponse(dap_seq, dap_cmd_str,
                    machine_err_str=LITERAL.error,
                    human_err_str=human_err_str))
            return

        if self.__check_debug(1):
            assert debuggee_response.request.cmd_code == CmdCode.VARIABLES
            assert len(debuggee_response.variables)

        # Only an eval of one variable is supported
        # See DAPEvaluateContext enum for description of eval context types
        dap_eval_ctx = DAPEvaluateContext.from_dap_str(
                            dap_args.get(LITERAL.context,None))
        include_parent_type = False
        if dap_eval_ctx == DAPEvaluateContext.REPL:
            include_parent_type = True
        bs_response = debuggee_response
        bs_request = bs_response.request
        bs_var = bs_response.variables[0]
        bs_var_type_str = bs_var.get_type_name_for_user()
        bs_var_value_str = debuggee_response.get_description_for_user(
            default_parent_name=var_path_str,
            include_parent_type=include_parent_type,
            include_children=False)
        stack_ref_id = 0
        if bs_var.is_container_type:
            stack_ref_id = dclient.get_stack_ref_id(bs_request.thread_index,
                bs_request.frame_index, bs_request.variable_path)
        self._send_dap_msg(DAPEvaluateResponse(dap_seq, dap_cmd_str,
            stack_ref_id, bs_var_value_str, bs_var_type_str))

    # A scopes requests is a request for variables scopes visible
    # from a specified stack frame.
    # REQUIRES: self.__debugger_client has cached the stack frame's variables
    def __respond_to_dap_scopes_request(self, dap_msg):
        if self.__check_debug(3):
            print('debug:dap: respond_to_dap_scopes_request()')
        dap_seq, dap_cmd_str = get_dap_seq_cmd(dap_msg)
        dap_args = dap_msg[LITERAL.arguments]
        frame_id = dap_args[LITERAL.frameId]
        dclient = self.__debugger_client
        thread_index, frame_index, _ = dclient.decode_stack_ref_id(frame_id)

        # REMIND: Check cached threads, stacktrace, and variables
        # responses in the dclient, to see if any of those requests
        # returned errors. If so, send a DAP error response.

        frame = dclient.get_thread_stacktrace(thread_index).frames[frame_index]
        assert frame
        vars = None
        vars_response = dclient.get_thread_stack_variables(thread_index,
                    frame_index, variable_path=None, get_child_keys=False)

        if vars_response and vars_response.is_error:
            msg = 'BrightScript protocol error: {}'.format(
                vars_response.err_code)
            with self.__dap_send_lock:  # re-entrant
                self._send_dap_msg(DAPErrorResponse(dap_seq, dap_cmd_str,
                    machine_err_str='error', human_err_str=msg))
                self.__send_dap_console_msg(msg)
            return True

        if vars_response:
            vars = vars_response.variables
        assert vars
        dap_file_path = self.__bs_to_dap_file_path(frame.file_path)

        # Currently, only one scope is supported per stack frame (the
        # 'local' scope), so frame_id can be used for scope_id.
        dap_scopes = list()
        dap_scopes.append(DAPScope(frame_id, 'Locals',
            frame.func_name, dap_file_path, frame.line_num, len(vars)))

        self._send_dap_msg(DAPScopesResponse(dap_seq, dap_cmd_str, dap_scopes))


    ####################################################################
    ###### END OF DAP (IDE) MESSAGE HANDLERS #####
    ####################################################################


    # Send the msg via the DAP protocol (the other end is typically an IDE)
    # @param dap_msg dict hierarchy with valid DAP message
    def _send_dap_msg(self, dap_msg):
        if self.__check_debug(9):
            do_print('debug: send_dap_msg(): msg={}'.format(
                to_debug_str(dap_msg)))
        try:
            msg_str = json.dumps(dap_msg, default=to_dap_dict)
            header_str = 'Content-Length: {}\r\n\r\n'.format(len(msg_str.encode('utf-8')))

            with self.__dap_send_lock:
                self.__fout.write(header_str)
                self.__fout.write(msg_str)
                # fout is typically stdout which may only autoflush on newline.
                # Cross-platform API to reduce buffering is inconsistent, so
                # flush each message manually.
                self.__fout.flush()
                self.__examine_sent_dap_msg(dap_msg)

            if self.__check_debug(3):
                do_print('debug:dap: dap_msg_sent: {}'.format(msg_str))

        except Exception as e:
            if self.__check_debug(1):
                traceback.print_exc()
            do_exit(1, 'Error sending via DAP: {}'.format(e))

    # Send a message to be displayed on the IDE's debug console
    def __send_dap_console_msg(self, msg):
        self._send_dap_msg(DAPOutputEvent(DAPOutputCategory.CONSOLE, msg))

    # Examine a message that was sent to the DAP client (typically an IDE),
    # and keeps track of what the IDE knows.
    # REQUIRES: __dap_send_lock is held
    # @param dap_msg is a python class instance (not json dicts)
    def __examine_sent_dap_msg(self, dap_msg):
        if self.__check_debug(8):
            do_print('debug:dap: examine_sent_dap_msg(),msg={}'.format(dap_msg))
        with self.__dap_send_lock:
            if isinstance(dap_msg, DAPThreadEvent):
                reason = dap_msg.body.reason
                thread_id = dap_msg.body.thread_id
                if reason == DAPThreadEventReason.EXITED.to_dap_str():
                    self.__dap_known_thread_ids.remove(thread_id)
                elif reason == DAPThreadEventReason.STARTED.to_dap_str():
                    self.__dap_known_thread_ids.add(thread_id)
            if isinstance(dap_msg, DAPThreadsResponse):
                for thread in dap_msg.body.threads:
                    thread_id = thread.id
                    self.__dap_known_thread_ids.add(thread_id)

    def __bs_to_dap_file_path(self, bs_file_path):
        dap_file_path = bs_file_path
        if dap_file_path.startswith('pkg:'):
            dap_file_path = dap_file_path[4:]
            # NB: paths from BrightScript debuggee always use '/' as file separator
            if len(dap_file_path) and dap_file_path.startswith('/'):
                dap_file_path = dap_file_path[1:]
            dap_file_path = os.path.join(self.__dap_project_root_dir_path,
                dap_file_path)
        return dap_file_path


    ####################################################################
    # UPDATES FROM DEBUGGEE
    ####################################################################

    def debuggee_update_received(self, update):
        assert update
        if self.__check_debug(5):
            do_print('debug:dap: debuggee_update_received():{}'.format(
                update.update_type.name))
        if self.__check_debug(3):
            if update.err_code != ErrCode.OK:
                msg = 'warn: error from debuggee: {}'.format(update)
                do_print(msg)

        handled = False

        if update.update_type == UpdateType.ALL_THREADS_STOPPED:
            handled = self.__handle_debuggee_event_all_threads_stopped(update)

        if update.update_type == UpdateType.THREAD_ATTACHED:
            handled = self.__handle_debuggee_event_thread_attached(update)

        elif update.update_type == UpdateType.COMMAND_RESPONSE:
            assert update.request
            success = not update.is_error
            debuggee_cmd_code = update.request.cmd_code
            assert debuggee_cmd_code
            dap_request = update.request.caller_data
            dap_seq = None
            dap_cmd_str = None
            if dap_request:
                dap_seq, dap_cmd_str = \
                            get_dap_seq_cmd(dap_request)
                assert dap_seq >= 0
                assert dap_cmd_str

            if debuggee_cmd_code == CmdCode.ADD_BREAKPOINTS:
                handled = self.__handle_debuggee_response_add_breakpoints(
                            update, dap_request)

            elif debuggee_cmd_code == CmdCode.CONTINUE:
                if dap_request:
                    self._send_dap_msg(DAPContinueResponse(dap_seq, dap_cmd_str,
                            True))
                handled = True

            elif debuggee_cmd_code == CmdCode.EXIT_CHANNEL:
                self._send_dap_msg(DAPResponse(dap_seq, dap_cmd_str, success))
                handled = True

            elif debuggee_cmd_code == CmdCode.STACKTRACE:
                handled = self.__handle_debuggee_response_stack_trace(update,
                            dap_request)

            elif debuggee_cmd_code == CmdCode.STEP:
                handled = self.__handle_debuggee_response_step(update,
                            dap_request)

            elif debuggee_cmd_code == CmdCode.STOP:
                handled = self.__handle_debuggee_response_stop(update,
                            dap_request)

            elif debuggee_cmd_code == CmdCode.THREADS:
                handled = self.__handle_debuggee_response_threads(update,
                            dap_request)

            elif debuggee_cmd_code == CmdCode.VARIABLES:
                handled = self.__handle_debuggee_response_variables(update,
                            dap_request)

        if self.__check_debug(1):
            assert handled, 'NOT HANDLED: {}'.format(update)

        return True

    # @return True if message handled (regardless of success), False otherwise
    def __handle_debuggee_event_all_threads_stopped(self, debuggee_event):
        if self.__check_debug(2):
            do_print('debug:dap: handle_debuggee_event_all_threads_stopped()')
        if self.__check_debug(1):
            assert debuggee_event.primary_thread_index >= 0

        # __debuggee_all_stopped_received is only accessed on this thread
        dclient = self.__debugger_client
        first_all_stopped = not self.__debuggee_all_stopped_received
        self.__debuggee_all_stopped_received = True
        if first_all_stopped and \
            dclient.has_feature(ProtocolFeature.STOP_ON_LAUNCH_ALWAYS):
            # Launching -- we are now ready for DAP configuration commands
            self._send_dap_msg(DAPInitializedEvent())
        else:
            # REMIND: need to get thread info for stop reason
            evt = debuggee_event
            thread_idx = evt.primary_thread_index
            self._send_dap_msg(DAPStoppedEvent(
                DAPStopReason.ERROR, evt.stop_reason_detail, thread_idx))
        return True

    # @return True if message handled (regardless of success), False otherwise
    def __handle_debuggee_event_thread_attached(self, debuggee_event):
        if self.__check_debug(2):
            do_print('debug:dap: handle_debuggee_event_thread_attached()')
        thread_id = debuggee_event.thread_index

        with self.__self_lock:
            if self.__ignore_next_bs_attached_message:
                if self.__check_debug(5):
                    print('debug:dap: ignoring thread_attached msg'
                        ' (bug workaround)')
                self.__ignore_next_bs_attached_message = False
                return True

        # The BrightScript debugger does not notify when threads start or
        # exit. We just found out about this thread, so send two DAP
        # events: thread entered, thread stopped
        self._send_dap_msg(DAPThreadEvent(thread_id,
                DAPThreadEventReason.STARTED))
        self._send_dap_msg(DAPStoppedEvent(DAPStopReason.PAUSE, 'Thread entered',
            thread_id))
        return True

    # @return True if message handled (regardless of success), False otherwise
    def __handle_debuggee_response_add_breakpoints(self, debuggee_response,
            dap_request):
        assert dap_request[LITERAL.command] == _DAP_CMD_SET_BREAKPOINTS
        dap_seq, dap_cmd_str = get_dap_seq_cmd(dap_request)
        if debuggee_response.err_code != ErrCode.OK:
            dap_msg = \
                DAPSetBreakpointsResponse(dap_seq, dap_cmd_str, False, None)
        else:
            dap_breaks = list()
            bs_request = debuggee_response.request
            for break_idx in range(0, len(debuggee_response.breakpoints)):
                # The response from the debuggee (potentially an inexpensive
                # low-performance device) is intentionally sparse. It
                # responds with an ID for each breakpoint in the request.
                bs_request_break = bs_request.breakpoints[break_idx]
                bs_response_break = debuggee_response.breakpoints[break_idx]
                line_num = bs_request_break.line_num
                file_name = os.path.basename(bs_request_break.file_path)
                file_path = bs_request_break.file_path
                dap_source_spec = DAPSource(file_name, file_path)
                dap_break = DAPBreakpoint(dap_source_spec, line_num)
                dap_break.id = bs_response_break.remote_id
                dap_break.verified = True   # False may be better
                dap_breaks.append(dap_break)
            dap_msg = DAPSetBreakpointsResponse(
                dap_seq, dap_cmd_str, True, dap_breaks)

        self._send_dap_msg(dap_msg)
        return True

    # @return True if message handled (regardless of success), False otherwise
    def __handle_debuggee_response_stack_trace(self, debuggee_response,
            dap_request):
        dap_seq, dap_cmd_str = get_dap_seq_cmd(dap_request)
        dclient = self.__debugger_client
        handled = False

        if dap_request:
            if debuggee_response.is_error:
                with self.__dap_send_lock:   # re-entrant
                    msg = 'BrightScript protocol error'\
                        ', dap_cmd={}, bs_cmd={}, bs_err={}'.format(
                            dap_cmd_str,
                            debuggee_response.request.cmd_code.to_user_str(),
                            debuggee_response.err_code.to_user_str())
                    self._send_dap_msg(DAPErrorResponse(dap_seq, dap_cmd_str,
                        LITERAL.error, msg))
                    self.__send_dap_console_msg(msg)
                return True

        if dap_cmd_str == _DAP_CMD_SCOPES:
            handled = self.__continue_dap_scopes_request(dap_request,
                    debuggee_response)

        elif dap_cmd_str == _DAP_CMD_STACK_TRACE:
            bs_request = debuggee_response.request
            thread_index = bs_request.thread_index
            dap_frames = list()
            frame_index = -1
            for bs_frame in debuggee_response.frames:
                frame_index += 1
                frame_id = dclient.get_stack_ref_id(thread_index, frame_index)
                dap_file_path = self.__bs_to_dap_file_path(bs_frame.file_path)
                dap_frames.append(DAPStackFrame(frame_id, bs_frame.func_name,
                    dap_file_path, bs_frame.line_num))
            self._send_dap_msg(
                DAPStackTraceResponse(dap_seq, dap_cmd_str, dap_frames))
            handled = True

        return handled

    # @return True if message handled (regardless of success), False otherwise
    def __handle_debuggee_response_step(self, debuggee_response,
            dap_request):
        dap_seq, dap_cmd_str, dap_args = get_dap_seq_cmd_args(dap_request)
        if self.__check_debug(1):
            assert dap_args
            assert dap_cmd_str == _DAP_CMD_NEXT or \
                    dap_cmd_str == _DAP_CMD_STEP_IN or \
                        dap_cmd_str == _DAP_CMD_STEP_OUT
            assert dap_args[LITERAL.threadId] >= 0 # pylint: disable=unsubscriptable-object

        thread_index = dap_args[LITERAL.threadId] # pylint: disable=unsubscriptable-object

        # BrightScript debugger fully executes the step command, before
        # sending one message: success/fail
        if debuggee_response.is_error:
            self._send_dap_msg(DAPErrorResponse(dap_seq, dap_cmd_str,
                machine_err_str='failed',
                human_err_str='Command failed: {}'. format(dap_cmd_str)))
        else:
            self._send_dap_msg(DAPStoppedEvent(DAPStopReason.STEP,
                dap_cmd_str, thread_index))
        return True

    # @return True if message handled (regardless of success), False otherwise
    def __handle_debuggee_response_stop(self, debuggee_response,
            dap_request):
        handled = False

        if dap_request:
            thread_id = 0
            dap_args = dap_request.get(LITERAL.arguments,None)
            if dap_args:
                thread_id = dap_args.get(LITERAL.threadId,thread_id)
            reason = DAPStopReason.PAUSE      # Will change with STEP command
            description = 'Paused'            # Displayed to user
            self._send_dap_msg(DAPStoppedEvent(reason, description,
                        thread_id))
            handled = True

        return handled


    # @return True if message handled (regardless of success), False otherwise
    def __handle_debuggee_response_threads(self, debuggee_response,
            dap_request):
        handled = False
        dap_seq, dap_cmd_str = get_dap_seq_cmd(dap_request)
        if self.__check_debug(5):
            do_print('debug:dap: handle_debuggee_response_threads()')
        if self.__check_debug(1):
            assert debuggee_response.request.cmd_code == CmdCode.THREADS
            assert dap_cmd_str == _DAP_CMD_THREADS or \
                    dap_cmd_str == _DAP_CMD_SCOPES

        # There is a difference here, between DAP and Roku. For
        # performance reasons, a Roku device will only provide
        # thread info when all threads are stopped. Therefore,
        # no persistent thread IDs are assigned and IDs are only
        # valid when all threads are stopped.

        if dap_cmd_str == _DAP_CMD_SCOPES:
            handled = self.__continue_dap_scopes_request(dap_request,
                debuggee_response)
        elif dap_cmd_str == _DAP_CMD_THREADS:
            dap_threads = list()
            next_thread_id = 0
            for _ in debuggee_response.threads:
                thread_id = next_thread_id
                next_thread_id += 1
                dap_threads.append(DAPThread(thread_id, str(thread_id)))
            self._send_dap_msg(DAPThreadsResponse(dap_seq, dap_cmd_str,
                dap_threads))
            handled = True

        return handled

    # @return True if message handled (regardless of success), False otherwise
    def __handle_debuggee_response_variables(self, debuggee_response,
            dap_request):
        handled = False
        if dap_request:
            _, dap_cmd_str = get_dap_seq_cmd(dap_request)
            if dap_cmd_str == _DAP_CMD_EVALUATE:
                self.__respond_to_dap_evaluate_request(dap_request,
                    debuggee_response)
                handled = True
            if dap_cmd_str == _DAP_CMD_SCOPES:
                self.__respond_to_dap_scopes_request(dap_request)
                handled = True
            elif dap_cmd_str == _DAP_CMD_VARIABLES:
                self.__respond_to_dap_variables_request(dap_request)
                handled = True

        return handled

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level

