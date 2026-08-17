# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Tests for the namespace claim CRUD primitive (phase 4, D3 and D8).

Covers the SQL layer of _direct_create_namespace_claim(),
_direct_update_namespace_claim(), _direct_delete_namespace_claim() and
the two reads, against mocked engines in the same style as
test_mariadb_capacity_admission.py: statement shapes are verified by
compiling what was executed, not by running it. The gRPC wrappers, the
servicer methods and the shared-aggregation invariant are covered here
too.

Execution against a real server -- where the guards actually bind, the
drawdown migration actually has to agree with the reconciler and the
driver's rowcount semantics actually matter -- is
test_mariadb_capacity_claims_live.py.
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
from shakenfist.protos import database_pb2
from shakenfist.tests import base


MYSQL_DIALECT = sa.dialects.mysql.dialect()

CLAIM1 = UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
CLAIM2 = UUID('dddddddd-dddd-dddd-dddd-dddddddddddd')


def _compiled(stmt):
    """Compile an executed statement for shape assertions."""
    compiled = stmt.compile(dialect=MYSQL_DIALECT)
    return str(compiled).strip(), compiled.params


def _operational_error(errno):
    """A SQLAlchemy OperationalError carrying a MariaDB errno."""
    return OperationalError('stmt', {}, Exception(errno, 'boom'))


def _claim_row(uuid=CLAIM1, namespace='ci-1', limit_cpus=16,
               limit_memory_mb=16384, limit_disk_gb=100, used_cpus=4,
               used_memory_mb=4096, used_disk_gb=40, state='active'):
    """A row in the shape _claim_select() projects."""
    return SimpleNamespace(
        uuid=uuid, namespace=namespace, limit_cpus=limit_cpus,
        limit_memory_mb=limit_memory_mb, limit_disk_gb=limit_disk_gb,
        used_cpus=used_cpus, used_memory_mb=used_memory_mb,
        used_disk_gb=used_disk_gb, state=state,
        expires_at=1800000000.0, updated_at=1799990000.0)


def _cluster_row(total_cpus=144, claimed_cpus=16, unclaimed_used_cpus=8):
    return SimpleNamespace(
        id=1, total_cpus=total_cpus, total_memory_mb=393216,
        total_disk_gb=1000, claimed_cpus=claimed_cpus,
        claimed_memory_mb=16384, claimed_disk_gb=100,
        unclaimed_used_cpus=unclaimed_used_cpus,
        unclaimed_used_memory_mb=8192, unclaimed_used_disk_gb=64)


class _ClaimRouter:
    """Routes a mocked connection's execute() to canned rows and rowcounts.

    Every claim transaction probes on its own connection first and then
    issues a fixed sequence of guarded writes inside a transaction, so
    statements are recorded separately per connection kind. That is what
    lets the tests assert the ER_CHECKREAD invariant directly: nothing
    inside the transaction may read before the first guarded UPDATE.
    """

    def __init__(self, drawdown=(0, 0, 0), cluster_row=_cluster_row(),
                 claim=None, claims=None, rowcounts=None):
        self.drawdown = drawdown
        self.cluster_row = cluster_row
        # The single claim row every namespace_claims SELECT returns,
        # and the list every fetchall() sees.
        self.claim = claim
        self.claims = claims if claims is not None else (
            [claim] if claim is not None else [])
        self.context = 'autocommit'
        self.transactional = []
        self.autocommit = []
        self.rowcounts = {
            'cluster_update': 1,
            'cluster_clamp': 1,
            'claim_update': 1,
            'claim_delete': 1,
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
            if 'FROM (' in text and 'used_cpus' in text:
                # The shared namespace usage aggregation.
                if self.drawdown is None:
                    return self._result()
                return self._result(rows=[SimpleNamespace(
                    used_cpus=self.drawdown[0],
                    used_memory_mb=self.drawdown[1],
                    used_disk_gb=self.drawdown[2])])
            if 'FROM namespace_claims' in text:
                return self._result(rows=list(self.claims))
            if 'FROM cluster_capacity' in text:
                if self.cluster_row is None:
                    return self._result()
                return self._result(rows=[self.cluster_row])
            return self._result()

        if text.startswith('UPDATE cluster_capacity'):
            # The delete path's clamp is the one that floors claimed_*;
            # create's GREATEST() is on the unclaimed side, so keying on
            # 'greatest' alone would confuse the two.
            key = ('cluster_clamp' if 'claimed_cpus=greatest' in text.lower()
                   else 'cluster_update')
            return self._result(rowcount=self.rowcounts[key])
        if text.startswith('UPDATE namespace_claims'):
            return self._result(rowcount=self.rowcounts['claim_update'])
        if text.startswith('DELETE FROM namespace_claims'):
            return self._result(rowcount=self.rowcounts['claim_delete'])
        if text.startswith('INSERT INTO namespace_claims'):
            return self._result(rowcount=1)
        return self._result()

    def statements(self, prefix):
        return [text for text, _ in self.executed if text.startswith(prefix)]


class _ClaimMixin:
    """Runs a claim operation against a routed mock engine."""

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


class SharedAggregationTestCase(base.ShakenFistTestCase):
    """The create-time seed and the reconcile recompute are one query.

    This is the property the whole D3 design rests on: if the two could
    disagree, a freshly created claim's counters would flap on every
    reconcile pass. It is asserted structurally here and demonstrated
    end to end by the live suite's create-then-reconcile test.
    """

    def test_both_statements_embed_the_same_aggregation(self):
        fragment = mariadb._NAMESPACE_USAGE_AGGREGATION
        self.assertIn(fragment, mariadb._RECONCILE_CLAIM_USAGE_SQL.text)
        self.assertIn(fragment, mariadb._NAMESPACE_DRAWDOWN_SQL.text)

    def test_the_aggregation_de_duplicates_the_placement_set(self):
        # A stale instance_location row must charge a namespace quota
        # once, however many nodes point at the instance.
        self.assertIn('SELECT DISTINCT r.target_uuid',
                      mariadb._NAMESPACE_USAGE_AGGREGATION)

    def test_the_drawdown_binds_the_reconcile_parameters(self):
        params = mariadb._namespace_drawdown_params('ci-1')
        for key, value in mariadb._reconcile_reference_params().items():
            self.assertEqual(value, params[key])
        self.assertEqual('ci-1', params['namespace'])

    def test_the_drawdown_restricts_by_namespace(self):
        self.assertIn('WHERE u.namespace = :namespace',
                      mariadb._NAMESPACE_DRAWDOWN_SQL.text)


class CreateNamespaceClaimTestCase(_ClaimMixin, base.ShakenFistTestCase):
    """The guarded create transaction (D3, D14)."""

    def _call(self, **kwargs):
        args = {
            'claim_uuid': str(CLAIM1), 'namespace': 'ci-1',
            'limit_cpus': 16, 'limit_memory_mb': 16384,
            'limit_disk_gb': 100, 'expires_in_seconds': 3600,
        }
        args.update(kwargs)
        return mariadb._direct_create_namespace_claim(**args)

    def test_the_transaction_opens_with_the_guarded_update(self):
        # The ER_CHECKREAD invariant, asserted rather than commented.
        router = _ClaimRouter()
        result = self._run(router)

        self.assertTrue(result['created'], result['error'])
        self.assertTrue(router.transactional[0].startswith(
            'UPDATE cluster_capacity'))

    def test_the_probes_run_outside_the_transaction(self):
        router = _ClaimRouter()
        self._run(router)

        # The drawdown, the singleton presence probe and the existing
        # claim probe are all autocommit reads.
        self.assertTrue(any('FROM (' in text for text in router.autocommit))
        self.assertTrue(any('FROM cluster_capacity' in text
                            for text in router.autocommit))
        self.assertTrue(any('FROM namespace_claims' in text
                            for text in router.autocommit))

    def test_the_writes_follow_the_canonical_order(self):
        router = _ClaimRouter()
        self._run(router)

        order = [text.split()[0] + ' ' + text.split()[1]
                 for text in router.transactional
                 if not text.startswith('SELECT')]
        self.assertEqual(['UPDATE cluster_capacity',
                          'INSERT INTO'], order[:2])

    def test_the_guard_is_d14s_migration_aware_mirror_shape(self):
        router = _ClaimRouter(drawdown=(4, 4096, 40))
        self._run(router)
        [(text, stmt)] = [(text, stmt) for text, stmt in router.executed
                          if text.startswith('UPDATE cluster_capacity')]
        # claimed + limit + GREATEST(0, unclaimed_used - migrated) <=
        # total, per dimension. The migrated term is what makes the
        # guard test the state this statement's own SET produces, rather
        # than one in which the namespace's usage is still counted on
        # the unclaimed side.
        for dimension in ('cpus', 'memory_mb', 'disk_gb'):
            self.assertIn(
                f'cluster_capacity.claimed_{dimension} + %s + '
                f'greatest(%s, cluster_capacity.unclaimed_used_{dimension} '
                f'- %s) <= cluster_capacity.total_{dimension}', text)
        # ...and the drawdown really is the value bound into it.
        bound = list(_compiled(stmt)[1].values())
        self.assertIn(4, bound)
        self.assertIn(40, bound)

    def test_the_migration_is_a_floored_decrement(self):
        router = _ClaimRouter(drawdown=(4, 4096, 40))
        self._run(router)
        [update] = [text for text in router.transactional
                    if text.startswith('UPDATE cluster_capacity')]
        self.assertIn('unclaimed_used_cpus=greatest', update.lower())

    def test_used_is_seeded_with_the_drawdown(self):
        router = _ClaimRouter(drawdown=(4, 4096, 40))
        self._run(router)
        [(_, insert)] = [(text, stmt) for text, stmt in router.executed
                         if text.startswith('INSERT INTO namespace_claims')]
        _, params = _compiled(insert)
        self.assertEqual(4, params['used_cpus'])
        self.assertEqual(4096, params['used_memory_mb'])
        self.assertEqual(40, params['used_disk_gb'])
        self.assertEqual('active', params['state'])

    def test_a_namespace_with_no_instances_seeds_zero(self):
        router = _ClaimRouter(drawdown=None)
        result = self._run(router)

        self.assertTrue(result['created'])
        [(_, insert)] = [(text, stmt) for text, stmt in router.executed
                         if text.startswith('INSERT INTO namespace_claims')]
        _, params = _compiled(insert)
        self.assertEqual(0, params['used_cpus'])

    def test_the_expiry_is_computed_server_side(self):
        router = _ClaimRouter()
        self._run(router)
        [(text, _)] = [(text, stmt) for text, stmt in router.executed
                       if text.startswith('INSERT INTO namespace_claims')]
        self.assertIn('from_unixtime(unix_timestamp()', text.lower())

    def test_a_guard_miss_is_a_capacity_refusal(self):
        router = _ClaimRouter(rowcounts={'cluster_update': 0})
        result = self._run(router)

        self.assertTrue(result['success'])
        self.assertFalse(result['created'])
        self.assertEqual('capacity', result['refused_reason'])
        # Nothing was inserted.
        self.assertEqual([], router.statements('INSERT'))

    def test_a_capacity_refusal_names_the_binding_dimensions(self):
        # total 144, claimed 16, unclaimed_used 8, so 200 cpus does not
        # fit and 16384 MB of the 393216 - 16384 - 8192 available does.
        router = _ClaimRouter(rowcounts={'cluster_update': 0})
        result = self._run(router, limit_cpus=200)

        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertTrue(detail['cpus']['exceeded'])
        self.assertEqual(128.0, detail['cpus']['limit'])
        self.assertEqual(8.0, detail['cpus']['used'])
        self.assertEqual(200.0, detail['cpus']['requested'])
        self.assertFalse(detail['memory_mb']['exceeded'])

    def test_a_missing_singleton_refuses_rather_than_failing_open(self):
        # Deliberately the opposite of P7's decision for a placement: an
        # unguarded claim is a promise against totals nothing computed.
        router = _ClaimRouter(cluster_row=None)
        result = self._run(router)

        self.assertTrue(result['success'])
        self.assertFalse(result['created'])
        self.assertEqual('no_cluster_capacity', result['refused_reason'])
        self.assertEqual([], router.transactional)

    def test_a_second_claim_for_one_namespace_is_refused(self):
        router = _ClaimRouter(claim=_claim_row())
        result = self._run(router)

        self.assertEqual('exists', result['refused_reason'])
        self.assertEqual([], router.transactional)

    def test_a_malformed_uuid_is_an_error(self):
        result = self._run(_ClaimRouter(), claim_uuid='not-a-uuid')
        self.assertFalse(result['success'])
        self.assertIn('malformed uuid', result['error'])

    def test_an_empty_namespace_is_an_error(self):
        result = self._run(_ClaimRouter(), namespace='')
        self.assertFalse(result['success'])
        self.assertIn('namespace is required', result['error'])

    def test_a_negative_limit_is_an_error(self):
        result = self._run(_ClaimRouter(), limit_disk_gb=-1)
        self.assertFalse(result['success'])
        self.assertIn('cannot be negative', result['error'])

    def test_a_non_positive_expiry_is_an_error(self):
        result = self._run(_ClaimRouter(), expires_in_seconds=0)
        self.assertFalse(result['success'])
        self.assertIn('expires_in_seconds', result['error'])

    def test_a_database_error_is_reported_not_raised(self):
        engine = mock.MagicMock()
        engine.connect.side_effect = _operational_error(2013)
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            result = mariadb._direct_create_namespace_claim(
                str(CLAIM1), 'ci-1', 4, 4096, 40, 3600)
        self.assertFalse(result['success'])
        self.assertIn('MariaDB error', result['error'])

    def test_a_deadlock_retries_the_whole_operation(self):
        # Including the probes: a transaction that lost a race must
        # re-read the world rather than re-decide on the losing view.
        router = _ClaimRouter()
        engine = mock.MagicMock()
        autocommit = mock.MagicMock()
        autocommit.execute.side_effect = router.on('autocommit')
        transaction = mock.MagicMock()
        transaction.execute.side_effect = router.on('transaction')
        engine.connect.return_value.__enter__.return_value = autocommit

        attempts = []

        def _begin():
            attempts.append(len(attempts))
            if len(attempts) == 1:
                raise _operational_error(1213)
            context = mock.MagicMock()
            context.__enter__.return_value = transaction
            return context

        engine.begin.side_effect = _begin

        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            with mock.patch('shakenfist.mariadb.time.sleep'):
                result = mariadb._direct_create_namespace_claim(
                    str(CLAIM1), 'ci-1', 4, 4096, 40, 3600)

        self.assertTrue(result['created'], result['error'])
        self.assertEqual(2, len(attempts))
        # The drawdown was probed once per attempt.
        self.assertEqual(2, len([text for text in router.autocommit
                                 if text.startswith('SELECT u.used_cpus')]))

    def test_exhausted_retries_surface_as_an_error(self):
        router = _ClaimRouter()
        engine = mock.MagicMock()
        autocommit = mock.MagicMock()
        autocommit.execute.side_effect = router.on('autocommit')
        engine.connect.return_value.__enter__.return_value = autocommit
        engine.begin.side_effect = _operational_error(1205)

        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            with mock.patch('shakenfist.mariadb.time.sleep'):
                result = mariadb._direct_create_namespace_claim(
                    str(CLAIM1), 'ci-1', 4, 4096, 40, 3600)

        self.assertFalse(result['success'])
        self.assertIn('MariaDB error', result['error'])


class UpdateNamespaceClaimTestCase(_ClaimMixin, base.ShakenFistTestCase):
    """Grow guarded, shrink floored, expiry re-dated (D8)."""

    def _call(self, **kwargs):
        args = {
            'claim_uuid': str(CLAIM1), 'fields': ['limit_cpus'],
            'limit_cpus': 32, 'limit_memory_mb': 0, 'limit_disk_gb': 0,
            'expires_in_seconds': 0,
        }
        args.update(kwargs)
        return mariadb._direct_update_namespace_claim(**args)

    def test_the_transaction_opens_with_the_guarded_update(self):
        router = _ClaimRouter(claim=_claim_row())
        result = self._run(router)

        self.assertTrue(result['updated'], result['error'])
        self.assertTrue(router.transactional[0].startswith(
            'UPDATE cluster_capacity'))

    def test_a_grow_is_guarded_against_the_cluster(self):
        router = _ClaimRouter(claim=_claim_row(limit_cpus=16))
        self._run(router, limit_cpus=32)
        [update] = [text for text in router.transactional
                    if text.startswith('UPDATE cluster_capacity')]
        self.assertIn(
            'cluster_capacity.claimed_cpus + %s + greatest(%s, '
            'cluster_capacity.unclaimed_used_cpus - %s) <= '
            'cluster_capacity.total_cpus', update)

    def test_a_grow_migrates_no_drawdown(self):
        # The guard's migrated term is zero for a grow, and must stay
        # zero: the namespace's usage is already counted in this claim's
        # used_*, so subtracting a drawdown from the cluster's unclaimed
        # sums here would credit the same capacity twice and grant a
        # claim the cluster cannot keep. This test exists so that a
        # later unification of the create and grow guards which hands
        # grow a non-zero drawdown is caught rather than shipped.
        node_drawdown = (4, 4096, 40)
        router = _ClaimRouter(drawdown=node_drawdown,
                              claim=_claim_row(limit_cpus=16))
        self._run(router, limit_cpus=32)
        [(_, stmt)] = [(text, stmt) for text, stmt in router.executed
                       if text.startswith('UPDATE cluster_capacity')]
        bound = list(_compiled(stmt)[1].values())
        # The delta is bound, the drawdown is not, and the GREATEST()
        # floor is against a literal zero.
        self.assertIn(16, bound)
        for migrated in node_drawdown:
            self.assertNotIn(migrated, bound)
        # A grow never touches unclaimed_used_* at all.
        [update] = [text for text in router.transactional
                    if text.startswith('UPDATE cluster_capacity')]
        self.assertNotIn('unclaimed_used_cpus=', update)

    def test_a_grow_the_cluster_refuses_leaves_the_claim_alone(self):
        router = _ClaimRouter(claim=_claim_row(limit_cpus=16),
                              rowcounts={'cluster_update': 0})
        result = self._run(router, limit_cpus=128)

        self.assertFalse(result['updated'])
        self.assertEqual('capacity', result['refused_reason'])
        self.assertEqual([], [text for text in router.transactional
                              if text.startswith('UPDATE namespace_claims')])

    def test_a_capacity_refusal_is_denominated_in_the_delta(self):
        # Growing 16 -> 130 asks the cluster for 114 more, not 130.
        router = _ClaimRouter(claim=_claim_row(limit_cpus=16),
                              rowcounts={'cluster_update': 0})
        result = self._run(router, limit_cpus=130)

        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(114.0, detail['cpus']['requested'])

    def test_a_shrink_carries_the_usage_floor(self):
        router = _ClaimRouter(claim=_claim_row(limit_cpus=16, used_cpus=8))
        self._run(router, limit_cpus=8)
        [update] = [text for text in router.transactional
                    if text.startswith('UPDATE namespace_claims')]
        self.assertIn('namespace_claims.used_cpus <= %s', update)

    def test_a_shrink_decrements_claimed_and_never_below_zero(self):
        router = _ClaimRouter(claim=_claim_row(limit_cpus=16, used_cpus=8))
        self._run(router, limit_cpus=8)
        [(text, stmt)] = [(text, stmt) for text, stmt in router.executed
                          if text.startswith('UPDATE cluster_capacity')]
        # A shrink is a negative delta added to claimed_*, floored by a
        # guard that will not let the singleton go below zero.
        self.assertIn('claimed_cpus=(cluster_capacity.claimed_cpus + %s)',
                      text)
        self.assertIn('claimed_cpus >= %s', text)
        self.assertIn(-8, _compiled(stmt)[1].values())

    def test_a_shrink_below_usage_is_refused_with_detail(self):
        # The claim UPDATE matches nothing and a fresh read shows the
        # usage the shrink could not get under.
        router = _ClaimRouter(claim=_claim_row(limit_cpus=16, used_cpus=12),
                              rowcounts={'claim_update': 0})
        result = self._run(router, limit_cpus=4)

        self.assertFalse(result['updated'])
        self.assertEqual('below_usage', result['refused_reason'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertTrue(detail['cpus']['exceeded'])
        self.assertEqual(4.0, detail['cpus']['limit'])
        self.assertEqual(12.0, detail['cpus']['used'])

    def test_a_stale_grow_retries_rather_than_reporting_below_usage(self):
        # An advisory over-limit claim -- limit 4, usage 10, which this
        # release permits -- being *grown* to 8. The claim UPDATE misses,
        # standing in for a concurrent writer moving the row.
        #
        # The shrink floor was never applied to cpus, because cpus is not
        # being shrunk, so it cannot be what blocked this. Diagnosing it
        # as below_usage would hand the operator a durable 409 saying a
        # claim cannot be shrunk below its usage, for a request that was
        # a grow, and would swallow the retry instead of taking it.
        router = _ClaimRouter(claim=_claim_row(limit_cpus=4, used_cpus=10),
                              rowcounts={'claim_update': 0})
        result = self._run(router, limit_cpus=8)

        self.assertFalse(result['updated'])
        self.assertEqual([], result['dimensions'])

        # Having declined to short circuit, it retried until the budget
        # ran out and reported the contention as what it was. 'conflict'
        # is a 503 the operator can retry; 'below_usage' would have been
        # a durable 409 telling them to stop.
        self.assertEqual(
            'conflict', result['refused_reason'],
            'a grow was diagnosed against a floor that never ran')

    def test_a_stale_shrink_still_reports_below_usage(self):
        # The other half of the same branch, so restricting it to
        # shrinking dimensions cannot silently disable the diagnosis.
        router = _ClaimRouter(claim=_claim_row(limit_cpus=16, used_cpus=12),
                              rowcounts={'claim_update': 0})
        result = self._run(router, limit_cpus=4)

        self.assertEqual('below_usage', result['refused_reason'])

    def test_one_request_may_grow_one_dimension_and_shrink_another(self):
        router = _ClaimRouter(
            claim=_claim_row(limit_cpus=16, limit_disk_gb=100,
                             used_disk_gb=20))
        result = self._run(
            router, fields=['limit_cpus', 'limit_disk_gb'],
            limit_cpus=32, limit_disk_gb=40)

        self.assertTrue(result['updated'], result['error'])
        [cluster] = [text for text in router.transactional
                     if text.startswith('UPDATE cluster_capacity')]
        # cpus grew, so it is guarded; disk shrank, so it is floored.
        self.assertIn('claimed_cpus + %s', cluster)
        self.assertIn('claimed_disk_gb >= %s', cluster)
        [claim] = [text for text in router.transactional
                   if text.startswith('UPDATE namespace_claims')]
        self.assertIn('namespace_claims.used_disk_gb <= %s', claim)
        self.assertNotIn('namespace_claims.used_cpus <= %s', claim)

    def test_an_unnamed_field_is_not_written(self):
        router = _ClaimRouter(claim=_claim_row())
        self._run(router, fields=['limit_cpus'], limit_cpus=32,
                  limit_disk_gb=1)
        [(text, stmt)] = [(text, stmt) for text, stmt in router.executed
                          if text.startswith('UPDATE namespace_claims')]
        self.assertIn('limit_cpus=%s', text)
        self.assertNotIn('limit_disk_gb=%s', text)

    def test_an_expiry_only_change_touches_no_counter(self):
        router = _ClaimRouter(claim=_claim_row())
        result = self._run(router, fields=['expires_in_seconds'],
                           expires_in_seconds=7200)

        self.assertTrue(result['updated'], result['error'])
        self.assertEqual([], [text for text in router.transactional
                              if text.startswith('UPDATE cluster_capacity')])
        [update] = [text for text in router.transactional
                    if text.startswith('UPDATE namespace_claims')]
        self.assertIn('from_unixtime(unix_timestamp()', update.lower())

    def test_an_expiry_only_change_survives_a_missing_singleton(self):
        router = _ClaimRouter(claim=_claim_row(), cluster_row=None)
        result = self._run(router, fields=['expires_in_seconds'],
                           expires_in_seconds=7200)
        self.assertTrue(result['updated'], result['error'])

    def test_a_grow_with_no_singleton_is_refused(self):
        router = _ClaimRouter(claim=_claim_row(), cluster_row=None)
        result = self._run(router, limit_cpus=32)

        self.assertFalse(result['updated'])
        self.assertEqual('no_cluster_capacity', result['refused_reason'])
        self.assertEqual([], router.transactional)

    def test_an_absent_claim_is_not_found(self):
        router = _ClaimRouter(claim=None)
        result = self._run(router)
        self.assertEqual('not_found', result['refused_reason'])

    def test_an_expired_claim_is_not_updatable(self):
        # Its limits are not in claimed_*, so adjusting that counter on
        # its behalf would adjust it for capacity nobody holds.
        router = _ClaimRouter(claim=_claim_row(state='expired'))
        result = self._run(router)
        self.assertEqual('not_active', result['refused_reason'])
        self.assertEqual([], router.transactional)

    def test_the_claim_update_guards_the_limits_it_computed_from(self):
        router = _ClaimRouter(claim=_claim_row(limit_cpus=16))
        self._run(router, limit_cpus=32)
        [update] = [text for text in router.transactional
                    if text.startswith('UPDATE namespace_claims')]
        self.assertIn('namespace_claims.limit_cpus = %s', update)
        self.assertIn('namespace_claims.limit_memory_mb = %s', update)

    def test_a_row_that_never_stops_moving_reports_conflict(self):
        # The claim UPDATE always misses and the fresh read shows the
        # shrink floor was not what blocked it.
        router = _ClaimRouter(claim=_claim_row(used_cpus=0),
                              rowcounts={'claim_update': 0})
        result = self._run(router, limit_cpus=32)

        self.assertTrue(result['success'])
        self.assertFalse(result['updated'])
        self.assertEqual('conflict', result['refused_reason'])

    def test_an_unknown_field_is_an_error(self):
        result = self._run(_ClaimRouter(), fields=['limit_gpus'])
        self.assertFalse(result['success'])
        self.assertIn('unknown claim fields', result['error'])

    def test_an_empty_field_mask_is_an_error(self):
        result = self._run(_ClaimRouter(), fields=[])
        self.assertFalse(result['success'])
        self.assertIn('fields is required', result['error'])

    def test_a_negative_limit_is_an_error(self):
        result = self._run(_ClaimRouter(), limit_cpus=-1)
        self.assertFalse(result['success'])
        self.assertIn('cannot be negative', result['error'])


class DeleteNamespaceClaimTestCase(_ClaimMixin, base.ShakenFistTestCase):
    """The floored mirror of creation."""

    def _call(self, **kwargs):
        args = {'claim_uuid': str(CLAIM1)}
        args.update(kwargs)
        return mariadb._direct_delete_namespace_claim(**args)

    def test_the_transaction_opens_with_the_guarded_update(self):
        router = _ClaimRouter(claim=_claim_row())
        result = self._run(router)

        self.assertTrue(result['deleted'], result['error'])
        self.assertTrue(router.transactional[0].startswith(
            'UPDATE cluster_capacity'))

    def test_capacity_goes_back_to_the_unclaimed_side(self):
        router = _ClaimRouter(claim=_claim_row(
            limit_cpus=16, used_cpus=4, used_memory_mb=4096, used_disk_gb=40))
        result = self._run(router)

        [update] = [text for text in router.transactional
                    if text.startswith('UPDATE cluster_capacity')]
        self.assertIn('claimed_cpus - %s', update)
        self.assertIn('unclaimed_used_cpus + %s', update)
        self.assertEqual(4, result['returned_cpus'])
        self.assertEqual(4096, result['returned_memory_mb'])
        self.assertEqual(40, result['returned_disk_gb'])

    def test_the_claimed_decrement_clamps_rather_than_going_negative(self):
        router = _ClaimRouter(claim=_claim_row(),
                              rowcounts={'cluster_update': 0})
        result = self._run(router)

        self.assertTrue(result['deleted'])
        self.assertTrue(result['clamped'])
        clamp = [text for text in router.transactional
                 if text.startswith('UPDATE cluster_capacity')][1]
        self.assertIn('greatest', clamp.lower())

    def test_a_missing_claim_is_a_harmless_no_op(self):
        router = _ClaimRouter(claim=None)
        result = self._run(router)

        self.assertTrue(result['success'])
        self.assertFalse(result['deleted'])
        self.assertEqual(0, result['returned_cpus'])
        self.assertEqual([], router.transactional)

    def test_an_expired_claim_returns_nothing_to_the_cluster(self):
        # Its limits already left claimed_* and its namespace's usage is
        # already in unclaimed_used_*, so migrating either would double.
        router = _ClaimRouter(claim=_claim_row(state='expired'))
        result = self._run(router)

        self.assertTrue(result['deleted'])
        self.assertEqual(0, result['returned_cpus'])
        self.assertEqual([], [text for text in router.transactional
                              if text.startswith('UPDATE cluster_capacity')])

    def test_the_delete_guards_the_counters_it_credited_from(self):
        router = _ClaimRouter(claim=_claim_row())
        self._run(router)
        [delete] = [text for text in router.transactional
                    if text.startswith('DELETE FROM namespace_claims')]
        self.assertIn('namespace_claims.used_cpus = %s', delete)
        self.assertIn('namespace_claims.limit_cpus = %s', delete)

    def test_a_row_that_never_stops_moving_is_an_error(self):
        router = _ClaimRouter(claim=_claim_row(),
                              rowcounts={'claim_delete': 0})
        result = self._run(router)

        self.assertFalse(result['success'])
        self.assertIn('kept changing', result['error'])

    def test_a_malformed_uuid_is_an_error(self):
        result = self._run(_ClaimRouter(), claim_uuid='not-a-uuid')
        self.assertFalse(result['success'])
        self.assertIn('malformed uuid', result['error'])


class ReadNamespaceClaimsTestCase(_ClaimMixin, base.ShakenFistTestCase):
    """The two reads, and their filter pushdown."""

    def _call(self, **kwargs):
        return mariadb._direct_get_namespace_claims(**kwargs)

    def test_a_namespace_filter_is_pushed_down_to_sql(self):
        router = _ClaimRouter(claims=[_claim_row()])
        rows = self._run(router, namespace='ci-1')

        self.assertEqual(1, len(rows))
        [select] = [text for text in router.autocommit
                    if 'FROM namespace_claims' in text]
        self.assertIn('WHERE namespace_claims.namespace = %s', select)

    def test_no_filter_lists_everything(self):
        router = _ClaimRouter(claims=[_claim_row(), _claim_row(CLAIM2)])
        rows = self._run(router, namespace='')

        self.assertEqual(2, len(rows))
        [select] = [text for text in router.autocommit
                    if 'FROM namespace_claims' in text]
        self.assertNotIn('WHERE', select)

    def test_timestamps_are_converted_server_side(self):
        router = _ClaimRouter(claims=[_claim_row()])
        self._run(router, namespace='')
        [select] = [text for text in router.autocommit
                    if 'FROM namespace_claims' in text]
        self.assertIn('unix_timestamp(namespace_claims.expires_at)',
                      select.lower())

    def test_one_claim_is_read_by_primary_key(self):
        router = _ClaimRouter(claims=[_claim_row()])
        engine = mock.MagicMock()
        autocommit = mock.MagicMock()
        autocommit.execute.side_effect = router.on('autocommit')
        engine.connect.return_value.__enter__.return_value = autocommit
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            row = mariadb._direct_get_namespace_claim(str(CLAIM1))

        self.assertEqual(str(CLAIM1), row['uuid'])
        self.assertEqual('active', row['state'])
        self.assertEqual(1800000000.0, row['expires_at'])

    def test_a_malformed_uuid_reads_as_absent(self):
        self.assertIsNone(mariadb._direct_get_namespace_claim('nope'))

    def _unreadable(self):
        """An engine whose every query fails the way a sick database does."""
        engine = mock.MagicMock()
        engine.connect.side_effect = OperationalError('SELECT', {}, Exception())
        return mock.patch('shakenfist.mariadb._get_engine',
                          return_value=engine)

    def test_an_unreadable_listing_raises_rather_than_reading_as_empty(self):
        # An empty list means "no claims", and Namespace.hard_delete()
        # deletes the namespace on the strength of it. Answering "none"
        # for a database that merely fell over would strand the claim
        # row holding cluster capacity with no namespace to explain it,
        # and nothing repairs that pairing.
        with self._unreadable():
            self.assertRaises(
                exceptions.DatabaseUnavailable,
                mariadb._direct_get_namespace_claims, 'ci-1')

    def test_an_unreadable_point_read_raises_rather_than_absent(self):
        with self._unreadable():
            self.assertRaises(
                exceptions.DatabaseUnavailable,
                mariadb._direct_get_namespace_claim, str(CLAIM1))


class ClaimGrpcWrapperTestCase(base.ShakenFistTestCase):
    """The gRPC layer, including its deliberate lack of a bounded budget."""

    def setUp(self):
        super().setUp()
        self.stub = mock.MagicMock()
        patcher = mock.patch('shakenfist.mariadb._get_database_stub',
                             return_value=self.stub)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _proto_claim(self):
        return database_pb2.NamespaceClaim(
            uuid=str(CLAIM1), namespace='ci-1', limit_cpus=16,
            limit_memory_mb=16384, limit_disk_gb=100, used_cpus=4,
            used_memory_mb=4096, used_disk_gb=40, state='active',
            expires_at=1800000000.0, updated_at=1799990000.0)

    def test_claim_calls_use_the_default_budget(self):
        # Claim CRUD is operator driven: it is not on the instance
        # create path and no daemon pets a watchdog behind it, so the
        # bounded budget the admission calls need would only make an
        # operator's write fail early for no gain (issue 3586).
        reply = database_pb2.CreateNamespaceClaimReply(
            success=True, created=True)
        reply.claim.CopyFrom(self._proto_claim())
        with mock.patch('shakenfist.mariadb._grpc_call',
                        return_value=reply) as call:
            mariadb._grpc_create_namespace_claim(
                str(CLAIM1), 'ci-1', 16, 16384, 100, 3600)
        _, kwargs = call.call_args
        self.assertNotIn('timeout', kwargs)
        self.assertNotIn('max_slow_failures', kwargs)

    def test_create_reply_is_unpacked(self):
        reply = database_pb2.CreateNamespaceClaimReply(
            success=True, created=True)
        reply.claim.CopyFrom(self._proto_claim())
        reply.dimensions.add(dimension='cpus', limit=128.0, used=8.0,
                             requested=200.0, exceeded=True)
        with mock.patch('shakenfist.mariadb._grpc_call', return_value=reply):
            result = mariadb._grpc_create_namespace_claim(
                str(CLAIM1), 'ci-1', 16, 16384, 100, 3600)

        self.assertTrue(result['created'])
        self.assertEqual(str(CLAIM1), result['claim']['uuid'])
        self.assertEqual(4, result['claim']['used_cpus'])
        self.assertTrue(result['dimensions'][0]['exceeded'])

    def test_a_refusal_carries_no_claim(self):
        reply = database_pb2.CreateNamespaceClaimReply(
            success=True, created=False, refused_reason='capacity')
        with mock.patch('shakenfist.mariadb._grpc_call', return_value=reply):
            result = mariadb._grpc_create_namespace_claim(
                str(CLAIM1), 'ci-1', 16, 16384, 100, 3600)

        self.assertIsNone(result['claim'])
        self.assertEqual('capacity', result['refused_reason'])

    def test_update_sends_the_field_mask(self):
        reply = database_pb2.UpdateNamespaceClaimReply(
            success=True, updated=True)
        reply.claim.CopyFrom(self._proto_claim())
        with mock.patch('shakenfist.mariadb._grpc_call',
                        return_value=reply) as call:
            result = mariadb._grpc_update_namespace_claim(
                str(CLAIM1), ['limit_cpus'], 32, 0, 0, 0)

        self.assertTrue(result['updated'])
        _, request = call.call_args[0]
        self.assertEqual(['limit_cpus'], list(request.fields))
        self.assertEqual(32, request.limit_cpus)
        self.assertEqual(str(CLAIM1), request.uuid)

    def test_delete_reply_is_unpacked(self):
        reply = database_pb2.DeleteNamespaceClaimReply(
            success=True, deleted=True, returned_cpus=4,
            returned_memory_mb=4096, returned_disk_gb=40, clamped=True)
        with mock.patch('shakenfist.mariadb._grpc_call', return_value=reply):
            result = mariadb._grpc_delete_namespace_claim(str(CLAIM1))

        self.assertTrue(result['deleted'])
        self.assertEqual(4096, result['returned_memory_mb'])
        self.assertTrue(result['clamped'])

    def test_a_write_reports_an_unreachable_database(self):
        with mock.patch('shakenfist.mariadb._grpc_call',
                        side_effect=exceptions.DatabaseUnavailable('gone')):
            result = mariadb._grpc_create_namespace_claim(
                str(CLAIM1), 'ci-1', 16, 16384, 100, 3600)
        self.assertFalse(result['success'])
        self.assertIn('database unavailable', result['error'])

    def test_a_read_raises_rather_than_reading_as_absent(self):
        # CLAUDE.md's rule for this layer: a None return has to mean the
        # claim genuinely is not there.
        with mock.patch('shakenfist.mariadb._grpc_call',
                        side_effect=exceptions.DatabaseUnavailable('gone')):
            self.assertRaises(
                exceptions.DatabaseUnavailable,
                mariadb._grpc_get_namespace_claim, str(CLAIM1))
            self.assertRaises(
                exceptions.DatabaseUnavailable,
                mariadb._grpc_get_namespace_claims, 'ci-1')

    def test_a_missing_claim_reads_as_none(self):
        reply = database_pb2.GetNamespaceClaimReply(found=False)
        with mock.patch('shakenfist.mariadb._grpc_call', return_value=reply):
            self.assertIsNone(
                mariadb._grpc_get_namespace_claim(str(CLAIM1)))

    def test_a_list_is_unpacked(self):
        reply = database_pb2.GetNamespaceClaimsReply()
        reply.claims.append(self._proto_claim())
        with mock.patch('shakenfist.mariadb._grpc_call', return_value=reply):
            rows = mariadb._grpc_get_namespace_claims('ci-1')
        self.assertEqual([str(CLAIM1)], [r['uuid'] for r in rows])


class ClaimServicerTestCase(base.ShakenFistTestCase):
    """The database daemon's side of the five RPCs."""

    def setUp(self):
        super().setUp()
        self.servicer = database_main.DatabaseService.__new__(
            database_main.DatabaseService)
        self.servicer.monitor = mock.MagicMock()
        self.context = mock.MagicMock()

    def _claim(self):
        return {
            'uuid': str(CLAIM1), 'namespace': 'ci-1', 'limit_cpus': 16,
            'limit_memory_mb': 16384, 'limit_disk_gb': 100,
            'used_cpus': 4, 'used_memory_mb': 4096, 'used_disk_gb': 40,
            'state': 'active', 'expires_at': 1800000000.0,
            'updated_at': 1799990000.0}

    def test_create_returns_the_claim(self):
        with mock.patch(
                'shakenfist.mariadb._direct_create_namespace_claim',
                return_value={
                    'success': True, 'error': '', 'created': True,
                    'refused_reason': '', 'dimensions': [],
                    'claim': self._claim()}):
            reply = self.servicer.CreateNamespaceClaim(
                database_pb2.CreateNamespaceClaimRequest(
                    uuid=str(CLAIM1), namespace='ci-1', limit_cpus=16,
                    limit_memory_mb=16384, limit_disk_gb=100,
                    expires_in_seconds=3600), self.context)

        self.assertTrue(reply.created)
        self.assertEqual(str(CLAIM1), reply.claim.uuid)
        self.assertEqual(4, reply.claim.used_cpus)

    def test_a_refusal_carries_its_dimensions(self):
        with mock.patch(
                'shakenfist.mariadb._direct_create_namespace_claim',
                return_value={
                    'success': True, 'error': '', 'created': False,
                    'refused_reason': 'capacity',
                    'dimensions': [{'dimension': 'cpus', 'limit': 128.0,
                                    'used': 8.0, 'requested': 200.0,
                                    'exceeded': True}],
                    'claim': None}):
            reply = self.servicer.CreateNamespaceClaim(
                database_pb2.CreateNamespaceClaimRequest(uuid=str(CLAIM1)),
                self.context)

        self.assertFalse(reply.created)
        self.assertEqual('capacity', reply.refused_reason)
        self.assertFalse(reply.HasField('claim'))
        self.assertTrue(reply.dimensions[0].exceeded)

    def test_an_exception_is_a_refusal_shaped_reply(self):
        with mock.patch(
                'shakenfist.mariadb._direct_create_namespace_claim',
                side_effect=Exception('boom')):
            reply = self.servicer.CreateNamespaceClaim(
                database_pb2.CreateNamespaceClaimRequest(uuid=str(CLAIM1)),
                self.context)
        self.assertFalse(reply.success)
        self.assertFalse(reply.created)

    def test_get_reports_a_missing_claim(self):
        with mock.patch('shakenfist.mariadb._direct_get_namespace_claim',
                        return_value=None):
            reply = self.servicer.GetNamespaceClaim(
                database_pb2.GetNamespaceClaimRequest(uuid=str(CLAIM1)),
                self.context)
        self.assertFalse(reply.found)

    def test_list_passes_the_namespace_through(self):
        with mock.patch('shakenfist.mariadb._direct_get_namespace_claims',
                        return_value=[self._claim()]) as direct:
            reply = self.servicer.GetNamespaceClaims(
                database_pb2.GetNamespaceClaimsRequest(namespace='ci-1'),
                self.context)
        direct.assert_called_once_with('ci-1')
        self.assertEqual(1, len(reply.claims))

    def test_a_failed_get_sets_a_status_code(self):
        # found=False on its own is a well formed "no such claim", which
        # the client wrapper has no way to see through. The status code
        # is the only thing that makes it raise instead.
        with mock.patch('shakenfist.mariadb._direct_get_namespace_claim',
                        side_effect=Exception('database on fire')):
            reply = self.servicer.GetNamespaceClaim(
                database_pb2.GetNamespaceClaimRequest(uuid=str(CLAIM1)),
                self.context)

        self.assertFalse(reply.found)
        self.context.set_code.assert_called_once_with(
            grpc.StatusCode.INTERNAL)

    def test_a_failed_list_sets_a_status_code(self):
        with mock.patch('shakenfist.mariadb._direct_get_namespace_claims',
                        side_effect=Exception('database on fire')):
            reply = self.servicer.GetNamespaceClaims(
                database_pb2.GetNamespaceClaimsRequest(namespace='ci-1'),
                self.context)

        self.assertEqual(0, len(reply.claims))
        self.context.set_code.assert_called_once_with(
            grpc.StatusCode.INTERNAL)

    def test_update_passes_the_field_mask_through(self):
        with mock.patch(
                'shakenfist.mariadb._direct_update_namespace_claim',
                return_value={
                    'success': True, 'error': '', 'updated': True,
                    'refused_reason': '', 'dimensions': [],
                    'claim': self._claim()}) as direct:
            reply = self.servicer.UpdateNamespaceClaim(
                database_pb2.UpdateNamespaceClaimRequest(
                    uuid=str(CLAIM1), limit_cpus=32,
                    fields=['limit_cpus']), self.context)

        direct.assert_called_once_with(
            str(CLAIM1), ['limit_cpus'], 32, 0, 0, 0)
        self.assertTrue(reply.updated)

    def test_delete_reports_what_it_returned(self):
        with mock.patch(
                'shakenfist.mariadb._direct_delete_namespace_claim',
                return_value={
                    'success': True, 'error': '', 'deleted': True,
                    'returned_cpus': 4, 'returned_memory_mb': 4096,
                    'returned_disk_gb': 40, 'clamped': False}):
            reply = self.servicer.DeleteNamespaceClaim(
                database_pb2.DeleteNamespaceClaimRequest(uuid=str(CLAIM1)),
                self.context)

        self.assertTrue(reply.deleted)
        self.assertEqual(40, reply.returned_disk_gb)


class ClaimPublicDispatchTestCase(base.ShakenFistTestCase):
    """The public wrappers pick the right layer."""

    def test_direct_when_there_is_no_database_service(self):
        with mock.patch('shakenfist.mariadb._use_database_service',
                        return_value=False):
            with mock.patch(
                    'shakenfist.mariadb._direct_create_namespace_claim',
                    return_value=None) as direct:
                mariadb.create_namespace_claim(
                    str(CLAIM1), 'ci-1', 16, 16384, 100, 3600)
        direct.assert_called_once()

    def test_grpc_when_there_is_one(self):
        with mock.patch('shakenfist.mariadb._use_database_service',
                        return_value=True):
            for public, wrapper, args in (
                    (mariadb.create_namespace_claim,
                     '_grpc_create_namespace_claim',
                     (str(CLAIM1), 'ci-1', 16, 16384, 100, 3600)),
                    (mariadb.update_namespace_claim,
                     '_grpc_update_namespace_claim',
                     (str(CLAIM1), ['limit_cpus'])),
                    (mariadb.delete_namespace_claim,
                     '_grpc_delete_namespace_claim', (str(CLAIM1),)),
                    (mariadb.get_namespace_claim,
                     '_grpc_get_namespace_claim', (str(CLAIM1),)),
                    (mariadb.get_namespace_claims,
                     '_grpc_get_namespace_claims', ())):
                with mock.patch(f'shakenfist.mariadb.{wrapper}',
                                return_value=None) as grpc_wrapper:
                    public(*args)
                grpc_wrapper.assert_called_once()
