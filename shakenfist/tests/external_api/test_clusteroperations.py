# Copyright 2019 Michael Still and contributors
import json
import logging
import sys
from unittest import mock
from uuid import uuid4

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import SFConfig
from shakenfist.external_api import app as external_api
from shakenfist.schema.cluster_operation_target import ClusterOperationTargetData
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


def _fake_op(uuid_, operation_type, depends_on=None, created_at=1.0,
             state='complete', tasks=None):
    """Build a minimal mock that mimics a BaseClusterOperation."""
    op = mock.MagicMock()
    op.uuid = uuid_
    op.object_type = operation_type
    op.depends_on = depends_on or []
    op.state.value = state
    op.external_view.return_value = {
        'operation_type': operation_type,
        'uuid': uuid_,
        'state': state,
        'tasks': tasks or [],
    }
    return op


class ClusterOperationChainTestCase(base.ShakenFistTestCase):
    """Tests for ``GET /clusteroperations/<op_uuid>/chain``."""

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        fake_config = SFConfig(
            NODE_NAME='seriously',
            NODE_EGRESS_IP='127.0.0.1',
            NETWORK_NODE_IP='127.0.0.1',
            NODE_EGRESS_NIC='eth0',
            NODE_MESH_NIC='eth1',
            NODE_IS_NETWORK_NODE=True,
        )
        self.config_patch = mock.patch(
            'shakenfist.external_api.base.config', fake_config)
        self.mock_config = self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.client = external_api.app.test_client()

        self.mock_etcd.create_namespace('system', 'key1', 'bar')
        self.mock_etcd.create_namespace('foo', 'key1', 'bar')
        self.mock_etcd.create_namespace('other', 'key1', 'bar')

        # The chain endpoint touches the network if any chain member
        # targets it. Pre-create a network in namespace 'foo'.
        self.network_uuid = str(uuid4())
        self.mock_etcd.create_network(
            'banana',
            uuid=self.network_uuid,
            namespace='foo',
            set_state=dbo.STATE_CREATED)

        # And a network in 'other' for foreign-namespace tests.
        self.other_network_uuid = str(uuid4())
        self.mock_etcd.create_network(
            'apple',
            uuid=self.other_network_uuid,
            namespace='other',
            set_state=dbo.STATE_CREATED)

        # Auth as foo (non-admin) by default; individual tests can
        # re-auth as system if they need admin.
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'foo', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.foo_token = 'Bearer %s' % resp.get_json()['access_token']

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.admin_token = 'Bearer %s' % resp.get_json()['access_token']

    def _patch_chain(self, ops_by_uuid, targets_by_uuid):
        """Patch the endpoint's mariadb / hydration helpers.

        ``ops_by_uuid``: dict[uuid -> (operation_type, depends_on, created_at)].
        ``targets_by_uuid``: dict[uuid -> (target_object_type, target_uuid)].
        """
        def fake_get_cluster_operation(uuid_):
            data = ops_by_uuid.get(uuid_)
            if data is None:
                return None
            operation_type, _depends_on, created_at = data
            return {
                'uuid': uuid_,
                'operation_type': operation_type,
                'created_at': created_at,
            }

        def fake_get_target(uuid_):
            t = targets_by_uuid.get(uuid_)
            if t is None:
                return None
            target_type, target_uuid = t
            return ClusterOperationTargetData(
                operation_uuid=uuid_,
                operation_type=ops_by_uuid[uuid_][0],
                target_object_type=target_type,
                target_uuid=target_uuid,
                sequence_number=1,
                created_at=ops_by_uuid[uuid_][2])

        # Build the fake op objects that _hydrate_op() would return for
        # operation lookups. Target-object lookups (the namespace check
        # path) still go through the real ``get_object_class`` so the
        # Network rows MockEtcd has stashed are visible.
        fakes = {
            uuid_: _fake_op(
                uuid_, op_type, depends_on=depends_on,
                created_at=created_at)
            for uuid_, (op_type, depends_on, created_at) in ops_by_uuid.items()
        }

        def fake_hydrate_op(uuid_):
            return fakes.get(uuid_)

        patches = [
            mock.patch(
                'shakenfist.external_api.clusteroperation.mariadb'
                '.get_cluster_operation',
                side_effect=fake_get_cluster_operation),
            mock.patch(
                'shakenfist.external_api.clusteroperation.mariadb'
                '.get_cluster_operation_target',
                side_effect=fake_get_target),
            mock.patch(
                'shakenfist.external_api.clusteroperation._hydrate_op',
                side_effect=fake_hydrate_op),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return fakes

    def test_chain_happy_path_three_ops_same_namespace(self):
        uuid_a = str(uuid4())
        uuid_b = str(uuid4())
        uuid_c = str(uuid4())
        ops = {
            uuid_a: ('net_op', [{'op_type': 'net_op', 'op_uuid': uuid_b}],
                     3.0),
            uuid_b: ('net_op', [{'op_type': 'net_op', 'op_uuid': uuid_c}],
                     2.0),
            uuid_c: ('net_op', [], 1.0),
        }
        targets = {
            uuid_a: ('network', self.network_uuid),
            uuid_b: ('network', self.network_uuid),
            uuid_c: ('network', self.network_uuid),
        }
        self._patch_chain(ops, targets)

        resp = self.client.get(
            '/clusteroperations/%s/chain' % uuid_a,
            headers={'Authorization': self.foo_token})
        self.assertEqual(200, resp.status_code)
        body = resp.get_json()
        self.assertEqual(3, len(body))
        # Newest first by created_at.
        self.assertEqual(uuid_a, body[0]['uuid'])
        self.assertEqual(uuid_b, body[1]['uuid'])
        self.assertEqual(uuid_c, body[2]['uuid'])

    def test_chain_unknown_uuid_returns_404(self):
        # No patches: mariadb.get_cluster_operation returns None.
        with mock.patch(
                'shakenfist.external_api.clusteroperation.mariadb'
                '.get_cluster_operation',
                return_value=None):
            resp = self.client.get(
                '/clusteroperations/%s/chain' % str(uuid4()),
                headers={'Authorization': self.foo_token})
        self.assertEqual(404, resp.status_code)

    def test_chain_foreign_namespace_returns_403_for_non_admin(self):
        uuid_a = str(uuid4())
        uuid_b = str(uuid4())
        ops = {
            uuid_a: ('net_op', [{'op_type': 'net_op', 'op_uuid': uuid_b}],
                     2.0),
            uuid_b: ('net_op', [], 1.0),
        }
        # uuid_b targets a network in the 'other' namespace.
        targets = {
            uuid_a: ('network', self.network_uuid),
            uuid_b: ('network', self.other_network_uuid),
        }
        self._patch_chain(ops, targets)

        resp = self.client.get(
            '/clusteroperations/%s/chain' % uuid_a,
            headers={'Authorization': self.foo_token})
        self.assertEqual(403, resp.status_code)

    def test_chain_admin_sees_full_chain_across_namespaces(self):
        uuid_a = str(uuid4())
        uuid_b = str(uuid4())
        ops = {
            uuid_a: ('net_op', [{'op_type': 'net_op', 'op_uuid': uuid_b}],
                     2.0),
            uuid_b: ('net_op', [], 1.0),
        }
        targets = {
            uuid_a: ('network', self.network_uuid),
            uuid_b: ('network', self.other_network_uuid),
        }
        self._patch_chain(ops, targets)

        resp = self.client.get(
            '/clusteroperations/%s/chain' % uuid_a,
            headers={'Authorization': self.admin_token})
        self.assertEqual(200, resp.status_code)
        body = resp.get_json()
        self.assertEqual(2, len(body))
        self.assertEqual({uuid_a, uuid_b}, {entry['uuid'] for entry in body})

    def test_chain_no_target_row_returns_403_for_non_admin(self):
        # An op with no cluster_operation_targets row cannot have its
        # namespace verified. The endpoint must fail closed for
        # non-admin callers rather than expose the chain blindly.
        uuid_a = str(uuid4())
        ops = {
            uuid_a: ('net_op', [], 1.0),
        }
        # No targets entry for uuid_a.
        targets: dict = {}
        self._patch_chain(ops, targets)

        resp = self.client.get(
            '/clusteroperations/%s/chain' % uuid_a,
            headers={'Authorization': self.foo_token})
        self.assertEqual(403, resp.status_code)

    def test_chain_no_target_row_admin_sees_full_chain(self):
        # Admins are not affected by the target-missing 403; the
        # namespace check is skipped entirely for the admin namespace.
        uuid_a = str(uuid4())
        ops = {
            uuid_a: ('net_op', [], 1.0),
        }
        targets: dict = {}
        self._patch_chain(ops, targets)

        resp = self.client.get(
            '/clusteroperations/%s/chain' % uuid_a,
            headers={'Authorization': self.admin_token})
        self.assertEqual(200, resp.status_code)
        body = resp.get_json()
        self.assertEqual(1, len(body))
        self.assertEqual(uuid_a, body[0]['uuid'])

    def test_chain_cluster_scoped_target_returns_403_for_non_admin(self):
        # A target like ``node`` or ``blob`` is cluster-scoped (no
        # namespace attribute), so non-admins must not see ops touching
        # it. Verified at the chain root rather than only on deep
        # ancestors.
        uuid_a = str(uuid4())
        ops = {
            uuid_a: ('net_op', [], 1.0),
        }
        # ``node`` returns ``None`` from _namespace_for_target because
        # Node objects have no namespace attribute.
        targets = {
            uuid_a: ('node', str(uuid4())),
        }
        self._patch_chain(ops, targets)

        resp = self.client.get(
            '/clusteroperations/%s/chain' % uuid_a,
            headers={'Authorization': self.foo_token})
        self.assertEqual(403, resp.status_code)

    def test_chain_self_referential_cycle_does_not_loop(self):
        # A self-referential ``depends_on`` (op refers to itself) must
        # be handled by the visited-set guard; the response is the
        # single-node chain rather than an infinite walk.
        uuid_a = str(uuid4())
        ops = {
            uuid_a: ('net_op',
                     [{'op_type': 'net_op', 'op_uuid': uuid_a}],
                     1.0),
        }
        targets = {
            uuid_a: ('network', self.network_uuid),
        }
        self._patch_chain(ops, targets)

        resp = self.client.get(
            '/clusteroperations/%s/chain' % uuid_a,
            headers={'Authorization': self.foo_token})
        self.assertEqual(200, resp.status_code)
        body = resp.get_json()
        self.assertEqual(1, len(body))
        self.assertEqual(uuid_a, body[0]['uuid'])

    def test_chain_two_node_cycle_returns_both_nodes(self):
        # A two-node cycle (A -> B -> A) terminates with both nodes
        # visited exactly once, in created_at order.
        uuid_a = str(uuid4())
        uuid_b = str(uuid4())
        ops = {
            uuid_a: ('net_op',
                     [{'op_type': 'net_op', 'op_uuid': uuid_b}],
                     2.0),
            uuid_b: ('net_op',
                     [{'op_type': 'net_op', 'op_uuid': uuid_a}],
                     1.0),
        }
        targets = {
            uuid_a: ('network', self.network_uuid),
            uuid_b: ('network', self.network_uuid),
        }
        self._patch_chain(ops, targets)

        resp = self.client.get(
            '/clusteroperations/%s/chain' % uuid_a,
            headers={'Authorization': self.foo_token})
        self.assertEqual(200, resp.status_code)
        body = resp.get_json()
        self.assertEqual(2, len(body))
        self.assertEqual({uuid_a, uuid_b}, {entry['uuid'] for entry in body})

    def test_chain_exceeds_max_returns_400(self):
        # A chain longer than MAX_CHAIN_NODES nodes terminates with a
        # 400. Patch the limit down to something small for the test.
        chain_len = 6
        uuids = [str(uuid4()) for _ in range(chain_len)]
        ops = {}
        targets = {}
        for i, u in enumerate(uuids):
            next_u = uuids[i + 1] if i + 1 < chain_len else None
            deps = (
                [{'op_type': 'net_op', 'op_uuid': next_u}]
                if next_u else [])
            ops[u] = ('net_op', deps, float(chain_len - i))
            targets[u] = ('network', self.network_uuid)
        self._patch_chain(ops, targets)

        with mock.patch(
                'shakenfist.external_api.clusteroperation.MAX_CHAIN_NODES',
                3):
            resp = self.client.get(
                '/clusteroperations/%s/chain' % uuids[0],
                headers={'Authorization': self.foo_token})
        self.assertEqual(400, resp.status_code)


class ClusterOperationsForTargetTestCase(base.ShakenFistTestCase):
    """Tests for ``GET /clusteroperations?target_object_type=&target_uuid=``."""

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        fake_config = SFConfig(
            NODE_NAME='seriously',
            NODE_EGRESS_IP='127.0.0.1',
            NETWORK_NODE_IP='127.0.0.1',
            NODE_EGRESS_NIC='eth0',
            NODE_MESH_NIC='eth1',
            NODE_IS_NETWORK_NODE=True,
        )
        self.config_patch = mock.patch(
            'shakenfist.external_api.base.config', fake_config)
        self.mock_config = self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.client = external_api.app.test_client()

        self.mock_etcd.create_namespace('system', 'key1', 'bar')
        self.mock_etcd.create_namespace('foo', 'key1', 'bar')
        self.mock_etcd.create_namespace('other', 'key1', 'bar')

        self.network_uuid = str(uuid4())
        self.mock_etcd.create_network(
            'banana',
            uuid=self.network_uuid,
            namespace='foo',
            set_state=dbo.STATE_CREATED)

        self.other_network_uuid = str(uuid4())
        self.mock_etcd.create_network(
            'apple',
            uuid=self.other_network_uuid,
            namespace='other',
            set_state=dbo.STATE_CREATED)

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'foo', 'key': 'bar'}))
        self.foo_token = 'Bearer %s' % resp.get_json()['access_token']

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.admin_token = 'Bearer %s' % resp.get_json()['access_token']

    def _patch_list_and_hydrate(self, op_records):
        """Patch list_cluster_operations_for_target and op hydration.

        ``op_records``: list of (uuid, operation_type, created_at) tuples
        in the order list_cluster_operations_for_target should return.
        """
        records = [
            {
                'uuid': u,
                'operation_type': op_type,
                'created_at': created_at,
            }
            for u, op_type, created_at in op_records
        ]
        fakes = {
            u: _fake_op(u, op_type, created_at=created_at)
            for u, op_type, created_at in op_records
        }

        def fake_hydrate_op(uuid_):
            return fakes.get(uuid_)

        patches = [
            mock.patch(
                'shakenfist.external_api.clusteroperation.mariadb'
                '.list_cluster_operations_for_target',
                return_value=records),
            mock.patch(
                'shakenfist.external_api.clusteroperation._hydrate_op',
                side_effect=fake_hydrate_op),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_happy_path_returns_op_list(self):
        op_uuid_1 = str(uuid4())
        op_uuid_2 = str(uuid4())
        self._patch_list_and_hydrate([
            (op_uuid_1, 'net_op', 2.0),
            (op_uuid_2, 'net_op', 1.0),
        ])

        resp = self.client.get(
            '/clusteroperations?target_object_type=network'
            '&target_uuid=%s' % self.network_uuid,
            headers={'Authorization': self.foo_token})
        self.assertEqual(200, resp.status_code)
        body = resp.get_json()
        self.assertEqual(
            [op_uuid_1, op_uuid_2],
            [entry['uuid'] for entry in body])

    def test_non_admin_foreign_namespace_returns_403(self):
        # No hydration patches needed -- the access check fails before
        # we'd consult mariadb.list_cluster_operations_for_target.
        resp = self.client.get(
            '/clusteroperations?target_object_type=network'
            '&target_uuid=%s' % self.other_network_uuid,
            headers={'Authorization': self.foo_token})
        self.assertEqual(403, resp.status_code)

    def test_admin_bypasses_namespace_check(self):
        op_uuid = str(uuid4())
        self._patch_list_and_hydrate([(op_uuid, 'net_op', 1.0)])

        resp = self.client.get(
            '/clusteroperations?target_object_type=network'
            '&target_uuid=%s' % self.other_network_uuid,
            headers={'Authorization': self.admin_token})
        self.assertEqual(200, resp.status_code)
        body = resp.get_json()
        self.assertEqual([op_uuid], [entry['uuid'] for entry in body])

    def test_empty_result_when_no_ops(self):
        self._patch_list_and_hydrate([])

        resp = self.client.get(
            '/clusteroperations?target_object_type=network'
            '&target_uuid=%s' % self.network_uuid,
            headers={'Authorization': self.foo_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual([], resp.get_json())

    def test_invalid_target_object_type_returns_400(self):
        resp = self.client.get(
            '/clusteroperations?target_object_type=nonsense'
            '&target_uuid=%s' % self.network_uuid,
            headers={'Authorization': self.foo_token})
        self.assertEqual(400, resp.status_code)

    def test_missing_target_uuid_returns_400(self):
        resp = self.client.get(
            '/clusteroperations?target_object_type=network',
            headers={'Authorization': self.foo_token})
        self.assertEqual(400, resp.status_code)
