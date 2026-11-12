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

        self.assertEqual(result['start_version'], 1)
        self.assertEqual(result['end_version'], 2)
        self.assertTrue(result['migrated'])
        mock_set_version.assert_called_once_with(
            mock_engine, 'trusted_issuer_attributes', 2)

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
        self.assertIn(
            'ALTER TABLE trusted_issuer_attributes MODIFY COLUMN '
            'jwks_uri VARCHAR(255) NULL', executed)
        self.assertEqual(7, len(executed))

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

        self.assertEqual(result['end_version'], 2)
        executed = self._executed(mock_engine)
        self.assertFalse([e for e in executed if 'ADD COLUMN' in e])
        self.assertEqual(1, len([e for e in executed if 'MODIFY COLUMN' in e]))
        mock_set_version.assert_called_once_with(
            mock_engine, 'trusted_issuer_attributes', 2)

    @mock.patch('shakenfist.mariadb.get_table_columns')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_already_at_v2_is_noop(
            self, mock_get_version, mock_set_version, mock_get_columns):
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_trusted_issuer_attributes_schema(mock_engine)

        self.assertEqual(result['end_version'], 2)
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()
        mock_get_columns.assert_not_called()
        self.assertEqual([], self._executed(mock_engine))

    def test_expected_version_registered(self):
        self.assertEqual(
            2, mariadb.EXPECTED_SCHEMA_VERSIONS['trusted_issuer_attributes'])


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
