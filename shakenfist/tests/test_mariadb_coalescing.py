# Copyright 2026 Michael Still and contributors
#
# Direct SQL tests for the cluster operation coalescing primitives
# (issue #3878).
#
# These execute the queries against a real database rather than a mocked
# engine. That distinction is the whole point of the module: the existing
# coalescing tests mock either ``mariadb.fold_coalescible_siblings`` (in
# shakenfist/tests/operations/test_baseoperation.py) or ``_get_engine``
# (in shakenfist/tests/test_mariadb_work_queue.py), so between them they
# assert that the dispatcher decides to call the primitive and that the
# statement has the right shape -- but never that the statement matches a
# row. Coalescing was consequently dead for three months while the suite
# stayed green.
#
# The specific trap is the one described in test_mariadb_orphans.py:
# object_states.object_uuid stores the dashed 36 character form, while
# cluster_operations.uuid is a sa.Uuid column storing undashed CHAR(32)
# on both sqlite and MariaDB. A join between them has to transform one
# side or it silently never matches.
#
# What these tests do NOT cover, so nobody reads more into a green run
# than is there:
#
# * The locking half of the fold. ``_direct_claim_coalescible_siblings``
#   relies on ``SELECT ... FOR UPDATE`` plus the ``state_value = 'queued'``
#   predicate to serialise against a concurrent dispatcher's
#   STATE_EXECUTING write, and then issues an UPDATE which does not
#   re-check the state because the rows are held. SQLAlchemy's sqlite
#   dialect emits nothing at all for FOR UPDATE, so every test here runs
#   uncontended and says nothing about that guarantee. Exercising it needs
#   a real MariaDB and real concurrency -- see issue #3879.
# * Anything about which *queue* a sibling is on. The statement cannot
#   filter on that (cluster_operations has no queue column); the
#   invariant that makes it safe is enforced at enqueue time instead, and
#   is tested in shakenfist/tests/schema/test_net_op_coalescing.py.

import inspect
import uuid
from typing import Any, Optional
from unittest import mock

import grpc
import sqlalchemy as sa

from shakenfist.daemons.database import main as database_main
from shakenfist.daemons.network import maintain as network_maintain
from shakenfist.daemons.network import workitem as network_workitem
from shakenfist.daemons.queues import workitem as queues_workitem
from shakenfist import constants
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.error_report import ErrorReport
from shakenfist.operations.net_op import NetOp
from shakenfist.operations.net_op import NoSuchNetwork
from shakenfist.protos import database_pb2
from shakenfist.protos import database_pb2_grpc
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations import net_op as net_op_schema
from shakenfist.tests import base
from shakenfist.tests import dbfixture


NETWORK_UUID = '11111111-1111-4111-8111-111111111111'
SURVIVOR_UUID = '99999999-9999-4999-8999-999999999999'
SIBLING_UUID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
TASK = 'network_apply_update_dnsmasq'

# Two per-node NetOp keys, plus the cluster-wide (None) case. These stand
# in for the two hypervisors in the regression
# test_single_virtual_networks_work caught when network_ensure_mesh was
# coalesced on the network alone: a fold on node A's queue must never
# touch node B's work on the same network.
NODE_A_UUID = 'c0000000-0000-4000-8000-000000000001'
NODE_B_UUID = 'c0000000-0000-4000-8000-000000000002'


class CoalescingTables(dbfixture.MariaDBTableFixture):
    """The tables the coalescing queries read, and a way to fill them.

    A mixin rather than a test case, so a test case built on it does not
    inherit, and run again, every test in ``CoalescingSQLTestCase``.
    """

    def _build_engine(self):
        return self.build_engine(
            [mariadb._get_object_states_table,
             mariadb._get_cluster_operations_table,
             mariadb._get_work_queue_table],
            json_shims=True)

    def _insert_op(self, conn, op_uuid, tasks=(TASK,), state='queued',
                   created_at=100.0, node_uuid=None,
                   priority='user_facing', with_work_queue_row=True):
        """Insert one cluster operation and its state row.

        The two rows deliberately go in the way production writes them:
        the static row through the sa.Uuid bind processor, which stores
        the undashed form, and the state row as the dashed string every
        caller hands to set_state().

        ``with_work_queue_row`` matches the production enqueue, which
        writes the work_queue row in the same transaction; passing
        False manufactures the issue-4303 orphan shape.

        ``node_uuid`` defaults to ``None`` -- a cluster-wide op -- so
        every caller written before the multi-column key still gets the
        same row it always did.
        """
        ops = mariadb._get_cluster_operations_table()
        states = mariadb._get_object_states_table()
        conn.execute(sa.insert(ops).values(
            uuid=uuid.UUID(op_uuid),
            operation_type='net_op',
            created_at=created_at,
            network_uuid=uuid.UUID(NETWORK_UUID),
            node_uuid=uuid.UUID(node_uuid) if node_uuid is not None else None,
            priority=priority,
            metadata_json={'tasks': list(tasks)}))
        conn.execute(sa.insert(states).values(
            object_uuid=op_uuid,
            object_type='net_op',
            state_value=state,
            update_time=created_at,
            message=None))
        if with_work_queue_row:
            # sqlite only autoincrements INTEGER primary keys, not the
            # BIGINT the real column is, so supply an id explicitly.
            self._work_queue_id = getattr(self, '_work_queue_id', 0) + 1
            work_queue = mariadb._get_work_queue_table()
            conn.execute(sa.insert(work_queue).values(
                id=self._work_queue_id,
                queue_name='networknode-net-user_facing',
                scheduled_at=created_at,
                claimed_at=None,
                claimed_by=None,
                attempts=0,
                payload={
                    'operation_type': 'net_op',
                    'operation_uuid': op_uuid,
                },
                created_at=created_at))


class CoalescingSQLTestCase(CoalescingTables, base.ShakenFistTestCase):
    """The coalescing queries, run against a real database."""

    def test_the_two_tables_really_do_store_uuids_differently(self):
        # The premise every other test here rests on. If this ever stops
        # being true the join transformation becomes unnecessary rather
        # than wrong, and the rest of this module would start passing for
        # the wrong reason.
        engine = self._build_engine()
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID)
            conn.commit()

            stored_op = conn.execute(
                sa.text('select uuid from cluster_operations')).scalar()
            stored_state = conn.execute(
                sa.text('select object_uuid from object_states')).scalar()

        self.assertEqual(SIBLING_UUID.replace('-', ''), stored_op)
        self.assertEqual(SIBLING_UUID, stored_state)
        self.assertNotEqual(stored_op, stored_state)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_matches_a_queued_sibling(self, mock_get_engine):
        # Issue #3878: this returned None for three months because the
        # join compared an undashed uuid to a dashed one.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID)
            conn.commit()

        self.assertEqual(
            SIBLING_UUID,
            mariadb._direct_find_existing_coalescible_op(
                'net_op', [('network_uuid', NETWORK_UUID)], TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_returns_the_oldest_of_several(
            self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID, created_at=50.0)
            self._insert_op(conn, SIBLING_UUID, created_at=150.0)
            conn.commit()

        self.assertEqual(
            SURVIVOR_UUID,
            mariadb._direct_find_existing_coalescible_op(
                'net_op', [('network_uuid', NETWORK_UUID)], TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_ignores_an_orphaned_op(self, mock_get_engine):
        # Issue #4303: an op still in 'queued' whose work_queue row is
        # gone can never run. Adopting it wedged every ensure_mesh
        # caller on sfcbr in raise_for_error() for 600s per daemon
        # start; the dedup must enqueue fresh work instead.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, with_work_queue_row=False)
            conn.commit()

        self.assertIsNone(
            mariadb._direct_find_existing_coalescible_op(
                'net_op', [('network_uuid', NETWORK_UUID)], TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_skips_an_orphan_for_a_live_sibling(
            self, mock_get_engine):
        # The orphan is older, but the live op is the one returned.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID, created_at=50.0,
                            with_work_queue_row=False)
            self._insert_op(conn, SIBLING_UUID, created_at=150.0)
            conn.commit()

        self.assertEqual(
            SIBLING_UUID,
            mariadb._direct_find_existing_coalescible_op(
                'net_op', [('network_uuid', NETWORK_UUID)], TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_ignores_a_non_queued_op(self, mock_get_engine):
        # An op already executing must not be reused: its work may have
        # started before the new caller's change was written.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, state='executing')
            conn.commit()

        self.assertIsNone(
            mariadb._direct_find_existing_coalescible_op(
                'net_op', [('network_uuid', NETWORK_UUID)], TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_ignores_a_multi_task_op(self, mock_get_engine):
        # A sibling carrying extra work must not be folded away, or the
        # extra work is silently dropped.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(
                conn, SIBLING_UUID, tasks=(TASK, 'network_apply_ensure_mesh'))
            conn.commit()

        self.assertIsNone(
            mariadb._direct_find_existing_coalescible_op(
                'net_op', [('network_uuid', NETWORK_UUID)], TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_claim_siblings_folds_and_excludes_the_survivor(
            self, mock_get_engine):
        # The other half of #3878, and the one with teeth: this marks
        # other workers' operations complete.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID)
            self._insert_op(conn, SIBLING_UUID)
            conn.commit()

        folded = mariadb._direct_claim_coalescible_siblings(
            'net_op', [('network_uuid', NETWORK_UUID)], [TASK],
            SURVIVOR_UUID)
        self.assertEqual([SIBLING_UUID], folded)

        states = mariadb._get_object_states_table()
        with engine.connect() as conn:
            rows = dict(conn.execute(sa.select(
                states.c.object_uuid, states.c.state_value)).fetchall())
        self.assertEqual('complete', rows[SIBLING_UUID])
        self.assertEqual('queued', rows[SURVIVOR_UUID])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_claim_siblings_folds_nothing_when_alone(self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID)
            conn.commit()

        self.assertEqual(
            [], mariadb._direct_claim_coalescible_siblings(
                'net_op', [('network_uuid', NETWORK_UUID)], [TASK],
                SURVIVOR_UUID))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_claim_siblings_leaves_another_network_alone(
            self, mock_get_engine):
        # The target filter is what stops a fold reaching across
        # networks; with the join broken this was untested in practice.
        other_network = '22222222-2222-4222-8222-222222222222'
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        ops = mariadb._get_cluster_operations_table()
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID)
            self._insert_op(conn, SIBLING_UUID)
            conn.execute(sa.update(ops)
                         .where(ops.c.uuid == uuid.UUID(SIBLING_UUID))
                         .values(network_uuid=uuid.UUID(other_network)))
            conn.commit()

        self.assertEqual(
            [], mariadb._direct_claim_coalescible_siblings(
                'net_op', [('network_uuid', NETWORK_UUID)], [TASK],
                SURVIVOR_UUID))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_claim_siblings_folds_for_a_multi_task_survivor(
            self, mock_get_engine):
        # Network creation and interface hot-plug both enqueue the
        # two-task list [network_apply_create_network_node,
        # network_ensure_mesh]. The survivor's own arity is not filtered
        # -- only the siblings' -- so a multi-task survivor must still
        # fold single-task siblings matching any of its coalescible
        # tasks.
        other_task = 'network_apply_create_network_node'
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID, tasks=(TASK, other_task))
            self._insert_op(conn, SIBLING_UUID, tasks=(other_task,))
            conn.commit()

        self.assertEqual(
            [SIBLING_UUID],
            mariadb._direct_claim_coalescible_siblings(
                'net_op', [('network_uuid', NETWORK_UUID)],
                [TASK, other_task], SURVIVOR_UUID))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_leaves_another_network_alone(
            self, mock_get_engine):
        # The claim path has this covered; the lookup path did not.
        other_network = '22222222-2222-4222-8222-222222222222'
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        ops = mariadb._get_cluster_operations_table()
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID)
            conn.execute(sa.update(ops)
                         .where(ops.c.uuid == uuid.UUID(SIBLING_UUID))
                         .values(network_uuid=uuid.UUID(other_network)))
            conn.commit()

        self.assertIsNone(
            mariadb._direct_find_existing_coalescible_op(
                'net_op', [('network_uuid', NETWORK_UUID)], TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_ignores_a_different_task(self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, tasks=('network_remove_nat',))
            conn.commit()

        self.assertIsNone(
            mariadb._direct_find_existing_coalescible_op(
                'net_op', [('network_uuid', NETWORK_UUID)], TASK))

    def test_an_unknown_operation_type_is_refused_loudly(self):
        # Both queries bind operation_type through ObjectType(). A value
        # which is not a member has to be refused rather than allowed to
        # raise inside the statement -- but silently refusing it is how
        # #3878 hid for three months, so the skip is logged.
        with mock.patch('shakenfist.mariadb.LOG') as mock_log:
            self.assertIsNone(
                mariadb._direct_find_existing_coalescible_op(
                    'not_an_object_type', [('network_uuid', NETWORK_UUID)],
                    TASK))
            self.assertEqual(
                [], mariadb._direct_claim_coalescible_siblings(
                    'not_an_object_type', [('network_uuid', NETWORK_UUID)],
                    [TASK], SURVIVOR_UUID))
        self.assertEqual(2, mock_log.warning.call_count)
        for call in mock_log.warning.call_args_list:
            self.assertIn('not a known ObjectType', call.args[0])

    def test_an_unknown_target_column_is_refused_loudly(self):
        with mock.patch('shakenfist.mariadb.LOG') as mock_log:
            self.assertIsNone(
                mariadb._direct_find_existing_coalescible_op(
                    'net_op', [('namespace', NETWORK_UUID)], TASK))
            self.assertEqual(
                [], mariadb._direct_claim_coalescible_siblings(
                    'net_op', [('namespace', NETWORK_UUID)], [TASK],
                    SURVIVOR_UUID))
        self.assertEqual(2, mock_log.warning.call_count)
        for call in mock_log.warning.call_args_list:
            self.assertIn('not a coalescible target column', call.args[0])

    # -- The two-pair (network_uuid, node_uuid) key ---------------------
    #
    # These tests prove the widened key is sound: a key that
    # names node_uuid must be strictly narrower than the network alone,
    # in both directions -- a per-node op folds only its own node's
    # siblings, and a cluster-wide op (node_uuid unset, binding IS NULL)
    # folds only other cluster-wide ops. Getting either direction wrong
    # reproduces one of two real failures: the
    # ``test_single_virtual_networks_work`` regression (one hypervisor's
    # fold silently eating another's work) or a near-miss caught in
    # review (widening the key to include node_uuid would have switched
    # off the only coalescing the cluster does, by refusing every
    # NULL-valued key instead of binding IS NULL).

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_claim_siblings_folds_a_sibling_on_the_same_node(
            self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID, node_uuid=NODE_A_UUID)
            self._insert_op(conn, SIBLING_UUID, node_uuid=NODE_A_UUID)
            conn.commit()

        folded = mariadb._direct_claim_coalescible_siblings(
            'net_op',
            [('network_uuid', NETWORK_UUID), ('node_uuid', NODE_A_UUID)],
            [TASK], SURVIVOR_UUID)
        self.assertEqual([SIBLING_UUID], folded)

        states = mariadb._get_object_states_table()
        with engine.connect() as conn:
            rows = dict(conn.execute(sa.select(
                states.c.object_uuid, states.c.state_value)).fetchall())
        self.assertEqual('complete', rows[SIBLING_UUID])
        self.assertEqual('queued', rows[SURVIVOR_UUID])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_claim_siblings_leaves_a_different_node_alone(
            self, mock_get_engine):
        # The regression the per-node key exists to prevent recurring:
        # node A's survivor must not fold node B's op on the same
        # network, or B's mesh apply is silently never run.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID, node_uuid=NODE_A_UUID)
            self._insert_op(conn, SIBLING_UUID, node_uuid=NODE_B_UUID)
            conn.commit()

        folded = mariadb._direct_claim_coalescible_siblings(
            'net_op',
            [('network_uuid', NETWORK_UUID), ('node_uuid', NODE_A_UUID)],
            [TASK], SURVIVOR_UUID)
        self.assertEqual([], folded)

        states = mariadb._get_object_states_table()
        with engine.connect() as conn:
            rows = dict(conn.execute(sa.select(
                states.c.object_uuid, states.c.state_value)).fetchall())
        self.assertEqual('queued', rows[SIBLING_UUID])
        self.assertEqual('queued', rows[SURVIVOR_UUID])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_claim_siblings_cluster_wide_survivor_folds_other_cluster_wide(
            self, mock_get_engine):
        # node_uuid unset on both sides binds IS NULL on both sides, so
        # this is the network-only key's behaviour: the two ops that
        # actually fold cluster-wide (network_apply_update_dnsmasq,
        # network_apply_create_network_node) live on the cluster-wide
        # queue and must keep folding each other under the wider key.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID, node_uuid=None)
            self._insert_op(conn, SIBLING_UUID, node_uuid=None)
            conn.commit()

        folded = mariadb._direct_claim_coalescible_siblings(
            'net_op',
            [('network_uuid', NETWORK_UUID), ('node_uuid', None)],
            [TASK], SURVIVOR_UUID)
        self.assertEqual([SIBLING_UUID], folded)

        states = mariadb._get_object_states_table()
        with engine.connect() as conn:
            rows = dict(conn.execute(sa.select(
                states.c.object_uuid, states.c.state_value)).fetchall())
        self.assertEqual('complete', rows[SIBLING_UUID])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_claim_siblings_cluster_wide_survivor_leaves_per_node_op_alone(
            self, mock_get_engine):
        # The regression binding IS NULL exists to prevent: if a NULL key
        # value were refused (rather than bound as IS NULL) the
        # cluster-wide fold above would have silently stopped matching
        # anything the moment the key widened. Proving the negative here
        # -- a per-node sibling on the same network is untouched -- is
        # the other half of the same property: IS NULL must be strictly
        # narrower, not broader.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID, node_uuid=None)
            self._insert_op(conn, SIBLING_UUID, node_uuid=NODE_A_UUID)
            conn.commit()

        folded = mariadb._direct_claim_coalescible_siblings(
            'net_op',
            [('network_uuid', NETWORK_UUID), ('node_uuid', None)],
            [TASK], SURVIVOR_UUID)
        self.assertEqual([], folded)

        states = mariadb._get_object_states_table()
        with engine.connect() as conn:
            rows = dict(conn.execute(sa.select(
                states.c.object_uuid, states.c.state_value)).fetchall())
        self.assertEqual('queued', rows[SIBLING_UUID])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_matches_only_the_same_node(
            self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, node_uuid=NODE_A_UUID)
            conn.commit()

        self.assertEqual(
            SIBLING_UUID,
            mariadb._direct_find_existing_coalescible_op(
                'net_op',
                [('network_uuid', NETWORK_UUID), ('node_uuid', NODE_A_UUID)],
                TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_ignores_a_different_nodes_op(
            self, mock_get_engine):
        # The enqueue-side counterpart of the cross-node fold: a
        # lookup for node A's work must not be satisfied by node B's
        # pending op, or the caller believes its work is already queued
        # when nobody is actually going to do it on node A.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, node_uuid=NODE_B_UUID)
            conn.commit()

        self.assertIsNone(
            mariadb._direct_find_existing_coalescible_op(
                'net_op',
                [('network_uuid', NETWORK_UUID), ('node_uuid', NODE_A_UUID)],
                TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_cluster_wide_key_ignores_a_per_node_op(
            self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, node_uuid=NODE_A_UUID)
            conn.commit()

        self.assertIsNone(
            mariadb._direct_find_existing_coalescible_op(
                'net_op',
                [('network_uuid', NETWORK_UUID), ('node_uuid', None)],
                TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_cluster_wide_key_matches_a_cluster_wide_op(
            self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, node_uuid=None)
            conn.commit()

        self.assertEqual(
            SIBLING_UUID,
            mariadb._direct_find_existing_coalescible_op(
                'net_op',
                [('network_uuid', NETWORK_UUID), ('node_uuid', None)],
                TASK))

    def test_an_empty_key_list_is_refused_loudly(self):
        # Without a single equality the statement would fold every
        # pending operation of this type in the cluster -- this is the
        # one case _coalescible_preflight refuses outright rather than
        # narrowing (contrast a None *value*, which binds IS NULL).
        with mock.patch('shakenfist.mariadb.LOG') as mock_log:
            self.assertIsNone(
                mariadb._direct_find_existing_coalescible_op(
                    'net_op', [], TASK))
            self.assertEqual(
                [], mariadb._direct_claim_coalescible_siblings(
                    'net_op', [], [TASK], SURVIVOR_UUID))
        self.assertEqual(2, mock_log.warning.call_count)
        for call in mock_log.warning.call_args_list:
            self.assertIn('coalescing key is empty', call.args[0])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_a_malformed_network_uuid_skips_the_query_and_logs(
            self, mock_get_engine):
        # A malformed uuid in either key position must be a loud skip,
        # not a raise -- letting a StatementError escape would kill the
        # dispatcher worker thread over what is only a cost optimisation.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, node_uuid=NODE_A_UUID)
            conn.commit()

        with mock.patch('shakenfist.mariadb.LOG') as mock_log:
            self.assertIsNone(
                mariadb._direct_find_existing_coalescible_op(
                    'net_op',
                    [('network_uuid', 'not-a-uuid'),
                     ('node_uuid', NODE_A_UUID)],
                    TASK))
            self.assertEqual(
                [], mariadb._direct_claim_coalescible_siblings(
                    'net_op',
                    [('network_uuid', 'not-a-uuid'),
                     ('node_uuid', NODE_A_UUID)],
                    [TASK], SURVIVOR_UUID))
        self.assertEqual(2, mock_log.warning.call_count)
        for call in mock_log.warning.call_args_list:
            self.assertIn('malformed uuid', call.args[0])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_a_malformed_node_uuid_skips_the_query_and_logs(
            self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, node_uuid=NODE_A_UUID)
            conn.commit()

        with mock.patch('shakenfist.mariadb.LOG') as mock_log:
            self.assertIsNone(
                mariadb._direct_find_existing_coalescible_op(
                    'net_op',
                    [('network_uuid', NETWORK_UUID),
                     ('node_uuid', 'not-a-uuid')],
                    TASK))
            self.assertEqual(
                [], mariadb._direct_claim_coalescible_siblings(
                    'net_op',
                    [('network_uuid', NETWORK_UUID),
                     ('node_uuid', 'not-a-uuid')],
                    [TASK], SURVIVOR_UUID))
        self.assertEqual(2, mock_log.warning.call_count)
        for call in mock_log.warning.call_args_list:
            self.assertIn('malformed uuid', call.args[0])

    # ------------------------------------------------------------------
    # The priority half of the dedup key.
    #
    # Reuse is one-sided: adopting a more urgent pending op is free,
    # adopting a less urgent one means adopting its queue. Queue names
    # are '{target}-{family}-{priority}', so a user_facing enqueue
    # deduped onto a queued background op does not simply lose its lane
    # -- the caller sitting in raise_for_error(), and the runs_after
    # dependency an instance start hangs off, both then wait out the
    # background lane's queue-sit tail. That overlap is real for both
    # coalescible tasks which have two enqueue sites: the maintainer
    # enqueues network_ensure_mesh and network_apply_create_network_node
    # at background for the same network and node the interactive paths
    # use at user_facing.
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_ignores_a_less_urgent_op(self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, priority='background')
            conn.commit()

        self.assertIsNone(
            mariadb._direct_find_existing_coalescible_op(
                'net_op', [('network_uuid', NETWORK_UUID)], TASK,
                ['user_waiting', 'user_facing']))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_reuses_a_more_urgent_op(self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, priority='user_waiting')
            conn.commit()

        self.assertEqual(
            SIBLING_UUID,
            mariadb._direct_find_existing_coalescible_op(
                'net_op', [('network_uuid', NETWORK_UUID)], TASK,
                ['user_waiting', 'user_facing', 'user_facing_high_io',
                 'background']))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_reuses_an_op_at_the_same_priority(
            self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, priority='user_facing')
            conn.commit()

        self.assertEqual(
            SIBLING_UUID,
            mariadb._direct_find_existing_coalescible_op(
                'net_op', [('network_uuid', NETWORK_UUID)], TASK,
                ['user_waiting', 'user_facing']))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_without_priorities_matches_any(
            self, mock_get_engine):
        # An absent or empty list means no priority filter. That is the
        # proto3 default, so it is also what a V2 server predating the
        # field applies -- the pre-change behaviour, and a lost
        # optimisation rather than a correctness problem.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, priority='background_high_io')
            conn.commit()

        for priorities in (None, []):
            self.assertEqual(
                SIBLING_UUID,
                mariadb._direct_find_existing_coalescible_op(
                    'net_op', [('network_uuid', NETWORK_UUID)], TASK,
                    priorities),
                f'priorities={priorities!r} should not have filtered')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_picks_the_oldest_eligible_not_the_oldest(
            self, mock_get_engine):
        # The priority filter has to apply before the ORDER BY, not
        # after: an older ineligible op must not shadow a younger
        # eligible one.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(
                conn, SIBLING_UUID, created_at=100.0, priority='background')
            self._insert_op(
                conn, SURVIVOR_UUID, created_at=200.0, priority='user_facing')
            conn.commit()

        self.assertEqual(
            SURVIVOR_UUID,
            mariadb._direct_find_existing_coalescible_op(
                'net_op', [('network_uuid', NETWORK_UUID)], TASK,
                ['user_waiting', 'user_facing']))


MESH_TASK = 'network_ensure_mesh'
CREATE_TASK = 'network_apply_create_network_node'
NODE_A_QUEUE_BACKGROUND = f'{NODE_A_UUID}-network-background'
NODE_A_QUEUE_USER_FACING = f'{NODE_A_UUID}-network-user_facing'
CLUSTER_WIDE_QUEUE = 'networknode-clusteroperation-user_facing'
DEPENDENT_UUID = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
SECOND_SIBLING_UUID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


class FakeRpcError(grpc.RpcError):
    def __init__(self, code: grpc.StatusCode) -> None:
        self._code = code

    def code(self) -> grpc.StatusCode:
        return self._code


class _Crash(BaseException):
    """Stands in for the process dying, which no except clause catches."""


class FoldHarness(CoalescingTables, base.ShakenFistTestCase):
    """The fold and what follows it, through a real ``NetOp.execute()``.

    Issue #4638: the fold used to set its siblings ``complete`` before the
    survivor had run a single task, so a dependent of a sibling was
    released as soon as the survivor started and went ahead even if the
    survivor then failed. These tests run the production fold statement,
    the real state machine and the real dispatchers against one sqlite
    database, so every state a waiter reads is the state the code wrote.

    Only the edges are faked: ``_direct_set_state`` (its upsert is MySQL
    only), the persisted error reports, work item re-enqueue, events, and
    the network the tasks act on.
    """

    def setUp(self) -> None:
        super().setUp()
        self.engine = self._build_engine()
        self.errors: dict[str, ErrorReport] = {}
        self.enqueued: list[tuple[str, dict[str, Any]]] = []
        self.failing_state_writes: set[str] = set()

        self.mock_get_engine = self._patch(
            'shakenfist.mariadb._get_engine', return_value=self.engine)
        self._patch('shakenfist.mariadb._use_database_service',
                    return_value=False)
        self._patch('shakenfist.mariadb._direct_set_state',
                    side_effect=self._set_state)
        self._patch('shakenfist.mariadb.set_cluster_operation_error',
                    side_effect=self._set_error)
        self._patch('shakenfist.mariadb.get_cluster_operation_error',
                    side_effect=lambda op_uuid: self.errors.get(str(op_uuid)))
        self._patch('shakenfist.mariadb.enqueue_work_item',
                    side_effect=self._enqueue_work_item)
        self._patch(
            'shakenfist.operations.baseoperation.eventlog.add_event_multi')
        self._patch('shakenfist.operations.net_op.add_event_multi')
        p = mock.patch.object(BaseClusterOperation, 'add_event')
        p.start()
        self.addCleanup(p.stop)

        network = mock.MagicMock()
        network.uuid = NETWORK_UUID
        network.state.value = 'created'
        network.ACTIVE_STATES = {'created'}
        self.mock_network_class = self._patch(
            'shakenfist.operations.net_op.Network')
        self.mock_network_class.from_db.return_value = network
        self.vxlan = self._patch(
            'shakenfist.operations.net_op.BridgedVXLanNetwork').return_value

    def _patch(self, target: str, **kwargs: Any) -> Any:
        p = mock.patch(target, **kwargs)
        started = p.start()
        self.addCleanup(p.stop)
        return started

    # -- the faked edges ------------------------------------------------

    def _set_state(self, object_type: ObjectType, object_uuid: str,
                   state: State) -> bool:
        if object_uuid in self.failing_state_writes:
            return False
        states = mariadb._get_object_states_table()
        with self.engine.connect() as conn:
            result = conn.execute(
                sa.update(states)
                .where(states.c.object_type == object_type)
                .where(states.c.object_uuid == object_uuid)
                .values(state_value=state.value,
                        update_time=state.update_time,
                        message=state.message))
            if result.rowcount == 0:
                conn.execute(sa.insert(states).values(
                    object_uuid=object_uuid, object_type=object_type,
                    state_value=state.value, update_time=state.update_time,
                    message=state.message))
            conn.commit()
        return True

    def _set_error(self, op_uuid: str, report: ErrorReport) -> bool:
        self.errors[str(op_uuid)] = report
        return True

    def _enqueue_work_item(self, queue_name: str, work_item: dict[str, Any],
                           delay: float = 0) -> None:
        self.enqueued.append((queue_name, work_item))

    # -- building and reading operations -------------------------------

    def _insert_net_op(self, op_uuid: str, tasks: tuple[str, ...],
                       node_uuid: Optional[str], priority: str,
                       created_at: float = 100.0,
                       depends_on: Optional[list[dict[str, str]]] = None
                       ) -> None:
        """A NetOp row complete enough for ``NetOp.from_db()`` to load."""
        ops = mariadb._get_cluster_operations_table()
        with self.engine.connect() as conn:
            self._insert_op(conn, op_uuid, tasks=tasks, node_uuid=node_uuid,
                            priority=priority, created_at=created_at)
            conn.execute(
                sa.update(ops)
                .where(ops.c.uuid == uuid.UUID(op_uuid))
                .values(metadata_json={
                    'tasks': list(tasks),
                    'network_uuid': NETWORK_UUID,
                    'node_uuid': node_uuid,
                    'priority': priority,
                    'request_id': None,
                    'depends_on': depends_on,
                    'runs_after': None,
                    'version': net_op_schema.current_version,
                }))
            conn.commit()

    def _load(self, op_uuid: str, queue_name: Optional[str] = None,
              batch_size: Optional[int] = None) -> NetOp:
        op = NetOp.from_db(op_uuid)
        self.assertIsNotNone(op)
        if queue_name is not None:
            op.queue_name = queue_name
        op.dispatcher_batch_size = batch_size
        return op

    def _state_row(self, op_uuid: str) -> Any:
        states = mariadb._get_object_states_table()
        with self.engine.connect() as conn:
            return conn.execute(
                sa.select(states.c.state_value, states.c.message)
                .where(states.c.object_uuid == op_uuid)).fetchone()

    def _state_of(self, op_uuid: str) -> str:
        return self._state_row(op_uuid).state_value

    def _has_work_queue_row(self, op_uuid: str) -> bool:
        work_queue = mariadb._get_work_queue_table()
        with self.engine.connect() as conn:
            payloads = conn.execute(
                sa.select(work_queue.c.payload)).scalars().all()
        return any(p.get('operation_uuid') == op_uuid for p in payloads)

    def _per_node_pair(self) -> NetOp:
        """Proof [4]'s shape: the maintainer's background mesh repair is
        the survivor, and a user_facing ensure_mesh for the same network
        and node -- the one an instance create names in depends_on -- is
        the sibling it folds. #4033 is what lets the two coexist."""
        self._insert_net_op(SURVIVOR_UUID, (MESH_TASK,), NODE_A_UUID,
                            'background', created_at=100.0)
        self._insert_net_op(SIBLING_UUID, (MESH_TASK,), NODE_A_UUID,
                            'user_facing', created_at=150.0)
        return self._load(SURVIVOR_UUID, NODE_A_QUEUE_BACKGROUND, 2)

    def _run_through_network_dispatcher(self, op: Any, queue_name: str
                                        ) -> None:
        job = network_workitem.Job.__new__(network_workitem.Job)
        job.log = mock.MagicMock()
        job._cluster_operation_execute(queue_name, op, 1, {})

    def _dependent(self) -> mock.MagicMock:
        """An operation which, like instance_create, names the sibling in
        depends_on."""
        dependent = mock.MagicMock()
        dependent.uuid = DEPENDENT_UUID
        dependent.state.value = BaseClusterOperation.STATE_QUEUED
        dependent.is_outstanding.return_value = True
        dependent.depends_on = [
            {'op_type': 'net_op', 'op_uuid': SIBLING_UUID}]
        dependent.runs_after = []
        dependent.current_defer_count = 0
        return dependent


class FoldLifecycleTestCase(FoldHarness):
    """What a folded sibling, and everything waiting on it, sees."""

    # -- what a sibling reads while the survivor works -----------------

    def test_fold_leaves_sibling_executing_at_survivor_dispatch(self):
        # The regression for proof [4] of the issue: at the moment the
        # survivor's task runs, the sibling used to read complete.
        survivor = self._per_node_pair()
        seen: dict[str, Any] = {}
        self.vxlan._apply_ensure_mesh.side_effect = (
            lambda: seen.setdefault('row', self._state_row(SIBLING_UUID)))

        survivor.execute()

        self.assertEqual('ran', survivor.coalesce_outcome)
        self.assertEqual(1, survivor.coalesce_folded)
        self.assertEqual('executing', seen['row'].state_value)
        self.assertEqual(
            f'coalesced into {SURVIVOR_UUID}', seen['row'].message)

    def test_survivor_success_completes_folded_siblings(self):
        survivor = self._per_node_pair()

        survivor.execute()

        self.assertEqual('complete', self._state_of(SURVIVOR_UUID))
        row = self._state_row(SIBLING_UUID)
        self.assertEqual('complete', row.state_value)
        # The terminal row still says which operation did the work.
        self.assertEqual(f'coalesced into {SURVIVOR_UUID}', row.message)
        self.vxlan._apply_ensure_mesh.assert_called_once()
        # A waiter is released, without an error, only now.
        self.assertIsNone(
            self._load(SIBLING_UUID).raise_for_error(timeout=0))

    # -- every other survivor outcome leaves the sibling alone ---------

    def test_survivor_error_leaves_sibling_executing_with_row(self):
        survivor = self._per_node_pair()
        self.vxlan._apply_ensure_mesh.side_effect = Exception('fdb write')

        survivor.execute()

        self.assertEqual('error', self._state_of(SURVIVOR_UUID))
        self.assertEqual('executing', self._state_of(SIBLING_UUID))
        # The survivor's failure is the survivor's: nothing is written
        # against the sibling, which has not run yet.
        self.assertIn(SURVIVOR_UUID, self.errors)
        self.assertNotIn(SIBLING_UUID, self.errors)
        # The fold never touches work_queue, so the sibling can still run.
        self.assertTrue(self._has_work_queue_row(SIBLING_UUID))

    def test_survivor_defer_leaves_sibling_executing(self):
        # The benign interface race: EnsureMeshFailed on an active network
        # defers the survivor back to queued.
        survivor = self._per_node_pair()
        self.vxlan._apply_ensure_mesh.side_effect = (
            exceptions.EnsureMeshFailed('Cannot find device'))

        survivor.execute()

        self.assertEqual('queued', self._state_of(SURVIVOR_UUID))
        self.assertEqual(
            [NODE_A_QUEUE_BACKGROUND], [q for q, _ in self.enqueued])
        self.assertEqual('executing', self._state_of(SIBLING_UUID))
        self.assertTrue(self._has_work_queue_row(SIBLING_UUID))

        # The sibling's own row comes up first. Running itself, it folds
        # the requeued survivor in turn, and its success completes both.
        self.vxlan._apply_ensure_mesh.side_effect = None
        job = network_workitem.Job.__new__(network_workitem.Job)
        job.log = mock.MagicMock()
        job._cluster_operation_execute(
            NODE_A_QUEUE_USER_FACING, self._load(SIBLING_UUID), 2, {})

        self.assertEqual('complete', self._state_of(SIBLING_UUID))
        row = self._state_row(SURVIVOR_UUID)
        self.assertEqual('complete', row.state_value)
        self.assertEqual(f'coalesced into {SIBLING_UUID}', row.message)
        self.assertEqual(2, self.vxlan._apply_ensure_mesh.call_count)

        # The survivor's requeued work item is then skipped as terminal.
        self._run_through_network_dispatcher(
            self._load(SURVIVOR_UUID), NODE_A_QUEUE_BACKGROUND)
        self.assertEqual(2, self.vxlan._apply_ensure_mesh.call_count)

    def test_survivor_raise_leaves_sibling_executing(self):
        survivor = self._per_node_pair()
        self.mock_network_class.from_db.return_value = None

        self.assertRaises(NoSuchNetwork, survivor.execute)

        self.assertEqual('executing', self._state_of(SIBLING_UUID))
        self.assertTrue(self._has_work_queue_row(SIBLING_UUID))

    def test_multi_task_survivor_failing_first_task_does_not_complete_sibling(
            self):
        # Network creation enqueues [create_network_node, ensure_mesh] on
        # the cluster-wide queue, and folds a cluster-wide ensure_mesh. If
        # the first task fails the mesh task never runs, so neither does
        # the sibling's work.
        self._insert_net_op(SURVIVOR_UUID, (CREATE_TASK, MESH_TASK), None,
                            'user_facing')
        self._insert_net_op(SIBLING_UUID, (MESH_TASK,), None, 'user_facing',
                            created_at=150.0)
        survivor = self._load(SURVIVOR_UUID, CLUSTER_WIDE_QUEUE, 2)
        self.vxlan._apply_create_on_network_node.side_effect = (
            Exception('dnsmasq'))

        survivor.execute()

        self.assertEqual(1, survivor.coalesce_folded)
        self.vxlan._apply_ensure_mesh.assert_not_called()
        self.assertEqual('error', self._state_of(SURVIVOR_UUID))
        self.assertEqual('executing', self._state_of(SIBLING_UUID))

    # -- waiters follow the sibling's own outcome ----------------------

    def test_dependent_defers_on_folded_dependency(self):
        # Both dispatchers, asked to run a dependent while the survivor is
        # mid-task, must wait rather than proceed.
        survivor = self._per_node_pair()
        outcomes: dict[str, mock.MagicMock] = {}

        def run_dependents() -> None:
            dependent = self._dependent()
            self._run_through_network_dispatcher(
                dependent, NODE_A_QUEUE_USER_FACING)
            outcomes['sf-net'] = dependent

            dependent = self._dependent()
            job = queues_workitem.Job.__new__(queues_workitem.Job)
            job.queue_name = f'{NODE_A_UUID}-clusteroperation-user_facing'
            job.batch_size = None
            job.log = mock.MagicMock()
            job.workitem = {'operation_type': 'node_inst_netdesc_op',
                            'operation_uuid': DEPENDENT_UUID,
                            'defer_count': 0}
            dependent_class = mock.MagicMock()
            dependent_class.from_db.return_value = dependent
            with mock.patch(
                    'shakenfist.daemons.queues.workitem.get_object_class',
                    side_effect=lambda t: (
                        dependent_class if t == 'node_inst_netdesc_op'
                        else constants.get_object_class(t))):
                job._cluster_operation_execute()
            outcomes['sf-queues'] = dependent

        self.vxlan._apply_ensure_mesh.side_effect = run_dependents
        survivor.execute()

        for dispatcher, dependent in outcomes.items():
            with self.subTest(dispatcher=dispatcher):
                dependent.defer.assert_called_once()
                (waited_on,) = dependent.defer.call_args.kwargs['waiting_on']
                self.assertEqual(SIBLING_UUID, str(waited_on.uuid))
                dependent.execute.assert_not_called()
                dependent.abort_for_unsuitable_dependency.assert_not_called()
        self.assertEqual({'sf-net', 'sf-queues'}, set(outcomes))

    def test_dependent_aborts_when_self_run_sibling_errors(self):
        survivor = self._per_node_pair()
        self.vxlan._apply_ensure_mesh.side_effect = Exception('fdb write')
        survivor.execute()

        dependent = self._dependent()
        self._run_through_network_dispatcher(
            dependent, NODE_A_QUEUE_USER_FACING)
        dependent.defer.assert_called_once()
        dependent.abort_for_unsuitable_dependency.assert_not_called()

        # The sibling's own work item comes up and it runs, and fails, by
        # itself.
        self._run_through_network_dispatcher(
            self._load(SIBLING_UUID), NODE_A_QUEUE_USER_FACING)
        self.assertEqual('error', self._state_of(SIBLING_UUID))

        dependent = self._dependent()
        self._run_through_network_dispatcher(
            dependent, NODE_A_QUEUE_USER_FACING)
        dependent.abort_for_unsuitable_dependency.assert_called_once()
        (dep_op,) = dependent.abort_for_unsuitable_dependency.call_args.args
        self.assertEqual(SIBLING_UUID, str(dep_op.uuid))
        dependent.execute.assert_not_called()

    def test_raise_for_error_on_sibling_follows_real_outcome(self):
        survivor = self._per_node_pair()
        self.vxlan._apply_ensure_mesh.side_effect = Exception('fdb write')
        survivor.execute()

        # Not done, so a waiter keeps waiting rather than returning.
        self.assertRaises(
            exceptions.OperationTimeout,
            self._load(SIBLING_UUID).raise_for_error, timeout=0)

        self._run_through_network_dispatcher(
            self._load(SIBLING_UUID), NODE_A_QUEUE_USER_FACING)

        with self.assertRaises(exceptions.NetworkOperationFailed) as ctx:
            self._load(SIBLING_UUID).raise_for_error(timeout=0)
        # The report is the sibling's own, from its own run.
        self.assertIs(self.errors[SIBLING_UUID], ctx.exception.error_report)
        self.assertEqual(2, self.vxlan._apply_ensure_mesh.call_count)

    # -- completing the siblings ---------------------------------------

    def test_propagation_skips_sibling_no_longer_executing(self):
        survivor = self._per_node_pair()

        def delete_sibling() -> None:
            self._load(SIBLING_UUID).state = 'deleted'

        self.vxlan._apply_ensure_mesh.side_effect = delete_sibling
        survivor.log = mock.MagicMock()
        survivor.execute()

        self.assertEqual('complete', self._state_of(SURVIVOR_UUID))
        self.assertEqual('deleted', self._state_of(SIBLING_UUID))
        # A sibling which ended some other way is skipped, not reported
        # as a failure to complete it: deletion mid-run is ordinary.
        survivor.log.with_fields.return_value.warning.assert_not_called()

    def test_propagation_failure_does_not_fail_survivor(self):
        real_get = mariadb._direct_get_cluster_operation

        def unreadable_sibling(op_uuid: Any) -> Any:
            if str(op_uuid) == SIBLING_UUID:
                raise exceptions.DatabaseUnavailable('gone')
            return real_get(op_uuid)

        def unexpected_error(op_uuid: Any) -> Any:
            if str(op_uuid) == SIBLING_UUID:
                raise ValueError('a row this code cannot parse')
            return real_get(op_uuid)

        for failure in ('state write', 'database unavailable',
                        'unexpected error'):
            with self.subTest(failure=failure):
                self.engine = self._build_engine()
                self.mock_get_engine.return_value = self.engine
                survivor = self._per_node_pair()

                if failure == 'state write':
                    self.failing_state_writes = {SIBLING_UUID}
                    survivor.execute()
                    self.failing_state_writes = set()
                else:
                    with mock.patch(
                            'shakenfist.mariadb._direct_get_cluster_operation',
                            side_effect=(unreadable_sibling
                                         if failure == 'database unavailable'
                                         else unexpected_error)):
                        survivor.execute()

                self.assertEqual('complete', self._state_of(SURVIVOR_UUID))
                # Left to run itself, exactly as after a survivor failure.
                self.assertEqual('executing', self._state_of(SIBLING_UUID))
                self.assertTrue(self._has_work_queue_row(SIBLING_UUID))

    def test_propagation_failure_still_completes_other_siblings(self):
        # Fail each sibling in turn, so that whichever order the fold
        # returns them in, one run has the failure come first.
        for failing in (SIBLING_UUID, SECOND_SIBLING_UUID):
            with self.subTest(failing=failing):
                self.engine = self._build_engine()
                self.mock_get_engine.return_value = self.engine
                self._per_node_pair()
                self._insert_net_op(SECOND_SIBLING_UUID, (MESH_TASK,),
                                    NODE_A_UUID, 'user_facing',
                                    created_at=160.0)
                survivor = self._load(SURVIVOR_UUID,
                                      NODE_A_QUEUE_BACKGROUND, 3)
                other = (SECOND_SIBLING_UUID if failing == SIBLING_UUID
                         else SIBLING_UUID)
                real_get = mariadb._direct_get_cluster_operation

                def unexpected_error(op_uuid: Any) -> Any:
                    if str(op_uuid) == failing:
                        raise ValueError('a row this code cannot parse')
                    return real_get(op_uuid)

                with mock.patch(
                        'shakenfist.mariadb._direct_get_cluster_operation',
                        side_effect=unexpected_error):
                    survivor.execute()

                self.assertEqual(2, survivor.coalesce_folded)
                self.assertEqual('complete', self._state_of(SURVIVOR_UUID))
                self.assertEqual('executing', self._state_of(failing))
                self.assertEqual('complete', self._state_of(other))

    def test_crash_between_fold_and_propagation_self_heals(self):
        survivor = self._per_node_pair()
        with mock.patch.object(BaseClusterOperation,
                               '_complete_folded_siblings',
                               side_effect=_Crash):
            self.assertRaises(_Crash, survivor.execute)
        self.assertEqual('complete', self._state_of(SURVIVOR_UUID))
        self.assertEqual('executing', self._state_of(SIBLING_UUID))

        self._run_through_network_dispatcher(
            self._load(SIBLING_UUID), NODE_A_QUEUE_USER_FACING)

        self.assertEqual('complete', self._state_of(SIBLING_UUID))
        self.assertEqual(2, self.vxlan._apply_ensure_mesh.call_count)

    # -- an old sf-database -------------------------------------------

    def test_fold_rpc_unimplemented_skips_fold(self):
        real_grpc_fold = mariadb._grpc_fold_coalescible_siblings
        survivor = self._per_node_pair()
        seen: dict[str, str] = {}
        self.vxlan._apply_ensure_mesh.side_effect = (
            lambda: seen.setdefault('state', self._state_of(SIBLING_UUID)))

        with mock.patch('shakenfist.mariadb.fold_coalescible_siblings',
                        side_effect=real_grpc_fold), \
                mock.patch('shakenfist.mariadb._get_database_stub') as stub, \
                mock.patch('shakenfist.mariadb._grpc_call',
                           side_effect=FakeRpcError(
                               grpc.StatusCode.UNIMPLEMENTED)) as call:
            survivor.execute()

        self.assertEqual('coalescing_unavailable', survivor.coalesce_outcome)
        # One call, to the fold: no fallback to an RPC which would mark
        # the sibling complete up front.
        call.assert_called_once()
        self.assertIs(
            stub.return_value.FoldCoalescibleSiblings, call.call_args.args[0])
        self.assertEqual('queued', seen['state'])
        self.assertEqual('queued', self._state_of(SIBLING_UUID))
        self.assertEqual('complete', self._state_of(SURVIVOR_UUID))

    def test_fold_rpc_sends_key_and_returns_folded_uuids(self):
        # A None key value travels as is_null, not as an empty uuid, so a
        # cluster-wide op folds only other cluster-wide ops.
        reply = database_pb2.ClaimCoalescibleSiblingsReply(
            folded_op_uuids=[SIBLING_UUID])
        with mock.patch('shakenfist.mariadb._get_database_stub') as stub, \
                mock.patch('shakenfist.mariadb._grpc_call',
                           return_value=reply) as call:
            folded = mariadb._grpc_fold_coalescible_siblings(
                'net_op',
                [('network_uuid', NETWORK_UUID), ('node_uuid', None)],
                [MESH_TASK], SURVIVOR_UUID)

        self.assertEqual([SIBLING_UUID], folded)
        rpc, request = call.call_args.args
        self.assertIs(stub.return_value.FoldCoalescibleSiblings, rpc)
        self.assertEqual('net_op', request.operation_type)
        self.assertEqual([MESH_TASK], list(request.task_names))
        self.assertEqual(SURVIVOR_UUID, request.exclude_op_uuid)
        self.assertEqual(
            [('network_uuid', NETWORK_UUID, False), ('node_uuid', '', True)],
            [(k.column, k.uuid, k.is_null) for k in request.keys])

    def test_fold_rpc_transient_error_runs_without_folding(self):
        # Any other gRPC failure is transient: the fold is skipped for
        # this run, but the database service does have the RPC, so it is
        # not reported as unavailable.
        real_grpc_fold = mariadb._grpc_fold_coalescible_siblings
        survivor = self._per_node_pair()

        with mock.patch('shakenfist.mariadb.fold_coalescible_siblings',
                        side_effect=real_grpc_fold), \
                mock.patch('shakenfist.mariadb._get_database_stub'), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           side_effect=FakeRpcError(
                               grpc.StatusCode.UNAVAILABLE)):
            survivor.execute()

        self.assertEqual('ran', survivor.coalesce_outcome)
        self.assertEqual(0, survivor.coalesce_folded)
        self.assertEqual('queued', self._state_of(SIBLING_UUID))
        self.assertEqual('complete', self._state_of(SURVIVOR_UUID))

    # -- the #4303 orphan pass ----------------------------------------

    def test_orphan_stmt_ignores_executing_sibling_with_row(self):
        # A sibling left executing by a failed survivor still has its
        # work_queue row, so it is not an orphan however long it waits.
        survivor = self._per_node_pair()
        self.vxlan._apply_ensure_mesh.side_effect = Exception('fdb write')
        survivor.execute()

        states = mariadb._get_object_states_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(states)
                         .where(states.c.object_uuid == SIBLING_UUID)
                         .values(update_time=100.0))
            conn.commit()
        self.assertEqual('executing', self._state_of(SIBLING_UUID))

        orphans = mariadb._direct_list_orphaned_cluster_operations(1800.0)
        self.assertNotIn(SIBLING_UUID, [o['uuid'] for o in orphans])

        # The control: it is the row, not the state, that protects it.
        work_queue = mariadb._get_work_queue_table()
        with self.engine.connect() as conn:
            for row in conn.execute(sa.select(work_queue)).fetchall():
                if row.payload.get('operation_uuid') == SIBLING_UUID:
                    conn.execute(sa.delete(work_queue)
                                 .where(work_queue.c.id == row.id))
            conn.commit()
        orphans = mariadb._direct_list_orphaned_cluster_operations(1800.0)
        self.assertIn(SIBLING_UUID, [o['uuid'] for o in orphans])


class FoldCircuitBreakerTestCase(FoldHarness):
    """The network maintainer's circuit breaker reads terminal NetOp states
    on the network. A sibling marked complete at fold time reads as a
    success there even when the survivor failed, and so resets a streak
    of failed repairs."""

    CIRCUIT_K = 5

    def _build_engine(self) -> sa.Engine:
        return self.build_engine(
            [mariadb._get_object_states_table,
             mariadb._get_cluster_operations_table,
             mariadb._get_work_queue_table,
             mariadb._get_cluster_operation_targets_table],
            json_shims=True, autoincrement_bigint_keys=True)

    def _target(self, op_uuid: str) -> None:
        targets = mariadb._get_cluster_operation_targets_table()
        with self.engine.connect() as conn:
            conn.execute(sa.insert(targets).values(
                operation_uuid=op_uuid, operation_type='net_op',
                target_object_type=ObjectType.NETWORK,
                target_uuid=NETWORK_UUID, created_at=100.0))
            conn.commit()

    def _guard_fires_after_a_failed_survivor(self) -> bool:
        states = mariadb._get_object_states_table()
        for i in range(self.CIRCUIT_K - 1):
            failed = f'eeeeeeee-eeee-4eee-8eee-{i:012d}'
            self._insert_net_op(failed, (MESH_TASK,), NODE_A_UUID,
                                'background', created_at=10.0 + i)
            with self.engine.connect() as conn:
                conn.execute(sa.update(states)
                             .where(states.c.object_uuid == failed)
                             .values(state_value='error',
                                     update_time=10.0 + i))
                conn.commit()
            self._target(failed)

        survivor = self._per_node_pair()
        self._target(SURVIVOR_UUID)
        self._target(SIBLING_UUID)
        self.vxlan._apply_ensure_mesh.side_effect = Exception('fdb write')
        survivor.execute()
        self.assertEqual('error', self._state_of(SURVIVOR_UUID))

        n = mock.MagicMock()
        n.uuid = NETWORK_UUID
        job = network_maintain.Job.__new__(network_maintain.Job)
        with mock.patch(
                'shakenfist.daemons.network.maintain.config') as mock_config:
            # No cooldown, so only the circuit breaker can fire.
            mock_config.MAINTAIN_RECONCILE_COOLDOWN_SECONDS = 0
            mock_config.MAINTAIN_RECONCILE_CIRCUIT_K = self.CIRCUIT_K
            return job._reconciliation_guard_fired(n, 'net_op')

    def test_folded_sibling_does_not_break_circuit_breaker_streak(self):
        self.assertTrue(self._guard_fires_after_a_failed_survivor())
        self.assertEqual('executing', self._state_of(SIBLING_UUID))

    def test_completing_at_fold_time_would_break_the_streak(self):
        # The control, run with the fold the V2 RPC still does.
        with mock.patch(
                'shakenfist.mariadb.fold_coalescible_siblings',
                side_effect=mariadb._direct_claim_coalescible_siblings):
            self.assertFalse(self._guard_fires_after_a_failed_survivor())
        self.assertEqual('complete', self._state_of(SIBLING_UUID))


class FoldServicerTestCase(CoalescingTables, base.ShakenFistTestCase):
    """The two servicers which fold, against the same rows."""

    def setUp(self) -> None:
        super().setUp()
        self.engine = self._build_engine()
        p = mock.patch('shakenfist.mariadb._get_engine',
                       return_value=self.engine)
        p.start()
        self.addCleanup(p.stop)

        with self.engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID, node_uuid=NODE_A_UUID)
            self._insert_op(conn, SIBLING_UUID, node_uuid=NODE_A_UUID)
            conn.commit()

        self.monitor = mock.MagicMock()
        self.monitor.counters = {
            'fold_coalescible_siblings': mock.MagicMock(),
            'claim_coalescible_siblings_v2': mock.MagicMock(),
        }
        self.servicer = database_main.DatabaseService(self.monitor)
        self.request = database_pb2.ClaimCoalescibleSiblingsV2Request(
            operation_type='net_op',
            keys=[database_pb2.CoalescibleKeyPair(
                      column='network_uuid', uuid=NETWORK_UUID),
                  database_pb2.CoalescibleKeyPair(
                      column='node_uuid', uuid=NODE_A_UUID)],
            task_names=[TASK],
            exclude_op_uuid=SURVIVOR_UUID)

    def _rows(self) -> dict[str, Any]:
        states = mariadb._get_object_states_table()
        with self.engine.connect() as conn:
            return {
                r.object_uuid: r for r in conn.execute(sa.select(
                    states.c.object_uuid, states.c.state_value,
                    states.c.message)).fetchall()}

    def test_fold_servicer_sets_executing_with_survivor_message(self):
        reply = self.servicer.FoldCoalescibleSiblings(
            self.request, mock.MagicMock())

        self.assertEqual([SIBLING_UUID], list(reply.folded_op_uuids))
        rows = self._rows()
        self.assertEqual('executing', rows[SIBLING_UUID].state_value)
        self.assertEqual(
            f'coalesced into {SURVIVOR_UUID}', rows[SIBLING_UUID].message)
        self.assertEqual('queued', rows[SURVIVOR_UUID].state_value)
        self.monitor.counters['fold_coalescible_siblings'].inc.\
            assert_called_once()
        self.monitor.counters['claim_coalescible_siblings_v2'].inc.\
            assert_not_called()

        # And the counter is one the Monitor actually registers, because
        # its list is hand maintained.
        with mock.patch.object(database_main.daemon.WorkerPoolDaemon,
                               '__init__', return_value=None), \
                mock.patch.object(database_main, 'start_http_server'), \
                mock.patch.object(database_main, 'Gauge'), \
                mock.patch.object(database_main, 'Counter'):
            monitor = database_main.Monitor('test')
        self.assertIn('fold_coalescible_siblings', monitor.counters)

    def test_v2_servicer_still_completes(self):
        # A client from before the fold RPC keeps the contract it was
        # built against until V2 is retired (#4639).
        reply = self.servicer.ClaimCoalescibleSiblingsV2(
            self.request, mock.MagicMock())

        self.assertEqual([SIBLING_UUID], list(reply.folded_op_uuids))
        rows = self._rows()
        self.assertEqual('complete', rows[SIBLING_UUID].state_value)
        self.assertEqual('coalesced into sibling op',
                         rows[SIBLING_UUID].message)
        self.monitor.counters['claim_coalescible_siblings_v2'].inc.\
            assert_called_once()
        self.monitor.counters['fold_coalescible_siblings'].inc.\
            assert_not_called()

    def test_an_unknown_folded_state_is_refused(self):
        self.assertRaises(
            ValueError, mariadb._direct_claim_coalescible_siblings,
            'net_op', [('network_uuid', NETWORK_UUID)], [TASK],
            SURVIVOR_UUID, folded_state='queued')


class RetiredSingleColumnRpcsTestCase(base.ShakenFistTestCase):
    """The single-column coalescing RPCs are gone (#4639).

    ``ClaimCoalescibleSiblings`` and ``FindExistingCoalescibleOp`` took
    one (column, uuid) pair, and their handlers accepted any task name
    with it, so a client could fold ``network_ensure_mesh`` across nodes
    through them. No release ever carried them (every tagged release
    predates NetOp), so nothing can still send them. The V2 names stay,
    because the suffix is what stops an old server folding on the first
    column of a wider key.
    """

    def test_the_proto_no_longer_carries_them(self):
        methods = database_pb2.DESCRIPTOR.services_by_name[
            'DatabaseService'].methods_by_name
        for name in ('ClaimCoalescibleSiblings', 'FindExistingCoalescibleOp'):
            self.assertNotIn(name, methods)
            self.assertFalse(
                hasattr(database_pb2_grpc.DatabaseServiceServicer, name), name)
        for message in ('ClaimCoalescibleSiblingsRequest',
                        'FindExistingCoalescibleOpRequest'):
            self.assertFalse(hasattr(database_pb2, message), message)
        self.assertIn('ClaimCoalescibleSiblingsV2', methods)
        self.assertIn('FindExistingCoalescibleOpV2', methods)
        self.assertIn('FoldCoalescibleSiblings', methods)

    def test_the_servicer_and_its_counters_no_longer_carry_them(self):
        for name in ('ClaimCoalescibleSiblings', 'FindExistingCoalescibleOp'):
            self.assertFalse(
                hasattr(database_main.DatabaseService, name), name)
        source = inspect.getsource(database_main.Monitor.__init__)
        self.assertNotIn("'claim_coalescible_siblings'", source)
        self.assertNotIn("'find_existing_coalescible_op'", source)
        self.assertIn("'claim_coalescible_siblings_v2'", source)
        self.assertIn("'find_existing_coalescible_op_v2'", source)
