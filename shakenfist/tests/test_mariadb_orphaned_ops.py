# Copyright 2026 Michael Still and contributors
#
# Direct SQL tests for the orphaned cluster operation sweep
# (issue #4303).
#
# A cluster operation is orphaned when it sits in a non-terminal state
# with no work_queue row referencing it: the enqueue writes both in one
# transaction, so the shape only occurs as failure debris (issue 4273's
# deadlock chain was one producer). These tests run the production
# statement against a real (sqlite) database, for the same reason
# test_mariadb_coalescing.py does: the query joins the undashed
# cluster_operations.uuid to the dashed object_states.object_uuid and
# binds one ObjectType enum per operation type, both of which are traps
# a mocked engine cannot spring.
#
# The dbfixture's unix_timestamp shim returns a fixed
# 1,750,000,000.0, so "now" in these tests is that instant: a state
# update_time of 100.0 is ancient and one just below the fixed stamp is
# fresh.

import uuid
from unittest import mock

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.tests import base
from shakenfist.tests import dbfixture


SHIM_NOW = 1_750_000_000.0
THRESHOLD = 1800.0

NETWORK_UUID = '11111111-1111-4111-8111-111111111111'
ORPHAN_UUID = '99999999-9999-4999-8999-999999999999'
ORPHAN2_UUID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
LIVE_UUID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
TASK = 'network_apply_ensure_mesh'


class OrphanedClusterOperationsTestCase(
        dbfixture.MariaDBTableFixture, base.ShakenFistTestCase):
    """The orphan sweep query, run against a real database."""

    def _build_engine(self):
        return self.build_engine(
            [mariadb._get_object_states_table,
             mariadb._get_cluster_operations_table,
             mariadb._get_work_queue_table],
            json_shims=True)

    def _insert_op(self, conn, op_uuid, state='queued', update_time=100.0,
                   operation_type='net_op', with_work_queue_row=False):
        ops = mariadb._get_cluster_operations_table()
        states = mariadb._get_object_states_table()
        conn.execute(sa.insert(ops).values(
            uuid=uuid.UUID(op_uuid),
            operation_type=operation_type,
            created_at=update_time,
            network_uuid=uuid.UUID(NETWORK_UUID),
            priority='user_facing',
            metadata_json={'tasks': [TASK]}))
        conn.execute(sa.insert(states).values(
            object_uuid=op_uuid,
            object_type=operation_type,
            state_value=state,
            update_time=update_time,
            message=None))
        if with_work_queue_row:
            # sqlite only autoincrements INTEGER primary keys, not the
            # BIGINT the real column is, so supply an id explicitly.
            self._work_queue_id = getattr(self, '_work_queue_id', 0) + 1
            work_queue = mariadb._get_work_queue_table()
            conn.execute(sa.insert(work_queue).values(
                id=self._work_queue_id,
                queue_name='networknode-net-user_facing',
                scheduled_at=update_time,
                claimed_at=None,
                claimed_by=None,
                attempts=0,
                payload={
                    'operation_type': operation_type,
                    'operation_uuid': op_uuid,
                },
                created_at=update_time))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_a_rowless_queued_op_is_reported(self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, ORPHAN_UUID)
            conn.commit()

        orphans = mariadb._direct_list_orphaned_cluster_operations(THRESHOLD)
        self.assertEqual(1, len(orphans))
        # The uuid comes back dashed, ready for from_db().
        self.assertEqual(ORPHAN_UUID, orphans[0]['uuid'])
        self.assertEqual('net_op', orphans[0]['operation_type'])
        self.assertEqual('queued', orphans[0]['state_value'])
        self.assertEqual(100.0, orphans[0]['update_time'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_a_rowless_executing_op_is_reported(self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, ORPHAN_UUID, state='executing')
            conn.commit()

        orphans = mariadb._direct_list_orphaned_cluster_operations(THRESHOLD)
        self.assertEqual(
            [ORPHAN_UUID], [o['uuid'] for o in orphans])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_an_op_with_a_work_queue_row_is_not_an_orphan(
            self, mock_get_engine):
        # The normal case: every live op's row was written in the same
        # transaction as the op itself.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, LIVE_UUID, with_work_queue_row=True)
            conn.commit()

        self.assertEqual(
            [], mariadb._direct_list_orphaned_cluster_operations(THRESHOLD))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_a_fresh_rowless_op_is_left_alone(self, mock_get_engine):
        # Younger than the threshold: not reported, however suspicious.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, ORPHAN_UUID, update_time=SHIM_NOW - 10.0)
            conn.commit()

        self.assertEqual(
            [], mariadb._direct_list_orphaned_cluster_operations(THRESHOLD))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_terminal_states_are_ignored(self, mock_get_engine):
        # Terminal ops legitimately have no work_queue row; the cleaner
        # owns them.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            for i, state in enumerate(
                    ['complete', 'error', 'abort', 'deleted']):
                self._insert_op(
                    conn, f'0000000{i}-0000-4000-8000-000000000000',
                    state=state)
            conn.commit()

        self.assertEqual(
            [], mariadb._direct_list_orphaned_cluster_operations(THRESHOLD))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_orphans_come_back_oldest_first(self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, ORPHAN2_UUID, update_time=200.0,
                            operation_type='node_net_op')
            self._insert_op(conn, ORPHAN_UUID, update_time=100.0)
            conn.commit()

        orphans = mariadb._direct_list_orphaned_cluster_operations(THRESHOLD)
        self.assertEqual(
            [ORPHAN_UUID, ORPHAN2_UUID], [o['uuid'] for o in orphans])
        self.assertEqual(
            ['net_op', 'node_net_op'],
            [o['operation_type'] for o in orphans])
