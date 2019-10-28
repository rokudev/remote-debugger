## Roku Remote Debugger

The Roku Remote Debugger is a Python-based sample commad-line remote debugger for testing and debugging Roku channels under development. The Roku Remote Debugger (**rokudebug.py**) provides the same functionality as the [BrightScript debug console](https://developer.roku.com/docs/developer-program/debugging/debugging-channels.md#brightscript-console-port-8085-commands); however, it demonstrates how the BrightScript debug protocol could be used to integrate a remote debugger into an IDE.

To run the Roku Remote Debugger, follow these steps: 

1. Download and then extract the debugger.

2. Verify that you have Python 3.5.3 (or greater) installed on your machine.

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

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
 ***
