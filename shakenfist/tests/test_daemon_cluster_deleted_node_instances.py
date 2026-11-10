# Copyright 2026 Michael Still and contributors
"""Tests for sf-cluster deleting the instances on a deleted node.

The deleted-node cleanup swept only ``healthy_instances_on_node()``, so
an instance in an error state, or in delete-wait or delete-wait-error,
was left behind with its placement, interfaces and row. Nothing else
would ever move it on: its own delete is enqueued for the node which no
longer exists, and the cleaner's delete-wait escalation runs on that
same node. Since issue 4847 an instance in any state but deleted also
blocks its namespace's delete, so such an instance made its namespace
undeletable with no operator recovery path (#4868).
"""

from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.instance import Instance
from shakenfist.node import Node
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class DeletedNodeInstanceSweepTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=2)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('unittest', 'key1', 'secret')

        self.dead_node = Node.from_db(
            self.mock_mariadb.node_uuids['node1_net'])
        # Smashed in rather than walked to: a node reaches deleted
        # through states a fresh test node has not been through.
        self.dead_node._state_update(Node.STATE_DELETED,
                                     skip_transition_validation=True)

    def _sweep(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.pet_watchdog = mock.MagicMock()
        m._delete_instances_on_deleted_node(self.dead_node)
        return m

    def _place(self, state, node='node1_net'):
        return self.mock_mariadb.create_instance(
            name='i-' + state, set_state=state, place_on_node=node)

    def test_undeleted_states_is_every_state_but_deleted(self):
        # The set the sweep selects on. Derived from the state machine
        # rather than written out, because a state left out of it is an
        # instance which can never be deleted once its node is gone --
        # the #4868 defect itself, which was HEALTHY_STATES being used
        # here.
        every_state = set(Instance.state_targets) | {dbo.STATE_HARD_DELETED}
        every_state.discard(None)
        self.assertEqual(
            every_state - {dbo.STATE_DELETED, dbo.STATE_HARD_DELETED},
            set(Instance.UNDELETED_STATES))

    def test_every_undeleted_state_reaches_deleted(self):
        # One pass, not two: the node row is hard deleted on the deleted
        # object sweep's own schedule, after which this code never runs
        # for it again. An instance left in a *-error state, or in
        # error, is one left there for good.
        instances = {
            state: self._place(state)
            for state in sorted(Instance.UNDELETED_STATES)
        }

        self._sweep()

        for state, inst in instances.items():
            self.assertEqual(
                dbo.STATE_DELETED, Instance.from_db(inst.uuid).state.value,
                f'an instance in {state} did not reach deleted')

    def test_the_sweep_releases_the_placement(self):
        # The INSTANCE_LOCATION rows are the sole record of placement,
        # and the capacity they claim is released with them. An errored
        # instance kept both.
        inst = self._place(Instance.STATE_CREATED_ERROR)
        self.assertEqual([str(inst.uuid)], self.dead_node.instances)

        self._sweep()

        self.assertEqual([], self.dead_node.instances)

    def test_another_nodes_instance_is_left_alone(self):
        inst = self._place(Instance.STATE_CREATED_ERROR, node='node2')

        self._sweep()

        self.assertEqual(Instance.STATE_CREATED_ERROR,
                         Instance.from_db(inst.uuid).state.value)

    def test_a_deleted_instance_is_not_swept_again(self):
        # It has released everything it held, and the deleted object
        # sweep hard deletes it by state from here.
        inst = self._place(dbo.STATE_DELETED)

        with mock.patch.object(Instance, 'add_event') as ae:
            self._sweep()

        self.assertEqual([], ae.call_args_list)
        self.assertEqual(dbo.STATE_DELETED,
                         Instance.from_db(inst.uuid).state.value)

    def test_the_sweep_records_why_on_both_objects(self):
        inst = self._place(Instance.STATE_CREATED_ERROR)

        with mock.patch.object(Instance, 'add_event') as inst_event:
            with mock.patch.object(Node, 'add_event') as node_event:
                self._sweep()

        message = 'deleting instance as hosting node as been deleted'
        self.assertIn(message, [c.args[1] for c in inst_event.call_args_list])
        node_calls = [c for c in node_event.call_args_list
                      if c.args[1] == message]
        self.assertEqual(1, len(node_calls))
        self.assertEqual({'instance_uuid': inst.uuid},
                         node_calls[0].kwargs['extra'])

    def test_the_interfaces_go_too(self):
        # An errored instance's interfaces were left behind with it,
        # holding their IPAM reservations and floating addresses.
        self._place(Instance.STATE_DELETE_WAIT_ERROR)
        ni = mock.MagicMock()

        with mock.patch.object(cluster_main.interface,
                               'interfaces_for_instance',
                               return_value=[ni]):
            self._sweep()

        ni.delete.assert_called_once_with()

    def test_an_empty_node_is_not_an_error(self):
        self._sweep()
