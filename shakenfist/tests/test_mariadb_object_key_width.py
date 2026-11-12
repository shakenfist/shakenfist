# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Every column which stores an object key must hold the widest one.

An object's key is its uuid, 36 characters -- except for a namespace,
whose key is its *name*. The create handler publishes and enforces a 63
character ceiling on a name, and `object_states.object_uuid` was still
VARCHAR(36), so every name between 37 and 63 characters was accepted by
the API and then refused by MariaDB with error 1406. `POST
/auth/namespaces` answered an opaque 500 and left a namespace row behind
with no state (issue 4856).

The number itself is not the interesting part, and neither is the one
column that happened to fail. What matters is that the published ceiling
and the column width have a single source of truth, and that a column
which stores an object key cannot be declared narrower than it. These
tests hold both ends.
"""

import re
import time
from unittest import mock
import uuid

from sqlalchemy.exc import OperationalError

from shakenfist import constants
from shakenfist import mariadb
from shakenfist.external_api import auth
from shakenfist.schema.cluster_operation_target import (
    ClusterOperationTargetData)
from shakenfist.schema.object_state import ObjectState
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


# The columns which store an object's key, rather than something which
# is always a real uuid (`events.event_uuid`, a blob hash, a
# `cluster_operation_targets.operation_uuid`). A new one belongs here,
# and `constants.OBJECT_KEY_MAX_LENGTH` is the width it must have.
OBJECT_KEY_COLUMNS = [
    ('object_states', 'object_uuid'),
    ('object_metadata', 'object_uuid'),
    ('event_objects', 'object_uuid'),
    ('cluster_operation_targets', 'target_uuid'),
    ('object_references', 'source_uuid'),
    ('object_references', 'target_uuid'),
]

# A column with one of these names holds an object key wherever it
# appears.
KEY_COLUMN_NAMES = {'object_uuid', 'target_uuid', 'source_uuid'}

# The Pydantic models which describe a row of one of those columns.
OBJECT_KEY_MODEL_FIELDS = [
    (ObjectState, 'object_uuid'),
    (ClusterOperationTargetData, 'target_uuid'),
]

# Table definition accessors, by table name.
TABLE_GETTERS = {
    'object_states': mariadb._get_object_states_table,
    'object_metadata': mariadb._get_object_metadata_table,
    'event_objects': mariadb._get_event_objects_table,
    'cluster_operation_targets': mariadb._get_cluster_operation_targets_table,
    'object_references': mariadb._get_object_references_table,
}


class ObjectKeyWidthTestCase(base.ShakenFistTestCase):
    def test_every_object_key_column_holds_the_widest_key(self):
        for table_name, column_name in OBJECT_KEY_COLUMNS:
            table = TABLE_GETTERS[table_name]()

            # Vacancy guard: a renamed column must fail here rather
            # than quietly stop being checked.
            self.assertIn(
                column_name, table.columns,
                '%s has no column %s, so this test is checking nothing'
                % (table_name, column_name))

            column = table.columns[column_name]
            self.assertGreaterEqual(
                column.type.length, constants.OBJECT_KEY_MAX_LENGTH,
                '%s.%s is %s characters wide, which cannot hold an object '
                'key of %s (issue 4856)'
                % (table_name, column_name, column.type.length,
                   constants.OBJECT_KEY_MAX_LENGTH))

    def test_every_column_named_object_uuid_is_wide_enough(self):
        # The same rule, applied by the column's name to every table the
        # module can build rather than to the list above, so a new table
        # keyed the same way is covered without anyone remembering to
        # add it. The tables are found by their accessor's name, which
        # is the one convention every table in mariadb.py follows.
        getters = [
            getattr(mariadb, name) for name in dir(mariadb)
            if name.startswith('_get_') and name.endswith('_table')]
        self.assertGreater(
            len(getters), len(TABLE_GETTERS),
            'found only %d table accessors in mariadb, so this test is '
            'not looking at the whole schema' % len(getters))

        checked = []
        for getter in getters:
            table = getter()
            for column in table.columns:
                if column.name not in KEY_COLUMN_NAMES:
                    continue
                self.assertGreaterEqual(
                    column.type.length, constants.OBJECT_KEY_MAX_LENGTH,
                    '%s.%s is %s characters wide'
                    % (table.name, column.name, column.type.length))
                checked.append((table.name, column.name))

        # Vacancy guard: every column the explicit list names must have
        # been found by the walk, or the walk is not seeing the schema.
        for key_column in OBJECT_KEY_COLUMNS:
            self.assertIn(key_column, checked)

    def test_every_model_of_an_object_key_holds_the_widest_key(self):
        # The column is not the only width: a Pydantic model which
        # describes the row validates before the write reaches it, so a
        # model capped at a uuid's width refuses a long namespace name
        # with a ValidationError rather than error 1406.
        for model, field_name in OBJECT_KEY_MODEL_FIELDS:
            field = model.model_fields[field_name]
            max_lengths = [
                m.max_length for m in field.metadata
                if getattr(m, 'max_length', None) is not None]
            self.assertEqual(
                [constants.OBJECT_KEY_MAX_LENGTH], max_lengths,
                '%s.%s is capped at %s, which cannot hold an object key '
                'of %s (issue 4856)'
                % (model.__name__, field_name, max_lengths,
                   constants.OBJECT_KEY_MAX_LENGTH))

    def test_a_cluster_operation_can_target_a_long_namespace(self):
        # The write mariadb.py makes for a cluster operation, at the
        # widest legal namespace name.
        name = 'n' * constants.NAMESPACE_NAME_MAX_LENGTH
        target = ClusterOperationTargetData(
            operation_uuid=str(uuid.uuid4()),
            operation_type='namespace_delete',
            target_object_type=ObjectType.NAMESPACE.value,
            target_uuid=name,
            created_at=time.time())
        self.assertEqual(name, target.target_uuid)

    def test_the_published_name_ceiling_is_the_column_width(self):
        # The defect was these two disagreeing. NAMESPACE_NAME_PATTERN is
        # published in the OpenAPI specification and enforced by the
        # create handler, so a ceiling above the column width is an
        # advertised 500.
        match = re.search(r'\{1,(\d+)\}', auth.NAMESPACE_NAME_PATTERN)
        self.assertIsNotNone(
            match,
            'cannot read a ceiling out of NAMESPACE_NAME_PATTERN %r'
            % auth.NAMESPACE_NAME_PATTERN)

        self.assertEqual(
            constants.NAMESPACE_NAME_MAX_LENGTH, int(match.group(1)))
        self.assertGreaterEqual(
            constants.OBJECT_KEY_MAX_LENGTH,
            constants.NAMESPACE_NAME_MAX_LENGTH)

    def test_the_longest_legal_namespace_name_matches_the_pattern(self):
        # And one length below, one above, so the ceiling is exactly
        # where the constant says it is.
        ceiling = constants.NAMESPACE_NAME_MAX_LENGTH
        self.assertTrue(
            re.fullmatch(auth.NAMESPACE_NAME_PATTERN, 'n' * ceiling))
        self.assertTrue(
            re.fullmatch(auth.NAMESPACE_NAME_PATTERN, 'n' * (ceiling - 1)))
        self.assertIsNone(
            re.fullmatch(auth.NAMESPACE_NAME_PATTERN, 'n' * (ceiling + 1)))


class WidenObjectKeyColumnTestCase(base.ShakenFistTestCase):
    def _connection(self, engine, current_width):
        conn = engine.connect.return_value.__enter__.return_value
        probe = mock.MagicMock()
        probe.first.return_value = (current_width,)
        return conn, probe

    def test_a_refused_instant_alter_falls_back(self):
        # A server which cannot widen the column instantly must still
        # widen it, without the INSTANT clause, and commit. The live
        # suite proves the same against a real server's refusal.
        engine = mock.MagicMock()
        conn, probe = self._connection(engine, 36)
        order = []

        def execute(statement, *args):
            sql = str(statement)
            if 'information_schema' in sql:
                return probe
            if 'ALGORITHM=INSTANT' in sql:
                order.append('instant')
                raise OperationalError(sql, {}, Exception(
                    'Cannot change column type. Try ALGORITHM=COPY'))
            order.append('plain')
            return mock.MagicMock()

        conn.execute.side_effect = execute
        conn.commit.side_effect = lambda: order.append('commit')

        self.assertTrue(mariadb._widen_object_key_column(
            engine, 'object_states', 'object_uuid'))
        self.assertEqual(['instant', 'plain', 'commit'], order)

        plain = str(conn.execute.call_args_list[-1].args[0])
        self.assertIn(
            'VARCHAR(%d) NOT NULL' % constants.OBJECT_KEY_MAX_LENGTH, plain)
        self.assertNotIn('ALGORITHM', plain)

    def test_an_accepted_instant_alter_does_not_fall_back(self):
        engine = mock.MagicMock()
        conn, probe = self._connection(engine, 36)
        conn.execute.side_effect = lambda statement, *args: (
            probe if 'information_schema' in str(statement)
            else mock.MagicMock())

        self.assertTrue(mariadb._widen_object_key_column(
            engine, 'object_states', 'object_uuid'))
        alters = [
            str(c.args[0]) for c in conn.execute.call_args_list
            if 'ALTER' in str(c.args[0])]
        self.assertEqual(1, len(alters))
        self.assertIn('ALGORITHM=INSTANT', alters[0])
        conn.commit.assert_called_once()
