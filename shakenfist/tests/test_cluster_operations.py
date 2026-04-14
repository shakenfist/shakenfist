# Copyright 2026 Michael Still and contributors
#
# Tests for the cluster_operations public API via the mock_etcd
# fixture. The mock is a small in-memory mirror of the real
# direct-layer contract; any deviation between it and the SQL
# behaviour would mask bugs, so these tests document the contract
# both the mock and the direct layer must satisfy.

from shakenfist import mariadb
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


OP_UUID_A = '11111111-1111-4111-8111-111111111111'
OP_UUID_B = '22222222-2222-4222-8222-222222222222'
OP_UUID_C = '33333333-3333-4333-8333-333333333333'

NODE_ONE = 'aaaa1111-1111-4111-8111-111111111111'
NODE_TWO = 'aaaa2222-2222-4222-8222-222222222222'

INSTANCE_UUID = 'bbbb1111-1111-4111-8111-111111111111'
NETWORK_UUID = 'cccc1111-1111-4111-8111-111111111111'


class ClusterOperationsRoundTripTestCase(base.ShakenFistTestCase):
    """Round-trip contract tests for the cluster_operations API."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    def test_create_and_get_round_trip(self):
        metadata = {
            'uuid': OP_UUID_A,
            'operation_type': 'instance_preflight',
            'node_uuid': NODE_ONE,
            'instance_uuid': INSTANCE_UUID,
            'priority': 'user_waiting',
            'tasks': ['fetch_image', 'provision_interfaces'],
        }

        created = mariadb.create_cluster_operation(
            OP_UUID_A, 'instance_preflight', metadata, 1000.0)
        self.assertTrue(created)

        fetched = mariadb.get_cluster_operation(OP_UUID_A)
        self.assertIsNotNone(fetched)

        # Contract: the returned dict contains the full metadata
        # flattened at the top level, with uuid / operation_type /
        # created_at present regardless of what was in metadata.
        self.assertEqual(fetched['uuid'], OP_UUID_A)
        self.assertEqual(
            fetched['operation_type'], 'instance_preflight')
        self.assertEqual(fetched['created_at'], 1000.0)
        self.assertEqual(fetched['node_uuid'], NODE_ONE)
        self.assertEqual(fetched['instance_uuid'], INSTANCE_UUID)
        self.assertEqual(fetched['priority'], 'user_waiting')
        self.assertEqual(
            fetched['tasks'],
            ['fetch_image', 'provision_interfaces'])

    def test_create_with_missing_optional_uuids(self):
        # A node-only operation: no instance_uuid, no network_uuid.
        metadata = {
            'uuid': OP_UUID_A,
            'node_uuid': NODE_ONE,
            'priority': 'background',
            'tasks': ['reap_stale_leases'],
        }

        created = mariadb.create_cluster_operation(
            OP_UUID_A, 'net_op', metadata, 1234.0)
        self.assertTrue(created)

        fetched = mariadb.get_cluster_operation(OP_UUID_A)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched['node_uuid'], NODE_ONE)
        self.assertNotIn('instance_uuid', fetched)
        self.assertNotIn('network_uuid', fetched)

    def test_duplicate_create_is_rejected(self):
        metadata = {
            'uuid': OP_UUID_A,
            'node_uuid': NODE_ONE,
            'priority': 'background',
            'tasks': ['x'],
        }

        first = mariadb.create_cluster_operation(
            OP_UUID_A, 'instance_preflight', metadata, 1000.0)
        self.assertTrue(first)

        # A second create with the same uuid must be refused without
        # clobbering the existing row.
        second_metadata = dict(metadata)
        second_metadata['tasks'] = ['different']
        second = mariadb.create_cluster_operation(
            OP_UUID_A, 'instance_preflight',
            second_metadata, 2000.0)
        self.assertFalse(second)

        fetched = mariadb.get_cluster_operation(OP_UUID_A)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched['tasks'], ['x'])
        self.assertEqual(fetched['created_at'], 1000.0)

    def test_get_missing_returns_none(self):
        self.assertIsNone(
            mariadb.get_cluster_operation(OP_UUID_A))

    def test_get_by_node_filters_and_orders(self):
        # Three rows: two targeting NODE_ONE (at different times) and
        # one targeting NODE_TWO. The NODE_ONE query must return only
        # the first two, ordered by created_at ascending.
        mariadb.create_cluster_operation(
            OP_UUID_A, 'instance_preflight',
            {
                'uuid': OP_UUID_A,
                'node_uuid': NODE_ONE,
                'priority': 'background',
                'tasks': ['a'],
            },
            2000.0)
        mariadb.create_cluster_operation(
            OP_UUID_B, 'instance_preflight',
            {
                'uuid': OP_UUID_B,
                'node_uuid': NODE_ONE,
                'priority': 'user_waiting',
                'tasks': ['b'],
            },
            1000.0)
        mariadb.create_cluster_operation(
            OP_UUID_C, 'instance_preflight',
            {
                'uuid': OP_UUID_C,
                'node_uuid': NODE_TWO,
                'priority': 'background',
                'tasks': ['c'],
            },
            1500.0)

        items = mariadb.get_cluster_operations_by_node(NODE_ONE)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['uuid'], OP_UUID_B)  # 1000.0 first
        self.assertEqual(items[1]['uuid'], OP_UUID_A)  # 2000.0 second

    def test_delete_removes_row(self):
        metadata = {
            'uuid': OP_UUID_A,
            'node_uuid': NODE_ONE,
            'priority': 'background',
            'tasks': ['x'],
        }
        mariadb.create_cluster_operation(
            OP_UUID_A, 'instance_preflight', metadata, 1000.0)

        self.assertTrue(
            mariadb.delete_cluster_operation(OP_UUID_A))
        self.assertIsNone(
            mariadb.get_cluster_operation(OP_UUID_A))

    def test_delete_missing_returns_false(self):
        self.assertFalse(
            mariadb.delete_cluster_operation(OP_UUID_A))

    def test_network_uuid_indexed_column(self):
        # An operation targeting a network (not an instance). The
        # network_uuid field must round-trip through the metadata
        # dict regardless of whether it was also used as an index.
        metadata = {
            'uuid': OP_UUID_A,
            'node_uuid': NODE_ONE,
            'network_uuid': NETWORK_UUID,
            'priority': 'background',
            'tasks': ['rebuild_dnsmasq'],
        }
        mariadb.create_cluster_operation(
            OP_UUID_A, 'net_op', metadata, 1000.0)

        fetched = mariadb.get_cluster_operation(OP_UUID_A)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched['network_uuid'], NETWORK_UUID)
