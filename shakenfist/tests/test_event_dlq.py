# Copyright 2026 Michael Still and contributors
#
# Tests for the event_dlq _direct_* helpers in mariadb.py.

from unittest import mock

from shakenfist import mariadb
from shakenfist.tests import base


class _MockResult:
    def __init__(self, rowcount=0, rows=None):
        self.rowcount = rowcount
        self._rows = rows or []

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _MockConnection:
    def __init__(self, result=None):
        self.result = result or _MockResult()
        self.executed = []
        self.committed = False

    def execute(self, stmt):
        self.executed.append(stmt)
        return self.result

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _MockEngine:
    def __init__(self, connection):
        self._connection = connection

    def connect(self):
        return self._connection


EVENT = {
    'timestamp': 1234567890.0,
    'event_type': 'audit',
    'object_type': 'instance',
    'object_uuid': 'uuid-1',
    'fqdn': 'node1',
    'duration': None,
    'message': 'test event',
    'extra': None,
    'correlation_id': None,
}


class DirectEnqueueEventDlqTestCase(base.ShakenFistTestCase):
    """Tests for _direct_enqueue_event_dlq."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_enqueue_inserts_row(self, mock_engine):
        conn = _MockConnection()
        mock_engine.return_value = _MockEngine(conn)

        mariadb._direct_enqueue_event_dlq(
            'instance', 'uuid-1', 1234567890.0, EVENT)
        self.assertTrue(conn.committed)
        self.assertEqual(len(conn.executed), 1)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_enqueue_handles_error(self, mock_engine):
        from sqlalchemy.exc import OperationalError
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        # Should not raise
        mariadb._direct_enqueue_event_dlq(
            'instance', 'uuid-1', 1234567890.0, EVENT)


class DirectDrainEventDlqTestCase(base.ShakenFistTestCase):
    """Tests for _direct_drain_event_dlq."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_drain_returns_rows(self, mock_engine):
        conn = _MockConnection(
            result=_MockResult(rows=[
                (1, 'instance', 'uuid-1', EVENT),
                (2, 'network', 'uuid-2', EVENT),
            ]))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_drain_event_dlq(limit=100)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['id'], 1)
        self.assertEqual(result[0]['object_type'], 'instance')
        self.assertEqual(result[1]['id'], 2)
        self.assertEqual(result[1]['object_type'], 'network')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_drain_returns_empty_on_no_rows(self, mock_engine):
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_drain_event_dlq()
        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_drain_returns_empty_on_error(self, mock_engine):
        from sqlalchemy.exc import OperationalError
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_drain_event_dlq()
        self.assertEqual(result, [])


class DirectDeleteEventDlqTestCase(base.ShakenFistTestCase):
    """Tests for _direct_delete_event_dlq."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_by_ids(self, mock_engine):
        conn = _MockConnection(result=_MockResult(rowcount=3))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_delete_event_dlq([1, 2, 3])
        self.assertEqual(result, 3)
        self.assertTrue(conn.committed)

    def test_delete_empty_list_is_noop(self):
        result = mariadb._direct_delete_event_dlq([])
        self.assertEqual(result, 0)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_returns_zero_on_error(self, mock_engine):
        from sqlalchemy.exc import OperationalError
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_delete_event_dlq([1, 2])
        self.assertEqual(result, 0)


class PublicEnqueueEventDlqTestCase(base.ShakenFistTestCase):
    """Tests for the public enqueue_event_dlq UUID coercion."""

    @mock.patch('shakenfist.mariadb._use_database_service')
    @mock.patch('shakenfist.mariadb._direct_enqueue_event_dlq')
    def test_coerces_uuid_objects_in_event_payload(
            self, mock_direct, mock_use_service):
        # The eventlog DLQ path passes ``extra`` dicts through
        # verbatim, and callers (e.g. InstancesEndpoint.post echoing
        # the request body) can legitimately include uuid.UUID
        # objects. Both the gRPC and direct paths down-stream rely
        # on the default JSON encoder, which refuses UUIDs -- the
        # public enqueue_event_dlq must normalise them so neither
        # path explodes.
        import uuid
        mock_use_service.return_value = False

        event_uuid = uuid.uuid4()
        payload = {
            'message': 'requested networking configuration',
            'extra': {
                'networks': [{'network_uuid': event_uuid}],
            },
        }
        mariadb.enqueue_event_dlq(
            'instance', 'uuid-1', 1234567890.0, payload)

        mock_direct.assert_called_once()
        _, _, _, sanitised = mock_direct.call_args[0]
        self.assertEqual(
            sanitised['extra']['networks'][0]['network_uuid'],
            str(event_uuid))
