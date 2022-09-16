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
# File: HTTPClient.py
# Requires python v3.5.3 or later
#
#
#  ********************************************
#  * WHY THIS FILE RE-INVENTS THE HTTP WHEEL  *
#  ********************************************
#
# REQUIREMENT:
# This package is intended as an easily-distributable script that
# should not require excessive effort or python expertese on the
# part of its user (a Roku channel developer) to install and use.
#
# The standard python 3.x http client packages are deficient and will not
# work for the purposes, here. In particular, it is a long-standing and
# apparently permanent problem that http.client and urllib do not
# support "Expect: 100-Continue" request headers, nor do they
# support "100: Continue" responses from the server. Additionally,
# urllib does not support "Connection: Keep-Alive" requests.
#
# There are some non-standard packages that solve these issues,
# such as 'requests' and 'urllib3'. However, the python package
# management also has a lot of vagaries and random failures that
# make them questionable for a script that is supposed to be
# easily usable, on any platform where python 3.x is supported.
#
# - For example, the setuptools are a bit of a mess (install_requirements,
# vs. required_packages, vs. just plain getting it to work, in the
# first place). Plus, it's not clear whether setuptools is a part of
# the standard distro.
# - Another example: pip works on windows 10, but does not work on
# linux (actually, pip gives the appearance of working perfectly on
# linux but only installs for python 2.x and python3 still won't work).
# - Additionally, most end users are going to ask, "what the heck is
# pipenv, suggested by the install docs for the requests package? Why
# doesn't pip work?" Does pip work? No clue. Again, python power user
# status absolutely must *not* be a requirement.
# - Compound all of the above with random failures of things that should
# work, and it's significantly less effort to simply re-write the http
# support, than it is to fight it out with the package/module management
# system.
# - And then circle back to, "why don't the standard packages support
# HTTP/1.1 features that have been around for many years?"
#
# All of the above can be, and often are, worked around by python experts
# installing a script. However, end users of this script should not be
# required to become python experts, just to run it.
#
# In short, this author lacks faith that the python package
# management system(s) will successfully install dependencies
# for this script. If end users experience problems, they are
# likely to give up on this script.
#
# And that is why the HTTP wheel is re-invented, here.
#
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

from .DebugUtils import dump_bytes

CRLF = '\r\n'
HDR_ENC = 'iso-8859-1'    # Encoding used for headers, responses
HTTP_VERSION_STR = 'HTTP/1.1'
DEFAULT_PORT = 80

# Specification of a form field, passed to buildMultipartFormData()
class HTTPFormFieldSpec(object):
    def __init__(self,
                 fieldName, fieldValue, attributes=None, contentType=None):
        self.mName = fieldName
        self.mValue = fieldValue
        self.mAttributes = attributes
        self.mContentType = contentType


class HTTPResponse(object):

    def __init__(self, socket, debug_level=0):
        self.__debug_level = debug_level
        self.mSocket = socket
        self._readResponse()

    def _readResponse(self):
        self.mHeaders = {}
        line = self._readLine()
        parts = line.split(' ', maxsplit=2)
        self.mHTTPVersion = parts[0]
        self.mStatus = int(parts[1])
        self.mReason = parts[2]
        while len(line):
            line = self._readLine()
            if len(line):
                parts = line.split(':', maxsplit=1)
                self.mHeaders[parts[0]] = parts[1].lstrip()

    def __str__(self):
        s = str('HTTPResponse[{} {}]'.format(
            self.mStatus, self.mReason))
        return s

    def getHeader(self, name):
        return self.mHeaders[name]

    # return a list of (name,value) tuples
    def getHeaders(self):
        headers = []
        for key in self.mHeaders.keys():
            headers.append((key, self.mHeaders[key]))
        return headers

    # internal - read to a crlf and return string of HDR_ENC encoding
    # return empty string at EOF
    def _readLine(self):
        buf = bytearray()
        while True:
            r = self.mSocket.recv(1)
            if not len(r): # EOF
                break
            buf.append(r[0])
            if (len(buf) >= 2) and (buf[len(buf)-2:] == b'\r\n'):
                break
        line = str(buf[:len(buf)-2], HDR_ENC)
        if (self.__debug_level >= 3) and len(line):
            print('debug: http recv: {}'.format(line))
        return line

# END class HTTPResponse


# A minimal subset of HTTP that supports 'Expect: 100-continue'
# in requests and reponse status '100: Continue'
class HTTPConnection(object):
    # netLoc is str host[:port]  (port is optional)
    def __init__(self, netLoc, debug_level=0):
        self.__debug_level = debug_level
        parts = netLoc.split(':')
        self.mHost = parts[0]
        if len(parts) > 1:
            self.mPort = int(parts[1])
        else:
            self.mPort = DEFAULT_PORT
        self.mSocket = None

    def set_debug_level(self, debug_level):
        self.__debug_level = debug_level

    def connect(self):
        import socket
        self.mSocket = socket.create_connection((self.mHost, self.mPort))

    def close(self):
        if self.mSocket:
            self.mSocket.close()
            self.mSocket = None

    def send(self, data):
        if self.__debug_level >= 5:
            maxLen = 500
            if self.__debug_level >= 10:
                maxLen = None
            print('debug: http send: ', end='')
            dump_bytes(data, forceEol=True, maxLen=maxLen)
        count = self.mSocket.send(data)
        if count != len(data):
            raise ConnectionError('Failed sending data to server')
        return count

    def putRequest(self, method, path):
        self.send('{} {} {}{}'.format(
            method, path, HTTP_VERSION_STR, CRLF).encode(HDR_ENC))

    # headers must be a list of (name,value) tuples
    def putHeaders(self, headers):
        for (name,value) in headers:
            self.send('{}: {}{}'.format(name, value, CRLF)
                .encode(HDR_ENC))

    def endHeaders(self):
        self.send(CRLF.encode(HDR_ENC))

    # @return HTTPResponse object
    def getResponse(self):
        return HTTPResponse(self.mSocket, self.__debug_level)

    # Get the data and a boundary token. The boundary separates
    # form fields in the body, and must be included in the
    # Content-Type header.
    # @return (str boundary, byte[] data)
    @staticmethod
    def buildMultipartFormData(fieldSpecs):
        import uuid
        boundary = '{}'.format(uuid.uuid4())  # random
        body = bytearray()
        crlfBytes = '\r\n'.encode(HDR_ENC)
        for field in fieldSpecs:
            body.extend('--{}\r\n'.format(boundary).encode(HDR_ENC))
            body.extend('Content-Disposition: form-data; name="{}"'.format(
                field.mName).encode(HDR_ENC))
            attrs = field.mAttributes
            if attrs:
                for iAttr in range(len(attrs))[::2]:
                    attrKey = field.mAttributes[iAttr]
                    attrValue = field.mAttributes[iAttr+1]
                    body.extend('; {}="{}"'.format(
                        attrKey, attrValue).encode(HDR_ENC))
            body.extend(crlfBytes)
            if field.mContentType:
                body.extend('Content-Type:{}\r\n'.format(
                    field.mContentType).encode(HDR_ENC))
            body.extend(crlfBytes)
            if isinstance(field.mValue, bytes) or \
                    isinstance(field.mValue, bytearray):
                body.extend(field.mValue)
            else:
                body.extend('{}'.format(field.mValue).encode(HDR_ENC))
            body.extend(crlfBytes)

        body.extend('--{}\r\n'.format(boundary).encode(HDR_ENC))

        return (boundary, body)

    def debugDumpRequest(self, headers, bodyData):
        print('\n\nvvvvvvvvvv debug:REQUEST vvvvvvvvvv')
        print('debug: HEADERS:')
        for name in sorted(headers.keys()):
            value = headers[name]
            print('{}:{}'.format(name, value))
        dump_bytes(bodyData, label='BODY', forceEol=True)
        print('^^^^^^^^^^ debug:REQUEST ^^^^^^^^^^')

import sys
def do_exit(errCode, msg=None):
    sys.modules['__main__'].do_exit(errCode, msg)
