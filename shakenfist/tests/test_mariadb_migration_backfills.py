# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Unit tests for batched schema-migration backfills (issue 4797).

``sf-ctl ensure-mariadb-schema`` runs before the nodes restart onto the
new code, so a backfill inside a migration rewrites a table every daemon
in the cluster is still using. A single table-wide ``UPDATE`` there is
one unbounded transaction which next-key locks every row it examines
until it commits -- the ``event_objects.timestamp`` backfill locked 71.9
million rows for 31 minutes and stalled event writes cluster-wide for
all of it.

Two things are tested here: that ``_migration_backfill_batched()`` walks
the table in bounded, individually committed batches, and that no
migration in ``mariadb.py`` issues a backfill which does not go through
it.
"""

import ast
import re
from unittest import mock

import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from shakenfist import mariadb
from shakenfist.tests import base


class _MockResult:
    """Minimal result stub supporting fetchall(), first() and rowcount."""

    def __init__(self, rowcount=0, rows=None, first=None):
        self.rowcount = rowcount
        self._rows = rows if rows is not None else []
        self._first = first

    def fetchall(self):
        return self._rows

    def first(self):
        # None reads as "no such column" to a migration's
        # information_schema probe; a mocked engine has none.
        return self._first


class _MockRow:
    def __init__(self, cursor_value):
        self.cursor_value = cursor_value


class _MockConnection:
    """Connection stub recording every statement, parameter set and commit."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []
        self.commits = 0

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _MockEngine:
    """Engine stub handing out one connection and counting connect() calls."""

    def __init__(self, connection):
        self._connection = connection
        self.connects = 0

    def connect(self):
        self.connects += 1
        return self._connection


def _rows(count, last='zzzz'):
    """``count`` candidate rows, the last of which carries ``last``."""
    return [_MockRow(f'aaaa{i:04d}') for i in range(count - 1)] + \
        [_MockRow(last)]


class MigrationBackfillBatchedTestCase(base.ShakenFistTestCase):
    """Tests for _migration_backfill_batched()."""

    def test_one_short_batch_reads_then_writes_then_stops(self):
        """A read which returns less than a full batch is the last one."""
        conn = _MockConnection([
            _MockResult(rows=_rows(3, last='cccc')),
            _MockResult(rowcount=3),
        ])
        engine = _MockEngine(conn)

        total = mariadb._migration_backfill_batched(
            engine, 'event_objects', 'event_uuid',
            'UPDATE event_objects SET x = 1 '
            'WHERE event_uuid >= :cursor_from AND event_uuid <= :cursor_to',
            'test backfill')

        self.assertEqual(3, total)
        self.assertEqual(2, len(conn.calls))

        select_sql, select_params = conn.calls[0]
        self.assertIn(
            'SELECT event_uuid AS cursor_value FROM event_objects',
            select_sql)
        self.assertIn('ORDER BY event_uuid LIMIT :batch', select_sql)
        # The first read has no lower bound to apply.
        self.assertNotIn('WHERE', select_sql)
        self.assertEqual(
            mariadb._MIGRATION_BACKFILL_BATCH_SIZE, select_params['batch'])

        # The write covers exactly the range the read handed back, so
        # its lock footprint is the batch and not the table.
        _, update_params = conn.calls[1]
        self.assertEqual(
            {'cursor_from': 'aaaa0000', 'cursor_to': 'cccc'}, update_params)

    def test_each_batch_is_its_own_committed_transaction(self):
        """A live writer waits for one batch at worst, not for the table."""
        batch = mariadb._MIGRATION_BACKFILL_BATCH_SIZE
        conn = _MockConnection([
            _MockResult(rows=_rows(batch, last='bbbb')),
            _MockResult(rowcount=batch),
            _MockResult(rows=_rows(batch, last='dddd')),
            _MockResult(rowcount=batch),
            _MockResult(rows=[]),
        ])
        engine = _MockEngine(conn)

        total = mariadb._migration_backfill_batched(
            engine, 'event_objects', 'event_uuid',
            'UPDATE event_objects SET x = 1 '
            'WHERE event_uuid >= :cursor_from AND event_uuid <= :cursor_to',
            'test backfill')

        self.assertEqual(2 * batch, total)
        self.assertEqual(2, conn.commits)

        # Reads are not committed and writes are, so one commit per
        # write and none held open across batches.
        self.assertEqual(
            [{'cursor_from': 'aaaa0000', 'cursor_to': 'bbbb'},
             {'cursor_from': 'aaaa0000', 'cursor_to': 'dddd'}],
            [params for sql, params in conn.calls
             if sql.startswith('UPDATE')])

    def test_later_batches_start_where_the_last_one_ended(self):
        """The walk is a cursor, not a repeated candidate query.

        A query which asks "which rows still need work?" has to scan
        past every row already done to find the next batch, which is
        quadratic in the size of the table. 36.5 million rows at a
        batch of 10,000 is 3,650 batches, so the difference is not
        academic.
        """
        batch = mariadb._MIGRATION_BACKFILL_BATCH_SIZE
        conn = _MockConnection([
            _MockResult(rows=_rows(batch, last='bbbb')),
            _MockResult(rowcount=batch),
            _MockResult(rows=[]),
        ])
        engine = _MockEngine(conn)

        mariadb._migration_backfill_batched(
            engine, 'event_objects', 'event_uuid',
            'UPDATE event_objects SET x = 1 '
            'WHERE event_uuid >= :cursor_from AND event_uuid <= :cursor_to',
            'test backfill')

        second_sql, second_params = conn.calls[2]
        self.assertIn('WHERE event_uuid > :cursor', second_sql)
        self.assertEqual('bbbb', second_params['cursor'])

    def test_the_write_does_not_inherit_the_reads_transaction(self):
        """Each statement gets its own connection, so its own read view.

        Under innodb_snapshot_isolation an UPDATE whose read view was
        opened by an earlier plain SELECT in the same transaction aborts
        with ER_CHECKREAD rather than re-evaluating (see standards.md).
        """
        conn = _MockConnection([
            _MockResult(rows=_rows(2, last='bbbb')),
            _MockResult(rowcount=2),
        ])
        engine = _MockEngine(conn)

        mariadb._migration_backfill_batched(
            engine, 'event_objects', 'event_uuid',
            'UPDATE event_objects SET x = 1 '
            'WHERE event_uuid >= :cursor_from AND event_uuid <= :cursor_to',
            'test backfill')

        self.assertEqual(2, engine.connects)

    def test_a_repeated_cursor_value_is_neither_skipped_nor_redone(self):
        """event_objects.event_uuid is not unique: one event, many objects.

        So a batch's last value can have rows the row limit cut off. The
        write bounds on the value inclusively, which covers them, and
        the next read starts strictly above it, which does not revisit
        them.
        """
        batch = mariadb._MIGRATION_BACKFILL_BATCH_SIZE
        conn = _MockConnection([
            # The last two rows of the batch share a cursor value, and
            # a third row with that value sits past the limit.
            _MockResult(rows=_rows(batch - 1, last='bbbb') +
                        [_MockRow('bbbb')]),
            _MockResult(rowcount=batch + 1),
            _MockResult(rows=[]),
        ])
        engine = _MockEngine(conn)

        total = mariadb._migration_backfill_batched(
            engine, 'event_objects', 'event_uuid',
            'UPDATE event_objects SET x = 1 '
            'WHERE event_uuid >= :cursor_from AND event_uuid <= :cursor_to',
            'test backfill')

        # The write reported more rows than the read returned, which is
        # the row past the limit being swept up by the inclusive bound.
        self.assertEqual(batch + 1, total)
        self.assertEqual('bbbb', conn.calls[1][1]['cursor_to'])
        self.assertEqual('bbbb', conn.calls[2][1]['cursor'])
        self.assertIn('WHERE event_uuid > :cursor', conn.calls[2][0])

    def test_an_empty_table_writes_nothing(self):
        conn = _MockConnection([_MockResult(rows=[])])
        engine = _MockEngine(conn)

        total = mariadb._migration_backfill_batched(
            engine, 'event_objects', 'event_uuid',
            'UPDATE event_objects SET x = 1 '
            'WHERE event_uuid >= :cursor_from AND event_uuid <= :cursor_to',
            'test backfill')

        self.assertEqual(0, total)
        self.assertEqual(1, len(conn.calls))
        self.assertEqual(0, conn.commits)

    def test_a_failed_batch_aborts_the_backfill(self):
        """A schema version must not advance over a half-filled column.

        The caller advances it only if this returns, so the error has to
        propagate rather than be logged and counted as success.
        """
        conn = _MockConnection([
            _MockResult(rows=_rows(2, last='bbbb')),
            OperationalError('UPDATE', {}, Exception('lock wait timeout')),
        ])
        engine = _MockEngine(conn)

        self.assertRaises(
            OperationalError,
            mariadb._migration_backfill_batched,
            engine, 'event_objects', 'event_uuid',
            'UPDATE event_objects SET x = 1 '
            'WHERE event_uuid >= :cursor_from AND event_uuid <= :cursor_to',
            'test backfill')


# ---------------------------------------------------------------------------
# The class guard
# ---------------------------------------------------------------------------

# Functions whose names match these prefixes run from
# sf-ctl ensure-mariadb-schema, which the deployer runs before the nodes
# restart onto the new code.
_MIGRATION_PREFIXES = ('_ensure_', '_migrate_')

# An UPDATE at the start of a statement, rather than the words "ON
# DUPLICATE KEY UPDATE" in a docstring.
_UPDATE_RE = re.compile(r'^\s*UPDATE\s', re.MULTILINE)


def _joined_text(node):
    """An f-string's text, with each interpolation reduced to a ``?``.

    Python parses ``f'UPDATE {table} ' 'SET a = 1'`` as one JoinedStr, so
    reading the pieces back out is how a statement assembled from an
    f-string and its continuations is seen whole. Reading the source
    segment instead would leave the ``f'`` prefix in front of the SQL,
    which is how the first version of this guard came to pass
    vacuously.
    """
    return ''.join(
        value.value if isinstance(value, ast.Constant) else '?'
        for value in node.values)


def _sql_literals(func):
    """Every string literal in ``func``, f-strings included, bar docstrings."""
    docstring = ast.get_docstring(func, clean=False)
    joined = [node for node in ast.walk(func)
              if isinstance(node, ast.JoinedStr)]
    inside_joined = {id(child) for node in joined
                     for child in ast.walk(node) if child is not node}

    literals = [_joined_text(node) for node in joined]
    for node in ast.walk(func):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in inside_joined
                and node.value != docstring):
            literals.append(node.value)
    return literals


class NoUnboundedMigrationBackfillTestCase(base.ShakenFistTestCase):
    """No migration may rewrite a live table in one transaction.

    Issue 4797: ``sf-ctl ensure-mariadb-schema`` runs while every daemon
    in the cluster is still serving, so an unbounded ``UPDATE`` in a
    migration locks the rows those daemons need. The one which shipped
    held 71.9 million row locks for 31 minutes. A new migration which
    does the same thing would not fail any existing test, so this reads
    the source.
    """

    def test_every_migration_update_is_bounded_by_a_cursor_range(self):
        source = open(mariadb.__file__).read()
        tree = ast.parse(source)

        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith(_MIGRATION_PREFIXES):
                continue
            for literal in _sql_literals(node):
                if (_UPDATE_RE.search(literal)
                        and ':cursor_to' not in literal):
                    offenders.append(f'{node.name} (line {node.lineno})')

        self.assertEqual(
            [], offenders,
            'A migration backfill must run through '
            '_migration_backfill_batched(), which bounds each '
            'transaction to a cursor range rather than locking the '
            'whole table while the cluster is serving. See '
            '"A schema backfill runs in bounded batches" in '
            f'docs/developer_guide/coding_rules.md. Offenders: {offenders}')

    def test_the_guard_sees_an_unbounded_update(self):
        """The guard above passes vacuously if the matcher is wrong.

        The f-string case is the one that matters: every backfill in
        mariadb.py interpolates its table name, and an earlier version
        of this guard read the literal's source segment and so never
        matched an UPDATE behind an ``f'`` prefix.
        """
        tree = ast.parse(
            'def _ensure_thing_schema(engine):\n'
            '    """Uses INSERT ... ON DUPLICATE KEY UPDATE for upsert."""\n'
            '    conn.execute(sa.text("UPDATE thing SET a = 1"))\n'
            '    conn.execute(sa.text(\n'
            '        f"UPDATE {table} eo "\n'
            '        "SET eo.a = 1"))\n')
        func = tree.body[0]
        literals = _sql_literals(func)

        self.assertEqual(
            ['UPDATE ? eo SET eo.a = 1', 'UPDATE thing SET a = 1'],
            sorted(lit for lit in literals if _UPDATE_RE.search(lit)))


class EventObjectsBackfillIsBatchedTestCase(base.ShakenFistTestCase):
    """The v1 to v2 event_objects migration is the one issue 4797 measured."""

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=1)
    def test_the_backfill_is_handed_to_the_batched_helper(
            self, mock_get_version, mock_set_version):
        conn = _MockConnection([_MockResult(), _MockResult(), _MockResult()])
        engine = _MockEngine(conn)

        with mock.patch(
                'shakenfist.mariadb._migration_backfill_batched') as batched:
            mariadb._ensure_event_objects_schema(engine)

        batched.assert_called_once()
        args = batched.call_args[0]
        self.assertEqual(engine, args[0])
        self.assertEqual('event_objects', args[1])
        # event_uuid is both the join key and an indexed column, so the
        # walk and the join read the same index.
        self.assertEqual('event_uuid', args[2])
        self.assertIn('SET eo.timestamp = e.timestamp', args[3])
        self.assertIn('WHERE eo.timestamp = 0', args[3])
        self.assertIn('eo.event_uuid >= :cursor_from', args[3])
        self.assertIn('eo.event_uuid <= :cursor_to', args[3])

        # The column has to exist before the backfill, and the index is
        # built afterwards from finished data.
        self.assertIn('ADD COLUMN timestamp', conn.calls[0][0])
        self.assertIn('idx_event_objects_object_timestamp', conn.calls[1][0])
        # v1 -> v2 (the backfill), then v2 -> v3 (the key width).
        mock_set_version.assert_any_call(engine, 'event_objects', 2)
        mock_set_version.assert_any_call(engine, 'event_objects', 3)

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=1)
    def test_a_failed_backfill_does_not_advance_the_version(
            self, mock_get_version, mock_set_version):
        engine = _MockEngine(_MockConnection([_MockResult()]))

        with mock.patch('shakenfist.mariadb._migration_backfill_batched',
                        side_effect=OperationalError(
                            'UPDATE', {}, Exception('lock wait timeout'))):
            self.assertRaises(
                OperationalError,
                mariadb._ensure_event_objects_schema, engine)

        mock_set_version.assert_not_called()


class NetworkInterfacesBackfillIsBatchedTestCase(base.ShakenFistTestCase):
    """The v2 to v3 network_interfaces migration reads object_states too.

    Unbounded, it takes shared next-key locks across the table every
    daemon's state transitions go through, so it is the worse of the two
    backfills even though network_interfaces is the smaller table.
    """

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_the_backfill_is_handed_to_the_batched_helper(
            self, mock_get_version, mock_set_version):
        conn = _MockConnection([_MockResult() for _ in range(4)])
        engine = _MockEngine(conn)

        with mock.patch(
                'shakenfist.mariadb._migration_backfill_batched') as batched:
            mariadb._ensure_network_interfaces_schema(engine)

        batched.assert_called_once()
        args = batched.call_args[0]
        self.assertEqual('network_interfaces', args[1])
        self.assertEqual('uuid', args[2])
        self.assertIn('LEFT JOIN object_states s', args[3])
        self.assertIn('ni.uuid >= :cursor_from', args[3])
        self.assertIn('ni.uuid <= :cursor_to', args[3])

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_a_failed_backfill_still_lands_the_constraint(
            self, mock_get_version, mock_set_version):
        """Deliberately unchanged: this one warns and carries on.

        The new UNIQUE constraint is correct whether or not ``active``
        was filled -- NULLs do not collide -- so the pre-existing
        behaviour of logging the failure and continuing is kept rather
        than turned into an aborted migration.
        """
        conn = _MockConnection([_MockResult() for _ in range(4)])
        engine = _MockEngine(conn)

        with mock.patch('shakenfist.mariadb._migration_backfill_batched',
                        side_effect=OperationalError(
                            'UPDATE', {}, Exception('lock wait timeout'))):
            result = mariadb._ensure_network_interfaces_schema(engine)

        self.assertEqual(3, result['end_version'])
        self.assertIn(
            'uq_network_interfaces_macaddr_active_network',
            ' '.join(sql for sql, _ in conn.calls))


class MigrationBackfillBatchSizeTestCase(base.ShakenFistTestCase):
    """The batch is the lock footprint, so its size is not incidental."""

    def test_the_batch_size_is_the_measured_sweep_size(self):
        self.assertEqual(
            mariadb._PRUNE_BATCH_SIZE,
            mariadb._MIGRATION_BACKFILL_BATCH_SIZE)

    def test_sqlalchemy_accepts_the_generated_candidate_read(self):
        """The generated SQL has to be a statement, not just a string."""
        stmt = sa.text(
            'SELECT event_uuid AS cursor_value FROM event_objects '
            'WHERE event_uuid > :cursor ORDER BY event_uuid LIMIT :batch')
        self.assertEqual(
            {'cursor', 'batch'},
            set(stmt.compile().params.keys()))
