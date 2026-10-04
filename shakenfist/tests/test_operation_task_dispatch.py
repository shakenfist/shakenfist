# Copyright 2026 Michael Still and contributors
"""Every operation task handler must exist, because it is found by name.

Commit 216fa31dd deleted ``NodeInstOp._health_check_kvm_process()`` on the
belief that nothing called it -- a grep for the method name found no call
site. In fact ``NodeInstOp.dispatch_task()`` calls it by constructing the
name at runtime from the enqueued task:
``self.__getattribute__(f'_{task.name}')(inst)``. A plain grep for
``_health_check_kvm_process`` cannot see that call, so the "uncalled"
method was in fact dispatched for every created instance on every
instance-check cycle, and deleting it turned every such dispatch into an
``AttributeError`` that drove the instance to ``created-error``.

This module scans every file under ``shakenfist/operations/`` for that
same by-name dispatch pattern, and for every operation class written that
way, asserts that every member of the ``model_tasks`` enum it dispatches
over has a matching ``_<task name>`` callable. This is the check a grep
cannot do, and it is the one that would have caught the regression above.
"""
import glob
import os
import re

from shakenfist.operations import baseoperation
from shakenfist.tests import base


# The source pattern every by-task-name dispatcher uses (see dispatch_task()
# in any of the operation modules discovered below), plus the getattr()
# spelling of the same lookup, so a dispatcher written that way is not
# silently skipped.
_DISPATCH_PATTERN = re.compile(
    r"""(self\.__getattribute__\(|getattr\(\s*self\s*,\s*)f['"]_\{task\.name\}['"]""")

# The number of dispatching modules when this test was written. Discovery
# finding fewer means the pattern above has drifted from the code, which
# would silently shrink what this test checks, so that fails too.
_MINIMUM_DISPATCH_MODULES = 14


def _discover_dispatch_modules():
    """Return the module names of every operation that dispatches by task name.

    Discovered by scanning source rather than hard-coding a list, so a
    new operation module that adopts the same pattern is covered
    automatically.
    """
    operations_dir = os.path.dirname(baseoperation.__file__)
    module_names = []

    for path in sorted(glob.glob(os.path.join(operations_dir, '*.py'))):
        basename = os.path.basename(path)
        if basename == '__init__.py':
            continue

        with open(path) as f:
            source = f.read()

        if _DISPATCH_PATTERN.search(source):
            module_names.append('shakenfist.operations.' + basename[:-len('.py')])

    return module_names


def _dispatch_classes(module):
    """Yield the classes in ``module`` that dispatch tasks by name.

    A dispatching operation class is a ``BaseClusterOperation`` subclass
    defined in this module (not merely imported into it) that also
    defines ``dispatch_task`` itself, so helper base classes are not
    mistaken for dispatchers.
    """
    for name in dir(module):
        obj = getattr(module, name)
        if (isinstance(obj, type) and
                obj.__module__ == module.__name__ and
                issubclass(obj, baseoperation.BaseClusterOperation) and
                'dispatch_task' in vars(obj)):
            yield obj


class OperationTaskDispatchTestCase(base.ShakenFistTestCase):
    def test_discovery_is_non_empty_and_includes_node_inst_op(self):
        discovered = _discover_dispatch_modules()
        self.assertGreaterEqual(
            len(discovered), _MINIMUM_DISPATCH_MODULES,
            f'only found {len(discovered)} dispatching modules: {discovered}')
        self.assertIn('shakenfist.operations.node_inst_op', discovered)

    def test_every_dispatched_task_has_a_handler(self):
        import importlib

        discovered = _discover_dispatch_modules()
        self.assertNotEqual([], discovered)

        checked_any_class = False
        for module_name in discovered:
            module = importlib.import_module(module_name)
            schema = getattr(module, 'schema', None)
            self.assertIsNotNone(
                schema,
                f'{module_name} dispatches tasks by name but has no '
                'module-level "schema" import to read model_tasks from')

            model_tasks = getattr(schema, 'model_tasks', None)
            self.assertIsNotNone(
                model_tasks,
                f'{module_name}.schema has no model_tasks enum')

            for cls in _dispatch_classes(module):
                checked_any_class = True
                for task in model_tasks:
                    handler_name = f'_{task.name}'
                    with self.subTest(module=module_name, cls=cls.__name__,
                                      task=task.name):
                        handler = getattr(cls, handler_name, None)
                        self.assertTrue(
                            callable(handler),
                            f'{cls.__module__}.{cls.__name__} has no '
                            f'callable {handler_name}() for task '
                            f'{schema.__name__}.model_tasks.{task.name}, '
                            'but dispatch_task() looks it up by name')

        self.assertTrue(checked_any_class)
