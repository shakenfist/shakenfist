from unittest import mock
from uuid import uuid4

from pydantic import ValidationError

from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.constants import OPERATION_NAMES_TO_CLASSES
from shakenfist.schema.operations.node_aop_op import create_and_enqueue
from shakenfist.schema.operations.node_aop_op import current_version
from shakenfist.schema.operations.node_aop_op import model
from shakenfist.schema.operations.node_aop_op import model_tasks
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.operations.node_aop_op import NodeAgentopOp
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class NodeAgentopOpTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

    def test_model(self):
        u1 = str(uuid4())
        u2 = str(uuid4())
        u3 = str(uuid4())

        u4 = str(uuid4())

        d = model(
            uuid=u1,
            node_uuid=u2,
            agentoperation_uuid=u3,
            instance_uuid=u4,
            priority=PRIORITY.background,
            request_id=None,
            tasks=[model_tasks.preflight],
            depends_on=[],
            runs_after=[],
            version=current_version
        )

        serialized = d.model_dump(mode='json')
        self.assertEqual(u1, serialized['uuid'])
        self.assertEqual(u2, serialized['node_uuid'])
        self.assertEqual(u3, serialized['agentoperation_uuid'])
        self.assertEqual(u4, serialized['instance_uuid'])
        self.assertEqual('background', serialized['priority'])
        self.assertEqual(None, serialized['request_id'])
        self.assertEqual(['preflight'], serialized['tasks'])
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
            agentoperation_uuid=u3,
            instance_uuid=str(uuid4()),
            priority=PRIORITY.background,
            request_id=None,
            tasks=[model_tasks.preflight],
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
        agentoperation_uuid = '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7'
        instance_uuid = '7d0e5b9e-2d6c-4c5a-9a3e-1f2b3c4d5e6f'
        op_type, op_uuid = create_and_enqueue(
            node_uuid, agentoperation_uuid, instance_uuid,
            [model_tasks.preflight], PRIORITY.background
        )
        self.assertEqual(ObjectType.NODE_AOP_OP, op_type)

        self.assertEqual(
            {
                'agentoperation_uuid': '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7',
                'depends_on': None,
                'runs_after': None,
                'instance_uuid': instance_uuid,
                'node_uuid': 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d',
                'priority': 'background',
                'request_id': None,
                'tasks': ['preflight'],
                'uuid': op_uuid,
                'version': 2
            },
            self.mock_mariadb.get_cluster_operation_metadata(op_uuid)
        )

        # The preflight targets its instance as well as its agent
        # operation, so that deleting the instance cancels it (issue
        # 4672).
        self.assertEqual(
            [('agentoperation', agentoperation_uuid),
             ('instance', instance_uuid)],
            [(t.target_object_type, t.target_uuid)
             for t in self.mock_mariadb.get_cluster_operation_targets(
                 op_uuid)])
        self.assertEqual(
            {
                'value': 'queued',
                'update_time': 123.0
            },
            self.mock_mariadb.get_mariadb_state('node_aop_op', op_uuid)
        )
        self.assertEqual(
            {
                'operation_type': 'node_aop_op',
                'operation_uuid': op_uuid
            },
            self.mock_mariadb.get_work_queue_payload(
                'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'
                '-clusteroperation-background')
        )

    def test_load_from_etcd(self):
        node_uuid = 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'
        agentoperation_uuid = '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7'
        instance_uuid = '7d0e5b9e-2d6c-4c5a-9a3e-1f2b3c4d5e6f'
        _, op_uuid = create_and_enqueue(
            node_uuid, agentoperation_uuid, instance_uuid,
            [model_tasks.preflight], PRIORITY.background
        )

        nbo = NodeAgentopOp.from_db(op_uuid)
        self.assertNotEqual(None, nbo)
        self.assertEqual('queued', nbo.state.value)
        self.assertEqual(instance_uuid, nbo.instance_uuid)
        self.assertEqual(instance_uuid, nbo.external_view()['instance_uuid'])

    def _version_1(self):
        return {
            'uuid': str(uuid4()),
            'node_uuid': str(uuid4()),
            'agentoperation_uuid': str(uuid4()),
            'priority': 'background',
            'request_id': None,
            'tasks': ['preflight'],
            'depends_on': None,
            'runs_after': None,
            'version': 1,
        }

    def test_upgrade_recovers_the_instance_from_the_agent_operation(self):
        static_values = self._version_1()
        aop = mock.MagicMock()
        aop.instance_uuid = '7d0e5b9e-2d6c-4c5a-9a3e-1f2b3c4d5e6f'
        with mock.patch(
                'shakenfist.operations.node_aop_op.AgentOperation.from_db',
                return_value=aop) as from_db:
            op = NodeAgentopOp(static_values)
        from_db.assert_called_once_with(
            static_values['agentoperation_uuid'],
            suppress_failure_audit=True)
        self.assertEqual(aop.instance_uuid, op.instance_uuid)
        self.assertEqual(2, static_values['version'])

    def test_upgrade_without_the_agent_operation(self):
        with mock.patch(
                'shakenfist.operations.node_aop_op.AgentOperation.from_db',
                return_value=None):
            op = NodeAgentopOp(self._version_1())
        self.assertIsNone(op.instance_uuid)
        self.assertIsNone(op.external_view()['instance_uuid'])

    def test_object_mapping(self):
        self.assertTrue(
            NodeAgentopOp.object_type in OPERATION_NAMES_TO_CLASSES)
        self.assertTrue(
            NodeAgentopOp.object_type in OBJECT_NAMES_TO_CLASSES)
