# The Craftr build system
# Copyright (C) 2016  Niklas Rosenstein
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from craftr import core
from craftr.core.config import read_config_file, InvalidConfigError
from craftr.core.logging import logger
from craftr.core.session import session, Session, Module, MANIFEST_FILENAME
from craftr.utils import path, shell
from nr.types.version import Version, VersionCriteria

import abc
import argparse
import atexit
import configparser
import craftr.defaults
import craftr.targetbuilder
import functools
import json
import os
import sys
import textwrap

CONFIG_FILENAME = '.craftrconfig'


def parse_cmdline_options(options):
  for item in options:
    key, sep, value = item.partition('=')
    if not sep:
      value = 'true'
    if not value:
      session.options.pop(key, None)
    else:
      session.options[key] = value


def read_cache(cachefile):
  try:
    with open(cachefile) as fp:
      try:
        session.read_cache(fp)
      except ValueError as exc:
        return False
  except IOError as exc:
    return False
  return True


def write_cache(cachefile):
  # Write back the cache.
  try:
    path.makedirs(path.dirname(cachefile))
    with open(cachefile, 'w') as fp:
      session.write_cache(fp)
  except OSError as exc:
    logger.error('error writing cache file:', cachefile)
    logger.error(exc, indent=1)
  else:
    logger.debug('cache written:', cachefile)


def serialise_loaded_module_info():
  """
  Converts all modules that have been loaded into the active session a JSON
  serialisable object that can be saved into the Craftr cache. This cache
  object contains the same nested structures as :attr:`Session.modules`.

  This cache is loaded when the exported project is being built to register
  when files have changed. Note that the modification time that is saved
  is the sum of all dependent files.

  Example:

  .. code:: json

    "modules": {
      "craftr.lang.cxx.msvc": {
        "1.0.0": {
          "deps": [
            "c:\\users\\niklas\\repos\\craftr\\craftr\\stl\\craftr.lang.cxx.msvc\\manifest.json",
            "c:\\users\\niklas\\repos\\craftr\\craftr\\stl\\craftr.lang.cxx.msvc\\craftrfile"
          ],
          "mtime": 2962706318
        }
      },
      "craftr.lib.sdl2": {
        "1.0.0": {
          "deps": [
            "c:\\users\\niklas\\repos\\craftr\\craftr\\stl_auxiliary\\craftr.lib.sdl2\\manifest.json",
            "c:\\users\\niklas\\repos\\craftr\\craftr\\stl_auxiliary\\craftr.lib.sdl2\\craftrfile"
          ],
          "mtime": 2963870114
        }
      },
      "craftr.lang.cxx": {
        "1.0.0": {
          "deps": [
            "c:\\users\\niklas\\repos\\craftr\\craftr\\stl\\craftr.lang.cxx\\manifest.json",
            "c:\\users\\niklas\\repos\\craftr\\craftr\\stl\\craftr.lang.cxx\\craftrfile"
          ],
          "mtime": 2963324399
        }
      },
      "examples.c-sdl2": {
        "1.0.0": {
          "deps": [
            "c:\\users\\niklas\\repos\\craftr\\examples\\examples.c-sdl2\\manifest.json",
            "c:\\users\\niklas\\repos\\craftr\\examples\\examples.c-sdl2\\craftrfile"
          ],
          "mtime": 2963870114
        }
      }
    }
  """

  modules = {}
  for name, versions in session.modules.items():
    module_versions = {}
    for version, module in versions.items():
      if not module.executed: continue
      module_versions[str(version)] = {
        "deps": module.dependent_files,
        "mtime": sum(map(path.getimtime, module.dependent_files))
      }
    if module_versions:
      modules[name] = module_versions
  return modules


def unserialise_loaded_module_info(modules):
  """
  This function takes the data generated with :func:`serialise_loaded_module_info`
  and converts it back to a format that is easier to use later in the build process.
  Currently, this function only converts the version-number fields to actual
  :class:`Version` objects and adds a ``"changed"`` key to a module based on
  the ``"mtime"`` and the current modification times of the files.
  """

  result = {}
  for name, versions in modules.items():
    result[name] = {}
    for version, module in versions.items():
      version = Version(version)
      result[name][version] = module
      mtime = 0
      for fn in module['deps']:
        try:
          mtime += path.getimtime(fn)
        except OSError:
          mtime = 0
          break
      module['changed'] = (mtime != module['mtime'])
  return result


def parse_module_spec(spec):
  """
  Parses a module spec as it can be specified on the command-line. The
  format is ``module_name[:version]``.
  """

  parts = spec.split(':')
  if len(parts) not in (1, 2):
    raise ValueError('invalid module spec: {!r}'.format(spec))
  if len(parts) == 1:
    parts.append('*')
  try:
    version = Version(parts[1])
  except ValueError as exc:
    version = VersionCriteria(parts[1])
  return parts[0], version


def get_volatile_module_version(name):
  """
  Given a module name of which the exact version number may be appended
  and separated by a hyphen, returns the raw module name and its version
  as a tuple. If the module is not suffixed with a version number, the
  returned version number is :const:`None` instead.
  """

  parts = name.rpartition('-')[::2]
  if len(parts) != 2:
    return name
  try:
    version = Version(parts[1])
  except ValueError:
    version = None
  else:
    name = parts[0]
  return name, version


@functools.lru_cache()
def get_ninja_version(ninja_bin):
  ''' Read the ninja version from the `ninja` program and return it. '''

  return shell.pipe([ninja_bin, '--version'], shell=True).output.strip()


def get_ninja_info():
  # Make sure the Ninja executable exists and find its version.
  ninja_bin = session.options.get('global.ninja') or \
      session.options.get('craftr.ninja') or os.getenv('NINJA', 'ninja')
  ninja_bin = shell.find_program(ninja_bin)
  ninja_version = get_ninja_version(ninja_bin)
  logger.debug('Ninja executable:', ninja_bin)
  logger.debug('Ninja version:', ninja_version)
  return ninja_bin, ninja_version


def finally_(finally_func):
  """
  Decorator that calls *finally_func* after the decorated function.
  """

  def decorator(func):
    def wrapper(*args, **kwargs):
      try:
        return func(*args, **kwargs)
      finally:
        finally_func(*args, **kwargs)
    return wrapper
  return decorator


class BaseCommand(object, metaclass=abc.ABCMeta):
  """
  Base class for Craftr subcommands.
  """

  @abc.abstractmethod
  def build_parser(self, parser):
    pass

  @abc.abstractmethod
  def execute(self, parser, args):
    pass



class BuildCommand(BaseCommand):

  def __init__(self, mode):
    assert mode in ('clean', 'build', 'export', 'run')
    self.mode = mode

  def build_parser(self, parser):
    # Inherit options from main parser so they can also be specified
    # after the sub-command.
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('-d', '--option', dest='options', action='append', default=[])

    if self.mode in ('export', 'run'):
      parser.add_argument('-m', '--module')
    else:
      parser.add_argument('targets', metavar='TARGET', nargs='*')
    if self.mode == 'clean':
      parser.add_argument('-r', '--recursive', action='store_true')
    parser.add_argument('-b', '--build-dir', default='build')
    parser.add_argument('-i', '--include-path', action='append', default=[])

  def __cleanup(self, parser, args):
    """
    Switch back to the original directory and check if we can clean up
    the build directory.
    """

    os.chdir(session.maindir)
    if not os.listdir(session.builddir):
      logger.debug('note: cleanup empty build directory:', session.builddir)
      os.rmdir(session.builddir)

  @finally_(__cleanup)
  def execute(self, parser, args):
    session.path.extend(map(path.norm, args.include_path))

    if self.mode in ('export', 'run'):
      # Determine the module to execute, either from the current working
      # directory or find it by name if one is specified.
      if not args.module:
        for fn in [MANIFEST_FILENAME, path.join('craftr', MANIFEST_FILENAME)]:
          if path.isfile(fn):
            module = session.parse_manifest(fn)
            break
        else:
          parser.error('"{}" does not exist'.format(MANIFEST_FILENAME))
      else:
        # TODO: For some reason, prints to stdout are not visible here.
        # TODO: Prints to stderr however work fine.
        try:
          module_name, version = parse_module_spec(args.module)
        except ValueError as exc:
          parser.error('{} (note: you have to escape > and < characters)'.format(exc))
        try:
          module = session.find_module(module_name, version)
        except Module.NotFound as exc:
          parser.error('module not found: ' + str(exc))
    else:
      module = None

    ninja_bin, ninja_version = get_ninja_info()

    # Create and switch to the build directory.
    session.builddir = path.abs(args.build_dir)
    path.makedirs(session.builddir)
    os.chdir(session.builddir)

    # Read the cache and parse command-line options.
    cachefile = path.join(session.builddir, '.craftrcache')
    if not read_cache(cachefile) and self.mode != 'export':
      logger.error('Unable to find file: .craftrcache')
      logger.error('Does not seemt to be a build directory: {}'.format(session.builddir))
      logger.error("Export build information using the 'craftr export' command.")
      return 1

    # Prepare options, loaders and execute.
    if self.mode in ('export', 'run'):
      session.expand_relative_options(module.manifest.name)
      session.cache['build'] = {}
      try:
        module.run()
      except Module.InvalidOption as exc:
        for error in exc.format_errors():
          logger.error(error)
        return 1
      except craftr.defaults.ModuleError as exc:
        logger.error('error:', exc)
        return 1
      finally:
        if sys.exc_info() and self.mode == 'export':
          # We still want to write the cache, especially so that data already
          # loaded with loaders doesn't need to be re-loaded. They'll find out
          # when the cached information was not valid.
          write_cache(cachefile)

      # Write the cache back.
      session.cache['build']['targets'] = list(session.graph.targets.keys())
      session.cache['build']['modules'] = serialise_loaded_module_info()
      session.cache['build']['main'] = module.ident
      session.cache['build']['options'] = args.options
      if self.mode == 'export':
        write_cache(cachefile)

        # Write the Ninja manifest.
        with open("build.ninja", 'w') as fp:
          platform = core.build.get_platform_helper()
          context = core.build.ExportContext(ninja_version)
          writer = core.build.NinjaWriter(fp)
          session.graph.export(writer, context, platform)
          logger.info('exported "build.ninja"')

    else:
      parse_cmdline_options(session.cache['build']['options'])
      main = session.cache['build']['main']
      available_targets = frozenset(session.cache['build']['targets'])
      available_modules = unserialise_loaded_module_info(session.cache['build']['modules'])

      logger.debug('build main module:', main)
      session.expand_relative_options(get_volatile_module_version(main)[0])

      # Check if any of the modules changed, so we can let the user know he
      # might have to re-export the build files.
      changed_modules = []
      for name, versions in available_modules.items():
        for version, info in versions.items():
          if info['changed']:
            changed_modules.append('{}-{}'.format(name, version))
      if changed_modules:
        if len(changed_modules) == 1:
          logger.info('note: module "{}" has changed, maybe you should re-export'.format(changed_modules[0]))
        else:
          logger.info('note: some modules have changed, maybe you should re-export')
          for name in changed_modules:
            logger.info('  -', name)

      # Check the targets and if they exist.
      targets = []
      for target_name in args.targets:
        if '.' not in target_name:
          target_name = main + '.' + target_name
        elif target_name.startswith('.'):
          target_name = main + target_name

        module_name, target_name = target_name.rpartition('.')[::2]
        module_name, version = get_volatile_module_version(module_name)

        if module_name not in available_modules:
          error('no such module:', module_name)
        if not version:
          version = max(available_modules[module_name].keys())

        target_name = craftr.targetbuilder.get_full_name(
            target_name, module_name=module_name, version=version)
        if target_name not in available_targets:
          parser.error('no such target: {}'.format(target_name))
        targets.append(target_name)

      # Make sure we get all the output before running the subcommand.
      logger.flush()

      # Execute the ninja build.
      cmd = [ninja_bin]
      if args.verbose:
        cmd += ['-v']
      if self.mode == 'clean':
        cmd += ['-t', 'clean']
        if not args.recursive:
          cmd += ['-r']
      cmd += targets
      return shell.run(cmd).returncode


class StartpackageCommand(BaseCommand):

  def build_parser(self, parser):
    parser.add_argument('name')
    parser.add_argument('directory', nargs='?', default=None)
    parser.add_argument('-n', '--nested', action='store_true')
    parser.add_argument('--version', type=Version, default='1.0.0')

  def execute(self, parser, args):
    directory = args.directory or args.name
    if path.maybedir(directory):
      directory = path.join(directory, args.name)

    if not path.exists(directory):
      logger.debug('creating directory "{}"'.format(directory))
      path.makedirs(directory)
    elif not path.isdir(directory):
      logger.error('"{}" is not a directory'.format(directory))
      return 1

    if args.nested:
      directory = path.join(directory, 'craftr')
      path.makedirs(directory)

    mfile = path.join(directory, MANIFEST_FILENAME)
    sfile = path.join(directory, 'Craftrfile')
    for fn in [mfile, sfile]:
      if path.isfile(fn):
        logger.error('"{}" already exists'.format(fn))
        return 1


    logger.debug('creating file "{}"'.format(mfile))
    with open(mfile, 'w') as fp:
      lines = textwrap.dedent('''
        {
          "name": "%s",
          "version": "%s",
          "project_dir": "..",
          "author": "",
          "url": "",
          "dependencies": {},
          "options": {}
        }\n''' % (args.name, args.version)).lstrip().split('\n')
      if not args.nested:
        del lines[3]
      fp.write('\n'.join(lines))

    logger.debug('creating file "{}"'.format(sfile))
    with open(sfile, 'w') as fp:
      print('# {}'.format(args.name), file=fp)


class VersionCommand(BaseCommand):

  def build_parser(self, parser):
    pass

  def execute(self, parser, args):
    print(craftr.__version__)


def main():
  # Create argument parsers and dynamically include all BaseCommand
  # subclasses into it.
  parser = argparse.ArgumentParser(prog='craftr', description='The Craftr build system')
  parser.add_argument('-v', '--verbose', action='store_true')
  parser.add_argument('-q', '--quiet', action='store_true')
  parser.add_argument('-c', '--config', action='append', default=[])
  parser.add_argument('-C', '--no-config', action='store_true')
  parser.add_argument('-d', '--option', dest='options', action='append', default=[])
  subparsers = parser.add_subparsers(dest='command')

  commands = {
    'clean': BuildCommand('clean'),
    'build': BuildCommand('build'),
    'export': BuildCommand('export'),
    'run': BuildCommand('run'),
    'startpackage': StartpackageCommand(),
    'version': VersionCommand()
  }

  for key, cmd in commands.items():
    cmd.build_parser(subparsers.add_parser(key))

  # Parse the arguments.
  args = parser.parse_args()
  if not args.command:
    parser.print_usage()
    return 0

  if args.verbose:
    logger.set_level(logger.DEBUG)
  elif args.quiet:
    logger.set_level(logger.WARNING)

  session = Session()

  # Parse the user configuration file.
  try:
    config_filename = path.expanduser('~/' + CONFIG_FILENAME)
    session.options = read_config_file(config_filename)
  except FileNotFoundError as exc:
    session.options = {}
  except InvalidConfigError as exc:
    parser.error(exc)
    return 1

  # Parse the local configuration file or the ones specified on command-line.
  if not args.no_config:
    try:
      for filename in args.config:
        session.options.update(read_config_file(filename))
      if not args.config:
        choices = [CONFIG_FILENAME, path.join('craftr', CONFIG_FILENAME)]
        for fn in choices:
          try:
            session.options.update(read_config_file(fn))
          except FileNotFoundError as exc:
            pass
    except InvalidConfigError as exc:
      parser.error(exc)
      return 1

  # Execute the command in the session context.
  with session:
    parse_cmdline_options(args.options)
    return commands[args.command].execute(parser, args)


def main_and_exit():
  sys.exit(main())


if __name__ == '__main__':
  main_and_exit()
