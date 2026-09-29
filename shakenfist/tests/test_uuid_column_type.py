# Copyright 2019 Michael Still and contributors
#
# Every SQLAlchemy Uuid column must keep the undashed CHAR(32) behaviour its
# table was created with. SQLAlchemy 2.1 made a bare sa.Uuid() go native on
# the MariaDB dialect, which silently broke every uuid bind against the
# existing CHAR(32) columns -- see uuid_column_type()'s docstring.
import inspect
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.mysql.mariadb import MariaDBDialect
from sqlalchemy.schema import CreateColumn

from shakenfist import mariadb
from shakenfist.schema.sqlalchemy import uuid_column_type
from shakenfist.tests import base


def _all_mariadb_tables() -> list[sa.Table]:
    """Build every table mariadb.py defines, via its getters.

    Discovered rather than listed, so a table added later is covered
    without anyone having to remember this test.
    """
    tables = []
    for name, getter in inspect.getmembers(mariadb, inspect.isfunction):
        if not (name.startswith('_get_') and name.endswith('_table')):
            continue
        if getter.__module__ != mariadb.__name__:
            continue
        required = [
            p for p in inspect.signature(getter).parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
        if required:
            continue
        tables.append(getter())
    return tables


class UuidColumnTypeTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.dialect = MariaDBDialect()

    def test_binds_undashed_hex_on_mariadb(self):
        value = uuid.UUID('12345678-1234-5678-1234-567812345678')
        process = uuid_column_type().bind_processor(self.dialect)
        self.assertIsNotNone(process)
        self.assertEqual('12345678123456781234567812345678', process(value))

    def test_reads_back_uuid_objects_on_mariadb(self):
        process = uuid_column_type().result_processor(self.dialect, None)
        self.assertIsNotNone(process)
        self.assertEqual(
            uuid.UUID('12345678-1234-5678-1234-567812345678'),
            process('12345678123456781234567812345678'))

    def test_renders_char32_on_mariadb(self):
        column = sa.Column('uuid', uuid_column_type())
        sa.Table('t', sa.MetaData(), column)
        ddl = str(CreateColumn(column).compile(dialect=self.dialect))
        self.assertIn('CHAR(32)', ddl)

    def test_every_uuid_column_is_character_based(self):
        tables = _all_mariadb_tables()
        # Guard against the discovery silently finding nothing.
        self.assertGreater(len(tables), 20)

        uuid_columns = 0
        native = []
        for table in tables:
            for column in table.columns:
                if isinstance(column.type, sa.Uuid):
                    uuid_columns += 1
                    if column.type.native_uuid:
                        native.append(f'{table.name}.{column.name}')
        self.assertGreater(uuid_columns, 0)
        self.assertEqual(
            [], native,
            'These columns use a bare sa.Uuid(), which is a native UUID on '
            'MariaDB under SQLAlchemy 2.1 and cannot match the CHAR(32) data '
            'already stored. Use uuid_column_type() instead.')
