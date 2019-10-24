## Roku Remote Debugger

The Roku Remote Debugger is a Python-based sample commad-line remote debugger for testing and debugging Roku channels under development. The Roku Remote Debugger (**rokudebug.py**) provides the same functionality as the BrightScript debug console; however, it demonstrates how the BrightScript debug protocol can be used to integrate a remote debug tool into an IDE.

To run the Roku Remote Debugger, follow these steps: 

1. Download and then extract the debugger.

2. Verify that you have Python 3.5.2 (or greater) installed on your machine.

3. [Create a ZIP file](docs/developer-program/getting-started/hello-world.md#compressing-the-contents-of-the-hello-world-directory) containing the development channel to be tested. You can also [download sample channels](https://github.com/rokudev/samples) to test with the debugger.

4. Sideload a channel by entering the following command in a terminal or command prompt:

   `python rokudebug.py --targetip <Roku device IP address> --targetpass <Roku device webserver password> <development channel zip file>` 

   The following example demonstrates a command for running the debugger

   `python3 rokudebug.py --targetip 192.168.1.10 --targetpass abcd VideoListExample/Archive.zip`

5. Enter **help** to view a list of the available debug commands, which are as follows:

   | Command   | Abbreviation | Description                                         |
   | --------- | ------------ | --------------------------------------------------- |
   | backtrace | bt           | Print stack backtrace of selected thread.           |
   | continue  | c            | Continue all threads.                               |
   | down      | d            | Move one frame down the function call stack.        |
   | help      | h            | Print the available commands.                       |
   | list      | l            | List the currently running function.                |
   | print     |              | Print the value of a variable.                      |
   | quit      | q            | Quit the Roku Remote Debugger and exit the channel. |
   | status    |              | Show the status of the Roku Remote Debugger.        |
   | stop      |              | Stop all threads.                                   |
   | thread    | th           | Inspect a thread.                                   |
   | threads   | ths          | Show all threads.                                   |
   | up        | u            | Move one frame up the function call stack.          |
   | vars      | v            | Show the variables in the current scope.            |
***
 Copyright 2019 Roku, Inc.

 Permission is hereby granted, free of charge, to any person obtaining
 a copy of this software and associated documentation files (the
 "Software"), to deal in the Software without restriction, including
 without limitation the rights to use, copy, modify, merge, publish,
 distribute, sublicense, and/or sell copies of the Software, and to
 permit persons to whom the Software is furnished to do so, subject
 to the following conditions:

 The above copyright notice and this permission notice shall be included
 in all copies or substantial portions of the Software.

 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
 OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
 MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
 IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
 CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
 TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
 SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 ***
