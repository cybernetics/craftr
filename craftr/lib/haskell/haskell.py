
import craftr
from nr import path

if OS.type == 'nt':
  exe_suffix = '.exe'
else:
  exe_suffix = ''


class HaskellTargetHandler(craftr.TargetHandler):

  def init(self, context):
    props = context.target_properties
    props.add('haskell.srcs', craftr.StringList)
    props.add('haskell.productName', craftr.String)
    props.add('haskell.compilerFlags', craftr.StringList)

  def translate_target(self, target):
    src_dir = target.directory
    build_dir = path.join(context.build_directory, target.module.name)
    data = target.get_props('haskell.', as_object=True)
    data.compilerFlags = target.get_prop_join('haskell.compilerFlags')

    if not data.productName:
      data.productName = target.name + target.module.version
    if data.srcs:
      data.srcs = [path.canonical(x, src_dir) for x in data.srcs]
      data.productFilename = path.join(build_dir, data.productName + exe_suffix)
      target.outputs.add(data.productFilename, ['exe'])

    if data.srcs:
      # Action to compile the sources to an executable.
      command = ['ghc', '-o', '$out', '$in']
      command += data.compilerFlags
      action = target.add_action('haskell.compile', commands=[command])
      build = action.add_buildset()
      build.files.add(data.srcs, ['in'])
      build.files.add(data.productFilename, ['out'])

      # Action to run the executable.
      command = [data.productFilename]
      action = target.add_action('haskell.run', commands=[command],
        explicit=True, syncio=True, output=False)
      action.add_buildset()


context.register_handler(HaskellTargetHandler())