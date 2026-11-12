# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""The batch hard delete's cases, independent of which database runs them.

Two modules run every case in this file. ``test_mariadb_hard_delete_objects``
``_engine`` runs them on every pull request against an in-memory sqlite
engine built from mariadb.py's own table definitions, and
``test_mariadb_hard_delete_objects_live`` runs them in the merge queue
against a real MariaDB. The cases are here, and not in either of those,
because the properties they check are properties of the statements rather
than of a dialect: that each statement deletes the rows it is meant to and
no others, that the guard and the deletes it gates are one transaction,
that a failure part way leaves every table as it was, and that a batch
naming another type's object touches nothing of it.

Before #4880 these cases existed only in the live module, which runs
nowhere but the merge queue, so the batch's real SQL was first executed
after each change to it had been reviewed and approved. The mock-backed
tests cannot stand in for them: MockMariaDB's double for
hard_delete_objects() calls hard_delete_objects_per_call() instead, so a
batch builder which omitted a table would pass them.

The rows are described below independently of the statements under test: a
statement keyed on the wrong column leaves its row behind, or takes another
object's.
"""

import random
import time
from unittest import mock
from uuid import UUID
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import INET4

from shakenfist import mariadb
from shakenfist import mariadb_hard_delete
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import hard_delete_expected


ROWS = {
    # The operation type is part of what makes these rows one
    # operation's, because every cluster operation type shares these
    # tables and a batch may only reach rows of the type it declares
    # (#4877). cluster_operation_errors is the exception: it carries no
    # type of its own, and is scoped by the operation's own row.
    'delete_cluster_operation_target': (
        mariadb._get_cluster_operation_targets_table,
        lambda t, u: {'operation_uuid': u, 'operation_type': str(t)}),
    'delete_cluster_operation_error': (
        mariadb._get_cluster_operation_errors_table,
        lambda t, u: {'op_uuid': UUID(u)}),
    'delete_cluster_operation': (
        mariadb._get_cluster_operations_table,
        lambda t, u: {'uuid': UUID(u), 'operation_type': str(t)}),
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

# From the independent copy of each type's delete set, not from the
# declaration these cases check. test_hard_delete_batch_parity holds the
# two to the same set of types.
BATCHABLE = list(hard_delete_expected.EXPECTED_DELETES)


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


class HardDeleteObjectsCases:
    """Hard delete batches of every batchable type against a real database.

    Mixed in ahead of the base test case by each backend module, which
    builds ``self.engine`` and points ``mariadb._get_engine`` at it.
    """

    def setUp(self):
        super().setUp()
        self.cutoff = time.time() - 3600
        self.old = self.cutoff - 60
        self.young = self.cutoff + 60

    def _row_specs(self, object_type):
        """The rows one object of ``object_type`` has, and their tables.

        Read from hard_delete_expected.EXPECTED_DELETES rather than from
        mariadb_hard_delete.HARD_DELETE_BATCH_FUNCTIONS, which is the declaration
        under test. Derived from the declaration, a case would seed no
        row in a table the declaration had lost, and so could not notice
        that it had lost one -- which is the hole #4880 describes, and
        the one a mutation check found these cases had inherited.
        """
        names = list(hard_delete_expected.EXPECTED_DELETES[object_type])
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
        self.assertEqual(set(mariadb_hard_delete.HARD_DELETE_STATEMENTS), set(ROWS))

    def test_guarded_deletes_only_the_eligible_object(self):
        for object_type in BATCHABLE:
            with self.subTest(object_type=object_type):
                eligible = self._seed(object_type, 'deleted', self.old)
                live = self._seed(object_type, 'created', self.old)
                young = self._seed(object_type, 'error', self.young)
                for uuid in (eligible, live, young):
                    self._assert_all(object_type, uuid, 1)

                deleted = mariadb_hard_delete.hard_delete_objects(
                    object_type, [live, eligible, young],
                    state_values=FINAL_STATES, updated_before=self.cutoff)

                self.assertEqual([eligible], deleted)
                self._assert_all(object_type, eligible, 0)
                self._assert_all(object_type, live, 1)
                self._assert_all(object_type, young, 1)

    def test_guarded_with_nothing_eligible_is_empty_not_a_failure(self):
        live = self._seed(ObjectType.NODE_INST_OP, 'created', self.old)
        deleted = mariadb_hard_delete.hard_delete_objects(
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
                        deleted = mariadb_hard_delete.hard_delete_objects(
                            object_type, [uuid], unconditional=True)
                    else:
                        deleted = mariadb_hard_delete.hard_delete_objects(
                            object_type, [uuid], state_values=FINAL_STATES,
                            updated_before=self.cutoff)

                    self.assertEqual([uuid], deleted)
                    self._assert_all(object_type, uuid, 0)
                    self.assertEqual(
                        {name: 1 for name, _ in other_specs},
                        self._remaining(ObjectType.INSTANCE, uuid,
                                        other_specs))

    def test_a_batch_declaring_another_type_deletes_nothing(self):
        """A batch may not reach outside the type it declares (#4877).

        Every cluster operation type shares cluster_operations,
        cluster_operation_targets and cluster_operation_errors, so a
        statement keyed on the operation's uuid alone finds another
        type's rows: a uuid is globally unique. An unconditional batch
        which named one took that operation's rows while the
        type-scoped object_states delete which opens the transaction
        matched nothing, leaving a state row with no object behind it.
        """
        for declared, seeded in ((ObjectType.NET_OP,
                                  ObjectType.NODE_INST_OP),
                                 (ObjectType.NODE_INST_OP,
                                  ObjectType.NET_OP)):
            with self.subTest(declared=declared, seeded=seeded):
                uuid = self._seed(seeded, 'deleted', self.old)
                self._assert_all(seeded, uuid, 1)

                mariadb_hard_delete.hard_delete_objects(
                    declared, [uuid], unconditional=True)
                self._assert_all(seeded, uuid, 1)

                # Guarded mode was never able to do this, because what
                # the state delete returns is what the rest of the
                # transaction deletes, and it is type-scoped.
                self.assertEqual([], mariadb_hard_delete.hard_delete_objects(
                    declared, [uuid], state_values=FINAL_STATES,
                    updated_before=self.cutoff))
                self._assert_all(seeded, uuid, 1)

                # And its own type's batch still takes it.
                self.assertEqual([uuid], mariadb_hard_delete.hard_delete_objects(
                    seeded, [uuid], state_values=FINAL_STATES,
                    updated_before=self.cutoff))
                self._assert_all(seeded, uuid, 0)

    def test_unconditional_deletes_whatever_the_state(self):
        for object_type in BATCHABLE:
            with self.subTest(object_type=object_type):
                live = self._seed(object_type, 'created', self.old)
                young = self._seed(object_type, 'deleted', self.young)
                stateless = self._seed(object_type, with_state=False)
                untouched = self._seed(object_type, 'deleted', self.old)

                deleted = mariadb_hard_delete.hard_delete_objects(
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
        names = mariadb_hard_delete.HARD_DELETE_BATCH_FUNCTIONS[ObjectType.BLOB]
        failing = 'delete_blob_attributes'
        self.assertGreaterEqual(names.index(failing), 2)
        self.assertLess(names.index(failing), len(names) - 1)
        broken = mariadb_hard_delete.HARD_DELETE_STATEMENTS[failing]._replace(
            build=lambda t, u: sa.text(
                'DELETE FROM blob_attributes WHERE no_such_column = 1'))

        for unconditional in (False, True):
            with self.subTest(unconditional=unconditional):
                uuid = self._seed(ObjectType.BLOB, 'deleted', self.old)
                with mock.patch.dict(mariadb_hard_delete.HARD_DELETE_STATEMENTS,
                                     {failing: broken}), \
                        mock.patch('shakenfist.mariadb_hard_delete.LOG') as mock_log:
                    if unconditional:
                        deleted = mariadb_hard_delete.hard_delete_objects(
                            ObjectType.BLOB, [uuid], unconditional=True)
                    else:
                        deleted = mariadb_hard_delete.hard_delete_objects(
                            ObjectType.BLOB, [uuid],
                            state_values=FINAL_STATES,
                            updated_before=self.cutoff)

                self.assertIsNone(deleted)
                mock_log.warning.assert_called_once()
                self._assert_all(ObjectType.BLOB, uuid, 1)

                # And with the statement restored, the same call works.
                deleted = mariadb_hard_delete.hard_delete_objects(
                    ObjectType.BLOB, [uuid], state_values=FINAL_STATES,
                    updated_before=self.cutoff)
                self.assertEqual([uuid], deleted)
                self._assert_all(ObjectType.BLOB, uuid, 0)

    def test_too_many_uuids_is_refused(self):
        eligible = self._seed(ObjectType.NET_OP, 'deleted', self.old)
        uuids = [eligible] + [str(uuid4()) for _ in range(
            mariadb_hard_delete.HARD_DELETE_OBJECTS_MAX)]
        self.assertRaises(
            ValueError, mariadb_hard_delete.hard_delete_objects, ObjectType.NET_OP,
            uuids, state_values=FINAL_STATES, updated_before=self.cutoff)
        self._assert_all(ObjectType.NET_OP, eligible, 1)

        # The most one call may name is accepted.
        deleted = mariadb_hard_delete.hard_delete_objects(
            ObjectType.NET_OP, uuids[:mariadb_hard_delete.HARD_DELETE_OBJECTS_MAX],
            state_values=FINAL_STATES, updated_before=self.cutoff)
        self.assertEqual([eligible], deleted)
        self._assert_all(ObjectType.NET_OP, eligible, 0)

    def test_per_call_deletes_what_the_batch_deletes(self):
        # The fallback for an sf-database without the batch removes the
        # same rows, one public call at a time.
        for object_type in BATCHABLE:
            with self.subTest(object_type=object_type):
                uuid = self._seed(object_type, 'deleted', self.old)
                with mock.patch('shakenfist.mariadb_hard_delete._direct_hard_delete_objects'
                                ) as batch:
                    mariadb_hard_delete.hard_delete_objects_per_call(object_type, uuid)
                batch.assert_not_called()
                self._assert_all(object_type, uuid, 0)
