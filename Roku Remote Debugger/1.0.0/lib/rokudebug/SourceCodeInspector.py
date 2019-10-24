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

import sys, traceback, zipfile

# ridiculously large, catch egregious issues
MANIFEST_MAX_SIZE_BYTES = 1000000


# Takes a zip file and allows inpection
class SourceCodeInspector(object):
    def __init__(self, channelZipPath):
        global gMain
        gMain = sys.modules['__main__'].gMain
        self.__debug = max(gMain.gDebugLevel, 0)
        self.__in_file = sys.stdin
        self.__out_file = sys.stdout
        self.__channel_zip_path = channelZipPath
        self.__is_verified = False
        if self.__debug > 1:
            print('debug: SourceCodeInpector({})'.format(channelZipPath))

    # Verifies that the file appears to be a valid channel zip file
    # Exits this script if unresolvable problems are found
    # If verification has already been done, returns True immediately
    # @return True or False
    def verify(self):
        if self.__is_verified:
            return True
        if self.__debug >= 1:
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
    # @return str or None
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
    # @return object or None
    def get_source_lines(self, file_name, first_line_number, last_line_number):
        if self.__debug >= 9:
            print('debug: getSourceLine({},{}-{})'.format(
                file_name, first_line_number, last_line_number))
        self.verify()
        lines = []

        if file_name.startswith('pkg:/'):
            file_name = file_name[5:]
        try:
            with zipfile.ZipFile(self.__channel_zip_path) as zip:
                with zip.open(file_name) as fd:
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
            if self.__debug >= 1:
                traceback.print_exc()
                print('debug: exception, {}'.format(e))
                print('debug: file not found in zip: {} {}'.format(
                    self.__channel_zip_path, file_name))

        if lines and not len(lines):
            # never return empty list, return None instead
            lines = None

        if self.__debug >= 5:
            num_lines = 0
            line0 = None
            if lines:
                num_lines = len(lines)
                line0 = lines[0]
            print('debug: sci.getSourceLines({},{}-{})'\
                ' returns {} lines, line0={}'.format(
                    file_name, first_line_number, last_line_number,
                    num_lines, line0))
        return lines

class LineInfo(object):
    def __init__(self, line_number, text):
        self.line_number = line_number
        self.text = text

    def __str__(self):
        return 'LineInfo[{},{}]'.format(self.line_number, self.text)


def do_exit(err_code, msg=None):
    sys.modules['__main__'].do_exit(err_code, msg)
