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

    def test_enqueue_side_dedup_refuses_a_less_urgent_pending_op(self):
        # The end to end version of the priority filter, through
        # MockMariaDB rather than against the SQL. A background repair
        # is already queued for this network and task -- which is what
        # daemons/network/maintain.py enqueues whenever is_mesh_okay()
        # reports drift -- and then an interactive caller asks for the
        # same work at user_facing. Reusing the background op would
        # hand the interactive caller a uuid on the background lane to
        # block raise_for_error() against.
        u1 = str(uuid4())

        _, background_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_apply_update_dnsmasq],
            priority=PRIORITY.background,
        )
        _, interactive_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_apply_update_dnsmasq],
            priority=PRIORITY.user_facing_high_io,
        )

        self.assertNotEqual(background_uuid, interactive_uuid)
        self.assertEqual(
            2,
            sum(
                1 for row
                in self.mock_mariadb.cluster_operations_store.values()
                if row.get('operation_type') == 'net_op'
                and row.get('network_uuid') == u1
            ))

    def test_enqueue_side_dedup_reuses_a_more_urgent_pending_op(self):
        # The other direction is fine and should still coalesce: the
        # background caller inherits work which runs sooner than it
        # asked for, which costs it nothing.
        u1 = str(uuid4())

        _, interactive_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_apply_update_dnsmasq],
            priority=PRIORITY.user_facing_high_io,
        )
        _, background_uuid = create_and_enqueue(
            network_uuid=u1,
            tasks=[model_tasks.network_apply_update_dnsmasq],
            priority=PRIORITY.background,
        )

        self.assertEqual(interactive_uuid, background_uuid)

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

    # The tests above exercise pydantic validation of an explicit
    # version. These exercise DatabaseBackedObject.upgrade(), which is
    # a different code path and the one a rolling upgrade actually
    # takes: an older sf-net hands NetOp a static_values dict at the
    # version its build wrote, and every intervening
    # _upgrade_step_N_to_N+1 has to exist and run. upgrade() resolves
    # each step with a bare getattr(self, step) and no default, so a
    # missing step raises AttributeError rather than the documented
    # UpgradeException -- which is survey finding 8 of the phase 11
    # plan, and why _upgrade_step_1_to_2 exists at all despite doing
    # nothing.
    def _static_values_at_version(self, version):
        # Deliberately built by *removing* the fields each version
        # added rather than by listing them, so a field added to the
        # model in a later version without a matching upgrade step
        # shows up here.
        static_values = {
            'uuid': str(uuid4()),
            'network_uuid': str(uuid4()),
            'priority': 'user_facing',
            'request_id': None,
            'depends_on': None,
            'runs_after': None,
            'tasks': ['network_apply_update_dnsmasq'],
            'version': version,
        }
        if version >= 2:
            static_values['floating_address'] = None
            static_values['inner_address'] = None
        if version >= 3:
            static_values['node_uuid'] = None
        return static_values

    def test_upgrade_from_version_1(self):
        """A version-1 row loads, reaching version 3 with a null node."""
        static_values = self._static_values_at_version(1)
        op = NetOp(static_values)

        self.assertEqual(current_version, static_values['version'])
        self.assertIsNone(op.node_uuid)
        self.assertIsNone(op.floating_address)
        self.assertIsNone(op.inner_address)
        # The key a cluster-wide operation folds on. A null node_uuid
        # binds IS NULL, so an upgraded row folds only the other
        # cluster-wide operations on its network -- exactly as a row
        # written by this build does.
        self.assertEqual(
            [('network_uuid', str(op.network_uuid)), ('node_uuid', None)],
            [(c, None if getattr(op, c) is None else str(getattr(op, c)))
             for c in NetOp.coalescible_key_columns])

    def test_upgrade_from_version_2(self):
        """A version-2 row loads, gaining only the null node_uuid."""
        static_values = self._static_values_at_version(2)
        op = NetOp(static_values)

        self.assertEqual(current_version, static_values['version'])
        self.assertIn('node_uuid', static_values)
        self.assertIsNone(static_values['node_uuid'])
        self.assertIsNone(op.node_uuid)

    def test_every_upgrade_step_between_the_versions_exists(self):
        """Each step is a real callable, not just a name in the source.

        The sweep in test_upgrade_step_sweep.py asks whether a step is
        defined anywhere in the two modules. This asks the narrower
        question upgrade() asks: does getattr(op, step) resolve on the
        object, and does calling it with a static_values dict work.
        """
        op = NetOp(self._static_values_at_version(current_version))
        for v in range(initial_version, current_version):
            step = '_upgrade_step_%d_to_%d' % (v, v + 1)
            step_func = getattr(op, step, None)
            self.assertIsNotNone(
                step_func, f'{step} does not resolve on a NetOp')
            self.assertTrue(
                callable(step_func), f'{step} is not callable')
            step_func({'uuid': str(uuid4()), 'version': v})


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
