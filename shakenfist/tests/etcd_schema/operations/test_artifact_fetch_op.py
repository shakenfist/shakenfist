from unittest import mock
from uuid import uuid4

from pydantic import ValidationError

from shakenfist.etcd_schema.operations.artifact_fetch_op import create_and_enqueue
from shakenfist.etcd_schema.operations.artifact_fetch_op import current_version
from shakenfist.etcd_schema.operations.artifact_fetch_op import model
from shakenfist.etcd_schema.operations.artifact_fetch_op import model_tasks
from shakenfist.etcd_schema.operations.baseclusteroperation \
    import CLUSTER_OPERATIONS
from shakenfist.etcd_schema.operations.baseclusteroperation \
    import Dependency
from shakenfist.etcd_schema.operations.baseclusteroperation import PRIORITY
from shakenfist.operations.artifact_fetch_op import ArtifactFetchOp
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class ArtifactFetchOpTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

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
                Dependency(
                    op_type=CLUSTER_OPERATIONS.node_blob_op,
                    op_uuid=u2
                )
            ],
            runs_after=[
                Dependency(
                    op_type=CLUSTER_OPERATIONS.node_blob_op,
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
                Dependency(
                    op_type=CLUSTER_OPERATIONS.node_blob_op,
                    op_uuid=u2
                )
            ],
            runs_after=[
                Dependency(
                    op_type=CLUSTER_OPERATIONS.node_blob_op,
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

        self.assertEqual(CLUSTER_OPERATIONS.artifact_fetch_op, op_type)

        self.assertEqual(
            {
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
            self.mock_etcd.get_raw(f'/sf/artifact_fetch_op/{op_uuid}')
        )
        self.assertEqual(
            {
                'value': 'queued',
                'update_time': 123.0
            },
            self.mock_etcd.get_raw(
                f'/sf/attribute/artifact_fetch_op/{op_uuid}/state')
        )
        self.assertEqual(
            {
                'operation_type': 'artifact_fetch_op',
                'operation_uuid': op_uuid
            },
            self.mock_etcd.get_raw(
                (
                    '/sf/queue/any-clusteroperation-user_facing/'
                    '123.0-asdjfhkjadsfh'
                )
            )
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
