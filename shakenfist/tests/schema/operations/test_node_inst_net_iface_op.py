from unittest import mock
from uuid import uuid4

from pydantic import ValidationError

from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.constants import OPERATION_NAMES_TO_CLASSES
from shakenfist.schema.operations.node_inst_net_iface_op \
    import create_and_enqueue
from shakenfist.schema.operations.node_inst_net_iface_op \
    import current_version
from shakenfist.schema.operations.node_inst_net_iface_op import model
from shakenfist.schema.operations.node_inst_net_iface_op \
    import model_tasks
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.operations.node_inst_net_iface_op import NodeInstNetIfaceOp
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class NodeInstNetIfaceOpTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

    def test_model(self):
        u1 = str(uuid4())
        u2 = str(uuid4())
        u3 = str(uuid4())
        u4 = str(uuid4())
        u5 = str(uuid4())

        d = model(
            uuid=u1,
            node_uuid=u2,
            instance_uuid=u3,
            network_uuid=u4,
            interface_uuid=u5,
            priority=PRIORITY.user_waiting,
            request_id=None,
            tasks=[
                model_tasks.hot_plug_instance_interface
            ],
            depends_on=[],
            runs_after=[],
            version=current_version
        )

        serialized = d.model_dump(mode='json')
        self.assertEqual(u1, serialized['uuid'])
        self.assertEqual(u2, serialized['node_uuid'])
        self.assertEqual(u3, serialized['instance_uuid'])
        self.assertEqual(u4, serialized['network_uuid'])
        self.assertEqual(u5, serialized['interface_uuid'])
        self.assertEqual('user_waiting', serialized['priority'])
        self.assertEqual(None, serialized['request_id'])
        self.assertEqual(
            ['hot_plug_instance_interface'], serialized['tasks'])
        self.assertEqual([], serialized['depends_on'])
        self.assertEqual([], serialized['runs_after'])
        self.assertEqual(current_version, serialized['version'])

    def test_model_bad_version(self):
        u1 = str(uuid4())
        u2 = str(uuid4())
        u3 = str(uuid4())
        u4 = str(uuid4())
        u4 = str(uuid4())
        u5 = str(uuid4())

        self.assertRaises(
            ValidationError,
            model,
            uuid=u1,
            node_uuid=u2,
            instance_uuid=u3,
            network_uuid=u4,
            interface_uuid=u5,
            priority=PRIORITY.user_waiting,
            request_id=None,
            tasks=[model_tasks.hot_plug_instance_interface],
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
        instance_uuid = '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7'
        network_uuid = '4c8ad117-8c78-4df0-8471-3dc1f1af6f2e'
        interface_uuid = 'a7a25f51-99fe-4a7e-be64-16d230609d51'

        op_type, op_uuid = create_and_enqueue(
            node_uuid, instance_uuid, network_uuid, interface_uuid, [
                model_tasks.hot_plug_instance_interface
            ],
            PRIORITY.user_waiting
        )
        self.assertEqual(ObjectType.NODE_INST_NET_IFACE_OP, op_type)

        self.assertEqual(
            {
                'instance_uuid': '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7',
                'network_uuid': '4c8ad117-8c78-4df0-8471-3dc1f1af6f2e',
                'interface_uuid': 'a7a25f51-99fe-4a7e-be64-16d230609d51',
                'depends_on': None,
                'runs_after': None,
                'node_uuid': 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d',
                'priority': 'user_waiting',
                'request_id': None,
                'tasks': [
                    'hot_plug_instance_interface'
                ],
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
            self.mock_mariadb.get_mariadb_state('node_inst_net_iface_op', op_uuid)
        )
        self.assertEqual(
            {
                'operation_type': 'node_inst_net_iface_op',
                'operation_uuid': op_uuid
            },
            self.mock_mariadb.get_work_queue_payload(
                'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'
                '-clusteroperation-user_waiting')
        )

    def test_load_from_etcd(self):
        node_uuid = 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'
        instance_uuid = '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7'
        network_uuid = '4c8ad117-8c78-4df0-8471-3dc1f1af6f2e'
        interface_uuid = 'a7a25f51-99fe-4a7e-be64-16d230609d51'

        _, op_uuid = create_and_enqueue(
            node_uuid,
            instance_uuid,
            network_uuid,
            interface_uuid,
            [
                model_tasks.hot_plug_instance_interface
            ],
            PRIORITY.user_facing
        )

        nio = NodeInstNetIfaceOp.from_db(op_uuid)
        self.assertNotEqual(None, nio)
        self.assertEqual('queued', nio.state.value)

    def test_object_mapping(self):
        self.assertTrue(
            NodeInstNetIfaceOp.object_type in OPERATION_NAMES_TO_CLASSES)
        self.assertTrue(
            NodeInstNetIfaceOp.object_type in OBJECT_NAMES_TO_CLASSES)
