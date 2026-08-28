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
    database_requests_total{caller_daemon="cleaner",operation="Sweep"} 5.0 1700000000000
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
        self.assertEqual(4, len(pairs))

    def test_a_trailing_timestamp_is_not_the_value(self):
        # The exposition format allows a sample to carry a trailing
        # millisecond timestamp. Reading the last whitespace field, which
        # both copies of this parser used to do, returns 1.7e12 as the
        # counter -- and since both copies did it, the parity test below
        # agreed with itself and nothing failed. prometheus_client does
        # not emit timestamps today, so this is the case which would have
        # gone unnoticed until something else served these metrics.
        with mock.patch.object(harness.requests, 'get',
                               return_value=FakeResponse(METRICS)):
            pairs = harness.scrape_request_pairs('10.0.0.1')
        self.assertEqual(5.0, pairs[('Sweep', 'cleaner')])

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

    def test_harness_driven_pairs_are_not_budgeted(self):
        # The two mechanisms answer for different pairs and must not both
        # answer for one. A budgeted pair never reaches the unbudgeted
        # branch, so an entry which is in both places is dead here and
        # silently carries CI-only traffic into the model every consumer
        # of the budget file reads -- which is the thing
        # HARNESS_DRIVEN_PAIRS exists to avoid rather than cause.
        budgeted = {e['operation'] + '/' + e['caller_daemon']
                    for e in harness.load_budget()['entries']}
        overlap = sorted(
            {'%s/%s' % pair for pair in harness.HARNESS_DRIVEN_PAIRS}
            & budgeted)
        self.assertEqual(
            [], overlap,
            'These pairs are exempted as harness traffic and are also in '
            'shakenfist/data/database_load_budget.yaml. Either a real '
            'cluster produces the traffic, and it belongs only in the '
            'budget, or this suite does, and it belongs only in '
            'HARNESS_DRIVEN_PAIRS.')

    def test_harness_driven_reads_a_pair_either_way_round(self):
        self.assertTrue(harness.harness_driven(('GetObjectEvents', 'api')))
        self.assertTrue(harness.harness_driven(['GetObjectEvents', 'api']))
        self.assertFalse(harness.harness_driven(('GetObjectEvents', 'net')))
        self.assertFalse(harness.harness_driven(('GetNodeDaemonState', 'net')))

    def test_the_suite_still_polls_an_events_endpoint(self):
        # The whole argument for exempting GetObjectEvents/api is that this
        # suite's own await helpers read an events endpoint on a timer. If
        # they stop -- because the waits move onto an operation or a
        # notification -- the exemption stops being an explanation and
        # becomes a hole, and the pair should go back to failing the build.
        # Derived from base.py rather than asserted about it, for the same
        # reason test_non_polling_daemons_do_not_reach_the_tier derives its
        # list: a comment cannot notice that it went stale.
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(os.path.dirname(here))
        path = os.path.join(root, 'shakenfist', 'deploy', 'shakenfist_ci',
                            'base.py')
        with open(path, encoding='utf-8') as f:
            tree = ast.parse(f.read())

        polling_loops = []
        for loop in ast.walk(tree):
            if not isinstance(loop, (ast.While, ast.For)):
                continue
            sleeps = False
            events = False
            for node in ast.walk(loop):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (isinstance(func, ast.Attribute)
                        and func.attr == 'sleep'):
                    sleeps = True
                if (isinstance(func, ast.Attribute)
                        and func.attr.endswith('_events')
                        and func.attr.startswith('get_')):
                    events = True
            if sleeps and events:
                polling_loops.append(loop.lineno)

        self.assertNotEqual(
            [], polling_loops,
            'No loop in shakenfist_ci/base.py both sleeps and reads an '
            'events endpoint, so this suite no longer polls for events on '
            'a timer. That is the entire justification for exempting '
            'GetObjectEvents/api in HARNESS_DRIVEN_PAIRS -- drop the '
            'exemption and let the idle load check see the pair again.')

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

    def test_elected_loop_interval_matches_the_daemon(self):
        # The other half of the pair above, and the one which was a bare
        # literal in four places until review noticed: the elected cluster
        # daemon polls once per loop, so its loop sleep sets a rate the
        # positive control predicts and the budget encodes.
        from shakenfist.daemons.cluster import main as cluster_main

        self.assertEqual(float(cluster_main.ELECTED_LOOP_POLL_SECONDS),
                         harness.ELECTED_CLUSTER_LOOP_SECONDS)

    def test_the_unbudgeted_ceiling_matches_the_server_side_one(self):
        # Two implementations of the same rule, for the same reason as
        # expected_qps() above. A CI check with a stricter idea of "new
        # poll" than the alert an operator runs is a check which fails
        # builds nobody else can reproduce.
        defaults = database_load_budget.load_budget().defaults
        raw = harness.load_budget()['defaults']
        for nodes in (1, 3, 6, 20, 60):
            self.assertAlmostEqual(defaults.unbudgeted_ceiling_qps(nodes),
                                   harness.unbudgeted_ceiling_qps(raw, nodes),
                                   msg='%d nodes' % nodes)

    def test_the_unbudgeted_ceiling_grows_with_the_cluster(self):
        # The point of the per-node term: the pairs left out of the budget
        # are mostly per-node loops, so a flat threshold is one ordinary
        # traffic crosses on a big enough cluster -- permanently, where
        # nothing is wrong.
        defaults = database_load_budget.load_budget().defaults
        self.assertLess(defaults.unbudgeted_ceiling_qps(6),
                        defaults.unbudgeted_ceiling_qps(60))
        # ... but never below the floor, so a two node cluster does not
        # get a stricter test than the one the budget was derived on.
        self.assertEqual(defaults.unbudgeted_fixed_rate_qps,
                         defaults.unbudgeted_ceiling_qps(1))

    def test_daemon_node_counts_reads_the_external_view(self):
        # The key parsing has to survive a daemon name containing the
        # word it is being stripped of, and has to drop the daemons which
        # never reach the tier -- a count for one of those makes the
        # positive control predict a rate which will never arrive, and
        # fails the build on a perfectly healthy cluster.
        nodes = [
            {'name': 'node1',
             'daemon-net-state': 'daemon-running',
             'daemon-cluster-state': 'daemon-running',
             'daemon-database-state': 'daemon-running',
             'daemon-sentinel-first-state': 'daemon-running',
             'ip': '10.0.0.1'},
            {'name': 'node2',
             'daemon-net-state': 'daemon-running',
             'daemon-cluster-state': 'daemon-stopped',
             'state': 'created'},
        ]
        self.assertEqual({'net': 2, 'cluster': 1},
                         harness.daemon_node_counts(nodes))

    def test_daemon_node_counts_of_nothing_is_empty(self):
        # Which is what the positive control asserts against before it
        # draws any conclusion: no daemons seen means the harness cannot
        # predict a rate, not that the cluster is quiet.
        self.assertEqual({}, harness.daemon_node_counts([]))
        self.assertEqual({}, harness.daemon_node_counts(
            [{'name': 'node1', 'state': 'created'}]))

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

    def test_the_spread_admits_the_straddle_and_nothing_much_wider(self):
        # FIXED_RATE_MAX_SPREAD is one constant because the two it
        # replaced were only ever used as a quotient: the code read
        # "high / low > MAX / MIN", so 0.6 and 1.7 meant 2.83, and
        # anybody tuning one of them to reduce flakes would have moved
        # the threshold somewhere they did not predict. Pin what the
        # single number means at both ends.
        self.assertLess(2.0, harness.FIXED_RATE_MAX_SPREAD,
                        'the one-sample-versus-two straddle above is a '
                        'factor of two and has to survive')

        just_inside = harness.FIXED_RATE_MAX_SPREAD - 0.1
        self.assertIn(('Poll', 'net'), harness.fixed_rate(
            [{('Poll', 'net'): 1.0}, {('Poll', 'net'): just_inside}]))

        just_outside = harness.FIXED_RATE_MAX_SPREAD + 0.1
        self.assertNotIn(('Poll', 'net'), harness.fixed_rate(
            [{('Poll', 'net'): 1.0}, {('Poll', 'net'): just_outside}]))

    def test_a_poll_is_independent_of_activity(self):
        # The rate holds while everything around it triples, so its share
        # of the tier's traffic is the measurement which moved.
        windows = [{('Poll', 'net'): 1.5, ('Work', 'api'): 4.0},
                   {('Poll', 'net'): 1.5, ('Work', 'api'): 12.0}]
        self.assertEqual({('Poll', 'net')},
                         harness.independent_of_activity(windows))

    def test_work_which_tracks_the_suite_is_not_independent(self):
        # This is the case the check was getting wrong: a blob heavy test
        # running at a level rate looks fixed-rate to fixed_rate(), and is
        # only distinguishable because it rises and falls with the rest of
        # the suite. Both pairs here are steady enough to survive
        # fixed_rate(); only the poll survives this.
        windows = [{('Poll', 'net'): 1.5, ('UpsertBlobHash', 'queues'): 1.0},
                   {('Poll', 'net'): 1.5, ('UpsertBlobHash', 'queues'): 2.5},
                   {('Poll', 'net'): 1.5, ('UpsertBlobHash', 'queues'): 1.4}]
        self.assertIn(('UpsertBlobHash', 'queues'),
                      harness.fixed_rate(windows))
        self.assertNotIn(('UpsertBlobHash', 'queues'),
                         harness.independent_of_activity(windows))

    def test_nothing_is_independent_when_the_suite_ran_level(self):
        # Dividing every window by the same number cannot change a ratio,
        # so on a level run a pair's share is exactly as steady as its
        # rate and neither measurement decides anything. Erring towards
        # the empty set is what makes the tie-break safe; the caller then
        # notices via activity_spread() and skips rather than reporting
        # whatever fell out.
        windows = [{('Poll', 'net'): 1.5, ('Work', 'api'): 4.0},
                   {('Poll', 'net'): 1.5, ('Work', 'api'): 4.0}]
        self.assertEqual(set(), harness.independent_of_activity(windows))
        self.assertEqual(1.0, harness.activity_spread(windows))

    def test_a_pair_measures_its_share_against_the_other_traffic(self):
        # A pair big enough to dominate the tier is most of its own
        # denominator, so dividing by the total would damp the variation
        # in everything else down to almost nothing and the pair would
        # read as tracking traffic it is not. Here the poll is twenty
        # times the size of the only other pair and perfectly flat while
        # that pair doubles: measured against the total its share moves by
        # 5%, and measured against the rest it moves by the full factor of
        # two.
        #
        # Dividing by the total gets this wrong in the direction which
        # hides a big new polling loop, which is the one thing this check
        # exists to find, so it is worth the subtraction.
        windows = [{('Poll', 'api'): 20.0, ('Small', 'net'): 1.0},
                   {('Poll', 'api'): 20.0, ('Small', 'net'): 2.0}]
        self.assertEqual({('Poll', 'api')},
                         harness.independent_of_activity(windows))

        # And the total moved by so little that the CI test would skip
        # this run rather than trust it -- the subtraction buys headroom
        # for the runs which do clear that gate, it does not replace it.
        self.assertLess(harness.activity_spread(windows),
                        harness.ACTIVITY_DISCRIMINATION_SPREAD)

    def test_a_lone_pair_is_independent_of_nothing(self):
        # There is no activity to be independent of, and the alternative
        # is dividing by zero.
        windows = [{('Poll', 'net'): 1.5}, {('Poll', 'net'): 1.5}]
        self.assertEqual(set(), harness.independent_of_activity(windows))
        self.assertEqual(set(), harness.independent_of_activity([]))

    def test_activity_spread_is_one_when_there_is_nothing_to_compare(self):
        self.assertEqual(1.0, harness.activity_spread([]))
        self.assertEqual(1.0, harness.activity_spread([{}, {}]))

    def test_activity_levels_total_every_pair(self):
        self.assertEqual(
            [3.0, 7.0],
            harness.activity_levels([{('a', 'b'): 1.0, ('c', 'd'): 2.0},
                                     {('a', 'b'): 3.0, ('c', 'd'): 4.0}]))

    def test_the_discrimination_threshold_admits_a_wobbling_poll(self):
        # ACTIVITY_DISCRIMINATION_SPREAD is the gate the CI test skips
        # below, and it has to leave room for a real poll whose own rate
        # is not perfectly flat -- otherwise a healthy cluster measured on
        # a quiet run reports its polls as workload and the check passes
        # vacuously. Pin the arithmetic that choice rests on.
        # A pair which itself rises by p sits in the numerator of its own
        # share, so the share moves by a/p and not by a. The condition is
        # therefore p squared, not p -- which is worth pinning, because
        # the obvious reading of these two constants is out by a square
        # root and would promise twice the headroom that exists.
        self.assertLessEqual(
            1.2 ** 2 * harness.ACTIVITY_INDEPENDENCE_MARGIN,
            harness.ACTIVITY_DISCRIMINATION_SPREAD,
            'a poll whose rate wobbles by a fifth must still be '
            'recognisable on a run which only just cleared the gate')

        # A fifth of wobble, against other traffic which moved by the
        # gate. Pin both sides of it: the boundary itself is exact, so a
        # run a shade busier recognises the poll and a run a shade flatter
        # does not -- which is the behaviour the skip in database_tier.py
        # relies on, since it is what stops a level run reporting its own
        # polls as workload.
        def wobbling_poll(activity_spread):
            base = 10.0
            return [{('Poll', 'net'): 1.0, ('Work', 'api'): base},
                    {('Poll', 'net'): 1.2,
                     ('Work', 'api'): base * activity_spread}]

        just_inside = harness.ACTIVITY_DISCRIMINATION_SPREAD + 0.1
        self.assertIn(
            ('Poll', 'net'),
            harness.independent_of_activity(wobbling_poll(just_inside)))

        just_outside = harness.ACTIVITY_DISCRIMINATION_SPREAD - 0.1
        self.assertNotIn(
            ('Poll', 'net'),
            harness.independent_of_activity(wobbling_poll(just_outside)))

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
