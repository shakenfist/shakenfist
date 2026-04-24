from unittest import mock

from shakenfist.network.interface import NetworkInterfaces
from shakenfist.schema.network_interface_data import NetworkInterfaceData
from shakenfist.tests import base


MARIADB_ALL_INTERFACES = [
    NetworkInterfaceData(
        uuid='11111111-1111-4111-8111-111111111111',
        instance_uuid='22222222-2222-4222-8222-222222222222',
        network_uuid='33333333-3333-4333-8333-333333333333',
        macaddr='1a:91:64:d2:15:39',
        ipv4='127.0.0.5',
        order=0,
        model='virtio',
        version=5
    ),
    NetworkInterfaceData(
        uuid='44444444-4444-4444-8444-444444444444',
        instance_uuid='22222222-2222-4222-8222-222222222222',
        network_uuid='33333333-3333-4333-8333-333333333333',
        macaddr='1a:91:64:d2:15:40',
        ipv4='127.0.0.6',
        order=1,
        model='virtio',
        version=5
    )
]

JUST_INTERFACES = [
    {
        'uuid': '11111111-1111-4111-8111-111111111111',
        'instance_uuid': '22222222-2222-4222-8222-222222222222',
        'network_uuid': '33333333-3333-4333-8333-333333333333',
        'macaddr': '1a:91:64:d2:15:39',
        'ipv4': '127.0.0.5',
        'order': 0,
        'model': 'virtio',
        'version': 5
    },
    {
        'uuid': '44444444-4444-4444-8444-444444444444',
        'instance_uuid': '22222222-2222-4222-8222-222222222222',
        'network_uuid': '33333333-3333-4333-8333-333333333333',
        'macaddr': '1a:91:64:d2:15:40',
        'ipv4': '127.0.0.6',
        'order': 1,
        'model': 'virtio',
        'version': 5
    }
]


class NetworkInterfaceTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.etcd.get', side_effect=JUST_INTERFACES)
    @mock.patch('shakenfist.mariadb.find_network_interfaces',
                return_value=MARIADB_ALL_INTERFACES)
    def test_ni_iterator_mocking(self, mock_find_nis, mock_get):
        self.assertEqual(2, len(list(NetworkInterfaces([]))))
