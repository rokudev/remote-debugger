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

import enum, sys
from .Verbosity import Verbosity

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level, do_exit()

_SUPPORTED_PROTOCOL_MAJOR_VERSIONS = [1,2,3] # must be array (list does not support "in")

_MAJOR_VERSION_MAX = 999
_MINOR_VERSION_MAX = 999
_PATCH_LEVEL_MAX = 999

@enum.unique
class ProtocolFeature(enum.IntEnum):
    UNDEF = 0,
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

    def to_user_string(self):
        feature = ProtocolFeature
        s = self.name
        user_string = s.lower() # pylint: disable=no-member
        # Special cases
        if self.value == feature.STOP_ON_LAUNCH_ALWAYS:
            user_string = "stop_on_launch"  # To user, it's not "always"
        return user_string

class ProtocolVersion(object):

    # @param major, minor, patch_level : int
    def __init__(self, major, minor, patch_level):
        self.major = major
        self.minor = minor
        self.patch_level = patch_level
        self.__platform_revision = None

    def __str__(self):
        return self.to_user_str()

    def __eq__(self, other):
        return ProtocolVersion.__static_to_int(self) == \
                ProtocolVersion.__static_to_int(other)
    def __ne__(self, other):
        return ProtocolVersion.__static_to_int(self) != \
                ProtocolVersion.__static_to_int(other)
    def __gt__(self, other):
        return ProtocolVersion.__static_to_int(self) > \
                ProtocolVersion.__static_to_int(other)
    def __ge__(self, other):
        return ProtocolVersion.__static_to_int(self) >= \
                ProtocolVersion.__static_to_int(other)
    def __lt__(self, other):
        return ProtocolVersion.__static_to_int(self) < \
                ProtocolVersion.__static_to_int(other)
    def __le__(self, other):
        return ProtocolVersion.__static_to_int(self) <= \
                ProtocolVersion.__static_to_int(other)

    @staticmethod
    def get_max_version():
        max_ver = ProtocolVersion(_MAJOR_VERSION_MAX, _MINOR_VERSION_MAX, _PATCH_LEVEL_MAX)
        assert max_ver.is_valid()
        return max_ver

    # During a platform release cycle, the debug target's behavior may be
    # different for different platform_revision timestamps. This is similar
    # to a build number and should never affect production builds.
    def set_platform_revision(self, platform_revision):
        self.__platform_revision = platform_revision

    def get_platform_revision(self):
        return self.__platform_revision

    # Very simplistic check on the validity of the version parts
    # (e.g., not negative, not ridiculously large)
    def is_valid(self):
        return self.major >= 0 and self.major <= _MAJOR_VERSION_MAX and \
                self.minor >= 0 and self.minor <= _MINOR_VERSION_MAX and \
                self.patch_level >= 0 and self.patch_level <= _PATCH_LEVEL_MAX

    # Safe for display to user
    def to_user_str(self):
        return '{}.{}.{}'.format(
            self.major, self.minor, self.patch_level)

    # @param feature enum ProtocolFeature
    def has_feature(self, feature):
        has_it = False
        enabled_by_revision = False
        disabled_by_revision = False

        # 1.1
        if feature == ProtocolFeature.STEP_COMMANDS:
            has_it = self >= ProtocolVersion(1,1,0)

        # 1.1.1 - 3.1.1+1660254781319
        elif feature == ProtocolFeature.BAD_LINE_NUMBER_IN_STACKTRACE_BUG:
            has_it = self >= ProtocolVersion(1,1,1) and self < ProtocolVersion(3,1,1)
            if not has_it:
                if self == ProtocolVersion(3,1,1) and self.__platform_revision < 1660254781319:
                    # pre-release build still has this bug
                    has_it = True
                    enabled_by_revision = True

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
            if has_it:
                if self.__platform_revision and self.__platform_revision < 1650905541605:
                    # pre-release build does not have feature
                    has_it = False
                    disabled_by_revision = True
        elif feature == ProtocolFeature.CASE_SENSITIVITY:
            has_it = self >= ProtocolVersion(3,1,0)
        elif feature == ProtocolFeature.CONDITIONAL_BREAKPOINTS:
            has_it = self >= ProtocolVersion(3,1,0)
        elif feature == ProtocolFeature.ERROR_FLAGS:
            has_it = self >= ProtocolVersion(3,1,0)
            if has_it:
                if self.__platform_revision and self.__platform_revision < 1658337558223:
                    has_it = False
                    disabled_by_revision = True

        # 3.1.1
        elif feature == ProtocolFeature.CONDITIONAL_BREAKPOINTS_ALLOW_EMPTY_CONDITION:
            has_it = self >= ProtocolVersion(3,1,1)

        if global_config.debug_level >= 1: # 1 = validation
            if has_it:
                assert not disabled_by_revision, f'feature={str(feature)}'
            else:
                assert not enabled_by_revision, f'feature={str(feature)}'
            assert not (enabled_by_revision and disabled_by_revision)

        if enabled_by_revision and global_config.verbosity >= Verbosity.NORMAL:
            print('info: enabling feature based on revision timestamp: {}'.format(str(feature)))
        if disabled_by_revision and global_config.verbosity >= Verbosity.NORMAL:
            print('info: disabling feature based on revision timestamp: {}'.format(str(feature)))

        return has_it

    # In python 3, "All integers are implemented as long integer
    # objects of arbitrary size."
    def __to_int(self):
        assert self.is_valid()
        # python has unlimited precision
        return self.major * int(1e+9) + \
                    self.minor * int(1e+6) + \
                    self.patch_level

    #@return large int representing protocol_version, 0 if protocol_version==None
    @staticmethod
    def __static_to_int(protocol_version):
        if not protocol_version:
            return 0
        return protocol_version.__to_int()


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
