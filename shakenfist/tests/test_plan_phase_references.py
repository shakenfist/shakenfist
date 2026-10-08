# Copyright 2026 Michael Still and contributors

"""Tests for tools/check-plan-phase-references.py.

Documentation describes the current state of the software, not the
history of how it was built, so it must not cite the phase numbers of
implementation plans (the plan-phase-references consistency audit).
Six such references accumulated on one page before the fleet-wide audit
reported them, because nothing here checked; the checker closes that
gap at commit time, these tests keep it honest, and the final test is
the regression guard for the documentation itself.
"""

import importlib.util
import os
import tempfile

from shakenfist.tests import base


def _load_checker():
    # The checker is a standalone script rather than an importable module,
    # because it is also a pre-commit hook entry point.
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    path = os.path.join(root, 'tools', 'check-plan-phase-references.py')
    spec = importlib.util.spec_from_file_location(
        'check_plan_phase_references', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


class PlanPhaseReferenceFixtureTestCase(base.ShakenFistTestCase):
    def _write(self, tmp, name, content):
        path = os.path.join(tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def test_a_clean_tree_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/guide.md',
                        'The binding guard is an atomic UPDATE.\n')
            self.assertEqual([], checker.problems(root_dir=tmp))

    def test_a_phase_reference_is_reported_with_its_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/guide.md',
                        'Fine prose.\n\nSince phase 3 the guard binds.\n')
            problems = checker.problems(root_dir=tmp)
            self.assertEqual(1, len(problems))
            self.assertIn('guide.md:3', problems[0])
            self.assertIn("'phase 3'", problems[0])

    def test_the_match_is_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/guide.md', "Phase 5's window read low.\n")
            self.assertEqual(1, len(checker.problems(root_dir=tmp)))

    def test_a_lettered_sub_phase_is_reported(self):
        # The audit's own pattern carries a trailing word boundary, so
        # "phase 4a" slips past it -- and did, in the operator guide. The
        # checker is deliberately looser.
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/guide.md',
                        'Until scheduler-reservations phase 4a the clause\n')
            self.assertEqual(1, len(checker.problems(root_dir=tmp)))

    def test_root_files_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/guide.md', 'Fine.\n')
            self._write(tmp, 'AGENTS.md', 'Built in phase 2 of the plan.\n')
            problems = checker.problems(root_dir=tmp)
            self.assertEqual(1, len(problems))
            self.assertIn('AGENTS.md', problems[0])

    def test_plans_directories_are_excluded_at_any_depth(self):
        # Plan documents legitimately discuss their own phases, including
        # the plans synchronised in under docs/components/.
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/plans/PLAN-thing.md',  # audit-ok: plan-reference
                        'Phase 1 lands the schema.\n')
            self._write(tmp, 'docs/other/plans/PLAN-deep.md',  # audit-ok: plan-reference
                        'Phase 2 lands the rest.\n')
            self.assertEqual([], checker.problems(root_dir=tmp))

    def test_components_are_excluded(self):
        # An automated import of the sibling repositories' documentation:
        # a finding there is fixed at the source, not here.
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/components/other/guide.md',
                        'Phase 6 added the bridge lifecycle.\n')
            self.assertEqual([], checker.problems(root_dir=tmp))

    def test_fenced_code_blocks_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/guide.md',
                        'Prose.\n\n```\ngrep "phase 1" docs/plans/\n```\n')
            self.assertEqual([], checker.problems(root_dir=tmp))

    def test_inline_code_spans_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/guide.md',
                        'The event is logged as `phase 1 complete`.\n')
            self.assertEqual([], checker.problems(root_dir=tmp))

    def test_a_reference_beside_an_inline_code_span_is_still_reported(self):
        # Stripping the span must not hide the rest of the line.
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/guide.md',
                        '`sf-ctl` grew this in phase 2 of the plan.\n')
            self.assertEqual(1, len(checker.problems(root_dir=tmp)))

    def test_the_audit_ok_marker_suppresses_a_line(self):
        # For the rare line where "phase <number>" is genuinely not a plan
        # reference.
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/guide.md',
                        'Three-phase 415V supply. '
                        '<!-- audit-ok: phase-reference -->\n')
            self.assertEqual([], checker.problems(root_dir=tmp))

    def test_the_word_phase_alone_is_not_a_reference(self):
        # "the last phase of every master plan" describes no number, and
        # AGENTS.md says exactly that today.
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/guide.md',
                        'The last phase of every plan is the audit, and a '
                        'phased rollout is fine.\n')
            self.assertEqual([], checker.problems(root_dir=tmp))


class PlanPhaseReferenceRegressionTestCase(base.ShakenFistTestCase):
    def test_the_shipped_documentation_is_clean(self):
        docs = os.path.join(checker.REPO_ROOT, 'docs')
        if not os.path.isdir(docs):
            self.skipTest('documentation tree is not present')

        problems = checker.problems()
        self.assertEqual(
            [], problems,
            'Plan phase references in documentation:\n' + '\n'.join(problems))
