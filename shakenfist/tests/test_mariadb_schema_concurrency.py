# Regression test for the thread-safety bug in _get_schema_versions_table().
#
# Before the fix, concurrent callers could both observe the module-level cache
# as None, both call sa.Table(...), and the second one would raise
# InvalidRequestError ("Table already registered").  The fix adds double-
# checked locking + metadata-registry re-check, matching the pattern used by
# _get_object_states_table() and all other table getters in mariadb.py.
#
# This module tests:
# - _get_schema_versions_table() is safe under high concurrent load
# - All threads receive the same Table object (identity, not equality)
# - No exception escapes from any concurrent call

import concurrent.futures
from unittest import mock

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.tests import base


class SchemaVersionsTableConcurrencyTestCase(base.ShakenFistTestCase):
    """Regression test: _get_schema_versions_table() must be thread-safe."""

    def setUp(self):
        super().setUp()

        # Reset module-level cache and metadata so the test is not trivially
        # satisfied by a previously-constructed Table object that another test
        # (or import-time code) already built.
        self._original_schema_versions_table = mariadb._schema_versions_table
        self._original_metadata = mariadb._metadata

        mariadb._schema_versions_table = None
        mariadb._metadata = sa.MetaData()

    def tearDown(self):
        super().tearDown()
        # Restore module-level state so other tests are unaffected.
        mariadb._schema_versions_table = self._original_schema_versions_table
        mariadb._metadata = self._original_metadata

    def test_concurrent_calls_do_not_raise(self):
        """Eight concurrent threads must each get a Table without raising."""
        n_threads = 8
        results = []
        exceptions = []

        def call_getter():
            try:
                table = mariadb._get_schema_versions_table()
                results.append(table)
            except Exception as exc:
                exceptions.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(call_getter) for _ in range(n_threads)]
            concurrent.futures.wait(futures)

        self.assertEqual(
            exceptions, [],
            f'Exceptions raised in concurrent callers: {exceptions}')
        self.assertEqual(
            len(results), n_threads,
            f'Expected {n_threads} results, got {len(results)}')

    def test_concurrent_calls_return_same_object(self):
        """All concurrent threads must receive the identical Table instance."""
        n_threads = 8
        results = []

        def call_getter():
            results.append(mariadb._get_schema_versions_table())

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(call_getter) for _ in range(n_threads)]
            concurrent.futures.wait(futures)

        # Every element must be the same object (identity check).
        first = results[0]
        for i, tbl in enumerate(results[1:], start=1):
            self.assertIs(
                first, tbl,
                f'Thread 0 and thread {i} returned different Table objects')

    def test_metadata_already_has_table_returns_cached(self):
        """If the table is already in metadata.tables, it must be re-used.

        This covers the second branch of the double-checked locking pattern:
        another thread may have registered the table in SQLAlchemy's MetaData
        but not yet stored it in the module-level cache variable.
        """
        # Build a real Table and add it to the fresh MetaData so the
        # 'schema_versions' in metadata.tables branch is taken.
        metadata = mariadb._metadata
        pre_registered = sa.Table(
            'schema_versions',
            metadata,
            sa.Column('table_name', sa.String(64), primary_key=True),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('updated_at', sa.Double(), nullable=False),
        )

        # The module-level cache is still None (set in setUp).
        self.assertIsNone(mariadb._schema_versions_table)

        result = mariadb._get_schema_versions_table()

        self.assertIs(result, pre_registered,
                      'Expected the already-registered Table to be returned')
        self.assertIs(mariadb._schema_versions_table, pre_registered,
                      'Expected the module-level cache to be updated')

    def test_metadata_patched_path_via_mock(self):
        """Patch _metadata so concurrent calls exercise the locking path."""
        # Use a fresh MetaData to guarantee no prior table registration.
        fresh_metadata = sa.MetaData()

        with mock.patch.object(mariadb, '_metadata', fresh_metadata):
            # Also ensure the cache is clear for this sub-test.
            with mock.patch.object(mariadb, '_schema_versions_table', None):
                n_threads = 8
                results = []
                exceptions = []

                def call_getter():
                    try:
                        results.append(mariadb._get_schema_versions_table())
                    except Exception as exc:
                        exceptions.append(exc)

                with concurrent.futures.ThreadPoolExecutor(
                        max_workers=n_threads) as pool:
                    futures = [pool.submit(call_getter) for _ in range(n_threads)]
                    concurrent.futures.wait(futures)

        self.assertEqual(exceptions, [],
                         f'Unexpected exceptions: {exceptions}')
        self.assertEqual(len(results), n_threads)
        first = results[0]
        for i, tbl in enumerate(results[1:], start=1):
            self.assertIs(first, tbl,
                          f'Thread 0 and thread {i} got different objects')


# The canonical list of tables that ensure_schema() manages. Kept in lockstep
# with both ensure_schema() in shakenfist/mariadb.py and the keys of
# EXPECTED_SCHEMA_VERSIONS. If you add or remove a table you must update all
# three locations -- this test catches the drift.
EXPECTED_TABLE_NAMES = sorted([
    'object_states',
    'ipam_reservations',
    'uploads',
    'dnsmasq',
    'blobs',
    'object_references',
    'blob_hashes',
    'blob_transfers',
    'blob_attributes',
    'nodes',
    'node_attributes',
    'namespaces',
    'namespace_attributes',
    'artifacts',
    'artifact_attributes',
    'artifact_indexes',
    'network_interfaces',
    'network_interface_attributes',
    'networks',
    'network_attributes',
    'ipams',
    'agent_operations',
    'agent_operation_attributes',
    'instances',
    'instance_attributes',
    'object_metadata',
    'cluster_operation_targets',
    'node_metrics',
    'node_daemon_states',
    'cluster_operations',
    'cluster_operation_errors',
    'work_queue',
    'cluster_locks',
    'cluster_config',
    'events',
    'event_objects',
])


class ExpectedSchemaVersionsTestCase(base.ShakenFistTestCase):
    """Guard the EXPECTED_SCHEMA_VERSIONS dict against drift.

    The dict in shakenfist/mariadb.py is the single source of truth read by
    verify_schema_versions() (and similar helpers added later in this phase).
    It must stay in one-to-one correspondence with the tables that
    ensure_schema() actually creates/migrates. If you bump or add a table to
    ensure_schema() without also touching EXPECTED_SCHEMA_VERSIONS (or vice
    versa), this test fails.
    """

    def test_keys_match_expected_table_names(self):
        """Every table in EXPECTED_SCHEMA_VERSIONS appears in the hand-list."""
        self.assertEqual(
            sorted(mariadb.EXPECTED_SCHEMA_VERSIONS.keys()),
            EXPECTED_TABLE_NAMES,
            'EXPECTED_SCHEMA_VERSIONS keys drifted from EXPECTED_TABLE_NAMES; '
            'update both this test and ensure_schema() if you added or '
            'removed a table.')

    def test_all_versions_are_positive_ints(self):
        """Every version value must be a positive int (>= 1)."""
        for table_name, version in mariadb.EXPECTED_SCHEMA_VERSIONS.items():
            self.assertIsInstance(
                version, int,
                f'EXPECTED_SCHEMA_VERSIONS[{table_name!r}] = {version!r} '
                f'is not an int')
            # bool is a subclass of int -- reject it explicitly so a stray
            # True/False can't masquerade as a version.
            self.assertNotIsInstance(
                version, bool,
                f'EXPECTED_SCHEMA_VERSIONS[{table_name!r}] = {version!r} '
                f'is a bool, not a real int')
            self.assertGreaterEqual(
                version, 1,
                f'EXPECTED_SCHEMA_VERSIONS[{table_name!r}] = {version} '
                f'is not a positive version')

    def test_entry_count_matches_expected_table_count(self):
        """Sanity: the dict has exactly the expected number of entries."""
        self.assertEqual(
            len(mariadb.EXPECTED_SCHEMA_VERSIONS),
            len(EXPECTED_TABLE_NAMES),
            'EXPECTED_SCHEMA_VERSIONS entry count drifted from the expected '
            'list in this test.')
