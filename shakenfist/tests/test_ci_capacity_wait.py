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
import json
import logging
import os
import shutil
import sys
import tempfile
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


def node(cpu_available, cpu_limit=None, cpu_committed=0,
         ram_available=64 * 1024, disk_available=1024,
         cpu_max_per_instance=None, ram_max_per_instance=None):
    """One entry as summarize_resources() publishes it, trimmed.

    Memory and disk default to far more than any test here asks for, so
    a test about cpus is about cpus. The two per-instance ceilings are
    omitted unless a test asks for them, because a node which has
    published no metrics for them omits them too.
    """
    entry = {
        'cpu_available': cpu_available,
        'cpu_limit': cpu_limit,
        'cpu_committed': cpu_committed,
        'cpu_committed_row_present': cpu_limit is not None,
        'ram_available': ram_available,
        'disk_available': disk_available,
    }
    if cpu_max_per_instance is not None:
        entry['cpu_max_per_instance'] = cpu_max_per_instance
    if ram_max_per_instance is not None:
        entry['ram_max_per_instance'] = ram_max_per_instance
    return entry


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


class RequestedDiskTestCase(test_base.ShakenFistTestCase):
    """The disk a create asks for, counted as the scheduler counts it."""

    def test_the_sizes_are_summed(self):
        self.assertEqual(28, retries.requested_disk_gb(
            [{'size': 20, 'type': 'disk'}, {'size': 8, 'type': 'disk'}]))

    def test_a_sizeless_disk_asks_for_nothing(self):
        """_has_sufficient_disk() ignores them, so this must too.

        A cdrom is exactly the size of its base image and carries no
        size, and charging it as zero is what the server does.
        """
        self.assertEqual(8, retries.requested_disk_gb(
            [{'size': 8, 'type': 'disk'},
             {'type': 'cdrom', 'base': 'sf://upload/system/debian-12'},
             {'size': None, 'type': 'disk'}]))

    def test_no_disk_spec_at_all_asks_for_nothing(self):
        self.assertEqual(0, retries.requested_disk_gb(None))
        self.assertEqual(0, retries.requested_disk_gb([]))

    def test_a_size_which_is_not_a_number_is_skipped_rather_than_fatal(self):
        """The wrapper must not raise on a spec the server would reject.

        A malformed disk spec is the server's 400 to give, and it gives a
        far better message than a TypeError from inside a wait predicate.
        """
        self.assertEqual(8, retries.requested_disk_gb(
            [{'size': 8}, {'size': 'banana'}, 'not-a-dict']))


class WaitForCapacityTestCase(test_base.ShakenFistTestCase):
    def _wait(self, cluster, cpus=2, memory_mb=1024, disk_gb=8, target=None,
              deadline=420, minimum_sleep=0):
        return retries.wait_for_capacity(
            cluster.poll, cpus, memory_mb, disk_gb, target,
            cluster.now + deadline, clock=cluster.clock, sleep=cluster.sleep,
            minimum_sleep=minimum_sleep)

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
        self.assertEqual(8, record['headroom_at_start']['cpus'])
        self.assertEqual(8, record['headroom_at_end']['cpus'])
        self.assertIsNone(
            record['binding_dimension'],
            'Nothing was short, so nothing was waited on.')

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
        self.assertEqual(0, record['headroom_at_start']['cpus'])
        self.assertEqual(4, record['headroom_at_end']['cpus'])
        self.assertEqual('cpus', record['binding_dimension'])

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
            0, record['headroom_at_start']['cpus'],
            'An absent node must read as no headroom, not as the '
            'headroom of whichever node happened to be listed.')
        self.assertEqual(8, record['headroom_at_end']['cpus'])

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
            1, record['headroom_at_start']['cpus'],
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
        self.assertEqual(1, record['headroom_at_start']['cpus'])

    def test_an_unpinned_create_takes_the_best_single_node(self):
        cluster = FakeCluster([roster({
            'n1': node(1, cpu_limit=16, cpu_committed=15),
            'n2': node(6, cpu_limit=16, cpu_committed=10)})])
        record = self._wait(cluster, cpus=4)

        self.assertTrue(record['satisfied'])
        self.assertEqual(6, record['headroom_at_end']['cpus'])

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

    def test_a_create_waits_on_memory_the_same_way_it_waits_on_cpus(self):
        """The dimension most likely to bind in CI, and the one D8 missed.

        CI instances are 1 vCPU and 1-2 GB, so a node runs out of memory
        long before it runs out of threads. A predicate which read only
        cpus would report "there is room now" for the whole deadline
        while the server went on refusing, and the caller would re-issue
        as fast as two HTTP round trips allow -- leaving an
        error-deleted instance behind every time.
        """
        cluster = FakeCluster([
            roster({'n1': node(64, cpu_limit=64, ram_available=512)}),
            roster({'n1': node(64, cpu_limit=64, ram_available=4096)}),
        ])
        record = self._wait(cluster, cpus=1, memory_mb=2048, target='n1')

        self.assertTrue(record['satisfied'])
        self.assertEqual(2, record['polls'])
        self.assertEqual(
            'memory_mb', record['binding_dimension'],
            'The wait must say what it waited on, or phase 5 reads a '
            'duration with no cause attached to it.')
        self.assertEqual(512, record['headroom_at_start']['memory_mb'])
        self.assertEqual(4096, record['headroom_at_end']['memory_mb'])

    def test_a_create_waits_on_disk_the_same_way(self):
        cluster = FakeCluster([
            roster({'n1': node(64, cpu_limit=64, disk_available=4)})])
        record = self._wait(cluster, cpus=1, disk_gb=20, target='n1',
                            deadline=5)

        self.assertFalse(record['satisfied'])
        self.assertEqual('disk_gb', record['binding_dimension'])

    def test_a_node_must_cover_every_dimension_at_once(self):
        """Two nodes which each cover half the request cover none of it.

        The same error as reading total['cpu_available'], one level down:
        an instance is placed on one node and needs all three of its
        dimensions there.
        """
        cluster = FakeCluster([roster({
            'plenty-of-cpu': node(64, cpu_limit=64, ram_available=512),
            'plenty-of-ram': node(1, cpu_limit=16, cpu_committed=15,
                                  ram_available=64 * 1024)})])
        record = self._wait(cluster, cpus=4, memory_mb=2048, deadline=5)

        self.assertFalse(
            record['satisfied'],
            'The wait was satisfied by cpus on one node and memory on '
            'another, which no single instance can be placed into.')

    def test_a_per_instance_ceiling_is_not_aggregate_headroom(self):
        """cpu_max_per_instance bounds one instance, not the node.

        A node can publish ample aggregate headroom and still refuse a
        create larger than the biggest single instance it will take, and
        that refusal is permanent rather than transient -- so a predicate
        which ignored the ceiling is satisfied forever while the server
        refuses forever.
        """
        cluster = FakeCluster([roster({
            'n1': node(64, cpu_limit=64, cpu_max_per_instance=2)})])
        record = self._wait(cluster, cpus=4, target='n1', deadline=5)

        self.assertFalse(record['satisfied'])
        self.assertEqual(2, record['headroom_at_start']['cpus'])
        self.assertEqual('cpus', record['binding_dimension'])

    def test_a_missing_per_instance_ceiling_does_not_read_as_zero(self):
        """A node publishing no cpu_max_per_instance metric publishes a 0.

        Reading that as "this node will take no instances" would make
        every wait against a node mid-upgrade run to its deadline.
        """
        entry = node(8, cpu_limit=16, cpu_committed=8,
                     cpu_max_per_instance=0)
        cluster = FakeCluster([roster({'n1': entry})])
        record = self._wait(cluster, cpus=4, target='n1', deadline=5)

        self.assertTrue(record['satisfied'])
        self.assertEqual(8, record['headroom_at_start']['cpus'])

    def test_a_memory_ceiling_of_zero_is_a_real_full_node(self):
        """ram_max_per_instance differs from the cpu one, deliberately.

        It is memory_available less the node's reservation, which is
        legitimately zero or negative on a genuinely full node rather
        than a sign of a missing metric, so it is read whenever present.
        """
        cluster = FakeCluster([roster({
            'n1': node(64, cpu_limit=64, ram_available=64 * 1024,
                       ram_max_per_instance=0)})])
        record = self._wait(cluster, cpus=1, memory_mb=1024, target='n1',
                            deadline=5)

        self.assertFalse(record['satisfied'])
        self.assertEqual('memory_mb', record['binding_dimension'])

    def test_a_dimension_the_create_asks_nothing_of_cannot_bind(self):
        """A sizeless disk spec asks for no disk.

        A node with no disk left would otherwise never satisfy a create
        which wants none of it, and the wait would run to its deadline
        over a dimension nobody asked about.
        """
        cluster = FakeCluster([roster({
            'n1': node(8, cpu_limit=16, cpu_committed=8, disk_available=0)})])
        record = self._wait(cluster, cpus=1, disk_gb=0, target='n1',
                            deadline=5)

        self.assertTrue(record['satisfied'])

    def test_a_satisfied_wait_can_be_paced_by_its_caller(self):
        """The floor which stops a caller busy-looping on a blind refusal.

        A satisfied wait hands control straight back to a caller which
        has just been refused. If the refusal is for something this
        predicate cannot see, satisfied is the permanent answer, and an
        unpaced caller re-issues as fast as two HTTP round trips allow
        until its deadline.
        """
        cluster = FakeCluster([
            roster({'n1': node(64, cpu_limit=64)})])
        record = self._wait(cluster, cpus=1, target='n1', minimum_sleep=10)

        self.assertTrue(record['satisfied'])
        self.assertEqual(1, record['polls'])
        self.assertEqual(1, cluster.sleeps)
        self.assertEqual(
            10.0, record['seconds_waited'],
            'A paced wait must report the time it actually spent, or the '
            'trace under-reports what the run cost.')

    def test_pacing_never_adds_to_a_wait_which_already_waited(self):
        """The floor is a minimum, not a tax on every wait."""
        cluster = FakeCluster([
            roster({'n1': node(0, cpu_limit=16, cpu_committed=16)}),
            roster({'n1': node(8, cpu_limit=16, cpu_committed=8)}),
        ])
        record = self._wait(cluster, cpus=1, target='n1', minimum_sleep=10)

        self.assertTrue(record['satisfied'])
        self.assertEqual(1, cluster.sleeps)
        self.assertEqual(10.0, record['seconds_waited'])

    def test_a_node_with_no_capacity_row_is_read_as_published(self):
        """cpu_limit None means admission is guarded by nothing.

        A node the reconciler has not sized is charged nothing by the
        guard, so the published headroom is both all we know and the
        right answer.
        """
        cluster = FakeCluster([roster({'n1': node(8, cpu_limit=None)})])
        record = self._wait(cluster, cpus=4, target='n1')

        self.assertTrue(record['satisfied'])
        self.assertEqual(8, record['headroom_at_end']['cpus'])


class _WrapperHarness(ci_base.BaseTestCase):
    """Drives create_instance() without running the suite's setUp.

    setUp() builds a real apiclient and writes a tracing event to
    /srv/ci/traces, neither of which a unit test should do, so the
    harness supplies the four things the wrapper touches instead.
    """

    def __init__(self, client, clock=None, advancing=False):
        # Deliberately not calling TestCase.__init__: this is not being
        # run as a test, only used as a bound self for the wrapper.
        self.test_client = client
        self.system_client = client
        self.details = {}
        self.failure = None
        self.uniquifiers = 0
        self.clock = list(clock or [0.0])
        self.advancing = advancing
        self.slept = []

    def _uniquifier(self):
        self.uniquifiers += 1
        return 'uniq%04d' % self.uniquifiers

    def id(self):
        # _append_capacity_wait_trace() reads this for the trace record's
        # test_id, and it is the only field with no source in the wait
        # dict. A harness without it makes every write raise into the
        # swallowing except clause, so the trace path looks exercised and
        # is not.
        return 'shakenfist_ci.tests.test_harness.FakeTest.test_create'

    def _now(self):
        """The scripted clock create_instance() reads its deadline from.

        The last value sticks rather than the script running out. A test
        here is saying "and then the deadline had passed", which stays
        true however many times the wrapper asks, so an extra read is
        not an error worth raising on. Scripting this rather than
        patching time.time() is what keeps the module's log lines out of
        the clock -- see BaseTestCase._now() for what that cost once.

        An 'advancing' harness is the other mode: one clock which moves
        only when something sleeps, for a test which drives the real
        wait_for_capacity() through the wrapper and needs the two to
        agree about what time it is.
        """
        if self.advancing:
            return self.clock[0]
        if len(self.clock) > 1:
            return self.clock.pop(0)
        return self.clock[0]

    def _sleep(self, seconds):
        """The seam which keeps a real wait out of real wall clock time."""
        self.slept.append(seconds)
        if self.advancing:
            self.clock[0] += seconds

    def addDetail(self, name, detail):
        self.details[name] = detail

    def fail(self, message):
        self.failure = message
        raise AssertionError(message)


class FakeInstanceClient:
    """Answers create_instance() from a script, and records what it saw.

    A script of one entry repeats that entry forever, which is how "the
    server refuses this create for a reason the roster does not show"
    is written down.
    """

    def __init__(self, answers, resources=None, repeat_last=False):
        self.answers = list(answers)
        self.repeat_last = repeat_last
        self.calls = []
        self.resources = resources or roster(
            {'n1': node(64, cpu_limit=64)})

    def create_instance(self, name, cpus, memory, network, disk, sshkey,
                        userdata, **kwargs):
        self.calls.append((name, cpus, kwargs))
        if self.repeat_last and len(self.answers) == 1:
            answer = self.answers[0]
        else:
            answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get_cluster_resources(self):
        return self.resources


class CreateInstanceWrapperTestCase(test_base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.trace = os.path.join(self.tempdir, 'instance-waits.jsonl')
        patcher = mock.patch.object(
            ci_base, 'CAPACITY_WAIT_TRACE_FILE', self.trace)
        patcher.start()
        self.addCleanup(patcher.stop)

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
                     'headroom_at_start': {'cpus': 0}, 'headroom_at_end': {'cpus': 4},
                     'per_node': {}, 'polls': 2, 'poll_errors': []}
        with mock.patch.object(ci_base.retries, 'wait_for_capacity',
                               return_value=dict(satisfied)) as waiter:
            inst = harness.create_instance(
                'retried', 4, 1024, None,
                [{'size': 12, 'type': 'disk'},
                 {'size': 8, 'type': 'disk'},
                 {'type': 'cdrom'}],
                None, None, force_placement='n1')

        self.assertEqual({'uuid': 'inst-2'}, inst)
        self.assertEqual('retried', client.calls[0][0])
        self.assertEqual(
            'retried-uniq0001', client.calls[1][0],
            'The retry reused the refused instance\'s name, which is '
            'still in use until its error delete completes.')

        # The wait watches the node the create was pinned to, and asks
        # for the whole size the create asked for -- all three dimensions
        # the scheduler pre-filters on, not just the cpus.
        self.assertEqual(4, waiter.call_args[0][1])
        self.assertEqual(1024, waiter.call_args[0][2])
        self.assertEqual(20, waiter.call_args[0][3])
        self.assertEqual('n1', waiter.call_args[0][4])

        self.assertEqual(
            ['capacity-wait-1'], list(harness.details),
            'A wait which is not attached to the test is a wait the '
            'run cannot be asked about afterwards.')

    def test_a_retry_cannot_build_a_name_the_server_will_reject(self):
        """63 characters, or a 400 which reads as an unrelated failure.

        external_api/instance.py rejects a name over 63 characters, and a
        retry appends a hyphen and eight characters. A caller near the
        limit would see its capacity retry come back as a
        RequestMalformedException, which is not caught here and so escapes
        the wrapper -- reported as a malformed request rather than as the
        capacity problem it actually is. TestDistroBoots builds its name
        from a base image name, so the length is not entirely under the
        suite's control.
        """
        refusal = ci_apiclient.InsufficientResourcesException(
            'full', status_code=507, text='full')
        client = FakeInstanceClient([refusal, {'uuid': 'inst-7'}])
        harness = _WrapperHarness(client)

        long_name = 'x' * 63
        satisfied = {'satisfied': True, 'mode': 'informed',
                     'seconds_waited': 10.0, 'node': None, 'cpus': 1,
                     'headroom_at_start': {'cpus': 0},
                     'headroom_at_end': {'cpus': 2},
                     'per_node': {}, 'polls': 2, 'poll_errors': []}
        with mock.patch.object(ci_base.retries, 'wait_for_capacity',
                               return_value=dict(satisfied)):
            harness.create_instance(long_name, 1, 1024, None, [], None, None)

        retried = client.calls[1][0]
        self.assertLessEqual(
            len(retried), 63,
            'The retry built a %d character name, which the server '
            'rejects with a 400 the wrapper does not catch.' % len(retried))
        self.assertTrue(retried.startswith('x' * 54))
        self.assertEqual(
            'x' * 63, client.calls[0][0],
            'Only a retry is trimmed. The first attempt must use the '
            'name the caller asked for.')

    def test_the_deadline_fails_with_the_refusal_and_the_roster(self):
        """A genuinely too-small cloud must still fail, and say what was full."""
        refusal = ci_apiclient.InsufficientResourcesException(
            'no node has 4 cpus', status_code=507,
            text='instance could not be placed')
        client = FakeInstanceClient([refusal])
        # Inside the deadline for the first check, past it for the second,
        # which is what makes the wrapper give up rather than loop.
        harness = _WrapperHarness(client, clock=[0.0, 0.0, 10000.0])

        exhausted = {'satisfied': False, 'mode': 'informed',
                     'seconds_waited': 420.0, 'node': 'n1',
                     'headroom_at_start': {'cpus': 0}, 'headroom_at_end': {'cpus': 0},
                     'per_node': {'n1': node(0, cpu_limit=16,
                                             cpu_committed=16)},
                     'polls': 43, 'poll_errors': ['ValueError: reset']}
        with mock.patch.object(ci_base.retries, 'wait_for_capacity',
                               return_value=dict(exhausted)):
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

    def test_a_refusal_the_predicate_cannot_see_does_not_busy_loop(self):
        """The seam between the wrapper and the real wait, unpatched.

        Every other test here hands the wrapper a canned wait record, so
        none of them can see what happens when the two disagree -- and
        that disagreement is the whole failure mode. The server refuses
        every create while the roster reports ample room in all three
        dimensions, which is what a refusal for something the predicate
        does not model looks like: an admission guard losing a race, a
        pre-filter this does not read, a 507 from a path the roster says
        nothing about.

        With no floor under the loop the wrapper re-issues as fast as two
        HTTP round trips allow for the whole seven minutes, leaving an
        error-deleted instance and a trace line behind on every turn --
        thousands of each from one refusal. With the floor the loop runs
        at the poll interval, so the attempts are bounded by the deadline
        and the clock really moves between them.
        """
        refusal = ci_apiclient.InsufficientResourcesException(
            'full', status_code=507, text='no node would take it')
        client = FakeInstanceClient([refusal], repeat_last=True)
        harness = _WrapperHarness(client, clock=[0.0], advancing=True)

        self.assertRaises(
            AssertionError, harness.create_instance,
            'blind', 1, 1024, None, [{'size': 8, 'type': 'disk'}], None, None)

        ceiling = (ci_base.CLUSTER_HEADROOM_WAIT //
                   ci_base.CAPACITY_POLL_INTERVAL) + 2
        self.assertLessEqual(
            len(client.calls), ceiling,
            'The wrapper issued %d creates inside one deadline. Each one '
            'leaves an error-deleted instance behind, so an unpaced loop '
            'turns a single transient refusal into an outage.'
            % len(client.calls))
        self.assertGreater(
            len(client.calls), 1,
            'The create was not retried at all, so this proves nothing '
            'about pacing.')
        self.assertEqual(
            len(client.calls) - 2, len(harness.slept),
            'The first retry is free -- capacity returning between the '
            'refusal and the poll is the common case -- and every retry '
            'after that must be preceded by a sleep.')
        self.assertGreater(
            harness.clock[0], ci_base.CLUSTER_HEADROOM_WAIT / 2,
            'The clock barely moved, so the loop was spinning rather '
            'than waiting.')

    def test_the_attempt_ceiling_says_what_it_means(self):
        """The failure a blind refusal produces must name its own cause.

        "The cluster did not free enough" is the wrong diagnosis when
        every poll said there was room. The message has to send the
        reader at the predicate instead.
        """
        refusal = ci_apiclient.InsufficientResourcesException(
            'full', status_code=507, text='no node would take it')
        client = FakeInstanceClient([refusal], repeat_last=True)
        harness = _WrapperHarness(client, clock=[0.0], advancing=True)

        self.assertRaises(
            AssertionError, harness.create_instance,
            'blind', 1, 1024, None, [{'size': 8, 'type': 'disk'}], None, None)

        self.assertIn('no node would take it', harness.failure)
        self.assertIn(
            'every wait said there was room', harness.failure,
            'The failure must say that nothing bound, which is the whole '
            'diagnosis: the refusal is for something the wait cannot see.')

    def test_a_wait_which_really_ends_lets_the_create_through(self):
        """The positive control for the test above.

        Without it, a wrapper which had simply stopped retrying would
        pass every assertion in test_a_refusal_the_predicate_cannot_see
        _does_not_busy_loop.
        """
        refusal = ci_apiclient.InsufficientResourcesException(
            'full', status_code=507, text='full')
        client = FakeInstanceClient(
            [refusal, refusal, {'uuid': 'inst-6'}],
            resources=roster({'n1': node(64, cpu_limit=64)}))
        harness = _WrapperHarness(client, clock=[0.0], advancing=True)

        inst = harness.create_instance(
            'eventually', 1, 1024, None, [{'size': 8, 'type': 'disk'}],
            None, None)

        self.assertEqual({'uuid': 'inst-6'}, inst)
        self.assertEqual(3, len(client.calls))
        self.assertEqual(
            [ci_base.CAPACITY_POLL_INTERVAL], harness.slept,
            'The first wait is free and every later one is paced, so two '
            'retries cost exactly one interval.')

    def test_recording_a_wait_may_never_fail_the_test_it_measures(self):
        """An instrument which breaks the run is worse than no instrument."""
        harness = _WrapperHarness(FakeInstanceClient([]))

        def explode(name, detail):
            raise OSError('no space left on device')

        harness.addDetail = explode
        harness._record_capacity_wait({'instance_name': 'x', 'mode': 'informed'})

    def test_a_wait_appends_one_well_formed_line_to_the_trace(self):
        """The trace is this phase's only output, so its shape is asserted.

        D14 makes the JSONL file the evidence a later phase reads the
        baseline from, and tools/ci_headroom_report.py --waits parses it
        one line at a time. A record missing a field the report names
        does not fail anything at write time; it shows up as a malformed
        line in a bundle weeks later, which is the wrong place to find
        out.
        """
        refusal = ci_apiclient.InsufficientResourcesException(
            'full', status_code=507, text='full')
        client = FakeInstanceClient([refusal, {'uuid': 'inst-4'}])
        harness = _WrapperHarness(client)

        satisfied = {'satisfied': True, 'mode': 'informed',
                     'seconds_waited': 30.0, 'node': 'n1', 'cpus': 2,
                     'memory_mb': 1024, 'disk_gb': 8,
                     'binding_dimension': 'memory_mb',
                     'headroom_at_start': {'cpus': 0},
                     'headroom_at_end': {'cpus': 4},
                     'per_node': {}, 'polls': 4, 'poll_errors': []}
        with mock.patch.object(ci_base.retries, 'wait_for_capacity',
                               return_value=dict(satisfied)):
            harness.create_instance(
                'traced', 2, 1024, None, [{'size': 8, 'type': 'disk'}], None,
                None, force_placement='n1')

        with open(self.trace) as f:
            lines = f.read().splitlines()

        self.assertEqual(
            1, len(lines),
            'One line per wait, not per run (D14): a worker which crashes '
            'mid-wait must lose at most its own in-flight line.')
        record = json.loads(lines[0])
        self.assertEqual('traced', record['instance_name'])
        self.assertEqual('n1', record['node'])
        self.assertEqual(2, record['cpus'])
        self.assertEqual(30.0, record['seconds_waited'])
        self.assertEqual('informed', record['mode'])
        self.assertEqual(
            1, record['attempt_number'],
            'The field is a 1-indexed position, not a count: a create '
            'refused three times writes three lines carrying 1, 2 and 3, '
            'and a reader who summed a field called "attempts" would get '
            'six.')
        self.assertEqual(1024, record['memory_mb'])
        self.assertEqual(8, record['disk_gb'])
        self.assertEqual('memory_mb', record['binding_dimension'])
        self.assertEqual({'cpus': 0}, record['headroom_at_first_refusal'])
        self.assertEqual({'cpus': 4}, record['headroom_at_admission'])
        self.assertIn('test_create', record['test_id'])

    def test_an_unwritable_trace_never_fails_the_create(self):
        """The trace is an instrument, and shares their one absolute rule.

        ci_headroom_collect.sh gives the reasoning for a job; it holds
        the same way for a test. /srv/ci/traces does not exist on a
        workstation, so this is the ordinary case when the suite is run
        outside CI rather than an exotic one.
        """
        refusal = ci_apiclient.InsufficientResourcesException(
            'full', status_code=507, text='full')
        client = FakeInstanceClient([refusal, {'uuid': 'inst-5'}])
        harness = _WrapperHarness(client)

        satisfied = {'satisfied': True, 'mode': 'informed',
                     'seconds_waited': 10.0, 'node': None, 'cpus': 1,
                     'headroom_at_start': {'cpus': 0}, 'headroom_at_end': {'cpus': 2},
                     'per_node': {}, 'polls': 2, 'poll_errors': []}
        with mock.patch.object(
                ci_base, 'CAPACITY_WAIT_TRACE_FILE',
                os.path.join(self.tempdir, 'no', 'such', 'waits.jsonl')):
            with mock.patch.object(ci_base.retries, 'wait_for_capacity',
                                   return_value=dict(satisfied)):
                inst = harness.create_instance(
                    'untraced', 1, 1024, None, [], None, None)

        self.assertEqual({'uuid': 'inst-5'}, inst)
        self.assertEqual(
            ['capacity-wait-1'], list(harness.details),
            'The wait was still attached to the test, so a missing trace '
            'file costs the bundle record and nothing else.')
