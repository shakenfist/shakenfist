# Copyright 2026 Michael Still and contributors

"""Tests for shakenfist/deploy/shakenfist_ci/load_budget.py.

The functional CI idle-load check needs a running database tier, but the
budget handling underneath it does not, and it is worth pinning down here
where it runs on every commit rather than only when a cluster gets built.

The assertion which matters is the parity one. The CI suite is standalone
-- it imports the client and nothing from the server -- so it evaluates the
budget's model itself rather than importing BudgetEntry. Two implementations
of the same arithmetic is exactly how a check and the thing it checks stop
agreeing about what normal is, so this asserts they produce the same answer
for every entry in the shipped budget, at four cluster shapes.
"""

import importlib.util
import os
import textwrap
from unittest import mock

from shakenfist.schema import database_load_budget
from shakenfist.tests import base


def _load_harness():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    path = os.path.join(root, 'shakenfist', 'deploy', 'shakenfist_ci',
                        'load_budget.py')
    spec = importlib.util.spec_from_file_location('ci_load_budget', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = _load_harness()


METRICS = textwrap.dedent("""\
    # HELP database_requests_total Requests by operation and caller
    # TYPE database_requests_total counter
    database_requests_total{caller_daemon="net",operation="GetNode"} 41.0
    database_requests_total{caller_daemon="api",operation="GetNode"} 7.0
    database_requests_total{operation="Dequeue",caller_daemon="queues"} 12.0
    database_get_node_total 48.0
    database_requests_total{caller_daemon="net"} 3.0
    """)


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class DatabaseTierHarnessTestCase(base.ShakenFistTestCase):
    def test_scrape_request_pairs_reads_both_label_orders(self):
        # Prometheus does not promise a label order, and the sample which
        # motivated this parser writes caller_daemon first on some lines
        # and operation first on others.
        with mock.patch.object(harness.requests, 'get',
                               return_value=FakeResponse(METRICS)):
            pairs = harness.scrape_request_pairs('10.0.0.1')
        self.assertEqual(41.0, pairs[('GetNode', 'net')])
        self.assertEqual(7.0, pairs[('GetNode', 'api')])
        self.assertEqual(12.0, pairs[('Dequeue', 'queues')])

    def test_scrape_request_pairs_ignores_unlabelled_samples(self):
        # database_get_node_total is the older per-operation counter and
        # carries no labels; a sample missing either label cannot be
        # attributed to a pair and must not be counted as one.
        with mock.patch.object(harness.requests, 'get',
                               return_value=FakeResponse(METRICS)):
            pairs = harness.scrape_request_pairs('10.0.0.1')
        self.assertEqual(3, len(pairs))

    def test_expected_qps_matches_the_server_side_model(self):
        # The assertion this file exists for.
        budget = database_load_budget.load_budget()
        raw = {(e['operation'], e['caller_daemon']): e
               for e in harness.load_budget()['entries']}
        self.assertEqual(len(budget.entries), len(raw))
        for entry in budget.entries:
            for nodes, instances in ((1, 0), (3, 8), (6, 47), (12, 200)):
                self.assertAlmostEqual(
                    entry.expected_qps(nodes, instances),
                    harness.expected_qps(raw[entry.key], nodes, instances),
                    msg='%s/%s disagrees at %d nodes, %d instances'
                        % (entry.operation, entry.caller_daemon, nodes,
                           instances))

    def test_enforced_matches_the_server_side_model(self):
        raw = {(e['operation'], e['caller_daemon']): e
               for e in harness.load_budget()['entries']}
        for entry in database_load_budget.load_budget().entries:
            self.assertEqual(entry.enforced, harness.enforced(raw[entry.key]),
                             '%s/%s' % entry.key)

    def test_harness_reads_the_shipped_budget_not_a_copy(self):
        # If the harness ever grows its own copy of the numbers this stops
        # being true, which is the failure decision 2 of the phase plan is
        # about.
        self.assertEqual(
            database_load_budget.load_budget().version,
            harness.load_budget()['version'])

    def test_poll_interval_matches_the_daemon(self):
        # The suite duplicates this constant because it does not import the
        # server package. Duplicated is fine; drifted is not.
        from shakenfist.daemons import daemon

        self.assertEqual(float(daemon.DAEMON_STATE_POLL_INTERVAL),
                         harness.DAEMON_STATE_POLL_INTERVAL)

    def test_non_polling_daemons_do_not_reach_the_tier(self):
        # sf-database has direct MariaDB access, so its own reads never
        # pass through the interceptor which increments the counter.
        # Predicting a poll rate for it would make the control fail on a
        # perfectly healthy cluster.
        self.assertIn('database', harness.NON_POLLING_DAEMONS)
        self.assertIn('sentinel-first', harness.NON_POLLING_DAEMONS)
        self.assertIn('sentinel-last', harness.NON_POLLING_DAEMONS)

    def test_a_steady_pair_is_fixed_rate(self):
        steady = harness.fixed_rate([{('GetNode', 'net'): 3.0},
                                     {('GetNode', 'net'): 3.02}])
        self.assertEqual({('GetNode', 'net'): 3.0}, steady)

    def test_a_burst_in_one_window_is_not_fixed_rate(self):
        # This is the case which decides whether the CI check flakes: the
        # suite runs in parallel, so another worker creating instances puts
        # a large rate in one window and nothing in the next.
        steady = harness.fixed_rate([{('CreateInstance', 'api'): 9.0},
                                     {('CreateInstance', 'api'): 0.2}])
        self.assertEqual({}, steady)

    def test_a_pair_absent_from_one_window_is_not_fixed_rate(self):
        steady = harness.fixed_rate([{('DeleteBlob', 'cluster'): 4.0},
                                     {}])
        self.assertEqual({}, steady)

    def test_a_pair_which_stopped_is_not_fixed_rate(self):
        steady = harness.fixed_rate([{('DeleteBlob', 'cluster'): 4.0},
                                     {('DeleteBlob', 'cluster'): 0.0}])
        self.assertEqual({}, steady)

    def test_fixed_rate_reports_the_lowest_observation(self):
        # Everything downstream asserts a rate is too high, so the
        # conservative reading is the one least likely to fail a build over
        # somebody else's test.
        steady = harness.fixed_rate([{('GetNode', 'net'): 3.4},
                                     {('GetNode', 'net'): 2.9}])
        self.assertEqual(2.9, steady[('GetNode', 'net')])

    def test_fixed_rate_of_nothing_is_nothing(self):
        self.assertEqual({}, harness.fixed_rate([]))
        self.assertEqual({}, harness.fixed_rate([{}, {}]))

    def test_a_slow_loop_straddling_a_window_survives(self):
        # A loop with a one minute period measured over two sixty second
        # windows can land one sample in one and two in the other. That is
        # a factor of two, and it must not be read as churn -- doing so
        # would make the check blind to exactly the slow polls which are
        # hardest to spot by reading code.
        per_window = harness.LOAD_WINDOW_SECONDS
        steady = harness.fixed_rate([{('Sweep', 'cluster'): 1.0 / per_window},
                                     {('Sweep', 'cluster'): 2.0 / per_window}])
        self.assertIn(('Sweep', 'cluster'), steady)
