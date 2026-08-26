# Copyright 2019 Michael Still and contributors
"""The claims suite must not assert cluster headroom it never reserved.

Creating or growing a namespace claim is a guarded admission against
the ``cluster_capacity`` singleton, so it needs free cluster capacity
at that instant. The functional claims tests run concurrently with the
rest of the cluster CI suite on one cluster, and originally asserted
200 unconditionally -- so whenever the sibling tests held every cpu at
the wrong moment, the tests failed against a 507 the server was right
to send. Issue 3907 records three such failures in one day, on both the
creation path (via the shared ``_create_claim`` helper) and the growth
path.

The fix routes every success-asserted claim request through a wrapper
which treats a full cluster as transient, built on a retry loop kept in
``shakenfist_ci/retries.py`` -- a module deliberately free of suite
imports so it can be loaded by path and exercised here with a fake
clock. The functional suite is a client of a deployed cluster and is
not otherwise importable from unit tests.

Two things are covered: the loop itself (only the named statuses are
retried, anything else comes straight back, and the deadline hands the
transient answer to the caller's assertion rather than raising), and
the wiring (the shared creation helper and the lifecycle test's growth
PUT actually go through the headroom-tolerant path, because the defect
was precisely those two call sites not doing so).
"""

import ast
import importlib.util
import os

from shakenfist.tests import base


CI_SUITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci')

RETRIES_PATH = os.path.join(CI_SUITE, 'retries.py')
CLAIMS_TEST_PATH = os.path.join(
    CI_SUITE, 'cluster_ci_tests', 'test_namespace_claims.py')


def _load_retries():
    spec = importlib.util.spec_from_file_location(
        'shakenfist_ci_retries_under_test', RETRIES_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


retries = _load_retries()


class FakeServer:
    """A scripted sequence of (status, body) answers, and a fake clock.

    The clock advances only when the loop sleeps, so a test can say
    precisely how many attempts fit inside a deadline.
    """

    def __init__(self, answers):
        self.answers = list(answers)
        self.now = 100.0
        self.sleeps = 0

    def request(self):
        if len(self.answers) > 1:
            return self.answers.pop(0)
        return self.answers[0]

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += seconds


class RetryWhileTransientTestCase(base.ShakenFistTestCase):
    def test_a_non_transient_answer_is_returned_immediately(self):
        """A refusal the caller means to assert on is never waited out.

        The IMPOSSIBLE_CPUS probe in the functional suite asserts a
        507; if the loop retried statuses it was not told are
        transient, that probe would spin for its whole deadline before
        returning the refusal it wanted.
        """
        server = FakeServer([(507, {'error': 'full'})])
        status, body = retries.retry_while_transient(
            server.request, transient_statuses=(503,),
            deadline=server.now + 420,
            clock=server.clock, sleep=server.sleep)
        self.assertEqual((507, {'error': 'full'}), (status, body))
        self.assertEqual(
            0, server.sleeps,
            'A status outside transient_statuses was retried, so a '
            'refusal a test asserts on would be waited out.')

    def test_transient_answers_are_retried_until_one_is_not(self):
        server = FakeServer([
            (503, 'not yet'), (507, 'full'), (200, {'uuid': 'x'})])
        status, body = retries.retry_while_transient(
            server.request, transient_statuses=(503, 507),
            deadline=server.now + 420,
            clock=server.clock, sleep=server.sleep)
        self.assertEqual((200, {'uuid': 'x'}), (status, body))
        self.assertEqual(
            2, server.sleeps,
            'Each transient answer should cost exactly one sleep '
            'before the next attempt.')

    def test_the_deadline_returns_the_transient_answer_as_it_stands(self):
        """Giving up is the caller's assertion to fail, not an exception.

        The functional callers assert on the returned status and
        include the body in the failure message, so a cluster which
        stays full for the whole wait must hand back the 507 and its
        body rather than raising or looping forever.
        """
        server = FakeServer([(507, {'error': 'still full'})])
        status, body = retries.retry_while_transient(
            server.request, transient_statuses=(503, 507),
            deadline=server.now + 25,
            clock=server.clock, sleep=server.sleep)
        self.assertEqual((507, {'error': 'still full'}), (status, body))
        # 25 seconds of deadline at a 10 second interval is attempts at
        # t=0, 10, 20 and a final one at t=30 which sees the deadline
        # passed and returns.
        self.assertEqual(
            3, server.sleeps,
            'The loop did not give up when the clock passed the '
            'deadline.')


class ClaimsSuiteWiringTestCase(base.ShakenFistTestCase):
    """The two call sites the defect lived in use the tolerant path.

    Source is parsed rather than imported: the functional suite needs
    shakenfist_client, which is not a test dependency here.
    """

    def _claims_test_tree(self):
        with open(CLAIMS_TEST_PATH) as f:
            return ast.parse(f.read())

    def _calls_of(self, node, method_name):
        return [
            call for call in ast.walk(node)
            if isinstance(call, ast.Call) and
            isinstance(call.func, ast.Attribute) and
            call.func.attr == method_name]

    def _function(self, tree, name):
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        self.fail(
            '%s defines no function %s, so this guard is looking at '
            'the wrong code and cannot be trusted.'
            % (CLAIMS_TEST_PATH, name))

    def test_claim_creation_tolerates_a_full_cluster(self):
        creator = self._function(self._claims_test_tree(), '_create_claim')
        self.assertNotEqual(
            [], self._calls_of(creator, '_claim_api_awaiting_headroom'),
            'The shared _create_claim helper does not go through '
            '_claim_api_awaiting_headroom, so every claims test asserts '
            'cluster capacity the concurrent suite is free to be '
            'holding (issue 3907).')

    def test_claim_growth_tolerates_a_full_cluster(self):
        lifecycle = self._function(
            self._claims_test_tree(), 'test_claim_lifecycle_and_refusals')
        grows = [
            call for call in self._calls_of(
                lifecycle, '_claim_api_awaiting_headroom')
            if call.args and isinstance(call.args[0], ast.Constant) and
            call.args[0].value == 'PUT']
        self.assertNotEqual(
            [], grows,
            'The lifecycle test grows a claim without going through '
            '_claim_api_awaiting_headroom, so the growth asserts a free '
            'cpu the concurrent suite is free to be holding (issue '
            '3907).')
