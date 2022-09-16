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
# File: CommandLineInterface.py
# Requires python 3.5.3 or later
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

import copy, enum, re, sys, queue, threading, time, traceback

from rokudebug.model import Breakpoint
from rokudebug.model import BreakpointManager
from rokudebug.model import CmdCode
from rokudebug.model import DebuggerRequest
from rokudebug.model import DebuggerRequest_AddBreakpoints
from rokudebug.model import DebuggerRequest_AddConditionalBreakpoints
from rokudebug.model import DebuggerRequest_Continue
from rokudebug.model import DebuggerRequest_ExitChannel
from rokudebug.model import DebuggerRequest_ListBreakpoints
from rokudebug.model import DebuggerRequest_RemoveBreakpoints
from rokudebug.model import DebuggerRequest_Stacktrace
from rokudebug.model import DebuggerRequest_Step
from rokudebug.model import DebuggerRequest_Stop
from rokudebug.model import DebuggerRequest_Threads
from rokudebug.model import DebuggerRequest_Variables
from rokudebug.model import DebuggerRequest_Execute
from rokudebug.model import DebuggerUpdate
from rokudebug.model import ErrCode
from rokudebug.model import ProtocolFeature
from rokudebug.model import ProtocolVersion
from rokudebug.model import SourceCodeInspector
from rokudebug.model import StepType
from rokudebug.model import ThreadStopReason
from rokudebug.model import UpdateType
from rokudebug.model import VariableType
from rokudebug.model import Verbosity
from rokudebug.model import get_stop_reason_str_for_user

from .CommandLineCompleter import CommandLineCompleter
from .CommandLineCompleter import CompletionDomain
from .UserInputProcessor import UserInputProcessor

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

@enum.unique
class _CommandMode(enum.IntEnum):
    COMMANDS = 0,           # Normal command processing
    BRIGHTSCRIPT = 1        # Executing BrightScript on target

@enum.unique
class _TargetState(enum.IntEnum):
    UNKNOWN = 0,
    RUNNING = 1,
    STOPPED = 2,
    STEPPING = 3,
    TERMINATED = 4,

_COMMAND_PROMPT = 'RRDB> '
_BS_PROMPT = 'BrightScript> '

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

# A hint presented to the user with a display count
# @param text text to display to user
# @param display_limit max number of times to display the hint
class _Hint(object):
    def __init__(self, text, display_limit=None):
        self.text = text
        self.display_count = 0
        self.display_limit = display_limit
        self.suppressed = False

    def print(self, force=False):
        full_text = self.get_text(force)
        if full_text:
            print('{}\n'.format(full_text))

    def get_text(self, force=False):
        full_text = None
        if force or not self.suppressed:
            full_text = 'hint: {}'.format(self.text)
            if not force:
                self.display_count += 1
                if self.display_limit != None and self.display_count >= self.display_limit:
                    self.suppressed = True
        return full_text

    def suppress(self):
        self.suppressed = True


class _CmdSpec(object):

    # example_args: If None or empty, no arguments will ever be passed
    # to func(), and an error will be shown to the user if an attempt
    # is made to call the function with parameters. If example_args
    # does have a value, the function is always called with an args_str
    # argument, and an error is shown to the user omits arguments to
    # the command.
    #
    # args_are_optional: If True, no validation is done of parameters
    # supplied by the user, prior to calling func. func() may be called
    # with or without an args_str parameter.
    #
    # is_active: An active command can be invoked by the user and appears
    # in short help (if is_visible). An inactive command will still appear
    # in short help if is_visible, but will never be invoked based on
    # user input.
    #
    # show_args_in_short_help: If True, the example_args appear with the
    # command in the short help summary (e.g., the 'help' command).
    #
    # the function will never be called with parameters, and an error
    # will be shown to the user if an parameters are given to the command.
    # If example_args has a value, the inference is that arguments are
    # always required, unless args_are_optional is True.
    # @param is_visible if True, the command is visible to the user
    def __init__(self, cmd_str, is_visible, sort_order, func,
                    short_aliases=None,
                    example_args=None,
                    args_are_optional=False,
                    short_desc=None,
                    long_help=None,
                    is_active=True,
                    show_args_in_short_help=False):

        # Validate parameters. There are a lot of parameters, and it's easy
        # to get them mixed up. Check 'em, to be sure.

        # Validate required arguments
        if (not cmd_str) or not isinstance(cmd_str, str):
            raise TypeError('cmd_str')
        if not isinstance(is_visible, bool):
            raise TypeError('is_visible')
        if (not func) or (not callable(func)):
            raise TypeError('func')
        if (not sort_order) or (not isinstance(sort_order, int)):
            raise TypeError('sort_order')

        # Validate optional arguments
        if short_aliases:
            if not isinstance(short_aliases, list):
                raise TypeError('short_aliases')
        if example_args:
            if not isinstance(example_args, str):
                raise TypeError('example_args')
        else:
            args_are_optional = False
        if not isinstance(args_are_optional, bool):
            raise ValueError('args_are_optional')
        if short_desc:
            if not isinstance(short_desc, str):
                raise TypeError('short_desc')
        if long_help:
            if not isinstance(long_help, str):
                raise TypeError('long_help')
        if not isinstance(is_active, bool):
            raise TypeError('is_active')
        if not isinstance(show_args_in_short_help, bool):
            raise TypeError('show_args_in_short_help')

        # Save the values

        self.is_visible = is_visible        # if True, show in help
        self.cmd_str = cmd_str              # separator label, if is_separator
        self.sort_order = sort_order
        self.short_aliases = short_aliases
        self.example_args = example_args
        self.args_are_optional = args_are_optional
        if self.example_args and not len(self.example_args):
            self.example_args = None
        self.has_args = bool(example_args)
        self.func = func
        self.short_desc = short_desc
        self.long_help = long_help
        self.is_separator = False
        self.is_active = is_active
        self.show_args_in_short_help = show_args_in_short_help

        # Final check
        if self.has_args:
            assert self.example_args and len(self.example_args)
        else:
            assert not self.args_are_optional
            assert not self.example_args

    def __str__(self):
        short_aliases_str = None
        if self.short_aliases:
            assert len(self.short_aliases)
            short_aliases_str = '[{}]'.format(','.join(self.short_aliases))
        s = '_CmdSpec['
        s += 'cmd={}'.format(self.cmd_str)
        s += ',sort={}'.format(self.sort_order)
        if self.is_active:
            s += ',active'
        if self.is_visible:
            s += ',visible'
        if self.is_separator:
            s += ',separator'
        if short_aliases_str:
            s += ',shortaliases={}'.format(short_aliases_str)
        if self.has_args:
            s += ',has_args'
        if self.example_args:
            s += ',exampleargs="{}"'.format(self.example_args)
        if self.args_are_optional:
            s += ',argsoptional'
        if self.short_desc:
            s += ',help={}'.format(self.short_desc)
        s += ']'
        return s

    # get string for display to the user
    def get_display_str(self, include_example_args=False):
        s = self.cmd_str
        if self.short_aliases and len(self.short_aliases):
            for alias in self.short_aliases:
                s = s + '|' + alias
        if include_example_args and self.example_args:
            s += ' {}'.format(self.example_args)
        return s
#END class _CmdSpec


# A separator is used to visually break up lists into functional areas
class _CmdSpecSeparator(_CmdSpec):

    def __init__(self, label, sort_order):
        super(_CmdSpecSeparator,self).__init__(
            cmd_str=label, sort_order=sort_order, is_visible=True, func=noop_func)
        self.func = None            # noop func used to pass validation in superclass
        self.is_separator = True
        self.is_active = False


# Command-line interface, to handle all user requests
# All output from the target is sent to the target_output_controller
# @param target_output_controller must have two file-like attrs:
#                               targetout, targeterr
# param debug_preserve_breakpoint_path if True, don't modify the breakpoint
#       path, to test the target's ability to handle arbitrary values
class CommandLineInterface(object):
    def __init__(self, channel_zip_file_path,
                    target_output_controller, stop_target_on_launch,
                    debug_preserve_breakpoint_path):
        self._debug_level = 0

        self.__stop_target_on_launch = stop_target_on_launch
        self.__debugger_client = None   # set in interact()
        self.__channel_zip_file_path = channel_zip_file_path
        self.__protocol_version = None  # set in interact()
        self.__in_file = sys.stdin
        self.__out_file = sys.stdout
        self.__target_output_controller = target_output_controller
        self.__prev_cmd_failed = True   # Add hint to first prompt

        # guard primarily for asynchronous shutdown requests
        self.__self_state_lock = threading.Lock()
        self.__is_interacting = False
        self.__is_shut_down = False

        self.__cmd_mode = _CommandMode.COMMANDS
        self.__is_connected = False
        self.__target_state = _TargetState.UNKNOWN # use accessor methods
        self.__target_state_lock = threading.Lock()
        self.__threads = None          # DebuggerReponse_Threads.ThreadInfo

        # "sel" = selected
        self.__sel_thread_index = None          # Index of selected thread
        self.__sel_thread_stack_index = None
        self.__sel_thread_stack_info = None     # DebuggerResponse_Stacktrace
        self.__sel_thread_vars = None           # DebuggerResponse_Variables

        self.__breakpoints = BreakpointManager()

        # protected
        self._src_inspector = SourceCodeInspector(self.__channel_zip_file_path)

        # private
        self.__input_cond_var = threading.Condition()  # notified on user or debugger event
        self.__user_input_queue = queue.Queue()        # Queue is thread-safe
        self.__debugger_update_queue = queue.Queue()   # Queue is thread-safe
        self.__shutdown_trigger = False             # Latched to True to shut down
        self.__debug_preserve_breakpoint_path = debug_preserve_breakpoint_path

        self.__all_cmds = None  # set in interact()

        self.__hint_bs_to_exit_interpreter = _Hint('"bs" or "." to exit BrightScript interpreter')
        self.__hint_bs_to_run_bs = _Hint('"bs" or "." to execute BrightScript (see "help bs")')
        self.__hint_use_help = _Hint(
                '"help" for list of commands, "help <command>" for command-specific help')
        self.__hint_tabs_complete = _Hint(
            'tab-completion of commands and files works on most platforms')

        if self.__check_debug(2):
            if self.__debug_preserve_breakpoint_path:
                print('debug: will pass breakpoint paths to target without modification')

    def interact(self, debugger_client):
        if self.__check_debug(2):
            print('debug:cli: interact() -- start')
        self.__debugger_client = debugger_client
        self.__protocol_version = debugger_client.protocol_version

        fin = self.__in_file
        fout = self.__out_file
        if self.__stop_target_on_launch:
            assert debugger_client.has_feature(
                            ProtocolFeature.STOP_ON_LAUNCH_ALWAYS)
        self.__all_cmds = self.__build_cmd_spec_list()
        assert self.__all_cmds and len(self.__all_cmds)

        with self.__self_state_lock:
            self.__is_interacting = True
        self.__set_target_state(_TargetState.RUNNING)
        self.__is_connected = True
        self.__waiting_for_initial_stopped_message = \
                self.__debugger_client.has_feature(
                    ProtocolFeature.STOP_ON_LAUNCH_ALWAYS)

        self.__print_intro()
        self.__input_processor = UserInputProcessor(
            [_COMMAND_PROMPT], self, CommandLineCompleter(self), fin, fout)
        self.__input_processor.start()

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
                    dclient = self.__debugger_client
                    while (not done) and dclient.has_pending_request():
                        if self.__shutdown_trigger:
                            done = True
                            break
                        if self.__check_debug(5):
                            print('debug: cli: wait for {} pending requests...' \
                                .format(self.__debugger_client.\
                                    get_pending_request_count()))

                        # On some platforms (e.g., Windows 10), a signal (e.g., ^C) will
                        # not interrupt a wait(), so we poll here to allow signal handling
                        self.__input_cond_var.wait(1.0)
                if self.__check_debug(5):
                    print('debug: pending requests: {}'.format(
                        self.__debugger_client.get_pending_request_count()))

                ##########################################################
                # Wait for user input or debugger updates
                ##########################################################
                with self.__input_cond_var:
                    while (not done) and \
                        self.__user_input_queue.empty() and \
                                self.__debugger_update_queue.empty():
                        if self.__shutdown_trigger:
                            done = True
                            break
                        input_count = self.__input_processor.get_input_count()

                        # Set prompt
                        prompts = list()
                        if self.__cmd_mode == _CommandMode.COMMANDS:
                            if self.__prev_cmd_failed or (input_count < 1):
                                self.__prev_cmd_failed = False
                                s = self.__hint_use_help.get_text()
                                if s:
                                    prompts.append(s)
                                s = self.__hint_tabs_complete.get_text()
                                if s:
                                    prompts.append(s)
                            if input_count >= 1:
                                self.__hint_use_help.suppress()
                                self.__hint_tabs_complete.suppress()
                            prompts.append(self.__get_status_line())
                            prompts.append(_COMMAND_PROMPT)
                        elif self.__cmd_mode == _CommandMode.BRIGHTSCRIPT:
                            prompts.append(_BS_PROMPT)
                        else:
                            if self.__check_debug(1):
                                raise AssertionError('bad command mode: {}'.
                                    format(self.__cmd_mode))
                        self.__input_processor.set_prompt_lines(prompts)
                        self.__input_processor.accept_input(True)

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
        except Exception:
            if self.__check_debug(2):
                print('debug:cli: exception:')
                traceback.print_exc(file=sys.stdout)
            raise

        with self.__self_state_lock:
            self.__is_interacting = False

        ############################################################
        # Shut down
        ############################################################
        if self.__check_debug(1):
            print('debug: cli.interact() exited loop, shutting down...')

        # Ignore exceptions during shutdown, because there is nothing
        # we can do about it.
        try:
            # This process is unlikely to be alive when the target responds
            # to the exit request. But, if it is alive, make sure the request
            # is in the pending queue to avoid crashes and assertion.
            cmd = DebuggerRequest_ExitChannel()
            self.__debugger_client.send(cmd)
        except Exception:
            if self.__check_debug(2):
                print('debug: exception:')
                traceback.print_exc(file=sys.stdout)
        try:
            self.__debugger_client.shutdown()
        except Exception:
            if self.__check_debug(2):
                print('debug: exception')
                traceback.print_exc(file=sys.stdout)

        with self.__self_state_lock:
            self.__is_shut_down = True
        if self.__check_debug(2):
            print('debug: cli.interact(): done')
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
                if not self.__is_interacting:
                    return
            if (self.__check_debug(2)):
                print('debug: cli.shutdown(): triggering shutdown')
            self.__shutdown_trigger = True
            self.__input_cond_var.notify_all()

        # REMIND: This sleep is to make sure an ExitChannel request
        # has been sent to the target. This should be improved, so
        # the sleep can be removed.
        if not self.__debugger_client.is_fake:
            time.sleep(3)

        # # Now block until final exit command has been sent to target
        # while not self.__is_shut_down:
        #     print('debug: cli.shutdown(): waiting for shutdown...')
        #     time.sleep(1)

    # Blocks while waiting for input
    # @return (potentially empty) string, never None
    def __read_line(self, prompt, completion_domain, strip=True, default=None):
        line = self.__input_processor.read_line_sync([prompt], completion_domain)
        if line and strip:
            line = line.strip()
        if (not line or not len(line)) and default:
            line = default
        if not line:
            line = ''
        return line

    # @return list of full commands (no aliases)
    def _get_all_cmd_strs(self):
        cmd_strs = list()
        all_cmds = self.__all_cmds
        for cmd_spec in all_cmds:
            if not cmd_spec.is_separator:
                cmd_strs.append(cmd_spec.cmd_str)
        return cmd_strs

    # Returns a list of pairs: (alias, full_command). There may be multiple
    # aliases for one command, and full_command will always be present in
    # the list returned by _get_all_cmd_strs()
    # @return list of tuples: (alias, full_command)
    def _get_all_cmd_aliases(self):
        aliases = list()
        all_cmds = self.__all_cmds
        for cmd_spec in all_cmds:
            if cmd_spec.short_aliases:
                for alias in cmd_spec.short_aliases:
                    aliases.append((alias, cmd_spec.cmd_str))
        return aliases

    def __build_cmd_spec_list(self):
        if self.__check_debug(2):
            print('debug: get_cmd_spec_list(),protocolver={}'.format(
                self.__protocol_version))

        ###############################################
        # Determine which commands to include
        ###############################################
        proto_ver = self.__debugger_client.protocol_version

        has_execute_command = proto_ver.has_feature(
                                ProtocolFeature.EXECUTE_COMMAND)

        has_step_commands = proto_ver.has_feature(
                                ProtocolFeature.STEP_COMMANDS)
        if has_step_commands and global_config.verbosity >= Verbosity.HIGH:
            print('info: protocol supports step commands')

        has_breakpoint_commands = proto_ver.has_feature(
                                ProtocolFeature.BREAKPOINTS)
        if has_breakpoint_commands and global_config.verbosity >= Verbosity.HIGH:
            print('info: protocol supports breakpoints')

        ###############################################
        # Create list of commands
        ###############################################
        cmds = list()

        # Default 1.0 commands

        # _CmdSpec(cmd_str, is_visible, sort_order, func,
        #           short_aliases=None, example_args=None, short_desc=None):

        cmds.extend([

            # General commands

            _CmdSpecSeparator('General', 100),
            # First line for 'help' is to provide info, function is noop
            _CmdSpec('help', True, 110, noop_func,
                    short_desc='Print this help',
                    is_active=False),
            # Second line of info for help is the real deal
            _CmdSpec('help', True, 111, self.__handle_cmd_help,
                    example_args='<command>',
                    args_are_optional=True,
                    short_desc='Print help for a command',
                    show_args_in_short_help=True,
                    is_active=True),
            _CmdSpec('quit', True, 120, self.__handle_cmd_quit,
                    short_desc='Quit debugger and terminate target'),
            _CmdSpec('status', True, 130, self.__handle_cmd_status,
                    short_desc='Show debugger status'),

            # Execution commands

            _CmdSpecSeparator('Execution', 200),
            _CmdSpec('continue', True, 210, self.__handle_cmd_continue,
                    short_desc='Continue all threads'),
            _CmdSpec('stop', True, 220, self.__handle_cmd_stop,
                    short_desc='Stop all threads'),

            # Inspection commands

            _CmdSpecSeparator('Inspection', 400),
            _CmdSpec('backtrace', True, 410, self.__handle_cmd_backtrace,
                    short_aliases=['bt'],
                    short_desc='Print stack backtrace of selected thread'),
            _CmdSpec('down', True, 420, self.__handle_cmd_down,
                    short_aliases=['d'],
                    short_desc='Move one frame down the function call stack'),
            _CmdSpec('list', True, 430, self.__handle_cmd_list,
                    short_desc='List current function'),
            _CmdSpec('show', True, 440, self.__handle_cmd_show, # was _print
                    example_args='<variable-name-or-path>',
                    short_desc='Print a variable\'s value'),
            _CmdSpec('thread', True, 450, self.__handle_cmd_thread,
                    short_aliases=['th'],
                    example_args='<threadid>',
                    short_desc='Select a thread for inspection'),
            _CmdSpec('threads', True, 460, self.__handle_cmd_threads,
                    short_aliases=['ths'],
                    short_desc='Show all threads'),
            _CmdSpec('up', True, 470, self.__handle_cmd_up,
                    short_aliases=['u'],
                    short_desc='Move one frame up the function call stack'),
            _CmdSpec('vars', True, 480, self.__handle_cmd_vars,
                    short_desc='Show variables in the current scope'),
        ])

        # VERSION-DEPENDENT COMMANDS
        # _CmdSpec(cmd_str, is_visible, sort_order, function,
        #           short_aliases=None, example_args=None, short_desc=None

        if has_step_commands:
            cmds.extend([
                _CmdSpec('over', True, 230, self.__handle_cmd_over,
                        short_aliases=['v'],
                        short_desc='Step over one program statement'),
                _CmdSpec('out', True, 240, self.__handle_cmd_out,
                        short_aliases=['o'],
                        short_desc='Step out of the current function'),
                _CmdSpec('step', True, 250, self.__handle_cmd_step,
                        short_aliases=['s','t'],
                        short_desc='Step one program statement'),
            ])

        if has_breakpoint_commands:
            cmds.extend([
                _CmdSpecSeparator("Breakpoints", 300),
                _CmdSpec('addbreak', True, 310,
                        self.__handle_cmd_add_breakpoint,
                        args_are_optional=True,
                        short_aliases=['break','ab'],
                        example_args='<filename:linenum> [ignore_count] | <no args for interactive>',
                        short_desc='Set a breakpoint',
                        long_help='Add a breakpoint at a given file name and'
                                    ' line number, with an optional'
                                    ' ignore_count.\n'
                                    'Examples:\n'
                                    '    addbreak\n'
                                    '    addbreak main.brs:25\n'
                                    '    addbreak main.brs:25 99\n'
                                    '    addbreak main.brs:25 99 x = 5'),
                _CmdSpec('rmbreaks', True, 320,
                        self.__handle_cmd_remove_breakpoints,
                        short_aliases=['rb'],
                        example_args='<breakpointid> [<breakpointid>...]',
                        short_desc='Clear (remove) breakpoints by ID'
                                    ', or * to clear all'),
                #_CmdSpec('disablebreak', True, 330,
                #        self.__handle_cmd_disable_breakpoint,
                #        short_aliases=['db'],
                #        short_desc='Disable breakpoints by ID or *'),
                #_CmdSpec('enablebreak', True, 340,
                #        self.__handle_cmd_enable_breakpoint,
                #        short_aliases=['eb'],
                #        short_desc='Disable breakpoints by ID or *'),
                _CmdSpec('listbreaks', True, 350,
                        self.__handle_cmd_list_breakpoints,
                        short_aliases=['lb'],
                        short_desc='List all breakpoints'),
            ])

        if has_execute_command:
                cmds.extend([
					# bs = switch mode between commands and BrightScript
                    _CmdSpec('bs', True, 105, self.__handle_cmd_bs,
                        short_aliases=['.'],
                        short_desc='Execute BrightScript statement, or enter interpreter',
                        example_args="<BrightScript statement> | <no args for interactive>",
                        args_are_optional=True,
                        long_help='Examples:\n'
                            '-----------------------------------------\n'
                            + _COMMAND_PROMPT + 'bs x = 5\n'
                            '-----------------------------------------\n'
                            + _COMMAND_PROMPT + '. x = 5\n'
                            '-----------------------------------------\n'
                            + _COMMAND_PROMPT + '.\n'
                            + self.__hint_bs_to_exit_interpreter.get_text() + '\n'
                            + _BS_PROMPT + 'x = 5\n'
                            + _BS_PROMPT + 'print x\n'
                            '5\n'
                            + _BS_PROMPT + '.\n'
                            + _COMMAND_PROMPT + '\n'
                            '-----------------------------------------')
                ])

        # sort the whole kit and kaboodle
        cmds.sort(key=lambda cmd : cmd.sort_order)
        return cmds

    # Invoked on a random thread for various reasons, such
    # as a ^C which sends a stop command
    def __queue_cmd(self, cmdStr):
        if self.__check_debug(2):
            print('debug: __queue_cmd({})'.format(cmdStr))
        with self.__self_state_lock:
            if self.__is_interacting:
                    self.__user_input_queue.put(cmdStr)
            with self.__input_cond_var:
                self.__input_cond_var.notify_all()

    # Prints the one-time intro message
    def __print_intro(self):
        fout = self.__out_file
        print('',file=fout)
        print('Roku Remote Debugger', file=fout)

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
        if self.__check_debug(5):
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
            self.__debugger_client.send(cmd)

        if (self.__check_debug(4)):
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
        file_path = sel_frame.file_path
        line_start = max(0, sel_frame.line_num - 7)
        line_end = sel_frame.line_num + 14
        lines = self._src_inspector.get_source_lines(
                                        file_path, line_start, line_end)

        # Mark all Program Counters in the call stack
        tail_pc_line_num = stack_frames[len(stack_frames)-1].line_num
        pc_line_nums = set([tail_pc_line_num])
        for i_frame in range(len(stack_frames)):
            one_frame = stack_frames[i_frame]
            if one_frame.file_path == sel_frame.file_path:
                pc_line_nums.add(one_frame.line_num)

        if not (lines and len(lines)):
            print('Could not find source lines: {}:{}-{}'.format(
                file_path, line_start, line_end),
                file=fout)
        else:
            print('Current Function:', file=fout)
            for line in lines:
                pc_token = ' '
                breakpoint_token = ' '
                is_error_line = False
                if line.line_number in pc_line_nums:
                    if line.line_number == tail_pc_line_num:
                        pc_token = '*'
                        if stop_reason and \
                                (stop_reason != ThreadStopReason.BREAK):
                            is_error_line = True
                    else:
                        pc_token = '>'
                brk_mgr = self.__breakpoints
                if brk_mgr.find_breakpoint_at_line(file_path, line.line_number):
                    breakpoint_token = '!'
                print('{:03d}:{}{} {}'.format(
                    line.line_number, breakpoint_token, pc_token, line.text), file=fout)

                if is_error_line:
                    print('', file=fout)
                    print(get_stop_reason_str_for_user(
                            stop_reason, stop_reason_detail),
                            file=fout)
                    print('')

    def __print_breakpoints(self):
        brk_mgr = self.__breakpoints
        if brk_mgr.is_empty():
            print('No breakpoints')
        else:
            print('Breakpoints:')
            for breakpoint in brk_mgr.breakpoints:

                cond_expr_str = ''
                if breakpoint.cond_expr and len(breakpoint.cond_expr):
                    cond_expr_str = ' {{{}}}'.format(breakpoint.cond_expr)

                ignore_count_str = ''
                if breakpoint.ignore_count:
                    ignore_count_str = ' ignore={}'.format(
                        breakpoint.ignore_count)

                disabled_str = ''
                if not breakpoint.is_enabled():
                    disabled_str = ' (disabled)'
                debug_str = ''
                if self.__check_debug(2):
                    debug_str = ' [debug: {}]'.format(breakpoint)
                print('    {:2d}: {}:{}{}{}{}{}'.format(
                    breakpoint.local_id,
                    breakpoint.file_uri,
                    breakpoint.line_num,
                    cond_expr_str,
                    ignore_count_str,
                    disabled_str,
                    debug_str))
        return True

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
            frame.file_path, frame.line_num), file=fout)

    # "sel" = selected by the user
    def __print_sel_stack_trace(self):
        if self.__check_debug(3):
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
        if self.__check_debug(3):
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
        local_src_line_info = self._src_inspector.get_source_line(
                                    thread.file_name, thread.line_num)
        # local_src_line = ''
        remote_src_line = ''
        src_line = ''
        if local_src_line_info and local_src_line_info.text:
            local_src_line = local_src_line_info.text.strip()
        if thread.code_snippet:
            remote_src_line = thread.code_snippet.strip()
        src_line = remote_src_line

        # REMIND: Enable this test when "missing code snippet" bug is fixed in Roku OS
        # if self.__check_debug(1):   # 1 = validate
        #     if local_src_line:
        #         # verify that the target is returning the correct line
        #         assert local_src_line == remote_src_line, \
        #                     f'local="{local_src_line}",remote="{remote_src_line}"'

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
        if self.__check_debug(5):
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
        if var.is_keys_case_sensitive:
            s += " casesensitive"
        if var.ref_count != None: # 0 is valid
            s += ' refcnt={}'.format(var.ref_count)
        if var.element_count != None:  # 0 is valid
            s += ' el_count:{}'.format(var.element_count)
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
        if self.__check_debug(9):
            print('debug: cli.__handle_cmd_line({}),mode={}'.format(cmd_line, self.__cmd_mode))
        if self.__cmd_mode == _CommandMode.BRIGHTSCRIPT:
            self.__handle_cmd_line_mode_bs(cmd_line)
        elif self.__cmd_mode == _CommandMode.COMMANDS:
            self.__handle_cmd_line_mode_cmd(cmd_line)
        else:
            print('WARN: bad command mode: {}'.format(self.__cmd_mode))

    def __handle_cmd_line_mode_bs(self, cmd_line):
        if self.__check_debug(2):
            print('debug: cli.__handle_cmd_line_mode_bs({})'.format(cmd_line))
        cmd_line_stripped = ''
        if cmd_line:
            cmd_line_stripped = cmd_line.strip()
        if len(cmd_line_stripped):
            if cmd_line_stripped == 'bs' or cmd_line_stripped == '.':
                self.__cmd_mode = _CommandMode.COMMANDS
            else:
                self.__send_execute_cmd(cmd_line)

    def __handle_cmd_line_mode_cmd(self, cmd_line):
        if self.__check_debug(9):
            print('debug: cli.__handle_cmd_line_mode_cmd({})'.format(cmd_line))
        cmd_spec, cmd_args_str = self.__get_cmd_and_args(cmd_line)
        if not cmd_spec:
            self.__prev_cmd_failed = True
            print('ERROR: unknown command: {}'.format(cmd_line))
            self.__hint_bs_to_run_bs.print()
        else:
            if not cmd_spec.args_are_optional:
                if cmd_spec.has_args:
                    if not cmd_args_str:
                        print('err: no args provided for command: {}'.format(
                            cmd_spec.cmd_str))
                        self.__prev_cmd_failed = True
                        return
                else:
                    if cmd_args_str:
                        print('err: args provided for command that takes none: {}'.\
                            format(cmd_spec.cmd_str))
                        self.__prev_cmd_failed = True
                        return
            ok = cmd_spec.func(cmd_spec, cmd_args_str)
            self.__prev_cmd_failed = not ok
            assert (ok != None), \
                    'cmd handler did not return a value for {}'.format(\
                        cmd_spec)
            done = not ok
            if done:
                if self.__check_debug(1):
                    print('debug: EXITING BECAUSE CMD HANDLER SAYS SO: {}'.\
                        format(cmd_spec))
                do_exit(0)

    # Get the command that starts with cmdPrefix, if there
    # is exactly one that matches. Returns None if cmdPrefix
    # matches none or is ambiguous.
    # @return _CmdSpec or None
    def __match_command(self, cmd_prefix):
        found = None
        try:
            if not cmd_prefix:
                return found

            found_cmds = []  # _CmdSpec(s)
            for cmd in self.__all_cmds:
                if not cmd.is_active:
                    continue

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
                if not cmd.is_visible:
                    continue

                if ((len(cmd_prefix) <= len(cmd_str)) and
                    (cmd_prefix == cmd_str[0:len(cmd_prefix)])):
                        found_cmds.append(cmd)

            found = None
            if len(found_cmds) < 1:
                pass
            elif len(found_cmds) > 1:
                dups = ''
                for cmd_spec in found_cmds:
                    if len(dups):
                        dups = dups + ','
                    dups = dups + cmd_spec.get_display_str()
                print('ERROR: Ambiguous command abbreviation: {} ({})'.format(
                        cmd_prefix, dups, file=self.__out_file))
            else:
                found = found_cmds[0]
        finally:
            if self.__check_debug(5):
                print('debug: cli.__match_command({}) -> {}'.format(
                    cmd_prefix, found))
        return found

    # break up args_str into the command and an argument string. The
    # returned cmd and/or args may be None
    # return: (cmd:CommandSpec|None, args:str|None)
    def __get_cmd_and_args(self, cmd_line):
        cmd_line = cmd_line.strip()
        cmd_parts = re.split('\\s', cmd_line, maxsplit=1)
        cmd_str = cmd_parts[0].strip()
        cmd_spec = self.__match_command(cmd_str)
        cmd_args_str = None
        if len(cmd_parts) >= 2 and len(cmd_parts[1].strip()):
            cmd_args_str = cmd_parts[1]

        return (cmd_spec, cmd_args_str)

    # @return true on success, false otherwise
    def __handle_cmd_backtrace(self, cmd_spec, args_str):
        if self.__check_debug(2):
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
            self.__debugger_client.send(cmd)
        else:
            self.__print_sel_stack_trace()
        return True

    # @return true on success, false otherwise
    def __handle_cmd_continue(self, cmd_spec, args_str):
        if self.__check_debug(2):
            print('debug: cli.__handle_cmd_continue()')
        if not self.__check_stopped():
            return True
        cmd = DebuggerRequest_Continue()
        self.__debugger_client.send(cmd)
        return True

    # Move down one in the thread's call stack
    # "down" means toward the first function called (the head of the call stack)
    def __handle_cmd_down(self, cmd_spec, args_str):
        fout = self.__out_file
        if self.__check_debug(1):
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

    # REMIND: Add -r option to recursively dump variables
    def __handle_cmd_show_impl(self, cmd_spec, args_str):
        if self.__check_debug(2):
            print('debug: _handle_cmd_show_impl("{}")'.format(args_str))
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
        path_force_insensitive = []
        for i in range(len(var_path)):
            if var_path[i].endswith("/i"):
                var_path[i] = var_path[i][0:-2]
                path_force_insensitive.append(True)
            else:
                path_force_insensitive.append(False)
        caller_data = None

        cmd = DebuggerRequest_Variables(
            self.__sel_thread_index,
            self.__sel_thread_stack_index,
            var_path,
            path_force_insensitive,
            get_child_keys,
            caller_data)

        self.__debugger_client.send(cmd)
        return True

    # @return True on success, False otherwise
    def __handle_cmd_help(self, cmd_spec, args_str):
        if self.__check_debug(2):
            assert (not args_str) or \
                        (len(args_str) and \
                            (len(args_str) == len(args_str.strip())))

        if args_str:
            return self.__print_help_for_cmd(args_str)

        return self.__print_help_general(cmd_spec, args_str)

    def __print_help_general(self, cmd_spec, args_str):
        fout = self.__out_file
        print('Roku Remote Debugger Help', file=fout)
        print(file=fout)

        # Determine the proper column width(s)
        cmd_width = 0
        help_width = 0
        for cmd_entry in self.__all_cmds:
            if (not cmd_entry.is_visible) or (cmd_entry.is_separator):
                continue
            displayStr = cmd_entry.get_display_str(
                                cmd_entry.show_args_in_short_help)
            cmd_width = max(cmd_width, len(displayStr))
            if (cmd_entry.short_desc):
                help_width = max(help_width, len(cmd_entry.short_desc))
        # total_width = min(80, cmd_width + help_width + 2) # approximate

        # Print the help
        fmtStr = '{:' + str(cmd_width) + 's}  {}'
        for cmd_entry in self.__all_cmds:
            if not cmd_entry.is_visible:
                continue
            if cmd_entry.is_separator:
                sep = '----- {} -----'.format(cmd_entry.cmd_str)
                indent_str = ''
                for _ in range(int((cmd_width)/2)):
                    indent_str += ' '
                print('{}{}'.format(indent_str, sep))
            else:
                print(fmtStr.format(
                        cmd_entry.get_display_str(
                                        cmd_entry.show_args_in_short_help),
                        cmd_entry.short_desc),
                        file=fout)
        print(file=fout)
        print('Commands may be abbreviated; e.g., q = quit)', file=fout)
        fout.flush()
        return True

    def __print_help_for_cmd(self, cmd_prefix):
        fout = self.__out_file
        print(file=fout)
        cmd_spec = self.__match_command(cmd_prefix)
        if not cmd_spec:
            print('ERROR: Unknown command or abbreviation: {}'.format(cmd_prefix),
                    file=fout)
            self.__hint_bs_to_run_bs.print(force=True)
        else:
            example_args_str = ''
            if cmd_spec.example_args:
                example_args_str = ' {} '.format(cmd_spec.example_args)
            short_desc_str = ''
            if cmd_spec.short_desc:
                short_desc_str = cmd_spec.short_desc
            print('{}{} : {}'.format(
                cmd_spec.get_display_str(), example_args_str, short_desc_str),
                file=fout)
            if cmd_spec.long_help:
                print(file=fout)
                print('{}'.format(cmd_spec.long_help), file=fout)
            else:
                if self.__check_debug(2):
                    print('debug: no additional help available', file=fout)
            print(file=fout)
        return True

    # list the source code of the current function
    # @return True on success, False otherwise
    def __handle_cmd_list(self, cmd_spec, args_str):
        if self.__check_debug(2):
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
            self.__debugger_client.send(cmd)
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
    def __handle_cmd_show(self, cmd_spec, args_str):
        return self.__handle_cmd_show_impl(cmd_spec, args_str)

    # @return true if session should continue, false if we need to quit
    def __handle_cmd_quit(self, cmd_spec, argStr):
        if self.__debugger_client.is_fake:
            print('debug: NOT sending ExitChannel because --debug-no-sideload')
            global_config.do_exit(0)
        cmd = DebuggerRequest_ExitChannel()
        self.__debugger_client.send(cmd)
        return True

    # @return true if session should continue, false otherwise
    def __handle_cmd_status(self, cmd_spec, args_str):
        if self.__check_debug(2):
            print('debug: __handle_cmd_status()')
        print(self.__get_status_line(), file=self.__out_file)
        return True

    # @return true if session should continue, false otherwise
    def __handle_cmd_step(self, cmd_spec, args_str):
        return self.__handle_cmd_step_any(cmd_spec, StepType.LINE)

    def __handle_cmd_step_any(self, cmd_spec, step_type):
        assert isinstance(step_type, StepType)
        if self.__check_debug(2):
            print('debug: __handle_cmd_step_any({})'.format(step_type.name))
        if not self.__check_stopped():
            return True
        self.__set_target_state(_TargetState.STEPPING)
        self.__reset_sel_thread(self.__sel_thread_index)
        cmd = DebuggerRequest_Step(self.__sel_thread_index, step_type)
        self.__debugger_client.send(
                            cmd,
                            allow_update=True,
                            allowed_update_types=[UpdateType.THREAD_ATTACHED,
                                                  UpdateType.ALL_THREADS_STOPPED])
        return True

    # @return true if session should continue, false otherwise
    def __handle_cmd_stop(self, cmd_spec, argStr):
        if self.__check_debug(2):
            print('debug: __handle_cmd_stop()')
        fout = self.__out_file
        with self.__target_state_lock:
            if self.__target_state == _TargetState.STOPPED:
                print('Already stopped.', file=fout)
                return True
        print('Suspending threads...', file=fout)
        cmd = DebuggerRequest_Stop()
        self.__debugger_client.send(cmd)
        return True

    # Select one thread for inspection
    # This command only has effect locally, does not require a debugger command
    # @return True if command processing should continue, false otherwise
    def __handle_cmd_thread(self, cmd_spec, args_str):
        if self.__check_debug(2):
            print('debug: handle_cmd_thread({})'.format(args_str))
        if not self.__check_stopped():
            return True
        thread_index = None
        if not (args_str and len(args_str)):
            self.__print_sel_thread()
        else:
            try:
                thread_index = int(args_str)
            except Exception:
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
        if self.__check_debug(2):
            print('debug: __handle_cmd_threads({})'.format(args_str))
        caller_data = {CallerKey.LISTING_THREADS:True}
        cmd = DebuggerRequest_Threads(caller_data)
        self.__debugger_client.send(cmd)
        return True

    # Move up one in the thread's call stack
    # "up" means toward the last function called (the tail of the call stack)
    def __handle_cmd_up(self, cmd_spec, args_str):
        fout = self.__out_file
        if self.__check_debug(2):
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
        if self.__check_debug(2):
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
            None, # path_force_case_insensitive
            True) # get_child_keys
        self.__debugger_client.send(cmd)
        return True

    # Execute args as BrightScript, or enter interactive BrightScript mode
    # Return True if cmd processing should continue, false otherwise
    def __handle_cmd_bs(self, cmd_spec, args_str):
        if self.__check_debug(2):
            print('debug: __handle_cmd_bs(),args="{}"'.format(args_str))
        ok = True
        if not args_str or not len(args_str.strip()):
            if self.__cmd_mode == _CommandMode.BRIGHTSCRIPT:
                self.__cmd_mode = _CommandMode.COMMANDS
            else:
                # Can only execute BrightScript if target is stopped
                if self.__check_stopped():
                    self.__cmd_mode = _CommandMode.BRIGHTSCRIPT
        else:
            ok = self.__send_execute_cmd(args_str)
        return ok

    def __send_execute_cmd(self, src_str):
        thread_index = self.__sel_thread_index
        stack_index = self.__sel_thread_stack_index
        source_code = src_str
        if self.__check_debug(2):
            print('debug: __send_execute_cmd(),thridx{},stkidx={},src="{}"'.format(
                thread_index, stack_index, source_code))
        if not self.__check_stopped():
            return True
        if (thread_index == None) or (stack_index == None): # 0 is valid
            return True
        cmd = DebuggerRequest_Execute(
            thread_index, stack_index, source_code)
        self.__debugger_client.send(cmd)
        time.sleep(0.2) # wait for execution response
        return True


    ######################### BREAKPOINTS #############################

    # args_str must be of the form filename:line_num
    # @return True if cmd processing should continue, False otherwise
    def __handle_cmd_add_breakpoint(self, cmd_spec, args_str):
        if self.__check_debug(2):
            print('debug: handle_cmd_add_breakpoint({})'.format(args_str))
        if not args_str:
            args_str = ''
        dclient = self.__debugger_client

        # format is filename:line_num ignore_count(optional)
        # E.g., 'main.brs:10' 'main.brs:44 99'
        unmodified_file_uri = None
        breakpoint_str = None
        uri_scheme = None
        uri_path = None
        line_num_str = None
        line_num = None             # required
        ignore_count_str = None
        ignore_count = 0            # optional, default=0
        cond_expr = None

        # No args - read interactively
        if not len(args_str.strip()):
            args = {}
            args_str = self.__read_breakpoint_interactive()
            if not args_str:
                return True

        # EXAMPLE: addbreak lib:main.brs:10 0 x == 5
        # uri scheme (e.g., lib:, pkg:) is optional
        # file_path is required
        # line_num is required
        # ignore_count is optional
        # cond_expr is optional
        # token 4 (cond_expr) is optional

        # split into left-hand (filespec+line_num) and right-hand sides (ignore_count and cond_expr)
        breakpoint_tokens_lhs = re.split(':', args_str, maxsplit=2)
        breakpoint_tokens_rhs = None

        if len(breakpoint_tokens_lhs) < 2:
            print('ERR: Invalid breakpoint specification: {}'.format(args_str))
            return True

        breakpoint_tokens_rhs = re.split('\\s+', breakpoint_tokens_lhs[-1],
            maxsplit=2)
        breakpoint_tokens_lhs[-1] = breakpoint_tokens_rhs[0]
        del breakpoint_tokens_rhs[0]

        if len(breakpoint_tokens_lhs) == 3:
            # e.g., 'pkg:/source/main.brs:10'
            uri_scheme = breakpoint_tokens_lhs[0]
            uri_path = breakpoint_tokens_lhs[1]
            line_num_str = breakpoint_tokens_lhs[2]
            unmodified_file_uri = '{}:{}'.format(uri_scheme, uri_path)
        if len(breakpoint_tokens_lhs) == 2:
            # e.g., 'source/main.brs:10'
            # could also be 'pkg:/source/main.brs' which is invalid
            uri_scheme = 'pkg'
            uri_path = breakpoint_tokens_lhs[0]
            line_num_str = breakpoint_tokens_lhs[1]
            unmodified_file_uri = uri_path
        breakpoint_str = '{}:{}'.format(unmodified_file_uri, line_num_str)

        if breakpoint_tokens_rhs and len(breakpoint_tokens_rhs):
            ignore_count_str = breakpoint_tokens_rhs[0]
            if len(breakpoint_tokens_rhs) >= 2:
                cond_expr = breakpoint_tokens_rhs[1]
                if not dclient.has_feature(
                        ProtocolFeature.CONDITIONAL_BREAKPOINTS):
                    print('warn: conditional breakpoints not supported by target'
                            ', cond_expr ignored: {}'.format(cond_expr))
                    cond_expr = None

        # Line number (required)
        if not line_num_str or not len(line_num_str):
            print('ERR: Missing line number: {}'.format(breakpoint_str))
            return True
        try:
            line_num = int(line_num_str)
        except Exception:
            print('ERR: Invalid line number ({}): {}'.format(line_num_str, breakpoint_str))
            return True

        # Ignore count (optional)
        if ignore_count_str and len(ignore_count_str):
            try:
                ignore_count = int(ignore_count_str)
            except Exception:
                print('err: Invalid ignore_count in breakpoint: {}'.format(
                        ignore_count_str))
                return True

        if self.__debug_preserve_breakpoint_path:
            file_uri = unmodified_file_uri
        else:
            file_uri = uri_scheme + ':'
            if not uri_path.startswith('/'):
                file_uri += '/'
            file_uri += uri_path

        if self.__check_debug(2):
            print('debug: cli: breakpoint unmod_file_uri={},breakpoint_str={}'.format(
                unmodified_file_uri, breakpoint_str))

        breakpoints = [Breakpoint(file_uri, line_num, ignore_count, cond_expr)]

        cmd = None
        has_cond_expr = bool(breakpoints[0].cond_expr)
        if has_cond_expr and self.__check_debug(1): # 1 = validate
            assert dclient.has_feature(ProtocolFeature.CONDITIONAL_BREAKPOINTS)
        if dclient.has_feature(ProtocolFeature.CONDITIONAL_BREAKPOINTS):
            if has_cond_expr or \
                    dclient.has_feature(ProtocolFeature.CONDITIONAL_BREAKPOINTS_ALLOW_EMPTY_CONDITION):
                # Any breakpoint can be set with ADD_CONDITIONAL_BREAKPOINTS
                cmd = DebuggerRequest_AddConditionalBreakpoints(breakpoints)
            else:
                # Target has bug where a conditional breakpoint generates a
                # syntax error if cond_expr==""
                cmd = DebuggerRequest_AddBreakpoints(breakpoints)
        else:
            cmd = DebuggerRequest_AddBreakpoints(breakpoints)
        if self.__check_debug(2):
            print('debug: cli: sending: {}'.format(cmd))
        dclient.send(cmd)
        return True

    def __handle_cmd_remove_breakpoints(self, cmd_spec, args_str):
        if self.__check_debug(2):
            print('debug: cli: handle_cmd_remove_breakpoints({})'.format(
                    args_str))
        brk_mgr = self.__breakpoints
        delete_all = False
        local_id_strs = re.split('\\s+', args_str.strip())
        local_ids = list()
        for local_id_str in local_id_strs:
            local_id_str = local_id_str.strip()
            if local_id_str == '*':
                delete_all = True
                local_ids.clear()
                break
            try:
                local_ids.append(int(local_id_str))
            except Exception:
                print('warn: ignoring invalid breakpoint ID: "{}"'.format(
                        local_id_str))
        if not (delete_all or len(local_ids)):
            print('err: No breakpoint IDs specified')
            return True

        # This potentially performs linear searches wrapped in linear
        # searches. However, performance shouldn't be a problem,
        # because this only needs to move at the speed of the user,
        # and there should never be a huge number of breakpoints
        if delete_all:
            local_ids = list()
            for breakpoint in brk_mgr.breakpoints:
                local_ids.append(breakpoint.local_id)
        remote_ids = list()
        for local_id in local_ids:
            breakpoint = brk_mgr.find_breakpoint_by_local_id(local_id)
            if not breakpoint:
                print('warn: ignoring unknown breakpoint ID: {}'.format(
                        local_id))
            else:
                remote_ids.append(breakpoint.remote_id)

        if not len(remote_ids):
            if delete_all:
                print('No breakpoints, nothing to do')
            else:
                print('err: No valid breakpoint IDs specified')
            return True

        # We have valid remote breakpoint IDs
        cmd = DebuggerRequest_RemoveBreakpoints(remote_ids)
        self.__debugger_client.send(cmd)
        return True

    def __handle_cmd_disable_breakpoint(self, cmd_spec, args_str):
        if self.__check_debug(2):
            print('debug: cli: handle_cmd_disable_breakpoints({})'.format(
                    args_str))
        return True

    def __handle_cmd_enable_breakpoint(self, cmd_spec, args_str):
        if self.__check_debug(2):
            print('debug: cli: handle_cmd_enable_breakpoints({})'.format(
                    args_str))
        return True

    def __handle_cmd_list_breakpoints(self, cmd_spec, args_str):
        if self.__check_debug(2):
            print('debug: cli: handle_cmd_list_breakpoints({})'
                    ',targetstate={}'.format(
                        args_str, str(self.__get_target_state())))
        assert not args_str
        if self.__get_target_state() != _TargetState.STOPPED:
            # We can't request an update, print what we have
            self.__print_breakpoints()
        else:
            # Target is stopped -- request an update before printing
            cmd = DebuggerRequest_ListBreakpoints()
            self.__debugger_client.send(cmd)
        return True

    # Interactively prompts for a breakpoint specification
    # @param args_out is populated with file_spec, line_number, ignore_count,
    #                 cond_expr.
    # @return string that can be parsed by handle_cmd_add_breakpoints or None on failure
    def __read_breakpoint_interactive(self):
        if self.__check_debug(3):
            print('debug: cli: read_breakpoint_interactive()')
        file_domain = CompletionDomain.FILE_SPEC
        none_domain = CompletionDomain.NONE
        file = self.__read_line(  'breakpoint            file: ', file_domain).strip()
        if not len(file):
            print('No file specified. Abort.')
            return None
        line = self.__read_line(  'breakpoint            line: ', none_domain).strip()
        if not len(line):
            print('No line number specified. Abort.')
            return None
        else:
            try:
                int(line)
            except Exception:
                print('Invalid line number ({}). Abort.'.format(line))
                return None
        ignore = self.__read_line('breakpoint ignore count(0): ', none_domain, default='0')
        cond = None
        if self.__debugger_client.has_feature(ProtocolFeature.CONDITIONAL_BREAKPOINTS):
            cond = self.__read_line(  'breakpoint   cond_expr(""): ', none_domain)
        breakpoint_str = '{}:{} {} {}'.format(file, line, ignore, cond)
        if self.__check_debug(3):
            print('debug: cli: interactive breakpoint: {}'.format(breakpoint_str))
        return breakpoint_str


    ####################################################################
    #
    # Process debugger updates and responses
    #
    ####################################################################

    # @return void
    def __process_debugger_update(self, update):
        if self.__check_debug(9):
            print('debug: cli.__process_debugger_update(), update={}'.format(
                update))
        if not self.__examine_update_and_handle_errors(update):
            return
        self.__handle_update(update)

    # bool validateUpdate(update)
    # Sanity-checks the update and its associated request, if any. Exits this
    # script if serious errors are detected.
    # @return True if update should be handled, false if it should be ignored
    def __examine_update_and_handle_errors(self, update):
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

        # If err_code != OK, update is DebuggerResponse_Error
        handled = False
        err_code = update.err_code
        if err_code == ErrCode.OK:
            handled = False
        elif err_code == ErrCode.NOT_STOPPED:
            handled = True
            print('ERROR: target must be stopped, but is running (use "stop")')
        elif update.invalid_value_path_index != None: # 0 is valid
            handled = True
            print('ERROR: Invalid value in variable path: {}'.format(
                '.'.join(update.request.variable_path[0:update.invalid_value_path_index+1])))
        elif update.missing_key_path_index != None: # 0 is valid
            handled = True
            print('ERROR: Key or variable not found in variable path: {}'.format(
                '.'.join(update.request.variable_path[0:update.missing_key_path_index+1])))
        else:
            handled = True
            print('ERROR: error received from target: {} ({})'.format(
                err_code.value, err_code.name),
                file=fout)

        return not handled

    # Process an update from the debug target. The update may be a
    # response to a specific request or an update without a request.
    def __handle_update(self, update):
        if self.__check_debug(3):
            print('debug: cli: __handle_update({})'.format(update))
        fout = self.__out_file
        update_type = update.update_type
        request = update.request  # May be None
        cmd_code = None
        if request:
            cmd_code = request.cmd_code
        update_type = update.update_type

        if update_type == UpdateType.ALL_THREADS_STOPPED:
            self.__handle_update_all_threads_stopped(update)
        elif update_type == UpdateType.BREAKPOINT_ERROR:
            self.__handle_update_breakpoint_error(update)
        elif update_type == UpdateType.COMPILE_ERROR:
            self.__handle_update_compile_error(update)
        elif update_type == UpdateType.THREAD_ATTACHED:
            self.__handle_update_thread_attached(update)

        # The UpdateType for all responses to specific commands is
        # COMMAND_RESPONSE, so the actual type of the data is determined
        # by the CmdCode that was sent with the request.

        elif cmd_code == CmdCode.ADD_BREAKPOINTS:
            self.__handle_update_add_breakpoints(update)
        elif cmd_code == CmdCode.ADD_CONDITIONAL_BREAKPOINTS:
            self.__handle_update_add_breakpoints(update)
        elif cmd_code == CmdCode.CONTINUE:
            self.__set_target_state(_TargetState.RUNNING)
            print(file=fout)
        elif cmd_code == CmdCode.EXECUTE:
            self.__handle_update_execute(update)
        elif cmd_code == CmdCode.EXIT_CHANNEL:
            self.__set_target_state(_TargetState.TERMINATED)
            self.__is_connected = False
            print(file=fout)
            print(self.__get_status_line(),file=fout)
            do_exit(0)
        elif cmd_code == CmdCode.LIST_BREAKPOINTS:
            self.__handle_update_list_breakpoints(update)
        elif cmd_code == CmdCode.REMOVE_BREAKPOINTS:
            self.__handle_update_remove_breakpoints(update)
        elif cmd_code == CmdCode.STACKTRACE:
            self.__handle_update_stack_trace(update)
        elif cmd_code == CmdCode.STEP:
            pass
        elif cmd_code == CmdCode.STOP:
            self.__set_target_state(_TargetState.STOPPED)
        elif cmd_code == CmdCode.THREADS:
            self.__handle_update_threads(update)
        elif cmd_code == CmdCode.VARIABLES:
            self.__handle_update_variables(update)
        elif cmd_code == CmdCode.EXECUTE:
            self.__handle_update_execute(update)
        else:
            if self.__check_debug(1):
                msg = 'debug:cli: err: unrecognized update: {}'.format(update)
                print(msg)
                raise AssertionError(msg)

        if self.__check_debug(3):
            print('debug: cli: __handle_update() done')

    def __handle_update_add_breakpoints(self, update):
        if self.__check_debug(3):
            print('debug: cli: handle_update_add_breakpoints({})'.format(update))
        assert update.request
        assert update.request.cmd_code == CmdCode.ADD_BREAKPOINTS or \
                update.request.cmd_code == CmdCode.ADD_CONDITIONAL_BREAKPOINTS

        request = update.request
        brk_mgr = self.__breakpoints

        # NB: breakpoint_update is not a Breakpoint
        index = -1
        for breakpoint_update in update.breakpoints:
            index += 1
            breakpoint_request = request.breakpoints[index]
            new_breakpoint = Breakpoint(
                    breakpoint_request.file_uri,
                    breakpoint_request.line_num,
                    breakpoint_request.ignore_count,
                    breakpoint_request.cond_expr)

            # remote ID will be invalid (0), if breakpoint creation failed
            new_breakpoint.set_remote_id(breakpoint_update.remote_id)
            brk_mgr.add_or_update_breakpoint(new_breakpoint)

        if self.__check_debug(5):
            print('debug: cli: after add breakpoints: breakmgr={}'.format(brk_mgr))
            brk_mgr.debug_dump()

        num_breakpoints = brk_mgr.count_breakpoints()
        s = ''
        if num_breakpoints != 1:
            s = 's'
        print('Breakpoint added or updated, now {} breakpoint{}'.format(
                brk_mgr.count_breakpoints(), s))

    def __handle_update_all_threads_stopped(self, update):

        if self.__waiting_for_initial_stopped_message:
            # This version of the protocol always sends an
            # ALL_THREADS_STOPPED update immediately upon launch
            self.__waiting_for_initial_stopped_message = False
            if not self.__stop_target_on_launch:
                # Silently tell target to continue
                self.__debugger_client.send(DebuggerRequest_Continue(self.__protocol_version))
                return

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
                    _COMMAND_PROMPT,
                    get_stop_reason_str_for_user(
                        update.stop_reason, update.stop_reason_detail)),
                file=fout)
        print('', file=fout)
        cmd = DebuggerRequest_Threads(caller_data=
            {CallerKey.STOPPING:{
                _LITERAL_PRIMARY_THREAD_INDEX:update.primary_thread_index}})
        self.__debugger_client.send(cmd)

    def __handle_update_breakpoint_error(self, update):
        if self.__check_debug(3):
            print('debug: cli: __handle_update_breakpoint_error({})'.format(update))
        if self.__check_debug(1): # 1 = silent validation
            assert update
            assert not update.request

        breakpoint = self.__breakpoints.find_breakpoint_by_remote_id(update.breakpoint_id)
        if self.__check_debug(1):  # 1 = silent validation
            assert breakpoint

        print()
        print('BREAKPOINT ERROR:')
        print(    '  breakpoint ID: {}'.format(update.breakpoint_id))
        if breakpoint:
            print('           file: {}'.format(breakpoint.file_spec))
            print('           line: {}'.format(breakpoint.line_number))
            print('     expression: {}'.format(breakpoint.cond_expr))
            # local ignore_count value is likely not valid
        else:
            print(' INTERNAL ERROR: breakpoint info not found')
        for err in update.compile_errors:
            print('    compile err: {}'.format(err))
        for err in update.runtime_errors:
            print('    runtime err: {}'.format(err))
        for err in update.other_errors:
            print('      other err: {}'.format(err))
        print()


    def __handle_update_execute(self, update):
        if self.__check_debug(3):
            print('debug: cli: __handle_update_execute({})'.format(update))
        assert update
        assert update.request

        # Report errors, if any
        if self.__debugger_client.has_feature(ProtocolFeature.EXECUTE_RETURNS_ERRORS):

            if update.run_success:
                if self.__check_debug(2):
                    print('debug: Livecompile success!')

            else:
                print('Livecompile failed')
                err_count = 0
                if update.run_stop_code:
                    print('Runtime stop reason = {}'.format(update.run_stop_code))
                if self.__check_debug(2):
                    print('debug: compile error count: {}'.format(len(update.compile_errors)))
                for err in update.compile_errors:
                    err_count += 1
                    print('{}'.format(err))
                if self.__check_debug(2):
                    print('debug: runtime error count: {}'.format(len(update.runtime_errors)))
                for err in update.runtime_errors:
                    err_count += 1
                    print('{}'.format(err))
                if self.__check_debug(2):
                    print('debug: other error count: {}'.format(len(update.other_errors)))
                for err in update.other_errors:
                    err_count += 1
                    print('{}'.format(err))
                if not err_count:
                    print('WARNING: Possible bug in target: failure reported without enumerated errors')

                if len(update.compile_errors):
                    cmd_spec, _ = self.__get_cmd_and_args(update.request.source_code)
                    if cmd_spec:    # user entered a debuger command while in bs interpreter
                        self.__hint_bs_to_exit_interpreter.print(force=True)

    def __handle_update_list_breakpoints(self, update):
        if self.__check_debug(3):
            print('debug: cli: handle_update_list_breakpoints({})'.format(update))
        assert update
        assert update.request
        brk_mgr = self.__breakpoints
        if self.__check_debug(1):   # validation
            if len(update.breakpoint_infos) != brk_mgr.count_breakpoints():
                raise AssertionError('mismatched breakpoint count: local={},remote={}'.format(
                    brk_mgr.count_breakpoints(), len(update.breakpoint_infos)))
        for info in update.breakpoint_infos:
            if info.remote_id < 1:
                print('warn: list breakpoints response includes'
                        ' invalid breakpoint_id (should never happen)')
                if self.__check_debug(1):
                    assert not 'bad breakpoint_id in response'
            brk_mgr.add_or_update_breakpoint(info)
            # REMIND: verify local list == remote list, adjust if necessary
        self.__print_breakpoints()

    def __handle_update_remove_breakpoints(self, update):
        if self.__check_debug(3):
            print('debug: cli: handle_update_remove_breakpoints({})'.format(update))
        assert update
        assert update.request
        request = update.request
        brk_mgr = self.__breakpoints
        removed_count = 0
        i = -1
        for info in update.breakpoint_infos:
            i += 1
            request_breakpoint_id = request.breakpoint_ids[i]
            if info.err_code == ErrCode.OK:
                breakpoint = brk_mgr.find_breakpoint_by_remote_id(info.remote_id)
                if breakpoint:
                    brk_mgr.remove_breakpoint_by_local_id(breakpoint.local_id)
                    removed_count += 1
                else:
                    if global_config.verbosity >= Verbosity.NORMAL:
                        print('warn: removed breakpoint remote ID not found: {}'.format(info.remote_id))
                    if self.__check_debug(1): # 1==validation
                        raise AssertionError('breakpoint remote ID not found locally: {}'.format(info.remote_id))
            else:
                # Removal of this breakpoint failed
                print(
                    'warn: Attempt to remove nonexistent breakpoint, remote_id={}'\
                        .format(request_breakpoint_id))
                if self.__check_debug(1):
                    assert not 'bad remote breakpoint_id'

        num_remaining = brk_mgr.count_breakpoints()
        s1 = ''
        if removed_count != 1:
            s1 = 's'
        print('{} breakpoint{} removed, {} remaining'.format(
            removed_count, s1, num_remaining))

    def __handle_update_stack_trace(self, update):
        if self.__check_debug(3):
            print('debug: handle_update_stack_trace({})'.format(update))
        request = update.request
        assert request
        assert request.thread_index != None     # 0 is valid

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
                if self.__check_debug(1):
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
                    None,  # path_force_case_insensitive
                    True,  # get_child_keys (children are the local vars)
                    caller_data=caller_data)
                self.__debugger_client.send(cmd)

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
                if self.__check_debug(1):
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
                    None,  # path_force_case_insensitive
                    True,  # get_child_keys (children are the local vars)
                    caller_data=caller_data)
                self.__debugger_client.send(cmd)

        # LISTING A FUNCTION

        elif self.__request_has_caller_key(request,
                                            CallerKey.LISTING_FUNCTION):
            self.__list_selected_function()

        # USER REQUEST FOR SPECIFIC STACK TRACE

        else:
            self.__print_stack_trace(update)

    def __handle_update_compile_error(self, update):
        if self.__check_debug(3):
            print('debug: handle_update_compile_error({})'.format(update))
        print('COMPILE ERROR: {}'.format(update.format_for_user()))

    def __handle_update_thread_attached(self, update):
        if self.__check_debug(3):
            print('debug: handle_update_thread_attached({})'.format(update))
        # Get more info about the new thread before announcing it
        self.__set_target_state(_TargetState.STOPPED)
        caller_data = {CallerKey.THREAD_ATTACHED:update}
        cmd = DebuggerRequest_Threads(caller_data=caller_data)
        self.__debugger_client.send(cmd)

    def __handle_update_threads(self, update):
        if self.__check_debug(3):
            print('debug: handle_update_threads({})'.format(update))
        self.__threads = update.threads
        request = update.request
        caller_data = None

        # find the primary/selected thread
        primary_thread_index = -1
        primary_count = 0
        for i in range(len(update.threads)):
            thread = update.threads[i]
            if thread.is_primary:
                primary_thread_index = i
                primary_count += 1
        if self.__check_debug(1):   # 1 == validate
            assert primary_count == 1

        if self.__request_has_caller_key(request, CallerKey.STOPPING):
            caller_data = dict(request.caller_data) # dup it
            self.__set_sel_thread(primary_thread_index)

            # Stopped for any number of reasons, provide details
            cmd = DebuggerRequest_Stacktrace(
                self.__sel_thread_index, caller_data=caller_data)
            self.__debugger_client.send(cmd)
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
        if self.__check_debug(3):
            print('debug: __handle_update_variables({})'.format(update))
        assert update and isinstance(update, DebuggerUpdate)
        request = update.request
        assert request
        if self.__check_debug(9):
            update.dump(self.__out_file,
                line_prefix='debug: __handle_update_variables: ')
        self.__sel_thread_vars = update

        # STOPPING ALL THREADS

        if self.__request_has_caller_key(request, CallerKey.STOPPING):
            # This is the last request needed to provide a stop/crash dump
            self.__print_crash_dump()

            if self.__check_debug(1): # 1 = validate
                self.__validate_when_stopped()

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
        if self.__check_debug(5):
            print('debug: cli: has_caller_key({},{}) -> {}'.format(request, key, ret_val))
        return ret_val

    # An update has been received from the debuggee, which may be the
    # the response to a request, or it may be an unsolicited change of
    # state.
    # Called on a separate thread by the DebuggerListener
    # @return True if more updates are expected, false if not
    def update_received(self, response):
        request = response.request
        if self.__check_debug(5):
            print('debug:cli: update_received(response={})'.format(request))

        self.__debugger_update_queue.put(response)  # thread-safe
        with self.__input_cond_var:
            self.__input_cond_var.notify_all()

        # Short-circuit an exited response -- the connection should be closed
        if request and (request.cmd_code == CmdCode.EXIT_CHANNEL):
            return False
        if self.__check_debug(9):
            print('debug:cli: update_received() done')
        return True

    # Called on the user input processor thread
    def _user_input_received(self, cmd_line):
        if self.__check_debug(3):
            print('debug: cli.__user_input_received, cmdline={}'.format(cmd_line))
        self.__user_input_queue.put(cmd_line)  # thread-safe
        with self.__input_cond_var:
            self.__input_cond_var.notify_all()

    # Validate data after a stop has completed and all data retrieved
    def __validate_when_stopped(self):
        has_line_number_bug = self.__debugger_client.has_feature( \
                ProtocolFeature.BAD_LINE_NUMBER_IN_STACKTRACE_BUG)
        tests_passed = []
        if self.__check_debug(3):
            print(f'debug: cli: validate when stopped (linenum fixup={has_line_number_bug})')
        if not has_line_number_bug:
            thread = self.__threads[self.__sel_thread_index]
            stack_frame = self.__sel_thread_stack_info.get_frames()[-1]
            assert thread.line_num == stack_frame.line_num, \
                f'thread_line={thread.line_num},stack_line={stack_frame.line_num}'
            tests_passed.append('stacktrace_line==threads_line')
            if self.__check_debug(2):
                print(f'debug: cli: stopped: tests passed: {tests_passed}')

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level
#END class CommandLineInterface


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

def noop_func():
    pass

def do_exit(err_code, msg=None):
    global_config.do_exit(err_code, msg)
