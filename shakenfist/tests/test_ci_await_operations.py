"""The functional suite's wait for an object's cluster operations.

The helper waits on the pending-operations field through the client, and
takes errors from listings either side of the wait, because the field is
a boolean and an operation in error is not pending. These tests script
the client and pin each of those decisions.

``base.py`` is loaded the way ``test_ci_capacity_wait`` loads it, with
stubs for the packages which are not test dependencies here.
"""

from shakenfist.tests import test_ci_capacity_wait as capacity_wait

ci_base = capacity_wait.ci_base
ci_apiclient = capacity_wait.ci_apiclient


def op(uuid, state, observational=False):
    return {'uuid': uuid, 'state': state, 'observational': observational}


class FakeClient:
    """A client with only the three calls the helper makes, scripted."""

    def __init__(self, listings, capable=True, wait_error=None):
        self.listings = list(listings)
        self.capable = capable
        self.wait_error = wait_error
        self.calls = []

    def check_capability(self, name):
        self.calls.append(('check_capability', name))
        return self.capable

    def list_cluster_operations_for_target(self, object_type, object_ref):
        self.calls.append(('list', object_type, object_ref))
        return self.listings.pop(0)

    def await_no_pending_operations(self, object_type, object_ref,
                                    namespace=None, timeout=None,
                                    interval=1):
        self.calls.append(
            ('await', object_type, object_ref, timeout, interval))
        if self.wait_error:
            raise self.wait_error


class _Harness(ci_base.BaseTestCase):
    """Supplies self.system_client and self.fail() without setUp()."""

    def __init__(self, client):
        # Deliberately not calling TestCase.__init__: this is only a
        # bound self for the helper.
        self.system_client = client

    def fail(self, message=None):
        raise AssertionError(message)


class AwaitObjectOperationsTestCase(capacity_wait.test_base.ShakenFistTestCase):
    def _run(self, client, object_type='instance', interval=10,
             label='instance'):
        _Harness(client)._await_object_operations_complete(
            object_type, 'abc', interval, label)

    def test_returns_when_nothing_new_errored(self):
        client = FakeClient([[op('o1', 'complete')], [op('o1', 'complete')]])
        self._run(client)

    def test_new_mutating_error_fails_naming_the_op(self):
        client = FakeClient([[], [op('o2', 'error')]])
        with self.assertRaises(AssertionError) as cm:
            self._run(client)
        self.assertIn('Cluster operation o2 for instance abc', str(cm.exception))
        self.assertIn('ended in error state', str(cm.exception))

    def test_error_before_the_wait_is_ignored(self):
        client = FakeClient([[op('o1', 'error')], [op('o1', 'error')]])
        self._run(client)

    def test_observational_error_is_ignored(self):
        client = FakeClient([[], [op('o3', 'error', observational=True)]])
        self._run(client)

    def test_client_timeout_becomes_five_minute_failure(self):
        client = FakeClient(
            [[]], wait_error=ci_apiclient.TimeoutException('slow'))
        with self.assertRaises(AssertionError) as cm:
            self._run(client, 'network', 5, 'network')
        self.assertEqual(
            'Network operations did not complete within 5 minutes for abc',
            str(cm.exception))

    def test_missing_capability_fails_before_listing_or_waiting(self):
        client = FakeClient([], capable=False)
        with self.assertRaises(AssertionError) as cm:
            self._run(client)
        self.assertIn('object-pending-operations', str(cm.exception))
        self.assertEqual(
            [('check_capability', 'object-pending-operations')],
            client.calls)

    def test_wait_is_called_with_type_uuid_timeout_and_interval(self):
        client = FakeClient([[], []])
        self._run(client, 'network', 5, 'network')
        self.assertIn(('await', 'network', 'abc', 300, 5), client.calls)
        self.assertEqual(
            ['check_capability', 'list', 'await', 'list'],
            [c[0] for c in client.calls])
