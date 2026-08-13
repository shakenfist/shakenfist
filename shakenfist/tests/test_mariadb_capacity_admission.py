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

from types import SimpleNamespace
from unittest import mock
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError

from shakenfist.daemons.database import main as database_main
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

    The admission transaction issues a fixed sequence of reads followed
    by a fixed sequence of guarded writes; each test says what it wants
    each of them to return and this dispatches on the compiled statement
    text. Every executed statement is recorded so the tests can assert
    on shapes as well as outcomes.
    """

    def __init__(self, claim=None, node_row=_capacity_row(),
                 cluster_row=_cluster_row(), rowcounts=None,
                 reference_nodes=None):
        self.claim = claim
        self.node_row = node_row
        self.cluster_row = cluster_row
        self.reference_nodes = reference_nodes or []
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

    def __call__(self, stmt, *args, **kwargs):
        text, _ = _compiled(stmt)
        self.executed.append((text, stmt))

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
    """Runs an admission or release against a routed mock engine."""

    def _run(self, router, **kwargs):
        engine = mock.MagicMock()
        conn = mock.MagicMock()
        conn.execute.side_effect = router
        engine.begin.return_value.__enter__.return_value = conn
        engine.connect.return_value.__enter__.return_value = conn
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

    def test_claim_guard_tests_used_against_limit(self):
        router = _PlacementRouter(claim=_claim_row())
        self._run(router)
        [update] = [text for text in router.statements('UPDATE')
                    if 'UPDATE namespace_claims' in text]
        self.assertIn('used_cpus + %s', update)
        self.assertIn('limit_cpus', update)
        self.assertIn('limit_disk_gb', update)

    def test_claim_lookup_requires_active_and_unexpired(self):
        router = _PlacementRouter(claim=None)
        self._run(router)
        [select] = [text for text in router.statements('SELECT')
                    if 'FROM namespace_claims' in text]
        self.assertIn('namespace_claims.state = %s', select)
        self.assertIn('namespace_claims.expires_at > now()', select)


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
        self.assertIsNone(mariadb._demand_guard_clause(NODE1, 10.0, 0.0))
        self.assertIsNone(mariadb._demand_guard_clause(NODE1, 10.0, -1.0))
        self.assertIsNotNone(mariadb._demand_guard_clause(NODE1, 10.0, 0.75))

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
        router = _PlacementRouter(claim=_claim_row())
        self._run(router)
        self.assertEqual([], [text for text in router.statements('UPDATE')
                              if 'UPDATE namespace_claims' in text])

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

    def test_operational_error_is_a_failed_rpc(self):
        engine = mock.MagicMock()
        conn = mock.MagicMock()
        conn.execute.side_effect = _operational_error(1064)
        engine.begin.return_value.__enter__.return_value = conn
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            result = self._call()
        self.assertFalse(result['success'])
        self.assertFalse(result['admitted'])
        self.assertIn('MariaDB error', result['error'])

    def test_integrity_error_is_a_failed_rpc(self):
        engine = mock.MagicMock()
        conn = mock.MagicMock()
        conn.execute.side_effect = IntegrityError('stmt', {}, Exception('x'))
        engine.begin.return_value.__enter__.return_value = conn
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            result = self._call()
        self.assertFalse(result['success'])


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

    def test_named_node_releases_without_consulting_references(self):
        router = _PlacementRouter(claim=None)
        result = self._run(router, node_uuid=str(NODE2))
        self.assertTrue(result['released'])
        self.assertEqual([], [text for text in router.statements('SELECT')
                              if 'FROM object_references' in text])

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
    }
    DENIED = {
        'success': True, 'error': '', 'admitted': False, 'unguarded': False,
        'clamped': False, 'failing_stage': 'node',
        'dimensions': [
            {'dimension': 'cpus', 'limit': 48.0, 'used': 46.0,
             'requested': 4.0, 'exceeded': True},
            {'dimension': 'demand', 'limit': 12.0, 'used': 12.5,
             'requested': 10.0, 'exceeded': True},
        ],
        'node_used_cpus': 0, 'node_used_memory_mb': 0,
        'node_used_disk_gb': 0, 'node_expected_demand': 0.0,
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
                    'clamped': True}
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
