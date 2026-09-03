import base64
import json
import os
import tempfile
import time
import uuid
from functools import partial
from unittest import mock

import pycdlib
import testtools
from shakenfist import artifact
from shakenfist import baseobject
from shakenfist import exceptions
from shakenfist import instance
from shakenfist import mariadb
from shakenfist.config import SFConfig
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class VirtMetaTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
        )

        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get_attribute',
                return_value={'value': None, 'update_time': 0})
    @mock.patch('shakenfist.locks.ClusterLock')
    @mock.patch('time.time', return_value=1234)
    def test_instance_new(self, mock_time, mock_get_lock, mock_get_attribute):
        instance.Instance.new(
            'barry', 1, 2048, 'namespace', 'sshkey',
            [{}], 'userdata', {'memory': 16384, 'model': 'cirrus', 'vdi': 'spice'},
            instance_uuid='42424242-4242-4242-8242-424242424242',)

        # State is now stored in MariaDB, so check via the mock
        self.assertEqual(
            {
                'value': instance.Instance.STATE_INITIAL,
                'update_time': 1234
            },
            self.mock_mariadb.get_mariadb_state(
                'instance', '42424242-4242-4242-8242-424242424242'))
        # power_state is now written to MariaDB only (no etcd dual-write)
        inst_attrs = self.mock_mariadb.get_mariadb_instance_attributes(
            '42424242-4242-4242-8242-424242424242')
        self.assertIsNotNone(inst_attrs)
        self.assertEqual(
            {'power_state': instance.Instance.STATE_INITIAL},
            inst_attrs.power_state)

        # etcd.create is no longer called (base class no longer writes
        # to etcd). Instance static values are written to MariaDB via
        # mariadb.create_instance() which is mocked by mock_mariadb.
        inst_data = self.mock_mariadb.instance_objects.get(
            '42424242-4242-4242-8242-424242424242')
        self.assertIsNotNone(inst_data)
        self.assertEqual(1, inst_data.cpus)
        self.assertEqual(2048, inst_data.memory)
        self.assertEqual('barry', inst_data.name)
        self.assertEqual('namespace', inst_data.namespace)


class InstanceTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
        )

        self.config = mock.patch('shakenfist.instance.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.gmov = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version', return_value=6)
        self.mock_gmov = self.gmov.start()
        self.addCleanup(self.gmov.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

    def test_set_state_valid1(self):
        instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', instance_uuid,
                                          set_state=instance.Instance.STATE_PREFLIGHT)
        i = instance.Instance.from_db(instance_uuid)

        with testtools.ExpectedException(exceptions.InvalidStateException):
            i.state = instance.Instance.STATE_INITIAL
        with testtools.ExpectedException(exceptions.InvalidStateException):
            i.state = instance.Instance.STATE_CREATED
        i.state = instance.Instance.STATE_CREATING
        i.state = instance.Instance.STATE_CREATED
        i.state = 'created-error'
        i.state = instance.Instance.STATE_ERROR
        i.state = instance.Instance.STATE_DELETED

    def test_set_state_valid2(self):
        instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', instance_uuid,
                                          set_state=instance.Instance.STATE_PREFLIGHT)
        i = instance.Instance.from_db(instance_uuid)

        i.state = 'preflight-error'
        with testtools.ExpectedException(exceptions.InvalidStateException):
            i.state = instance.Instance.STATE_CREATED

    def test_set_state_delete_wait_error(self):
        instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance(
            'cirros', instance_uuid,
            set_state=instance.Instance.STATE_DELETE_WAIT_ERROR)
        i = instance.Instance.from_db(instance_uuid)

        # Instance.state_targets[STATE_DELETE_WAIT_ERROR] must be a 1-element tuple,
        # not a string (which would allow substring transitions like 'err', 'r', 'o', etc.
        # or reject exact match if compared against string characters).
        self.assertEqual((baseobject.DatabaseBackedObject.STATE_ERROR,),
                         instance.Instance.state_targets[instance.Instance.STATE_DELETE_WAIT_ERROR])

        # Invalid transitions from delete-wait-error must fail
        invalid_states = [
            instance.Instance.STATE_DELETED,
            instance.Instance.STATE_DELETE_WAIT,
            instance.Instance.STATE_CREATED,
            'err',
            'error-extra',
            'e',
            'r',
            'o',
        ]
        for inv_state in invalid_states:
            with testtools.ExpectedException(exceptions.InvalidStateException):
                i.state = inv_state

        # Intended valid transition from delete-wait-error to error must succeed
        i.state = instance.Instance.STATE_ERROR
        self.assertEqual(instance.Instance.STATE_ERROR, i.state.value)

    def test_update_power_state(self):
        instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', instance_uuid)
        i = instance.Instance.from_db(instance_uuid)
        i.update_power_state('off')

        etcd_value = i._db_get_attribute('power_state')
        self.assertTrue(time.time() - etcd_value['power_state_updated'] < 3)
        self.assertEqual('off', etcd_value['power_state'])
        self.assertEqual('initial', etcd_value['power_state_previous'])

    def test_update_power_state_duplicate(self):
        instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', instance_uuid)
        i = instance.Instance.from_db(instance_uuid)
        i.update_power_state('off')
        etcd_value_one = i._db_get_attribute('power_state')

        i.update_power_state('off')
        etcd_value_two = i._db_get_attribute('power_state')

        # That is, the second update was ignored
        self.assertEqual(etcd_value_one['power_state_updated'],
                         etcd_value_two['power_state_updated'])

    def test_str(self):
        instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', instance_uuid)
        i = instance.Instance.from_db(instance_uuid)
        s = str(i)
        self.assertEqual('instance(%s)' % instance_uuid, s)

    def test_requested_placement_survives_the_database(self):
        # The requested placement is the node uuid string the API
        # resolved from the caller's placed_on. _db_create used to
        # discard it (a dict-only normalization), so preflight never saw
        # a targeted create as targeted and silently redirected it to
        # another node (issue 3496).
        instance_uuid = str(uuid.uuid4())
        node_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance(
            'cirros', instance_uuid, requested_placement=node_uuid)
        i = instance.Instance.from_db(instance_uuid)
        self.assertEqual(node_uuid, i.requested_placement)

    def test_no_requested_placement_stored_as_none(self):
        # An untargeted create (the mock helper's default is the empty
        # string, matching etcd-era callers) must normalize to None so
        # preflight's truthiness guard does not fire.
        instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', instance_uuid)
        i = instance.Instance.from_db(instance_uuid)
        self.assertIsNone(i.requested_placement)

    def test_delete_runs_both_phases(self):
        instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', instance_uuid)
        i = instance.Instance.from_db(instance_uuid)

        with mock.patch.object(i, '_delete_on_hypervisor') as mock_hyp, \
                mock.patch.object(i, '_delete_globally') as mock_glob:
            i.delete()

        mock_hyp.assert_called_once_with()
        mock_glob.assert_called_once_with()

    def test_delete_global_only_skips_hypervisor_teardown(self):
        # The cluster maintainer deletes instances hosted on a deleted
        # node with global_only=True: the hypervisor is gone, and the
        # maintainer must not run local teardown (power off, libvirt
        # undefine, disk removal) against its own paths on behalf of an
        # instance that was never there (issue 3803).
        instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', instance_uuid)
        i = instance.Instance.from_db(instance_uuid)

        with mock.patch.object(i, '_delete_on_hypervisor') as mock_hyp, \
                mock.patch.object(i, '_delete_globally') as mock_glob:
            i.delete(global_only=True)

        mock_hyp.assert_not_called()
        mock_glob.assert_called_once_with()

    def test_nvme_bus_disk_stops_on_io_error(self):
        # NVME-bus disks are attached via raw qemu -drive args rather than a
        # libvirt <disk>, so they must carry werror/rerror=stop themselves to
        # get the same pause-on-I/O-error behaviour libvirt.tmpl gives the
        # other buses. Without it a backing-store failure would be invisible
        # (guest keeps running, taking EIO).
        instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance(
            'nvmebox', instance_uuid,
            disk_spec=[{'base': 'cirros', 'size': 21},
                       {'size': 10, 'bus': 'nvme'}])
        i = instance.Instance.from_db(instance_uuid)

        block_devices = i._initialize_block_devices()
        extra = block_devices['extracommands']
        drive_args = [extra[idx + 1] for idx, tok in enumerate(extra)
                      if tok == '-drive']
        self.assertEqual(1, len(drive_args))
        self.assertIn('werror=stop', drive_args[0])
        self.assertIn('rerror=stop', drive_args[0])

    def test_make_config_drive(self):
        instance_uuid = str(uuid.uuid4())
        network_uuid = str(uuid.uuid4())
        iface_uuid_one = str(uuid.uuid4())
        iface_uuid_two = str(uuid.uuid4())

        self.mock_mariadb.create_network('testing', network_uuid, netblock='127.0.0.0/8')
        self.mock_mariadb.create_network_interface(
            iface_uuid_one,
            {
                'network_uuid': network_uuid,
                'address': '127.0.0.5',
                'model': None,
                'macaddress': '1a:91:64:d2:15:39',
            },
            instance_uuid=instance_uuid, order=0)
        self.mock_mariadb.create_network_interface(
            iface_uuid_two,
            {
                'network_uuid': network_uuid,
                'address': '127.0.0.6',
                'model': None,
                'macaddress': '1a:91:64:d2:15:40',
            },
            instance_uuid=instance_uuid, order=1)
        self.mock_mariadb.create_instance(
            'cirros', instance_uuid, 1, ssh_key='thisisasshkey',
            user_data=str(base64.b64encode(b'thisisuserdata'), 'utf-8'))

        i = instance.Instance.from_db(instance_uuid)
        # The NetworkInterface rows above carry instance_uuid; the
        # query-backed ``interfaces`` property finds them automatically.

        (fd, cd_file) = tempfile.mkstemp()
        os.close(fd)

        try:
            i._make_config_drive_openstack_disk(cd_file)
            cd = pycdlib.PyCdlib()
            cd.open(cd_file)

            entries = {}
            for dirname, _, filelist in cd.walk(rr_path='/'):
                for filename in filelist:
                    cd_file_path = os.path.join(dirname, filename)
                    with cd.open_file_from_iso(rr_path=cd_file_path) as f:
                        entries[cd_file_path] = f.read()

            for entry in list(entries.keys()):
                if entry.endswith('vendor_data.json'):
                    self.assertEqual(b'{}', entries[entry],
                                     '%s does not match' % entry)
                    del entries[entry]

                if entry.endswith('vendor_data2.json'):
                    self.assertEqual(b'{}', entries[entry],
                                     '%s does not match' % entry)
                    del entries[entry]

                if entry.endswith('user_data'):
                    self.assertEqual(b'thisisuserdata', entries[entry],
                                     '%s does not match' % entry)
                    del entries[entry]

                if entry.endswith('network_data.json'):
                    nd = json.loads(entries[entry])
                    self.assertEqual(
                        {
                            'links': [
                                {
                                    'ethernet_mac_address': '1a:91:64:d2:15:39',
                                    'id': 'eth0',
                                    'mtu': 7950,
                                    'name': 'eth0',
                                    'type': 'vif',
                                    'vif_id': iface_uuid_one
                                },
                                {
                                    'ethernet_mac_address': '1a:91:64:d2:15:40',
                                    'id': 'eth1',
                                    'mtu': 7950,
                                    'name': 'eth1',
                                    'type': 'vif',
                                    'vif_id': iface_uuid_two
                                }
                            ],
                            'networks': [
                                {
                                    'id': '%s-0' % network_uuid,
                                    'ip_address': '127.0.0.5',
                                          'link': 'eth0',
                                          'netmask': '255.0.0.0',
                                          'network_id': network_uuid,
                                          'routes': [{'gateway': '127.0.0.1',
                                                      'netmask': '0.0.0.0',
                                                      'network': '0.0.0.0'}],
                                          'type': 'ipv4'
                                },
                                {
                                    'id': '%s-1' % network_uuid,
                                    'ip_address': '127.0.0.6',
                                    'link': 'eth1',
                                    'netmask': '255.0.0.0',
                                    'network_id': network_uuid,
                                    'type': 'ipv4'
                                }
                            ],
                            'services': [
                                {
                                    'address': '8.8.8.8',
                                    'type': 'dns'
                                }
                            ]
                        },
                        nd, '%s does not match' % entry
                    )
                    del entries[entry]

                if entry.endswith('meta_data.json'):
                    md = json.loads(entries[entry])
                    if 'random_seed' in md:
                        md['random_seed'] = '...lol...'
                    self.assertEqual(
                        {
                            'availability_zone': 'sfzone',
                            'devices': [],
                            'hostname': 'cirros.local',
                            'launch_index': 0,
                            'name': 'cirros',
                            'project_id': None,
                            'public_keys': {
                                'mykey': 'thisisasshkey'
                            },
                            'random_seed': '...lol...',
                            'uuid': instance_uuid
                        },
                        md, '%s does not match' % entry
                    )
                    del entries[entry]

            self.assertEqual({}, entries)
            cd.close()

        finally:
            if os.path.exists(cd_file):
                os.unlink(cd_file)

    def test_make_config_drive_provide_dns(self):
        instance_uuid = str(uuid.uuid4())
        network_uuid = str(uuid.uuid4())
        iface_uuid_one = str(uuid.uuid4())
        iface_uuid_two = str(uuid.uuid4())

        self.mock_mariadb.create_network(
            'testing', network_uuid, netblock='10.0.0.0/8', provide_dns=True)
        self.mock_mariadb.create_network_interface(
            iface_uuid_one,
            {
                'network_uuid': network_uuid,
                'address': '10.0.0.5',
                'model': None,
                'macaddress': '1a:91:64:d2:15:39',
            },
            instance_uuid=instance_uuid, order=0)
        self.mock_mariadb.create_network_interface(
            iface_uuid_two,
            {
                'network_uuid': network_uuid,
                'address': '10.0.0.6',
                'model': None,
                'macaddress': '1a:91:64:d2:15:40',
            },
            instance_uuid=instance_uuid, order=1)
        self.mock_mariadb.create_instance(
            'cirros', instance_uuid, 1, ssh_key='thisisasshkey',
            user_data=str(base64.b64encode(b'thisisuserdata'), 'utf-8'))

        i = instance.Instance.from_db(instance_uuid)
        # The NetworkInterface rows above carry instance_uuid; the
        # query-backed ``interfaces`` property finds them automatically.

        (fd, cd_file) = tempfile.mkstemp()
        os.close(fd)

        try:
            i._make_config_drive_openstack_disk(cd_file)
            cd = pycdlib.PyCdlib()
            cd.open(cd_file)

            entries = {}
            for dirname, _, filelist in cd.walk(rr_path='/'):
                for filename in filelist:
                    cd_file_path = os.path.join(dirname, filename)
                    with cd.open_file_from_iso(rr_path=cd_file_path) as f:
                        entries[cd_file_path] = f.read()

            for entry in list(entries.keys()):
                if entry.endswith('vendor_data.json'):
                    self.assertEqual(b'{}', entries[entry],
                                     '%s does not match' % entry)
                    del entries[entry]

                if entry.endswith('vendor_data2.json'):
                    self.assertEqual(b'{}', entries[entry],
                                     '%s does not match' % entry)
                    del entries[entry]

                if entry.endswith('user_data'):
                    self.assertEqual(b'thisisuserdata', entries[entry],
                                     '%s does not match' % entry)
                    del entries[entry]

                if entry.endswith('network_data.json'):
                    nd = json.loads(entries[entry])
                    self.assertEqual(
                        {
                            'links': [
                                {
                                    'ethernet_mac_address': '1a:91:64:d2:15:39',
                                    'id': 'eth0',
                                    'mtu': 7950,
                                    'name': 'eth0',
                                    'type': 'vif',
                                    'vif_id': iface_uuid_one
                                },
                                {
                                    'ethernet_mac_address': '1a:91:64:d2:15:40',
                                    'id': 'eth1',
                                    'mtu': 7950,
                                    'name': 'eth1',
                                    'type': 'vif',
                                    'vif_id': iface_uuid_two
                                }
                            ],
                            'networks': [
                                {
                                    'id': '%s-0' % network_uuid,
                                    'ip_address': '10.0.0.5',
                                          'link': 'eth0',
                                          'netmask': '255.0.0.0',
                                          'network_id': network_uuid,
                                          'routes': [{'gateway': '10.0.0.1',
                                                      'netmask': '0.0.0.0',
                                                      'network': '0.0.0.0'}],
                                          'type': 'ipv4'
                                },
                                {
                                    'id': '%s-1' % network_uuid,
                                    'ip_address': '10.0.0.6',
                                    'link': 'eth1',
                                    'netmask': '255.0.0.0',
                                    'network_id': network_uuid,
                                    'type': 'ipv4'
                                }
                            ],
                            'services': [
                                {
                                    'address': '10.0.0.1',
                                    'type': 'dns',
                                    'search': ['unittest.sfzone'],
                                }
                            ]
                        },
                        nd, '%s does not match' % entry
                    )
                    del entries[entry]

                if entry.endswith('meta_data.json'):
                    md = json.loads(entries[entry])
                    if 'random_seed' in md:
                        md['random_seed'] = '...lol...'
                    self.assertEqual(
                        {
                            'availability_zone': 'sfzone',
                            'devices': [],
                            'hostname': 'cirros.local',
                            'launch_index': 0,
                            'name': 'cirros',
                            'project_id': None,
                            'public_keys': {
                                'mykey': 'thisisasshkey'
                            },
                            'random_seed': '...lol...',
                            'uuid': instance_uuid
                        },
                        md, '%s does not match' % entry
                    )
                    del entries[entry]

            self.assertEqual({}, entries)
            cd.close()

        finally:
            if os.path.exists(cd_file):
                os.unlink(cd_file)


class FakeLibvirtError(Exception):
    ...


class FakeLibvirtModule:
    libvirtError = FakeLibvirtError
    VIR_DOMAIN_REBOOT_ACPI_POWER_BTN = 4


class FakeDomain:
    def __init__(self, active=True, error=None):
        self._active = active
        self._error = error
        self.reboot_flags = None
        self.reset_calls = 0

    def isActive(self):
        return 1 if self._active else 0

    def reboot(self, flags=0):
        if self._error:
            raise self._error
        self.reboot_flags = flags

    def reset(self):
        if self._error:
            raise self._error
        self.reset_calls += 1


class FakeLibvirtConnection:
    def __init__(self, domain):
        self.libvirt = FakeLibvirtModule()
        self._domain = domain

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_domain_from_sf_uuid(self, u):
        return self._domain


class InstanceRebootTestCase(base.ShakenFistTestCase):
    """Reboot of a domain which is not running must raise
    InvalidLifecycleState (which the API maps to the documented 409), not
    leak a libvirtError to the generic 500 handler (issue 3630).

    Shaken Fist domains are persistent, so a powered off instance is
    normally a defined-but-inactive domain rather than an undefined one:
    the original 'if not inst' guard never fired for it.
    """

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
        )

        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.gmov = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version', return_value=6)
        self.mock_gmov = self.gmov.start()
        self.addCleanup(self.gmov.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', self.instance_uuid)
        self.inst = instance.Instance.from_db(self.instance_uuid)

    def _mock_libvirt(self, domain):
        lc = mock.patch(
            'shakenfist.instance.util_libvirt.LibvirtConnection',
            return_value=FakeLibvirtConnection(domain))
        lc.start()
        self.addCleanup(lc.stop)

    def test_reboot_hard_no_domain(self):
        self._mock_libvirt(None)
        with testtools.ExpectedException(exceptions.InvalidLifecycleState):
            self.inst.reboot(hard=True)

    def test_reboot_hard_inactive_domain(self):
        self._mock_libvirt(FakeDomain(active=False))
        with testtools.ExpectedException(exceptions.InvalidLifecycleState):
            self.inst.reboot(hard=True)

    def test_reboot_soft_inactive_domain(self):
        self._mock_libvirt(FakeDomain(active=False))
        with testtools.ExpectedException(exceptions.InvalidLifecycleState):
            self.inst.reboot(hard=False)

    def test_reboot_hard_active_domain(self):
        domain = FakeDomain()
        self._mock_libvirt(domain)
        self.inst.reboot(hard=True)
        self.assertEqual(1, domain.reset_calls)

    def test_reboot_soft_active_domain(self):
        domain = FakeDomain()
        self._mock_libvirt(domain)
        self.inst.reboot(hard=False)
        self.assertEqual(
            FakeLibvirtModule.VIR_DOMAIN_REBOOT_ACPI_POWER_BTN,
            domain.reboot_flags)

    def test_reboot_hard_domain_stops_after_check(self):
        # The domain can shut off between the isActive() check and the
        # reboot call. That libvirtError must also be translated.
        self._mock_libvirt(FakeDomain(error=FakeLibvirtError(
            'Requested operation is not valid: domain is not running')))
        with testtools.ExpectedException(exceptions.InvalidLifecycleState):
            self.inst.reboot(hard=True)

    def test_reboot_soft_domain_stops_after_check(self):
        self._mock_libvirt(FakeDomain(error=FakeLibvirtError(
            'Requested operation is not valid: domain is not running')))
        with testtools.ExpectedException(exceptions.InvalidLifecycleState):
            self.inst.reboot(hard=False)

    def test_reboot_hard_other_libvirt_error_passes_through(self):
        self._mock_libvirt(FakeDomain(error=FakeLibvirtError(
            'internal error: something else entirely')))
        with testtools.ExpectedException(FakeLibvirtError):
            self.inst.reboot(hard=True)


class InstancesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.mock_mariadb.create_instance(
            name='cirros', uuid='373a165e-9720-4e14-bd0e-9612de79ff15',
            namespace='gerkin', set_state=instance.Instance.STATE_DELETED,
            place_on_node='node1')
        self.mock_mariadb.create_instance(
            name='cirros', uuid='b078cb4e-857c-4f04-b011-751742ef5817',
            namespace='namespace', set_state=instance.Instance.STATE_CREATED,
            place_on_node='node1')
        self.mock_mariadb.create_instance(
            name='cirros', uuid='a7c5ecec-c3a9-4774-ad1b-249d9e90e806',
            namespace='namespace', set_state=instance.Instance.STATE_DELETED,
            place_on_node='node1')

    def test_base_iteration(self):
        uuids = []
        for i in instance.all_instances():
            uuids.append(str(i.uuid))

        self.assertEqual(3, len(uuids))
        self.assertTrue('373a165e-9720-4e14-bd0e-9612de79ff15' in uuids)
        self.assertTrue('b078cb4e-857c-4f04-b011-751742ef5817' in uuids)
        self.assertTrue('a7c5ecec-c3a9-4774-ad1b-249d9e90e806' in uuids)

    def test_placement_filter_all(self):
        uuids = []
        for i in instance.Instances([partial(instance.placement_filter, 'node1')]):
            uuids.append(str(i.uuid))

        self.assertEqual(3, len(uuids))
        self.assertTrue('373a165e-9720-4e14-bd0e-9612de79ff15' in uuids)
        self.assertTrue('b078cb4e-857c-4f04-b011-751742ef5817' in uuids)
        self.assertTrue('a7c5ecec-c3a9-4774-ad1b-249d9e90e806' in uuids)

    def test_placement_filter_none(self):
        uuids = []
        for i in instance.Instances([partial(instance.placement_filter, 'node2')]):
            uuids.append(str(i.uuid))

        self.assertEqual([], uuids)

    def test_namespace_filter(self):
        uuids = []
        for i in instance.Instances([partial(baseobject.namespace_filter, 'gerkin')]):
            uuids.append(str(i.uuid))

        self.assertEqual(['373a165e-9720-4e14-bd0e-9612de79ff15'], uuids)


class AgentOperationQueueTestCase(base.ShakenFistTestCase):
    """Regression tests for the crash-safe agent operation dispatch queue.

    agent_operation_next() must never lose an operation: a queued head is
    returned but left on the queue, an executing head is left on the
    queue for the sidechannel daemon's reaper to resolve (the entry is
    only retired once the operation reaches a terminal state), and
    terminal or invalid heads must not wedge the queue.
    """

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
        )

        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.gmov = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version', return_value=6)
        self.mock_gmov = self.gmov.start()
        self.addCleanup(self.gmov.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', self.instance_uuid)
        self.inst = instance.Instance.from_db(self.instance_uuid)

    def _make_agentop(self, state=None, deadline=None):
        op = AgentOperation.new(
            str(uuid.uuid4()), 'unittest', self.instance_uuid,
            [{'command': 'execute', 'commandline': 'true'}],
            deadline=deadline)
        if state:
            op.state = state
        self.inst.agent_operation_enqueue(op.uuid)
        return op

    def _queue(self):
        return self.inst.agent_operations.get('queue', [])

    def test_next_empty_queue(self):
        self.assertIsNone(self.inst.agent_operation_next())

    def test_next_returns_queued_head_without_popping(self):
        op1 = self._make_agentop(state=AgentOperation.STATE_QUEUED)
        op2 = self._make_agentop(state=AgentOperation.STATE_QUEUED)

        # The head is returned, but stays at the head of the queue: if the
        # dispatcher dies before delivering it, a later call must be able to
        # return it again.
        self.assertEqual(
            str(op1.uuid), str(self.inst.agent_operation_next().uuid))
        self.assertEqual([str(op1.uuid), str(op2.uuid)], self._queue())
        self.assertEqual(
            str(op1.uuid), str(self.inst.agent_operation_next().uuid))

    def test_next_retires_finished_head(self):
        op1 = self._make_agentop(state=AgentOperation.STATE_QUEUED)
        op2 = self._make_agentop(state=AgentOperation.STATE_QUEUED)

        # Once the head has reached a terminal state it is lazily popped
        # and the next operation dispatched.
        op1.state = AgentOperation.STATE_EXECUTING
        op1.state = AgentOperation.STATE_COMPLETE

        self.assertEqual(
            str(op2.uuid), str(self.inst.agent_operation_next().uuid))
        self.assertEqual([str(op2.uuid)], self._queue())

    def test_next_retires_errored_head(self):
        op1 = self._make_agentop(state=AgentOperation.STATE_QUEUED)
        op2 = self._make_agentop(state=AgentOperation.STATE_QUEUED)

        # An errored head must not block the queue forever.
        op1.state = baseobject.DatabaseBackedObject.STATE_ERROR

        self.assertEqual(
            str(op2.uuid), str(self.inst.agent_operation_next().uuid))
        self.assertEqual([str(op2.uuid)], self._queue())

    def test_next_waits_for_initial_head(self):
        op1 = self._make_agentop()
        op2 = self._make_agentop(state=AgentOperation.STATE_QUEUED)

        # The head is still being enqueued by the API (state initial), so
        # nothing is dispatchable yet -- order is preserved.
        self.assertIsNone(self.inst.agent_operation_next())
        self.assertEqual([str(op1.uuid), str(op2.uuid)], self._queue())

        # Once the API finishes, the head dispatches.
        op1.state = AgentOperation.STATE_QUEUED
        self.assertEqual(
            str(op1.uuid), str(self.inst.agent_operation_next().uuid))

    def test_next_retires_invalid_head(self):
        self.inst.agent_operation_enqueue(str(uuid.uuid4()))
        op2 = self._make_agentop(state=AgentOperation.STATE_QUEUED)

        # A queue entry with no backing operation is retired, not returned.
        self.assertEqual(
            str(op2.uuid), str(self.inst.agent_operation_next().uuid))
        self.assertEqual([str(op2.uuid)], self._queue())

    def test_next_expires_a_queued_head_past_its_deadline(self):
        # A caller who has already given up must not be allowed to
        # occupy the instance's single executor slot. The expired head
        # is retired and the next entry considered in the same pass.
        op1 = self._make_agentop(
            state=AgentOperation.STATE_QUEUED, deadline=1.0)
        op2 = self._make_agentop(state=AgentOperation.STATE_QUEUED)

        self.assertEqual(
            str(op2.uuid), str(self.inst.agent_operation_next().uuid))
        self.assertEqual([str(op2.uuid)], self._queue())
        self.assertEqual(AgentOperation.STATE_EXPIRED, op1.state.value)
        self.assertEqual(
            'the operation deadline passed while queued', op1.state.message)

    def test_next_expires_consecutive_expired_heads(self):
        op1 = self._make_agentop(
            state=AgentOperation.STATE_QUEUED, deadline=1.0)
        op2 = self._make_agentop(
            state=AgentOperation.STATE_QUEUED, deadline=2.0)
        op3 = self._make_agentop(state=AgentOperation.STATE_QUEUED)

        self.assertEqual(
            str(op3.uuid), str(self.inst.agent_operation_next().uuid))
        self.assertEqual([str(op3.uuid)], self._queue())
        self.assertEqual(AgentOperation.STATE_EXPIRED, op1.state.value)
        self.assertEqual(AgentOperation.STATE_EXPIRED, op2.state.value)

    def test_next_returns_a_head_within_its_deadline(self):
        op1 = self._make_agentop(
            state=AgentOperation.STATE_QUEUED,
            deadline=time.time() + 3600)

        self.assertEqual(
            str(op1.uuid), str(self.inst.agent_operation_next().uuid))
        self.assertEqual([str(op1.uuid)], self._queue())
        self.assertEqual(AgentOperation.STATE_QUEUED, op1.state.value)

    def test_next_never_expires_an_explicit_zero_deadline(self):
        # 0.0 is the caller's "no wall-clock deadline at all"
        # sentinel, not an ancient timestamp.
        op1 = self._make_agentop(
            state=AgentOperation.STATE_QUEUED, deadline=0.0)

        with mock.patch('time.time', return_value=time.time() + 1000000):
            self.assertEqual(
                str(op1.uuid), str(self.inst.agent_operation_next().uuid))
        self.assertEqual(AgentOperation.STATE_QUEUED, op1.state.value)

    def test_next_expires_a_null_deadline_head_on_the_default(self):
        # A row written by an API server which predates deadlines. This
        # is the shape a rolling upgrade produces, and the only tests
        # of the fallback anchor otherwise go through the helper rather
        # than through the enforcement site.
        op1 = self._make_agentop(state=AgentOperation.STATE_QUEUED)
        op2 = self._make_agentop(
            state=AgentOperation.STATE_QUEUED, deadline=time.time() + 3600)
        self.assertIsNone(op1.deadline)

        anchor = op1.state.update_time
        with mock.patch('time.time', return_value=anchor + 601):
            self.assertEqual(
                str(op2.uuid), str(self.inst.agent_operation_next().uuid))

        self.assertEqual(AgentOperation.STATE_EXPIRED, op1.state.value)
        self.assertEqual([str(op2.uuid)], self._queue())

    def test_next_keeps_a_null_deadline_head_inside_the_default(self):
        op1 = self._make_agentop(state=AgentOperation.STATE_QUEUED)

        anchor = op1.state.update_time
        with mock.patch('time.time', return_value=anchor + 599):
            self.assertEqual(
                str(op1.uuid), str(self.inst.agent_operation_next().uuid))
        self.assertEqual(AgentOperation.STATE_QUEUED, op1.state.value)

    def test_next_leaves_a_preflight_head_alone(self):
        # A preflight head is mid-creation and is enforced by
        # NodeAgentopOp._preflight(), not here. Expiring it in the
        # queue would race a preflight task which is still working.
        op1 = self._make_agentop(
            state=AgentOperation.STATE_PREFLIGHT, deadline=1.0)
        self._make_agentop(state=AgentOperation.STATE_QUEUED)

        self.assertIsNone(self.inst.agent_operation_next())
        self.assertEqual(AgentOperation.STATE_PREFLIGHT, op1.state.value)

    def test_next_retires_an_already_expired_head(self):
        # Expired elsewhere -- by preflight or the executor -- the head
        # is popped by the ordinary terminal state rule.
        op1 = self._make_agentop(state=AgentOperation.STATE_QUEUED)
        op2 = self._make_agentop(state=AgentOperation.STATE_QUEUED)
        op1.expire('expired somewhere else')

        self.assertEqual(
            str(op2.uuid), str(self.inst.agent_operation_next().uuid))
        self.assertEqual([str(op2.uuid)], self._queue())

    def test_next_leaves_an_executing_head_alone(self):
        # An executing head is being worked on, and its queue entry is
        # what remains if the executor dies mid-flight. Popping it is
        # what used to leak an operation orphaned in executing; the
        # sidechannel daemon's reaper, not this method, is what resolves
        # one.
        op1 = self._make_agentop(state=AgentOperation.STATE_QUEUED)
        op2 = self._make_agentop(state=AgentOperation.STATE_QUEUED)
        op1.state = AgentOperation.STATE_EXECUTING

        self.assertIsNone(self.inst.agent_operation_next())
        self.assertEqual([str(op1.uuid), str(op2.uuid)], self._queue())

        # Still there on the next pass: op2 must not be dispatched from
        # behind it either, because order is preserved.
        self.assertIsNone(self.inst.agent_operation_next())
        self.assertEqual([str(op1.uuid), str(op2.uuid)], self._queue())
        self.assertEqual(AgentOperation.STATE_EXECUTING, op1.state.value)

    def test_next_retires_every_terminal_head(self):
        # The other half of the terminal-only pop rule, and the half a
        # regression would be silent about: an executing head surviving
        # is worth nothing if a terminal head stops popping, because a
        # queue whose head errored would block forever again.
        for terminal in AgentOperation.TERMINAL_STATES:
            op1 = self._make_agentop(state=AgentOperation.STATE_QUEUED)
            op2 = self._make_agentop(state=AgentOperation.STATE_QUEUED)

            if terminal == AgentOperation.STATE_COMPLETE:
                op1.state = AgentOperation.STATE_EXECUTING
            op1.state = terminal

            self.assertEqual(
                str(op2.uuid), str(self.inst.agent_operation_next().uuid),
                f'a {terminal} head was not retired')
            self.assertEqual([str(op2.uuid)], self._queue())

            # Drain op2 so the next iteration starts from an empty queue.
            op2.state = AgentOperation.STATE_EXECUTING
            op2.state = AgentOperation.STATE_COMPLETE
            self.assertIsNone(self.inst.agent_operation_next())
            self.assertEqual([], self._queue())


class AgentOperationDeadlineTestCase(base.ShakenFistTestCase):
    """The deadline and progress bookkeeping an operation carries.

    Enforcement is tested where it happens (AgentOperationQueueTestCase
    here, test_agent_operation_expiry.py for the helpers), so these
    tests are about storage and retrieval, and about what each of the
    three possible values means. None means no client intent was recorded, so the server
    default applies; an explicit 0.0 means the caller asked for none.
    Collapsing those two is the failure this schema exists to avoid.
    """

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
        )

        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.gmov = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version', return_value=6)
        self.mock_gmov = self.gmov.start()
        self.addCleanup(self.gmov.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', self.instance_uuid)

    def _make_agentop(self, **kwargs):
        return AgentOperation.new(
            str(uuid.uuid4()), 'unittest', self.instance_uuid,
            [{'command': 'execute', 'commandline': 'true'}], **kwargs)

    def test_defaults_are_unset(self):
        # An operation created the way today's three API endpoints
        # create one records no timing intent at all.
        op = self._make_agentop()
        self.assertIsNone(op.deadline)
        self.assertIsNone(op.progress_timeout)

    def test_values_round_trip_through_the_database(self):
        op = self._make_agentop(deadline=1787427490.5, progress_timeout=30.0)
        reread = AgentOperation.from_db(op.uuid)
        self.assertEqual(1787427490.5, reread.deadline)
        self.assertEqual(30.0, reread.progress_timeout)

    def test_zero_is_not_none(self):
        # 0.0 is the sentinel for "the caller asked for no deadline",
        # and must survive as something other than "unset". A real
        # deadline is an absolute timestamp, so zero is unambiguous.
        op = self._make_agentop(deadline=0.0, progress_timeout=0.0)
        reread = AgentOperation.from_db(op.uuid)
        self.assertIsNotNone(reread.deadline)
        self.assertEqual(0.0, reread.deadline)
        self.assertEqual(0.0, reread.progress_timeout)

    def test_attribute_defaults(self):
        op = self._make_agentop()
        self.assertIsNone(op.last_progress)
        self.assertEqual(0, op.attempts)

    def test_add_result_does_not_clobber_progress_attributes(self):
        # add_result() builds a fresh attributes object whose
        # last_progress and attempts carry model defaults, so without
        # its field mask it would push those defaults over whatever a
        # concurrent progress writer had committed. This is the
        # cross-attribute lost update the mask exists to prevent, and
        # it only became a real hazard once these columns existed.
        op = self._make_agentop()
        attrs = mariadb.get_agent_operation_attributes(uuid.UUID(str(op.uuid)))
        attrs.last_progress = 1787427490.5
        attrs.attempts = 2
        mariadb.update_agent_operation_attributes(
            attrs, fields=['last_progress', 'attempts'])

        op.add_result(0, {'status': 0})

        self.assertEqual({'0': {'status': 0}}, op.results)
        self.assertEqual(1787427490.5, op.last_progress)
        self.assertEqual(2, op.attempts)

    def _view(self, op):
        # The reference grouping in external_view() reads the caller's
        # namespace out of a JWT, so it needs a request context this
        # test has no interest in standing up. It predates this work.
        with mock.patch(
                'shakenfist.operations.agentoperation.'
                'references_to_grouped_dict', return_value={}):
            return op.external_view()

    def test_external_view_carries_every_value(self):
        op = self._make_agentop(deadline=1787427490.5, progress_timeout=30.0)
        view = self._view(op)
        self.assertEqual(1787427490.5, view['deadline'])
        self.assertEqual(30.0, view['progress_timeout'])
        self.assertIsNone(view['last_progress'])
        self.assertEqual(0, view['attempts'])
        self.assertEqual({}, view['results'])

    def test_attributes_survive_a_lost_get_or_create_race(self):
        # _attributes() is a get-or-create, so it has to cope with
        # losing the create to another thread. It re-reads, but the row
        # can be gone again by then -- the operation was deleted
        # between the two calls. Returning None there would raise
        # AttributeError in every caller, including external_view() on
        # a user-facing path, so the fallback is the defaults.
        op = self._make_agentop()
        with mock.patch('shakenfist.mariadb.get_agent_operation_attributes',
                        return_value=None), \
            mock.patch(
                'shakenfist.mariadb.create_agent_operation_attributes',
                return_value=False):
            self.assertEqual({}, op.results)
            self.assertIsNone(op.last_progress)
            self.assertEqual(0, op.attempts)

    def test_external_view_reads_attributes_once(self):
        # The three attribute values are taken from a single read
        # rather than through their properties, which would each cost
        # a round trip. If this ever regresses the view silently
        # triples its database load.
        op = self._make_agentop()
        with mock.patch(
                'shakenfist.mariadb.get_agent_operation_attributes',
                side_effect=mariadb.get_agent_operation_attributes) as m:
            self._view(op)
        self.assertEqual(1, m.call_count)


class InstanceAttributeFieldMaskTestCase(base.ShakenFistTestCase):
    """Regression tests for the cross-attribute lost update.

    Instance._db_set_attribute is get-row, set-one-field, write-row.
    Before field masking, the write-row step wrote every column, so a
    writer of one attribute could revert a concurrent writer's
    committed change to a different attribute of the same row to the
    stale value it had read (observed as an agent operation enqueued by
    the API vanishing when the sidechannel monitor wrote agent facts at
    the same moment). Every single-attribute write must therefore name
    the one column it is changing.
    """

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
        )

        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.gmov = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version', return_value=6)
        self.mock_gmov = self.gmov.start()
        self.addCleanup(self.gmov.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', self.instance_uuid)
        self.inst = instance.Instance.from_db(self.instance_uuid)

    def _last_update_fields(self, mock_update):
        self.assertTrue(mock_update.called)
        return mock_update.call_args.kwargs.get('fields')

    def test_set_attribute_masks_named_field(self):
        with mock.patch(
                'shakenfist.mariadb.update_instance_attributes',
                return_value=True) as mock_update:
            self.inst._db_set_attribute(
                'agent_attributes', {'facts': {'os': 'debian'}})
        self.assertEqual(
            ['agent_attributes'], self._last_update_fields(mock_update))

    def test_set_attribute_masks_error_as_error_message(self):
        with mock.patch(
                'shakenfist.mariadb.update_instance_attributes',
                return_value=True) as mock_update:
            self.inst._db_set_attribute('error', {'message': 'boom'})
        self.assertEqual(
            ['error_message'], self._last_update_fields(mock_update))

    def test_set_attribute_masks_kvm_pid(self):
        with mock.patch(
                'shakenfist.mariadb.update_instance_attributes',
                return_value=True) as mock_update:
            self.inst._db_set_attribute('kvm_pid', {'pid': 123})
        self.assertEqual(
            ['kvm_pid'], self._last_update_fields(mock_update))

    def test_concurrent_writers_do_not_lose_updates(self):
        # Writer one (the API on another node) enqueues an agent
        # operation. Writer two (the sidechannel monitor) writes agent
        # facts from a row snapshot read before the enqueue. With
        # field-masked writes the enqueue must survive.
        self.inst.agent_operation_enqueue('op-from-the-api')

        stale = self.mock_mariadb._mariadb_get_instance_attributes(
            self.instance_uuid).model_copy(deep=True)
        stale.agent_operations = None
        stale.agent_attributes = {'facts': {'os': 'debian'}}
        self.mock_mariadb._mariadb_update_instance_attributes(
            stale, fields=['agent_attributes'])

        self.assertEqual(
            ['op-from-the-api'],
            self.inst.agent_operations.get('queue', []))
        self.assertEqual({'os': 'debian'}, self.inst.agent_facts)


class InstanceAttributeMemoTestCase(base.ShakenFistTestCase):
    """Regression tests for the per-external-view attributes memo.

    Every MariaDB backed instance attribute lives in one
    instance_attributes row, but _db_get_attribute fetched that row per
    read. Building an external view reads nine of them, so a single API
    GET of an instance cost nine identical GetInstanceAttributes RPCs.
    Inside an attribute_memo() block the row must be fetched once, while
    remaining a per-call memo -- no read outside the block, and no read
    after a write inside it, may be served from stale data.
    """

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
        )

        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.gmov = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version', return_value=6)
        self.mock_gmov = self.gmov.start()
        self.addCleanup(self.gmov.stop)

        # Object reference grouping asks who is making the request, which
        # outside of a Flask request context has no answer.
        self.request_namespace = mock.patch(
            'shakenfist.schema.object_reference.request_namespace',
            return_value='system')
        self.mock_request_namespace = self.request_namespace.start()
        self.addCleanup(self.request_namespace.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance(
            'cirros', self.instance_uuid,
            set_state=instance.Instance.STATE_CREATED)
        self.inst = instance.Instance.from_db(self.instance_uuid)

    def _counting_fetch(self):
        """Count fetches of the instance_attributes row."""
        return mock.patch(
            'shakenfist.mariadb.get_instance_attributes',
            side_effect=self.mock_mariadb._mariadb_get_instance_attributes)

    def test_external_view_fetches_attributes_row_once(self):
        with self._counting_fetch() as fetch:
            self.inst.external_view()
        self.assertEqual(1, fetch.call_count)

    def test_external_view_still_reports_attributes(self):
        self.inst.ports = {'console_port': 12345, 'vdi_port': 12346}
        self.inst._db_set_attribute(
            'power_state', {'power_state': 'on'})

        view = self.inst.external_view()
        self.assertEqual(12345, view['console_port'])
        self.assertEqual(12346, view['vdi_port'])
        self.assertEqual('on', view['power_state'])

    def test_memo_does_not_persist_between_calls(self):
        # A memo which outlived the call would be a cache with staleness
        # semantics of its own, which is explicitly not what this is.
        with self._counting_fetch() as fetch:
            self.inst.external_view()
            self.inst.external_view()
        self.assertEqual(2, fetch.call_count)

    def test_no_memo_outside_a_block(self):
        with self._counting_fetch() as fetch:
            self.inst.power_state
            self.inst.ports
        self.assertEqual(2, fetch.call_count)

    def test_memo_serves_repeated_reads_from_one_fetch(self):
        with self._counting_fetch() as fetch:
            with self.inst.attribute_memo():
                self.inst.power_state
                self.inst.ports
                self.inst.block_devices
        self.assertEqual(1, fetch.call_count)

    def test_nested_memo_blocks_share_one_fetch(self):
        with self._counting_fetch() as fetch:
            with self.inst.attribute_memo():
                self.inst.power_state
                with self.inst.attribute_memo():
                    self.inst.ports
                # The inner block exiting must not discard the outer
                # block's memo.
                self.inst.block_devices
            self.inst.power_state
        self.assertEqual(2, fetch.call_count)

    def test_write_inside_a_memo_is_visible_to_later_reads(self):
        with self.inst.attribute_memo():
            self.assertEqual({}, self.inst.ports)
            self.inst.ports = {'console_port': 4242}
            self.assertEqual({'console_port': 4242}, self.inst.ports)

    def test_write_by_another_writer_is_seen_after_the_block(self):
        with self.inst.attribute_memo():
            self.inst.power_state

        other = instance.Instance.from_db(self.instance_uuid)
        other._db_set_attribute('power_state', {'power_state': 'off'})
        self.assertEqual({'power_state': 'off'}, self.inst.power_state)

    def test_memo_is_released_when_the_block_raises(self):
        try:
            with self.inst.attribute_memo():
                raise exceptions.InstanceException('boom')
        except exceptions.InstanceException:
            pass

        with self._counting_fetch() as fetch:
            self.inst.power_state
            self.inst.ports
        self.assertEqual(2, fetch.call_count)


class InstanceSnapshotTargetTestCase(base.ShakenFistTestCase):
    """Which artifact a snapshot is indexed into.

    The fourth write path, and the one the original #3640 sweep missed
    because that only looked at `external_api/` and `operations/`. It
    resolved by visibility and fed the result straight to `add_index`,
    which ends in `delete_old_versions`.

    It is not reachable across namespaces today -- the URL carries the
    instance UUID and the type filter pins it to TYPE_SNAPSHOT, so
    nothing else resolves there. That makes the guard cheap rather than
    unnecessary, and this test is what stops the next artifact type
    minted against an instance URL having to rediscover the rule.
    """

    URL = '%san-instance/vda' % artifact.INSTANCE_URL

    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        for ns in ['system', 'owner', 'stranger']:
            self.mock_mariadb.create_namespace(ns, 'key1', '%skey' % ns)

        for target in ['os.path.exists',
                       'shakenfist.instance.niso_snapshot',
                       'shakenfist.instance.niso_create_and_enqueue']:
            patcher = mock.patch(target)
            started = patcher.start()
            self.addCleanup(patcher.stop)
            if target == 'os.path.exists':
                started.return_value = True

    def _snapshot_as(self, namespace):
        """Run the resolution half of Instance.snapshot.

        A MagicMock stands in for the instance: building a real one
        needs a scheduler, a node and a disk on the filesystem, none of
        which have anything to say about which artifact the snapshot is
        written into.
        """
        inst = mock.MagicMock()
        inst.uuid = 'an-instance'
        inst.namespace = namespace
        inst.uefi = False
        inst.block_devices = {'devices': [{
            'type': 'qcow2', 'device': 'vda',
            'path': '/somewhere/vda', 'snapshot_ignores': False}]}

        out = instance.Instance.snapshot(inst)
        return out['vda']['artifact_uuid']

    def test_a_foreign_artifact_is_not_snapshotted_into(self):
        # Shared, so visibility resolution would have found it. Snapshot
        # URLs are not caller supplied, so this is the invariant rather
        # than an exploit -- but it is the invariant the docs assert.
        theirs = artifact.Artifact.new(
            artifact.Artifact.TYPE_SNAPSHOT, self.URL,
            name='an-instance/vda', namespace='owner')
        theirs.state = artifact.Artifact.STATE_CREATED
        theirs.shared = True

        self.assertNotEqual(str(theirs.uuid), self._snapshot_as('stranger'))

    def test_our_own_snapshot_artifact_is_reused(self):
        # The control. Resolving rather than creating is what makes a
        # second snapshot of the same disk a new version of one artifact
        # instead of a second artifact, which is what max_versions
        # counts.
        first = self._snapshot_as('owner')
        self.assertEqual(first, self._snapshot_as('owner'))


class InstanceDiskFetchTestCase(base.ShakenFistTestCase):
    """Coverage for Instance.enqueue_disk_fetches (issue 3720).

    Every placement decision must be paired with artifact fetches
    targeting the chosen node, because instance create assumes the
    image for each disk is already in the node-local cache.
    """

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
        )

        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.gmov = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version', return_value=6)
        self.mock_gmov = self.gmov.start()
        self.addCleanup(self.gmov.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    @mock.patch('shakenfist.instance.afo_create_and_enqueue')
    @mock.patch('shakenfist.artifact.Artifact.owned_from_url_or_new')
    def test_enqueue_disk_fetches(self, mock_owned, mock_afo):
        blob_uuid = str(uuid.uuid4())
        instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance(
            'cirros', instance_uuid,
            disk_spec=[
                {'base': 'http://example.com/image', 'size': 8},
                {'blob_uuid': blob_uuid, 'size': 8},
                {'size': 8},
            ])
        i = instance.Instance.from_db(instance_uuid)

        fake_artifact = mock.MagicMock()
        fake_artifact.uuid = str(uuid.uuid4())
        mock_owned.return_value = fake_artifact
        op_uuids = [str(uuid.uuid4()), str(uuid.uuid4())]
        mock_afo.side_effect = [
            (ObjectType.ARTIFACT_FETCH_OP, op_uuids[0]),
            (ObjectType.ARTIFACT_FETCH_OP, op_uuids[1]),
        ]

        target_node = str(uuid.uuid4())
        deps = i.enqueue_disk_fetches(
            target_node, PRIORITY.user_waiting, request_id='req-1',
            artifact_event='fetch requested by instance start redirect')

        # The empty disk enqueues nothing; the base URL and blob disks
        # each get a fetch targeting the placed node.
        self.assertEqual([
            mock.call(
                'unittest', 'http://example.com/image', i.uuid,
                [instance.afo_tasks.image_fetch], PRIORITY.user_waiting,
                artifact_uuid=fake_artifact.uuid, request_id='req-1',
                target_node=target_node),
            mock.call(
                'unittest', f'{artifact.BLOB_URL}{blob_uuid}', i.uuid,
                [instance.afo_tasks.image_fetch], PRIORITY.user_waiting,
                artifact_uuid=fake_artifact.uuid, request_id='req-1',
                target_node=target_node),
        ], mock_afo.call_args_list)
        self.assertEqual(op_uuids, [str(d.op_uuid) for d in deps])
        self.assertEqual(
            [ObjectType.ARTIFACT_FETCH_OP, ObjectType.ARTIFACT_FETCH_OP],
            [d.op_type for d in deps])
        self.assertEqual(2, fake_artifact.add_event.call_count)

    @mock.patch('shakenfist.instance.afo_create_and_enqueue')
    @mock.patch('shakenfist.artifact.Artifact.owned_from_url_or_new')
    def test_enqueue_disk_fetches_no_images(self, mock_owned, mock_afo):
        instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance(
            'cirros', instance_uuid, disk_spec=[{'size': 8}])
        i = instance.Instance.from_db(instance_uuid)

        deps = i.enqueue_disk_fetches(str(uuid.uuid4()), PRIORITY.user_waiting)

        self.assertEqual([], deps)
        mock_afo.assert_not_called()
        mock_owned.assert_not_called()


class InstancePlacementAdmissionTestCase(base.ShakenFistTestCase):
    """Placement goes through the atomic admission and release RPCs.

    Placement used to be a non-atomic triple: write the placement
    attribute, remove the old node's reference, insert the new one. It
    is now a single database transaction which also draws down the
    capacity counters, so a placement can never be recorded without the
    capacity it consumes (scheduler-reservations phase 3).
    """

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
        )

        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.gmov = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version', return_value=6)
        self.mock_gmov = self.gmov.start()
        self.addCleanup(self.gmov.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance(
            'cirros', self.instance_uuid, cpus=2, memory=2048,
            disk_spec=[{'base': 'cirros', 'size': 8}],
            set_state=instance.Instance.STATE_CREATED)
        self.inst = instance.Instance.from_db(self.instance_uuid)

        self.node2 = self.mock_mariadb.node_uuids['node2']
        self.node3 = self.mock_mariadb.node_uuids['node3']

    def _placed_on(self):
        """Which nodes hold an INSTANCE_LOCATION row for this instance."""
        return sorted(
            r.source_uuid
            for r in self.mock_mariadb.object_references.values()
            if r.relationship == RelationshipType.INSTANCE_LOCATION
            and r.target_uuid == self.instance_uuid)

    def test_placement_writes_attribute_and_reference(self):
        self.inst.place_instance(self.node2)

        self.assertEqual(self.node2, self.inst.placement['node'])
        self.assertEqual(1, self.inst.placement['placement_attempts'])
        self.assertEqual([self.node2], self._placed_on())

    def test_placement_claims_capacity(self):
        row = self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.inst.place_instance(self.node2)

        self.assertEqual(2, row['used_cpus'])
        self.assertEqual(2048, row['used_memory_mb'])
        self.assertEqual(8, row['used_disk_gb'])

    def test_unchanged_placement_is_not_rewritten(self):
        self.inst.place_instance(self.node2)
        with mock.patch('shakenfist.mariadb.admit_instance_placement') as a:
            self.inst.place_instance(self.node2)
        self.assertFalse(a.called)
        self.assertEqual(1, self.inst.placement['placement_attempts'])

    def test_move_leaves_exactly_one_placement_row(self):
        old = self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)
        new = self.mock_mariadb.set_node_capacity(
            self.node3, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)

        self.inst.place_instance(self.node2)
        self.inst.place_instance(self.node3)

        self.assertEqual([self.node3], self._placed_on())
        self.assertEqual(0, old['used_cpus'])
        self.assertEqual(2, new['used_cpus'])
        self.assertEqual(2, self.inst.placement['placement_attempts'])

    def test_denial_raises_the_typed_exception(self):
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)

        exc = self.assertRaises(
            exceptions.CapacityAdmissionDenied,
            self.inst.place_instance, self.node2)
        self.assertEqual('node', exc.failing_stage)
        self.assertEqual(
            ['cpus'], [d['dimension'] for d in exc.dimensions
                       if d['exceeded']])

        # Nothing was recorded: a denial is not a placement.
        self.assertEqual({}, self.inst.placement)
        self.assertEqual([], self._placed_on())

    def test_a_denied_node_does_not_stop_a_later_candidate(self):
        # The shape step 5's pick-then-claim walk depends on.
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.mock_mariadb.set_node_capacity(
            self.node3, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)

        self.assertRaises(
            exceptions.CapacityAdmissionDenied,
            self.inst.place_instance, self.node2)
        self.inst.place_instance(self.node3)
        self.assertEqual([self.node3], self._placed_on())

    def test_enforce_false_records_over_limit_placements(self):
        # P5: the cleaner and the startup reconciliation record where a
        # libvirt domain already is, and a guard cannot refuse reality.
        row = self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)

        self.inst.place_instance(self.node2, enforce=False)

        self.assertEqual(self.node2, self.inst.placement['node'])
        self.assertEqual([self.node2], self._placed_on())
        self.assertEqual(2, row['used_cpus'])

    def test_enforce_false_events_the_over_limit_write(self):
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)

        with mock.patch.object(self.inst, 'add_event') as add_event:
            self.inst.place_instance(self.node2, enforce=False)

        messages = [c.args[1] for c in add_event.call_args_list]
        self.assertIn(
            'placement recorded despite exceeding capacity guard', messages)

    def test_a_demand_only_refusal_is_not_evented_as_over_limit(self):
        # P9: demand is a spreader, never a capacity bound, so a
        # ground-truth write refused on demand alone is not an
        # over-limit condition. The probe waives the demand clause, so
        # the write admits first time (one RPC, no loud event).
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100, demand_limit=1.0)

        with mock.patch.object(self.inst, 'add_event') as add_event:
            self.inst.place_instance(self.node2, enforce=False)

        messages = [c.args[1] for c in add_event.call_args_list]
        self.assertNotIn(
            'placement recorded despite exceeding capacity guard', messages)
        self.assertIn('instance placed', messages)
        self.assertEqual(self.node2, self.inst.placement['node'])
        self.assertEqual([self.node2], self._placed_on())

    def test_a_within_limits_write_is_not_evented_as_over_limit(self):
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)

        with mock.patch.object(self.inst, 'add_event') as add_event:
            self.inst.place_instance(self.node2, enforce=False)

        messages = [c.args[1] for c in add_event.call_args_list]
        self.assertNotIn(
            'placement recorded despite exceeding capacity guard', messages)
        self.assertIn('instance placed', messages)

    #
    # D16 advisory claim accounting. CLAIM_ENFORCEMENT_HARD is False for
    # this release, so a placement which draws its namespace past the
    # claim it declared is admitted and evented rather than refused.
    #
    CLAIM_EVENT = 'placement admitted over namespace capacity claim'
    NODE_EVENT = 'placement recorded despite exceeding capacity guard'

    def _claim_events(self, add_event):
        """The advisory claim events emitted, as their extra dicts."""
        return [c.kwargs['extra'] for c in add_event.call_args_list
                if c.args[1] == self.CLAIM_EVENT]

    def test_an_over_claim_create_is_admitted_and_evented(self):
        claim = self.mock_mariadb.set_namespace_claim(
            'unittest', limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)

        with mock.patch.object(self.inst, 'add_event') as add_event:
            self.inst.place_instance(self.node2)

        # Advisory means the create happens: the claim is drawn past its
        # limit rather than defended.
        self.assertEqual(self.node2, self.inst.placement['node'])
        self.assertEqual([self.node2], self._placed_on())
        self.assertEqual(2, claim['used_cpus'])

        extras = self._claim_events(add_event)
        self.assertEqual(1, len(extras))
        self.assertEqual(self.node2, extras[0]['node'])
        self.assertEqual('unittest', extras[0]['namespace'])
        # Only the dimension actually over is reported, and its usage is
        # what the claim held before this admission.
        self.assertEqual(
            [{'dimension': 'cpus', 'limit': 1.0, 'used': 0.0,
              'requested': 2.0, 'exceeded': True}],
            extras[0]['claim_dimensions'])

    def _capture_admissions(self):
        """Patch the admission RPC so its replies can be read back.

        The reply is not otherwise observable from outside
        place_instance(), and claim_uuid's whole contract is about what
        the reply carries when nothing was charged.
        """
        replies = []

        def _admit(*args, **kwargs):
            reply = self.mock_mariadb._mariadb_admit_instance_placement(
                *args, **kwargs)
            replies.append(reply)
            return reply

        return replies, mock.patch(
            'shakenfist.mariadb.admit_instance_placement', side_effect=_admit)

    def test_an_over_claim_create_also_events_the_namespace(self):
        # G2: the instance's own trail says its placement went over; the
        # namespace's trail durably records the same facts, because
        # "what happened to my namespace's capacity" outlives any one
        # instance's placement. One placement, two events -- one on the
        # instance (already covered above) and one on the namespace --
        # carrying the same claim_dimensions, and correlatable both by
        # the instance uuid and by the uuid of the claim actually drawn
        # down. The claim uuid is the one that survives an operator
        # growing a claim by delete-and-create, which is the case G2
        # exists for.
        claim = self.mock_mariadb.set_namespace_claim(
            'unittest', limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)

        with mock.patch.object(self.inst, 'add_event') as add_event:
            with mock.patch(
                    'shakenfist.instance.eventlog.add_event') as ns_event:
                self.inst.place_instance(self.node2)

        instance_extras = self._claim_events(add_event)
        self.assertEqual(1, len(instance_extras))

        self.assertEqual(1, ns_event.call_count)
        args, kwargs = ns_event.call_args
        self.assertEqual(
            (EVENT_TYPE_AUDIT, 'namespace', 'unittest',
             self.CLAIM_EVENT),
            args)
        self.assertTrue(kwargs['suppress_event_logging'])

        ns_extra = kwargs['extra']
        self.assertEqual(str(self.inst.uuid), ns_extra['instance'])
        self.assertEqual(self.node2, ns_extra['node'])
        self.assertEqual('unittest', ns_extra['namespace'])
        # Same dimensions on both events -- this is one fact, recorded
        # twice.
        self.assertEqual(
            instance_extras[0]['claim_dimensions'],
            ns_extra['claim_dimensions'])

        # And it names the claim that was actually drawn down, on both
        # copies, so the namespace's trail says *which* claim was
        # exceeded rather than only that some claim was.
        self.assertEqual(claim['uuid'], ns_extra['claim'])
        self.assertEqual(claim['uuid'], instance_extras[0]['claim'])
        # The claim really is the one that moved, not a uuid that merely
        # happens to be seeded.
        self.assertEqual(2, claim['used_cpus'])

    def test_an_unclaimed_placement_reports_no_claim_uuid(self):
        # A namespace with no claim charges the cluster singleton, not a
        # claim, so there is no claim uuid to report and the reply must
        # say so with an empty name rather than inventing one. Readers
        # test for the name, never for a zero.
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)

        replies, patcher = self._capture_admissions()
        with patcher:
            with mock.patch.object(self.inst, 'add_event') as add_event:
                self.inst.place_instance(self.node2)

        self.assertEqual(1, len(replies))
        self.assertEqual('', replies[0]['claim_uuid'])
        # Nothing was over a claim there is not, either.
        self.assertFalse(replies[0]['claim_over_limit'])
        self.assertEqual([], self._claim_events(add_event))

    def test_a_claimed_placement_names_the_claim_it_charged(self):
        # The complement: a claim which is *not* exceeded still reports
        # which claim the placement was charged against, because the
        # field names the drawdown rather than the exceedance.
        claim = self.mock_mariadb.set_namespace_claim(
            'unittest', limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)

        replies, patcher = self._capture_admissions()
        with patcher:
            self.inst.place_instance(self.node2)

        self.assertEqual(1, len(replies))
        self.assertEqual(claim['uuid'], replies[0]['claim_uuid'])
        self.assertFalse(replies[0]['claim_over_limit'])

    def test_a_create_within_the_claim_is_not_evented(self):
        self.mock_mariadb.set_namespace_claim(
            'unittest', limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)

        with mock.patch.object(self.inst, 'add_event') as add_event:
            self.inst.place_instance(self.node2)

        messages = [c.args[1] for c in add_event.call_args_list]
        self.assertNotIn(self.CLAIM_EVENT, messages)
        self.assertIn('instance placed', messages)

    def test_a_node_denial_does_not_event_the_claim(self):
        # The two events must not cross-fire: a refused placement charged
        # no claim, so there is no exceedance to report.
        claim = self.mock_mariadb.set_namespace_claim(
            'unittest', limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)

        with mock.patch.object(self.inst, 'add_event') as add_event:
            self.assertRaises(
                exceptions.CapacityAdmissionDenied,
                self.inst.place_instance, self.node2)

        self.assertEqual([], self._claim_events(add_event))
        self.assertEqual(0, claim['used_cpus'])

    def test_both_over_limit_events_can_fire_for_one_placement(self):
        # A ground-truth write into an over-claim namespace, onto a node
        # which is itself over its guard: the two conditions are
        # independent and neither event suppresses the other.
        self.mock_mariadb.set_namespace_claim(
            'unittest', limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)

        with mock.patch.object(self.inst, 'add_event') as add_event:
            self.inst.place_instance(self.node2, enforce=False)

        messages = [c.args[1] for c in add_event.call_args_list]
        self.assertIn(self.NODE_EVENT, messages)
        self.assertIn(self.CLAIM_EVENT, messages)

    def test_the_claim_event_fires_once_from_the_recording_reply(self):
        # The enforce=False probe-then-force path makes two RPCs. The
        # first is a probe whose denial rolled back, charging no claim;
        # the second is the write which actually recorded the placement
        # and charged it. The event must come from the second, exactly
        # once -- an event per RPC would double count an exceedance, and
        # an event from the probe would report a drawdown that was undone.
        claim = self.mock_mariadb.set_namespace_claim(
            'unittest', limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)

        with mock.patch(
                'shakenfist.mariadb.admit_instance_placement',
                side_effect=(
                    self.mock_mariadb._mariadb_admit_instance_placement)) as a:
            with mock.patch.object(self.inst, 'add_event') as add_event:
                self.inst.place_instance(self.node2, enforce=False)

        self.assertEqual(
            2, a.call_count,
            'this test is only meaningful on the probe-then-force path')
        extras = self._claim_events(add_event)
        self.assertEqual(
            1, len(extras),
            'the advisory claim event must fire exactly once, from the '
            f'reply which recorded the placement, got {extras}')
        # The claim was charged exactly once too, so the reported
        # exceedance is the one the surviving write produced.
        self.assertEqual(2, claim['used_cpus'])
        self.assertEqual(
            [{'dimension': 'cpus', 'limit': 1.0, 'used': 0.0,
              'requested': 2.0, 'exceeded': True}],
            extras[0]['claim_dimensions'])

    def test_the_mock_can_produce_a_claim_stage_denial(self):
        # Not a test of enforcement, which does not exist: it is a test
        # that the fixture can produce the reply shape phase 5's callers
        # will have to handle, which is the gap the phase 3 plan recorded
        # against mock_mariadb. Patching the constant here is what keeps
        # the mock and mariadb.py flipping together.
        self.mock_mariadb.set_namespace_claim(
            'unittest', limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)

        with mock.patch('shakenfist.mariadb.CLAIM_ENFORCEMENT_HARD', True):
            exc = self.assertRaises(
                exceptions.CapacityAdmissionDenied,
                self.inst.place_instance, self.node2)

        self.assertEqual('claim', exc.failing_stage)
        self.assertEqual(
            ['cpus'], [d['dimension'] for d in exc.dimensions
                       if d['exceeded']])
        self.assertEqual([], self._placed_on())

    def test_an_unguarded_placement_is_loud(self):
        # P7: no capacity row for this node yet, mid-upgrade.
        with mock.patch.object(self.inst, 'add_event') as add_event:
            self.inst.place_instance(self.node2)

        messages = [c.args[1] for c in add_event.call_args_list]
        self.assertIn('instance placed without capacity guard', messages)

    def test_a_failed_write_raises_rather_than_reading_as_full(self):
        # A database blip must not be indistinguishable from "the
        # cluster has no room", or a caller walking candidates would 507
        # a create which had plenty of capacity.
        failure = {
            'success': False, 'error': 'database unavailable',
            'admitted': False, 'unguarded': False, 'clamped': False,
            'failing_stage': '', 'dimensions': [], 'node_used_cpus': 0,
            'node_used_memory_mb': 0, 'node_used_disk_gb': 0,
            'node_expected_demand': 0.0}
        with mock.patch('shakenfist.mariadb.admit_instance_placement',
                        return_value=failure):
            self.assertRaises(
                exceptions.WriteException,
                self.inst.place_instance, self.node2)

    def test_a_failed_write_does_not_raise_when_not_enforcing(self):
        # The cleaner runs this for every domain on the node; a database
        # blip must not abort its pass. The next pass retries, because
        # the placement attribute was not changed.
        failure = {
            'success': False, 'error': 'database unavailable',
            'admitted': False, 'unguarded': False, 'clamped': False,
            'failing_stage': '', 'dimensions': [], 'node_used_cpus': 0,
            'node_used_memory_mb': 0, 'node_used_disk_gb': 0,
            'node_expected_demand': 0.0}
        with mock.patch('shakenfist.mariadb.admit_instance_placement',
                        return_value=failure):
            self.inst.place_instance(self.node2, enforce=False)
        self.assertEqual({}, self.inst.placement)

    def test_a_denied_unguarded_retry_does_not_raise_when_not_enforcing(self):
        # The unguarded retry's key-only UPDATE can match nothing when
        # the capacity row vanished between the probe and the write (the
        # reconciler dropped a node which stopped being a schedulable
        # hypervisor mid-pass). That is a benign abort, not a capacity
        # denial: a ground-truth writer has no candidate to walk to, and
        # raising would abort the rest of the cleaner's pass.
        denied = {
            'success': True, 'error': '',
            'admitted': False, 'unguarded': False, 'clamped': False,
            'failing_stage': 'node', 'dimensions': [], 'node_used_cpus': 0,
            'node_used_memory_mb': 0, 'node_used_disk_gb': 0,
            'node_expected_demand': 0.0}
        with mock.patch('shakenfist.mariadb.admit_instance_placement',
                        return_value=denied):
            self.inst.place_instance(self.node2, enforce=False)
        self.assertEqual({}, self.inst.placement)

    def _counting_fetch(self):
        """Count fetches of the instance_attributes row."""
        return mock.patch(
            'shakenfist.mariadb.get_instance_attributes',
            side_effect=self.mock_mariadb._mariadb_get_instance_attributes)

    def test_placement_is_visible_inside_an_enclosing_memo(self):
        # The RPC writes the placement column behind the object's back,
        # so the memo of the attributes row has to be dropped just as
        # _db_set_attribute() would have dropped it. Reading it back
        # through a second Instance object rather than the placing one
        # is what makes this test bite: the placing object's own dict
        # would look right whether or not the memo was invalidated.
        other = instance.Instance.from_db(self.instance_uuid)

        with self._counting_fetch() as fetch:
            with self.inst.attribute_memo():
                self.assertEqual({}, self.inst.placement)
                self.inst.place_instance(self.node2)
                self.assertEqual(self.node2, self.inst.placement['node'])
                self.assertEqual(self.node2, other.placement['node'])
                # Three fetches: the pre-placement read, a second for the
                # memoised object because the write dropped its memo, and
                # one for the unmemoised second object. Without the
                # invalidation the middle one would be served from the
                # memo and this would be two.
                self.assertEqual(3, fetch.call_count)

    def test_a_denial_leaves_no_trace_in_an_enclosing_memo(self):
        # A denial writes nothing, so nothing about the instance may
        # change -- including the placement dict a memoised read handed
        # out, which must not end up holding the refused node or a
        # bumped placement_attempts.
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=1, limit_memory_mb=16384,
            limit_disk_gb=100)

        with self.inst.attribute_memo():
            memoised = self.inst.placement
            self.assertRaises(
                exceptions.CapacityAdmissionDenied,
                self.inst.place_instance, self.node2)
            self.assertEqual({}, memoised)
            self.assertEqual({}, self.inst.placement)

    def test_delete_globally_releases_capacity(self):
        row = self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.inst.place_instance(self.node2)

        self.inst._delete_globally()

        self.assertEqual(0, row['used_cpus'])
        self.assertEqual(0, row['used_memory_mb'])
        self.assertEqual(0, row['used_disk_gb'])
        self.assertEqual([], self._placed_on())

        # P8: where the instance was is still readable after delete.
        self.assertEqual(self.node2, self.inst.placement['node'])

    #
    # G4: the placement ledger is only auditable from events if both
    # halves are legible. 'instance placed' carries the node, the
    # request and the post-drawdown counters; 'instance placement
    # released' used to carry the node and nothing else.
    #
    COUNTER_KEYS = {'node_used_cpus', 'node_used_memory_mb',
                    'node_used_disk_gb', 'node_expected_demand'}

    def _event_extra(self, add_event, message):
        """The extra dict of the one event carrying this message."""
        extras = [c.kwargs['extra'] for c in add_event.call_args_list
                  if c.args[1] == message]
        self.assertEqual(
            1, len(extras), f'expected exactly one {message!r} event, '
            f'got {len(extras)}')
        return extras[0]

    def test_the_release_event_carries_the_placement_vocabulary(self):
        row = self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)
        # Another instance's usage, so the counters this release reports
        # are not zero and a dropped field cannot pass as a real value.
        row['used_cpus'] = 8
        row['used_memory_mb'] = 8192
        row['used_disk_gb'] = 32

        with mock.patch.object(self.inst, 'add_event') as add_event:
            self.inst.place_instance(self.node2)
            placed = self._event_extra(add_event, 'instance placed')
            self.inst._delete_globally()
            released = self._event_extra(
                add_event, 'instance placement released')

        # Strictly more than the node it used to carry on its own.
        self.assertTrue(
            set(released) > {'node'},
            f'the release event still carries only the node: {released}')

        # And the counters are named the way the drawdown half names
        # them, so a consumer reading both halves of the ledger needs
        # one vocabulary rather than two.
        self.assertTrue(
            self.COUNTER_KEYS <= set(placed),
            f'the placement event lost a counter: {sorted(placed)}')
        self.assertTrue(
            self.COUNTER_KEYS <= set(released),
            "the release event does not use the placement event's counter "
            f'names: {sorted(released)}')

        # What was given back: the same triple handed to the RPC.
        self.assertEqual(
            {'node': self.node2, 'cpus': 2, 'memory_mb': 2048, 'disk_gb': 8},
            {k: released[k]
             for k in ('node', 'cpus', 'memory_mb', 'disk_gb')})

        # And where the node's counters stand afterwards -- the other
        # instance's usage, with this one's contribution credited back.
        self.assertEqual(8, released['node_used_cpus'])
        self.assertEqual(8192, released['node_used_memory_mb'])
        self.assertEqual(32, released['node_used_disk_gb'])

    def test_the_placement_event_names_every_counter_the_release_does(self):
        # G4's done-criterion, as a set relation rather than as a list
        # nobody remembers to update: every name the release half
        # reports must also be a name the drawdown half reports, so one
        # vocabulary reads the ledger in both directions. disk_gb is the
        # key that made this unsatisfiable -- 'instance placed' reported
        # cpus and memory_mb and left a third of the allocation out
        # entirely -- so the placement event gains it rather than the
        # release event dropping it.
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)

        with mock.patch.object(self.inst, 'add_event') as add_event:
            self.inst.place_instance(self.node2)
            placed = self._event_extra(add_event, 'instance placed')
            self.inst._delete_globally()
            released = self._event_extra(
                add_event, 'instance placement released')

        self.assertIn(
            'disk_gb', placed,
            f'the placement event does not report disk: {sorted(placed)}')
        self.assertTrue(
            set(released) <= set(placed),
            'the release event uses names the placement event does not: '
            f'{sorted(set(released) - set(placed))}')

        # And the two halves agree on what those names mean, because
        # both read _capacity_claim: the request drawn down and the
        # request given back are the same triple.
        self.assertEqual(
            {'cpus': 2, 'memory_mb': 2048, 'disk_gb': 8},
            {k: placed[k] for k in ('cpus', 'memory_mb', 'disk_gb')})
        self.assertEqual(
            {k: released[k] for k in ('cpus', 'memory_mb', 'disk_gb')},
            {k: placed[k] for k in ('cpus', 'memory_mb', 'disk_gb')})

    def test_a_release_with_no_capacity_row_reports_no_counters(self):
        # P7's fail-open case: this node has no capacity row, so there
        # are no counters to report and a zero would read as "the node
        # now holds nothing" rather than "nobody looked".
        with mock.patch.object(self.inst, 'add_event') as add_event:
            self.inst.place_instance(self.node2)
            self.inst._delete_globally()
            released = self._event_extra(
                add_event, 'instance placement released')

        self.assertEqual(
            {'node': self.node2, 'cpus': 2, 'memory_mb': 2048, 'disk_gb': 8},
            released)

    def test_an_unnamed_release_events_the_node_it_released_from(self):
        # hard_delete()'s sweep passes an empty node_uuid on purpose --
        # it knows the instance rather than its node -- and an event
        # naming no node is unreadable, so the reply's node fills in.
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.inst.place_instance(self.node2)

        with mock.patch.object(self.inst, 'add_event') as add_event:
            self.inst._release_placement()
            released = self._event_extra(
                add_event, 'instance placement released')

        self.assertEqual(self.node2, released['node'])

    def test_repeated_delete_of_an_errored_instance_releases_once(self):
        # _delete_globally() names the release node from the placement
        # attribute, which is never cleared (P8), and an instance which
        # ends in state error rather than deleted passes the delete
        # path's re-entrancy guard on every subsequent attempt. Release
        # is reference-gated for exactly this reason: the second pass
        # finds no INSTANCE_LOCATION row and must not decrement again.
        row = self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)
        # Another instance's usage, so the server-side floors cannot
        # mask a second decrement of ours.
        row['used_cpus'] = 8
        row['used_memory_mb'] = 8192
        row['used_disk_gb'] = 32

        self.inst.place_instance(self.node2)
        self.assertEqual(10, row['used_cpus'])

        self.inst.state = f'{self.inst.state.value}-error'
        self.inst._delete_globally()
        self.assertEqual(instance.Instance.STATE_ERROR,
                         self.inst.state.value)
        self.assertEqual(8, row['used_cpus'])
        self.assertEqual(8192, row['used_memory_mb'])
        self.assertEqual(32, row['used_disk_gb'])

        # The placement attribute still names node2, so the second
        # delete asks to release from it again.
        self.assertEqual(self.node2, self.inst.placement['node'])
        self.inst._delete_globally()

        self.assertEqual(8, row['used_cpus'])
        self.assertEqual(8192, row['used_memory_mb'])
        self.assertEqual(32, row['used_disk_gb'])

    def test_delete_returns_the_capacity_to_the_claim(self):
        # Admission charges the namespace's claim, so release has to
        # credit it back or every create-and-delete cycle leaks a claim.
        claim = self.mock_mariadb.set_namespace_claim(
            'unittest', limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)

        self.inst.place_instance(self.node2)
        self.assertEqual(2, claim['used_cpus'])
        self.assertEqual(2048, claim['used_memory_mb'])
        self.assertEqual(8, claim['used_disk_gb'])

        self.inst._delete_globally()

        self.assertEqual(0, claim['used_cpus'])
        self.assertEqual(0, claim['used_memory_mb'])
        self.assertEqual(0, claim['used_disk_gb'])

    def test_release_is_skipped_when_never_placed(self):
        with mock.patch('shakenfist.mariadb.release_instance_placement') as r:
            self.inst._delete_globally()
        self.assertFalse(r.called)

    def test_hard_delete_releases_before_deleting_the_rows(self):
        # The release needs the instance's cpus, memory and disk spec,
        # which only exist while the static and attribute rows do.
        self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.inst.place_instance(self.node2)

        seen = {}
        real = self.mock_mariadb._mariadb_release_instance_placement

        def _watch(*args, **kwargs):
            seen['rows_present'] = (
                self.instance_uuid in self.mock_mariadb.instance_objects
                and self.instance_uuid in self.mock_mariadb.instance_attributes)
            seen['args'] = args
            return real(*args, **kwargs)

        with mock.patch('shakenfist.mariadb.release_instance_placement',
                        side_effect=_watch):
            self.inst.hard_delete()

        self.assertTrue(seen['rows_present'])
        self.assertEqual(
            (self.instance_uuid, 'unittest', 2, 2048, 8), seen['args'])
        self.assertEqual([], self._placed_on())

    def test_hard_delete_release_behind_delete_globally_is_a_noop(self):
        row = self.mock_mariadb.set_node_capacity(
            self.node2, limit_cpus=16, limit_memory_mb=16384,
            limit_disk_gb=100)
        self.inst.place_instance(self.node2)
        self.inst._delete_globally()

        released = []
        real = self.mock_mariadb._mariadb_release_instance_placement

        def _watch(*args, **kwargs):
            result = real(*args, **kwargs)
            released.append(result['released'])
            return result

        with mock.patch('shakenfist.mariadb.release_instance_placement',
                        side_effect=_watch):
            self.inst.hard_delete()

        self.assertEqual([False], released)
        self.assertEqual(0, row['used_cpus'])


class InstancePortAllocationTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
        )

        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.gmov = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version', return_value=6)
        self.mock_gmov = self.gmov.start()
        self.addCleanup(self.gmov.stop)

        # Object reference grouping asks who is making the request, which
        # outside of a Flask request context has no answer.
        self.request_namespace = mock.patch(
            'shakenfist.schema.object_reference.request_namespace',
            return_value='system')
        self.mock_request_namespace = self.request_namespace.start()
        self.addCleanup(self.request_namespace.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

        self.instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance(
            'ports-test', self.instance_uuid,
            video={'memory': 16384, 'model': 'cirrus', 'vdi': 'spice'},
            set_state=instance.Instance.STATE_CREATED)
        self.inst = instance.Instance.from_db(self.instance_uuid)

        self.consumed = mock.patch(
            'shakenfist.mariadb.get_consumed_ports_for_node', return_value=[])
        self.mock_consumed = self.consumed.start()
        self.addCleanup(self.consumed.stop)

    def _fake_sockets(self):
        """Replace probe sockets with mocks and record them in order."""
        created = []

        def _make(*args, **kwargs):
            s = mock.MagicMock()
            created.append(s)
            return s

        return mock.patch(
            'shakenfist.instance.socket.socket', side_effect=_make), created

    @mock.patch('shakenfist.instance.random.randint',
                side_effect=[40000, 40000, 40001, 40000, 40001, 40002])
    def test_sibling_draws_are_mutually_exclusive(self, mock_randint):
        # Issue 3897: the three draws for one instance must exclude each
        # other, not just the ports already persisted for the node. The
        # forced randint sequence repeats each port before offering a
        # fresh one, so an allocator without sibling exclusion would
        # return a duplicate.
        patcher, _ = self._fake_sockets()
        with patcher:
            self.inst.allocate_instance_ports()

        self.assertEqual(
            {'console_port': 40000, 'vdi_port': 40001, 'vdi_tls_port': 40002},
            self.inst.ports)

    def test_node_consumed_ports_are_excluded(self):
        self.mock_consumed.return_value = [40000, 40001]
        patcher, _ = self._fake_sockets()
        with patcher, mock.patch(
                'shakenfist.instance.random.randint',
                side_effect=[40000, 40001, 40002, 40003, 40004]):
            self.inst.allocate_instance_ports()

        self.assertEqual(
            {'console_port': 40002, 'vdi_port': 40003, 'vdi_tls_port': 40004},
            self.inst.ports)

    def test_bind_collision_selects_another_port(self):
        sockets = []

        def _make(*args, **kwargs):
            s = mock.MagicMock()
            if not sockets:
                s.bind.side_effect = OSError('port in use')
            sockets.append(s)
            return s

        with mock.patch('shakenfist.instance.socket.socket',
                        side_effect=_make), \
                mock.patch('shakenfist.instance.random.randint',
                           side_effect=[40000, 40001, 40002, 40003]):
            self.inst.allocate_instance_ports()

        self.assertEqual(
            {'console_port': 40001, 'vdi_port': 40002, 'vdi_tls_port': 40003},
            self.inst.ports)
        for s in sockets:
            s.close.assert_called_once_with()

    def test_probe_sockets_held_until_ports_persisted(self):
        # The probe sockets must stay bound until the allocation is
        # persisted, so concurrent allocations on the node cannot
        # interleave into the same gap.
        patcher, sockets = self._fake_sockets()
        closed_before_write = []
        real_set = self.inst._db_set_attribute

        def _watch(attribute, value, **kwargs):
            if attribute == 'ports':
                closed_before_write.extend(
                    s for s in sockets if s.close.called)
            return real_set(attribute, value, **kwargs)

        with patcher, mock.patch.object(
                self.inst, '_db_set_attribute', side_effect=_watch):
            self.inst.allocate_instance_ports()

        self.assertEqual(3, len(sockets))
        self.assertEqual([], closed_before_write)
        for s in sockets:
            s.close.assert_called_once_with()

    def test_existing_ports_are_preserved(self):
        self.inst.ports = {
            'console_port': 31000, 'vdi_port': 31001, 'vdi_tls_port': 31002}
        with mock.patch('shakenfist.instance.socket.socket') as mock_socket:
            self.inst.allocate_instance_ports()

        mock_socket.assert_not_called()
        self.assertEqual(
            {'console_port': 31000, 'vdi_port': 31001, 'vdi_tls_port': 31002},
            self.inst.ports)

    def test_no_tls_port_for_non_spice_vdi(self):
        other_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance(
            'vnc-test', other_uuid,
            video={'memory': 16384, 'model': 'cirrus', 'vdi': 'vnc'},
            set_state=instance.Instance.STATE_CREATED)
        other = instance.Instance.from_db(other_uuid)

        patcher, sockets = self._fake_sockets()
        with patcher:
            other.allocate_instance_ports()

        self.assertEqual(
            {'console_port', 'vdi_port'}, set(other.ports.keys()))
        self.assertEqual(2, len(sockets))
