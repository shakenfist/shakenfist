"""Functional coverage of agent operation dependency edges.

An agent operation can name other agent operations which must finish before
it runs: depends_on, which is fate sharing, and runs_after, which is
ordering only. The sidechannel dispatcher evaluates both between dequeue and
dispatch, and cascades a dependent whose depends_on dependency failed to the
abort terminal state, recording the cause in dependency_failure. Until this
file existed nothing in functional CI created an edge at all, so neither the
evaluation nor the cascade had ever run against a real cluster.

Both tests here assert on which state an operation reached and on why, never
on how long anything took. CI hardware is contended and shared, so every
absolute bound below is insurance against a hang rather than a statement
about latency.

Submitting an edge needs a client which knows how: apiclient's module level
agent_dependency() and the depends_on keyword on the three agent verbs. An
older client fails these tests with an AttributeError out of
agent_dependency() rather than with a wrong answer, which is the right way
round -- but it is also why this file cannot pass until that client is the
one CI checks out.
"""

import json
import time

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


# How long the blocking operation in each test occupies an instance's single
# executor slot. It only has to outlive what is queued behind it -- a
# deadline which passes five seconds after its operation is created, or the
# handful of dispatcher passes the ordering test wants to observe -- so this
# is six times what is strictly needed. Cheap insurance in a suite that runs
# on every pull request, where the alternative is a test that occasionally
# proves nothing.
#
# It also has to stay comfortably inside the dispatcher's dependency wait
# budget (AGENT_OPERATION_DEPENDENCY_WAIT, 300 seconds by default), because
# a dependent held longer than that is aborted for running out of budget
# rather than ordered -- which would make the ordering test below pass for
# the wrong reason. No mirror constant for that budget: nothing here asserts
# against it, and a mirror with no reader is just a maintenance trap.
BLOCKER_SECONDS = 30

# The deadline given to the operation which is expected to expire, so that
# its failure can be cascaded to its dependent. Small enough that it has
# certainly passed by the time the executor slot frees, and deliberately not
# zero: deadline_seconds=0 means "no wall-clock deadline at all" to the API,
# not "expire immediately".
SHORT_DEADLINE_SECONDS = 5

# An absolute bound on every wait below. These exist only to turn a hung
# test into a diagnosable failure -- an operation which aborts in ten
# seconds and one which aborts in two minutes are both a pass -- so they are
# deliberately generous, in the same spirit as base.py's
# AGENT_OPERATION_TIMEOUT.
AGENTOP_STATE_TIMEOUT = 300

# How long to leave between polls of an operation's state. Matched to the
# dispatcher's own DISPATCH_CHECK_INTERVAL (shakenfist/daemons/sidechannel/
# main.py), because polling faster than the thing being observed can change
# only spends API requests, and polling much slower would let the ordering
# test's window close between two reads.
AGENTOP_POLL_SECONDS = 5

# How many polls the ordering test must have seen with its dependency still
# unfinished and its dependent still undispatched before it will believe it
# proved anything. BLOCKER_SECONDS at AGENTOP_POLL_SECONDS offers about six,
# so this is a floor well under what a healthy run produces -- it is here to
# fail a run where the blocker died immediately, which would otherwise pass
# without the test ever having had a window to observe.
ORDERING_OBSERVATIONS_REQUIRED = 2


class TestAgentOperationDependencies(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'aopdeps'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()

        # The client only sends depends_on and runs_after to a server which
        # advertises this capability, and raises IncapableException rather
        # than silently dropping an edge against one which does not. So
        # against an incapable server these tests would fail out of the
        # create call with an exception about a capability, which is a
        # confusing way to learn that the cluster under test predates
        # dependencies entirely. Fail loudly and legibly here instead.
        self.assertTrue(
            self.test_client.check_capability(
                'agentoperation-dependencies'),
            'The API server does not advertise the '
            'agentoperation-dependencies capability, so the client will not '
            'send the dependency edges these tests are about.')

        # Creating an agent operation through self.test_client blocks.
        # instance_execute(), instance_put_blob() and instance_get() all end
        # in the client's _await_agentop(), which polls until the async
        # strategy's deadline -- 60 seconds for the ASYNC_PAUSE client the
        # base class builds -- and raises AgentOperationFailed the instant it
        # polls a terminal failure state. Both of those are fatal to this
        # file. An operation which is *expected* to abort would arrive as an
        # exception out of the create call rather than as something to
        # inspect, and an operation deliberately held behind a dependency
        # would spend a minute being polled before the test could even
        # submit the thing it is waiting for. An ASYNC_CONTINUE client
        # returns the operation exactly as the POST response described it
        # and never polls, which is what these tests want: they do their own
        # waiting, explicitly and with a bound.
        self.nonblocking_client = apiclient.Client(
            base_url=self.system_client.base_url,
            namespace=self.namespace, key=self.namespace_key,
            async_strategy=apiclient.ASYNC_CONTINUE)

        self.net_one = self.test_client.allocate_network(
            '192.168.246.0/24', True, True, '%s-net-one' % self.namespace,
            provide_dns=True)
        self.addDetail(
            'net_one',
            content.text_content(json.dumps(self.net_one, indent=4,
                                            sort_keys=True)))
        self._await_networks_ready([self.net_one['uuid']])

    def _create_ready_instance(self, name):
        inst = self.create_instance(
            name, 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)
        self.addDetail(
            name,
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])
        return inst

    def test_dependency_orders_across_two_instances(self):
        # The two instances are the whole design of this test, and it is
        # worth saying why before someone simplifies them back to one.
        #
        # The sidechannel dispatcher runs at most one executor per instance,
        # so two operations on the *same* instance are already serialised by
        # that queue whether or not either declares an edge. A test which
        # put both here and asserted the second waited would pass with the
        # depends_on removed, which means it would assert nothing about
        # dependencies at all.
        #
        # Across two instances there is no such queue. Instance two's
        # executor is idle from the moment its operation is created, so an
        # unordered dependent is dispatched on the dispatcher's very next
        # pass. The edge is the only thing that can hold it, and so the
        # assertion below has something to fail against.
        inst_one = self._create_ready_instance('test-aop-dependency-one')
        inst_two = self._create_ready_instance('test-aop-dependency-two')

        # The dependency. It takes the server default deadline, which is
        # twenty times longer than it needs.
        first = self.nonblocking_client.instance_execute(
            inst_one['uuid'], f'sleep {BLOCKER_SECONDS}')

        # The dependent, on the other instance. The client verifies that the
        # edges it sent came back on the operation it created and refuses a
        # cluster which dropped them, so reaching the next line at all means
        # the edge is stored -- there is nothing for this test to check about
        # that.
        second = self.nonblocking_client.instance_execute(
            inst_two['uuid'], 'whoami',
            depends_on=[apiclient.agent_dependency(first)])

        terminal = tuple(self.AGENT_OPERATION_FAILED_STATES) + ('complete',)
        observations = 0
        start_time = time.time()

        while True:
            # The dependent is read *before* its dependency, every time, and
            # that order is load bearing rather than arbitrary. If the
            # dependent is found dispatched at some moment, dispatch must
            # have happened before that moment, so a dependency read
            # afterwards is read strictly later -- and terminal is
            # absorbing, so a dependency which was legitimately finished at
            # dispatch still reads finished now. Reading the dependency
            # first would leave a window in which it finished between the
            # two reads and a perfectly ordered dispatch looked like a
            # violation.
            second = self.test_client.get_agent_operation(second['uuid'])
            first = self.test_client.get_agent_operation(first['uuid'])

            # attempts is written by the executor as it moves an operation
            # to executing, so it is the signal which cannot be missed
            # between two polls: a whoami which was dispatched and completed
            # inside one poll interval still carries the attempt. The state
            # is checked as well so that a dependent which reached any
            # terminal state early is caught too.
            if second['attempts'] > 0 or second['state'] in terminal:
                if first['state'] not in terminal:
                    self._raise_agent_operation_failure(
                        inst_two['uuid'], 'the dependent operation', second,
                        f"agent operation {second['uuid']} was dispatched "
                        f"(state {second['state']}, "
                        f"{second['attempts']} attempts) while the operation "
                        f"it depends on, {first['uuid']}, was still in state "
                        f"{first['state']}")
                break

            if first['state'] in terminal:
                break

            observations += 1

            if time.time() - start_time > AGENTOP_STATE_TIMEOUT:
                self._raise_agent_operation_failure(
                    inst_two['uuid'], 'the dependent operation', second,
                    f"agent operation {second['uuid']} was still in state "
                    f"{second['state']} after {AGENTOP_STATE_TIMEOUT} "
                    f'seconds, and so was the operation it depends on, '
                    f"{first['uuid']}, in state {first['state']}")

            time.sleep(AGENTOP_POLL_SECONDS)

        # The dependency is settled first, and deliberately before the
        # vacuity check below. The loop above exits early if the dependency
        # reached any terminal state, including a failed one, and a
        # dependency which errored is both the likeliest reason this test
        # had no window to observe and a far more useful thing to be told
        # than that the window was short -- this prints the operation and
        # the instance's events, where the count below would just report a
        # number.
        self._await_agentop_complete(
            inst_one['uuid'], first, AGENTOP_STATE_TIMEOUT,
            f'sleep {BLOCKER_SECONDS}')

        self.assertGreaterEqual(
            observations, ORDERING_OBSERVATIONS_REQUIRED,
            f'The dependency finished too quickly for this test to observe '
            f'its dependent being held: only {observations} of the required '
            f'{ORDERING_OBSERVATIONS_REQUIRED} polls saw an unfinished '
            f'dependency. The ordering assertion therefore proved nothing.')

        # An edge delays a dependent, it does not strand one. This also
        # leaves both instances idle for the namespace teardown rather than
        # racing it against a live agent operation.
        second = self._await_agentop_complete(
            inst_two['uuid'], second, AGENTOP_STATE_TIMEOUT,
            'whoami ordered behind an operation on another instance')

        # And it ran rather than merely reaching complete.
        self.assertEqual(
            'root\n', second['results']['0']['stdout'],
            f'Agent operation result "0" stdout value lacks expected value '
            f'"root\\n": {second}')

    def test_failed_dependency_aborts_its_dependent(self):
        # One instance is correct here, and for the opposite reason to the
        # test above: the single executor per instance is the lever which
        # makes the victim fail at all. A command which exits non-zero
        # *completes* as an operation -- the exit code is a result, not a
        # failure -- so there is no command which can make a dependency fail
        # on demand. An operation which expires while it is still queued
        # behind something else can, and expired is a failed depends_on
        # dependency.
        inst = self._create_ready_instance('test-aop-dependency-cascade')

        # Holds the instance's only executor slot for the whole sleep, so
        # everything enqueued behind it waits.
        blocker = self.nonblocking_client.instance_execute(
            inst['uuid'], f'sleep {BLOCKER_SECONDS}')

        # Cannot possibly run until the blocker finishes, by which time its
        # own deadline has long passed. Instance.agent_operation_next() is
        # what notices: it expires and retires a queued head whose
        # wall-clock deadline has passed rather than handing it to the
        # executor.
        victim = self.nonblocking_client.instance_execute(
            inst['uuid'], 'whoami', deadline_seconds=SHORT_DEADLINE_SECONDS)

        # Third in line, so it only becomes the queue head once the victim
        # has been retired -- at which point the dispatcher evaluates its
        # depends_on, finds the victim expired, and cascades.
        dependent = self.nonblocking_client.instance_execute(
            inst['uuid'], 'whoami',
            depends_on=[apiclient.agent_dependency(victim)])

        victim = self._await_agentop_state(
            inst['uuid'], victim, ('expired',), AGENTOP_STATE_TIMEOUT,
            'the dependency expected to expire while queued')

        dependent = self._await_agentop_state(
            inst['uuid'], dependent, ('abort',), AGENTOP_STATE_TIMEOUT,
            'the dependent expected to abort on its failed dependency')

        # abort on its own only says the dispatcher gave up on this
        # operation. dependency_failure is what makes it diagnosable without
        # reading events, and it is an enumerated fact rather than prose
        # precisely so that a client can branch on it -- which is why this
        # asserts on both of its values and not merely that the field
        # arrived. The state matters as much as the UUID: the same field
        # carries a dependency which has *not* failed when a dependent runs
        # out of its dependency wait budget, and the two have different
        # remedies.
        failure = dependent['dependency_failure']
        self.assertIsNotNone(
            failure,
            f'Aborted operation records no dependency failure: {dependent}')
        self.assertEqual(
            victim['uuid'], failure['op_uuid'],
            f'Aborted operation blames an operation other than the '
            f'dependency which expired, {victim["uuid"]}: {dependent}')
        self.assertEqual(
            'expired', failure['state'],
            f'Aborted operation does not record that its dependency '
            f'expired: {dependent}')

        # It never ran. attempts is incremented by the executor at the
        # moment it moves an operation to executing, so a zero here is the
        # assertion that the cascade happened where it is supposed to --
        # between dequeue and dispatch, rather than by killing something
        # already in flight.
        self.assertEqual(
            0, dependent['attempts'],
            f'Aborted operation was dispatched to an executor anyway: '
            f'{dependent}')
        self.assertEqual(
            {}, dependent['results'],
            f'Aborted operation produced results: {dependent}')

        # Leave the instance idle for the namespace teardown. The blocker
        # has necessarily already finished -- the victim's expiry cannot
        # happen until it does -- so this returns on its first poll.
        self._await_agentop_complete(
            inst['uuid'], blocker, AGENTOP_STATE_TIMEOUT,
            f'sleep {BLOCKER_SECONDS}')
