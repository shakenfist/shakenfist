# Copyright 2026 Michael Still and contributors
import importlib.util
import os
import sys
import types
from unittest import mock

from shakenfist.tests import base


# The collection module is not importable in the normal way: it lives in an
# ansible collection tree (no __init__.py), and it imports ansible and
# shakenfist_client, neither of which is a test dependency of this
# repository. Load it from source with those two imports stubbed out so the
# decisions the module makes can be tested here rather than only in the
# ansible module CI job. This mirrors test_ansible_sf_instance.py.
MODULE_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'plugins',
    'modules', 'sf_claim.py'))


class FakeAPIException(Exception):
    def __init__(self, message, method, url, status_code, text):
        super().__init__(message)
        self.message = message
        self.method = method
        self.url = url
        self.status_code = status_code
        self.text = text


class FakeResourceNotFoundException(FakeAPIException):
    ...


class FakeInsufficientResourcesException(FakeAPIException):
    ...


class FakeResourceStateConflictException(FakeAPIException):
    ...


def _load_sf_claim():
    stubs = {}

    ansible = types.ModuleType('ansible')
    ansible.__path__ = []
    module_utils = types.ModuleType('ansible.module_utils')
    module_utils.__path__ = []
    basic = types.ModuleType('ansible.module_utils.basic')
    basic.AnsibleModule = mock.MagicMock()
    module_utils.basic = basic
    ansible.module_utils = module_utils
    stubs['ansible'] = ansible
    stubs['ansible.module_utils'] = module_utils
    stubs['ansible.module_utils.basic'] = basic

    client = types.ModuleType('shakenfist_client')
    client.__path__ = []
    apiclient = types.ModuleType('shakenfist_client.apiclient')

    # Mirror the real hierarchy: both of the specific exceptions subclass
    # APIException, so the exception clause ordering in the module (the
    # capacity refusal before the generic API failure) is exercised the same
    # way it runs in production.
    apiclient.APIException = FakeAPIException
    apiclient.ResourceNotFoundException = FakeResourceNotFoundException
    apiclient.InsufficientResourcesException = FakeInsufficientResourcesException
    apiclient.ResourceStateConflictException = FakeResourceStateConflictException
    apiclient.UnconfiguredException = Exception
    apiclient.ASYNC_BLOCK = 'block'
    apiclient.Client = mock.MagicMock()
    client.apiclient = apiclient
    stubs['shakenfist_client'] = client
    stubs['shakenfist_client.apiclient'] = apiclient

    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            'sf_claim_under_test', MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, previous in saved.items():
            if previous is None:
                del sys.modules[name]
            else:
                sys.modules[name] = previous

    return module


sf_claim = _load_sf_claim()


class ModuleExited(Exception):
    def __init__(self, result):
        super().__init__('module exited')
        self.result = result


class ModuleFailed(Exception):
    def __init__(self, result):
        super().__init__('module failed')
        self.result = result


class FakeModule:
    """Enough of AnsibleModule for the decision logic.

    exit_json() and fail_json() terminate the module in production, so they
    raise here. A mock which simply returned would let the module carry on
    past its own exit and test a code path which cannot run.
    """

    def __init__(self, check_mode=False, **params):
        self.params = {
            'namespace': 'static-runners',
            'limit_cpus': 32,
            'limit_memory_mb': 65536,
            'limit_disk_gb': 1200,
            'expires_in_seconds': 2592000,
            'renew_within_seconds': None,
            'state': 'present'
        }
        self.params.update(params)
        self.check_mode = check_mode

    def exit_json(self, **kwargs):
        raise ModuleExited(kwargs)

    def fail_json(self, **kwargs):
        raise ModuleFailed(kwargs)


def _claim(uuid='11111111-1111-1111-1111-111111111111', coverage_state='active',
           expires_at=1000.0, **kwargs):
    claim = {
        'uuid': uuid,
        'state': 'created',
        'coverage_state': coverage_state,
        'namespace': 'static-runners',
        'limit_cpus': 32,
        'limit_memory_mb': 65536,
        'limit_disk_gb': 1200,
        'expires_at': expires_at
    }
    claim.update(kwargs)
    return claim


class SfClaimPartitionTestCase(base.ShakenFistTestCase):
    def test_deleted_claims_are_not_live(self):
        claims = sf_claim._live_claims([
            _claim(uuid='a', state='deleted'), _claim(uuid='b')])
        self.assertEqual(['b'], [c['uuid'] for c in claims])

    def test_no_claims(self):
        active, extra, inactive = sf_claim._partition_claims([])
        self.assertIsNone(active)
        self.assertEqual([], extra)
        self.assertEqual([], inactive)

    def test_one_active_claim(self):
        claims = [_claim(uuid='a')]
        active, extra, inactive = sf_claim._partition_claims(claims)
        self.assertEqual('a', active['uuid'])
        self.assertEqual([], extra)
        self.assertEqual([], inactive)

    def test_expired_claim_is_not_active(self):
        claims = [_claim(uuid='a', coverage_state='expired')]
        active, extra, inactive = sf_claim._partition_claims(claims)
        self.assertIsNone(active)
        self.assertEqual([], extra)
        self.assertEqual(['a'], [c['uuid'] for c in inactive])

    def test_racing_creates_pick_the_lowest_uuid(self):
        # Two creates racing for one namespace can both commit, and
        # admission then draws down the lowest uuid. That is the claim to
        # re-date; the other is reported and left alone.
        claims = [_claim(uuid='b'), _claim(uuid='a')]
        active, extra, inactive = sf_claim._partition_claims(claims)
        self.assertEqual('a', active['uuid'])
        self.assertEqual(['b'], [c['uuid'] for c in extra])
        self.assertEqual([], inactive)


class SfClaimLimitDifferenceTestCase(base.ShakenFistTestCase):
    def test_matching_limits_do_not_differ(self):
        self.assertEqual(
            {}, sf_claim._limit_differences(_claim(), FakeModule()))

    def test_only_the_changed_dimension_is_reported(self):
        self.assertEqual(
            {'limit_cpus': 40},
            sf_claim._limit_differences(_claim(), FakeModule(limit_cpus=40)))

    def test_every_dimension_can_change(self):
        self.assertEqual(
            {'limit_cpus': 1, 'limit_memory_mb': 2, 'limit_disk_gb': 3},
            sf_claim._limit_differences(
                _claim(),
                FakeModule(limit_cpus=1, limit_memory_mb=2, limit_disk_gb=3)))


class SfClaimPresentTestCase(base.ShakenFistTestCase):
    def _present(self, client, module):
        try:
            sf_claim._ensure_present(client, module, [])
        except ModuleExited as e:
            return e.result
        self.fail('the module did not exit')

    def _present_failure(self, client, module):
        try:
            sf_claim._ensure_present(client, module, [])
        except ModuleFailed as e:
            return e.result
        self.fail('the module did not fail')

    def test_creates_when_the_namespace_holds_nothing(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = []
        client.create_namespace_claim.return_value = _claim()

        result = self._present(client, FakeModule())
        self.assertTrue(result['changed'])
        # By keyword, because the SDK is in another repository and these
        # are four mutually type-compatible integers.
        client.create_namespace_claim.assert_called_once_with(
            'static-runners', limit_cpus=32, limit_memory_mb=65536,
            limit_disk_gb=1200, expires_in_seconds=2592000)
        client.delete_namespace_claim.assert_not_called()

    def test_a_lapsed_claim_is_replaced_and_reaped_afterwards(self):
        # The server refuses to re-date an inactive claim, so the module
        # replaces it. The create must happen before the delete: a capacity
        # refusal then leaves the namespace exactly as it was found.
        client = mock.MagicMock()
        calls = []
        client.get_namespace_claims.return_value = [
            _claim(uuid='old', coverage_state='expired')]
        client.create_namespace_claim.side_effect = (
            lambda *a, **kw: calls.append('create') or _claim())
        client.delete_namespace_claim.side_effect = (
            lambda *a, **kw: calls.append('delete'))

        result = self._present(client, FakeModule())
        self.assertTrue(result['changed'])
        self.assertEqual(['create', 'delete'], calls)
        client.delete_namespace_claim.assert_called_once_with(
            'static-runners', 'old')

    def test_a_lapsed_claim_is_not_reaped_when_the_create_is_refused(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [
            _claim(uuid='old', coverage_state='expired')]
        client.create_namespace_claim.side_effect = (
            FakeInsufficientResourcesException(
                'no capacity', 'POST', '/claims', 507, 'detail'))

        result = self._present_failure(client, FakeModule())
        self.assertEqual(507, result['refusal']['status_code'])
        self.assertEqual('detail', result['refusal']['text'])
        client.delete_namespace_claim.assert_not_called()

    def test_a_redate_which_moves_the_expiry_is_a_change(self):
        # The idempotence contract: a re-date alone reports changed, because
        # the claim now holds cluster capacity for longer than it did. The
        # test is made against the server's own before and after expiry, so
        # the control node's clock is never compared to the cluster's.
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim(expires_at=1000.0)]
        client.update_namespace_claim.return_value = _claim(expires_at=2000.0)

        result = self._present(client, FakeModule())
        self.assertTrue(result['changed'])
        client.update_namespace_claim.assert_called_once_with(
            'static-runners', '11111111-1111-1111-1111-111111111111',
            expires_in_seconds=2592000)

    def test_a_redate_which_moves_nothing_is_not_a_change(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim(expires_at=1000.0)]
        client.update_namespace_claim.return_value = _claim(expires_at=1000.0)

        result = self._present(client, FakeModule())
        self.assertFalse(result['changed'])

    def test_a_differing_limit_is_a_change_and_is_the_only_field_sent(self):
        # Only the dimensions which actually differ are named in the field
        # mask, so an unchanged dimension cannot race a resize somebody else
        # is making. The expiry is always named.
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim(expires_at=1000.0)]
        client.update_namespace_claim.return_value = _claim(
            expires_at=1000.0, limit_cpus=40)

        result = self._present(client, FakeModule(limit_cpus=40))
        self.assertTrue(result['changed'])
        client.update_namespace_claim.assert_called_once_with(
            'static-runners', '11111111-1111-1111-1111-111111111111',
            expires_in_seconds=2592000, limit_cpus=40)

    def test_check_mode_writes_nothing(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim()]

        result = self._present(client, FakeModule(check_mode=True))
        self.assertTrue(result['changed'])
        client.update_namespace_claim.assert_not_called()
        client.create_namespace_claim.assert_not_called()

    def test_check_mode_creates_nothing(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = []

        result = self._present(client, FakeModule(check_mode=True))
        self.assertTrue(result['changed'])
        client.create_namespace_claim.assert_not_called()

    def test_a_capacity_refusal_on_a_grow_is_reported_as_a_refusal(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim()]
        client.update_namespace_claim.side_effect = (
            FakeInsufficientResourcesException(
                'no capacity', 'PUT', '/claims/x', 507, 'detail'))

        result = self._present_failure(client, FakeModule(limit_cpus=40))
        self.assertEqual(507, result['refusal']['status_code'])

    def test_a_missing_namespace_fails_clearly(self):
        client = mock.MagicMock()
        client.get_namespace_claims.side_effect = (
            FakeResourceNotFoundException(
                'not found', 'GET', '/claims', 404, ''))

        result = self._present_failure(client, FakeModule())
        self.assertIn('does not exist', result['msg'])

    def test_a_transient_listing_failure_is_reported_as_a_refusal(self):
        # A 503 means the cluster capacity accounting is not ready yet, or
        # the row was contended. The play needs the status code to know it
        # is worth retrying, so it does not arrive as a bare traceback.
        client = mock.MagicMock()
        client.get_namespace_claims.side_effect = FakeAPIException(
            'not ready', 'GET', '/claims', 503, 'retry')

        result = self._present_failure(client, FakeModule())
        self.assertEqual(503, result['refusal']['status_code'])


class SfClaimAbsentTestCase(base.ShakenFistTestCase):
    def _absent(self, client, module):
        try:
            sf_claim._ensure_absent(client, module, [])
        except ModuleExited as e:
            return e.result
        self.fail('the module did not exit')

    def test_no_claims_is_no_change(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = []

        result = self._absent(client, FakeModule(state='absent'))
        self.assertFalse(result['changed'])
        client.delete_namespace_claim.assert_not_called()

    def test_a_missing_namespace_is_no_change(self):
        client = mock.MagicMock()
        client.get_namespace_claims.side_effect = (
            FakeResourceNotFoundException(
                'not found', 'GET', '/claims', 404, ''))

        result = self._absent(client, FakeModule(state='absent'))
        self.assertFalse(result['changed'])

    def test_every_claim_is_deleted(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [
            _claim(uuid='a'), _claim(uuid='b', coverage_state='expired')]

        result = self._absent(client, FakeModule(state='absent'))
        self.assertTrue(result['changed'])
        self.assertEqual(
            [mock.call('static-runners', 'a'),
             mock.call('static-runners', 'b')],
            client.delete_namespace_claim.call_args_list)

    def test_check_mode_deletes_nothing(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim()]

        result = self._absent(client, FakeModule(state='absent', check_mode=True))
        self.assertTrue(result['changed'])
        client.delete_namespace_claim.assert_not_called()

    def test_a_transient_listing_failure_is_reported_as_a_refusal(self):
        client = mock.MagicMock()
        client.get_namespace_claims.side_effect = FakeAPIException(
            'not ready', 'GET', '/claims', 503, 'retry')

        try:
            sf_claim._ensure_absent(client, FakeModule(state='absent'), [])
        except ModuleFailed as e:
            self.assertEqual(503, e.result['refusal']['status_code'])
        else:
            self.fail('the module did not fail')

    def test_a_claim_which_vanished_is_not_an_error(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim(uuid='a')]
        client.delete_namespace_claim.side_effect = (
            FakeResourceNotFoundException(
                'not found', 'DELETE', '/claims/a', 404, ''))

        result = self._absent(client, FakeModule(state='absent'))
        self.assertTrue(result['changed'])


class SfClaimReapingTestCase(base.ShakenFistTestCase):
    """Lapsed rows are reaped on both paths, and reaping is not a change."""

    def _present(self, client, module):
        try:
            sf_claim._ensure_present(client, module, [])
        except ModuleExited as e:
            return e.result
        self.fail('the module did not exit')

    def _present_failure(self, client, module):
        try:
            sf_claim._ensure_present(client, module, [])
        except ModuleFailed as e:
            return e.result
        self.fail('the module did not fail')

    def test_lapsed_rows_beside_an_active_claim_are_reaped(self):
        # Without this the rows accumulate forever: the server has no soft
        # delete and no sweep which removes them.
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [
            _claim(uuid='a'), _claim(uuid='old', coverage_state='expired')]
        client.update_namespace_claim.return_value = _claim(
            uuid='a', expires_at=2000.0)

        result = self._present(client, FakeModule())
        self.assertTrue(result['changed'])
        client.delete_namespace_claim.assert_called_once_with(
            'static-runners', 'old')

    def test_reaping_alone_is_not_a_change(self):
        # An expired claim holds no cluster capacity and no admission
        # decision depends on it, so removing the row alters nothing an
        # operator can observe about the namespace's coverage.
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [
            _claim(uuid='a', expires_at=1000.0),
            _claim(uuid='old', coverage_state='expired')]
        client.update_namespace_claim.return_value = _claim(
            uuid='a', expires_at=1000.0)

        result = self._present(client, FakeModule())
        self.assertFalse(result['changed'])
        client.delete_namespace_claim.assert_called_once_with(
            'static-runners', 'old')

    def test_check_mode_reaps_nothing(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [
            _claim(uuid='a'), _claim(uuid='old', coverage_state='expired')]

        self._present(client, FakeModule(check_mode=True))
        client.delete_namespace_claim.assert_not_called()

    def test_a_failed_reap_after_a_create_still_reports_the_new_claim(self):
        # The create half succeeded, and a failure which hides it leaves the
        # play with no claim details for a claim which now exists.
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [
            _claim(uuid='old', coverage_state='expired')]
        client.create_namespace_claim.return_value = _claim(uuid='new')
        client.delete_namespace_claim.side_effect = FakeAPIException(
            'boom', 'DELETE', '/claims/old', 500, 'detail')

        result = self._present_failure(client, FakeModule())
        self.assertEqual('new', result['meta']['uuid'])
        self.assertEqual(500, result['refusal']['status_code'])

    def test_a_failed_reap_after_a_redate_still_reports_the_claim(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [
            _claim(uuid='a'), _claim(uuid='old', coverage_state='expired')]
        client.update_namespace_claim.return_value = _claim(
            uuid='a', expires_at=2000.0)
        client.delete_namespace_claim.side_effect = FakeAPIException(
            'boom', 'DELETE', '/claims/old', 500, 'detail')

        result = self._present_failure(client, FakeModule())
        self.assertEqual('a', result['meta']['uuid'])
        self.assertEqual(2000.0, result['meta']['expires_at'])

    def test_a_reap_failure_on_absent_reports_no_claim(self):
        # state: absent wrote no claim, so there is nothing to hand back.
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim(uuid='a')]
        client.delete_namespace_claim.side_effect = FakeAPIException(
            'boom', 'DELETE', '/claims/a', 500, 'detail')

        try:
            sf_claim._ensure_absent(client, FakeModule(state='absent'), [])
        except ModuleFailed as e:
            self.assertIsNone(e.result['meta'])
        else:
            self.fail('the module did not fail')


class SfClaimRenewalWindowTestCase(base.ShakenFistTestCase):
    """renew_within_seconds, the opt in which makes repeat runs converge."""

    def _present(self, client, module, now=0.0):
        with mock.patch.object(sf_claim.time, 'time', return_value=now):
            try:
                sf_claim._ensure_present(client, module, [])
            except ModuleExited as e:
                return e.result
        self.fail('the module did not exit')

    def test_without_a_window_every_run_redates(self):
        # The default, and D10's "four chances to re-date before a lapse".
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim(expires_at=1000.0)]
        client.update_namespace_claim.return_value = _claim(expires_at=2000.0)

        result = self._present(client, FakeModule(), now=100.0)
        self.assertTrue(result['changed'])
        client.update_namespace_claim.assert_called_once()

    def test_a_claim_outside_the_window_is_left_alone(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim(expires_at=1000.0)]

        result = self._present(
            client, FakeModule(renew_within_seconds=100), now=100.0)
        self.assertFalse(result['changed'])
        self.assertEqual('11111111-1111-1111-1111-111111111111',
                         result['meta']['uuid'])
        client.update_namespace_claim.assert_not_called()

    def test_a_claim_inside_the_window_is_redated(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim(expires_at=1000.0)]
        client.update_namespace_claim.return_value = _claim(expires_at=3592000.0)

        result = self._present(
            client, FakeModule(renew_within_seconds=100), now=950.0)
        self.assertTrue(result['changed'])
        client.update_namespace_claim.assert_called_once_with(
            'static-runners', '11111111-1111-1111-1111-111111111111',
            expires_in_seconds=2592000)

    def test_check_mode_converges_when_a_window_is_set(self):
        # The point of the option: --check on an already correct claim
        # reports no change rather than a change it cannot know about.
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim(expires_at=1000.0)]

        result = self._present(
            client, FakeModule(renew_within_seconds=100, check_mode=True),
            now=100.0)
        self.assertFalse(result['changed'])
        client.update_namespace_claim.assert_not_called()

    def test_a_resize_is_made_even_outside_the_window(self):
        # The window governs the re-date, not the limits. A resize is an
        # instruction and is carried out whenever it differs.
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim(expires_at=1000.0)]
        client.update_namespace_claim.return_value = _claim(
            expires_at=1000.0, limit_cpus=40)

        result = self._present(
            client, FakeModule(renew_within_seconds=100, limit_cpus=40),
            now=100.0)
        self.assertTrue(result['changed'])
        client.update_namespace_claim.assert_called_once_with(
            'static-runners', '11111111-1111-1111-1111-111111111111',
            expires_in_seconds=2592000, limit_cpus=40)

    def test_a_claim_with_no_expiry_is_redated(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim(expires_at=None)]
        client.update_namespace_claim.return_value = _claim(expires_at=2000.0)

        result = self._present(
            client, FakeModule(renew_within_seconds=100), now=100.0)
        self.assertTrue(result['changed'])
        client.update_namespace_claim.assert_called_once()

    def test_lapsed_rows_are_still_reaped_when_nothing_is_written(self):
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [
            _claim(uuid='a', expires_at=1000.0),
            _claim(uuid='old', coverage_state='expired')]

        result = self._present(
            client, FakeModule(renew_within_seconds=100), now=100.0)
        self.assertFalse(result['changed'])
        client.update_namespace_claim.assert_not_called()
        client.delete_namespace_claim.assert_called_once_with(
            'static-runners', 'old')


class SfClaimConflictTestCase(base.ShakenFistTestCase):
    def test_a_409_names_the_two_things_it_can_mean(self):
        # The server answers 409 for a shrink below current usage and for a
        # claim which is no longer active. Both are the caller's to fix, so
        # the message says which they are rather than reporting a bare
        # failure with the body attached.
        client = mock.MagicMock()
        client.get_namespace_claims.return_value = [_claim()]
        client.update_namespace_claim.side_effect = (
            FakeResourceStateConflictException(
                'a claim cannot be shrunk below what it is already using',
                'PUT', '/claims/x', 409, 'detail'))

        try:
            sf_claim._ensure_present(client, FakeModule(limit_cpus=1), [])
        except ModuleFailed as e:
            self.assertIn('shrunk below what it is already using', e.result['msg'])
            self.assertIn('no longer active', e.result['msg'])
            self.assertEqual(409, e.result['refusal']['status_code'])
        else:
            self.fail('the module did not fail')


class SfClaimClientVerbsTestCase(base.ShakenFistTestCase):
    """The claim verbs are newer than any released client."""

    class OldClient:
        def get_namespace_claims(self, namespace):
            ...

    class NewClient(OldClient):
        def create_namespace_claim(self, namespace, **kwargs):
            ...

        def update_namespace_claim(self, namespace, claim_uuid, **kwargs):
            ...

        def delete_namespace_claim(self, namespace, claim_uuid):
            ...

    def test_a_client_without_the_verbs_fails_with_advice(self):
        try:
            sf_claim._require_claim_verbs(self.OldClient(), FakeModule(), [])
        except ModuleFailed as e:
            self.assertIn('create_namespace_claim', e.result['msg'])
            self.assertIn('client-python@develop', e.result['msg'])
        else:
            self.fail('the module did not fail')

    def test_a_client_with_the_verbs_is_accepted(self):
        # Returns rather than failing, so this starts passing on its own
        # once a release carries the verbs.
        self.assertIsNone(
            sf_claim._require_claim_verbs(self.NewClient(), FakeModule(), []))


class SfClaimArgumentSpecTestCase(base.ShakenFistTestCase):
    """run_module()'s validation, which the decision functions do not see.

    AnsibleModule itself is not importable here, so the spec it is handed
    is asserted rather than exercised. That is the whole of what the module
    controls: required_together and required_if are enforced by ansible.
    """

    def _run_module(self, **params):
        module = FakeModule(**params)
        client = mock.MagicMock()
        with mock.patch.object(sf_claim, 'AnsibleModule') as ansible_module, \
                mock.patch.object(sf_claim, '_make_client') as make_client, \
                mock.patch.object(sf_claim, '_ensure_present') as present, \
                mock.patch.object(sf_claim, '_ensure_absent') as absent:
            ansible_module.return_value = module
            make_client.return_value = client
            try:
                sf_claim.run_module()
                failure = None
            except ModuleFailed as e:
                failure = e.result
            return ansible_module.call_args, failure, present, absent

    def test_the_connection_triple_is_all_or_nothing(self):
        # Otherwise a partial triple is silently ignored in favour of the
        # ambient credentials, which may be a different cluster -- and this
        # module's namespace parameter names the claim target rather than
        # the identity, so a playbook written against the rest of the
        # collection lands exactly there.
        call, failure, _present, _absent = self._run_module()
        self.assertIsNone(failure)
        self.assertEqual(
            [['api_url', 'auth_namespace', 'key']],
            call.kwargs['required_together'])

    def test_a_non_positive_expiry_is_rejected(self):
        _call, failure, present, _absent = self._run_module(
            expires_in_seconds=0)
        self.assertIn('expires_in_seconds must be positive', failure['msg'])
        present.assert_not_called()

    def test_a_non_positive_renewal_window_is_rejected(self):
        # A negative window would silently mean "never renew", which is the
        # opposite of what somebody setting it wants.
        _call, failure, present, _absent = self._run_module(
            renew_within_seconds=-1)
        self.assertIn('renew_within_seconds must be positive', failure['msg'])
        present.assert_not_called()

    def test_the_claim_verbs_are_checked_before_anything_is_done(self):
        with mock.patch.object(sf_claim, '_require_claim_verbs') as require:
            call, _failure, present, _absent = self._run_module()
        self.assertEqual(1, require.call_count)
        self.assertEqual(1, present.call_count)
        self.assertIn('renew_within_seconds', call.kwargs['argument_spec'])
