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
# File: Test.py
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
# python more or less enforces the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import abc  # abstract base class
import enum, os, re, sys, time

from rokudebug.model.DebuggerRequest import CmdCode
from rokudebug.model.DebuggerResponse import UpdateType
from rokudebug.model.ProtocolVersion import ProtocolFeature, ProtocolVersion

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

@enum.unique
class TestState(enum.IntEnum):
    NOT_STARTED = enum.auto()
    RUNNING = enum.auto()
    DONE_SUCCESS = enum.auto()
    DONE_FAIL = enum.auto()

    def __str__(self):
        return '{}({})'.format(self.name, self.value)


# Key into the set used to track one channel stop event
@enum.unique
class EventUpdateKey(enum.IntEnum):
    ALL_THREADS_STOPPED = enum.auto()
    STACKTRACE = enum.auto()
    THREADS = enum.auto()
    VARIABLES = enum.auto()


# Abstract base class for all tests
class Test(abc.ABC):

    ignore = False          # If true, test is not instantiated

    # Subclasses must set some attributes in their own __init__()
    # For now, _test_mgr_data is opaque and contents may change in future versions
    @abc.abstractmethod
    def __init__(self, test_mgr):

        # Required: Attributes that must be set by subclass
        self.test_channel_name = None   # name of subdirectory that contains the test channel

        # Optional: Attributes that can be overidden but have reasonable default values for most tests
        test_name = re.sub('^Test', '', re.sub('Test$', '', self.__class__.__name__)).lower()
        self.name = test_name               # No spaces - used to reference test on command line
        self.stop_channel_on_launch = False # if True, test is run with channel stopped on first line of main()
                                            # If False, test is run after initial startup sequence
        self.description = 'Test {}'.format(self.name)
        self.timeout_seconds = 10
        self.min_protocol_version = ProtocolVersion(3,0,0)
        self.causes_connection_errors = False   # If True, don't fail test because
                                                # of connection or I/O errors

        # Protected: Attributes that can be accessed by a subclass
        if not test_mgr:
            raise ValueError('test_mgr required but not supplied')
        self._test_mgr = test_mgr
        self._protocol_version = None   # actual protocol test is running against

        # Private attributes
        self.__debug_level = 0
        self.__tracking = {}        # event_key -> set of updates seen for that event

    def __str__(self):
        return '{}[{}]'.format(self.__class__.__name__, self.str_params())

    ####################################################################
    ##### Public methods used by test driver
    ##### Subclasses generally should NOT override these
    ####################################################################

    # Returns elapsed seconds as float:
    # - 0.0 if the test is not started
    # - current elapsed time if this test is running
    # - total elapsed run time if the test is finished
    def get_elapsed_time_seconds(self) -> float:
        return self._test_mgr.get_test_elapsed_time_seconds(self)

    def is_running(self) -> bool:
        return self._test_mgr.test_is_running(self)

    def is_done(self) -> bool:
        return self._test_mgr.test_is_done(self)

    def get_state(self) -> object:
        return self._test_mgr.get_test_state(self)

    # A timeout is a failure of the test
    def is_timed_out(self) -> bool:
        return self._test_mgr.test_is_timed_out(self)


    ####################################################################
    ##### Tests (Subclasses) may override the methods below
    ####################################################################

    def start(self, debugger_client) -> bool:
        if self.__check_debug(1):   # 1 = internal validation
            assert debugger_client
            assert debugger_client.get_protocol_version()
        self._protocol_version = debugger_client.get_protocol_version()
        return True

    # If a string (not None) is returned, the command is executed as a
    # debugger command line. The default implementation always returns None,
    # subclasses can override to execute debugger commands.
    def get_next_cmd_line(self) -> str:
        return None

    # REMIND: COMMENT
    def handle_update(self, debugger_update) -> bool:
        pass

    # @param lines list of strings
    def examine_target_output(self, lines) -> None:
        pass

    # Called by the test manager to get an additional short one-line
    # annotation to be added to the final status line of this test.
    # @param final_test_state the final test state (TestState enum)
    def get_final_state_annotation(self, final_test_state):
        return None

    # Returned string is added to the str() return value
    def str_params(self) -> str:
        s = 'name={}'.format(self.name)
        test_mgr = self._test_mgr
        if test_mgr:
            s += ',state={}'.format(test_mgr.get_test_state(self))
        return s


    ####################################################################
    ##### "Protected" methods useful for subclasses
    ####################################################################

    # A subclass must use this to note that a test has finished
    def mark_done(self, final_state, done_detail_str=None):
        self._test_mgr.mark_test_done(self, final_state, done_detail_str)

    # Basic validation of update. Verifies:
    # - If the update is a response to a command, that it has an attached request
    # - If there is an attached request, that the request appears valid
    # If allow_error is False (the default), any update that is an error
    # will be flagged as a failure. Tests that validate error responses
    # should specify allow_error=True and inspect the error responses.
    # @return True if update is valid, False otherwise
    def _validate_update(self, update, allow_error=False) -> bool:
        request = None
        if update.update_type == UpdateType.COMMAND_RESPONSE:
            if not update.request:
                self.mark_done(TestState.DONE_FAIL,
                    'No request in command response: {}'.format(update))
                return False
            request = update.request
            if request.request_id <= 0:
                self.mark_done(TestState.DONE_FAIL, 'bad ID in request: {}'.format(request.request_id))
                return False
        else:
            if update.request:
                if update.request.cmd_code == CmdCode.STEP:
                    # For a step command, stopped or attached is expected and correlated
                    if update.update_type == UpdateType.ALL_THREADS_STOPPED or \
                            update.update_type == UpdateType.THREAD_ATTACHED:
                        return True
                else:
                    self.mark_done(TestState.DONE_FAIL,
                        'Request present in update but should not be: {}'.format(update))
                    return False

        if update.is_error and not allow_error:
            self.mark_done(TestState.DONE_FAIL,
                           'unexpected error in update: {}'.format(update))
            return False

        return True

    # Returns True when all expected updates related to a channel stop have
    # been received and contain accurate data, False if more updates are
    # expected. This should be called for each debugger update, when a
    # channel stop is expected, and four updates are required to consider
    # the stop complete, in any order: all_threads_stopped, threads,
    # stacktrace, variables
    #
    # event_key must be unique for one stop event, and all calls to this
    # function to track the same stop must include the same event_key, to allow
    # for coordination of overlapping events, which do occur (e.g., thread_attached
    # and all_threads_stopped can cause overlapping threads/stacktrace requests)
    #
    # If the data in the updates is incorrect (e.g., the line number is
    # wrong), the stop will never be complete, this will endlessly return
    # False, and this test will likely time out.
    #
    # @param event_key must be unique for each stop event being tracked
    # @param annotation:str if not None, included in output text
    # @return True if channel stop is complete, False otherwise
    def _track_channel_stop(self, update, event_key,
            expected_stop_reason, expected_primary_thread_index,
            expected_src_file_uri, expected_src_file_line_num,
            annotation=None) -> bool:
        request = update.request

        # REMIND: primary_thread_index param is ignored and should be removed.
        # That is because the index of the primary thread is not predictable
        # as threads start and stop in the debug target. What's important
        # is that the updates that denote which thread is primary have
        # matching values on that thread, regardless of its index.

        if event_key not in self.__tracking:
            self.__tracking[event_key] = set()
        tracking = self.__tracking[event_key]

        if update.is_error:
            if self.__check_debug(2):
                print('debug: trackstop: update error={}'.format(update))

        elif update.update_type == UpdateType.ALL_THREADS_STOPPED:
            if self.__check_attr(update, 'stop_reason', expected_stop_reason,
                                                                annotation):
                tracking.add(EventUpdateKey.ALL_THREADS_STOPPED)

        elif request and request.cmd_code == CmdCode.THREADS:
            primary = update.get_primary_thread()
            if self.__check_attr(primary, 'stop_reason', expected_stop_reason,
                                                                annotation) and \
                    self.__check_attr(primary, 'file_name', expected_src_file_uri,
                                                                annotation) and \
                    self.__check_attr(primary, 'line_num', expected_src_file_line_num,
                                                                annotation):
                tracking.add(EventUpdateKey.THREADS)

        elif request and request.cmd_code == CmdCode.STACKTRACE:
            stack_frame = update.frames[-1]
            # Ignore line_num in versions that are known to have incorrect ones
            line_ok = self._protocol_version.has_feature(
                            ProtocolFeature.BAD_LINE_NUMBER_IN_STACKTRACE_BUG) or \
                        self.__check_attr(stack_frame, 'line_num',
                            expected_src_file_line_num, annotation)
            if line_ok and self.__check_attr(stack_frame, 'file_path', expected_src_file_uri,
                                                                annotation):
                tracking.add(EventUpdateKey.STACKTRACE)

        elif request and request.cmd_code == CmdCode.VARIABLES:
            tracking.add(EventUpdateKey.VARIABLES)

        stopped = tracking.issuperset({EventUpdateKey.ALL_THREADS_STOPPED,
            EventUpdateKey.STACKTRACE, EventUpdateKey.THREADS,
            EventUpdateKey.VARIABLES})
        if self.__check_debug(5):
            print('debug: trackstop: ({}) -> {}'.format(tracking, stopped))
        return stopped

    ####################################################################
    ##### PRIVATE METHODS
    ####################################################################

    # Safely verifies that obj has attr_name attribute. If expected_value
    # is not None, also verifies that the attribute's value matched.
    # Marks test as DONE_FAIL, if criteria are not met
    # @param msg if specified, included in any output
    # @return True if all is well, False if test has been marked DONE_FAIL
    def __check_attr(self, obj, attr_name, expected_value,
                     annotation=None) -> bool:
        try:
            val = getattr(obj, attr_name)
        except AttributeError:
            self.mark_done(TestState.DONE_FAIL, '{} does not have attr {}'.format( \
                obj, attr_name))
            return False
        # expected_value of "" and 0 must be checked
        if expected_value != None and val != expected_value:
            if self.__check_debug(2):
                print('debug: trackstop: unexpected attr value in update: attr={},expected={}'\
                        ',actual={},obj={}'.format(attr_name, expected_value,
                        val, obj))
            return False
        return True

    def __check_debug(self, min_level):
        level = max(self.__debug_level, global_config.debug_level)
        return level >= min_level

# END class Test
