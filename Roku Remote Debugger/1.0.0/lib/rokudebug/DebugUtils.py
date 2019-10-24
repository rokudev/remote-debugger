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

import sys

class DebugUtils(object):
    @staticmethod
    def dumpBytes(bytes, label=None, forceEol=False, maxLen=None):
        len = 0
        atEol = False
        if label:
            print('{}>>>>>'.format(label), end='')
        for b in bytes:
            if b <= 127:
                s = str(bytearray([b]), 'ascii')
                if s.isprintable():
                    b = None
                    print(s, end='')
            if b != None:
                print('\\{:#x}'.format(b), end='')
                if b == ord('\n'):
                    atEol = True
                    print()
                else:
                    atEol = False
            len += 1
            if maxLen and (len >= maxLen):
                print('...', end='')
                break

        if label:
            print('<<<<<{}'.format(label))
        if forceEol and not atEol:
            print()

import sys
def do_exit(errCode, msg=None):
    sys.modules['__main__'].do_exit(errCode, msg)
