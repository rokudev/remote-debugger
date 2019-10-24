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
# File: DebuggerClient.py
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

import socket, sys, threading, time

from .DebuggerIOListener import DebuggerIOListener
from .StreamUtils import StreamUtils

DEBUGGER_PORT = 8081
DEBUGGER_CONNECTION_TIMEOUT_SECONDS = 60
DEBUGGER_MAGIC = 0x0067756265647362 # 64-bit = [b'bsdebug\0' little-endian]
DEBUGGER_MAGIC_BYTES_LE = b'bsdebug\0'

class DebuggerClient(object):

    # Attribute protocol_version is None until successful call is made
    # to connect_control(), which performs the handshake to the debug
    # target
    def __init__(self, target_ip_addr):
        global gMain
        gMain = sys.modules['__main__'].gMain
        self.protocol_version = None   # Set in connect_control()
        self.has_bad_line_number_in_stop_bug = None # Set in connect_control()
        self.__debug = max(gMain.gDebugLevel, 0)
        self.__io_listener = None
        self.__next_request_id = 1 # start with 1 b/c 0 is confused with None
        self.__target_ip_addr = target_ip_addr
        self.__request_id_lock = threading.Lock()

    # @return None if connect_control() has not been called
    def get_protocol_version_str(self):
        if self.protocol_version == None:
            return None
        s = ''
        for v in self.protocol_version:
            if len(s):
                s = s + '.'
            s += str(v)
        return s

    def get_next_request_id(self):
        with self.__request_id_lock:
            id = self.__next_request_id
            self.__next_request_id += 1
        return id

    # Connect to the debugger's control port
    # Sets self.protocol_version, self.has_stop_line_number_bug
    def connect_control(self):
        print('info: connecting to debug target {}:{} ...'.format(
                self.__target_ip_addr, DEBUGGER_PORT))
        # If we attempt to connect, prior to the target listening, there
        # is a lag of several seconds between the target listening and the
        # connection being established. To speed things, up, we attempt
        # a connection repeatedly with a short timeout.
        timeout = DEBUGGER_CONNECTION_TIMEOUT_SECONDS
        connected = False
        tryCount = 0
        now = gMain.get_monotonic_time()
        retryEndTime = now + DEBUGGER_CONNECTION_TIMEOUT_SECONDS
        while ((not connected) and (now < retryEndTime)):
            tryCount += 1
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            if self.__debug >= 1:
                print('debug: attempting connection {} (timeout={}s,remaining={}s)...'.format(
                    tryCount, timeout, (retryEndTime-now)))
            try:
                sock.connect((self.__target_ip_addr, DEBUGGER_PORT))
                connected = True
            except socket.timeout:
                now = gMain.get_monotonic_time()
                timeout = min((1.1 * timeout), (retryEndTime - now))
                pass
            now = gMain.get_monotonic_time()

        if not connected:
            do_exit(1, 'Could not connect to {}:{}'.format(
                self.__target_ip_addr, DEBUGGER_PORT))

        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(1e+6) # normal state is blocked waiting for event
        self.mSock = sock
        self.__do_handshake()

        # bad line number in stop bug was fixed in protocol v1.1.1
        protoVer = self.protocol_version
        self.has_bad_line_number_in_stop_bug = \
            ((protoVer[0] <= 1) and (protoVer[1] <= 1) and (protoVer[2] <= 0))

        print('info: connected to debug target, protocol version={}.{}.{} build={}'.format(
            self.protocol_version[0],
            self.protocol_version[1],
            self.protocol_version[2],
            self.protocol_version[3]))

    def recv_double(self):
        return StreamUtils.read_ieee754binary64_le(self.mSock)

    def recv_float(self):
        return StreamUtils.read_ieee754binary32_le(self.mSock)

    def recv_byte(self):
        return StreamUtils.read_uint8(self.mSock)

    def recv_int(self):
        return StreamUtils.read_int32_le(self.mSock)

    def recv_uint(self):
        return StreamUtils.read_uint32_le(self.mSock)

    def recv_long(self):
        return StreamUtils.read_int64_le(self.mSock)

    def recv_str(self):
        s = StreamUtils.read_utf8(self.mSock)
        if self.__debug >= 10:
            print('debug: dclient.recv_str() s={}'.format(s))
        return s

    def send_bool(self, bool_val):
        return StreamUtils.write_bool(self.mSock, bool_val)

    def send_byte(self, byte_val):
        return StreamUtils.write_uint8(self.mSock, byte_val)

    def send_uint(self, val):
        return StreamUtils.write_uint32_le(self.mSock, val)

    def send_str(self, val):
        return StreamUtils.write_utf8(self.mSock, val)

    # Shuts down the connection to the debugging target.
    # This should only be called after the response to the
    # final request is received, because unsent data will
    # be discarded (at least on some platforms).
    def shutdown(self):
        if self.mSock:
            if self.__debug >= 1:
                print('debug: closing socket')
            try:
                self.mSock.shutdown(socket.SHUT_RDWR)
            except:
                pass
            try:
                self.mSock.close()
            except:
                pass
            self.mSock = None

    # Connect to the debugger's I/O port. This happens when a message
    # comes over an existing connection to the debugger's control port,
    # which tells this client to connect to another port to retrieve
    # output from the script being debugged.
    def connect_io_port(self, port, out):
        self.__io_listener = DebuggerIOListener(
            self.__target_ip_addr, port, out)

    # Initial handshake with debug server
    # REQUIRES: self.mSock is a connected socket
    # MODIFIES: sets self.protocol_version
    def __do_handshake(self):
        sock = self.mSock
        if self.__debug >= 1:
            print('debug: socket connected, attempting handshake')

        # Exchange magic number
        StreamUtils.write_uint64_le(self.mSock, DEBUGGER_MAGIC)
        readMagic = StreamUtils.read_uint64_le(sock)
        if readMagic != DEBUGGER_MAGIC:
            do_exit(1, "Bad magic number from debug target")

        # Get protocol version from target
        self.protocol_version = [
            StreamUtils.read_uint32_le(sock),
            StreamUtils.read_uint32_le(sock),
            StreamUtils.read_uint32_le(sock),
            ""  # build ID
        ]


import sys
def do_exit(errCode, msg=None):
    sys.modules['__main__'].do_exit(errCode, msg)
