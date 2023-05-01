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
# failures would cause users to give up on this script.
#
# And that is why the HTTP wheel is re-invented, here.
#

# NAMING CONVENTIONS:
#
# TypeNames are CamelCase
# CONSTANT_VALUES are CAPITAL_SNAKE_CASE
# all_other_identifiers are snake_case
# _protected members begin with a single underscore '_' (subclasses can access)
# __private members begin with double underscore: '__'
#
# python more or less enforces the double-underscore as private
# by prepending the class name to those identifiers. That makes
# it difficult (but not impossible) for other classes to access
# those identifiers.

import hashlib, os, re, sys, threading, traceback
from urllib.parse import urlparse
from .HTTPClient import HTTPConnection, HTTPResponse, HTTPFormFieldSpec

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config    # verbosity, global debug_level

CRLF = '\r\n'
CRLF2 = '\r\n\r\n'
HTTP_VERSION_STR = 'HTTP/1.1'
INSTALLER_REALM = 'rokudev'
ISO_8859_1 = 'iso-8859-1'
TIMEOUT_SECONDS = 30
USER_NAME = 'rokudev'
UTF8 = 'utf-8'
PORT = 80


class AppInstallerClient(object):
    def __init__(self, ip_addr, user_password):
        self._debug_level = 0
        self.__thread = None
        self.__ip_addr = ip_addr
        self.__user_password = user_password
        self.__installer_base_url = \
             'http://{}:{}/plugin_install'.format(self.__ip_addr, PORT)

    def get_target_ip_addr(self):
        return self.__ip_addr

    def remove(self):
        print('info: Removing dev channel, if installed...')
        (boundary, body_data) = HTTPConnection.buildMultipartFormData([
            HTTPFormFieldSpec('mysubmit', 'Delete'),
            HTTPFormFieldSpec('archive', ''),
        ])
        headers = self.__get_headers_for_post(boundary, body_data)
        self.do_post(headers, body_data)

    # @return void
    def install(self, channel_file_path, remote_debug, asynchronous=True):
        if asynchronous:
            if self.__check_debug(2):
                print('debug: appinst: install(): path={},remote={},async={}'.format(
                    channel_file_path, remote_debug, asynchronous))
            self.__thread = threading.Thread(
                target=lambda: self.install_impl(channel_file_path, remote_debug))
            self.__thread.start()
        else:
            self.install_impl(channel_file_path, remote_debug)

    # @return void
    def install_impl(self, channel_file_path, remote_debug):
        channel_file_name = os.path.basename(channel_file_path)
        print('info: Installing dev channel ({})...'.format(channel_file_name))
        f = open(channel_file_path, 'rb')
        channel_contents = f.read()
        f.close()

        fields = [
            HTTPFormFieldSpec('mysubmit', 'Install'),
            HTTPFormFieldSpec('archive', channel_contents,
                attributes=['filename',
                            os.path.basename(channel_file_path)],
                contentType='application/octet-stream'),
        ]
        if remote_debug:
            fields.append(HTTPFormFieldSpec('remotedebug', '1'))
            fields.append(HTTPFormFieldSpec('remotedebug_connect_early', '1'))
        (boundary, body_data) = HTTPConnection.buildMultipartFormData(fields)
        headers = self.__get_headers_for_post(boundary, body_data)
        return self.do_post(headers, body_data)

    # @return void
    def do_post(self, headers, body_data):
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

        url = self.__installer_base_url
        conn = None

        try:
            url_parts = urlparse(url)
            netLoc = url_parts[1]  # netLoc = 'host[:port]'
            path = ''
            for i in range(2, len(url_parts)):
                path += url_parts[i]

            # Send the request

            conn = HTTPConnection(netLoc)
            conn.set_debug_level(self._debug_level)
            conn.connect()
            conn.putRequest('POST', path)  # takes path, not URL (despite docs)
            conn.putHeaders(headers)
            conn.endHeaders()

            # Wait for a response
            # We've specified 'Exit: 100-continue', so wait for 100-continue
            # from server before sending message body
            response = conn.getResponse()

            if self.__check_debug(3):
                print('debug: appinst: POST response, uri={}: {} {}, headers={}'.format(
                    url, response.mStatus, response.mReason, response.getHeaders()))
            if response.mStatus == 401:
                # The 401 error contains one-time-use crypto info for the auth
                # digest response
                # Note: might want to create function for 401.
                #       e.g. response = self.handleHttp401(...)
                if self.__check_debug(2):
                    print('debug: 401 from app installer, retrying')
                responseHeaders = response.getHeaders()
                contentLenStr = response.getHeader('Content-Length')

                if (not contentLenStr) or (contentLenStr != '0'):
                    raise ValueError(
                        'Bad Content-Length in 401 auth response from server:{}'.format(
                            contentLenStr))

                self.__add_digest_auth_headers(
                    headers, responseHeaders, path, self.__user_password)
                if self.__check_debug(2):
                    print("debug: appinst: sending follow-up to 401 error, headers={}".format(
                        headers))
                conn = HTTPConnection(netLoc)
                conn.set_debug_level(self.__check_debug(1))
                conn.connect()
                conn.putRequest('POST', path)
                conn.putHeaders(headers)
                conn.endHeaders()
                response = conn.getResponse()
                if response.mStatus == 100:
                    if self.__check_debug(3):
                        print('debug: appinst: recv: {}'.format(response))
                else:
                    global_config.do_exit(1,
                        'Bad response from app installer: {} {}'.format(
                        response.mStatus, response.mReason))
                conn.send(body_data)
                response = conn.getResponse()
                print('info: final response from device: {} {}'.format(
                        response.mStatus, response.mReason))
                conn.close()
            else:
                global_config.do_exit(1,
                    'Bad response from app installer: {} {}'.format(
                    response.mStatus, response.mReason))

            if self.__check_debug(3):
                print('debug: appinst: POST response, uri={}: {} {}, headers={}'.format(
                    url, response.mStatus, response.mReason, response.getHeaders()))

        except Exception as e:
            if self.__check_debug(1):
                sys.stdout.flush()
                traceback.print_exc(file=sys.stderr)
            try:
                if conn:
                    conn.close()
                    conn = None
            except Exception:
                if self.__check_debug(1):
                    traceback.print_exc(file=sys.stderr)

            global_config.do_exit(1, 'Failed: {}: {}'.format(e, url))

        if self.__check_debug(2):
            print('debug: appinst: http response: {}'.format(response))

    # @return dict of header name:value
    def __get_headers_for_get(self):
        headers = self.__get_common_headers(None)
        return headers

    # @return list of header (name,value) tuples
    def __get_headers_for_post(self, boundary, body_data):
        headers = self.__get_common_headers(body_data)
        headers.append(('Content-Type', 'multipart/form-data; boundary={}'.\
            format(boundary)))
        headers.append(('Expect', '100-continue'))
        return headers

    # @return list of header (name,value) tuples
    def __get_common_headers(self, body_data):
        body_len = 0
        if body_data:
            body_len = len(body_data)
        return [
            ('Accept', '*/*'),
            ('Content-Length', body_len),
            ('User-Agent', 'rokudebug/{}'.format(
                                global_config.get_version_str()))
        ]

    def __calc_md5_for_header(self, value):
        value_bytes = value
        if isinstance(value, str):
            value_bytes = value.encode(UTF8)
        return hashlib.md5(value_bytes).hexdigest()


    # See doPost() for comments regarding the deficiencies in the
    # python3 http client packages, that require this to be done here.
    def __add_digest_auth_headers(self, headers, http_401_response_headers,
            path, passwd):
        auth_val = None
        conent_length_str = None
        for (name, value) in http_401_response_headers:
            if name == 'WWW-Authenticate':
                auth_val = value
            if name == 'Content-Length':
                conent_length_str = value

        content_length = None
        try:
            content_length = int(conent_length_str)
        except Exception as e:
            sys.stdout.flush()
            traceback.print_exc(file=sys.stderr)
            print('Bad Content-Length: {}'.format(e))

        assert auth_val
        assert content_length != None    # 0 is valid

        # server nonce is a one-time use key used for secure hashing
        # REMIND: handle None return from search() (perhaps try/except)
        server_nonce = re.search('nonce="(.*?)"', auth_val).group(1)
        realm = re.search('realm="(.*?)"', auth_val).group(1)
        qop = re.search('qop="(.*?)"', auth_val).group(1)

        # client nonce is generated by the client (this program) and is used
        # by the server to securely hash responses
        client_nonce_int = 0
        for b in os.urandom(4):
            client_nonce_int <<= 8
            client_nonce_int |= b
        client_nonce = '{:08x}'.format(client_nonce_int)

        # client nonce count must increase with each request, for cryptographic
        # security. However, we are sending only one request with this nonce.
        client_nonce_count = "00000001"

        ha1 = self.__calc_md5_for_header('{}:{}:{}'.format(
            USER_NAME, realm, passwd))
        ha2 = self.__calc_md5_for_header('POST:{}'.format(path))
        response = self.__calc_md5_for_header('{}:{}:{}:{}:{}:{}'.format(
            ha1, server_nonce, client_nonce_count, client_nonce, qop, ha2))
        headers.append(('Authorization',
            'Digest'
            ' username="{}", realm="{}", nonce="{}", uri="{}"'
            ', algorithm={}, response="{}", qop={}'
            ', nc={}, cnonce="{}"'.format(
                USER_NAME, realm, server_nonce, path, 'MD5',
                response, qop, client_nonce_count, client_nonce)
        ))

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, self._debug_level)
        if lvl: assert global_config.debug_level >= 0 and self._debug_level >= 0 and min_level >= 1
        return lvl >= min_level

#END class AppInstallerClient
