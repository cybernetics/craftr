
import nodepy
import os
import re
import subprocess

import {Writer as NinjaWriter} from './ninja_syntax'


def quote(s, for_ninja=False):
  """
  Enhanced implementation of :func:`shlex.quote` as it generates single-quotes
  on Windows which can lead to problems.
  """

  if os.name == 'nt' and os.sep == '\\':
    s = s.replace('"', '\\"')
    if re.search('\s', s) or any(c in s for c in '<>'):
      s = '"' + s + '"'
  else:
    s = shlex.quote(s)
  if for_ninja:
    # Fix escaped $ variables on Unix, see issue craftr-build/craftr#30
    s = re.sub(r"'(\$\w+)'", r'\1', s)
  return s


def make_rule_description(node):
  commands = [' '.join(map(quote, x)) for x in node.commands]
  return ' && '.join(commands)


def make_rule_name(graph, node):
  return re.sub('[^\d\w\-_\.]+', '_', node.name) + '_' + graph.hash(node)


def prepare_build(build_directory, graph):
  build_file = os.path.join(build_directory, 'build.ninja')
  print('note: writing "{}"'.format(build_file))
  with open(build_file, 'w') as fp:
    writer = NinjaWriter(fp, width=9000)
    writer.comment('This file was automatically generated by Craftr')
    writer.comment('It is not recommended to edit this file manually.')
    writer.newline()

    # writer.variable('msvc_deps_prefix')  # TODO
    writer.variable('builddir', build_directory)
    writer.variable('nodepy_exec_args', ' '.join(map(quote, nodepy.runtime.exec_args)))
    writer.newline()

    non_explicit = []
    for node in sorted(graph.nodes(), key=lambda x: x.name):
      phony_name = make_rule_name(graph, node)
      rule_name = 'rule_' + phony_name
      if not node.explicit:
        non_explicit.append(phony_name)

      command = [
        '$nodepy_exec_args',
        str(require.resolve('craftr/main').filename),
        '--build-directory', build_directory,
        '--run-node', node.name
      ]
      order_only = []
      for dep in [graph[x] for x in node.deps]:
        #if not dep.output_files:
          order_only.append(make_rule_name(graph, dep))
        #else:
        #  order_only.extend(dep.output_files)

      writer.rule(rule_name, command, description=make_rule_description(node), pool = 'console' if node.console else None)
      writer.build(
        outputs = node.output_files or [phony_name],
        rule = rule_name,
        inputs = node.input_files,
        order_only = order_only)
      if node.output_files:
        writer.build([phony_name], 'phony', node.output_files)
      writer.newline()

    if non_explicit:
      writer.default(non_explicit)


def build(build_directory, graph, args):
  targets = [make_rule_name(graph, node) for node in graph.selected()]
  command = ['ninja', '-f', os.path.join(build_directory, 'build.ninja')] + list(args) + targets
  subprocess.run(command)


def clean(build_directory, graph, args):
  targets = [make_rule_name(graph, node) for node in graph.selected()]
  command = ['ninja', '-f', os.path.join(build_directory, 'build.ninja'), '-t', 'clean'] + list(args) + targets
  subprocess.run(command)