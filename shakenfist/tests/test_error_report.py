# Copyright 2019 Michael Still and contributors
#
# Tests for shakenfist.operations.error_report.

import uuid
from unittest import mock

from sqlalchemy.exc import OperationalError

from shakenfist import mariadb
from shakenfist.config import BaseSettings
from shakenfist.exceptions import AddFloatingIPFailed
from shakenfist.exceptions import CannotAssignFloatingGateway
from shakenfist.exceptions import CongestedNetwork
from shakenfist.exceptions import CreateNetworkNamespaceFailed
from shakenfist.exceptions import CreateVXLANInterfaceFailed
from shakenfist.exceptions import DeadNetwork
from shakenfist.exceptions import EnableNATFailed
from shakenfist.exceptions import EnsureMeshFailed
from shakenfist.exceptions import ListingInterfaceAddressesFailed
from shakenfist.exceptions import RemoveFloatingIPFailed
from shakenfist.operations.error_report import ErrorReport
from shakenfist.operations.error_report import _EXCEPTION_CODE_REGISTRY
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    MARIADB_GATEWAY_HOSTS: list[str] = ['192.168.1.1']
    MARIADB_GATEWAY_PORT: int = 13005
    MARIADB_HOST: str = 'localhost'
    NODE_NAME: str = 'testnode'


fake_config = FakeConfig()


class ErrorReportFromExceptionTestCase(base.ShakenFistTestCase):
    """``ErrorReport.from_exception`` maps registered exceptions correctly."""

    def test_ensure_mesh_failed_maps_to_network_ensure_mesh_failed(self):
        report = ErrorReport.from_exception(EnsureMeshFailed('boom'))
        self.assertEqual('network.ensure_mesh.failed', report.code)
        self.assertEqual('boom', report.message)
        self.assertEqual(
            'shakenfist.exceptions.EnsureMeshFailed', report.origin_class)

    def test_dead_network_maps_to_network_dead(self):
        report = ErrorReport.from_exception(DeadNetwork('network is dead'))
        self.assertEqual('network.dead', report.code)
        self.assertEqual('network is dead', report.message)
        self.assertEqual(
            'shakenfist.exceptions.DeadNetwork', report.origin_class)

    def test_create_vxlan_failed_maps_to_network_create_vxlan_failed(self):
        report = ErrorReport.from_exception(
            CreateVXLANInterfaceFailed('nope'))
        self.assertEqual('network.create_vxlan.failed', report.code)
        self.assertEqual(
            'shakenfist.exceptions.CreateVXLANInterfaceFailed',
            report.origin_class)

    def test_cannot_assign_floating_maps_to_floating_assign_failed(self):
        report = ErrorReport.from_exception(
            CannotAssignFloatingGateway('no addr'))
        self.assertEqual('network.floating.assign_failed', report.code)
        self.assertEqual(
            'shakenfist.exceptions.CannotAssignFloatingGateway',
            report.origin_class)

    def test_add_floating_ip_failed_maps_to_floating_add_failed(self):
        report = ErrorReport.from_exception(AddFloatingIPFailed('add failed'))
        self.assertEqual('network.floating.add_failed', report.code)
        self.assertEqual(
            'shakenfist.exceptions.AddFloatingIPFailed', report.origin_class)

    def test_remove_floating_ip_failed_maps_to_floating_remove_failed(self):
        report = ErrorReport.from_exception(
            RemoveFloatingIPFailed('remove failed'))
        self.assertEqual('network.floating.remove_failed', report.code)
        self.assertEqual(
            'shakenfist.exceptions.RemoveFloatingIPFailed',
            report.origin_class)

    def test_enable_nat_failed_maps_to_nat_enable_failed(self):
        report = ErrorReport.from_exception(EnableNATFailed('nat failed'))
        self.assertEqual('network.nat.enable_failed', report.code)
        self.assertEqual(
            'shakenfist.exceptions.EnableNATFailed', report.origin_class)

    def test_congested_network_maps_to_network_congested(self):
        report = ErrorReport.from_exception(CongestedNetwork('congested'))
        self.assertEqual('network.congested', report.code)
        self.assertEqual(
            'shakenfist.exceptions.CongestedNetwork', report.origin_class)

    def test_create_network_namespace_failed_maps_to_create_namespace_failed(self):
        report = ErrorReport.from_exception(
            CreateNetworkNamespaceFailed('ns failed'))
        self.assertEqual('network.create_namespace.failed', report.code)
        self.assertEqual(
            'shakenfist.exceptions.CreateNetworkNamespaceFailed',
            report.origin_class)

    def test_listing_interface_addresses_failed_maps_to_list_interface_addresses_failed(
            self):
        report = ErrorReport.from_exception(
            ListingInterfaceAddressesFailed('list failed'))
        self.assertEqual('network.list_interface_addresses.failed', report.code)
        self.assertEqual(
            'shakenfist.exceptions.ListingInterfaceAddressesFailed',
            report.origin_class)

    def test_registry_contains_exactly_ten_entries(self):
        self.assertEqual(10, len(_EXCEPTION_CODE_REGISTRY))

    def test_unregistered_exception_becomes_internal_unknown(self):
        report = ErrorReport.from_exception(ValueError('test'))
        self.assertEqual('internal.unknown', report.code)
        self.assertEqual('test', report.message)
        self.assertEqual('builtins.ValueError', report.origin_class)

    def test_details_argument_is_surfaced(self):
        report = ErrorReport.from_exception(
            ValueError('x'), details={'network_uuid': 'abc'})
        self.assertEqual({'network_uuid': 'abc'}, report.details)

    def test_details_defaults_to_empty_dict(self):
        report = ErrorReport.from_exception(ValueError('x'))
        self.assertEqual({}, report.details)

    def test_traceback_filled_inside_except_block(self):
        try:
            raise EnsureMeshFailed('oops')
        except EnsureMeshFailed as exc:
            report = ErrorReport.from_exception(exc)
        self.assertIn('EnsureMeshFailed', report.traceback)

    def test_traceback_empty_outside_except_block(self):
        report = ErrorReport.from_exception(ValueError('no handler'))
        self.assertEqual('', report.traceback)


class ErrorReportToHttpTestCase(base.ShakenFistTestCase):
    """``ErrorReport.to_http`` maps codes to HTTP statuses + body shape."""

    def _build(self, code):
        return ErrorReport(
            code=code,
            message='msg',
            details={'k': 'v'},
            origin_class='shakenfist.exceptions.EnsureMeshFailed',
            traceback='Traceback line\n',
        )

    def test_network_dead_returns_410(self):
        status, _ = self._build('network.dead').to_http()
        self.assertEqual(410, status)

    def test_network_ensure_mesh_failed_returns_500(self):
        status, _ = self._build('network.ensure_mesh.failed').to_http()
        self.assertEqual(500, status)

    def test_network_create_vxlan_failed_returns_500(self):
        status, _ = self._build('network.create_vxlan.failed').to_http()
        self.assertEqual(500, status)

    def test_network_floating_assign_failed_returns_500(self):
        status, _ = self._build('network.floating.assign_failed').to_http()
        self.assertEqual(500, status)

    def test_network_floating_add_failed_returns_500(self):
        status, _ = self._build('network.floating.add_failed').to_http()
        self.assertEqual(500, status)

    def test_network_floating_remove_failed_returns_500(self):
        status, _ = self._build('network.floating.remove_failed').to_http()
        self.assertEqual(500, status)

    def test_network_nat_enable_failed_returns_500(self):
        status, _ = self._build('network.nat.enable_failed').to_http()
        self.assertEqual(500, status)

    def test_network_congested_returns_503(self):
        status, _ = self._build('network.congested').to_http()
        self.assertEqual(503, status)

    def test_network_create_namespace_failed_returns_500(self):
        status, _ = self._build('network.create_namespace.failed').to_http()
        self.assertEqual(500, status)

    def test_network_list_interface_addresses_failed_returns_500(self):
        status, _ = self._build(
            'network.list_interface_addresses.failed').to_http()
        self.assertEqual(500, status)

    def test_internal_unknown_returns_500(self):
        status, _ = self._build('internal.unknown').to_http()
        self.assertEqual(500, status)

    def test_body_does_not_include_traceback_or_origin_class(self):
        _, body = self._build('network.dead').to_http()
        self.assertNotIn('traceback', body)
        self.assertNotIn('origin_class', body)

    def test_body_includes_code_message_details(self):
        _, body = self._build('network.dead').to_http()
        self.assertEqual(
            {'code': 'network.dead', 'message': 'msg',
             'details': {'k': 'v'}},
            body)


class DirectSetClusterOperationErrorTestCase(base.ShakenFistTestCase):
    """``_direct_set_cluster_operation_error`` upserts via SA."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    def _report(self):
        return ErrorReport(
            code='network.dead',
            message='gone',
            details={},
            origin_class='shakenfist.exceptions.DeadNetwork',
            traceback='',
        )

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_upsert_succeeds(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        op_uuid = uuid.uuid4()
        result = mariadb._direct_set_cluster_operation_error(
            op_uuid, self._report(), 1.0)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_false_on_operational_error(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = OperationalError(
            'statement', {}, Exception('boom'))
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_set_cluster_operation_error(
            uuid.uuid4(), self._report(), 1.0)

        self.assertFalse(result)


class DirectGetClusterOperationErrorTestCase(base.ShakenFistTestCase):
    """``_direct_get_cluster_operation_error`` returns the report or None."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_report_when_row_present(self, mock_get_engine):
        op_uuid = uuid.uuid4()
        stored = {
            'code': 'network.dead',
            'message': 'gone',
            'details': {'k': 'v'},
            'origin_class': 'shakenfist.exceptions.DeadNetwork',
            'traceback': '',
        }
        mock_row = mock.MagicMock()
        mock_row.error_report = stored

        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_cluster_operation_error(op_uuid)

        self.assertIsInstance(result, ErrorReport)
        self.assertEqual('network.dead', result.code)
        self.assertEqual('gone', result.message)
        self.assertEqual({'k': 'v'}, result.details)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_none_when_row_absent(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_cluster_operation_error(uuid.uuid4())

        self.assertIsNone(result)


class PublicClusterOperationErrorTestCase(base.ShakenFistTestCase):
    """Public wrappers route via ``_use_database_service``."""

    def _report(self):
        return ErrorReport(
            code='internal.unknown',
            message='x',
            details={},
            origin_class='builtins.ValueError',
            traceback='',
        )

    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    @mock.patch('shakenfist.mariadb._direct_set_cluster_operation_error')
    def test_set_routes_to_direct_when_local(
            self, mock_direct, _mock_use_grpc):
        mock_direct.return_value = True
        op_uuid = uuid.uuid4()
        result = mariadb.set_cluster_operation_error(
            op_uuid, self._report(), created_at=12.0)
        self.assertTrue(result)
        mock_direct.assert_called_once()
        args = mock_direct.call_args.args
        self.assertEqual(op_uuid, args[0])
        self.assertEqual(12.0, args[2])

    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    @mock.patch('shakenfist.mariadb._grpc_set_cluster_operation_error')
    def test_set_routes_to_grpc_when_remote(
            self, mock_grpc, _mock_use_grpc):
        mock_grpc.return_value = True
        op_uuid = uuid.uuid4()
        result = mariadb.set_cluster_operation_error(
            op_uuid, self._report(), created_at=12.0)
        self.assertTrue(result)
        mock_grpc.assert_called_once()

    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    @mock.patch('shakenfist.mariadb._direct_get_cluster_operation_error')
    def test_get_routes_to_direct_when_local(
            self, mock_direct, _mock_use_grpc):
        mock_direct.return_value = None
        result = mariadb.get_cluster_operation_error(uuid.uuid4())
        self.assertIsNone(result)
        mock_direct.assert_called_once()

    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    @mock.patch('shakenfist.mariadb._grpc_get_cluster_operation_error')
    def test_get_routes_to_grpc_when_remote(
            self, mock_grpc, _mock_use_grpc):
        mock_grpc.return_value = self._report()
        result = mariadb.get_cluster_operation_error(uuid.uuid4())
        self.assertIsNotNone(result)
        mock_grpc.assert_called_once()

    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    @mock.patch('shakenfist.mariadb._direct_delete_cluster_operation_error')
    def test_delete_routes_to_direct_when_local(
            self, mock_direct, _mock_use_grpc):
        mock_direct.return_value = True
        op_uuid = uuid.uuid4()
        result = mariadb.delete_cluster_operation_error(op_uuid)
        self.assertTrue(result)
        mock_direct.assert_called_once()
        self.assertEqual(op_uuid, mock_direct.call_args.args[0])

    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    @mock.patch('shakenfist.mariadb._grpc_delete_cluster_operation_error')
    def test_delete_routes_to_grpc_when_remote(
            self, mock_grpc, _mock_use_grpc):
        mock_grpc.return_value = True
        result = mariadb.delete_cluster_operation_error(uuid.uuid4())
        self.assertTrue(result)
        mock_grpc.assert_called_once()


class BaseClusterOperationHardDeleteTestCase(base.ShakenFistTestCase):
    """``BaseClusterOperation.hard_delete`` cleans up sibling tables.

    Verifies the cleanup chain that the cluster cleaner triggers when a
    cluster operation reaches terminal state. The ``cluster_operations``
    row and any ``cluster_operation_errors`` row must both be removed
    so neither table grows unbounded over time.
    """

    @mock.patch('shakenfist.operations.baseoperation.mariadb'
                '.delete_cluster_operation_target')
    @mock.patch('shakenfist.operations.baseoperation.mariadb'
                '.delete_cluster_operation_error')
    @mock.patch('shakenfist.operations.baseoperation.mariadb'
                '.delete_cluster_operation')
    @mock.patch('shakenfist.baseobject.mariadb.delete_state')
    @mock.patch('shakenfist.baseobject.mariadb.delete_object_metadata')
    @mock.patch('shakenfist.baseobject.mariadb.delete_object_events')
    def test_hard_delete_removes_error_and_operation_rows(
            self, mock_delete_events, mock_delete_metadata, mock_delete_state,
            mock_delete_op, mock_delete_error, mock_delete_target):
        from shakenfist.operations.baseoperation import BaseClusterOperation

        # A concrete subclass is required because ``super().hard_delete()``
        # in the production code resolves through the MRO. We do NOT call
        # the constructor — that would need a full static-values dict
        # and schema. Instead we build an instance via ``__new__`` and
        # set the only attribute the production method reads.
        op_uuid = '00000000-0000-0000-0000-000000000001'

        class _ConcreteOp(BaseClusterOperation):
            pass

        op = _ConcreteOp.__new__(_ConcreteOp)
        # ``uuid`` is a read-only property backed by the
        # name-mangled ``_DatabaseBackedObject__uuid`` attribute.
        op._DatabaseBackedObject__uuid = uuid.UUID(op_uuid)
        # Stub out add_event (called by the inherited hard_delete).
        op.add_event = mock.Mock()

        op.hard_delete()

        mock_delete_target.assert_called_once_with(op_uuid)
        mock_delete_error.assert_called_once_with(op_uuid)
        mock_delete_op.assert_called_once_with(op_uuid)
        # ``super().hard_delete()`` walks up to DatabaseBackedObject,
        # which deletes state and object_metadata. Verify the chain
        # reached the base class.
        mock_delete_state.assert_called_once()
        mock_delete_metadata.assert_called_once()


class DirectDeleteClusterOperationErrorTestCase(base.ShakenFistTestCase):
    """``_direct_delete_cluster_operation_error`` is idempotent."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_true_when_row_existed(self, mock_get_engine):
        # rowcount=1 path: the underlying engine.execute returned a
        # successful delete result.
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_cluster_operation_error(
            uuid.uuid4())

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_true_when_row_missing(self, mock_get_engine):
        # Idempotent: a DELETE that matches zero rows still returns
        # True, since hard_delete callers should not need to check.
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_cluster_operation_error(
            uuid.uuid4())

        self.assertTrue(result)
