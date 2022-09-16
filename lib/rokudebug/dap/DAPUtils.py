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
# File: DAPUtils.py
# Requires python 3.5.3 or later
#
# General utilities, used by DAP package
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

import sys, threading

from .DAPTypes import LITERAL

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level, do_exit()

def to_debug_str(any_value):
    done = False
    s = ''

    # String
    if isinstance(any_value, str):
        s = "'{}'".format(any_value)
        done = True

    # Object
    if not done:
        try:
            any_dict = any_value.__dict__
            if any_dict:
                type_array = str(type(any_value)).split('.')
                type_str = type_array[len(type_array)-1]
                while type_str.endswith(("'", '>')):
                    type_str = type_str[:len(type_str)-1]
                s += type_str + '['
                first = True

                for name, value in any_dict.items():
                    if value != None:
                        if first:
                            first = False
                        else:
                            s += ','
                        s += '{}={}'.format(name,to_debug_str(value))
                s += ']'
                done = True
        except: pass

    # Iterable
    if not done:
        try:
            first = True
            for x in any_value:
                if first:
                    first = False
                    s += '['
                else:
                    s += ','
                s += to_debug_str(x)
            if not first:
                s += ']'
            done = True
        except: pass

    if not done:
        s = str(any_value)
        done = True
    return s

# If dap_msg is None, returns (None,None)
# @return sequence,cmd_str
def get_dap_seq_cmd(dap_msg):
    if not dap_msg:
        return None, None
    dap_seq = dap_msg.get(LITERAL.seq, None)
    if not dap_seq:
        dap_seq = dap_msg.get(LITERAL.request_seq, None)
    return (dap_seq, dap_msg.get(LITERAL.command,None))

# if dap_msg is None, returns (None,None,None)
# @return sequence, cmd_str, args
def get_dap_seq_cmd_args(dap_msg):
    if not dap_msg:
        return (None,None,None)
    return ( *get_dap_seq_cmd(dap_msg), dap_msg.get(LITERAL.arguments,None) )

def to_dap_dict(any_value):
    # Creates a shallow copy of any_value, into a dict. This is called by the
    # JSON encoder. Currently, this only works for class instances,
    # and cases can be added as necessary.
    dap_dict = dict()
    obj_dict = any_value.__dict__
    for key, value in obj_dict.items():
        if value != None:
            dap_dict[key] = value
    return dap_dict

# Stdout may have been directed to stream that does not flush; this
# adds explicit flushing
def do_print(msg, end=None):
    if end:
        print(msg, end=end)
    else:
        print(msg)
    sys.stdout.flush()

def do_exit(exit_code, msg=None):
    global_config.do_exit(exit_code, msg)
