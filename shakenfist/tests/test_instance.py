import base64
from functools import partial
import json
import mock
import os
import pycdlib
import tempfile
import testtools
import time

from shakenfist import baseobject
from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    State)
from shakenfist import exceptions
from shakenfist.ipmanager import IPManager
from shakenfist import instance
from shakenfist.config import SFConfig
from shakenfist.tests import base


class FakeNetwork(object):
    object_type = 'network'

    def __init__(self):
        self.ipmanager = IPManager('uuid', '127.0.0.0/8')
        self.router = self.ipmanager.get_address_at_index(1)
        self.netmask = '255.0.0.0'
        self.dhcp_start = '127.0.0.2'
        self.broadcast = '127.255.255.255'
        self.provide_nat = True


class FakeNetworkInterface(object):
    object_type = 'networkinterface'

    def __init__(self, values):
        self.uuid = values['uuid']
        self.instance_uuid = values['instance_uuid']
        self.network_uuid = values['network_uuid']
        self.macaddr = values['macaddr']
        self.ipv4 = values['ipv4']
        self.order = values['order']


class VirtMetaTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(VirtMetaTestCase, self).setUp()
        fake_config = SFConfig(
            STORAGE_PATH="/a/b/c",
            DISK_BUS="virtio",
            ZONE="sfzone",
            NODE_NAME="node01",
        )

        self.config = mock.patch('shakenfist.instance.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

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
                        'thisisuserdata'.encode('utf-8')), 'utf-8'),
                    'video': {'model': 'cirrus', 'memory': 16384},
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
    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('time.time', return_value=1234)
    def test_instance_new(self, mock_time, mock_get_lock, mock_get_attribute,
                          mock_create, mock_put, mock_get):
        instance.Instance.new('barry', 1, 2048, 'namespace', 'sshkey',
                              [{}], 'userdata', {
                                  'memory': 16384, 'model': 'cirrus'},
                              uuid='uuid42',)

        self.assertEqual(
            ('attribute/instance', 'uuid42', 'state',
             State(instance.Instance.STATE_INITIAL, 1234)),
            mock_put.mock_calls[1][1])
        self.assertEqual(
            ('attribute/instance', 'uuid42', 'error', {'message': None}),
            mock_put.mock_calls[3][1])
        self.assertEqual(
            ('attribute/instance', 'uuid42',
             'power_state', {'power_state': instance.Instance.STATE_INITIAL}),
            mock_put.mock_calls[4][1])

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
                 'version': 6,
                 'video': {'memory': 16384, 'model': 'cirrus'},
                 'uefi': False,
                 'configdrive': 'openstack-disk',
                 'nvram_template': None,
                 'secure_boot': False
             }),
            mock_create.mock_calls[0][1])

    @mock.patch('shakenfist.etcd.get',
                return_value={
                    'cpus': 1,
                    'disk_spec': [{}],
                    'memory': 2048,
                    'name': 'barry',
                    'namespace': 'namespace',
                    'requested_placement': None,
                    'ssh_key': 'sshkey',
                    'user_data': 'userdata',
                    'uuid': 'uuid42',
                    'version': 6,
                    'video': {'memory': 16384, 'model': 'cirrus'},
                    'uefi': False,
                    'configdrive': 'openstack-disk',
                    'nvram_template': None,
                    'secure_boot': False
                })
    def test_from_db(self, mock_get):
        inst = instance.Instance.from_db('uuid42')
        self.assertEqual(1, inst.cpus)
        self.assertEqual([{}], inst.disk_spec)
        self.assertEqual(2048, inst.memory)
        self.assertEqual('barry', inst.name)
        self.assertEqual('namespace', inst.namespace)
        self.assertEqual(None, inst.requested_placement)
        self.assertEqual('sshkey', inst.ssh_key)
        self.assertEqual('userdata', inst.user_data)
        self.assertEqual('uuid42', inst.uuid)
        self.assertEqual(6, inst.version)
        self.assertEqual('openstack-disk', inst.configdrive)
        self.assertEqual(None, inst.nvram_template)
        self.assertEqual(False, inst.secure_boot)
        self.assertEqual({'memory': 16384, 'model': 'cirrus'}, inst.video)
        self.assertEqual('/a/b/c/instances/uuid42', inst.instance_path)


class InstanceTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(InstanceTestCase, self).setUp()
        fake_config = SFConfig(
            STORAGE_PATH="/a/b/c",
            DISK_BUS="virtio",
            ZONE="sfzone",
            NODE_NAME="node01",
        )

        self.config = mock.patch('shakenfist.instance.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        # self.libvirt = mock.patch('libvirt')
        # self.mock_libvirt = self.libvirt.start()

        self.put = mock.patch('shakenfist.etcd.put')
        self.mock_put = self.put.start()
        self.addCleanup(self.put.stop)

    @mock.patch('shakenfist.instance.Instance._db_create')
    @mock.patch('shakenfist.instance.Instance._db_get',
                return_value={
                    'uuid': 'fakeuuid',
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
                        'thisisuserdata'.encode('utf-8')), 'utf-8'),
                    'video': {'model': 'cirrus', 'memory': 16384},
                    'uefi': False,
                    'configdrive': 'openstack-disk',
                    'version': 6,
                    'nvram_template': None,
                    'secure_boot': False
                })
    def _make_instance(self, mock_get_instance, mock_create_instance):
        return instance.Instance.new(
            'cirros', 1, 1024,  'namespace',
            uuid='fakeuuid',
            disk_spec=[{
                'base': 'cirros',
                'size': 8
            }],
            ssh_key='thisisasshkey',
            user_data=str(base64.b64encode(
                'thisisuserdata'.encode('utf-8')), 'utf-8'),
        )

    @mock.patch('shakenfist.instance.Instance.error',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.instance.Instance._db_get_attribute',
                side_effect=[
                    {'value': None, 'update_time': 0},
                    {'value': instance.Instance.STATE_INITIAL, 'update_time': 0},
                ])
    @mock.patch('shakenfist.instance.Instance._db_set_attribute')
    @mock.patch('shakenfist.etcd.put')
    def test_set_state(
            self, mock_put, mock_attribute_set, mock_state_get, mock_lock,
            mock_error):
        i = self._make_instance()
        i.state = 'preflight'

        etcd_write = mock_attribute_set.mock_calls[2]
        self.assertTrue(time.time() - etcd_write[1][1].update_time < 3)
        self.assertEqual('preflight', etcd_write[1][1].value)

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.instance.Instance._db_get_attribute',
                side_effect=[
                    {'value': None, 'update_time': 0},
                    {'value': instance.Instance.STATE_INITIAL, 'update_time': 0},
                    {'value': 'preflight', 'update_time': 0},
                    {'value': 'preflight', 'update_time': 0},
                    {'value': 'preflight', 'update_time': 0},
                    {'value': instance.Instance.STATE_CREATING, 'update_time': 0},
                    {'value': instance.Instance.STATE_CREATED, 'update_time': 0},
                    {'value': 'created-error', 'update_time': 0},
                    {'value': instance.Instance.STATE_ERROR, 'update_time': 0},
                ])
    @mock.patch('shakenfist.instance.Instance._db_set_attribute')
    @mock.patch('shakenfist.etcd.put')
    def test_set_state_valid1(
            self, mock_put, mock_attribute_set, mock_state_get, mock_lock):

        i = self._make_instance()
        i.state = 'preflight'
        with testtools.ExpectedException(exceptions.InvalidStateException):
            i.state = instance.Instance.STATE_INITIAL
        with testtools.ExpectedException(exceptions.InvalidStateException):
            i.state = instance.Instance.STATE_CREATED
        i.state = instance.Instance.STATE_CREATING
        i.state = instance.Instance.STATE_CREATED
        i.state = 'created-error'
        i.state = instance.Instance.STATE_ERROR
        i.state = instance.Instance.STATE_DELETED

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.instance.Instance._db_get_attribute',
                side_effect=[
                    {'value': None, 'update_time': 0},
                    {'value': instance.Instance.STATE_INITIAL, 'update_time': 0},
                    {'value': 'preflight', 'update_time': 0},
                    {'value': instance.Instance.STATE_ERROR, 'update_time': 0},
                ])
    @mock.patch('shakenfist.instance.Instance._db_set_attribute')
    @mock.patch('shakenfist.etcd.put')
    def test_set_state_valid2(
            self, mock_put, mock_attribute_set, mock_state_get, mock_lock):

        i = self._make_instance()
        i.state = 'preflight'
        i.state = 'preflight-error'
        with testtools.ExpectedException(exceptions.InvalidStateException):
            i.state = instance.Instance.STATE_CREATED

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.instance.Instance.power_state',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.instance.Instance._db_set_attribute')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.state',
                new_callable=mock.PropertyMock)
    def test_update_power_state(self, mock_update, mock_attribute_set,
                                mock_power_state_get, mock_lock):
        mock_power_state_get.return_value = {
            'power_state_previous': 'off',
            'power_state': 'on',
            'power_state_updated': 0
        }

        i = self._make_instance()
        i.update_power_state('off')

        etcd_write = mock_attribute_set.mock_calls[1][1]
        self.assertTrue(time.time() - etcd_write[1]['power_state_updated'] < 3)
        self.assertEqual('off', etcd_write[1]['power_state'])
        self.assertEqual('on', etcd_write[1]['power_state_previous'])

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.instance.Instance.power_state',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.instance.Instance._db_set_attribute')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.state',
                new_callable=mock.PropertyMock)
    def test_update_power_state_duplicate(self, mock_get, mock_attribute_set,
                                          mock_power_state_get, mock_lock):
        mock_power_state_get.return_value = {
            'power_state_previous': 'off',
            'power_state': 'on',
            'power_state_updated': time.time()
        }

        i = self._make_instance()
        i.update_power_state('on')
        self.assertEqual(
            [mock.call('power_state', {
                       'power_state': instance.Instance.STATE_INITIAL})],
            mock_attribute_set.mock_calls)

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.instance.Instance.power_state',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.instance.Instance._db_set_attribute')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.state',
                new_callable=mock.PropertyMock)
    def test_update_power_state_transition_new(
            self, mock_update,
            mock_attribute_set, mock_power_state_get, mock_lock):
        mock_power_state_get.return_value = {
            'power_state_previous': 'on',
            'power_state': 'transition-to-off',
            'power_state_updated': time.time()
        }

        i = self._make_instance()
        i.update_power_state('on')
        self.assertEqual(
            [mock.call('power_state', {
                       'power_state': instance.Instance.STATE_INITIAL})],
            mock_attribute_set.mock_calls)

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.instance.Instance.power_state',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.instance.Instance._db_set_attribute')
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.state',
                new_callable=mock.PropertyMock)
    def test_update_power_state_transition_old(
            self, mock_update, mock_put, mock_attribute_set,
            mock_power_state_get, mock_lock):
        mock_power_state_get.return_value = {
            'power_state_previous': 'on',
            'power_state': 'transition-to-off',
            'power_state_updated': time.time() - 71
        }

        i = self._make_instance()
        i.update_power_state('on')

        etcd_write = mock_attribute_set.mock_calls[1][1]
        self.assertTrue(
            time.time() - etcd_write[1]['power_state_updated'] < 30)
        self.assertEqual('on', etcd_write[1]['power_state'])
        self.assertEqual('transition-to-off',
                         etcd_write[1]['power_state_previous'])

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.state',
                new_callable=mock.PropertyMock)
    def test_str(self, mock_update):
        i = self._make_instance()
        s = str(i)
        self.assertEqual('instance(fakeuuid)', s)

    # create, delete

    @mock.patch('shakenfist.instance.Instance._db_get_attribute',
                return_value=['ifaceuuid', 'ifaceuuid2'])
    @mock.patch('shakenfist.networkinterface.NetworkInterface.from_db',
                side_effect=[
                    FakeNetworkInterface({
                        'uuid': 'ifaceuuid',
                        'instance_uuid': 'instuuid',
                        'network_uuid': 'netuuid',
                        'macaddr': '1a:91:64:d2:15:39',
                        'ipv4': '127.0.0.5',
                        'order': 0
                    }),
                    FakeNetworkInterface({
                        'uuid': 'ifaceuuid2',
                        'instance_uuid': 'instuuid',
                        'network_uuid': 'netuuid',
                        'macaddr': '1a:91:64:d2:15:40',
                        'ipv4': '127.0.0.6',
                        'order': 1
                    })
                ])
    @mock.patch('shakenfist.net.Network.from_db',
                return_value=FakeNetwork())
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.state',
                new_callable=mock.PropertyMock)
    def test_make_config_drive(self, mock_update, mock_net_from_db,
                               mock_interfaces, mock_get_attribute):
        i = self._make_instance()

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
                            "links": [
                                {
                                    "ethernet_mac_address": "1a:91:64:d2:15:39",
                                    "id": "eth0",
                                    "mtu": 7950,
                                    "name": "eth0",
                                    "type": "vif",
                                    "vif_id": "ifaceuuid"
                                },
                                {
                                    "ethernet_mac_address": "1a:91:64:d2:15:40",
                                    "id": "eth1",
                                    "mtu": 7950,
                                    "name": "eth1",
                                    "type": "vif",
                                    "vif_id": "ifaceuuid2"
                                }
                            ],
                            "networks": [
                                {
                                    'id': 'netuuid-0',
                                    'ip_address': '127.0.0.5',
                                          'link': 'eth0',
                                          'netmask': '255.0.0.0',
                                          'network_id': 'netuuid',
                                          'routes': [{'gateway': '127.0.0.1',
                                                      'netmask': '0.0.0.0',
                                                      'network': '0.0.0.0'}],
                                          'type': 'ipv4'
                                },
                                {
                                    'id': 'netuuid-1',
                                    'ip_address': '127.0.0.6',
                                    'link': 'eth1',
                                    'netmask': '255.0.0.0',
                                    'network_id': 'netuuid',
                                    'type': 'ipv4'
                                }
                            ],
                            "services": [
                                {
                                    "address": "8.8.8.8",
                                    "type": "dns"
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
                            "availability_zone": "sfzone",
                            "devices": [],
                            "hostname": "cirros.local",
                            "launch_index": 0,
                            "name": "cirros",
                            "project_id": None,
                            "public_keys": {
                                "mykey": "thisisasshkey"
                            },
                            "random_seed": "...lol...",
                            "uuid": "fakeuuid"
                        },
                        md, '%s does not match' % entry
                    )
                    del entries[entry]

            self.assertEqual({}, entries)
            cd.close()

        finally:
            if os.path.exists(cd_file):
                os.unlink(cd_file)


GET_ALL_INSTANCES = [
    # Present here, and in the get
    (None, {
        'uuid': '373a165e-9720-4e14-bd0e-9612de79ff15',
        'cpus': 1,
        'disk_spec': [{
            'base': 'cirros',
            'size': 8
        }],
        'memory': 1024,
        'name': 'cirros',
        'namespace': 'gerkin',
        'requested_placement': None,
        'ssh_key': 'thisisasshkey',
        'user_data': None,
        'video': {'model': 'cirrus', 'memory': 16384},
        'uefi': False,
        'configdrive': 'openstack-disk',
        'version': 6,
        'nvram_template': None,
        'secure_boot': False
    }),
    # Present here, but not in the get (a race?)
    (None, {
        'uuid': 'b078cb4e-857c-4f04-b011-751742ef5817',
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
        'user_data': None,
        'video': {'model': 'cirrus', 'memory': 16384},
        'uefi': False,
        'configdrive': 'openstack-disk',
        'version': 6,
        'nvram_template': None,
        'secure_boot': False
    }),
    (None, {
        'uuid': 'a7c5ecec-c3a9-4774-ad1b-249d9e90e806',
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
        'user_data': None,
        'video': {'model': 'cirrus', 'memory': 16384},
        'uefi': False,
        'configdrive': 'openstack-disk',
        'version': 6,
        'nvram_template': None,
        'secure_boot': False
    })
]


class InstancesTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.etcd.get_all', return_value=GET_ALL_INSTANCES)
    def test_base_iteration(self, mock_get_all):
        uuids = []
        for i in instance.Instances([]):
            uuids.append(i.uuid)

        self.assertEqual(['373a165e-9720-4e14-bd0e-9612de79ff15',
                          'b078cb4e-857c-4f04-b011-751742ef5817',
                          'a7c5ecec-c3a9-4774-ad1b-249d9e90e806'], uuids)

    @mock.patch('shakenfist.instance.Instance._db_get_attribute',
                return_value={'node': 'node1'})
    @mock.patch('shakenfist.etcd.get_all', return_value=GET_ALL_INSTANCES)
    def test_placement_filter_all(self, mock_get_all, mock_attr):
        uuids = []
        for i in instance.Instances([partial(instance.placement_filter, 'node1')]):
            uuids.append(i.uuid)

        self.assertEqual(['373a165e-9720-4e14-bd0e-9612de79ff15',
                          'b078cb4e-857c-4f04-b011-751742ef5817',
                          'a7c5ecec-c3a9-4774-ad1b-249d9e90e806'], uuids)

    @mock.patch('shakenfist.instance.Instance._db_get_attribute',
                return_value={'node': 'node2'})
    @mock.patch('shakenfist.etcd.get_all', return_value=GET_ALL_INSTANCES)
    def test_placement_filter_none(self, mock_get_all, mock_attr):
        uuids = []
        for i in instance.Instances([partial(instance.placement_filter, 'node1')]):
            uuids.append(i.uuid)

        self.assertEqual([], uuids)

    @mock.patch('shakenfist.instance.Instance._db_get_attribute',
                return_value={'value': dbo.STATE_CREATED, 'update_time': 1})
    @mock.patch('shakenfist.etcd.get_all', return_value=GET_ALL_INSTANCES)
    def test_state_filter_all(self, mock_get_all, mock_attr):
        uuids = []
        for i in instance.Instances([partial(baseobject.state_filter, dbo.STATE_CREATED)]):
            uuids.append(i.uuid)

        self.assertEqual(['373a165e-9720-4e14-bd0e-9612de79ff15',
                          'b078cb4e-857c-4f04-b011-751742ef5817',
                          'a7c5ecec-c3a9-4774-ad1b-249d9e90e806'], uuids)

    @mock.patch('shakenfist.instance.Instance._db_get_attribute',
                return_value={'value': instance.Instance.STATE_DELETED, 'update_time': 1})
    @mock.patch('shakenfist.etcd.get_all', return_value=GET_ALL_INSTANCES)
    def test_state_filter_none(self, mock_get_all, mock_attr):
        uuids = []
        for i in instance.Instances([partial(baseobject.state_filter, dbo.STATE_CREATED)]):
            uuids.append(i.uuid)

        self.assertEqual([], uuids)

    @mock.patch('shakenfist.instance.Instance._db_get_attribute',
                side_effect=[{'value': instance.Instance.STATE_DELETED, 'update_time': 1},
                             {'value': instance.Instance.STATE_DELETED,
                                 'update_time': 1},
                             {'value': instance.Instance.STATE_INITIAL, 'update_time': 1}])
    @mock.patch('shakenfist.etcd.get_all', return_value=GET_ALL_INSTANCES)
    def test_state_filter_active(self, mock_get_all, mock_attr):
        uuids = []
        for i in instance.Instances([instance.active_states_filter]):
            uuids.append(i.uuid)

        self.assertEqual(['a7c5ecec-c3a9-4774-ad1b-249d9e90e806'], uuids)

    @mock.patch('shakenfist.etcd.get_all', return_value=GET_ALL_INSTANCES)
    def test_namespace_filter(self, mock_get_all):
        uuids = []
        for i in instance.Instances([partial(baseobject.namespace_filter, 'gerkin')]):
            uuids.append(i.uuid)

        self.assertEqual(['373a165e-9720-4e14-bd0e-9612de79ff15'], uuids)
