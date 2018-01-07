
import collections
import os
import re
import string

import craftr, {path} from 'craftr'

ConfigResult = collections.namedtuple('ConfigResult', 'output directory')


def configure_file(input, output=None, environ={}, inherit_environ=True):
  """
  Renders the CMake configuration file using the specified environment
  and additionally the current process environment (optional).

  If the #output parameter is omitted, an output filename in a
  special ``include/`` directory will be generated from the *input*
  filename. The ``.in`` suffix from #input will be removed if it
  exists.

  :param input: Absolute path to the CMake config file.
  :param output: Name of the output file. Will be automatically
    generated if omitted.
  :param environ: A dictionary containing the variables for
    rendering the CMake configuration file. Non-existing
    variables are considered undefined.
  :param inherit_environ: If True, the environment variables of the
    Craftr process are additionally taken into account.
  :return: A :class:`ConfigResult` object.
  """

  input = craftr.localpath(input)

  if not output:
    output = craftr.buildlocal('include', path.base(input))
    if output.endswith('.in'):
      output = output[:-3]
    elif output.endswith('.cmake'):
      output = output[:-6]

  if inherit_environ:
    new_env = os.environ.copy()
    new_env.update(environ)
    environ = new_env
    del new_env

  output_dir = path.dir(output)
  path.makedirs(output_dir)

  def replace_var(match):
    return environ.get(match.group(1), '')

  with open(input) as src:
    with open(output, 'w') as dst:
      for line_num, line in enumerate(src):
        match = re.match('\s*#cmakedefine(01)?\s+(\w+)\s*(.*)', line)
        if match:
          is01, var, value = match.groups()
          if is01 and value:
            raise ValueError("invalid configuration file: {!r}\n"
              "line {}: #cmakedefine01 does not expect a value part".format(input, line_num))
          if is01:
            if environ.get(var):
              line = '#define {} 1\n'.format(var)
            else:
              line = '#define {} 0\n'.format(var)
          else:
            if environ.get(var):
              line = '#define {} {}\n'.format(var, value)
            else:
              line = '/* #undef {} */\n'.format(var)

        line = re.sub('@([A-z_0-9]+)@', replace_var, line)

        # Replace variable references with $X or ${X}
        def replace(match):
          value = environ.get(match.group(3), None)
          if value:
            return str(value)
          return ''
        line = string.Template.pattern.sub(replace, line)

        dst.write(line)

  return ConfigResult(output, output_dir)