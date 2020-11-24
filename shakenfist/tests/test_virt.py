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

    @mock.patch('shakenfist.etcd.put')
    def test_instance_new(self, mock_put):
        virt.Instance.new('barry', 1, 2048, 'namespace', 'sshkey',
                          [{}],
                          'userdata', {'memory': 16384, 'model': 'cirrus'},
                          uuid='uuid42',)

        etcd_write = mock_put.mock_calls[0][1]
        del etcd_write[3]['node']
        del etcd_write[3]['state_updated']

        self.assertEqual(
            ('instance', None, 'uuid42',
             {
                 'version': 1,
                 'uuid': 'uuid42',
                 'name': 'barry',
                 'cpus': 1,
                 'memory': 2048,
                 'disk_spec': [{}],
                 'ssh_key': 'sshkey',
                 'console_port': 0,
                 'vdi_port': 0,
                 'user_data': 'userdata',
                 'state': 'initial',
                 'namespace': 'namespace',
                 'power_state': 'initial',
                 'video': {'memory': 16384, 'model': 'cirrus'},
                 'node_history': [],
                 'error_message': None,
                 'requested_placement': None,
                 'placement_attempts': 0,
                 'devices': None,
                 'power_state_previous': None,
                 'power_state_updated': 0,
                 'block_devices': {'devices': [
                     {
                        'base': None,
                        'bus': 'virtio',
                        'device': 'vda',
                        'path': '/a/b/c/instances/uuid42/vda',
                        'present_as': 'disk',
                        'size': None,
                        'snapshot_ignores': False,
                        'type': 'qcow2'
                        },
                     {
                        'bus': 'virtio',
                        'device': 'vdb',
                        'path': '/a/b/c/instances/uuid42/vdb',
                        'present_as': 'disk',
                        'snapshot_ignores': True,
                        'type': 'raw',
                        }
                     ],
                    'finalized': False},
             }),
            etcd_write)

    @mock.patch(
        'shakenfist.etcd.get', return_value={
            'version': 1,
            'uuid': 'uuid42',
            'name': 'barry',
            'cpus': 1,
            'memory': 2048,
            'disk_spec': [{}],
            'ssh_key': 'sshkey',
            'console_port': 0,
            'vdi_port': 0,
            'user_data': 'userdata',
            'state': 'initial',
            'namespace': 'namespace',
            'power_state': 'initial',
            'video': {'memory': 16384, 'model': 'cirrus'},
            'node_history': [],
            'error_message': None,
            'requested_placement': None,
            'placement_attempts': 0,
            'devices': None,
            'power_state_previous': None,
            'power_state_updated': 0,
            'block_devices': {
                'devices': [
                    {
                        'base': None,
                        'bus': 'virtio',
                        'device': 'vda',
                        'path': '/a/b/c/instances/uuid42/vda',
                        'present_as': 'disk',
                        'size': None,
                        'snapshot_ignores': False,
                        'type': 'qcow2'
                        },
                    {
                        'bus': 'virtio',
                        'device': 'vdb',
                        'path': '/a/b/c/instances/uuid42/vdb',
                        'present_as': 'disk',
                        'snapshot_ignores': True,
                        'type': 'raw',
                        }
                ],
                'finalized': False
            },
        },
    )
    def test_from_db(self, mock_get):
        inst = virt.Instance.from_db('uuid42')
        self.assertEqual('uuid42', inst.uuid)
        self.assertEqual('barry', inst.name)
        self.assertEqual(1, inst.cpus)
        self.assertEqual(2048, inst.memory)
        self.assertEqual([{}], inst.disk_spec)
        self.assertEqual('sshkey', inst.ssh_key)
        self.assertEqual(0, inst.console_port)
        self.assertEqual(0, inst.vdi_port)
        self.assertEqual('userdata', inst.user_data)
        self.assertEqual('initial', inst.state)
        self.assertEqual('namespace', inst.namespace)
        self.assertEqual('initial', inst.power_state)
        self.assertEqual({'memory': 16384, 'model': 'cirrus'}, inst.video)
        self.assertEqual([], inst.node_history)
        self.assertEqual(None, inst.error_message)
        self.assertEqual(None, inst.requested_placement)
        self.assertEqual(0, inst.placement_attempts)
        self.assertEqual(None, inst.devices)
        self.assertEqual(None, inst.power_state_previous)
        self.assertEqual(0, inst.power_state_updated)


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

    def _make_instance(self):
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

    @mock.patch('shakenfist.etcd.put')
    def test_update_instance_state(self, mock_put):
        i = self._make_instance()
        i.state = 'initial'
        i.update_instance_state('created')

        etcd_write = mock_put.mock_calls[2][1]
        self.assertEqual(('instance', None, 'fakeuuid'), etcd_write[0:3])
        self.assertTrue(time.time() - etcd_write[3]['state_updated'] < 3)
        self.assertEqual('created', etcd_write[3]['state'])
        self.assertEqual('fakeuuid', etcd_write[3]['uuid'])

    @mock.patch('shakenfist.etcd.put')
    def test_update_instance_state_duplicate(self, mock_put):
        i = self._make_instance()
        i.state = 'created'
        i.update_instance_state('created')
        self.assertEqual(2, mock_put.call_count)

    @mock.patch('shakenfist.etcd.put')
    def test_update_power_state(self, mock_put):
        i = self._make_instance()
        i.power_state = 'on'
        i.update_power_state('off')

        etcd_write = mock_put.mock_calls[2][1]
        self.assertEqual(('instance', None, 'fakeuuid'), etcd_write[0:3])
        self.assertTrue(time.time() - etcd_write[3]['power_state_updated'] < 3)
        self.assertEqual('off', etcd_write[3]['power_state'])
        self.assertEqual('on', etcd_write[3]['power_state_previous'])
        self.assertEqual('fakeuuid', etcd_write[3]['uuid'])

    @mock.patch('shakenfist.etcd.put')
    def test_update_power_state_duplicate(self, mock_put):
        i = self._make_instance()
        i.power_state = 'on'
        i.update_power_state('on')
        self.assertEqual(2, mock_put.call_count)

    @mock.patch('shakenfist.etcd.put')
    def test_update_power_state_transition_new(self, mock_put):
        i = self._make_instance()
        i.power_state_previous = 'on'
        i.power_state = 'transition-to-off'
        i.power_state_updated = time.time()
        i.update_power_state('on')
        self.assertEqual(2, mock_put.call_count)

    @mock.patch('shakenfist.etcd.put')
    def test_update_power_state_transition_old(self, mock_put):
        i = self._make_instance()
        i.power_state_previous = 'on'
        i.power_state = 'transition-to-off'
        i.power_state_updated = time.time() - 71
        i.update_power_state('on')

        etcd_write = mock_put.mock_calls[2][1]
        self.assertEqual(('instance', None, 'fakeuuid'), etcd_write[0:3])
        self.assertTrue(time.time() - etcd_write[3]['power_state_updated'] < 3)
        self.assertEqual('on', etcd_write[3]['power_state'])
        self.assertEqual('transition-to-off', etcd_write[3]['power_state_previous'])
        self.assertEqual('fakeuuid', etcd_write[3]['uuid'])

    def test_init(self):
        i = self._make_instance()

        self.assertEqual('/a/b/c/instances/fakeuuid', i.instance_path())
        self.assertEqual('/a/b/c/snapshots', i.snapshot_path())
        self.assertEqual('/a/b/c/instances/fakeuuid/libvirt.xml', i.xml_file())
        self.assertEqual(
            [
                {
                    'base': 'cirros',
                    'device': 'vda',
                    'bus': 'virtio',
                    'path': '/a/b/c/instances/fakeuuid/vda',
                    'size': 8,
                    'type': 'qcow2',
                    'present_as': 'disk',
                    'snapshot_ignores': False
                },
                {
                    'device': 'vdb',
                    'bus': 'virtio',
                    'path': '/a/b/c/instances/fakeuuid/vdb',
                    'type': 'raw',
                    'present_as': 'disk',
                    'snapshot_ignores': True
                }
            ], i.block_devices['devices'])

    def test_init_multiple_disks(self):
        i = virt.Instance.new(
            'cirros', 1, 1024, 'namespace',
            uuid='fakeuuid',
            disk_spec=[
                {
                    'base': 'cirros',
                    'size': 8
                },
                {
                    'size': 16
                },
                {
                    'size': 24
                }
            ],
        )

        self.assertEqual('/a/b/c/instances/fakeuuid', i.instance_path())
        self.assertEqual('/a/b/c/snapshots', i.snapshot_path())
        self.assertEqual('/a/b/c/instances/fakeuuid/libvirt.xml', i.xml_file())
        self.assertEqual(
            [
                {
                    'base': 'cirros',
                    'device': 'vda',
                    'bus': 'virtio',
                    'path': '/a/b/c/instances/fakeuuid/vda',
                    'size': 8,
                    'type': 'qcow2',
                    'present_as': 'disk',
                    'snapshot_ignores': False
                },
                {
                    'device': 'vdb',
                    'bus': 'virtio',
                    'path': '/a/b/c/instances/fakeuuid/vdb',
                    'type': 'raw',
                    'present_as': 'disk',
                    'snapshot_ignores': True
                },
                {
                    'base': None,
                    'device': 'vdc',
                    'bus': 'virtio',
                    'path': '/a/b/c/instances/fakeuuid/vdc',
                    'size': 16,
                    'type': 'qcow2',
                    'present_as': 'disk',
                    'snapshot_ignores': False
                },
                {
                    'base': None,
                    'device': 'vdd',
                    'bus': 'virtio',
                    'path': '/a/b/c/instances/fakeuuid/vdd',
                    'size': 24,
                    'type': 'qcow2',
                    'present_as': 'disk',
                    'snapshot_ignores': False
                }
            ], i.block_devices['devices'])

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
