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
# File: BreakpointManager.py
# Requires python v3.5.3 or later
#
# NAMING CONVENTIONS:
#
# TypeIdentifiers are CamelCase
# CONSTANTS_ARE_CAPITALIZED SNAKE_CASE
# all_other_identifiers are lower_snake_case
# _protected members begin with a single underscore '_' (avail to friends)
# __private members begin with double underscore: '__'
#
# python more or less enforces the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import sys

from .Breakpoint import Breakpoint

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

class BreakpointManager(object):

    def __init__(self):
        self.__debug = max(global_config.debug_level, 0)
        self.__next_breakpoint_id = 1000   # 0 is invalid
        self.breakpoints = list()          # list of Breakpoint(s)

    def is_empty(self):
        return (len(self.breakpoints) == 0)

    def count_breakpoints(self):
        return len(self.breakpoints)

    # Attempts to find a matching breakpoint, by the following means:
    # 1) Look for matching remote_id, if breakpoint.remote_id is valid
    # 2) Look for matching file_path,line_num
    # If an existing breakpoint is found, it is updated. If an existing
    # breakpoint is not found, adds breakpoint to this manager.
    # @return the breakpoint stored in this manager, never None
    def add_or_update_breakpoint(self, breakpoint):
        if not breakpoint:
            raise ValueError
        managed = None
        if breakpoint.remote_id:    # None and 0 are invalid
            managed = self.find_breakpoint_by_remote_id(breakpoint.remote_id)
        if not managed:
            managed = self.find_breakpoint_by_spec(
                            breakpoint.file_uri, breakpoint.line_num)
        if managed:
            managed.ignore_count = breakpoint.ignore_count
            managed.remote_id = breakpoint.remote_id
        else:
            self.breakpoints.append(breakpoint)
            managed = breakpoint
        self.__update_local_ids()
        return managed

    def remove_breakpoint_by_local_id(self, local_id):
        if not isinstance(local_id, int):
            raise TypeError('local_id')
        breakpoint = self.find_breakpoint_by_local_id(local_id)
        if breakpoint:
            self.breakpoints.remove(breakpoint)
        self.__update_local_ids()

    def find_breakpoint_by_local_id(self, local_id):
        if not isinstance(local_id, int):
            raise TypeError('local_id')
        found = None
        for breakpoint in self.breakpoints:
            if breakpoint.local_id == local_id:
                found = breakpoint
                break
        return found

    def find_breakpoint_by_remote_id(self, remote_id):
        if (remote_id == None) or (remote_id < 1):
            raise ValueError('remote_id')
        found = None
        for breakpoint in self.breakpoints:
            if breakpoint.remote_id == remote_id:
                found = breakpoint
                break
        return found

    # Requires an exact match of file_path+line_num
    def find_breakpoint_by_spec(self, file_path, line_num):
        if self.__debug >= 5:
            print('debug: brkmgr: find_breakpoint_by_spec({},{})'.format(
                    file_path, line_num))
        if not (file_path and len(file_path)):
            raise ValueError('file_path')
        if (line_num == None) or (line_num < 1):
            raise ValueError('line_num')
        found = None
        for breakpoint in self.breakpoints:
            if (breakpoint.file_uri == file_path) and \
                (breakpoint.line_num == line_num):
                found = breakpoint
                break
        return found

    # Finds a breakpoint that is on the given file_path and line_num
    # The file_path may be a superset of the breakpoint's specification
    def find_breakpoint_at_line(self, file_path, line_num):
        if self.__debug >= 5:
            print('debug: brkmgr: find_breakpoint_at_line({},{})'.format(
                    file_path, line_num))
        if not (file_path and len(file_path)):
            raise ValueError('file_path')
        if (line_num == None) or (line_num < 1):
            raise ValueError('line_num')
        found = None
        for breakpoint in self.breakpoints:
            if file_path.endswith(breakpoint.file_uri) and \
                (breakpoint.line_num == line_num):
                found = breakpoint
                break
        return found

    def debug_dump(self):
        print('debug: Dumping BreakpointManager (#breakpoints={})'.format(
            len(self.breakpoints)))
        i = -1
        for breakpoint in self.breakpoints:
            i += 1
            print('debug:     {}: {}'.format(i, str(breakpoint)))

    def __update_local_ids(self):
        for breakpoint in self.breakpoints:
            if not breakpoint.local_id:
                breakpoint.local_id = self.__next_breakpoint_id
                self.__next_breakpoint_id += 1

    def __str__(self):
        s = 'BreakpointManager['
        s += '#breakpoints={}'.format(len(self.breakpoints))
        s += ']'
        return s


import sys
def do_exit(errCode, msg=None):
    sys.modules['__main__'].do_exit(errCode, msg)
