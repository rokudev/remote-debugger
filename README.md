## Roku Remote Debugger

The Roku Remote Debugger is a Python-based reference implementation of a command-line remote debugger for testing and debugging Roku channels under development. The Roku Remote Debugger (**rokudebug.py**) provides the same functionality as the [BrightScript debug console](https://developer.roku.com/docs/developer-program/debugging/debugging-channels.md#brightscript-console-port-8085-commands); however, it demonstrates how the BrightScript debug protocol could be used to integrate a remote debugger into an IDE.

To run the Roku Remote Debugger, follow these steps: 

1. Download or clone this project

2. Verify that you have Python 3.5.3 (or greater) installed on your machine.

3. [Create a ZIP file](https://developer.roku.com/docs/developer-program/getting-started/hello-world.md#compressing-the-contents-of-the-hello-world-directory) containing the development channel to be tested. You can also [download sample channels](https://github.com/rokudev/samples) to test with the debugger.

4. Sideload a channel by entering the following command in a terminal or command prompt:

   `python rokudebug.py --targetip <Roku device IP address> --targetpass <Roku device webserver password> <development channel zip file>` 

   The following example demonstrates a command for running the debugger

   `python3 rokudebug.py --targetip 192.168.1.10 --targetpass abcd VideoListExample/Archive.zip`

5. Enter **help** to view a list of the available debug commands, which are as follows:

      | Command                | Abbreviation | Description                                         |
   | ---------------------- | ------------ | --------------------------------------------------- |
   | addbreak               | break, ab    | Adds a breakpoint                                   |
   | backtrace              | bt           | Print stack backtrace of selected thread.           |
   | continue               | c            | Continue all threads.                               |
   | down                   | d            | Move one frame down the function call stack.        |
   | help                   | h            | Print the available commands.                       |
   | list                   | l            | List the currently running function.                |
   | listbreak              | Lb           | List all breakpoints                                |
   | out                    | o            | Step out of the current function                    |
   | over                   | v            | Step over one program statement                     |
   | print *var*            |              | Print the value of a specific variable.             |
   | rmbreak *breakpointid* | rb           | Clears the specified breakpoint                     |
   | quit                   | q            | Quit the Roku Remote Debugger and exit the channel. |
   | status                 |              | Show the status of the Roku Remote Debugger.        |
   | step                   | s, t         | Step one program statement                          |
   | stop                   |              | Stop all threads.                                   |
   | thread                 | th           | Inspect a thread.                                   |
   | threads                | ths          | Show all threads.                                   |
   | up                     | u            | Move one frame up the function call stack.          |
   | vars                   | v            | Show the variables in the current scope.            |

## [BETA] Visual Studio Code extension

You can [download](https://github.com/rokudev/debug-protocol-vscode-ext-beta) the beta version of the Visual Studio Code extension for the Roku BrightScript debug protocol. After extracting and installing the extension, you can use it for debugging Roku channels in Visual Studio.

## Change log

- **04-28-2023**: Roku Remote debugger 3.2.0 release. Supports protocol 3.2.0 features (breakpoint verified updates, protocol error updates)
- **09-14-2022**: Roku Remote debugger 3.1.0 release. Supports protocol 3.1.0 features (conditional breakpoints, packet length, improved error responses)
- **08-14-2020**: Beta release of [Visual Studio Code extension](https://github.com/rokudev/debug-protocol-vscode-ext-beta). Updated debug command table.
- **03-29-2020**: Roku Remote debugger 2.0.0 release. Added breakpoint and step commands.  
- **11-09-2019**: Roku Remote debugger 1.0.1 release. 

***
 Copyright 2019-2022 Roku, Inc.

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
