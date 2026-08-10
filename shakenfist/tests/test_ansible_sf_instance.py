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
