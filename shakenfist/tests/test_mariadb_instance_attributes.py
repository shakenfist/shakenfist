# Copyright 2019 Michael Still and contributors
#
# Tests for per-column instance_attributes updates. The full-row
# read-modify-write these replace was a cross-attribute lost update:
# two writers of different attributes on different nodes (the
# sidechannel monitor's agent state cache and the API's agent
# operation enqueue) could interleave so the second write reverted the
# first writer's committed column to the stale value it had read.

import json
import uuid
from unittest import mock

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.schema.agentoperation_attributes import (
    AgentOperationAttributesData)
from shakenfist.schema.artifact_attributes import ArtifactAttributesData
from shakenfist.schema.blob_attributes import BlobAttributesData
from shakenfist.schema.instance_attributes import InstanceAttributesData
from shakenfist.schema.mapping_rule_attributes import (
    MappingRuleAttributesData)
from shakenfist.schema.namespace_attributes import NamespaceAttributesData
from shakenfist.schema.network_attributes import NetworkAttributesData
from shakenfist.schema.node_attributes import NodeAttributesData
from shakenfist.schema.trusted_issuer_attributes import (
    TrustedIssuerAttributesData)
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


class AgentOperationColumnValuesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.data = AgentOperationAttributesData(
            uuid=uuid.uuid4(),
            results={'0': {'status': 0}},
            last_progress=1234.5,
            attempts=2)

    def test_no_mask_returns_every_column(self):
        values = mariadb._agent_operation_attributes_column_values(self.data)
        self.assertEqual(
            {'results', 'last_progress', 'attempts', 'expiry_reason'},
            set(values))

    def test_mask_limits_columns(self):
        values = mariadb._agent_operation_attributes_column_values(
            self.data, ['results'])
        self.assertEqual({'results'}, set(values))
        self.assertEqual('{"0": {"status": 0}}', values['results'])

    def test_mask_limits_columns_for_progress_fields(self):
        # The point of the mask is that a writer of one attribute
        # cannot push a stale snapshot of the others over a concurrent
        # writer's committed change. Now that three columns exist,
        # that is a real hazard rather than a theoretical one.
        values = mariadb._agent_operation_attributes_column_values(
            self.data, ['last_progress'])
        self.assertEqual({'last_progress'}, set(values))
        self.assertEqual(1234.5, values['last_progress'])

        values = mariadb._agent_operation_attributes_column_values(
            self.data, ['attempts'])
        self.assertEqual({'attempts'}, set(values))
        self.assertEqual(2, values['attempts'])

        values = mariadb._agent_operation_attributes_column_values(
            self.data, ['expiry_reason'])
        self.assertEqual({'expiry_reason'}, set(values))
        self.assertIsNone(values['expiry_reason'])

    def test_unknown_field_rejected(self):
        self.assertRaises(
            ValueError, mariadb._agent_operation_attributes_column_values,
            self.data, ['results', 'not_a_column'])


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


NODE_ALL_COLUMNS = {
    'last_seen', 'installed_version', 'spice_server_cert_subject',
    'is_etcd_master', 'is_hypervisor', 'is_network_node', 'is_eventlog_node',
    'is_database_node', 'daemons', 'daemon_states',
    'qemu_version', 'libvirt_version', 'python_version',
    'python_implementation', 'dependency_versions', 'process_metrics',
}


class NodeColumnValuesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.data = NodeAttributesData(
            uuid=uuid.uuid4(),
            last_seen=1234567890.0,
            daemons=['queues', 'resources'],
            process_metrics={'process_cpu_time_sf_api': 1.5})

    def test_no_mask_returns_every_column(self):
        values = mariadb._node_attributes_column_values(self.data)
        self.assertEqual(NODE_ALL_COLUMNS, set(values))

    def test_mask_limits_columns(self):
        # This mask is the one observe_this_node uses: it must never
        # include the daemons list, whose writers hold a different
        # attribute lock.
        values = mariadb._node_attributes_column_values(
            self.data, ['last_seen', 'installed_version', 'is_etcd_master',
                        'is_hypervisor', 'is_network_node',
                        'is_eventlog_node', 'is_database_node'])
        self.assertNotIn('daemons', values)
        self.assertEqual(1234567890.0, values['last_seen'])

    def test_unknown_field_rejected(self):
        self.assertRaises(
            ValueError, mariadb._node_attributes_column_values,
            self.data, ['last_seen', 'not_a_column'])


class NamespaceColumnValuesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.data = NamespaceAttributesData(
            name='testspace',
            keys={'nonced_keys': {'deploy': {'key': 'abc', 'nonce': 'n'}}},
            trust=['system'])

    def test_no_mask_returns_every_column(self):
        values = mariadb._namespace_attributes_column_values(self.data)
        self.assertEqual({'keys', 'trust'}, set(values))

    def test_mask_limits_columns(self):
        values = mariadb._namespace_attributes_column_values(
            self.data, ['trust'])
        self.assertEqual({'trust'}, set(values))
        self.assertEqual(['system'], values['trust'])

    def test_unknown_field_rejected(self):
        self.assertRaises(
            ValueError, mariadb._namespace_attributes_column_values,
            self.data, ['keys', 'not_a_column'])


class BlobColumnValuesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.data = BlobAttributesData(
            uuid=uuid.uuid4(),
            size=1024,
            info={'mime-type': 'application/octet-stream'},
            last_used=1234567890.0,
            expires_at=1244567890.0)

    def test_no_mask_returns_every_column(self):
        values = mariadb._blob_attributes_column_values(self.data)
        self.assertEqual(
            {'size', 'info', 'last_used', 'expires_at'}, set(values))

    def test_mask_limits_columns(self):
        # set_lifetime must not carry a stale last_used along with its
        # expires_at write.
        values = mariadb._blob_attributes_column_values(
            self.data, ['expires_at'])
        self.assertEqual({'expires_at'}, set(values))
        self.assertEqual(1244567890.0, values['expires_at'])

    def test_unknown_field_rejected(self):
        self.assertRaises(
            ValueError, mariadb._blob_attributes_column_values,
            self.data, ['expires_at', 'not_a_column'])


class TrustedIssuerColumnValuesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.data = TrustedIssuerAttributesData(
            uuid=uuid.uuid4(),
            issuer_url='https://token.actions.githubusercontent.com',
            jwks_uri='https://token.actions.githubusercontent.com/jwks',
            audience='https://sf.example.com')

    def test_no_mask_returns_every_column(self):
        values = mariadb._trusted_issuer_attributes_column_values(self.data)
        self.assertEqual({'issuer_url', 'jwks_uri', 'audience'}, set(values))

    def test_mask_limits_columns(self):
        # issuer_url and jwks_uri travel together -- a new URL with the
        # old key source is a cluster verifying against the wrong
        # provider -- so a writer of one must not carry a stale other.
        values = mariadb._trusted_issuer_attributes_column_values(
            self.data, ['audience'])
        self.assertEqual({'audience'}, set(values))
        self.assertEqual('https://sf.example.com', values['audience'])

    def test_unknown_field_rejected(self):
        self.assertRaises(
            ValueError, mariadb._trusted_issuer_attributes_column_values,
            self.data, ['audience', 'not_a_column'])


class MappingRuleColumnValuesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.data = MappingRuleAttributesData(
            uuid=uuid.uuid4(),
            issuer='github',
            bound_claims={'repository': 'shakenfist/ryll'},
            scopes=['instance.read'],
            key_ttl=900,
            key_name_prefix='ryll-ci')

    def test_no_mask_returns_every_column(self):
        values = mariadb._mapping_rule_attributes_column_values(self.data)
        self.assertEqual(
            {'issuer', 'bound_claims', 'scopes', 'key_ttl',
             'key_name_prefix'}, set(values))

    def test_mask_limits_columns(self):
        # The scopes a rule grants are the column worth protecting: a
        # writer touching anything else must not carry a stale copy of
        # them, or narrowing a rule could be silently reverted.
        values = mariadb._mapping_rule_attributes_column_values(
            self.data, ['key_ttl'])
        self.assertEqual({'key_ttl'}, set(values))
        self.assertNotIn('scopes', values)

    def test_json_columns_are_encoded(self):
        # bound_claims and scopes are JSON columns, so a masked write
        # has to encode them rather than hand SQLAlchemy the objects.
        values = mariadb._mapping_rule_attributes_column_values(
            self.data, ['bound_claims', 'scopes'])
        self.assertEqual(
            {'repository': 'shakenfist/ryll'},
            json.loads(values['bound_claims']))
        self.assertEqual(['instance.read'], json.loads(values['scopes']))

    def test_unknown_field_rejected(self):
        self.assertRaises(
            ValueError, mariadb._mapping_rule_attributes_column_values,
            self.data, ['scopes', 'not_a_column'])
