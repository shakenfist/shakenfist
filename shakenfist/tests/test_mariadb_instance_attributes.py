# Copyright 2019 Michael Still and contributors
#
# Tests for per-column instance_attributes updates. The full-row
# read-modify-write these replace was a cross-attribute lost update:
# two writers of different attributes on different nodes (the
# sidechannel monitor's agent state cache and the API's agent
# operation enqueue) could interleave so the second write reverted the
# first writer's committed column to the stale value it had read.

import uuid
from unittest import mock

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.schema.artifact_attributes import ArtifactAttributesData
from shakenfist.schema.instance_attributes import InstanceAttributesData
from shakenfist.schema.network_attributes import NetworkAttributesData
from shakenfist.tests import base


ALL_COLUMNS = {
    'placement', 'power_state', 'ports', 'enforced_deletes',
    'block_devices', 'agent_state', 'agent_attributes',
    'agent_operations', 'kvm_pid', 'error_message', 'vsock_cids',
}


class ColumnValuesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.data = InstanceAttributesData(
            uuid=uuid.uuid4(),
            placement={'node': 'node01'},
            agent_operations={'queue': ['op1'], 'all': ['op1']})

    def test_no_mask_returns_every_column(self):
        values = mariadb._instance_attributes_column_values(self.data)
        self.assertEqual(ALL_COLUMNS, set(values))

    def test_empty_mask_returns_every_column(self):
        values = mariadb._instance_attributes_column_values(self.data, [])
        self.assertEqual(ALL_COLUMNS, set(values))

    def test_mask_limits_columns(self):
        values = mariadb._instance_attributes_column_values(
            self.data, ['agent_operations'])
        self.assertEqual({'agent_operations'}, set(values))
        self.assertEqual(
            '{"queue": ["op1"], "all": ["op1"]}',
            values['agent_operations'])

    def test_mask_with_multiple_fields(self):
        values = mariadb._instance_attributes_column_values(
            self.data, ['placement', 'kvm_pid'])
        self.assertEqual({'placement', 'kvm_pid'}, set(values))

    def test_unknown_field_rejected(self):
        self.assertRaises(
            ValueError, mariadb._instance_attributes_column_values,
            self.data, ['agent_operations', 'not_a_column'])


class DirectUpdateFieldMaskTestCase(base.ShakenFistTestCase):
    """The masked UPDATE must only write the named columns."""

    def _run_update(self, fields):
        data = InstanceAttributesData(
            uuid=uuid.uuid4(),
            agent_attributes={'facts': {'os': 'debian'}},
            agent_operations={'queue': ['op1'], 'all': ['op1']})

        mock_engine = mock.MagicMock(spec=sa.Engine)
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        result = mock.MagicMock()
        result.rowcount = 1
        mock_conn.execute.return_value = result

        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=mock_engine):
            self.assertTrue(mariadb._direct_update_instance_attributes(
                data, fields=fields))

        stmt = mock_conn.execute.call_args[0][0]
        return stmt.compile().params

    def test_masked_update_writes_only_named_column(self):
        params = self._run_update(['agent_attributes'])
        self.assertIn('agent_attributes', params)
        self.assertNotIn('agent_operations', params)
        self.assertNotIn('placement', params)

    def test_unmasked_update_writes_every_column(self):
        params = self._run_update(None)
        for column in ALL_COLUMNS:
            self.assertIn(column, params)


class NetworkColumnValuesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.data = NetworkAttributesData(
            uuid=uuid.uuid4(),
            floating_gateway='192.168.20.2',
            hosteddns={'www': '192.168.20.10'})

    def test_no_mask_returns_every_column(self):
        values = mariadb._network_attributes_column_values(self.data)
        self.assertEqual({'floating_gateway', 'hosteddns'}, set(values))

    def test_mask_limits_columns(self):
        values = mariadb._network_attributes_column_values(
            self.data, ['hosteddns'])
        self.assertEqual({'hosteddns'}, set(values))
        self.assertEqual('{"www": "192.168.20.10"}', values['hosteddns'])

    def test_unknown_field_rejected(self):
        self.assertRaises(
            ValueError, mariadb._network_attributes_column_values,
            self.data, ['floating_gateway', 'not_a_column'])


class ArtifactColumnValuesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.data = ArtifactAttributesData(
            uuid=uuid.uuid4(),
            max_versions=3,
            shared=True,
            highest_index=7)

    def test_no_mask_returns_every_column(self):
        values = mariadb._artifact_attributes_column_values(self.data)
        self.assertEqual(
            {'max_versions', 'shared', 'highest_index'}, set(values))

    def test_mask_limits_columns(self):
        values = mariadb._artifact_attributes_column_values(
            self.data, ['highest_index'])
        self.assertEqual({'highest_index'}, set(values))
        self.assertEqual(7, values['highest_index'])

    def test_unknown_field_rejected(self):
        self.assertRaises(
            ValueError, mariadb._artifact_attributes_column_values,
            self.data, ['shared', 'not_a_column'])
