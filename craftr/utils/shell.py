# Copyright (C) 2015 Niklas Rosenstein
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

from craftr.utils.lists import autoexpand

import os
import re
import shlex
import subprocess
import sys


class Process(object):
  ''' A simple wrapper for `subprocess.Popen` class with easier access
  to the processes standard and error output. When an instance of this
  class is created, the process will immediately be started and waited
  for termination.

  Arguments:
    command: The argument list for the process.
    shell (bool): True if the command should be executed in the shell.
    merge (bool): True if the standard output should be merged with
      the error output, False if not.
  '''

  class ExitCodeError(Exception):
    ''' Raised if the process exits with a non-zero exit-code. '''

    def __init__(self, process):
      self.process = process

    def __str__(self):
      return "Process '{0}' exited with exit-code {1}".format(
        self.process.command[0], self.process.returncode)

  def __init__(self, command, input_=None, encoding=sys.getdefaultencoding(),
      pipe=True, merge=False, shell=False):

    super().__init__()

    command = autoexpand(command)
    if pipe:
      stdout = subprocess.PIPE
      if merge:
        stderr = subprocess.STDOUT
      else:
        stderr = subprocess.PIPE
    else:
      stdout = stderr = None

    if shell:
      command = [' '.join(map(quote, command))]

    self.popen = subprocess.Popen(command, shell=shell, stdout=stdout,
      stderr=stderr)
    self.stdout, self.stderr = self.popen.communicate(input_)

    if encoding is not None:
      if self.stdout is not None:
        self.stdout = self.stdout.decode(encoding)
      if self.stderr is not None:
        self.stderr = self.stderr.decode(encoding)

    if self.returncode != 0:
      raise Process.ExitCodeError(self)

  @property
  def returncode(self):
    return self.popen.returncode


def quote(s):
  ''' Enhanced implementation for Windows systems as the original
  `shlex.quote()` function uses single-quotes on Windows which can lead
  to problems. '''

  if os.name == 'nt' and os.sep == '\\':
    s = s.replace('"', '\\"')
    if re.search('\s', s):
      s = '"' + s + '"'
    return s
  else:
    return shlex.quote(s)


def call(command, session=None, shell=False):
  command = autoexpand(command)
  if session is not None:
    session.info('running {}'.format(' '.join(map(quote, command))))
  Process(command, shell=shell, pipe=False)
