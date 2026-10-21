# Copyright 2026 Michael Still and contributors
#
# Instance, network and artifact views carry has_pending_operations, the
# answer to has_pending_cluster_operation(), so a client need not read
# last_cluster_operation (which a later terminal op masks) and walk the chain.

import time
import uuid
from unittest import mock

from shakenfist import artifact
from shakenfist import baseobject
from shakenfist import instance
from shakenfist.config import SFConfig
from shakenfist.network import network
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class HasPendingOperationsViewTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c', DISK_BUS='virtio', ZONE='sfzone',
            NODE_NAME='node01')
        patcher = mock.patch('shakenfist.instance.config', fake_config)
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version',
            return_value=6)
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher = mock.patch(
            'shakenfist.schema.object_reference.request_namespace',
            return_value='system')
        patcher.start()
        self.addCleanup(patcher.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance(
            'cirros', self.instance_uuid,
            set_state=instance.Instance.STATE_CREATED)
        self.inst = instance.Instance.from_db(self.instance_uuid)

        self.network_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_network('bobnet', self.network_uuid)
        self.net = network.Network.from_db(self.network_uuid)

        self.art = artifact.Artifact.new(
            artifact.Artifact.TYPE_IMAGE, artifact.BLOB_URL + 'abc123',
            namespace='system')

    def _views(self):
        return {
            'instance': self.inst.external_view(),
            'network': self.net.external_view(),
            'artifact': self.art.external_view_without_index(),
        }

    def test_key_follows_has_pending_cluster_operation(self):
        for value in (True, False):
            with mock.patch.object(
                    baseobject.DatabaseBackedObjectWithOperations,
                    'has_pending_cluster_operation', return_value=value):
                for name, view in self._views().items():
                    self.assertIs(value, view['has_pending_operations'], name)

    def test_in_memory_artifact_reports_false_without_a_database_call(self):
        self.assertTrue(self.art.in_memory_only)
        with mock.patch(
                'shakenfist.mariadb.has_pending_cluster_operation_target'
        ) as has_pending:
            view = self.art.external_view_without_index()
        self.assertIs(False, view['has_pending_operations'])
        has_pending.assert_not_called()

    def test_base_view_does_not_carry_the_key(self):
        self.assertNotIn(
            'has_pending_operations', self.inst._external_view())
        self.assertNotIn(
            'has_pending_operations', self.net._external_view())

    def _seed(self, op_uuid, state, observational=False):
        self.mock_mariadb.seed_cluster_operation_target(
            op_uuid, 'node_inst_op', ObjectType.INSTANCE, self.instance_uuid,
            time.time(), observational=observational)
        self.mock_mariadb.mariadb_states[f'node_inst_op/{op_uuid}'] = {
            'object_type': 'node_inst_op', 'object_uuid': op_uuid,
            'state_value': state, 'update_time': time.time(),
            'message': None}

    def test_view_through_the_mock_targets(self):
        self.assertIs(
            False, self.inst.external_view()['has_pending_operations'])

        self._seed(str(uuid.uuid4()), 'complete')
        self._seed(str(uuid.uuid4()), 'queued', observational=True)
        self.assertIs(
            False, self.inst.external_view()['has_pending_operations'])

        for state in ('queued', 'preflight', 'executing'):
            op_uuid = str(uuid.uuid4())
            self._seed(op_uuid, state)
            self.assertIs(
                True, self.inst.external_view()['has_pending_operations'],
                state)
            self.mock_mariadb.mariadb_states[
                f'node_inst_op/{op_uuid}']['state_value'] = 'complete'
