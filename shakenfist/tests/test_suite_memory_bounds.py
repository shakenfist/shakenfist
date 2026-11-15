# Copyright 2026 Michael Still and contributors

"""The unit test suite polices its own memory use.

A stestr worker runs many tests in one long-lived process, and nothing
bounded how much it could allocate: a leaking test passed, the worker
carried the leak forward, and one reached 16.3 GB resident plus 5.9 GB
of swap before a human killed it (issue #4928). Two things now stop
that -- ``tools/bounded-test-run.sh`` bounds the run, and
``shakenfist/tests/memory.py`` names the test which grew the worker.

Both are tested here, including the wiring: a guard which is correct but
not reached from tox.ini protects nothing, and the only full-suite runs
anybody makes go through tox.
"""

import os
import re
import resource
import subprocess
import sys
from unittest import mock

import fixtures
import testtools

from shakenfist.tests import base
from shakenfist.tests import memory


REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
WRAPPER = os.path.join(REPO_ROOT, 'tools', 'bounded-test-run.sh')
TOX_INI = os.path.join(REPO_ROOT, 'tox.ini')
MEGABYTE = 1024 * 1024
RLIMIT_PROBE = 'import resource; print(resource.getrlimit(resource.RLIMIT_DATA))'

# A `stestr run` in a tox.ini command line, and the wrapper it has to be
# reached through.
STESTR_RUN_RE = re.compile(r'^(?!\s*#).*\bstestr\s+run\b')
WRAPPER_RE = re.compile(r'bash tools/bounded-test-run\.sh stestr run')


class _UnlatchedCeiling(fixtures.Fixture):
    """Clear the once-per-worker ceiling latch for one test."""

    def _setUp(self):
        self.useFixture(
            fixtures.MockPatchObject(memory, '_ceiling_reported', False))


def _touch(megabytes):
    """A bytearray with a byte written in every page, so it is resident."""
    held = bytearray(megabytes * MEGABYTE)
    held[::4096] = b'x' * len(held[::4096])
    return held


class ResidentBytesTestCase(base.ShakenFistTestCase):
    def test_a_reading_is_a_plausible_size(self):
        resident = memory.resident_bytes()
        self.assertIsNotNone(resident)
        # A CPython process which has imported this suite is well over a
        # megabyte and nowhere near a terabyte.
        self.assertGreater(resident, MEGABYTE)
        self.assertLess(resident, 1024 * 1024 * MEGABYTE)

    def test_an_allocation_moves_the_reading(self):
        before = memory.resident_bytes()
        held = _touch(64)
        self.assertGreater(memory.resident_bytes() - before, 32 * MEGABYTE)
        del held

    def test_an_unreadable_proc_reads_as_unknown(self):
        # Not every platform has /proc, and a reading which cannot be
        # taken must not fail the test it was taken for.
        self.assertIsNone(
            memory.resident_bytes(path='/proc/self/no-such-thing'))

    def test_a_malformed_proc_reads_as_unknown(self):
        self.assertIsNone(memory.resident_bytes(path=os.devnull))


class LimitParsingTestCase(base.ShakenFistTestCase):
    def test_an_unset_variable_is_the_default_not_disabled(self):
        self.assertEqual(memory.DEFAULT_GROWTH_LIMIT_MB * MEGABYTE,
                         memory.growth_limit_bytes({}))
        self.assertEqual(memory.DEFAULT_CEILING_MB * MEGABYTE,
                         memory.ceiling_bytes({}))

    def test_an_explicit_value_is_honoured(self):
        self.assertEqual(
            17 * MEGABYTE,
            memory.growth_limit_bytes({'SF_TEST_MEMORY_GROWTH_MB': ' 17 '}))
        self.assertEqual(
            18 * MEGABYTE,
            memory.ceiling_bytes({'SF_TEST_MEMORY_CEILING_MB': '18'}))

    def test_none_and_zero_disable_the_limit(self):
        for value in ('none', '0', '-1'):
            self.assertIsNone(
                memory.growth_limit_bytes(
                    {'SF_TEST_MEMORY_GROWTH_MB': value}),
                'SF_TEST_MEMORY_GROWTH_MB=%s should disable the limit'
                % value)
            self.assertIsNone(
                memory.ceiling_bytes({'SF_TEST_MEMORY_CEILING_MB': value}),
                'SF_TEST_MEMORY_CEILING_MB=%s should disable the limit'
                % value)

    def test_a_typo_falls_back_to_the_default_rather_than_off(self):
        # A guard a typo silently switches off is not a guard.
        self.assertEqual(
            memory.DEFAULT_GROWTH_LIMIT_MB * MEGABYTE,
            memory.growth_limit_bytes({'SF_TEST_MEMORY_GROWTH_MB': '256MB'}))

    def test_the_defaults_leave_room_for_the_suite_as_it_stands(self):
        # The worst-behaved test in the suite grew a worker by 63MB, and
        # a worker peaked at about 225MB. A threshold without margin over
        # those is a flake generator, not a guard.
        self.assertGreater(memory.DEFAULT_GROWTH_LIMIT_MB, 2 * 63)
        self.assertGreater(memory.DEFAULT_CEILING_MB, 2 * 225)
        self.assertGreater(memory.DEFAULT_CEILING_MB,
                           memory.DEFAULT_GROWTH_LIMIT_MB)


class ProblemSinceTestCase(base.ShakenFistTestCase):
    def test_a_test_within_both_bounds_is_no_problem(self):
        self.assertIsNone(memory.problem_since(
            memory.resident_bytes(),
            {'SF_TEST_MEMORY_GROWTH_MB': '512',
             'SF_TEST_MEMORY_CEILING_MB': '65536'}))

    def test_growth_over_the_limit_names_the_growth(self):
        # The fabricated starting point is far below the real one on
        # purpose. problem_since() re-reads after a gc.collect(), and
        # that collect can hand tens of megabytes back to the kernel --
        # which is the whole reason it is there, and which made an
        # earlier 64MB version of this test fail under coverage.
        problem = memory.problem_since(
            memory.resident_bytes() - 4096 * MEGABYTE,
            {'SF_TEST_MEMORY_GROWTH_MB': '1',
             'SF_TEST_MEMORY_CEILING_MB': '1048576'})
        self.assertIn('grew the worker', problem)
        self.assertIn('SF_TEST_MEMORY_GROWTH_MB', problem)

    def _over_the_ceiling(self):
        # Growth is within its limit, so only the ceiling is breached.
        return memory.problem_since(
            memory.resident_bytes(),
            {'SF_TEST_MEMORY_GROWTH_MB': '512',
             'SF_TEST_MEMORY_CEILING_MB': '1'})

    def test_a_worker_over_the_ceiling_says_it_may_be_a_victim(self):
        self.useFixture(_UnlatchedCeiling())
        problem = self._over_the_ceiling()
        self.assertIn('not necessarily', problem)
        self.assertIn('SF_TEST_MEMORY_CEILING_MB', problem)

    def test_the_ceiling_is_reported_once_per_worker(self):
        # A worker over the ceiling stays over it, so every test after
        # the first would otherwise fail for the same reason and bury
        # the one reading which locates the growth.
        self.useFixture(_UnlatchedCeiling())
        self.assertIsNotNone(self._over_the_ceiling())
        self.assertIsNone(self._over_the_ceiling())

    def test_both_limits_disabled_reports_nothing(self):
        self.assertIsNone(memory.problem_since(
            0, {'SF_TEST_MEMORY_GROWTH_MB': 'none',
                'SF_TEST_MEMORY_CEILING_MB': 'none'}))

    def test_an_unknown_starting_point_reports_nothing(self):
        self.assertIsNone(memory.problem_since(
            None, {'SF_TEST_MEMORY_GROWTH_MB': '1',
                   'SF_TEST_MEMORY_CEILING_MB': '1'}))


class BaseClassEnforcementTestCase(base.ShakenFistTestCase):
    """The check is wired into ShakenFistTestCase, not merely available.

    The inner test cases are defined inside the test methods on purpose:
    a TestCase subclass at module scope is collected and run by stestr
    like any other, and one of these is meant to fail.
    """

    def _run_inner(self, body, limit_mb):
        patcher = mock.patch.dict(
            os.environ, {'SF_TEST_MEMORY_GROWTH_MB': str(limit_mb),
                         'SF_TEST_MEMORY_CEILING_MB': 'none'})
        patcher.start()
        self.addCleanup(patcher.stop)

        class Inner(base.ShakenFistTestCase):
            def test_inner(inner_self):
                body(inner_self)

        result = testtools.TestResult()
        Inner('test_inner').run(result)
        # A result holds the test object, and so whatever that object
        # leaked. Reduce it to strings and drop it, or the inner test's
        # allocation is charged to this one as well.
        problems = [str(detail) for _, detail
                    in result.failures + result.errors]
        del result
        return problems

    def test_a_test_which_grows_the_worker_fails(self):
        def leak(inner_self):
            # Held by the test instance, so it is still resident when the
            # cleanup takes its sample.
            inner_self.leaked = _touch(64)

        problems = self._run_inner(leak, 8)
        self.assertEqual(
            1, len(problems),
            'A test which grew the worker by 64MB against an 8MB limit '
            'should have been failed by ShakenFistTestCase, but the result '
            'holds %r' % (problems,))
        self.assertIn('grew the worker', problems[0])

    def test_a_test_which_releases_what_it_allocated_passes(self):
        def tidy(inner_self):
            held = _touch(64)
            del held

        self.assertEqual(
            [], self._run_inner(tidy, 32),
            'A test which released its allocation before cleanup should '
            'not be reported as growing the worker.')

    def test_a_disabled_limit_lets_growth_through(self):
        def leak(inner_self):
            inner_self.leaked = _touch(64)

        self.assertEqual([], self._run_inner(leak, 'none'))


class BoundedTestRunWrapperTestCase(base.ShakenFistTestCase):
    """tools/bounded-test-run.sh, exercised as CI and a developer run it."""

    def _run(self, arguments, extra_environment=None):
        environment = dict(os.environ)
        # The cgroup scope is the preferred mechanism but needs a user
        # systemd session, which a CI runner does not have. These cases
        # are about the RLIMIT_DATA fallback, so the scope is refused
        # here rather than left to depend on the host.
        environment['SF_TEST_MEMORY_MAX'] = 'none'
        environment.update(extra_environment or {})
        return subprocess.run(
            ['bash', WRAPPER] + arguments, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=120)

    def test_no_command_is_a_usage_error(self):
        completed = self._run([])
        self.assertEqual(2, completed.returncode)
        self.assertIn('usage:', completed.stdout)

    def test_the_command_runs_under_rlimit_data(self):
        completed = self._run([sys.executable, '-I', '-c', RLIMIT_PROBE],
                              {'SF_TEST_HEAP_MAX_MB': '1234'})
        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertIn(str(1234 * MEGABYTE), completed.stdout)

    def test_the_limit_bites_before_the_machine_does(self):
        completed = self._run(
            [sys.executable, '-I', '-c',
             'held = []\n'
             'try:\n'
             '    while True:\n'
             '        held.append(bytearray(8 * 1024 * 1024))\n'
             'except MemoryError:\n'
             '    print("bounded")\n'],
            {'SF_TEST_HEAP_MAX_MB': '128'})
        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertIn('bounded', completed.stdout)

    def test_the_commands_exit_status_is_passed_through(self):
        completed = self._run([sys.executable, '-I', '-c',
                               'raise SystemExit(7)'])
        self.assertEqual(7, completed.returncode, completed.stdout)

    def test_an_opt_out_leaves_the_limit_alone(self):
        inherited = resource.getrlimit(resource.RLIMIT_DATA)
        completed = self._run([sys.executable, '-I', '-c', RLIMIT_PROBE],
                              {'SF_TEST_HEAP_MAX_MB': 'none'})
        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertIn(str(inherited), completed.stdout)


class ToxWiringTestCase(base.ShakenFistTestCase):
    def test_every_tox_stestr_run_is_bounded(self):
        unbounded = []
        with open(TOX_INI) as f:
            for number, line in enumerate(f, start=1):
                if not STESTR_RUN_RE.match(line):
                    continue
                if not WRAPPER_RE.search(line):
                    unbounded.append((number, line.rstrip()))

        self.assertEqual(
            [], unbounded,
            'tox.ini runs the test suite without a memory bound on %s. A '
            'leaking worker then grows until the kernel or a human stops it '
            '(issue #4928) -- run it through tools/bounded-test-run.sh.'
            % unbounded)

    def test_the_scan_found_the_runs_it_is_guarding(self):
        # Both the py3 and the cover environments run the suite, so a
        # pattern change which matched nothing would otherwise pass.
        with open(TOX_INI) as f:
            matched = [line.rstrip() for line in f
                       if STESTR_RUN_RE.match(line)]
        self.assertEqual(
            2, len(matched),
            'Expected the testenv and cover stestr runs, found %r. Either '
            'tox.ini gained or lost a full-suite run, or this scan has gone '
            'blind.' % matched)

    def test_the_cover_environment_inherits_the_base_setenv(self):
        # A testenv which declares setenv replaces the base one rather
        # than extending it, which silently cost the environment CI
        # actually runs both OS_TEST_TIMEOUT and SHAKENFIST_LOG_TO_STDOUT.
        with open(TOX_INI) as f:
            body = f.read().split('[testenv:cover]')[1].split('\n[')[0]
        self.assertIn('{[testenv]setenv}', body)
