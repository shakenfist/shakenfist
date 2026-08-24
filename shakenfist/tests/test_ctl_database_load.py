# Copyright 2026 Michael Still and contributors

"""Tests for "sf-ctl database-load".

This is the path for a deployer with no monitoring stack, and the thing to
ask for in a bug report, so the property which matters most is that it
never reports a partial measurement as a whole one. A tier gateway which
does not answer takes its share of the load with it, and a total quietly
missing a third of the cluster reads as load having fallen -- which is the
same mistake, in a smaller way, that #3708 made for a fortnight of phase 6.
"""

import json
import sys
from unittest import mock

from click.testing import CliRunner

from shakenfist.tests import base


FIRST = {
    'gw1': {('GetNodeDaemonState', 'net'): 100.0,
            ('GetReferencesFrom', 'api'): 500.0,
            ('Mystery', 'net'): 10.0},
    'gw2': {('GetNodeDaemonState', 'net'): 200.0,
            ('GetReferencesFrom', 'api'): 900.0,
            ('Mystery', 'net'): 20.0},
}

SECOND = {
    'gw1': {('GetNodeDaemonState', 'net'): 130.0,
            ('GetReferencesFrom', 'api'): 560.0,
            ('Mystery', 'net'): 70.0},
    'gw2': {('GetNodeDaemonState', 'net'): 230.0,
            ('GetReferencesFrom', 'api'): 960.0,
            ('Mystery', 'net'): 80.0},
}


class FakeConfig:
    MARIADB_GATEWAY_HOSTS = ['gw1', 'gw2']
    MARIADB_GATEWAY_METRICS_PORT = 13006


class DatabaseLoadCommandTestCase(base.ShakenFistTestCase):
    @classmethod
    def setUpClass(cls):
        cls.verify_config_patcher = mock.patch(
            'shakenfist.config.verify_config', mock.MagicMock())
        cls.verify_config_patcher.start()
        if 'shakenfist.client.ctl' in sys.modules:
            del sys.modules['shakenfist.client.ctl']

    @classmethod
    def tearDownClass(cls):
        cls.verify_config_patcher.stop()

    def setUp(self):
        super().setUp()
        self.runner = CliRunner()

    def _run(self, samples, args=None, hosts=None, nodes=6, instances=8):
        from shakenfist.client import ctl

        calls = {'n': 0}

        def _scrape(host, port, timeout=5):
            sample = samples[calls['n']]
            if host not in sample:
                raise OSError('connection refused')
            return dict(sample[host])

        def _sleep(_):
            calls['n'] += 1

        config = FakeConfig()
        if hosts is not None:
            config.MARIADB_GATEWAY_HOSTS = hosts

        with mock.patch.object(ctl.metrics_scrape, 'scrape_request_pairs',
                               side_effect=_scrape), \
                mock.patch.object(ctl.time, 'sleep', side_effect=_sleep), \
                mock.patch.object(ctl, 'config', config), \
                mock.patch.object(ctl, '_cluster_shape',
                                  return_value=(nodes, instances)):
            return self.runner.invoke(
                ctl.database_load, (args or []) + ['--window', '10'])

    def test_reports_measured_and_modelled(self):
        # --all-pairs because GetNodeDaemonState/net at 6/s is inside its
        # budget for a six node cluster and is filtered out by default.
        result = self._run([FIRST, SECOND], ['--json', '--all-pairs'])
        self.assertEqual(0, result.exit_code, result.output)
        report = json.loads(result.output)
        self.assertEqual({'nodes': 6, 'standing_instances': 8},
                         report['cluster'])
        self.assertEqual(['gw1', 'gw2'], report['gateways_measured'])
        self.assertEqual([], report['gateways_unreachable'])
        # 30 + 30 counts over a 10s window, summed across both gateways.
        pairs = {(p['operation'], p['caller_daemon']): p
                 for p in report['pairs']}
        self.assertEqual(6.0, pairs[('GetNodeDaemonState', 'net')]
                         ['measured_qps'])

    def test_an_unreachable_gateway_is_named_not_hidden(self):
        # The failure this is about: gw2 disappears, its share of the load
        # goes with it, and a total which does not say so reads as the
        # cluster having got quieter.
        second = {'gw1': SECOND['gw1']}
        result = self._run([FIRST, second], ['--json'])
        self.assertEqual(0, result.exit_code, result.output)
        report = json.loads(result.output)
        self.assertEqual(['gw1'], report['gateways_measured'])
        self.assertEqual(['gw2'], report['gateways_unreachable'])
        self.assertIn('gw2', report['gateway_errors'])

    def test_an_unreachable_gateway_warns_in_the_table_output(self):
        result = self._run([FIRST, {'gw1': SECOND['gw1']}])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('WARNING', result.output)
        self.assertIn('1 of 2 gateways did not answer', result.output)

    def test_a_gateway_seen_only_in_the_second_sample_is_excluded(self):
        # It cannot contribute a rate: there is nothing to subtract, and
        # counting its absolute counter as a delta would invent load.
        result = self._run([{'gw1': FIRST['gw1']}, SECOND],
                           ['--json', '--all-pairs'])
        report = json.loads(result.output)
        self.assertEqual(['gw1'], report['gateways_measured'])
        self.assertEqual(['gw2'], report['gateways_unreachable'])
        pairs = {(p['operation'], p['caller_daemon']): p
                 for p in report['pairs']}
        self.assertEqual(3.0, pairs[('GetNodeDaemonState', 'net')]
                         ['measured_qps'])

    def test_a_restarted_gateway_does_not_produce_negative_load(self):
        # sf-database restarting inside the window resets its counters, so
        # after minus before is negative. That is an unknown contribution,
        # not a negative one.
        second = {'gw1': {('GetNodeDaemonState', 'net'): 5.0},
                  'gw2': SECOND['gw2']}
        result = self._run([FIRST, second], ['--json', '--all-pairs'])
        report = json.loads(result.output)
        pairs = {(p['operation'], p['caller_daemon']): p
                 for p in report['pairs']}
        self.assertEqual(3.0, pairs[('GetNodeDaemonState', 'net')]
                         ['measured_qps'])
        self.assertLessEqual(0.0, report['total_measured_qps'])

    def test_no_gateways_configured_is_an_error(self):
        result = self._run([FIRST, SECOND], hosts=[])
        self.assertNotEqual(0, result.exit_code)
        self.assertIn('no database tier', result.output)

    def test_no_gateway_answers_is_an_error_not_an_empty_report(self):
        result = self._run([{}, {}])
        self.assertNotEqual(0, result.exit_code)
        self.assertIn('No sf-database gateway answered', result.output)

    def test_a_provisional_pair_is_flagged_and_not_over_budget(self):
        # GetReferencesFrom/api is a known defect (#3876). Telling a
        # deployer to report a bug we already have is worse than saying
        # nothing.
        result = self._run([FIRST, SECOND], ['--json'])
        report = json.loads(result.output)
        pairs = {(p['operation'], p['caller_daemon']): p
                 for p in report['pairs']}
        entry = pairs[('GetReferencesFrom', 'api')]
        self.assertIn('provisional:#3876', entry['flags'])
        self.assertFalse(entry['over_budget'])

    def test_an_unbudgeted_pair_is_flagged(self):
        result = self._run([FIRST, SECOND], ['--json'])
        report = json.loads(result.output)
        pairs = {(p['operation'], p['caller_daemon']): p
                 for p in report['pairs']}
        self.assertIn('unbudgeted', pairs[('Mystery', 'net')]['flags'])

    def test_rows_are_sorted_by_excess_not_by_rate(self):
        # The busiest pair on any cluster is usually a poll doing exactly
        # what it should, so sorting by rate would bury the finding.
        result = self._run([FIRST, SECOND], ['--json', '--all-pairs'])
        report = json.loads(result.output)
        excesses = [p['excess_qps'] for p in report['pairs']]
        self.assertEqual(sorted(excesses, reverse=True), excesses)

    def test_table_output_is_not_json(self):
        result = self._run([FIRST, SECOND])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('OPERATION', result.output)
        self.assertIn('standing instances', result.output)
