# Copyright 2019 Michael Still and contributors
"""Regression tests for network cleanup in ``NodeInstOp._instance_delete``.

Concurrent instance deletes on one hypervisor used to race tearing
down the same network, and the loser's ProcessExecutionError
propagated out of the per-network cleanup loop -- so for a
multi-homed instance every network after the one that raised got no
hypervisor-local teardown at all, leaving its bridge and vxlan
devices behind as strays (issue 4165). The loop must record a
failure, continue with the remaining networks, and only then
re-raise so the operation still lands in the error state.

The second family here is about *whether* a network should be torn
off this hypervisor at all. That used to be sampled once before
``inst.delete()`` and then trusted for the rest of the operation, so
an interface attached in between lost its bridge (issue 4958). The
question is now asked at the point of decision, of the records and
then of the host.
"""

from unittest import mock
from uuid import uuid4

from shakenfist.exceptions import InstancePowerOffFailed
from shakenfist.exceptions import ProcessExecutionError
from shakenfist.operations.node_inst_op import NodeInstOp
from shakenfist.schema.object_state import State
from shakenfist.schema.operations.node_inst_op import create_and_enqueue
from shakenfist.schema.operations.node_inst_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class FakeInstance:
    """An instance double for driving _instance_delete directly."""

    def __init__(self, state_value='created'):
        self._state_value = state_value
        self.uuid = str(uuid4())
        self.power_off = mock.Mock()
        self.delete = mock.Mock()
        self.add_event = mock.Mock()
        lock = mock.MagicMock()
        lock.__enter__ = mock.Mock(return_value=None)
        lock.__exit__ = mock.Mock(return_value=False)
        self.get_lock = mock.Mock(return_value=lock)

    @property
    def state(self):
        return State(value=self._state_value, update_time=1.0)

    @state.setter
    def state(self, new_value):
        self._state_value = new_value


def _make_network(uuid, vxid=0x123):
    n = mock.MagicMock()
    n.uuid = uuid
    n.vxid = vxid
    n.state = State(value='created', update_time=1.0)
    return n


class _RecordingInstanceList(list):
    """An always empty instance list which records each time it is read."""

    def __init__(self, calls):
        super().__init__()
        self._calls = calls

    def __iter__(self):
        self._calls.append('membership')
        return super().__iter__()


def _make_instance_using(network_uuid):
    """An unrelated instance on this node with an interface on a network."""
    i = mock.MagicMock()
    i.uuid = str(uuid4())
    ni = mock.MagicMock()
    ni.network_uuid = network_uuid
    i.interfaces = [ni]
    return i


class InstanceDeleteNetworkCleanupTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

        self.network_uuids = [str(uuid4()), str(uuid4())]
        self.networks = {u: _make_network(u) for u in self.network_uuids}

        # Two interfaces, one per network, so instance_networks has two
        # entries in a deterministic order.
        self.interfaces = []
        for u in self.network_uuids:
            ni = mock.MagicMock()
            ni.network_uuid = u
            self.interfaces.append(ni)

        # What the node's other instances are, as the point of decision
        # check reads them. A test which simulates an attach replaces
        # this list, or appends to it from a side effect.
        self.other_instances = []

        for target, kwargs in [
                ('shakenfist.operations.node_inst_op.'
                 'interfaces_for_instance',
                 {'side_effect': lambda inst: iter(self.interfaces)}),
                ('shakenfist.operations.node_inst_op.Instances',
                 {'side_effect': lambda *a, **kw: list(self.other_instances)}),
                ('shakenfist.operations.node_inst_op.Network.from_db',
                 {'side_effect': lambda u: self.networks.get(u)}),
        ]:
            patcher = mock.patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

        # The host side veto. Nothing we did not put there is enslaved to
        # the bridge unless a test says otherwise.
        self.mock_foreign = mock.patch(
            'shakenfist.operations.node_inst_op.util_network.'
            'foreign_bridge_members', return_value=[]).start()

        config = mock.MagicMock()
        config.NODE_IS_NETWORK_NODE = False
        patcher = mock.patch(
            'shakenfist.operations.node_inst_op.config', config)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.mock_bvn = mock.patch(
            'shakenfist.operations.node_inst_op.BridgedVXLanNetwork').start()
        self.addCleanup(mock.patch.stopall)

    def _make_op(self):
        _, op_uuid = create_and_enqueue(
            node_uuid=str(uuid4()),
            instance_uuid=str(uuid4()),
            tasks=[model_tasks.instance_delete],
            priority=PRIORITY.user_facing,
        )
        op = NodeInstOp.from_db(op_uuid)
        self.assertIsNotNone(op)
        return op

    def test_all_networks_torn_down(self):
        op = self._make_op()
        op._instance_delete(FakeInstance())

        # Both networks get a dnsmasq prune and a hypervisor teardown.
        for n in self.networks.values():
            n.update_dnsmasq.assert_called_once_with()
        self.assertEqual(2, self.mock_bvn.call_count)
        self.assertEqual(
            2, self.mock_bvn.return_value.
            _apply_delete_on_hypervisor.call_count)

    def test_teardown_failure_does_not_abandon_other_networks(self):
        # The first network's teardown raises; the second network must
        # still be cleaned up, and the failure re-raised afterwards so
        # the operation lands in the error state.
        failure = ProcessExecutionError(
            '', 'Cannot find device "br-vxlan-000123"\n', 1,
            'ip link delete br-vxlan-000123')
        self.mock_bvn.return_value._apply_delete_on_hypervisor.\
            side_effect = [failure, None]

        op = self._make_op()
        with mock.patch(
                'shakenfist.operations.node_inst_op.util_exceptions.'
                'ignore_exception'):
            self.assertRaises(
                ProcessExecutionError, op._instance_delete, FakeInstance())

        # Both networks were still processed.
        for n in self.networks.values():
            n.update_dnsmasq.assert_called_once_with()
        self.assertEqual(
            2, self.mock_bvn.return_value.
            _apply_delete_on_hypervisor.call_count)

    def test_dnsmasq_enqueue_failure_does_not_abandon_other_networks(self):
        # A failure enqueueing the dnsmasq prune for one network must
        # not abandon the other network's cleanup either.
        first = self.networks[self.network_uuids[0]]
        first.update_dnsmasq.side_effect = RuntimeError('enqueue failed')

        op = self._make_op()
        with mock.patch(
                'shakenfist.operations.node_inst_op.util_exceptions.'
                'ignore_exception'):
            self.assertRaises(
                RuntimeError, op._instance_delete, FakeInstance())

        second = self.networks[self.network_uuids[1]]
        second.update_dnsmasq.assert_called_once_with()
        self.mock_bvn.return_value.\
            _apply_delete_on_hypervisor.assert_called_once()

    def _delete_whose_power_off_fails(self, state_value):
        """Run a delete whose power off leaves the domain running."""
        inst = FakeInstance(state_value)
        inst.power_off.side_effect = InstancePowerOffFailed(
            'internal error: something else entirely')

        op = self._make_op()
        op._instance_delete(inst)
        return inst

    def _assert_deferred(self, inst):
        """Nothing beyond the power off happened: no interface, network or
        disk was removed, and placement was not released (delete() does
        both), so the cleaner's re-enqueued delete finds them all."""
        inst.power_off.assert_called_once_with()
        inst.delete.assert_not_called()
        for ni in self.interfaces:
            ni.delete.assert_not_called()
        for n in self.networks.values():
            n.update_dnsmasq.assert_not_called()
        self.mock_bvn.assert_not_called()
        inst.add_event.assert_called_once_with(
            'audit', 'delete deferred, domain still running',
            extra={'message': 'internal error: something else entirely'})

    def test_failed_power_off_defers_the_delete(self):
        # power_off() raises when destroy() leaves the domain running. The
        # delete then stops, without raising, leaving the instance in
        # delete-wait for the cleaner (issue #4486). Raising would move it
        # to delete-wait-error, which the cleaner does not escalate.
        inst = self._delete_whose_power_off_fails('created')

        self._assert_deferred(inst)
        self.assertEqual('delete-wait', inst.state.value)

    def test_failed_power_off_moves_an_errored_instance_to_delete_wait(self):
        # A delete from an error state skips delete-wait, but the cleaner
        # only escalates delete-wait instances.
        inst = self._delete_whose_power_off_fails('created-error')

        self._assert_deferred(inst)
        self.assertEqual('delete-wait', inst.state.value)

    def test_failed_power_off_leaves_delete_wait_error_alone(self):
        # delete-wait-error has no transition to delete-wait.
        inst = self._delete_whose_power_off_fails('delete-wait-error')

        self._assert_deferred(inst)
        self.assertEqual('delete-wait-error', inst.state.value)

    def test_network_used_by_another_instance_is_left_alone(self):
        # The second network is also used by another instance on this
        # node, so only the first one is torn off the hypervisor.
        self.other_instances = [
            _make_instance_using(self.network_uuids[1])]

        op = self._make_op()
        op._instance_delete(FakeInstance())

        # Both networks still get their dnsmasq prune: the lease of the
        # instance which just went away is stale either way.
        for n in self.networks.values():
            n.update_dnsmasq.assert_called_once_with()
        self.mock_bvn.assert_called_once_with(
            self.networks[self.network_uuids[0]])
        self.mock_bvn.return_value.\
            _apply_delete_on_hypervisor.assert_called_once()

    def test_the_instance_being_deleted_does_not_protect_its_own_network(self):
        # The instance under delete is excluded explicitly, so a delete
        # whose own record is still readable as active -- a deleted state
        # which has not landed yet, say -- still cleans up.
        inst = FakeInstance()
        self_entry = mock.MagicMock()
        self_entry.uuid = inst.uuid
        self_entry.interfaces = self.interfaces
        self.other_instances = [self_entry]

        op = self._make_op()
        op._instance_delete(inst)

        self.assertEqual(
            2, self.mock_bvn.return_value.
            _apply_delete_on_hypervisor.call_count)

    def test_interface_attached_during_the_delete_keeps_its_bridge(self):
        # The issue 4958 regression. An interface hot plug, or a create
        # landing on this node, writes its NetworkInterface row while
        # this delete is in progress. The membership question used to be
        # sampled before inst.delete() and the per-network dnsmasq
        # enqueues, so the attach was invisible to it and the bridge was
        # removed from underneath the new, running, interface.
        inst = FakeInstance()
        inst.delete.side_effect = lambda: self.other_instances.append(
            _make_instance_using(self.network_uuids[0]))

        op = self._make_op()
        op._instance_delete(inst)

        self.mock_bvn.assert_called_once_with(
            self.networks[self.network_uuids[1]])
        self.mock_bvn.return_value.\
            _apply_delete_on_hypervisor.assert_called_once()

    def test_membership_is_read_after_the_delete(self):
        # The ordering the fix depends on, pinned directly: no answer
        # read before inst.delete() can be trusted by the loop which
        # runs after it.
        calls = []
        inst = FakeInstance()
        inst.delete.side_effect = lambda: calls.append('delete')
        self.other_instances = _RecordingInstanceList(calls)

        op = self._make_op()
        op._instance_delete(inst)

        self.assertEqual(
            ['delete', 'membership', 'membership'], calls)

    def test_enslaved_guest_tap_vetoes_the_teardown(self):
        # The records say nobody is using the network and the host says
        # otherwise. A tap on the bridge is proof that a domain is
        # attached right now, so the host wins.
        self.mock_foreign.side_effect = (
            lambda vxid: ['vnet7'] if vxid == 0x456 else [])
        self.networks[self.network_uuids[1]].vxid = 0x456

        op = self._make_op()
        op._instance_delete(FakeInstance())

        self.mock_bvn.assert_called_once_with(
            self.networks[self.network_uuids[0]])
        self.mock_bvn.return_value.\
            _apply_delete_on_hypervisor.assert_called_once()

    def test_an_unanswerable_host_vetoes_the_teardown(self):
        # "Could not ask the host" is not "the host says nobody is using
        # it". A leaked bridge is reaped by a later maintain pass; a
        # bridge taken from a live domain is an outage.
        self.mock_foreign.side_effect = Exception('ip is having a bad day')

        op = self._make_op()
        with mock.patch(
                'shakenfist.operations.node_inst_op.util_exceptions.'
                'ignore_exception'):
            op._instance_delete(FakeInstance())

        self.mock_bvn.assert_not_called()

    def test_the_network_node_asks_nothing_and_tears_nothing_down(self):
        # The network node always carries every network, so it neither
        # asks the question nor removes the plumbing.
        config = mock.MagicMock()
        config.NODE_IS_NETWORK_NODE = True
        with mock.patch(
                'shakenfist.operations.node_inst_op.config', config):
            op = self._make_op()
            op._instance_delete(FakeInstance())

        self.mock_bvn.assert_not_called()
        self.mock_foreign.assert_not_called()
