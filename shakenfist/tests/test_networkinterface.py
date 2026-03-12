from unittest import mock

from shakenfist.network.interface import NetworkInterfaces
from shakenfist.tests import base


GET_ALL_INTERFACES = [
    (None, {
        'uuid': '11111111-1111-4111-8111-111111111111',
        'instance_uuid': '22222222-2222-4222-8222-222222222222',
        'network_uuid': '33333333-3333-4333-8333-333333333333',
        'macaddr': '1a:91:64:d2:15:39',
        'ipv4': '127.0.0.5',
        'order': 0,
        'model': 'virtio',
        'version': 5
    }),
    (None, {
        'uuid': '44444444-4444-4444-8444-444444444444',
        'instance_uuid': '22222222-2222-4222-8222-222222222222',
        'network_uuid': '33333333-3333-4333-8333-333333333333',
        'macaddr': '1a:91:64:d2:15:40',
        'ipv4': '127.0.0.6',
        'order': 1,
        'model': 'virtio',
        'version': 5
    })
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
    @mock.patch('shakenfist.etcd.get_all', return_value=GET_ALL_INTERFACES)
    @mock.patch('shakenfist.mariadb.get_network_interface', return_value=None)
    def test_ni_iterator_mocking(self, mock_mariadb_get, mock_get_all, mock_get):
        self.assertEqual(2, len(list(NetworkInterfaces([]))))
