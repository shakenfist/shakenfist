# Copyright 2019 Michael Still and contributors
from unittest import mock
from uuid import uuid4

from pydantic import ValidationError

from shakenfist import exceptions
from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.constants import OPERATION_NAMES_TO_CLASSES
from shakenfist.schema.operations.net_op import create_and_enqueue
from shakenfist.schema.operations.net_op import current_version
from shakenfist.schema.operations.net_op import initial_version
from shakenfist.schema.operations.net_op import model
from shakenfist.schema.operations.net_op import model_tasks
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.operations.net_op import NetOp
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class NetOpTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

    def test_model(self):
        u1 = str(uuid4())
        u2 = str(uuid4())

        d = model(
            uuid=u1,
            network_uuid=u2,
            tasks=[model_tasks.network_deploy],
            priority=PRIORITY.user_waiting,
            request_id=None,
            depends_on=None,
            runs_after=None,
            version=current_version
        )

        serialized = d.model_dump(mode='json')
        self.assertEqual(u1, serialized['uuid'])
        self.assertEqual(u2, serialized['network_uuid'])
        self.assertEqual(['network_deploy'], serialized['tasks'])
        self.assertEqual('user_waiting', serialized['priority'])
        self.assertEqual(None, serialized['request_id'])
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
            network_uuid=u2,
            tasks=[model_tasks.network_deploy],
            priority=PRIORITY.user_waiting,
            request_id=None,
            depends_on=None,
            runs_after=None,
            version=current_version + 1
        )

    def test_model_floating_network(self):
        u1 = str(uuid4())

        d = model(
            uuid=u1,
            network_uuid=FLOATING_NETWORK_UUID,
            tasks=[model_tasks.network_deploy],
            priority=PRIORITY.user_waiting,
            request_id=None,
            depends_on=None,
            runs_after=None,
            version=current_version
        )

        serialized = d.model_dump(mode='json')
        self.assertEqual(str(FLOATING_NETWORK_UUID), serialized['network_uuid'])

    def test_model_bad_network_uuid(self):
        u1 = str(uuid4())

        self.assertRaises(
            ValidationError,
            model,
            uuid=u1,
            network_uuid='notfloating',
            tasks=[model_tasks.network_deploy],
            priority=PRIORITY.user_waiting,
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

        op_type, op_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_deploy],
            priority=PRIORITY.user_waiting
        )

        self.assertEqual(ObjectType.NET_OP, op_type)

        self.assertEqual(
            {
                'uuid': op_uuid,
                'network_uuid': u1,
                'node_uuid': None,
                'floating_address': None,
                'inner_address': None,
                'tasks': ['network_deploy'],
                'priority': 'user_waiting',
                'depends_on': None,
                'runs_after': None,
                'request_id': None,
                'version': current_version
            },
            self.mock_mariadb.get_cluster_operation_metadata(op_uuid)
        )
        self.assertEqual(
            {
                'value': 'queued',
                'update_time': 123.0
            },
            self.mock_mariadb.get_mariadb_state('net_op', op_uuid)
        )
        self.assertEqual(
            {
                'operation_type': 'net_op',
                'operation_uuid': op_uuid
            },
            self.mock_mariadb.get_work_queue_payload(
                'networknode-clusteroperation-user_waiting')
        )

    def test_create_and_enqueue_derives_node_uuid_from_target(self):
        # ``target`` is queue routing, but for a per-node enqueue it
        # *is* the node uuid -- decision 2 derives node_uuid from it
        # rather than taking a separate parameter, so a caller cannot
        # forget to pass one and silently degrade the coalescing key.
        u1 = str(uuid4())
        node = str(uuid4())

        _, op_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_remove_nat],
            priority=PRIORITY.user_facing,
            target=node,
        )

        self.assertEqual(
            node,
            self.mock_mariadb.get_cluster_operation_metadata(
                op_uuid)['node_uuid'])

    def test_create_and_enqueue_node_uuid_none_for_networknode_target(self):
        # The cluster-wide network-node queue is not a node's own queue,
        # so it must not be mistaken for one -- node_uuid stays None.
        u1 = str(uuid4())

        _, op_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_remove_nat],
            priority=PRIORITY.user_facing,
            target='networknode',
        )

        self.assertIsNone(
            self.mock_mariadb.get_cluster_operation_metadata(
                op_uuid)['node_uuid'])

    def test_load_from_etcd(self):
        u1 = str(uuid4())

        _, op_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_deploy],
            priority=PRIORITY.user_waiting
        )

        nii = NetOp.from_db(op_uuid)
        self.assertNotEqual(None, nii)
        self.assertEqual('queued', nii.state.value)

    def test_object_mapping(self):
        self.assertTrue(NetOp.object_type in OPERATION_NAMES_TO_CLASSES)
        self.assertTrue(NetOp.object_type in OBJECT_NAMES_TO_CLASSES)

    def test_enqueue_side_dedup_reuses_existing_pending_op(self):
        # Two consecutive enqueues for the same network and a
        # coalescible task: the second call must short-circuit and
        # return the first op's uuid instead of inserting a new row.
        u1 = str(uuid4())

        _, first_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_apply_update_dnsmasq],
            priority=PRIORITY.user_facing_high_io,
        )

        _, second_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_apply_update_dnsmasq],
            priority=PRIORITY.user_facing_high_io,
        )

        # Same uuid handed back; only one cluster_operations row was
        # actually inserted.
        self.assertEqual(first_uuid, second_uuid)
        self.assertEqual(
            1,
            sum(
                1 for row
                in self.mock_mariadb.cluster_operations_store.values()
                if row.get('operation_type') == 'net_op'
                and row.get('network_uuid') == u1
            ))

    def test_enqueue_side_dedup_skipped_for_non_coalescible_task(self):
        # network_remove_dnsmasq is *not* in COALESCIBLE_TASKS, so two
        # enqueues must produce two distinct rows.
        u1 = str(uuid4())

        _, first_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_remove_dnsmasq],
            priority=PRIORITY.user_facing,
        )
        _, second_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_remove_dnsmasq],
            priority=PRIORITY.user_facing,
        )

        self.assertNotEqual(first_uuid, second_uuid)

    def test_enqueue_side_dedup_skipped_when_dependencies_present(self):
        # An op with depends_on encodes an ordering constraint the
        # caller cares about; reusing a sibling without that
        # constraint would erase it. Dedup must skip this case.
        u1 = str(uuid4())
        u2 = str(uuid4())

        # The pre-existing op is a vanilla single-task enqueue.
        _, baseline_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_apply_update_dnsmasq],
            priority=PRIORITY.user_facing_high_io,
        )

        # The new caller wants the same work but ordered against
        # another op; dedup must NOT reuse the baseline.
        from shakenfist.schema.operations.baseclusteroperation \
            import dependency
        _, new_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_apply_update_dnsmasq],
            priority=PRIORITY.user_facing_high_io,
            depends_on=[
                dependency(op_type=ObjectType.NET_OP, op_uuid=u2)],
        )

        self.assertNotEqual(baseline_uuid, new_uuid)


class EnqueueTimeCoalescibleGuardTestCase(base.ShakenFistTestCase):
    """Decision 4: a coalescible task's target must be one the key can
    distinguish, on a dispatcher that partitions its workers by it.

    ``shakenfist/tests/schema/test_net_op_coalescing.py`` covers this
    guard exhaustively with the enqueue path mocked out; these tests
    exercise it through the same real MockMariaDB path the rest of this
    module uses, so the derived node_uuid and the guard's own decision
    are proven against the same enqueue.
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    def test_coalescible_task_per_node_clusteroperation_family_raises(self):
        # Decision 5: the default 'clusteroperation' family routes a
        # per-node target to sf-queues, which has no per-target worker
        # partitioning -- a node-aware key is necessary but not
        # sufficient there. This is the case that must never regress.
        self.assertRaises(
            exceptions.InvalidCoalescibleEnqueue,
            create_and_enqueue,
            network_uuid=str(uuid4()),
            tasks=[model_tasks.network_ensure_mesh],
            priority=PRIORITY.user_facing,
            target=str(uuid4()),
        )

    def test_coalescible_task_per_node_network_family_is_allowed(self):
        # The case this phase exists to allow: family='network' reaches
        # sf-net, which partitions its workers by target.
        u1 = str(uuid4())
        node = str(uuid4())

        _, op_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_ensure_mesh],
            priority=PRIORITY.user_facing,
            target=node,
            family='network',
        )

        metadata = self.mock_mariadb.get_cluster_operation_metadata(op_uuid)
        self.assertEqual(node, metadata['node_uuid'])

    def test_non_coalescible_task_per_node_target_is_always_allowed(self):
        # The guard must not become a blanket ban on per-node NetOps: a
        # task which never folds is unaffected by where it is drained,
        # so it is fine on the default clusteroperation family too.
        u1 = str(uuid4())
        node = str(uuid4())

        _, op_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_remove_nat],
            priority=PRIORITY.user_facing,
            target=node,
        )

        self.assertIsNotNone(op_uuid)


class ModelTasksEnumTestCase(base.ShakenFistTestCase):
    """Verify all model_tasks enum values are correct and stable."""

    def test_existing_task_values_are_unchanged(self):
        """Tasks 1-6 must not change values (break in-flight records)."""
        self.assertEqual(1, model_tasks.network_deploy.value)
        self.assertEqual(2, model_tasks.network_destroy.value)
        self.assertEqual(3, model_tasks.network_update_dnsmasq.value)
        self.assertEqual(4, model_tasks.network_remove_dnsmasq.value)
        self.assertEqual(5, model_tasks.network_remove_nat.value)
        self.assertEqual(6, model_tasks.network_ensure_mesh.value)

    def test_new_floating_ip_task_values(self):
        """New floating-IP tasks have values 7 and 8."""
        self.assertEqual(7, model_tasks.network_add_floating_ip.value)
        self.assertEqual(8, model_tasks.network_remove_floating_ip.value)

    def test_new_dnsmasq_apply_task_values(self):
        """New dnsmasq apply tasks have values 9 and 10."""
        self.assertEqual(9, model_tasks.network_apply_update_dnsmasq.value)
        self.assertEqual(10, model_tasks.network_apply_remove_dnsmasq.value)

    def test_new_lifecycle_apply_task_values(self):
        """New lifecycle apply tasks have values 11 and 12."""
        self.assertEqual(11, model_tasks.network_apply_create_network_node.value)
        self.assertEqual(12, model_tasks.network_apply_delete_network_node.value)


class ModelVersionTestCase(base.ShakenFistTestCase):
    """Version range acceptance tests for the schema model."""

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    def _base_kwargs(self):
        return {
            'uuid': str(uuid4()),
            'network_uuid': str(uuid4()),
            'tasks': [model_tasks.network_deploy],
            'priority': PRIORITY.user_waiting,
            'request_id': None,
            'depends_on': None,
            'runs_after': None,
        }

    def test_version_1_accepted_without_floating_fields(self):
        """Old version-1 records (no floating fields) parse correctly."""
        m = model(version=initial_version, **self._base_kwargs())
        self.assertEqual(1, m.version)
        self.assertIsNone(m.floating_address)
        self.assertIsNone(m.inner_address)

    def test_version_2_accepted_with_floating_fields(self):
        """Version-2 records with floating_address and inner_address parse correctly."""
        m = model(
            version=2,
            floating_address='192.0.2.1',
            inner_address='10.0.0.1',
            **self._base_kwargs()
        )
        self.assertEqual(2, m.version)
        self.assertEqual('192.0.2.1', m.floating_address)
        self.assertEqual('10.0.0.1', m.inner_address)

    def test_version_2_accepted_without_floating_fields(self):
        """Version-2 records without floating fields are still valid (optional)."""
        m = model(version=2, **self._base_kwargs())
        self.assertEqual(2, m.version)
        self.assertIsNone(m.floating_address)
        self.assertIsNone(m.inner_address)

    def test_version_3_accepted_with_node_uuid(self):
        """Version-3 records carry the node the operation was targeted at."""
        node_uuid = str(uuid4())
        m = model(
            version=current_version,
            node_uuid=node_uuid,
            **self._base_kwargs()
        )
        self.assertEqual(3, m.version)
        self.assertEqual(node_uuid, str(m.node_uuid))

    def test_version_3_accepted_without_node_uuid(self):
        """node_uuid is optional -- cluster-wide operations have none."""
        m = model(version=current_version, **self._base_kwargs())
        self.assertEqual(3, m.version)
        self.assertIsNone(m.node_uuid)

    def test_version_4_rejected(self):
        """Version 4 is out of range and must be rejected."""
        self.assertRaises(
            ValidationError,
            model,
            version=4,
            **self._base_kwargs()
        )


class CreateAndEnqueueFloatingIpTestCase(base.ShakenFistTestCase):
    """create_and_enqueue threads floating_address and inner_address into metadata."""

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    @mock.patch('time.time', return_value=456.0)
    def test_floating_address_and_inner_address_in_metadata(self, _mock_time):
        """Passing floating/inner address stores them in the persisted metadata."""
        network_uuid = str(uuid4())

        op_type, op_uuid = create_and_enqueue(
            network_uuid=network_uuid,
            tasks=[model_tasks.network_add_floating_ip],
            priority=PRIORITY.user_facing,
            floating_address='203.0.113.5',
            inner_address='10.0.1.42',
        )

        self.assertEqual(ObjectType.NET_OP, op_type)
        metadata = self.mock_mariadb.get_cluster_operation_metadata(op_uuid)
        self.assertEqual('203.0.113.5', metadata['floating_address'])
        self.assertEqual('10.0.1.42', metadata['inner_address'])
        self.assertEqual(['network_add_floating_ip'], metadata['tasks'])
        self.assertEqual(current_version, metadata['version'])

    @mock.patch('time.time', return_value=456.0)
    def test_remove_floating_ip_task_in_metadata(self, _mock_time):
        """network_remove_floating_ip task is stored correctly in metadata."""
        network_uuid = str(uuid4())

        op_type, op_uuid = create_and_enqueue(
            network_uuid=network_uuid,
            tasks=[model_tasks.network_remove_floating_ip],
            priority=PRIORITY.user_facing,
            floating_address='203.0.113.5',
            inner_address='10.0.1.42',
        )

        metadata = self.mock_mariadb.get_cluster_operation_metadata(op_uuid)
        self.assertEqual('203.0.113.5', metadata['floating_address'])
        self.assertEqual('10.0.1.42', metadata['inner_address'])
        self.assertEqual(['network_remove_floating_ip'], metadata['tasks'])

    @mock.patch('time.time', return_value=456.0)
    def test_omitted_floating_fields_default_to_none(self, _mock_time):
        """Callers that don't pass floating fields get None in metadata."""
        network_uuid = str(uuid4())

        _, op_uuid = create_and_enqueue(
            network_uuid=network_uuid,
            tasks=[model_tasks.network_deploy],
            priority=PRIORITY.user_waiting,
        )

        metadata = self.mock_mariadb.get_cluster_operation_metadata(op_uuid)
        self.assertIsNone(metadata['floating_address'])
        self.assertIsNone(metadata['inner_address'])
