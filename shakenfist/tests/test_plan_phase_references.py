# Copyright 2026 Michael Still and contributors
# audit-ok: phase-reference-file -- this suite tests the plan tooling, so its fixtures are made of plan phase
# numbers and must be written that way.

"""Tests for tools/check-plan-phase-references.py.

Documentation describes the current state of the software, not the
history of how it was built, so it must not cite the phase numbers of
implementation plans (the plan-phase-references consistency audit).
Six such references accumulated on one page before the fleet-wide audit
reported them, because nothing here checked; the checker closes that
gap at commit time, these tests keep it honest, and the final test is
the regression guard for the documentation itself.
"""

import contextlib
import importlib.util
import io
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


class PlanHistoryInSourceFixtureTestCase(base.ShakenFistTestCase):
    """The source side of the same rule (the plan-history-in-source audit).

    A comment saying a guard was added in a numbered phase, or that a
    limit is what it is per a numbered decision, records how the code
    came to be and not why it is the way it is -- and the reader of the
    code has not read the plan, which is often finished or archived.
    """

    def _write(self, tmp, name, content):
        path = os.path.join(tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def _problems(self, name, content):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, name, content)
            return checker.source_problems(root_dir=tmp)

    def test_clean_source_passes(self):
        self.assertEqual([], self._problems(
            'shakenfist/thing.py',
            '# The guard sits where the exception is raised.\n'))

    def test_a_phase_citation_is_reported_with_its_line(self):
        problems = self._problems(
            'shakenfist/thing.py',
            '# A comment.\n# The branch landed in phase 3.\n')
        self.assertEqual(1, len(problems))
        self.assertIn('thing.py:2', problems[0])
        self.assertIn("'phase 3'", problems[0])

    def test_the_three_extra_source_shapes_are_reported(self):
        for citation in ('the phase-4 leaks pass', 'per decision 6.4',
                         'pending step 5f'):
            self.assertEqual(
                1, len(self._problems('shakenfist/thing.py',
                                      f'# {citation}\n')),
                citation)

    def test_a_bare_numbered_step_is_not_a_citation(self):
        # "step 1: open the file" is ordinary procedural prose, so only
        # the lettered form is read as a plan's.
        self.assertEqual([], self._problems(
            'shakenfist/thing.py', '# step 1: open the file\n'))

    def test_an_unnumbered_phase_is_not_a_citation(self):
        self.assertEqual([], self._problems(
            'shakenfist/thing.py', '# A two-phase commit, in phases.\n'))

    def test_a_quoted_shape_is_ignored(self):
        # A comment may say what shape it is talking about, in single or
        # double backticks.
        self.assertEqual([], self._problems(
            'shakenfist/thing.py',
            '# The nav entry reads `phase 3`, and so does ``phase 4a``.\n'))

    def test_a_shape_beside_a_quoted_one_is_still_reported(self):
        self.assertEqual(1, len(self._problems(
            'shakenfist/thing.py',
            '# `sf-ctl` grew this in phase 2 of the plan.\n')))

    def test_a_url_is_an_address_not_prose(self):
        self.assertEqual([], self._problems(
            'shakenfist/thing.py',
            '# See https://example.com/PLAN-thing/phase-4/notes\n'))

    def test_a_plan_filename_is_left_to_the_other_check(self):
        # A phase-per-file plan carries the number in its name, and an
        # unbuilt behaviour may legitimately name the plan which will
        # build it. Whether the file resolves is a different audit's.
        self.assertEqual([], self._problems(
            'shakenfist/thing.py',
            '# See docs/plans/PLAN-thing-phase-04-guard.md.\n'))

    def test_the_line_marker_suppresses_a_line(self):
        self.assertEqual([], self._problems(
            'shakenfist/thing.py',
            "# A three-phase 415V supply.  # audit-ok: phase-reference\n"))

    def test_the_file_marker_suppresses_the_whole_file(self):
        self.assertEqual([], self._problems(
            'shakenfist/thing.py',
            '# audit-ok: phase-reference-file -- fixtures.\n'
            '# Landed in phase 3, and in decision 2.\n'))

    def test_mkdocs_is_skipped(self):
        # A plan with a page per phase puts a nav entry per phase in it.
        self.assertEqual([], self._problems(
            'mkdocs.yml', '    - "Phase 3: the guard": plans/p3.md\n'))

    def test_markdown_and_plans_directories_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/guide.md', 'Phase 3 landed the guard.\n')
            self._write(tmp, 'docs/plans/helper.py',
                        '# Phase 3 landed the guard.\n')
            self._write(tmp, 'docs/components/other/plans/h.py',
                        '# Phase 4 landed the rest.\n')
            self.assertEqual([], checker.source_problems(root_dir=tmp))

    def test_a_citation_split_by_a_line_wrap_is_reported(self):
        # The fleet audit reads one line at a time and cannot see these.
        # Seven were in this repository while it reported one reference.
        problems = self._problems(
            'shakenfist/thing.py',
            '        """A docstring.\n'
            '\n'
            '        This is a warning rather than an error. Phase\n'
            '        5 turns it into a refusal.\n'
            '        """\n')
        self.assertEqual(1, len(problems))
        self.assertIn('thing.py:3', problems[0])
        self.assertIn("'Phase 5'", problems[0])

    def test_a_wrapped_bare_step_is_still_not_a_citation(self):
        self.assertEqual([], self._problems(
            'shakenfist/thing.py',
            '# This is the table the sweep publishes, and steps\n'
            '# 2 and 3 act on it.\n'))

    def test_a_wrapped_lettered_step_is_reported(self):
        self.assertEqual(1, len(self._problems(
            'shakenfist/thing.py',
            '# Not reproducible from the README step\n'
            '# 2d is required to write.\n')))

    def test_a_wrapped_lettered_phase_is_reported(self):
        # The phase shape carries no trailing word boundary, so a
        # sub-phase matches wrapped as well as whole.
        self.assertEqual(1, len(self._problems(
            'shakenfist/thing.py',
            '# Check-then-charge, as the real clause does since phase\n'
            '# 4a: the charge lands for the next decision.\n')))

    def test_a_binary_file_is_not_read_as_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'thing.bin')
            with open(path, 'wb') as f:
                f.write(b'phase 3\x00phase 4')
            self.assertEqual([], checker.source_problems(root_dir=tmp))


class PlanHistoryInSourceRegressionTestCase(base.ShakenFistTestCase):
    def test_the_shipped_source_is_clean(self):
        problems = checker.source_problems()
        self.assertEqual(
            [], problems,
            'Plan history references in source:\n' + '\n'.join(problems))


class PlanCitationAdvisoryFixtureTestCase(base.ShakenFistTestCase):
    """The advisory Python pass.

    A decision cited by its shorthand (`D13`) and a pointer to a plan
    file are plan history as much as `decision 13` is, but several
    hundred are already in the tree, so they are reported without
    failing the commit and fixed in the files a change touches.
    """

    def _write(self, tmp, name, content):
        path = os.path.join(tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def _problems(self, name, content):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, name, content)
            return checker.advisory_problems(root_dir=tmp)

    def test_clean_source_passes(self):
        self.assertEqual([], self._problems(
            'shakenfist/thing.py',
            '# The guard sits where the exception is raised.\n'))

    def test_a_decision_shorthand_in_a_comment_is_reported(self):
        problems = self._problems(
            'shakenfist/thing.py',
            '# A comment.\n# The ceiling is advisory (D16).\n')
        self.assertEqual(1, len(problems))
        self.assertIn('thing.py:2', problems[0])
        self.assertIn("'D16'", problems[0])

    def test_a_decision_shorthand_in_a_docstring_is_reported_at_its_line(self):
        # A docstring is one token spanning many lines, so the citation
        # has to be reported where it is written.
        problems = self._problems(
            'shakenfist/thing.py',
            'def guard():\n'
            '    """One line.\n'
            '\n'
            '    The clause the node guard applies, per D13.\n'
            '    """\n')
        self.assertEqual(1, len(problems))
        self.assertIn('thing.py:4', problems[0])

    def test_a_citation_in_an_fstring_is_reported(self):
        # Python 3.12 stopped emitting an f-string as one STRING token.
        problems = self._problems(
            'shakenfist/thing.py',
            "def thing(node):\n"
            "    return f'{node} refused per D13'\n")
        self.assertEqual(1, len(problems))
        self.assertIn('thing.py:2', problems[0])

    def test_a_plan_filename_is_reported(self):
        problems = self._problems(
            'shakenfist/thing.py',
            '# Deferred; see docs/plans/PLAN-thing-phase-04-guard.md.\n')
        self.assertEqual(1, len(problems))
        self.assertIn("'PLAN-thing-phase-04-guard.md'", problems[0])

    def test_a_citation_in_code_rather_than_prose_is_not_reported(self):
        # This is what reading prose rather than lines buys: in code a
        # `D13` is a name somebody chose, and the shape is unusable
        # without the distinction.
        self.assertEqual([], self._problems(
            'shakenfist/thing.py', 'D13 = 4\nvalue = D13 + 1\n'))

    def test_a_definition_name_carrying_a_citation_is_reported(self):
        for name in ('test_cluster_guard_is_d14_shape',
                     'test_phase_5_refuses', 'test_step_5f_waits',
                     '_decision_3_limit'):
            problems = self._problems(
                'shakenfist/thing.py',
                f'class {name}:\n    pass\n')
            self.assertEqual(1, len(problems), name)
            self.assertIn(repr(name), problems[0])

    def test_an_ordinary_definition_name_is_not_reported(self):
        # The last two are the pattern's deliberate limit: it anchors on
        # underscores, so a run-together CamelCase citation is missed.
        # Every one in the tree is a snake_case test name.
        for name in ('test_ipv6_address_parses', 'test_d_is_not_a_decision',
                     'Instance', 'PhaseD3Guard', 'D14Guard'):
            self.assertEqual([], self._problems(
                'shakenfist/thing.py', f'class {name}:\n    pass\n'), name)

    def test_the_line_marker_suppresses_a_line(self):
        self.assertEqual([], self._problems(
            'shakenfist/thing.py',
            '# Deferred; see PLAN-thing.md.  # audit-ok: plan-reference\n'))

    def test_the_phase_reference_line_marker_also_suppresses_a_line(self):
        self.assertEqual([], self._problems(
            'shakenfist/thing.py',
            '# Model D13 of the pump.  # audit-ok: phase-reference\n'))

    def test_the_file_marker_suppresses_the_whole_file(self):
        self.assertEqual([], self._problems(
            'shakenfist/thing.py',
            '# audit-ok: plan-reference-file -- fixtures.\n'
            '# See PLAN-thing.md, per D13.\n'))

    def test_only_python_is_read(self):
        # The blocking pass reads every tracked file; this one needs a
        # tokeniser and an abstract syntax tree.
        self.assertEqual([], self._problems(
            'deploy/thing.yaml', '# Deferred; see PLAN-thing.md, per D13.\n'))

    def test_a_plans_directory_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'docs/plans/helper.py',
                        '# See PLAN-thing.md, per D13.\n')
            self.assertEqual([], checker.advisory_problems(root_dir=tmp))

    def test_unparseable_python_is_skipped_rather_than_crashing(self):
        # A file mid-conflict or written for another interpreter must not
        # take the hook down with it.
        self.assertEqual([], self._problems(
            'shakenfist/thing.py', 'def (((:\n'))


class PlanCitationAdvisoryRegressionTestCase(base.ShakenFistTestCase):
    def test_the_advisory_backlog_does_not_fail_the_checker(self):
        # The point of the advisory pass: the tree carries hundreds of
        # these citations, and the checker still exits zero so that they
        # are worked off file by file rather than in one sweep. The
        # blocking passes stay blocking, which the two regression tests
        # above cover.
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            status = checker.main(argv=[])
        self.assertEqual(0, status)
        self.assertIn('advisory plan citation', printed.getvalue())
        self.assertIn('do not fail the commit', printed.getvalue())
