from unittest import mock
from uuid import uuid4

from pydantic import ValidationError

from shakenfist.etcd_schema.operations.artifact_fetch_op \
    import object_type as afo_object_type
from shakenfist.etcd_schema.operations.node_inst_netdesc_op \
    import create_and_enqueue
from shakenfist.etcd_schema.operations.node_inst_netdesc_op import current_version
from shakenfist.etcd_schema.operations.node_inst_netdesc_op import model
from shakenfist.etcd_schema.operations.node_inst_netdesc_op import model_tasks
from shakenfist.etcd_schema.operations.baseclusteroperation \
    import CLUSTER_OPERATIONS
from shakenfist.etcd_schema.operations.baseclusteroperation import PRIORITY
from shakenfist.operations.node_inst_netdesc_op import NodeInstNetDescOp
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class NodeInstNetdescOpTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

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
            net_desc=[
                {
                    'network_uuid': u5,
                    'macaddress': None,
                    'float': True
                }
            ],
            priority=PRIORITY.user_waiting,
            request_id=None,
            tasks=[
                model_tasks.instance_preflight,
                model_tasks.instance_start
            ],
            depends_on=[
                {
                    'op_type': afo_object_type,
                    'op_uuid': u4
                }
            ],
            version=current_version
        )

        serialized = d.model_dump(mode='json')
        self.assertEqual(u1, serialized['uuid'])
        self.assertEqual(u2, serialized['node_uuid'])
        self.assertEqual(u3, serialized['instance_uuid'])
        self.assertEqual([
            {
                'network_uuid': u5,
                'macaddress': None,
                'float': True
            }
        ], serialized['net_desc'])
        self.assertEqual('user_waiting', serialized['priority'])
        self.assertEqual(None, serialized['request_id'])
        self.assertEqual(
            ['instance_preflight', 'instance_start'], serialized['tasks'])
        self.assertEqual([
            {
                'op_type': 'artifact_fetch_op',
                'op_uuid': u4
            }
        ],
            serialized['depends_on']
        )
        self.assertEqual(current_version, serialized['version'])

    def test_model_bad_version(self):
        u1 = str(uuid4())
        u2 = str(uuid4())
        u3 = str(uuid4())
        u4 = str(uuid4())
        u5 = str(uuid4())

        self.assertRaises(
            ValidationError,
            model,
            uuid=u1,
            node_uuid=u2,
            instance_uuid=u3,
            net_desc=[
                {
                    'network_uuid': u5,
                    'macaddress': None,
                    'float': True
                }
            ],
            priority=PRIORITY.user_waiting,
            request_id=None,
            tasks=[model_tasks.instance_preflight],
            depends_on=[(afo_object_type, u4)],
            version=current_version + 1
        )

    @mock.patch(
        'shakenfist_utilities.random.random_id',
        return_value='asdjfhkjadsfh'
    )
    @mock.patch('time.time', return_value=123.0)
    def test_create_and_enqueue(self, _mock_time, _mock_id):
        node_uuid = 'sf-1'
        instance_uuid = '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7'
        net_desc = [
            {
                'network_uuid': 'e7319f3b-ec81-4b2a-a5eb-914d89b5441b',
                'model': 'e1000',
                'address': '192.168.0.1',
                'order': 0,
                'float': True
            }
        ]
        op_type, op_uuid = create_and_enqueue(
            node_uuid, instance_uuid, net_desc, [
                model_tasks.instance_preflight,
                model_tasks.instance_start
            ],
            PRIORITY.user_waiting
        )
        self.assertEqual(CLUSTER_OPERATIONS.node_inst_netdesc_op, op_type)

        self.assertEqual(
            {
                'instance_uuid': '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7',
                'net_desc': [
                    {
                        'network_uuid': 'e7319f3b-ec81-4b2a-a5eb-914d89b5441b',
                        'model': 'e1000',
                        'address': '192.168.0.1',
                        'order': 0,
                        'float': True
                    }
                ],
                'depends_on': None,
                'node_uuid': 'sf-1',
                'priority': 'user_waiting',
                'request_id': None,
                'tasks': [
                    'instance_preflight',
                    'instance_start'
                ],
                'uuid': op_uuid,
                'version': 1
            },
            self.mock_etcd.get_raw(f'/sf/node_inst_netdesc_op/{op_uuid}')
        )
        self.assertEqual(
            {
                'value': 'queued',
                'update_time': 123.0
            },
            self.mock_etcd.get_raw(
                f'/sf/attribute/node_inst_netdesc_op/{op_uuid}/state')
        )
        self.assertEqual(
            {
                'operation_type': 'node_inst_netdesc_op',
                'operation_uuid': op_uuid
            },
            self.mock_etcd.get_raw(
                (
                    '/sf/queue/sf-1-clusteroperation-user_waiting/'
                    '123.0-asdjfhkjadsfh'
                )
            )
        )

    def test_load_from_etcd(self):
        node_uuid = 'sf-1'
        instance_uuid = '5c61e63d-8bd7-4d14-9af2-fa946ae9b1e7'
        _, op_uuid = create_and_enqueue(
            node_uuid,
            instance_uuid,
            [
                {
                    'network_uuid': 'e7319f3b-ec81-4b2a-a5eb-914d89b5441b',
                    'model': 'e1000',
                    'address': '192.168.0.1',
                    'order': 0,
                    'float': True
                }
            ],
            [
                model_tasks.instance_preflight,
                model_tasks.instance_start
            ],
            PRIORITY.user_facing
        )

        nio = NodeInstNetDescOp.from_db(op_uuid)
        self.assertNotEqual(None, nio)
        self.assertEqual('queued', nio.state.value)
