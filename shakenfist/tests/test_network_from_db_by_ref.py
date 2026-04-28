# Tests for Network.from_db_by_ref (phase 3 SQL pushdown filtering).
#
# This module tests:
# - UUID input short-circuits to cls.from_db (find_networks NOT called)
# - Non-UUID name with specific namespace passes criteria correctly
# - Non-UUID name with namespace='system' passes criteria.namespace=None
# - Non-UUID name with namespace=None passes criteria.namespace=None
# - Zero matches returns None
# - One match returns a Network instance built via _static_values_to_dict
# - Two matches raises exceptions.MultipleObjects with expected message
#
# Constructor side-effects: Network.__init__ calls ipam.IPAM.from_db and
# potentially ipam.IPAM.new to create or load an IPAM object, then accesses
# several IPAM properties (network_address, get_address_at_index, netmask,
# broadcast_address). These are mocked in tests 2-7 via Option A: mock
# ipam.IPAM.from_db to return a MagicMock whose attributes satisfy every
# property access inside __init__. This avoids any real database calls and
# keeps the tests fast. No other DB calls are made by the constructor when
# version == Network.current_version.

import uuid
from unittest import mock

from shakenfist import exceptions
from shakenfist import ipam as ipam_module
from shakenfist.network.network import Network
from shakenfist.schema.network_data import NetworkData
from shakenfist.tests import base


# A valid UUID4 string used for the UUID short-circuit test.
_NETWORK_UUID = str(uuid.uuid4())

# A second UUID for two-match scenario.
_NETWORK_UUID_2 = str(uuid.uuid4())

# Minimal netblock that satisfies IPAM construction if it runs for real.
_NETBLOCK = '10.0.0.0/24'


def _make_network_data(
        net_uuid=None, name='test-net', namespace='tenant-a',
        netblock=_NETBLOCK, vxid=42):
    """Construct a real NetworkData Pydantic instance with minimal fields."""
    return NetworkData(
        uuid=net_uuid or _NETWORK_UUID,
        name=name,
        namespace=namespace,
        netblock=netblock,
        provide_dhcp=False,
        provide_nat=False,
        provide_dns=False,
        vxid=vxid,
        version=Network.current_version,
    )


def _make_mock_ipam():
    """Return a MagicMock that satisfies all Network.__init__ IPAM accesses."""
    mock_ipam = mock.MagicMock()
    mock_ipam.network_address = '10.0.0.0'
    mock_ipam.netmask = '255.255.255.0'
    mock_ipam.broadcast_address = '10.0.0.255'
    mock_ipam.get_address_at_index.side_effect = lambda i: f'10.0.0.{i}'
    return mock_ipam


class NetworkFromDbByRefTestCase(base.ShakenFistTestCase):
    """Unit tests for Network.from_db_by_ref."""

    # ------------------------------------------------------------------
    # Test 1: UUID input short-circuits to cls.from_db
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.network.network.mariadb.find_networks')
    @mock.patch.object(Network, 'from_db')
    def test_uuid_input_calls_from_db_not_find_networks(
            self, mock_from_db, mock_find_networks):
        """UUID ref short-circuits to from_db; find_networks is never called."""
        sentinel = mock.sentinel.network_obj
        mock_from_db.return_value = sentinel

        result = Network.from_db_by_ref(_NETWORK_UUID)

        mock_from_db.assert_called_once_with(_NETWORK_UUID)
        mock_find_networks.assert_not_called()
        self.assertIs(result, sentinel)

    # ------------------------------------------------------------------
    # Test 2: Non-UUID name with specific namespace
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.network.network.mariadb.find_networks')
    @mock.patch.object(ipam_module.IPAM, 'from_db')
    def test_name_with_specific_namespace_passes_correct_criteria(
            self, mock_ipam_from_db, mock_find_networks):
        """Specific namespace is forwarded as criteria.namespace."""
        mock_ipam_from_db.return_value = _make_mock_ipam()
        data = _make_network_data(name='test-net', namespace='tenant-a')
        mock_find_networks.return_value = [data]

        Network.from_db_by_ref('test-net', namespace='tenant-a')

        mock_find_networks.assert_called_once()
        criteria = mock_find_networks.call_args[0][0]
        self.assertEqual(sorted(criteria.states),
                         sorted(list(Network.ACTIVE_STATES)))
        self.assertEqual(criteria.namespace, 'tenant-a')
        self.assertEqual(criteria.name, 'test-net')

    # ------------------------------------------------------------------
    # Test 3: Non-UUID name with namespace='system' → criteria.namespace=None
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.network.network.mariadb.find_networks')
    @mock.patch.object(ipam_module.IPAM, 'from_db')
    def test_name_with_system_namespace_passes_none_criteria_namespace(
            self, mock_ipam_from_db, mock_find_networks):
        """namespace='system' collapses to criteria.namespace=None."""
        mock_ipam_from_db.return_value = _make_mock_ipam()
        data = _make_network_data(name='test-net', namespace='system')
        mock_find_networks.return_value = [data]

        Network.from_db_by_ref('test-net', namespace='system')

        criteria = mock_find_networks.call_args[0][0]
        self.assertIsNone(criteria.namespace)
        self.assertEqual(criteria.name, 'test-net')

    # ------------------------------------------------------------------
    # Test 4: Non-UUID name with namespace=None → criteria.namespace=None
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.network.network.mariadb.find_networks')
    @mock.patch.object(ipam_module.IPAM, 'from_db')
    def test_name_with_none_namespace_passes_none_criteria_namespace(
            self, mock_ipam_from_db, mock_find_networks):
        """namespace=None collapses to criteria.namespace=None."""
        mock_ipam_from_db.return_value = _make_mock_ipam()
        data = _make_network_data(name='test-net', namespace='any')
        mock_find_networks.return_value = [data]

        Network.from_db_by_ref('test-net', namespace=None)

        criteria = mock_find_networks.call_args[0][0]
        self.assertIsNone(criteria.namespace)
        self.assertEqual(criteria.name, 'test-net')

    # ------------------------------------------------------------------
    # Test 5: Zero matches returns None
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.network.network.mariadb.find_networks')
    def test_zero_matches_returns_none(self, mock_find_networks):
        """find_networks returning [] causes from_db_by_ref to return None."""
        mock_find_networks.return_value = []

        result = Network.from_db_by_ref('no-such-net', namespace='tenant-a')

        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Test 6: Exactly one match returns a Network instance
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.network.network.mariadb.find_networks')
    @mock.patch.object(ipam_module.IPAM, 'from_db')
    def test_one_match_returns_network_object(
            self, mock_ipam_from_db, mock_find_networks):
        """One NetworkData returned → Network instance with correct name."""
        mock_ipam_from_db.return_value = _make_mock_ipam()
        data = _make_network_data(name='test-net', namespace='tenant-a')
        mock_find_networks.return_value = [data]

        result = Network.from_db_by_ref('test-net', namespace='tenant-a')

        self.assertIsInstance(result, Network)
        self.assertEqual(result.name, 'test-net')

    # ------------------------------------------------------------------
    # Test 7: Two matches raises MultipleObjects with expected message
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.network.network.mariadb.find_networks')
    def test_two_matches_raises_multiple_objects(self, mock_find_networks):
        """Two NetworkData records → MultipleObjects with name and namespace."""
        data1 = _make_network_data(
            net_uuid=_NETWORK_UUID, name='test-net',
            namespace='tenant-a', vxid=42)
        data2 = _make_network_data(
            net_uuid=_NETWORK_UUID_2, name='test-net',
            namespace='tenant-a', vxid=43)
        mock_find_networks.return_value = [data1, data2]

        with self.assertRaises(exceptions.MultipleObjects) as ctx:
            Network.from_db_by_ref('test-net', namespace='tenant-a')

        msg = str(ctx.exception)
        self.assertIn('test-net', msg)
        self.assertIn('tenant-a', msg)
