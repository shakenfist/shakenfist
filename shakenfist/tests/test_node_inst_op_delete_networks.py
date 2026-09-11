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
"""

from unittest import mock
from uuid import uuid4

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


def _make_network(uuid):
    n = mock.MagicMock()
    n.uuid = uuid
    n.state = State(value='created', update_time=1.0)
    return n


class InstanceDeleteNetworkCleanupTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

        self.network_uuids = [str(uuid4()), str(uuid4())]
        self.networks = {u: _make_network(u) for u in self.network_uuids}

        # Two interfaces, one per network, so instance_networks has two
        # entries in a deterministic order.
        interfaces = []
        for u in self.network_uuids:
            ni = mock.MagicMock()
            ni.network_uuid = u
            interfaces.append(ni)

        for target, kwargs in [
                ('shakenfist.operations.node_inst_op.'
                 'interfaces_for_instance',
                 {'side_effect': lambda inst: iter(interfaces)}),
                ('shakenfist.operations.node_inst_op.Instances',
                 {'return_value': []}),
                ('shakenfist.operations.node_inst_op.Network.from_db',
                 {'side_effect': lambda u: self.networks.get(u)}),
        ]:
            patcher = mock.patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

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
