# -*- coding: utf8 -*-
# The MIT License (MIT)
#
# Copyright (c) 2018  Niklas Rosenstein
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
This module implements the API for the Craftr build scripts.

Craftr build scripts are plain Python scripts that import the members of
this module to generate a build graph. The functions in this module are based
on a global thread local that binds the current build graph master, target,
etc. so they do not have to be explicitly declared and passed around.
"""

__all__ = [
  'Session',
  'Scope',
  'Target',
  'Operator',
  'BuildSet',
  'session',
  'current_session',
  'current_scope',
  'current_target',
  'current_build_set',
  'bind_target',
  'bind_build_set'
]

import collections
import contextlib
import nodepy
import nr.fs

from craftr.core import build as _build
from nodepy.utils import pathlib
from nr.stream import stream
from nr.types import OrderedSet
from .modules import CraftrModuleLoader
from .proplib import PropertySet, Properties, NoSuchProperty

STDLIB_DIR = nr.fs.join(nr.fs.dir(nr.fs.dir(nr.fs.dir(__file__))))

session = None  # The current #Session


class Session(_build.Master):
  """
  This is the root instance for a build session. It introduces a new virtual
  entity called a "scope" that is created for every build script. Target names
  will be prepended by that scope, relative paths are treated relative to the
  scopes current directory and every scope gets its own build output directory.
  """

  class _GraphvizExporter(_build.GraphvizExporter):

    def handle_master(self, master):
      super().handle_master(master)
      [self.handle_build_set(x) for x in master._build_sets]
      [self.handle_file_set(x) for x in master._file_sets]

  def __init__(self, build_directory: str,
               behaviour: _build.Behaviour = None):
    super().__init__(behaviour or _build.Behaviour())
    self._build_directory = nr.fs.canonical(build_directory)
    self._current_scopes = []
    self._build_sets = []  # Registers all build sets
    self._file_sets = []  # Registers all file sets
    self.loader = CraftrModuleLoader(self)
    self.nodepy_context = nodepy.context.Context()
    self.nodepy_context.resolver.loaders.append(self.loader)
    self.nodepy_context.resolver.paths.append(pathlib.Path(STDLIB_DIR))
    self.target_props = PropertySet()
    self.dependency_props = PropertySet()

  def load_module(self, name):
    return self.nodepy_context.require(name + '.craftr', exports=False)

  def load_module_from_file(self, filename, is_main=False):
    filename = pathlib.Path(nr.fs.canonical(filename))
    module = self.loader.load_module(self.nodepy_context, None, filename)
    module.is_main = is_main
    self.nodepy_context.register_module(module)
    self.nodepy_context.load_module(module)
    return module

  @property
  def build_directory(self):
    return self._build_directory

  @contextlib.contextmanager
  def enter_scope(self, name, version, directory):
    scope = Scope(self, name, version, directory)
    self._current_scopes.append(scope)
    try: yield
    finally:
      assert self._current_scopes.pop() is scope

  @property
  def current_scope(self):
    return self._current_scopes[-1] if self._current_scopes else None

  @property
  def current_target(self):
    if self._current_scopes:
      return self._current_scopes[-1].current_target
    return None

  def dump_graphviz(self, *args, **kwargs):
    return _build.dump_graphviz(self, *args,
      exporter_class=self._GraphvizExporter, **kwargs)


class Scope:
  """
  A scope basically represents a Craftr build module. The name of a scope is
  usually determined by the Craftr module loader.

  Note that a scope may be created with a name and version set to #None. The
  scope must be initialized with the #module_id() build script function.
  """

  def __init__(self, session: Session, name: str, version: str, directory: str):
    self.session = session
    self.name = name
    self.version = version
    self.directory = directory
    self.current_target = None

  @property
  def build_directory(self):
    return nr.fs.join(self.session.build_directory, self.name)


class Target(_build.Target):
  """
  Extends the graph target class by a property that describes the active
  build set that is supposed to be used by the next function that creates an
  operator.
  """

  class Dependency:
    def __init__(self, target, public):
      self.target = target
      self.public = public
      self.properties = Properties(session.dependency_props, owner=current_scope())

  def __init__(self, name: str):
    super().__init__(name, session)
    self.current_build_set = None
    self.properties = Properties(session.target_props, owner=current_scope())
    self.public_properties = Properties(session.target_props, owner=current_scope())
    self._dependencies = []
    self._operator_name_counter = collections.defaultdict(lambda: 1)

  def __getitem__(self, prop_name):
    """
    Read a (combined) property value from the target.
    """

    prop = self.properties.propset[prop_name]
    inherit = prop.options.get('inherit', False)
    return self.get_prop(prop_name, inherit=inherit)

  @property
  def dependencies(self):
    return list(self._dependencies)

  def add_dependency(self, target: 'Target', public: bool):
    """
    Adds another target as a dependency to this target. This will cause public
    properties to be inherited when using the #prop() method.
    """

    if not isinstance(target, Target):
      raise TypeError('expected Target, got {}'.format(
        type(target).__name__))

    for x in self._dependencies:
      if x.target is target:
        raise RuntimeError('dependency to "{}" already exists'.format(target.name))
    dep = Target.Dependency(target, public)
    self._dependencies.append(dep)
    return dep

  def get_prop(self, prop_name, inherit=False, default=NotImplemented):
    """
    Returns a property value. If a value exists in #exported_props and #props,
    the #exported_props takes preference.

    If *inherit* is #True, the property must be a #proplib.List property
    and the values in the exported and non-exported property containers as
    well as transitive dependencies are respected.

    Note that this method does not take property options into account, so
    even if you specified `options={'inherit': True}` on the property you
    want to retrieve, you will need to pass `inherit=True` explicitly to this
    method. If you want this to happen automatically, use the #__getitem__().
    """

    if inherit:
      def iter_values():
        yield self.public_properties[prop_name]
        yield self.properties[prop_name]
        for target in self.transitive_dependencies().attr('target'):
          yield target.public_properties[prop_name]
      prop = self.properties.propset[prop_name]
      return prop.type.inherit(prop_name, iter_values())
    else:
      if self.public_properties.is_set(prop_name):
        return self.public_properties[prop_name]
      elif self.properties.is_set(prop_name):
        return self.properties[prop_name]
      elif default is NotImplemented:
        return self.properties.propset[prop_name].get_default()
      else:
        return default

  def transitive_dependencies(self):
    """
    Returns an iterator that yields the #Dependency objects of this target
    and all of their (public) transitive dependencies. The returned iterator
    is a #stream instance, thus you can use any streaming operations on the
    returned object.
    """

    def worker(target, private=False):
      for dep in target.dependencies:
        if dep.public or private:
          yield dep
        yield from worker(dep.target)
    return stream.unique(worker(self, private=True))


class Operator(_build.Operator):
  """
  Extends the graph operator class so that the build master does not need
  to be passed explicitly.
  """

  def __init__(self, name, commands):
    super().__init__(name, session, commands)


class BuildSet(_build.BuildSet):
  """
  Extends the graph BuildSet class so that the build master does not need to
  be passed explicitly and supporting some additional parameters for
  convenient construction.
  """

  """
  def __init__(self, inputs: list = (),
               from_: list = None,
               description: str = None,
               alias: str = None,
               **kwargs):
    super().__init__(session, (), description, alias)
    for bset in (from_ or ()):
      self.add_from(bset)
    if from_ is not None:
      # Only take into account BuildSets in the inputs that are not
      # already dependend upon transitively. This is to reduce the
      # number of connections (mainly for a nice Graphviz representation)
      # between build sets while keeping the API as simple as passing both
      # the from_ and inputs parameters.
      for x in inputs:
        if not self._has_transitive_input(self, x):
          self._inputs.add(x)
    else:
      self._inputs.update(inputs)
    for key, value in kwargs.items():
      if isinstance(value, str):
        self.variables[key] = value
      else:
        self.add_files(key, value)
    session._build_sets.append(self)  # TODO
  """

  @classmethod
  def _has_transitive_input(cls, self, build_set):
    for x in self._inputs:
      if build_set is x:
        return True
      if cls._has_transitive_input(x, build_set):
        return True
    return False

  def partite(self, *on_sets, fizzle=True):
    """
    Partite the build set into a #BuildSet per input/output file combination.

    If *on_sets* is specified, it must specify the set names on which the
    1-to-1 partition occurs, other sets will be contained unpartitioned in
    every build set that is returned by this function.

    Otherwise, the partition will be performed over all file sets and the
    cardinality of all file sets must be the same.

    The link between the build sets is removed unless *fizzle* is False.
    """

    partition_inputs = []
    partition_outputs = []
    copy_inputs = []
    copy_outputs = []

    if not on_sets:
      on_sets = set(self.inputs.keys()) | set(self.outputs.keys())
    else:
      on_sets = OrderedSet(on_sets)

    for name in self.inputs:
      (partition_inputs if name in on_sets else copy_inputs).append(name)
    for name in self.outputs:
      (partition_outputs if name in on_sets else copy_outputs).append(name)

    if not partition_inputs or not partition_outputs:
      raise ValueError('no inputs or outputs to partition')

    # Ensure that the cardinality of all sets that the partition is
    # run on is the same.
    counts = set(len(self.inputs[x]) for x in partition_inputs)
    counts |= set(len(self.outputs[x]) for x in partition_outputs)
    if len(counts) != 1:
      raise ValueError('cardinality of sets that are to be partitioned '
                       'must match (input sets: {}, output sets: {}, counts: {})'
                       .format(partition_inputs, partition_outputs, counts))

    if fizzle:
      self.fizzle()

    for i in range(counts.pop()):
      insets = {}
      outsets = {}
      for name in partition_inputs:
        insets[name] = file_set([self.inputs[name][i]], [self.inputs[name]])
      for name in partition_outputs:
        outsets[name] = file_set([self.outputs[name][i]], [self.outputs[name]])
      for name in copy_inputs:
        insets[name] = self.inputs[name]
      for name in copy_outputs:
        outsets[name] = self.outputs[name]
      yield build_set(insets, outsets, self.variables, description=self._description)

  # _build.BuildSet

  def fizzle(self):
    session._build_sets.remove(self)  # TODO
    super().fizzle()


def current_session(do_raise=True):
  if do_raise and session is None:
    raise RuntimeError('no current session')
  return session


def current_scope(do_raise=True):
  scope = session.current_scope
  if do_raise and scope is None:
    raise RuntimeError('no current scope')
  if not scope.name or not scope.version:
    raise RuntimeError('current scope has no name/version, use '
                       'project() function to initialize')
  return scope


def current_target(do_raise=True):
  scope = current_scope(do_raise)
  target = scope and scope.current_target
  if do_raise and target is None:
    raise RuntimeError('no current target')
  return target


def current_build_set(do_raise=True):
  target = current_target(do_raise)
  build_set = target and target.current_build_set
  if do_raise and build_set is None:
    raise RuntimeError('no current build set')
  return build_set


def bind_target(target):
  """
  Binds the specified *target* as the current target in the current scope.
  """

  session.current_scope.current_target = target


def bind_build_set(build_set):
  """
  Binds the specified *build_set* in the currently active target.
  """

  current_target().current_build_set = build_set


# Public API Level 1 (Build Scripts)
# ==================================

__all__ += [
  'project',
  'target',
  'file_set',
  'join_file_sets',
  'extract_file_set',
  'properties',
  'depends',
  'build_set',
  'glob',
  'chfdir'
]


def project(name, version):
  scope = session.current_scope
  scope.name = name
  scope.version = version


def target(name, bind=True):
  """
  Create a new target with the specified *name* in the current scope and
  set it as the current target.
  """

  scope = current_scope()
  target = session.add_target(Target(scope.name + '@' + name))
  if bind:
    bind_target(target)
  return target


def file_set(files, inputs=None):
  """
  Create a new file set. File sets are usually passed as values into
  target properties using the #properties() method.
  """

  directory = current_scope().directory
  files = [nr.fs.abs(x, directory) for x in files]
  fset = _build.FileSet(session, files, inputs)
  session._file_sets.append(fset)
  return fset


def join_file_sets(sets):
  """
  Joins multiple #FileSet objects into one. If *sets* contains only a single
  set, that set is returned as is.
  """

  if len(sets) == 1:
    return sets[0]

  files = [y for x in sets for y in x]
  return file_set(files, sets)


def extract_file_set(set_name, build_sets):
  """
  Extract a single file set from the outputs of one or multiple build sets.
  """

  if isinstance(build_sets, _build.BuildSet):
    build_sets = [build_sets]
  fset = file_set([])
  for x in build_sets:
    fset.add_from(x.outputs[set_name])
  return fset


def properties(_props=None, _target=None, **kwarg_props):
  """
  Sets properties in the current target.

  _props (dict): A dictionary of properties to set. The keys in the
      dictionary can have special syntax to mark a property as publicly
      visible (prefix with `@`) and/or to append to existing values in
      the same target (suffix with `+`).
  _target (Target): The target to set the properties in. Defaults to
      the currently active target.
  kwarg_props: Keyword-argument style property values. Similar to the
      *_props* dictionary, keys in this dictionary may be prefixed with
      `public__` and/or suffixed with `__append`.
  """

  target = _target or current_target()
  props = {}

  # Prepare the parameters from both sources.
  for key, value in (_props or {}).items():
    public = key[0] == '@'
    if public: key = key[1:]
    append = key[-1] == '+'
    if append: key = key[:-1]
    props.setdefault(key, []).append((value, public, append))
  for key, value in kwarg_props.items():
    public = key.startswith('public__')
    if public: key = key[8:]
    append = key.endswith('__append')
    if append: key = key[:-8]
    props.setdefault(key, []).append((value, public, append))

  for key, operations in props.items():
    for value, public, append in operations:
      dest = target.public_properties if public else target.properties
      if append and dest.is_set(key):
        prop = dest.propset[key]
        value = prop.coerce(value, dest.owner)
        value = dest.propset[key].type.inherit(key, [dest[key], value])
      try:
        dest[key] = value
      except NoSuchProperty as exc:
        print('[WARNING]: Property {} does not exist'.format(exc)) # TODO


def depends(target, public=False):
  """
  Add *target* as a dependency to the current target.
  """

  if isinstance(target, str):
    scope, name = target.partition(':')[::2]
    if not scope:
      scope = current_scope().name
    target = session.get_target(scope + '@' + name)

  return current_target().add_dependency(target, public)


def glob(patterns, parent=None, excludes=None, include_dotfiles=False,
         ignore_false_excludes=False):
  if not parent:
    parent = session.current_scope.directory
  return nr.fs.glob(patterns, parent, excludes, include_dotfiles,
                    ignore_false_excludes)


def chfdir(filename):
  if nr.fs.isabs(filename):
    filename = nr.fs.rel(filename, current_scope().directory)
  return nr.fs.join(current_scope().build_directory, filename)


# Public API Level 2 (Build Modules)
# ==================================

__all__ += [
  'build_set',
  'create_operator'
]


def build_set(inputs, outputs, variables=None, alias=None, description=None):
  """
  Create a new #BuildSet.
  """

  bset = BuildSet(session, alias, description, inputs, outputs, variables)
  session._build_sets.append(bset)
  bind_build_set(bset)
  return bset


def create_operator(*, for_each=False, variables=None, **kwargs):
  """
  This function is not usually called from a build script unless you want
  to hard code a command. It will inspect the commands list for input and
  output files and respectively generate new build sets accordingly.

  for_each (bool): If this is set to #True, all inputs and outputs will be
    partitioned into separate build sets.
  kwargs: Additional arguments passed to the #Operator constructor.
  """

  target = current_target()

  commands = kwargs['commands']
  subst = session.behaviour.get_substitutor()
  insets, outsets, varnames = subst.multi_occurences(commands)

  name = kwargs['name']
  if '#' not in name:
    count = target._operator_name_counter[name]
    target._operator_name_counter[name] = count + 1
    name += '#' + str(count)
    kwargs['name'] = name

  build_set = current_build_set()
  operator = target.add_operator(Operator(**kwargs))
  operator.variables.update(variables or {})
  if for_each:
    for split_set in build_set.partite(*insets, *outsets):
      operator.add_build_set(split_set)
  else:
    operator.add_build_set(build_set)

  return operator
