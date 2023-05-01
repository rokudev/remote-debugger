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
# File: Breakpoint.py
# Requires python v3.5.3 or later
#
# NAMING CONVENTIONS:
#
# TypeIdentifiers are CamelCase
# CONSTANTS_ARE CAPITALIZED_SNAKE_CASE
# all_other_identifiers are lower_snake_case
# _protected members begin with a single underscore '_' (avail to friends)
# __private members begin with double underscore: '__'
#
# python more or less enforces the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import sys

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

# Generally speaking, every file_uri should include a scheme (pkg:/, lib:/).
# The notable exception is mangling done in a temporary copy of a breakpoint
# when sending to a target that does not understand URIs directly.
class Breakpoint(object):
    def __init__(self, file_uri, line_num, ignore_count=0, cond_expr=None):
        if not (file_uri and line_num):
            raise ValueError
        self.__debug_level = 0
        self.__debug_level = max(global_config.debug_level, 0)
        self.file_uri = file_uri
        self.line_num = line_num
        self.local_id = None    # ID assigned locally, presented to user
        self.remote_id = None   # ID assigned by debugging target
        self.is_verified = False
        self.ignore_count = ignore_count
        self.cond_expr = cond_expr
        if not self.cond_expr:  # Change empty string to None
            self.cond_expr = None

        if self.__check_debug(1): # 1 = validate
            self._validate()

    def _validate(self):
        assert self.cond_expr == None or len(self.cond_expr) > 0

    def set_verified(self, verified):
        self.is_verified = verified

    # The ID used locally and presented to the user
    def set_local_id(self, local_id):
        self.local_id = local_id

    # The ID assigned by the debugging target
    def set_remote_id(self, remote_id):
        self.remote_id = remote_id

    def is_on_device(self):
        return (self.remote_id != None)

    def is_enabled(self):
        return self.is_on_device()

    def str_params(self):
        s = '{}:{}'.format(self.file_uri, self.line_num)
        if self.local_id:
            s += ',localid={}'.format(self.local_id)
        if self.remote_id:
            s += ',rmtid={}'.format(self.remote_id)
        if self.is_verified:
            s += ',verified'
        if self.ignore_count:
            s += ',ignorecount={}'.format(self.ignore_count)
        if self.cond_expr:
            s += f',cond_expr={self.cond_expr}'
        return s

    def __str__(self):
        return 'Breakpoint[{}]'.format(self.str_params())

    def __check_debug(self, min_level):
        return max(global_config.debug_level, self.__debug_level) >= min_level


import sys
def do_exit(errCode, msg=None):
    sys.modules['__main__'].do_exit(errCode, msg)
