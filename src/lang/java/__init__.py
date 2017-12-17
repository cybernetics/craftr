"""
Targets for building Java projects.
"""

import copy
import nodepy
import re
import typing as t

import craftr, {path, options} from 'craftr'
import maven from './maven'


# TODO: Ensure consistent command-line arguments order


ONEJAR_FILENAME = path.canonical(
  options.get('java.onejar',
  path.join(str(module.directory), 'one-jar-boot-0.97.jar')))


def _init_repos():
  repo_config = copy.deepcopy(options.get('java.maven_repos', {}))
  repo_config.setdefault('default', 'http://repo1.maven.org/maven2/')

  repos = []
  if repo_config['default']:
    repos.append(maven.MavenRepository('default', repo_config.pop('default')))
  for name, repo_url in repo_config.items():
    repos.append(maven.MavenRepository(name, repo_url))
  return repos

repos = _init_repos()


def partition_sources(
    sources: t.List[str],
    base_dirs: t.List[str],
    parent: str
  ) -> t.Dict[str, t.List[str]]:
  """
  Partitions a set of files in *sources* to the appropriate parent directory
  in *base_dirs*. If a file is found that is not located in one of the
  *base_dirs*, a #ValueError is raised.

  A relative path in *sources* and *base_dirs* will be automatically converted
  to an absolute path using the *parent*, which defaults to the currently
  executed module's directory.
  """

  base_dirs = [path.canonical(x, parent) for x in base_dirs]
  result = {}
  for source in [path.canonical(x, parent) for x in sources]:
    for base in base_dirs:
      rel_source = path.rel(source, base)
      if path.isrel(rel_source):
        break
    else:
      raise ValueError('could not find relative path for {!r} given the '
        'specified base dirs:\n  '.format(source) + '\n  '.join(base_dirs))
    result.setdefault(base, []).append(rel_source)
  return result


class JavaBase(craftr.TargetTrait):
  """
  jar_dir:
    The directory where the jar will be created in.

  jar_name:
    The name of the JAR file. Defaults to the target name. Note that the Java
    JAR command does not support dots in the output filename, thus it will
    be rendered to a temporary directory.

  main_class:
    A classname that serves as an entry point for the JAR archive. Note that
    you should prefer to use `java_binary()` to create a JAR binary that
    contains all its dependencies merged into one.

  javac_jar:
    The name of Java JAR command to use. If not specified, defaults to the
    value of the `java.javac_jar` option or simply "jar".
  """

  def __init__(self, jar_dir: str = None,
                     jar_name: str = None,
                     main_class: str = None,
                     javac_jar: str = None):
    if jar_dir:
      jar_dir = path.canonical(self.jar_dir, self.target.cell.build_directory)
    self.jar_dir = jar_dir
    self.jar_name = jar_name
    self.main_class = main_class
    self.javac_jar = javac_jar or options.get('java.javac_jar', 'jar')
    if not self.jar_dir:
      self.jar_dir = self.target.cell.build_directory
    if not self.jar_name:
      self.jar_name = (self.target.cell.name.split('/')[-1] + '-' + self.target.name + '-' + self.target.cell.version)

  @property
  def jar_filename(self) -> str:
    return path.join(self.jar_dir, self.jar_name) + '.jar'


@craftr.TargetFactory
class library(JavaBase):
  """
  The base target for compiling Java source code and creating a Java binary
  or libary JAR file.

  # Parameters
  srcs:
    A list of source files. If `srcs_root` is not specified, the config option
    `java.src_roots` to determine the root of the source files.

  src_roots:
    A list of source root directories. Defaults to `java.src_roots` config.
    The default value for this configuration is `['src', 'java', 'javatest']`.

  class_dir:
    The directory where class files will be compiled to.

  javac:
    The name of Java compiler to use. If not specified, defaults to the value
    of the `java.javac` option or simply "javac".

  extra_arguments:
    A list of additional arguments for the Java compiler. They will be
    appended to the ones specified in the configuration.
  """

  def __init__(self, srcs: t.List[str],
                     src_roots: t.List[str] = None,
                     class_dir: str = None,
                     javac: str = None,
                     extra_arguments: t.List[str] = None,
                     **kwargs):
    super().__init__(**kwargs)

    if src_roots is None:
      src_roots = options.get('java.src_roots', ['src', 'java', 'javatest'])
    if src_roots:
      src_roots = [craftr.localpath(x) for x in src_roots]

    self.srcs = [craftr.localpath(x) for x in srcs]
    self.src_roots = src_roots
    self.class_dir = class_dir
    self.javac = javac or options.get('java.javac', 'javac')
    self.extra_arguments = extra_arguments
    if not self.class_dir:
      self.class_dir = 'classes/' + self.target.name
    self.class_dir = path.canonical(self.class_dir, self.target.cell.build_directory)

  def translate(self, jar_filename=None):
    jar_filename = jar_filename or self.jar_filename
    extra_arguments = options.get('java.extra_arguments', []) + (self.extra_arguments or [])
    classpath = []

    for data in self.target.dep_traits():
      if isinstance(data, library):
        classpath.append(data.jar_filename)
      elif isinstance(data, prebuilt):
        classpath.extend(data.binary_jars)

    # Calculate output files.
    classfiles = []
    for base, sources in partition_sources(self.srcs, self.src_roots, self.target.cell.directory).items():
      for src in sources:
        classfiles.append(path.join(self.class_dir, path.setsuffix(src, '.class')))

    if self.srcs:
      command = [self.javac, '-d', self.class_dir]
      if classpath:
        command += ['-classpath', path.pathsep.join(classpath)]
      command += self.srcs
      command += extra_arguments

      javac = craftr.BuildAction(
        name = 'javac',
        deps = ...,
        commands = [command],
        input_files = classpath + self.srcs,
        output_files = classfiles
      )
      self.target.add_action(javac)
    else:
      javac = None

    flags = 'cvf'
    if self.main_class:
      flags += 'e'

    command = [self.javac_jar, flags, jar_filename]
    if self.main_class:
      command.append(self.main_class)
    command += ['-C', self.class_dir, '.']

    self.target.add_action(craftr.BuildAction(
      name = 'jar',
      deps = [javac or ...],
      commands = [command],
      input_files = classfiles,
      output_files = [jar_filename]
    ))


@craftr.TargetFactory
class binary(library.cls):
  """
  Takes a list of Java dependencies and creates an executable JAR archive
  from them.

  # Parameters
  dist_type:
    The distribution type. Can be `'merge'`, `'onejar'` or `None`. The default
    is the value configured in `java.dist_type` (which defaults to `'merge'`).
  """

  def __init__(self, main_class: str = None,
                     dist_type: str = NotImplemented,
                     **kwargs):
    super().__init__(**kwargs)
    if not main_class:
      raise ValueError('missing value for "main_class"')
    if dist_type not in ('merge', 'onejar', None, NotImplemented):
      raise ValueError('invalid dist_type: {!r}'.format(dist_type))
    self.main_class = main_class
    self.dist_type = dist_type

  def complete(self):
    if self.dist_type is NotImplemented:
      self.dist_type = options.get('java.dist_type', 'merge')

  def translate(self):
    inputs = []

    if self.srcs:
      if self.dist_type is None:
        sub_jar = self.jar_filename
      else:
        sub_jar = path.addtobase(self.jar_filename, '-classes')
      super().translate(jar_filename=sub_jar)
      inputs.append(sub_jar)

    if self.dist_type is None:
      return

    for data in self.target.dep_traits():
      if isinstance(data, library):
        inputs.append(data.jar_filename)
      elif isinstance(data, prebuilt):
        inputs.extend(data.binary_jars)

    command = nodepy.runtime.exec_args + [str(require.resolve('./augjar').filename)]
    command += ['-o', self.jar_filename]

    if self.dist_type == 'merge':
      command += [inputs[0]]  # Merge into the first specified dependency.
      command += ['-s', 'Main-Class=' + self.main_class]
      for infile in inputs[1:]:
        command += ['-m', infile]

    else:
      assert self.dist_type == 'onejar'
      command += [ONEJAR_FILENAME]
      command += ['-s', 'One-Jar-Main-Class=' + self.main_class]
      for infile in inputs:
        command += ['-f', 'lib/' + path.base(infile) + '=' + infile]

    self.target.add_action(craftr.BuildAction(
      name = self.dist_type,
      deps = [self.target.actions.get('jar') or ...],
      commands = [command],
      input_files = inputs,
      output_files = [self.jar_filename]
    ))


@craftr.TargetFactory
class prebuilt(craftr.TargetTrait):
  """
  Represents a prebuilt JAR. If an artifact ID is specified, the artifact will
  be downloaded from Maven Central, or the configured maven repositories.

  # Parameters
  binary_jar:
    The path to a binary `.jar` file.
  artifact:
    An artifact ID of the format `group:artifact:version` which will be
    downloaded from Maven.
  """

  def __init__(self,
      binary_jar: str = None,
      binary_jars: t.List[str] = None,
      artifact: str = None,
      artifacts: t.List[str] = None):
    if not binary_jars:
      binary_jars = []
    if binary_jar:
      binary_jars.append(binary_jar)
    if not artifacts:
      artifacts = []
    if artifact:
      artifacts.append(artifact)

    self.artifacts = [maven.Artifact.from_id(x) for x in artifacts]
    self.binary_jars = [craftr.localpath(x) for x in binary_jars]
    self.did_install = False

  def install(self):
    """
    Download the artifacts from the repository and add the binary JAR files
    to the #binary_jars list.
    """

    if self.did_install:
      return
    self.did_install = True

    poms = {}
    queue = [(0, x) for x in reversed(self.artifacts)]

    # XXX This should probably be compared against a global set of already
    #     installed dependencies, so we don't do the same work twice if
    #     two separate projects need the same dependency.

    print('[{}] Resolving JARs...'.format(self.target.long_name))
    while queue:
      depth, artifact = queue.pop()
      if artifact.scope != 'compile':
        continue

      # For now, we use this to avoid downloading the same dependency in
      # different versions, instead only the first version that we find.
      artifact_id = '{}:{}'.format(artifact.group, artifact.artifact)
      if artifact_id in poms:
        continue

      # Try to find a POM manifest for the artifact.
      for repo in repos:
        # If the artifact has no version, that version may be filled in by
        # the repository, but we only want to use that filled in version if
        # we can get a POM.
        artifact_clone = copy.copy(artifact)
        pom = repo.download_pom(artifact_clone)
        if pom:
          artifact = artifact_clone
          break
      else:
        if not artifact.optional:
          raise RuntimeError('could not find artifact: {}'.format(artifact))
        print(indent[:-2] + '    SKIP (Optional)')
        continue

      # Cache the POM and add its dependencies so we can "recursively"
      # resolve them.
      poms[artifact_id] = (artifact, pom, repo)
      queue.extend([(depth+1, x) for x in reversed(maven.pom_eval_deps(pom))])

      # Print dependency info.
      #tree_indicator = '| ' if queue and queue[-1][0] > depth else '\\ '
      indent = '| ' * depth
      print('  {}{} ({})'.format('| ' * depth, artifact, repo.name))

    # Download dependencies.
    for artifact, pom, repo in poms.values():
      binary_jar = path.join(craftr.build_directory, '.maven-artifacts', artifact.to_local_path('jar'))
      self.binary_jars.append(binary_jar)

      # Create a download action. We may create actions even before
      # #translate(), so we save ourselves some trouble.
      command = nodepy.runtime.exec_args + [
        str(require.resolve('craftr/tools/download').filename),
        '--url', repo.get_artifact_uri(artifact, 'jar'),
        '--to', binary_jar
      ]
      self.target.add_action(craftr.BuildAction(
        name = str(artifact),
        deps = ...,
        commands = [command],
        output_files = [binary_jar]
      ))

  def complete(self):
    self.install()

  def translate(self):
    pass


@craftr.TargetFactory
class proguard(craftr.TargetTrait):
  """
  Run ProGuard on the JAR dependencies of this target. If the *pro_file*
  argument is specified, it must be the name of a response file that will
  be passed to the ProGuard commandline.

  The *cwd* will default to the parent directory of *pro_file*, if specified.
  """

  def __init__(self, pro_file=None, options=(), cwd=None, java=None,
               outjars=None):
    self.java = java or options.get('java.java', 'java')
    self.pro_file = craftr.localpath(pro_file) if pro_file else None
    if not cwd and self.pro_file:
      cwd = path.dir(self.pro_file)
    self.cwd = craftr.localpath(cwd) if cwd else None
    self.options = options
    self.outjars = outjars

  def translate(self):
    injars = []
    for data in self.target.traits():
      if isinstance(data, prebuilt):
        injars.append(data.binary_jar)
      elif isinstance(data, (binary, library)):
        injars.append(data.jar_filename)

    if self.outjars:
      outjars = self.outjars
    else:
      outjars = [path.canonical(path.addtobase(x, '.proguard')) for x in injars]

    args = [self.java, '-jar', str(module.directory.joinpath('proguard-5.3.3.jar'))]
    if self.pro_file:
      args.append('@' + self.pro_file)
    args += ['-injars', path.pathsep.join(injars)]
    args += ['-outjars', path.pathsep.join(outjars)]
    # XXX -libraryjars for all transitive dependencies?
    # XXX rt.jar no longer available in Java 9
    args += ['-libraryjars', '<java.home>/lib/rt.jar']
    args += self.options

    self.target.add_action(craftr.BuildAction(
      deps = ...,
      commands = [args],
      input_files = injars,
      output_files = outjars,
      cwd = self.cwd
    ))


def run(binary, *argv, name=None, java=None, jvm_args=(), **kwargs):
  kwargs.setdefault('explicit', True)
  kwargs.setdefault('console', True)
  target = craftr.resolve_target(binary)
  if name is None:
    name = target.name + '_run'
  if java is None:
    java = options.get('java.java', 'java')
  command = [java] + list(jvm_args) + ['-jar', target.trait.jar_filename] + list(argv)
  return craftr.gentarget(name = name, deps = [target], commands = [command], **kwargs)
