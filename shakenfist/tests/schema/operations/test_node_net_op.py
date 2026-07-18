from unittest import mock
from uuid import uuid4

from pydantic import ValidationError

from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.constants import OPERATION_NAMES_TO_CLASSES
from shakenfist.schema.operations.node_net_op import create_and_enqueue
from shakenfist.schema.operations.node_net_op import current_version
from shakenfist.schema.operations.node_net_op import model
from shakenfist.schema.operations.node_net_op import model_tasks
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.operations.node_net_op import NodeNetOp
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class NodeNetOpTestCase(base.ShakenFistTestCase):
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
            node_uuid=u2,
            network_uuid=u3,
            priority=PRIORITY.background,
            request_id=None,
            tasks=[model_tasks.network_destroy],
            depends_on=[],
            runs_after=[],
            version=current_version
        )

        serialized = d.model_dump(mode='json')
        self.assertEqual(u1, serialized['uuid'])
        self.assertEqual(u2, serialized['node_uuid'])
        self.assertEqual(u3, serialized['network_uuid'])
        self.assertEqual('background', serialized['priority'])
        self.assertEqual(None, serialized['request_id'])
        self.assertEqual(['network_destroy'], serialized['tasks'])
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
            network_uuid=u3,
            priority=PRIORITY.background,
            request_id=None,
            tasks=[model_tasks.network_destroy],
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
        node_uuid = 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'
        network_uuid = '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7'
        op_type, op_uuid = create_and_enqueue(
            node_uuid, network_uuid, [model_tasks.network_destroy],
            PRIORITY.background
        )
        self.assertEqual(ObjectType.NODE_NET_OP, op_type)

        self.assertEqual(
            {
                'network_uuid': '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7',
                'depends_on': None,
                'runs_after': None,
                'node_uuid': 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d',
                'priority': 'background',
                'request_id': None,
                'tasks': ['network_destroy'],
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
            self.mock_mariadb.get_mariadb_state('node_net_op', op_uuid)
        )
        self.assertEqual(
            {
                'operation_type': 'node_net_op',
                'operation_uuid': op_uuid
            },
            self.mock_mariadb.get_work_queue_payload(
                'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'
                '-clusteroperation-background')
        )

    def test_load_from_etcd(self):
        node_uuid = 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'
        network_uuid = '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7'
        _, op_uuid = create_and_enqueue(
            node_uuid, network_uuid, [model_tasks.network_destroy],
            PRIORITY.background
        )

        nbo = NodeNetOp.from_db(op_uuid)
        self.assertNotEqual(None, nbo)
        self.assertEqual('queued', nbo.state.value)

    def test_object_mapping(self):
        self.assertTrue(NodeNetOp.object_type in OPERATION_NAMES_TO_CLASSES)
        self.assertTrue(NodeNetOp.object_type in OBJECT_NAMES_TO_CLASSES)


class ModelTasksEnumTestCase(base.ShakenFistTestCase):
    """Verify all model_tasks enum values are correct and stable."""

    def test_existing_task_values_are_unchanged(self):
        """network_destroy must remain value 1 (breaks in-flight records if changed)."""
        self.assertEqual(1, model_tasks.network_destroy.value)

    def test_new_lifecycle_apply_task_value(self):
        """network_apply_create_hypervisor must have value 2."""
        self.assertEqual(2, model_tasks.network_apply_create_hypervisor.value)

    def test_model_accepts_network_apply_create_hypervisor(self):
        """model accepts network_apply_create_hypervisor as a task."""
        node_uuid = str(uuid4())
        network_uuid = str(uuid4())

        m = model(
            uuid=str(uuid4()),
            node_uuid=node_uuid,
            network_uuid=network_uuid,
            priority=PRIORITY.user_facing,
            request_id=None,
            tasks=[model_tasks.network_apply_create_hypervisor],
            depends_on=None,
            runs_after=None,
            version=current_version
        )

        serialized = m.model_dump(mode='json')
        self.assertEqual(node_uuid, serialized['node_uuid'])
        self.assertEqual(network_uuid, serialized['network_uuid'])
        self.assertEqual(['network_apply_create_hypervisor'], serialized['tasks'])
