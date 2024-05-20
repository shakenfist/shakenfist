from unittest import mock

from shakenfist.networkinterface import NetworkInterfaces
from shakenfist.tests import base


GET_ALL_INTERFACES = [
    (None, {
        'uuid': 'ifaceuuid',
        'instance_uuid': 'instuuid',
        'network_uuid': 'netuuid',
        'macaddr': '1a:91:64:d2:15:39',
        'ipv4': '127.0.0.5',
        'order': 0,
        'model': 'virtio',
        'version': 3
    }),
    (None, {
        'uuid': 'ifaceuuid2',
        'instance_uuid': 'instuuid',
        'network_uuid': 'netuuid',
        'macaddr': '1a:91:64:d2:15:40',
        'ipv4': '127.0.0.6',
        'order': 1,
        'model': 'virtio',
        'version': 3
    })
]

JUST_INTERFACES = [
    {
        'uuid': 'ifaceuuid',
        'instance_uuid': 'instuuid',
        'network_uuid': 'netuuid',
        'macaddr': '1a:91:64:d2:15:39',
        'ipv4': '127.0.0.5',
        'order': 0,
        'model': 'virtio',
        'version': 3
    },
    {
        'uuid': 'ifaceuuid2',
        'instance_uuid': 'instuuid',
        'network_uuid': 'netuuid',
        'macaddr': '1a:91:64:d2:15:40',
        'ipv4': '127.0.0.6',
        'order': 1,
        'model': 'virtio',
        'version': 3
    }
]


class NetworkInterfaceTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.etcd.get', side_effect=JUST_INTERFACES)
    @mock.patch('shakenfist.etcd.get_all', return_value=GET_ALL_INTERFACES)
    def test_ni_iterator_mocking(self, mock_get_all, mock_get):
        self.assertEqual(2, len(list(NetworkInterfaces([]))))
