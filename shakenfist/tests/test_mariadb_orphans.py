# Copyright 2026 Michael Still and contributors
#
# Direct SQL tests for the orphan reconciliation queries (issue 3534).
#
# Format note baked into these tests: object_states.object_uuid stores the
# dashed 36 character uuid form, while the static tables use sa.Uuid
# columns which store undashed CHAR(32) on non-native backends (sqlite in
# these tests, MariaDB in production). The queries must transform between
# the two forms or they silently never match.

import itertools
import time
import uuid
from unittest import mock

import grpc
import sqlalchemy as sa

from shakenfist import constants
from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObject
from shakenfist.daemons.cluster import scheduled_tasks
from shakenfist.daemons.database import main as daemons_database_main
from shakenfist.protos import database_pb2
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests import dbfixture


class OrphanQueryTestCase(
        dbfixture.MariaDBTableFixture, base.ShakenFistTestCase):
    def _build_engine(self, tables):
        # The fixture also restores mariadb's table caches and MetaData
        # afterwards, which this module used to leave cleared -- a later
        # test in the same worker which walked the full schema then saw
        # only the handful of tables built here.
        return self.build_engine(tables)

    def _insert_state(self, conn, states, object_uuid, object_type,
                      state_value='created', update_time=100.0):
        conn.execute(sa.insert(states).values(
            object_uuid=object_uuid,
            object_type=object_type,
            state_value=state_value,
            update_time=update_time,
            message=None))

    def _insert_ipam(self, conn, ipams, ipam_uuid):
        conn.execute(sa.insert(ipams).values(
            uuid=ipam_uuid,
            namespace='system',
            network_uuid=ipam_uuid,
            ipblock='10.0.0.0/24',
            version=1))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_phantom_states_deleted_real_states_kept(self, mock_get_engine):
        engine = self._build_engine(
            [mariadb._get_object_states_table, mariadb._get_ipams_table])
        mock_get_engine.return_value = engine
        states = mariadb._get_object_states_table()
        ipams = mariadb._get_ipams_table()

        real = uuid.uuid4()
        phantom = uuid.uuid4()
        with engine.connect() as conn:
            self._insert_ipam(conn, ipams, real)
            self._insert_state(conn, states, str(real), 'ipam')
            self._insert_state(conn, states, str(phantom), 'ipam')
            conn.commit()

        deleted = mariadb._direct_delete_orphaned_object_states(
            ObjectType.IPAM, updated_before=200.0)
        self.assertEqual(1, deleted)

        with engine.connect() as conn:
            remaining = conn.execute(sa.select(states.c.object_uuid)).fetchall()
        self.assertEqual([(str(real),)], remaining)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_phantom_age_guard(self, mock_get_engine):
        # A young phantom (an object mid-creation whose static row has
        # not landed yet, or landed and been read before the state row)
        # must not be removed.
        engine = self._build_engine(
            [mariadb._get_object_states_table, mariadb._get_ipams_table])
        mock_get_engine.return_value = engine
        states = mariadb._get_object_states_table()

        phantom = uuid.uuid4()
        with engine.connect() as conn:
            self._insert_state(
                conn, states, str(phantom), 'ipam', update_time=500.0)
            conn.commit()

        deleted = mariadb._direct_delete_orphaned_object_states(
            ObjectType.IPAM, updated_before=200.0)
        self.assertEqual(0, deleted)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_phantom_delete_only_touches_requested_type(
            self, mock_get_engine):
        engine = self._build_engine(
            [mariadb._get_object_states_table, mariadb._get_ipams_table])
        mock_get_engine.return_value = engine
        states = mariadb._get_object_states_table()

        phantom = uuid.uuid4()
        with engine.connect() as conn:
            self._insert_state(conn, states, str(phantom), 'ipam')
            self._insert_state(conn, states, str(phantom), 'network')
            conn.commit()

        deleted = mariadb._direct_delete_orphaned_object_states(
            ObjectType.IPAM, updated_before=200.0)
        self.assertEqual(1, deleted)

        with engine.connect() as conn:
            remaining = conn.execute(
                sa.select(states.c.object_type)).fetchall()
        self.assertEqual([('network',)], remaining)

    @mock.patch('shakenfist.mariadb._PRUNE_BATCH_SIZE', 3)
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_phantom_delete_windows_cover_whole_key_range(
            self, mock_get_engine):
        # The windowed walk (issue 4659) must reach phantoms in every
        # window, including the short tail window past the last full one.
        engine = self._build_engine(
            [mariadb._get_object_states_table, mariadb._get_ipams_table])
        mock_get_engine.return_value = engine
        states = mariadb._get_object_states_table()
        ipams = mariadb._get_ipams_table()

        # uuid.UUID(int=n) sorts as a string in the same order as n, so
        # phantom positions within the windows are deterministic: 1 is in
        # the first window, 5 in the second, 10 in the tail.
        uuids = [uuid.UUID(int=n) for n in range(1, 11)]
        phantoms = {uuids[0], uuids[4], uuids[9]}
        with engine.connect() as conn:
            for object_uuid in uuids:
                if object_uuid not in phantoms:
                    self._insert_ipam(conn, ipams, object_uuid)
                self._insert_state(conn, states, str(object_uuid), 'ipam')
            conn.commit()

        deleted = mariadb._direct_delete_orphaned_object_states(
            ObjectType.IPAM, updated_before=200.0)
        self.assertEqual(3, deleted)

        with engine.connect() as conn:
            remaining = conn.execute(
                sa.select(states.c.object_uuid)).fetchall()
        self.assertEqual(
            sorted(str(u) for u in uuids if u not in phantoms),
            sorted(row.object_uuid for row in remaining))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_phantom_delete_expired_deadline_does_nothing(
            self, mock_get_engine):
        engine = self._build_engine(
            [mariadb._get_object_states_table, mariadb._get_ipams_table])
        mock_get_engine.return_value = engine
        states = mariadb._get_object_states_table()

        phantom = uuid.uuid4()
        with engine.connect() as conn:
            self._insert_state(conn, states, str(phantom), 'ipam')
            conn.commit()

        deleted = mariadb._direct_delete_orphaned_object_states(
            ObjectType.IPAM, updated_before=200.0,
            deadline=time.time() - 1.0)
        self.assertEqual(0, deleted)

        with engine.connect() as conn:
            remaining = conn.execute(
                sa.select(states.c.object_uuid)).fetchall()
        self.assertEqual([(str(phantom),)], remaining)

    @mock.patch('shakenfist.mariadb._PRUNE_BATCH_SIZE', 3)
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_phantom_delete_stops_between_windows_keeping_progress(
            self, mock_get_engine):
        # A deadline expiring mid-walk stops the sweep between windows:
        # phantoms in the windows already walked stay deleted, phantoms
        # past the stopping point are left for the next pass.
        engine = self._build_engine(
            [mariadb._get_object_states_table, mariadb._get_ipams_table])
        mock_get_engine.return_value = engine
        states = mariadb._get_object_states_table()
        ipams = mariadb._get_ipams_table()

        uuids = [uuid.UUID(int=n) for n in range(1, 11)]
        phantoms = {uuids[0], uuids[4], uuids[9]}
        with engine.connect() as conn:
            for object_uuid in uuids:
                if object_uuid not in phantoms:
                    self._insert_ipam(conn, ipams, object_uuid)
                self._insert_state(conn, states, str(object_uuid), 'ipam')
            conn.commit()

        # The walk checks the clock once per window; let the first two
        # windows (rows 1-3 and 4-6) run, then expire the deadline. Only
        # mariadb's reference to the time module is patched, so
        # sqlalchemy's own clock reads are unaffected.
        fake_time = mock.MagicMock()
        fake_time.time.side_effect = itertools.chain(
            [0.0, 0.0], itertools.repeat(100.0))
        with mock.patch('shakenfist.mariadb.time', fake_time):
            deleted = mariadb._direct_delete_orphaned_object_states(
                ObjectType.IPAM, updated_before=200.0, deadline=50.0)
        self.assertEqual(2, deleted)

        with engine.connect() as conn:
            remaining = {row.object_uuid for row in conn.execute(
                sa.select(states.c.object_uuid)).fetchall()}
        self.assertNotIn(str(uuids[0]), remaining)
        self.assertNotIn(str(uuids[4]), remaining)
        self.assertIn(str(uuids[9]), remaining)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_zombie_static_rows_listed_dashed(self, mock_get_engine):
        engine = self._build_engine(
            [mariadb._get_object_states_table, mariadb._get_ipams_table])
        mock_get_engine.return_value = engine
        states = mariadb._get_object_states_table()
        ipams = mariadb._get_ipams_table()

        with_state = uuid.uuid4()
        zombie = uuid.uuid4()
        with engine.connect() as conn:
            self._insert_ipam(conn, ipams, with_state)
            self._insert_ipam(conn, ipams, zombie)
            self._insert_state(conn, states, str(with_state), 'ipam')
            # A state row for the same uuid but a different object type
            # must not mask the zombie.
            self._insert_state(conn, states, str(zombie), 'network')
            conn.commit()

        result = mariadb._direct_get_stateless_object_uuids(ObjectType.IPAM)
        self.assertEqual([str(zombie)], result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_op_type_zombies_scoped_by_operation_type(self, mock_get_engine):
        # All cluster operation types share one table; the query must
        # only return rows for the requested operation type.
        engine = self._build_engine(
            [mariadb._get_object_states_table,
             mariadb._get_cluster_operations_table])
        mock_get_engine.return_value = engine
        ops = mariadb._get_cluster_operations_table()

        net_op = uuid.uuid4()
        node_op = uuid.uuid4()
        with engine.connect() as conn:
            for op_uuid, op_type in ((net_op, 'net_op'),
                                     (node_op, 'node_inst_op')):
                conn.execute(sa.insert(ops).values(
                    uuid=op_uuid,
                    operation_type=op_type,
                    created_at=100.0,
                    metadata_json={}))
            conn.commit()

        result = mariadb._direct_get_stateless_object_uuids(ObjectType.NET_OP)
        self.assertEqual([str(net_op)], result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_orphaned_artifact_attributes_deleted(self, mock_get_engine):
        engine = self._build_engine(
            [mariadb._get_object_states_table,
             mariadb._get_artifacts_table,
             mariadb._get_artifact_attributes_table])
        mock_get_engine.return_value = engine
        artifacts = mariadb._get_artifacts_table()
        attrs = mariadb._get_artifact_attributes_table()

        real = uuid.uuid4()
        orphan = uuid.uuid4()
        with engine.connect() as conn:
            conn.execute(sa.insert(artifacts).values(
                uuid=real,
                artifact_type='image',
                source_url='http://example.com/img',
                name='img',
                namespace='system',
                version=1))
            for att_uuid in (real, orphan):
                conn.execute(sa.insert(attrs).values(
                    uuid=att_uuid,
                    max_versions=3,
                    shared=False,
                    highest_index=0))
            conn.commit()

        deleted = mariadb._direct_delete_orphaned_artifact_attributes()
        self.assertEqual(1, deleted)

        with engine.connect() as conn:
            remaining = conn.execute(sa.select(attrs.c.uuid)).fetchall()
        self.assertEqual([(real,)], remaining)

    def test_unmapped_type_is_noop(self):
        self.assertEqual(0, mariadb._direct_delete_orphaned_object_states(
            ObjectType.UNKNOWN, updated_before=200.0))
        self.assertEqual([], mariadb._direct_get_stateless_object_uuids(
            ObjectType.UNKNOWN))

    def test_namespace_keys_are_reconcilable(self):
        # namespace_key was missing from _STATIC_TABLE_GETTERS from the
        # day the object landed, so zombie keys (static row, no state
        # row) were invisible to the reconciler while the expiry sweep
        # re-evented every one of them every pass (issue 3588). Pin the
        # membership so the reconciler keeps covering keys.
        self.assertIn(ObjectType.NAMESPACE_KEY.value,
                      mariadb.ORPHAN_RECONCILABLE_OBJECT_TYPES)

        entry = mariadb._static_table_for_object_type(
            ObjectType.NAMESPACE_KEY)
        self.assertIsNotNone(entry)
        table, pk = entry
        self.assertEqual('namespace_keys', table.name)
        self.assertEqual('uuid', pk)

    def test_federated_identity_objects_are_reconcilable(self):
        # trusted_issuer and mapping_rule were missing from
        # _STATIC_TABLE_GETTERS from the day federated identity landed,
        # the same omission as namespace_key (issue 3588) in two more
        # object types, so their orphans were unreapable in both
        # directions (issue 3788).
        for objtype, table_name in (
                (ObjectType.TRUSTED_ISSUER, 'trusted_issuers'),
                (ObjectType.MAPPING_RULE, 'mapping_rules')):
            self.assertIn(objtype.value,
                          mariadb.ORPHAN_RECONCILABLE_OBJECT_TYPES)

            entry = mariadb._static_table_for_object_type(objtype)
            self.assertIsNotNone(entry)
            table, pk = entry
            self.assertEqual(table_name, table.name)
            self.assertEqual('uuid', pk)

    def test_every_object_class_has_a_static_table_getter(self):
        # Three object types have now each been registered in
        # constants.OBJECT_NAMES_TO_CLASSES but not in
        # _STATIC_TABLE_GETTERS (namespace_key in issue 3588,
        # trusted_issuer and mapping_rule in issue 3788), and each time a
        # human happened to notice. The two registries now agree exactly,
        # so assert the invariant directly. If a future object type
        # legitimately has a class but no static-values table, this
        # assertion is the place to argue that case, with a comment
        # saying why.
        self.assertEqual(
            set(constants.OBJECT_NAMES_TO_CLASSES),
            set(mariadb._STATIC_TABLE_GETTERS),
            'every object type with a class needs a static table getter, '
            'or the orphan reconciler silently skips it in both directions '
            '(issues 3588, 3788)')

    def test_zombie_repair_state_overrides_are_pinned(self):
        # Registering a type above makes the reconciler repair its
        # zombies, which by default means marking them deleted. That is
        # only safe if nothing can observe a stateless object of that
        # type. namespace_key, trusted_issuer and mapping_rule each have
        # a read path which honours one, and repairing them to deleted
        # destroyed live credentials (issue 3836). Pin the set, so that
        # adding a type -- or an override -- is a decision someone made.
        default = DatabaseBackedObject.zombie_repair_state.__func__
        overriding = set()
        for objtype in mariadb.ORPHAN_RECONCILABLE_OBJECT_TYPES:
            if objtype in scheduled_tasks.ZOMBIE_REPAIR_EXCLUDED_TYPES:
                continue
            cls = constants.get_object_class(objtype)
            if cls.zombie_repair_state.__func__ is not default:
                overriding.add(objtype)

        self.assertEqual(
            {'namespace_key', 'trusted_issuer', 'mapping_rule'},
            overriding,
            'the set of object types which override zombie_repair_state() '
            'changed. If you added an object type, decide whether any of '
            'its read paths honour an object with no state row (a by-name '
            'lookup or listing which does not check state, or which rejects '
            'only deleted). If one does, the orphan reconciler would '
            'destroy live objects of that type: override '
            'zombie_repair_state() to return a live state when the object '
            'would be honoured, and add the type here (issue 3836)')


class DeleteOrphanedObjectStatesServicerTestCase(base.ShakenFistTestCase):
    """The servicer must bound the sweep inside the caller's deadline.

    The sweep used to run as one unbounded DELETE the server kept
    running after the client's deadline expired, and each retry queued
    another copy over the same rows (issue 4659, the issue 3607 shape).
    """

    def setUp(self):
        super().setUp()
        self.servicer = daemons_database_main.DatabaseService(
            mock.MagicMock())
        self.request = database_pb2.DeleteOrphanedObjectStatesRequest(
            object_type=ObjectType.IPAM.proto_id, updated_before=200.0)

    def _context(self, time_remaining):
        context = mock.MagicMock()
        context.time_remaining.return_value = time_remaining
        return context

    @mock.patch('shakenfist.mariadb._direct_delete_orphaned_object_states')
    def test_expired_call_is_dropped_without_a_sweep(self, mock_direct):
        context = self._context(0.0)

        reply = self.servicer.DeleteOrphanedObjectStates(
            self.request, context)

        mock_direct.assert_not_called()
        self.assertFalse(reply.success)
        context.set_code.assert_called_once_with(
            grpc.StatusCode.DEADLINE_EXCEEDED)

    @mock.patch('shakenfist.mariadb._direct_delete_orphaned_object_states',
                return_value=3)
    def test_live_call_is_bounded_inside_the_deadline(self, mock_direct):
        context = self._context(30.0)
        before = time.time()

        reply = self.servicer.DeleteOrphanedObjectStates(
            self.request, context)

        self.assertTrue(reply.success)
        self.assertEqual(3, reply.deleted)
        context.set_code.assert_not_called()
        _, kwargs = mock_direct.call_args
        # The sweep must stop early enough that the final in-flight
        # window and the reply itself still beat the caller's deadline.
        self.assertGreater(kwargs['deadline'], before)
        self.assertLessEqual(
            kwargs['deadline'],
            before + mariadb.DELETE_ORPHANS_TIME_BUDGET + 1.0)

    @mock.patch('shakenfist.mariadb._direct_delete_orphaned_object_states',
                return_value=0)
    def test_tight_caller_deadline_shrinks_the_budget(self, mock_direct):
        context = self._context(12.0)
        before = time.time()

        reply = self.servicer.DeleteOrphanedObjectStates(
            self.request, context)

        self.assertTrue(reply.success)
        _, kwargs = mock_direct.call_args
        self.assertLessEqual(kwargs['deadline'], before + 3.0)

    @mock.patch('shakenfist.mariadb._direct_delete_orphaned_object_states',
                return_value=0)
    def test_call_without_a_deadline_uses_the_fixed_budget(
            self, mock_direct):
        # time_remaining() is None when the caller set no deadline at all.
        context = self._context(None)
        before = time.time()

        reply = self.servicer.DeleteOrphanedObjectStates(
            self.request, context)

        self.assertTrue(reply.success)
        context.set_code.assert_not_called()
        _, kwargs = mock_direct.call_args
        self.assertGreater(kwargs['deadline'], before)
        self.assertLessEqual(
            kwargs['deadline'],
            before + mariadb.DELETE_ORPHANS_TIME_BUDGET + 1.0)
