# Copyright 2026 Michael Still and contributors
#
# Every operation schema whose current_version exceeds its
# initial_version must have every intervening
# ``_upgrade_step_N_to_N+1``, defined on either the schema module
# itself or the matching class in ``shakenfist/operations/``.
#
# ``shakenfist.baseobject.DatabaseBackedObject.upgrade()`` resolves each
# step with a bare ``getattr(self, step)`` and no default, so a missing
# step raises ``AttributeError`` rather than the documented
# ``UpgradeException`` -- unreachable code below that getattr was
# written to raise it. The window is narrow (cluster operations are
# hard deleted thirty seconds after going terminal, so only a rolling
# upgrade can present an old row) but real: survey finding 8 of
# docs/plans/PLAN-queue-performance-phase-11-multi-column-key.md found
# exactly one gap this way, NetOp's missing ``_upgrade_step_1_to_2``,
# by running the sweep below.

import ast
import glob
import os
import re
import tempfile

import shakenfist
from shakenfist.tests import base


def _defined_function_names(path):
    """Every function or method *defined* anywhere in one source file.

    Parsed with ``ast`` rather than matched as text. The whole point of
    the sweep is to catch a step which is referenced but never defined,
    so deciding "it exists" from a substring search is the wrong side
    of the question: a step named only in a comment, a docstring, an
    error message or the ``getattr`` string which looks it up would all
    satisfy it. Walking the tree counts definitions and nothing else,
    at any nesting depth, so a step on a class body is found without
    the sweep having to know which class.

    A file which does not parse returns nothing, so a syntax error
    surfaces as a missing step rather than as a silent pass. Returns an
    empty set for a file which does not exist, which is the common case
    for a schema with no matching module in ``shakenfist/operations/``.
    """
    if not os.path.exists(path):
        return set()
    try:
        tree = ast.parse(open(path).read(), filename=path)
    except SyntaxError:
        return set()
    return {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _find_missing_upgrade_steps(schema_operations_root, operations_root):
    """Every ``*_op.py`` schema with a version gap needs every step.

    Returns a list of ``(schema_filename, missing_step_name)`` pairs.
    Both roots are directories, so the caller decides whether to point
    this at the real source tree or at a throwaway one built to prove
    the sweep actually catches something.

    The version declarations are still matched as text, because they
    are module-level assignments of integer literals and reading them
    with ``ast`` would buy nothing. Whether a *step* exists is decided
    by ``_defined_function_names`` -- see there for why that one has to
    be a definition and not a mention.
    """
    missing = []
    for path in sorted(glob.glob(os.path.join(schema_operations_root, '*.py'))):
        src = open(path).read()
        lo = re.search(r'^initial_version = (\d+)', src, re.M)
        hi = re.search(r'^current_version = (\d+)', src, re.M)
        if not (lo and hi) or lo.group(1) == hi.group(1):
            continue

        # A step may live on the schema module itself or on the
        # matching class in shakenfist/operations/ -- NetOp's are on
        # the operation class, agentoperation.py's convention.
        defined = (
            _defined_function_names(path)
            | _defined_function_names(
                os.path.join(operations_root, os.path.basename(path))))

        for v in range(int(lo.group(1)), int(hi.group(1))):
            step = '_upgrade_step_%d_to_%d' % (v, v + 1)
            if step not in defined:
                missing.append((os.path.basename(path), step))
    return missing


class UpgradeStepSweepTestCase(base.ShakenFistTestCase):
    def _package_root(self):
        # Derived from the shakenfist package's own __file__ rather
        # than os.getcwd() or a fixed count of os.path.dirname() calls,
        # so the sweep is robust to being run from any working
        # directory -- stestr, tox, or an IDE runner started elsewhere.
        return os.path.dirname(os.path.abspath(shakenfist.__file__))

    def test_every_operation_schema_has_its_upgrade_steps(self):
        root = self._package_root()
        missing = _find_missing_upgrade_steps(
            os.path.join(root, 'schema', 'operations'),
            os.path.join(root, 'operations'))

        self.assertEqual(
            [], missing,
            f'operation schema(s) missing an upgrade step: {missing}. '
            f'DatabaseBackedObject.upgrade() calls getattr(self, step) '
            f'with no default, so a missing step raises AttributeError '
            f'instead of UpgradeException when a rolling upgrade '
            f'presents an older row.')

    def test_the_sweep_finds_a_real_gap(self):
        # Mutation coverage for the sweep itself: prove it actually
        # reports a version gap with no step anywhere, rather than only
        # ever returning an empty list because the real tree is clean
        # today.
        with tempfile.TemporaryDirectory() as tmp:
            schema_dir = os.path.join(tmp, 'schema', 'operations')
            operations_dir = os.path.join(tmp, 'operations')
            os.makedirs(schema_dir)
            os.makedirs(operations_dir)

            with open(os.path.join(schema_dir, 'fake_op.py'), 'w') as f:
                f.write('initial_version = 1\ncurrent_version = 2\n')
            # Deliberately no _upgrade_step_1_to_2 in either directory.

            self.assertEqual(
                [('fake_op.py', '_upgrade_step_1_to_2')],
                _find_missing_upgrade_steps(schema_dir, operations_dir))

    def test_the_sweep_accepts_a_step_on_either_side(self):
        # The step can live on the schema module or on the operation
        # class -- NetOp's steps are on the operation class
        # (shakenfist/operations/net_op.py), matching
        # agentoperation.py's convention, so the sweep has to look in
        # both places.
        with tempfile.TemporaryDirectory() as tmp:
            schema_dir = os.path.join(tmp, 'schema', 'operations')
            operations_dir = os.path.join(tmp, 'operations')
            os.makedirs(schema_dir)
            os.makedirs(operations_dir)

            with open(os.path.join(schema_dir, 'fake_op.py'), 'w') as f:
                f.write('initial_version = 1\ncurrent_version = 2\n')
            with open(os.path.join(operations_dir, 'fake_op.py'), 'w') as f:
                f.write(
                    'def _upgrade_step_1_to_2(cls, static_values):\n'
                    '    pass\n')

            self.assertEqual(
                [], _find_missing_upgrade_steps(schema_dir, operations_dir))

    def test_a_mention_is_not_a_definition(self):
        # The sweep exists to catch a step which is referenced and
        # never defined, so a file which only *names* the step -- in a
        # comment, a docstring, or the getattr string which looks it up
        # -- must still be reported missing. A substring search over
        # the file contents would pass every one of these.
        for body in (
                '# _upgrade_step_1_to_2 is still to be written\n',
                'DOC = "see _upgrade_step_1_to_2"\n',
                'step = getattr(self, "_upgrade_step_1_to_2")\n'):
            with tempfile.TemporaryDirectory() as tmp:
                schema_dir = os.path.join(tmp, 'schema', 'operations')
                operations_dir = os.path.join(tmp, 'operations')
                os.makedirs(schema_dir)
                os.makedirs(operations_dir)

                with open(os.path.join(schema_dir, 'fake_op.py'), 'w') as f:
                    f.write('initial_version = 1\ncurrent_version = 2\n')
                with open(
                        os.path.join(operations_dir, 'fake_op.py'), 'w') as f:
                    f.write(body)

                self.assertEqual(
                    [('fake_op.py', '_upgrade_step_1_to_2')],
                    _find_missing_upgrade_steps(schema_dir, operations_dir),
                    f'a mention rather than a definition was accepted: '
                    f'{body!r}')

    def test_a_step_on_a_class_body_is_found(self):
        # NetOp's steps are methods on the operation class, not module
        # level functions, so the ast walk has to find a definition at
        # any nesting depth rather than only at module scope.
        with tempfile.TemporaryDirectory() as tmp:
            schema_dir = os.path.join(tmp, 'schema', 'operations')
            operations_dir = os.path.join(tmp, 'operations')
            os.makedirs(schema_dir)
            os.makedirs(operations_dir)

            with open(os.path.join(schema_dir, 'fake_op.py'), 'w') as f:
                f.write('initial_version = 1\ncurrent_version = 2\n')
            with open(os.path.join(operations_dir, 'fake_op.py'), 'w') as f:
                f.write(
                    'class FakeOp:\n'
                    '    @classmethod\n'
                    '    def _upgrade_step_1_to_2(cls, static_values):\n'
                    '        pass\n')

            self.assertEqual(
                [], _find_missing_upgrade_steps(schema_dir, operations_dir))

    def test_a_missing_current_version_declaration_is_skipped(self):
        # A schema module with no version declarations at all (not
        # every schema in shakenfist/schema/ is an operation) must not
        # be misread as a version-0-to-0 gap.
        with tempfile.TemporaryDirectory() as tmp:
            schema_dir = os.path.join(tmp, 'schema', 'operations')
            operations_dir = os.path.join(tmp, 'operations')
            os.makedirs(schema_dir)
            os.makedirs(operations_dir)

            with open(os.path.join(schema_dir, 'no_version_op.py'), 'w') as f:
                f.write('object_type = None\n')

            self.assertEqual(
                [], _find_missing_upgrade_steps(schema_dir, operations_dir))
