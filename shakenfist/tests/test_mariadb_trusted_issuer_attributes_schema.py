# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Tests for the trusted_issuer_attributes discovery columns.

The migration DDL runs against a mocked engine, so no real MariaDB is
needed; these tests exercise the version branching and the generated SQL,
not its execution (functional CI covers the live migration).
"""

from unittest import mock
from uuid import uuid4

from shakenfist import mariadb
from shakenfist.schema.trusted_issuer_attributes import TrustedIssuerAttributesData
from shakenfist.tests import base

V1_COLUMNS = ('uuid', 'issuer_url', 'jwks_uri', 'audience')


class EnsureTrustedIssuerAttributesSchemaTestCase(base.ShakenFistTestCase):
    """Tests for _ensure_trusted_issuer_attributes_schema() v1 -> v2."""

    def _executed(self, mock_engine):
        conn = mock_engine.begin.return_value.__enter__.return_value
        return [str(call.args[0]) for call in conn.execute.call_args_list]

    def test_table_definition_matches_model(self):
        table = mariadb._get_trusted_issuer_attributes_table()
        for name in mariadb.TRUSTED_ISSUER_ATTRIBUTES_V2_COLUMNS:
            self.assertIn(name, table.c)
            self.assertTrue(table.c[name].nullable)
        self.assertTrue(table.c['jwks_uri'].nullable)
        self.assertFalse(table.c['issuer_url'].nullable)
        self.assertFalse(table.c['audience'].nullable)

    @mock.patch('shakenfist.mariadb.get_table_columns')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=1)
    def test_v1_to_v2_adds_columns_and_relaxes_jwks_uri(
            self, mock_get_version, mock_set_version, mock_get_columns):
        mock_get_columns.return_value = {name: {} for name in V1_COLUMNS}
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_trusted_issuer_attributes_schema(mock_engine)

        # A v1 table runs on through v3 in the same call, so the end
        # version is the target and both steps are recorded.
        self.assertEqual(result['start_version'], 1)
        self.assertEqual(result['end_version'], 3)
        self.assertTrue(result['migrated'])
        self.assertEqual(
            [mock.call(mock_engine, 'trusted_issuer_attributes', 2),
             mock.call(mock_engine, 'trusted_issuer_attributes', 3)],
            mock_set_version.call_args_list)

        executed = self._executed(mock_engine)
        joined = ' '.join(executed)
        for name in mariadb.TRUSTED_ISSUER_ATTRIBUTES_V2_COLUMNS:
            self.assertIn(
                f'ALTER TABLE trusted_issuer_attributes ADD COLUMN {name} ',
                joined)
        self.assertIn('oidc_scopes VARCHAR(255) NULL', joined)
        self.assertIn('client_id VARCHAR(255) NULL', joined)
        self.assertIn('authorization_endpoint LONGTEXT NULL', joined)
        self.assertIn('token_endpoint LONGTEXT NULL', joined)
        self.assertIn('device_authorization_endpoint LONGTEXT NULL', joined)
        self.assertIn('discovered_at DOUBLE NULL', joined)
        # The v2 step relaxes jwks_uri, at the width the model declares
        # rather than the 255 a str field used to get regardless.
        self.assertIn(
            'ALTER TABLE trusted_issuer_attributes MODIFY COLUMN '
            'jwks_uri VARCHAR(1024) NULL', executed)
        self.assertEqual(10, len(executed))

    @mock.patch('shakenfist.mariadb.get_table_columns')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=1)
    def test_v1_to_v2_is_idempotent(
            self, mock_get_version, mock_set_version, mock_get_columns):
        """A re-run against a table that already has the columns adds
        nothing, but still advances the version."""
        table = mariadb._get_trusted_issuer_attributes_table()
        mock_get_columns.return_value = {c.name: {} for c in table.c}
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_trusted_issuer_attributes_schema(mock_engine)

        self.assertEqual(result['end_version'], 3)
        executed = self._executed(mock_engine)
        self.assertFalse([e for e in executed if 'ADD COLUMN' in e])
        # One from v2 for jwks_uri, three from v3.
        self.assertEqual(4, len([e for e in executed if 'MODIFY COLUMN' in e]))

    @mock.patch('shakenfist.mariadb.get_table_columns')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_v2_to_v3_widens_the_declared_columns(
            self, mock_get_version, mock_set_version, mock_get_columns):
        """The three columns whose declaration their column could not hold.

        Each declares max_length=1024 and was created as VARCHAR(255),
        so a value the model accepted could not be stored (issue 4901).
        The widths come from the table definition, which now reads the
        declaration, so this cannot drift from the model.
        """
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_trusted_issuer_attributes_schema(mock_engine)

        self.assertEqual(result['start_version'], 2)
        self.assertEqual(result['end_version'], 3)
        self.assertTrue(result['migrated'])
        mock_set_version.assert_called_once_with(
            mock_engine, 'trusted_issuer_attributes', 3)

        executed = self._executed(mock_engine)
        self.assertEqual(
            ['ALTER TABLE trusted_issuer_attributes MODIFY COLUMN '
             'issuer_url VARCHAR(1024) NOT NULL',
             'ALTER TABLE trusted_issuer_attributes MODIFY COLUMN '
             'jwks_uri VARCHAR(1024) NULL',
             'ALTER TABLE trusted_issuer_attributes MODIFY COLUMN '
             'audience VARCHAR(1024) NOT NULL'],
            executed)
        # No ADD COLUMN: a v2 table already has every column.
        mock_get_columns.assert_not_called()

    @mock.patch('shakenfist.mariadb.get_table_columns')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_v2_to_v3_keeps_each_column_as_nullable_as_it_was(
            self, mock_get_version, mock_set_version, mock_get_columns):
        """MODIFY COLUMN replaces a whole definition.

        A MODIFY which left the nullability out would quietly make
        jwks_uri NOT NULL, which is the opposite of what the v2
        migration went to the trouble of relaxing.
        """
        mock_engine = mock.MagicMock()
        mariadb._ensure_trusted_issuer_attributes_schema(mock_engine)

        table = mariadb._get_trusted_issuer_attributes_table()
        for statement in self._executed(mock_engine):
            # The column name, its type and then the rest, which is the
            # whole nullability clause. Compared by equality rather than
            # with endswith(), because 'NOT NULL' ends with 'NULL' and
            # an endswith() test would pass for exactly the statement
            # this exists to catch.
            column_name, _, null_clause = statement.split(
                'MODIFY COLUMN ')[1].split(None, 2)
            expected = ('NULL' if table.c[column_name].nullable
                        else 'NOT NULL')
            self.assertEqual(
                expected, null_clause,
                f'{column_name} should be modified {expected}: {statement}')

    @mock.patch('shakenfist.mariadb.get_table_columns')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=3)
    def test_already_at_v3_is_noop(
            self, mock_get_version, mock_set_version, mock_get_columns):
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_trusted_issuer_attributes_schema(mock_engine)

        self.assertEqual(result['end_version'], 3)
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()
        mock_get_columns.assert_not_called()
        self.assertEqual([], self._executed(mock_engine))

    def test_expected_version_registered(self):
        self.assertEqual(
            3, mariadb.EXPECTED_SCHEMA_VERSIONS['trusted_issuer_attributes'])

    def test_a_fresh_install_needs_no_widening(self):
        """create_all() builds the wide columns from the model.

        So the v3 migration exists only for a cluster created before the
        column builder read max_length, and the two paths agree.
        """
        table = mariadb._get_trusted_issuer_attributes_table()
        for name in mariadb.TRUSTED_ISSUER_ATTRIBUTES_V3_WIDENED_COLUMNS:
            self.assertEqual(
                1024, table.c[name].type.length,
                f'{name} should be built wide enough for its declaration')


class TrustedIssuerAttributesProtoTestCase(base.ShakenFistTestCase):
    """The gRPC helpers keep NULL distinct from the empty string."""

    def _data(self, **kwargs):
        return TrustedIssuerAttributesData(
            uuid=uuid4(), issuer_url='https://idp.example.com',
            audience='sf', **kwargs)

    def test_unset_fields_round_trip_as_none(self):
        data = self._data()
        proto = mariadb._trusted_issuer_attrs_to_proto(data)
        self.assertFalse(proto.HasField('jwks_uri'))
        self.assertEqual(data, mariadb._trusted_issuer_attrs_from_proto(proto))

    def test_populated_fields_round_trip(self):
        data = self._data(
            jwks_uri='https://idp.example.com/jwks',
            authorization_endpoint='https://idp.example.com/auth',
            token_endpoint='https://idp.example.com/token',
            device_authorization_endpoint='https://idp.example.com/device',
            client_id='sf-cli', oidc_scopes='openid profile',
            discovered_at=1762800000.5)
        proto = mariadb._trusted_issuer_attrs_to_proto(data)
        self.assertEqual(data, mariadb._trusted_issuer_attrs_from_proto(proto))

    def test_empty_string_is_not_none(self):
        data = self._data(client_id='')
        proto = mariadb._trusted_issuer_attrs_to_proto(data)
        self.assertTrue(proto.HasField('client_id'))
        self.assertEqual(
            '', mariadb._trusted_issuer_attrs_from_proto(proto).client_id)
