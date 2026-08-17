# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Tests for the scheduler capacity reconciler (phase 2, D5).

Covers the pure limit-derivation and demand-decay helpers (including
scheduler-parity cases mirroring the arithmetic in scheduler.py's
_has_sufficient_cpu/_ram/_disk), the disk_spec reference aggregation,
and the SQL layer of _direct_reconcile_scheduler_capacity() against
mocked engines in the style of test_mariadb_node_metrics_schema.py:
statement shapes are verified by compiling what was executed, not by
running it (step 4 of the plan validates execution against a real
MariaDB).
"""

import math
from types import SimpleNamespace
from unittest import mock
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from shakenfist.constants import GiB
from shakenfist.daemons.database import main as database_main
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.protos import database_pb2
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.tests import base


MYSQL_DIALECT = sa.dialects.mysql.dialect()


def _compiled(stmt):
    """Compile an executed statement for shape assertions."""
    compiled = stmt.compile(dialect=MYSQL_DIALECT)
    return str(compiled), compiled.params


class LimitDerivationTestCase(base.ShakenFistTestCase):
    """_derive_cpu_memory_limits() mirrors scheduler.py arithmetic."""

    def test_cpu_limit_matches_scheduler_bound(self):
        # scheduler.py _has_sufficient_cpu admits while
        # current_cpu + cpus <= cpu_schedulable * CPU_OVERCOMMIT_RATIO.
        cpu_schedulable = 14
        ratio = 3.0
        limit_cpus, _ = mariadb._derive_cpu_memory_limits(
            cpu_schedulable, None, None, ratio, 3.0)
        self.assertEqual(42, limit_cpus)

        # Guard parity at the boundary: the phase 3 guard
        # used + requested <= limit admits exactly what the scheduler's
        # current + requested > hard_max rejects.
        hard_max = cpu_schedulable * ratio
        for current, requested in ((40, 2), (40, 3), (0, 42), (0, 43)):
            scheduler_admits = not (current + requested > hard_max)
            guard_admits = current + requested <= limit_cpus
            self.assertEqual(scheduler_admits, guard_admits,
                             f'divergence at {current}+{requested}')

    def test_cpu_limit_floors_fractional_ratio(self):
        limit_cpus, _ = mariadb._derive_cpu_memory_limits(
            5, None, None, 2.5, 3.0)
        self.assertEqual(12, limit_cpus)

    def test_memory_limit_matches_scheduler_overcommit_bound(self):
        # scheduler.py _has_sufficient_ram rejects when
        # instance_memory / memory_max > RAM_OVERCOMMIT_RATIO. The
        # ledger form floors the product and subtracts the node's
        # published reservation (the documented deviation: allocated
        # MB, not KSM-aware actuals).
        _, limit_memory_mb = mariadb._derive_cpu_memory_limits(
            None, 64243, 4096, 3.0, 3.0)
        self.assertEqual(math.floor(64243 * 3.0) - 4096, limit_memory_mb)
        self.assertEqual(188633, limit_memory_mb)

    def test_memory_limit_floors_fractional_ratio(self):
        _, limit_memory_mb = mariadb._derive_cpu_memory_limits(
            None, 3, 1, 3.0, 1.5)
        self.assertEqual(3, limit_memory_mb)

    def test_memory_limit_clamps_at_zero(self):
        # A published reservation exceeding the overcommit ceiling
        # (RAM_OVERCOMMIT_RATIO at or below 1.0 with a large
        # NODE_MEMORY_RESERVATION_MB) means the node admits nothing.
        # Zero says that without silently dragging
        # cluster_capacity.total_memory_mb below the sum of the other
        # nodes the way a negative limit would.
        _, limit_memory_mb = mariadb._derive_cpu_memory_limits(
            None, 1024, 4096, 3.0, 1.0)
        self.assertEqual(0, limit_memory_mb)

    def test_null_cpu_input_derives_none(self):
        limit_cpus, limit_memory_mb = mariadb._derive_cpu_memory_limits(
            None, 64243, 4096, 3.0, 3.0)
        self.assertIsNone(limit_cpus)
        self.assertIsNotNone(limit_memory_mb)

    def test_null_memory_inputs_derive_none(self):
        for memory_max, reserved in ((None, 4096), (64243, None),
                                     (None, None)):
            limit_cpus, limit_memory_mb = mariadb._derive_cpu_memory_limits(
                14, memory_max, reserved, 3.0, 3.0)
            self.assertEqual(42, limit_cpus)
            self.assertIsNone(limit_memory_mb)


class DiskLimitTestCase(base.ShakenFistTestCase):
    """_derive_disk_limit_gb() mirrors _has_sufficient_disk.

    disk_overcommit=1.0 is passed throughout unless the case is
    specifically about the ratio (decision P3,
    docs/plans/PLAN-scheduler-reservations-phase-03-primitive.md), so
    it reproduces the pre-ratio arithmetic these cases were written
    against.
    """

    def test_matches_scheduler_free_space_check(self):
        # scheduler.py _has_sufficient_disk admits while
        # requested <= disk_free_instances/GiB - disk_reservation_gb.
        # The guard form used + requested <= limit reduces to
        # requested <= floor(free/GiB) - reservation: identical for
        # whole-GB requests, up to a sub-GB conservative rounding.
        disk_free = 803469852672  # bytes; 748.28... GiB
        reservation = 20
        used = 100
        limit = mariadb._derive_disk_limit_gb(
            used, disk_free, reservation, 1.0)
        self.assertEqual(used + 728, limit)

        scheduler_headroom = disk_free / GiB - reservation
        for requested in (727, 728, 729):
            scheduler_admits = not (requested > scheduler_headroom)
            guard_admits = used + requested <= limit
            self.assertEqual(scheduler_admits, guard_admits,
                             f'divergence at {requested}')

    def test_exact_gib_conversion(self):
        self.assertEqual(
            1, mariadb._derive_disk_limit_gb(0, GiB, 0, 1.0))

    def test_clamps_negative_headroom(self):
        # Free space below the reservation must clamp to zero headroom,
        # not shrink the limit below current drawdown.
        self.assertEqual(
            100, mariadb._derive_disk_limit_gb(100, 10 * GiB, 20, 1.0))

    def test_null_inputs_derive_none(self):
        self.assertIsNone(mariadb._derive_disk_limit_gb(100, None, 20, 1.0))
        self.assertIsNone(mariadb._derive_disk_limit_gb(100, GiB, None, 1.0))

    def test_overcommit_scales_headroom_only(self):
        # used stays untouched; only the free-space headroom term is
        # multiplied by the ratio.
        used = 100
        disk_free = 10 * GiB
        reservation = 2
        headroom = math.floor(disk_free / GiB) - reservation
        limit = mariadb._derive_disk_limit_gb(
            used, disk_free, reservation, 4.0)
        self.assertEqual(used + math.floor(headroom * 4.0), limit)
        self.assertEqual(used + 32, limit)

    def test_overcommit_one_reproduces_previous_arithmetic(self):
        disk_free = 803469852672  # bytes; 748.28... GiB
        reservation = 20
        used = 100
        self.assertEqual(
            mariadb._derive_disk_limit_gb(used, disk_free, reservation, 1.0),
            used + max(0, math.floor(disk_free / GiB) - reservation))

    def test_non_positive_overcommit_falls_back_to_one(self):
        # An unset proto3 double field reads as 0.0; a negative value
        # should never reach here, but both must not silently zero
        # every node's headroom.
        used = 100
        disk_free = 10 * GiB
        reservation = 2
        expected = mariadb._derive_disk_limit_gb(
            used, disk_free, reservation, 1.0)
        for overcommit in (0.0, -1.0):
            self.assertEqual(
                expected,
                mariadb._derive_disk_limit_gb(
                    used, disk_free, reservation, overcommit))

    def test_zero_free_space_yields_used_regardless_of_ratio(self):
        used = 100
        for overcommit in (1.0, 5.0, 0.0):
            self.assertEqual(
                used,
                mariadb._derive_disk_limit_gb(used, 0, 0, overcommit))

    def test_reservation_exceeds_free_space_regardless_of_ratio(self):
        # Negative headroom clamps to zero before the ratio is
        # applied, so the ratio cannot rescue a genuinely full disk.
        used = 100
        for overcommit in (1.0, 5.0, 100.0):
            self.assertEqual(
                used,
                mariadb._derive_disk_limit_gb(used, 10 * GiB, 20, overcommit))


class DemandDecayTestCase(base.ShakenFistTestCase):
    """_decayed_demand_contribution() implements the D13 linear decay."""

    def test_age_zero_is_full_contribution(self):
        self.assertEqual(
            10.0, mariadb._decayed_demand_contribution(4, 0.0, 2.5, 600))

    def test_half_window_is_half_contribution(self):
        self.assertEqual(
            5.0, mariadb._decayed_demand_contribution(4, 300.0, 2.5, 600))

    def test_full_window_is_zero(self):
        self.assertEqual(
            0.0, mariadb._decayed_demand_contribution(4, 600.0, 2.5, 600))

    def test_beyond_window_is_zero(self):
        self.assertEqual(
            0.0, mariadb._decayed_demand_contribution(4, 601.0, 2.5, 600))

    def test_negative_age_clamps_to_full_contribution(self):
        # Clock skew between the placement writer and the database can
        # make a placement look like it is from the future.
        self.assertEqual(
            10.0, mariadb._decayed_demand_contribution(4, -5.0, 2.5, 600))

    def test_zero_window_is_zero_not_division_error(self):
        self.assertEqual(
            0.0, mariadb._decayed_demand_contribution(4, 0.0, 2.5, 0))

    def test_negative_window_is_zero(self):
        self.assertEqual(
            0.0, mariadb._decayed_demand_contribution(4, 0.0, 2.5, -600))


class DiskSpecReferenceTestCase(base.ShakenFistTestCase):
    """disk_spec_virtual_gb() is the JSON_TABLE reference semantics."""

    def test_sums_sizes(self):
        self.assertEqual(
            30, mariadb.disk_spec_virtual_gb(
                [{'size': 10}, {'size': 20}]))

    def test_sizeless_and_null_elements_contribute_zero(self):
        # CD ROM style disks have no size; explicit nulls also occur.
        self.assertEqual(
            10, mariadb.disk_spec_virtual_gb(
                [{'size': 10}, {'base': 'cd'}, {'size': None}]))

    def test_string_size_coerces(self):
        self.assertEqual(
            8, mariadb.disk_spec_virtual_gb([{'size': '8'}]))

    def test_fractional_sizes_round_half_away_from_zero(self):
        # MariaDB's JSON-number-to-BIGINT cast rounds half away from
        # zero, probed on a real server rather than assumed: 10.5 -> 11,
        # 10.4 -> 10, -2.5 -> -3, and numeric strings round too
        # ('8.7' -> 9). Python's round() would give banker's rounding
        # (10.5 -> 10) and int() would truncate; either silently
        # diverges from the SQL this helper is the oracle for.
        self.assertEqual(
            11, mariadb.disk_spec_virtual_gb([{'size': 10.5}]))
        self.assertEqual(
            10, mariadb.disk_spec_virtual_gb([{'size': 10.4}]))
        self.assertEqual(
            -3, mariadb.disk_spec_virtual_gb([{'size': -2.5}]))
        self.assertEqual(
            9, mariadb.disk_spec_virtual_gb([{'size': '8.7'}]))

    def test_boolean_size_casts_like_sql(self):
        # JSON true/false cast to 1/0 in the BIGINT column.
        self.assertEqual(
            1, mariadb.disk_spec_virtual_gb([{'size': True}]))
        self.assertEqual(
            0, mariadb.disk_spec_virtual_gb([{'size': False}]))

    def test_garbage_size_contributes_zero(self):
        self.assertEqual(
            10, mariadb.disk_spec_virtual_gb(
                [{'size': 10}, {'size': 'banana'}]))

    def test_non_dict_element_contributes_zero(self):
        self.assertEqual(
            10, mariadb.disk_spec_virtual_gb([{'size': 10}, 'garbage']))

    def test_non_list_spec_is_zero(self):
        self.assertEqual(0, mariadb.disk_spec_virtual_gb({'size': 10}))
        self.assertEqual(0, mariadb.disk_spec_virtual_gb(None))


class ClaimUsageStatementTestCase(base.ShakenFistTestCase):
    """_RECONCILE_CLAIM_USAGE_SQL's shape.

    The behaviour these assertions stand in for is proven against a real
    server in test_mariadb_capacity_reconcile_live.py -- a mock can not
    show that a duplicated placement row counts once. What they do catch
    is somebody editing the statement into a shape that quietly loses
    one of its two load-bearing properties.
    """

    def test_placements_are_deduplicated_by_instance(self):
        text = str(mariadb._RECONCILE_CLAIM_USAGE_SQL)
        # A namespace quota must charge an instance once however many
        # placement rows point at it, and a stale INSTANCE_LOCATION row
        # left behind by a lost node is how it gets two.
        self.assertIn('SELECT DISTINCT r.target_uuid', text)

    def test_claims_without_instances_are_zeroed_not_skipped(self):
        text = str(mariadb._RECONCILE_CLAIM_USAGE_SQL)
        # An inner join would leave a claim whose namespace has no
        # instances at whatever its counters last were, forever.
        self.assertIn('UPDATE namespace_claims c\n      LEFT JOIN', text)
        self.assertIn('c.used_cpus = COALESCE(u.used_cpus, 0)', text)
        self.assertIn('c.used_memory_mb = COALESCE(u.used_memory_mb, 0)',
                      text)
        self.assertIn('c.used_disk_gb = COALESCE(u.used_disk_gb, 0)', text)

    def test_only_active_claims_are_touched(self):
        self.assertIn("WHERE c.state = 'active'",
                      str(mariadb._RECONCILE_CLAIM_USAGE_SQL))

    def test_same_ground_truth_conventions_as_the_node_query(self):
        text = str(mariadb._RECONCILE_CLAIM_USAGE_SQL)
        # Pitfall 6 again: the dashed reference-side uuid is transformed
        # so the join lands on the undashed instances primary key, while
        # object_states.object_uuid is dashed and compares directly.
        self.assertIn("REPLACE(p.target_uuid, '-', '')", text)
        self.assertIn('s.object_uuid = p.target_uuid', text)
        self.assertIn('JSON_TABLE', text)
        self.assertIn("JSON_TYPE(i2.disk_spec) = 'ARRAY'", text)
        self.assertIn('s.state_value IS NULL', text)
        self.assertIn("s.state_value != 'deleted'", text)

    def test_reference_params_spell_each_convention(self):
        # Shared with _reconcile_fetch_usage(), so both aggregations
        # agree on which column holds a member name and which a value.
        self.assertEqual({
            'instance_object_type': 'INSTANCE',
            'instance_ref_type': 'instance',
            'node_object_type': 'node',
            'instance_location': 'instance_location',
        }, mariadb._reconcile_reference_params())


class FetchUsageTestCase(base.ShakenFistTestCase):
    """_reconcile_fetch_usage() statement shape and row folding."""

    def test_query_uses_dashed_undashed_transform_and_json_table(self):
        conn = mock.MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        mariadb._reconcile_fetch_usage(conn)

        stmt, params = conn.execute.call_args.args
        text = str(stmt)
        # Pitfall 6: the reference-side dashed uuid is transformed so
        # the join lands on the instances primary key.
        self.assertIn("REPLACE(r.target_uuid, '-', '')", text)
        self.assertIn('JSON_TABLE', text)
        self.assertIn("JSON_TYPE(i2.disk_spec) = 'ARRAY'", text)
        # Only 'deleted' is excluded; stateless rows count.
        self.assertIn('s.state_value IS NULL', text)
        self.assertIn("s.state_value != 'deleted'", text)
        # Two storage conventions, and each binding must match its
        # column's exactly rather than leaning on a case-insensitive
        # collation: object_states.object_type is a native sa.Enum,
        # which persists member *names*; the object_references type and
        # relationship columns are plain strings written by
        # _direct_record_relationship() as str(member), which for these
        # str-subclass enums is the member *value*. Binding the name
        # against object_references matches under utf8mb4_general_ci
        # and silently returns zero rows under utf8mb4_bin (the live
        # suite runs under utf8mb4_bin to catch exactly that).
        self.assertEqual('INSTANCE', params['instance_object_type'])
        self.assertEqual('instance', params['instance_ref_type'])
        self.assertEqual('node', params['node_object_type'])
        self.assertEqual('instance_location', params['instance_location'])

    def test_bindings_match_what_the_write_path_stores(self):
        # _direct_record_relationship() writes str(member) into the
        # object_references columns. These str-subclass enums override
        # __str__ to return the member value, so the value is what the
        # columns contain -- pin that here, because if the enums ever
        # stopped overriding __str__ (Python's default str() for a
        # str-mixin enum is 'ObjectType.NODE') the write path and the
        # reconcile bindings would both change character silently.
        self.assertEqual(ObjectType.NODE.value, str(ObjectType.NODE))
        self.assertEqual(ObjectType.INSTANCE.value,
                         str(ObjectType.INSTANCE))
        self.assertEqual(RelationshipType.INSTANCE_LOCATION.value,
                         str(RelationshipType.INSTANCE_LOCATION))

    def test_folds_rows_and_skips_malformed_node_uuid(self):
        conn = mock.MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            SimpleNamespace(
                node_uuid='11111111-1111-1111-1111-111111111111',
                namespace='ns1', used_cpus=3, used_memory_mb=2560,
                used_disk_gb=30),
            SimpleNamespace(
                node_uuid='not-a-uuid', namespace='ns1', used_cpus=1,
                used_memory_mb=1, used_disk_gb=1),
        ]
        usage = mariadb._reconcile_fetch_usage(conn)
        self.assertEqual(
            {(UUID('11111111-1111-1111-1111-111111111111'), 'ns1'):
             (3, 2560, 30)}, usage)


class FetchDemandTestCase(base.ShakenFistTestCase):
    """_reconcile_fetch_demand() statement shape and decay folding."""

    def test_zero_window_skips_query(self):
        conn = mock.MagicMock()
        self.assertEqual({}, mariadb._reconcile_fetch_demand(
            conn, 1000.0, 2.5, 0))
        conn.execute.assert_not_called()

    def test_query_uses_dashed_undashed_transform_and_window(self):
        conn = mock.MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        mariadb._reconcile_fetch_demand(conn, 1000.0, 2.5, 600)

        text, params = _compiled(conn.execute.call_args.args[0])
        self.assertIn(
            'replace(object_references.target_uuid', text.lower())
        self.assertIn('object_references.created >', text)
        # The window cutoff is now - decay = 1000 - 600.
        self.assertIn(400.0, params.values())

    def test_folds_decayed_contributions(self):
        node = '11111111-1111-1111-1111-111111111111'
        conn = mock.MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            SimpleNamespace(source_uuid=node, created=1000.0, cpus=4),
            SimpleNamespace(source_uuid=node, created=700.0, cpus=4),
            SimpleNamespace(source_uuid='not-a-uuid', created=1000.0,
                            cpus=4),
        ]
        demand = mariadb._reconcile_fetch_demand(conn, 1000.0, 2.5, 600)
        # age 0 contributes 4 x 2.5 = 10; age 300 contributes 5.
        self.assertEqual({UUID(node): 15.0}, demand)


class ReconcileEmptyClusterTestCase(base.ShakenFistTestCase):
    """An empty cluster completes and returns zero counts."""

    ZERO_CLUSTER = {
        'total_cpus': 0, 'total_memory_mb': 0, 'total_disk_gb': 0,
        'claimed_cpus': 0, 'claimed_memory_mb': 0, 'claimed_disk_gb': 0,
        'unclaimed_used_cpus': 0, 'unclaimed_used_memory_mb': 0,
        'unclaimed_used_disk_gb': 0,
    }

    def _run_empty(self):
        mock_engine = mock.MagicMock()
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []
        conn.execute.return_value.rowcount = 0
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=mock_engine):
            result = mariadb._direct_reconcile_scheduler_capacity(2.5, 600, 1.0)
        return result, conn

    def test_empty_cluster_returns_zero_counts(self):
        result, conn = self._run_empty()
        self.assertEqual({
            'success': True,
            'nodes_added': 0,
            'nodes_removed': 0,
            'claims_expired': 0,
            'nodes': [],
            'cluster': self.ZERO_CLUSTER,
        }, result)
        conn.commit.assert_called_once()

    def test_claim_expiry_update_shape(self):
        _, conn = self._run_empty()
        # The expiry sweep is the first statement of the pass.
        text, params = _compiled(conn.execute.call_args_list[0].args[0])
        self.assertIn('UPDATE namespace_claims', text)
        self.assertIn('expires_at < now()', text)
        self.assertEqual('active', params['state_1'])
        self.assertEqual('expired', params['state'])

    def test_cluster_singleton_upsert_shape(self):
        _, conn = self._run_empty()
        upserts = []
        for call in conn.execute.call_args_list:
            stmt = call.args[0]
            if isinstance(stmt, sa.sql.expression.Insert):
                upserts.append(_compiled(stmt))
        self.assertEqual(1, len(upserts))
        text, params = upserts[0]
        self.assertIn('INSERT INTO cluster_capacity', text)
        self.assertIn('ON DUPLICATE KEY UPDATE', text)
        self.assertEqual(1, params['id'])
        self.assertEqual(0, params['total_cpus'])
        self.assertEqual(0, params['unclaimed_used_disk_gb'])

    def test_operational_error_returns_none(self):
        mock_engine = mock.MagicMock()
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = OperationalError(
            'stmt', {}, Exception('boom'))
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=mock_engine):
            self.assertIsNone(
                mariadb._direct_reconcile_scheduler_capacity(2.5, 600, 1.0))


NODE1 = UUID('11111111-1111-1111-1111-111111111111')
NODE2 = UUID('22222222-2222-2222-2222-222222222222')
NODE3 = UUID('33333333-3333-3333-3333-333333333333')
NODE4 = UUID('44444444-4444-4444-4444-444444444444')
NODE5 = UUID('55555555-5555-5555-5555-555555555555')
CLAIM1 = UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')


def _metrics_row(node_uuid, cpu_schedulable=None, memory_max=None,
                 memory_reserved_mb=None, disk_free_instances=None,
                 disk_reservation_gb=None):
    return SimpleNamespace(
        node_uuid=node_uuid, cpu_schedulable=cpu_schedulable,
        memory_max=memory_max, memory_reserved_mb=memory_reserved_mb,
        disk_free_instances=disk_free_instances,
        disk_reservation_gb=disk_reservation_gb)


def _is_non_hypervisor_query(text):
    """Is this the node_metrics query for confirmed non-hypervisors?

    The reconciler issues three node_metrics selects: the capacity
    columns for hypervisors with fresh metrics, the uuids of nodes known
    not to be hypervisors, and the uuids of nodes with any fresh row at
    all. The first two are told apart by the polarity of the
    is_hypervisor predicate.
    """
    return 'is_hypervisor IS false' in text


def _is_fresh_metrics_query(text):
    """Is this the node_metrics query for the fresh-row set?

    Distinguished from the capacity-columns query (which also filters
    on freshness) by having no is_hypervisor predicate: the fresh set
    deliberately includes rows whose is_hypervisor is still NULL so the
    mid-upgrade retention behaviour is preserved.
    """
    return 'node_metrics.timestamp >' in text and 'is_hypervisor' not in text


def _deleted_row_count(stmt):
    """How many node uuids does a capacity DELETE actually name?

    The IN list binds as one expanding parameter, so this is the length
    of that list -- what a real database would report as the rowcount
    given the fixture's rows all exist.
    """
    params = stmt.compile(dialect=MYSQL_DIALECT).params
    for value in params.values():
        if isinstance(value, (list, tuple)):
            return len(value)
    return len(params)


def _capacity_row(node_uuid, limit_cpus, limit_memory_mb, limit_disk_gb,
                  used_cpus=0, used_memory_mb=0, used_disk_gb=0):
    return SimpleNamespace(
        node_uuid=node_uuid, limit_cpus=limit_cpus,
        limit_memory_mb=limit_memory_mb, limit_disk_gb=limit_disk_gb,
        used_cpus=used_cpus, used_memory_mb=used_memory_mb,
        used_disk_gb=used_disk_gb, expected_demand=0.0, updated_at=None)


class _ReconcileRouterMixin:
    """Routes a mocked connection's execute() calls to canned rows.

    A reconcile pass issues a fixed sequence of reads; each test sets the
    rows it wants each of them to return and this dispatches on the
    compiled statement text.
    """

    def _fake_result(self, rows=None, rowcount=0):
        result = mock.MagicMock()
        result.fetchall.return_value = rows or []
        result.rowcount = rowcount
        return result

    def _route(self, stmt, *args, **kwargs):
        try:
            text = str(stmt.compile(dialect=MYSQL_DIALECT))
        except AttributeError:
            text = str(stmt)

        if 'UPDATE namespace_claims' in text and 'expires_at' in text:
            return self._fake_result(rowcount=3)
        if 'DELETE FROM scheduler_node_capacity' in text:
            # Report the number of uuids the statement actually names, so
            # nodes_removed assertions test the reconciler's removal set
            # rather than a hardcoded number the fixture cannot produce.
            return self._fake_result(rowcount=_deleted_row_count(stmt))
        if 'FROM scheduler_node_capacity' in text:
            return self._fake_result(rows=self.previous_rows)
        if 'FROM node_metrics' in text:
            if _is_non_hypervisor_query(text):
                return self._fake_result(rows=self.non_hypervisor_rows)
            if _is_fresh_metrics_query(text):
                return self._fake_result(rows=self.fresh_metrics_rows)
            return self._fake_result(rows=self.metrics_rows)
        if 'FROM nodes' in text:
            return self._fake_result(rows=self.node_rows)
        if 'FROM object_states' in text:
            return self._fake_result(rows=self.active_rows)
        if 'FROM namespace_claims' in text:
            return self._fake_result(rows=self.claim_rows)
        self.executed.append((text, stmt))
        return self._fake_result()


class ReconcileScenarioTestCase(_ReconcileRouterMixin, base.ShakenFistTestCase):
    """A populated pass: add, keep-on-NULL-metrics, remove, claims."""

    def _run_scenario(self):
        # node1: fresh metrics, active, no previous row -> added.
        # node2: previous row, metrics row with NULL capacity columns
        #        -> limits kept, usage refreshed.
        # node3: previous row, no metrics row, not in nodes -> removed.
        # node4: previous row, has metrics, but not in the active state
        #        set (deleted) -> removed.
        # node5: metrics row but not active -> never inserted.
        self.previous_rows = [
            _capacity_row(NODE2, 10, 1000, 50, used_cpus=5,
                          used_memory_mb=500, used_disk_gb=10),
            _capacity_row(NODE3, 1, 1, 1),
            _capacity_row(NODE4, 1, 1, 1),
        ]
        self.metrics_rows = [
            _metrics_row(NODE1, cpu_schedulable=14, memory_max=64243,
                         memory_reserved_mb=4096,
                         disk_free_instances=803469852672,
                         disk_reservation_gb=20),
            _metrics_row(NODE2),
            _metrics_row(NODE4, cpu_schedulable=1, memory_max=1,
                         memory_reserved_mb=0, disk_free_instances=GiB,
                         disk_reservation_gb=0),
            _metrics_row(NODE5, cpu_schedulable=1, memory_max=1,
                         memory_reserved_mb=0, disk_free_instances=GiB,
                         disk_reservation_gb=0),
        ]
        self.node_rows = [
            SimpleNamespace(uuid=NODE1), SimpleNamespace(uuid=NODE2),
            SimpleNamespace(uuid=NODE4), SimpleNamespace(uuid=NODE5)]
        self.active_rows = [
            SimpleNamespace(object_uuid=str(NODE1)),
            SimpleNamespace(object_uuid=str(NODE2))]
        self.claim_rows = [
            SimpleNamespace(uuid=CLAIM1, namespace='ns1', limit_cpus=8,
                            limit_memory_mb=8192, limit_disk_gb=100,
                            state='active')]
        self.non_hypervisor_rows = []
        # Every node that has a metrics row in this scenario has a
        # fresh one; NODE3 has no metrics row at all, so it is absent
        # here and the freshness rule removes its previous row (as does
        # its absence from nodes).
        self.fresh_metrics_rows = [
            SimpleNamespace(node_uuid=NODE1),
            SimpleNamespace(node_uuid=NODE2),
            SimpleNamespace(node_uuid=NODE4),
            SimpleNamespace(node_uuid=NODE5)]
        self.executed = []

        usage = {
            (NODE1, 'ns1'): (2, 2048, 30),
            (NODE1, 'ns2'): (4, 4096, 8),
            (NODE2, 'ns2'): (1, 1024, 5),
            # Stranded on a node that ends the pass without a capacity
            # row (node3 is removed): counts toward nothing at cluster
            # scope, so the singleton stays a closed accounting over the
            # schedulable cluster.
            (NODE3, 'ns2'): (7, 7168, 70),
        }
        demand = {NODE1: 10.0}

        mock_engine = mock.MagicMock()
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = self._route
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=mock_engine), \
                mock.patch('shakenfist.mariadb._reconcile_fetch_usage',
                           return_value=usage), \
                mock.patch('shakenfist.mariadb._reconcile_fetch_demand',
                           return_value=demand), \
                mock.patch.object(mariadb.config,
                                  'CPU_OVERCOMMIT_RATIO', 3.0), \
                mock.patch.object(mariadb.config,
                                  'RAM_OVERCOMMIT_RATIO', 3.0):
            result = mariadb._direct_reconcile_scheduler_capacity(2.5, 600, 1.0)
        return result, conn

    def test_counts_and_reply_rows(self):
        result, _ = self._run_scenario()
        self.assertEqual(1, result['nodes_added'])
        self.assertEqual(2, result['nodes_removed'])
        self.assertEqual(3, result['claims_expired'])

        self.assertEqual(2, len(result['nodes']))
        node1, node2 = result['nodes']

        # node1: fresh limits from metrics. used_disk 38 (30 + 8), so
        # limit_disk = 38 + (floor(748.28) - 20) = 766.
        self.assertEqual({
            'node_uuid': str(NODE1), 'limit_cpus': 42,
            'limit_memory_mb': 188633, 'limit_disk_gb': 766,
            'used_cpus': 6, 'used_memory_mb': 6144, 'used_disk_gb': 38,
            'expected_demand': 10.0, 'delta_used_cpus': 6,
            'delta_used_memory_mb': 6144, 'delta_used_disk_gb': 38,
        }, node1)

        # node2: NULL metrics columns keep the previous limits; usage
        # is refreshed from ground truth and deltas reflect it.
        self.assertEqual({
            'node_uuid': str(NODE2), 'limit_cpus': 10,
            'limit_memory_mb': 1000, 'limit_disk_gb': 50,
            'used_cpus': 1, 'used_memory_mb': 1024, 'used_disk_gb': 5,
            'expected_demand': 0.0, 'delta_used_cpus': -4,
            'delta_used_memory_mb': 524, 'delta_used_disk_gb': -5,
        }, node2)

    def test_cluster_row_sums(self):
        result, _ = self._run_scenario()
        self.assertEqual({
            'total_cpus': 52, 'total_memory_mb': 189633,
            'total_disk_gb': 816,
            # The active ns1 claim's limits.
            'claimed_cpus': 8, 'claimed_memory_mb': 8192,
            'claimed_disk_gb': 100,
            # ns2 has no active claim: (4 + 1, 4096 + 1024, 8 + 5).
            # The (NODE3, ns2) usage is absent because node3 holds no
            # capacity row -- that exclusion is a decision, not an
            # accident (a stranded instance may not inflate the used
            # side of a total its node contributes nothing to).
            'unclaimed_used_cpus': 5, 'unclaimed_used_memory_mb': 5120,
            'unclaimed_used_disk_gb': 13,
        }, result['cluster'])

    def test_removals_and_claim_update_statements(self):
        _, conn = self._run_scenario()

        deletes = [str(call.args[0].compile(dialect=MYSQL_DIALECT))
                   for call in conn.execute.call_args_list
                   if isinstance(call.args[0], sa.sql.expression.Delete)]
        self.assertEqual(1, len(deletes))
        self.assertIn('DELETE FROM scheduler_node_capacity', deletes[0])

        # The per-claim usage recompute is one set-based statement, so
        # the only constructed UPDATE against namespace_claims in the
        # whole pass is the expiry sweep. A regression to one UPDATE per
        # claim -- N round trips holding write locks inside the pass's
        # transaction -- shows up here as a second entry.
        claim_updates = []
        for call in conn.execute.call_args_list:
            stmt = call.args[0]
            if not isinstance(stmt, sa.sql.expression.Update):
                continue
            compiled = stmt.compile(dialect=MYSQL_DIALECT)
            if 'namespace_claims' in str(compiled):
                claim_updates.append(compiled.params)
        self.assertEqual(1, len(claim_updates))
        self.assertEqual('expired', claim_updates[0]['state'])

    def test_claim_usage_recompute_runs_once_with_both_conventions(self):
        _, conn = self._run_scenario()

        recomputes = [
            call for call in conn.execute.call_args_list
            if isinstance(call.args[0], sa.sql.elements.TextClause)
            and 'UPDATE namespace_claims' in str(call.args[0])]
        self.assertEqual(1, len(recomputes),
                         'the per-claim recompute is one statement')

        # The bindings span the same two enum storage conventions as
        # _RECONCILE_USAGE_SQL, and getting one wrong silently matches
        # nothing under a case-sensitive collation rather than erroring.
        params = recomputes[0].args[1]
        self.assertEqual('INSTANCE', params['instance_object_type'])
        self.assertEqual('instance', params['instance_ref_type'])
        self.assertEqual('node', params['node_object_type'])
        self.assertEqual('instance_location', params['instance_location'])

    def test_node_upserts_have_on_duplicate_key_update(self):
        _, conn = self._run_scenario()
        upserts = []
        for call in conn.execute.call_args_list:
            stmt = call.args[0]
            if not isinstance(stmt, sa.sql.expression.Insert):
                continue
            text = str(stmt.compile(dialect=MYSQL_DIALECT))
            if 'scheduler_node_capacity' in text:
                upserts.append(text)
        self.assertEqual(2, len(upserts))
        for text in upserts:
            self.assertIn('ON DUPLICATE KEY UPDATE', text)


class ReconcileHypervisorFilterTestCase(
        _ReconcileRouterMixin, base.ShakenFistTestCase):
    """Only hypervisors get capacity rows (the scheduler's own rule).

    sf-resources publishes metrics from every node whatever its roles, so
    without an is_hypervisor filter a network-only or database-only node
    would get a capacity row and its unschedulable capacity would be
    summed into the cluster totals.
    """

    def _run_roles(self, previous_rows, metrics_rows, non_hypervisor_rows,
                   node_rows, active_rows=None, fresh_metrics_rows=None):
        # Nodes named in node_rows default to active, and nodes with a
        # metrics row default to fresh: these tests are each about one
        # filter, and the state and freshness filters would otherwise
        # mask the one under test (a node with no active state row or
        # no fresh metrics row never qualifies). Tests about the
        # freshness rule itself pass fresh_metrics_rows explicitly.
        self.previous_rows = previous_rows
        self.metrics_rows = metrics_rows
        self.non_hypervisor_rows = non_hypervisor_rows
        self.node_rows = node_rows
        if active_rows is None:
            active_rows = [SimpleNamespace(object_uuid=str(row.uuid))
                           for row in node_rows]
        self.active_rows = active_rows
        if fresh_metrics_rows is None:
            fresh_metrics_rows = [SimpleNamespace(node_uuid=row.node_uuid)
                                  for row in metrics_rows]
        self.fresh_metrics_rows = fresh_metrics_rows
        self.claim_rows = []
        self.executed = []

        mock_engine = mock.MagicMock()
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = self._route
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=mock_engine), \
                mock.patch('shakenfist.mariadb._reconcile_fetch_usage',
                           return_value={}), \
                mock.patch('shakenfist.mariadb._reconcile_fetch_demand',
                           return_value={}), \
                mock.patch.object(mariadb.config,
                                  'CPU_OVERCOMMIT_RATIO', 3.0), \
                mock.patch.object(mariadb.config,
                                  'RAM_OVERCOMMIT_RATIO', 3.0):
            result = mariadb._direct_reconcile_scheduler_capacity(2.5, 600, 1.0)
        return result, conn

    def test_metrics_select_filters_on_is_hypervisor(self):
        # Both polarities are queried, and the capacity columns only ever
        # come from the hypervisor side.
        _, conn = self._run_roles(
            previous_rows=[], metrics_rows=[], non_hypervisor_rows=[],
            node_rows=[])
        metrics_queries = []
        for call in conn.execute.call_args_list:
            try:
                text = str(call.args[0].compile(dialect=MYSQL_DIALECT))
            except AttributeError:
                continue
            if 'FROM node_metrics' in text:
                metrics_queries.append(text)
        self.assertEqual(3, len(metrics_queries))
        hypervisor = [t for t in metrics_queries
                      if not _is_non_hypervisor_query(t)
                      and not _is_fresh_metrics_query(t)]
        self.assertEqual(1, len(hypervisor))
        self.assertIn('is_hypervisor IS true', hypervisor[0])
        self.assertIn('cpu_schedulable', hypervisor[0])
        # The capacity columns are also gated on metrics freshness, so a
        # node whose resources daemon died stops contributing.
        self.assertIn('node_metrics.timestamp >', hypervisor[0])
        # The fresh set is expressed positively (membership, not a
        # stale-set subtraction, so a node with no metrics row at all
        # is excluded) and deliberately has no is_hypervisor predicate,
        # so a fresh row whose is_hypervisor is still NULL mid-upgrade
        # keeps qualifying for the no-evidence retention.
        fresh = [t for t in metrics_queries if _is_fresh_metrics_query(t)]
        self.assertEqual(1, len(fresh))

    def test_non_hypervisor_gets_no_capacity_row(self):
        # NODE1 is a hypervisor, NODE2 is a network-only node: it has a
        # metrics row with perfectly good capacity columns, but the
        # hypervisor query never returns it.
        result, _ = self._run_roles(
            previous_rows=[],
            metrics_rows=[
                _metrics_row(NODE1, cpu_schedulable=10, memory_max=1024,
                             memory_reserved_mb=0,
                             disk_free_instances=100 * GiB,
                             disk_reservation_gb=0)],
            non_hypervisor_rows=[SimpleNamespace(node_uuid=NODE2)],
            node_rows=[SimpleNamespace(uuid=NODE1),
                       SimpleNamespace(uuid=NODE2)])

        self.assertEqual([str(NODE1)],
                         [n['node_uuid'] for n in result['nodes']])
        self.assertEqual(1, result['nodes_added'])
        # The cluster totals see only the hypervisor's capacity.
        self.assertEqual(30, result['cluster']['total_cpus'])
        self.assertEqual(3072, result['cluster']['total_memory_mb'])
        self.assertEqual(100, result['cluster']['total_disk_gb'])

    def test_demoted_hypervisor_loses_its_row(self):
        # NODE2 has a capacity row from when it was a hypervisor. It is
        # still a live node, so the not-in-nodes removal rule does not
        # catch it -- the is_hypervisor=False rule must. This is also how
        # rows written before the filter existed are cleaned up.
        result, conn = self._run_roles(
            previous_rows=[_capacity_row(NODE2, 10, 1000, 50)],
            metrics_rows=[],
            non_hypervisor_rows=[SimpleNamespace(node_uuid=NODE2)],
            node_rows=[SimpleNamespace(uuid=NODE2)],
            fresh_metrics_rows=[SimpleNamespace(node_uuid=NODE2)])

        self.assertEqual([], result['nodes'])
        self.assertEqual(1, result['nodes_removed'])
        deletes = [str(call.args[0].compile(dialect=MYSQL_DIALECT))
                   for call in conn.execute.call_args_list
                   if isinstance(call.args[0], sa.sql.expression.Delete)]
        self.assertEqual(1, len(deletes))
        self.assertIn('DELETE FROM scheduler_node_capacity', deletes[0])
        self.assertEqual(0, result['cluster']['total_cpus'])

    def test_null_is_hypervisor_keeps_an_existing_row(self):
        # A metrics row written by a pre-upgrade resources daemon has
        # is_hypervisor NULL, so the node appears in neither polarity of
        # the role query -- but its row is fresh, so it is in the fresh
        # set. That is not evidence of anything, so an existing row
        # keeps its limits rather than being deleted on the strength of
        # a value that has simply not been written yet.
        result, _ = self._run_roles(
            previous_rows=[_capacity_row(NODE2, 10, 1000, 50)],
            metrics_rows=[],
            non_hypervisor_rows=[],
            node_rows=[SimpleNamespace(uuid=NODE2)],
            fresh_metrics_rows=[SimpleNamespace(node_uuid=NODE2)])

        self.assertEqual(1, len(result['nodes']))
        self.assertEqual(str(NODE2), result['nodes'][0]['node_uuid'])
        self.assertEqual(10, result['nodes'][0]['limit_cpus'])
        self.assertEqual(0, result['nodes_removed'])


class ReconcileNodeStateFilterTestCase(
        _ReconcileRouterMixin, base.ShakenFistTestCase):
    """Only active nodes get capacity rows.

    The scheduler builds its candidate set from
    Nodes([], prefilter='active'), so an errored, missing, stopping or
    stopped node is not a scheduling candidate. A hypervisor taken out of
    service by the node-health cascade must stop contributing its limits
    to the cluster totals rather than advertising capacity nothing can be
    placed on.
    """

    _run_roles = ReconcileHypervisorFilterTestCase._run_roles

    def test_state_query_selects_the_active_set(self):
        _, conn = self._run_roles(
            previous_rows=[], metrics_rows=[], non_hypervisor_rows=[],
            node_rows=[])
        state_queries = []
        for call in conn.execute.call_args_list:
            try:
                compiled = call.args[0].compile(dialect=MYSQL_DIALECT)
            except AttributeError:
                continue
            if 'FROM object_states' in str(compiled):
                state_queries.append(compiled)
        self.assertEqual(1, len(state_queries))
        text = str(state_queries[0])
        # Expressed as IN the active set (with candidates intersected
        # against the result), so both a state added to the state
        # machine later and a node with no state row at all are excluded
        # by default rather than silently counting as schedulable. An
        # earlier NOT-IN-the-inactive-set form let stateless nodes
        # through.
        self.assertIn('state_value IN', text)
        self.assertNotIn('NOT IN', text)
        bound = set()
        for value in state_queries[0].params.values():
            # The IN list binds as a single expanding parameter.
            if isinstance(value, (list, tuple)):
                bound.update(value)
            else:
                bound.add(value)
        for state in mariadb.NODE_ACTIVE_STATES:
            self.assertIn(state, bound)

    def test_errored_node_gets_no_capacity_row(self):
        # A hypervisor with good, fresh metrics that the node-health
        # cascade has moved to error.
        result, _ = self._run_roles(
            previous_rows=[],
            metrics_rows=[
                _metrics_row(NODE1, cpu_schedulable=10, memory_max=1024,
                             memory_reserved_mb=0,
                             disk_free_instances=100 * GiB,
                             disk_reservation_gb=0),
                _metrics_row(NODE2, cpu_schedulable=64, memory_max=65536,
                             memory_reserved_mb=0,
                             disk_free_instances=500 * GiB,
                             disk_reservation_gb=0)],
            non_hypervisor_rows=[],
            node_rows=[SimpleNamespace(uuid=NODE1),
                       SimpleNamespace(uuid=NODE2)],
            active_rows=[SimpleNamespace(object_uuid=str(NODE1))])

        self.assertEqual([str(NODE1)],
                         [n['node_uuid'] for n in result['nodes']])
        # NODE2's 64 schedulable threads are nowhere in the totals.
        self.assertEqual(30, result['cluster']['total_cpus'])

    def test_stateless_node_gets_no_capacity_row(self):
        # A node with a nodes row and fresh hypervisor metrics but no
        # object_states row at all. The scheduler cannot see it --
        # Nodes([], prefilter='active') resolves through
        # get_objects_by_state, which only returns objects that have a
        # state row -- so it must not get a capacity row either.
        # Stateless zombies are a real condition (the orphan reconciler
        # exists for them), and a subtractive inactive-set filter let
        # them through: absent from the inactive set is not the same as
        # present in the active one.
        result, _ = self._run_roles(
            previous_rows=[],
            metrics_rows=[
                _metrics_row(NODE1, cpu_schedulable=10, memory_max=1024,
                             memory_reserved_mb=0,
                             disk_free_instances=100 * GiB,
                             disk_reservation_gb=0),
                _metrics_row(NODE2, cpu_schedulable=64, memory_max=65536,
                             memory_reserved_mb=0,
                             disk_free_instances=500 * GiB,
                             disk_reservation_gb=0)],
            non_hypervisor_rows=[],
            node_rows=[SimpleNamespace(uuid=NODE1),
                       SimpleNamespace(uuid=NODE2)],
            active_rows=[SimpleNamespace(object_uuid=str(NODE1))])

        self.assertEqual([str(NODE1)],
                         [n['node_uuid'] for n in result['nodes']])
        self.assertEqual(30, result['cluster']['total_cpus'])

    def test_node_entering_error_loses_its_row(self):
        result, conn = self._run_roles(
            previous_rows=[_capacity_row(NODE2, 192, 196608, 500)],
            metrics_rows=[],
            non_hypervisor_rows=[],
            node_rows=[SimpleNamespace(uuid=NODE2)],
            active_rows=[],
            fresh_metrics_rows=[SimpleNamespace(node_uuid=NODE2)])

        self.assertEqual([], result['nodes'])
        self.assertEqual(1, result['nodes_removed'])
        deletes = [str(call.args[0].compile(dialect=MYSQL_DIALECT))
                   for call in conn.execute.call_args_list
                   if isinstance(call.args[0], sa.sql.expression.Delete)]
        self.assertEqual(1, len(deletes))
        self.assertEqual(0, result['cluster']['total_cpus'])

    def test_stale_metrics_node_loses_its_row(self):
        # node_metrics rows are only deleted when the node is, so a live
        # node whose resources daemon has died would otherwise contribute
        # its last known limits forever. A stale row means absence from
        # the fresh set.
        result, _ = self._run_roles(
            previous_rows=[_capacity_row(NODE2, 192, 196608, 500)],
            metrics_rows=[],
            non_hypervisor_rows=[],
            node_rows=[SimpleNamespace(uuid=NODE2)],
            fresh_metrics_rows=[])

        self.assertEqual([], result['nodes'])
        self.assertEqual(1, result['nodes_removed'])
        self.assertEqual(0, result['cluster']['total_cpus'])

    def test_no_metrics_row_at_all_loses_its_row(self):
        # sf-resources deletes its own node's metrics rows at daemon
        # startup before the first upsert, so a resources daemon that
        # dies in that window leaves a live, active node with no
        # node_metrics row at all. To the positive fresh set that is
        # indistinguishable from a stale row and the capacity row is
        # removed; a stale-set subtraction would have found the node in
        # neither set and rewritten its last-known limits forever --
        # the freshness twin of the state filter's polarity bug.
        result, _ = self._run_roles(
            previous_rows=[_capacity_row(NODE2, 192, 196608, 500)],
            metrics_rows=[],
            non_hypervisor_rows=[],
            node_rows=[SimpleNamespace(uuid=NODE2)],
            fresh_metrics_rows=[])

        self.assertEqual([], result['nodes'])
        self.assertEqual(1, result['nodes_removed'])
        self.assertEqual(0, result['cluster']['total_cpus'])

    def test_staleness_window_is_above_the_reconcile_cadence(self):
        # A window at or below the five minute pass cadence would make
        # rows flap in and out between passes and make the reply's
        # nodes_added/nodes_removed counts meaningless.
        self.assertGreater(mariadb.RECONCILE_METRICS_MAX_AGE_SECONDS, 300)


class ReconcileReplyRoundTripTestCase(base.ShakenFistTestCase):
    """The summary dict survives the trip through the proto and back.

    The keys _direct_ produces, the proto field names, and the keys the
    cluster daemon reads are kept in sync by nothing but care: the
    servicer splats the dict into the message (raising on an unknown
    field name) and the client hand-copies all twenty fields back out. A
    rename on either side would pass every other test and fail only on a
    live cluster, so exercise both layers against each other.
    """

    SUMMARY = {
        'success': True,
        'nodes_added': 2,
        'nodes_removed': 1,
        'claims_expired': 3,
        'nodes': [{
            'node_uuid': str(NODE1),
            'limit_cpus': 42, 'limit_memory_mb': 188633,
            'limit_disk_gb': 766,
            'used_cpus': 6, 'used_memory_mb': 6144, 'used_disk_gb': 38,
            'expected_demand': 10.0,
            'delta_used_cpus': 6, 'delta_used_memory_mb': 6144,
            'delta_used_disk_gb': 38,
        }],
        'cluster': {
            'total_cpus': 52, 'total_memory_mb': 189633,
            'total_disk_gb': 816,
            'claimed_cpus': 8, 'claimed_memory_mb': 8192,
            'claimed_disk_gb': 100,
            'unclaimed_used_cpus': 5, 'unclaimed_used_memory_mb': 5120,
            'unclaimed_used_disk_gb': 13,
        },
    }

    def _servicer_reply(self, direct_result):
        servicer = database_main.DatabaseService.__new__(
            database_main.DatabaseService)
        servicer.monitor = mock.MagicMock()
        request = database_pb2.ReconcileSchedulerCapacityRequest(
            demand_per_vcpu=2.5, demand_decay_seconds=600,
            disk_overcommit=5.0)
        with mock.patch(
                'shakenfist.mariadb._direct_reconcile_scheduler_capacity',
                return_value=direct_result):
            return servicer.ReconcileSchedulerCapacity(
                request, mock.MagicMock())

    def test_round_trip_preserves_every_field(self):
        reply = self._servicer_reply(self.SUMMARY)
        self.assertTrue(reply.success)

        stub = mock.MagicMock()
        with mock.patch('shakenfist.mariadb._get_database_stub',
                        return_value=stub), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           return_value=reply):
            unpacked = mariadb._grpc_reconcile_scheduler_capacity(
                2.5, 600, 5.0)

        self.assertEqual(self.SUMMARY, unpacked)

    def test_grpc_call_uses_the_bounded_budget(self):
        # This runs inside the cluster daemon's watchdog-petting elected
        # loop, so it must not be able to block for the default
        # GRPC_RETRIES * GRPC_TIMEOUT worst case (issue 3586).
        reply = self._servicer_reply(self.SUMMARY)
        stub = mock.MagicMock()
        with mock.patch('shakenfist.mariadb._get_database_stub',
                        return_value=stub), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           return_value=reply) as mock_call:
            mariadb._grpc_reconcile_scheduler_capacity(2.5, 600, 5.0)
        self.assertEqual(mariadb.BOUNDED_QUERY_TIMEOUT,
                         mock_call.call_args.kwargs['timeout'])
        self.assertEqual(1, mock_call.call_args.kwargs['max_slow_failures'])

    def test_database_unavailable_is_a_quiet_skip(self):
        # The bounded budget makes an exhausted-retries DatabaseUnavailable
        # the expected result of a loaded or restarting database tier. It
        # must be converted to None here rather than escaping to the
        # scheduled task's ignore_exception(), which logs at ERROR with a
        # traceback and writes a recorded-exception file -- every five
        # minutes, for a condition the design calls harmless, and against
        # cluster CI's log-error checks.
        stub = mock.MagicMock()
        with mock.patch('shakenfist.mariadb._get_database_stub',
                        return_value=stub), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           side_effect=exceptions.DatabaseUnavailable(
                               'no gateways')), \
                mock.patch.object(mariadb, 'LOG') as mock_log:
            self.assertIsNone(
                mariadb._grpc_reconcile_scheduler_capacity(2.5, 600, 5.0))

        mock_log.warning.assert_called_once()
        mock_log.error.assert_not_called()

    def test_direct_failure_becomes_an_unsuccessful_reply(self):
        reply = self._servicer_reply(None)
        self.assertFalse(reply.success)

        stub = mock.MagicMock()
        with mock.patch('shakenfist.mariadb._get_database_stub',
                        return_value=stub), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           return_value=reply):
            self.assertIsNone(
                mariadb._grpc_reconcile_scheduler_capacity(2.5, 600, 5.0))

    def test_servicer_swallows_an_unexpected_exception(self):
        # _direct_ only converts OperationalError and IntegrityError to
        # None; anything else propagates and is caught here.
        servicer = database_main.DatabaseService.__new__(
            database_main.DatabaseService)
        servicer.monitor = mock.MagicMock()
        request = database_pb2.ReconcileSchedulerCapacityRequest(
            demand_per_vcpu=2.5, demand_decay_seconds=600,
            disk_overcommit=5.0)
        with mock.patch(
                'shakenfist.mariadb._direct_reconcile_scheduler_capacity',
                side_effect=ValueError('boom')):
            reply = servicer.ReconcileSchedulerCapacity(
                request, mock.MagicMock())
        self.assertFalse(reply.success)


class ReconcilePublicRoutingTestCase(base.ShakenFistTestCase):
    """reconcile_scheduler_capacity() routes and passes config values."""

    @mock.patch('shakenfist.mariadb._grpc_reconcile_scheduler_capacity')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    def test_routes_to_grpc(self, mock_use, mock_grpc):
        with mock.patch.object(mariadb.config,
                               'SCHEDULER_DEMAND_PER_VCPU', 2.5), \
                mock.patch.object(mariadb.config,
                                  'SCHEDULER_DEMAND_DECAY_SECONDS', 600), \
                mock.patch.object(mariadb.config,
                                  'SCHEDULER_DISK_OVERCOMMIT', 5.0):
            mariadb.reconcile_scheduler_capacity()
        mock_grpc.assert_called_once_with(2.5, 600, 5.0)

    @mock.patch('shakenfist.mariadb._direct_reconcile_scheduler_capacity')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    def test_routes_to_direct(self, mock_use, mock_direct):
        with mock.patch.object(mariadb.config,
                               'SCHEDULER_DEMAND_PER_VCPU', 2.5), \
                mock.patch.object(mariadb.config,
                                  'SCHEDULER_DEMAND_DECAY_SECONDS', 600), \
                mock.patch.object(mariadb.config,
                                  'SCHEDULER_DISK_OVERCOMMIT', 5.0):
            mariadb.reconcile_scheduler_capacity()
        mock_direct.assert_called_once_with(2.5, 600, 5.0)
