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
from shakenfist import baseobject
from shakenfist import exceptions
from shakenfist import instance
from shakenfist.config import SFConfig
from shakenfist.operations.agentoperation import AgentOperation
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
    returned but left on the queue (the entry is only retired once the
    operation has provably left the queued state), and finished or invalid
    heads must not wedge the queue.
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

    def _make_agentop(self, state=None):
        op = AgentOperation.new(
            str(uuid.uuid4()), 'unittest', self.instance_uuid,
            [{'command': 'execute', 'commandline': 'true'}])
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

        # Once the head has left the queued state it is lazily popped and
        # the next operation dispatched.
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
