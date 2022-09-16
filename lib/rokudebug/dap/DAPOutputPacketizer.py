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
# File: DAPOutputPacketizer.py
# Requires python 3.5.3 or later
#
# Turns output stream into a series of DAP Output Event(s)
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

import sys, threading, time, traceback

from .DAPEvent import DAPOutputCategory
from .DAPEvent import DAPOutputEvent
from .DAPTypes import LITERAL
from .DAPUtils import do_exit, do_print

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level, do_exit()

_FLUSH_INTERVAL_SECONDS = 0.25


# A file-like object that packetizes writes into DAP Output Events
class DAPOutputPacketizer(object):

    # category is included with DAP events, and tell the DAP client
    # (typically an IDE) how to organize/display the output.
    # @param category enum DAPOutputCategory
    def __init__(self, dap, category):
        self._debug_level = 0
        self._done = False
        self.__dap = dap
        self.__category = category
        self.__buf = ''
        self.__cond_var = threading.Condition(threading.Lock())
        self.__thread = _DAPOutputThread(self, self.__cond_var,
                    debug_level=self._debug_level)
        self.__thread.start()

    def write(self, s):
        with self.__cond_var:
            self.__buf += s
            if self.__buf.endswith('\n'):
                self._flush_nolock()
            else:
                self.__thread.set_flushing(True)

    def flush(self):
        with self.__cond_var:
            self._flush_nolock()

    def _flush_nolock(self):
        assert not self.__cond_var.acquire(blocking=False)
        if len(self.__buf):
            self.__dap._send_dap_msg(
                DAPOutputEvent(self.__category, self.__buf))
            self.__buf = ''
        self.__thread.set_flushing(False)

    def close(self):
        with self.__cond_var:
            self._done = True
            self.__cond_var.notify()

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level


class _DAPOutputThread(threading.Thread):

    def __init__(self, packetizer, cond_var, debug_level=0):
        super(_DAPOutputThread, self).__init__(daemon=True, name='DAP-Output')
        self._debug_level = debug_level
        self.__cond_var = cond_var
        self.__packetizer = packetizer
        self.__next_flush_time = 0
        self.__flushing = False

    # Requires: cond_var lock is held
    def set_flushing(self, flushing):
        assert not self.__cond_var.acquire(blocking=False)
        if flushing:
            if not self.__flushing:
                # start flushing
                self.__next_flush_time = time.time() + _FLUSH_INTERVAL_SECONDS
        else:
            if self.__flushing:
                # stop flushing
                self.__next_flush_time = None

        self.__flushing = flushing
        self.__cond_var.notify()

    def run(self):
        try:
            self.run_impl()
        except Exception as e:
            traceback.print_exc()
            do_exit(1, str(e))

    def run_impl(self):
        if self.__check_debug(5):
            do_print('debug:dap_opak: thread running')
        done = False

        while not done:
            with self.__cond_var:
                wait_time = None
                if self.__flushing:
                    wait_time = max(0.001, self.__next_flush_time - time.time())
                self.__cond_var.wait(timeout=wait_time)
                if self.__flushing and time.time() >= self.__next_flush_time:
                    self.__packetizer._flush_nolock()
                    self.__next_flush_time = time.time() + _FLUSH_INTERVAL_SECONDS
                done = self.__packetizer._done

        self.__packetizer.flush()

        if self.__check_debug(2):
            do_print('debug:dap_opak: thread exiting')

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level
