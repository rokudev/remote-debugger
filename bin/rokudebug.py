#!/usr/bin/python3
# Requires python3 v3.5.3 or later
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
# File: rokudebug.py
# Requires python v3.5.3 or later
#
# This is a reference implementation of a command-line debugger that
# uses the BrightScript debugger protocol. That protocol was first
# included in Roku OS 9.2.
#
# THIS ONE FILE IS COMPATIBLE WITH ALL 2.x AND 3.x VERSIONS OF
# PYTHON, so that error messages about the python version can be
# printed before exiting.
# See the minPythonVersion variable below, for the python version
# required for the rest of the scripts in this deployment.
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


# In case this is run with python 2.x (does not cause python3 to fail)
from __future__ import print_function

import importlib, os, sys, traceback

# SystemExit only exits the current thread, so call it by its real name
ThreadExit = SystemExit

try:

    #############################################################
    # Basic requirements definition
    #############################################################

    # Minimum python version [major:int,minor:int,patchlevel:int]
    min_python_version = [3, 5, 3]

    #############################################################

    debug = False

    # Verify python version
    # ints in python of arbitrary size -- allow six digits for each element
    min_ver = min_python_version
    py_ver = sys.version_info
    ver_ok = False
    if py_ver[0] >= min_ver[0]:
        if py_ver[0] > min_ver[0]:
            ver_ok = True
        elif py_ver[1] >= min_ver[1]:
            if py_ver[1] > min_ver[1]:
                ver_ok = True
            else:
                ver_ok = (py_ver[2] >= min_ver[2])
    if not ver_ok:
        sys.stdout.flush()
        print('Minimum python version is {}.{}.{}'\
                  ', but this script was run with version {}.{}.{}'.format(
                  min_python_version[0], min_python_version[1], min_python_version[2],
                  sys.version_info[0], sys.version_info[1], sys.version_info[2]),
                  file=sys.stderr)
        print("\n"
              "HINT: try the command 'python3'\n"
              "      If neither 'python' nor 'python3' work, you will need to\n"
              "      install python version {}.{}.{} or later.".format(
                  min_ver[0], min_ver[1], min_ver[2]),
              file=sys.stderr)
        sys.exit(1)

    # Prepend our lib dir to the path ( ../lib )
    # This is necessary, to avoid conflict with our parent directory
    # that has the same name as the package.
    os.sys.path.insert(0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
            'lib'))

    # Free unneeded variables
    del min_python_version
    del min_ver
    del py_ver
    del ver_ok

    # Run it!
    import rokudebug.RokuDebug
    rokudebug.RokuDebug().main()

# Catch any wayward exceptions.
# Ideally, this should never happen, because the scripts should be
# handling exceptions locally whenever reasonable.
except ThreadExit: raise
except:     # Yes, catch EVERYTHING (not just Exception)
    sys.stdout.flush()
    traceback.print_exc(file=sys.stderr)
    print('ERROR: Failed with exception', file=sys.stderr)
    sys.exit(1)

sys.exit(0)

