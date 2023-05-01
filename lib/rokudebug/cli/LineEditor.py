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
# File: LineEditor.py
# Requires python 3.5.3 or later
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

_module_debug_level = 0

import atexit, re, sys, threading, traceback

# SystemExit only exits the current thread, so call it by its real name
ThreadExit = SystemExit

global_config = getattr(sys.modules['__main__'], 'global_config', None)
assert global_config

# Import modules for line editing, tab completion, and history. On some
# platforms, the readline and rlcompleter modules must be imported to have
# features; on some platforms, it is built in; and we assume that some
# platforms don't support any of it.
_platform_has_readline = False
_platform_has_tab_completion = False
try:
    import termios
    import tty
    import readline
    import rlcompleter
    _platform_has_readline = True
except Exception:
    pass
try:
    readline.parse_and_bind('tab: complete') # default, but verify packages
    _platform_has_tab_completion = True
except Exception:
    pass
if _module_debug_level > 2:
        print('debug: lined: platform has readline={},tabcompletion={}'.format(
                _platform_has_readline, _platform_has_tab_completion))


# Provides line editing, as provided by the packages installed on the
# current platform.
# For usage, see the test_driver() function, near the end of this module
class LineEditor(object):

    # controller must have this callable attribute:
    #    - iterable:str get_completions(text, beginidx, endidx)
    # The get_completions callback must return an array of str, to
    # replace the token being completed at beginidx:endidx, which
    # may be empty if there are no completions.
    def __init__(self, controller):
        self.__self_debug_level = 0
        if self.__check_debug(5):
            print('debug: lined: init()')

        global _platform_has_tab_completion
        self.has_tab_completion = _platform_has_tab_completion
        self.__controller = controller
        self.__line = None
        self.__state = None

        # Gnu readline leaves the tty in a bad state, if this script is
        # killed (even with sys.exit()) while blocked on input. Save and
        # forcibly restore the previous state on platforms that support it
        self.__saved_tty_attrs = None
        try:
            self.__saved_tty_attrs = termios.tcgetattr(sys.stdin)
        except Exception: pass

        atexit.register(self._at_exit)

    # @return LineEditorState
    def input(self, prompt=None):
        global _platform_has_readline
        if not prompt:
            prompt = ''
        line = ''
        saved_delims = None
        saved_completed_func = None
        if _platform_has_readline:
            saved_delims = readline.get_completer_delims()
            saved_completer_func = readline.get_completer()
            readline.set_completer(self.__completer)
            delims = ''
            for c in saved_delims:
                if c != '/' and c != ':':  # Don't split file paths or URIs ("pkg:/a/b.brs")
                    delims = delims + c
            readline.set_completer_delims(delims)
        try:
            done = False
            while not done:
                try:
                    line = input(prompt)
                    done = True
                except EOFError:
                    if _platform_has_readline:
                        line = readline.get_line_buffer()
                        if self.__check_debug(2):
                            print('debug: aborted text: "{}"'.format(line))
            if self.__check_debug(5):
                print('debug: lined: edit() done: "{}"'.format(line))
        finally:
            if _platform_has_readline:
                readline.set_completer(saved_completer_func)
                readline.set_completer_delims(delims)
        return line

    # Returns a list of tokens found in text, and the index of the
    # token containing beginidx:endidx. If the cursor is outside of
    # a token, selected_index will be negative: -1 indicates completion
    # before the first token, otherwise completion is after the token
    # at index ((-selected_index)-2). E.g.: where ^ is the cursor,
    # selected_index will have the following values:
    # "^ a b c": selected_index == -1
    # " a ^ b c": selected:index == -2
    # " a b c ^": selcted_index == -4
    # @return (token_dicts, selected_idx); list may be empty never None
    @staticmethod
    def parse_tokens(text, beginidx, endidx):
        tokens_and_delims = re.split('(\\s+)', text)
        selected_idx = -1
        token_dicts = list()
        token_idx = -1
        str_idx = 0
        for iter_idx in range(len(tokens_and_delims)):
            token_or_delim = tokens_and_delims[iter_idx]
            next_str_idx = str_idx + len(token_or_delim)
            if not re.fullmatch('\\s*', token_or_delim):
                token_idx = token_idx + 1
                token = token_or_delim
                token_dict = dict()
                token_dict['text'] = token
                token_dict['token_idx'] = token_idx
                if (endidx < str_idx) or (endidx > next_str_idx):
                    token_dict['cursor_idx'] = None
                    token_dict['is_selected'] = False
                    if endidx > next_str_idx:
                        selected_idx = -(token_idx + 2)
                else:
                    selected_idx = token_idx
                    token_dict['cursor_idx'] = endidx - str_idx
                    token_dict['is_selected'] = True
                token_dicts.append(token_dict)
            str_idx = next_str_idx

        if _module_debug_level >= 5:
            print('test: debug: parse_tokens(): token_dicts={},selidx={}'.\
                format(token_dicts, selected_idx))
        return token_dicts, selected_idx

    # NB: 'state' is an index into a list of completions. That name is
    # confusing, so it is named 'completion_index' here.
    #
    # Called repeatedly by readline module, with completion_index=0,1,2,...
    # Caling with completion_index==0 generates a list of possible completions,
    # discarding any previous list. Each subsequent call returns the next
    # completion and None when the list is exhausted.
    def __completer(self, text, state):
        completion_index = state        # 'state' is an oddly-named parameter
        state = None                    # reduce naming ambiguity
        if self.__check_debug(5):
            print('debug: lined: compidx={},text={}'.format(
                completion_index, text))
        try:
            if not completion_index:
                # First call with new parameters. Get the entire list of
                # possible completions, and return them one by one, below
                self.__state = None
                line = readline.get_line_buffer()
                idx0 = readline.get_begidx()
                idx1 = readline.get_endidx()
                self.__state = _LineEditorState(line, idx0, idx1,
                                    self.__controller.get_completions(
                                        line, idx0, idx1))
                if self.__check_debug(5):
                    self.__state.dump()
        except Exception:
            if self.__check_debug(2):
                print('debug: lined: exeption occurred:')
                traceback.print_exc()
            raise

        completion = None
        if self.__state and (completion_index < len(self.__state.completions)):
            completion = self.__state.completions[completion_index]
        if not completion:
            # done
            self.__state = None

        if self.__check_debug(5):
            print('debug: lined: completer({}) => {}'.format(
                completion_index, completion))
        return completion

    def __check_debug(self, min_level):
        lvl = max(global_config.debug_level, _module_debug_level,
                  self.__self_debug_level)
        return lvl >= min_level

    def _restore_tty(self):
        if self.__saved_tty_attrs:
            termios.tcsetattr(sys.stdin, tty.TCSANOW, self.__saved_tty_attrs)

    def _at_exit(self):
        self._restore_tty()


class _LineEditorState(object):

    # completions is an array of str, may be empty or None
    def __init__(self, text, beginidx, endidx, completions):
        self.text = text
        self.beginidx = beginidx
        self.endidx = endidx
        self.completions = completions
        if not self.completions:
            self.completions = []

    def dump(self, file=None):
        fout = file
        if not fout:
            fout = sys.stdout
        print('LineEditorState:', file=fout)
        print('    text={}'.format(self.text), file=fout)
        print('    indexes={},{}'.format(self.beginidx, self.endidx),
            file=fout)
        print('    seltext={}'.format(self.text[self.beginidx:self.endidx]),
            file=fout)
        print('    completions=[{}]'.format(','.join(self.completions)),
            file=fout)


########################################################################
# TEST DRIVER
########################################################################

if bool(__name__ == '__main__'):
    import re, time

    _done = False
    _condition = threading.Condition()

    # Set into global global_config
    class TestConfig(object):
        def __init__(self):
            self.debug_level = 1        # Test config: turn on validation

    class TestEditController(object):
        colors = ['red', 'blue', 'blueish', 'bluegreen']
        items = ['apple', 'applepie', 'applecrisp', 'blueberry']

        def __init__(self):
            super(TestEditController,self).__init__()
            pass

        def get_completions(self, text, beginidx, endidx):
            completions = None
            tokens, selidx = LineEditor.parse_tokens(text, beginidx, endidx)
            if (selidx == -1) or (not (tokens and len(tokens))):
                completions = TestEditController.colors
            elif selidx < 0:
                completions = ['<after:{}>'.format((-selidx)-2)]
            elif selidx == 0:
                completions = self.__find_extrapolations(
                        tokens[selidx]['text'], TestEditController.colors)
            elif selidx == 1:
                completions = self.__find_extrapolations(
                        tokens[selidx]['text'], TestEditController.items)
            return completions

        # Find elements in possibilities that start with stem, and are
        # longer than stem. Will not return an exact match.
        # @return list of extrapolations or None
        def __find_extrapolations(self, stem, possibilities):
            extraps = []
            for poss in possibilities:
                if poss.startswith(stem) and (stem != poss):
                    extraps.append(poss)
            if not len(extraps):
                extraps = None
            return extraps

    def test_debug_level():
        global global_config
        return global_config.debug_level

    def test_driver():
        print("test: Test driver running... Type some stuff ('q' to quit)...")
        global _done
        global global_config
        global_config = TestConfig()
        if test_debug_level():
            print('debug: debug level: {}'.format(test_debug_level()))
        controller = TestEditController()
        lined = LineEditor(controller)
        while True:
            line = lined.input('testdriver> ')
            print('test: cmd: "{}"'.format(line))
            if line == 'q':
                print('test: notify main to exit (takes 1 second)')
                with _condition:
                    _done = True
                    _condition.notify()
        assert False, 'main thread should have exited'

    # Process input on another thread, to simulate real-world use
    # GNU readline leaves the tty in a bad state, if the script terminates
    # while blocked reading input (even with sys.exit()). This tests
    # the workaround in LineEditor.at_exit().
    class InputThread(threading.Thread):
        def __init__(self):
            super(InputThread,self).__init__(
                target=self, daemon=True, name='Input-Thread')

        def __call__(self):
            try:
                test_driver()
            except ThreadExit: raise
            except: # Yes, catch EVERYTHING
                print('INTERNAL ERROR: uncaught exception', file=sys.stderr)
                sys.exit(1)

    input_thread = InputThread()
    input_thread.start()

    while not _done:
        with _condition:
            _condition.wait()
    time.sleep(1)        # wait for re-entrance into input()
    sys.exit(0)

assert global_config
