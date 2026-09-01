# Copyright 2026 Michael Still and contributors
#
# Direct SQL tests for the cluster operation coalescing primitives
# (issue #3878).
#
# These execute the queries against a real database rather than a mocked
# engine. That distinction is the whole point of the module: the existing
# coalescing tests mock either ``mariadb.claim_coalescible_siblings`` (in
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

import uuid
from unittest import mock

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.tests import base
from shakenfist.tests import dbfixture


NETWORK_UUID = '11111111-1111-4111-8111-111111111111'
SURVIVOR_UUID = '99999999-9999-4999-8999-999999999999'
SIBLING_UUID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
TASK = 'network_apply_update_dnsmasq'

# Two per-node NetOp keys, plus the cluster-wide (None) case. These stand
# in for the two hypervisors in the phase 8 regression
# (test_single_virtual_networks_work): a fold on node A's queue must
# never touch node B's work on the same network.
NODE_A_UUID = 'c0000000-0000-4000-8000-000000000001'
NODE_B_UUID = 'c0000000-0000-4000-8000-000000000002'


class CoalescingSQLTestCase(
        dbfixture.MariaDBTableFixture, base.ShakenFistTestCase):
    """The coalescing queries, run against a real database."""

    def _build_engine(self):
        return self.build_engine(
            [mariadb._get_object_states_table,
             mariadb._get_cluster_operations_table],
            json_shims=True)

    def _insert_op(self, conn, op_uuid, tasks=(TASK,), state='queued',
                   created_at=100.0, node_uuid=None,
                   priority='user_facing'):
        """Insert one cluster operation and its state row.

        The two rows deliberately go in the way production writes them:
        the static row through the sa.Uuid bind processor, which stores
        the undashed form, and the state row as the dashed string every
        caller hands to set_state().

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

    # -- Phase 11: the two-pair (network_uuid, node_uuid) key -----------
    #
    # These are the tests decision 8 exists to be proven by: a key that
    # names node_uuid must be strictly narrower than the network alone,
    # in both directions -- a per-node op folds only its own node's
    # siblings, and a cluster-wide op (node_uuid unset, binding IS NULL)
    # folds only other cluster-wide ops. Getting either direction wrong
    # reproduces one of two real failures: the phase 8
    # ``test_single_virtual_networks_work`` regression (one hypervisor's
    # fold silently eating another's work) or decision 8's near-miss
    # (widening the key to include node_uuid would have switched off the
    # only coalescing the cluster does, by refusing every NULL-valued
    # key instead of binding IS NULL).

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
        # The phase 8 regression this whole phase exists to prevent
        # recurring: node A's survivor must not fold node B's op on the
        # same network, or B's mesh apply is silently never run.
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
        # this is the pre-phase-11 behaviour: the two ops that actually
        # fold today (network_apply_update_dnsmasq,
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
        # The regression decision 8 exists to prevent: if a NULL key
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
        # The enqueue-side counterpart of the phase 8 regression: a
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
