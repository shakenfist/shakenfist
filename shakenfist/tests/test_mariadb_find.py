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
# - find_network_vxids() — the stray vxlan reaper's claim lookup, whose
#   error contract is the inverse of every other finder here: it must
#   raise rather than return {}, because an unclaimed vxid authorises
#   deletion of host network devices

import collections
from unittest import mock
import uuid

import grpc
from sqlalchemy.exc import OperationalError

from shakenfist import mariadb
from shakenfist.config import BaseSettings
from shakenfist.schema.object_filter import ObjectFilterCriteria
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    MARIADB_GATEWAY_HOSTS: list[str] = ['192.168.1.1']
    MARIADB_GATEWAY_PORT: int = 13005
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


# ---------------------------------------------------------------------------
# _direct_find_network_interfaces
# ---------------------------------------------------------------------------

NI_UUID = uuid.uuid4()
NI_NETWORK_UUID = uuid.uuid4()
NI_INSTANCE_UUID = uuid.uuid4()


def _ni_row(
        ni_uuid=None, network_uuid=None, instance_uuid=None,
        macaddr='52:54:00:ab:cd:ef', ipv4='10.0.0.2',
        order=0, model='virtio', version=1):
    """Return a mock DB row for the network_interfaces table."""
    row = mock.MagicMock()
    row.uuid = ni_uuid or NI_UUID
    row.network_uuid = network_uuid or NI_NETWORK_UUID
    row.instance_uuid = instance_uuid or NI_INSTANCE_UUID
    row.macaddr = macaddr
    row.ipv4 = ipv4
    row.order = order
    row.model = model
    row.version = version
    return row


class DirectFindNetworkInterfacesTestCase(base.ShakenFistTestCase):
    """Tests for _direct_find_network_interfaces()."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_all_filters_returns_match(self, mock_get_engine):
        """State filter present — matching row returned (namespace/name no-ops)."""
        row = _ni_row()
        mock_get_engine.return_value = _make_engine_mock([row])

        criteria = ObjectFilterCriteria(
            states=['created'], namespace='tenant-a', name='eth0')
        result = mariadb._direct_find_network_interfaces(criteria)

        self.assertEqual(len(result), 1)
        self.assertEqual(str(result[0].uuid), str(NI_UUID))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_state_filter_only(self, mock_get_engine):
        """states only — no namespace/name filter."""
        mock_get_engine.return_value = _make_engine_mock([_ni_row()])

        result = mariadb._direct_find_network_interfaces(
            ObjectFilterCriteria(states=['created']))

        self.assertEqual(len(result), 1)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_filters_returns_all(self, mock_get_engine):
        """No filters — all rows returned."""
        rows = [_ni_row(ni_uuid=uuid.uuid4(), order=i) for i in range(3)]
        mock_get_engine.return_value = _make_engine_mock(rows)

        result = mariadb._direct_find_network_interfaces(
            ObjectFilterCriteria())

        self.assertEqual(len(result), 3)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_empty_states_list_returns_all(self, mock_get_engine):
        """states=[] — no state filter, all rows returned."""
        rows = [_ni_row(ni_uuid=uuid.uuid4(), order=i) for i in range(2)]
        mock_get_engine.return_value = _make_engine_mock(rows)

        result = mariadb._direct_find_network_interfaces(
            ObjectFilterCriteria(states=[]))

        self.assertEqual(len(result), 2)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_match_returns_empty(self, mock_get_engine):
        """Query returns no rows — result is []."""
        mock_get_engine.return_value = _make_engine_mock([])

        result = mariadb._direct_find_network_interfaces(
            ObjectFilterCriteria(states=['deleted']))

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_empty_table_returns_empty(self, mock_get_engine):
        """Empty table — []."""
        mock_get_engine.return_value = _make_engine_mock([])

        result = mariadb._direct_find_network_interfaces(
            ObjectFilterCriteria())

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_operational_error_logs_and_returns_empty(self, mock_get_engine):
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
            states=['created'], namespace='tenant-a', name='eth0')

        with self.assertLogs('shakenfist.mariadb', level='WARNING') as cm:
            result = mariadb._direct_find_network_interfaces(criteria)

        self.assertEqual(result, [])
        combined = ' '.join(cm.output)
        self.assertIn('network_interfaces', combined)
        self.assertIn("['created']", combined)
        self.assertIn('tenant-a', combined)
        self.assertIn('eth0', combined)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_namespace_in_criteria_is_silently_ignored(self, mock_get_engine):
        """namespace in criteria is stripped — result equals namespace=None call."""
        rows = [_ni_row(ni_uuid=uuid.uuid4(), order=i) for i in range(2)]
        mock_get_engine.return_value = _make_engine_mock(rows)

        # Calling with namespace set and without should hit the same SQL path
        # because the helper strips namespace to None before building the query.
        # Both calls see the same mock rows, so both results must be equal.
        result_with_ns = mariadb._direct_find_network_interfaces(
            ObjectFilterCriteria(states=['created'], namespace='tenant-a'))

        mock_get_engine.return_value = _make_engine_mock(rows)
        result_without_ns = mariadb._direct_find_network_interfaces(
            ObjectFilterCriteria(states=['created']))

        self.assertEqual(len(result_with_ns), len(result_without_ns))
        self.assertEqual(
            [str(r.uuid) for r in result_with_ns],
            [str(r.uuid) for r in result_without_ns],
        )

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_name_in_criteria_is_silently_ignored(self, mock_get_engine):
        """name in criteria is stripped — result equals name=None call."""
        rows = [_ni_row(ni_uuid=uuid.uuid4(), order=i) for i in range(2)]
        mock_get_engine.return_value = _make_engine_mock(rows)

        result_with_name = mariadb._direct_find_network_interfaces(
            ObjectFilterCriteria(states=['created'], name='eth0'))

        mock_get_engine.return_value = _make_engine_mock(rows)
        result_without_name = mariadb._direct_find_network_interfaces(
            ObjectFilterCriteria(states=['created']))

        self.assertEqual(len(result_with_name), len(result_without_name))
        self.assertEqual(
            [str(r.uuid) for r in result_with_name],
            [str(r.uuid) for r in result_without_name],
        )

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_network_uuid_filter(self, mock_get_engine):
        """network_uuid in criteria adds a WHERE clause on network_uuid."""
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [_ni_row()]
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        target_uuid = str(uuid.uuid4())
        result = mariadb._direct_find_network_interfaces(
            ObjectFilterCriteria(network_uuid=target_uuid))

        self.assertEqual(len(result), 1)
        # Inspect the compiled SQL to confirm the WHERE clause is present.
        stmt = mock_conn.execute.call_args[0][0]
        rendered = str(
            stmt.compile(compile_kwargs={'literal_binds': False}))
        self.assertIn('network_uuid', rendered)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_instance_uuid_filter(self, mock_get_engine):
        """instance_uuid in criteria adds a WHERE clause on instance_uuid."""
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [_ni_row()]
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        target_uuid = str(uuid.uuid4())
        result = mariadb._direct_find_network_interfaces(
            ObjectFilterCriteria(instance_uuid=target_uuid))

        self.assertEqual(len(result), 1)
        stmt = mock_conn.execute.call_args[0][0]
        rendered = str(
            stmt.compile(compile_kwargs={'literal_binds': False}))
        self.assertIn('instance_uuid', rendered)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_results_are_ordered_by_order_column(self, mock_get_engine):
        """SELECT carries ORDER BY ``order`` so callers iterate
        interfaces in user-specified order."""
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [_ni_row()]
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        mariadb._direct_find_network_interfaces(
            ObjectFilterCriteria(states=['created']))

        stmt = mock_conn.execute.call_args[0][0]
        rendered = str(
            stmt.compile(compile_kwargs={'literal_binds': False}))
        self.assertIn('ORDER BY', rendered)
        # ``order`` is a SQL reserved word — the rendered statement
        # should reference the column, however the dialect chooses to
        # quote it.
        self.assertIn('order', rendered)


# ---------------------------------------------------------------------------
# Public wrapper — routing (direct vs gRPC)
# ---------------------------------------------------------------------------

class FindNetworkInterfacesPublicTestCase(base.ShakenFistTestCase):
    """Tests for find_network_interfaces() public wrapper routing."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._direct_find_network_interfaces')
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_routes_to_direct(self, _mock_uds, mock_direct):
        """_use_database_service=False → _direct_find_network_interfaces called."""
        mock_direct.return_value = []
        criteria = ObjectFilterCriteria(states=['created'])
        result = mariadb.find_network_interfaces(criteria)
        mock_direct.assert_called_once_with(criteria)
        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._grpc_find_network_interfaces')
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_routes_to_grpc(self, _mock_uds, mock_grpc):
        """_use_database_service=True → _grpc_find_network_interfaces called."""
        mock_grpc.return_value = []
        criteria = ObjectFilterCriteria(namespace='tenant-a')
        result = mariadb.find_network_interfaces(criteria)
        mock_grpc.assert_called_once_with(criteria)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# gRPC wrapper — proto conversion smoke test
# ---------------------------------------------------------------------------

class GrpcFindNetworkInterfacesTestCase(base.ShakenFistTestCase):
    """Smoke tests for _grpc_find_network_interfaces() proto conversion."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_criteria_converted_to_proto(self, mock_stub, mock_grpc_call):
        """Criteria fields (states, namespace, name) forwarded to proto request."""
        from shakenfist.protos import database_pb2
        mock_reply = mock.MagicMock()
        mock_reply.network_interfaces = []
        mock_grpc_call.return_value = mock_reply

        criteria = ObjectFilterCriteria(
            states=['created'], namespace='tenant-a', name='eth0')
        result = mariadb._grpc_find_network_interfaces(criteria)

        self.assertEqual(result, [])
        mock_grpc_call.assert_called_once()
        request = mock_grpc_call.call_args[0][1]
        self.assertIsInstance(
            request, database_pb2.FindNetworkInterfacesRequest)
        self.assertIn('created', list(request.criteria.states))
        self.assertEqual(request.criteria.namespace, 'tenant-a')
        self.assertEqual(request.criteria.name, 'eth0')


# ---------------------------------------------------------------------------
# find_network_vxids — the stray vxlan reaper's claim lookup
# ---------------------------------------------------------------------------

class DirectFindNetworkVxidsTestCase(base.ShakenFistTestCase):
    """Tests for _direct_find_network_vxids().

    This finder is deliberately unlike every other one in this module:
    its caller (the stray vxlan reaper in the network maintainer) reads
    an absent vxid as permission to delete host network devices, so a
    database failure must raise rather than present as "nothing claims
    these vxids". The sibling finders above each have a
    test_operational_error case asserting the *opposite* contract, so
    this test is what stands between a well-meaning refactor which makes
    them all consistent and a database outage deleting cluster
    networking.
    """

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_empty_input_does_not_touch_the_database(self, mock_get_engine):
        """No candidates means no query."""
        self.assertEqual({}, mariadb._direct_find_network_vxids([]))
        mock_get_engine.assert_not_called()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_claims_are_mapped_to_network_uuids(self, mock_get_engine):
        """Claimed vxids map to the uuid of the claiming network, and
        vxids nothing claims are simply absent."""
        row = mock.MagicMock()
        row.vxid = 42
        row.uuid = NETWORK_UUID
        mock_get_engine.return_value = _make_engine_mock([row])

        result = mariadb._direct_find_network_vxids([42, 43])

        self.assertEqual({42: str(NETWORK_UUID)}, result)
        self.assertNotIn(43, result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_claims_returns_empty(self, mock_get_engine):
        """A vxid no network holds is genuinely unclaimed."""
        mock_get_engine.return_value = _make_engine_mock([])
        self.assertEqual({}, mariadb._direct_find_network_vxids([42]))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_operational_error_propagates(self, mock_get_engine):
        """A database failure must NOT be swallowed into an empty
        result. An empty result here authorises device deletion."""
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = OperationalError(
            'statement', {}, Exception('DB gone'))
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        self.assertRaises(
            OperationalError, mariadb._direct_find_network_vxids, [1, 2])


class FindNetworkVxidsPublicTestCase(base.ShakenFistTestCase):
    """Tests for find_network_vxids() public wrapper routing."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._direct_find_network_vxids')
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_routes_to_direct(self, _mock_uds, mock_direct):
        mock_direct.return_value = {}
        self.assertEqual({}, mariadb.find_network_vxids([42]))
        mock_direct.assert_called_once_with([42])

    @mock.patch('shakenfist.mariadb._grpc_find_network_vxids')
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_routes_to_grpc(self, _mock_uds, mock_grpc):
        mock_grpc.return_value = {}
        self.assertEqual({}, mariadb.find_network_vxids([42]))
        mock_grpc.assert_called_once_with([42])

    @mock.patch('shakenfist.mariadb._use_database_service')
    def test_empty_input_short_circuits(self, mock_uds):
        self.assertEqual({}, mariadb.find_network_vxids([]))
        mock_uds.assert_not_called()


class GrpcFindNetworkVxidsTestCase(base.ShakenFistTestCase):
    """Smoke tests for _grpc_find_network_vxids() proto conversion."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_claims_converted_from_proto(self, mock_stub, mock_grpc_call):
        from shakenfist.protos import database_pb2

        reply = database_pb2.FindNetworkVxidsReply(
            claims=[database_pb2.NetworkVxidClaim(
                vxid=42, uuid=str(NETWORK_UUID))])
        mock_grpc_call.return_value = reply

        result = mariadb._grpc_find_network_vxids([42, 43])

        self.assertEqual({42: str(NETWORK_UUID)}, result)
        request = mock_grpc_call.call_args[0][1]
        self.assertIsInstance(request, database_pb2.FindNetworkVxidsRequest)
        self.assertEqual([42, 43], list(request.vxids))

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_rpc_error_propagates(self, mock_stub, mock_grpc_call):
        """As with the direct path, an error must not present as "these
        vxids are unclaimed"."""
        mock_grpc_call.side_effect = Exception('UNIMPLEMENTED')
        self.assertRaises(
            Exception, mariadb._grpc_find_network_vxids, [42])


class ServicerFindNetworkVxidsTestCase(base.ShakenFistTestCase):
    """Tests for the DatabaseService.FindNetworkVxids servicer method.

    The reply proto has no way to say "the query failed" -- an empty
    claims list is indistinguishable from a successful lookup which
    found nothing -- so the failure path has to set a non-OK status,
    otherwise the client reads the failure as an answer and deletes
    devices on the strength of it.
    """

    def _servicer(self):
        from shakenfist.daemons.database.main import DatabaseService
        monitor = mock.MagicMock()
        monitor.counters = collections.defaultdict(mock.MagicMock)
        return DatabaseService(monitor)

    @mock.patch('shakenfist.mariadb._direct_find_network_vxids')
    def test_claims_returned(self, mock_direct):
        mock_direct.return_value = {42: str(NETWORK_UUID)}
        context = mock.MagicMock()

        from shakenfist.protos import database_pb2
        reply = self._servicer().FindNetworkVxids(
            database_pb2.FindNetworkVxidsRequest(vxids=[42, 43]), context)

        mock_direct.assert_called_once_with([42, 43])
        self.assertEqual(1, len(reply.claims))
        self.assertEqual(42, reply.claims[0].vxid)
        self.assertEqual(str(NETWORK_UUID), reply.claims[0].uuid)
        context.set_code.assert_not_called()

    @mock.patch('shakenfist.mariadb._direct_find_network_vxids')
    def test_failure_sets_internal_status(self, mock_direct):
        mock_direct.side_effect = OperationalError(
            'statement', {}, Exception('DB gone'))
        context = mock.MagicMock()

        from shakenfist.protos import database_pb2
        reply = self._servicer().FindNetworkVxids(
            database_pb2.FindNetworkVxidsRequest(vxids=[42]), context)

        self.assertEqual([], list(reply.claims))
        context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)
        context.set_details.assert_called_once()


# ---------------------------------------------------------------------------
# End-to-end JOIN regression — runs the actual SQL against in-memory SQLite.
# ---------------------------------------------------------------------------

class BuildObjectFilterQueryJoinTestCase(base.ShakenFistTestCase):
    """Run ``_build_object_filter_query`` against a real engine.

    The mocking-heavy tests above never execute the rendered SQL, so they
    cannot catch a JOIN whose WHERE clause silently matches zero rows.
    Phase 1 of the SQL-pushdown work introduced exactly that bug:
    ``object_states.object_uuid`` is ``VARCHAR(36)`` (with dashes) but
    every per-type ``uuid`` column is ``sa.Uuid()`` which renders as
    ``CHAR(32)`` (no dashes) on MariaDB. The CI smoke tests broke because
    the resulting JOIN never matched anything, making
    ``Artifact.from_url`` return ``None`` and every instance creation
    flow fail with 404 ``artifact not found``.

    These tests build the same schema in-memory, insert a row through
    each path the way the real code does, and assert the JOIN returns
    the row.
    """

    def _build_engine(self):
        import sqlalchemy as sa  # noqa: F401  (import locally so the rest
        # of the module's mocked tests stay independent of SQLAlchemy
        # state).

        # Reset module-level table caches so the new in-memory engine
        # gets a fresh metadata.
        for attr in (
                '_object_states_table',
                '_artifacts_table',
                '_instances_table',
                '_networks_table',
                '_network_interfaces_table'):
            setattr(mariadb, attr, None)
        mariadb._metadata = None

        engine = sa.create_engine('sqlite:///:memory:')
        # Build the tables we need.
        states = mariadb._get_object_states_table()
        artifacts = mariadb._get_artifacts_table()
        nis = mariadb._get_network_interfaces_table()
        states.metadata.create_all(engine, tables=[states, artifacts, nis])
        return engine, states, artifacts, nis

    def test_join_matches_artifact_row(self):
        from shakenfist.schema.object_types import ObjectType
        import sqlalchemy as sa

        engine, states, artifacts, _ = self._build_engine()
        a_uuid = uuid.uuid4()
        with engine.connect() as conn:
            conn.execute(sa.insert(artifacts).values(
                uuid=a_uuid,
                artifact_type='image',
                source_url='sf://upload/system/debian-12',
                name='debian-12',
                namespace='system',
                version=9))
            # ``mariadb.set_state`` writes the dashed string form.
            conn.execute(sa.insert(states).values(
                object_uuid=str(a_uuid),
                object_type='artifact',
                state_value='created',
                update_time=0.0,
                message=None))
            conn.commit()

            criteria = ObjectFilterCriteria(states=['created'])
            stmt = mariadb._build_object_filter_query(
                artifacts, ObjectType.ARTIFACT, criteria)
            rows = conn.execute(stmt).fetchall()

        self.assertEqual(1, len(rows))
        self.assertEqual('debian-12', rows[0].name)

    def test_join_excludes_other_object_types(self):
        from shakenfist.schema.object_types import ObjectType
        import sqlalchemy as sa

        engine, states, artifacts, _ = self._build_engine()
        a_uuid = uuid.uuid4()
        with engine.connect() as conn:
            conn.execute(sa.insert(artifacts).values(
                uuid=a_uuid, artifact_type='image',
                source_url='x', name='x', namespace='ns', version=9))
            # Same UUID but stored under a different object_type — the
            # JOIN must not pick this up.
            conn.execute(sa.insert(states).values(
                object_uuid=str(a_uuid), object_type='instance',
                state_value='created', update_time=0.0, message=None))
            conn.commit()

            criteria = ObjectFilterCriteria(states=['created'])
            stmt = mariadb._build_object_filter_query(
                artifacts, ObjectType.ARTIFACT, criteria)
            rows = conn.execute(stmt).fetchall()

        self.assertEqual(0, len(rows))


class DirectGetObjectsByStateTestCase(base.ShakenFistTestCase):
    """``_direct_get_objects_by_state`` empty-states semantics.

    ``Nodes([])`` (no prefilter) resolves to an empty state list and
    relies on the default iterator's ``get_objects_by_state`` path. An
    empty list must mean "no state filter — return every object of this
    type" (preserving the pre-phase-5 ``Nodes([])`` semantics that
    returned every node, including DELETED). This was missed in the
    initial port and surfaced as ``test_metadata.TestNodeMetadata``
    failing with ``IndexError`` because ``get_nodes()`` came back empty.
    """

    def _build_engine(self):
        import sqlalchemy as sa

        for attr in (
                '_object_states_table',
                '_artifacts_table',
                '_instances_table',
                '_networks_table',
                '_network_interfaces_table'):
            setattr(mariadb, attr, None)
        mariadb._metadata = None

        engine = sa.create_engine('sqlite:///:memory:')
        states = mariadb._get_object_states_table()
        states.metadata.create_all(engine, tables=[states])
        return engine, states

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_empty_states_returns_all_for_object_type(
            self, mock_get_engine):
        from shakenfist.schema.object_types import ObjectType
        import sqlalchemy as sa

        engine, states = self._build_engine()
        mock_get_engine.return_value = engine

        with engine.connect() as conn:
            for state_value in ('created', 'deleted', 'error'):
                conn.execute(sa.insert(states).values(
                    object_uuid=str(uuid.uuid4()),
                    object_type='node',
                    state_value=state_value,
                    update_time=0.0,
                    message=None))
            # Different object_type — must not be returned.
            conn.execute(sa.insert(states).values(
                object_uuid=str(uuid.uuid4()),
                object_type='instance',
                state_value='created',
                update_time=0.0,
                message=None))
            conn.commit()

        result = mariadb._direct_get_objects_by_state(ObjectType.NODE, [])
        self.assertEqual(3, len(result))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_state_filter_still_applies_when_provided(
            self, mock_get_engine):
        from shakenfist.schema.object_types import ObjectType
        import sqlalchemy as sa

        engine, states = self._build_engine()
        mock_get_engine.return_value = engine

        with engine.connect() as conn:
            for state_value in ('created', 'deleted', 'error'):
                conn.execute(sa.insert(states).values(
                    object_uuid=str(uuid.uuid4()),
                    object_type='node',
                    state_value=state_value,
                    update_time=0.0,
                    message=None))
            conn.commit()

        result = mariadb._direct_get_objects_by_state(
            ObjectType.NODE, ['created'])
        self.assertEqual(1, len(result))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_updated_before_filters_young_rows(self, mock_get_engine):
        from shakenfist.schema.object_types import ObjectType
        import sqlalchemy as sa

        engine, states = self._build_engine()
        mock_get_engine.return_value = engine

        old_uuid = str(uuid.uuid4())
        with engine.connect() as conn:
            conn.execute(sa.insert(states).values(
                object_uuid=old_uuid,
                object_type='node',
                state_value='deleted',
                update_time=100.0,
                message=None))
            conn.execute(sa.insert(states).values(
                object_uuid=str(uuid.uuid4()),
                object_type='node',
                state_value='deleted',
                update_time=200.0,
                message=None))
            conn.commit()

        result = mariadb._direct_get_objects_by_state(
            ObjectType.NODE, ['deleted'], updated_before=150.0)
        self.assertEqual([old_uuid], result)


class NetworkInterfaceMacaddrUniquenessTestCase(base.ShakenFistTestCase):
    """The macaddr UNIQUE constraint is scoped to active interfaces.

    Background: ``test_interface_plug_and_exec_dhcp`` and
    ``test_interface_plug_and_exec_reboot`` both hard-code MAC
    ``02:00:00:ea:3a:28``. The dhcp test soft-deletes its interface
    on tearDown, but the cluster cleaner only runs on a
    ``CLEANER_DELAY`` schedule (default 1h) so the row stayed in
    ``network_interfaces`` for the entire CI run. With a global
    UNIQUE on ``macaddr`` the reboot test then failed to insert
    its hot-plug interface. The same wall hits operators
    redeploying VMs with stable MACs.

    The fix: the ``active`` column is NULLed when an interface
    transitions to ``deleted``, and the UNIQUE is on
    ``(macaddr, active, network_uuid)``. NULLs do not collide in
    MariaDB UNIQUE indexes, so soft-deleted rows do not block
    MAC reuse, while two simultaneously-active rows with the same
    MAC on the same network still error out as before.
    """

    def _build_engine(self):
        from shakenfist.schema.network_interface_data import (
            NetworkInterfaceData)
        import sqlalchemy as sa

        for attr in (
                '_object_states_table',
                '_object_metadata_table',
                '_network_interfaces_table',
                '_network_interface_attributes_table'):
            setattr(mariadb, attr, None)
        mariadb._metadata = None

        engine = sa.create_engine('sqlite:///:memory:')
        states = mariadb._get_object_states_table()
        nis = mariadb._get_network_interfaces_table()
        ni_attrs = mariadb._get_network_interface_attributes_table()
        states.metadata.create_all(
            engine, tables=[states, nis, ni_attrs])

        for idx in nis.indexes:
            idx.create(engine, checkfirst=True)
        # Recreate the UNIQUE constraint as an Index for SQLite — the
        # sa.UniqueConstraint attached to the Table is enforced by
        # CREATE TABLE on MariaDB but does not auto-create on SQLite
        # via metadata.create_all when added post-hoc.
        sa.Index(
            'uq_network_interfaces_macaddr_active_network',
            nis.c.macaddr,
            nis.c.active,
            nis.c.network_uuid,
            unique=True,
        ).create(engine, checkfirst=True)

        return engine, states, nis, ni_attrs, NetworkInterfaceData

    def _make_data(self, macaddr, network_uuid, NIData, ni_uuid=None):
        return NIData(
            uuid=ni_uuid or uuid.uuid4(),
            network_uuid=network_uuid,
            instance_uuid=uuid.uuid4(),
            macaddr=macaddr,
            ipv4='10.0.0.5',
            order=0,
            model='virtio',
            version=5,
        )

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_macaddr_reusable_when_active_is_null(self, mock_get_engine):
        """active=NULL on an existing row lets the MAC be inserted again."""
        import sqlalchemy as sa

        engine, _, nis, _, NIData = self._build_engine()
        mock_get_engine.return_value = engine

        net_uuid = uuid.uuid4()
        macaddr = '02:00:00:ea:3a:28'

        first = self._make_data(macaddr, net_uuid, NIData)
        self.assertTrue(mariadb._direct_create_network_interface(first))

        # Simulate ``_direct_set_state(INTERFACE, _, deleted)`` having
        # already nulled the flag. The state transition itself is
        # tested in DirectSetStateInterfaceDeletedHookTestCase below
        # because it relies on a MariaDB-specific INSERT ... ON
        # DUPLICATE KEY UPDATE that SQLite cannot compile.
        with engine.connect() as conn:
            conn.execute(sa.update(nis).where(
                nis.c.uuid == first.uuid).values(active=None))
            conn.commit()

        # Reuse the same MAC on the same network — must succeed.
        second = self._make_data(macaddr, net_uuid, NIData)
        self.assertTrue(mariadb._direct_create_network_interface(second))

        # The audit trail of the deleted row is preserved.
        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(nis.c.uuid, nis.c.active).where(
                    nis.c.macaddr == macaddr)
            ).fetchall()
        by_uuid = {str(r.uuid): r.active for r in rows}
        self.assertEqual(2, len(by_uuid))
        self.assertIsNone(by_uuid[str(first.uuid)])
        self.assertTrue(by_uuid[str(second.uuid)])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_macaddr_collision_among_active_interfaces_fails(
            self, mock_get_engine):
        """Two active interfaces with the same (mac, network) cannot coexist."""
        engine, _, _, _, NIData = self._build_engine()
        mock_get_engine.return_value = engine

        net_uuid = uuid.uuid4()
        macaddr = '02:00:00:ea:3a:29'

        first = self._make_data(macaddr, net_uuid, NIData)
        self.assertTrue(mariadb._direct_create_network_interface(first))

        # Without soft-deleting the first interface, a second insert
        # with the same (mac, network) must fail. Mirrors the
        # operator-error case "two VMs configured with the same MAC".
        second = self._make_data(macaddr, net_uuid, NIData)
        self.assertFalse(
            mariadb._direct_create_network_interface(second))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_macaddr_reuse_across_networks(self, mock_get_engine):
        """Same MAC on a different network is allowed.

        Different VXLAN networks are isolated broadcast domains, so
        a MAC clash across them does not break dnsmasq or ARP. The
        constraint reflects that.
        """
        engine, _, _, _, NIData = self._build_engine()
        mock_get_engine.return_value = engine

        macaddr = '02:00:00:ea:3a:2a'

        first = self._make_data(macaddr, uuid.uuid4(), NIData)
        self.assertTrue(mariadb._direct_create_network_interface(first))

        second = self._make_data(macaddr, uuid.uuid4(), NIData)
        self.assertTrue(mariadb._direct_create_network_interface(second))


class DirectSetStateInterfaceDeletedHookTestCase(base.ShakenFistTestCase):
    """``_direct_set_state`` clears active on INTERFACE -> deleted only.

    The hook is what makes MAC reuse possible: when an interface
    transitions to deleted, the row's ``active`` column is NULLed so
    the composite UNIQUE constraint stops counting it. This test
    runs against a mock engine because the production upsert uses
    MariaDB's INSERT ... ON DUPLICATE KEY UPDATE which SQLite cannot
    compile.
    """

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    def _make_engine(self):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        return mock_engine, mock_conn

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_interface_deleted_runs_active_null_update(
            self, mock_get_engine):
        from shakenfist.schema.object_state import State
        from shakenfist.schema.object_types import ObjectType

        engine, conn = self._make_engine()
        mock_get_engine.return_value = engine

        ni_uuid = uuid.uuid4()
        self.assertTrue(mariadb._direct_set_state(
            ObjectType.INTERFACE, str(ni_uuid),
            State(value='deleted', update_time=0.0)))

        # Two execute calls: the upsert into object_states and the
        # UPDATE on network_interfaces clearing active.
        executed_sql = [
            str(call.args[0]) for call in conn.execute.call_args_list
        ]
        self.assertEqual(2, len(executed_sql))
        update_sql = executed_sql[1]
        self.assertIn('UPDATE network_interfaces', update_sql)
        self.assertIn('active', update_sql)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_interface_other_state_does_not_touch_active(
            self, mock_get_engine):
        from shakenfist.schema.object_state import State
        from shakenfist.schema.object_types import ObjectType

        engine, conn = self._make_engine()
        mock_get_engine.return_value = engine

        ni_uuid = uuid.uuid4()
        self.assertTrue(mariadb._direct_set_state(
            ObjectType.INTERFACE, str(ni_uuid),
            State(value='created', update_time=0.0)))

        # Only the object_states upsert runs; no UPDATE on
        # network_interfaces.
        executed_sql = [
            str(call.args[0]) for call in conn.execute.call_args_list
        ]
        self.assertEqual(1, len(executed_sql))
        self.assertNotIn('network_interfaces', executed_sql[0])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_other_object_type_deleted_does_not_touch_active(
            self, mock_get_engine):
        """Network -> deleted must not touch network_interfaces."""
        from shakenfist.schema.object_state import State
        from shakenfist.schema.object_types import ObjectType

        engine, conn = self._make_engine()
        mock_get_engine.return_value = engine

        net_uuid = uuid.uuid4()
        self.assertTrue(mariadb._direct_set_state(
            ObjectType.NETWORK, str(net_uuid),
            State(value='deleted', update_time=0.0)))

        executed_sql = [
            str(call.args[0]) for call in conn.execute.call_args_list
        ]
        self.assertEqual(1, len(executed_sql))
        self.assertNotIn('network_interfaces', executed_sql[0])
