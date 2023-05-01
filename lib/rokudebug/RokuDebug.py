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
#
# File: RokuDebug.py
# Requires python v3.5.3 or later
#
# This is a reference implementation of a command-line debugger that
# uses the BrightScript debugging protocol. That protocol was first
# included with Roku OS 9.2.
#
# NAMING CONVENTIONS:
#
# TypeNames are CamelCase
# CONSTANT_VALUES are CAPITAL_SNAKE_CASE
# all_other_identifiers are snake_case
# _protected members begin with a single underscore '_' (subclasses can access)
# __private members begin with double underscore: '__'
#
# python more or less enforces the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

# System imports
import argparse, atexit, datetime, enum, inspect, ipaddress, os, pathlib
import platform, signal, sys, tempfile, time, threading, traceback

# SystemExit only exits the current thread, so call it by its real name
ThreadExit = SystemExit

########################################################################
# GLOBAL CONFIGURATION
# This module is the main entry point for this package. Set up global
# config used by all modules.
# This must be done, PRIOR to importing other classes from this package.
########################################################################
class GlobalConfig(object):
    def __init__(self):
        # Add all attributes here, to make it clear what this object contains.
        # All values will be set when a RokuDebug instance is created.
        # done loading.

        # Data
        self.debug_level = 0             # global, may be bumped up locally
        self.verbosity = None

        # functions
        self.do_exit = None             # function: always use this to exit
        self.set_exit_code = None       # function: Override exit_code passed to do_exit()
        self.get_is_exiting = None      # function: is rokudebug exiting?
        self.get_monotonic_time = None
        self.get_version_str = None

global_config = GlobalConfig()
sys.modules['__main__'].global_config = global_config
from .model import Verbosity            # done after main.global_config set
global_config.verbosity = Verbosity.NORMAL
########################################################################

# Local imports
from tempfile import mkstemp
from .model import AppInstallerClient
from .model import DebuggerClient
from .model import DebugUtils
from .model import FakeDebuggerClient       # used for debugging
from .model import LibrarySourceSpecifier
from .model import MonotonicClock
from .model import ProtocolFeature
from .model import SourceCodeInspector
from .model.testmgr import NullTestManager, TestManager
from .model import Verbosity
from .model import get_supported_protocols_str, check_debuggee_protocol_version
from .cli import CommandLineInterface
from .dap import DebugAdapterProtocol

# When changing the version number, be sure to update SOFTWARE_REVISION_TIMESTAMP
VERSION_MAJOR        = 3            # int major
VERSION_MINOR        = 2            # int minor
VERSION_PATCH_LEVEL  = 0            # int patch level

# Software revision timestamp is similar to a build number, and is primarly
# used to differentiate between pre-release builds. It is milliseconds since
# 1970-01-01T00:00:00.000Z (64 bits) and must be updated when any change is
# made that may affect the behavior of this program.
# Calculate timestamp on linux: date -u +%s%3N    or   expr 1000 \* `date -u +%s`
SOFTWARE_REVISION_TIMESTAMP = 1675444061659 # 64-bit long int

# We treat signals as names because not all enum values are
# available on all platforms
CTRL_BREAK_EVENT_LITERAL = 'CTRL_BREAK_EVENT'
CTRL_C_EVENT_LITERAL = 'CTRL_C_EVENT'
SIGHUP_LITERAL = 'SIGHUP'
SIGINT_LITERAL = 'SIGINT'
SIGTERM_LITERAL = 'SIGTERM'

_rokudebug_main = None

# Validated set of options from the command line
class RokuDebugOptions(object):
    def __init__(self):
        self.channel_file = None
        self.dap_log_file_path = None
        self.no_execute = False
        self.run_mode = RunMode.CLI
        self.stop_target_on_launch = False
        self.target_ip = None
        self.target_pass = None

@enum.unique
class RunMode(enum.IntEnum):
    DAP = enum.auto()     # Run as a Debug Adapter Protocol server/bridge
    CLI = enum.auto()     # Go to command-line interface, don't load channel
    DEBUG = enum.auto()   # Upload and run the channel, attach to debuggee. go to CLI
    REMOVE = enum.auto()  # Remove installed channel
    RUN = enum.auto()     # Upload and run the channel, do not attach to debuggee

    def to_option_str(self):
        return '--{}'.format(self.name.lower()) # pylint: disable=no-member

    def to_user_str(self):
        return self.name.lower()    # pylint: disable=no-member


# This is the primary entry point for this script. Must be a global singleton.
class RokuDebug(object):
    __lifecycle_lock = threading.RLock()
    __lifecycle_cond_var = threading.Condition(lock=__lifecycle_lock)

    def __init__(self):
        with RokuDebug.__lifecycle_lock:
            self.__init_nolock()

    def __init_nolock(self):
        global _rokudebug_main
        assert not _rokudebug_main      # enforce singleton
        self._debug_level = 0           # debug level for this object

        self.__orig_stdin = None        # set in main()
        self.__orig_stderr = None       # set in main()
        self.__orig_stdout = None       # set in main()

        self.options = RokuDebugOptions()

        # REMIND: These should be moved to the module, so that do_exit()
        # will work when there is no global object instance.
        self._exit_now = False
        self._exit_cond_var = threading.Condition(lock=threading.Lock()) # main thread waits on this
        self._exit_code = None          # None is not 0

        self.__tmp_dir = None           # Created iff needed, use self.get_tmp_dir_path()
        self.__test_mgr = None          # Always exists, may have no tests
        self.__lib_sources = []        # Source not in channel package (e.g., a library)
        self.__test_dirs = []           # Directories to load tests from
        self.__run_test_name = None     # Name of test to auto-run on startup
        self.__debug_fake_connection = False
        self.__interface_thread = None  # runs cli, set in main()
        self.__monotonic_clock = None   # set in main()
        self.__debugger_client = None
        self.__cli = None  # Command-line interface
        self.__dap = None   # Debug Adapter Protocol (IDE integration)

        # output controller has four stream-like attributes:
        #    localout, localerr, targetout, targeterr
        self.__output_controller = None

        # protected with RokuDebug.__lifecycle_lock
        self.__is_shut_down = False
        self.__is_cli_running = False

        # Unsupported signals will have a value of None
        self.__signal_name_to_enum = {
            CTRL_BREAK_EVENT_LITERAL:None,
            CTRL_C_EVENT_LITERAL:None,
            SIGHUP_LITERAL:None,
            SIGINT_LITERAL:None,
            SIGTERM_LITERAL:None
        }
        self.__valid_signals = {'C'}

        self.__tmp_files = []  # automatically deleted upon exit

        # Set global attributes, used by other modules
        global_config.get_monotonic_time = self.get_monotonic_time
        global_config.get_version_str = self.get_version_str
        # global_config.do_exit was set when this module was loaded

        # module-global reference used by signal handlers and maybe others
        _rokudebug_main = self

    def main(self):
        try:
            try:
                return self.__main_impl()
            except ThreadExit: raise	# Normal exit
            except BaseException as e:
                print('INTERNAL ERROR: Exception in main():')
                traceback.print_exc(file=sys.stderr)
                # do_exit() raises ThreadExit exception on this main (non-daemon) thread
                do_exit(1, 'INTERNAL ERROR: exception in main(): {}'.format(e))
        except ThreadExit as e:
            # Normal shutdown path - exiting this thread exits the process
            # Wait for all daemon threads to exit, because if they try
            # to print anything while this thread terminates, the python
            # interpreter will have a hissy fit and dump a core.
            self.shutdown()
            raise e
        raise AssertionError('Should not reach this line')

    def __main_impl(self):
        atexit.register(exit_handler)
        self.__install_signal_handlers()
        self.__orig_stdin = sys.stdin
        self.__orig_stderr = sys.stderr
        self.__orig_stdout = sys.stdout
        self.__parse_args()

        if self.options.no_execute:
            self.__validate_files_and_exit()

        self.__monotonic_clock = MonotonicClock(global_config.debug_level)
        self.__print_startup_info()
        self.__init_test_mgr()

        if self.options.run_mode == RunMode.DAP:
            self.__dap = DebugAdapterProtocol(self.__orig_stdin,
                            self.__orig_stdout)
            self.__dap.start()
        else:
            self.__interface_thread = threading.Thread(
                    name='Interface', target=self, daemon=True)
            self.__interface_thread.start()

        # Idle and wait for events, signals, and interrupts. This thread
        # sits idle because only this initial/main thread can exit this
        # process cleanly, because sys.exit() (and the ThreadExit exception)
        # are ignored on other threads. Also, python will deliver all
        # signals to this initial/main thread.
        if self.__check_debug(3):
            print('debug:main: main() idling...')
        with self._exit_cond_var:
            while not self._exit_now:
                # As a backup, poll periodically without a cond_var
                # notification.
                self._exit_cond_var.wait(5)

        if self.__check_debug(2):
            print('debug: main thread exits')
        do_exit(0)
    # END main_impl()

    # Invoked with --no-execute to validate files in self.options
    # Exits script, never returns
    def __validate_files_and_exit(self):
        options = self.options
        assert options.no_execute
        if options.run_mode in (RunMode.DEBUG, RunMode.RUN):
            assert options.channel_file
            path = pathlib.Path(options.channel_file)
            err_msg = None
            if path.is_dir():
                err_msg = 'Is a directory (not a file): {}'.format(path)
            elif not os.access(path, os.R_OK):
                err_msg = 'File does not exist, or is not readable: {}'.format(
                    path)
            if err_msg:
                if global_config.verbosity >= Verbosity.ERRORS_ONLY:
                    print(err_msg, file=sys.stderr)
                do_exit(1)
            if global_config.verbosity >= Verbosity.NORMAL:
                print('Would {}: {}'.format(
                    options.run_mode.to_user_str(), path))
        do_exit(0)

    # Called on self.__interface_thread
    def __call__(self):
        try:
            self.__run_interface()
        except ThreadExit: raise
        except: # Yes, catch EVERYTHING
            traceback.print_exc()
            global_config.do_exit(1, 'INTERNAL ERROR: Uncaught exception')

    # Called on self.__interface_thread
    def __run_interface(self):
        if self.__check_debug(2):
            print('debug: rdb.__run_interface(), mode={}'.format(
                self.options.run_mode.name))
        if self.options.channel_file:
            if not SourceCodeInspector(self.options.channel_file).verify():
                do_exit(1, 'ERROR: Bad channel file: {}'.format(
                                        self.options.channel_file))

        installer = AppInstallerClient(self.options.target_ip,
                        self.options.target_pass)

        if self.options.run_mode == RunMode.CLI:
            self.__start_plain_cli(installer)
        elif self.options.run_mode == RunMode.DEBUG:
            self.__debug_channel(installer)
        elif self.options.run_mode == RunMode.REMOVE:
            self.__remove_channel(installer)
        elif self.options.run_mode == RunMode.RUN:
            self.__run_channel(installer)
        else:
            raise AssertionError(
                'INTERNAL ERROR: bad run mode: {}'.format(self.options.run_mode))

        do_exit(0)

    def get_monotonic_time(self):
        return self.__monotonic_clock.get_time()

    def get_tmp_dir_path(self):
        with self.__lifecycle_lock:
            if not self.__tmp_dir:
                self.__tmp_dir = tempfile.TemporaryDirectory(prefix='rrdb_')
        return self.__tmp_dir.name

    def __install_signal_handlers(self):
        # MS-Windows (and probably other platforms) don't support signals,
        # or may have different signal symbols/numbers. Let's try each
        # one and handle failure gracefully
        for sig_name in self.__signal_name_to_enum.keys():
            self.__install_one_signal_handler(sig_name)

    def __install_one_signal_handler(self, signame):
        sig = None
        err = None
        try:
            sig = getattr(signal, signame, None)
            if not sig:
                raise ValueError(signame)
            signal.signal(sig, _global_signal_handler)
        except Exception:
            err = 'signal not supported on platform (ignored): {}'.format(
                        signame)
            if global_config.debug_level >= 5:
                print('debug: main: DUMPING EXCEPTION (IGNORED):')
                print(traceback.format_exc())

        if global_config.debug_level >= 1:
            if err:
                print('debug: main: {}'.format(err))
            else:
                print('debug: main: signal handler installed: {}'.format(signame))

        self.__signal_name_to_enum[signame] = sig

    # Upon return, self.options will be in a consistent state, with
    # no conflicting options. If a consistent state could not be achieved
    # with the provided command-line options, exits this script with
    # an error.
    def __parse_args(self):
        self.__program_name = os.path.basename(sys.argv[0])
        use_help_str = ' Use --help for help'
        options = self.options

        ################################################################
        # PRIORITY ARGUMENT PARSING
        # Process arguments that take effect early and affect the behavior
        # of other options, regardless of their order on the command line.
        ################################################################


        ##### PRIORITY 0 ARGS #####
        # Arguments that affect other arguments, regardless of order

        # If Debug Adapter Protocol (DAP) is specified, stdin/stdout
        # are used for the protocol and NO other I/O can go to those
        # streams. Redirect immediately.
        add_arg_dap = lambda parser: \
            parser.add_argument('--dap', dest='dap',
                action='store_true', default=False,
                help = 'Expect Debug Adapter Protocol on stdin/stdout.'
                       ' (IDE Integration)')

        add_arg_dap_log_file = lambda parser: \
            parser.add_argument('--dap-log', dest='dap_log_file_path',
                action='store', type=str, default=None,
                help='Output file for errors and warnings, when in DAP mode')

        # Never redirect anything if --no-execute specified
        add_arg_no_execute = lambda parser: \
            parser.add_argument('--no-execute', '-n',
                action='store_true', default=False,
                help='Validate command-line arguments, but do not'
                     ' perform any actions')

        parser = argparse.ArgumentParser(add_help=False)
        add_arg_dap(parser)
        add_arg_dap_log_file(parser)
        add_arg_no_execute(parser)
        args, _ = parser.parse_known_args()

        if args.dap:
            options.run_mode = RunMode.DAP
            raise NotImplementedError(
              'Sorry, the Debug Adapter Protocol (DAP)'
                  ' needs maintenance and has been disabled')
        if args.dap_log_file_path:
            if options.run_mode != RunMode.DAP:
                do_exit(1, '--dap-log only valid with --dap')
        else:
            if args.dap:
                do_exit(1, '--dap requires --dap-log')
        options.dap_log_file_path = args.dap_log_file_path
        options.no_execute = args.no_execute

        if options.run_mode == RunMode.DAP:
            self.__redirect_for_dap()   # if no_execute, only validates

        # Avoid using stale objects, below
        _ = None
        parser = None
        args = None


        ##### PRIORITY 1 ARGS #####
        # More options that affect other options, regardless of order

        def add_arg_debug_level(parser, include_help):
            help_arg = 'Debug this script: 1=silent validation, 2-10=more output' \
                if include_help else argparse.SUPPRESS
            parser.add_argument('--debug-level', dest='debug_level', type=int,
                action='store',default=0,
                help=help_arg)

        def add_arg_debug(parser, include_help=True):
            help_arg = 'Upload, run, and debug channel (default)' \
                    if include_help else argparse.SUPPRESS
            parser.add_argument('--debug', dest='debug_channel',
                action='store_true',default=False,
                help = help_arg)

        def add_arg_long_help(parser, include_help):
            help_arg = 'Show long help with debugging and test options, then exit' \
                if include_help else argparse.SUPPRESS
            parser.add_argument('--long-help', dest='long_help',
                action='store_true',default=False,
                help=help_arg)

        parser = argparse.ArgumentParser(add_help=False)
        add_arg_debug_level(parser, False)
        add_arg_long_help(parser, False)
        # --debug is not a high-priority arg, but it needs to be here
        # so that it is ignored for now, rather than being interpreted
		# as --debug-level.
        add_arg_debug(parser, False)
        args, _ = parser.parse_known_args()

        # Global debug level (can be overridden in modules)
        global_config.debug_level = args.debug_level  # global debug level

        if options.run_mode == RunMode.DAP and self.__check_debug(2):
            print('debug: Testing stdout redirect to DAP log')
            print('debug: Testing stderr redirect to DAP log', file=sys.stderr)

        show_long_help_and_exit = args.long_help

        # Make sure we don't use stale objects, below
        parser = None
        args = None
        _ = None



        ################################################################
        # Normal argument parsing
        # All of the options are parsed here, so that they will all show
        # up in help. Some options may be re-parsed, but the result
        # should be identical.
        ################################################################

        #
        # Define arguments
        # ArgumentParser help lists these arguments, in the order they
        # are added.
        parser = argparse.ArgumentParser()
        parser.description = 'Client for the Roku debugging protocol'
        add_arg_long_help(parser, True)
        add_arg_dap(parser)
        add_arg_dap_log_file(parser)
        add_arg_debug(parser)
        add_arg_no_execute(parser)
        parser.add_argument('--remove', dest='remove_channel',
            action='store_true', default=False,
            help = 'Remove the installed channel')
        parser.add_argument('--run','-r', dest='run_channel',
            action='store_true',default=False,
            help = 'Upload and run the channel, but do not debug it')
        parser.add_argument('--stop-on-launch', '-s',
            dest='stop_target_on_launch',
            action='store_true',default=False,
            help = 'Stop target immediately upon launch, allows'
                    ' breakpoints to be set prior to execution'),
        parser.add_argument('--targetip','-t', dest='target_ip',
            action='store',type=str,
            help='IP Address of the target Roku device.'
                 ' If not specified, looks at ROKU_DEV_TARGET'
                 ' environment variable.')
        parser.add_argument('--targetpass','-p', dest='target_pass',
            action='store',type=str,
            help='Password for the target device app installer.'
                 ' If not specified, looks at 1) ROKU_DEV_PASSWORD'
                 ' environment variable, 2) DEVPASSWORD env var.'
                 ' 3) If still not found, interactively'
                 ' asks for password.')
        # REMIND: Add better descriptions of verbosity levels
        parser.add_argument('-v', dest='verbosity',
            action='count',default=Verbosity.NORMAL.value,
            help = 'Increase verbosity by one (may be used multiple times)')
        parser.add_argument('--verbosity', dest='verbosity',
            action='store',default=Verbosity.NORMAL.value,type=int,
            help = 'Set verbosity level 0=silent|1=errors|2=normal|3=high')

        parser.add_argument('--version', '--Version', '-V', dest='print_version',
            action='store_true',default=False,
            help='Print version of this program and exit')

        # Collect the channel file path
        parser.add_argument('channel_file', nargs='?')

        ################################################################
        # Options that only appear with --long-help
        # --debug-* arguments are used to debug this script.
        add_arg_debug_level(parser, show_long_help_and_exit)
        parser.add_argument("--debug-fake-connection", dest='debug_fake_connection',
            action='store_true', default=False,
            help='Don\'t sideload channel, go straight into command-line with fake connection.'\
                ' Useful when developing this script'
                if show_long_help_and_exit else argparse.SUPPRESS)

        # allow breakpoints like "components/KeyHandler.brs" w/o lib: or pkg: URI scheme
        parser.add_argument('--debug-preserve-breakpoint-path',
            dest='debug_preserve_breakpoint_path',
            action='store_true', default=False,
            help='Don\'t add pkg: and lib: prefixes to breakpoint paths'\
                if show_long_help_and_exit else argparse.SUPPRESS)

        # load additional source directory
        parser.add_argument('--add-lib-src', dest='lib_src',
            action='append', default=[],
            help='Add source for library: "mylibname:/path/to/source"'
                ', may be used multiple times')

        # load external tests
        # dest appears in help, so name it accordingly: --add-test-dir TEST_DIR
        parser.add_argument('--add-test-dir', dest='test_dir',
            action='append', default=[],
            help='Load external tests, may be used multiple times'\
                if show_long_help_and_exit else argparse.SUPPRESS)

        # load external tests
        # dest appears in help, so name it accordingly: --run-test TEST_NAME
        parser.add_argument('--run-test', dest='test_name', type=str,
            action='store', default=None,
            help='Run an externally-loaded test'\
                if show_long_help_and_exit else argparse.SUPPRESS)

        ################################################################

        if show_long_help_and_exit:
            parser.parse_args(['--help'])
            if self.__check_debug(1):
                raise AssertionError('parse_args() did not exit')
        args = parser.parse_args()

        #
        # Validate and commit arguments
        #

        # debuglevel has already been set, above

        if (args.print_version):
            do_exit(0, '{} {}'.format(
                self.__program_name, self.get_version_str(True)))

        # Stop on launch
        options.stop_target_on_launch = args.stop_target_on_launch

        # Target IP
        target_ip = args.target_ip
        if not target_ip:
            target_ip = os.environ.get('ROKU_DEV_TARGET', None)
        if target_ip:
            try:
                options.target_ip = ipaddress.ip_address(target_ip)
            except Exception:
                do_exit(1, 'bad target IP: {}.'.
                    format(target_ip)+use_help_str)
        else:
            do_exit(1, '--targetip not specified, no environment variables found.' + use_help_str)
        options.target_ip = target_ip

        # Target app installer password
        target_pass = args.target_pass
        if not target_pass:
            target_pass = os.environ.get('ROKU_DEV_PASSWORD', None)
        if not target_pass:
            target_pass = os.environ.get('DEVPASSWORD', None)
        if not target_pass:
            import getpass
            target_pass = getpass.getpass('Password for {}: '.format(options.target_ip))
        options.target_pass = target_pass

        # Verbosity
        global_config.verbosity = Verbosity.from_int(args.verbosity)

        # Channel operation (debug/run, mutually exclusive)
        # channel_required must be set for each operation
        run_modes_selected = []
        if args.dap:
            channel_required = False
            options.run_mode = RunMode.DAP
            run_modes_selected.append(options.run_mode.to_option_str())
        if args.debug_channel:
            channel_required = True
            options.run_mode = RunMode.DEBUG
            run_modes_selected.append(options.run_mode.to_option_str())
        if args.remove_channel:
            channel_required = False
            options.run_mode = RunMode.REMOVE
            run_modes_selected.append(options.run_mode.to_option_str())
        if args.run_channel:
            channel_required = True
            options.run_mode = RunMode.RUN
            run_modes_selected.append(options.run_mode.to_option_str())
        if args.test_name:
            channel_required = False
            options.run_mode = RunMode.DEBUG
            run_modes_selected.append('--run-test')

        if not len(run_modes_selected):
            if args.channel_file:
                options.run_mode = RunMode.DEBUG
                channel_required = True
                if global_config.verbosity >= Verbosity.HIGH:
                    print('info: no mode specified for channel, defaulting to --debug')
            else:
                options.run_mode = RunMode.CLI
                channel_required = False
                if global_config.verbosity >= Verbosity.HIGH:
                    print('info: no mode and no channel specified, going to command line')
            run_modes_selected.append(options.run_mode.to_option_str())
        elif len(run_modes_selected) > 1:
            msg = 'Options are incompatible: {}'.format(' '.join(run_modes_selected))
            do_exit(1, msg)
        mode_arg = run_modes_selected[0]

        # channel_file
        if args.channel_file:
            if not channel_required:
                do_exit(1, 'channel file not allowed with {}.{}'.format(mode_arg, use_help_str))
            self.options.channel_file = args.channel_file
        else:
            if channel_required:
                do_exit(1, 'Channel file required with {}.{}'.format(mode_arg, use_help_str))
            self.options.channel_file = None

        self.__debug_fake_connection = args.debug_fake_connection
        if self.__debug_fake_connection:
            global_config.debug_level = max(global_config.debug_level, 1) # 1 = internal validation

        for lib_src_spec in args.lib_src:
            try:
                self.__lib_sources.append(LibrarySourceSpecifier(lib_src_spec))
            except ValueError as e:
                do_exit(1, 'bad library source specifier: {}'.format(e))

        if args.test_dir:
            self.__test_dirs = args.test_dir
            global_config.debug_level = max(global_config.debug_level, 1) # 1 = internal validation
        if args.test_name:
            self.__run_test_name = args.test_name
            global_config.debug_level = max(global_config.debug_level, 1) # 1 = internal validation

        self.__debug_preserve_breakpoint_path = args.debug_preserve_breakpoint_path
        if self.__debug_preserve_breakpoint_path:
            global_config.debug_level = max(global_config.debug_level, 1) # enable debug validation

    # END __parse_args()

    # REQUIRES: valid attributes self.options.dap,dap_log_file_path,no_execute
    # If no_execute, only verifies that log file is writeable
    # Exits this script if any error occurs
    def __redirect_for_dap(self):
        assert self.__orig_stdin
        assert self.__orig_stderr
        assert self.__orig_stdout
        assert self.options.run_mode == RunMode.DAP
        assert self.options.dap_log_file_path     # required with dap
        path = pathlib.Path(self.options.dap_log_file_path)
        if self.options.no_execute:
            if path.is_dir():
                do_exit(1, 'DAP log path is a directory (not a file): {}'.format(path))
            if path.exists():
                if not os.access(path, os.W_OK):
                    do_exit(1, 'DAP log file is not writeable: {}'.format(path))
            else:
                if not os.access(path.parent, os.W_OK):
                    do_exit(1, 'Directory not writeable: {}'.format(path.parent))
        else:
            try:
                new_out = open(path, mode='w')
            except OSError as e:
                do_exit(1, 'Could not write to {} ({})'.format(
                    path, e.strerror))
            sys.stdout = new_out
            sys.stderr = new_out

    def __print_startup_info(self):
        needs_hrule = global_config.debug_level >= 1 or global_config.verbosity >= Verbosity.HIGH
        if needs_hrule:
            print('------------------------------------------------------')
        if (global_config.debug_level >= 1):
            print('debug:     debuglevel: {}'.format(global_config.debug_level))
            print('debug:     validation: internal validation enabled (debuglevel > 0)')
            print('debug:      verbosity: {}({})'.format(
                global_config.verbosity.name, global_config.verbosity.value))
        if (global_config.verbosity >= Verbosity.HIGH) or (global_config.debug_level >= 2):
            if global_config.verbosity >= Verbosity.HIGH:
                pre = 'info: '
            else:
                pre = 'debug: '
            print('{} {:>18s}: {}'.format(
                    pre, self.__program_name, self.get_version_str()))
            print('{}          verbosity: {}({})'.format(
                    pre, global_config.verbosity.name.lower(), global_config.verbosity.value))
            print('{}          this o.s.: {}'.format(
                    pre, ' '.join(platform.uname())))
            print('{}           targetip: {}'.format(pre, self.options.target_ip))
            print('{}         targetpass: {}'.format(pre, self.options.target_pass))
            print('{}supported protocols: {}'.format(
                    pre, get_supported_protocols_str()))
            if self.__lib_sources:
                for lib_src in self.__lib_sources:
                    print('{}         lib source: {}'.format(
                        pre, lib_src))
            if self.__test_dirs:
                print('{}          test dirs: {}'.format(
                    pre, ', '.join(self.__test_dirs)))
            if self.__run_test_name:
                print('{}      auto-run test: {}'.format(pre, self.__run_test_name))
        if needs_hrule:
            print('------------------------------------------------------')

        sys.stdout.flush()      # Helpful when stdout redirected

    # [int,int,int,[int-or-string]] get_version()
    # Get the version number as an array. If includeBuild is False, returns:
    #     [int major, int minor, int patchlevel]
    # If includeBuild is True, returns:
    #     [int major, int minor, int patchlevel, int-or-string buildID]
    # The buildID is only included if includeBuild is True, and it may
    # be an int, or it may be a string (e.g., 'localbuild').
    @staticmethod
    def get_version(includeRevision=False):
        version = [VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH_LEVEL]
        if includeRevision:
            version.append(SOFTWARE_REVISION_TIMESTAMP)
        return version

    @staticmethod
    def get_version_str(includeRevision=False):
        versionString = '{}.{}.{}'.format(
            VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH_LEVEL)
        if includeRevision:
            rev_str = DebugUtils.revision_timestamp_to_str(SOFTWARE_REVISION_TIMESTAMP)
            versionString += ' ' + rev_str
        return versionString

    # Start the command-line interface without launching a channel
    def __start_plain_cli(self, app_installer):
        if self.__check_debug(2):
            print('debug: start_plain_cli()')
        self.__cli = CommandLineInterface(self.options.channel_file,
            self.__lib_sources, self.__output_controller,
            self.options.stop_target_on_launch, self.__test_mgr,
            self.__debug_preserve_breakpoint_path)

        with self.__lifecycle_lock:
            self.__is_cli_running = True
        try:
            self.__cli.interact(app_installer, None)
        finally:
            with self.__lifecycle_lock:
                self.__is_cli_running = False
                self.__lifecycle_cond_var.notify_all()

    def __debug_channel(self, app_installer):
        if self.__check_debug(2):
            print('debug: debug_channel()')

        # Create the debugger client
        dclient = None
        self.__cli = CommandLineInterface(self.options.channel_file,
            self.__lib_sources, self.__output_controller,
            self.options.stop_target_on_launch, self.__test_mgr,
            self.__debug_preserve_breakpoint_path)
        if self.__debug_fake_connection:
            if global_config.verbosity >= Verbosity.NORMAL:
                print('info: NOT side-loading channel, because --debug-fake-connection')
            self.__debugger_client = FakeDebuggerClient(self.__cli.update_received)
            dclient = self.__debugger_client
        else:
            app_installer.remove()
            app_installer.install(self.options.channel_file, remote_debug=True)
            self.__debugger_client = \
                DebuggerClient(self.options.target_ip,
                    self.__cli.update_received, sys.stdout)
            dclient = self.__debugger_client
            dclient.connect()
            self.__check_protocol_version(dclient, print_warnings=True)

        # Verify test is compatible with target
        test = self.__test_mgr.get_current_test()
        if test:
            protocol_version = self.__debugger_client.get_protocol_version()
            if protocol_version < test.min_protocol_version:
                print('ERROR: Incompatible protocol, required={},actual={}'.format(\
                    test.min_protocol_version.to_user_str(True),
                    protocol_version.to_user_str(True)))
                global_config.do_exit(1, 'Incompatible protocol')
            del test, protocol_version

        # Start the interface
        if self.__check_debug(2):
            print('debug: stop on launch: {}'.format(
                self.options.stop_target_on_launch))

        with self.__lifecycle_lock:
            self.__is_cli_running = True
        try:
            self.__cli.interact(app_installer, self.__debugger_client)
        finally:
            with self.__lifecycle_lock:
                self.__is_cli_running = False
                self.__lifecycle_cond_var.notify_all()

    def __remove_channel(self, app_installer):
        app_installer.remove()

    def __run_channel(self, app_installer):
        app_installer.remove()
        app_installer.install(self.options.channel_file, remote_debug=False)

    # Exits this script if the target's protocol version is not supported
    def __check_protocol_version(self, debugger_client, print_warnings=False):
        check_debuggee_protocol_version(debugger_client.protocol_version)
        if print_warnings and \
                        (global_config.verbosity > Verbosity.ERRORS_ONLY):
            if self.options.stop_target_on_launch and \
                not debugger_client.has_feature(
                                ProtocolFeature.STOP_ON_LAUNCH_ALWAYS):
                    print('warn: disabling stop-on-launch'
                            ' (unsupported by debuggee)')
                    self.options.stop_target_on_launch = False

    # Returns path to tmp file. File will be deleted when this script exits
    def create_temp_file(self):
        fileInfo = mkstemp()
        os.close(fileInfo[0])
        self.__tmp_files.append(fileInfo[1])
        return fileInfo[1]

    # Blocks until all daemon threads have exited
    def shutdown(self):
        try:
            if self.__check_debug(2):
                print('debug: RokuDebug:shutdown() start')
            self.__shutdown_impl()
            if self.__check_debug(2):
                print('debug: RokuDebug:shutdown() complete')

        # Catch and print any exception here, because if daemon threads
        # have not yet exited, the python interpreter may freak out
        # and choose to dump core rather than printing the exception.
        except BaseException as e:
            traceback.print_exc(file=sys.stderr)
            print('INTERNAL ERROR: exception in shutdown(): {}'.format(e),
                  file=sys.stderr)

    # Blocks until all daemon threads have exited
    def __shutdown_impl(self):
        wait_for_cli_shutdown = False
        with self.__lifecycle_lock:
            if self.__is_shut_down:
                return

            # Disable reporting errors that normally occur during shutdown
            if self.__debugger_client:
                self.__debugger_client.set_suppress_connection_errors(True)

            # Shut down user interface
            if self.__cli:
                self.__cli.shutdown_async()
                self.__cli = None
                wait_for_cli_shutdown = True

            # Shut down the connection to the debug target
            # Close the debugger client explicitly, in case the user
            # interface has not been created (e.g., on a protocol mismatch)
            if self.__debugger_client:
                self.__debugger_client.shutdown()
                self.__debugger_client = None

        if wait_for_cli_shutdown:
            with self.__lifecycle_cond_var:
                while self.__is_cli_running:
                    self.__lifecycle_cond_var.wait(1.0)

        with self.__lifecycle_lock:
            # Clean up tmp files and whatnot
            self.cleanup()
            self.__is_shut_down = True

    # Cleanup tmp files, etc
    # This is often called twice during process exit: once while shutting
    # down this debugger, and once as python atexit hook
    def cleanup(self):
        if self.__check_debug(2):
            print('debug: RokuDebug: cleanup()')
        with self.__lifecycle_lock:
            for tmp_file in self.__tmp_files:
                if (os.path.exists(tmp_file)):
                    print("removing temp file: {:s}".format(tmp_file))
                    os.remove(tmp_file)
            self.__tmp_files = []

    # Sets the exit code that will be returned by this process to the OS,
    # overriding any value sent to do_exit(). This should only be called
    # when the exit sequence has begun.
    # @return the actual exit_code
    def set_exit_code(self, exit_code):
        # Locking is by far preferred, but don't deadlock during shutdown
        # sequence.
        locked = self._exit_cond_var.acquire(blocking=True, timeout=0.1)
        try:
            return self._set_exit_code_nolock(exit_code)
        finally:
            if locked:
                self._exit_cond_var.release()

    # Always creates self.__test_mgr. If test directories have been specified,
    # loads the tests in those directories.
    def __init_test_mgr(self) -> None:
        if self.__check_debug(1):   # 1 = validation
            assert not self.__test_mgr

        if self.__test_dirs:
            self.__test_mgr = TestManager(self.get_tmp_dir_path())
            if global_config.verbosity >= Verbosity.NORMAL:
                print('info: loading tests')
            for test_dir in self.__test_dirs:
                self.__test_mgr.load_dir(test_dir)
        else:
            self.__test_mgr = NullTestManager()

        if self.__run_test_name:
            if not self.__test_mgr.set_current_test(self.__run_test_name):
                print('FATAL: Test not found: {}'.format(self.__run_test_name))
                raise ThreadExit(1)

        if self.__test_mgr.get_current_test():
            test = self.__test_mgr.get_current_test()
            self.options.channel_file = self.__test_mgr.get_test_channel_package_path(test)
            self.options.stop_target_on_launch = test.stop_channel_on_launch

        if self.__check_debug(1): # 1 = validation
            assert self.__test_mgr

        return None

    # @return the new exit code
    # @see set_exit_code()
    def _set_exit_code_nolock(self, exit_code):
        if self.__check_debug(1):
            # This thread may have legitimately failed to get the lock
            assert not self._exit_cond_var.acquire(blocking=False), \
                        '*MAYBE* a locking problem in exit handling'

        if self._exit_code == None:
            self._exit_code = exit_code

        if self.__check_debug(2):
            print('debug: set_exit_code({}) -> {}'.format(exit_code,
                self._exit_code))
        return self._exit_code

    # Always called on main thread (the same one that called main())
    def _signal_handler(self, signum, frame):

        debug_level = global_config.debug_level
        if debug_level >= 2:
            ident = None
            for key,value in self.__signal_name_to_enum.items():
                if value == signum:
                    ident = key
            print('debug: dumping stack traces on signal {},ident={}'.format(
                       signum, ident))
            traceback.print_stack()

        # Local reference to avoid race to destruction
        cli = self.__cli
        exitNow = False
        name_to_enum = self.__signal_name_to_enum
        if (signum == name_to_enum[SIGHUP_LITERAL]) or \
                (signum == name_to_enum[SIGTERM_LITERAL]):
            if cli:
                cli.shutdown_async()
            else:
                exitNow = True
        elif (signum == name_to_enum[SIGINT_LITERAL]) or \
                (signum == name_to_enum[CTRL_BREAK_EVENT_LITERAL]) or \
                (signum == name_to_enum[CTRL_C_EVENT_LITERAL]):
            if cli:
                cli.stop_target()
            else:
                exitNow = True
        else:
            if debug_level >= 1:
                print('debug: main: ignoring signal {}'.format(
                    signum))
        if exitNow:
            do_exit(1, 'Exiting on signal {}'.format(signum))

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level


# END class RokuDebug

class _NullOutputController(object):

    def __init__(self):
        super().__init__()
        self.localerr = sys.stderr
        self.localout = sys.stdout
        self.targeterr = sys.stderr
        self.targetout = sys.stdout


# Always called on main thread (the same thread that invoked main())
def _global_signal_handler(signum, frame):
    _rokudebug_main._signal_handler(signum, frame)

def exit_handler():
    # print('debug: main: exit_handler()',file=sys.__stdout__)
    _rokudebug_main.cleanup()

# Sets the exit code that will be returned by this process to the OS,
# overriding any value sent to do_exit(). This should only be called
# when the exit sequence has begun.
# @return the actual exit_code
def set_exit_code(exit_code):
    global _rokudebug_main
    if _rokudebug_main:
        _rokudebug_main.set_exit_code(exit_code)
    else:
        if global_config.debug_level >= 1:
            raise AssertionError(
                'set_exit_code() called with no RokuDebug instance')
global_config.set_exit_code = set_exit_code     # make this available to all modules

# NB: python's SystemExit exception only exits one thread, so it is
# universally referred to as 'ThreadExit' in this set of scripts.
#
# This may be called on any thread and starts the shutdown sequence
# to exit this process. On the main thread, this throws a ThreadExit
# (AKA SystemExit) exception. On other threads, it sets the state and
# returns so the main thread can take care of the shutdown.
#
# If a shutdown is already in progress, additional calls to this function
# on non-main threads are ignored and assumed to be cascading errors (e.g.,
# I/O errors after sockets have been closed).
#
# This deals with Python's goofy exit handling. There appears to be no way
# for a thread other than main to cleanly exit this process. That's because
# sys.exit() raises a ThreadExit exception that is ignored, unless it is
# raised on the thread that called main(). Using os._exit() is not
# a good idea, because that does not invoke shutdown hooks.
# @see set_exit_code()
def do_exit(exit_code, msg=None) -> None:
    global _rokudebug_main
    exit_code_at_entry = exit_code
    on_main_thread = threading.current_thread() is threading.main_thread()
    if global_config.debug_level >= 2:
        print('debug: do_exit({}) onmainthread:{}'.format(
            exit_code_at_entry, on_main_thread))

    # Coordinate exit parameters with other threads
    #
    # If the exit lock cannot be acquired, proceed while unlocked
    # That's scary but we cannot lock up permanently while exiting

    condition = _rokudebug_main._exit_cond_var
    locked = condition.acquire(blocking=True, timeout=1)
    try:
        if global_config.debug_level >= 1: # 1 = internal validation
            assert locked
        if _rokudebug_main._exit_now:
            # Shutdown has started
            if global_config.debug_level >= 2 and msg:
                print('debug: ignoring exit msg after shutdown started: {}'.format(msg))
            msg = None  # Don't report cascading errors during shutdown
            exit_code = _rokudebug_main._exit_code # exit code was already set
        else:
            # Shutdown has not started (let's start it)
            _rokudebug_main._exit_now = True
            _rokudebug_main._exit_code = exit_code

    finally:
        if locked:
            condition.release()
            locked = False

    # Print the message, if provided

    if global_config.debug_level >= 2:
        # Make output easier to read
        # Don't do this at debug level 1, which is validation only
        sys.stdout.flush()
        sys.stderr.flush()
    if msg:
        out = sys.stdout
        if (exit_code):
            out = sys.stderr
            if not msg.startswith('FATAL'):
                msg = 'FATAL: {}'.format(msg)
        print(msg, file=out)

    if on_main_thread:
        # This is the only thread that can actually exit this process
        # raises ThreadExit exception
        sys.exit(exit_code)
global_config.do_exit = do_exit     # make this available to all modules

def is_exiting() -> bool:
    global _rokudebug_main

    # If the exit lock cannot be acquired, proceed while unlocked
    # That's scary but we cannot lock up permanently while exiting
    condition = _rokudebug_main._exit_cond_var
    locked = condition.acquire(blocking=True, timeout=1)
    try:
        return _rokudebug_main._exit_now
    finally:
        if locked:
            condition.release()
            locked = False
global_config.get_is_exiting = is_exiting
