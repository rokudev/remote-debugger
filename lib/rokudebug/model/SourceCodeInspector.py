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
# File: SourceCodeInspector.py
# Requires python v3.5.3 or later
#
# NAMING CONVENTIONS:
#
# Type identifiers are CamelCase
# All other identifiers are snake_case
# Protected members begin with a single underscore '_'
# Private members begin with double underscore: '__'
#
# python more or less enfores the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import os, re, sys, traceback, zipfile

from .Verbosity import Verbosity

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

_module_debug_level = 0

# ridiculously large, catch egregious issues
MANIFEST_MAX_SIZE_BYTES = 1000000

# Paths in zip files should always use '/' as path separator, but
# but filesystem path separator may be '/'. Look for both.
_PATH_SEPARATORS = ('/', '\\')

class LibrarySourceSpecifier(object):
    # @param src_spec_str mylibname:/path/to/lib/src/dir
    def __init__(self, src_spec_str):

        parts = src_spec_str.split(':', 1)
        if len(parts) != 2:
            raise ValueError('no ":" found in specifier: {}'.format(src_spec_str))
        self.lib_name = parts[0]
        self.src_dir_path = parts[1]

        # validate
        if not self.lib_name:
            raise ValueError('no library name in specifier: {}'.format(src_spec_str))

        if not self.src_dir_path:
            raise ValueError('no filesystem path found in specifier: {}'.\
                             format(src_spec_str))
        try:
            with os.scandir(self.src_dir_path):
                pass
        except OSError as e:
            raise ValueError('could not read directory: {}'.format(e))

    def __repr__(self):
        return '{}[{}:{}]'.format(self.__class__.__name__, self.lib_name,
                                  self.src_dir_path)

    def __str__(self):
        return '{}:{}'.format(self.lib_name, self.src_dir_path)


# Finds and accesses the for one library
class _LibrarySource(object):
    def __init__(self, lib_src_spec):
        self.spec = lib_src_spec

    # Read [first_line_number, last_line_number] inclusive, 1-based
    def read_lines(self, file_path, first_line_number, last_line_number):
        if self.__check_debug(3):
            print('debug: libsrc: read_lines() {},{},{},{})'.format(
                self.spec, file_path, first_line_number, last_line_number))
        while len(file_path) and file_path[0] in _PATH_SEPARATORS:
            file_path = file_path[1:]
        full_file_path = os.path.join(self.spec.src_dir_path,
                                      file_path)
        lines = list()
        try:
            line_num = 0
            with open(full_file_path, encoding='utf-8') as f:
                for line in f:
                    line_num += 1
                    line = line.rstrip('\r\n\0')
                    if line_num >= first_line_number and \
                            line_num <= last_line_number:
                        lines.append(LineInfo(line_num, line))
                    if line_num >= last_line_number:
                        break
        except OSError as e:
            if self.__check_debug(5):
                traceback.print_exc()
                print('debug: exception reading file: {}'.format(e))
            if global_config.verbosity >= Verbosity.NORMAL:
                print('info: failed to read source file for lib {}: {}'.format(\
                    self.spec.lib_name, e))

        return lines

    # Get a list of file specifiers (e.g., libname:/libsource/prog.brs)
    # for the regular files (not dirs) in the library, never None
    # @return list may be empty never None
    def get_file_specs(self):
        file_specs = list()
        lib_root_dir_len = len(self.spec.src_dir_path)
        try:
            for root, dirs, files in os.walk(self.spec.src_dir_path):
                for file in files:
                    if self.__check_debug(1): # 1 = validation
                        assert len(root) >= lib_root_dir_len
                    dir = root[lib_root_dir_len:]
                    while dir.startswith(os.path.sep):
                        dir = dir[1:]
                    path = os.path.join(dir, file)
                    if not path.startswith(os.path.sep):
                        path = '/' + path
                    file_spec = 'lib:/{}{}'.format(self.spec.lib_name, path)
                    file_specs.append(file_spec)
        except OSError as e:
            if self.__check_debug(2):
                print('debug: exception reading lib dir: {}'.format(e))
        return file_specs

    def __repr__(self):
        return '{}[{}]'.format(self.__class__.__name__, repr(self.spec))

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, _module_debug_level)
        return lvl >= min_level


# Takes a zip file and allows inpection
# @param lib_src_specs iterable of LibrarySourceSpecifier(s) or None
class SourceCodeInspector(object):
    def __init__(self, channelZipPath, lib_src_specs=None):
        self.__local_debug_level = 0
        self.__in_file = sys.stdin
        self.__out_file = sys.stdout
        self.__channel_zip_path = channelZipPath
        self.__libs = dict()
        if lib_src_specs:
            for spec in lib_src_specs:
                self.__libs[spec.lib_name] = _LibrarySource(spec)
        self.__is_verified = False
        if self.__check_debug(2):
            print('debug: SourceCodeInpector({})'.format(channelZipPath))

    # Verifies that the file appears to be a valid channel zip file
    # Exits this script if unresolvable problems are found
    # If verification has already been done, returns True immediately
    # @return True or False
    def verify(self):
        if self.__is_verified:
            return True
        if self.__check_debug(2):
            print('debug: validating zip file: {}'.format(self.__channel_zip_path))
        file_path = self.__channel_zip_path

        # Validate the zip file, as a zip file
        zip = None
        bad_entry_name = None
        try:
            zip = zipfile.ZipFile(file_path)       # can throw BadZipFile
            bad_entry_name = zip.testzip()
        except zipfile.BadZipFile:
            if not bad_entry_name:
                bad_entry_name = "unknown"
        if bad_entry_name:
            do_exit(1, 'Invalid or corrupt zip file: {} (bad entry {})'.format(
                file_path, bad_entry_name))

        # Verify that the zip has a manifest
        entry = None
        try:
            entry = zip.getinfo('manifest')
        except KeyError:
            do_exit(1, 'Channel file has no manifest: {}'.format(file_path))
        if entry:
            if entry.file_size > MANIFEST_MAX_SIZE_BYTES:
                do_exit(1, 'Channel file has ridiculously large manifest ({} bytes): {}'.\
                    format(entry.file_size, file_path))
        self.__is_verified = True
        return True

    # Returns an object with attributes line_number and text
    # CR/LF are stripped from the returned line
    # @return LineInfo or None
    def get_source_line(self, file_name, line_number):
        line = None
        lines = self.get_source_lines(file_name, line_number, line_number)
        if lines:
            line = lines[0]
        return line

    # Returns an array of objects (or None), with two attributes:
    #    line_number, text
    # If any lines in the range are found, the returned array will have
    # non-zero length. The lines returned may be fewer than the requested
    # range, and if no lines in the range are found, returns None
    # @return iterable of LineInfo or None
    def get_source_lines(self, file_spec, first_line_number, last_line_number):
        if self.__check_debug(5):
            print('debug: sci: get_source_lines({},{}-{})'.format(
                file_spec, first_line_number, last_line_number))
        self.verify()
        lines = []

        file_loc = None
        file_path = None
        parts = file_spec.split(':', 1)
        if len(parts) == 2:
            file_loc = parts[0]
            file_path = parts[1]
        else:
            file_path = file_spec   # shouldn't happen

        if file_loc == 'pkg':
            # pkg:/ URI
            lines = self.__read_lines_from_zip(file_path, first_line_number,
                        last_line_number)
        elif file_loc:
            # <library_name>: URI
            lib = self.__libs.get(file_loc, None)
            if lib:
                lines = lib.read_lines(file_path, first_line_number,
                                       last_line_number)

        if lines and not len(lines):
            # never return empty list, return None instead
            lines = None

        if self.__check_debug(5):
            num_lines = 0
            line0 = None
            if lines:
                num_lines = len(lines)
                line0 = lines[0]
            print('debug: sci.getSourceLines({},{}-{})'\
                ' returns {} lines, line0={}'.format(
                    file_spec, first_line_number, last_line_number,
                    num_lines, line0))
        return lines

    def __read_lines_from_zip(self, file_path, first_line_number,
            last_line_number):
        while len(file_path) and file_path[0] in _PATH_SEPARATORS:
            file_path = file_path[1:]
        lines = list()
        try:
            with zipfile.ZipFile(self.__channel_zip_path) as zip:
                with zip.open(file_path) as fd:
                    line_number = 1
                    line = fd.readline()
                    while line:
                        if line_number >= first_line_number:
                            line = str(line, encoding='utf-8').rstrip('\r\n\0')
                            lines.append(LineInfo(line_number, line))
                        if line_number >= last_line_number:
                            break
                        line = fd.readline()
                        line_number += 1

        except zipfile.BadZipFile as e:
            do_exit(1, 'bad zip file: {}'.format(e))
        except KeyError as e:
            if self.__check_debug(5):
                traceback.print_exc()
                print('debug: exception, {}'.format(e))
                print('debug: file not found in zip: {} {}'.format(
                    self.__channel_zip_path, file_path))

        if lines and not len(lines):
            # never return empty list, return None instead
            lines = None

        return lines

    # return all known source files, as pkg:/... and <libname>:/...
    # specifiers, sorted alphabetically
    def get_all_source_file_specs(self):
        src_specs = list()
        def is_source(file_path):
            return re.search('\\.brs$|\\.xml$', file_path, re.IGNORECASE)
        with zipfile.ZipFile(self.__channel_zip_path) as myzip:
            for tmp_path in myzip.namelist():
                if is_source(tmp_path):
                    src_specs.append('pkg:/' + tmp_path)

        if self.__libs:
            for lib_name, lib in self.__libs.items():
                for tmp_path in lib.get_file_specs():
                    if is_source(tmp_path):
                        src_specs.append(tmp_path)

        sorted(src_specs)
        return src_specs

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self.__local_debug_level)
        return lvl >= min_level

#END class SourceCodeInspector


class LineInfo(object):
    def __init__(self, line_number, text):
        self.line_number = line_number
        self.text = text

    def __str__(self):
        return 'LineInfo[{},{}]'.format(self.line_number, self.text)


def do_exit(err_code, msg=None):
    sys.modules['__main__'].global_config.do_exit(err_code, msg)
