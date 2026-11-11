# Copyright 2026 Michael Still and contributors
#
# The per-object cluster operation listing, against real SQL.
#
# DatabaseBackedObjectWithOperations.get_cluster_operations() backs the
# /instances|networks|artifacts/<ref>/clusteroperations endpoints. It lists
# every operation whose target rows name the object, newest first, each with
# the observational flag stored on its row, and nothing that only targets
# some other object. These tests enqueue operations through the production
# schema models into sqlite copies of the cluster operation tables (see
# test_observational_current_state for the fixture), then hydrate them
# through the production from_db path.

from unittest import mock
from uuid import UUID
from uuid import uuid4

import sqlalchemy as sa

from shakenfist.baseobject import DatabaseBackedObjectWithOperations
from shakenfist.baseobject import targeted_cluster_operation_views
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.operations.node_inst_op import NodeInstOp
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations import node_inst_net_iface_op
from shakenfist.schema.operations import node_inst_op
from shakenfist.schema.operations import node_net_op
from shakenfist.tests import base
from shakenfist.tests.test_observational_current_state import ClusterOperationTablesFixture


NODE_UUID = 'aaaa3333-3333-4333-8333-333333333333'
INSTANCE_UUID = 'bbbb3333-3333-4333-8333-333333333333'
NETWORK_UUID = 'cccc3333-3333-4333-8333-333333333333'
INTERFACE_UUID = 'dddd3333-3333-4333-8333-333333333333'

SWEEP_TASKS = [
    node_inst_op.model_tasks.collect_billing_statistics,
    node_inst_op.model_tasks.health_check_kvm_process,
]
MUTATING_TASKS = [node_inst_op.model_tasks.instance_delete]


class _Instance(DatabaseBackedObjectWithOperations):
    """Just enough of an object with operations to be a target."""
    object_type = ObjectType.INSTANCE


class _Network(DatabaseBackedObjectWithOperations):
    object_type = ObjectType.NETWORK


class ObjectClusterOperationsTestCase(
        ClusterOperationTablesFixture, base.ShakenFistTestCase):

    def _enqueue_inst(self, tasks, instance_uuid=INSTANCE_UUID):
        _, op_uuid = node_inst_op.create_and_enqueue(
            NODE_UUID, instance_uuid, tasks, PRIORITY.background)
        return op_uuid

    def _uuids(self, views):
        return [v['uuid'] for v in views]

    def test_success_criterion(self):
        # A queued mutating op, then a later mutating op which completes.
        # The pointer moves to the completed op, which used to hide the
        # queued one from the listing.
        queued = self._enqueue_inst(MUTATING_TASKS)
        completed = self._enqueue_inst(MUTATING_TASKS)
        self._set_op_state(completed, 'complete')

        instance = _Instance(INSTANCE_UUID)
        self.assertEqual(
            completed, instance.last_cluster_operation['op_uuid'])

        views = instance.cluster_operation_views()
        self.assertEqual([queued], self._uuids(views))
        self.assertEqual('queued', views[0]['state'])
        self.assertFalse(views[0]['observational'])

        views = instance.cluster_operation_views(outstanding_only=False)
        self.assertEqual([completed, queued], self._uuids(views))
        self.assertEqual(['complete', 'queued'], [v['state'] for v in views])

    def test_newest_first(self):
        ops = [self._enqueue_inst(MUTATING_TASKS) for _ in range(4)]
        targeted = _Instance(INSTANCE_UUID).get_cluster_operations()
        self.assertEqual(list(reversed(ops)),
                         [str(t.op.uuid) for t in targeted])

    def test_queued_sweep_is_listed_as_observational(self):
        sweep = self._enqueue_inst(SWEEP_TASKS)

        views = _Instance(INSTANCE_UUID).cluster_operation_views()
        self.assertEqual(
            [{
                'operation_type': 'node_inst_op',
                'uuid': sweep,
                'state': 'queued',
                'tasks': [t.name for t in SWEEP_TASKS],
                'node_uuid': NODE_UUID,
                'instance_uuid': INSTANCE_UUID,
                'observational': True,
            }],
            views)

    def test_pending_field_agrees_with_listing(self):
        # Each mix is a list of (tasks, state) enqueued in order against a
        # fresh instance. The pending field is true exactly when the default
        # listing holds a mutating op.
        mixes = {
            'nothing': [],
            'queued sweep': [(SWEEP_TASKS, 'queued')],
            'queued mutation': [(MUTATING_TASKS, 'queued')],
            'executing mutation': [(MUTATING_TASKS, 'executing')],
            'finished mutation, queued sweep': [
                (MUTATING_TASKS, 'complete'), (SWEEP_TASKS, 'queued')],
            'queued mutation, executing sweep': [
                (MUTATING_TASKS, 'queued'), (SWEEP_TASKS, 'executing')],
            'queued mutation, later finished mutation': [
                (MUTATING_TASKS, 'queued'), (MUTATING_TASKS, 'complete')],
            'errored and aborted mutations': [
                (MUTATING_TASKS, 'error'), (MUTATING_TASKS, 'abort')],
        }
        seen = set()
        for name, mix in mixes.items():
            with self.subTest(mix=name):
                instance_uuid = str(uuid4())
                for tasks, state in mix:
                    op_uuid = self._enqueue_inst(
                        tasks, instance_uuid=instance_uuid)
                    if state != 'queued':
                        self._set_op_state(op_uuid, state)

                instance = _Instance(instance_uuid)
                pending = instance.has_pending_cluster_operation()
                self.assertEqual(
                    pending,
                    any(not v['observational']
                        for v in instance.cluster_operation_views()))
                seen.add(pending)

        # Both answers were exercised, so the equality is not vacuous.
        self.assertEqual({True, False}, seen)

    def test_hard_deleted_op_is_skipped_without_audit(self):
        gone = self._enqueue_inst(MUTATING_TASKS)
        kept = self._enqueue_inst(MUTATING_TASKS)

        # Delete the op's row after its target row has been read, as the
        # cleaner can between the two reads.
        read_targets = mariadb.get_cluster_operation_targets_for_object
        read = []

        def read_then_delete(*args, **kwargs):
            rows = read_targets(*args, **kwargs)
            read.extend(r.operation_uuid for r in rows)
            ops = mariadb._get_cluster_operations_table()
            with self.engine.begin() as conn:
                conn.execute(sa.delete(ops).where(ops.c.uuid == UUID(gone)))
            return rows

        with mock.patch(
                'shakenfist.mariadb.get_cluster_operation_targets_for_object',
                side_effect=read_then_delete), \
                mock.patch('shakenfist.eventlog.add_event') as add_event:
            targeted = _Instance(INSTANCE_UUID).get_cluster_operations()

        self.assertEqual({gone, kept}, set(read))
        self.assertEqual([kept], [str(t.op.uuid) for t in targeted])
        add_event.assert_not_called()

    def test_target_row_of_unknown_type_is_skipped(self):
        unknown = self._enqueue_inst(MUTATING_TASKS)
        kept = self._enqueue_inst(MUTATING_TASKS)

        targets = mariadb._get_cluster_operation_targets_table()
        with self.engine.begin() as conn:
            result = conn.execute(
                sa.update(targets)
                .where(targets.c.operation_uuid == unknown)
                .values(operation_type='no_such_op'))
        self.assertEqual(1, result.rowcount)

        instance = _Instance(INSTANCE_UUID)
        for outstanding_only in (True, False):
            with self.subTest(outstanding_only=outstanding_only):
                self.assertEqual(
                    [kept],
                    self._uuids(instance.cluster_operation_views(
                        outstanding_only=outstanding_only)))

    def test_network_op_the_instance_op_depends_on_is_not_listed(self):
        _, net_op = node_net_op.create_and_enqueue(
            NODE_UUID, NETWORK_UUID,
            [node_net_op.model_tasks.network_apply_create_hypervisor],
            PRIORITY.user_facing)
        _, plug_op = node_inst_net_iface_op.create_and_enqueue(
            NODE_UUID, INSTANCE_UUID, NETWORK_UUID, INTERFACE_UUID,
            [node_inst_net_iface_op.model_tasks.attach_instance_interface],
            PRIORITY.user_facing,
            depends_on=[{'op_type': ObjectType.NODE_NET_OP,
                         'op_uuid': net_op}])

        instance = _Instance(INSTANCE_UUID)
        targeted = instance.get_cluster_operations()
        self.assertEqual([plug_op], [str(t.op.uuid) for t in targeted])
        self.assertEqual(net_op, targeted[0].op.depends_on[0]['op_uuid'])

        # The network op is in the network's listing, behind the hot-plug
        # which also targets the network.
        self.assertEqual(
            [plug_op, net_op],
            self._uuids(_Network(NETWORK_UUID).cluster_operation_views()))

    def test_terminal_rows_are_not_hydrated(self):
        # The state filter runs in the query, so a finished op costs no
        # operation read. The listing would be the same without it; only
        # the number of reads differs.
        finished = self._enqueue_inst(MUTATING_TASKS)
        self._set_op_state(finished, 'complete')
        queued = self._enqueue_inst(MUTATING_TASKS)

        instance = _Instance(INSTANCE_UUID)
        for outstanding_only, reads, expected in (
                (True, 1, [queued]), (False, 2, [queued, finished])):
            with self.subTest(outstanding_only=outstanding_only):
                with mock.patch.object(
                        NodeInstOp, 'from_db',
                        side_effect=NodeInstOp.from_db) as from_db:
                    views = instance.cluster_operation_views(
                        outstanding_only=outstanding_only)
                self.assertEqual(expected, self._uuids(views))
                self.assertEqual(reads, from_db.call_count)

    def test_limit_caps_the_listing_and_the_reads_it_costs(self):
        # The listing was unbounded: with all=true it hydrated every
        # retained target row, and an errored operation's rows are kept
        # for seven days (issue 4667). The cap is applied to the rows
        # before they are hydrated, so it bounds the operation reads and
        # not merely the reply.
        ops = [self._enqueue_inst(MUTATING_TASKS) for _ in range(5)]
        instance = _Instance(INSTANCE_UUID)

        with mock.patch.object(NodeInstOp, 'from_db',
                               side_effect=NodeInstOp.from_db) as from_db:
            views = instance.cluster_operation_views(limit=2)
        self.assertEqual(list(reversed(ops))[:2], self._uuids(views))
        self.assertEqual(2, from_db.call_count)

        # An uncapped read is the default for the operation objects,
        # because Instance.enqueue_delete() aborts every outstanding
        # operation it finds and a dropped row there would leave one
        # running against an instance being torn down.
        self.assertEqual(
            list(reversed(ops)),
            [str(t.op.uuid) for t in instance.get_cluster_operations()])

    def test_both_listing_routes_give_the_same_answer(self):
        # GET /clusteroperations?target_object_type=&target_uuid= is
        # targeted_cluster_operation_views() and the per-object routes
        # are cluster_operation_views(); the two used to be separate
        # implementations which ordered differently and disagreed about
        # the observational key (issue 4667).
        self._enqueue_inst(SWEEP_TASKS)
        self._set_op_state(self._enqueue_inst(MUTATING_TASKS), 'complete')
        self._enqueue_inst(MUTATING_TASKS)

        instance = _Instance(INSTANCE_UUID)
        for outstanding_only in (True, False):
            with self.subTest(outstanding_only=outstanding_only):
                views = instance.cluster_operation_views(
                    outstanding_only=outstanding_only)
                self.assertNotEqual([], views)
                self.assertEqual(
                    views,
                    targeted_cluster_operation_views(
                        ObjectType.INSTANCE, INSTANCE_UUID,
                        outstanding_only=outstanding_only))

    def test_in_memory_object_lists_nothing(self):
        self._enqueue_inst(MUTATING_TASKS)
        with mock.patch(
                'shakenfist.mariadb.get_cluster_operation_targets_for_object'
        ) as targets:
            instance = _Instance(INSTANCE_UUID, in_memory_only=True)
            self.assertEqual([], instance.get_cluster_operations())
            self.assertEqual([], instance.cluster_operation_views())
        targets.assert_not_called()


class PendingClusterOperationDependenciesTestCase(
        ClusterOperationTablesFixture, base.ShakenFistTestCase):
    """The runs_after list which waits for everything already in flight.

    pending_cluster_operation_dependencies() replaced
    runs_after=[obj.last_cluster_operation] at the instance snapshot, the
    interface hot plug and the artifact fetch route. The pointer names
    only the newest mutating operation, so an older pending operation
    enqueued without runs_after -- a disk fetch, a redirected start,
    instance_create -- was skipped and ran alongside the new work
    (issue 4673).
    """

    def _enqueue_inst(self, tasks, instance_uuid=INSTANCE_UUID):
        _, op_uuid = node_inst_op.create_and_enqueue(
            NODE_UUID, instance_uuid, tasks, PRIORITY.background)
        return op_uuid

    def test_success_criterion(self):
        # Two queued mutating ops, neither chained behind the other, which
        # is how the disk fetches and the redirected start arrive. The
        # pointer names only the newer one; the dependency list names both.
        older = self._enqueue_inst(MUTATING_TASKS)
        newer = self._enqueue_inst(MUTATING_TASKS)

        instance = _Instance(INSTANCE_UUID)
        self.assertEqual(newer, instance.last_cluster_operation['op_uuid'])
        self.assertEqual(
            {older, newer},
            {d['op_uuid']
             for d in instance.pending_cluster_operation_dependencies()})

    def test_op_type_is_carried_so_the_dispatcher_can_load_it(self):
        op_uuid = self._enqueue_inst(MUTATING_TASKS)
        self.assertEqual(
            [{'op_type': ObjectType.NODE_INST_OP, 'op_uuid': op_uuid}],
            _Instance(INSTANCE_UUID).pending_cluster_operation_dependencies())

    def test_what_is_and_is_not_waited_for(self):
        # Terminal ops are not waited for, observational sweeps are not
        # waited for, and an op targeting another object is not either.
        queued = self._enqueue_inst(MUTATING_TASKS)
        self._set_op_state(self._enqueue_inst(MUTATING_TASKS), 'complete')
        self._set_op_state(self._enqueue_inst(MUTATING_TASKS), 'error')
        self._enqueue_inst(SWEEP_TASKS)
        self._enqueue_inst(MUTATING_TASKS, instance_uuid=str(uuid4()))

        instance = _Instance(INSTANCE_UUID)
        self.assertEqual(
            [queued],
            [d['op_uuid']
             for d in instance.pending_cluster_operation_dependencies()])

        # The sweep really is pending and really is in the listing, so the
        # assertion above is about the observational filter and not about
        # an empty listing.
        views = instance.cluster_operation_views()
        self.assertEqual(
            [True], [v['observational'] for v in views
                     if v['uuid'] != queued])

    def test_nothing_in_flight_is_an_empty_list(self):
        # An empty list is falsy, and _convert_deps() turns it into None,
        # so an object with nothing outstanding enqueues unchained.
        self._set_op_state(self._enqueue_inst(MUTATING_TASKS), 'complete')
        self.assertEqual(
            [], _Instance(INSTANCE_UUID).pending_cluster_operation_dependencies())

    def test_a_failed_read_raises_rather_than_answering_empty(self):
        # A failed read of the target rows answers with no rows, which
        # cannot be told apart from "nothing is in flight" and would
        # enqueue the work with no ordering at all. raise_on_error is
        # passed so that the read raises instead, and the exception is
        # not swallowed on the way back out.
        self._enqueue_inst(MUTATING_TASKS)
        instance = _Instance(INSTANCE_UUID)

        with mock.patch(
                'shakenfist.mariadb.'
                'get_cluster_operation_targets_for_object',
                side_effect=exceptions.DatabaseUnavailable('tier down')) as r:
            self.assertRaises(
                exceptions.DatabaseUnavailable,
                instance.pending_cluster_operation_dependencies)
        self.assertTrue(r.call_args.kwargs['raise_on_error'])

    def test_in_memory_object_waits_for_nothing(self):
        self._enqueue_inst(MUTATING_TASKS)
        with mock.patch(
                'shakenfist.mariadb.get_cluster_operation_targets_for_object'
        ) as targets:
            self.assertEqual(
                [],
                _Instance(INSTANCE_UUID, in_memory_only=True)
                .pending_cluster_operation_dependencies())
        targets.assert_not_called()
