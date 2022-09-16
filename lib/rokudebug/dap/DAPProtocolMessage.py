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
# File: DAPMessage.py
# Requires python 3.5.3 or later
#
# Base class(es) for all Debug Adapter Protocol (DAP) message,
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

import sys

from .DAPTypes import LITERAL
from .DAPUtils import to_debug_str

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level, do_exit()

# Base class for all messages sent over the Debug Adapter Protocol (DAP),
# in both directions. That includes requests, responses, and events.
class DAPProtocolMessage(object):
    def __init__(self, msg_type):
        self.type = msg_type        # str

        # The spec defines 'seq' as a required field, but it does not seem
        # to be actually required in messages from the adapter to the
        # client.
        self.seq = None         # int

    def __str__(self):
        return to_debug_str(self)
