# Tests for the mariadb find functions (phase 1 SQL pushdown filtering).
#
# This module tests:
# - find_artifacts() — public wrapper (direct and gRPC paths)
# - find_instances() — public wrapper (direct and gRPC paths)
# - find_networks()  — public wrapper (direct and gRPC paths)
# - _direct_find_artifacts() — all-filters, each-filter-alone, no-filters,
#   states=[], no-match, mismatched namespace, empty table, OperationalError
# - _direct_find_instances() — same eight scenarios
# - _direct_find_networks()  — same eight scenarios
# - _grpc_find_* smoke tests — verify proto conversion without DB access

from unittest import mock
import uuid

from sqlalchemy.exc import OperationalError

from shakenfist import mariadb
from shakenfist.config import BaseSettings
from shakenfist.schema.object_filter import ObjectFilterCriteria
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    DATABASE_NODE_IP: str = '192.168.1.1'
    DATABASE_API_PORT: int = 13005
    MARIADB_HOST: str = 'localhost'
    NODE_NAME: str = 'testnode'


fake_config = FakeConfig()

ARTIFACT_UUID = uuid.uuid4()
INSTANCE_UUID = uuid.uuid4()
NETWORK_UUID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine_mock(rows):
    """Return a mock engine whose conn.execute().fetchall() yields rows."""
    mock_engine = mock.MagicMock()
    mock_conn = mock.MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = rows
    mock_engine.connect.return_value.__enter__ = mock.Mock(
        return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = mock.Mock(
        return_value=False)
    return mock_engine


def _artifact_row(
        art_uuid=None, artifact_type='image',
        source_url='http://example.com/img.qcow2',
        name='img.qcow2', namespace='system', version=1):
    row = mock.MagicMock()
    row.uuid = art_uuid or ARTIFACT_UUID
    row.artifact_type = artifact_type
    row.source_url = source_url
    row.name = name
    row.namespace = namespace
    row.version = version
    return row


def _network_row(
        net_uuid=None, name='testnet', namespace='system',
        netblock='10.0.0.0/24', provide_dhcp=True, provide_nat=False,
        provide_dns=False, vxid=42, egress_nic=None, mesh_nic=None,
        version=1):
    row = mock.MagicMock()
    row.uuid = net_uuid or NETWORK_UUID
    row.name = name
    row.namespace = namespace
    row.netblock = netblock
    row.provide_dhcp = provide_dhcp
    row.provide_nat = provide_nat
    row.provide_dns = provide_dns
    row.vxid = vxid
    row.egress_nic = egress_nic
    row.mesh_nic = mesh_nic
    row.version = version
    return row


def _instance_row(
        inst_uuid=None, cpus=2, memory=1024, name='vm1',
        namespace='system', version=1):
    row = mock.MagicMock()
    row.uuid = inst_uuid or INSTANCE_UUID
    row.cpus = cpus
    row.memory = memory
    row.name = name
    row.namespace = namespace
    row.disk_spec = '[]'
    row.requested_placement = None
    row.ssh_key = None
    row.user_data = None
    row.video = '{}'
    row.uefi = False
    row.configdrive = 'openstack-disk'
    row.nvram_template = None
    row.secure_boot = False
    row.machine_type = 'pc'
    row.side_channels = '[]'
    row.version = version
    return row


# ---------------------------------------------------------------------------
# _direct_find_artifacts
# ---------------------------------------------------------------------------

class DirectFindArtifactsTestCase(base.ShakenFistTestCase):
    """Tests for _direct_find_artifacts()."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_all_filters_returns_match(self, mock_get_engine):
        """All three filters present — matching row returned."""
        row = _artifact_row(namespace='system', name='img.qcow2')
        mock_get_engine.return_value = _make_engine_mock([row])

        criteria = ObjectFilterCriteria(
            states=['active'], namespace='system', name='img.qcow2')
        result = mariadb._direct_find_artifacts(criteria)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].namespace, 'system')
        self.assertEqual(result[0].name, 'img.qcow2')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_state_filter_only(self, mock_get_engine):
        """states only — no namespace/name filter."""
        row = _artifact_row()
        mock_get_engine.return_value = _make_engine_mock([row])

        criteria = ObjectFilterCriteria(states=['active'])
        result = mariadb._direct_find_artifacts(criteria)

        self.assertEqual(len(result), 1)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_namespace_filter_only(self, mock_get_engine):
        """namespace only — no state/name filter."""
        row = _artifact_row(namespace='ns1')
        mock_get_engine.return_value = _make_engine_mock([row])

        criteria = ObjectFilterCriteria(namespace='ns1')
        result = mariadb._direct_find_artifacts(criteria)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].namespace, 'ns1')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_name_filter_only(self, mock_get_engine):
        """name only — no state/namespace filter."""
        row = _artifact_row(name='myimage')
        mock_get_engine.return_value = _make_engine_mock([row])

        criteria = ObjectFilterCriteria(name='myimage')
        result = mariadb._direct_find_artifacts(criteria)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'myimage')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_filters_returns_all(self, mock_get_engine):
        """No filters at all — every row in the table comes back."""
        rows = [_artifact_row(art_uuid=uuid.uuid4()) for _ in range(3)]
        mock_get_engine.return_value = _make_engine_mock(rows)

        criteria = ObjectFilterCriteria()
        result = mariadb._direct_find_artifacts(criteria)

        self.assertEqual(len(result), 3)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_empty_states_list_returns_all(self, mock_get_engine):
        """states=[] is treated the same as None — no state filter applied."""
        rows = [_artifact_row(art_uuid=uuid.uuid4()) for _ in range(2)]
        mock_get_engine.return_value = _make_engine_mock(rows)

        criteria = ObjectFilterCriteria(states=[])
        result = mariadb._direct_find_artifacts(criteria)

        self.assertEqual(len(result), 2)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_match_returns_empty(self, mock_get_engine):
        """Query returns no rows — result is []."""
        mock_get_engine.return_value = _make_engine_mock([])

        criteria = ObjectFilterCriteria(states=['deleted'])
        result = mariadb._direct_find_artifacts(criteria)

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_mismatched_namespace_returns_empty(self, mock_get_engine):
        """DB returns no rows for mismatched namespace — [] without error."""
        mock_get_engine.return_value = _make_engine_mock([])

        criteria = ObjectFilterCriteria(namespace='other-ns')
        result = mariadb._direct_find_artifacts(criteria)

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_empty_table_returns_empty(self, mock_get_engine):
        """Empty table — returns []."""
        mock_get_engine.return_value = _make_engine_mock([])

        criteria = ObjectFilterCriteria()
        result = mariadb._direct_find_artifacts(criteria)

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_operational_error_logs_and_returns_empty(
            self, mock_get_engine):
        """OperationalError — logs warning with all criteria and returns []."""
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = OperationalError(
            'statement', {}, Exception('DB gone'))
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        criteria = ObjectFilterCriteria(
            states=['active'], namespace='system', name='img.qcow2')

        with self.assertLogs('shakenfist.mariadb', level='WARNING') as cm:
            result = mariadb._direct_find_artifacts(criteria)

        self.assertEqual(result, [])
        combined = ' '.join(cm.output)
        self.assertIn('artifacts', combined)
        self.assertIn("['active']", combined)
        self.assertIn('system', combined)
        self.assertIn('img.qcow2', combined)


# ---------------------------------------------------------------------------
# _direct_find_networks
# ---------------------------------------------------------------------------

class DirectFindNetworksTestCase(base.ShakenFistTestCase):
    """Tests for _direct_find_networks()."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_all_filters_returns_match(self, mock_get_engine):
        """All three filters present — matching row returned."""
        row = _network_row(namespace='system', name='testnet')
        mock_get_engine.return_value = _make_engine_mock([row])

        criteria = ObjectFilterCriteria(
            states=['active'], namespace='system', name='testnet')
        result = mariadb._direct_find_networks(criteria)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].namespace, 'system')
        self.assertEqual(result[0].name, 'testnet')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_state_filter_only(self, mock_get_engine):
        """states only — no namespace/name filter."""
        mock_get_engine.return_value = _make_engine_mock([_network_row()])

        result = mariadb._direct_find_networks(
            ObjectFilterCriteria(states=['active']))

        self.assertEqual(len(result), 1)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_namespace_filter_only(self, mock_get_engine):
        """namespace only — no state/name filter."""
        mock_get_engine.return_value = _make_engine_mock(
            [_network_row(namespace='ns2')])

        result = mariadb._direct_find_networks(
            ObjectFilterCriteria(namespace='ns2'))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].namespace, 'ns2')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_name_filter_only(self, mock_get_engine):
        """name only — no state/namespace filter."""
        mock_get_engine.return_value = _make_engine_mock(
            [_network_row(name='mynet')])

        result = mariadb._direct_find_networks(
            ObjectFilterCriteria(name='mynet'))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'mynet')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_filters_returns_all(self, mock_get_engine):
        """No filters — all rows returned."""
        rows = [_network_row(net_uuid=uuid.uuid4(), vxid=i)
                for i in range(3)]
        mock_get_engine.return_value = _make_engine_mock(rows)

        result = mariadb._direct_find_networks(ObjectFilterCriteria())

        self.assertEqual(len(result), 3)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_empty_states_list_returns_all(self, mock_get_engine):
        """states=[] — no state filter, all rows returned."""
        rows = [_network_row(net_uuid=uuid.uuid4(), vxid=i)
                for i in range(2)]
        mock_get_engine.return_value = _make_engine_mock(rows)

        result = mariadb._direct_find_networks(
            ObjectFilterCriteria(states=[]))

        self.assertEqual(len(result), 2)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_match_returns_empty(self, mock_get_engine):
        """No matching rows — []."""
        mock_get_engine.return_value = _make_engine_mock([])

        result = mariadb._direct_find_networks(
            ObjectFilterCriteria(states=['deleted']))

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_mismatched_namespace_returns_empty(self, mock_get_engine):
        """Mismatched namespace — [] without error."""
        mock_get_engine.return_value = _make_engine_mock([])

        result = mariadb._direct_find_networks(
            ObjectFilterCriteria(namespace='no-such-ns'))

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_empty_table_returns_empty(self, mock_get_engine):
        """Empty table — []."""
        mock_get_engine.return_value = _make_engine_mock([])

        result = mariadb._direct_find_networks(ObjectFilterCriteria())

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_operational_error_logs_and_returns_empty(
            self, mock_get_engine):
        """OperationalError — logs warning with all criteria and returns []."""
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = OperationalError(
            'statement', {}, Exception('DB gone'))
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        criteria = ObjectFilterCriteria(
            states=['active'], namespace='ns1', name='mynet')

        with self.assertLogs('shakenfist.mariadb', level='WARNING') as cm:
            result = mariadb._direct_find_networks(criteria)

        self.assertEqual(result, [])
        combined = ' '.join(cm.output)
        self.assertIn('networks', combined)
        self.assertIn("['active']", combined)
        self.assertIn('ns1', combined)
        self.assertIn('mynet', combined)


# ---------------------------------------------------------------------------
# _direct_find_instances
# ---------------------------------------------------------------------------

class DirectFindInstancesTestCase(base.ShakenFistTestCase):
    """Tests for _direct_find_instances()."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_all_filters_returns_match(self, mock_get_engine):
        """All three filters present — matching row returned."""
        row = _instance_row(namespace='system', name='vm1')
        mock_get_engine.return_value = _make_engine_mock([row])

        criteria = ObjectFilterCriteria(
            states=['active'], namespace='system', name='vm1')
        result = mariadb._direct_find_instances(criteria)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].namespace, 'system')
        self.assertEqual(result[0].name, 'vm1')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_state_filter_only(self, mock_get_engine):
        """states only — no namespace/name filter."""
        mock_get_engine.return_value = _make_engine_mock([_instance_row()])

        result = mariadb._direct_find_instances(
            ObjectFilterCriteria(states=['active']))

        self.assertEqual(len(result), 1)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_namespace_filter_only(self, mock_get_engine):
        """namespace only — no state/name filter."""
        mock_get_engine.return_value = _make_engine_mock(
            [_instance_row(namespace='ns3')])

        result = mariadb._direct_find_instances(
            ObjectFilterCriteria(namespace='ns3'))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].namespace, 'ns3')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_name_filter_only(self, mock_get_engine):
        """name only — no state/namespace filter."""
        mock_get_engine.return_value = _make_engine_mock(
            [_instance_row(name='myvm')])

        result = mariadb._direct_find_instances(
            ObjectFilterCriteria(name='myvm'))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'myvm')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_filters_returns_all(self, mock_get_engine):
        """No filters — all rows returned."""
        rows = [_instance_row(inst_uuid=uuid.uuid4()) for _ in range(4)]
        mock_get_engine.return_value = _make_engine_mock(rows)

        result = mariadb._direct_find_instances(ObjectFilterCriteria())

        self.assertEqual(len(result), 4)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_empty_states_list_returns_all(self, mock_get_engine):
        """states=[] — no state filter, all rows returned."""
        rows = [_instance_row(inst_uuid=uuid.uuid4()) for _ in range(2)]
        mock_get_engine.return_value = _make_engine_mock(rows)

        result = mariadb._direct_find_instances(
            ObjectFilterCriteria(states=[]))

        self.assertEqual(len(result), 2)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_match_returns_empty(self, mock_get_engine):
        """No matching rows — []."""
        mock_get_engine.return_value = _make_engine_mock([])

        result = mariadb._direct_find_instances(
            ObjectFilterCriteria(states=['deleted']))

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_mismatched_namespace_returns_empty(self, mock_get_engine):
        """Mismatched namespace — [] without error."""
        mock_get_engine.return_value = _make_engine_mock([])

        result = mariadb._direct_find_instances(
            ObjectFilterCriteria(namespace='no-such-ns'))

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_empty_table_returns_empty(self, mock_get_engine):
        """Empty table — []."""
        mock_get_engine.return_value = _make_engine_mock([])

        result = mariadb._direct_find_instances(ObjectFilterCriteria())

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_operational_error_logs_and_returns_empty(
            self, mock_get_engine):
        """OperationalError — logs warning with all criteria and returns []."""
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = OperationalError(
            'statement', {}, Exception('DB gone'))
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        criteria = ObjectFilterCriteria(
            states=['active'], namespace='ns1', name='myvm')

        with self.assertLogs('shakenfist.mariadb', level='WARNING') as cm:
            result = mariadb._direct_find_instances(criteria)

        self.assertEqual(result, [])
        combined = ' '.join(cm.output)
        self.assertIn('instances', combined)
        self.assertIn("['active']", combined)
        self.assertIn('ns1', combined)
        self.assertIn('myvm', combined)


# ---------------------------------------------------------------------------
# Public wrappers — routing (direct vs gRPC)
# ---------------------------------------------------------------------------

class FindArtifactsPublicTestCase(base.ShakenFistTestCase):
    """Tests for find_artifacts() public wrapper routing."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._direct_find_artifacts')
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_routes_to_direct(self, _mock_uds, mock_direct):
        """_use_database_service=False → _direct_find_artifacts called."""
        mock_direct.return_value = []
        criteria = ObjectFilterCriteria(states=['active'])
        result = mariadb.find_artifacts(criteria)
        mock_direct.assert_called_once_with(criteria)
        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._grpc_find_artifacts')
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_routes_to_grpc(self, _mock_uds, mock_grpc):
        """_use_database_service=True → _grpc_find_artifacts called."""
        mock_grpc.return_value = []
        criteria = ObjectFilterCriteria(namespace='system')
        result = mariadb.find_artifacts(criteria)
        mock_grpc.assert_called_once_with(criteria)
        self.assertEqual(result, [])


class FindNetworksPublicTestCase(base.ShakenFistTestCase):
    """Tests for find_networks() public wrapper routing."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._direct_find_networks')
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_routes_to_direct(self, _mock_uds, mock_direct):
        """_use_database_service=False → _direct_find_networks called."""
        mock_direct.return_value = []
        criteria = ObjectFilterCriteria(name='net1')
        result = mariadb.find_networks(criteria)
        mock_direct.assert_called_once_with(criteria)
        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._grpc_find_networks')
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_routes_to_grpc(self, _mock_uds, mock_grpc):
        """_use_database_service=True → _grpc_find_networks called."""
        mock_grpc.return_value = []
        criteria = ObjectFilterCriteria(states=['active'])
        result = mariadb.find_networks(criteria)
        mock_grpc.assert_called_once_with(criteria)
        self.assertEqual(result, [])


class FindInstancesPublicTestCase(base.ShakenFistTestCase):
    """Tests for find_instances() public wrapper routing."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._direct_find_instances')
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_routes_to_direct(self, _mock_uds, mock_direct):
        """_use_database_service=False → _direct_find_instances called."""
        mock_direct.return_value = []
        criteria = ObjectFilterCriteria(namespace='ns1', name='vm1')
        result = mariadb.find_instances(criteria)
        mock_direct.assert_called_once_with(criteria)
        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._grpc_find_instances')
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_routes_to_grpc(self, _mock_uds, mock_grpc):
        """_use_database_service=True → _grpc_find_instances called."""
        mock_grpc.return_value = []
        criteria = ObjectFilterCriteria(states=['active'], namespace='ns1')
        result = mariadb.find_instances(criteria)
        mock_grpc.assert_called_once_with(criteria)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# gRPC wrappers — proto conversion smoke tests (no engine interaction)
# ---------------------------------------------------------------------------

class GrpcFindArtifactsTestCase(base.ShakenFistTestCase):
    """Smoke tests for _grpc_find_artifacts() proto conversion."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_criteria_converted_to_proto(self, mock_stub, mock_grpc_call):
        """Criteria fields are forwarded to the proto request."""
        from shakenfist.protos import database_pb2
        mock_reply = mock.MagicMock()
        mock_reply.artifacts = []
        mock_grpc_call.return_value = mock_reply

        criteria = ObjectFilterCriteria(
            states=['active'], namespace='system', name='img.qcow2')
        result = mariadb._grpc_find_artifacts(criteria)

        self.assertEqual(result, [])
        mock_grpc_call.assert_called_once()
        call_args = mock_grpc_call.call_args[0]
        request = call_args[1]
        self.assertIsInstance(
            request, database_pb2.FindArtifactsRequest)
        self.assertIn('active', list(request.criteria.states))
        self.assertEqual(request.criteria.namespace, 'system')
        self.assertEqual(request.criteria.name, 'img.qcow2')

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_none_states_becomes_empty_proto_list(
            self, mock_stub, mock_grpc_call):
        """states=None maps to empty proto repeated field."""
        mock_reply = mock.MagicMock()
        mock_reply.artifacts = []
        mock_grpc_call.return_value = mock_reply

        criteria = ObjectFilterCriteria()
        mariadb._grpc_find_artifacts(criteria)

        request = mock_grpc_call.call_args[0][1]
        self.assertEqual(list(request.criteria.states), [])


class GrpcFindNetworksTestCase(base.ShakenFistTestCase):
    """Smoke tests for _grpc_find_networks() proto conversion."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_criteria_converted_to_proto(self, mock_stub, mock_grpc_call):
        """Criteria fields forwarded to FindNetworksRequest."""
        from shakenfist.protos import database_pb2
        mock_reply = mock.MagicMock()
        mock_reply.networks = []
        mock_grpc_call.return_value = mock_reply

        criteria = ObjectFilterCriteria(
            states=['active'], namespace='ns1', name='net1')
        mariadb._grpc_find_networks(criteria)

        request = mock_grpc_call.call_args[0][1]
        self.assertIsInstance(request, database_pb2.FindNetworksRequest)
        self.assertIn('active', list(request.criteria.states))
        self.assertEqual(request.criteria.namespace, 'ns1')
        self.assertEqual(request.criteria.name, 'net1')


class GrpcFindInstancesTestCase(base.ShakenFistTestCase):
    """Smoke tests for _grpc_find_instances() proto conversion."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_criteria_converted_to_proto(self, mock_stub, mock_grpc_call):
        """Criteria fields forwarded to FindInstancesRequest."""
        from shakenfist.protos import database_pb2
        mock_reply = mock.MagicMock()
        mock_reply.instances = []
        mock_grpc_call.return_value = mock_reply

        criteria = ObjectFilterCriteria(
            states=['initial', 'active'], namespace='system')
        mariadb._grpc_find_instances(criteria)

        request = mock_grpc_call.call_args[0][1]
        self.assertIsInstance(request, database_pb2.FindInstancesRequest)
        self.assertIn('active', list(request.criteria.states))
        self.assertIn('initial', list(request.criteria.states))
        self.assertEqual(request.criteria.namespace, 'system')
