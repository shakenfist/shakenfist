# Copyright 2026 Michael Still and contributors

"""Tests for tools/ci_stestr_times.py.

What has to hold: a seeded repository makes stestr's own scheduler balance
the workers by time, which is the whole point, so that is tested through
stestr rather than by reading back what was written; a times file which is
not usable is refused rather than half applied; and the durations a CI log
reports are read out of it exactly, and only for tests which passed.

The tool is loaded by path because tools/ is not a package. stestr is a test
dependency, so the seeding runs against the real thing.
"""

import importlib.util
import json
import os
import tempfile
from unittest import mock

from stestr import scheduler
from stestr.repository import util as repo_util

from shakenfist.tests import base


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL = os.path.join(REPO_ROOT, 'tools', 'ci_stestr_times.py')
SUITES = ('cluster_ci_tests', 'guest_ci_tests', 'smoke_ci_tests')


def _load():
    spec = importlib.util.spec_from_file_location('ci_stestr_times', TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


times_tool = _load()


class SeedTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # As stestr means it: the directory holding .stestr.
        self.repo_url = self.tmp.name
        self.repo_dir = os.path.join(self.tmp.name, '.stestr')

    def _write_times(self, times):
        path = os.path.join(self.tmp.name, 'stestr-times.json')
        with open(path, 'w') as f:
            json.dump({'times': times}, f)
        return path

    def test_a_seeded_repository_balances_the_workers_by_time(self):
        # Three long tests and nine short ones, named so that round robin in
        # name order deals all three long ones to the same worker -- the
        # shape #5010 found.
        times = {}
        for i in range(12):
            times['shakenfist_ci.smoke_ci_tests.test_m.T.test_%02d' % i] = (
                300.0 if i % 3 == 0 and i < 9 else 10.0)
        ids = sorted(times)

        unseeded = scheduler.partition_tests(
            ids, 3, repo_util.get_repo_initialise(repo_url=self.repo_url), None)
        # Tests 00, 03, 06 and 09: all three long ones and a short one.
        self.assertEqual(
            910.0, max(sum(times[t] for t in p) for p in unseeded))

        self.assertEqual(12, times_tool.seed(times, self.repo_url))
        seeded = scheduler.partition_tests(
            ids, 3, repo_util.get_repo_open(repo_url=self.repo_url), None)
        loads = [sum(times[t] for t in p) for p in seeded]
        self.assertEqual([330.0, 330.0, 330.0], sorted(loads))

    def test_seed_creates_the_repository_when_there_is_none(self):
        self.assertFalse(os.path.exists(self.repo_dir))
        times = {'shakenfist_ci.smoke_ci_tests.test_m.T.test_a': 5.0}
        self.assertEqual(1, times_tool.seed(times, self.repo_url))
        self.assertTrue(os.path.isdir(self.repo_dir))

    def test_seed_adds_to_a_repository_which_already_exists(self):
        repo_util.get_repo_initialise(repo_url=self.repo_url)
        times = {'shakenfist_ci.smoke_ci_tests.test_m.T.test_a': 5.0,
                 'shakenfist_ci.smoke_ci_tests.test_m.T.test_b': 7.5}
        self.assertEqual(2, times_tool.seed(times, self.repo_url))
        known = repo_util.get_repo_open(repo_url=self.repo_url).get_test_times(
            list(times))['known']
        self.assertEqual(times, known)

    def test_a_tagged_test_id_is_found_under_its_clean_name(self):
        # stestr strips "[tags]" from an id before it looks the duration up,
        # so it must be stored without them too.
        times_tool.seed(
            {'shakenfist_ci.smoke_ci_tests.test_m.T.test_a[smoke]': 5.0},
            self.repo_url)
        known = repo_util.get_repo_open(repo_url=self.repo_url).get_test_times(
            ['shakenfist_ci.smoke_ci_tests.test_m.T.test_a'])['known']
        self.assertEqual(
            {'shakenfist_ci.smoke_ci_tests.test_m.T.test_a': 5.0}, known)

    def test_seeding_records_no_run(self):
        # A made up run would be what stestr failing reports when the real
        # run is killed before it commits, which is what a timeout does.
        times_tool.seed({'shakenfist_ci.smoke_ci_tests.test_m.T.test_a': 5.0},
                        self.repo_url)
        repo = repo_util.get_repo_open(repo_url=self.repo_url)
        self.assertEqual(0, repo.count())

    def test_main_seeds_and_succeeds(self):
        path = self._write_times(
            {'shakenfist_ci.smoke_ci_tests.test_m.T.test_a': 5.0})
        self.assertEqual(0, times_tool.main(
            ['seed', '--times', path, '--repo-url', self.repo_url]))

    def test_main_fails_when_stestr_does_not_see_every_duration(self):
        # The seed is read back through stestr; a shortfall means the
        # partitions are only partly balanced, which the caller logs.
        path = self._write_times(
            {'shakenfist_ci.smoke_ci_tests.test_m.T.test_a': 5.0,
             'shakenfist_ci.smoke_ci_tests.test_m.T.test_b': 5.0})
        with mock.patch.object(times_tool, 'seed', return_value=1):
            self.assertEqual(1, times_tool.main(
                ['seed', '--times', path, '--repo-url', self.repo_url]))

    def test_main_refuses_a_missing_times_file_without_creating_a_repository(self):
        self.assertEqual(1, times_tool.main(
            ['seed', '--times', os.path.join(self.tmp.name, 'absent.json'),
             '--repo-url', self.repo_url]))
        self.assertFalse(os.path.exists(self.repo_dir))


class LoadTimesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _path(self, content):
        path = os.path.join(self.tmp.name, 'times.json')
        with open(path, 'w') as f:
            f.write(content)
        return path

    def test_integers_are_read_as_seconds(self):
        self.assertEqual(
            {'a': 3.0}, times_tool.load_times(self._path('{"times": {"a": 3}}')))

    def test_refusals(self):
        for content in ('not json', '[]', '{}', '{"times": {}}',
                        '{"times": []}', '{"times": {"a": -1}}',
                        '{"times": {"a": "12"}}', '{"times": {"a": true}}',
                        '{"times": {"a": null}}'):
            self.assertRaises(times_tool.TimesError, times_tool.load_times,
                              self._path(content))

    def test_an_unreadable_file_is_refused(self):
        self.assertRaises(times_tool.TimesError, times_tool.load_times,
                          os.path.join(self.tmp.name, 'absent.json'))


class HarvestTestCase(base.ShakenFistTestCase):
    def test_parse_log_reads_passing_tests_only(self):
        log = '\n'.join([
            'Smoke tests\tUNKNOWN STEP\t2026-11-22T05:04:47.7834214Z {1} '
            'shakenfist_ci.smoke_ci_tests.test_a.TestA.test_one '
            '[209.790488s] ... ok',
            '2026-11-22T05:04:51.0236199Z {0} '
            'shakenfist_ci.cluster_ci_tests.test_b.TestB.test_two [3s] ... ok',
            '2026-11-22T05:23:38.3378818Z {1} '
            'shakenfist_ci.smoke_ci_tests.test_c.TestC.test_skipped ... '
            'SKIPPED: not on this topology',
            '2026-11-22T05:24:00.0000000Z {2} '
            'shakenfist_ci.smoke_ci_tests.test_d.TestD.test_failed '
            '[12.5s] ... FAILED',
            'shakenfist_ci.smoke_ci_tests.test_e.TestE.test_mentioned ok',
        ])
        self.assertEqual(
            [('shakenfist_ci.smoke_ci_tests.test_a.TestA.test_one', 209.790488),
             ('shakenfist_ci.cluster_ci_tests.test_b.TestB.test_two', 3.0)],
            list(times_tool.parse_log(log)))

    def test_render_takes_the_median_and_round_trips(self):
        text = times_tool.render(
            {'shakenfist_ci.smoke_ci_tests.t.T.test_b': [10.0, 300.0, 20.04],
             'shakenfist_ci.smoke_ci_tests.t.T.test_a': [1.0, 2.0]},
            [2, 1], '2026-11-23')
        document = json.loads(text)
        self.assertEqual(
            {'shakenfist_ci.smoke_ci_tests.t.T.test_a': 1.5,
             'shakenfist_ci.smoke_ci_tests.t.T.test_b': 20.0},
            document['times'])
        self.assertEqual(['shakenfist_ci.smoke_ci_tests.t.T.test_a',
                          'shakenfist_ci.smoke_ci_tests.t.T.test_b'],
                         list(document['times']))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, 'times.json')
        with open(path, 'w') as f:
            f.write(text)
        self.assertEqual(document['times'], times_tool.load_times(path))


class CheckedInTimesTestCase(base.ShakenFistTestCase):
    def test_the_checked_in_times_file_is_usable(self):
        times = times_tool.load_times(times_tool.TIMES_FILE)
        for test_id in times:
            parts = test_id.split('.')
            self.assertEqual('shakenfist_ci', parts[0], test_id)
            self.assertIn(parts[1], SUITES, test_id)
        # Every suite is represented, or a lane runs unbalanced.
        self.assertEqual(set(SUITES), {t.split('.')[1] for t in times})

    def test_the_times_file_sits_where_the_suites_run(self):
        # smoke-cluster.yml runs stestr, and this tool's seed with its
        # default --times, from shakenfist/deploy next to the configurations.
        deploy = os.path.join(REPO_ROOT, 'shakenfist', 'deploy')
        self.assertEqual(os.path.join(deploy, 'stestr-times.json'),
                         times_tool.TIMES_FILE)
        for conf in ('smoke-ci.conf', 'cluster-ci.conf', 'guest-ci.conf'):
            self.assertTrue(os.path.exists(os.path.join(deploy, conf)), conf)
