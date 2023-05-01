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
# File: DebuggerIOListener.py
# Requires python v3.5.3 or later
#
# NAMING CONVENTIONS:
#
# CONSTANT_VALUES ARE CAPITAL_SNAKE_CASE
# TypeNames are CamelCase
# all_other_identifiers are snake_case
# Protected members begin with a single underscore '_'
# Private members begin with double underscore: '__'
#
# python more or less enforces the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import socket, sys, threading, traceback

# SystemExit only exits the current thread, so call it by its real name
ThreadExit = SystemExit

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

# Uses a separate thread to listen to the debugger's I/O port,
# to retrieve output from the running script and forward it
# to out_file.
class DebuggerIOListener(object):

    # @param updateHandler.updateReceived(update)
    def __init__(self, host, port, out_file):
        self._debug_level = 0
        if self.__check_debug(2):
            print("debug:io_lis: __init__()")
        self.__thread = _IOListenerThread(host, port, out_file)
        self.__thread.start()

    def set_save_output(self, enable) -> bool:
        return self.__thread.set_save_output(enable)

    def get_output_lines(self) -> list:
        return self.__thread.get_saved_lines()

    def disconnect(self):
        self.__thread.disconnect()

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level


class _IOListenerThread(threading.Thread):

    def __init__(self, host, port, out_file):
        super(_IOListenerThread, self).__init__(daemon=True)
        self.name = 'DebuggerIOListener'
        self.__debug_level = 0
        self.__host = host
        self.__port = port
        self.__out_file = out_file
        self.__socket = None

        # Saved lines. These are normally only requested when tests
        # are being run, so that the tests can examine the target's output.
        self.__save_output_lock = threading.Lock()
        self.__save_output = False
        self.__save_buffer = ''
        self.__saved_lines = list()

    # @return True on success, False otherwise
    def set_save_output(self, enable) -> bool:
        with self.__save_output_lock:
            if enable == self.__save_output:
                # No change
                return True
            self.__save_output = enable
            self.__save_buffer = ''
            self.__saved_lines = list()
            return True

    def get_saved_lines(self) -> list:
        with self.__save_output_lock:
            lines = self.__saved_lines
            self.__saved_lines = list()
            return lines

    def run(self):
        if self.__check_debug(2):
            print('debug:io_lis: thread running, host={},port={}'.format(
                self.__host, self.__port))
        try:
            self.__socket = socket.create_connection((self.__host, self.__port))
            if self.__check_debug(2):
                print('debug:io_lis: connected to IO {}:{}'.format(
                    self.__host,self.__port))
            done = False
            while not done:
                try:
                    buf = self.__socket.recv(1)
                    if buf and len(buf):
                        b = buf[0]
                        c = chr(b)
                        print(c, file=self.__out_file, end='')
                        self.__add_char_to_saved(c)

                    else:
                        # EOF
                        done = True
                        if self.__check_debug(2):
                            print('debug:io_lis: EOF on target I/O stream')
                except:
                    pass

        except ThreadExit: raise
        except:     # yes, catch EVERYTHING
            sys.stdout.flush()
            traceback.print_exc(file=sys.stderr)
            global_config.do_exit(1, 'Uncaught exception in I/O listener thread')

        if self.__check_debug(2):
            print('debug:io_lis: thread exiting cleanly')

    def disconnect(self):
        if self.__socket != None:
            self.__socket.shutdown(socket.SHUT_RDWR)
            self.__socket.close()
            self.__socket = None

    def __add_char_to_saved(self, c) -> None:
        with self.__save_output_lock:
            if not self.__save_output:
                return None
            if c == '\n':
                self.__saved_lines.append(self.__save_buffer)
                self.__save_buffer = ''
            else:
                self.__save_buffer += c
        return None

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self.__debug_level)
        if lvl: assert global_config.debug_level >= 0 and self.__debug_level >= 0 and min_level >= 1
        return lvl >= min_level
