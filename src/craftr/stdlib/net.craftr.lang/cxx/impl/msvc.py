
from craftr.api import *
from nr.stream import unique
from typing import Union, List
import logging as log
import nr.fs as path
import base from './base'
import msvc from 'net.craftr.compiler.msvc'
import {options} from '../build.craftr'


"""
class MsvcCompilerOptions(CompilerOptions):

  __annotations__ = [
    ('msvc_nodefaultlib', Union[bool, List[str]], None),
    ('embedd_debug_symbols', bool, True),
    ('msvc_disable_warnings', List[str], None),
    ('msvc_warnings_as_errors', List[str], None),
    ('msvc_compile_flags', List[str], None),
    ('msvc_resource_files', List[str], None)
  ]

  def __init__(self, **kwargs):
    if kwargs.get('msvc_nodefaultlib') == True:
      kwargs['msvc_nodefaultlib'] = ['']
    super().__init__(**kwargs)
"""

class MsvcCompiler(base.Compiler):

  id = 'msvc'
  family = 'msvc'

  #compiler_c = ['cl', '/nologo']
  #compiler_cpp = ['cl', '/nologo']
  compiler_out = ['/c', '/Fo%ARG%']

  c_std = ['/std:%ARG%']
  cpp_std = ['/std:%ARG%']
  pic_flag = []
  debug_flag = []  # handled explicitly together with embedd_debug_symbols
  define_flag = '/D%ARG%'
  include_flag = '/I%ARG%'
  expand_flag = '/E'
  warnings_flag = '/W4'
  warnings_as_errors_flag = '/WX'
  optimize_none_flag = '/Od'
  optimize_speed_flag = '/O2'
  optimize_size_flag = ['/O1', '/Os']
  optimize_best_flag = ['/Ox']
  enable_exceptions = '/EHsc'
  disable_exceptions = []
  enable_rtti = '/GR'
  disable_rtti = '/GR-'
  force_include = ['/FI', '%ARG%']
  save_temps = ['/P', '/Fi$out.i']  # TODO: Prevents the compilation step. :(

  compiler_supports_openmp = True
  compiler_enable_openmp = lambda: lambda self: ['-Xclang', '-fopenmp'] if self.is_clang_cl else ['/openmp']
  linker_enable_openmp = lambda: lambda self: ['libiomp5md.lib'] if self.is_clang_cl else []

  #linker_c = ['link', '/nologo']
  #linker_cpp = linker_c
  linker_out = '/OUT:${@product}'
  linker_shared = '/DLL'
  linker_exe = []
  linker_lib = '%ARG%.lib'
  linker_libpath = '/LIBPATH:%ARG%'
  linker_runtime = {}  # Required flags will be added in build_compile_flags()

  #archiver = ['lib', '/nologo']
  archiver_out = '/OUT:${@product}'

  def __init__(self, toolkit):
    if toolkit.type == toolkit.TYPE_MSVC:
      name = 'Microsoft Visual C++'
    elif toolkit.type == toolkit.TYPE_LLVM:
      name = 'LLVM Clang-CL'
    else:
      name = toolkit.type
    super().__init__(
      name = name,
      compiler_c = [toolkit.cl_bin, '/nologo'],
      compiler_cpp = [toolkit.cl_bin, '/nologo'],
      linker_c = [toolkit.cl_info.link_program, '/nologo'],
      linker_cpp = [toolkit.cl_info.link_program, '/nologo'],
      archiver = [toolkit.cl_info.lib_program, '/nologo'],
      version = toolkit.cl_version,
      arch = toolkit.cl_info.target,
      compiler_env = toolkit.environ,
      linker_env = toolkit.environ,
      archiver_env = toolkit.environ,
      deps_prefix = toolkit.deps_prefix
    )
    self.toolkit = toolkit
    self.is_clang_cl = 'clang' in self.name.lower()

    for key in self.__fields__:
      value = getattr(self, key)
      if callable(value):
        setattr(self, key, value(self))

  def info_string(self):
    return '{} v{version} ({id}) {cl_version} for {arch}'.format(
      self.name,
      version = self.toolkit.version,
      id = self.id,
      cl_version = self.toolkit.cl_version,
      arch = self.arch)

  def init(self):
    props = session.target_props
    props.add('cxx.msvcDisableWarnings', 'StringList')
    props.add('cxx.msvcWarningsAsErrors', 'StringList')
    props.add('cxx.msvcCompilerFlags', 'StringList')
    props.add('cxx.msvcLinkerFlags', 'StringList')
    props.add('cxx.msvcNoDefaultLib', 'StringList')
    props.add('cxx.msvcResourceFiles', 'PathList')
    props.add('cxx.msvcConformance', 'StringList', options={'inherit': True})
    props.add('cxx.outMsvcResourceFiles', 'PathList')

  # @override
  def translate_target(self, target, data):
    src_dir = target.scope.directory
    obj_dir = path.join(target.build_directory, 'obj')
    if data.msvcResourceFiles:
      outfiles = [chfdir(nr.fs.setsuffix(x, '.res'), obj_dir, src_dir)
                  for x in data.msvcResourceFiles]
      command = ['rc', '/r', '/nologo', '/fo', '$@out', '$<in']
      operator('cxx.msvcRc', commands=[command], environ=self.compiler_env)
      build_set({'in': data.msvcResourceFiles}, {'out': outfiles})
      properties(target, {'@cxx.outMsvcResourceFiles+': outfiles})
    if base.is_sharedlib(data):
      data.defines += ['_WINDLL']

  # @override
  def get_compile_command(self, target, data, lang):
    command = super().get_compile_command(target, data, lang)

    # Translate a potentially unknown C++ standard name for MSVC.
    if data.cppStd == 'c++17':
      data.cppStd = 'c++latest'

    if BUILD.debug:
      command += ['/Od', '/RTC1', '/FC']
      if not self.version or self.version >= '18':
        # Enable parallel writes to .pdb files.
        command += ['/FS']
      if data.separateDebugInformation:
        command += ['/Zi', '/Fd${@outPdb}']  # TODO: no .pdb files generated.?
      else:
        command += ['/Z7']
    command += ['/wd' + str(x) for x in unique(data.msvcDisableWarnings)]
    command += ['/bigobj']

    if not data.runtimeLibrary:
      data.runtimeLibrary = 'static' if options.staticRuntime else 'dynamic'
    if data.runtimeLibrary == 'static':
      command += ['/MTd' if BUILD.debug else '/MT']
    elif data.runtimeLibrary == 'dynamic' or data.runtimeLibrary is None:
      command += ['/MDd' if BUILD.debug else '/MD']
    else:
      error('invalid cxx.runtimeLibrary: {!r}'.format(data.runtimeLibrary))

    command += ['/we' + str(x) for x in unique(data.msvcWarningsAsErrors)]
    command += data.msvcCompilerFlags

    for conf in data.msvcConformance:
      arg = '/Zc:' + conf
      command.append(arg)

    if self.deps_prefix:
      command += ['/showIncludes']
    return command

  # @override
  def add_objects_for_source(self, target, data, lang, src, buildset, objdir):
    super().add_objects_for_source(target, data, lang, src, buildset, objdir)
    obj = buildset.outputs['obj'][0]
    if BUILD.debug and data.separateDebugInformation:
      pdb = path.setsuffix(obj, '.pdb')
      buildset.add_output_files('outPdb', [pdb])

  # @override
  def get_link_command(self, target, data, lang):
    command = super().get_link_command(target, data, lang)
    command += [
      ('/NODEFAULTLIB:' + x) if x else '/NODEFAULTLIB'
      for x in unique(data.msvcNoDefaultLib)
    ]
    if base.is_sharedlib(data):
      command += ['/IMPLIB:${@outImplib}']  # set from add_link_outputs()
    if BUILD.debug and base.is_sharedlib(data):
      command += ['/DEBUG']
    return command

  # @override
  def add_link_outputs(self, target, data, lang, buildset):
    buildset.add_input_files('in', target['cxx.outMsvcResourceFiles'])
    if base.is_sharedlib(data):
      implib = path.setsuffix(data.productFilename, '.lib')
      buildset.add_output_files('outImplib', [implib])
      properties({'@+cxx.outLinkLibraries': [implib]}, target=target)
    super().add_link_outputs(target, data, lang, buildset)


def get_compiler(fragment):
  if fragment:
    log.warn('craftr/lang/cxx/msvc: Fragment not supported. (fragment = {!r})'
      .format(fragment))
  return MsvcCompiler(msvc.MsvcToolkit.from_config())
