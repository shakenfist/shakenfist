# Copyright 2026 Michael Still and contributors
"""Tests for the cluster daemon draining a deleted node's queues.

When sf-cluster cleaned up a deleted node it drained the queues named by
the node's fqdn, but per-node queues are named by node uuid, so the dead
node's own work items were never drained and their operations never
aborted. Worse, the drain list also included the five shared any-* queues,
so every item waiting there -- live work belonging to other nodes -- was
dequeued and its operation aborted, once per deleted node (#4655, the
sf-cluster sibling of #4543 and #4652).
"""

import threading
import time
import types
from unittest import mock

from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.node import Node
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import get_node_background_node_queues
from shakenfist.operations.baseoperation import get_node_user_facing_node_queues
from shakenfist.tests import base


NODE_UUID = '6a3c5a8e-0c8a-4a8f-9d77-6f1f4f0f8d11'
NODE_FQDN = 'sf-dead.example.com'
OWN_USER_FACING_QUEUE = f'{NODE_UUID}-clusteroperation-user_facing'
OWN_BACKGROUND_QUEUE = f'{NODE_UUID}-clusteroperation-background'
SHARED_QUEUE = 'any-clusteroperation-user_facing'


class FakeOp:
    def __init__(self, state):
        self._state = state
        self.events = []

    @property
    def state(self):
        return types.SimpleNamespace(value=self._state)

    @state.setter
    def state(self, new_state):
        self._state = new_state

    def add_event(self, event_type, message, extra=None):
        self.events.append(message)


class DeletedNodeQueueDrainTestCase(base.ShakenFistTestCase):
    """The drain covers the dead node's own queues and nothing else."""

    def setUp(self):
        super().setUp()

        self.ops = {}
        self.work_queue = []
        self.dequeue_requests = []

        def enqueue(queue_name, op_uuid):
            self.work_queue.append({
                'id': len(self.work_queue) + 1,
                'queue_name': queue_name,
                'claimed': False,
                'payload': {
                    'operation_type': 'artifact_fetch_op',
                    'operation_uuid': op_uuid,
                },
            })

        self.enqueue = enqueue

        def dequeue_work_items(queue_names, limit=10):
            self.dequeue_requests.append(list(queue_names))
            items = []
            for row in self.work_queue:
                if row['queue_name'] in queue_names and not row['claimed']:
                    row['claimed'] = True
                    items.append(
                        (row['queue_name'], str(row['id']), row['payload']))
                    if len(items) >= limit:
                        break
            return items

        def resolve_work_item(queue_name, jobname):
            self.work_queue = [
                r for r in self.work_queue
                if not (r['queue_name'] == queue_name and
                        str(r['id']) == jobname)]

        self.mariadb = mock.MagicMock()
        self.mariadb.delete_stale_transfers.return_value = 0
        self.mariadb.delete_stale_cluster_operation_targets.return_value = 0
        self.mariadb.get_all_namespace_names.return_value = ['system']
        self.mariadb.get_active_blob_uuids.return_value = []
        self.mariadb.get_expired_blob_uuids.return_value = []
        self.mariadb.get_stale_transcoded_blob_uuids.return_value = []
        self.mariadb.dequeue_work_items.side_effect = dequeue_work_items
        self.mariadb.resolve_work_item.side_effect = resolve_work_item

        self.instance = mock.MagicMock()
        self.instance.instance_blob_usage.return_value = {}
        self.instance.healthy_instances_on_node.return_value = []

        self.ipam = mock.MagicMock()
        self.ipam.IPAMs.return_value = []
        self.network = mock.MagicMock()
        self.network.floating_network.return_value = None
        self.artifact = mock.MagicMock()
        self.artifact.Artifacts.return_value = []

        self.node = mock.MagicMock()
        self.node.uuid = NODE_UUID
        self.node.fqdn = NODE_FQDN
        self.node.last_seen = time.time()
        self.node.state = types.SimpleNamespace(value=Node.STATE_DELETED)

        op_class = mock.MagicMock()
        op_class.from_db.side_effect = lambda op_uuid: self.ops.get(op_uuid)

        for p in [
                mock.patch.object(cluster_main, 'mariadb', self.mariadb),
                mock.patch.object(cluster_main, 'instance', self.instance),
                mock.patch.object(cluster_main, 'ipam', self.ipam),
                mock.patch.object(cluster_main, 'network', self.network),
                mock.patch.object(cluster_main, 'artifact', self.artifact),
                mock.patch.object(cluster_main, 'remove_abandoned_uploads'),
                mock.patch.object(cluster_main, 'nodes_by_free_disk_descending',
                                  return_value=[]),
                mock.patch.object(cluster_main, 'Nodes',
                                  return_value=[self.node]),
                mock.patch.object(cluster_main, 'get_object_class',
                                  return_value=op_class)]:
            p.start()
            self.addCleanup(p.stop)

    def _run_cleanup(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.abort_path = '/nonexistent/sf-test-cluster-abort-path'
        m.lock = mock.MagicMock()
        m.lock.lost_event = threading.Event()
        m.is_elected = True
        m.pet_watchdog = mock.MagicMock()
        m._cascaded_error_nodes = set()
        m._drop_blob_locations = mock.MagicMock()
        m._cluster_wide_cleanup(last_loop_run=0)

    def test_own_queue_items_are_drained_and_ops_aborted(self):
        # Per-node queues are named by node uuid; the fqdn-keyed drain
        # matched none of them, so these items sat queued until the
        # reaper errored their operations (#4655).
        user_facing = self.ops['uf'] = FakeOp(BaseClusterOperation.STATE_QUEUED)
        background = self.ops['bg'] = FakeOp(BaseClusterOperation.STATE_QUEUED)
        self.enqueue(OWN_USER_FACING_QUEUE, 'uf')
        self.enqueue(OWN_BACKGROUND_QUEUE, 'bg')

        self._run_cleanup()

        self.assertEqual([], self.work_queue)
        self.assertEqual(BaseClusterOperation.STATE_ABORT,
                         user_facing.state.value)
        self.assertEqual(BaseClusterOperation.STATE_ABORT,
                         background.state.value)
        self.assertEqual(2, self.node.add_event.call_count)

    def test_shared_queue_items_are_left_alone(self):
        # The any-* queues carry live work belonging to other nodes;
        # draining them aborted operations which had nothing to do with
        # the deleted node, once per deleted node.
        shared = self.ops['shared'] = FakeOp(BaseClusterOperation.STATE_QUEUED)
        self.enqueue(SHARED_QUEUE, 'shared')
        own = self.ops['own'] = FakeOp(BaseClusterOperation.STATE_QUEUED)
        self.enqueue(OWN_USER_FACING_QUEUE, 'own')

        self._run_cleanup()

        self.assertEqual([SHARED_QUEUE],
                         [r['queue_name'] for r in self.work_queue])
        self.assertFalse(self.work_queue[0]['claimed'])
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, shared.state.value)
        self.assertEqual(BaseClusterOperation.STATE_ABORT, own.state.value)

    def test_drain_asks_for_queues_named_by_uuid_not_fqdn(self):
        self.enqueue(OWN_USER_FACING_QUEUE, 'own')
        self.ops['own'] = FakeOp(BaseClusterOperation.STATE_QUEUED)

        self._run_cleanup()

        self.assertNotEqual([], self.dequeue_requests)
        expected = set(get_node_user_facing_node_queues(NODE_UUID) +
                       get_node_background_node_queues(NODE_UUID))
        for request in self.dequeue_requests:
            self.assertEqual(expected, set(request))
            for queue_name in request:
                self.assertNotIn(NODE_FQDN, queue_name)
                self.assertFalse(queue_name.startswith('any-'))
