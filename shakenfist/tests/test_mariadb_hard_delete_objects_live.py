# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB tests for the batch hard delete.

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh,
which runs every test_mariadb_*_live module behind one MariaDB install;
developers can point at a local instance). The unit tests mock the
connection, so they cannot show that each statement deletes the rows it
is meant to and no others, that the guard and the deletes it gates are
one transaction, or that a failure part way leaves every table as it
was. That is what these tests check, for every batchable type, by
putting a row in every table the type's hard delete removes from and
counting what is left.

The rows are described below independently of the statements under
test: a statement keyed on the wrong column leaves its row behind, or
takes another object's.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment. The
test_mariadb_*_live modules share one database, so they must be run
serially -- ``stestr run --serial``, as CI does.
"""

import os
import random
import time
import unittest
from unittest import mock
from uuid import UUID
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import INET4

from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'

# The tables the batch deletes from, created in the order ensure_schema()
# creates them.
ENSURES = [
    mariadb._ensure_object_states_schema,
    mariadb._ensure_ipam_reservations_schema,
    mariadb._ensure_uploads_schema,
    mariadb._ensure_dnsmasq_schema,
    mariadb._ensure_blobs_schema,
    mariadb._ensure_object_references_schema,
    mariadb._ensure_blob_hashes_schema,
    mariadb._ensure_blob_transfers_schema,
    mariadb._ensure_blob_attributes_schema,
    # The namespace key migration reads namespace attributes.
    mariadb._ensure_namespaces_schema,
    mariadb._ensure_namespace_attributes_schema,
    mariadb._ensure_namespace_key_attributes_schema,
    mariadb._ensure_namespace_keys_schema,
    mariadb._ensure_trusted_issuer_attributes_schema,
    mariadb._ensure_trusted_issuers_schema,
    mariadb._ensure_mapping_rule_attributes_schema,
    mariadb._ensure_mapping_rules_schema,
    mariadb._ensure_artifacts_schema,
    mariadb._ensure_artifact_attributes_schema,
    mariadb._ensure_artifact_indexes_schema,
    mariadb._ensure_network_interfaces_schema,
    mariadb._ensure_network_interface_attributes_schema,
    mariadb._ensure_networks_schema,
    mariadb._ensure_network_attributes_schema,
    mariadb._ensure_ipams_schema,
    mariadb._ensure_agent_operations_schema,
    mariadb._ensure_agent_operation_attributes_schema,
    mariadb._ensure_object_metadata_schema,
    mariadb._ensure_cluster_operation_targets_schema,
    mariadb._ensure_cluster_operations_schema,
    mariadb._ensure_cluster_operation_errors_schema,
    mariadb._ensure_event_objects_schema,
]

TEST_TABLES = [
    'object_states', 'ipam_reservations', 'uploads', 'dnsmasq', 'blobs',
    'object_references', 'blob_hashes', 'blob_transfers', 'blob_attributes',
    'namespaces', 'namespace_attributes', 'namespace_key_attributes',
    'namespace_keys', 'trusted_issuer_attributes',
    'trusted_issuers', 'mapping_rule_attributes', 'mapping_rules', 'artifacts',
    'artifact_attributes', 'artifact_indexes', 'network_interfaces',
    'network_interface_attributes', 'networks', 'network_attributes', 'ipams',
    'agent_operations', 'agent_operation_attributes', 'object_metadata',
    'cluster_operation_targets', 'cluster_operations',
    'cluster_operation_errors', 'event_objects', 'schema_versions',
]

# For each public delete function the batch mirrors: the table it
# deletes from, and the column values which make a row in it one
# object's. Written from the _direct_ functions, not from the batch's
# builders, so that the two can disagree.
ROWS = {
    'delete_cluster_operation_target': (
        mariadb._get_cluster_operation_targets_table,
        lambda t, u: {'operation_uuid': u}),
    'delete_cluster_operation_error': (
        mariadb._get_cluster_operation_errors_table,
        lambda t, u: {'op_uuid': UUID(u)}),
    'delete_cluster_operation': (
        mariadb._get_cluster_operations_table,
        lambda t, u: {'uuid': UUID(u)}),
    'delete_cluster_operation_targets_for_object': (
        mariadb._get_cluster_operation_targets_table,
        lambda t, u: {'target_object_type': t, 'target_uuid': u}),
    'delete_agent_operation_attributes': (
        mariadb._get_agent_operation_attributes_table,
        lambda t, u: {'uuid': UUID(u)}),
    'delete_agent_operation': (
        mariadb._get_agent_operations_table,
        lambda t, u: {'uuid': UUID(u)}),
    'remove_all_references_from': (
        mariadb._get_object_references_table,
        lambda t, u: {'source_object_type': t.value, 'source_uuid': u}),
    'delete_all_artifact_indexes': (
        mariadb._get_artifact_indexes_table,
        lambda t, u: {'artifact_uuid': UUID(u)}),
    'delete_artifact_attributes': (
        mariadb._get_artifact_attributes_table,
        lambda t, u: {'uuid': UUID(u)}),
    'delete_artifact': (
        mariadb._get_artifacts_table, lambda t, u: {'uuid': UUID(u)}),
    'delete_network_attributes': (
        mariadb._get_network_attributes_table,
        lambda t, u: {'uuid': UUID(u)}),
    'delete_network': (
        mariadb._get_networks_table, lambda t, u: {'uuid': UUID(u)}),
    'delete_blob_hashes': (
        mariadb._get_blob_hashes_table, lambda t, u: {'blob_uuid': u}),
    'delete_blob_transfers_for_blob': (
        mariadb._get_blob_transfers_table, lambda t, u: {'blob_uuid': u}),
    'delete_blob_attributes': (
        mariadb._get_blob_attributes_table, lambda t, u: {'uuid': UUID(u)}),
    'delete_blob': (
        mariadb._get_blobs_table, lambda t, u: {'uuid': UUID(u)}),
    'delete_dnsmasq': (
        mariadb._get_dnsmasq_table, lambda t, u: {'uuid': UUID(u)}),
    'delete_network_interface_attributes': (
        mariadb._get_network_interface_attributes_table,
        lambda t, u: {'uuid': UUID(u)}),
    'delete_network_interface': (
        mariadb._get_network_interfaces_table,
        lambda t, u: {'uuid': UUID(u)}),
    'delete_reservations_for_ipam': (
        mariadb._get_ipam_reservations_table,
        lambda t, u: {'ipam_uuid': UUID(u)}),
    'delete_ipam': (
        mariadb._get_ipams_table, lambda t, u: {'uuid': UUID(u)}),
    'delete_namespace_key_attributes': (
        mariadb._get_namespace_key_attributes_table,
        lambda t, u: {'uuid': UUID(u)}),
    'delete_namespace_key': (
        mariadb._get_namespace_keys_table, lambda t, u: {'uuid': UUID(u)}),
    'delete_trusted_issuer_attributes': (
        mariadb._get_trusted_issuer_attributes_table,
        lambda t, u: {'uuid': UUID(u)}),
    'delete_trusted_issuer': (
        mariadb._get_trusted_issuers_table, lambda t, u: {'uuid': UUID(u)}),
    'delete_mapping_rule_attributes': (
        mariadb._get_mapping_rule_attributes_table,
        lambda t, u: {'uuid': UUID(u)}),
    'delete_mapping_rule': (
        mariadb._get_mapping_rules_table, lambda t, u: {'uuid': UUID(u)}),
    'delete_upload': (
        mariadb._get_uploads_table, lambda t, u: {'uuid': UUID(u)}),
}

# The rows every type has, from delete_state(), delete_object_metadata()
# and delete_object_events().
BASE_ROWS = {
    'delete_state': (
        mariadb._get_object_states_table,
        lambda t, u: {'object_type': t, 'object_uuid': u}),
    'delete_object_metadata': (
        mariadb._get_object_metadata_table,
        lambda t, u: {'object_type': t, 'object_uuid': u}),
    'delete_object_events': (
        mariadb._get_event_objects_table,
        lambda t, u: {'object_type': t.value, 'object_uuid': u}),
}

# The tables keyed on object type as well as uuid, which another type's
# object with the same uuid can also have rows in.
TYPE_KEYED_ROWS = ['delete_state', 'delete_object_metadata',
                   'delete_object_events',
                   'delete_cluster_operation_targets_for_object',
                   'remove_all_references_from']

FINAL_STATES = ['deleted', 'error']
BATCHABLE = list(mariadb.HARD_DELETE_BATCH_FUNCTIONS)


def _filler(column):
    """Some value for a required column the test does not care about.

    Random, so that rows for different objects never collide on a
    primary key or unique index made of columns the test does not set.
    """
    column_type = column.type
    if isinstance(column_type, sa.Uuid):
        return uuid4()
    if isinstance(column_type, sa.Enum):
        if column_type.enum_class is not None:
            return list(column_type.enum_class)[0]
        return column_type.enums[0]
    if isinstance(column_type, INET4):
        return '10.%d.%d.%d' % tuple(random.randint(0, 255) for _ in range(3))
    if isinstance(column_type, sa.Boolean):
        return False
    if isinstance(column_type, sa.JSON):
        return {}
    if isinstance(column_type, sa.Integer):
        return random.randint(1, 2 ** 31)
    if isinstance(column_type, (sa.Float, sa.Numeric)):
        return 0.0
    if isinstance(column_type, sa.String):
        return uuid4().hex[:column_type.length or 32]
    raise AssertionError(f'no filler for {column.table.name}.{column.name} '
                         f'of type {column_type!r}')


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class HardDeleteObjectsLiveTestCase(base.ShakenFistTestCase):
    """Hard delete batches of every batchable type against a real MariaDB."""

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)
        self._drop_tables()

        mariadb._ensure_schema_versions_table(self.engine)
        for ensure in ENSURES:
            ensure(self.engine)

        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.cutoff = time.time() - 3600
        self.old = self.cutoff - 60
        self.young = self.cutoff + 60

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _row_specs(self, object_type):
        names = list(mariadb.HARD_DELETE_BATCH_FUNCTIONS[object_type])
        return [(name, ROWS[name]) for name in names] + list(BASE_ROWS.items())

    def _insert(self, conn, get_table, keys):
        table = get_table()
        values = dict(keys)
        for column in table.c:
            if (column.name in values or column.nullable
                    or column.default is not None
                    or column.server_default is not None
                    or column is table.autoincrement_column):
                continue
            values[column.name] = _filler(column)
        conn.execute(sa.insert(table).values(**values))

    def _seed(self, object_type, state=None, update_time=None,
              with_state=True):
        """Put a row for a new object in every table its delete covers."""
        uuid = str(uuid4())
        with self.engine.connect() as conn:
            for name, (get_table, keys) in self._row_specs(object_type):
                if name == 'delete_state':
                    if not with_state:
                        continue
                    row = dict(keys(object_type, uuid))
                    row['state_value'] = state
                    row['update_time'] = update_time
                    self._insert(conn, get_table, row)
                elif name == 'delete_object_events':
                    row = dict(keys(object_type, uuid))
                    row['event_uuid'] = str(uuid4())
                    self._insert(conn, get_table, row)
                else:
                    self._insert(conn, get_table, keys(object_type, uuid))
            conn.commit()
        return uuid

    def _seed_other_type(self, other_type, uuid):
        """Rows for another type's object which shares ``uuid``."""
        with self.engine.connect() as conn:
            for name in TYPE_KEYED_ROWS:
                get_table, keys = {**ROWS, **BASE_ROWS}[name]
                row = dict(keys(other_type, uuid))
                if name == 'delete_state':
                    row['state_value'] = 'deleted'
                    row['update_time'] = self.old
                elif name == 'delete_object_events':
                    row['event_uuid'] = str(uuid4())
                self._insert(conn, get_table, row)
            conn.commit()

    def _count(self, get_table, keys):
        table = get_table()
        with self.engine.connect() as conn:
            return conn.execute(
                sa.select(sa.func.count()).select_from(table).where(
                    sa.and_(*[table.c[column] == value
                              for column, value in keys.items()]))
            ).scalar_one()

    def _remaining(self, object_type, uuid, specs=None):
        """{function name: rows left} for one object."""
        if specs is None:
            specs = self._row_specs(object_type)
        return {name: self._count(get_table, keys(object_type, uuid))
                for name, (get_table, keys) in specs}

    def _assert_all(self, object_type, uuid, expected):
        remaining = self._remaining(object_type, uuid)
        self.assertEqual({name: expected for name in remaining}, remaining,
                         f'{object_type} {uuid}')

    def test_rows_cover_every_statement(self):
        self.assertEqual(set(mariadb.HARD_DELETE_STATEMENTS), set(ROWS))

    def test_guarded_deletes_only_the_eligible_object(self):
        for object_type in BATCHABLE:
            with self.subTest(object_type=object_type):
                eligible = self._seed(object_type, 'deleted', self.old)
                live = self._seed(object_type, 'created', self.old)
                young = self._seed(object_type, 'error', self.young)
                for uuid in (eligible, live, young):
                    self._assert_all(object_type, uuid, 1)

                deleted = mariadb.hard_delete_objects(
                    object_type, [live, eligible, young],
                    state_values=FINAL_STATES, updated_before=self.cutoff)

                self.assertEqual([eligible], deleted)
                self._assert_all(object_type, eligible, 0)
                self._assert_all(object_type, live, 1)
                self._assert_all(object_type, young, 1)

    def test_guarded_with_nothing_eligible_is_empty_not_a_failure(self):
        live = self._seed(ObjectType.NODE_INST_OP, 'created', self.old)
        deleted = mariadb.hard_delete_objects(
            ObjectType.NODE_INST_OP, [live, str(uuid4())],
            state_values=FINAL_STATES, updated_before=self.cutoff)
        self.assertEqual([], deleted)
        self._assert_all(ObjectType.NODE_INST_OP, live, 1)

    def test_shared_uuid_of_another_type_is_untouched(self):
        other_specs = [(name, {**ROWS, **BASE_ROWS}[name])
                       for name in TYPE_KEYED_ROWS]
        for object_type in (ObjectType.ARTIFACT, ObjectType.AGENTOPERATION,
                            ObjectType.NETWORK, ObjectType.NODE_INST_OP):
            for unconditional in (False, True):
                with self.subTest(object_type=object_type,
                                  unconditional=unconditional):
                    uuid = self._seed(object_type, 'deleted', self.old)
                    self._seed_other_type(ObjectType.INSTANCE, uuid)
                    self.assertEqual(
                        {name: 1 for name, _ in other_specs},
                        self._remaining(ObjectType.INSTANCE, uuid,
                                        other_specs))

                    if unconditional:
                        deleted = mariadb.hard_delete_objects(
                            object_type, [uuid], unconditional=True)
                    else:
                        deleted = mariadb.hard_delete_objects(
                            object_type, [uuid], state_values=FINAL_STATES,
                            updated_before=self.cutoff)

                    self.assertEqual([uuid], deleted)
                    self._assert_all(object_type, uuid, 0)
                    self.assertEqual(
                        {name: 1 for name, _ in other_specs},
                        self._remaining(ObjectType.INSTANCE, uuid,
                                        other_specs))

    def test_unconditional_deletes_whatever_the_state(self):
        for object_type in BATCHABLE:
            with self.subTest(object_type=object_type):
                live = self._seed(object_type, 'created', self.old)
                young = self._seed(object_type, 'deleted', self.young)
                stateless = self._seed(object_type, with_state=False)
                untouched = self._seed(object_type, 'deleted', self.old)

                deleted = mariadb.hard_delete_objects(
                    object_type, [live, young, stateless],
                    unconditional=True)

                self.assertEqual([live, young, stateless], deleted)
                for uuid in (live, young, stateless):
                    self._assert_all(object_type, uuid, 0)
                self._assert_all(object_type, untouched, 1)

    def test_failure_part_way_rolls_everything_back(self):
        # The blob's attributes statement fails, after the state delete
        # and the statements ahead of it have run in the same
        # transaction. It is found by name, not position, so adding a
        # statement to the blob's batch does not break the test.
        names = mariadb.HARD_DELETE_BATCH_FUNCTIONS[ObjectType.BLOB]
        failing = 'delete_blob_attributes'
        self.assertGreaterEqual(names.index(failing), 2)
        self.assertLess(names.index(failing), len(names) - 1)
        broken = mariadb.HARD_DELETE_STATEMENTS[failing]._replace(
            build=lambda t, u: sa.text(
                'DELETE FROM blob_attributes WHERE no_such_column = 1'))

        for unconditional in (False, True):
            with self.subTest(unconditional=unconditional):
                uuid = self._seed(ObjectType.BLOB, 'deleted', self.old)
                with mock.patch.dict(mariadb.HARD_DELETE_STATEMENTS,
                                     {failing: broken}), \
                        mock.patch('shakenfist.mariadb.LOG') as mock_log:
                    if unconditional:
                        deleted = mariadb.hard_delete_objects(
                            ObjectType.BLOB, [uuid], unconditional=True)
                    else:
                        deleted = mariadb.hard_delete_objects(
                            ObjectType.BLOB, [uuid],
                            state_values=FINAL_STATES,
                            updated_before=self.cutoff)

                self.assertIsNone(deleted)
                mock_log.warning.assert_called_once()
                self._assert_all(ObjectType.BLOB, uuid, 1)

                # And with the statement restored, the same call works.
                deleted = mariadb.hard_delete_objects(
                    ObjectType.BLOB, [uuid], state_values=FINAL_STATES,
                    updated_before=self.cutoff)
                self.assertEqual([uuid], deleted)
                self._assert_all(ObjectType.BLOB, uuid, 0)

    def test_too_many_uuids_is_refused(self):
        eligible = self._seed(ObjectType.NET_OP, 'deleted', self.old)
        uuids = [eligible] + [str(uuid4()) for _ in range(
            mariadb.HARD_DELETE_OBJECTS_MAX)]
        self.assertRaises(
            ValueError, mariadb.hard_delete_objects, ObjectType.NET_OP,
            uuids, state_values=FINAL_STATES, updated_before=self.cutoff)
        self._assert_all(ObjectType.NET_OP, eligible, 1)

        # The most one call may name is accepted.
        deleted = mariadb.hard_delete_objects(
            ObjectType.NET_OP, uuids[:mariadb.HARD_DELETE_OBJECTS_MAX],
            state_values=FINAL_STATES, updated_before=self.cutoff)
        self.assertEqual([eligible], deleted)
        self._assert_all(ObjectType.NET_OP, eligible, 0)

    def test_per_call_deletes_what_the_batch_deletes(self):
        # The fallback for an sf-database without the batch removes the
        # same rows, one public call at a time.
        for object_type in BATCHABLE:
            with self.subTest(object_type=object_type):
                uuid = self._seed(object_type, 'deleted', self.old)
                with mock.patch('shakenfist.mariadb._direct_hard_delete_objects'
                                ) as batch:
                    mariadb.hard_delete_objects_per_call(object_type, uuid)
                batch.assert_not_called()
                self._assert_all(object_type, uuid, 0)
