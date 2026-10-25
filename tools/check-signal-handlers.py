#!/usr/bin/env python3

# Copyright 2026 Michael Still and contributors

"""Check that signal handlers only do things which cannot wait.

Python runs a signal handler on the main thread, between any two of that
thread's bytecodes. If the handler needs a lock the main thread already
holds, it waits for itself, forever. The obvious-looking things a handler
wants to do are exactly the ones which take such locks: logging (the Loki
handler enqueues every line under the logship spool's non-reentrant lock,
and the main thread logs constantly), database calls (connection pool and
channel locks, and a slow database outlasts TimeoutStopSec by itself),
threading primitives, and context managers. sf-api's drain handler
deadlocked on logging (#3495), and every daemon's exit_gracefully() made
database calls and logged, which got sf-database SIGKILLed (#4601). Both
read like ordinary code in review, which is why this is checked here.

Every signal.signal(signal, handler) call under shakenfist/ (tests
excluded) is found, and its handler must be a function this tool can
see: a module-level function, a function defined in the function making
the call, or a method of the calling class (self.name), including any
override of that method in a subclass. A lambda or a variable cannot be
checked, so it is an error.

A handler, and every helper it calls, may contain only plain statements:
assignments, expressions, if, return, pass, raise, try, global and
nonlocal. Loops, with blocks and nested definitions are refused.

An assignment may bind a name, an attribute of self, or an item of a plain
name (FLAGS[key] = value). Anything else can run code the handler cannot
see -- in this codebase obj.state = value is usually a property setter
which writes to the database and adds events -- so it is refused unless
its line carries a "# signal-safe: <reason>" waiver. An attribute of self
is refused the same way when the calling class, a base of it or a class
deriving from it defines that name as a property.

Every call must be one of:

* os.open, os.write or os.close: raw system calls, which take no Python
  lock. daemon.write_abort_file() uses these to create an abort file.
* A function in this repository decorated with
  shakenfist.util.signals.signal_safe. The decorator does nothing at run
  time; it marks the function for this tool, which checks its body by the
  same rules, so a helper which later starts logging is caught.
* A call on a line ending in a "# signal-safe: <reason>" comment, for a
  call this tool cannot see is safe -- for example Event.set() on an
  event the main thread never waits on. The reason is required, and is
  what a reviewer judges.
"""

import ast
import functools
import os
import re
import sys


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = 'shakenfist'
EXCLUDED_DIRS = ('tests',)

RAW_SYSCALLS = {('os', 'open'), ('os', 'write'), ('os', 'close')}
DECORATOR = 'signal_safe'
WAIVER_RE = re.compile(r'#\s*signal-safe:\s*\S')

ALLOWED_STATEMENTS = (
    ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr, ast.If, ast.Return,
    ast.Pass, ast.Raise, ast.Try, ast.Global, ast.Nonlocal)
REFUSED_EXPRESSIONS = (ast.Await, ast.Yield, ast.YieldFrom, ast.Lambda)


class Module:
    def __init__(self, path, root):
        self.path = path
        self.relpath = os.path.relpath(path, root)
        with open(path) as f:
            self.source = f.read()
        self.lines = self.source.splitlines()
        self.tree = ast.parse(self.source, path)
        self.parents = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent

        # Local name -> dotted module name, for modules imported whole.
        self.module_aliases = {}
        # Local name -> (dotted module name, name), for names imported from
        # a module.
        self.name_imports = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.module_aliases[alias.asname] = alias.name
                    else:
                        top = alias.name.split('.')[0]
                        self.module_aliases[top] = top
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    local = alias.asname or alias.name
                    self.name_imports[local] = (node.module, alias.name)

    def enclosing(self, node, kinds):
        node = self.parents.get(node)
        while node is not None and not isinstance(node, kinds):
            node = self.parents.get(node)
        return node

    def top_level_function(self, name):
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
        return None

    def classes(self):
        return [n for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef)]


def method_of(cls, name):
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def dotted(node):
    """('a', 'b', 'c') for a.b.c, or None for anything else."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return tuple(reversed(parts))


def is_signal_safe(func):
    for decorator in func.decorator_list:
        name = dotted(decorator)
        if name and name[-1] == DECORATOR:
            return True
    return False


class Checker:
    def __init__(self, root=None):
        self.root = root or REPO_ROOT
        self.modules = {}
        self.errors = []
        self.handlers = []
        self.checked = set()

    def module_for(self, dotted_name):
        path = os.path.join(self.root, *dotted_name.split('.')) + '.py'
        if not os.path.isfile(path):
            return None
        if path not in self.modules:
            self.modules[path] = Module(path, self.root)
        return self.modules[path]

    def all_modules(self):
        for dirpath, dirnames, filenames in os.walk(os.path.join(self.root, PACKAGE)):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS and d != '__pycache__')
            for filename in sorted(filenames):
                if filename.endswith('.py'):
                    path = os.path.join(dirpath, filename)
                    if path not in self.modules:
                        self.modules[path] = Module(path, self.root)
                    yield self.modules[path]

    def error(self, module, node, message):
        self.errors.append(f'{module.relpath}:{node.lineno}: {message}')

    @functools.cached_property
    def class_index(self):
        """Class name -> [(module, ClassDef)] for every class in the package."""
        index = {}
        for module in list(self.all_modules()):
            for c in module.classes():
                index.setdefault(c.name, []).append((module, c))
        return index

    def class_family(self, cls):
        """cls, the classes it derives from and the classes deriving from it, matched by name."""
        index = self.class_index
        family = {cls.name}
        pending = [cls]
        while pending:
            for base in pending.pop().bases:
                name = dotted(base)
                if name and name[-1] not in family:
                    family.add(name[-1])
                    pending.extend(c for _, c in index.get(name[-1], []))
        family |= self.derived_names(cls, index)
        return [c for name in sorted(family) for _, c in index.get(name, [])]

    def derived_names(self, cls, index):
        derived = {cls.name}
        changed = True
        while changed:
            changed = False
            for entries in index.values():
                for _, c in entries:
                    bases = {b[-1] for b in (dotted(base) for base in c.bases) if b}
                    if c.name not in derived and bases & derived:
                        derived.add(c.name)
                        changed = True
        return derived

    def is_property(self, module, func, name):
        """Whether self.name, assigned in func, may be a property of self."""
        cls = module.enclosing(func, ast.ClassDef)
        if cls is None:
            return False
        for c in self.class_family(cls):
            for node in c.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                    for decorator in node.decorator_list:
                        d = dotted(decorator)
                        if d and d[-1] in ('property', 'cached_property', 'setter', 'deleter'):
                            return True
        return False

    def subclass_overrides(self, cls, name):
        """Methods called name in classes deriving, by name, from cls."""
        index = self.class_index
        derived = self.derived_names(cls, index)
        overrides = []
        for class_name in sorted(derived - {cls.name}):
            for module, c in index[class_name]:
                method = method_of(c, name)
                if method:
                    overrides.append((module, method))
        return overrides

    def resolve_handler(self, module, call):
        handler = call.args[1] if len(call.args) > 1 else None
        if isinstance(handler, ast.Name):
            scope = module.enclosing(call, (ast.FunctionDef, ast.AsyncFunctionDef))
            if scope is not None:
                for node in ast.walk(scope):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == handler.id:
                        return [(module, node)]
            func = module.top_level_function(handler.id)
            if func:
                return [(module, func)]
            if handler.id in module.name_imports:
                source, name = module.name_imports[handler.id]
                other = self.module_for(source)
                func = other.top_level_function(name) if other else None
                if func:
                    return [(other, func)]
        elif dotted(handler) and len(dotted(handler)) == 2 and dotted(handler)[0] == 'self':
            cls = module.enclosing(call, ast.ClassDef)
            method = method_of(cls, dotted(handler)[1]) if cls else None
            if method:
                return [(module, method)] + self.subclass_overrides(cls, method.name)
        return None

    def find_handlers(self):
        for module in list(self.all_modules()):
            for node in ast.walk(module.tree):
                if not isinstance(node, ast.Call) or dotted(node.func) != ('signal', 'signal'):
                    continue
                if len(node.args) > 1 and dotted(node.args[1]) in (
                        ('signal', 'SIG_DFL'), ('signal', 'SIG_IGN')):
                    continue
                resolved = self.resolve_handler(module, node)
                if resolved is None:
                    self.error(module, node, 'signal handler cannot be found to check: use a named function '
                                             'or a method of this class')
                    continue
                for handler_module, handler in resolved:
                    self.handlers.append((handler_module, handler))
                    self.check_function(handler_module, handler, f'signal handler {handler.name}()')

    def resolve_call(self, module, call, func_node):
        """The (module, function) a call reaches, or None if it is not found."""
        name = dotted(call.func)
        if name is None:
            return None
        if len(name) == 1:
            scope = func_node
            while scope is not None:
                for node in ast.walk(scope):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name[0] \
                            and node is not func_node:
                        return module, node
                scope = module.enclosing(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
            func = module.top_level_function(name[0])
            if func:
                return module, func
            if name[0] in module.name_imports:
                source, imported = module.name_imports[name[0]]
                other = self.module_for(source)
                func = other.top_level_function(imported) if other else None
                if func:
                    return other, func
            return None
        if len(name) == 2 and name[0] == 'self':
            cls = module.enclosing(func_node, ast.ClassDef)
            method = method_of(cls, name[1]) if cls else None
            return (module, method) if method else None
        if len(name) == 2:
            target = module.module_aliases.get(name[0])
            if target is None and name[0] in module.name_imports:
                source, imported = module.name_imports[name[0]]
                target = f'{source}.{imported}'
            other = self.module_for(target) if target else None
            func = other.top_level_function(name[1]) if other else None
            return (other, func) if func else None
        return None

    def check_call(self, module, call, func_node, context):
        line = module.lines[call.lineno - 1]
        if WAIVER_RE.search(line):
            return
        name = dotted(call.func)
        if name and len(name) == 2 and module.module_aliases.get(name[0]) == 'os' \
                and ('os', name[1]) in RAW_SYSCALLS:
            return
        shown = '.'.join(name) if name else ast.unparse(call.func)
        target = self.resolve_call(module, call, func_node)
        if target is None or not is_signal_safe(target[1]):
            self.error(module, call, f'{context} calls {shown}(), which is not known to be safe in a signal '
                                     f'handler: decorate it with @signal_safe if it is, or mark the line '
                                     f'"# signal-safe: <reason>"')
            return
        self.check_function(target[0], target[1], f'@signal_safe {target[1].name}()')

    def check_function(self, module, func, context):
        key = (module.path, func.lineno)
        if key in self.checked:
            return
        self.checked.add(key)
        for statement in func.body:
            self.check_statement(module, statement, func, context)

    def check_target(self, module, target, func, context):
        if isinstance(target, ast.Name):
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self.check_target(module, element, func, context)
            return
        if isinstance(target, ast.Starred):
            self.check_target(module, target.value, func, context)
            return
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self' \
                and not self.is_property(module, func, target.attr):
            return
        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            return
        if WAIVER_RE.search(module.lines[target.lineno - 1]):
            return
        self.error(module, target, f'{context} assigns to {ast.unparse(target)}, which may run a property setter '
                                   f'or __setitem__: assign to a name, self.<name> or <name>[key], or mark the '
                                   f'line "# signal-safe: <reason>"')

    def check_statement(self, module, statement, func, context):
        if not isinstance(statement, ALLOWED_STATEMENTS):
            self.error(module, statement, f'{context} uses a {type(statement).__name__} statement, which a signal '
                                          f'handler may not')
            return
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                self.check_target(module, target, func, context)
        elif isinstance(statement, (ast.AugAssign, ast.AnnAssign)):
            self.check_target(module, statement.target, func, context)
        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.stmt):
                self.check_statement(module, child, func, context)
            elif isinstance(child, ast.excepthandler):
                for inner in child.body:
                    self.check_statement(module, inner, func, context)
            else:
                self.check_expression(module, child, func, context)

    def check_expression(self, module, expression, func, context):
        for node in ast.walk(expression):
            if isinstance(node, REFUSED_EXPRESSIONS):
                self.error(module, node, f'{context} uses a {type(node).__name__}, which a signal handler may not')
            elif isinstance(node, ast.Call):
                self.check_call(module, node, func, context)

    def run(self):
        self.find_handlers()
        return self.errors


def main():
    checker = Checker()
    errors = checker.run()
    for error in errors:
        print(error)
    if errors:
        print(f'\n{len(errors)} unsafe signal handler construct(s). See tools/check-signal-handlers.py for '
              f'what a handler may do.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
