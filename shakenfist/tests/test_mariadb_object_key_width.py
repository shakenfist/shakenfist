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

from shakenfist import constants
from shakenfist import mariadb
from shakenfist.external_api import auth
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
        # The same rule, applied by the column's name rather than by the
        # list above, so a new table keyed the same way is covered
        # without anyone remembering to add it.
        checked = []
        for table_name, getter in TABLE_GETTERS.items():
            table = getter()
            for column in table.columns:
                if column.name != 'object_uuid':
                    continue
                self.assertGreaterEqual(
                    column.type.length, constants.OBJECT_KEY_MAX_LENGTH,
                    '%s.object_uuid is %s characters wide'
                    % (table_name, column.type.length))
                checked.append(table_name)

        self.assertNotEqual(
            [], checked, 'no object_uuid column was found to check')

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
