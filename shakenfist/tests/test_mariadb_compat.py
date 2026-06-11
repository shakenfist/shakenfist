# Copyright 2019 Michael Still and contributors
#
# Tests for the MariaDB compatibility and schema-version verification helpers.

from unittest import mock

import sqlalchemy as sa

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.mariadb import EXPECTED_SCHEMA_VERSIONS
from shakenfist.tests import base


def _make_mock_engine(*scalar_values):
    """Build a mock SA engine whose connection returns scalar_values in order.

    Each positional argument becomes the return value of .scalar() on
    successive conn.execute() calls. Suitable for verify_mariadb_compat
    which issues exactly four SELECT ... scalar() calls.
    """
    mock_engine = mock.MagicMock(spec=sa.Engine)
    mock_conn = mock.MagicMock()
    mock_engine.connect.return_value.__enter__ = mock.Mock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = mock.Mock(return_value=False)

    result_objs = []
    for v in scalar_values:
        r = mock.MagicMock()
        r.scalar.return_value = v
        result_objs.append(r)
    mock_conn.execute.side_effect = result_objs
    return mock_engine


# ---------------------------------------------------------------------------
# _parse_mariadb_version
# ---------------------------------------------------------------------------

class TestParseMariaDBVersion(base.ShakenFistTestCase):
    """Unit tests for mariadb._parse_mariadb_version."""

    def test_plain_mariadb(self):
        self.assertEqual(
            mariadb._parse_mariadb_version('10.6.0-MariaDB'),
            (10, 6, 0),
        )

    def test_debian_ubuntu_long_string(self):
        self.assertEqual(
            mariadb._parse_mariadb_version(
                '10.11.5-MariaDB-1:10.11.5+maria~ubu2204'),
            (10, 11, 5),
        )

    def test_high_patch(self):
        self.assertEqual(
            mariadb._parse_mariadb_version('10.5.99-MariaDB'),
            (10, 5, 99),
        )

    def test_no_trailing_label(self):
        self.assertEqual(
            mariadb._parse_mariadb_version('10.6.0'),
            (10, 6, 0),
        )

    def test_mysql_bare(self):
        # Parses fine; the MariaDB-brand check is a separate gate.
        self.assertEqual(
            mariadb._parse_mariadb_version('8.0.35'),
            (8, 0, 35),
        )

    def test_mysql_ubuntu(self):
        self.assertEqual(
            mariadb._parse_mariadb_version('8.0.36-0ubuntu0.22.04.1'),
            (8, 0, 36),
        )

    def test_garbage_raises_value_error(self):
        with self.assertRaises(ValueError):
            mariadb._parse_mariadb_version('garbage')

    def test_only_two_parts_raises_value_error(self):
        with self.assertRaises(ValueError):
            mariadb._parse_mariadb_version('10.6')

    def test_non_numeric_raises_value_error(self):
        with self.assertRaises(ValueError):
            mariadb._parse_mariadb_version('a.b.c')


# ---------------------------------------------------------------------------
# verify_mariadb_compat
# ---------------------------------------------------------------------------

_GOOD_VERSION = '10.11.5-MariaDB-1:10.11.5+maria~ubu2204'
_GOOD_ENGINE = 'InnoDB'
_GOOD_CHARSET = 'utf8mb4'
_GOOD_COLLATION = 'utf8mb4_unicode_ci'


class TestVerifyMariaDBCompat(base.ShakenFistTestCase):
    """Unit tests for mariadb.verify_mariadb_compat."""

    def _good_engine(
        self,
        version=_GOOD_VERSION,
        engine=_GOOD_ENGINE,
        charset=_GOOD_CHARSET,
        collation=_GOOD_COLLATION,
    ):
        return _make_mock_engine(version, engine, charset, collation)

    def test_all_checks_pass(self):
        # Should return None and raise nothing.
        result = mariadb.verify_mariadb_compat(self._good_engine())
        self.assertIsNone(result)

    def test_version_below_floor(self):
        mock_engine = self._good_engine(version='10.5.0-MariaDB')
        with self.assertRaises(exceptions.MariaDBIncompatibleError) as ctx:
            mariadb.verify_mariadb_compat(mock_engine)
        msg = str(ctx.exception)
        self.assertIn('10.5.0', msg)
        self.assertIn('10.6.0', msg)

    def test_non_mariadb_server(self):
        # '8.0.35' contains no 'MariaDB' substring.
        mock_engine = self._good_engine(version='8.0.35')
        with self.assertRaises(exceptions.MariaDBIncompatibleError) as ctx:
            mariadb.verify_mariadb_compat(mock_engine)
        msg = str(ctx.exception)
        self.assertIn('non-MariaDB', msg)

    def test_version_and_brand_both_fail(self):
        # '10.5.0' is both below floor AND has no 'MariaDB' brand.
        mock_engine = self._good_engine(version='10.5.0')
        with self.assertRaises(exceptions.MariaDBIncompatibleError) as ctx:
            mariadb.verify_mariadb_compat(mock_engine)
        msg = str(ctx.exception)
        self.assertIn('older than', msg)
        self.assertIn('non-MariaDB', msg)

    def test_unparseable_version(self):
        mock_engine = self._good_engine(version='foo')
        with self.assertRaises(exceptions.MariaDBIncompatibleError) as ctx:
            mariadb.verify_mariadb_compat(mock_engine)
        msg = str(ctx.exception)
        self.assertIn('Could not parse', msg)

    def test_engine_mismatch(self):
        mock_engine = self._good_engine(engine='MyISAM')
        with self.assertRaises(exceptions.MariaDBIncompatibleError) as ctx:
            mariadb.verify_mariadb_compat(mock_engine)
        msg = str(ctx.exception)
        self.assertIn('MyISAM', msg)
        self.assertIn('InnoDB', msg)

    def test_charset_mismatch(self):
        mock_engine = self._good_engine(charset='latin1')
        with self.assertRaises(exceptions.MariaDBIncompatibleError) as ctx:
            mariadb.verify_mariadb_compat(mock_engine)
        msg = str(ctx.exception)
        self.assertIn('latin1', msg)
        self.assertIn('utf8mb4', msg)

    def test_collation_mismatch(self):
        mock_engine = self._good_engine(collation='latin1_swedish_ci')
        with self.assertRaises(exceptions.MariaDBIncompatibleError) as ctx:
            mariadb.verify_mariadb_compat(mock_engine)
        msg = str(ctx.exception)
        self.assertIn('latin1_swedish_ci', msg)
        self.assertIn('utf8mb4_', msg)

    def test_all_four_mismatches(self):
        # All four independent checks fail simultaneously.  Use a parseable
        # but below-floor MariaDB version so check (a) contributes exactly one
        # bullet ("older than …") rather than two.  The four bullets are:
        # (a) version below floor, (b) engine, (c) charset, (d) collation.
        mock_engine = _make_mock_engine(
            '10.5.0-MariaDB', 'MyISAM', 'latin1', 'latin1_swedish_ci')
        with self.assertRaises(exceptions.MariaDBIncompatibleError) as ctx:
            mariadb.verify_mariadb_compat(mock_engine)
        msg = str(ctx.exception)
        # Exactly four bullet lines (one per check).
        self.assertEqual(msg.count('  - '), 4)


# ---------------------------------------------------------------------------
# verify_schema_versions
# ---------------------------------------------------------------------------

class TestVerifySchemaVersions(base.ShakenFistTestCase):
    """Unit tests for mariadb.verify_schema_versions."""

    def _mock_engine(self):
        return mock.MagicMock(spec=sa.Engine)

    def _patch_inspect(self, has_table_return):
        """Return a context-manager patch for sa.inspect(engine).has_table."""
        mock_inspect = mock.MagicMock()
        mock_inspect.return_value.has_table.return_value = has_table_return
        return mock.patch('shakenfist.mariadb.sa.inspect', mock_inspect)

    def test_schema_versions_table_missing(self):
        mock_engine = self._mock_engine()
        with self._patch_inspect(False):
            with self.assertRaises(exceptions.SchemaVersionMismatchError) as ctx:
                mariadb.verify_schema_versions(mock_engine)
        msg = str(ctx.exception)
        self.assertIn('has not been initialised', msg)
        self.assertIn('sf-ctl ensure-mariadb-schema', msg)

    def test_all_versions_match(self):
        mock_engine = self._mock_engine()
        with self._patch_inspect(True):
            with mock.patch(
                'shakenfist.mariadb._get_table_version',
                side_effect=lambda eng, name: EXPECTED_SCHEMA_VERSIONS[name],
            ):
                result = mariadb.verify_schema_versions(mock_engine)
        self.assertIsNone(result)

    def test_one_mismatch(self):
        expected_ver = EXPECTED_SCHEMA_VERSIONS['object_states']
        wrong_ver = expected_ver - 1

        def side_effect(eng, name):
            if name == 'object_states':
                return wrong_ver
            return EXPECTED_SCHEMA_VERSIONS[name]

        mock_engine = self._mock_engine()
        with self._patch_inspect(True):
            with mock.patch(
                'shakenfist.mariadb._get_table_version',
                side_effect=side_effect,
            ):
                with self.assertRaises(exceptions.SchemaVersionMismatchError) as ctx:
                    mariadb.verify_schema_versions(mock_engine)
        msg = str(ctx.exception)
        self.assertIn(
            f'object_states: expected v{expected_ver}, found v{wrong_ver}', msg)

    def test_missing_table_counted_as_zero(self):
        expected_ver = EXPECTED_SCHEMA_VERSIONS['object_states']

        def side_effect(eng, name):
            if name == 'object_states':
                return 0
            return EXPECTED_SCHEMA_VERSIONS[name]

        mock_engine = self._mock_engine()
        with self._patch_inspect(True):
            with mock.patch(
                'shakenfist.mariadb._get_table_version',
                side_effect=side_effect,
            ):
                with self.assertRaises(exceptions.SchemaVersionMismatchError) as ctx:
                    mariadb.verify_schema_versions(mock_engine)
        msg = str(ctx.exception)
        self.assertIn(
            f'object_states: expected v{expected_ver}, found v0', msg)

    def test_multiple_mismatches_in_one_exception(self):
        expected_states = EXPECTED_SCHEMA_VERSIONS['object_states']
        expected_instances = EXPECTED_SCHEMA_VERSIONS['instances']

        def side_effect(eng, name):
            if name == 'object_states':
                return expected_states - 1
            if name == 'instances':
                return expected_instances - 1
            return EXPECTED_SCHEMA_VERSIONS[name]

        mock_engine = self._mock_engine()
        with self._patch_inspect(True):
            with mock.patch(
                'shakenfist.mariadb._get_table_version',
                side_effect=side_effect,
            ):
                with self.assertRaises(exceptions.SchemaVersionMismatchError) as ctx:
                    mariadb.verify_schema_versions(mock_engine)
        msg = str(ctx.exception)
        self.assertIn('object_states', msg)
        self.assertIn('instances', msg)
        self.assertEqual(msg.count('  - '), 2)

    def test_mismatch_message_contains_fix_pointer(self):
        expected_ver = EXPECTED_SCHEMA_VERSIONS['object_states']

        def side_effect(eng, name):
            if name == 'object_states':
                return expected_ver - 1
            return EXPECTED_SCHEMA_VERSIONS[name]

        mock_engine = self._mock_engine()
        with self._patch_inspect(True):
            with mock.patch(
                'shakenfist.mariadb._get_table_version',
                side_effect=side_effect,
            ):
                with self.assertRaises(exceptions.SchemaVersionMismatchError) as ctx:
                    mariadb.verify_schema_versions(mock_engine)
        msg = str(ctx.exception)
        self.assertIn('sf-ctl ensure-mariadb-schema', msg)
