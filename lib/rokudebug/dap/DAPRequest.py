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
# File: DAPRequest.py
# Requires python 3.5.3 or later
#
# Data types used specifically for interpreting requests from a Debug
# Adapter Protocol (DAP) client, typically an IDE. Common data types
# used by events, requests, and/or responses should be defined in
# DAPTypes.py, and not in this module.
#
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

import enum, sys

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level, do_exit()


# An output category that is used with DAPOutputEvent(s)
# Numeric values are only used internally within this script.
# Use to_dap_str() to get string that can be passed in a DAP message.
@enum.unique
class DAPEvaluateContext(enum.IntEnum):
    UNDEF = 0,
    HOVER = 1,      # Hover over identifier in IDE
    REPL = 2,       # User entered an expression in the IDE (interactive)
    WATCH = 3,

    # Get a string that can be part of a DAP message. Messages always
    # use these strings, and never integer values.
    def to_dap_str(self):
        if not self.value:
            if global_config.debug_level >= 1: assert self.value
            return None
        return self.name.lower()    # pylint: disable=no-member

    # Return UNDEF if dap_str is None or an unknown string
    @staticmethod
    def from_dap_str(dap_str):
        enum_val = DAPEvaluateContext.UNDEF
        try:
            enum_val = DAPEvaluateContext[dap_str.upper()]
        except: pass
        if global_config.debug_level >= 1:
            assert enum_val != DAPEvaluateContext.UNDEF, dap_str
        return enum_val
