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
# File: ProtocolVersion.py
# Requires python v3.5.3 or later
#
# Defines which protocol versions are supported by this debugger
#

########################################################################
# Unit test setup
########################################################################
__RUN_UNIT_TESTS = __name__ == '__main__'
if __RUN_UNIT_TESTS:
    import enum, sys
    @enum.unique
    class Verbosity(enum.IntEnum):
        SILENT      = 0,
        ERRORS_ONLY = 1,
        NORMAL      = 2,
        HIGH        = 3,
        HIGHER      = 4,
        HIGHEST     = 5,

    class UnitTestConfig(object):
        def __init__(self):
            self.verbosity = Verbosity.NORMAL

    sys.modules['__main__'].global_config = UnitTestConfig()
########################################################################

import enum, sys
if not __RUN_UNIT_TESTS:
    from .Verbosity import Verbosity
    from rokudebug.model.DebugUtils import revision_timestamp_to_str

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level, do_exit()

_SUPPORTED_PROTOCOL_MAJOR_VERSIONS = [1,2,3] # must be array (list does not support "in")

_MAJOR_VERSION_MAX = 999
_MINOR_VERSION_MAX = 999
_PATCH_LEVEL_MAX = 999

@enum.unique
class ProtocolFeature(enum.IntEnum):
    ATTACHED_MESSAGE_DURING_STEP_BUG = enum.auto()  # incorrect THREAD_ATTACHED during step in/over/out
    BAD_LINE_NUMBER_IN_STACKTRACE_BUG = enum.auto()
    BREAKPOINTS = enum.auto()
    BREAKPOINTS_URI_SUPPORT = enum.auto()       # "pkg:" and "lib:/<libname>" URIs
    CASE_SENSITIVITY = enum.auto()              # info & control over case sensitivity
    EXECUTE_COMMAND = enum.auto()
    EXECUTE_RETURNS_ERRORS = enum.auto()        # EXECUTE command returns livecompile errs
    STOP_ON_LAUNCH_ALWAYS = enum.auto()
    STEP_COMMANDS = enum.auto()
    UPDATES_HAVE_PACKET_LENGTH = enum.auto()    # All updates/responses from target have packet length
    CONDITIONAL_BREAKPOINTS = enum.auto()
    ERROR_FLAGS = enum.auto()                   # Error responses include additional data
    CONDITIONAL_BREAKPOINTS_ALLOW_EMPTY_CONDITION = enum.auto() # empty condition = no condition
    IMPROVED_LINE_NUMBERS_IN_TRACES = enum.auto() # Threads and Stacktrace responses have better line numbers

    def to_user_string(self):
        feature = ProtocolFeature
        s = self.name
        user_string = s.lower() # pylint: disable=no-member
        # Special cases
        if self.value == feature.STOP_ON_LAUNCH_ALWAYS:
            user_string = "stop_on_launch"  # To user, it's not "always"
        return user_string

# A ProtocolVersion is major.minor.patch_level (e.g., 3.2.1), and
# an optional software revision timetamp may also be included. The
# revision timestamp is primarily used to distinguish between pre-
# release build, similar to a build number. When comparing two
# versions, the revision_timestamp is only considered if the major
# minor,patch_level are equal and BOTH version have a revision timestamp.
#
# This is a small and lightweight class. As such, there are no internal
# data structures created to speed up feature queries and those
# queries can be inefficient (e.g., O(n) based on the total possible
# number of features). Clients should cache query results needed regularly.
class ProtocolVersion(object):

    # @param major, minor, patch_level, software_revision_timestamp : int
    def __init__(self, major, minor, patch_level, software_revision_timestamp=None):
        self.major = major
        self.minor = minor
        self.patch_level = patch_level
        self.__software_revision = software_revision_timestamp

    def __str__(self):
        return self.to_user_str()

    # software_revision is used iff major, minor, patch are equal and
    # both self and other have a software_revision.
    def __eq__(self, other):
        isit = self.major == other.major and \
            self.minor == other.minor and \
            self.patch_level == other.patch_level
        if isit and ProtocolVersion.__have_revisions(self, other):
            isit = self.__software_revision == other.__software_revision
        return isit

    def __ne__(self, other):
        return not self == other

    # software_revision is used iff major, minor, patch are equal and
    # both self and other have a software_revision.
    def __gt__(self, other):
        if self.major > other.major:
            return True
        elif self.major == other.major:
            if self.minor > other.minor:
                return True
            elif self.minor == other.minor:
                if self.patch_level > other.patch_level:
                    return True
                elif self.patch_level == other.patch_level and \
                    ProtocolVersion.__have_revisions(self, other):
                        return self.__software_revision > other.__software_revision
        return False

    def __ge__(self, other):
        return self > other or self == other

    # software_revision is used iff major, minor, patch are equal and
    # both self and other have a software_revision.
    def __lt__(self, other):
        if self.major < other.major:
            return True
        elif self.major == other.major:
            if self.minor < other.minor:
                return True
            elif self.minor == other.minor:
                if self.patch_level < other.patch_level:
                    return True
                elif self.patch_level == other.patch_level and \
                    ProtocolVersion.__have_revisions(self, other):
                        return self.__software_revision < other.__software_revision
        return False

    def __le__(self, other):
        return self < other or self == other

    # Very simplistic check on the validity of the version parts
    # (e.g., not negative, not ridiculously large)
    def is_valid(self):
        return self.major >= 0 and self.major <= _MAJOR_VERSION_MAX and \
                self.minor >= 0 and self.minor <= _MINOR_VERSION_MAX and \
                self.patch_level >= 0 and self.patch_level <= _PATCH_LEVEL_MAX

    # Safe for display to user
    def to_user_str(self, include_software_revision=False):
        s = '{}.{}.{}'.format(self.major, self.minor, self.patch_level)
        if (include_software_revision and self.__software_revision):
            s += '+{}({})'.format(self.__software_revision,
                revision_timestamp_to_str(self.__software_revision))
        return s

    # Efficiency: O(n), based on total number of features possible
    # Clients are advised to cache the results if needed regularly.
    # @param feature enum ProtocolFeature
    # @see class ProtocolVersion
    def has_feature(self, feature):
        has_it = False

        # A feature under development can gated on a software revision
        # timestamp (which is similar to a build number), in addition
        # to a protocol version.
        #
        # e.g., self >= ProtocolVersion(3,2,0,1662563049603)

        # 1.1
        if feature == ProtocolFeature.STEP_COMMANDS:
            has_it = self >= ProtocolVersion(1,1,0)

        # 1.1.1 - 3.1.x
        elif feature == ProtocolFeature.BAD_LINE_NUMBER_IN_STACKTRACE_BUG:
            has_it = self >= ProtocolVersion(1,1,1) and \
                                self < ProtocolVersion(3,2,0)

        # 1.2
        elif feature == ProtocolFeature.BREAKPOINTS:
            has_it = self >= ProtocolVersion(1,2,0)

        # 2.0
        elif feature == ProtocolFeature.STOP_ON_LAUNCH_ALWAYS:
            has_it = self >= ProtocolVersion(2,0,0)
        elif feature == ProtocolFeature.ATTACHED_MESSAGE_DURING_STEP_BUG:
            has_it = self >= ProtocolVersion(2,0,0)

        # 2.1
        elif feature == ProtocolFeature.EXECUTE_COMMAND:
            has_it = self >= ProtocolVersion(2,1,0)

        # 3.0
        elif feature == ProtocolFeature.EXECUTE_RETURNS_ERRORS:
            has_it = self >= ProtocolVersion(3,0,0)

        elif feature == ProtocolFeature.UPDATES_HAVE_PACKET_LENGTH:
            has_it = self >= ProtocolVersion(3,0,0)

        # 3.1
        elif feature == ProtocolFeature.BREAKPOINTS_URI_SUPPORT:
            has_it = self >= ProtocolVersion(3,1,0)
        elif feature == ProtocolFeature.CASE_SENSITIVITY:
            has_it = self >= ProtocolVersion(3,1,0)
        elif feature == ProtocolFeature.CONDITIONAL_BREAKPOINTS:
            has_it = self >= ProtocolVersion(3,1,0)
        elif feature == ProtocolFeature.ERROR_FLAGS:
            has_it = self >= ProtocolVersion(3,1,0)

        # 3.1.1
        elif feature == ProtocolFeature.CONDITIONAL_BREAKPOINTS_ALLOW_EMPTY_CONDITION:
            has_it = self >= ProtocolVersion(3,1,1)

        # 3.2
        elif feature == ProtocolFeature.IMPROVED_LINE_NUMBERS_IN_TRACES:
            has_it = self >= ProtocolVersion(3,2,0)

        return has_it

    @staticmethod
    def __have_revisions(pver1, pver2):
        return pver1.__software_revision != None and \
            pver2.__software_revision != None

########################################################################
# Global functions
########################################################################

def get_supported_protocol_major_versions():
    return _SUPPORTED_PROTOCOL_MAJOR_VERSIONS

def get_supported_protocols_str():
    supported_str = ''
    for one_ver in sorted(_SUPPORTED_PROTOCOL_MAJOR_VERSIONS):
        if len(supported_str):
            supported_str += ','
        supported_str += '{}.x'.format(one_ver)
    return supported_str

# Exits this script if this client's protocol version is not
# compatible with any of this debugger's supported_versions.
# @param debuggee_version: ProtocolVersion
# @return void
def check_debuggee_protocol_version(debuggee_version):
    if debuggee_version.major not in _SUPPORTED_PROTOCOL_MAJOR_VERSIONS:
        msg = 'Unsupported protocol version: {}'.format(
                debuggee_version.to_user_str())
        print(msg, file=sys.stderr)
        print('Protocol versions supported are: {}'.format(
                get_supported_protocols_str()),
            file=sys.stderr)
        do_exit(1, msg)

def do_exit(exit_code, msg=None):
    global_config.do_exit(exit_code, msg)

########################################################################
# UNIT TESTS
########################################################################

if __RUN_UNIT_TESTS:
    tsearly = 1675381652000
    tslate  = 1675381660000

    # test ==
    assert ProtocolVersion(3,0,0) == ProtocolVersion(3,0,0)
    assert ProtocolVersion(3,2,0) == ProtocolVersion(3,2,0)
    assert ProtocolVersion(3,2,1) == ProtocolVersion(3,2,1)
    assert ProtocolVersion(3,0,0,tsearly) == ProtocolVersion(3,0,0)
    assert ProtocolVersion(3,0,0) == ProtocolVersion(3,0,0,tsearly)
    assert ProtocolVersion(3,0,0,tsearly) == ProtocolVersion(3,0,0,tsearly)
    assert not ProtocolVersion(4,0,0) == ProtocolVersion(3,0,0)
    assert not ProtocolVersion(3,1,0) == ProtocolVersion(3,0,0)
    assert not ProtocolVersion(3,2,1) == ProtocolVersion(3,2,0)

    # test >
    assert ProtocolVersion(4,3,2) > ProtocolVersion(3,9,9)
    assert ProtocolVersion(4,3,2) > ProtocolVersion(4,2,9)
    assert ProtocolVersion(4,3,2) > ProtocolVersion(4,3,1)
    assert ProtocolVersion(4,3,2,tslate) > ProtocolVersion(4,3,2,tsearly)
    assert ProtocolVersion(3,9,0,tsearly) > ProtocolVersion(3,8,0)
    assert ProtocolVersion(3,9,0) > ProtocolVersion(3,8,0,tslate)
    assert not ProtocolVersion(4,0,0) > ProtocolVersion(4,0,0)
    assert not ProtocolVersion(3,1,0) > ProtocolVersion(3,2,0)
    assert not ProtocolVersion(3,2,1) > ProtocolVersion(3,2,2)
    assert not ProtocolVersion(3,0,0,tsearly) > ProtocolVersion(3,0,0,tslate)
    assert not ProtocolVersion(3,8,0,tslate) > ProtocolVersion(3,8,0)
    assert not ProtocolVersion(3,8,0) > ProtocolVersion(3,8,0,tslate)

    # test <
    assert ProtocolVersion(4,3,2) < ProtocolVersion(5,9,9)
    assert ProtocolVersion(4,3,2) < ProtocolVersion(4,4,9)
    assert ProtocolVersion(4,3,2) < ProtocolVersion(4,3,9)
    assert ProtocolVersion(4,3,2,tsearly) < ProtocolVersion(4,3,2,tslate)
    assert ProtocolVersion(4,3,2,tsearly) < ProtocolVersion(4,3,3)
    assert ProtocolVersion(4,3,2,tsearly) <  ProtocolVersion(4,3,2, tslate)
    assert not ProtocolVersion(5,9,9) < ProtocolVersion(4,3,2)
    assert not ProtocolVersion(4,4,9) < ProtocolVersion(4,3,2)
    assert not ProtocolVersion(4,3,3) < ProtocolVersion(4,3,2)
    assert not ProtocolVersion(4,3,2,tslate) < ProtocolVersion(4,3,2,tsearly)
    assert not ProtocolVersion(4,3,3) < ProtocolVersion(4,3,2,tsearly)
    assert not ProtocolVersion(4,3,2,tslate) <  ProtocolVersion(4,3,2,tsearly)

    # Don't need to test !=, <=, nor >=, because they are implemented
    # with ==, <, >

    print('ProtocolVersion unit tests: PASS')
