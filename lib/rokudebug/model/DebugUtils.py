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
# File: DebugUtils.py
# Requires python v3.5.3 or later
#
# NAMING CONVENTIONS:
#
# Type identifiers are CamelCase
# All other identifiers are snake_case
# _protected members begin with a single underscore '_' (avail to friends)
# __private members begin with double underscore: '__'
#
# python more or less enfores the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import datetime, os, sys

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config

# return enum name or None if enum_value is None or not an enum
def get_enum_name(enum_value):
    if enum_value == None:
        return None
    return getattr(enum_value, 'name', None)

# return file name without path or None if file_path is None
def get_file_name(file_path):
    if not file_path:
        return None
    return os.path.split(file_path)[-1]

def revision_timestamp_to_str(timestamp):
    rev_time = datetime.datetime.fromtimestamp(
        timestamp / 1000.0, datetime.timezone.utc)
    rev_time_str = '{}({})'.format(timestamp, 
        rev_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ").replace("000Z", "Z"))
    return rev_time_str

def dump_bytes(bytes, label=None, forceEol=False, maxLen=None):
    len = 0
    atEol = False
    if label:
        do_print('{}>>>>>'.format(label), end='')
    for b in bytes:
        if b <= 127:
            s = str(bytearray([b]), 'ascii')
            if s.isprintable():
                b = None
                do_print(s, end='')
        if b != None:
            do_print('\\{:#x}'.format(b), end='')
            if b == ord('\n'):
                atEol = True
                do_print()
            else:
                atEol = False
        len += 1
        if maxLen and (len >= maxLen):
            do_print('...', end='')
            break

    if label:
        do_print('<<<<<{}'.format(label))
    if forceEol and not atEol:
        do_print()

# Stdout may have been directed to stream that does not flush; this
# adds explicit flushing
def do_print(msg=None, end=None):
    if msg:
        print(msg,end=end,flush=True)
    else:
        print(end=end,flush=True)

def do_exit(exit_code, msg=None):
    global_config.do_exit(exit_code, msg)

