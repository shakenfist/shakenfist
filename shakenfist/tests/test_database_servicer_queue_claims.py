# Copyright 2026 Michael Still and contributors

"""A work queue claim must name the node whose worker holds it.

`work_queue.claimed_by` is supposed to say which node claimed a row, but
over gRPC the servicer recorded its own NODE_NAME, because `Dequeue`
carried no caller identity. Every claim in a cluster using the database
tier therefore named the database node, so nothing could tell a stopped
node's abandoned claim on a shared `any-*` queue -- which every node's
sf-queues drains -- from a live peer's claim on an item that peer is
running right now (#4652).

That is what made sf-queues' startup reset unsafe on those queues: it
cleared every claim, including live ones, and a second worker then
either ran the operation concurrently or resolved its row and stranded
it until the reaper's orphan pass 30 minutes later.
"""

from unittest import mock

from shakenfist.daemons.database import main as daemons_database_main
from shakenfist.protos import database_pb2
from shakenfist.tests import base


class DequeueWorkerAttributionTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.servicer = daemons_database_main.DatabaseService(
            mock.MagicMock())
        self.config = mock.patch.object(
            daemons_database_main, 'config', mock.MagicMock())
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)
        self.mock_config.NODE_NAME = 'sf-database-1'

    @mock.patch('shakenfist.mariadb._direct_work_queue_dequeue_batch',
                return_value=[])
    def test_the_callers_node_is_recorded_not_ours(self, mock_dequeue):
        request = database_pb2.DequeueRequest(
            queue_names=['any-clusteroperation-user_facing'], limit=3,
            worker_id='sf-3')

        self.servicer.Dequeue(request, mock.MagicMock())

        mock_dequeue.assert_called_once_with(
            ['any-clusteroperation-user_facing'], 'sf-3', 3)

    @mock.patch('shakenfist.mariadb._direct_work_queue_dequeue_batch',
                return_value=[])
    def test_an_older_client_still_gets_our_node_name(self, mock_dequeue):
        # A client from before worker_id existed sends nothing. Falling
        # back to our own name is what it used to get, so a rolling
        # upgrade is no worse than before rather than writing an empty
        # claimed_by that matches nobody's reset.
        request = database_pb2.DequeueRequest(
            queue_names=['any-clusteroperation-user_facing'], limit=1)

        self.servicer.Dequeue(request, mock.MagicMock())

        mock_dequeue.assert_called_once_with(
            ['any-clusteroperation-user_facing'], 'sf-database-1', 1)


class RestartQueueScopeTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.servicer = daemons_database_main.DatabaseService(
            mock.MagicMock())

    @mock.patch('shakenfist.mariadb._direct_work_queue_restart',
                return_value=0)
    def test_one_workers_claims_are_cleared(self, mock_restart):
        request = database_pb2.RestartQueueClaimsByRequest(
            queue_name='any-clusteroperation-background',
            claimed_by='6a3c5a8e-0c8a-4a8f-9d77-6f1f4f0f8d11')

        reply = self.servicer.RestartQueueClaimsBy(request, mock.MagicMock())

        self.assertTrue(reply.success)
        mock_restart.assert_called_once_with(
            'any-clusteroperation-background',
            '6a3c5a8e-0c8a-4a8f-9d77-6f1f4f0f8d11')

    @mock.patch('shakenfist.mariadb._direct_work_queue_restart',
                return_value=0)
    def test_an_empty_worker_is_refused_not_read_as_everyone(
            self, mock_restart):
        # Clearing every claim on a shared queue is the defect itself.
        request = database_pb2.RestartQueueClaimsByRequest(
            queue_name='any-clusteroperation-background')

        reply = self.servicer.RestartQueueClaimsBy(request, mock.MagicMock())

        self.assertFalse(reply.success)
        mock_restart.assert_not_called()

    @mock.patch('shakenfist.mariadb._direct_work_queue_restart',
                return_value=0)
    def test_restart_queue_still_clears_the_whole_queue(self, mock_restart):
        # The negative control: a queue only one node drains is reset
        # unfiltered, exactly as it always was.
        request = database_pb2.RestartQueueRequest(queue_name='q')

        self.servicer.RestartQueue(request, mock.MagicMock())

        mock_restart.assert_called_once_with('q')
