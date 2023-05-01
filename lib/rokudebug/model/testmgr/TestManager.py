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
# File: TestManager.py
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
import sys, time

from rokudebug.model.Verbosity import Verbosity

from .OneTestData import _OneTestData
from .Test import Test, TestState
from .TestDir import TestDir

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

class AbstractTestManager(abc.ABC):

    @abc.abstractmethod
    def add_listener(self, listener) -> None:
        return None

    @abc.abstractmethod
    def get_current_test(self) -> None:
        return None

    @abc.abstractmethod
    def get_current_test_name(self) -> None:
        return None

    @abc.abstractmethod
    def count_tests(self) -> int:
        return 0

    @abc.abstractmethod
    def get_tests_sorted(self) -> list:
        return []

    @abc.abstractmethod
    def set_current_test(self, test_name) -> None:
        return None

    @abc.abstractmethod
    def current_test_is_running(self) -> bool:
        return False

    # Test has run and is complete, whether failed or successful
    @abc.abstractmethod
    def current_test_is_done(self) -> bool:
        return False


# Loads and runs tests
class TestManager(AbstractTestManager):

    class __Notification(object):
        def __init__(self, test, prev_test):
            self.test = test
            self.prev_test = prev_test

    def __init__(self, tmp_dir_path) -> None:

        self.__debug_level = 0
        self.__tmp_dir_path = tmp_dir_path
        self.test_dirs = []
        self.tests = {}
        self.__cur_test = None

        # Listeners are only set at startup, so there is (currently)
        #  no need for a mutex.
        self.__listeners = list()
        return None

    # When test changes, listener.test_changed(notification) is invoked,
    # where notification has attributes 'test' and 'prev_test'
    def add_listener(self, listener):
        self.__listeners.append(listener)

    # @ return True if test is running, False if test is not running or is None
    def test_is_running(self, test) -> bool:
        return test and test._test_mgr_data.state == TestState.RUNNING

    def current_test_is_running(self) -> bool:
        return self.test_is_running(self.__cur_test)

    # @ return True if test is done, False if test is not done or is None
    def test_is_done(self, test) -> bool:
        return test and (test._test_mgr_data.state == TestState.DONE_FAIL or \
                            test._test_mgr_data.state == TestState.DONE_SUCCESS)

    def current_test_is_done(self) -> bool:
        return self.test_is_done(self.__cur_test)

    # @return last state reported to the user, or None
    def get_test_last_user_reported_state(self, test) -> object:
        if not test:
            return None
        return test._test_mgr_data.last_user_reported_state

    def get_current_test_last_user_reported_state(self) -> object:
        return self.get_test_last_user_reported_state(self.__cur_test)

    def set_test_last_user_reported_state(self, test, state) -> None:
        if not test:
            return None
        test._test_mgr_data.last_user_reported_state = state

    def set_current_test_last_user_reported_state(self, state) -> None:
        return self.set_test_last_user_reported_state(self.__cur_test, state)

    def get_test_state(self, test) -> object:
        if not test:
            return None
        return test._test_mgr_data.state

    def get_current_test_state(self) -> object:
        return self.get_test_state(self.__cur_test)

    def get_test_elapsed_time_seconds(self, test) -> float:
        if not test:
            return 0.0
        elapsed = 0.0
        if self.test_is_running(test):
            elapsed = time.time() - test._test_mgr_data.start_time
        elif self.test_is_done(test):
            elapsed = test._test_mgr_data.end_time - test._test_mgr_data.start_time
        return elapsed

    def get_current_test_elapsed_time_seconds(self) -> float:
        return self.get_test_elapsed_time_seconds(self.__cur_test)

    def test_is_timed_out(self, test) -> bool:
        if not test:
            return False
        return self.get_test_elapsed_time_seconds(test) >= test.timeout_seconds

    def current_test_is_timed_out(self):
        return self.test_is_timed_out(self.__cur_test)

    def mark_test_if_timed_out(self, test) -> None:
        if not test:
            return None
        if self.test_is_timed_out(test):
            if not self.test_is_done(test):
                self.mark_test_done(test, TestState.DONE_FAIL, 'timeout at {:.3f}s (limit {}s)'.format(\
                    self.get_test_elapsed_time_seconds(test), test.timeout_seconds))

    def mark_current_test_if_timed_out(self) -> None:
        return self.mark_test_if_timed_out(self.__cur_test)

    def get_test_status_summary(self, test):
        if self.__check_debug(1): # 1 = validation
            assert test
            assert test._test_mgr
            assert test._test_mgr_data
        tm_data = getattr(test, '_test_mgr_data', None) if test else None
        if not test or not tm_data:
            return 'No test'

        state = tm_data.state
        s = ''
        if state == TestState.DONE_FAIL:
            s = 'TEST FAILED: '
        elif state == TestState.DONE_SUCCESS:
            s = 'TEST PASSED: '
        else:
            s = '{}: '.format(state)
        s = s + test.name
        s = s + ' elapsed={:.3f}s'.format(self.get_test_elapsed_time_seconds(test))
        detail = getattr(tm_data, 'done_detail_str', None)
        if detail:
            s = s + ': {}'.format(detail)
        return s

    def get_current_test_status_summary(self):
        return self.get_test_status_summary(self.__cur_test)

    def mark_test_done(self, test, final_state, done_detail_str=None) -> None:
        tm_data = test._test_mgr_data
        if self.__check_debug(1): # 1 = validation
            assert final_state == TestState.DONE_FAIL or final_state == TestState.DONE_SUCCESS
            assert tm_data
            # Verify success/fail state has not changed
            if final_state == TestState.DONE_SUCCESS:
                assert tm_data.state != TestState.DONE_FAIL
            elif final_state == TestState.DONE_FAIL:
                assert tm_data.state != TestState.DONE_SUCCESS
        if self.__check_debug(3):
            print('debug: mark_test_done(), test={}, final_state={}, detail={}'.format(\
                test.name, final_state, done_detail_str))

        annotation = test.get_final_state_annotation(final_state)
        if annotation:
            if done_detail_str:
                done_detail_str += ', {}'.format(annotation)
            else:
                done_detail_str = annotation

        tm_data.end_time = time.time()
        tm_data.state = final_state
        tm_data.done_detail_str = done_detail_str
        return None

    # test_obj or name can be: a Test instance, a string name, or None
    # @return Test instance if test set successfully, false otherwise
    def set_current_test(self, test_obj_or_name) -> object:
        prev_test = self.__cur_test
        test = None
        if test_obj_or_name:
            if isinstance(test_obj_or_name, str):
                test = self.get_test_by_name(test_obj_or_name)
            elif not isinstance(test_obj_or_name, Test):
                test = None

        self.__cur_test = test
        self.__notify_listeners(test, prev_test)
        return self.__cur_test

    # @return True if test started, false otherwise
    def start_current_test(self, debugger_client) -> bool:
        test = self.__cur_test
        if self.__check_debug(1): # 1 = validation
            assert test
            assert test._test_mgr_data.state == TestState.NOT_STARTED

        if not test:
            return False
        if test._test_mgr_data.state != TestState.NOT_STARTED:
            return False
        if debugger_client.get_protocol_version() < test.min_protocol_version:
            if global_config.verbosity >= Verbosity.NORMAL:
                print('ERROR: incompatible protocol, test requires {}, client is {}'.format(\
                    test.min_protocol_version, debugger_client.get_protocol_version()))
            return False

        if self.__check_debug(5):
            print('debug: testmgr: start test: {}'.format(test))

        debugger_client.set_save_target_output(True)
        test._test_mgr_data.state = TestState.RUNNING
        test._test_mgr_data.start_time = time.time()
        test.start(debugger_client)
        return True

    def get_current_test(self) -> object:
        return self.__cur_test

    # @return None if there is no current test
    def get_current_test_name(self) -> str:
        if not self.__cur_test:
            return None
        return self.__cur_test.name

    def load_dir(self, dir_path):
        test_dir = TestDir(self, dir_path, self.__tmp_dir_path)
        self.test_dirs.append(test_dir)
        for test in test_dir.get_tests():
            self.tests[test.name] = test

    def count_tests(self) -> int:
        return len(self.tests)

    # Gets a list of tests sorted by name
    def get_tests_sorted(self) -> list:
        test_list = []
        for name in sorted(self.tests.keys()):
            test_list.append(self.tests[name])
        return test_list

    def get_test_by_name(self, name) -> object:
        return self.tests.get(name, None)

    def get_test_channel_package_path(self, test) -> str:
        return test._test_mgr_data.channel_package_path


    ####################################################################
    # Internal methods that should not be called outside of this module
    ####################################################################

    def _init_test(self, test):
        test._test_mgr_data = _OneTestData()

    def _send_current_test_target_output(self, debugger_client):
        # Always retrieve the output to clear the buffer
        lines = debugger_client.get_target_output_lines()
        test = self.__cur_test
        if test and not self.test_is_done(test):
            test.examine_target_output(lines)


    ####################################################################
    # Private methods
    ####################################################################

    def __notify_listeners(self, test, prev_test):
        notification = TestManager.__Notification(test, prev_test)
        for listener in self.__listeners:
            listener.test_changed(notification)

    def __check_debug(self, lvl):
        return lvl <= max(self.__debug_level, global_config.debug_level)

