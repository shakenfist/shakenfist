# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB tests for the event_objects timestamp backfill.

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh,
which runs every test_mariadb_*_live module behind one MariaDB install;
developers can point at a local instance).

The backfill is a multi-table ``UPDATE ... JOIN``, which is MariaDB
syntax sqlite does not have, so the unit tests can only assert its
shape against a mocked connection. What they cannot show is the thing
issue 4782 is about: that the statements match the rows a daemon on the
previous release left at the column default, give them the timestamp of
the event they reference, and leave every other row alone. A statement
keyed on the wrong column, or a WHERE which matched nothing, would pass
every shape assertion and repair nothing.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment. The
test_mariadb_*_live modules share one database, so they must be run
serially -- ``stestr run --serial``, as CI does.
"""

import os
import unittest
from unittest import mock
from uuid import uuid4

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'

TEST_TABLES = [
    'event_objects',
    'events',
    'schema_versions',
]


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class EventObjectTimestampBackfillLiveTestCase(base.ShakenFistTestCase):
    """Repair the rows a rolling upgrade's old writers left behind."""

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)
        self._drop_tables()

        mariadb._ensure_schema_versions_table(self.engine)
        mariadb._ensure_events_schema(self.engine)
        mariadb._ensure_event_objects_schema(self.engine)

        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.events = mariadb._get_events_table()
        self.event_objects = mariadb._get_event_objects_table()

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _write(self, timestamp, object_type='instance', object_uuid=None,
               reference_timestamp=None, with_event=True):
        """Write one event and its reference row, as the old code did.

        ``reference_timestamp=None`` means the event_objects row does
        not name a timestamp at all, which is what a daemon written
        before the column did and what leaves the row at DEFAULT 0.
        """
        event_uuid = str(uuid4())
        object_uuid = object_uuid or str(uuid4())
        with self.engine.connect() as conn:
            if with_event:
                conn.execute(sa.insert(self.events).values(
                    event_uuid=event_uuid, event_type='audit',
                    timestamp=timestamp, fqdn='node.example.com',
                    duration=None, message='live test event',
                    extra=None, request_id=None))
            values = {
                'object_type': object_type,
                'object_uuid': object_uuid,
                'event_uuid': event_uuid,
            }
            if reference_timestamp is not None:
                values['timestamp'] = reference_timestamp
            conn.execute(sa.insert(self.event_objects).values(**values))
            conn.commit()
        return object_type, object_uuid, event_uuid

    def _reference_timestamps(self):
        with self.engine.connect() as conn:
            return {
                row.event_uuid: row.timestamp
                for row in conn.execute(sa.text(
                    'SELECT event_uuid, timestamp FROM event_objects'))
            }

    def test_an_old_writers_row_really_does_land_at_zero(self):
        """The control. Without it the repair could be fixing nothing.

        An insert which does not mention the column is the whole
        premise of issue 4782: if MariaDB filled it from somewhere
        there would be no defect and no need for this file.
        """
        _, _, event_uuid = self._write(1_762_200_000.0)
        self.assertEqual(0.0, self._reference_timestamps()[event_uuid])

    def test_the_backfill_fills_them_from_the_event(self):
        left_behind = [self._write(1_762_200_000.0 + i)[2] for i in range(5)]

        repaired = mariadb._direct_backfill_event_object_timestamps()

        self.assertEqual(5, repaired)
        timestamps = self._reference_timestamps()
        for i, event_uuid in enumerate(left_behind):
            self.assertEqual(1_762_200_000.0 + i, timestamps[event_uuid])

    def test_rows_already_carrying_a_timestamp_are_not_touched(self):
        """The new code's own rows must not be rewritten.

        They are matched by nothing in the candidate SELECT, so a
        second pass is free; a WHERE which had lost its
        ``eo.timestamp = 0`` would rewrite the whole table every day.
        """
        _, _, written_properly = self._write(
            1_762_200_000.0, reference_timestamp=1_762_200_000.0)

        self.assertEqual(
            0, mariadb._direct_backfill_event_object_timestamps())
        self.assertEqual(
            1_762_200_000.0,
            self._reference_timestamps()[written_properly])

    def test_every_object_of_one_event_is_repaired(self):
        """An event names several objects, and each has its own row."""
        event_uuid = None
        with self.engine.connect() as conn:
            event_uuid = str(uuid4())
            conn.execute(sa.insert(self.events).values(
                event_uuid=event_uuid, event_type='audit',
                timestamp=1_762_200_500.0, fqdn='node.example.com',
                duration=None, message='live test event',
                extra=None, request_id=None))
            for object_type in ['instance', 'network', 'api-request']:
                conn.execute(sa.insert(self.event_objects).values(
                    object_type=object_type, object_uuid=str(uuid4()),
                    event_uuid=event_uuid))
            conn.commit()

        self.assertEqual(
            3, mariadb._direct_backfill_event_object_timestamps())

    def test_a_reference_with_no_event_is_left_for_the_prune(self):
        """The join cannot repair it, and it must not stall the sweep.

        A row whose events row has already been pruned has no timestamp
        to copy. Stage C is what removes it; the backfill has to ignore
        it rather than select it again on every batch.
        """
        _, _, orphan = self._write(1_762_200_000.0, with_event=False)

        self.assertEqual(
            0, mariadb._direct_backfill_event_object_timestamps())
        self.assertEqual(0.0, self._reference_timestamps()[orphan])

    def test_an_event_at_timestamp_zero_does_not_stall_the_sweep(self):
        """Unrepairable, because the value to copy is the default.

        Selecting it would mean selecting it again on every batch, so
        the sweep would spend its whole budget on one row and never
        reach the rows it can fix.
        """
        _, _, unrepairable = self._write(0.0)
        _, _, repairable = self._write(1_762_200_000.0)

        self.assertEqual(
            1, mariadb._direct_backfill_event_object_timestamps())
        timestamps = self._reference_timestamps()
        self.assertEqual(0.0, timestamps[unrepairable])
        self.assertEqual(1_762_200_000.0, timestamps[repairable])

    def test_the_repaired_rows_sort_with_the_rest(self):
        """The symptom the issue was filed for.

        The per-object read orders on the event_objects copy of the
        timestamp, so before the repair an event written during the
        upgrade window is the oldest the object has, whatever its
        actual time.
        """
        object_uuid = str(uuid4())
        for i in range(3):
            self._write(1_762_200_000.0 + i, object_uuid=object_uuid,
                        reference_timestamp=1_762_200_000.0 + i)
        self._write(1_762_200_100.0, object_uuid=object_uuid)

        before = mariadb._direct_get_object_events('instance', object_uuid)
        self.assertEqual(1_762_200_002.0, before[0].timestamp)
        # The newest event the object has, sorted last.
        self.assertEqual(1_762_200_100.0, before[-1].timestamp)

        mariadb._direct_backfill_event_object_timestamps()

        after = mariadb._direct_get_object_events('instance', object_uuid)
        self.assertEqual(1_762_200_100.0, after[0].timestamp)
        self.assertEqual(
            sorted([r.timestamp for r in after], reverse=True),
            [r.timestamp for r in after])
