import base64
from functools import partial
import json
from unittest import mock
import os
import pycdlib
import tempfile
import testtools
import time
import uuid

from shakenfist import baseobject
from shakenfist.baseobject import State
from shakenfist import exceptions
from shakenfist import instance
from shakenfist.config import SFConfig
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class VirtMetaTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
            ETCD_HOST='127.0.0.1'
        )

        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.cache.update_object_state_cache')
    @mock.patch('shakenfist.etcd.get',
                return_value={
                    'uuid': 'uuid42',
                    'cpus': 1,
                    'disk_spec': [{
                        'base': 'cirros',
                        'size': 8
                    }],
                    'memory': 1024,
                    'name': 'cirros',
                    'namespace': 'namespace',
                    'requested_placement': None,
                    'ssh_key': 'thisisasshkey',
                    'user_data': str(base64.b64encode(
                        b'thisisuserdata'), 'utf-8'),
                    'video': {'model': 'cirrus', 'memory': 16384, 'vdi': 'spice'},
                    'uefi': False,
                    'configdrive': 'openstack-disk',
                    'version': 6,
                    'nvram_template': None,
                    'secure_boot': False
                })
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('shakenfist.etcd.create')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get_attribute',
                return_value={'value': None, 'update_time': 0})
    @mock.patch('shakenfist.etcd.get_lock')
    @mock.patch('time.time', return_value=1234)
    def test_instance_new(self, mock_time, mock_get_lock, mock_get_attribute,
                          mock_create, mock_put, mock_get, mock_cache_update):
        instance.Instance.new(
            'barry', 1, 2048, 'namespace', 'sshkey',
            [{}], 'userdata', {'memory': 16384, 'model': 'cirrus', 'vdi': 'spice'},
            instance_uuid='uuid42',)

        self.assertEqual(
            ('attribute/instance', 'uuid42', 'state',
             State(instance.Instance.STATE_INITIAL, 1234)),
            mock_put.mock_calls[0][1])
        self.assertEqual(
            ('attribute/instance', 'uuid42',
             'power_state', {'power_state': instance.Instance.STATE_INITIAL}),
            mock_put.mock_calls[1][1])

        self.assertEqual(
            ('instance', None, 'uuid42',
             {
                 'cpus': 1,
                 'disk_spec': [{}],
                 'machine_type': 'pc',
                 'memory': 2048,
                 'name': 'barry',
                 'namespace': 'namespace',
                 'requested_placement': None,
                 'ssh_key': 'sshkey',
                 'user_data': 'userdata',
                 'uuid': 'uuid42',
                 'version': 13,
                 'video': {'memory': 16384, 'model': 'cirrus', 'vdi': 'spice'},
                 'uefi': False,
                 'configdrive': 'openstack-disk',
                 'nvram_template': None,
                 'secure_boot': False,
                 'side_channels': None
             }),
            mock_create.mock_calls[0][1])


class InstanceTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
            ETCD_HOST='127.0.0.1'
        )

        self.config = mock.patch('shakenfist.instance.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.gmov = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version', return_value=6)
        self.mock_gmov = self.gmov.start()
        self.addCleanup(self.gmov.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

    def test_set_state_valid1(self):
        instance_uuid = str(uuid.uuid4())
        self.mock_etcd.create_instance('cirros', instance_uuid,
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
        self.mock_etcd.create_instance('cirros', instance_uuid,
                                       set_state=instance.Instance.STATE_PREFLIGHT)
        i = instance.Instance.from_db(instance_uuid)

        i.state = 'preflight-error'
        with testtools.ExpectedException(exceptions.InvalidStateException):
            i.state = instance.Instance.STATE_CREATED

    def test_update_power_state(self):
        instance_uuid = str(uuid.uuid4())
        self.mock_etcd.create_instance('cirros', instance_uuid)
        i = instance.Instance.from_db(instance_uuid)
        i.update_power_state('off')

        etcd_value = i._db_get_attribute('power_state')
        self.assertTrue(time.time() - etcd_value['power_state_updated'] < 3)
        self.assertEqual('off', etcd_value['power_state'])
        self.assertEqual('initial', etcd_value['power_state_previous'])

    def test_update_power_state_duplicate(self):
        instance_uuid = str(uuid.uuid4())
        self.mock_etcd.create_instance('cirros', instance_uuid)
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
        self.mock_etcd.create_instance('cirros', instance_uuid)
        i = instance.Instance.from_db(instance_uuid)
        s = str(i)
        self.assertEqual('instance(%s)' % instance_uuid, s)

    # create, delete
    def test_make_config_drive(self):
        instance_uuid = str(uuid.uuid4())
        network_uuid = str(uuid.uuid4())
        iface_uuid_one = str(uuid.uuid4())
        iface_uuid_two = str(uuid.uuid4())

        self.mock_etcd.create_network('testing', network_uuid, netblock='127.0.0.0/8')
        self.mock_etcd.create_network_interface(
            iface_uuid_one,
            {
                'network_uuid': network_uuid,
                'address': '127.0.0.5',
                'model': None,
                'macaddress': '1a:91:64:d2:15:39',
            },
            instance_uuid=instance_uuid, order=0)
        self.mock_etcd.create_network_interface(
            iface_uuid_two,
            {
                'network_uuid': network_uuid,
                'address': '127.0.0.6',
                'model': None,
                'macaddress': '1a:91:64:d2:15:40',
            },
            instance_uuid=instance_uuid, order=1)
        self.mock_etcd.create_instance(
            'cirros', instance_uuid, 1, ssh_key='thisisasshkey',
            user_data=str(base64.b64encode(b'thisisuserdata'), 'utf-8'))

        i = instance.Instance.from_db(instance_uuid)
        i.interfaces = [iface_uuid_one, iface_uuid_two]

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


class InstancesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.mock_etcd.create_instance(
            name='cirros', uuid='373a165e-9720-4e14-bd0e-9612de79ff15',
            namespace='gerkin', set_state=instance.Instance.STATE_DELETED,
            place_on_node='node1')
        self.mock_etcd.create_instance(
            name='cirros', uuid='b078cb4e-857c-4f04-b011-751742ef5817',
            namespace='namespace', set_state=instance.Instance.STATE_CREATED,
            place_on_node='node1')
        self.mock_etcd.create_instance(
            name='cirros', uuid='a7c5ecec-c3a9-4774-ad1b-249d9e90e806',
            namespace='namespace', set_state=instance.Instance.STATE_DELETED,
            place_on_node='node1')

    def test_base_iteration(self):
        uuids = []
        for i in instance.all_instances():
            uuids.append(i.uuid)

        self.assertEqual(3, len(uuids))
        self.assertTrue('373a165e-9720-4e14-bd0e-9612de79ff15' in uuids)
        self.assertTrue('b078cb4e-857c-4f04-b011-751742ef5817' in uuids)
        self.assertTrue('a7c5ecec-c3a9-4774-ad1b-249d9e90e806' in uuids)

    def test_placement_filter_all(self):
        uuids = []
        for i in instance.Instances([partial(instance.placement_filter, 'node1')]):
            uuids.append(i.uuid)

        self.assertEqual(3, len(uuids))
        self.assertTrue('373a165e-9720-4e14-bd0e-9612de79ff15' in uuids)
        self.assertTrue('b078cb4e-857c-4f04-b011-751742ef5817' in uuids)
        self.assertTrue('a7c5ecec-c3a9-4774-ad1b-249d9e90e806' in uuids)

    def test_placement_filter_none(self):
        uuids = []
        for i in instance.Instances([partial(instance.placement_filter, 'node2')]):
            uuids.append(i.uuid)

        self.assertEqual([], uuids)

    def test_namespace_filter(self):
        uuids = []
        for i in instance.Instances([partial(baseobject.namespace_filter, 'gerkin')]):
            uuids.append(i.uuid)

        self.assertEqual(['373a165e-9720-4e14-bd0e-9612de79ff15'], uuids)
