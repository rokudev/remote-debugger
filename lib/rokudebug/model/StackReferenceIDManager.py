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
# File: StackReferenceIDManager.py
# Requires python 3.5.3 or later
#
# Converts (thread_index, frame_index, variable_path) triplets to
# and from an integer value. IDs are allocated sparsely, as needed.
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

import copy, re, sys, threading

from .DebugUtils import do_print

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level, do_exit()

# Converts (thread_index, frame_index, variable_path[]) <=> int
# IDs are allocated sparsely, as needed. This is useful for using
# an integer value to refer to a stack frame or local variable path.
# The returned IDs can be used as variablesReference values in the
# Debug Adapter Protocol (DAP).
# This class is thread-safe
class StackReferenceIDManager(object):
    __create_lock = threading.Lock()
    __singleton_created = False

    def __init__(self):
        with StackReferenceIDManager.__create_lock:
            assert not StackReferenceIDManager.__singleton_created, 'must be singleton'
            StackReferenceIDManager.__singleton_created = True

        self._debug_level = 0
        self.__lock = threading.Lock()
        self.__next_id = 1              # 0 is invalid
        self.__id_to_indexes = dict()
        self.__indexes_to_id = dict()

    # Returns existing stack ref ID for the triplet, or allocates
    # a new one. All stack_ref_id(s) are > 0 (0 is invalid)
    # @param variable_path None or an iterable of strings
    def get_stack_ref_id(self, thread_index, frame_index, variable_path=None,
                        allow_create=True):
        assert thread_index >= 0
        assert frame_index >= 0
        if type(variable_path) == str:
            raise TypeError('variable_path')
        id = None
        with self.__lock:
            key = self.__encode_key(thread_index, frame_index, variable_path)
            id = self.__indexes_to_id.get(key, None)
            if not id and allow_create:
                id = self.__next_id
                self.__next_id += 1
                self.__indexes_to_id[key] = id
                self.__id_to_indexes[id] = \
                            (thread_index, frame_index, copy.copy(variable_path))
        if self.__check_debug(9):
            do_print('debug:stkref: get id: {} -> {}'.format(
                (thread_index, frame_index, variable_path), id))
        return id

    # Get or allocate a new id representing a child of stack_ref_id
    # All stack_ref_id(s) are > 0 (0 is invalid)
    # @raise KeyError if stack_ref_id is unknown
    def get_child_stack_ref_id(self, stack_ref_id, child_name,
            allow_create=True):
        if not (child_name and type(child_name) == str):
            raise TypeError(child_name)
        thr_idx, frm_idx, var_path = self.get_indexes(stack_ref_id)
        if var_path and len(var_path):
            var_path = copy.copy(var_path)
        else:
            var_path = list()
        var_path.append(child_name)
        child_stack_ref_id = self.get_stack_ref_id(thr_idx, frm_idx, var_path)
        return child_stack_ref_id

    # @return thread_index,frame_index,variable_name
    # @raise KeyError if stack_ref_id is not known
    def get_indexes(self, stack_ref_id):
        indexes = None
        with self.__lock:
            indexes = self.__id_to_indexes.get(stack_ref_id)
        if self.__check_debug(9):
            do_print('debug:stkref: get indexes: {} -> {}'.format(
                stack_ref_id, indexes))
        return indexes

    # Encode the key, so that every path will create a unique key.
    # This is necessary because path entries can be AA keys themselves
    # and can contain any unicode character.
    def __encode_key(self, thread_index, frame_index, variable_path):
        key = ''
        key += str(thread_index)
        key += '|'
        key += str(frame_index)
        if variable_path and len(variable_path):
            for entry in variable_path:
                key += '|'
                # entry can be a variable name, an index into an array, or
                # a key in an associative array (AA). '|' is legal as a
                # key in an AA, so escape it, to avoid ambiguity.
                # NB: | is also a valid regex character.
                key += re.sub(r'\|', '|vbar;', entry)
        return key

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level
#END class StackReferenceIDManager
