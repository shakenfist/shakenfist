"""Functional coverage of the agent operation timing budgets.

Phases 1 to 5 of docs/plans/PLAN-agent-operation-deadlines.md replaced a
hardcoded 900 second backstop in the sidechannel daemon with two budgets a
caller can set per operation: a wall-clock deadline, and a progress timeout
which only applies to commands which can actually report progress. Nothing
in this suite exercised either of them, which is survey finding F5 of
docs/plans/PLAN-agent-operation-deadlines-phase-07-docs-and-ci.md.

Every assertion here is about a state, or about the presence and rough
magnitude of an absolute timestamp. None of them is about how many seconds
something took. CI hardware is contended and shared, so a test which asserts
that an operation expired "within ten seconds" is a flake written on
purpose; see decision 5 of that phase plan.
"""

import json
import time

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


# Mirrors of the two server side defaults, which live in
# shakenfist/config.py as AGENT_OPERATION_DEFAULT_DEADLINE (:240) and
# AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT (:259). Neither is published
# through the REST API, so the suite cannot read them from the cluster it is
# talking to. If either default is retuned, this pair moves with it.
AGENT_OPERATION_DEFAULT_DEADLINE = 600
AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT = 30

# Mirror of AGENT_OPERATION_MAX_DEADLINE, the operator ceiling on both
# timing parameters (issue #4074), for the same cannot-read-it-from-the-
# cluster reason as the pair above.
AGENT_OPERATION_MAX_DEADLINE = 86400

# How far a freshly created operation's deadline may sit from where this
# test's own clock says it should. The deadline is an absolute timestamp
# stamped by whichever API node answered the request, so this window is
# tolerance for clock skew between that node and the node running the suite,
# plus however long the request spent on the wire. It is not a statement
# about how precise the deadline is. Two minutes is far more skew than an
# NTP synchronised cluster ever shows, and is still narrow enough that no
# default at all, or the 900 second backstop this replaced, fails here.
DEADLINE_CLOCK_SKEW_ALLOWANCE = 120

# How long the blocking operation in the two queue tests occupies the
# instance's single executor slot. It only has to outlast the deadline on
# the operation queued behind it, which passes five seconds after that
# operation is created, so this is six times what is strictly needed --
# cheap insurance in a suite that runs on every pull request, where the
# alternative is a test that occasionally proves nothing.
BLOCKER_SECONDS = 30

# The deadline given to the operation which is expected to expire. Small
# enough that it has certainly passed by the time the executor slot frees,
# and deliberately not zero: deadline_seconds=0 means "no wall-clock
# deadline at all" to the API, not "expire immediately".
SHORT_DEADLINE_SECONDS = 5

# How long the silent command in the progress timeout test produces no
# output for. Twice the default progress window, so a run which passes
# cannot be explained by scheduling jitter around the boundary, and no
# longer than that because the whole cost is paid on every pull request.
NO_PROGRESS_SECONDS = 2 * AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT

# An absolute bound on every wait below. These exist only to turn a hung
# test into a diagnosable failure -- an operation which expires in ten
# seconds and one which expires in two minutes are both a pass -- so they
# are deliberately generous, in the same spirit as base.py's
# AGENT_OPERATION_TIMEOUT.
AGENTOP_STATE_TIMEOUT = 300


class TestAgentOperationDeadlines(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'aopdeadlines'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()

        # The client only sends deadline_seconds and
        # progress_timeout_seconds to a server which advertises this
        # capability (_add_agentop_timing() in client-python's apiclient).
        # Against a server which does not, it silently sends neither and
        # every operation below quietly gets the server default instead of
        # the deadline it asked for -- which would make the expiry tests
        # pass or fail for reasons that have nothing to do with what they
        # claim to cover. Fail loudly here instead of subtly there.
        self.assertTrue(
            self.test_client.check_capability('agentoperation-deadlines'),
            'The API server does not advertise the agentoperation-deadlines '
            'capability, so the client will not send the timing parameters '
            'these tests are about.')

        # Creating an agent operation through self.test_client blocks.
        # instance_execute(), instance_put_blob() and instance_get() all end
        # in the client's _await_agentop(), which polls until the async
        # strategy's deadline -- 60 seconds for the ASYNC_PAUSE client the
        # base class builds -- and, worse for these tests, raises
        # AgentOperationFailed the instant it polls a terminal failure
        # state. An operation which is *expected* to expire would therefore
        # arrive as an exception out of the create call rather than as
        # something to inspect. An ASYNC_CONTINUE client returns the
        # operation exactly as the POST response described it and never
        # polls, which is what these tests want: they do their own waiting,
        # explicitly and with a bound. The alternative -- catching
        # AgentOperationFailed and reading e.op_view -- would work, but it
        # makes an assertion about the operation contingent on an exception
        # having been raised, which is a worse thing to have to read.
        self.nonblocking_client = apiclient.Client(
            base_url=self.system_client.base_url,
            namespace=self.namespace, key=self.namespace_key,
            async_strategy=apiclient.ASYNC_CONTINUE)

        self.net_one = self.test_client.allocate_network(
            '192.168.243.0/24', True, True, '%s-net-one' % self.namespace,
            provide_dns=True)
        self.addDetail(
            'net_one',
            content.text_content(json.dumps(self.net_one, indent=4,
                                            sort_keys=True)))
        self._await_networks_ready([self.net_one['uuid']])

    def _create_ready_instance(self, name):
        inst = self.test_client.create_instance(
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
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])
        return inst

    def _await_agentop_state(self, instance_uuid, aop, states, timeout,
                             description):
        """Poll one agent operation until it reaches one of states.

        base.BaseNamespacedTestCase._await_agentop_complete() only knows how
        to wait for success. These tests wait for expired, and for
        executing, so they need a version which is told which states it is
        happy with. It lives here rather than in base.py because nothing
        else in the suite waits for an agent operation to reach a state
        other than complete, and base.py is where a helper with one caller
        goes to be copied later.

        Reaching some *other* terminal state fails immediately rather than
        burning the rest of the window, and every failure routes through
        _raise_agent_operation_failure() so it prints the operation and the
        instance's recent events. That matters most for an expiry: the
        external view's expiry_reason says which budget ran out, but the
        prose reason lives on the state row's message, which is not part
        of the view. expire() audits it against the instance, so the
        events are where "the operation deadline passed while queued" is
        actually readable.
        """
        terminal = tuple(self.AGENT_OPERATION_FAILED_STATES) + ('complete',)
        wanted = ', '.join(states)
        start_time = time.time()

        while aop['state'] not in states:
            if aop['state'] in terminal:
                self._raise_agent_operation_failure(
                    instance_uuid, description, aop,
                    f"agent operation {aop['uuid']} for {description} "
                    f"finished in state {aop['state']}, but this test "
                    f'expected it to reach one of {wanted}')

            if time.time() - start_time > timeout:
                self._raise_agent_operation_failure(
                    instance_uuid, description, aop,
                    f"agent operation {aop['uuid']} for {description} was "
                    f"still in state {aop['state']} after {timeout} seconds "
                    f'without reaching one of {wanted}')

            time.sleep(5)
            aop = self.test_client.get_agent_operation(aop['uuid'])

        return aop

    def test_queued_operation_expires_on_its_deadline(self):
        inst = self._create_ready_instance('test-aop-deadline-queued')

        # The sidechannel dispatcher runs at most one executor per instance
        # and skips an instance which already has a live one, so this
        # operation holds the instance's only executor slot for the whole
        # sleep and anything enqueued behind it waits. It takes the server
        # default deadline, which is twenty times longer than it needs.
        blocker = self.nonblocking_client.instance_execute(
            inst['uuid'], f'sleep {BLOCKER_SECONDS}')

        # This one cannot possibly run until the blocker finishes, by which
        # time its own deadline has long passed.
        # Instance.agent_operation_next() is what notices: it is the
        # dequeue enforcement point, and it expires and retires a queued
        # head whose wall-clock deadline has passed rather than handing it
        # to the executor.
        victim = self.nonblocking_client.instance_execute(
            inst['uuid'], 'whoami', deadline_seconds=SHORT_DEADLINE_SECONDS)

        victim = self._await_agentop_state(
            inst['uuid'], victim, ('expired',), AGENTOP_STATE_TIMEOUT,
            'the operation expected to expire while queued')

        # It never executed. attempts is incremented by the executor at the
        # moment it moves an operation to executing (record_attempt(), from
        # SideChannelExecutorJob's socket loop), so a zero here is the
        # assertion that this operation expired without ever occupying the
        # executor -- which is the entire point of enforcing the deadline at
        # dequeue rather than only inside the executor.
        self.assertEqual(
            0, victim['attempts'],
            f'Expired operation was dispatched to an executor anyway: {victim}')
        self.assertEqual(
            {}, victim['results'],
            f'Expired operation produced results: {victim}')

        # Which budget ran out is an enumerated fact on the external
        # view (issue 4075), so a client can branch on it without
        # parsing the audit event's prose. This one was the wall clock.
        self.assertEqual(
            'deadline', victim['expiry_reason'],
            f'Expired operation does not name the budget that expired '
            f'it: {victim}')

        # Leave the instance idle for the namespace teardown rather than
        # racing it against a live agent operation. The blocker has
        # necessarily already finished -- the expiry above cannot happen
        # until it does -- so this returns on its first poll.
        self._await_agentop_complete(
            inst['uuid'], blocker, AGENTOP_STATE_TIMEOUT,
            f'sleep {BLOCKER_SECONDS}')

    def test_default_deadline_is_published(self):
        inst = self._create_ready_instance('test-aop-deadline-default')

        # No deadline_seconds, so the API server applies
        # AGENT_OPERATION_DEFAULT_DEADLINE at request receipt and stores it
        # as an absolute timestamp. The bracket is read around the call
        # because the request itself takes time, and the deadline is
        # stamped somewhere in the middle of it.
        before = time.time()
        aop = self.nonblocking_client.instance_execute(inst['uuid'], 'whoami')
        after = time.time()

        self.assertIsNotNone(
            aop.get('deadline'),
            f'Agent operation created with no deadline_seconds has no '
            f'deadline: {aop}')

        earliest = (before + AGENT_OPERATION_DEFAULT_DEADLINE
                    - DEADLINE_CLOCK_SKEW_ALLOWANCE)
        latest = (after + AGENT_OPERATION_DEFAULT_DEADLINE
                  + DEADLINE_CLOCK_SKEW_ALLOWANCE)
        self.assertTrue(
            earliest <= aop['deadline'] <= latest,
            f'Agent operation deadline {aop["deadline"]} is not roughly '
            f'{AGENT_OPERATION_DEFAULT_DEADLINE} seconds from now: expected '
            f'between {earliest} and {latest}. The operation was: {aop}')

        # A deadline above the operator ceiling is refused with a 400
        # before anything is created (issue #4074). Piggybacked on this
        # test rather than given its own, because the refusal happens
        # after the agent readiness check and so needs a ready instance
        # -- and booting one for a single expected 400 would double the
        # suite's cost for no more coverage.
        self.assertRaises(
            apiclient.RequestMalformedException,
            self.nonblocking_client.instance_execute,
            inst['uuid'], 'whoami',
            deadline_seconds=AGENT_OPERATION_MAX_DEADLINE + 1)

        self._await_agentop_complete(
            inst['uuid'], aop, AGENTOP_STATE_TIMEOUT, 'whoami')

    def test_silent_execute_survives_the_progress_timeout(self):
        inst = self._create_ready_instance('test-aop-deadline-noprogress')

        aop = self.nonblocking_client.instance_execute(
            inst['uuid'], f'sleep {NO_PROGRESS_SECONDS}; echo done')

        # The execute endpoint publishes no progress_timeout_seconds and
        # records an explicit zero, because no command it builds can report
        # progress -- and the executor independently declines to apply the
        # window unless the in-flight handler says it reports progress
        # (ExecuteCommand.reports_progress is False). Asserting the stored
        # zero here means a regression which quietly started applying the
        # default to execute is reported as itself, rather than as a
        # mysterious expiry further down.
        self.assertEqual(
            0, aop['progress_timeout'],
            f'Agent execute operation was given a progress timeout: {aop}')

        aop = self._await_agentop_complete(
            inst['uuid'], aop, AGENTOP_STATE_TIMEOUT,
            f'sleep {NO_PROGRESS_SECONDS}; echo done')

        self.assertEqual(
            'done\n', aop['results']['0']['stdout'],
            f'Agent operation result "0" stdout value lacks expected value '
            f'"done\\n": {aop}')

    def test_expiry_frees_the_executor_slot(self):
        inst = self._create_ready_instance('test-aop-deadline-unblocks')

        # Same shape as test_queued_operation_expires_on_its_deadline, plus
        # a third operation behind the one which expires. The question this
        # asks is whether an expired operation is retired from its
        # instance's queue or left there as a corpse the dispatcher trips
        # over -- before the queue learned about terminal states, a head
        # which was never going to run blocked its instance forever.
        blocker = self.nonblocking_client.instance_execute(
            inst['uuid'], f'sleep {BLOCKER_SECONDS}')
        victim = self.nonblocking_client.instance_execute(
            inst['uuid'], 'whoami', deadline_seconds=SHORT_DEADLINE_SECONDS)
        follower = self.nonblocking_client.instance_execute(
            inst['uuid'], 'whoami')

        victim = self._await_agentop_state(
            inst['uuid'], victim, ('expired',), AGENTOP_STATE_TIMEOUT,
            'the operation expected to expire while queued')

        # complete is accepted alongside executing because whoami is fast
        # enough to be finished before the next poll, and a completed
        # operation is a strictly stronger demonstration that the slot
        # freed than a dispatched one.
        follower = self._await_agentop_state(
            inst['uuid'], follower, ('executing', 'complete'),
            AGENTOP_STATE_TIMEOUT, 'whoami queued behind an expired operation')
        follower = self._await_agentop_complete(
            inst['uuid'], follower, AGENTOP_STATE_TIMEOUT,
            'whoami queued behind an expired operation')

        self.assertEqual(
            'root\n', follower['results']['0']['stdout'],
            f'Agent operation result "0" stdout value lacks expected value '
            f'"root\\n": {follower}')

        self._await_agentop_complete(
            inst['uuid'], blocker, AGENTOP_STATE_TIMEOUT,
            f'sleep {BLOCKER_SECONDS}')
