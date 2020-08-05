import base64
import json
import mock
import os
import pycdlib
import tempfile
import testtools


from shakenfist import ipmanager
from shakenfist import virt


class FakeNetwork(object):
    def __init__(self):
        self.ipmanager = ipmanager.NetBlock('127.0.0.0/8')
        self.router = self.ipmanager.get_address_at_index(1)
        self.netmask = '255.0.0.0'
        self.dhcp_start = '127.0.0.2'
        self.broadcast = '127.255.255.255'


class VirtTestCase(testtools.TestCase):
    def setUp(self):
        super(VirtTestCase, self).setUp()

        def fake_config(key):
            if key == 'STORAGE_PATH':
                return '/a/b/c'
            if key == 'DISK_BUS':
                return 'virtio'
            if key == 'DISK_FORMAT':
                return 'qcow'
            if key == 'ZONE':
                return 'sfzone'
            if key == 'NODE_NAME':
                return 'node01'
            raise Exception('Unknown key')

        self.config = mock.patch('shakenfist.config.parsed.get',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        # self.libvirt = mock.patch('libvirt')
        # self.mock_libvirt = self.libvirt.start()

    def _make_instance(self):
        return virt.Instance({
            'uuid': 'fakeuuid',
            'name': 'cirros',
            'disk_spec': [{
                'base': 'cirros',
                'size': 8
            }],
            'ssh_key': 'thisisasshkey',
            'user_data': str(base64.b64encode(
                'thisisuserdata'.encode('utf-8')), 'utf-8'),
            'block_devices': None
        })

    def test_init(self):
        i = self._make_instance()

        self.assertEqual('/a/b/c/instances/fakeuuid', i.instance_path)
        self.assertEqual('/a/b/c/snapshots', i.snapshot_path)
        self.assertEqual('/a/b/c/instances/fakeuuid/libvirt.xml', i.xml_file)
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
            ], i.db_entry['block_devices']['devices'])

    def test_init_muliple_disks(self):
        i = virt.Instance({
            'uuid': 'fakeuuid',
            'name': 'cirros',
            'disk_spec':
            [
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
            'block_devices': None
        })

        self.assertEqual('/a/b/c/instances/fakeuuid', i.instance_path)
        self.assertEqual('/a/b/c/snapshots', i.snapshot_path)
        self.assertEqual('/a/b/c/instances/fakeuuid/libvirt.xml', i.xml_file)
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
            ], i.db_entry['block_devices']['devices'])

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
    @mock.patch('shakenfist.net.from_db',
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
