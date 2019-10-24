########################################################################
# Copyright 2019 Roku, Inc.
#
#Licensed under the Apache License, Version 2.0 (the "License");
#you may not use this file except in compliance with the License.
#You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#Unless required by applicable law or agreed to in writing, software
#distributed under the License is distributed on an "AS IS" BASIS,
#WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#See the License for the specific language governing permissions and
#limitations under the License.
########################################################################
#
# File: MonotonicClock.py
# Requires python v3.5.3 or later
#
# Finds the best clock on the platform for monotonic time measurements
# (i.e., a clock that will never run backward, due to NTP or time zone
# changes).
#
# NAMING CONVENTIONS:
#
# TypeIdentifiers are CamelCase
# CONSTANTS are CAPITAL_CASE
# all_other_identifiers are snake_case
# _protected members begin with a single underscore '_' (subclasses can access)
# __private members begin with double underscore: '__'
#
# python more or less enfores the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.


import time, traceback

# Finds the best clock on the platform for monotonic time measurements
# (i.e., a clock that will never run backward, due to NTP or time zone
# changes).
class MonotonicClock(object):

    def __init__(self, debug_level=0):
        self.__debug = max(debug_level, 0)
        self.name = None   # Set in __find_monotonic_clock()
        self.__clock_get_time_impl = self.__find_monotonic_clock()
        self.__prev_time = None

        assert self.name
        assert self.__clock_get_time_impl

        if self.__debug >= 1:
            print('debug: using monotonic clock: {}'.format(self.name))

    def get_time(self):
        cur_time = self.__clock_get_time_impl()
        if not self.__prev_time:
            self.__prev_time = cur_time

        # Some clocks that claim to be monotonic can still be affected
        # by NTP adjustments. Make sure time does not go backward
        if cur_time < self.__prev_time:
            cur_time = self.__prev_time

        self.__prev_time = cur_time
        return cur_time

    # @return callable that returns monotonic time in seconds as float
    def __find_monotonic_clock(self):
        if self.__debug >= 2:
            print('debug: __find_monotonic_clock()')

        clock_info = {'name': None,
                      'get_function': None,
                      'is_monotonic': False}

        clocks = ['monotonic_raw', 'monotonic', 'perf_counter', 'clock']
        for clock_name in clocks:   # Try to find a non-adjustable mono clock
            if not clock_info['is_monotonic']:
                clock_info = self.__check_monotonic_clock(clock_name, False)
        for clock_name in clocks:   # Next, allows adjustable mono clock
            if not clock_info['is_monotonic']:
                clock_info = self.__check_monotonic_clock(clock_name, True)
        if not clock_info['is_monotonic']:  # Fall back to default clock
            clock_info = self.__check_monotonic_clock('time', True)

        get_function = clock_info['get_function']
        if not get_function:
            raise NotImplementedError('No system clock found')

        if clock_info['is_monotonic']:
            get_function = clock_info['get_function']
        else:
            print('WARNING: monotonic clock not found, using wall-clock time')
            clock_info['name'] = \
                'simulated_monotonic:{}'.format(clock_info['name'])
        self.name = clock_info['name']

        assert get_function
        return get_function

    # If returned get_function is None, is_monotonic will be false
    # @return dict with elements, 'name', 'get_function' and 'is_monotonic'
    def __check_monotonic_clock(self, clock_name, adjustable_ok):
        if self.__debug >= 3:
            print('debug: __check_mono_clock({},adjustable={})'.format(
                clock_name, adjustable_ok))
        ret_val = {'name': clock_name,
                   'get_function': None,
                   'is_monotonic': False}
        try:
            get_function = None
            if clock_name == 'monotonic_raw':
                get_function = lambda: time.clock_gettime(time.CLOCK_MONOTONIC_RAW)
                get_function()  # make sure it's callable
                ret_val['is_monotonic'] = True
            else:
                get_function = getattr(time, clock_name)
                sys_clock_info = time.get_clock_info(clock_name)
                # check first, may raise exc
                if sys_clock_info.monotonic:
                    if adjustable_ok:
                        ret_val['is_monotonic'] = True
                    else:
                        ret_val['is_monotonic'] = not sys_clock_info.adjustable
            ret_val['name'] = clock_name
            ret_val['get_function'] = get_function
        except Exception as e:
            if self.__debug >= 5:
                print('debug: DUMPING EXEPTION')
                print('debug: -----------------------------------')
                traceback.print_exception(
                    type(e), e, e.__traceback__, file=sys.stdout)
                print('debug: -----------------------------------')
        if self.__debug >= 3:
            print('debug: __check_mono_clock -> {}'.format(ret_val))
        return ret_val


import sys
def do_exit(err_code, msg=None):
    sys.modules['__main__'].do_exit(err_code, msg)
