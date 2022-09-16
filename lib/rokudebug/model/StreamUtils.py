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
# File: StreamUtils.py
# Requires python v3.5.3 or later
#
# NAMING CONVENTIONS:
#
# Type identifiers are CamelCase
# all_other identifiers are snake_case
# _protected members begin with a single underscore '_' (friends can access)
# __private members begin with double underscore: '__'
#
# python more or less enfores the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import ctypes, struct, sys, traceback

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # do_exit(), verbosity, global debug_level

BITS_PER_BYTE = 8
IEEE754_BINARY64_NUM_BYTES = 8
IEEE754_BINARY32_NUM_BYTES = 4
UINT32_NUM_BYTES = 4
UINT64_NUM_BYTES = 8

_debug_level = 0

class StreamUtils(object):

    ##########################################################################
    # READ DATA
    ##########################################################################

    @staticmethod
    def read_uint8(sock, counter):
        return StreamUtils.recv(sock, 1, counter)[0]    # read unsigned 32-bit value, little-endian

    # read little-endian unsigned value of specified length
    # return uint32 value
    @staticmethod
    def read_uint_le(sock, num_bytes, counter):
        buf = StreamUtils.recv(sock, num_bytes, counter)
        uintVal = 0
        for i in range(0,len(buf)):
            uintVal |= (buf[i] << (BITS_PER_BYTE*i))
        return uintVal

    # return uint32 value
    @staticmethod
    def read_uint32_le(sock, counter):
        return StreamUtils.read_uint_le(sock, UINT32_NUM_BYTES, counter)

    # read signed 64-bit value, little-endian
    @staticmethod
    def read_int64_le(sock, counter):
        # python does not have signed values, so read unsigned
        # two's complement and convert
        uval = StreamUtils.read_uint64_le(sock, counter)
        if uval & 0x8000000000000000:
            ival = uval - (2 ** 64)
        else:
            ival = uval
        return ival

    # read signed 32-bit value, little-endian
    @staticmethod
    def read_int32_le(sock, counter):
        # python does not have signed values, so read unsigned
        # two's complement and convert
        uval = StreamUtils.read_uint32_le(sock, counter)
        if uval & 0x80000000:
            ival = uval - (2 ** 32)
        else:
            ival = uval
        return ival

    # read unsigned 64-bit value, little-endian
    # return uint64 value
    @staticmethod
    def read_uint64_le(sock, counter):
        return StreamUtils.read_uint_le(sock, UINT64_NUM_BYTES, counter)

    # read 32-bit floating-point IEEE-754 binary32 value, encoded little-endian
    # @return 64-bit floating point
    @staticmethod
    def read_ieee754binary32_le(sock, counter):
        # struct pack/unpack explicitly support IEEE-754 binary32/64 data
        # 'd' = double, 'f' = float, '<' = little-endian
        assert(struct.calcsize('<f') == IEEE754_BINARY32_NUM_BYTES)
        buf = StreamUtils.recv(sock, IEEE754_BINARY32_NUM_BYTES, counter)
        return struct.unpack('<f', buf)[0]

    # read 64-bit floating-point IEEE-754 binary64 value, encoded little-endian
    # @return 64-bit floating point
    @staticmethod
    def read_ieee754binary64_le(sock, counter):
        # struct pack/unpack explicitly support IEEE-754 binary32/64 data
        # 'd' = double, 'f' = float, <' = little-endian
        assert(struct.calcsize('<d') == IEEE754_BINARY64_NUM_BYTES)
        buf = StreamUtils.recv(sock, IEEE754_BINARY64_NUM_BYTES, counter)
        return struct.unpack('<d', buf)[0]

    @staticmethod
    def read_utf8(sock, counter):
        buf = bytearray()
        while True:
            b = StreamUtils.recv(sock, 1, counter)[0]
            if not b:
                break
            buf.append(b)
        return str(buf, encoding='utf-8')

    # Exits this script if EOF is seen
    # @return byte array
    # private method, intended only for use within this module
    @staticmethod
    def recv(sock, num_bytes, counter):
        buf_len = 0
        try:
            buf = sock.recv(num_bytes)
            buf_len = len(buf)
            if counter:
                counter.byte_read_count += buf_len;
        except Exception:
            if global_config.debug_level >= 2:
                print('debug: exception:')
                traceback.print_exc(file=sys.stdout)

        if buf_len != num_bytes:
            if StreamUtils.__check_debug(2):
                print('debug: bad read occurred during recv(), expected={},actual={}:'.\
                    format(num_bytes, buf_len))
                traceback.print_stack()
            global_config.do_exit(1,
                'Unexpected EOF reading debug target stream')
        return buf

    ##########################################################################
    # WRITE DATA
    ##########################################################################

    # write lowest 8 bits int_val as one unsigned byte
    # @return number of bytes written
    @staticmethod
    def write_uint8(sock, int_val):
        if not isinstance(int_val, int):
            raise TypeError('not a byte/int type')
        int_val = int_val & 0xFF
        return StreamUtils.send(sock, bytes([int_val]))

    # write unsigned value of specified length, as
    # stream of little-endian bytes
    # @return number of bytes written
    @staticmethod
    def write_uint_le(sock, uint_val, numBytes):
        buf = bytearray(numBytes)
        for i in range(0,len(buf)):
            buf[i] = uint_val & 0xff
            uint_val >>= BITS_PER_BYTE
        return StreamUtils.send(sock, buf)

    # write unsigned 32-bit value, little-endian
    # @return number of bytes written
    @staticmethod
    def write_uint32_le(sock, val):
        return StreamUtils.write_uint_le(sock, val, UINT32_NUM_BYTES)

    # write unsigned 64-bit value, little-endian
    # @return number of bytes written
    @staticmethod
    def write_uint64_le(sock, val):
        return StreamUtils.write_uint_le(sock, val, UINT64_NUM_BYTES)

    @staticmethod
    def write_utf8(sock, val):
        buf = val.encode('utf-8')  # does not place trailing 0 in buf
        count = StreamUtils.send(sock, buf)
        count += StreamUtils.send(sock, b'\0')
        return count

    # Exits this script if connection is closed
    # @return number of bytes written
    @staticmethod
    def send(sock, byte_buf):
        count = sock.send(byte_buf)
        if len(byte_buf) != count:
            global_config.do_exit(1,
                'Unexpected EOF writing debug target stream')
        return count


    ##########################################################################
    # UTILITY FUNCTIONS
    ##########################################################################

    @staticmethod
    def __check_debug(min_level):
        lvl = max(global_config.debug_level, _debug_level)
        if lvl: assert global_config.debug_level >= 0 and _debug_level >= 0 and min_level >= 1
        return lvl >= min_level
