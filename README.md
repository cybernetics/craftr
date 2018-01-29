<img align="right" src=".assets/craftr-logo.png">

## The Craftr build system

<a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/license-MIT-yellow.svg?style=flat-square"></a>
<img src="https://img.shields.io/badge/version-3.0.0--dev-blue.svg?style=flat-square"/>
<a href="https://travis-ci.org/craftr-build/craftr"><img src="https://travis-ci.org/craftr-build/craftr.svg?branch=master"></a>
<a href="https://ci.appveyor.com/project/NiklasRosenstein/craftr"><img src="https://ci.appveyor.com/api/projects/status/6v01441cdq0s7mik?svg=true"></a>

Craftr is a modular build system inspired by [Buck], [CMake], [QBS] and
previous versions of Craftr itself. It combines a declarative syntax
with the ability to evaluate Python code in the build script. The backbone
for the build process is [Ninja], however, extensions can be used to target
other build backends.

  [Buck]: https://buckbuild.com/
  [CMake]: https://cmake.org/
  [QBS]: https://bugreports.qt.io/projects/QBS/summary
  [Ninja]: https://github.com/ninja-build/ninja.git

### Build script language definition

The Craftr DSL is a very similar to the one of QBS. Originally, Craftr build
scripts were plain Python code. However, a custom DSL allows for a lot of
customizablity and declarative power.  
A build script consists of statements and blocks, and most blocks have their
own inner grammar. Not all blocks can be nested inside each other.

```python
project "myproject" v1.6.4

options:
  int option1 = 42
  str option2 = "Hello, World"
  bool option3 = False

eval print('This is a single line of Python code!')

eval:
  print('This is a block of Python code!')
  print('The options you chose are:', option1, option2, option3)
  print('You are on', OSNAME)
  includes = ['./include']

pool "myPool" 4

export target "lib":
  export requires "cxx"
  export requires "cxx/libs/curl":
    cxx.link = True  # That's the default

  this.pool = "myPool"
  cxx.type = 'library'  # Defaults to 'executable'
  cxx.srcs = glob(['./src/*.cpp', './src/' + OSNAME + '/*.cpp'].
    excludes = ['./src/main.cpp'])

  # Can be a block or a statement. These have the same effect.
  # Remember that we defined `includes` in the eval section above.
  export cxx.includePaths = includes
  export:
    cxx.includePaths = includes

target "main":
  requires "@lib"
  cxx.srcs = ['./src/main.cpp']
```

#### Statement `project`

This statement is mandatory and must be specified as the first non-comment
line in the build script. It accepts a literal string for the project's name
and optionally a version as argument.

    <project> := "project" <str> [<version>]
    <str>     := "\"" + [^"]* + "\""
    <version> := "v" + <num> + "." + <num> + "." + <num>
    <num>     := "0" | ([1-9] + [0-9])

#### Statement `eval`

This statement executes a line of Python code. Everything following the
statement on the same line is treated as one line of Python code.

    <eval>        := "eval" + <python_expr>
    <python_expr> := ...

#### Statement `load`

This statement loads a Python script and executes it in the same scope as
`eval` would do. Using `load` multiple times will execute the Python script
multiple times.

    <load>  := "load" + <str>

#### Statement `export`

This statement can only be used inside, or as a prefix to, a `target` block.
It is the single-line form of the `export` block and can be added before a
variable assignment to export that variable to targets that depend on it.

Additionall, an `export` can be prepended to a `requires` block or statement
to export the dependency to other targets.

Exported targets are visible to targets in other modules using when depending
on a module with `requires`.

#### Statement `requires`

This statement is the single-line form of the `requires` block. It can only be
used inside a `target` block and accepts exactly one string literal as argument
that is used as the name of the dependency.

    <requires> := "requires" + <str> | "export" + "requires" + <str>

#### Statement `pool`

This statement is used to create a new job pool with a certain depth. Targets
that are assigned to this pool will be limited in their parallel execution.
Note that maybe not all build backends support this option.

    <pool> := "pool" + <str> + <num>`

#### Block `options`

This block can be used once in a build script and specifies the options that
will be automatically parsed from the command-line and configuration files and
made available to the build script's scope.

The `options` block has a special inner grammar.

    <options_line>  := <type> + <name> | <type> + <name> + "=" + <python_expr>
    <type>          := "int" | "str" | "bool"
    <name>          := [\w\d\_]+

#### Block `eval`

This block will evaluate Python code that is put inside the block. `eval`
blocks are executed in the order they are specified in the build script.
`eval` blocks may be used on the global level or inside a `target` block.

Inside a target, you have access to the option namespace provided by the
dependencies. Example:

```python
target "main":
  requires "cxx"
  eval:
    if cxx.compiler_id == 'msvc':
      error('Can not be compiled with MSVC.')
    cxx.srcs = glob('src/*.cpp')
    cxx.__exported__.includePaths = ['./include']
```

#### Block `target`

This block defines a new build target. A target is usually only useful with a
`requires` statement or block inside that loads a module which implements the
ability to build your target (like the `"cpp"` module).

Inside target blocks, there can be `requires` or `export` statements and
blocks as well as assignments in the form of `<key> = <python_expr>`. If the
`<key>` is not a registered target property (either a standard property or
registered by one of the target's dependencies), a warning will be printed
when assigning the key.

| Property        | Type | Description |
| --------------- | ---- | ----------- |
| `this.pool`     | str  | The name of a job pool to execute the target in. Note that some target handlers may provide their own properties to override the pool for certain parts of the build. |
| `this.syncio`   | bool | Synchronize the standard input/output of commands executed by the target with the console. This does not pair with the `pool` option. |
| `this.explicit` | bool | Do not build this target unless it is required by another target or it is explicitly specified on the command-line. |

#### Block `requires`

Similar to a `requires` statement, only that the block form allows you to
supply properties on this dependency. These properties will influence the
way the dependency is treated.  

Additionally, it allows you to select a subset of the dependencies' targets
that will be taken into account. By default, all exported targets of the
dependency are considered (or all, if no targets are exported).

```python
target "main":
  requires "cpp"
  requires "niklasrosenstein/maxon.c4d":
    this.select = ['c4d_legacy', 'python']
```

The string value that is passed to the `requires` statement or block may be
prefixed with an `@` (at) sign to indicate that the target does not require
another module, but a target from the same build script.

### Built-in variables

| Name | Type | Description |
| - | - | - |
| `OSNAME` | str | The name of the operating system, eg. `windows`, `macos` or `linux`. |
| `OSID` | str | An ID for the operating system, eg. `win32`, `darwin` or `linux`. |
| `OSARCH` | str | The architecture that the operating system is running, eg. `x86_64` or `x86`. |
| `MODE` | str | The build mode, either `"debug"` or `"release"`. |
| `DEBUG` | bool | True when the build mode is `"debug"`. |
| `RELEASE` | bool | True when the build mode is `"release"`. |
| `glob(patterns, excludes=[], parent=None)` | function | Match glob patterns relative to `parent`. If `parent` is omitted, defaults to the parent directory of the build script. |
| `error(message)` | function | Raise a build error with the specified message. |

### Implementing a target handler

If a module that implements a target handler is added as a dependency to a
target, that handler is also automatically added to that target. Usually,
target handlers require a fair amount of code for their implementation, thus
it is usually inconvenient to implement them inside an `eval` block.  
Instead, use `load` statement to load a Python file that implements a target
handler.

```python
project "cxx" v1.0.0
load "./cxx-handler.py"
```

A target handler can be implemented using the `craftr.TargetHandler` class
and must be registered to the project with `project.register_target_handler()`.

```python
import craftr

class CxxTargetHandler(craftr.TargetHandler):

  def setup_target(self, target):
    target.define_property('cpp.srcs', list)
    target.define_property('cpp.includePaths', list)
    target.define_property('cpp.defines', list)
    target.define_property('cpp.compilerId', str, 'msvc', readonly=True)
  
  def setup_requires(self, requires):
    requires.define_property('cpp.link', bool, True)

  def translate_target(self, target):
    srcs = target.get_property('cpp.srcs')
    for dep in target.requires():
      if dep.get_property('cpp.link'):
        for file in dep.output_files():
          if file.tag in ('c', 'cpp'):
            srcs.append(file.name)
    # TODO ..

project.register_target_handler(CxxTargetHandler)
```

---

<p align="center">Copyright &copy; 2015-2018 &nbsp; Niklas Rosenstein</p>
