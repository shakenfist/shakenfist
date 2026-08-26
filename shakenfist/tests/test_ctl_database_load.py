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

    def _run(self, samples, args=None, hosts=None, nodes=6, instances=8,
             scrape_seconds=0.0):
        from shakenfist.client import ctl

        calls = {'n': 0}

        # A fake monotonic clock, advanced by the sleep and by each
        # scrape. Rates are computed from what it says elapsed between
        # the two reads of a counter, so a test which left it real would
        # divide a counter delta by a few microseconds.
        clock = {'t': 1000.0}

        def _scrape(host, port, timeout=5):
            sample = samples[calls['n']]
            if host not in sample:
                raise OSError('connection refused')
            clock['t'] += scrape_seconds
            return dict(sample[host])

        def _sleep(seconds):
            calls['n'] += 1
            clock['t'] += seconds

        config = FakeConfig()
        if hosts is not None:
            config.MARIADB_GATEWAY_HOSTS = hosts

        with mock.patch.object(ctl.metrics_scrape, 'scrape_request_pairs',
                               side_effect=_scrape), \
                mock.patch.object(ctl.time, 'sleep', side_effect=_sleep), \
                mock.patch.object(ctl.time, 'monotonic',
                                  side_effect=lambda: clock['t']), \
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

    def test_an_unbudgeted_pair_over_its_ceiling_is_over_budget(self):
        # Mystery/net runs at 12/s here, against the 0.25/s ceiling an
        # unbudgeted pair gets. That is precisely the new polling loop
        # this whole phase exists to find, and the command used to show
        # it as a flagged row and then print "nothing is over budget"
        # underneath -- because the enforcement test asked whether the
        # entry was enforced, and an unbudgeted pair has no entry. The
        # other two consumers of this budget both act on this case:
        # ShakenFistUnbudgetedDatabasePolling fires and
        # test_no_unbudgeted_fixed_rate_database_polling fails the build.
        result = self._run([FIRST, SECOND], ['--json'])
        report = json.loads(result.output)
        pairs = {(p['operation'], p['caller_daemon']): p
                 for p in report['pairs']}
        self.assertTrue(pairs[('Mystery', 'net')]['over_budget'])
        self.assertLess(0, report['pairs_over_budget'])

    def test_a_quiet_unbudgeted_pair_is_not_over_budget(self):
        # The other half of the same rule: unbudgeted is not by itself a
        # problem. Below the ceiling it is a row worth printing and
        # nothing to report.
        quiet_second = {}
        for host, pairs in SECOND.items():
            quiet = dict(pairs)
            quiet[('Mystery', 'net')] = FIRST[host][('Mystery', 'net')] + 1.0
            quiet_second[host] = quiet
        result = self._run([FIRST, quiet_second], ['--json'])
        report = json.loads(result.output)
        pairs = {(p['operation'], p['caller_daemon']): p
                 for p in report['pairs']}
        entry = pairs[('Mystery', 'net')]
        self.assertIn('unbudgeted', entry['flags'])
        self.assertFalse(entry['over_budget'])

    def test_rates_are_divided_by_measured_time_not_the_window(self):
        # A counter delta covers the sleep plus the scrapes, and the
        # scrapes are not free: at the default 5s timeout a handful of
        # gateways is already several percent of a 60s window. Dividing
        # by the nominal window overstates every rate, which on a
        # budgeted pair reads as a regression that is not there.
        #
        # Two gateways scraped at 1s each: the first sample is read at
        # +1s and +2s, the sleep takes the clock to +12s, the second
        # sample is read at +13s and +14s. Each gateway's counter
        # therefore covers 12 seconds, not the 10 that was slept, and
        # 30 counts on each gateway is 5/s rather than 6/s.
        result = self._run([FIRST, SECOND], ['--json', '--all-pairs'],
                           scrape_seconds=1.0)
        report = json.loads(result.output)
        pairs = {(p['operation'], p['caller_daemon']): p
                 for p in report['pairs']}
        self.assertEqual(5.0, pairs[('GetNodeDaemonState', 'net')]
                         ['measured_qps'])
        self.assertEqual({'gw1': 12.0, 'gw2': 12.0},
                         report['measured_seconds'])

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


def metrics_row(node, active, age=0.0):
    return {'node_uuid': node, 'fqdn': node, 'timestamp': 1000.0 - age,
            'metrics': {'instances_active': active, 'instances_total': 99}}


class ClusterShapeTestCase(base.ShakenFistTestCase):
    """The model has to be evaluated against what it was fitted against.

    per_instance_qps is a regression against sum(instances_active), which
    counts running libvirt domains. This used to count every instance in
    the created state instead, which is a different and larger number on
    any cluster with powered off instances -- and since the ceiling is a
    multiple of the modelled value, counting them raises the cluster's own
    ceiling for load those instances do not produce. The generated
    Prometheus rules read the right series, so the two disagreed.
    """

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

    def _shape(self, rows):
        from shakenfist.client import ctl

        with mock.patch.object(ctl.mariadb, 'get_all_node_metrics',
                               return_value=rows), \
                mock.patch.object(ctl.time, 'time', return_value=1000.0):
            return ctl._cluster_shape()

    def test_standing_instances_are_the_running_domains(self):
        self.assertEqual(
            (2, 7),
            self._shape([metrics_row('a', 3), metrics_row('b', 4)]))

    def test_a_node_running_nothing_still_counts_as_a_node(self):
        # The per-node base term is most of the budget, so an idle node
        # has to be counted or every modelled value reads low.
        self.assertEqual(
            (2, 3),
            self._shape([metrics_row('a', 3), metrics_row('b', 0)]))

    def test_a_stale_row_is_not_a_node(self):
        # sf-resources only clears its own row, and only at startup, so a
        # node which has left the cluster leaves one behind. Prometheus
        # drops the series; counting it here would keep charging the
        # per-node term for a node which is gone.
        shape = self._shape([
            metrics_row('a', 3),
            metrics_row('gone', 40, age=ctl_stale() + 1)])
        self.assertEqual((1, 3), shape)

    def test_no_fresh_metrics_is_an_error_not_an_empty_cluster(self):
        # Returning (0, 0) would make every modelled value zero and every
        # ceiling the tolerance floor, so a healthy cluster would report
        # every pair it has as over budget.
        from shakenfist.client import ctl

        self.assertRaises(ctl.click.ClickException, self._shape, [])


def ctl_stale():
    from shakenfist.client import ctl
    return ctl.NODE_METRICS_STALE_SECONDS
