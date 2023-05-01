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
# File: OneTestData.py
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

import sys, time

from .Test import TestState

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

# Management data about one test, typically attached directly to a test
# This class is internal to the TestManager and may change at any time.
# Tests should not rely on its contents, but should use TestManager methods.
class _OneTestData(object):
    def __init__(self):
        self.test_channel_path = None
        self.is_loaded = False
        self.start_time = None
        self.end_time = None
        self.state = TestState.NOT_STARTED
        self.last_user_reported_state = None    # Last state that was reported to the user
        self.done_detail_str = None

