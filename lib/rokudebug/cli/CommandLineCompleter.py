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
# File: CommandLineCompleter.py
# Requires python 3.5.3 or later
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

import enum, sys, threading

from .LineEditor import LineEditor

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

@enum.unique
class CompletionDomain(enum.IntEnum):
    COMMAND_LINE = 0,   # entire command line. E.g., addbreak source/main.brs:15 50
    FILE_SPEC = 1,
    NONE = 2            # don't complete

    def __str__(self):
        return '{}({})'.format(self.name, self.value)


# Used by UserInputProcessor/LineEditor to enable interactive tab-completion.
class CommandLineCompleter(object):

    def __init__(self, cli):
        self.__debug_level = max(global_config.debug_level, 0)
        self.__lock = threading.Lock()
        self.__cli = cli
        self.__completion_domain = CompletionDomain.COMMAND_LINE

    def set_completion_domain(self, completion_domain):
        if self.__check_debug(3):
            print('debug: clc: set_completion_domain({})'.format(completion_domain))
        if self.__check_debug(1): # 1 == validation
            assert completion_domain != None
        with self.__lock:
            self.__completion_domain = completion_domain

    # @return enum CompletionDomain
    def get_completion_domain(self):
        with self.__lock:
            return self.__completion_domain

    def get_completions(self, full_text, beginidx, endidx):
        token_info, selidx = LineEditor.parse_tokens(full_text, beginidx, endidx)
        token = ''
        cmd_line_token_index = 0          # position on command line 0..n
        if selidx >= 0:
            token = token_info[selidx]['text']
            cmd_line_token_index = selidx
        else:
            cmd_line_token_index = -selidx -1
        if not token:
            token = ''

        if self.__check_debug(5):
            print('debug: clc: get_completions() cmd_line_token_index={},token="{}"'
                    ',beginidx={},endidx={},selidx={},"full_text="{}"'.format(
                cmd_line_token_index, token, beginidx, endidx, selidx, full_text))

        completions = None
        if self.__completion_domain == CompletionDomain.COMMAND_LINE:
            completions = self.__get_completions_command_line(cmd_line_token_index, token)
        elif self.__completion_domain == CompletionDomain.FILE_SPEC:
            completions = self.__get_completions_file_spec(token)
        elif self.__completion_domain == CompletionDomain.NONE:
            pass
        else:
            if self.__check_debug(1):  # 1 = validation
                raise AssertionError('Unknown completion domain: {}'.format(
                    self.__completion_domain))

        return completions

    # entire command line. E.g., addbreak source/main.brs:15 50
    # cmd_line_token_index (position on the command line) determines the completion that is done
    # @param cmd_line_token_index position on commmand line 0..n
    # @param token may be empty but not None
    def __get_completions_command_line(self, cmd_line_token_index, token):
        completions = None
        if cmd_line_token_index == 0:
            completions = self.__complete_command(token)
        elif cmd_line_token_index == 1:
            completions = self.__complete_file_spec(token)
        return completions

    # @param token may be empty but not None
    def __get_completions_file_spec(self, token):
        return self.__complete_file_spec(token)

    # @param token:string may be empty but never None
    def __complete_command(self, token):
        if self.__check_debug(3):
            print('debug: clc: complete_command(),token={}'.format(token))
        completions = list()

        # Look for exact match to an alias
        aliases = self.__cli._get_all_cmd_aliases()
        for alias_pair in aliases:
            if alias_pair[0] == token:
                completions.append(alias_pair[1])

        extraps = self.__find_extrapolations(
                        token, self.__cli._get_all_cmd_strs())
        if extraps:
            completions.extend(extraps)

        # sort and unique the list
        completions = list(set(completions))
        completions.sort()
        return completions

    def __complete_file_spec(self, token):
        if self.__check_debug(3):
            print('debug: clc: complete_file("{}")'.format(token))
        completions = list()
        cmp_token = token.casefold()
        for file_path in self.__cli._src_inspector.get_source_file_paths():
            file_path = 'pkg:/' + file_path
            if cmp_token in file_path.casefold():
                completions.append(file_path)

        return completions

    # Find elements of possibilities that start with stem, or == stem
    # @return list of extrapolations or None
    def __find_extrapolations(self, stem, possibilities):
        extraps = []
        for poss in possibilities:
            if stem == '' or poss.startswith(stem):
                extraps.append(poss)
        if not len(extraps):
            extraps = None
        return extraps

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self.__debug_level)
        if lvl: assert global_config.debug_level >= 0 and self.__debug_level >= 0 and min_level >= 1
        return lvl >= min_level
