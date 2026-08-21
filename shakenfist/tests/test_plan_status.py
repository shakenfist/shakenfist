# Copyright 2026 Michael Still and contributors

"""Tests for tools/check-plan-status.py.

The plan index publishes two derived quantities -- a whole-plan status, and
how many of that plan's phases are complete -- whose truth lives in each
master plan's own Execution table. Both drift the moment one is edited and
the other is not, and that drift is invisible: a wrong count still renders.
The checker recomputes them. It also enforces the invariant the plan
template now rests on, that a phase plan is reachable, since phase plans are
absent from `order.yml` and so from the site navigation, leaving the
master plan's Execution table as the only path to them. Thirty-eight of the
ninety-two phase plans had no inbound link at all when that check was
written.

The final test is the regression guard for `docs/plans/` itself. No workflow
runs pre-commit, so the hook alone would not catch a contributor who has not
installed it, nor the merge queue. The guard only closes that gap because
`functional-tests.yml` gates its unit test job on a `plans` paths-filter
output as well as `code`: a plan-only edit is docs-only, so it does not match
the `code` filter, and without that second output the job -- and this guard --
would skip on exactly the changes it exists to police.
"""

import importlib.util
import os
import shutil
import tempfile

from shakenfist.tests import base


def _load_checker():
    # The checker is a standalone script rather than an importable module,
    # because it is also a pre-commit hook entry point.
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    path = os.path.join(root, 'tools', 'check-plan-status.py')
    spec = importlib.util.spec_from_file_location('check_plan_status', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REPO_ROOT = root
    return module, root


checker, REPO_ROOT = _load_checker()


VOCABULARY_BLOCK = """<!-- shared-block: plan-status-vocabulary v1 -->
- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something changes.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan.
<!-- shared-block-end -->
"""

MASTER = """# A plan

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. One | [FIXTURE-plan-phase-01-one.md](FIXTURE-plan-phase-01-one.md) | Complete |
| 2. Two | [FIXTURE-plan-phase-02-two.md](FIXTURE-plan-phase-02-two.md) | Not started |
"""

INDEX = """# Plans

| Date | Plan | Intent | Status | Phases |
|------|------|--------|--------|--------|
| 2026-01-01 | [A plan](FIXTURE-plan.md) | Does a thing | In progress | 1 of 2 |
"""


class PlanStatusHelperTestCase(base.ShakenFistTestCase):
    def test_cells_splits_a_row(self):
        self.assertEqual(['a', 'b', 'c'],
                         checker.cells('| a | b | c |'))

    def test_cells_keeps_an_escaped_pipe_in_its_own_cell(self):
        # Splitting on every pipe would shift the status a column left, and
        # the checker would then blame the index for arithmetic that is
        # correct. Ten phase plans already quote LogQL this way.
        self.assertEqual(
            ['1. One', '`grep -E a|b`', 'Complete'],
            checker.cells(r'| 1. One | `grep -E a\|b` | Complete |'))

    def test_vocabulary_is_read_from_the_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'PLAN-TEMPLATE.md')
            with open(path, 'w') as f:
                f.write('preamble\n' + VOCABULARY_BLOCK + 'trailer\n')
            self.assertEqual(
                ['Proposed', 'Not started', 'In progress', 'Blocked',
                 'Complete', 'Abandoned', 'Superseded'],
                checker.vocabulary(path))

    def test_vocabulary_is_empty_without_the_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'PLAN-TEMPLATE.md')
            with open(path, 'w') as f:
                f.write('no block here\n')
            self.assertEqual([], checker.vocabulary(path))

    def test_execution_table_finds_the_status_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'FIXTURE-plan.md')
            with open(path, 'w') as f:
                f.write(MASTER)
            table = checker.execution_table(path)
            self.assertEqual(['Complete', 'Not started'],
                             [value for _, value in table['rows']])

    def test_execution_table_ignores_a_table_without_phases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'FIXTURE-plan.md')
            with open(path, 'w') as f:
                f.write('| Repo | Status |\n|------|--------|\n'
                        '| a | Complete |\n')
            self.assertIsNone(checker.execution_table(path))

    def test_execution_table_picks_the_longest(self):
        # A cross-repo plan tracks phases in a sibling repository too; the
        # plan's own table is the longer one.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'FIXTURE-plan.md')
            with open(path, 'w') as f:
                f.write('| Phase | Status |\n|-------|--------|\n'
                        '| 1 | Complete |\n\nprose\n\n'
                        + MASTER)
            self.assertEqual(2, len(checker.execution_table(path)['rows']))

    def test_index_rows_are_keyed_by_plan_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'index.md')
            with open(path, 'w') as f:
                f.write(INDEX)
            rows = checker.index_rows(path)
            self.assertEqual({'FIXTURE-plan.md'}, set(rows))
            _, status, phases = rows['FIXTURE-plan.md']
            self.assertEqual('In progress', status)
            self.assertEqual('1 of 2', phases)


class PlanStatusFixtureTestCase(base.ShakenFistTestCase):
    """The checker's verdicts, over a small tree we can break on purpose."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.plans = os.path.join(self.tmp, 'docs', 'plans')
        os.makedirs(self.plans)

        self._write('PLAN-TEMPLATE.md', VOCABULARY_BLOCK, root=True)
        self._write('FIXTURE-plan.md', MASTER)
        self._write('FIXTURE-plan-phase-01-one.md', '# One\n')
        self._write('FIXTURE-plan-phase-02-two.md', '# Two\n')
        self._write('index.md', INDEX)
        self._write('order.yml', '- index.md: Plans index\n'
                                 '- FIXTURE-plan.md: A plan\n')

        self.original_root = checker.REPO_ROOT
        checker.REPO_ROOT = self.tmp
        self.addCleanup(setattr, checker, 'REPO_ROOT', self.original_root)

    def _write(self, name, content, root=False):
        path = os.path.join(self.tmp if root else self.plans, name)
        with open(path, 'w') as f:
            f.write(content)

    def test_a_consistent_tree_passes(self):
        self.assertEqual([], checker.problems())

    def assertProblem(self, fragment):
        """Exactly one problem, and it is the one the test is about.

        Asserting on the exit code alone lets a test pass because some
        *other* rule fired: breaking a status usually moves the arithmetic
        too, so a vocabulary test written that way survives the vocabulary
        check being deleted.
        """
        found = checker.problems()
        matched = [p for p in found if fragment in p]
        self.assertEqual(
            1, len(matched),
            'expected one problem containing %r, got %r' % (fragment, found))

    def test_a_status_outside_the_vocabulary_fails(self):
        # Mutating the *unfinished* phase, so the completed count does not
        # move and only the vocabulary rule can fire.
        self._write('FIXTURE-plan.md',
                    MASTER.replace('| Not started |', '| Done |'))
        self.assertProblem("phase status 'Done' is not one of")

    def test_wrong_arithmetic_fails(self):
        self._write('index.md', INDEX.replace('1 of 2', '2 of 2'))
        self.assertProblem("says '2 of 2', but")

    def test_complete_with_an_unfinished_phase_fails(self):
        self._write('index.md', INDEX.replace('| In progress |', '| Complete |'))
        self.assertProblem('is Complete, but')

    def test_not_started_with_every_phase_resolved_fails(self):
        self._write('FIXTURE-plan.md',
                    MASTER.replace('| Not started |', '| Complete |'))
        self._write('index.md', INDEX.replace('| In progress | 1 of 2 |',
                                              '| Not started | 2 of 2 |'))
        self.assertProblem('every phase in')

    def test_an_unregistered_master_plan_fails(self):
        # A plan missing from the index is normally missing from order.yml
        # too, so both rules fire; this asserts the index one specifically.
        self._write('FIXTURE-other.md', '# Other\n')
        self.assertProblem('is not registered in %s'
                           % os.path.join(self.plans, 'index.md'))

    def test_a_master_plan_missing_from_order_fails(self):
        self._write('order.yml', '- index.md: Plans index\n')
        self.assertProblem('order.yml')

    def test_an_unlinked_phase_plan_fails(self):
        # The regression this check exists for: a bare filename in the
        # Execution table is a page with no way in.
        self._write('FIXTURE-plan.md', MASTER.replace(
            '[FIXTURE-plan-phase-02-two.md](FIXTURE-plan-phase-02-two.md)',
            'FIXTURE-plan-phase-02-two.md'))
        self.assertProblem('has no inbound link')

    def test_a_link_to_a_missing_file_fails(self):
        self._write('FIXTURE-plan.md', MASTER.replace(
            '(FIXTURE-plan-phase-02-two.md)', '(FIXTURE-plan-phase-99-gone.md)'))
        self.assertProblem('which does not exist')

    def test_a_missing_vocabulary_block_fails(self):
        self._write('PLAN-TEMPLATE.md', 'nothing here\n', root=True)
        self.assertProblem('carries no plan-status-vocabulary shared block')


class PlanStatusRegressionTestCase(base.ShakenFistTestCase):
    def test_the_real_plans_directory_is_consistent(self):
        # The guard for docs/plans/ itself, and the reason this file exists:
        # no workflow runs pre-commit, so the hook alone does not cover a
        # contributor who has not installed it, nor the merge queue.
        original = checker.REPO_ROOT
        checker.REPO_ROOT = REPO_ROOT
        try:
            self.assertEqual([], checker.problems())
        finally:
            checker.REPO_ROOT = original
