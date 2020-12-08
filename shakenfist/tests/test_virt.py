import base64
import json
import mock
import os
import pycdlib
import tempfile
import time

from shakenfist.ipmanager import IPManager
from shakenfist import virt
from shakenfist.config import SFConfig
from shakenfist.tests import test_shakenfist


class FakeNetwork(object):
    def __init__(self):
        self.ipmanager = IPManager('uuid', '127.0.0.0/8')
        self.router = self.ipmanager.get_address_at_index(1)
        self.netmask = '255.0.0.0'
        self.dhcp_start = '127.0.0.2'
        self.broadcast = '127.255.255.255'


class VirtMetaTestCase(test_shakenfist.ShakenFistTestCase):
    def setUp(self):
        super(VirtMetaTestCase, self).setUp()
        fake_config = SFConfig(
            STORAGE_PATH="/a/b/c",
            DISK_BUS="virtio",
            DISK_FORMAT="qcow",
            ZONE="sfzone",
            NODE_NAME="node01",
        )

        self.config = mock.patch('shakenfist.virt.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.etcd.get',
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
                    'version': 2
                })
    @mock.patch('shakenfist.etcd.put')
    def test_instance_new(self, mock_put, mock_get):
        virt.Instance.new('barry', 1, 2048, 'namespace', 'sshkey',
                          [{}], 'userdata', {'memory': 16384, 'model': 'cirrus'},
                          uuid='uuid42',)

        self.assertEqual(
            ('attribute/instance', 'uuid42', 'state', {'state': 'initial'}),
            mock_put.mock_calls[0][1])
        self.assertEqual(
            ('attribute/instance', 'uuid42',
             'power_state', {'power_state': 'initial'}),
            mock_put.mock_calls[1][1])

        self.assertEqual(
            ('instance', None, 'uuid42',
             {'cpus': 1,
              'disk_spec': [{}],
              'memory': 2048,
              'name': 'barry',
              'namespace': 'namespace',
              'requested_placement': None,
              'ssh_key': 'sshkey',
              'user_data': 'userdata',
              'uuid': 'uuid42',
              'version': 2,
              'video': {'memory': 16384, 'model': 'cirrus'}}),
            mock_put.mock_calls[2][1])

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
                    'version': 2,
                    'video': {'memory': 16384, 'model': 'cirrus'}
                })
    def test_from_db(self, mock_get):
        inst = virt.Instance.from_db('uuid42')
        self.assertEqual({
            'cpus': 1,
            'disk_spec': [{}],
            'memory': 2048,
            'name': 'barry',
            'namespace': 'namespace',
            'requested_placement': None,
            'ssh_key': 'sshkey',
            'user_data': 'userdata',
            'uuid': 'uuid42',
            'version': 2,
            'video': {'memory': 16384, 'model': 'cirrus'}
        }, inst.static_values)


class VirtTestCase(test_shakenfist.ShakenFistTestCase):
    def setUp(self):
        super(VirtTestCase, self).setUp()
        fake_config = SFConfig(
            STORAGE_PATH="/a/b/c",
            DISK_BUS="virtio",
            DISK_FORMAT="qcow",
            ZONE="sfzone",
            NODE_NAME="node01",
        )

        self.config = mock.patch('shakenfist.virt.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        # self.libvirt = mock.patch('libvirt')
        # self.mock_libvirt = self.libvirt.start()

        self.put = mock.patch('shakenfist.etcd.put')
        self.mock_put = self.put.start()
        self.addCleanup(self.put.stop)

    @mock.patch('shakenfist.db.create_instance')
    @mock.patch('shakenfist.db.get_instance',
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
                    'version': 2
                })
    def _make_instance(self, mock_get_instance, mock_create_instance):
        return virt.Instance.new(
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

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.db.get_instance_attribute',
                return_value={
                    'state': 'initial'
                })
    @mock.patch('shakenfist.etcd.put')
    def test_update_instance_state(self, mock_put, mock_get_attribute,
                                   mock_lock):
        i = self._make_instance()
        i.update_instance_state('created')

        etcd_write = mock_put.mock_calls[1][1]
        self.assertTrue(time.time() - etcd_write[3]['state_updated'] < 3)
        self.assertEqual('created', etcd_write[3]['state'])

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.db.get_instance_attribute',
                return_value={
                    'state': 'created'
                })
    @mock.patch('shakenfist.etcd.put')
    def test_update_instance_state_duplicate(self, mock_put, mock_get_attribute,
                                             mock_lock):
        i = self._make_instance()
        i.update_instance_state('created')
        self.assertEqual(1, mock_put.call_count)

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.db.get_instance_attribute',
                return_value={
                    'power_state': 'on',
                    'power_state_updated': 0
                })
    @mock.patch('shakenfist.etcd.put')
    def test_update_power_state(self, mock_put, mock_get_attribute, mock_lock):
        i = self._make_instance()
        i.update_power_state('off')

        etcd_write = mock_put.mock_calls[1][1]
        self.assertTrue(time.time() - etcd_write[3]['power_state_updated'] < 3)
        self.assertEqual('off', etcd_write[3]['power_state'])
        self.assertEqual('on', etcd_write[3]['power_state_previous'])

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.db.get_instance_attribute',
                return_value={
                    'power_state': 'on',
                    'power_state_updated': 0
                })
    @mock.patch('shakenfist.etcd.put')
    def test_update_power_state_duplicate(self, mock_put, mock_get_attribute,
                                          mock_lock):
        i = self._make_instance()
        i.update_power_state('on')
        self.assertEqual(1, mock_put.call_count)

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.db.get_instance_attribute',
                return_value={
                    'power_state_previous': 'on',
                    'power_state': 'transition-to-off',
                    'power_state_updated': time.time()
                })
    @mock.patch('shakenfist.etcd.put')
    def test_update_power_state_transition_new(self, mock_put, mock_get_attribute,
                                               mock_lock):
        i = self._make_instance()
        i.update_power_state('on')
        self.assertEqual(1, mock_put.call_count)

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.db.get_instance_attribute',
                return_value={
                    'power_state_previous': 'on',
                    'power_state': 'transition-to-off',
                    'power_state_updated': time.time() - 71
                })
    @mock.patch('shakenfist.etcd.put')
    def test_update_power_state_transition_old(self, mock_put, mock_get_attribute,
                                               mock_lock):
        i = self._make_instance()
        i.update_power_state('on')

        etcd_write = mock_put.mock_calls[1][1]
        self.assertTrue(time.time() - etcd_write[3]['power_state_updated'] < 3)
        self.assertEqual('on', etcd_write[3]['power_state'])
        self.assertEqual('transition-to-off',
                         etcd_write[3]['power_state_previous'])

    def test_helpers(self):
        self.assertEqual('/a/b/c/instances/fakeuuid',
                         virt.instance_path('fakeuuid'))
        self.assertEqual('/a/b/c/snapshots',
                         virt._snapshot_path('fakeuuid'))
        self.assertEqual('/a/b/c/instances/fakeuuid/libvirt.xml',
                         virt._xml_file('fakeuuid'))

    def test_str(self):
        i = self._make_instance()
        s = str(i)
        self.assertEqual('instance(fakeuuid)', s)

    # create, delete

    @mock.patch('shakenfist.db.get_network',
                return_value={
                    'uuid': 'netuuid',
                    'netblock': '127.0.0.0/8'
                })
    @mock.patch('shakenfist.db.get_instance_interfaces',
                return_value=[
                    {
                        'uuid': 'ifaceuuid',
                        'instance_uuid': 'instuuid',
                        'network_uuid': 'netuuid',
                        'macaddr': '1a:91:64:d2:15:39',
                        'ipv4': '127.0.0.5',
                        'order': 0
                    },
                    {
                        'uuid': 'ifaceuuid2',
                        'instance_uuid': 'instuuid',
                        'network_uuid': 'netuuid',
                        'macaddr': '1a:91:64:d2:15:40',
                        'ipv4': '127.0.0.6',
                        'order': 1
                    }
                ])
    @mock.patch('shakenfist.net.Network.from_db',
                return_value=FakeNetwork())
    def test_make_config_drive(self, mock_net_from_db, mock_interfaces,
                               mock_network):
        i = self._make_instance()

        (fd, cd_file) = tempfile.mkstemp()
        os.close(fd)

        try:
            i._make_config_drive(cd_file)
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
                                    "mtu": 1450,
                                    "name": "eth0",
                                    "type": "vif",
                                    "vif_id": "ifaceuuid"
                                },
                                {
                                    "ethernet_mac_address": "1a:91:64:d2:15:40",
                                    "id": "eth1",
                                    "mtu": 1450,
                                    "name": "eth1",
                                    "type": "vif",
                                    "vif_id": "ifaceuuid2"
                                }
                            ],
                            "networks": [
                                {
                                    "id": "netuuid",
                                    "ip_address": "127.0.0.5",
                                    "link": "eth0",
                                    "netmask": "255.0.0.0",
                                    "network_id": "netuuid",
                                    "routes": [
                                        {
                                            "gateway": "127.0.0.1",
                                            "netmask": "0.0.0.0",
                                            "network": "0.0.0.0"
                                        }
                                    ],
                                    "type": "ipv4"
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
