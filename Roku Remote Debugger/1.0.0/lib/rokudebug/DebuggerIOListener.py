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
# File: DebuggerIOListener.py
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

import socket, sys, threading, traceback

# Uses a separate thread to listen to the debugger's I/O port,
# to retrieve output from the running script.
class DebuggerIOListener(object):

    # @param updateHandler.updateReceived(update)
    def __init__(self, host, port, fileOut):
        global gMain
        gMain = sys.modules['__main__'].gMain
        self.mDebug = max(gMain.gDebugLevel, 0)
        self.mThread = IOListenerThread(host, port, fileOut)
        self.mThread.start()

class IOListenerThread(threading.Thread):

    def __init__(self, host, port, fileOut):
        super(IOListenerThread, self).__init__(daemon=True)
        self.name = 'DebuggerIOListener'
        self.mDebug = max(gMain.gDebugLevel, 0)
        self.mHost = host
        self.mPort = port
        self.mOut = fileOut

    def run(self):
        if self.mDebug >= 1:
            print('debug: I/O listener: thread running, host={},port={}'.format(
                self.mHost, self.mPort))
        try:
            self.mSocket = socket.create_connection((self.mHost, self.mPort))
            if self.mDebug >= 1:
                print('debug: connected to IO {}:{}'.format(
                    self.mHost,self.mPort))
            done = False
            while not done:
                buf = self.mSocket.recv(1)
                if buf and len(buf):
                    b = buf[0]
                    c = chr(b)
                    print(c, file=self.mOut, end='')
                else:
                    # EOF
                    done = True
                    if self.mDebug >= 1:
                        print('debug: iolisten: EOF on target I/O stream')

        except:
            sys.stdout.flush()
            traceback.print_exc(file=sys.stderr)
            print('ERROR: Failed with exception', file=sys.stderr)
            do_exit(1, 'Uncaught exception in I/O listener thread')

        if self.mDebug >= 1:
            print('debug: iolisten: thread exiting cleanly')

import sys
def do_exit(exitCode, msg=None):
    sys.modules['__main__'].do_exit(exitCode, msg)
