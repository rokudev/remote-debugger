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


import argparse, atexit, enum, inspect, ipaddress, os, platform
import signal, sys, time, threading, traceback
from tempfile import mkstemp
from .AppInstallerClient import AppInstallerClient
from .CommandLineInterface import CommandLineInterface
from .DebuggerClient import DebuggerClient
from .MonotonicClock import MonotonicClock
from .SourceCodeInspector import SourceCodeInspector
from .Verbosity import Verbosity

VERSION_MAJOR        = 1                # int: major
VERSION_MINOR        = 0                # int: minor
VERSION_PATCH_LEVEL  = 0                # int: patch level
VERSION_BUILD_ID = '20191023T003300Z'   # str: typically, a timestamp

# We treat signals as names because not all enum values are
# available on all platforms
CTRL_BREAK_EVENT_LITERAL = 'CTRL_BREAK_EVENT'
CTRL_C_EVENT_LITERAL = 'CTRL_C_EVENT'
SIGHUP_LITERAL = 'SIGHUP'
SIGINT_LITERAL = 'SIGINT'
SIGTERM_LITERAL = 'SIGTERM'

@enum.unique
class RunMode(enum.IntEnum):
    DEBUG  = 1  # Upload and run the channel, attach to target debugger
    REMOVE = 2  # Remove installed channel
    RUN = 3     # Upload and run the channel, do not attach to debugger

    @staticmethod
    def to_option_string(runMode):
        return '--{}'.format(runMode.name.lower())


# This must be a global singleton
# After the singleton RokuDebug object is created, it can be accessed with:
#    sys.modules['__main__'].gMain
class RokuDebug(object):

    def __init__(self):
        global gMain
        gMain = self
        sys.modules['__main__'].gMain = gMain
        self.supported_protocol_versions = ([1],[1,0,0],[1,1,0],[1,1,1])
        self.test_name = None   # perform semi-automated test (private use only)
        self.verbosity = Verbosity.NORMAL
        self.__monotonic_clock = None   # set in main()
        self.__debug = 0     # Potentially bumped up, based on user args
        self.__cli = None  # Command-line interface
        self.__shutdown_lock = threading.Lock()
        self.__is_shut_down = False
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

    def main(self):
        self.__parse_args()
        self.__monotonic_clock = MonotonicClock(self.gDebugLevel)

        self.__install_signal_handlers()

        atexit.register(exit_handler)

        self.__print_startup_info()

        if not SourceCodeInspector(self.__channel_file).verify():
            do_exit(1, 'ERROR: Bad channel file: {}'.format(self.__channel_file))

        installer = AppInstallerClient(self.__target_ip, self.__target_pass)

        if self.__run_mode == RunMode.DEBUG:
            self.__debug_channel(installer)
        elif self.__run_mode == RunMode.REMOVE:
            self.__remove_channel(installer)
        elif self.__run_mode == RunMode.RUN:
            self.__run_channel(installer)
        else:
            raise AssertionError(
                'INTERNAL ERROR: bad run mode: {}'.format(self.__run_mode))

        do_exit(0)

    def get_supported_protocols_str(self):
        s = ''
        for one_ver in self.supported_protocol_versions:
            if len(s):
                s = s + ','
            one_ver_str = ''
            for velem in one_ver:
                if len(one_ver_str):
                    one_ver_str = one_ver_str + '.'
                one_ver_str = one_ver_str + str(velem)
            if len(one_ver) < 3:
                one_ver_str += '.x'
            s = s + one_ver_str
        return s

    def get_monotonic_time(self):
        return self.__monotonic_clock.get_time()

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
        except:
            err = 'signal not supported on platform (ignored): {}'.format(
                        signame)
            if self.gDebugLevel >= 5:
                print('debug: DUMPING EXCEPTION (IGNORED):')
                print(traceback.format_exc())

        if self.gDebugLevel >= 1:
            if err:
                print('debug: {}'.format(err))
            else:
                print('debug: signal handler installed: {}'.format(signame))

        self.__signal_name_to_enum[signame] = sig

    def __parse_args(self):
        self.__program_name = os.path.basename(sys.argv[0])
        useHelpStr = ' Use --help for help'

        #
        # Define arguments
        #
        parser = argparse.ArgumentParser()
        parser.description = 'Client for the Roku debugging protocol'
        parser.add_argument('--debug', dest='debug_channel',
            action='store_true',default=False,
            help = 'Upload, run, and debug channel (default)')
        parser.add_argument('-d', dest='debugLevel',
            action='count',default=0,
            help=argparse.SUPPRESS)
        parser.add_argument('--debuglevel', dest='debugLevel',type=int,
            action='store',default=0,
            help=argparse.SUPPRESS)
        parser.add_argument('--remove', dest='remove_channel',
            action='store_true', default=False,
            help = 'Remove the installed channel')
        parser.add_argument('--run','-r', dest='run_channel',
            action='store_true',default=False,
            help = 'Upload and run the channel, but do not debug it')
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
            help='Print version of this program and exit'
                 ' (if verbosity>=high, print build ID)')

        parser.add_argument('--xxx-test-name', dest='test_name',
            action='store', default=None, type=str,
            help=argparse.SUPPRESS)

        # Collect the channel file path
        parser.add_argument('channelFile', nargs='?')

        args = parser.parse_args()

        #
        # Validate and commit arguments
        #

        # Verbosity
        self.verbosity = Verbosity.from_int(args.verbosity)

        if (args.print_version):
            show_build_id = (self.verbosity >= Verbosity.HIGH)
            do_exit(0, '{} {}'.format(
                self.__program_name, self.get_version_str(show_build_id)))

        # Global debug level (can be overridden in modules)
        self.gDebugLevel = args.debugLevel  # global debug level
        self.__debug = max(self.__debug, self.gDebugLevel)

        # Target IP
        target_ip = args.target_ip
        if not target_ip:
            target_ip = os.environ.get('ROKU_DEV_TARGET', None)
        if target_ip:
            try:
                self.__target_ip = ipaddress.ip_address(target_ip)
            except:
                do_exit(1, 'bad target IP: {}.'.
                    format(target_ip)+useHelpStr)
        else:
            do_exit(1, '--targetip not specified, no environment variables found.' + useHelpStr)
        self.__target_ip = target_ip

        # Target app installer password
        target_pass = args.target_pass
        if not target_pass:
            target_pass = os.environ.get('ROKU_DEV_PASSWORD', None)
        if not target_pass:
            target_pass = os.environ.get('DEVPASSWORD', None)
        if not target_pass:
            import getpass
            target_pass = getpass.getpass('Password for {}: '.format(self.__target_ip))
        self.__target_pass = target_pass

        # Channel operation (debug/run, mutually exclusive)
        # channel_required must be set for each operation
        op_count = 0
        if args.debug_channel:
            op_count += 1
            channel_required = True
            self.__run_mode = RunMode.DEBUG
        if args.remove_channel:
            op_count += 1
            channel_required = False
            self.__run_mode = RunMode.REMOVE
        if args.run_channel:
            op_count += 1
            channel_required = True
            self.__run_mode = RunMode.RUN

        if not op_count:
            if gMain.gDebugLevel >= 1:
                print('debug: no operation, defaulting to --debug')
            self.__run_mode = RunMode.DEBUG
            channel_required = True
        elif op_count > 1:
            do_exit(1,
            'Zero or one of --debug --remove --run'
            ' must be specified, but multiple seen.'+useHelpStr)

        # channelFile
        if args.channelFile:
            if not channel_required:
                do_exit(1, 'channelFile not allowed with {}.{}'.format(
                    RunMode.to_option_string(self.__run_mode),useHelpStr))
            self.__channel_file = args.channelFile
        else:
            if channel_required:
                do_exit(1, 'Channel file required with {}.{}'.format(
                    RunMode.to_option_string(self.__run_mode),useHelpStr))
            self.__channel_file = None

        # test_name
        # This is the name of an automated or semi-automated test that
        # this script should run
        self.test_name = args.test_name
    # end of __parse_args()

    def __print_startup_info(self):
        if (self.gDebugLevel >= 1):
            if (self.verbosity < Verbosity.HIGH):
                self.verbosity = Verbosity.HIGH
                print('debug: verbosity set to high(3) because debug enabled')
            print('debug: debuglevel: {}'.format(self.gDebugLevel))
            print('debug:  verbosity: {}({})'.format(
                self.verbosity.name, self.verbosity.value))
        if (self.verbosity >= Verbosity.HIGH):
            print('info: {:>17s}: {}'.format(
                        self.__program_name, self.get_version_str()))
            print('info:         verbosity: {}({})'.format(
                    self.verbosity.name.lower(), self.verbosity.value))
            print('info:         this o.s.: {}'.format(' '.join(platform.uname())))
            print('info:          targetip: {}'.format(self.__target_ip))
            print('info:        targetpass: {}'.format(self.__target_pass))
            print('info: protocol versions: {}'.format(
                                        self.get_supported_protocols_str()))

        if self.test_name:
            print('debug: RUNNING DEBUG TEST: {}'.format(self.test_name))

    # [int,int,int,[int-or-string]] get_version()
    # Get the version number as an array. If includeBuild is False, returns:
    #     [int major, int minor, int patchlevel]
    # If includeBuild is True, returns:
    #     [int major, int minor, int patchlevel, int-or-string buildID]
    # The buildID is only included if includeBuild is True, and it may
    # be an int, or it may be a string (e.g., 'localbuild').
    @staticmethod
    def get_version(includeBuild=False):
        version = [VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH_LEVEL]
        if includeBuild:
            version.append(VERSION_BUILD_ID)
        return version

    @staticmethod
    def get_version_str(includeBuild=False):
        versionString = '{}.{}.{}'.format(
            VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH_LEVEL)
        if includeBuild:
            versionString += ' ' + VERSION_BUILD_ID
        return versionString

    def __debug_channel(self, app_installer):
        app_installer.remove()
        app_installer.install(self.__channel_file, remoteDebug=True)
        client = DebuggerClient(self.__target_ip)
        client.connect_control()
        if self.__debug >= 1:
            if client.has_bad_line_number_in_stop_bug:
                print('debug: client has "bad line number in stop" bug')
            else:
                print('debug: client'
                       ' DOES NOT have "bad line number in stop" bug')
        self.__check_protocol_version(client)
        self.__cli = CommandLineInterface(client, self.__channel_file)
        self.__cli.interact()

    def __remove_channel(self, app_installer):
        app_installer.remove()

    def __run_channel(self, app_installer):
        app_installer.remove()
        app_installer.install(self.__channel_file, remoteDebug=False)

    def __check_protocol_version(self, debugger_client):
        major_versions = set()
        for version in self.supported_protocol_versions:
            major_versions.add(version[0])
        if debugger_client.protocol_version[0] not in major_versions:
                msg = 'Unsupported protocol version: {}'.format(
                    debugger_client.get_protocol_version_str())
                print(msg, file=sys.stderr)
                print('Protocol versions supported are: {}'.format(
                    self.get_supported_protocols_str()), file=sys.stderr)
                do_exit(1, msg)

    # Returns path to tmp file. File will be deleted when this script exits
    def create_temp_file(self):
        fileInfo = mkstemp()
        os.close(fileInfo[0])
        self.__tmp_files.append(fileInfo[1])
        return fileInfo[1]

    def cleanup(self):
        if self.__debug >= 1:
            print('debug: RokuDebug:cleanup()')
        with self.__shutdown_lock:
            if self.__is_shut_down:
                return

            if self.__cli:
                self.__cli.shutdown()
                self.__cli = None
            for tmp_file in self.__tmp_files:
                if (os.path.exists(tmp_file)):
                    print("removing temp file: {:s}".format(tmp_file))
                    os.remove(tmp_file)

            self.__is_shut_down = True

    # Always called on main thread (the same one that called main())
    def _signal_handler(self, signum, frame):

        debug_level = self.gDebugLevel
        if debug_level >= 1:
            ident = None
            for key,value in self.__signal_name_to_enum.items():
                if value == signum:
                    ident = key
            print('debug: dumping stack traces on signal {},ident={}'.format(
                       signum, ident))
            traceback.print_stack()

        # Local reference to avoid race to destruction
        cli = gMain.__cli
        exitNow = False
        name_to_enum = self.__signal_name_to_enum
        if (signum == name_to_enum[SIGHUP_LITERAL]) or \
                (signum == name_to_enum[SIGTERM_LITERAL]):
            if cli:
                cli.shutdown()
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
                print('debug: ignoring signal {}'.format(
                    signum))
        if exitNow:
            do_exit(1, 'Exiting on signal {}'.format(signum))


# Always called on main thread (the same thread that invoked main())
def _global_signal_handler(signum, frame):
    gMain._signal_handler(signum, frame)

def exit_handler():
    gMain.cleanup()

def do_exit(exit_code, msg=None):
    out = sys.stdout
    sys.stdout.flush()
    sys.stderr.flush()
    if msg:
        if (exit_code):
            out = sys.stderr
            if (not msg.startswith('ERR')):
                msg = 'ERROR: {}'.format(msg)
        print(msg, file=out)

    gMain.cleanup()

    # We don't call sys.exit() because that only raises a
    # SystemExit exception. If this is called on a worker
    # thread, that will likely have no effect.
    os._exit(exit_code)
# Make this function available to all modules
sys.modules['__main__'].do_exit = do_exit
