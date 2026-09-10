# Copyright 2019 Michael Still and contributors
"""A capacity refusal in CI is usually about a moment, not the cluster.

The functional suite runs concurrently against one cluster, so a create
which is refused for insufficient resources is very often refused
because a sibling test is holding the capacity and is already deleting
the instance which holds it -- deletion returns capacity
asynchronously. Treating every 507 as a hard failure makes a run's
pass/fail a coin flip on sibling timing.

``BaseTestCase.create_instance()`` waits such a refusal out, and
``shakenfist_ci/retries.py``'s ``wait_for_capacity()`` decides when to
stop waiting. That decision is the whole risk of the design, because
getting it wrong does not fail loudly: a wrapper which stops waiting
early re-issues a create which refuses again, turning one 507 into six
while reporting that it waited.

So the predicate is what most of this file is about. ``/admin/resources``
publishes two cpu ledgers deliberately: ``cpu_available`` is live
overcommit arithmetic, while ``cpu_limit`` is the capacity row's
``limit_cpus`` -- which is what admission's guarded UPDATE is actually
measured against, and which refreshes only once a reconcile period. The
window where they disagree is precisely the window this wait exists to
survive, so the predicate takes whichever binds. It also reads per-node
figures only: ``total['cpu_available']`` is a sum across nodes
(``scheduler.py``) and an instance has to fit on one of them.

``retries.py`` is loaded by path and ``base.py`` by path with stubs,
because the functional suite imports ``shakenfist_client`` and
``prettytable``, neither of which is a test dependency of this
repository.
"""

import importlib.util
import logging
import os
import sys
import types

from unittest import mock

from shakenfist.tests import base as test_base


CI_SUITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci')

RETRIES_PATH = os.path.join(CI_SUITE, 'retries.py')


def _load_retries():
    spec = importlib.util.spec_from_file_location(
        'shakenfist_ci_retries_capacity_under_test', RETRIES_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


retries = _load_retries()


def _client_stubs():
    """A shakenfist_client which is only its exception taxonomy.

    The wrapper's behaviour turns entirely on which exception classes
    it catches, so the stub defines those and nothing else. The class
    names and, more importantly, the fact that a 409 and a 507 are
    *siblings* rather than one being a subclass of the other, are
    copied from client-python's STATUS_CODES_TO_ERRORS. (On the server
    the affinity exception is a subclass of the low-resource one, which
    is why the API path's except clause ordering matters there; the
    client's mapping is by status code, so the two arrive here
    unrelated.)
    """
    client = types.ModuleType('shakenfist_client')
    apiclient = types.ModuleType('shakenfist_client.apiclient')

    class APIException(Exception):
        def __init__(self, message='', method=None, url=None,
                     status_code=None, text=None):
            super().__init__(message)
            self.status_code = status_code
            self.text = text

    apiclient.APIException = APIException
    for name in ('InsufficientResourcesException',
                 'ResourceStateConflictException',
                 'ResourceNotFoundException',
                 'RequestMalformedException',
                 'ServiceUnavailableException',
                 'IncapableException',
                 'AgentCommandError',
                 'AgentOperationFailed',
                 'AgentAwaitTimeout'):
        setattr(apiclient, name, type(name, (APIException,), {}))

    apiclient.ASYNC_PAUSE = 'pause'
    apiclient.Client = type('Client', (), {'__init__': lambda self, *a, **kw: None})

    client.apiclient = apiclient
    return client, apiclient


def _load_ci_base():
    """Import the functional suite's base.py without its dependencies.

    Everything mutated here is put back, because stestr runs the whole
    unit suite in one process and a stubbed shakenfist_client left in
    sys.modules would be a trap for a later test rather than a
    convenience for this one.
    """
    stub_names = ('prettytable', 'shakenfist_client',
                  'shakenfist_client.apiclient', 'shakenfist_ci',
                  'shakenfist_ci.base', 'shakenfist_ci.process',
                  'shakenfist_ci.retries')
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules.get(name) for name in stub_names}
    root_logger = logging.getLogger()
    saved_handlers = list(root_logger.handlers)
    saved_level = root_logger.level

    try:
        sys.path.insert(0, os.path.dirname(CI_SUITE))
        for name in stub_names:
            sys.modules.pop(name, None)

        prettytable = types.ModuleType('prettytable')
        prettytable.PrettyTable = type('PrettyTable', (), {})
        sys.modules['prettytable'] = prettytable

        client, apiclient = _client_stubs()
        sys.modules['shakenfist_client'] = client
        sys.modules['shakenfist_client.apiclient'] = apiclient

        import shakenfist_ci.base as ci_base
        return ci_base, apiclient

    finally:
        sys.path[:] = saved_path
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        root_logger.handlers[:] = saved_handlers
        root_logger.setLevel(saved_level)


ci_base, ci_apiclient = _load_ci_base()


def node(cpu_available, cpu_limit=None, cpu_committed=0):
    """One entry as summarize_resources() publishes it, trimmed."""
    return {
        'cpu_available': cpu_available,
        'cpu_limit': cpu_limit,
        'cpu_committed': cpu_committed,
        'cpu_committed_row_present': cpu_limit is not None,
    }


def roster(per_node, degraded=False, total_cpu_available=None):
    if total_cpu_available is None:
        total_cpu_available = sum(
            max(0, entry['cpu_available']) for entry in per_node.values())
    return {
        'total': {
            'cpu_available': total_cpu_available,
            'capacity_degraded': degraded,
        },
        'per_node': per_node,
    }


class FakeCluster:
    """A scripted sequence of resource rosters, and a fake clock.

    The clock advances only when the loop sleeps, so a test can say
    exactly how many polls fit inside a deadline. An entry which is an
    exception instance is raised instead of returned, which is how a
    briefly unreachable endpoint is scripted.
    """

    def __init__(self, answers):
        self.answers = list(answers)
        self.now = 100.0
        self.sleeps = 0
        self.polls = 0

    def poll(self):
        self.polls += 1
        if len(self.answers) > 1:
            answer = self.answers.pop(0)
        else:
            answer = self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += seconds


class WaitForCapacityTestCase(test_base.ShakenFistTestCase):
    def _wait(self, cluster, cpus=2, target=None, deadline=420):
        return retries.wait_for_capacity(
            cluster.poll, cpus, target, cluster.now + deadline,
            clock=cluster.clock, sleep=cluster.sleep)

    def test_room_already_means_no_wait_at_all(self):
        """The common case must cost one poll and no sleep.

        Every retried create pays for this loop, so a wrapper which
        slept once before believing an answer would add ten seconds to
        every refusal in the run.
        """
        cluster = FakeCluster([
            roster({'n1': node(8, cpu_limit=16, cpu_committed=4)})])
        record = self._wait(cluster)

        self.assertTrue(record['satisfied'])
        self.assertEqual(0, cluster.sleeps)
        self.assertEqual(1, record['polls'])
        self.assertEqual(0.0, record['seconds_waited'])
        self.assertEqual('informed', record['mode'])
        self.assertEqual(8, record['headroom_at_start'])
        self.assertEqual(8, record['headroom_at_end'])

    def test_a_wait_ends_when_a_sibling_gives_its_capacity_back(self):
        """The whole point: full now is not full soon.

        The headroom at the first poll and at admission are both kept,
        because a wait record which said only "waited 20s" could not
        tell a reader whether the cluster was one cpu short or empty.
        """
        cluster = FakeCluster([
            roster({'n1': node(0, cpu_limit=16, cpu_committed=16)}),
            roster({'n1': node(1, cpu_limit=16, cpu_committed=15)}),
            roster({'n1': node(4, cpu_limit=16, cpu_committed=12)}),
        ])
        record = self._wait(cluster, cpus=2)

        self.assertTrue(record['satisfied'])
        self.assertEqual(3, record['polls'])
        self.assertEqual(2, cluster.sleeps)
        self.assertEqual(20.0, record['seconds_waited'])
        self.assertEqual(0, record['headroom_at_start'])
        self.assertEqual(4, record['headroom_at_end'])

    def test_a_cluster_which_stays_full_gives_up_at_the_deadline(self):
        """Waiting is bounded, and the roster comes back with the answer.

        The caller fails the test with this record in hand, so a
        cluster which is genuinely too small still produces a failure
        -- a slower one, but one which says what was full.
        """
        cluster = FakeCluster([
            roster({'n1': node(0, cpu_limit=16, cpu_committed=16)})])
        record = self._wait(cluster, cpus=2, deadline=25)

        self.assertFalse(record['satisfied'])
        # 25 seconds of deadline at a 10 second interval is polls at
        # t=0, 10, 20 and a final one at t=30 which sees the deadline
        # passed and returns.
        self.assertEqual(4, record['polls'])
        self.assertEqual(3, cluster.sleeps)
        self.assertEqual(30.0, record['seconds_waited'])
        self.assertEqual({'n1': node(0, cpu_limit=16, cpu_committed=16)},
                         record['per_node'])

    def test_a_degraded_read_waits_blind_and_admits_to_it(self):
        """capacity_degraded means cpu_limit is None for an unrelated reason.

        Every node's limit going missing because the capacity mapping
        could not be read looks identical, to the predicate, to every
        node having no capacity row. Waiting informed on that is
        waiting on noise, so the loop sleeps one interval and hands
        back a record which says the wait told us nothing.
        """
        cluster = FakeCluster([
            roster({'n1': node(64)}, degraded=True)])
        record = self._wait(cluster, cpus=2)

        self.assertEqual('degraded', record['mode'])
        self.assertEqual(1, record['degraded_polls'])
        self.assertFalse(record['satisfied'])
        self.assertEqual(1, cluster.sleeps)
        self.assertEqual(10.0, record['seconds_waited'])
        self.assertIsNone(
            record['headroom_at_start'],
            'A degraded read must not be recorded as a headroom '
            'measurement, or the summary reports noise as data.')

    def test_a_target_missing_from_the_roster_is_not_yet_rather_than_never(self):
        """per_node omits a node for two reasons which both clear.

        summarize_resources() skips a node whose queue is over the
        unreasonable length and a node which has published no metrics
        yet, and a node the cluster has just started shows up a moment
        later. A predicate written as per_node[target]['cpu_available']
        raises KeyError inside the loop for a condition which is
        exactly what the loop is waiting out.
        """
        cluster = FakeCluster([
            roster({'other': node(64, cpu_limit=64)}),
            roster({'other': node(64, cpu_limit=64),
                    'n1': node(8, cpu_limit=16, cpu_committed=8)}),
        ])
        record = self._wait(cluster, cpus=2, target='n1')

        self.assertTrue(record['satisfied'])
        self.assertEqual(2, record['polls'])
        self.assertEqual(
            0, record['headroom_at_start'],
            'An absent node must read as no headroom, not as the '
            'headroom of whichever node happened to be listed.')
        self.assertEqual(8, record['headroom_at_end'])

    def test_a_binding_limit_beats_generous_published_headroom(self):
        """The decision this whole phase rests on.

        A node can measure as idle -- 64 threads of live overcommit
        headroom -- while the capacity row admission is guarded by says
        it has one cpu left, because the row refreshes once a reconcile
        period and the node was filled since. A wait which believed
        cpu_available would stop immediately, re-issue the create, be
        refused again, and record a wait far shorter than the one the
        run actually spent.

        Deleting the cpu_limit clause from node_available_cpus() makes
        this test fail, which was confirmed by doing it.
        """
        cluster = FakeCluster([
            roster({'n1': node(64, cpu_limit=16, cpu_committed=15)})])
        record = self._wait(cluster, cpus=4, target='n1', deadline=5)

        self.assertFalse(
            record['satisfied'],
            'The wait believed the published headroom over the ledger '
            'admission is measured against, so it would stop waiting '
            'during exactly the window the two disagree.')
        self.assertEqual(
            1, record['headroom_at_start'],
            'The headroom recorded must be the binding one (limit 16 '
            'less 15 committed), not the published 64.')

    def test_an_unpinned_create_reads_nodes_and_never_the_cluster_total(self):
        """total['cpu_available'] is a sum, and an instance is not.

        Ten nodes with one spare thread each publish a cluster total of
        ten, and refuse a two cpu create every time.
        """
        per_node = {
            'n%d' % i: node(1, cpu_limit=16, cpu_committed=15)
            for i in range(10)}
        cluster = FakeCluster([roster(per_node)])
        self.assertEqual(10, roster(per_node)['total']['cpu_available'])

        record = self._wait(cluster, cpus=2, deadline=5)

        self.assertFalse(
            record['satisfied'],
            'The wait was satisfied by capacity spread across ten '
            'nodes, which no single instance can be placed into.')
        self.assertEqual(1, record['headroom_at_start'])

    def test_an_unpinned_create_takes_the_best_single_node(self):
        cluster = FakeCluster([roster({
            'n1': node(1, cpu_limit=16, cpu_committed=15),
            'n2': node(6, cpu_limit=16, cpu_committed=10)})])
        record = self._wait(cluster, cpus=4)

        self.assertTrue(record['satisfied'])
        self.assertEqual(6, record['headroom_at_end'])

    def test_a_poll_which_raises_is_not_evidence_about_capacity(self):
        """An unreachable endpoint says nothing, so the wait continues.

        It is recorded rather than swallowed, because a wait which
        ended at its deadline having never once read the roster is a
        different diagnosis from one which read a full cluster the
        whole time.
        """
        cluster = FakeCluster([
            ValueError('connection reset'),
            roster({'n1': node(8, cpu_limit=16, cpu_committed=8)}),
        ])
        record = self._wait(cluster, cpus=2)

        self.assertTrue(record['satisfied'])
        self.assertEqual(2, record['polls'])
        self.assertEqual(['ValueError: connection reset'],
                         record['poll_errors'])

    def test_a_node_with_no_capacity_row_is_read_as_published(self):
        """cpu_limit None means admission is guarded by nothing.

        A node the reconciler has not sized is charged nothing by the
        guard, so the published headroom is both all we know and the
        right answer.
        """
        cluster = FakeCluster([roster({'n1': node(8, cpu_limit=None)})])
        record = self._wait(cluster, cpus=4, target='n1')

        self.assertTrue(record['satisfied'])
        self.assertEqual(8, record['headroom_at_end'])


class _WrapperHarness(ci_base.BaseTestCase):
    """Drives create_instance() without running the suite's setUp.

    setUp() builds a real apiclient and writes a tracing event to
    /srv/ci/traces, neither of which a unit test should do, so the
    harness supplies the four things the wrapper touches instead.
    """

    def __init__(self, client):
        # Deliberately not calling TestCase.__init__: this is not being
        # run as a test, only used as a bound self for the wrapper.
        self.test_client = client
        self.system_client = client
        self.details = {}
        self.failure = None
        self.uniquifiers = 0

    def _uniquifier(self):
        self.uniquifiers += 1
        return 'uniq%04d' % self.uniquifiers

    def addDetail(self, name, detail):
        self.details[name] = detail

    def fail(self, message):
        self.failure = message
        raise AssertionError(message)


class FakeInstanceClient:
    """Answers create_instance() from a script, and records what it saw."""

    def __init__(self, answers, resources=None):
        self.answers = list(answers)
        self.calls = []
        self.resources = resources or roster(
            {'n1': node(64, cpu_limit=64)})

    def create_instance(self, name, cpus, memory, network, disk, sshkey,
                        userdata, **kwargs):
        self.calls.append((name, cpus, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get_cluster_resources(self):
        return self.resources


class CreateInstanceWrapperTestCase(test_base.ShakenFistTestCase):
    def test_a_create_which_is_not_refused_is_not_wrapped_at_all(self):
        client = FakeInstanceClient([{'uuid': 'inst-1'}])
        harness = _WrapperHarness(client)

        inst = harness.create_instance(
            'plain', 1, 1024, None, [], None, None)

        self.assertEqual({'uuid': 'inst-1'}, inst)
        self.assertEqual([('plain', 1, {'force_placement': None})],
                         client.calls)
        self.assertEqual({}, harness.details)

    def test_an_affinity_refusal_is_not_waited_out(self):
        """A 409 does not become satisfiable by waiting.

        The server keeps the affinity refusal apart from the capacity
        one only by the ordering of two except clauses, because the
        affinity exception is a subclass of the low resource one there.
        A wrapper which caught the family rather than the exact
        capacity exception would spend seven minutes turning a clear
        failure into a slow one.
        """
        refusal = ci_apiclient.ResourceStateConflictException(
            'affinity constraint cannot be satisfied', status_code=409)
        client = FakeInstanceClient([refusal])
        harness = _WrapperHarness(client)

        with mock.patch.object(ci_base.retries, 'wait_for_capacity') as waiter:
            self.assertRaises(
                ci_apiclient.ResourceStateConflictException,
                harness.create_instance,
                'affine', 1, 1024, None, [], None, None,
                force_placement='n1')

        self.assertEqual(
            1, len(client.calls),
            'The 409 was retried, so an unsatisfiable affinity '
            'constraint would be waited out for the whole deadline.')
        waiter.assert_not_called()
        self.assertIsNone(harness.failure)

    def test_a_retried_create_uses_a_fresh_name(self):
        """The refused instance holds the caller's name until it is gone.

        A refusal creates the instance and then enqueues a delete for
        it, which completes asynchronously, so re-issuing under the
        same name races the delete for a duplicate-name refusal that
        has nothing to do with capacity.
        """
        refusal = ci_apiclient.InsufficientResourcesException(
            'no node has 4 cpus', status_code=507, text='no node has 4 cpus')
        client = FakeInstanceClient([refusal, {'uuid': 'inst-2'}])
        harness = _WrapperHarness(client)

        satisfied = {'satisfied': True, 'mode': 'informed',
                     'seconds_waited': 12.5, 'node': 'n1',
                     'headroom_at_start': 0, 'headroom_at_end': 4,
                     'per_node': {}, 'polls': 2, 'poll_errors': []}
        with mock.patch.object(ci_base.retries, 'wait_for_capacity',
                               return_value=dict(satisfied)) as waiter:
            inst = harness.create_instance(
                'retried', 4, 1024, None, [], None, None,
                force_placement='n1')

        self.assertEqual({'uuid': 'inst-2'}, inst)
        self.assertEqual('retried', client.calls[0][0])
        self.assertEqual(
            'retried-uniq0001', client.calls[1][0],
            'The retry reused the refused instance\'s name, which is '
            'still in use until its error delete completes.')

        # The wait watches the node the create was pinned to, and asks
        # for the cpus the create asked for.
        self.assertEqual(4, waiter.call_args[0][1])
        self.assertEqual('n1', waiter.call_args[0][2])

        self.assertEqual(
            ['capacity-wait-1'], list(harness.details),
            'A wait which is not attached to the test is a wait the '
            'run cannot be asked about afterwards.')

    def test_the_deadline_fails_with_the_refusal_and_the_roster(self):
        """A genuinely too-small cloud must still fail, and say what was full."""
        refusal = ci_apiclient.InsufficientResourcesException(
            'no node has 4 cpus', status_code=507,
            text='instance could not be placed')
        client = FakeInstanceClient([refusal])
        harness = _WrapperHarness(client)

        exhausted = {'satisfied': False, 'mode': 'informed',
                     'seconds_waited': 420.0, 'node': 'n1',
                     'headroom_at_start': 0, 'headroom_at_end': 0,
                     'per_node': {'n1': node(0, cpu_limit=16,
                                             cpu_committed=16)},
                     'polls': 43, 'poll_errors': ['ValueError: reset']}
        with mock.patch.object(ci_base.retries, 'wait_for_capacity',
                               return_value=dict(exhausted)):
            with mock.patch.object(ci_base.time, 'time',
                                   side_effect=[0.0, 0.0, 10000.0]):
                self.assertRaises(
                    AssertionError, harness.create_instance,
                    'toobig', 4, 1024, None, [], None, None,
                    force_placement='n1')

        self.assertIn('instance could not be placed', harness.failure)
        self.assertIn('n1', harness.failure)
        self.assertIn('cpu_committed', harness.failure)
        self.assertIn('ValueError: reset', harness.failure)
        self.assertIn('420', harness.failure)

    def test_a_degraded_wait_retries_the_create_at_the_same_cadence(self):
        """A blind wait is still a wait, and it still gets recorded.

        D10's fallback is to keep re-issuing at the poll interval, so
        the create must be retried after a degraded wait rather than
        the wrapper concluding anything from it.
        """
        refusal = ci_apiclient.InsufficientResourcesException(
            'full', status_code=507, text='full')
        client = FakeInstanceClient([refusal, {'uuid': 'inst-3'}])
        harness = _WrapperHarness(client)

        degraded = {'satisfied': False, 'mode': 'degraded',
                    'seconds_waited': 10.0, 'node': None,
                    'degraded_polls': 1, 'headroom_at_start': None,
                    'headroom_at_end': None, 'per_node': {},
                    'polls': 1, 'poll_errors': []}
        with mock.patch.object(ci_base.retries, 'wait_for_capacity',
                               return_value=dict(degraded)):
            inst = harness.create_instance(
                'blind', 1, 1024, None, [], None, None)

        self.assertEqual({'uuid': 'inst-3'}, inst)
        self.assertEqual(2, len(client.calls))
        self.assertEqual(['capacity-wait-1'], list(harness.details))

    def test_recording_a_wait_may_never_fail_the_test_it_measures(self):
        """An instrument which breaks the run is worse than no instrument."""
        harness = _WrapperHarness(FakeInstanceClient([]))

        def explode(name, detail):
            raise OSError('no space left on device')

        harness.addDetail = explode
        harness._record_capacity_wait({'instance_name': 'x', 'mode': 'informed'})
