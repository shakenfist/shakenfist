# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Tests for the shape hold booking primitive.

Covers the SQL layer of _direct_book_claim_holds(), the hold read, the
claim-side sum invariant where the claim create also asserts it, the
gRPC wrappers and the NamespaceClaim method that chooses the nodes --
against mocked engines in the same style as
test_mariadb_capacity_admission.py: statement shapes are verified by
compiling what was executed, not by running it.

Two things in here cannot be tested this way and are tested in
test_mariadb_capacity_holds_live.py instead: that the guards actually
bind against real rows, and that two concurrent bookings of one claim
cannot both pass the sum invariant. The second is entirely a property
of InnoDB re-evaluating a guarded UPDATE's WHERE -- including the
subquery in it -- against the rows as committed by whoever held the
lock first, and no mock can say anything about it.
"""

from types import SimpleNamespace
from unittest import mock
from uuid import UUID

import grpc
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from shakenfist.daemons.database import main as database_main
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist import namespace_claim
from shakenfist.protos import database_pb2
from shakenfist.tests import base


MYSQL_DIALECT = sa.dialects.mysql.dialect()

CLAIM1 = UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
NODE1 = UUID('11111111-1111-1111-1111-111111111111')
NODE2 = UUID('22222222-2222-2222-2222-222222222222')
HOLD1 = UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
INST1 = UUID('eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee')


def _compiled(stmt):
    """Compile an executed statement for shape assertions."""
    compiled = stmt.compile(dialect=MYSQL_DIALECT)
    return str(compiled).strip(), compiled.params


def _params(stmt):
    return stmt.compile(dialect=MYSQL_DIALECT).params


def _claim_row(uuid=CLAIM1, namespace='ci-1', limit_cpus=16,
               limit_memory_mb=16384, limit_disk_gb=200, used_cpus=4,
               used_memory_mb=4096, used_disk_gb=40, state='active'):
    """A row in the shape _claim_select() projects."""
    return SimpleNamespace(
        uuid=uuid, namespace=namespace, limit_cpus=limit_cpus,
        limit_memory_mb=limit_memory_mb, limit_disk_gb=limit_disk_gb,
        used_cpus=used_cpus, used_memory_mb=used_memory_mb,
        used_disk_gb=used_disk_gb, peak_used_cpus=used_cpus,
        peak_used_memory_mb=used_memory_mb, peak_used_disk_gb=used_disk_gb,
        state=state, expires_at=1800000000.0, updated_at=1799990000.0)


def _capacity_row(node_uuid=NODE1, limit_cpus=16, limit_memory_mb=16384,
                  limit_disk_gb=200, used_cpus=2, used_memory_mb=2048,
                  used_disk_gb=20, held_cpus=4, held_memory_mb=4096,
                  held_disk_gb=40):
    return SimpleNamespace(
        node_uuid=node_uuid, limit_cpus=limit_cpus,
        limit_memory_mb=limit_memory_mb, limit_disk_gb=limit_disk_gb,
        used_cpus=used_cpus, used_memory_mb=used_memory_mb,
        used_disk_gb=used_disk_gb, expected_demand=0.0,
        held_cpus=held_cpus, held_memory_mb=held_memory_mb,
        held_disk_gb=held_disk_gb, updated_at=None)


def _hold_row(uuid=HOLD1, claim_uuid=CLAIM1, node_uuid=NODE1,
              instance_uuid=None, cpus=4, memory_mb=4096, disk_gb=40):
    """A row in the shape _hold_select() projects."""
    return SimpleNamespace(
        uuid=uuid, claim_uuid=claim_uuid, namespace='ci-1', cpus=cpus,
        memory_mb=memory_mb, disk_gb=disk_gb, node_uuid=node_uuid,
        instance_uuid=instance_uuid, created_at=1799000000.0,
        updated_at=1799000000.0)


def _hold_sums(cpus=4, memory_mb=4096, disk_gb=40):
    """The row the claim-stage denial detail's aggregate read returns."""
    return SimpleNamespace(cpus=cpus, memory_mb=memory_mb, disk_gb=disk_gb)


class _HoldRouter:
    """Routes a mocked connection's execute() to canned rows and rowcounts.

    A booking probes the claim on its own connection and then issues a
    fixed sequence of guarded writes inside a transaction, so statements
    are recorded separately per connection kind. That is what lets the
    tests assert the ER_CHECKREAD invariant directly: nothing inside the
    transaction may read before the first guarded UPDATE.
    """

    def __init__(self, claim=_claim_row(), node_row=_capacity_row(),
                 hold_rows=None, hold_sums=None, cluster_row=None,
                 rowcounts=None):
        self.claim = claim
        self.node_row = node_row
        # Only the claim create reaches cluster_capacity; a booking
        # never touches it, and a router given no row says so.
        self.cluster_row = cluster_row
        self.hold_rows = hold_rows
        self.hold_sums = hold_sums if hold_sums is not None else _hold_sums()
        self.context = 'autocommit'
        self.transactional = []
        self.autocommit = []
        self.rowcounts = {
            'claim_update': 1,
            'node_update': 1,
            'hold_insert': 1,
            'cluster_update': 1,
        }
        self.rowcounts.update(rowcounts or {})
        self.executed = []

    def _result(self, rows=None, rowcount=0):
        result = mock.MagicMock()
        result.fetchall.return_value = rows or []
        result.first.return_value = rows[0] if rows else None
        result.rowcount = rowcount
        return result

    def on(self, context):
        """A connection execute() side effect tagged with its context."""
        def _execute(stmt, *args, **kwargs):
            self.context = context
            return self(stmt, *args, **kwargs)
        return _execute

    def __call__(self, stmt, *args, **kwargs):
        text, _ = _compiled(stmt)
        self.executed.append((text, stmt))
        if self.context == 'transaction':
            self.transactional.append(text)
        else:
            self.autocommit.append(text)

        if text.startswith('SELECT'):
            if 'FROM namespace_claim_holds' in text:
                # The denial detail's aggregate read has no uuid column
                # to project, which is what tells it from a row read.
                if 'sum(' in text:
                    return self._result(
                        rows=[self.hold_sums] if self.hold_sums else [])
                rows = (self.hold_rows if self.hold_rows is not None
                        else [_hold_row()])
                return self._result(rows=rows)
            if 'FROM namespace_claims' in text:
                return self._result(rows=[self.claim] if self.claim else [])
            if 'FROM scheduler_node_capacity' in text:
                if self.node_row is None:
                    return self._result()
                return self._result(rows=[self.node_row])
            if 'FROM cluster_capacity' in text:
                if self.cluster_row is None:
                    return self._result()
                return self._result(rows=[self.cluster_row])
            return self._result()

        if text.startswith('UPDATE cluster_capacity'):
            return self._result(rowcount=self.rowcounts['cluster_update'])
        if text.startswith('UPDATE namespace_claims'):
            return self._result(rowcount=self.rowcounts['claim_update'])
        if text.startswith('UPDATE scheduler_node_capacity'):
            return self._result(rowcount=self.rowcounts['node_update'])
        if text.startswith('INSERT INTO namespace_claim_holds'):
            return self._result(rowcount=self.rowcounts['hold_insert'])
        return self._result()

    def statements(self, prefix):
        return [text for text, _ in self.executed if text.startswith(prefix)]

    def of(self, prefix):
        """Every executed statement object whose text starts with prefix."""
        return [stmt for text, stmt in self.executed
                if text.startswith(prefix)]


class _HoldMixin:
    """Runs a booking against a routed mock engine."""

    def _run(self, router, **kwargs):
        engine = mock.MagicMock()
        transaction = mock.MagicMock()
        transaction.execute.side_effect = router.on('transaction')
        autocommit = mock.MagicMock()
        autocommit.execute.side_effect = router.on('autocommit')
        engine.begin.return_value.__enter__.return_value = transaction
        engine.connect.return_value.__enter__.return_value = autocommit
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            return self._call(**kwargs)


class HoldSumGuardTestCase(base.ShakenFistTestCase):
    """The one rendering of the claim-side invariant."""

    def _where(self, requested=0):
        stmt = sa.update(mariadb._get_namespace_claims_table()).where(
            mariadb._claim_hold_sum_guard('cpus', CLAIM1, requested)).values(
                updated_at=sa.func.now())
        text, _ = _compiled(stmt)
        return ' '.join(text.split('WHERE', 1)[1].split())

    def test_the_sum_rides_inside_the_statement_as_a_subquery(self):
        # The whole point: a SELECT SUM(...) in front of the guarded
        # UPDATE re-introduces ER_CHECKREAD under
        # innodb_snapshot_isolation, and a subquery inside the DML does
        # not. The form is the defence, so the form is asserted.
        where = self._where()
        self.assertIn('(SELECT sum(namespace_claim_holds.cpus)', where)
        self.assertIn('FROM namespace_claim_holds', where)
        self.assertIn('WHERE namespace_claim_holds.claim_uuid = %s', where)
        self.assertIn('namespace_claims.limit_cpus >=', where)

    def test_the_empty_sum_is_coalesced_to_zero(self):
        # Load bearing rather than defensive: SUM over no rows is NULL
        # and x >= NULL is not true, so without this the first booking
        # against a claim, and every claim create, fails its own guard.
        self.assertIn('coalesce((SELECT sum(namespace_claim_holds.cpus)',
                      self._where())

    def test_the_request_is_added_to_the_booked_sum(self):
        stmt = sa.update(mariadb._get_namespace_claims_table()).where(
            mariadb._claim_hold_sum_guard('cpus', CLAIM1, 7)).values(
                updated_at=sa.func.now())
        _, params = _compiled(stmt)
        self.assertIn(7, params.values())
        self.assertIn(') + %s', self._where(requested=7))

    def test_every_dimension_is_guarded_the_same_way(self):
        for dimension in mariadb.CAPACITY_DIMENSIONS:
            stmt = sa.update(mariadb._get_namespace_claims_table()).where(
                mariadb._claim_hold_sum_guard(dimension, CLAIM1)).values(
                    updated_at=sa.func.now())
            text, _ = _compiled(stmt)
            self.assertIn(
                f'coalesce((SELECT sum(namespace_claim_holds.{dimension})',
                ' '.join(text.split()))


class BookClaimHoldsTestCase(_HoldMixin, base.ShakenFistTestCase):
    """The guarded booking transaction."""

    def _call(self, **kwargs):
        args = {
            'claim_uuid': str(CLAIM1),
            'shapes': [{'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40,
                        'node_uuid': str(NODE1)}],
        }
        args.update(kwargs)
        return mariadb._direct_book_claim_holds(**args)

    def test_a_booking_writes_one_row_per_shape_and_charges_each_node(self):
        router = _HoldRouter()
        result = self._run(router, shapes=[
            {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40,
             'node_uuid': str(NODE1)},
            {'cpus': 2, 'memory_mb': 2048, 'disk_gb': 20,
             'node_uuid': str(NODE2)},
        ])

        self.assertTrue(result['booked'], result['error'])
        [insert] = router.of('INSERT INTO namespace_claim_holds')
        params = _params(insert)
        self.assertEqual(4, params['cpus_m0'])
        self.assertEqual(2, params['cpus_m1'])
        self.assertEqual(NODE1, params['node_uuid_m0'])
        self.assertEqual(NODE2, params['node_uuid_m1'])
        # Exactly the two nodes named, one statement each.
        charged = [_params(stmt)['node_uuid_1']
                   for stmt in router.of('UPDATE scheduler_node_capacity')]
        self.assertEqual([NODE1, NODE2], charged)

    def test_the_tables_are_taken_in_the_canonical_order(self):
        # cluster_capacity is not touched -- a hold lives inside its
        # claim's limits, which are already inside claimed_*, so there
        # is no cluster-side counter to move -- and the hold rows go
        # last, after the claim and the node they are booked against.
        router = _HoldRouter()
        self.assertTrue(self._run(router)['booked'])
        self.assertEqual([], router.statements('UPDATE cluster_capacity'))
        order = [text.split(' ')[0] + ' ' + text.split(' ')[1] + ' '
                 + text.split(' ')[2]
                 for text in router.transactional]
        self.assertEqual('UPDATE namespace_claims SET', order[0])
        self.assertTrue(order[1].startswith('UPDATE scheduler_node_capacity'))
        self.assertTrue(order[2].startswith('INSERT INTO'))
        self.assertIn('namespace_claim_holds', router.transactional[2])

    def test_nothing_reads_inside_the_transaction_before_the_guard(self):
        # The ER_CHECKREAD invariant, asserted rather than commented:
        # the first statement on the transactional connection has to be
        # the guarded UPDATE, and the claim's hold sum rides inside it.
        router = _HoldRouter()
        self.assertTrue(self._run(router)['booked'])
        self.assertTrue(router.transactional[0].startswith(
            'UPDATE namespace_claims'))
        self.assertIn('coalesce((SELECT sum(namespace_claim_holds.cpus)',
                      ' '.join(router.transactional[0].split()))
        reads_before = [text for text in router.transactional[:1]
                        if text.startswith('SELECT')]
        self.assertEqual([], reads_before)

    def test_the_claim_probe_runs_outside_the_transaction(self):
        router = _HoldRouter()
        self.assertTrue(self._run(router)['booked'])
        self.assertTrue(any(text.startswith('SELECT')
                            and 'FROM namespace_claims' in text
                            for text in router.autocommit))

    def test_the_claim_statement_tests_but_does_not_charge_the_claim(self):
        # Held room is not counted on the claim row: used_* there is
        # instance usage, and a second copy of the booking on the claim
        # would be a ledger that agrees with the hold rows only until
        # the next write.
        router = _HoldRouter()
        self.assertTrue(self._run(router)['booked'])
        [update] = router.of('UPDATE namespace_claims')
        text, _ = _compiled(update)
        sets = text.split('SET', 1)[1].split('WHERE', 1)[0]
        self.assertNotIn('used_', sets)
        self.assertNotIn('held_', sets)
        self.assertIn('updated_at=now()', ' '.join(sets.split()))

    def test_the_node_guard_tests_used_plus_held_plus_request(self):
        router = _HoldRouter()
        self.assertTrue(self._run(router)['booked'])
        [update] = router.of('UPDATE scheduler_node_capacity')
        text, _ = _compiled(update)
        where = ' '.join(text.split('WHERE', 1)[1].split())
        for dimension in mariadb.CAPACITY_DIMENSIONS:
            self.assertIn(
                f'scheduler_node_capacity.used_{dimension} + '
                f'scheduler_node_capacity.held_{dimension} + %s <= '
                f'scheduler_node_capacity.limit_{dimension}', where)

    def test_the_node_statement_moves_held_and_nothing_else(self):
        router = _HoldRouter()
        self.assertTrue(self._run(router)['booked'])
        [update] = router.of('UPDATE scheduler_node_capacity')
        text, _ = _compiled(update)
        sets = ' '.join(text.split('SET', 1)[1].split('WHERE', 1)[0].split())
        for dimension in mariadb.CAPACITY_DIMENSIONS:
            self.assertIn(
                f'held_{dimension}=(scheduler_node_capacity.'
                f'held_{dimension} + %s)', sets)
            self.assertNotIn(f'used_{dimension}=', sets)
        self.assertNotIn('expected_demand=', sets)

    def test_the_node_rows_are_taken_in_uuid_order(self):
        # Two statements against one table, so a fixed intra-table order
        # is what stops two bookings touching the same pair of nodes in
        # opposite orders from deadlocking instead of queueing. The
        # caller's order is deliberately not honoured here.
        router = _HoldRouter()
        self.assertTrue(self._run(router, shapes=[
            {'cpus': 1, 'memory_mb': 1, 'disk_gb': 1,
             'node_uuid': str(NODE2)},
            {'cpus': 1, 'memory_mb': 1, 'disk_gb': 1,
             'node_uuid': str(NODE1)},
        ])['booked'])
        charged = [_params(stmt)['node_uuid_1']
                   for stmt in router.of('UPDATE scheduler_node_capacity')]
        self.assertEqual([NODE1, NODE2], charged)

    def test_two_shapes_on_one_node_are_charged_together(self):
        # One guarded statement for the node, asking for the sum: a pair
        # that fits one at a time and not together has to be refused,
        # which it cannot be if each shape is tested on its own.
        router = _HoldRouter()
        self.assertTrue(self._run(router, shapes=[
            {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40,
             'node_uuid': str(NODE1)},
            {'cpus': 2, 'memory_mb': 2048, 'disk_gb': 20,
             'node_uuid': str(NODE1)},
        ])['booked'])
        [update] = router.of('UPDATE scheduler_node_capacity')
        params = _params(update)
        self.assertEqual(6, params['held_cpus_1'])
        self.assertEqual(6144, params['held_memory_mb_1'])
        self.assertEqual(60, params['held_disk_gb_1'])
        self.assertEqual(6, params['param_1'])
        # And still two hold rows: the holds are the ground truth and
        # two bookings of one shape are two holds.
        [insert] = router.of('INSERT INTO namespace_claim_holds')
        self.assertEqual(2, _params(insert)['cpus_m1'])

    def test_the_claim_guard_charges_the_whole_booking(self):
        router = _HoldRouter()
        self.assertTrue(self._run(router, shapes=[
            {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40,
             'node_uuid': str(NODE1)},
            {'cpus': 2, 'memory_mb': 2048, 'disk_gb': 20,
             'node_uuid': str(NODE2)},
        ])['booked'])
        [update] = router.of('UPDATE namespace_claims')
        self.assertIn(6, _params(update).values())
        self.assertIn(6144, _params(update).values())

    def test_the_hold_rows_start_unconsumed(self):
        router = _HoldRouter()
        self.assertTrue(self._run(router)['booked'])
        [insert] = router.of('INSERT INTO namespace_claim_holds')
        self.assertIsNone(_params(insert)['instance_uuid_m0'])

    def test_the_hold_rows_carry_the_claims_namespace(self):
        router = _HoldRouter(claim=_claim_row(namespace='ci-7'))
        self.assertTrue(self._run(router)['booked'])
        [insert] = router.of('INSERT INTO namespace_claim_holds')
        self.assertEqual('ci-7', _params(insert)['namespace_m0'])

    def test_the_timestamps_come_from_the_server(self):
        router = _HoldRouter()
        self.assertTrue(self._run(router)['booked'])
        [insert] = router.of('INSERT INTO namespace_claim_holds')
        text, _ = _compiled(insert)
        self.assertIn('now(), now())', text)

    def test_a_refused_claim_guard_writes_nothing(self):
        # All or nothing: the node is never touched and no hold row is
        # written, because the whole transaction rolls back.
        router = _HoldRouter(rowcounts={'claim_update': 0})
        result = self._run(router)

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['booked'])
        self.assertEqual('claim_capacity', result['refused_reason'])
        self.assertEqual([], result['holds'])
        self.assertEqual([], router.statements('UPDATE '
                                               'scheduler_node_capacity'))
        self.assertEqual([], router.statements('INSERT'))

    def test_a_refused_claim_guard_names_the_dimensions_that_failed(self):
        router = _HoldRouter(
            claim=_claim_row(limit_cpus=16),
            hold_sums=_hold_sums(cpus=14, memory_mb=1024, disk_gb=10),
            rowcounts={'claim_update': 0})
        result = self._run(router, shapes=[
            {'cpus': 4, 'memory_mb': 1024, 'disk_gb': 10,
             'node_uuid': str(NODE1)}])

        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(16.0, detail['cpus']['limit'])
        self.assertEqual(14.0, detail['cpus']['used'])
        self.assertEqual(4.0, detail['cpus']['requested'])
        self.assertTrue(detail['cpus']['exceeded'])
        self.assertEqual(2.0, detail['cpus']['shortfall'])
        self.assertFalse(detail['memory_mb']['exceeded'])

    def test_a_refused_node_guard_writes_no_hold_row(self):
        router = _HoldRouter(rowcounts={'node_update': 0})
        result = self._run(router, shapes=[
            {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40,
             'node_uuid': str(NODE1)},
            {'cpus': 2, 'memory_mb': 2048, 'disk_gb': 20,
             'node_uuid': str(NODE2)},
        ])

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['booked'])
        self.assertEqual('node_capacity', result['refused_reason'])
        self.assertEqual([], result['holds'])
        self.assertEqual([], router.statements('INSERT'))
        # And it stopped at the first node rather than charging the rest.
        self.assertEqual(
            1, len(router.statements('UPDATE scheduler_node_capacity')))

    def test_a_node_denial_reports_used_plus_held_as_used(self):
        # The guard tested used + held, so the detail has to say so:
        # reporting used_* alone would describe a refusal that did not
        # happen and read as a defect.
        router = _HoldRouter(
            node_row=_capacity_row(limit_cpus=16, used_cpus=6, held_cpus=8),
            rowcounts={'node_update': 0})
        result = self._run(router, shapes=[
            {'cpus': 4, 'memory_mb': 1024, 'disk_gb': 10,
             'node_uuid': str(NODE1)}])

        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(14.0, detail['cpus']['used'])
        self.assertEqual(4.0, detail['cpus']['requested'])
        self.assertTrue(detail['cpus']['exceeded'])

    def test_a_node_with_no_capacity_row_refuses_rather_than_failing_open(self):
        # Deliberately the opposite of what an instance admission does
        # with the same missing row. Admitting an instance unguarded
        # records where a machine already is; granting a booking against
        # a row nothing has sized promises room the cluster may have no
        # way to keep.
        router = _HoldRouter(node_row=None, rowcounts={'node_update': 0})
        result = self._run(router)

        self.assertFalse(result['booked'])
        self.assertEqual('node_capacity', result['refused_reason'])
        # Nothing to read back, so no detail is invented for it.
        self.assertEqual([], result['dimensions'])
        self.assertEqual([], router.statements('INSERT'))

    def test_a_claim_that_is_gone_refuses_as_not_found(self):
        router = _HoldRouter(claim=None)
        result = self._run(router)

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['booked'])
        self.assertEqual('not_found', result['refused_reason'])
        self.assertEqual([], router.transactional)

    def test_an_expired_claim_refuses_as_not_active(self):
        # An expired claim's limits are not in claimed_*, so there is no
        # cluster-side aggregate for its bookings to sit inside.
        router = _HoldRouter(claim=_claim_row(state='expired'))
        result = self._run(router)

        self.assertFalse(result['booked'])
        self.assertEqual('not_active', result['refused_reason'])
        self.assertEqual([], router.transactional)

    def test_the_claim_guard_also_tests_coverage_in_sql(self):
        # The probe is time-of-check-to-time-of-use racy, so the
        # statement carries the coverage test as well: a claim that
        # lapses between the probe and the transaction is still refused.
        router = _HoldRouter()
        self.assertTrue(self._run(router)['booked'])
        [update] = router.of('UPDATE namespace_claims')
        text, _ = _compiled(update)
        self.assertIn('namespace_claims.state = %s', text)
        self.assertIn('active', _params(update).values())

    def test_an_empty_booking_is_an_error_not_a_success(self):
        # Refused for the reason find_candidates_for_shapes() refuses an
        # empty set: a caller asking whether a booking fits would read
        # success as "all of it does".
        router = _HoldRouter()
        result = self._run(router, shapes=[])

        self.assertFalse(result['success'])
        self.assertIn('at least one shape', result['error'])
        self.assertEqual([], router.executed)

    def test_a_shape_with_no_node_is_an_error(self):
        # Choosing nodes belongs to the scheduler, above this tier, so a
        # shape without one is a caller defect rather than a capacity
        # decision this function could make for itself.
        router = _HoldRouter()
        result = self._run(router, shapes=[
            {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40}])

        self.assertFalse(result['success'])
        self.assertIn('names no node', result['error'])
        self.assertEqual([], router.executed)

    def test_a_malformed_node_is_an_error(self):
        router = _HoldRouter()
        result = self._run(router, shapes=[
            {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40,
             'node_uuid': 'not-a-uuid'}])

        self.assertFalse(result['success'])
        self.assertIn('malformed node', result['error'])

    def test_a_malformed_claim_uuid_is_an_error(self):
        router = _HoldRouter()
        result = self._run(router, claim_uuid='not-a-uuid')

        self.assertFalse(result['success'])
        self.assertIn('malformed uuid', result['error'])
        self.assertEqual([], router.executed)

    def test_a_negative_shape_is_an_error(self):
        router = _HoldRouter()
        result = self._run(router, shapes=[
            {'cpus': -1, 'memory_mb': 4096, 'disk_gb': 40,
             'node_uuid': str(NODE1)}])

        self.assertFalse(result['success'])
        self.assertIn('cannot be negative', result['error'])

    def test_a_transient_failure_retries_the_whole_transaction(self):
        router = _HoldRouter()
        attempts = []

        def _flaky(stmt, *args, **kwargs):
            router.context = 'transaction'
            text, _ = _compiled(stmt)
            if text.startswith('UPDATE namespace_claims'):
                attempts.append(text)
                if len(attempts) == 1:
                    raise OperationalError(
                        'stmt', {}, Exception(1213, 'deadlock'))
            return router(stmt, *args, **kwargs)

        engine = mock.MagicMock()
        transaction = mock.MagicMock()
        transaction.execute.side_effect = _flaky
        autocommit = mock.MagicMock()
        autocommit.execute.side_effect = router.on('autocommit')
        engine.begin.return_value.__enter__.return_value = transaction
        engine.connect.return_value.__enter__.return_value = autocommit
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine), \
                mock.patch('shakenfist.mariadb.time.sleep'):
            result = mariadb._direct_book_claim_holds(str(CLAIM1), [
                {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40,
                 'node_uuid': str(NODE1)}])

        self.assertTrue(result['booked'], result['error'])
        self.assertEqual(2, len(attempts))

    def test_a_database_error_is_a_failure_and_not_a_refusal(self):
        router = _HoldRouter()
        engine = mock.MagicMock()
        engine.begin.side_effect = OperationalError(
            'stmt', {}, Exception(1146, 'no such table'))
        autocommit = mock.MagicMock()
        autocommit.execute.side_effect = router.on('autocommit')
        engine.connect.return_value.__enter__.return_value = autocommit
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            result = mariadb._direct_book_claim_holds(str(CLAIM1), [
                {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40,
                 'node_uuid': str(NODE1)}])

        self.assertFalse(result['success'])
        self.assertEqual('', result['refused_reason'])
        self.assertIn('MariaDB error', result['error'])


class ClaimCreateHoldInvariantTestCase(_HoldMixin, base.ShakenFistTestCase):
    """The claim create asserts the same invariant, in the same form."""

    def _call(self, **kwargs):
        args = {
            'claim_uuid': str(CLAIM1), 'namespace': 'ci-1',
            'limit_cpus': 16, 'limit_memory_mb': 16384,
            'limit_disk_gb': 200, 'expires_in_seconds': 3600,
        }
        args.update(kwargs)
        return mariadb._direct_create_namespace_claim(**args)

    def _router(self, **kwargs):
        # No active claim for the namespace, so the create's existence
        # probe passes; the cluster singleton is present so its mirror
        # guard can admit the claim.
        return _HoldRouter(
            claim=None,
            cluster_row=SimpleNamespace(
                id=1, total_cpus=144, total_memory_mb=393216,
                total_disk_gb=1000, claimed_cpus=0, claimed_memory_mb=0,
                claimed_disk_gb=0, unclaimed_used_cpus=0,
                unclaimed_used_memory_mb=0, unclaimed_used_disk_gb=0),
            **kwargs)

    def test_the_create_guards_the_hold_sum_in_the_same_form(self):
        # Unreachable today -- a fresh uuid4 has no hold rows -- and
        # here so that both of the statements which set a claim's limits
        # assert the invariant, rather than only the one where it can
        # currently fail.
        router = self._router()
        result = self._run(router)

        self.assertTrue(result['created'], result['error'])
        guards = [text for text in router.statements('UPDATE '
                                                     'namespace_claims')
                  if 'namespace_claim_holds' in text]
        self.assertEqual(1, len(guards))
        for dimension in mariadb.CAPACITY_DIMENSIONS:
            self.assertIn(
                f'coalesce((SELECT sum(namespace_claim_holds.{dimension})',
                ' '.join(guards[0].split()))

    def test_the_guard_comes_after_the_insert_it_guards(self):
        router = self._router()
        self.assertTrue(self._run(router)['created'])
        inserted = [index for index, text in enumerate(router.transactional)
                    if text.startswith('INSERT INTO namespace_claims')]
        guarded = [index for index, text in enumerate(router.transactional)
                   if text.startswith('UPDATE namespace_claims')
                   and 'namespace_claim_holds' in text]
        self.assertLess(inserted[0], guarded[0])

    def test_a_claim_uuid_that_already_holds_is_a_failed_write(self):
        # Not a refusal: a refusal is a guard saying no to something the
        # caller asked for, and this says the claim row and the hold
        # rows disagree about a state neither can reach.
        router = self._router(rowcounts={'claim_update': 0})
        result = self._run(router)

        self.assertFalse(result['success'])
        self.assertFalse(result['created'])
        self.assertEqual('', result['refused_reason'])
        self.assertIn('already carries holds', result['error'])


class ReadClaimHoldsTestCase(_HoldMixin, base.ShakenFistTestCase):
    """The hold listing."""

    def _call(self, **kwargs):
        args = {'claim_uuid': str(CLAIM1)}
        args.update(kwargs)
        return mariadb._direct_get_namespace_claim_holds(**args)

    def test_a_listing_reads_the_claims_holds_oldest_first(self):
        router = _HoldRouter(hold_rows=[_hold_row()])
        holds = self._run(router)

        self.assertEqual(1, len(holds))
        self.assertEqual(str(HOLD1), holds[0]['uuid'])
        self.assertEqual(str(NODE1), holds[0]['node_uuid'])
        self.assertEqual('', holds[0]['instance_uuid'])
        [select] = router.of('SELECT')
        text, _ = _compiled(select)
        self.assertIn('ORDER BY namespace_claim_holds.created_at, '
                      'namespace_claim_holds.uuid', ' '.join(text.split()))
        self.assertIn('namespace_claim_holds.claim_uuid = %s', text)

    def test_the_timestamps_are_converted_server_side(self):
        router = _HoldRouter(hold_rows=[_hold_row()])
        self._run(router)
        [select] = router.of('SELECT')
        text, _ = _compiled(select)
        self.assertIn('unix_timestamp(namespace_claim_holds.created_at)', text)

    def test_a_consumed_hold_reports_its_instance(self):
        router = _HoldRouter(hold_rows=[_hold_row(instance_uuid=INST1)])
        [hold] = self._run(router)
        self.assertEqual(str(INST1), hold['instance_uuid'])

    def test_a_displaced_hold_reports_an_empty_node(self):
        router = _HoldRouter(hold_rows=[_hold_row(node_uuid=None)])
        [hold] = self._run(router)
        self.assertEqual('', hold['node_uuid'])

    def test_a_claim_with_no_holds_reads_as_empty(self):
        router = _HoldRouter(hold_rows=[])
        self.assertEqual([], self._run(router))

    def test_a_malformed_uuid_holds_nothing(self):
        router = _HoldRouter()
        self.assertEqual([], self._run(router, claim_uuid='not-a-uuid'))

    def test_a_failed_read_raises_rather_than_reading_as_empty(self):
        # An empty list has to mean "no holds". A release path handed one
        # by a database that was merely unwell would conclude there was
        # nothing to give back and move the counters accordingly.
        engine = mock.MagicMock()
        engine.connect.return_value.__enter__.return_value.execute \
            .side_effect = OperationalError(
                'stmt', {}, Exception(2006, 'server has gone away'))
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            self.assertRaises(
                exceptions.DatabaseUnavailable,
                mariadb._direct_get_namespace_claim_holds, str(CLAIM1))


class HoldGrpcWrapperTestCase(base.ShakenFistTestCase):
    """The client side of the two hold RPCs this step implements."""

    def setUp(self):
        super().setUp()
        self.stub = mock.MagicMock()
        patcher = mock.patch('shakenfist.mariadb._get_database_stub',
                             return_value=self.stub)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_booking_sends_every_shape_with_its_node(self):
        self.stub.BookNamespaceClaimHolds.return_value = \
            database_pb2.BookNamespaceClaimHoldsReply(
                success=True, booked=True, holds=[
                    database_pb2.NamespaceClaimHold(
                        uuid=str(HOLD1), claim_uuid=str(CLAIM1),
                        namespace='ci-1', cpus=4, memory_mb=4096,
                        disk_gb=40, node_uuid=str(NODE1))])

        result = mariadb._grpc_book_claim_holds(str(CLAIM1), [
            {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40,
             'node_uuid': str(NODE1)}])

        self.assertTrue(result['booked'])
        self.assertEqual(str(NODE1), result['holds'][0]['node_uuid'])
        self.assertEqual('', result['holds'][0]['instance_uuid'])
        request = self.stub.BookNamespaceClaimHolds.call_args[0][0]
        self.assertEqual(str(CLAIM1), request.claim_uuid)
        self.assertEqual(1, len(request.shapes))
        self.assertEqual(str(NODE1), request.shapes[0].node_uuid)
        self.assertEqual(4096, request.shapes[0].memory_mb)

    def test_a_booking_refusal_carries_its_dimensions_back(self):
        reply = database_pb2.BookNamespaceClaimHoldsReply(
            success=True, booked=False, refused_reason='claim_capacity')
        reply.dimensions.add(dimension='cpus', limit=16.0, used=14.0,
                             requested=4.0, exceeded=True, shortfall=2.0)
        self.stub.BookNamespaceClaimHolds.return_value = reply

        result = mariadb._grpc_book_claim_holds(str(CLAIM1), [
            {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40,
             'node_uuid': str(NODE1)}])

        self.assertFalse(result['booked'])
        self.assertEqual('claim_capacity', result['refused_reason'])
        self.assertEqual(2.0, result['dimensions'][0]['shortfall'])

    def test_an_unreachable_database_is_a_failed_booking(self):
        self.stub.BookNamespaceClaimHolds.side_effect = \
            exceptions.DatabaseUnavailable('down')
        result = mariadb._grpc_book_claim_holds(str(CLAIM1), [
            {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40,
             'node_uuid': str(NODE1)}])

        self.assertFalse(result['success'])
        self.assertFalse(result['booked'])
        self.assertIn('database unavailable', result['error'])

    def test_a_listing_converts_every_hold(self):
        self.stub.GetNamespaceClaimHolds.return_value = \
            database_pb2.GetNamespaceClaimHoldsReply(holds=[
                database_pb2.NamespaceClaimHold(
                    uuid=str(HOLD1), claim_uuid=str(CLAIM1),
                    namespace='ci-1', cpus=4, memory_mb=4096, disk_gb=40,
                    node_uuid=str(NODE1), instance_uuid=str(INST1),
                    created_at=1799000000.0, updated_at=1799000001.0)])

        [hold] = mariadb._grpc_get_namespace_claim_holds(str(CLAIM1))
        self.assertEqual(str(INST1), hold['instance_uuid'])
        self.assertEqual(1799000001.0, hold['updated_at'])

    def test_a_failed_listing_raises_rather_than_reading_as_empty(self):
        # The convention this tier states for every reply whose only
        # payload is a repeated field: an empty list means "none", so a
        # failure has to raise.
        with mock.patch('shakenfist.mariadb._grpc_call',
                        side_effect=grpc.RpcError()):
            self.assertRaises(
                exceptions.DatabaseUnavailable,
                mariadb._grpc_get_namespace_claim_holds, str(CLAIM1))


class HoldPublicDispatchTestCase(base.ShakenFistTestCase):
    """The public wrappers pick a tier and change nothing else."""

    def test_booking_dispatches_to_grpc_when_the_service_is_in_use(self):
        with mock.patch('shakenfist.mariadb._use_database_service',
                        return_value=True), \
                mock.patch('shakenfist.mariadb._grpc_book_claim_holds',
                           return_value='grpc') as booked:
            self.assertEqual('grpc', mariadb.book_claim_holds('c', []))
        booked.assert_called_once_with('c', [])

    def test_booking_dispatches_direct_otherwise(self):
        with mock.patch('shakenfist.mariadb._use_database_service',
                        return_value=False), \
                mock.patch('shakenfist.mariadb._direct_book_claim_holds',
                           return_value='direct') as booked:
            self.assertEqual('direct', mariadb.book_claim_holds('c', []))
        booked.assert_called_once_with('c', [])

    def test_a_listing_dispatches_to_grpc_when_the_service_is_in_use(self):
        with mock.patch('shakenfist.mariadb._use_database_service',
                        return_value=True), \
                mock.patch(
                    'shakenfist.mariadb._grpc_get_namespace_claim_holds',
                    return_value=['grpc']) as listed:
            self.assertEqual(
                ['grpc'], mariadb.get_namespace_claim_holds('c'))
        listed.assert_called_once_with('c')

    def test_a_listing_dispatches_direct_otherwise(self):
        with mock.patch('shakenfist.mariadb._use_database_service',
                        return_value=False), \
                mock.patch(
                    'shakenfist.mariadb._direct_get_namespace_claim_holds',
                    return_value=['direct']) as listed:
            self.assertEqual(
                ['direct'], mariadb.get_namespace_claim_holds('c'))
        listed.assert_called_once_with('c')


class HoldServicerTestCase(base.ShakenFistTestCase):
    """The servicer handlers behind the two implemented RPCs."""

    def setUp(self):
        super().setUp()
        self.service = database_main.DatabaseService.__new__(
            database_main.DatabaseService)
        self.service.monitor = mock.MagicMock()
        self.context = mock.MagicMock()

    def _hold(self, **kwargs):
        hold = {
            'uuid': str(HOLD1), 'claim_uuid': str(CLAIM1),
            'namespace': 'ci-1', 'cpus': 4, 'memory_mb': 4096,
            'disk_gb': 40, 'node_uuid': str(NODE1), 'instance_uuid': '',
            'created_at': 1799000000.0, 'updated_at': 1799000000.0,
        }
        hold.update(kwargs)
        return hold

    def test_booking_forwards_the_shapes_and_the_reply(self):
        with mock.patch('shakenfist.mariadb._direct_book_claim_holds',
                        return_value={
                            'success': True, 'error': '', 'booked': True,
                            'refused_reason': '', 'dimensions': [],
                            'holds': [self._hold()]}) as booked:
            request = database_pb2.BookNamespaceClaimHoldsRequest(
                claim_uuid=str(CLAIM1), shapes=[
                    database_pb2.NamespaceClaimHoldShape(
                        cpus=4, memory_mb=4096, disk_gb=40,
                        node_uuid=str(NODE1))])
            reply = self.service.BookNamespaceClaimHolds(
                request, self.context)

        self.assertTrue(reply.booked)
        self.assertEqual(str(NODE1), reply.holds[0].node_uuid)
        self.assertEqual('', reply.holds[0].instance_uuid)
        booked.assert_called_once_with(str(CLAIM1), [
            {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40,
             'node_uuid': str(NODE1)}])

    def test_a_listing_is_served_from_the_direct_read(self):
        with mock.patch(
                'shakenfist.mariadb._direct_get_namespace_claim_holds',
                return_value=[self._hold(instance_uuid=str(INST1))]):
            reply = self.service.GetNamespaceClaimHolds(
                database_pb2.GetNamespaceClaimHoldsRequest(
                    claim_uuid=str(CLAIM1)), self.context)
        self.assertEqual(str(INST1), reply.holds[0].instance_uuid)

    def test_a_failed_listing_sets_internal(self):
        with mock.patch(
                'shakenfist.mariadb._direct_get_namespace_claim_holds',
                side_effect=exceptions.DatabaseUnavailable('down')):
            reply = self.service.GetNamespaceClaimHolds(
                database_pb2.GetNamespaceClaimHoldsRequest(
                    claim_uuid=str(CLAIM1)), self.context)
        self.assertEqual([], list(reply.holds))
        self.context.set_code.assert_called_once_with(
            grpc.StatusCode.INTERNAL)


class ClaimBookHoldsTestCase(base.ShakenFistTestCase):
    """NamespaceClaim.book_holds(), which is where the nodes are chosen."""

    def setUp(self):
        super().setUp()
        self.claim = namespace_claim.NamespaceClaim({
            'uuid': str(CLAIM1), 'namespace': 'ci-1', 'version': 1})
        events = mock.patch.object(
            namespace_claim.NamespaceClaim, 'add_event')
        self.add_event = events.start()
        self.addCleanup(events.stop)

    def _scheduler(self, assignment):
        scheduler = mock.MagicMock()
        scheduler.find_candidates_for_shapes.return_value = assignment
        return scheduler

    def test_each_shape_is_booked_on_the_head_of_its_own_list(self):
        # The head is the node the scheduler charged that shape to, and
        # every shape after it was ranked as though it were there, so
        # anything further down the list is arithmetic nobody did.
        scheduler = self._scheduler({0: [str(NODE1), str(NODE2)],
                                     1: [str(NODE2), str(NODE1)]})
        with mock.patch('shakenfist.mariadb.book_claim_holds',
                        return_value={
                            'success': True, 'error': '', 'booked': True,
                            'refused_reason': '', 'dimensions': [],
                            'holds': []}) as booked:
            self.claim.book_holds([
                {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40},
                {'cpus': 2, 'memory_mb': 2048, 'disk_gb': 20},
            ], scheduler=scheduler)

        booked.assert_called_once_with(str(CLAIM1), [
            {'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40,
             'node_uuid': str(NODE1)},
            {'cpus': 2, 'memory_mb': 2048, 'disk_gb': 20,
             'node_uuid': str(NODE2)},
        ])

    def test_the_shapes_reach_the_scheduler_in_the_namespace(self):
        scheduler = self._scheduler({0: [str(NODE1)]})
        with mock.patch('shakenfist.mariadb.book_claim_holds',
                        return_value={
                            'success': True, 'error': '', 'booked': True,
                            'refused_reason': '', 'dimensions': [],
                            'holds': []}):
            self.claim.book_holds(
                [{'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40}],
                scheduler=scheduler)

        shapes = scheduler.find_candidates_for_shapes.call_args[0][0]
        self.assertEqual(1, len(shapes))
        self.assertEqual('ci-1', shapes[0].namespace)
        self.assertEqual(4, shapes[0].cpus)
        self.assertEqual({}, shapes[0].affinity)
        self.assertIsNone(shapes[0].instance_uuid)

    def test_a_refused_placement_propagates_with_its_stage_and_shape(self):
        # Not translated into a claim refusal: the stage says why
        # nothing fitted and the shape index says which request it was,
        # and a caller cannot act on either if they are collapsed.
        refusal = exceptions.LowResourceException(
            'no nodes', stage='sufficient_idle_cpu')
        refusal.shape = 1
        scheduler = mock.MagicMock()
        scheduler.find_candidates_for_shapes.side_effect = refusal

        with mock.patch('shakenfist.mariadb.book_claim_holds') as booked:
            raised = self.assertRaises(
                exceptions.LowResourceException, self.claim.book_holds,
                [{'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40},
                 {'cpus': 8, 'memory_mb': 8192, 'disk_gb': 80}],
                scheduler=scheduler)

        self.assertEqual('sufficient_idle_cpu', raised.stage)
        self.assertEqual(1, raised.shape)
        booked.assert_not_called()

    def test_a_refused_booking_raises_claim_refused_with_its_detail(self):
        scheduler = self._scheduler({0: [str(NODE1)]})
        with mock.patch('shakenfist.mariadb.book_claim_holds',
                        return_value={
                            'success': True, 'error': '', 'booked': False,
                            'refused_reason': 'node_capacity',
                            'dimensions': [{'dimension': 'cpus',
                                            'limit': 16.0, 'used': 14.0,
                                            'requested': 4.0,
                                            'exceeded': True}],
                            'holds': []}):
            raised = self.assertRaises(
                namespace_claim.ClaimRefused, self.claim.book_holds,
                [{'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40}],
                scheduler=scheduler)

        self.assertEqual('node_capacity', raised.reason)
        self.assertTrue(raised.dimensions[0]['exceeded'])

    def test_a_failed_write_raises_rather_than_refusing(self):
        scheduler = self._scheduler({0: [str(NODE1)]})
        with mock.patch('shakenfist.mariadb.book_claim_holds',
                        return_value={
                            'success': False, 'error': 'MariaDB error: boom',
                            'booked': False, 'refused_reason': '',
                            'dimensions': [], 'holds': []}):
            self.assertRaises(
                exceptions.WriteException, self.claim.book_holds,
                [{'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40}],
                scheduler=scheduler)

    def test_a_booking_is_recorded_against_the_claim(self):
        scheduler = self._scheduler({0: [str(NODE1)]})
        hold = {
            'uuid': str(HOLD1), 'claim_uuid': str(CLAIM1),
            'namespace': 'ci-1', 'cpus': 4, 'memory_mb': 4096,
            'disk_gb': 40, 'node_uuid': str(NODE1), 'instance_uuid': '',
            'created_at': 1799000000.0, 'updated_at': 1799000000.0,
        }
        with mock.patch('shakenfist.mariadb.book_claim_holds',
                        return_value={
                            'success': True, 'error': '', 'booked': True,
                            'refused_reason': '', 'dimensions': [],
                            'holds': [hold]}):
            booked = self.claim.book_holds(
                [{'cpus': 4, 'memory_mb': 4096, 'disk_gb': 40}],
                scheduler=scheduler)

        self.assertEqual([hold], booked)
        extra = self.add_event.call_args[1]['extra']
        self.assertEqual(str(HOLD1), extra['holds'][0]['uuid'])
        self.assertEqual(str(NODE1), extra['holds'][0]['node'])

    def test_an_empty_request_is_refused_before_the_scheduler_runs(self):
        scheduler = mock.MagicMock()
        self.assertRaises(ValueError, self.claim.book_holds, [],
                          scheduler=scheduler)
        scheduler.find_candidates_for_shapes.assert_not_called()
