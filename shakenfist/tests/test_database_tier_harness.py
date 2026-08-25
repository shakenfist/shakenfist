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

import ast
import importlib.util
import json
import os
import textwrap
import tomllib
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


def _repository_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _daemon_modules():
    """Every daemon name which has a module behind it, and its path.

    Read from the console scripts in pyproject.toml, because that is what
    actually decides which module a daemon name runs -- and it is where
    the mapping stops being the identity, sf-net being
    shakenfist.daemons.network. A name in Node.VALID_DAEMONS with no entry
    here has no daemon module at all: sf-api is gunicorn over
    external_api, and eventlog and checksums are names nothing implements.
    """
    root = _repository_root()
    with open(os.path.join(root, 'pyproject.toml'), 'rb') as f:
        scripts = tomllib.load(f)['project']['scripts']

    modules = {}
    for script, target in scripts.items():
        module = target.split(':')[0]
        if not script.startswith('sf-'):
            continue
        if not module.startswith('shakenfist.daemons.'):
            continue
        modules[script[len('sf-'):]] = os.path.join(
            root, *module.split('.')) + '.py'
    return modules


def _daemons_which_poll():
    """The daemons whose loop reaches Daemon.check_daemon_state().

    Parsed rather than imported, because importing a daemon's main module
    to ask a question about its shape runs its module level code. A daemon
    polls if it defines a class deriving from the Daemon base class and its
    module reaches the poll -- either through Daemon.idle() or by calling
    check_daemon_state() directly, which the cluster and queues daemons do
    from loops that sleep elsewhere.
    """
    bases = ('Daemon', 'WorkerPoolDaemon')
    polls = set()

    for name, path in _daemon_modules().items():
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as f:
            source = f.read()

        tree = ast.parse(source)
        subclasses = False
        for statement in ast.walk(tree):
            if not isinstance(statement, ast.ClassDef):
                continue
            for parent in statement.bases:
                if isinstance(parent, ast.Attribute) and parent.attr in bases:
                    subclasses = True
                elif isinstance(parent, ast.Name) and parent.id in bases:
                    subclasses = True

        if not subclasses:
            continue
        if 'check_daemon_state' in source or '.idle(' in source:
            polls.add(name)

    return polls


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
        # The positive control predicts a GetNodeDaemonState rate for every
        # daemon a node reports running and not on this list, so a daemon
        # in the wrong half fails the control on a perfectly healthy
        # cluster -- which is what a hand written list of three entries
        # did, because api, nodelock and privexec do not run the base
        # class' loop and so never poll at all.
        #
        # So derive it rather than list it, from the two reasons a daemon
        # has for not appearing in the counter. A new daemon then lands in
        # the right half or fails here, rather than at the end of a cluster
        # build.
        from shakenfist import mariadb
        from shakenfist import node

        polls = _daemons_which_poll()
        direct = set(mariadb.DIRECT_MARIADB_CALLERS)
        expected = (set(node.Node.VALID_DAEMONS) - polls) | (polls & direct)

        self.assertEqual(
            expected, set(harness.NON_POLLING_DAEMONS),
            'NON_POLLING_DAEMONS no longer matches the daemons which '
            'actually poll their own daemon state row over the tier. '
            'Daemons running Daemon.idle(): %s. Daemons with direct '
            'MariaDB access: %s.'
            % (json.dumps(sorted(polls)), json.dumps(sorted(direct))))

    def test_the_poller_derivation_sees_a_real_poller(self):
        # The check above passes vacuously if the derivation finds nothing,
        # so pin two daemons known to sit on opposite sides of it. sf-net
        # is also the case which proves the daemon name and its module
        # directory are not assumed to be the same string.
        polls = _daemons_which_poll()
        self.assertIn('net', polls)
        self.assertNotIn('nodelock', polls)

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

    def test_parser_matches_the_server_side_parser(self):
        # shakenfist/util/metrics_scrape.py is the same parser for
        # sf-ctl database-load. The CI suite carries its own copy because
        # it imports nothing from the server package; a copy which parses
        # differently would make the two disagree about what the tier is
        # serving, which is the whole thing this budget work is trying to
        # prevent.
        from shakenfist.util import metrics_scrape

        with mock.patch.object(harness.requests, 'get',
                               return_value=FakeResponse(METRICS)):
            ci_pairs = harness.scrape_request_pairs('10.0.0.1')
        self.assertEqual(metrics_scrape.parse_request_pairs(METRICS),
                         ci_pairs)
