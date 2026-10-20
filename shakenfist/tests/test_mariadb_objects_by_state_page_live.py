# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB test for the keyset-paged object_states read.

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh,
which runs every test_mariadb_*_live module behind one MariaDB install;
developers can point at a local instance). The unit tests mock the
connection, so they cannot show that the keyset predicate, the ORDER BY
and the LIMIT one-past-the-page compose into a walk which visits every
matching row exactly once. That is what these tests check, by walking a
type at several page sizes and comparing the union of the pages with
the unpaged read.

They cannot show which index the optimiser picks: a test table is far
too small for it to choose the way it would on a large deployment.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment. The
test_mariadb_*_live modules share one database, so they must be run
serially -- ``stestr run --serial``, as CI does.
"""

import os
import time
import unittest
from unittest import mock
from uuid import uuid4

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'

TEST_TABLES = [
    'object_states',
    'schema_versions',
]

FINAL_STATES = ['deleted', 'error']


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class ObjectsByStatePageLiveTestCase(base.ShakenFistTestCase):
    """Walk object_states a page at a time against a real MariaDB."""

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)
        self._drop_tables()

        mariadb._ensure_schema_versions_table(self.engine)
        mariadb._ensure_object_states_schema(self.engine)

        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.now = time.time()
        self.cutoff = self.now - 3600
        self._seed()

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _seed(self):
        """Seed two types with rows in mixed states and ages.

        ``self.expected`` is the set of blob uuids a read with
        FINAL_STATES and ``updated_before=self.cutoff`` must return. The
        other rows each fail exactly one filter. The instance rows
        include one which shares a blob's uuid, which the composite
        primary key allows, so a walk which lost its object_type filter
        would return it.
        """
        old = self.cutoff - 60
        young = self.cutoff + 60
        self.expected = set()
        rows = []

        for i in range(7):
            uuid = str(uuid4())
            rows.append((ObjectType.BLOB, uuid, FINAL_STATES[i % 2], old))
            self.expected.add(uuid)
        self.young_blobs = set()
        for state in FINAL_STATES:
            uuid = str(uuid4())
            rows.append((ObjectType.BLOB, uuid, state, young))
            self.young_blobs.add(uuid)
        self.live_blobs = set()
        for _ in range(3):
            uuid = str(uuid4())
            rows.append((ObjectType.BLOB, uuid, 'created', old))
            self.live_blobs.add(uuid)

        self.instances = {str(uuid4()) for _ in range(4)}
        self.instances.add(sorted(self.expected)[0])
        for uuid in self.instances:
            rows.append((ObjectType.INSTANCE, uuid, 'deleted', old))
        self.instances_only = self.instances - self.expected

        table = mariadb._get_object_states_table()
        with self.engine.connect() as conn:
            for object_type, uuid, state, update_time in rows:
                conn.execute(sa.insert(table).values(
                    object_type=object_type, object_uuid=uuid,
                    state_value=state, update_time=update_time))
            conn.commit()

    def _walk(self, page_size):
        pages = []
        after_uuid = ''
        while True:
            page = mariadb.get_objects_by_state_page(
                ObjectType.BLOB, FINAL_STATES, page_size,
                after_uuid=after_uuid, updated_before=self.cutoff)
            self.assertIsNotNone(page)
            pages.append(page)
            if not page.more_available:
                return pages
            self.assertTrue(page.uuids)
            after_uuid = page.uuids[-1]
            self.assertLess(len(pages), 100, 'the walk did not terminate')

    def _assert_walk(self, page_size):
        unpaged = mariadb.get_objects_by_state(
            ObjectType.BLOB, FINAL_STATES, updated_before=self.cutoff)
        self.assertEqual(self.expected, set(unpaged))

        pages = self._walk(page_size)
        walked = [uuid for page in pages for uuid in page.uuids]

        # The union of the pages is the unpaged read, with nothing seen
        # twice.
        self.assertEqual(sorted(unpaged), sorted(walked))
        self.assertEqual(len(walked), len(set(walked)))

        # Each page is in ascending order, and so is the whole walk,
        # which is what makes the last uuid a usable cursor.
        for page in pages:
            self.assertEqual(sorted(page.uuids), page.uuids)
            self.assertLessEqual(len(page.uuids), page_size)
        self.assertEqual(sorted(walked), walked)

        # Only the last page reports that the walk is complete.
        self.assertEqual(
            [True] * (len(pages) - 1) + [False],
            [page.more_available for page in pages])

        # The filters hold: no young, live or other-type rows.
        self.assertFalse(set(walked) & self.young_blobs)
        self.assertFalse(set(walked) & self.live_blobs)
        self.assertFalse(set(walked) & self.instances_only)
        return pages

    def test_walk_one_at_a_time(self):
        pages = self._assert_walk(1)
        # Seven single-uuid pages and no trailing empty one: the extra
        # row read past each page is what tells the seventh it is last.
        self.assertEqual([1] * 7, [len(page.uuids) for page in pages])

    def test_walk_two_at_a_time(self):
        pages = self._assert_walk(2)
        self.assertEqual([2, 2, 2, 1], [len(page.uuids) for page in pages])

    def test_walk_whole_type_in_one_page(self):
        pages = self._assert_walk(len(self.expected))
        self.assertEqual(1, len(pages))
        self.assertFalse(pages[0].more_available)

    def test_cursor_need_not_be_a_matching_row(self):
        # A cursor is only a position. A row the cursor names may have
        # been deleted, or left the filtered states, since the page that
        # produced it was read.
        cursor = sorted(self.live_blobs)[0]
        page = mariadb.get_objects_by_state_page(
            ObjectType.BLOB, FINAL_STATES, 100, after_uuid=cursor,
            updated_before=self.cutoff)
        self.assertEqual(
            sorted(uuid for uuid in self.expected if uuid > cursor),
            page.uuids)
        self.assertFalse(page.more_available)

    def test_other_type_walks_separately(self):
        page = mariadb.get_objects_by_state_page(
            ObjectType.INSTANCE, ['deleted'], 100,
            updated_before=self.cutoff)
        self.assertEqual(sorted(self.instances), page.uuids)
        self.assertFalse(page.more_available)

    def test_empty_type_is_an_empty_page_not_a_failure(self):
        page = mariadb.get_objects_by_state_page(
            ObjectType.NETWORK, FINAL_STATES, 10)
        self.assertEqual(
            mariadb.ObjectsByStatePage(uuids=[], more_available=False),
            page)
