# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Every generated table's DDL, against its schema version.

The ``_ensure_*_schema()`` functions in mariadb.py create a table from
its definition only when the table is absent, so a definition change
reaches a fresh install and nothing else. An upgraded cluster gets it
only through a migration, and a migration only runs when the table's
entry in ``EXPECTED_SCHEMA_VERSIONS`` moves. A definition that changes
without the version moving gives new clusters one schema and upgraded
ones another, and ``verify_schema_versions()`` sees nothing wrong,
because the version it checks did not change.

Since issue 4901 a str field's ``Field(max_length=N)`` builds its
column, so raising a declaration past 255 is one such change, and an
easy one to make without noticing it is DDL at all. So this records
what every table in ``EXPECTED_SCHEMA_VERSIONS`` compiles to -- each
column's type and nullability, the primary key and each index -- with
the version it was recorded at, and fails when the two move apart:

* the DDL moved and the version did not: bump the version and write
  the migration;
* the version moved: re-record, so the next change is compared against
  the schema that version describes.

To re-record after a deliberate change, run this module:

    python -m shakenfist.tests.schema.test_schema_fingerprints
"""

import json
import os
import sys

from sqlalchemy.dialects.mysql import mariadb as mariadb_dialect

from shakenfist import mariadb
from shakenfist.tests import base


FINGERPRINTS_PATH = os.path.join(
    os.path.dirname(__file__), 'schema_fingerprints.json')

RERECORD = 'python -m shakenfist.tests.schema.test_schema_fingerprints'


def _fingerprint(table):
    """What a table's CREATE TABLE says, as a comparable structure."""
    dialect = mariadb_dialect.MariaDBDialect()
    return {
        'columns': {
            column.name: '%s%s' % (column.type.compile(dialect=dialect),
                                   ' NULL' if column.nullable
                                   else ' NOT NULL')
            for column in table.columns},
        'primary_key': [column.name for column in table.primary_key.columns],
        'indexes': {
            index.name: '%s(%s)' % ('UNIQUE ' if index.unique else '',
                                    ', '.join(c.name for c in index.columns))
            for index in table.indexes},
    }


def _current():
    """{table: {'version': N, ...fingerprint}} for every versioned table.

    Built through each table's own ``_get_<name>_table()``, which is the
    definition its ``_ensure_*_schema()`` creates from; a table without
    one fails here rather than being skipped.
    """
    out = {}
    for name, version in sorted(mariadb.EXPECTED_SCHEMA_VERSIONS.items()):
        table = getattr(mariadb, '_get_%s_table' % name)()
        out[name] = dict(version=version, **_fingerprint(table))
    return out


def _problems(recorded, current):
    """Why ``current`` cannot be accepted against ``recorded``."""
    problems = []
    for name in sorted(set(recorded) | set(current)):
        if name not in current:
            problems.append(
                '%s is recorded but no longer versioned; re-record' % name)
            continue
        if name not in recorded:
            problems.append(
                '%s is versioned but not recorded; re-record' % name)
            continue

        was, now = recorded[name], current[name]
        moved = sorted(
            key for key in ('columns', 'primary_key', 'indexes')
            if was[key] != now[key])
        if was['version'] == now['version']:
            if moved:
                detail = []
                for key in moved:
                    if isinstance(now[key], dict):
                        for item in sorted(set(was[key]) | set(now[key])):
                            if was[key].get(item) != now[key].get(item):
                                detail.append('%s %s: %s -> %s' % (
                                    key, item, was[key].get(item),
                                    now[key].get(item)))
                    else:
                        detail.append('%s: %s -> %s'
                                      % (key, was[key], now[key]))
                problems.append(
                    '%s generates different DDL (%s) but '
                    'EXPECTED_SCHEMA_VERSIONS still says %d. A fresh '
                    'install would get the new definition and an upgraded '
                    'cluster would keep the old one: bump the version, '
                    'write the migration, then re-record'
                    % (name, '; '.join(detail), now['version']))
        else:
            problems.append(
                '%s moved from version %d to %d; re-record so the next '
                'change is compared against what version %d creates'
                % (name, was['version'], now['version'], now['version']))
    return problems


class SchemaFingerprintTestCase(base.ShakenFistTestCase):

    def test_no_table_changes_ddl_without_changing_version(self):
        with open(FINGERPRINTS_PATH) as f:
            recorded = json.load(f)
        current = _current()

        # A floor, so the comparison below cannot pass vacuously on a
        # table walk that found nothing.
        self.assertGreater(len(current), 40)

        problems = _problems(recorded, current)
        self.assertEqual(
            [], problems,
            '\n'.join(problems) + '\n\nRe-record with: ' + RERECORD)

    def test_a_widened_column_without_a_version_bump_is_found(self):
        """The positive control, in the shape issue 4901 opened."""
        recorded = {'probe': {
            'version': 1, 'columns': {'value': 'VARCHAR(255) NOT NULL'},
            'primary_key': ['id'], 'indexes': {}}}
        current = {'probe': dict(
            recorded['probe'], columns={'value': 'VARCHAR(1024) NOT NULL'})}

        problems = _problems(recorded, current)
        self.assertEqual(1, len(problems))
        self.assertIn('VARCHAR(255) NOT NULL -> VARCHAR(1024) NOT NULL',
                      problems[0])
        self.assertIn('bump the version', problems[0])

    def test_an_added_index_without_a_version_bump_is_found(self):
        recorded = {'probe': {
            'version': 1, 'columns': {}, 'primary_key': ['id'],
            'indexes': {}}}
        current = {'probe': dict(
            recorded['probe'], indexes={'idx_probe_value': '(value)'})}

        self.assertEqual(1, len(_problems(recorded, current)))

    def test_a_bumped_version_asks_to_be_re_recorded(self):
        recorded = {'probe': {
            'version': 1, 'columns': {'value': 'VARCHAR(255) NOT NULL'},
            'primary_key': ['id'], 'indexes': {}}}
        current = {'probe': dict(
            recorded['probe'], version=2,
            columns={'value': 'VARCHAR(1024) NOT NULL'})}

        problems = _problems(recorded, current)
        self.assertEqual(1, len(problems))
        self.assertIn('moved from version 1 to 2', problems[0])
        self.assertNotIn('bump the version', problems[0])

        # A bump with no DDL change is still a fixture to refresh.
        same_ddl = {'probe': dict(recorded['probe'], version=2)}
        self.assertEqual(1, len(_problems(recorded, same_ddl)))

    def test_an_unchanged_table_is_accepted(self):
        recorded = {'probe': {
            'version': 1, 'columns': {'value': 'VARCHAR(255) NOT NULL'},
            'primary_key': ['id'], 'indexes': {}}}
        self.assertEqual([], _problems(recorded, dict(recorded)))


if __name__ == '__main__':
    with open(FINGERPRINTS_PATH, 'w') as f:
        json.dump(_current(), f, indent=2, sort_keys=True)
        f.write('\n')
    print('recorded %d tables to %s'
          % (len(mariadb.EXPECTED_SCHEMA_VERSIONS), FINGERPRINTS_PATH),
          file=sys.stderr)
