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
# File: CommandLineInterface.py
# Requires python 3.5.3 or later
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


import copy, enum, re, sys, queue, threading, time, traceback

from .DebuggerListener import DebuggerControlListener
from .DebuggerRequest import CmdCode
from .DebuggerRequest import DebuggerRequest
from .DebuggerRequest import DebuggerRequest_Continue
from .DebuggerRequest import DebuggerRequest_ExitChannel
from .DebuggerRequest import DebuggerRequest_Stacktrace
from .DebuggerRequest import DebuggerRequest_Step
from .DebuggerRequest import DebuggerRequest_Stop
from .DebuggerRequest import DebuggerRequest_Threads
from .DebuggerRequest import DebuggerRequest_Variables
from .DebuggerRequest import StepType
from .DebuggerResponse import DebuggerUpdate
from .DebuggerResponse import ErrCode
from .DebuggerResponse import UpdateType
from .DebuggerResponse import ThreadStopReason
from .DebuggerResponse import VariableType
from .DebuggerResponse import get_stop_reason_str_for_user
from .SourceCodeInspector import SourceCodeInspector
from .Verbosity import Verbosity

global gMain

@enum.unique
class _TargetState(enum.IntEnum):
    UNKNOWN = 0,
    RUNNING = 1,
    STOPPED = 2,
    STEPPING = 3,
    TERMINATED = 4,

_BASE_PROMPT = 'RRDB> '

_THREAD_HDR_FMT = '{:<2s}   {:<40s}{}'
_THREAD_LINE_FMT = '{:2d}{:1s} {:<40s} {}'

_LITERAL_PRIMARY_THREAD_INDEX = 'primary_thread_index'
_LITERAL_THREAD_INDEX = 'thread_index'

# We add a dict to caller_data for requests, that only this module
# accesses. These are the keys in that dict.
@enum.unique
class CallerKey(enum.IntEnum):
    # Skip 0 because it is too often confused with None
    BACKTRACE               = 1,
    LISTING_FUNCTION        = 2,
    LISTING_THREADS         = 3,
    SELECTING_THREAD        = 4,
    STOPPING                = 5,
    THREAD_ATTACHED         = 6,


class CmdSpec(object):
    # @param is_documented True if the command should show up in help
    def __init__(self, cmd_str, short_aliases, is_documented, has_args,
                    func, help_text):
        self.is_documented = is_documented  # if True, show in help
        self.cmd_str = cmd_str
        self.short_aliases = short_aliases
        self.has_args = has_args
        self.func = func
        self.help_text = help_text

    def __str__(self):
        short_aliases = None
        if self.short_aliases and len(self.short_aliases):
            short_aliases = '['
            for alias in self.short_aliases:
                if short_aliases[len(short_aliases)-1] != '[':
                    short_aliases += ','
                short_aliases += alias
            short_aliases += ']'
        return "CmdSpec[str='{}',shortaliases='{}',isdocumented={},help='{}']".format(
            self.cmd_str, short_aliases,
            self.is_documented, self.help_text)

    # get string for display to the user
    def get_display_str(self):
        s = self.cmd_str
        if self.short_aliases and len(self.short_aliases):
            for alias in self.short_aliases:
                s = s + '|' + alias
        return s

# Command-line interface, to handle all user requests
class CommandLineInterface(object):
    def __init__(self, debugger_client, channel_zip_file_path):
        global gMain
        gMain = sys.modules['__main__'].gMain
        self.__debug = max(gMain.gDebugLevel, 0)
        self.__verbosity = gMain.verbosity
        self.__debugger_client = debugger_client
        self.__channel_sip_file_path = channel_zip_file_path
        self.__protocol_version = debugger_client.protocol_version
        self.__in_file = sys.stdin
        self.__out_file = sys.stdout

        # guard primarily for asynchronous shutdown requests
        self.__self_state_lock = threading.Lock()
        self.__is_running = False
        self.__is_shut_down = False

        self.__is_connected = False
        self.__target_state = _TargetState.UNKNOWN # use accessor methods
        self.__target_state_lock = threading.Lock()
        self.__threads = None          # DebuggerReponse_Threads.ThreadInfo

        # "sel" = selected
        self.__sel_thread_index = None          # Index of selected thread
        self.__sel_thread_stack_index = None
        self.__sel_thread_stack_info = None     # DebuggerResponse_Stacktrace
        self.__sel_thread_vars = None           # DebuggerResponse_Variables

        self.__src_inspector = SourceCodeInspector(self.__channel_sip_file_path)
        self.__input_cond_var = threading.Condition()  # notified on user or debugger event
        self.__user_input_queue = queue.Queue()        # Queue is thread-safe
        self.__debugger_update_queue = queue.Queue()   # Queue is thread-safe
        self.__shutdown_trigger = False             # Latched to True to shut down

        self.__all_cmds = self.__get_cmd_spec_list()

        # Start the listener
        self.__debugger_listener = DebuggerControlListener(self.__debugger_client, self)

    def interact(self):
        if self.__debug >= 1:
            print('debug: cli.interact -- start')
        fin = self.__in_file
        fout = self.__out_file

        with self.__self_state_lock:
            self.__is_running = True
        self.__set_target_state(_TargetState.RUNNING)
        self.__is_connected = True

        self.__print_intro()
        input_processor = UserInputProcessor(self, fin, fout)

        done = False
        try:
            while not done:

                if self.__shutdown_trigger:
                    done = True
                    break

                ############################################################
                # Wait for pending requests to complete
                # Items are removed from the pending queue when matching
                # responses are received, so wait for all of those responses.
                # REMIND: we should have a timeout on this, in case the device
                #         is disconnected (or crashes)
                ###########################################################
                with self.__input_cond_var:
                    while (not done) and self.__debugger_listener.has_pending_request():
                        if self.__shutdown_trigger:
                            done = True
                            break
                        if self.__debug >= 5:
                            print('debug: cli: wait for {} pending requests...' \
                                .format(self.__debugger_listener.\
                                    get_pending_request_count()))

                        # On some platforms (e.g., Windows 10), a signal (e.g., ^C) will
                        # not interrupt a wait(), so we poll here to allow signal handling
                        self.__input_cond_var.wait(1.0)
                if self.__debug >= 5:
                    print('debug: pending requests: {}'.format(
                        self.__debugger_listener.get_pending_request_count()))


                ##########################################################
                # Wait for user input or debugger updates
                ##########################################################
                with self.__input_cond_var:
                    need_prompt_on_empty = True
                    while (not done) and \
                        self.__user_input_queue.empty() and \
                                self.__debugger_update_queue.empty():
                        if self.__shutdown_trigger:
                            done = True
                            break
                        if need_prompt_on_empty:
                            input_processor.set_prompt_lines(
                                [self.__get_status_line(), _BASE_PROMPT])
                            input_processor.print_prompt()
                            need_prompt_on_empty = False

                        # On some platforms (e.g., Windows 10), a signal (e.g., ^C) will
                        # not interrupt a wait(), so we poll here to allow signal handling
                        self.__input_cond_var.wait(1.0)


                #########################################################
                # Process pending updates and user input
                # We know something is pending
                #########################################################

                # queue.Queue is thread-safe. Nothing else is removing elements
                # from the queues, so no additional synchronization is necessary.

                # Process debugger updates first
                while (not done) and (not self.__debugger_update_queue.empty()):
                    self.__process_debugger_update(self.__debugger_update_queue.get())

                # Process user input
                while (not done) and (not self.__user_input_queue.empty()):
                    if self.__shutdown_trigger:
                        done = True
                        break
                    self.__handle_cmd_line(self.__user_input_queue.get())
            # end: while not done
        except:
            sys.stdout.flush()
            traceback.print_exc(file=sys.stderr)
            print('INTERNAL ERROR: Command processing failed with exception',
                    file=sys.stderr)

        with self.__self_state_lock:
            self.__is_running = False

        ############################################################
        # Shut down
        ############################################################
        if self.__debug >= 1:
            print('debug: cli.interact() exited loop, shutting down...')

        # Ignore exceptions during shutdown, because there is nothing
        # we can do about it.
        try:
            # This process is unlikely to be alive when the target responds
            # to the exit request. But, if it is alive, make sure the request
            # is in the pending queue to avoid crashes and assertion.
            cmd = DebuggerRequest_ExitChannel()
            self.__debugger_listener.add_pending_request(cmd)
            cmd.send(self.__debugger_client)
        except: pass
        try:
            self.__debugger_client.shutdown()
        except: pass

        with self.__self_state_lock:
            self.__is_shut_down = True
        if self.__debug >= 1:
            print('debug: cli.interact() -- shut down -- done')
    # end: while not done

    # Can be invoked by any thread to stop the debug target, which will
    # begin a debugging session.
    # E.g., this is called from a signal handler when the user presses ^C
    def stop_target(self):
        self.__queue_cmd('stop')

    # Begins the shutdown sequence, and returns immediately
    # The shutdown sequence sends termination requests to the target,
    # closes I/O channels, stops processing commands, and returns from
    # interact().
    # May be called from any thread
    def shutdown(self):
        with self.__input_cond_var:
            # lock ordering : 1)input_cond_var's lock, 2)__self_state_lock
            with self.__self_state_lock:
                if not self.__is_running:
                    return
            if (self.__debug >= 2):
                print('debug: cli.shutdown(): triggering shutdown')
            self.__shutdown_trigger = True
            self.__input_cond_var.notify_all()

        # This should block until interact() is done
        time.sleep(3)

        # # Now block until final exit command has been sent to target
        # while not self.__is_shut_down:
        #     print('debug: cli.shutdown(): waiting for shutdown...')
        #     time.sleep(1)

    def __get_cmd_spec_list(self):
        proto_ver_major = self.__protocol_version[0]
        proto_ver_minor = self.__protocol_version[1]
        proto_ver_patchlevel = self.__protocol_version[2]
        if self.__debug >= 1:
            print('debug: get_cmd_spec_list(),protocolver={}.{}.{}'.format(
                    proto_ver_major, proto_ver_minor, proto_ver_patchlevel))


        ###############################################
        # Determine which commands to include
        ###############################################
        has_step_commands = False

        if self.__verbosity >= Verbosity.HIGH:
            print('info: enabling commands for protocol 1.0')

        # Enable commands for version 1.1
        if (proto_ver_major >= 2) or \
            ((proto_ver_major == 1) and (proto_ver_minor >= 1)):
            if self.__verbosity >= Verbosity.HIGH:
                print('info: enabling commands for protocol 1.1')
            has_step_commands = True


        ###############################################
        # Create list of commands
        ###############################################
        cmds = list()

        # Default 1.0 commands

        # CmdSpec(cmd_str, short_aliases, is_documented, has_args,
        #         function, help_text)
        cmds.extend([
            CmdSpec('backtrace', ['bt'], True, False,
                    self.__handle_cmd_backtrace,
                    'Print stack backtrace of selected thread'),
            CmdSpec('continue', None, True, False,
                    self.__handle_cmd_continue,
                    'Continue all threads'),
            CmdSpec('down', ['d'], True, False,
                    self.__handle_cmd_down,
                    'Move one frame down the function call stack'),
            CmdSpec('help', None, True, False,
                    self.__handle_cmd_help,
                    'Print this help'),
            CmdSpec('list', None, True, False,
                    self.__handle_cmd_list,
                    'List current function'),
            CmdSpec('print', None, True, True,
                    self.__handle_cmd_print,
                    'Print a variable\'s value'),
            CmdSpec('quit', None, True, False,
                    self.__handle_cmd_quit,
                    'Quit debugger and terminate channel'),
            CmdSpec('status', None, True, False,
                    self.__handle_cmd_status,
                    'Show debugger status'),
            CmdSpec('stop', None, True, False,
                    self.__handle_cmd_stop,
                    'Stop all threads'),
            CmdSpec('thread', ['th'], True, True,
                    self.__handle_cmd_thread,
                    'Select a thread for inspection'),
            CmdSpec('threads', ['ths'], True, False,
                    self.__handle_cmd_threads,
                    'Show all threads'),
            CmdSpec('up', ['u'], True, False,
                    self.__handle_cmd_up,
                    'Move one frame up the function call stack'),
            CmdSpec('vars', None, True, False,
                    self.__handle_cmd_vars,
                    'Show variables in the current scope'),
        ])

        # Add version-dependent commands

        if has_step_commands:
            cmds.extend([
                CmdSpec('over', ['v'], True, False,
                        self.__handle_cmd_over,
                        'Step one program statement'),
                CmdSpec('out', ['o'], True, False,
                        self.__handle_cmd_out,
                        'Step one program statement'),
                CmdSpec('step', ['s','t'], True, False,
                        self.__handle_cmd_step,
                        'Step one program statement'),
            ])

        # sort the whole kit and kaboodle
        cmds.sort(key=lambda cmd : cmd.cmd_str)
        return cmds

    # Invoked on a random thread for various reasons, such
    # as a ^C which sends a stop command
    def __queue_cmd(self, cmdStr):
        if self.__debug >= 1:
            print('debug: __queue_cmd({})'.format(cmdStr))
        with self.__self_state_lock:
            if self.__is_running:
                    self.__user_input_queue.put(cmdStr)
            with self.__input_cond_var:
                self.__input_cond_var.notify_all()

    # Prints the one-time intro message
    def __print_intro(self):
        fout = self.__out_file
        print('',file=fout)
        print('Roku remote debugger', file=fout)
        self.__print_use_help_for_help()

    # Prints the info that appears before each command prompt
    def __print_debugging_status(self):
        fout = self.__out_file
        print(file=fout)
        self.__print_threads()

    # Updates the selected thread index and stack frame within that thread's
    # call stack, clearing out any cached data if the selection has changed.
    # If nothing changes, nothing is done.
    # If stack_index is None, sets the selected stack index to be the bottom-
    # most in the call stack.
    # NB: "sel" = "selected"
    # @param ok_to_send if True may send a request to the debugger
    # @param caller_data additional keys added to request, if a request is sent
    # @return True if data is valid upon return, False if a request was sent
    #         to the debugger and a response is required
    def __set_sel_thread(self,
            thread_index, stack_index=None, ok_to_send=False, caller_data=None):
        if self.__debug >= 5:
            print('debug: setselthread(thridx={},stkidx={},ok_to_send={})'.format(
                thread_index, stack_index, ok_to_send))
        prev_thread_index = self.__sel_thread_index
        prev_stack_index = self.__sel_thread_stack_index

        if not stack_index:
            num_frames = 0
            if self.__sel_thread_stack_info:
                num_frames = self.__sel_thread_stack_info.get_num_frames()
            if num_frames:
                stack_index = num_frames - 1

        self.__sel_thread_index = thread_index
        self.__sel_thread_stack_index = stack_index

        # Clear out any obsolete stored data
        need_new_stack_trace = False
        if prev_thread_index != thread_index:
            self.__sel_thread_stack_info = None
            self.__sel_thread_vars = None
            if ok_to_send:
                need_new_stack_trace = True
        elif prev_stack_index != stack_index:
            # stack trace is still valid
            self.__sel_thread_vars = None

        if need_new_stack_trace:
            cmd_caller_data = {CallerKey.SELECTING_THREAD:True}
            if caller_data:
                cmd_caller_data.update(caller_data)
            cmd = DebuggerRequest_Stacktrace(
                self.__sel_thread_index, cmd_caller_data)
            self.__debugger_listener.add_pending_request(cmd)
            cmd.send(self.__debugger_client)

        if (self.__debug >= 4):
            print('debug: setselthread done, thridx={},stkidx={} returns {}'.format(
                self.__sel_thread_index, self.__sel_thread_stack_index,
                (not need_new_stack_trace)))

        # Return True if the data is already valid, False if we are waiting
        return not need_new_stack_trace

    # Resets/clears all data associated with the selected thread,
    # including stack trace, stack index, and local variables.
    # NB: "sel" = "selected"
    def __reset_sel_thread(self, new_thread_index=None):
        self.__sel_thread_index = new_thread_index
        self.__sel_thread_stack_index = None
        self.__sel_thread_stack_info = None
        self.__sel_thread_vars = None

    # Does a list of source code, targeted at the function that contains
    # the current thread and stack frame.
    # @param stop_reason enum DebuggerResponse.ThreadStopReason, may be None
    # @param stop_reason_detail str, may be None
    def __list_selected_function(self):
        # "sel" = selected
        fout = self.__out_file

        # 0 is valid (but None is not)
        if (self.__sel_thread_index == None) or \
            (self.__sel_thread_stack_index == None):
                print('No function selected')
                return

        thread_index = self.__sel_thread_index
        stack_index = self.__sel_thread_stack_index
        stack_frames = self.__sel_thread_stack_info.get_frames()
        thread_info = self.__threads[thread_index]

        stop_reason = thread_info.stop_reason
        stop_reason_detail = thread_info.stop_reason_detail
        sel_frame = stack_frames[stack_index]   # "sel" = selected
        file_name = sel_frame.file_name
        line_start = max(0, sel_frame.line_num - 7)
        line_end = sel_frame.line_num + 14
        lines = self.__src_inspector.get_source_lines(
                                        file_name, line_start, line_end)

        # Mark all Program Counters in the call stack
        tail_pc_line_num = stack_frames[len(stack_frames)-1].line_num
        pc_line_nums = set([tail_pc_line_num])
        for i_frame in range(len(stack_frames)):
            one_frame = stack_frames[i_frame]
            if one_frame.file_name == sel_frame.file_name:
                pc_line_nums.add(one_frame.line_num)

        if not (lines and len(lines)):
            print('Could not find source lines: {}:{}-{}'.format(
                file_name, line_start, line_end),
                file=fout)
        else:
            print('Current Function:', file=fout)
            for line in lines:
                is_error_line = False
                if line.line_number in pc_line_nums:
                    if line.line_number == tail_pc_line_num:
                        pc_str = '*'
                        if stop_reason and \
                                (stop_reason != ThreadStopReason.BREAK):
                            is_error_line = True
                    else:
                        pc_str = '>'
                else:
                    pc_str = ' '
                print('{:03d}:{} {}'.format(
                    line.line_number, pc_str, line.text), file=fout)

                if is_error_line:
                    print('', file=fout)
                    print(get_stop_reason_str_for_user(
                            stop_reason, stop_reason_detail),
                            file=fout)
                    print('')

    # update is a reponse to a CmdCode.STACKTRACE request
    def __print_stack_trace(self, update, last_frame_index=None):
        assert update
        frames = update.get_frames()
        if last_frame_index == None:  # 0 is a valid value
            last_frame_index = len(frames)-1
        print('Backtrace:')
        for frame_index in range(last_frame_index, -1, -1):
            frame = frames[frame_index]
            self.__print_stack_frame(frame, frame_index)

    def __print_stack_frame(self, frame, frame_index):
        fout = self.__out_file
        print('#{:<2d} Function {}'.format(
            frame_index, frame.func_name), file=fout)
        print('   file/line: {}({})'.format(
            frame.file_name, frame.line_num), file=fout)

    # "sel" = selected by the user
    def __print_sel_stack_trace(self):
        if self.__debug >= 3:
            print('debug: __print_sel_stack_trace(), selstkinfo={}, '
                    'selstkidx={}'.format(
                        self.__sel_thread_stack_info,
                        self.__sel_thread_stack_index))
        self.__print_stack_trace(
            self.__sel_thread_stack_info, self.__sel_thread_stack_index)

    def __print_sel_stack_frame(self):
        stack_index = self.__sel_thread_stack_index
        frame = self.__sel_thread_stack_info.get_frames()[stack_index]
        self.__print_stack_frame(frame, stack_index)

    def __print_threads(self):
        if self.__debug >= 3:
            print('debug: print_threads(),selthridx={},selstkidx={}'.format(
                self.__sel_thread_index, self.__sel_thread_stack_index))
        fout = self.__out_file
        threads = self.__threads
        if not (threads and len(threads)):
            print('No threads', file=fout)
        else:
            print('Threads:')
            print(_THREAD_HDR_FMT.format(
                'ID', 'Location', 'Source Code'), file=fout)
            for iThread in range(len(threads)):
                self.__print_thread(threads[iThread], iThread)
        print(' *selected', file=fout)
        print(file=fout)

    def __print_thread(self, thread_info, thread_index):
        fout = self.__out_file
        thread = thread_info
        src_line_info = self.__src_inspector.get_source_line(
                                    thread.file_name, thread.line_num)
        if src_line_info and src_line_info.text:
            src_line = src_line_info.text.strip()
        elif thread.code_snippet:
            src_line = thread.code_snippet.strip()
        else:
            src_line = "??"
        file_info = '{}({})'.format(thread.file_name, thread.line_num)
        primary = ' '
        if thread_index == self.__sel_thread_index:
            primary = '*'
        print(_THREAD_LINE_FMT.format(
                    thread_index, primary, file_info, src_line),
                    file=fout)

    def __print_sel_thread(self):
        thread_index = self.__sel_thread_index
        thread = self.__threads[thread_index]
        self.__print_thread(thread, thread_index)

    def __print_selected_variables(self):
        return self.__print_all_variables(self.__sel_thread_vars)

    # depth:int specifies indent
    def __print_all_variables(self, update):
        fout = self.__out_file

        if not update:
            print('No Local Variables')
            return
        print('Local Variables:')

        vars = update.variables
        if not (vars and len(vars)):
            print('    <NONE>', file=fout)
        else:
            for var in vars:
                self.__print_variable(var, 0)

    # depth specifies indent
    # name_width:int specifies minimum characters used for variable name
    def __print_variable(self, var_info, depth, name_width_min=16):
        if self.__debug >= 5:
            print('debug: __print_variable(depth={},namewidth={},var=[{}])'.format(
                depth, name_width_min, var_info))
        fout = self.__out_file
        indent = build_indent_str(depth)

        var = var_info
        var_name = var.name
        if var_name == None:
            var_name = ''
        if name_width_min >= 1:
            fmt = '{{}}{{:{}s}} {{}}'.format(name_width_min)
        else:
            fmt = '{}{} {}'
        s = fmt.format(indent, var_name, var.get_type_name_for_user())
        if var.ref_count != None: # 0 is valid
            s += ' refcnt={}'.format(var.ref_count)
        if var.element_count != None:  # 0 is valid
            s += ' count:{}'.format(var.element_count)
        if var.value != None:  # 0 is valid
            s += ' val:{}'.format(var.get_value_str_for_user())
        print(s, file=fout)

    def __print_thread_attached_message(
        self, thread_attached_update, threads_update):
        fout = self.__out_file
        thread_index = thread_attached_update.thread_index
        thread = None
        if thread_index < len(threads_update.threads):
            thread = threads_update.threads[thread_index]
        print('', file=fout)
        print('Thread attached: ', end='', file=fout)
        if thread:
            self.__print_thread(thread, thread_index)
        else:
            print('<UNKNOWN>')
        print('', file=fout)

    # Called after a crash or stop, to display all relevant information
    # to the user (e.g., threads, stack trace, variables).
    # REQUIRES: All necessary information has been collected
    def __print_crash_dump(self):
        fout = self.__out_file
        self.__list_selected_function()
        print('', file=fout)
        self.__print_sel_stack_trace()
        print('', file=fout)
        self.__print_selected_variables()
        print('', file=fout)
        self.__print_threads()

    # Gets one-line status to present to user
    def __get_status_line(self):
        return 'Channel is {}, {}'.format(
                    self.__get_target_state().name.lower(),
                    ["disconnected","connected"][int(self.__is_connected)])


    ####################################################################
    #
    # Process user commands
    #
    ####################################################################

    # Prints a message to the user, if the debug target is not stopped
    # @return True if stopped, False otherwise
    def __check_stopped(self):
        if self.__get_target_state() != _TargetState.STOPPED:
            print('ERROR: Target not stopped (use "stop")')
            return False
        return True

    # param cmd_line str
    # @return void
    def __handle_cmd_line(self, cmd_line):
        if self.__debug >= 9:
            print('debug: cli.__handle_cmd_line({})'.format(cmd_line))
        cmd_line = cmd_line.strip()
        cmdParts = re.split('\\s', cmd_line, maxsplit=1)
        cmdPrefix = cmdParts[0].strip()
        cmdArgStr = None
        if len(cmdParts) >= 2:
            cmdArgStr = cmdParts[1].strip()
        cmdSpec = self.__match_command(cmdPrefix)
        if (self.__debug >= 5) and len(cmdPrefix):
            print('debug: prefix={},cmd={}'.format(cmdPrefix, cmdSpec))
        if cmdSpec:
            if cmdArgStr and len(cmdArgStr) and not cmdSpec.has_args:
                print('error: args provided for command that takes none: {}'.\
                    format(cmdSpec.cmd_str))
                return
            ok = cmdSpec.func(cmdSpec, cmdArgStr)
            assert (ok != None), \
                    'cmd handler did not return a value for {}'.format(\
                        cmdSpec)
            done = not ok
            if done:
                if self.__debug >= 1:
                    print('debug: EXITING BECAUSE CMD HANDLER SAYS SO: {}'.\
                        format(cmdSpec))
                do_exit(0)

    # Get the command that starts with cmdPrefix, if there
    # is exactly one that matches. Returns None if cmdPrefix
    # matches none or is ambiguous.
    # @return CmdSpec or None
    def __match_command(self, cmd_prefix):
        if not cmd_prefix:
            return None

        found_cmds = []  # CmdSpec(s)
        for cmd in self.__all_cmds:
            cmd_str = cmd.cmd_str
            cmd_short_aliases = cmd.short_aliases
            if cmd_short_aliases == None:
                cmd_short_aliases = []

            # Look for an exact match
            found_exact = False
            if cmd_prefix == cmd_str:
                found_exact = True
                found_cmds = [cmd]
            # Short aliases always require an exact match
            for short_alias in cmd_short_aliases:
                if cmd_prefix == short_alias:
                    found_exact = True
                    found_cmds = [cmd]
            if found_exact:
                break

            # Look for an abbreviation (prefix match)
            # Undocumented commands cannot be abbreviated
            if not cmd.is_documented:
                continue

            if ((len(cmd_prefix) <= len(cmd_str)) and
                (cmd_prefix == cmd_str[0:len(cmd_prefix)])):
                    found_cmds.append(cmd)

        found = None
        if len(found_cmds) < 1:
            print('ERROR: No such command: {}'.format(cmd_prefix),
                    file=self.__out_file)
            self.__print_use_help_for_help()
        elif len(found_cmds) > 1:
            dups = ''
            for cmd_spec in found_cmds:
                if len(dups):
                    dups = dups + ','
                dups = dups + cmd_spec.get_display_str()
            print('ERROR: Ambiguous command abbreviation: {} ({})'.format(
                    cmd_prefix, dups, file=self.__out_file))
            self.__print_use_help_for_help()
        else:
            found = found_cmds[0]
        if self.__debug >= 5:
            print('debug: cli.__match_command({}) -> {}'.format(
                cmd_prefix, found))
        return found

    # @return true on success, false otherwise
    def __handle_cmd_backtrace(self, cmd_spec, args_str):
        if self.__debug >= 1:
            print('debug: cli.__handle_cmd_backtrace()')
        if not self.__check_stopped():
            return True
        if not (self.__threads and len(self.__threads)):
            print('No threads')
            return True
        elif not self.__sel_thread_stack_info:
            caller_data = {CallerKey.BACKTRACE : True}
            cmd = DebuggerRequest_Stacktrace(
                        self.__sel_thread_index, caller_data=caller_data)
            self.__debugger_listener.add_pending_request(cmd)
            cmd.send(self.__debugger_client)
        else:
            self.__print_sel_stack_trace()
        return True

    # @return true on success, false otherwise
    def __handle_cmd_continue(self, cmd_spec, args_str):
        if self.__debug >= 1:
            print('debug: cli.__handle_cmd_continue()')
        if not self.__check_stopped():
            return True
        cmd = DebuggerRequest_Continue()
        self.__debugger_listener.add_pending_request(cmd)
        cmd.send(self.__debugger_client)
        return True

    # Move down one in the thread's call stack
    # "down" means toward the first function called (the head of the call stack)
    def __handle_cmd_down(self, cmd_spec, args_str):
        fout = self.__out_file
        if self.__debug >= 1:
            print('debug: __handle_cmd_down()')
        if not self.__check_stopped():
            return True
        if not self.__sel_thread_stack_info:
            print('No stack information', file=fout)
            return True

        # frames[0] = first function called, frames[nframes-1] = last function
        if self.__sel_thread_stack_index <= 0:
            print('At top of call chain', file=fout)
        else:
            self.__sel_thread_stack_index -= 1
            self.__print_sel_stack_frame()
        return True

    # @param dump boolean dump tree if true, just variable if false
    def __handle_cmd_print_impl(self, cmd_spec, args_str):
        if self.__debug >= 1:
            print('debug: handle_cmd_print_impl()'.format(args_str))
        if not self.__check_stopped():
            return True
        var_path_str = args_str
        if not (args_str and len(args_str)):
            err_str = 'ERROR: variable name or path required'
            print(err_str)
            return True
        if not (self.__sel_thread_vars):
            print("No variables found")
            return True

        get_child_keys = True
        var_path = var_path_str.split('.')
        caller_data = None

        cmd = DebuggerRequest_Variables(
            self.__sel_thread_index,
            self.__sel_thread_stack_index,
            var_path,
            get_child_keys,
            caller_data)

        self.__debugger_listener.add_pending_request(cmd)
        cmd.send(self.__debugger_client)
        return True

    # @return True on success, False otherwise
    def __handle_cmd_help(self, cmd_spec, args_str):
        fout = self.__out_file
        print('Roku Remote Debugger Help', file=fout)
        print(file=fout)

        cmd_width = 0
        for cmd_entry in self.__all_cmds:
            if not cmd_entry.is_documented:
                continue
            displayStr = cmd_entry.get_display_str()
            cmd_width = max(cmd_width, len(displayStr))
        fmtStr = '{:' + str(cmd_width) + 's}  {}'
        for cmd_entry in self.__all_cmds:
            if not cmd_entry.is_documented:
                continue
            print(fmtStr.format(cmd_entry.get_display_str(), cmd_entry.help_text),
                        file=fout)
        print(file=fout)
        print('Commands may be abbreviated; e.g., q = quit)', file=fout)
        fout.flush()
        return True

    # list the source code of the current function
    # @return True on success, False otherwise
    def __handle_cmd_list(self, cmd_spec, args_str):
        if self.__debug >= 1:
            print('debug:'
                'cli.__handle_cmd_list(), selthrdidx={}, selthrdstk={}'.format(
                    self.__sel_thread_index, self.__sel_thread_stack_info))
        if not self.__check_stopped():
            return True
        if (self.__sel_thread_index != None) and \
                        (not self.__sel_thread_stack_info):
            # Thread has been selected, but no info has been saved
            caller_data = {CallerKey.LISTING_FUNCTION : True }
            cmd = DebuggerRequest_Stacktrace(
                self.__sel_thread_index, caller_data=caller_data)
            self.__debugger_listener.add_pending_request(cmd)
            cmd.send(self.__debugger_client)
        else:
            self.__list_selected_function()
        return True

    # @return true if session should continue, false otherwise
    def __handle_cmd_out(self, cmd_spec, args_str):
        return self.__handle_cmd_step_any(cmd_spec, StepType.OUT)

    # @return true if session should continue, false otherwise
    def __handle_cmd_over(self, cmd_spec, args_str):
        return self.__handle_cmd_step_any(cmd_spec, StepType.OVER)

    # Print the value of one variable
    # @return True if command processing should continue, false otherwise
    def __handle_cmd_print(self, cmd_spec, args_str):
        return self.__handle_cmd_dump_or_print(cmd_spec, args_str, False)

    # @return true if session should continue, false if we need to quit
    def __handle_cmd_quit(self, cmd_spec, argStr):
        cmd = DebuggerRequest_ExitChannel()
        self.__debugger_listener.add_pending_request(cmd)
        cmd.send(self.__debugger_client)
        return True

    # @return true if session should continue, false otherwise
    def __handle_cmd_status(self, cmd_spec, args_str):
        if self.__debug >= 1:
            print('debug: __handle_cmd_status()')
        print(self.__get_status_line(), file=self.__out_file)
        return True

    # @return true if session should continue, false otherwise
    def __handle_cmd_step(self, cmd_spec, args_str):
        return self.__handle_cmd_step_any(cmd_spec, StepType.LINE)

    def __handle_cmd_step_any(self, cmd_spec, step_type):
        assert isinstance(step_type, StepType)
        if self.__debug >= 1:
            print('debug: __handle_cmd_step_any({})'.format(step_type.name))
        if not self.__check_stopped():
            return True
        self.__set_target_state(_TargetState.STEPPING)
        self.__reset_sel_thread(self.__sel_thread_index)
        cmd = DebuggerRequest_Step(self.__sel_thread_index, step_type)
        self.__debugger_listener.add_pending_request(cmd)
        self.__debugger_listener.add_pending_request(
                            cmd,
                            allow_update=True,
                            allowed_update_types=[UpdateType.THREAD_ATTACHED,
                                                  UpdateType.ALL_THREADS_STOPPED])
        cmd.send(self.__debugger_client)
        return True

    # @return true if session should continue, false otherwise
    def __handle_cmd_stop(self, cmd_spec, argStr):
        if self.__debug >= 1:
            print('debug: __handle_cmd_stop()')
        fout = self.__out_file
        with self.__target_state_lock:
            if self.__target_state == _TargetState.STOPPED:
                print('Already stopped.', file=fout)
                return True
        print('Suspending threads...', file=fout)
        cmd = DebuggerRequest_Stop()
        self.__debugger_listener.add_pending_request(cmd)
        cmd.send(self.__debugger_client)
        return True

    # Select one thread for inspection
    # This command only has effect locally, does not require a debugger command
    # @return True if command processing should continue, false otherwise
    def __handle_cmd_thread(self, cmd_spec, args_str):
        if self.__debug >= 1:
            print('debug: handle_cmd_thread({})'.format(args_str))
        if not self.__check_stopped():
            return True
        thread_index = None
        if not (args_str and len(args_str)):
            self.__print_sel_thread()
        else:
            try:
                thread_index = int(args_str)
            except:
                print('ERROR: Invalid thread index (must be int): {}'.format(args_str))
        if thread_index != None:
            if (thread_index == 0) or \
                        (thread_index in range(self.__get_num_threads())):
                # 0 is always allowed
                self.__set_sel_thread(thread_index, ok_to_send=True)
            else:
                print('ERROR: Invalid thread index (must be {}..{}): {}'.format(
                    0, self.__get_num_threads()-1, args_str))
        return True

    # Get a list of all threads
    def __handle_cmd_threads(self, cmd_spec, args_str):
        if self.__debug >= 1:
            print('debug: __handle_cmd_threads({})'.format(args_str))
        caller_data = {CallerKey.LISTING_THREADS:True}
        cmd = DebuggerRequest_Threads(caller_data)
        self.__debugger_listener.add_pending_request(cmd)
        cmd.send(self.__debugger_client)
        return True

    # Move up one in the thread's call stack
    # "up" means toward the last function called (the tail of the call stack)
    def __handle_cmd_up(self, cmd_spec, args_str):
        fout = self.__out_file
        if self.__debug >= 1:
            print('debug: __handle_cmd_up()')
        if not self.__check_stopped():
            return True
        if not self.__sel_thread_stack_info:
            print('No stack information', file=fout)
            return True

        # frames[0] = first function called, frames[nframes-1] = last function
        frames = self.__sel_thread_stack_info.get_frames()
        num_frames = len(frames)
        if self.__sel_thread_stack_index >= (num_frames - 1):
            print('At top of call chain', file=fout)
        else:
            self.__sel_thread_stack_index += 1
            self.__print_sel_stack_frame()
        return True

    def __handle_cmd_vars(self, cmd_spec, args_str):
        thread_index = self.__sel_thread_index
        stack_index = self.__sel_thread_stack_index
        if self.__debug >= 1:
            print('debug: __handle_cmd_vars(),thridx{},stkidx={}'.format(
                thread_index, stack_index))
        if not self.__check_stopped():
            return True
        if (thread_index == None) or (stack_index == None): # 0 is valid
            self.__print_all_variables(None)
            return True
        cmd = DebuggerRequest_Variables(
            thread_index, stack_index,
            None, # var path
            True) # get_child_keys
        self.__debugger_listener.add_pending_request(cmd)
        cmd.send(self.__debugger_client)
        return True


    ####################################################################
    #
    # Process debugger updates and responses
    #
    ####################################################################

    # @return void
    def __process_debugger_update(self, update):
        if self.__debug >= 9:
            print('debug: cli.__process_debugger_update(), update={}'.format(
                update))
        if not self.__validate_update(update):
            return
        self.__handle_update(update)

    # bool validateUpdate(update)
    # Sanity-checks the update and its associated request, if any. Exits this
    # script if serious errors are detected.
    # @return True if update should be handled, false if it should be ignored
    def __validate_update(self, update):
        fout = self.__out_file
        update_type = update.update_type
        request = update.request

        if update.request_id:
            # Response to a specific request
            if not request:
                do_exit(1,
                    'INTERNAL ERROR: update with request ID has no request')
            if update_type != UpdateType.COMMAND_RESPONSE:
                do_exit(1, 'INTERNAL ERROR:'\
                    ' update with request ID has bad UpdateType: {}'.format(
                        update_type))
            assert update.request_id == request.request_id

        else:
            # Update with no request ID
            if request:
                # Some asynchronous updates are the result of a request,
                # others cannot be the result of a request.
                assert update_type == UpdateType.THREAD_ATTACHED or \
                        update_type == UpdateType.ALL_THREADS_STOPPED

            if (update_type == None) or \
                        (update_type == UpdateType.COMMAND_RESPONSE):
                do_exit(1, 'INTERNAL ERROR:'\
                    ' update with no request has bad update_type: {}'.format(
                        update_type))

        err_code = update.err_code
        if err_code == ErrCode.OK:
            pass
        elif err_code == ErrCode.NOT_STOPPED:
            print('ERROR: target must be stopped, but is running (use "stop")')
            return False
        else:
            print('ERROR: error received from target: {} ({})'.format(
                err_code.value, err_code.name),
                file=fout)
            return False

        return True

    # Process an update from the debug target. The update may be a
    # response to a specific request or an update without a request.
    def __handle_update(self, update):
        if self.__debug >= 1:
            print('debug: cli.__handle_update({}),request={}'.format(
                                                update, update.request))
        fout = self.__out_file
        update_type = update.update_type
        request = update.request  # May be None
        cmd_code = None
        if request:
            cmd_code = request.cmd_code
        update_type = update.update_type

        if update_type == UpdateType.CONNECT_IO_PORT:
            self.__debugger_client.connect_io_port(update.io_port, fout)
        elif update_type == UpdateType.ALL_THREADS_STOPPED:
            self.__handle_update_all_threads_stopped(update)
        elif update_type == UpdateType.THREAD_ATTACHED:
            self.__handle_update_thread_attached(update)

        # The UpdateType for all responses to specific commands is
        # COMMAND_RESPONSE, so the actual type of the data is determined
        # by the CmdCode that was sent with the request.

        elif cmd_code == CmdCode.CONTINUE:
            self.__set_target_state(_TargetState.RUNNING)
            print(file=fout)
            print(self.__get_status_line())
        elif cmd_code == CmdCode.EXIT_CHANNEL:
            self.__set_target_state(_TargetState.TERMINATED)
            self.__is_connected = False
            print(file=fout)
            print(self.__get_status_line(),file=fout)
            do_exit(0)
        elif cmd_code == CmdCode.STACKTRACE:
            self.__handle_update_stack_trace(update)
        elif cmd_code == CmdCode.STOP:
            self.__set_target_state(_TargetState.STOPPED)
        elif cmd_code == CmdCode.THREADS:
            self.__handle_update_threads(update)
        elif cmd_code == CmdCode.VARIABLES:
            self.__handle_update_variables(update)

        if self.__debug >= 1:
            print('debug: cli.__handle_update() done')

    def __handle_update_all_threads_stopped(self, update):
        fout = self.__out_file
        self.__set_target_state(_TargetState.STOPPED)
        primary_thridx = update.primary_thread_index
        if primary_thridx < 0:
            primary_thridx = 0
        self.__set_sel_thread(primary_thridx, ok_to_send=False)
        print('', file=fout)
        print(get_stop_reason_str_for_user(
                update.stop_reason, update.stop_reason_detail),
                file=fout)
        print('{}CHANNEL STOPPED ({})'.format(
                    _BASE_PROMPT,
                    get_stop_reason_str_for_user(
                        update.stop_reason, update.stop_reason_detail)),
                file=fout)
        print('', file=fout)
        cmd = DebuggerRequest_Threads(caller_data=
            {CallerKey.STOPPING:{
                _LITERAL_PRIMARY_THREAD_INDEX:update.primary_thread_index}})
        self.__debugger_listener.add_pending_request(cmd)
        cmd.send(self.__debugger_client)

    def __handle_update_stack_trace(self, update):
        if self.__debug >= 5:
            print('debug: handle_update_stack_trace({})'.format(update))
        request = update.request
        assert request
        assert request.thread_index != None     # 0 is valid

        if self.__debug >= 1:
            if self.__debugger_client.has_bad_line_number_in_stop_bug:
                print('debug: working around "bad line number in stop" bug')
            else:
                print('debug: NOT working around "bad line number in stop" bug')

        if self.__debugger_client.has_bad_line_number_in_stop_bug:
            # The line number of the tail stack frame is wrong, in the stack
            # trace response. Use the value returned by the threads command.
            correct_info = self.__threads[request.thread_index]
            bad_frame = update.frames[len(update.frames)-1]
            bad_frame.copy_from(correct_info)

        if request.thread_index == self.__sel_thread_index:
            self.__sel_thread_stack_info = update
            self.__set_sel_thread(request.thread_index) # updates selected frame

        # STOPPING ALL THREADS

        if self.__request_has_caller_key(request, CallerKey.STOPPING):
            caller_data = dict(request.caller_data)

            # It's possible that a stack trace comes back empty. If so,
            # we can't request variables in a given stack frame (there aren't any)
            # NB: 0 is a valid value for indexes
            if (self.__sel_thread_index == None) or \
                    (self.__sel_thread_stack_index == None):
                # No stack frames
                if self.__debug >= 1:
                    print(
                        'debug: WARNING: bad thread or stack index:'
                        ' thridx={},stkidx={}'.format(
                            self.__sel_thread_index,
                            self.__sel_thread_stack_index))
                self.__print_crash_dump()
            else:
                cmd = DebuggerRequest_Variables(
                    self.__sel_thread_index,
                    self.__sel_thread_stack_index,  # stack_index
                    None,  # var path: None = local variables in stack frame
                    True,  # get_child_keys (children are the local vars)
                    caller_data=caller_data)
                self.__debugger_listener.add_pending_request(cmd)
                cmd.send(self.__debugger_client)

        # SELECTING THREAD

        elif self.__request_has_caller_key(request,
                                            CallerKey.SELECTING_THREAD):
            caller_data = dict(request.caller_data)

            # It's possible that a stack trace comes back empty. If so,
            # we can't request variables in a given stack frame (there aren't any)
            # NB: 0 is a valid value for indexes
            if (self.__sel_thread_index == None) or \
                (self.__sel_thread_stack_index == None):
                                # No stack frames
                if self.__debug >= 1:
                    print(
                        'debug: WARNING: bad thread or stack index:'
                        ' thridx={},stkidx={}'.format(
                            self.__sel_thread_index,
                            self.__sel_thread_stack_index))
                self.__print_sel_thread()
            else:
                # Get the variables, in case the user wants to print one
                cmd = DebuggerRequest_Variables(
                    self.__sel_thread_index,
                    self.__sel_thread_stack_index,  # stack_index
                    None,  # var path: None = local variables in stack frame
                    True,  # get_child_keys (children are the local vars)
                    caller_data=caller_data)
                self.__debugger_listener.add_pending_request(cmd)
                cmd.send(self.__debugger_client)

        # LISTING A FUNCTION

        elif self.__request_has_caller_key(request,
                                            CallerKey.LISTING_FUNCTION):
            self.__list_selected_function()

        # USER REQUEST FOR SPECIFIC STACK TRACE

        else:
            self.__print_stack_trace(update)

    def __handle_update_thread_attached(self, update):
        if self.__debug >= 5:
            print('debug: handle_update_thread_attached({})'.format(update))
        # Get more info about the new thread before announcing it
        self.__set_target_state(_TargetState.STOPPED)
        caller_data = {CallerKey.THREAD_ATTACHED:update}
        cmd = DebuggerRequest_Threads(caller_data=caller_data)
        self.__debugger_listener.add_pending_request(cmd)
        cmd.send(self.__debugger_client)

    def __handle_update_threads(self, update):
        if self.__debug >= 5:
            print('debug: handle_update_threads({}),request={}'.format(
                                            update, update.request))
        self.__threads = update.threads
        request = update.request
        caller_data = None
        if self.__request_has_caller_key(request, CallerKey.STOPPING):
            caller_data = dict(request.caller_data) # dup it

            # Stopped for any number of reasons, provide details
            cmd = DebuggerRequest_Stacktrace(
                self.__sel_thread_index, caller_data=caller_data)
            self.__debugger_listener.add_pending_request(cmd)
            cmd.send(self.__debugger_client)
        elif self.__request_has_caller_key(
                        request, CallerKey.THREAD_ATTACHED):
            thread_attached_update =\
                request.caller_data[CallerKey.THREAD_ATTACHED]
            self.__print_thread_attached_message(
                thread_attached_update, update)
        else:
            # User request for threads, just print 'em
            self.__print_threads()

    def __handle_update_variables(self, update):
        if self.__debug >= 5:
            print('debug: __handle_update_variables({})'.format(update))
        assert update and isinstance(update, DebuggerUpdate)
        request = update.request
        assert request
        if self.__debug >= 9:
            update.dump(self.__out_file,
                line_prefix='debug: __handle_update_variables: ')
        self.__sel_thread_vars = update

        # STOPPING ALL THREADS

        if self.__request_has_caller_key(request, CallerKey.STOPPING):
            # This is the last request needed to provide a stop/crash dump
            self.__print_crash_dump()

        # SELECTING A THREAD

        elif self.__request_has_caller_key(request, CallerKey.SELECTING_THREAD):
            self.__print_sel_thread()

        # USER REQUEST FOR ALL LOCAL VARIABLES

        else:
            self.__print_all_variables(update)

    # @return previous state
    def __set_target_state(self, new_state):
        with self.__target_state_lock:
            prev_state = self.__target_state
            if prev_state == new_state:
                return prev_state

            if new_state == _TargetState.RUNNING:
                assert prev_state != _TargetState.STEPPING
            if new_state == _TargetState.STEPPING:
                assert prev_state == _TargetState.STOPPED
            self.__target_state = new_state

            return prev_state

    def __get_target_state(self):
        with self.__target_state_lock:
            return self.__target_state

    def __get_num_threads(self):
        if self.__threads:
            return len(self.__threads)
        return 0

    # If this returns ttrue, then request.caller_data[key] exists
    def __request_has_caller_key(self, request, key):
        assert (request == None) or isinstance(request, DebuggerRequest)
        assert (key == None) or isinstance(key, CallerKey)
        ret_val = False
        if request and key and request.caller_data:
            ret_val = (key in request.caller_data)
        if self.__debug >= 10:
            print('debug: has_key({},{}) -> {}'.format(request, key, ret_val))
        return ret_val

    def __print_use_help_for_help(self):
        print('Use "help" for help', file=self.__out_file)

    # An update has been received from the debugger, which may be the
    # the response to a request, or it may be an unsolicited change of
    # state.
    # Called on a separate thread by the DebuggerListener
    # @return True if more updates are expected, false if not
    def update_received(self, response):
        request = response.request
        if self.__debug >= 5:
            print('debug: updateReceived(response={},request={}'.format(
                    response, request))

        self.__debugger_update_queue.put(response)  # thread-safe
        with self.__input_cond_var:
            self.__input_cond_var.notify_all()

        # Short-circuit an exited response -- the connection should be closed
        if request and (request.cmd_code == CmdCode.EXIT_CHANNEL):
            return False
        if self.__debug >= 9:
            print('debug: updatedReceived() done')
        return True

    # Called on the user input processor thread
    def _user_input_received(self, cmd_line):
        if self.__debug >= 3:
            print('debug: cli.__user_input_received, cmdline={}'.format(cmd_line))
        self.__user_input_queue.put(cmd_line)  # thread-safe
        with self.__input_cond_var:
            self.__input_cond_var.notify_all()


# Processes user input and notifies its listener with
# command lines.
class UserInputProcessor(object):

    def __init__(self, input_listener, fin, fout):
        super(UserInputProcessor, self).__init__()
        self.__debug = max(gMain.gDebugLevel, 0)
        self.__input_listener = input_listener
        self.__cmd_queue = queue.Queue()  # Queue is thread-safe
        self.__lock = threading.Lock()
        self.__prompt_lines = ['> ']
        self.__in_file = fin
        self.__out_file = fout

        # Start the processor thread
        self.__thread = threading.Thread(
                target=self, name='User-Input-0', daemon=True)
        self.__thread.start()

    def set_prompt_lines(self, prompt_lines):
        with self.__lock:
            self.__prompt_lines = prompt_lines

    def print_prompt(self):
        fout = self.__out_file
        with self.__lock:
            for i in range(len(self.__prompt_lines)):
                line = self.__prompt_lines[i]
                if i < (len(self.__prompt_lines) - 1):
                    print(line, file=fout)
                else:
                    print(line, file=fout, end='')
            fout.flush()

    def simulate_input(self, cmd_str):
        fout = self.__out_file
        print('RRDB-AUTO> {}'.format(cmd_str), file=fout)
        self.__input_listener._user_input_received(cmd_str)

    def run(self):
        if self.__debug >= 1:
            print('debug: cli user input thread running...')
        fin = self.__in_file

        if gMain.test_name == "vars":
            self.simulate_input('stop')

        while True:
            cmd_line = fin.readline().rstrip('\r\n')
            if len(cmd_line):
                self.__input_listener._user_input_received(cmd_line)
            else:
                self.print_prompt()

        if self.__debug >= 1:
            print('debug: cli user input thread exiting')

    def __call__(self):
        self.run()

def build_indent_str(depth):
    if depth == None:
        depth = 0
    s = ''
    for _ in range(0,depth):
        s = s + '    '
    return s

def safe_len(obj):
    if obj:
        return len(obj)
    return 0

def is_empty(obj):
    return not (obj and len(obj))

import sys
def do_exit(err_code, msg=None):
    sys.modules['__main__'].do_exit(err_code, msg)
