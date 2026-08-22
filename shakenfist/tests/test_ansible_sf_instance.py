# Copyright 2019 Michael Still and contributors
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
# pure logic in _check_instance() can be tested here rather than only in the
# ansible module CI job.
MODULE_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'plugins',
    'modules', 'sf_instance.py'))


def _load_sf_instance():
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

    class _ResourceNotFoundException(Exception):
        ...

    apiclient.ResourceNotFoundException = _ResourceNotFoundException
    apiclient.APIException = Exception
    apiclient.Client = mock.MagicMock()
    client.apiclient = apiclient
    stubs['shakenfist_client'] = client
    stubs['shakenfist_client.apiclient'] = apiclient

    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            'sf_instance_under_test', MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, previous in saved.items():
            if previous is None:
                del sys.modules[name]
            else:
                sys.modules[name] = previous

    return module


sf_instance = _load_sf_instance()


class SfInstanceDiskDirtinessTestCase(base.ShakenFistTestCase):
    # A minimal instance which is not dirty for any reason other than
    # (possibly) its disks. Interfaces are empty so _check_instance() never
    # calls out to the client.
    def _existing(self, disk_spec):
        return {
            'name': 'test',
            'cpus': 1,
            'memory': 1024,
            'interfaces': [],
            'disk_spec': disk_spec
        }

    def _params(self, **kwargs):
        params = {
            'name': 'test',
            'cpu': 1,
            'ram': 1024
        }
        params.update(kwargs)
        return params

    def _check(self, existing, params):
        log = []
        dirty, _, _ = sf_instance._check_instance(
            mock.MagicMock(), existing, params, log)
        return dirty, log

    def test_server_resolved_blob_uuid_is_not_dirty(self):
        # The exact signature from issue 3669: the server resolved the disk's
        # base into a blob once the image fetch completed, and the unchanged
        # request must still compare clean.
        existing = self._existing([{
            'base': 'debian:11',
            'blob_uuid': '73b4e968-7276-4b10-a03e-1258bcb930f1',
            'bus': None,
            'size': 10,
            'type': 'disk'
        }])
        dirty, log = self._check(existing, self._params(disks=['10@debian:11']))
        self.assertFalse(dirty, log)

    def test_unresolved_disk_spec_is_not_dirty(self):
        # The same request before the server resolved the base.
        existing = self._existing([{
            'base': 'debian:11',
            'bus': None,
            'size': 10,
            'type': 'disk'
        }])
        dirty, log = self._check(existing, self._params(disks=['10@debian:11']))
        self.assertFalse(dirty, log)

    def test_disk_base_is_still_stripped(self):
        existing = self._existing([{
            'base': None,
            'disk_base': None,
            'bus': None,
            'size': 10,
            'type': 'disk'
        }])
        dirty, log = self._check(existing, self._params(disks=['10']))
        self.assertFalse(dirty, log)

    def test_existing_disk_spec_is_not_mutated(self):
        # _check_instance() is called with the instance the caller fetched
        # from the API, and must not edit it in place.
        disk = {
            'base': 'debian:11',
            'blob_uuid': '73b4e968-7276-4b10-a03e-1258bcb930f1',
            'disk_base': 'blob://73b4e968-7276-4b10-a03e-1258bcb930f1',
            'bus': None,
            'size': 10,
            'type': 'disk'
        }
        existing = self._existing([disk])
        self._check(existing, self._params(disks=['10@debian:11']))
        self.assertIn('blob_uuid', disk)
        self.assertIn('disk_base', disk)

    def test_explicitly_requested_blob_uuid_is_compared(self):
        # A caller who names a blob_uuid in a diskspec does want it compared,
        # so a mismatch is still dirty.
        existing = self._existing([{
            'base': None,
            'blob_uuid': 'aaaaaaaa-0000-0000-0000-000000000000',
            'bus': None,
            'size': 10,
            'type': 'disk'
        }])
        params = self._params(diskspecs=[
            'size=10,blob_uuid=bbbbbbbb-0000-0000-0000-000000000000'])
        dirty, _ = self._check(existing, params)
        self.assertTrue(dirty)

    def test_explicitly_requested_blob_uuid_matching_is_clean(self):
        existing = self._existing([{
            'base': None,
            'blob_uuid': 'aaaaaaaa-0000-0000-0000-000000000000',
            'bus': None,
            'size': 10,
            'type': 'disk'
        }])
        params = self._params(diskspecs=[
            'size=10,blob_uuid=aaaaaaaa-0000-0000-0000-000000000000'])
        dirty, log = self._check(existing, params)
        self.assertFalse(dirty, log)

    def test_changed_disk_size_is_still_dirty(self):
        existing = self._existing([{
            'base': 'debian:11',
            'blob_uuid': '73b4e968-7276-4b10-a03e-1258bcb930f1',
            'bus': None,
            'size': 10,
            'type': 'disk'
        }])
        dirty, _ = self._check(existing, self._params(disks=['20@debian:11']))
        self.assertTrue(dirty)

    def test_extra_existing_disk_is_still_dirty(self):
        # An unmatched existing disk has no requested counterpart, so its
        # server populated keys are stripped -- and the length difference
        # must still be caught.
        existing = self._existing([
            {
                'base': 'debian:11',
                'blob_uuid': '73b4e968-7276-4b10-a03e-1258bcb930f1',
                'bus': None,
                'size': 10,
                'type': 'disk'
            },
            {
                'base': None,
                'bus': None,
                'size': 20,
                'type': 'disk'
            }
        ])
        dirty, _ = self._check(existing, self._params(disks=['10@debian:11']))
        self.assertTrue(dirty)


class _Succeeded(SystemExit):
    """The module called exit_json()."""


class _Failed(SystemExit):
    """The module called fail_json()."""


class SfInstanceCreateBudgetTestCase(base.ShakenFistTestCase):
    """await_timeout is the budget for the whole create, not one leg.

    shakenfist/kerbside#355: the client is built with ASYNC_BLOCK, so
    create_instance waited an hour for the instance to leave 'creating'
    and then returned it still transitional. Only then did the 600 second
    await start, on the same condition. The task took the sum, about 4200
    seconds, and reported the 600 -- which is why three occurrences all
    landed within twelve seconds of each other.
    """

    PARAMS = {
        'name': 'test', 'uuid': None, 'cpu': 1, 'ram': 1024,
        'disks': ['8@cirros'], 'diskspecs': None, 'networks': None,
        'networkspecs': None, 'ssh_key': None, 'user_data': None,
        'placement': None, 'video': None, 'nvram_template': None,
        'configdrive': None, 'side_channels': None, 'uefi': None,
        'secureboot': None, 'metadata': None, 'state': 'present',
        'api_url': 'http://localhost:13000', 'namespace': 'ns',
        'key': 'notreallyakey',
    }

    def _run(self, *args, **kwargs):
        _module, client = self._run_module_and_client(*args, **kwargs)
        return client

    def _run_module(self, *args, **kwargs):
        module, _client = self._run_module_and_client(*args, **kwargs)
        return module

    def _run_module_and_client(self, await_instance,
                               client_takes_timeout=True, create_seconds=0,
                               existing=None, await_raises=None,
                               expect_failure=False):
        params = dict(self.PARAMS)
        params['await'] = await_instance
        params['await_timeout'] = 600

        module = mock.MagicMock()
        module.params = params
        module.check_mode = False
        # The real AnsibleModule.exit_json terminates the module; a bare
        # MagicMock would let execution fall through into the deletion
        # path below it. Distinct exception types rather than a shared
        # SystemExit, because assertRaises(SystemExit) cannot tell a
        # success from an early bail-out, and a test whose body is two
        # negative assertions would then pass without running any of the
        # code it names.
        module.exit_json.side_effect = _Succeeded
        module.fail_json.side_effect = _Failed

        created = {'uuid': 'notreallyauuid', 'state': 'creating'}

        # An explicit pair rather than an open ended counter: the module
        # reads the clock exactly twice on this path (once before the
        # create, once before the await), so a third read from anywhere
        # fails loudly instead of quietly shifting the elapsed time.
        clock = iter([0, create_seconds])

        # The signature is the thing under test on the compatibility
        # path, so build create_instance from a real function rather than
        # a bare mock, whose signature is always (*args, **kwargs).
        if client_takes_timeout:
            def create_instance(*args, timeout=None, **kwargs):
                return created
        else:
            def create_instance(*args, **kwargs):
                return created

        client = mock.MagicMock()
        # create_autospec rather than a spec'd MagicMock: only the former
        # keeps a signature inspect.signature() can read, which is the
        # thing _create_accepts_timeout() looks at.
        client.create_instance = mock.create_autospec(
            create_instance, side_effect=create_instance)
        if existing is None:
            # Absent to begin with, so the module takes the create path;
            # every later lookup (the exit_json payload) finds it.
            first = sf_instance.apiclient.ResourceNotFoundException()
        else:
            first = existing
        client.get_instance.side_effect = [first] + [created] * 8
        if await_raises:
            client.await_instance_create.side_effect = await_raises

        with mock.patch.object(
                sf_instance, 'AnsibleModule', return_value=module), \
                mock.patch.object(
                    sf_instance, '_make_client', return_value=client), \
                mock.patch.object(
                    sf_instance.time, 'monotonic',
                    side_effect=lambda: next(clock)):
            self.assertRaises(
                _Failed if expect_failure else _Succeeded,
                sf_instance.run_module)

        return module, client

    def test_awaiting_makes_the_create_return_immediately(self):
        client = self._run(await_instance=True)

        _args, kwargs = client.create_instance.call_args
        self.assertEqual(0, kwargs.get('timeout'))

        # The create returns straight away, so the whole budget is left
        # for the await -- which is the point: the task now fails at
        # await_timeout rather than at 3600 + await_timeout.
        _args, kwargs = client.await_instance_create.call_args
        self.assertEqual(600, kwargs['timeout'])

    def test_not_awaiting_leaves_the_create_blocking(self):
        # Without an await the create is the only thing that can wait, so
        # the historical behaviour has to survive.
        client = self._run(await_instance=False)

        _args, kwargs = client.create_instance.call_args
        self.assertNotIn('timeout', kwargs)
        client.await_instance_create.assert_not_called()

    def test_an_older_client_is_not_handed_the_new_argument(self):
        # The collection requires shakenfist-client unpinned, so the
        # control node may predate the timeout argument. Passing it there
        # is a TypeError that stops every instance creation.
        client = self._run(await_instance=True, client_takes_timeout=False)

        _args, kwargs = client.create_instance.call_args
        self.assertNotIn('timeout', kwargs)

    def test_time_spent_creating_comes_out_of_the_await_budget(self):
        # The fallback for an older client: it cannot get the task down to
        # await_timeout, but the two waits stop being added together.
        client = self._run(await_instance=True, client_takes_timeout=False,
                           create_seconds=100)

        _args, kwargs = client.await_instance_create.call_args
        self.assertEqual(500, kwargs['timeout'])

    def test_the_budget_never_goes_negative(self):
        client = self._run(await_instance=True, client_takes_timeout=False,
                           create_seconds=3600)

        _args, kwargs = client.await_instance_create.call_args
        self.assertEqual(0, kwargs['timeout'])

    def test_an_exhausted_budget_names_the_real_cause(self):
        # The client's own message would report a zero second timeout,
        # which is the symptom of the budget having gone rather than an
        # explanation of where it went -- and an unhelpful message is
        # what kerbside#355 was reported as in the first place.
        module = self._run_module(
            await_instance=True, client_takes_timeout=False,
            create_seconds=3600, await_raises=Exception('not created'),
            expect_failure=True)

        msg = module.fail_json.call_args[1]['msg']
        self.assertIn(
            'entire await_timeout budget of 600 seconds was consumed', msg)

    def test_an_instance_needing_no_replacement_gets_the_whole_budget(self):
        # No delete and no create happen on this path at all, so there
        # is nothing to deduct and the whole budget reaches the await.
        client = self._run(
            await_instance=True,
            existing={
                'uuid': 'notreallyauuid',
                'name': 'test',
                'cpus': 1,
                'memory': 1024,
                'namespace': 'ns',
                'interfaces': [],
                'disk_spec': [
                    {'base': 'cirros', 'bus': None, 'size': 8, 'type': 'disk'}
                ]
            })

        client.create_instance.assert_not_called()
        _args, kwargs = client.await_instance_create.call_args
        self.assertEqual(600, kwargs['timeout'])

    def test_the_chosen_path_is_recorded_in_the_log(self):
        # The fallback is otherwise invisible: an operator debugging a
        # recurrence from the task output alone cannot tell whether the
        # fix engaged or the module quietly took the old behaviour.
        module = self._run_module(await_instance=True)
        self.assertIn('Client accepts a create timeout, so the create will '
                      'not wait', module.exit_json.call_args[1]['log'])

        module = self._run_module(
            await_instance=True, client_takes_timeout=False)
        self.assertIn('Client predates the create timeout argument, so the '
                      'create will wait and what it spends is deducted from '
                      'the await budget instead',
                      module.exit_json.call_args[1]['log'])


class SfInstanceCreateTimeoutDetectionTestCase(base.ShakenFistTestCase):
    """Feature detection, rather than a version test.

    The collection requires shakenfist-client unpinned, so the control
    node's client is whatever pip resolved. There is no version to test
    against which is not itself a guess about backports.
    """

    def test_a_new_client_is_detected(self):
        client = mock.MagicMock()
        client.create_instance = mock.create_autospec(
            lambda *args, timeout=None, **kwargs: None)
        self.assertTrue(sf_instance._create_accepts_timeout(client))

    def test_an_old_client_is_detected(self):
        client = mock.MagicMock()
        client.create_instance = mock.create_autospec(
            lambda *args, **kwargs: None)
        self.assertFalse(sf_instance._create_accepts_timeout(client))

    def test_a_callable_inspect_cannot_describe_is_assumed_old(self):
        # Some C implementations have no signature to read. Guessing
        # "new" there is a TypeError which stops every instance creation
        # in the fleet, so the unreadable case has to fall back.
        client = mock.MagicMock()
        client.create_instance = dict.update
        self.assertFalse(sf_instance._create_accepts_timeout(client))

    def test_a_bare_mock_is_assumed_old(self):
        # A MagicMock does not raise from inspect.signature() -- it
        # reports (*args, **kwargs), which has no timeout parameter.
        self.assertFalse(sf_instance._create_accepts_timeout(mock.MagicMock()))
