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
# File: TestDir.py
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

import importlib, inspect, os, pathlib, re, sys, tempfile, traceback, zipfile

from rokudebug.model import Verbosity
from .Test import Test
from .OneTestData import _OneTestData

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

class TestChannelInfo(object):
    def __init__(self, name, package_path):
        self.name = name
        self.package_path = package_path

# Currently, this class is opaque to test subclasses and its contents may
# change in future versions.
# However, if additional configuration info is needed during test initialization,
# additional attributes could be added here to pass that information to a test.
class TestMgrData(object):
    def __init__(self):
        self.channel_package_path = None

# One directory that contains tests
# A test directory is a python module that must have an __init__.py
class TestDir(object):

    def __init__(self, test_mgr, dir_path, tmp_dir_parent_path) -> None:
        if global_config.verbosity >= Verbosity.HIGH:
            print('info: loading test dir: {} (tmp_dir={})'.format(dir_path, tmp_dir_parent_path))
        while dir_path.endswith(os.path.sep):
            dir_path = dir_path[0:-1]

        self._debug_level = 0
        self.__test_mgr = test_mgr
        self.__dir_path = dir_path
        self.__parent_dir_path = os.path.dirname(dir_path)
        self.__module_name = os.path.basename(dir_path)
        self.__module = None
        self.__tmp_dir_parent_path = tmp_dir_parent_path
        self.__tmp_dir = None       # dir obj (not a string), created iff needed, use self.get_tmp_dir_path()
        self.__test_classes = []
        self.tests = []
        self.test_channels = {}     # str:test_name -> TestChannelInfo

        if not self.__verify_readable_dir(dir_path, 'ERROR: Failed to load test directory '):
            return None

        if sys.path[0] != self.__parent_dir_path:
            sys.path.insert(0, self.__parent_dir_path)
        self.__module = importlib.import_module(self.__module_name)
        for module_obj in inspect.getmembers(self.__module):
            module_class = module_obj[1] if inspect.isclass(module_obj[1]) else None
            if module_class and issubclass(module_class, Test):
                if module_class.ignore:
                    if global_config.verbosity >= Verbosity.HIGHER:
                        print('info: ignoring test(ignore=True): {}:{}'.format(\
                            self.__parent_dir_path, module_class.__name__))
                else:
                    self.__test_classes.append(module_class)

        for test_class in self.__test_classes:
            self.__load_test_class(test_class)

        if global_config.verbosity >= Verbosity.NORMAL:
            if len(self.tests):
                if global_config.verbosity >= Verbosity.HIGHER:
                    print('info: tests loaded from {}:'.format(dir_path))
                    for test in self.tests:
                        print('info:    {} (loaded)'.format(test.name))
            else:
                print('info: no tests loaded from {}'.format(os.path.join(\
                    self.__parent_dir_path, self.__module_name)))
        return None

    def __verify_readable_dir(self, dir_path_str, err_prefix) -> bool:
        dir = pathlib.Path(dir_path_str)
        err_str = None
        entry_found = False
        try:
            for f in os.listdir(dir_path_str):
                entry_found = True
                break
        except Exception as e:
            err_str = '{}{}: {}'.format(err_prefix, dir_path_str, e)
        if not err_str and not entry_found:
            err_str = '{}{}: Directory empty or unreadable'.format(err_prefix, dir_path_str)

        if err_str:
            if global_config.verbosity >= Verbosity.ERRORS_ONLY:
                print(err_str)
            return False
        return True

    # REQUIRES: test_class is a class that is a subclass of rokudebug.model.test.Test
    # @return True on success, False otherwise
    def __load_test_class(self, test_class) -> bool:
        if self._check_debug(1): # 1 = validation
            assert test_class
            assert inspect.isclass(test_class)
            assert issubclass(test_class, Test)
        success = False
        test_name = test_class.__name__
        try:
            test = test_class(self.__test_mgr)
            self.__test_mgr._init_test(test)
            if not test.name:
                raise ValueError('name required but not set in {}'.format(test_name))
            if re.search(test.name, '\s'):
                raise ValueError('test has illegal name (whitespace?): "{}"'.format(test_name))
            test_name = test.name
            if test.test_channel_name:
                self.__assign_test_channel(test)
            test._test_mgr_data.is_loaded = True
            self.tests.append(test)
            success = True
        except Exception as e:
            if self._check_debug(2):
                traceback.print_exception(type(e), e, e.__traceback__)
            print('ERROR: Failed to load test {}: {} {}'.format(test_name, e.__class__.__name__, e))
        return success

    # Finds or builds test channel
    # @throw exception on failure
    def __assign_test_channel(self, test) -> None:
        if test.test_channel_name and test.name not in self.test_channels:
            self.__build_test_channel(test)

        # success
        test._test_mgr_data.channel_package_path = self.test_channels[test.name].package_path
        if self._check_debug(1): # 1 = validation
            assert test._test_mgr_data.channel_package_path
        return None

    # Packages the test channel and saves it in self.__test_channels
    # @throw exception on failure
    def __build_test_channel(self, test) -> None:
        if self._check_debug(1): # 1 = validation
            assert test.name not in self.test_channels
        if global_config.verbosity >= Verbosity.HIGH:
            print('info: packaging test channel: {}'.format(test.test_channel_name))

        success = False
        tmp_dir_path = self.__get_tmp_dir_path()
        channel_name = test.test_channel_name
        channel_package_path_str = os.path.join(tmp_dir_path, channel_name + '.zip')
        channel_root_path_str = os.path.join(self.__dir_path, channel_name)

        # Build the channel
        if (self._check_debug(2)):
            print('debug: building test channel package: {} from {}'.format(\
                channel_package_path_str, channel_root_path_str))
        channel_root_path = pathlib.Path(channel_root_path_str)
        if not self.__verify_readable_dir(channel_root_path,
                'ERROR: Failed to package test channel '):
            return None

        with zipfile.ZipFile(channel_package_path_str, 'w', allowZip64=False) as zip:
            paths = sorted(channel_root_path.rglob('*'))
            for path in paths:
                rel_path = path.relative_to(channel_root_path)
                zip.write(str(path), arcname=str(rel_path))

        self.test_channels[test.name] = TestChannelInfo(channel_name, channel_package_path_str)
        return None

    def __get_tmp_dir_path(self) -> str:
        if not self.__tmp_dir:
            self.__tmp_dir = tempfile.TemporaryDirectory(dir=self.__tmp_dir_parent_path,
                prefix='td_{}_'.format(self.__module_name[0:10]))
        return self.__tmp_dir.name

    def get_tests(self) -> list:
        return self.tests

    def _check_debug(self, lvl) -> bool:
        return max(global_config.debug_level, self._debug_level) >= lvl
