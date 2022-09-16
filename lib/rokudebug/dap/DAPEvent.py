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
# File: DAPEvent.py
# Requires python 3.5.3 or later
#
# Events are unsolicited messages from this script to the DAP receiver
# (usually an IDE).
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

import enum, sys

from rokudebug.model.DebuggerResponse import ThreadStopReason as BsThreadStopReason

from .DAPProtocolMessage import DAPProtocolMessage
from .DAPTypes import DAPOutputCategory
from .DAPTypes import LITERAL

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level, do_exit()

########################################################################
# DAP EVENTS
# Messages from this adapter via the DAP protocol (typically to an IDE),
# without a request
########################################################################

# Numeric values are only used locally. Use to_dap_str() to get a
# string that can be passed in a DAP message.
@enum.unique
class DAPStopReason(enum.IntEnum):
    UNDEF = 0
    BREAKPOINT = enum.auto()
    ERROR = enum.auto()
    PAUSE = enum.auto()
    STEP = enum.auto()

    # Return a string that can be part of a DAP message. Messages always
    # use these strings, and never integer values.
    def to_dap_str(self):
        if not self.value:
            return None
        return self.name.lower()    # pylint: disable=no-member

# Numeric values are only used locally. Use to_dap_str() to get a
# string that can be passed in a DAP message
@enum.unique
class DAPThreadEventReason(enum.IntEnum):
    UNDEF = 0
    EXITED = enum.auto()
    STARTED = enum.auto()

    # Return a string that can be part of a DAP message. Messages always
    # use these strings, and never integer values.
    def to_dap_str(self):
        if not self.value:
            return None
        return self.name.lower()    # pylint: disable=no-member



class _EventBody(object): pass


class DAPEvent(DAPProtocolMessage):
    def __init__(self, event_name):
        super(DAPEvent,self).__init__(LITERAL.event)
        self.event = event_name
        self.body = None        # Optional: set by subclass


# Sent when this debugger is ready to accept configuration
# requests, such as setBreakpoints. This may be after the
# debuggee has been started.
class DAPInitializedEvent(DAPEvent):
    def __init__(self):
        super(DAPInitializedEvent, self).__init__(LITERAL.initialized)


# Output from the debuggee, sent to the DAP receiver (typically an IDE)
class DAPOutputEvent(DAPEvent):
    # @param category enum DAPOutputCategory
    def __init__(self, category, output):
        super(DAPOutputEvent, self).__init__(LITERAL.output)
        assert isinstance(category, DAPOutputCategory)
        assert output
        self.body = _EventBody()
        self.body.category = category.to_dap_str()
        self.body.output = output


class DAPStoppedEvent(DAPEvent):

    # @param reason enum DAPStopReason
    # @param description human-readable string
    def __init__(self, reason, description, thread_id):
        super(DAPStoppedEvent, self).__init__(LITERAL.stopped)
        body = _EventBody()
        self.body = body
        body.reason = reason.to_dap_str()
        body.thread_id = thread_id
        body.description = description
        body.text = description
        body.allThreadsStopped = True


class DAPThreadEvent(DAPEvent):
    # @param reason enum ThreadEventReason
    def __init__(self, thread_id, reason):
        assert thread_id != None and thread_id >= 0
        assert isinstance(reason, DAPThreadEventReason)

        super(DAPThreadEvent, self).__init__(LITERAL.thread)
        self.body = _EventBody()
        self.body.reason = reason.to_dap_str()
        self.body.thread_id = thread_id
