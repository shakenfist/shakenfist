# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Tests for the placement admission primitive (phase 3, D1 and D3).

Covers the pure retry-classification and denial-detail helpers, and the
SQL layer of _direct_admit_instance_placement() /
_direct_release_instance_placement() against mocked engines in the same
style as test_mariadb_capacity_reconcile.py: statement shapes are
verified by compiling what was executed, not by running it.

Execution against a real server -- where the guards actually bind, the
uuid forms actually have to match and the driver's rowcount semantics
actually matter -- is test_mariadb_capacity_admission_live.py.
"""

import inspect
from types import SimpleNamespace
from unittest import mock
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError

from shakenfist.daemons.database import main as database_main
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.protos import database_pb2
from shakenfist.tests import base


MYSQL_DIALECT = sa.dialects.mysql.dialect()

NODE1 = UUID('11111111-1111-1111-1111-111111111111')
NODE2 = UUID('22222222-2222-2222-2222-222222222222')
INST1 = UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
CLAIM1 = UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')

PLACEMENT_JSON = '{"node": "11111111-1111-1111-1111-111111111111", ' \
                 '"placement_attempts": 1}'


def _compiled(stmt):
    """Compile an executed statement for shape assertions."""
    compiled = stmt.compile(dialect=MYSQL_DIALECT)
    return str(compiled), compiled.params


def _operational_error(errno):
    """A SQLAlchemy OperationalError carrying a MariaDB errno."""
    return OperationalError('stmt', {}, Exception(errno, 'boom'))


def _claim_row(uuid=CLAIM1, limit_cpus=16, limit_memory_mb=16384,
               limit_disk_gb=100, used_cpus=0, used_memory_mb=0,
               used_disk_gb=0):
    return SimpleNamespace(
        uuid=uuid, namespace='ci-1', limit_cpus=limit_cpus,
        limit_memory_mb=limit_memory_mb, limit_disk_gb=limit_disk_gb,
        used_cpus=used_cpus, used_memory_mb=used_memory_mb,
        used_disk_gb=used_disk_gb, state='active')


def _capacity_row(node_uuid=NODE1, limit_cpus=48, limit_memory_mb=196608,
                  limit_disk_gb=500, used_cpus=6, used_memory_mb=6144,
                  used_disk_gb=33, expected_demand=8.5):
    return SimpleNamespace(
        node_uuid=node_uuid, limit_cpus=limit_cpus,
        limit_memory_mb=limit_memory_mb, limit_disk_gb=limit_disk_gb,
        used_cpus=used_cpus, used_memory_mb=used_memory_mb,
        used_disk_gb=used_disk_gb, expected_demand=expected_demand,
        updated_at=None)


def _cluster_row(total_cpus=144, claimed_cpus=16, unclaimed_used_cpus=8):
    return SimpleNamespace(
        id=1, total_cpus=total_cpus, total_memory_mb=393216,
        total_disk_gb=1000, claimed_cpus=claimed_cpus,
        claimed_memory_mb=16384, claimed_disk_gb=100,
        unclaimed_used_cpus=unclaimed_used_cpus,
        unclaimed_used_memory_mb=8192, unclaimed_used_disk_gb=64)


class _PlacementRouter:
    """Routes a mocked connection's execute() to canned rows and rowcounts.

    An admission reads its branch and presence probes in autocommit and
    then issues a fixed sequence of guarded writes inside a transaction;
    each test says what it wants each of them to return and this
    dispatches on the compiled statement text. Every executed statement
    is recorded so the tests can assert on shapes as well as outcomes,
    and recorded separately per connection kind so a test can assert the
    ER_CHECKREAD invariant: nothing inside the transaction reads before
    the first guarded UPDATE.
    """

    def __init__(self, claim=None, node_row=_capacity_row(),
                 cluster_row=_cluster_row(), rowcounts=None,
                 reference_nodes=None):
        self.claim = claim
        self.node_row = node_row
        self.cluster_row = cluster_row
        self.reference_nodes = reference_nodes or []
        self.context = 'autocommit'
        self.transactional = []
        self.autocommit = []
        self.rowcounts = {
            'claim_update': 1,
            'cluster_update': 1,
            'node_claim': 1,
            'node_decrement': 1,
            'node_clamp': 1,
            'namespace_decrement': 1,
            'namespace_clamp': 1,
            'attributes': 1,
            'references': 1,
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
        """A connection-level execute() side effect tagged as autocommit or not."""
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
            if 'FROM namespace_claims' in text:
                return self._result(
                    rows=[self.claim] if self.claim else [])
            if 'FROM scheduler_node_capacity' in text:
                if self.node_row is None:
                    return self._result()
                return self._result(rows=[self.node_row])
            if 'FROM cluster_capacity' in text:
                if self.cluster_row is None:
                    return self._result()
                return self._result(rows=[self.cluster_row])
            if 'FROM node_metrics' in text:
                return self._result(rows=[SimpleNamespace(
                    cpu_load_1=4.0, cpu_schedulable=16)])
            if 'FROM object_references' in text:
                return self._result(rows=[
                    SimpleNamespace(source_uuid=str(node))
                    for node in self.reference_nodes])
            return self._result()

        if 'UPDATE namespace_claims' in text:
            key = ('namespace_clamp' if 'greatest' in text.lower()
                   else ('claim_update' if 'used_cpus + ' in text
                         or '+ %s' in text and 'used_cpus=' in text
                         and ' - %s' not in text
                         else 'namespace_decrement'))
            return self._result(rowcount=self.rowcounts[key])
        if 'UPDATE cluster_capacity' in text:
            key = ('namespace_clamp' if 'greatest' in text.lower()
                   else ('cluster_update' if ' - %s' not in text
                         else 'namespace_decrement'))
            return self._result(rowcount=self.rowcounts[key])
        if 'UPDATE scheduler_node_capacity' in text:
            if 'greatest' in text.lower():
                return self._result(rowcount=self.rowcounts['node_clamp'])
            if 'expected_demand=' in text:
                return self._result(rowcount=self.rowcounts['node_claim'])
            return self._result(rowcount=self.rowcounts['node_decrement'])
        if 'UPDATE instance_attributes' in text:
            return self._result(rowcount=self.rowcounts['attributes'])
        if 'object_references' in text:
            return self._result(rowcount=self.rowcounts['references'])
        return self._result()

    def statements(self, prefix):
        return [text for text, _ in self.executed if text.startswith(prefix)]


class _PlacementMixin:
    """Runs an admission or release against a routed mock engine.

    The transactional connection (``engine.begin()``) and the autocommit
    one (``engine.connect()``, where the probes and the denial detail
    read run) are separate mocks over the same router, so tests can tell
    which statement ran where.
    """

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


class AdmitBranchSelectionTestCase(_PlacementMixin, base.ShakenFistTestCase):
    """Which of the two D14 branches an admission draws down."""

    def _call(self, **kwargs):
        args = {
            'instance_uuid': str(INST1), 'namespace': 'ci-1',
            'node_uuid': str(NODE1), 'old_node_uuid': '', 'cpus': 4,
            'memory_mb': 4096, 'disk_gb': 20, 'demand_add': 10.0,
            'target_load': 0.75, 'enforce': True,
            'placement_json': PLACEMENT_JSON,
        }
        args.update(kwargs)
        return mariadb._direct_admit_instance_placement(**args)

    def test_unclaimed_namespace_draws_down_the_cluster_row(self):
        router = _PlacementRouter(claim=None)
        result = self._run(router)

        self.assertTrue(result['admitted'])
        updates = router.statements('UPDATE')
        self.assertTrue(any('UPDATE cluster_capacity' in u for u in updates))
        self.assertFalse(any('UPDATE namespace_claims' in u for u in updates))

    def test_cluster_guard_is_d14_unclaimed_shape(self):
        router = _PlacementRouter(claim=None)
        self._run(router)
        [update] = [text for text in router.statements('UPDATE')
                    if 'UPDATE cluster_capacity' in text]
        # unclaimed_used + x <= total - claimed, per dimension.
        self.assertIn('unclaimed_used_cpus + %s', update)
        self.assertIn('total_cpus - cluster_capacity.claimed_cpus', update)
        self.assertIn('unclaimed_used_disk_gb + %s', update)

    def test_claimed_namespace_draws_down_the_claim_row(self):
        router = _PlacementRouter(claim=_claim_row())
        result = self._run(router)

        self.assertTrue(result['admitted'])
        updates = router.statements('UPDATE')
        self.assertTrue(any('UPDATE namespace_claims' in u for u in updates))
        # P4/D14: the claim was accounted against the cluster row when it
        # was created, so drawing it down again here would double count.
        self.assertFalse(any('UPDATE cluster_capacity' in u
                             for u in updates))

    def test_the_claim_increment_is_advisory_this_phase(self):
        # D4/D16: claim ceilings are advisory for one release, so the
        # increment lands with nothing but the primary key in its WHERE.
        router = _PlacementRouter(claim=_claim_row())
        self._run(router)
        [update] = [text for text in router.statements('UPDATE')
                    if 'UPDATE namespace_claims' in text]
        self.assertIn('used_cpus=', update)
        self.assertNotIn('<=', update.split('WHERE', 1)[1])

    def test_the_claim_guard_survives_a_missing_node_capacity_row(self):
        # D6, and the property this whole step exists to establish: P7's
        # fail-open is a statement about *this node's* limits being
        # absent from the totals the node and cluster guards test, and a
        # claim's limits are namespace-denominated and node-independent,
        # so a missing node row must not drop the claim guard with them.
        #
        # CLAIM_ENFORCEMENT_HARD is False this phase, which makes the
        # property unobservable through default behaviour -- with the
        # guard off either way there is nothing to see. So it is pinned
        # here against the flag computation itself, by flipping the
        # constant the way phase 5 will: if claim_guarded ever gets
        # folded back into node_present, this fails the day the
        # constant flips rather than the day a namespace overspends.
        with mock.patch.object(mariadb, 'CLAIM_ENFORCEMENT_HARD', True):
            router = _PlacementRouter(claim=_claim_row(), node_row=None)
            result = self._run(router)

        self.assertTrue(result['admitted'])
        # The node had no capacity row, so its guard and the cluster's
        # did fail open...
        self.assertTrue(result['unguarded'])
        [update] = [text for text in router.statements('UPDATE')
                    if 'UPDATE namespace_claims' in text]
        # ...and the claim's did not.
        self.assertIn('limit_cpus', update)
        self.assertIn('limit_memory_mb', update)
        self.assertIn('limit_disk_gb', update)

    def test_enforce_false_still_disables_the_claim_guard(self):
        # P5's ground-truth writers record where a domain already is, and
        # a guard cannot refuse reality -- that stays true when phase 5
        # turns claim enforcement on.
        with mock.patch.object(mariadb, 'CLAIM_ENFORCEMENT_HARD', True):
            router = _PlacementRouter(claim=_claim_row())
            self._run(router, enforce=False)
        [update] = [text for text in router.statements('UPDATE')
                    if 'UPDATE namespace_claims' in text]
        self.assertNotIn('<=', update.split('WHERE', 1)[1])

    def test_claim_lookup_requires_active_and_unexpired(self):
        router = _PlacementRouter(claim=None)
        self._run(router)
        [select] = [text for text in router.statements('SELECT')
                    if 'FROM namespace_claims' in text]
        self.assertIn('namespace_claims.state = %s', select)
        self.assertIn('namespace_claims.expires_at > now()', select)


class AdmitAdvisoryClaimTestCase(_PlacementMixin, base.ShakenFistTestCase):
    """D5's record-don't-refuse accounting, read back after the write.

    The router returns the same canned claim row for both the branch
    probe and the read-back, so a row seeded with post-increment
    counters is what an over-claim admission sees on its way out.
    """

    def _call(self, **kwargs):
        args = {
            'instance_uuid': str(INST1), 'namespace': 'ci-1',
            'node_uuid': str(NODE1), 'old_node_uuid': '', 'cpus': 4,
            'memory_mb': 4096, 'disk_gb': 20, 'demand_add': 10.0,
            'target_load': 0.75, 'enforce': True,
            'placement_json': PLACEMENT_JSON,
        }
        args.update(kwargs)
        return mariadb._direct_admit_instance_placement(**args)

    def test_an_over_claim_admission_is_admitted_and_reported(self):
        router = _PlacementRouter(claim=_claim_row(
            limit_cpus=8, used_cpus=10, limit_memory_mb=16384,
            used_memory_mb=8192, limit_disk_gb=100, used_disk_gb=40))
        result = self._run(router)

        self.assertTrue(result['admitted'])
        # Advisory, so nothing about this reads as a refusal.
        self.assertEqual('', result['failing_stage'])
        self.assertEqual([], result['dimensions'])

        self.assertTrue(result['claim_over_limit'])
        detail = {d['dimension']: d for d in result['claim_dimensions']}
        # Only the dimension actually over is named.
        self.assertEqual(['cpus'], list(detail))
        self.assertEqual(8.0, detail['cpus']['limit'])
        # used is what the claim held before this admission, so the
        # triple reads exactly as a denial's does.
        self.assertEqual(6.0, detail['cpus']['used'])
        self.assertEqual(4.0, detail['cpus']['requested'])
        self.assertTrue(detail['cpus']['exceeded'])

    def test_every_over_dimension_is_named(self):
        router = _PlacementRouter(claim=_claim_row(
            limit_cpus=8, used_cpus=10, limit_memory_mb=4096,
            used_memory_mb=8192, limit_disk_gb=10, used_disk_gb=40))
        result = self._run(router)
        self.assertTrue(result['claim_over_limit'])
        self.assertEqual(
            ['cpus', 'memory_mb', 'disk_gb'],
            [d['dimension'] for d in result['claim_dimensions']])

    def test_a_within_limits_admission_reports_nothing(self):
        router = _PlacementRouter(claim=_claim_row(
            limit_cpus=16, used_cpus=8, limit_memory_mb=16384,
            used_memory_mb=8192, limit_disk_gb=100, used_disk_gb=40))
        result = self._run(router)

        self.assertTrue(result['admitted'])
        self.assertFalse(result['claim_over_limit'])
        self.assertEqual([], result['claim_dimensions'])

    def test_exactly_at_the_limit_is_not_over_it(self):
        # The guard phase 5 will restore is used + x <= limit, so a claim
        # sitting exactly on its ceiling has not exceeded it.
        router = _PlacementRouter(claim=_claim_row(
            limit_cpus=8, used_cpus=8, limit_memory_mb=16384,
            used_memory_mb=8192, limit_disk_gb=100, used_disk_gb=40))
        result = self._run(router)
        self.assertFalse(result['claim_over_limit'])

    def test_an_unclaimed_namespace_reads_the_claim_row_back_at_all(self):
        # No claim branch ran, so there is nothing this admission could
        # have pushed over a limit and no read-back to pay for.
        router = _PlacementRouter(claim=None)
        result = self._run(router)

        self.assertTrue(result['admitted'])
        self.assertFalse(result['claim_over_limit'])
        # The only namespace_claims read is the branch probe, and it ran
        # outside the transaction.
        self.assertEqual([], [text for text in router.transactional
                              if 'FROM namespace_claims' in text])

    def test_the_read_back_happens_after_the_write_it_reads(self):
        # The ER_CHECKREAD invariant: a read inside the transaction is
        # only legal because we already hold that row's lock.
        router = _PlacementRouter(claim=_claim_row(
            limit_cpus=8, used_cpus=10))
        self._run(router)

        transactional = router.transactional
        write = transactional.index(
            [t for t in transactional
             if t.startswith('UPDATE namespace_claims')][0])
        read = transactional.index(
            [t for t in transactional
             if t.startswith('SELECT') and 'FROM namespace_claims' in t][0])
        self.assertLess(write, read)
        # And nothing at all read before that first write.
        self.assertEqual(0, write)


class AdmitGuardTestCase(_PlacementMixin, base.ShakenFistTestCase):
    """The node guard, the enforce flag and the P7 fail-open."""

    def _call(self, **kwargs):
        args = {
            'instance_uuid': str(INST1), 'namespace': 'ci-1',
            'node_uuid': str(NODE1), 'old_node_uuid': '', 'cpus': 4,
            'memory_mb': 4096, 'disk_gb': 20, 'demand_add': 10.0,
            'target_load': 0.75, 'enforce': True,
            'placement_json': PLACEMENT_JSON,
        }
        args.update(kwargs)
        return mariadb._direct_admit_instance_placement(**args)

    def _node_update(self, router):
        return [text for text in router.statements('UPDATE')
                if 'UPDATE scheduler_node_capacity' in text][0]

    def test_node_guard_covers_three_dimensions_and_demand(self):
        router = _PlacementRouter()
        self._run(router)
        update = self._node_update(router)
        self.assertIn('used_cpus + %s', update)
        self.assertIn('used_memory_mb + %s', update)
        self.assertIn('used_disk_gb + %s', update)
        # The D13 feedforward clause reads the measured inputs from the
        # typed node_metrics columns inside the transaction.
        self.assertIn('node_metrics.cpu_load_1', update)
        self.assertIn('node_metrics.cpu_schedulable', update)
        self.assertIn('expected_demand', update)

    def test_demand_clause_passes_when_metrics_are_null(self):
        # A node whose resources daemon has not published typed columns
        # has no bound to compute, so the clause must not deny it.
        router = _PlacementRouter()
        self._run(router)
        self.assertIn('IS NULL', self._node_update(router))

    def test_demand_clause_is_skipped_for_a_non_positive_target_load(self):
        # An unset proto3 double reads as 0.0. A caller mid-upgrade that
        # does not send target_load must not have every placement denied.
        self.assertIsNone(mariadb._demand_guard_clause(NODE1, 0.0))
        self.assertIsNone(mariadb._demand_guard_clause(NODE1, -1.0))
        self.assertIsNotNone(mariadb._demand_guard_clause(NODE1, 0.75))

    def test_the_demand_clause_does_not_charge_the_placement(self):
        # Issue #3813: the clause compared a per-request charge against
        # a per-node budget, which made it unsatisfiable below 3.34
        # schedulable threads whatever the node's real headroom. The
        # charge is gone, and structurally so -- the helper does not
        # take one, so wiring it back in cannot be a one-line edit at
        # the call site -- while the same UPDATE still adds it to
        # expected_demand for the next decision.
        self.assertNotIn(
            'demand_add',
            inspect.signature(mariadb._demand_guard_clause).parameters)

        clause = mariadb._demand_guard_clause(NODE1, 0.75)
        text, params = _compiled(sa.select(sa.literal(1)).where(clause))
        self.assertIn('cpu_load_1', text)
        self.assertIn('cpu_schedulable', text)
        self.assertIn('expected_demand', text)
        # The only numbers bound into the comparison are the target load
        # and the coalesce default for a NULL measured load. Anything
        # else is a per-request term that does not belong here.
        self.assertEqual(
            {0.0, 0.75},
            {v for v in params.values() if isinstance(v, float)})

        # The charge does still reach the SET, so a burst spreads.
        router = _PlacementRouter()
        self._run(router)
        update = self._node_update(router)
        self.assertIn('expected_demand=(scheduler_node_capacity.'
                      'expected_demand + %s)', update)
        # ... and the comparison is now node state on both sides.
        self.assertIn('scheduler_node_capacity.expected_demand <= %s * '
                      '(SELECT node_metrics.cpu_schedulable', update)

    def test_enforce_false_keeps_the_counters_and_drops_the_guards(self):
        router = _PlacementRouter(claim=None)
        result = self._run(router, enforce=False)

        self.assertTrue(result['admitted'])
        node_update = self._node_update(router)
        # Still incrementing.
        self.assertIn('SET used_cpus=', node_update)
        # But the only WHERE left is the primary key.
        where = node_update.split('WHERE', 1)[1]
        self.assertIn('node_uuid', where)
        self.assertNotIn('<=', where)

        cluster_update = [text for text in router.statements('UPDATE')
                          if 'UPDATE cluster_capacity' in text][0]
        self.assertNotIn('<=', cluster_update.split('WHERE', 1)[1])

    def test_missing_node_row_admits_unguarded(self):
        # P7: mid-upgrade a node can have no capacity row. Fail closed
        # here would make the whole cluster refuse creates.
        router = _PlacementRouter(node_row=None)
        result = self._run(router)

        self.assertTrue(result['admitted'])
        self.assertTrue(result['unguarded'])
        self.assertEqual([], [text for text in router.statements('UPDATE')
                              if 'UPDATE scheduler_node_capacity' in text])
        # And the remaining guards are dropped with it: the cluster
        # totals do not include a node that has no capacity row.
        cluster_update = [text for text in router.statements('UPDATE')
                          if 'UPDATE cluster_capacity' in text][0]
        self.assertNotIn('<=', cluster_update.split('WHERE', 1)[1])

    def test_missing_cluster_row_admits_unguarded(self):
        # A cluster whose reconciler has never run has no singleton to
        # draw down; denying every create until it does would be worse.
        router = _PlacementRouter(cluster_row=None)
        result = self._run(router)

        self.assertTrue(result['admitted'])
        self.assertTrue(result['unguarded'])
        self.assertEqual([], [text for text in router.statements('UPDATE')
                              if 'UPDATE cluster_capacity' in text])
        # The node guard still applies -- that row exists.
        self.assertIn('<=', self._node_update(router).split('WHERE', 1)[1])

    def test_post_admit_counters_come_from_a_pk_select(self):
        # MariaDB has no UPDATE ... RETURNING.
        router = _PlacementRouter(node_row=_capacity_row(
            used_cpus=10, used_memory_mb=10240, used_disk_gb=53,
            expected_demand=18.5))
        result = self._run(router)
        self.assertEqual(10, result['node_used_cpus'])
        self.assertEqual(10240, result['node_used_memory_mb'])
        self.assertEqual(53, result['node_used_disk_gb'])
        self.assertEqual(18.5, result['node_expected_demand'])


class AdmitDenialTestCase(_PlacementMixin, base.ShakenFistTestCase):
    """rowcount 0 on a guarded UPDATE aborts and reports why."""

    def _call(self, **kwargs):
        args = {
            'instance_uuid': str(INST1), 'namespace': 'ci-1',
            'node_uuid': str(NODE1), 'old_node_uuid': '', 'cpus': 4,
            'memory_mb': 4096, 'disk_gb': 20, 'demand_add': 10.0,
            'target_load': 0.75, 'enforce': True,
            'placement_json': PLACEMENT_JSON,
        }
        args.update(kwargs)
        return mariadb._direct_admit_instance_placement(**args)

    def test_cluster_denial_names_its_stage_and_aborts(self):
        router = _PlacementRouter(
            claim=None, rowcounts={'cluster_update': 0},
            cluster_row=_cluster_row(
                total_cpus=20, claimed_cpus=0, unclaimed_used_cpus=18))
        result = self._run(router)

        self.assertTrue(result['success'])
        self.assertFalse(result['admitted'])
        self.assertEqual('cluster', result['failing_stage'])
        # Nothing downstream of the failing guard ran.
        self.assertEqual([], [text for text in router.statements('UPDATE')
                              if 'instance_attributes' in text])

    def test_cluster_denial_detail_uses_the_unclaimed_limit(self):
        router = _PlacementRouter(
            claim=None, rowcounts={'cluster_update': 0},
            cluster_row=_cluster_row(
                total_cpus=20, claimed_cpus=0, unclaimed_used_cpus=18))
        result = self._run(router)

        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(20.0, detail['cpus']['limit'])
        self.assertEqual(18.0, detail['cpus']['used'])
        self.assertEqual(4.0, detail['cpus']['requested'])
        self.assertTrue(detail['cpus']['exceeded'])
        # The dimensions which did fit say so, so the audit event and the
        # 507 name the binding one rather than all three.
        self.assertFalse(detail['memory_mb']['exceeded'])

    def test_claim_denial_reports_the_claim_limits(self):
        # Advisory mode (D16) means the claim's WHERE is a bare primary
        # key this release, so the only way it matches no row is that the
        # row went away between the probe and the transaction. The denial
        # path is unchanged and still has to report why, because phase 5
        # turns the guard back on and reaches it the ordinary way.
        router = _PlacementRouter(
            claim=_claim_row(limit_cpus=8, used_cpus=6),
            rowcounts={'claim_update': 0})
        result = self._run(router)

        self.assertEqual('claim', result['failing_stage'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(8.0, detail['cpus']['limit'])
        self.assertEqual(6.0, detail['cpus']['used'])
        self.assertTrue(detail['cpus']['exceeded'])

    def test_node_denial_reports_the_demand_dimension_too(self):
        # A node guard can refuse on the D13 clause with every
        # allocation dimension fitting, and a denial with no exceeded
        # dimension at all is exactly what an operator needs told.
        router = _PlacementRouter(rowcounts={'node_claim': 0})
        result = self._run(router)

        self.assertEqual('node', result['failing_stage'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertIn('demand', detail)
        # 0.75 x 16 schedulable threads.
        self.assertEqual(12.0, detail['demand']['limit'])
        # cpu_load_1 of 4.0 plus the row's expected_demand of 8.5.
        self.assertEqual(12.5, detail['demand']['used'])
        self.assertEqual(10.0, detail['demand']['requested'])
        self.assertTrue(detail['demand']['exceeded'])
        self.assertFalse(detail['cpus']['exceeded'])
        # The two terms of used mean different things -- ground truth
        # versus feedforward estimate -- so they are also reported
        # separately (issue 3913), and only on the demand dimension.
        self.assertEqual(4.0, detail['demand']['cpu_load_1'])
        self.assertEqual(8.5, detail['demand']['expected_demand'])
        self.assertNotIn('cpu_load_1', detail['cpus'])
        self.assertNotIn('expected_demand', detail['cpus'])

    def test_an_allocation_denial_with_quiet_demand_is_not_waivable(self):
        # The converse of the demand-only case, and the one that decides
        # the waiver does *not* fire: a node refused on cpus while its
        # demand sits comfortably under target must report exactly
        # {'cpus'} as exceeded. Asserting this alongside
        # test_a_node_under_target_does_not_report_demand_exceeded
        # matters because that test passes for the weaker reason that
        # nothing at all is exceeded.
        router = _PlacementRouter(
            node_row=_capacity_row(used_cpus=47, expected_demand=0.5),
            rowcounts={'node_claim': 0})
        result = self._run(router)

        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertTrue(detail['cpus']['exceeded'])
        self.assertFalse(detail['demand']['exceeded'])
        self.assertEqual(
            {'cpus'},
            {d['dimension'] for d in result['dimensions'] if d['exceeded']})

        denial = exceptions.CapacityAdmissionDenied(
            result['failing_stage'], result['dimensions'])
        self.assertFalse(denial.demand_only)

    def test_a_node_under_target_does_not_report_demand_exceeded(self):
        # The discriminating case for phase 4a. Measured load 4.0 plus
        # expected_demand 0.5 is 4.5 against a budget of 12.0, so the
        # clause passed on demand and the node refused on something
        # else. Charging the placement's 10.0 would push 4.5 over 12.0
        # and report a refusal the clause never made -- which would
        # then make the denial look demand_only and fire the P9 waiver
        # for a denial the waiver must not touch.
        router = _PlacementRouter(
            node_row=_capacity_row(expected_demand=0.5),
            rowcounts={'node_claim': 0})
        result = self._run(router)

        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(12.0, detail['demand']['limit'])
        self.assertEqual(4.5, detail['demand']['used'])
        self.assertEqual(10.0, detail['demand']['requested'])
        self.assertFalse(detail['demand']['exceeded'])

        denial = exceptions.CapacityAdmissionDenied(
            result['failing_stage'], result['dimensions'])
        self.assertFalse(denial.demand_only)

    def test_denied_admission_is_not_an_rpc_failure(self):
        # A caller that conflated the two would walk to the next
        # candidate on a database blip and eventually 507 a create that
        # had plenty of capacity.
        router = _PlacementRouter(claim=None,
                                  rowcounts={'cluster_update': 0})
        result = self._run(router)
        self.assertTrue(result['success'])
        self.assertEqual('', result['error'])


class AdmitMoveTestCase(_PlacementMixin, base.ShakenFistTestCase):
    """The old node's floored decrement, and reference rewriting."""

    def _call(self, **kwargs):
        args = {
            'instance_uuid': str(INST1), 'namespace': 'ci-1',
            'node_uuid': str(NODE2), 'old_node_uuid': str(NODE1), 'cpus': 4,
            'memory_mb': 4096, 'disk_gb': 20, 'demand_add': 10.0,
            'target_load': 0.75, 'enforce': True,
            'placement_json': PLACEMENT_JSON,
        }
        args.update(kwargs)
        return mariadb._direct_admit_instance_placement(**args)

    def test_move_decrements_the_old_node_and_claims_the_new_one(self):
        router = _PlacementRouter()
        result = self._run(router)

        self.assertTrue(result['admitted'])
        node_updates = [text for text in router.statements('UPDATE')
                        if 'UPDATE scheduler_node_capacity' in text]
        self.assertEqual(2, len(node_updates))
        # The lower uuid is touched first whichever direction the move
        # goes, so two moves crossing between the same pair of nodes
        # cannot take each other's rows in opposite orders.
        self.assertNotIn('expected_demand=', node_updates[0])
        self.assertIn('expected_demand=', node_updates[1])

    def test_move_to_a_lower_uuid_still_touches_rows_in_uuid_order(self):
        router = _PlacementRouter()
        self._run(router, node_uuid=str(NODE1), old_node_uuid=str(NODE2))
        node_updates = [text for text in router.statements('UPDATE')
                        if 'UPDATE scheduler_node_capacity' in text]
        self.assertEqual(2, len(node_updates))
        # NODE1 is the target here and sorts first, so its claim runs
        # before the decrement of NODE2.
        self.assertIn('expected_demand=', node_updates[0])
        self.assertNotIn('expected_demand=', node_updates[1])

    def test_a_move_does_not_touch_the_namespace_side(self):
        # Both the cluster singleton's unclaimed sums and a claim's
        # used_* are namespace-denominated and node-independent, and a
        # move never changes namespace, so there is nothing to wash: an
        # increment here has no matching decrement (the old node's row
        # is the other side of the ledger) and inflates the namespace by
        # one instance per move until the next reconcile pass.
        router = _PlacementRouter(claim=None)
        self._run(router)
        self.assertEqual([], [text for text in router.statements('UPDATE')
                              if 'UPDATE cluster_capacity' in text])

    def test_a_move_does_not_draw_down_a_claim_either(self):
        # ...and with no claim branch there is no advisory read-back
        # either: a move consumes nothing new on the namespace side, so
        # it cannot have pushed the claim over anything. A claim already
        # over its limits (which advisory mode allows) must therefore
        # not be reported again on every subsequent move of an unrelated
        # instance.
        router = _PlacementRouter(claim=_claim_row(
            limit_cpus=8, used_cpus=10))
        result = self._run(router)
        self.assertEqual([], [text for text in router.statements('UPDATE')
                              if 'UPDATE namespace_claims' in text])
        self.assertFalse(result['claim_over_limit'])
        self.assertEqual([], result['claim_dimensions'])
        self.assertEqual([], [text for text in router.transactional
                              if 'FROM namespace_claims' in text])

    def test_expected_demand_is_never_decremented(self):
        # The D13 term decays with instance age and the reconciler
        # recomputes it, so crediting the original contribution back
        # would over-credit a node whose contribution had decayed.
        router = _PlacementRouter()
        self._run(router)
        decrement = [text for text in router.statements('UPDATE')
                     if 'UPDATE scheduler_node_capacity' in text
                     and 'expected_demand=' not in text][0]
        self.assertIn('used_cpus=', decrement)

    def test_floored_decrement_clamps_and_says_so(self):
        router = _PlacementRouter(rowcounts={'node_decrement': 0})
        result = self._run(router)

        self.assertTrue(result['admitted'])
        self.assertTrue(result['clamped'])
        clamps = [text for text in router.statements('UPDATE')
                  if 'greatest' in text.lower()]
        self.assertEqual(1, len(clamps))
        self.assertIn('greatest', clamps[0].lower())

    def test_absent_old_node_row_is_not_a_clamp(self):
        # P7's fail-open again: no row at all is not the same condition
        # as a row whose counters are already too low.
        router = _PlacementRouter(rowcounts={'node_decrement': 0,
                                             'node_clamp': 0})
        result = self._run(router)
        self.assertTrue(result['admitted'])
        self.assertFalse(result['clamped'])

    def test_replacing_onto_the_same_node_is_not_a_move(self):
        router = _PlacementRouter()
        self._run(router, node_uuid=str(NODE1), old_node_uuid=str(NODE1))
        node_updates = [text for text in router.statements('UPDATE')
                        if 'UPDATE scheduler_node_capacity' in text]
        self.assertEqual(1, len(node_updates))
        self.assertIn('expected_demand=', node_updates[0])

    def test_every_stale_reference_row_is_deleted_before_the_insert(self):
        # Deleting only the old node's row is what let duplicate
        # placement rows survive, and the reconciler's ledger charges an
        # instance to every node holding one.
        router = _PlacementRouter()
        self._run(router)
        refs = [text for text, _ in router.executed
                if 'object_references' in text and not
                text.startswith('SELECT')]
        self.assertEqual(2, len(refs))
        self.assertTrue(refs[0].startswith('DELETE'))
        self.assertNotIn('source_uuid', refs[0])
        self.assertTrue(refs[1].startswith('INSERT'))

    def test_reference_insert_matches_record_relationship_conventions(self):
        router = _PlacementRouter()
        self._run(router)
        insert = [(text, stmt) for text, stmt in router.executed
                  if text.startswith('INSERT INTO object_references')][0]
        params = insert[1].compile(dialect=MYSQL_DIALECT).params
        # The enum columns store the member value, and the uuid columns
        # the dashed 36 character form -- the conventions the
        # reconciler's ground-truth query reads.
        self.assertEqual('node', params['source_object_type'])
        self.assertEqual('instance', params['target_object_type'])
        self.assertEqual('instance_location', params['relationship'])
        self.assertIsNone(params['relationship_value'])
        self.assertEqual(str(NODE2), params['source_uuid'])
        self.assertEqual(str(INST1), params['target_uuid'])


class AdmitInputValidationTestCase(_PlacementMixin, base.ShakenFistTestCase):
    """Bad input fails the RPC rather than being papered over."""

    def _call(self, **kwargs):
        args = {
            'instance_uuid': str(INST1), 'namespace': 'ci-1',
            'node_uuid': str(NODE1), 'old_node_uuid': '', 'cpus': 4,
            'memory_mb': 4096, 'disk_gb': 20, 'demand_add': 10.0,
            'target_load': 0.75, 'enforce': True,
            'placement_json': PLACEMENT_JSON,
        }
        args.update(kwargs)
        return mariadb._direct_admit_instance_placement(**args)

    def test_missing_attributes_row_is_an_error_not_a_denial(self):
        # Callers create instances before placing them, so a missing
        # attributes row is a bug. Inserting one here would produce a row
        # missing every other attribute the create path sets.
        router = _PlacementRouter(rowcounts={'attributes': 0})
        result = self._run(router)

        self.assertFalse(result['success'])
        self.assertFalse(result['admitted'])
        self.assertIn('instance_attributes', result['error'])
        # And the placement references were not rewritten.
        self.assertEqual([], [text for text, _ in router.executed
                              if text.startswith('DELETE')])

    def test_malformed_uuid_is_refused_before_any_write(self):
        router = _PlacementRouter()
        result = self._run(router, node_uuid='not-a-uuid')
        self.assertFalse(result['success'])
        self.assertIn('malformed uuid', result['error'])
        self.assertEqual([], router.executed)

    def test_empty_placement_json_is_refused(self):
        router = _PlacementRouter()
        result = self._run(router, placement_json='')
        self.assertFalse(result['success'])
        self.assertEqual([], router.executed)

    def test_invalid_placement_json_is_refused(self):
        # The column is JSON typed, so garbage would otherwise surface
        # as an opaque MariaDB error deep inside the transaction.
        router = _PlacementRouter()
        result = self._run(router, placement_json='{not json')
        self.assertFalse(result['success'])
        self.assertIn('valid JSON', result['error'])
        self.assertEqual([], router.executed)

    def _run_with_transaction_error(self, error):
        """A working probe connection and a transaction that always fails."""
        router = _PlacementRouter()
        engine = mock.MagicMock()
        transaction = mock.MagicMock()
        transaction.execute.side_effect = error
        autocommit = mock.MagicMock()
        autocommit.execute.side_effect = router.on('autocommit')
        engine.begin.return_value.__enter__.return_value = transaction
        engine.connect.return_value.__enter__.return_value = autocommit
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            return self._call()

    def test_operational_error_is_a_failed_rpc(self):
        result = self._run_with_transaction_error(_operational_error(1064))
        self.assertFalse(result['success'])
        self.assertFalse(result['admitted'])
        self.assertIn('MariaDB error', result['error'])

    def test_integrity_error_is_a_failed_rpc(self):
        result = self._run_with_transaction_error(
            IntegrityError('stmt', {}, Exception('x')))
        self.assertFalse(result['success'])

    def test_a_failed_probe_is_a_failed_rpc(self):
        # The probes moved out of the transaction, but they are still
        # part of the operation: a database that cannot answer them must
        # not read as an admission or a denial.
        engine = mock.MagicMock()
        autocommit = mock.MagicMock()
        autocommit.execute.side_effect = _operational_error(2006)
        engine.connect.return_value.__enter__.return_value = autocommit
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            result = self._call()
        self.assertFalse(result['success'])
        self.assertFalse(result['admitted'])
        self.assertIn('MariaDB error', result['error'])
        engine.begin.assert_not_called()


class ReleaseTestCase(_PlacementMixin, base.ShakenFistTestCase):
    """Floored decrements, idempotence and the no-op reply."""

    def _call(self, **kwargs):
        args = {
            'instance_uuid': str(INST1), 'namespace': 'ci-1',
            'node_uuid': '', 'cpus': 4, 'memory_mb': 4096, 'disk_gb': 20,
        }
        args.update(kwargs)
        return mariadb._direct_release_instance_placement(**args)

    def test_release_follows_the_references(self):
        router = _PlacementRouter(claim=None, reference_nodes=[NODE1])
        result = self._run(router)

        self.assertTrue(result['released'])
        self.assertFalse(result['clamped'])
        updates = router.statements('UPDATE')
        self.assertTrue(any('UPDATE cluster_capacity' in u for u in updates))
        self.assertTrue(any('UPDATE scheduler_node_capacity' in u
                            for u in updates))
        self.assertEqual(1, len([text for text, _ in router.executed
                                 if text.startswith('DELETE')]))

    def test_release_runs_in_canonical_order(self):
        # The same order admission uses (cluster or claim, then node), so
        # a release cannot deadlock against a concurrent admission.
        router = _PlacementRouter(claim=None, reference_nodes=[NODE1])
        self._run(router)
        updates = router.statements('UPDATE')
        self.assertIn('cluster_capacity', updates[0])
        self.assertIn('scheduler_node_capacity', updates[1])

    def test_release_of_a_claimed_namespace_hits_the_claim(self):
        router = _PlacementRouter(claim=_claim_row(used_cpus=8),
                                  reference_nodes=[NODE1])
        self._run(router)
        updates = router.statements('UPDATE')
        self.assertTrue(any('UPDATE namespace_claims' in u for u in updates))
        self.assertFalse(any('UPDATE cluster_capacity' in u
                             for u in updates))

    def test_a_named_node_still_consults_the_references(self):
        # A named node filters the located rows rather than replacing
        # them, so the lookup runs in both call forms.
        router = _PlacementRouter(claim=None, reference_nodes=[NODE2])
        result = self._run(router, node_uuid=str(NODE2))

        self.assertTrue(result['released'])
        self.assertEqual(1, len([text for text in router.statements('SELECT')
                                 if 'FROM object_references' in text]))

    def test_a_named_node_holding_no_reference_releases_nothing(self):
        # The repeated-delete shape: _delete_globally() names the node
        # from the never-cleared placement attribute, and an instance
        # which ended in state error reaches it on every delete attempt.
        # Trusting the name would decrement the counters again each
        # time, and the floors cannot catch it because other instances'
        # usage keeps the counters above the released amount.
        router = _PlacementRouter(claim=None, reference_nodes=[])
        result = self._run(router, node_uuid=str(NODE2))

        self.assertTrue(result['success'])
        self.assertFalse(result['released'])
        self.assertEqual([], router.statements('UPDATE'))
        self.assertEqual([], [text for text, _ in router.executed
                              if text.startswith('DELETE')])

    def test_a_named_node_which_is_not_the_located_one_releases_nothing(self):
        router = _PlacementRouter(claim=None, reference_nodes=[NODE1])
        result = self._run(router, node_uuid=str(NODE2))

        self.assertTrue(result['success'])
        self.assertFalse(result['released'])
        self.assertEqual([], router.statements('UPDATE'))

    def test_a_named_node_releases_only_its_own_row(self):
        router = _PlacementRouter(claim=None,
                                  reference_nodes=[NODE1, NODE2])
        result = self._run(router, node_uuid=str(NODE2))

        self.assertTrue(result['released'])
        node_updates = [text for text in router.statements('UPDATE')
                        if 'UPDATE scheduler_node_capacity' in text]
        self.assertEqual(1, len(node_updates))

    def test_a_named_node_release_deletes_only_its_own_row(self):
        # The rows deleted are exactly the rows credited back. A
        # historical duplicate on another node keeps both its row and
        # its charge -- deleting it without a decrement would strand
        # that node's charge until the next reconcile pass.
        router = _PlacementRouter(claim=None,
                                  reference_nodes=[NODE1, NODE2])
        result = self._run(router, node_uuid=str(NODE2))

        self.assertTrue(result['released'])
        deletes = [stmt for text, stmt in router.executed
                   if text.startswith('DELETE')]
        self.assertEqual(1, len(deletes))
        _, params = _compiled(deletes[0])
        sources = [v for k, v in params.items()
                   if k.startswith('source_uuid')]
        self.assertEqual([[str(NODE2)]], sources)

    def test_double_release_is_a_harmless_no_op(self):
        # No reference rows and no node named: nothing was held, so
        # decrementing would take capacity from an instance that never
        # had it.
        router = _PlacementRouter(claim=None, reference_nodes=[])
        result = self._run(router)

        self.assertTrue(result['success'])
        self.assertFalse(result['released'])
        self.assertEqual([], router.statements('UPDATE'))
        self.assertEqual([], [text for text, _ in router.executed
                              if text.startswith('DELETE')])

    def test_clamped_node_decrement_is_reported(self):
        router = _PlacementRouter(claim=None, reference_nodes=[NODE1],
                                  rowcounts={'node_decrement': 0})
        result = self._run(router)
        self.assertTrue(result['released'])
        self.assertTrue(result['clamped'])

    def test_clamped_namespace_decrement_is_reported(self):
        router = _PlacementRouter(claim=None, reference_nodes=[NODE1],
                                  rowcounts={'namespace_decrement': 0})
        result = self._run(router)
        self.assertTrue(result['clamped'])

    def test_duplicate_placements_release_every_node_once(self):
        router = _PlacementRouter(claim=None,
                                  reference_nodes=[NODE1, NODE2, NODE1])
        self._run(router)
        node_updates = [text for text in router.statements('UPDATE')
                        if 'UPDATE scheduler_node_capacity' in text]
        self.assertEqual(2, len(node_updates))
        # The namespace side is charged once per instance however many
        # nodes hold a reference.
        cluster_updates = [text for text in router.statements('UPDATE')
                           if 'UPDATE cluster_capacity' in text]
        self.assertEqual(1, len(cluster_updates))

    def test_malformed_uuid_is_refused(self):
        router = _PlacementRouter()
        result = self._run(router, node_uuid='not-a-uuid')
        self.assertFalse(result['success'])
        self.assertIn('malformed uuid', result['error'])


class SnapshotIsolationInvariantTestCase(_PlacementMixin,
                                         base.ShakenFistTestCase):
    """No plain SELECT may precede the first guarded UPDATE (step 6a).

    innodb_snapshot_isolation is ON by default from MariaDB 11.6.2. A
    plain SELECT inside the transaction establishes its read view early,
    and every later guarded UPDATE against a row another admission has
    since changed then aborts with ER_CHECKREAD (1020) rather than
    blocking and re-evaluating its WHERE. Phase 0's benchmark predicted
    it; phase 3's step 6 validation reproduced it as 46 of 50 concurrent
    admissions exhausting the retry budget and 500ing an instance
    create.

    These are the structural regression tests for that. The live suite's
    PlacementAdmissionConcurrencyLiveTestCase is the behavioural one,
    but it only bites against a real server with the variable ON, which
    the debian-12 CI runner does not have.
    """

    def _admit(self, **kwargs):
        args = {
            'instance_uuid': str(INST1), 'namespace': 'ci-1',
            'node_uuid': str(NODE1), 'old_node_uuid': '', 'cpus': 4,
            'memory_mb': 4096, 'disk_gb': 20, 'demand_add': 10.0,
            'target_load': 0.75, 'enforce': True,
            'placement_json': PLACEMENT_JSON,
        }
        args.update(kwargs)
        return mariadb._direct_admit_instance_placement(**args)

    def _release(self, **kwargs):
        args = {
            'instance_uuid': str(INST1), 'namespace': 'ci-1',
            'node_uuid': '', 'cpus': 4, 'memory_mb': 4096, 'disk_gb': 20,
        }
        args.update(kwargs)
        return mariadb._direct_release_instance_placement(**args)

    def _assert_opens_with_an_update(self, router):
        self.assertNotEqual(
            [], router.transactional,
            'the transaction issued no statements at all')
        self.assertTrue(
            router.transactional[0].startswith('UPDATE'),
            'the transaction opened with %r, which is not a guarded '
            'UPDATE -- see the ER_CHECKREAD invariant in mariadb.py'
            % router.transactional[0].split('\n')[0])

    def _assert_no_read_before_the_first_write(self, router):
        writes = [i for i, text in enumerate(router.transactional)
                  if not text.startswith('SELECT')]
        self.assertNotEqual([], writes)
        self.assertEqual(
            [], [text for text in router.transactional[:writes[0]]
                 if text.startswith('SELECT')])

    def test_an_unclaimed_admission_opens_with_the_cluster_update(self):
        self._call = self._admit
        router = _PlacementRouter(claim=None)
        self._run(router)
        self._assert_opens_with_an_update(router)
        self.assertIn('UPDATE cluster_capacity', router.transactional[0])

    def test_a_claimed_admission_opens_with_the_claim_update(self):
        self._call = self._admit
        router = _PlacementRouter(claim=_claim_row())
        self._run(router)
        self._assert_opens_with_an_update(router)
        self.assertIn('UPDATE namespace_claims', router.transactional[0])

    def test_a_move_opens_with_a_node_update(self):
        # A move skips the cluster/claim stage entirely, so the first
        # statement is one of the two scheduler_node_capacity writes.
        self._call = self._admit
        router = _PlacementRouter(claim=None)
        self._run(router, old_node_uuid=str(NODE2))
        self._assert_opens_with_an_update(router)
        self.assertIn('UPDATE scheduler_node_capacity',
                      router.transactional[0])

    def test_a_fully_unguarded_admission_still_opens_with_an_update(self):
        # No node capacity row and no cluster singleton: every guard
        # fails open, and the first statement is the placement attribute
        # write. Still an UPDATE, still no early read view.
        self._call = self._admit
        router = _PlacementRouter(claim=None, node_row=None,
                                  cluster_row=None)
        result = self._run(router)
        self.assertTrue(result['admitted'])
        self.assertTrue(result['unguarded'])
        self._assert_opens_with_an_update(router)

    def test_a_release_opens_with_the_floored_namespace_decrement(self):
        self._call = self._release
        router = _PlacementRouter(claim=None, reference_nodes=[NODE1])
        self._run(router)
        self._assert_opens_with_an_update(router)
        self.assertIn('UPDATE cluster_capacity', router.transactional[0])

    def test_the_probes_run_outside_the_transaction(self):
        self._call = self._admit
        router = _PlacementRouter(claim=_claim_row())
        self._run(router)
        # The branch select and both presence probes ran in autocommit.
        self.assertTrue(any('FROM namespace_claims' in text
                            for text in router.autocommit))
        self.assertTrue(any('FROM scheduler_node_capacity' in text
                            for text in router.autocommit))
        self.assertTrue(any('FROM cluster_capacity' in text
                            for text in router.autocommit))

    def test_the_reference_lookup_runs_outside_the_release_transaction(self):
        self._call = self._release
        router = _PlacementRouter(claim=None, reference_nodes=[NODE1])
        self._run(router)
        self.assertTrue(any('FROM object_references' in text
                            for text in router.autocommit))
        # The invariant is that no read establishes a read view before
        # the first guarded UPDATE, not that the transaction never
        # reads: the post-release counter read below is a read after our
        # own writes, which is the same thing admission does.
        self._assert_no_read_before_the_first_write(router)
        self.assertEqual(
            [], [text for text in router.transactional
                 if text.startswith('SELECT')
                 and 'FROM object_references' in text])

    def test_the_post_release_counter_read_is_allowed_after_our_writes(self):
        # The release half of the counter read, and safe for the same
        # reason as the admission half: the capacity row it reads is
        # locked by the decrement this transaction already issued.
        self._call = self._release
        router = _PlacementRouter(claim=None, reference_nodes=[NODE1])
        self._run(router)
        self._assert_no_read_before_the_first_write(router)
        self.assertTrue(
            any(text.startswith('SELECT')
                and 'FROM scheduler_node_capacity' in text
                for text in router.transactional))

    def test_the_post_admit_counter_read_is_allowed_after_our_writes(self):
        # Reads after our own writes are safe: those rows are locked by
        # the UPDATEs we already issued, so they cannot move under us.
        self._call = self._admit
        router = _PlacementRouter(claim=None)
        self._run(router)
        self._assert_no_read_before_the_first_write(router)
        self.assertTrue(any(text.startswith('SELECT')
                            for text in router.transactional))

    def test_a_double_release_opens_no_transaction_at_all(self):
        self._call = self._release
        router = _PlacementRouter(claim=None, reference_nodes=[])
        result = self._run(router)
        self.assertFalse(result['released'])
        self.assertEqual([], router.transactional)


class FlooredDecrementTestCase(base.ShakenFistTestCase):
    """_floored_node_decrement's three outcomes (P6)."""

    def _run(self, guarded_rowcount, clamp_rowcount):
        conn = mock.MagicMock()
        results = []
        for rowcount in (guarded_rowcount, clamp_rowcount):
            result = mock.MagicMock()
            result.rowcount = rowcount
            results.append(result)
        conn.execute.side_effect = results
        return mariadb._floored_node_decrement(conn, NODE1, 4, 4096, 20), conn

    def test_guarded_decrement_wins_the_ordinary_case(self):
        (touched, clamped), conn = self._run(1, 1)
        self.assertTrue(touched)
        self.assertFalse(clamped)
        # The clamp statement was never issued.
        self.assertEqual(1, conn.execute.call_count)

    def test_guard_miss_clamps_at_zero(self):
        (touched, clamped), conn = self._run(0, 1)
        self.assertTrue(touched)
        self.assertTrue(clamped)
        self.assertEqual(2, conn.execute.call_count)
        text, _ = _compiled(conn.execute.call_args_list[1].args[0])
        self.assertIn('greatest', text.lower())

    def test_absent_row_is_neither_touched_nor_clamped(self):
        (touched, clamped), _ = self._run(0, 0)
        self.assertFalse(touched)
        self.assertFalse(clamped)

    def test_guarded_form_tests_used_against_the_release(self):
        (_, _), conn = self._run(1, 1)
        text, _ = _compiled(conn.execute.call_args_list[0].args[0])
        self.assertIn('used_cpus >= %s', text)
        self.assertIn('used_memory_mb >= %s', text)
        self.assertIn('used_disk_gb >= %s', text)


class TransactionRetryTestCase(base.ShakenFistTestCase):
    """Which MariaDB errors re-run the whole transaction (D1)."""

    def test_transient_errnos_are_retryable(self):
        for errno in (1213, 1205, 1020):
            self.assertTrue(
                mariadb._is_retryable_transaction_error(
                    _operational_error(errno)),
                f'{errno} should be retried')

    def test_other_errnos_are_not_retryable(self):
        for errno in (1064, 1146, 1062, 2006):
            self.assertFalse(
                mariadb._is_retryable_transaction_error(
                    _operational_error(errno)),
                f'{errno} should not be retried')

    def test_an_error_with_no_orig_is_not_retryable(self):
        self.assertFalse(
            mariadb._is_retryable_transaction_error(
                OperationalError('stmt', {}, None)))

    def test_retry_runs_again_until_it_succeeds(self):
        attempts = []

        def _flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise _operational_error(1213)
            return 'done'

        with mock.patch('shakenfist.mariadb.time.sleep'):
            self.assertEqual(
                'done', mariadb._retry_transaction(_flaky, 'test'))
        self.assertEqual(3, len(attempts))

    def test_retry_gives_up_and_re_raises(self):
        with mock.patch('shakenfist.mariadb.time.sleep'):
            self.assertRaises(
                OperationalError, mariadb._retry_transaction,
                lambda: (_ for _ in ()).throw(_operational_error(1205)),
                'test')

    def test_a_non_transient_error_is_not_retried(self):
        attempts = []

        def _broken():
            attempts.append(1)
            raise _operational_error(1064)

        self.assertRaises(
            OperationalError, mariadb._retry_transaction, _broken, 'test')
        self.assertEqual(1, len(attempts))

    def test_the_lock_paths_keep_their_narrower_classification(self):
        # _retry_on_deadlock guards the cluster lock paths, where a 1205
        # means a lease genuinely could not be taken inside the caller's
        # budget. Widening it would eat the caller's acquire timeout.
        self.assertTrue(
            mariadb._is_innodb_deadlock(_operational_error(1213)))
        self.assertFalse(
            mariadb._is_innodb_deadlock(_operational_error(1205)))


class DimensionDetailTestCase(base.ShakenFistTestCase):
    """_capacity_dimension recomputes the guard comparison honestly."""

    def test_exceeded_is_the_guard_comparison(self):
        for used, requested, limit, expected in (
                (6, 4, 10, False),      # exactly fits
                (7, 4, 10, True),
                (0, 48, 48, False),
                (0, 49, 48, True)):
            detail = mariadb._capacity_dimension(
                'cpus', limit, used, requested)
            self.assertEqual(expected, detail['exceeded'],
                             f'{used}+{requested} vs {limit}')

    def test_values_are_floats_for_the_fractional_demand_dimension(self):
        detail = mariadb._capacity_dimension('demand', 12.0, 12.5, 10.0)
        self.assertIsInstance(detail['limit'], float)
        self.assertIsInstance(detail['used'], float)
        self.assertTrue(detail['exceeded'])

    def test_an_uncharged_dimension_reports_state_not_state_plus_request(self):
        # The demand dimension is the one caller: since phase 4a the
        # clause tests the node's existing state alone, so the detail
        # has to as well or it names refusals the guard did not make.
        # requested is still reported, because an operator wants to
        # know what the placement would have added.
        for used, requested, limit, charged, expected in (
                (11.0, 10.0, 12.0, True, True),
                (11.0, 10.0, 12.0, False, False),
                (12.5, 10.0, 12.0, False, True),
                (12.0, 0.5, 12.0, False, False)):
            detail = mariadb._capacity_dimension(
                'demand', limit, used, requested, charged=charged)
            self.assertEqual(
                expected, detail['exceeded'],
                f'used {used} requested {requested} limit {limit} '
                f'charged {charged}')
            self.assertEqual(float(requested), detail['requested'])

    def test_the_demand_breakdown_is_present_only_when_supplied(self):
        # The demand dimension's used is two terms that mean different
        # things -- measured load and the feedforward estimate -- so a
        # caller that has them reports them separately (issue 3913). The
        # allocation dimensions have no such split and must not grow
        # spurious zero-valued keys.
        detail = mariadb._capacity_dimension(
            'demand', 12.0, 12.5, 10.0, charged=False,
            cpu_load_1=4.0, expected_demand=8.5)
        self.assertEqual(4.0, detail['cpu_load_1'])
        self.assertEqual(8.5, detail['expected_demand'])
        self.assertEqual(detail['used'],
                         detail['cpu_load_1'] + detail['expected_demand'])

        detail = mariadb._capacity_dimension('cpus', 48.0, 46.0, 4.0)
        self.assertNotIn('cpu_load_1', detail)
        self.assertNotIn('expected_demand', detail)

    def test_shortfall_is_floored_at_zero_when_it_fits(self):
        # used + requested (46 + 4 = 50) is under the limit (100), so
        # there is no shortfall -- and, critically, not a negative one:
        # a signed value here would be headroom, which the three other
        # fields already let a reader derive.
        detail = mariadb._capacity_dimension('cpus', 100.0, 46.0, 4.0)
        self.assertFalse(detail['exceeded'])
        self.assertEqual(0.0, detail['shortfall'])

    def test_shortfall_is_the_amount_over_the_limit(self):
        # used + requested (46 + 4 = 50) is 2 over the limit (48).
        detail = mariadb._capacity_dimension('cpus', 48.0, 46.0, 4.0)
        self.assertTrue(detail['exceeded'])
        self.assertEqual(2.0, detail['shortfall'])

    def test_shortfall_never_disagrees_with_exceeded(self):
        # One dimension that fits (shortfall 0.0) and one that does not
        # (a positive shortfall), exercised together so a reader can
        # never see 'exceeded': False alongside a nonzero shortfall or
        # vice versa.
        fits = mariadb._capacity_dimension('memory_mb', 16384.0, 4096.0,
                                           4096.0)
        over = mariadb._capacity_dimension('cpus', 16.0, 15.0, 4.0)
        self.assertFalse(fits['exceeded'])
        self.assertEqual(0.0, fits['shortfall'])
        self.assertTrue(over['exceeded'])
        self.assertEqual(3.0, over['shortfall'])

    def test_shortfall_on_an_uncharged_dimension_ignores_requested(self):
        # The demand dimension's exceeded test is used > limit, not
        # used + requested > limit (phase 4a), and shortfall has to
        # track the same effective-used value or the two fields would
        # disagree about which dimension is over.
        detail = mariadb._capacity_dimension(
            'demand', 12.0, 12.5, 10.0, charged=False)
        self.assertTrue(detail['exceeded'])
        self.assertEqual(0.5, detail['shortfall'])

        detail = mariadb._capacity_dimension(
            'demand', 12.0, 11.0, 10.0, charged=False)
        self.assertFalse(detail['exceeded'])
        self.assertEqual(0.0, detail['shortfall'])


class ServicerRoundTripTestCase(base.ShakenFistTestCase):
    """The result dicts survive the trip through the proto and back.

    The keys _direct_ produces, the proto field names, and the keys the
    caller reads are kept in sync by nothing but care, so exercise both
    layers against each other.
    """

    ADMITTED = {
        'success': True, 'error': '', 'admitted': True, 'unguarded': False,
        'clamped': True, 'failing_stage': '', 'dimensions': [],
        'node_used_cpus': 10, 'node_used_memory_mb': 10240,
        'node_used_disk_gb': 53, 'node_expected_demand': 18.5,
        'claim_over_limit': False, 'claim_dimensions': [],
        # An unclaimed namespace charges the cluster singleton, so there
        # is no claim to name and the empty string is what says so.
        'claim_uuid': '',
    }
    DENIED = {
        'success': True, 'error': '', 'admitted': False, 'unguarded': False,
        'clamped': False, 'failing_stage': 'node',
        'dimensions': [
            {'dimension': 'cpus', 'limit': 48.0, 'used': 46.0,
             'requested': 4.0, 'exceeded': True, 'shortfall': 2.0},
            # The demand breakdown keys (issue 3913) ride along; the
            # cpus dimension above must come back without them, which
            # the dict-equality assertions check exactly.
            {'dimension': 'demand', 'limit': 12.0, 'used': 12.5,
             'requested': 10.0, 'exceeded': True, 'shortfall': 0.5,
             'cpu_load_1': 4.0, 'expected_demand': 8.5},
        ],
        'node_used_cpus': 0, 'node_used_memory_mb': 0,
        'node_used_disk_gb': 0, 'node_expected_demand': 0.0,
        'claim_over_limit': False, 'claim_dimensions': [],
        # A denial rolled its transaction back, so nothing was charged.
        'claim_uuid': '',
    }
    OVER_CLAIM = {
        'success': True, 'error': '', 'admitted': True, 'unguarded': False,
        'clamped': False, 'failing_stage': '', 'dimensions': [],
        'node_used_cpus': 10, 'node_used_memory_mb': 10240,
        'node_used_disk_gb': 53, 'node_expected_demand': 18.5,
        'claim_over_limit': True,
        'claim_dimensions': [
            {'dimension': 'cpus', 'limit': 8.0, 'used': 6.0,
             'requested': 4.0, 'exceeded': True, 'shortfall': 2.0},
        ],
        # Which claim was charged, so the namespace-side audit event can
        # name it after the claim itself has been deleted and recreated.
        'claim_uuid': str(CLAIM1),
    }

    def _servicer(self):
        servicer = database_main.DatabaseService.__new__(
            database_main.DatabaseService)
        servicer.monitor = mock.MagicMock()
        return servicer

    def _admit_reply(self, direct_result):
        request = database_pb2.AdmitInstancePlacementRequest(
            instance_uuid=str(INST1), namespace='ci-1',
            node_uuid=str(NODE1), cpus=4, memory_mb=4096, disk_gb=20,
            demand_add=10.0, target_load=0.75, enforce=True,
            placement_json=PLACEMENT_JSON)
        with mock.patch(
                'shakenfist.mariadb._direct_admit_instance_placement',
                return_value=direct_result):
            return self._servicer().AdmitInstancePlacement(
                request, mock.MagicMock())

    def _unpack(self, reply):
        stub = mock.MagicMock()
        with mock.patch('shakenfist.mariadb._get_database_stub',
                        return_value=stub), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           return_value=reply) as mock_call:
            unpacked = mariadb._grpc_admit_instance_placement(
                str(INST1), 'ci-1', str(NODE1), '', 4, 4096, 20, 10.0,
                0.75, True, PLACEMENT_JSON)
        return unpacked, mock_call

    def test_admitted_round_trip_preserves_every_field(self):
        unpacked, _ = self._unpack(self._admit_reply(self.ADMITTED))
        self.assertEqual(self.ADMITTED, unpacked)

    def test_denied_round_trip_preserves_the_dimension_detail(self):
        unpacked, _ = self._unpack(self._admit_reply(self.DENIED))
        self.assertEqual(self.DENIED, unpacked)

    def test_a_reply_without_the_breakdown_reads_as_absent_not_zero(self):
        # Mixed-version window: an sf-database predating the optional
        # cpu_load_1/expected_demand fields never sets them, and that
        # must unpack as "no breakdown available" rather than as a
        # breakdown of zeroes under a non-zero used -- which would read
        # as an estimator defect that is not there (issue 3913).
        reply = database_pb2.AdmitInstancePlacementReply(
            success=True, admitted=False, failing_stage='node')
        reply.dimensions.add(dimension='demand', limit=12.0, used=12.5,
                             requested=10.0, exceeded=True, shortfall=0.5)
        unpacked, _ = self._unpack(reply)
        self.assertEqual(
            [{'dimension': 'demand', 'limit': 12.0, 'used': 12.5,
              'requested': 10.0, 'exceeded': True, 'shortfall': 0.5}],
            unpacked['dimensions'])
        # Same window, same reasoning for claim_uuid: proto3's default
        # for an unset string is empty, which is exactly what "no claim
        # was charged" reads as, so an old server cannot fabricate one.
        self.assertEqual('', unpacked['claim_uuid'])

    def test_over_claim_round_trip_preserves_the_advisory_detail(self):
        # An admitted placement carrying advisory over-limit detail: the
        # two dimension lists must not bleed into each other, because
        # 'dimensions' means refused and this one was not.
        reply = self._admit_reply(self.OVER_CLAIM)
        self.assertEqual([], list(reply.dimensions))
        self.assertEqual(1, len(reply.claim_dimensions))
        unpacked, _ = self._unpack(reply)
        self.assertEqual(self.OVER_CLAIM, unpacked)

    def test_admission_uses_the_bounded_budget(self):
        # This is on the instance create hot path with a user waiting.
        _, mock_call = self._unpack(self._admit_reply(self.ADMITTED))
        self.assertEqual(mariadb.BOUNDED_QUERY_TIMEOUT,
                         mock_call.call_args.kwargs['timeout'])
        self.assertEqual(1, mock_call.call_args.kwargs['max_slow_failures'])

    def test_request_carries_the_scheduler_constants(self):
        # The database daemon needs no copy of the scheduler config.
        captured = {}

        def _capture(method, request, **kwargs):
            captured['request'] = request
            return self._admit_reply(self.ADMITTED)

        with mock.patch('shakenfist.mariadb._get_database_stub',
                        return_value=mock.MagicMock()), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           side_effect=_capture):
            mariadb._grpc_admit_instance_placement(
                str(INST1), 'ci-1', str(NODE1), '', 4, 4096, 20, 10.0,
                0.75, True, PLACEMENT_JSON)
        self.assertEqual(10.0, captured['request'].demand_add)
        self.assertEqual(0.75, captured['request'].target_load)

    def test_release_round_trip(self):
        released = {'success': True, 'error': '', 'released': True,
                    'clamped': True, 'counters_node_uuid': str(NODE1),
                    'node_used_cpus': 6, 'node_used_memory_mb': 8192,
                    'node_used_disk_gb': 40, 'node_expected_demand': 1.5}
        request = database_pb2.ReleaseInstancePlacementRequest(
            instance_uuid=str(INST1), namespace='ci-1', cpus=4,
            memory_mb=4096, disk_gb=20)
        with mock.patch(
                'shakenfist.mariadb._direct_release_instance_placement',
                return_value=released):
            reply = self._servicer().ReleaseInstancePlacement(
                request, mock.MagicMock())

        stub = mock.MagicMock()
        with mock.patch('shakenfist.mariadb._get_database_stub',
                        return_value=stub), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           return_value=reply):
            unpacked = mariadb._grpc_release_instance_placement(
                str(INST1), 'ci-1', '', 4, 4096, 20)
        self.assertEqual(released, unpacked)

    def test_servicer_swallows_an_unexpected_exception(self):
        request = database_pb2.AdmitInstancePlacementRequest(
            instance_uuid=str(INST1), namespace='ci-1',
            node_uuid=str(NODE1), placement_json=PLACEMENT_JSON)
        with mock.patch(
                'shakenfist.mariadb._direct_admit_instance_placement',
                side_effect=ValueError('boom')):
            reply = self._servicer().AdmitInstancePlacement(
                request, mock.MagicMock())
        self.assertFalse(reply.success)
        self.assertFalse(reply.admitted)

    def test_database_unavailable_is_a_failed_rpc_not_a_denial(self):
        from shakenfist import exceptions

        stub = mock.MagicMock()
        with mock.patch('shakenfist.mariadb._get_database_stub',
                        return_value=stub), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           side_effect=exceptions.DatabaseUnavailable('no')):
            result = mariadb._grpc_admit_instance_placement(
                str(INST1), 'ci-1', str(NODE1), '', 4, 4096, 20, 10.0,
                0.75, True, PLACEMENT_JSON)
        self.assertFalse(result['success'])
        self.assertFalse(result['admitted'])

    def test_both_rpcs_have_a_prometheus_counter(self):
        # The Monitor's counter list is a hand-maintained enumeration, so
        # a new RPC silently loses its metric unless it is registered.
        with mock.patch.object(database_main.daemon.WorkerPoolDaemon,
                               '__init__', return_value=None), \
                mock.patch.object(database_main, 'start_http_server'), \
                mock.patch.object(database_main, 'Gauge'), \
                mock.patch.object(database_main, 'Counter'):
            monitor = database_main.Monitor('test')
        self.assertIn('admit_instance_placement', monitor.counters)
        self.assertIn('release_instance_placement', monitor.counters)


class PublicRoutingTestCase(base.ShakenFistTestCase):
    """The public entry points route and supply the scheduler config."""

    @mock.patch('shakenfist.mariadb._grpc_admit_instance_placement')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    def test_admit_routes_to_grpc_with_derived_demand(self, _use, mock_grpc):
        with mock.patch.object(mariadb.config,
                               'SCHEDULER_DEMAND_PER_VCPU', 2.5), \
                mock.patch.object(mariadb.config,
                                  'SCHEDULER_TARGET_LOAD', 0.75):
            mariadb.admit_instance_placement(
                str(INST1), 'ci-1', str(NODE1), 4, 4096, 20,
                PLACEMENT_JSON)
        mock_grpc.assert_called_once_with(
            str(INST1), 'ci-1', str(NODE1), '', 4, 4096, 20, 10.0, 0.75,
            True, PLACEMENT_JSON)

    @mock.patch('shakenfist.mariadb._direct_admit_instance_placement')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    def test_admit_routes_to_direct(self, _use, mock_direct):
        with mock.patch.object(mariadb.config,
                               'SCHEDULER_DEMAND_PER_VCPU', 2.5), \
                mock.patch.object(mariadb.config,
                                  'SCHEDULER_TARGET_LOAD', 0.75):
            mariadb.admit_instance_placement(
                str(INST1), 'ci-1', str(NODE1), 4, 4096, 20,
                PLACEMENT_JSON, old_node_uuid=str(NODE2), enforce=False)
        mock_direct.assert_called_once_with(
            str(INST1), 'ci-1', str(NODE1), str(NODE2), 4, 4096, 20, 10.0,
            0.75, False, PLACEMENT_JSON)

    @mock.patch('shakenfist.mariadb._grpc_admit_instance_placement')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    def test_waiving_demand_sends_a_zero_target_load(self, _use, mock_grpc):
        # enforce_demand=False is expressed as a zero target load, which
        # the guard clause treats as "disabled" -- but the demand_add is
        # still sent, because the admission still accumulates the
        # placement's demand contribution for later enforced admissions
        # to see.
        with mock.patch.object(mariadb.config,
                               'SCHEDULER_DEMAND_PER_VCPU', 2.5), \
                mock.patch.object(mariadb.config,
                                  'SCHEDULER_TARGET_LOAD', 0.75):
            mariadb.admit_instance_placement(
                str(INST1), 'ci-1', str(NODE1), 4, 4096, 20,
                PLACEMENT_JSON, enforce_demand=False)
        mock_grpc.assert_called_once_with(
            str(INST1), 'ci-1', str(NODE1), '', 4, 4096, 20, 10.0, 0.0,
            True, PLACEMENT_JSON)

    @mock.patch('shakenfist.mariadb._grpc_release_instance_placement')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    def test_release_routes_to_grpc(self, _use, mock_grpc):
        mariadb.release_instance_placement(
            str(INST1), 'ci-1', 4, 4096, 20)
        mock_grpc.assert_called_once_with(
            str(INST1), 'ci-1', '', 4, 4096, 20)

    @mock.patch('shakenfist.mariadb._direct_release_instance_placement')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    def test_release_routes_to_direct(self, _use, mock_direct):
        mariadb.release_instance_placement(
            str(INST1), 'ci-1', 4, 4096, 20, node_uuid=str(NODE1))
        mock_direct.assert_called_once_with(
            str(INST1), 'ci-1', str(NODE1), 4, 4096, 20)

    @mock.patch('shakenfist.mariadb._grpc_get_scheduler_node_capacity')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    def test_get_capacity_routes_to_grpc(self, _use, mock_grpc):
        mock_grpc.return_value = mariadb.SchedulerNodeCapacityRead(
            rows=[], degraded=False)
        self.assertEqual(
            mariadb.SchedulerNodeCapacityRead(rows=[], degraded=False),
            mariadb.get_scheduler_node_capacity())
        mock_grpc.assert_called_once_with()

    @mock.patch('shakenfist.mariadb._direct_get_scheduler_node_capacity')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    def test_get_capacity_routes_to_direct(self, _use, mock_direct):
        mock_direct.return_value = mariadb.SchedulerNodeCapacityRead(
            rows=[], degraded=False)
        self.assertEqual(
            mariadb.SchedulerNodeCapacityRead(rows=[], degraded=False),
            mariadb.get_scheduler_node_capacity())
        mock_direct.assert_called_once_with()


class GetSchedulerNodeCapacityTestCase(base.ShakenFistTestCase):
    """Reading the counters back, which is what admission drew down.

    summarize_resources() publishes these numbers, so a reader which
    silently dropped or retyped a column would advertise headroom that
    admission does not agree with -- the exact disagreement deleting the
    issue-3498 stopgap was supposed to make impossible.
    """

    EXPECTED = {
        'node_uuid': str(NODE1),
        'limit_cpus': 48,
        'limit_memory_mb': 196608,
        'limit_disk_gb': 500,
        'used_cpus': 6,
        'used_memory_mb': 6144,
        'used_disk_gb': 33,
        'expected_demand': 8.5,
    }

    def _run_direct(self, rows=None, error=None):
        engine = mock.MagicMock()
        conn = mock.MagicMock()
        if error is not None:
            conn.execute.side_effect = error
        else:
            conn.execute.return_value.fetchall.return_value = rows or []
        engine.connect.return_value.__enter__.return_value = conn
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            return mariadb._direct_get_scheduler_node_capacity(), conn

    def test_every_column_is_read_and_typed(self):
        read, conn = self._run_direct(rows=[_capacity_row()])
        self.assertEqual([self.EXPECTED], read.rows)
        self.assertFalse(read.degraded)
        text, _ = _compiled(conn.execute.call_args.args[0])
        self.assertTrue(text.startswith('SELECT'))
        self.assertIn('FROM scheduler_node_capacity', text)
        # No filtering: the table is one row per schedulable hypervisor.
        self.assertNotIn('WHERE', text)

    def test_an_empty_table_reads_as_no_rows(self):
        # And is *not* degraded: a cluster the reconciler has not
        # reached yet reads this way on every schedule, so a caller
        # which treated emptiness as a failure would cry wolf on every
        # create there.
        read, _ = self._run_direct(rows=[])
        self.assertEqual([], read.rows)
        self.assertFalse(read.degraded)

    def test_a_database_error_reads_as_no_rows(self):
        # A node with no row is charged nothing and guarded by nothing,
        # so an unreadable table degrades to "nothing is counted" rather
        # than to an exception out of an admin endpoint. The swallow
        # stays; the flag beside it is how a caller tells this outcome
        # from the empty table above.
        read, _ = self._run_direct(error=_operational_error(2006))
        self.assertEqual([], read.rows)
        self.assertTrue(read.degraded)

    def _servicer(self):
        servicer = database_main.DatabaseService.__new__(
            database_main.DatabaseService)
        servicer.monitor = mock.MagicMock()
        return servicer

    def test_round_trip_through_the_rpc(self):
        with mock.patch(
                'shakenfist.mariadb._direct_get_scheduler_node_capacity',
                return_value=mariadb.SchedulerNodeCapacityRead(
                    rows=[self.EXPECTED], degraded=False)):
            reply = self._servicer().GetSchedulerNodeCapacity(
                database_pb2.GetSchedulerNodeCapacityRequest(),
                mock.MagicMock())

        with mock.patch('shakenfist.mariadb._get_database_stub',
                        return_value=mock.MagicMock()), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           return_value=reply):
            unpacked = mariadb._grpc_get_scheduler_node_capacity()
        self.assertEqual([self.EXPECTED], unpacked.rows)
        self.assertFalse(unpacked.degraded)

    def test_a_failed_rpc_reads_as_degraded(self):
        # The swallow at the client end is deliberate (issue 3586's
        # watchdog window), so the read still returns rather than
        # raising -- but it now says the counters were not read, which
        # is what the scheduler publishes against the instance.
        with mock.patch('shakenfist.mariadb._get_database_stub',
                        return_value=mock.MagicMock()), \
                mock.patch(
                    'shakenfist.mariadb._grpc_call',
                    side_effect=exceptions.DatabaseUnavailable('nope')):
            read = mariadb._grpc_get_scheduler_node_capacity()
        self.assertEqual([], read.rows)
        self.assertTrue(read.degraded)

    def test_the_capacity_read_uses_the_bounded_budget(self):
        # This read sits in front of the admission RPC on the create hot
        # path (find_candidates(), and the queues daemon preflight), so
        # it carries the same bounded budget. An unbounded read in front
        # of a bounded write reopens the issue-3586 watchdog window that
        # bounding the write closed.
        with mock.patch('shakenfist.mariadb._get_database_stub',
                        return_value=mock.MagicMock()), \
                mock.patch('shakenfist.mariadb._grpc_call') as mock_call:
            mock_call.return_value.rows = []
            mariadb._grpc_get_scheduler_node_capacity()
        self.assertEqual(mariadb.BOUNDED_QUERY_TIMEOUT,
                         mock_call.call_args.kwargs['timeout'])
        self.assertEqual(1, mock_call.call_args.kwargs['max_slow_failures'])

    def test_servicer_swallows_an_unexpected_exception(self):
        with mock.patch(
                'shakenfist.mariadb._direct_get_scheduler_node_capacity',
                side_effect=ValueError('boom')):
            reply = self._servicer().GetSchedulerNodeCapacity(
                database_pb2.GetSchedulerNodeCapacityRequest(),
                mock.MagicMock())
        self.assertEqual(0, len(reply.rows))

    def test_the_rpc_has_a_prometheus_counter(self):
        with mock.patch.object(database_main.daemon.WorkerPoolDaemon,
                               '__init__', return_value=None), \
                mock.patch.object(database_main, 'start_http_server'), \
                mock.patch.object(database_main, 'Gauge'), \
                mock.patch.object(database_main, 'Counter'):
            monitor = database_main.Monitor('test')
        self.assertIn('get_scheduler_node_capacity', monitor.counters)


class DemandOnlyDenialTestCase(base.ShakenFistTestCase):
    """CapacityAdmissionDenied.demand_only decides waiver eligibility.

    The walkers' second pass hinges on this property, so its edges are
    pinned: a denial is waivable only when the node stage refused and
    the D13 demand term was the only exceeded dimension.
    """

    def _denial(self, failing_stage, exceeded_dimensions):
        dimensions = [
            {'dimension': d, 'limit': 6.0, 'used': 9.2, 'requested': 2.5,
             'exceeded': d in exceeded_dimensions}
            for d in ('cpus', 'memory_mb', 'disk_gb', 'demand')]
        return exceptions.CapacityAdmissionDenied(
            failing_stage, dimensions)

    def test_a_demand_only_node_denial_is_waivable(self):
        self.assertTrue(self._denial('node', {'demand'}).demand_only)

    def test_a_real_dimension_makes_it_unwaivable(self):
        self.assertFalse(
            self._denial('node', {'demand', 'cpus'}).demand_only)

    def test_a_cluster_stage_denial_is_never_waivable(self):
        # The cluster and claim stages have no demand term; a 'demand'
        # dimension appearing there would be a bug, not a waiver.
        self.assertFalse(self._denial('cluster', {'demand'}).demand_only)

    def test_no_exceeded_dimension_is_not_waivable(self):
        self.assertFalse(self._denial('node', set()).demand_only)
