from unittest import mock
from uuid import uuid4

from pydantic import ValidationError

from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.constants import OPERATION_NAMES_TO_CLASSES
from shakenfist.etcd_schema.operations.net_iface_op import create_and_enqueue
from shakenfist.etcd_schema.operations.net_iface_op import current_version
from shakenfist.etcd_schema.operations.net_iface_op import model
from shakenfist.etcd_schema.operations.net_iface_op import model_tasks
from shakenfist.etcd_schema.operations.baseclusteroperation \
    import CLUSTER_OPERATIONS
from shakenfist.etcd_schema.operations.baseclusteroperation import PRIORITY
from shakenfist.operations.net_iface_op import NetIfaceOp
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class NetIfaceOpTestCase(base.ShakenFistTestCase):
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
            network_uuid=u2,
            interface_uuid=u3,
            tasks=[model_tasks.interface_float],
            priority=PRIORITY.user_facing,
            request_id=None,
            depends_on=None,
            runs_after=None,
            version=current_version
        )

        serialized = d.model_dump(mode='json')
        self.assertEqual(u1, serialized['uuid'])
        self.assertEqual(u2, serialized['network_uuid'])
        self.assertEqual(u3, serialized['interface_uuid'])
        self.assertEqual(['interface_float'], serialized['tasks'])
        self.assertEqual('user_facing', serialized['priority'])
        self.assertEqual(None, serialized['request_id'])
        self.assertEqual(None, serialized['depends_on'])
        self.assertEqual(None, serialized['runs_after'])
        self.assertEqual(current_version, serialized['version'])

    def test_model_bad_version(self):
        u1 = str(uuid4())
        u2 = str(uuid4())
        u3 = str(uuid4())

        self.assertRaises(
            ValidationError,
            model,
            uuid=u1,
            network_uuid=u2,
            interface_uuid=u3,
            tasks=[model_tasks.interface_float],
            priority=PRIORITY.user_facing,
            request_id=None,
            depends_on=None,
            runs_after=None,
            version=current_version + 1
        )

    def test_model_floating_network(self):
        u1 = str(uuid4())
        u2 = str(uuid4())

        d = model(
            uuid=u1,
            network_uuid='floating',
            interface_uuid=u2,
            tasks=[model_tasks.interface_float],
            priority=PRIORITY.user_facing,
            request_id=None,
            depends_on=None,
            runs_after=None,
            version=current_version
        )

        serialized = d.model_dump(mode='json')
        self.assertEqual('floating', serialized['network_uuid'])

    def test_model_bad_network_uuid(self):
        u1 = str(uuid4())
        u2 = str(uuid4())

        self.assertRaises(
            ValidationError,
            model,
            uuid=u1,
            network_uuid='notfloating',
            interface_uuid=u2,
            tasks=[model_tasks.interface_float],
            priority=PRIORITY.user_facing,
            request_id=None,
            depends_on=None,
            runs_after=None,
            version=current_version
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
            network_uuid=u1,
            interface_uuid=u2,
            tasks=[model_tasks.interface_float],
            priority=PRIORITY.user_facing
        )

        self.assertEqual(CLUSTER_OPERATIONS.net_iface_op, op_type)

        self.assertEqual(
            {
                'uuid': op_uuid,
                'network_uuid': u1,
                'interface_uuid': u2,
                'tasks': ['interface_float'],
                'priority': 'user_facing',
                'depends_on': None,
                'runs_after': None,
                'request_id': None,
                'version': 1
            },
            self.mock_etcd.get_raw(f'/sf/net_iface_op/{op_uuid}')
        )
        self.assertEqual(
            {
                'value': 'queued',
                'update_time': 123.0
            },
            self.mock_etcd.get_raw(
                f'/sf/attribute/net_iface_op/{op_uuid}/state')
        )
        self.assertEqual(
            {
                'operation_type': 'net_iface_op',
                'operation_uuid': op_uuid
            },
            self.mock_etcd.get_raw(
                (
                    '/sf/queue/networknode-clusteroperation-user_facing/'
                    '123.0-asdjfhkjadsfh'
                )
            )
        )

    def test_load_from_etcd(self):
        u1 = str(uuid4())
        u2 = str(uuid4())

        _, op_uuid = create_and_enqueue(
            network_uuid=u1,
            interface_uuid=u2,
            tasks=[model_tasks.interface_float],
            priority=PRIORITY.user_facing
        )

        nii = NetIfaceOp.from_db(op_uuid)
        self.assertNotEqual(None, nii)
        self.assertEqual('queued', nii.state.value)

    def test_object_mapping(self):
        self.assertTrue(
            NetIfaceOp.object_type in OPERATION_NAMES_TO_CLASSES)
        self.assertTrue(
            NetIfaceOp.object_type in OBJECT_NAMES_TO_CLASSES)
