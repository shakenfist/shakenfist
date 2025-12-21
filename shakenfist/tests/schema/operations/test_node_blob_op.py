from unittest import mock
from uuid import uuid4

from pydantic import ValidationError

from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.constants import OPERATION_NAMES_TO_CLASSES
from shakenfist.schema.operations.node_blob_op import create_and_enqueue
from shakenfist.schema.operations.node_blob_op import current_version
from shakenfist.schema.operations.node_blob_op import model
from shakenfist.schema.operations.node_blob_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation \
    import CLUSTER_OPERATIONS
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.operations.node_blob_op import NodeBlobOp
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class NodeBlobOpTestCase(base.ShakenFistTestCase):
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
            node_uuid=u2,
            blob_uuid=u3,
            priority=PRIORITY.background_high_io,
            request_id=None,
            tasks=[
                model_tasks.ensure_local,
                model_tasks.verify_size_and_checksum
            ],
            depends_on=[],
            runs_after=[],
            version=current_version
        )

        serialized = d.model_dump(mode='json')
        self.assertEqual(u1, serialized['uuid'])
        self.assertEqual(u2, serialized['node_uuid'])
        self.assertEqual(u3, serialized['blob_uuid'])
        self.assertEqual('background_high_io', serialized['priority'])
        self.assertEqual(None, serialized['request_id'])
        self.assertEqual([
            'ensure_local',
            'verify_size_and_checksum'
        ], serialized['tasks'])
        self.assertEqual([], serialized['depends_on'])
        self.assertEqual([], serialized['runs_after'])
        self.assertEqual(current_version, serialized['version'])

    def test_model_bad_version(self):
        u1 = str(uuid4())
        u2 = str(uuid4())
        u3 = str(uuid4())

        self.assertRaises(
            ValidationError,
            model,
            uuid=u1,
            node_uuid=u2,
            blob_uuid=u3,
            priority=PRIORITY.background_high_io,
            request_id=None,
            tasks=[
                model_tasks.ensure_local,
                model_tasks.verify_size_and_checksum
            ],
            depends_on=[],
            runs_after=[],
            version=current_version + 1
        )

    @mock.patch(
        'shakenfist_utilities.random.random_id',
        return_value='asdjfhkjadsfh'
    )
    @mock.patch('time.time', return_value=123.0)
    def test_create_and_enqueue(self, _mock_time, _mock_id):
        node_uuid = 'sf-1'
        blob_uuid = '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7'
        op_type, op_uuid = create_and_enqueue(
            node_uuid, blob_uuid, [
                model_tasks.ensure_local,
                model_tasks.verify_size_and_checksum
            ],
            PRIORITY.background_high_io
        )
        self.assertEqual(CLUSTER_OPERATIONS.node_blob_op, op_type)

        self.assertEqual(
            {
                'blob_uuid': '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7',
                'depends_on': None,
                'runs_after': None,
                'node_uuid': 'sf-1',
                'priority': 'background_high_io',
                'request_id': None,
                'tasks': ['ensure_local', 'verify_size_and_checksum'],
                'uuid': op_uuid,
                'version': 1
            },
            self.mock_etcd.get_raw(f'/sf/node_blob_op/{op_uuid}')
        )
        self.assertEqual(
            {
                'value': 'queued',
                'update_time': 123.0
            },
            self.mock_etcd.get_raw(
                f'/sf/attribute/node_blob_op/{op_uuid}/state')
        )
        self.assertEqual(
            {
                'operation_type': 'node_blob_op',
                'operation_uuid': op_uuid
            },
            self.mock_etcd.get_raw(
                (
                    '/sf/queue/sf-1-clusteroperation-background_high_io/'
                    '123.0-asdjfhkjadsfh'
                )
            )
        )

    def test_load_from_etcd(self):
        node_uuid = 'sf-1'
        blob_uuid = '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7'
        _, op_uuid = create_and_enqueue(
            node_uuid, blob_uuid, [
                model_tasks.ensure_local,
                model_tasks.verify_size_and_checksum
            ],
            PRIORITY.background_high_io
        )

        nbo = NodeBlobOp.from_db(op_uuid)
        self.assertNotEqual(None, nbo)
        self.assertEqual('queued', nbo.state.value)

    def test_object_mapping(self):
        self.assertTrue(NodeBlobOp.object_type in OPERATION_NAMES_TO_CLASSES)
        self.assertTrue(NodeBlobOp.object_type in OBJECT_NAMES_TO_CLASSES)
