# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB tests for retiring namespace_attributes.keys (issue 5016).

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh,
which runs every test_mariadb_*_live module behind one MariaDB install;
developers can point at a local instance), which in CI means the merge
queue.

What a unit test cannot show is the thing which would actually hurt: the
namespace_keys v1->v2 data migration is the only reader the legacy keys
column has, and the namespace_attributes v2->v3 step drops it, so the
order ensure_schema() runs them in decides whether a cluster upgrading
from before first-class keys keeps its keys or loses every one of them.
Both steps are DDL and raw SQL against a column no table definition
declares any more, so these tests build the version 2 shape with keys in
it, run the upgrade the way ensure_schema() does, and look at what is
left.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment. The
test_mariadb_*_live modules share one database, so they must be run
serially -- ``stestr run --serial``, as CI does.
"""

import json
import os
import unittest

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'

TEST_TABLES = [
    'namespace_key_attributes',
    'namespace_keys',
    'namespace_attributes',
    'namespaces',
    'object_states',
    'schema_versions',
]

# The four tables in ensure_schema() order. namespace_attributes is last
# because its v2->v3 step drops the column the namespace_keys migration
# reads.
ENSURES = [
    mariadb._ensure_namespaces_schema,
    mariadb._ensure_namespace_key_attributes_schema,
    mariadb._ensure_namespace_keys_schema,
    mariadb._ensure_namespace_attributes_schema,
]


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class NamespaceKeysColumnLiveTestCase(base.ShakenFistTestCase):
    """Upgrade a version 2 namespace_attributes table and look at it."""

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)
        self._drop_tables()

        mariadb._ensure_schema_versions_table(self.engine)
        mariadb._ensure_object_states_schema(self.engine)
        for ensure in ENSURES:
            ensure(self.engine)

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _regress_to_version_2(self):
        """Turn the tables the ensures built back into their v2 shape.

        A cluster which has not upgraded onto first-class keys has a
        namespace_attributes table with a keys JSON column, at version
        2, and a namespace_keys table at version 1 with nothing in it.
        """
        with self.engine.connect() as conn:
            conn.execute(sa.text(
                'ALTER TABLE namespace_attributes '
                'ADD COLUMN `keys` JSON NULL'))
            conn.commit()
        mariadb._set_table_version(self.engine, 'namespace_attributes', 2)
        mariadb._set_table_version(self.engine, 'namespace_keys', 1)

    def _write_legacy_namespace(self, name, keys):
        with self.engine.connect() as conn:
            conn.execute(sa.text(
                'INSERT INTO namespaces (name, version) VALUES (:name, 7)'),
                {'name': name})
            conn.execute(sa.text(
                'INSERT INTO namespace_attributes (name, `keys`, trust) '
                'VALUES (:name, :keys, :trust)'),
                {'name': name, 'keys': json.dumps(keys),
                 'trust': json.dumps(['system'])})
            conn.commit()

    def _columns(self):
        with self.engine.connect() as conn:
            return {row[0] for row in conn.execute(sa.text(
                'SELECT COLUMN_NAME FROM information_schema.COLUMNS '
                'WHERE TABLE_SCHEMA = DATABASE() '
                "AND TABLE_NAME = 'namespace_attributes'")).fetchall()}

    def _keys_in_the_new_tables(self):
        keys = mariadb._get_namespace_keys_table()
        attrs = mariadb._get_namespace_key_attributes_table()
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa.select(keys.c.namespace,
                          keys.c.name.label('key_name'),
                          attrs.c.key.label('hash'),
                          attrs.c.nonce, attrs.c.expiry)
                .select_from(keys.join(attrs, keys.c.uuid == attrs.c.uuid))
            ).fetchall()
        return {(r.namespace, r.key_name): (r.hash, r.nonce, r.expiry)
                for r in rows}

    def test_a_fresh_install_never_has_the_column(self):
        """setUp() built the tables from scratch, as a new cluster does."""
        self.assertEqual({'name', 'trust'}, self._columns())
        self.assertEqual(
            3,
            mariadb._get_table_version(self.engine, 'namespace_attributes'))

    def test_the_upgrade_fans_keys_out_and_then_drops_the_column(self):
        self._regress_to_version_2()
        self._write_legacy_namespace('banana', {'nonced_keys': {
            'first': {'key': 'aGFzaC1vbmU=', 'nonce': 'nonce-one'},
            'second': {'key': 'aGFzaC10d28=', 'nonce': 'nonce-two',
                       'expiry': 4242.0},
        }})

        results = {}
        for ensure in ENSURES:
            result = ensure(self.engine)
            results[result['table']] = result

        self.assertEqual(2, results['namespace_keys']['end_version'])
        self.assertEqual(3, results['namespace_attributes']['end_version'])

        # The keys reached the new tables, hash, nonce and expiry
        # verbatim, so tokens minted before the upgrade keep validating.
        self.assertEqual(
            {('banana', 'first'): ('aGFzaC1vbmU=', 'nonce-one', None),
             ('banana', 'second'): ('aGFzaC10d28=', 'nonce-two', 4242.0)},
            self._keys_in_the_new_tables())

        # And only then did the column go.
        self.assertEqual({'name', 'trust'}, self._columns())

    def test_the_upgrade_is_safe_to_re_run(self):
        self._regress_to_version_2()
        self._write_legacy_namespace('banana', {'nonced_keys': {
            'first': {'key': 'aGFzaC1vbmU=', 'nonce': 'nonce-one'},
        }})

        for _ in range(2):
            for ensure in ENSURES:
                ensure(self.engine)

        # One pass over a dropped column is a no-op rather than an
        # error, and the key is not duplicated by the second run.
        self.assertEqual(
            {('banana', 'first'): ('aGFzaC1vbmU=', 'nonce-one', None)},
            self._keys_in_the_new_tables())
        self.assertEqual({'name', 'trust'}, self._columns())

    def test_a_namespace_row_survives_the_drop(self):
        """Everything else on the row is untouched by the ALTER."""
        self._regress_to_version_2()
        self._write_legacy_namespace('banana', {'nonced_keys': {}})

        for ensure in ENSURES:
            ensure(self.engine)

        table = mariadb._get_namespace_attributes_table()
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(table).where(
                table.c.name == 'banana')).fetchone()
        self.assertEqual('banana', row.name)
        self.assertEqual(['system'], row.trust)
