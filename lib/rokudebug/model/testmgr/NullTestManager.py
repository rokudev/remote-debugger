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
# File: NullTestManager.py
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

import sys

from .TestManager import AbstractTestManager

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level


# No-op test manager, used when no tests are specified
class NullTestManager(AbstractTestManager):

    def add_listener(self, listener) -> None:
        return None

    def get_current_test(self) -> None:
        return None

    def get_current_test_name(self) -> None:
        return None

    def count_tests(self) -> int:
        return 0

    def get_tests_sorted(self) -> list:
        return []

    def set_current_test(self, test_name) -> None:
        return None

    def current_test_is_running(self) -> bool:
        return False

    # Test has run and is complete, whether failed or successful
    def current_test_is_done(self) -> bool:
        return False
