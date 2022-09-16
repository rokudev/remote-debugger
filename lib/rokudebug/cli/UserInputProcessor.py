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
# File: LineEditor.py
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

_module_debug_level = 0

import sys, threading, traceback
from .LineEditor import LineEditor

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config

_SIMULATED_COMMAND_PROMPT = "RRDB(AUTO)>"

# Processes user input and notifies its listener with
# command lines.
class UserInputProcessor(object):

    def __init__(self, prompt_lines, input_listener, cmd_completer, fin, fout):
        super(UserInputProcessor, self).__init__()
        self._debug_level = 0
        self.__input_listener = input_listener
        self.__cmd_completer = cmd_completer
        self.__lock = threading.Lock()
        self.__condition = threading.Condition(self.__lock)
        self.__prompt_lines = prompt_lines  # protected with self.__lock
        self.__in_file = fin
        self.__out_file = fout
        self.__input_count = 0
        self.__input_ok = False
        self.__reading_input_now = False

        # Used by get_input_line_sync(). Protected with self.__lock
        self.__return_input_sync = False
        self.__return_input_sync_str = None
        self.__return_input_sync_str_valid = False

        self.__thread = None

    # Start the processor thread
    def start(self):
        self.__thread = threading.Thread(
                target=self, name='User-Input-0', daemon=True)
        self.__thread.start()

    # Reads one line of text, blocking until read is complete
    # @prompt_lines list of strings
    # @return string or None
    def read_line_sync(self, prompt_lines, completion_domain):
        if self.__check_debug(3):
            print('debug: uip: read_line_sync()')
        saved_input_ok = None
        saved_prompt_lines = None
        input_str = None
        with self.__lock:
            if self.__return_input_sync:
                # Overlapping calls are not allowed
                if self.__check_debug(1): # 1 == validation
                    raise AssertionError('overlapping call to get_input_line_sync()') 
                return None
            saved_input_ok = self.__input_ok
            saved_prompt_lines = self.__prompt_lines
            saved_completion_type = self.__cmd_completer.get_completion_domain()
            self.__input_ok = True
            self.__prompt_lines = prompt_lines
            self.__cmd_completer.set_completion_domain(completion_domain)
            self.__return_input_sync = True
            self.__condition.notify_all()
            while not self.__return_input_sync_str_valid:
                self.__condition.wait()

            input_str = self.__return_input_sync_str

            # Prep for next call
            self.__return_input_sync = False
            self.__return_input_sync_str = None
            self.__return_input_sync_str_valid = False
            self.__input_ok = saved_input_ok
            self.__prompt_lines = saved_prompt_lines

        if self.__check_debug(2):
            print('debug: uip: read_line_sync() returns "{}"'.format(input_str))
        return input_str

    # Get count of non-empty command lines enter by the user
    def get_input_count(self):
        with self.__lock:
            return self.__input_count

    def set_prompt_lines(self, prompt_lines):
        with self.__lock:
            self.__prompt_lines = prompt_lines

    # If this processor is not actively reading the input, start
    # doing so
    def accept_input(self, input_ok):
        with self.__lock:
            if (self._debug_level >= 3) and (input_ok != self.__input_ok):
                print('debug: uip: accept_input(), ok becomes {},prompt={}'.\
                    format(input_ok, self.__prompt_lines))
            self.__input_ok = input_ok
            self.__condition.notify_all()

    def simulate_input(self, cmd_str):
        fout = self.__out_file
        print('{} {}'.format(_SIMULATED_COMMAND_PROMPT, cmd_str), file=fout)
        self.__input_listener._user_input_received(cmd_str)

    def run(self):
        if self._debug_level >= 2:
            print('debug: uip: user input thread running...')

        line_editor = LineEditor(self.__cmd_completer)
        while True:
            with self.__lock:
                while not self.__input_ok:
                    self.__condition.wait()
                self.__reading_input_now = True

            line_prompt = ''
            with self.__lock:
                if len(self.__prompt_lines):
                    line_prompt = self.__prompt_lines[-1]
            sys.stdout.flush()
            sys.stderr.flush()
            self.__print_prompt_prelude()
            cmd_line = line_editor.input(line_prompt)

            return_sync = False
            has_input = False
            with self.__lock:
                self.__reading_input_now = False
                return_sync = self.__return_input_sync
                if return_sync or len(cmd_line):
                    # async: only send non-empty lines
                    # sync: always return line, even if emtpy
                    has_input = True
                if has_input:
                    self.__input_count += 1
                    # Don't accept more input until client calls accept_input() or read_line_sync()
                    self.__input_ok = False
                    if return_sync:
                        if self.__check_debug(1): # 1 = validation
                            assert not self.__return_input_sync_str
                            assert not self.__return_input_sync_str_valid
                        self.__return_input_sync_str = cmd_line
                        self.__return_input_sync_str_valid = True
                        self.__condition.notify_all()

            # Send to listener without holding internal lock
            if has_input and not return_sync:
                    self.__input_listener._user_input_received(cmd_line)

        if self.__check_debug(2):
            print('debug: uip: user input thread exiting')

    # prints all but the last prompt line, which is printed by the input()
    # command.
    def __print_prompt_prelude(self):
        fout = self.__out_file
        with self.__lock:
            for i in range(len(self.__prompt_lines)-1):
                line = self.__prompt_lines[i]
                if i < (len(self.__prompt_lines) - 1):
                    print(line, file=fout)
                else:
                    print(line, file=fout, end='')
            fout.flush()

    def __call__(self):
        try:
            self.run()
        except SystemExit: raise
        except: # Yes, catch EVERYTHING
            traceback.print_exc()
            global_config.do_exit(1, "INTERNAL ERROR: uncaught exception")

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level

#END: class UserInputProcessor
