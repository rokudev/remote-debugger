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
# File: NullDebuggerClient.py
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

from rokudebug.model.DebuggerClient import AbstractDebuggerClient


# Null debugger client that returns simplistic values, does not accept
# requests and does no processing. Using this avoids having to check
# debugger_client==None everywhere.
class NullDebuggerClient(AbstractDebuggerClient):

    def __init__(self, protocol_version):
        super().__init__(True)
        self.protocol_version = protocol_version

    # If suppress==True, connection errors are not reported to the user,
    # may be changed at any time.
    # This is useful during shutdown and for tests that test failure modes
    def set_suppress_connection_errors(self, suppress) -> None:
        pass

    def is_connected(self) -> bool:
        return False

    def get_protocol_version(self):
        return self.protocol_version

    # @param feature: enum ProtocolFeature
    def has_feature(self, feature):
        assert feature
        return self.protocol_version.has_feature(feature)

    # @return frozenset of ProtocolFeature(s)
    def get_features(self) -> frozenset:
        return frozenset()

    def get_pending_request_count(self):
        return 0

    def has_pending_request(self):
        return False

    def shutdown(self):
        pass
