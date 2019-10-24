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
# File: AppInstallerClient.py
# Requires python v3.5.3 or later
#
#  ********************************************
#  * WHY THIS FILE RE-INVENTS THE HTTP WHEEL  *
#  ********************************************
#
# REQUIREMENT:
# This file is intended as an easily-distributable script that
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
# easily installable on any platform where python 3.x is supported.
#
# - For example, the setuptools are a bit of a mess (install_requirements,
# vs. required_packages, vs. just plain getting it to work, in the
# first place). Plus, it's not clear whether setuptools is a part of
# the standard distro.
# - Another example:, pip works on windows 10, but does not work on
# linux (actually, pip gives the appearance of working perfectly on
# linux but only installs for python 2.x and python3 still won't work).
# - Additionally, most end users are going to ask, "what the heck is
# pipenv, suggested by the install docs for the requests package? Why
# doesn't pip work?" Does pip work? No clue. Again, python power user
# status must not be a requirement.
# - Compound all of the above with random failures of things that should
# work, and it's less effort to simply re-write the http support, than
# it is to fight it out with the package/module management system.
# - And then circle back to, "why don't the standard packages support
# HTTP/1.1 features that have been around for many years?"
#
# All of the above can be, and often are, worked around by python experts
# installing a script. However, end users of this script should not be
# required to become python experts, just to run it.
#
# In short, this author lacks faith that the python package management
# system(s) will successfully install dependencies for end users. Spurious
# failures will cause users to give up on this script.
#
# And that is why the HTTP wheel is re-invented, here.
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

import hashlib, os, re, sys, traceback
from urllib.parse import urlparse
from .HTTPClient import HTTPConnection, HTTPResponse, HTTPFormFieldSpec

CRLF = '\r\n'
CRLF2 = '\r\n\r\n'
HTTP_VERSION_STR = 'HTTP/1.1'
INSTALLER_REALM = 'rokudev'
ISO_8859_1 = 'iso-8859-1'
TIMEOUT_SECONDS = 60
USER_NAME = 'rokudev'
UTF8 = 'utf-8'
PORT = 80


class AppInstallerClient(object):
    def __init__(self, ipAddr, userPass):
        global gMain
        gMain = sys.modules['__main__'].gMain
        self.mDebug = max(gMain.gDebugLevel, 0)
        self.mIPAddr = ipAddr
        self.mUserPass = userPass
        self.mInstallerBaseUrl = \
             'http://{}:{}/plugin_install'.format(self.mIPAddr, PORT)

    def remove(self):
        print('Removing dev channel, if installed...')
        (boundary, bodyData) = HTTPConnection.buildMultipartFormData([
            HTTPFormFieldSpec('mysubmit', 'Delete'),
            HTTPFormFieldSpec('archive', ''),
        ])
        headers = self.getHeadersForPost(boundary, bodyData)
        self.doPost(headers, bodyData)

    def install(self, channelFilePath, remoteDebug):
        channelFileName = os.path.basename(channelFilePath)
        print('Installing dev channel ({})...'.format(channelFileName))
        f = open(channelFilePath, 'rb')
        channelContents = f.read()
        f.close()

        fields = [
            HTTPFormFieldSpec('mysubmit', 'Install'),
            HTTPFormFieldSpec('archive', channelContents,
                attributes=['filename',
                            os.path.basename(channelFilePath)],
                contentType='application/octet-stream'),
        ]
        if remoteDebug:
            fields.append(HTTPFormFieldSpec('remotedebug', '1'))
        (boundary, bodyData) = HTTPConnection.buildMultipartFormData(fields)
        headers = self.getHeadersForPost(boundary, bodyData)
        return self.doPost(headers, bodyData)

    def doPost(self, headers, bodyData):
        # The Roku Application Installer uses digest authentication. This
        # is how the upload typically works:
        #
        # 1) This client sends a POST request to upload a channel, with
        #    the header "Expect: 100-continue"
        # 2) After sending the request headers (but not the body), this
        #    client waits for a response from the server.
        # 3) The server responds with "401 Unauthorized". The response
        #    includes a "WWW-Authenticate" header with one-time-use crypto
        #    information that can be used to authenticate this client.
        # 4) If a 401 response code is received, this client does not send
        #    the message body. This is crucial, because the server is not
        #    expecting a body, and the connection would fail while sending.
        # 5) This client re-submits the same request with an additional
        #    "Authentication" header, derived from the 401 response. This
        #    request also includes an "Expect: 100-continue" header.
        # 6) After sending the request headers (but *not* the body), this
        #    client waits for a "100 Continue" or error response.
        # 7) The server responds with "100 Continue"
        # 8) This client sends the message body
        # 9) The server responds with "200 OK"
        #

        url = self.mInstallerBaseUrl
        conn = None

        try:
            urlParts = urlparse(url)
            netLoc = urlParts[1]  # netLoc = 'host[:port]'
            path = ''
            for i in range(2, len(urlParts)):
                path += urlParts[i]

            # Send the request

            conn = HTTPConnection(netLoc)
            conn.setDebugLevel(self.mDebug)
            conn.connect()
            conn.putRequest('POST', path)  # takes path, not URL (despite docs)
            conn.putHeaders(headers)
            conn.endHeaders()

            # Wait for a response
            # We've specified 'Exit: 100-continue', so wait for 100-continue
            # from server before sending message body
            response = conn.getResponse()

            if self.mDebug >= 3:
                print('debug: POST response, uri={}: {} {}, headers={}'.format(
                    url, response.mStatus, response.mReason, response.getHeaders()))
            if response.mStatus == 401:
                # The 401 error contains one-time-use crypto info for the auth
                # digest response
                # Note: might want to create function for 401.
                #       e.g. response = self.handleHttp401(...)
                if self.mDebug >= 1:
                    print('debug: 401 from app installer, retrying')
                responseHeaders = response.getHeaders()
                contentLenStr = response.getHeader('Content-Length')

                if (not contentLenStr) or (contentLenStr != '0'):
                    raise ValueError(
                        'Bad Content-Length in 401 auth response from server:{}'.format(
                            contentLenStr))

                self.addDigestAuthHeaders(
                    headers, responseHeaders, path, self.mUserPass)
                if self.mDebug >= 1:
                    print("debug: sending follow-up to 401 error, headers={}".format(
                        headers))
                conn = HTTPConnection(netLoc)
                conn.setDebugLevel(self.mDebug)
                conn.connect()
                conn.putRequest('POST', path)
                conn.putHeaders(headers)
                conn.endHeaders()
                response = conn.getResponse()
                if response.mStatus == 100:
                    if self.mDebug >= 3:
                        print('debug: recv: {}'.format(response))
                else:
                    do_exit(1, 'Bad response from app installer: {} {}'.format(
                        response.mStatus, response.mReason))
                conn.send(bodyData)
                response = conn.getResponse()
                print('info: final response from device: {} {}'.format(
                        response.mStatus, response.mReason))
                conn.close()
            else:
                do_exit(1, 'Bad response from app installer: {} {}'.format(
                    response.mStatus, response.mReason))

            if self.mDebug >= 3:
                print('debug: POST response, uri={}: {} {}, headers={}'.format(
                    url, response.mStatus, response.mReason, response.getHeaders()))

        except Exception as e:
            print('debug = {}'.format(self.mDebug))
            if self.mDebug:
                sys.stdout.flush()
                traceback.print_exc(file=sys.stderr)
            try:
                if conn:
                    conn.close()
                    conn = None
            except Exception:
                if self.mDebug:
                    traceback.print_exc(file=sys.stderr)

            do_exit(1, 'Failed: {}: {}'.format(e, url))

        if self.mDebug >= 1:
            print('debug: http response: {}'.format(response))

    # @return dict of header name:value
    def getHeadersForGet(self):
        headers = self.getCommonHeaders(None)
        return headers

    # @return list of header (name,value) tuples
    def getHeadersForPost(self, boundary, bodyData):
        headers = self.getCommonHeaders(bodyData)
        headers.append(('Content-Type', 'multipart/form-data; boundary={}'.\
            format(boundary)))
        headers.append(('Expect', '100-continue'))
        return headers

    # @return list of header (name,value) tuples
    def getCommonHeaders(self, bodyData):
        bodyLen = 0
        if bodyData:
            bodyLen = len(bodyData)
        return [
            ('Accept', '*/*'),
            ('Content-Length', bodyLen),
            ('User-Agent', 'rokudebug/{}'.format(gMain.get_version_str()))
        ]

    def calcMD5ForHeader(self, value):
        valueBytes = value
        if isinstance(value, str):
            valueBytes = value.encode(UTF8)
        return hashlib.md5(valueBytes).hexdigest()


    # See doPost() for comments regarding the deficiencies in the
    # python3 http client packages, that require this to be done here.
    def addDigestAuthHeaders(self, headers, http401ResponseHeaders, path, passwd):
        authVal = None
        contentLengthStr = None
        for (name, value) in http401ResponseHeaders:
            if name == 'WWW-Authenticate':
                authVal = value
            if name == 'Content-Length':
                contentLengthStr = value

        contentLength = None
        try:
            contentLength = int(contentLengthStr)
        except Exception as e:
            sys.stdout.flush()
            traceback.print_exc(file=sys.stderr)
            print('Bad Content-Length: {}'.format(e))

        assert authVal
        assert contentLength != None    # 0 is valid

        # server nonce is a one-time use key used for secure hashing
        # REMIND: handle None return from search() (perhaps try/except)
        serverNonce = re.search('nonce="(.*?)"', authVal).group(1)
        realm = re.search('realm="(.*?)"', authVal).group(1)
        qop = re.search('qop="(.*?)"', authVal).group(1)

        # client nonce is generated by the client (this program) and is used
        # by the server to securely hash responses
        clientNonceInt = 0
        for b in os.urandom(4):
            clientNonceInt <<= 8
            clientNonceInt |= b
        clientNonce = '{:08x}'.format(clientNonceInt)

        # client nonce count must increase with each request, for cryptographic
        # security. However, we are sending only one request with this nonce.
        clientNonceCount = "00000001"

        HA1 = self.calcMD5ForHeader('{}:{}:{}'.format(
            USER_NAME, realm, passwd))
        HA2 = self.calcMD5ForHeader('POST:{}'.format(path))
        response = self.calcMD5ForHeader('{}:{}:{}:{}:{}:{}'.format(
            HA1, serverNonce, clientNonceCount, clientNonce, qop, HA2))
        headers.append(('Authorization',
            'Digest'
            ' username="{}", realm="{}", nonce="{}", uri="{}"'
            ', algorithm={}, response="{}", qop={}'
            ', nc={}, cnonce="{}"'.format(
                USER_NAME, realm, serverNonce, path, 'MD5',
                response, qop, clientNonceCount, clientNonce)
        ))
    # end of class AppInstallerClient

import sys
def do_exit(errCode, msg=None):
    sys.modules['__main__'].do_exit(errCode, msg)
