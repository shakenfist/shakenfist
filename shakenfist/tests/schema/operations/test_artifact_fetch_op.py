from unittest import mock
from uuid import uuid4

from pydantic import ValidationError

from shakenfist.artifact import Artifact
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.constants import OPERATION_NAMES_TO_CLASSES
from shakenfist.exceptions import BlobFetchFailed
from shakenfist.exceptions import BlobTransferSetupFailed
from shakenfist.schema.operations.artifact_fetch_op import create_and_enqueue
from shakenfist.schema.operations.artifact_fetch_op import current_version
from shakenfist.schema.operations.artifact_fetch_op import model
from shakenfist.schema.operations.artifact_fetch_op import model_tasks
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import dependency
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.operations.artifact_fetch_op import ArtifactFetchOp
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class ArtifactFetchOpTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

    def test_model(self):
        u1 = str(uuid4())
        u2 = str(uuid4())
        u3 = str(uuid4())

        d = model(
            uuid=u1,
            namespace='system',
            url='http://www.shakenfist.com',
            instance_uuid=None,
            priority=PRIORITY.user_facing,
            request_id=None,
            tasks=[model_tasks.image_fetch],
            depends_on=[
                dependency(
                    op_type=ObjectType.NODE_BLOB_OP,
                    op_uuid=u2
                )
            ],
            runs_after=[
                dependency(
                    op_type=ObjectType.NODE_BLOB_OP,
                    op_uuid=u3
                )
            ],
            version=current_version
        )

        serialized = d.model_dump(mode='json')
        self.assertEqual(u1, serialized['uuid'])
        self.assertEqual('system', serialized['namespace'])
        self.assertEqual('http://www.shakenfist.com', serialized['url'])
        self.assertEqual(None, serialized['instance_uuid'])
        self.assertEqual('user_facing', serialized['priority'])
        self.assertEqual(None, serialized['request_id'])
        self.assertEqual(['image_fetch'], serialized['tasks'])
        self.assertEqual(
            [
                {
                    'op_type': 'node_blob_op',
                    'op_uuid': u2
                }
            ],
            serialized['depends_on']
        )
        self.assertEqual(
            [
                {
                    'op_type': 'node_blob_op',
                    'op_uuid': u3
                }
            ],
            serialized['runs_after']
        )
        self.assertEqual(current_version, serialized['version'])

    def test_model_bad_version(self):
        u1 = str(uuid4())
        u2 = str(uuid4())
        u3 = str(uuid4())

        self.assertRaises(
            ValidationError,
            model,
            uuid=u1,
            namespace='system',
            url='http://www.shakenfist.com',
            instance_uuid=None,
            priority=PRIORITY.user_facing,
            request_id=None,
            tasks=[model_tasks.image_fetch],
            depends_on=[
                dependency(
                    op_type=ObjectType.NODE_BLOB_OP,
                    op_uuid=u2
                )
            ],
            runs_after=[
                dependency(
                    op_type=ObjectType.NODE_BLOB_OP,
                    op_uuid=u3
                )
            ],
            version=current_version + 1
        )

    @mock.patch(
        'shakenfist_utilities.random.random_id',
        return_value='asdjfhkjadsfh'
    )
    @mock.patch('time.time', return_value=123.0)
    def test_create_and_enqueue(self, _mock_time, _mock_id):
        op_type, op_uuid = create_and_enqueue(
            'system',
            'http://www.shakenfist.com',
            None,
            [model_tasks.image_fetch],
            PRIORITY.user_facing
        )

        self.assertEqual(ObjectType.ARTIFACT_FETCH_OP, op_type)

        self.assertEqual(
            {
                'artifact_uuid': None,
                'depends_on': None,
                'runs_after': None,
                'instance_uuid': None,
                'namespace': 'system',
                'priority': 'user_facing',
                'request_id': None,
                'tasks': ['image_fetch'],
                'url': 'http://www.shakenfist.com',
                'uuid': op_uuid,
                'version': 1
            },
            self.mock_mariadb.get_cluster_operation_metadata(op_uuid)
        )
        self.assertEqual(
            {
                'value': 'queued',
                'update_time': 123.0
            },
            self.mock_mariadb.get_mariadb_state('artifact_fetch_op', op_uuid)
        )
        self.assertEqual(
            {
                'operation_type': 'artifact_fetch_op',
                'operation_uuid': op_uuid
            },
            self.mock_mariadb.get_work_queue_payload(
                'any-clusteroperation-user_facing')
        )

    def test_load_from_etcd(self):
        _, op_uuid = create_and_enqueue(
            'system',
            'http://www.shakenfist.com',
            None,
            [model_tasks.image_fetch],
            PRIORITY.user_facing
        )

        afo = ArtifactFetchOp.from_db(op_uuid)
        self.assertNotEqual(None, afo)
        self.assertEqual('queued', afo.state.value)

    def test_object_mapping(self):
        self.assertTrue(
            ArtifactFetchOp.object_type in OPERATION_NAMES_TO_CLASSES)
        self.assertTrue(
            ArtifactFetchOp.object_type in OBJECT_NAMES_TO_CLASSES)


class ArtifactFetchOpResolutionTestCase(base.ShakenFistTestCase):
    """Which artifact the fetch operation writes into.

    This is where the write actually lands: get_image ends in
    add_index, and add_index ends in delete_old_versions. Both routes
    which enqueue this operation resolve by ownership before they do,
    so in practice it re-resolves to the artifact they already settled
    on -- but "in practice" is an inspection of every caller, and this
    is one line in the operation.
    """

    URL = 'https://example.com/an-image.qcow2'

    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        for ns in ['system', 'owner', 'stranger']:
            self.mock_mariadb.create_namespace(ns, 'key1', '%skey' % ns)

        self.theirs = Artifact.new(
            Artifact.TYPE_IMAGE, self.URL, name='an-image',
            namespace='owner')
        self.theirs.state = Artifact.STATE_CREATED
        self.theirs.shared = True

    def _fetch_for(self, namespace):
        """Run the resolution, and report the artifact it chose.

        The download itself is stubbed: what this asks is which object
        ImageFetchHelper was pointed at, not whether it can fetch.
        """
        _, op_uuid = create_and_enqueue(
            namespace, self.URL, None, [model_tasks.image_fetch],
            PRIORITY.user_facing)
        afo = ArtifactFetchOp.from_db(op_uuid)

        with mock.patch(
                'shakenfist.operations.artifact_fetch_op.images') as images:
            afo._image_fetch(None)

        images.ImageFetchHelper.assert_called_once()
        return images.ImageFetchHelper.call_args.args[1]

    def test_a_shared_artifact_is_not_fetched_into(self):
        # The regression. Resolution by visibility landed here, and the
        # operator guide says a non-system namespace should not be able
        # to update a shared artifact.
        a = self._fetch_for('stranger')

        self.assertNotEqual(str(self.theirs.uuid), str(a.uuid))
        self.assertEqual('stranger', a.namespace)

    def test_our_own_artifact_is_fetched_into(self):
        # The control. Ownership resolution has to still find the
        # artifact the enqueueing route created, or every fetch would
        # mint a second artifact for the same URL.
        a = self._fetch_for('owner')

        self.assertEqual(str(self.theirs.uuid), str(a.uuid))


class ArtifactFetchOpBlobReplicationFailureTestCase(base.ShakenFistTestCase):
    """Blob replication failures retry with backoff, and once the retry
    budget is exhausted the instance is driven to an error state.

    Issue 3494: every replication source timed out awaiting the fetching
    node's transfer connection, the fetch op errored in under a minute
    with no retry, and the instance sat in state initial forever because
    the dependent instance start operation was aborted by the dispatcher
    without ever executing.
    """

    URL = 'https://example.com/an-image.qcow2'
    QUEUE = 'test-clusteroperation-user_facing'

    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'systemkey')

    def _make_op(self, defer_count=0, instance_uuid=None):
        _, op_uuid = create_and_enqueue(
            'system', self.URL, instance_uuid, [model_tasks.image_fetch],
            PRIORITY.user_facing)
        afo = ArtifactFetchOp.from_db(op_uuid)
        afo.state = ArtifactFetchOp.STATE_EXECUTING
        afo.queue_name = self.QUEUE
        afo.current_defer_count = defer_count
        return afo

    def _fetch_with_failure(self, afo, inst, exc):
        with mock.patch(
                'shakenfist.operations.artifact_fetch_op.images') as images:
            images.ImageFetchHelper.return_value.get_image.side_effect = exc
            afo._image_fetch(inst)

    def test_transient_replication_failure_defers(self):
        afo = self._make_op()
        inst = mock.MagicMock()

        self._fetch_with_failure(
            afo, inst, BlobFetchFailed('no connection before timeout'))

        payload = self.mock_mariadb.get_work_queue_payload(self.QUEUE)
        self.assertEqual(1, payload['defer_count'])
        self.assertEqual('queued', afo.state.value)
        inst.enqueue_delete_due_error.assert_not_called()

    def test_transfer_setup_failure_defers_too(self):
        afo = self._make_op()

        self._fetch_with_failure(
            afo, None, BlobTransferSetupFailed('state is missing'))

        payload = self.mock_mariadb.get_work_queue_payload(self.QUEUE)
        self.assertEqual(1, payload['defer_count'])
        self.assertEqual('queued', afo.state.value)

    def test_exhausted_retries_error_the_instance_and_op(self):
        afo = self._make_op(defer_count=3)
        inst = mock.MagicMock()

        self._fetch_with_failure(
            afo, inst, BlobFetchFailed('no connection before timeout'))

        inst.enqueue_delete_due_error.assert_called_once()
        self.assertEqual('error', afo.state.value)
        self.assertIsNone(self.mock_mariadb.get_work_queue_payload(self.QUEUE))

    def test_exhausted_retries_leave_a_created_artifact_alone(self):
        # The artifact is not bad just because this node could not
        # replicate its blob -- other nodes still hold valid copies.
        a = Artifact.new(
            Artifact.TYPE_IMAGE, self.URL, name='an-image',
            namespace='system')
        a.state = Artifact.STATE_CREATED

        afo = self._make_op(defer_count=3)
        self._fetch_with_failure(
            afo, None, BlobFetchFailed('no connection before timeout'))

        self.assertEqual(
            Artifact.STATE_CREATED, Artifact.from_db(str(a.uuid)).state.value)

    def test_exhausted_retries_error_a_never_fetched_artifact(self):
        a = Artifact.new(
            Artifact.TYPE_IMAGE, self.URL, name='an-image',
            namespace='system')

        afo = self._make_op(defer_count=3)
        self._fetch_with_failure(
            afo, None, BlobFetchFailed('no connection before timeout'))

        self.assertEqual(
            Artifact.STATE_ERROR, Artifact.from_db(str(a.uuid)).state.value)

    def test_unexpected_dispatch_failure_errors_the_instance(self):
        # The dispatch_task catch-all previously errored the op without
        # telling the instance, which is how issue 3494's instance was
        # abandoned in state initial.
        inst = mock.MagicMock()
        afo = self._make_op(instance_uuid=str(uuid4()))

        with mock.patch.object(
                ArtifactFetchOp, '_image_fetch',
                side_effect=Exception('boom')), \
            mock.patch(
                'shakenfist.operations.artifact_fetch_op.Instance.from_db',
                return_value=inst):
            afo.dispatch_task(model_tasks.image_fetch)

        inst.enqueue_delete_due_error.assert_called_once()
        self.assertEqual('error', afo.state.value)

    def test_dispatch_failure_of_an_aborted_op_stays_aborted(self):
        # An op aborted because its instance start request was aborted
        # must not be forced to error (an invalid transition), and the
        # abort must survive the failure.
        inst = mock.MagicMock()
        afo = self._make_op(instance_uuid=str(uuid4()))
        afo.state = ArtifactFetchOp.STATE_ABORT

        with mock.patch.object(
                ArtifactFetchOp, '_image_fetch',
                side_effect=Exception('boom')), \
            mock.patch(
                'shakenfist.operations.artifact_fetch_op.Instance.from_db',
                return_value=inst):
            afo.dispatch_task(model_tasks.image_fetch)

        self.assertEqual('abort', afo.state.value)
