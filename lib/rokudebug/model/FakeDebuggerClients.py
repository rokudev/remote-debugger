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
# File: FakeDebuggerClient.py
# Requires python v3.5.3 or later
#
# This file defines minimal stub classes, used for debugging. Primarily,
# these classes are used to debug this script's external interfaces,
# without requiring a target device.
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

import sys

from .ProtocolVersion import ProtocolVersion

_PROTOCOL_VERSION = ProtocolVersion(1,2,0)

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

# Minimal fake debugger client, used for debugging
class FakeDebuggerClient(object):

    def __init__(self, target_ip_addr):
        self.protocol_version = _PROTOCOL_VERSION
        self.is_fake = True

    # @param feature: enum ProtocolFeature
    def has_feature(self, feature):
        assert feature
        return self.protocol_version.has_feature(feature)

    def get_next_request_id(self):
        assert False, 'Attempt to use fake client for real purpose'


# Minimal stub, used for debugging
class FakeDebuggerControlListener(object):

    def __init__(self, debugger_client, update_listener):
        self.__debugger_client = debugger_client
        self.__update_listener = update_listener

    def add_pending_request(self, request, allow_update=False,
                            allowed_update_types=None):
        pass

    def has_pending_request(self):
        return False

    def get_pending_request_count(self):
        return 0
