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

import glob
import os
import re
import tempfile

import shakenfist
from shakenfist.tests import base


def _find_missing_upgrade_steps(schema_operations_root, operations_root):
    """Every ``*_op.py`` schema with a version gap needs every step.

    Returns a list of ``(schema_filename, missing_step_name)`` pairs.
    Both roots are directories, so the caller decides whether to point
    this at the real source tree or at a throwaway one built to prove
    the sweep actually catches something.
    """
    missing = []
    for path in sorted(glob.glob(os.path.join(schema_operations_root, '*.py'))):
        src = open(path).read()
        lo = re.search(r'^initial_version = (\d+)', src, re.M)
        hi = re.search(r'^current_version = (\d+)', src, re.M)
        if not (lo and hi) or lo.group(1) == hi.group(1):
            continue

        op_path = os.path.join(operations_root, os.path.basename(path))
        op_src = open(op_path).read() if os.path.exists(op_path) else ''

        for v in range(int(lo.group(1)), int(hi.group(1))):
            step = '_upgrade_step_%d_to_%d' % (v, v + 1)
            if step not in op_src and step not in src:
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
