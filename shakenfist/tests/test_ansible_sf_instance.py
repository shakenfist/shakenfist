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

    def _run(self, await_instance, client_takes_timeout=True,
             create_seconds=0):
        params = dict(self.PARAMS)
        params['await'] = await_instance
        params['await_timeout'] = 600

        module = mock.MagicMock()
        module.params = params
        module.check_mode = False
        # The real AnsibleModule.exit_json terminates the module; a bare
        # MagicMock would let execution fall through into the deletion
        # path below it.
        module.exit_json.side_effect = SystemExit
        module.fail_json.side_effect = SystemExit

        created = {'uuid': 'notreallyauuid', 'state': 'creating'}
        clock = iter(range(0, 100000, max(create_seconds, 1)))

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
        # Absent to begin with, so the module takes the create path; every
        # later lookup (the exit_json payload) finds it.
        client.get_instance.side_effect = [
            sf_instance.apiclient.ResourceNotFoundException()] + [created] * 8

        with mock.patch.object(
                sf_instance, 'AnsibleModule', return_value=module), \
                mock.patch.object(
                    sf_instance, '_make_client', return_value=client), \
                mock.patch.object(
                    sf_instance.time, 'time', side_effect=lambda: next(clock)):
            self.assertRaises(SystemExit, sf_instance.run_module)

        return client

    def test_awaiting_makes_the_create_return_immediately(self):
        client = self._run(await_instance=True)

        _args, kwargs = client.create_instance.call_args
        self.assertEqual(0, kwargs.get('timeout'))

        # The create returns straight away, so essentially the whole
        # budget is left for the await -- which is the point: the task
        # now fails at await_timeout rather than at 3600 + await_timeout.
        _args, kwargs = client.await_instance_create.call_args
        self.assertGreater(kwargs['timeout'], 590)

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
        self.assertLess(kwargs['timeout'], 600)

    def test_the_budget_never_goes_negative(self):
        client = self._run(await_instance=True, client_takes_timeout=False,
                           create_seconds=3600)

        _args, kwargs = client.await_instance_create.call_args
        self.assertEqual(0, kwargs['timeout'])
