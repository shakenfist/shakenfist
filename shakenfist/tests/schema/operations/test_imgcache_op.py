from unittest import mock
from uuid import uuid4

from pydantic import ValidationError

from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.constants import OPERATION_NAMES_TO_CLASSES
from shakenfist.constants import TRANSCODE_DESCRIPTION
from shakenfist.schema.operations.imgcache_op import create_and_enqueue
from shakenfist.schema.operations.imgcache_op import current_version
from shakenfist.schema.operations.imgcache_op import model
from shakenfist.schema.operations.imgcache_op import model_tasks
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.operations.imgcache_op import ImageCacheOp
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


CACHE_PATH = ('/srv/shakenfist/image_cache/'
              '17e5983f-ca2c-4c16-aa07-2d7f82ee584d.qcow2')


class ImageCacheOpTestCase(base.ShakenFistTestCase):
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
            cache_path=CACHE_PATH,
            transcode_description=TRANSCODE_DESCRIPTION,
            tasks=[model_tasks.archive_transcode],
            priority=PRIORITY.background_high_io,
            request_id=None,
            depends_on=None,
            runs_after=None,
            version=current_version
        )

        serialized = d.model_dump(mode='json')
        self.assertEqual(u1, serialized['uuid'])
        self.assertEqual(u2, serialized['node_uuid'])
        self.assertEqual(u3, serialized['blob_uuid'])
        self.assertEqual(CACHE_PATH, serialized['cache_path'])
        self.assertEqual(
            TRANSCODE_DESCRIPTION, serialized['transcode_description'])
        self.assertEqual('background_high_io', serialized['priority'])
        self.assertEqual(None, serialized['request_id'])
        self.assertEqual(['archive_transcode'], serialized['tasks'])
        self.assertEqual(None, serialized['depends_on'])
        self.assertEqual(None, serialized['runs_after'])
        self.assertEqual(current_version, serialized['version'])

    def test_model_bad_version(self):
        u1 = str(uuid4())
        u2 = str(uuid4())

        self.assertRaises(
            ValidationError,
            model,
            uuid=u1,
            node_uuid=u2,
            cache_path=CACHE_PATH,
            transcode_description=TRANSCODE_DESCRIPTION,
            tasks=[model_tasks.archive_transcode],
            priority=PRIORITY.background_high_io,
            version=current_version + 1
        )

    @mock.patch(
        'shakenfist_utilities.random.random_id',
        return_value='asdjfhkjadsfh'
    )
    @mock.patch('time.time', return_value=123.0)
    def test_create_and_enqueue(self, _mock_time, _mock_id):
        u1 = str(uuid4())
        u2 = str(uuid4())

        op_type, op_uuid = create_and_enqueue(
            u1,
            u2,
            CACHE_PATH,
            TRANSCODE_DESCRIPTION,
            [model_tasks.archive_transcode],
            PRIORITY.background_high_io
        )

        self.assertEqual(ObjectType.IMGCACHE_OP, op_type)

        self.assertEqual(
            {
                'node_uuid': u1,
                'blob_uuid': u2,
                'cache_path': CACHE_PATH,
                'transcode_description': TRANSCODE_DESCRIPTION,
                'depends_on': None,
                'runs_after': None,
                'priority': 'background_high_io',
                'request_id': None,
                'tasks': ['archive_transcode'],
                'uuid': op_uuid,
                'version': 1
            },
            self.mock_etcd.get_cluster_operation_metadata(op_uuid)
        )
        self.assertEqual(
            {
                'value': 'queued',
                'update_time': 123.0
            },
            self.mock_etcd.get_mariadb_state('imgcache_op', op_uuid)
        )
        self.assertEqual(
            {
                'operation_type': 'imgcache_op',
                'operation_uuid': op_uuid
            },
            self.mock_etcd.get_work_queue_payload(
                f'{u1}-clusteroperation-background_high_io')
        )

    def test_load_from_etcd(self):
        u1 = str(uuid4())
        u2 = str(uuid4())

        _, op_uuid = create_and_enqueue(
            u1,
            u2,
            CACHE_PATH,
            TRANSCODE_DESCRIPTION,
            [model_tasks.archive_transcode],
            PRIORITY.background_high_io
        )

        ico = ImageCacheOp.from_db(op_uuid)
        self.assertNotEqual(None, ico)
        self.assertEqual('queued', ico.state.value)

    def test_object_mapping(self):
        self.assertTrue(
            ImageCacheOp.object_type in OPERATION_NAMES_TO_CLASSES)
        self.assertTrue(
            ImageCacheOp.object_type in OBJECT_NAMES_TO_CLASSES)
