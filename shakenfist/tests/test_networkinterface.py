import uuid
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import SFConfig
from shakenfist.network import network
from shakenfist.network.interface import NetworkInterface
from shakenfist.network.interface import NetworkInterfaces
from shakenfist.schema.network_interface_data import NetworkInterfaceData
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


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


class NetworkInterfaceTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.mariadb.find_network_interfaces',
                return_value=MARIADB_ALL_INTERFACES)
    def test_ni_iterator_mocking(self, mock_find_nis):
        self.assertEqual(2, len(list(NetworkInterfaces([]))))


class NetworkInterfacesPropertyTestCase(base.ShakenFistTestCase):
    """Behaviour tests for ``Network.networkinterfaces`` (phase 7).

    The property is query-backed: each access runs
    ``find_network_interfaces`` with a state filter pinned to
    ``ACTIVE_STATES``. Tests below assert that the property reflects
    the live state of the ``network_interfaces`` table — including
    that interfaces transitioned to ``DELETED`` drop out of the
    list.
    """

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
            ETCD_HOST='127.0.0.1',
        )
        self.config = mock.patch(
            'shakenfist.network.network.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

    def _build(self):
        net_uuid = str(uuid.uuid4())
        inst_uuid = str(uuid.uuid4())
        ni_one_uuid = str(uuid.uuid4())
        ni_two_uuid = str(uuid.uuid4())

        self.mock_etcd.create_network(
            'testnet', net_uuid, netblock='127.0.0.0/8')
        ni_one = self.mock_etcd.create_network_interface(
            ni_one_uuid,
            {
                'network_uuid': net_uuid,
                'address': '127.0.0.5',
                'model': 'virtio',
                'macaddress': '1a:91:64:d2:15:39',
            },
            instance_uuid=inst_uuid, order=0)
        ni_two = self.mock_etcd.create_network_interface(
            ni_two_uuid,
            {
                'network_uuid': net_uuid,
                'address': '127.0.0.6',
                'model': 'virtio',
                'macaddress': '1a:91:64:d2:15:40',
            },
            instance_uuid=inst_uuid, order=1)
        return network.Network.from_db(net_uuid), ni_one, ni_two

    def test_property_returns_hydrated_objects(self):
        n, ni_one, ni_two = self._build()
        nis = n.networkinterfaces
        self.assertEqual(2, len(nis))
        for ni in nis:
            self.assertIsInstance(ni, NetworkInterface)
        self.assertEqual(
            {str(ni_one.uuid), str(ni_two.uuid)},
            {str(ni.uuid) for ni in nis})

    def test_deleted_ni_drops_out_of_property(self):
        n, ni_one, ni_two = self._build()
        self.assertEqual(2, len(n.networkinterfaces))

        # Simulate the terminal lifecycle transition that
        # ``NetworkInterface.delete`` lands on. ``ACTIVE_STATES``
        # excludes ``deleted``, so the query-backed property must
        # stop returning this NI.
        ni_one._state_update(
            dbo.STATE_DELETED, skip_transition_validation=True)

        remaining = n.networkinterfaces
        self.assertEqual(1, len(remaining))
        self.assertEqual(str(ni_two.uuid), str(remaining[0].uuid))

    def test_property_empty_for_network_with_no_nis(self):
        net_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'lonely', net_uuid, netblock='127.0.0.0/8')
        n = network.Network.from_db(net_uuid)
        self.assertEqual([], n.networkinterfaces)


class NetworkInterfaceDataSchemaTestCase(base.ShakenFistTestCase):
    """``ipv4`` is Optional[str] because OpenStack Kolla compatibility
    lets callers create interfaces with ``address=none`` — see
    ``external_api/instance.py:_netdesc_allocate_address``. A regression
    here surfaces as a 500 from POST /instances complaining that ipv4
    must be a string.
    """

    def test_ipv4_can_be_none(self):
        data = NetworkInterfaceData(
            uuid='11111111-1111-4111-8111-111111111111',
            instance_uuid='22222222-2222-4222-8222-222222222222',
            network_uuid='33333333-3333-4333-8333-333333333333',
            macaddr='1a:91:64:d2:15:39',
            ipv4=None,
            order=0,
            model='virtio',
            version=5,
        )
        self.assertIsNone(data.ipv4)

    def test_ipv4_defaults_to_none(self):
        data = NetworkInterfaceData(
            uuid='11111111-1111-4111-8111-111111111111',
            instance_uuid='22222222-2222-4222-8222-222222222222',
            network_uuid='33333333-3333-4333-8333-333333333333',
            macaddr='1a:91:64:d2:15:39',
            order=0,
            model='virtio',
            version=5,
        )
        self.assertIsNone(data.ipv4)
