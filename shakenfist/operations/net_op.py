from typing import Any

from shakenfist_utilities import logs  # noreorder

from shakenfist import mariadb
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist.eventlog import add_event_multi
from shakenfist.schema.operations import net_op as schema
from shakenfist.exceptions import CreateVXLANInterfaceFailed
from shakenfist.exceptions import EnsureMeshFailed
from shakenfist.network.bridged_vxlan_network import BridgedVXLanNetwork
from shakenfist.network.network import Network
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.operations.error_report import ErrorReport
from shakenfist.util import exceptions as util_exceptions


LOG, HANDLER = logs.setup(__name__)


class NetOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.network_uuid = op.network_uuid


class NoSuchTask(NetOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchNetwork(NetOpException):
    def __init__(self, op):
        super().__init__(op, 'network missing')


class InvalidStateForTask(NetOpException):
    def __init__(self, op):
        super().__init__(op, 'network not in a state which allows this task')


class NetOp(BaseClusterOperation):
    object_type = schema.object_type
    initial_version = schema.initial_version
    current_version = schema.current_version

    # See ``BaseClusterOperation.coalescible_tasks`` and the
    # ``COALESCIBLE_TASKS`` block in
    # ``shakenfist/schema/operations/net_op.py`` for the rationale
    # behind which tasks fold. The key is ``network_uuid`` alone --
    # every NetOp targets exactly one network, and the
    # ``cluster_operations`` table already has an indexed
    # ``network_uuid`` column for the sibling-finding query the
    # dispatcher / enqueue path issues. That single column cannot tell
    # one hypervisor's node-local work apart from another's, which is
    # why ``network_ensure_mesh`` is not in ``COALESCIBLE_TASKS``.
    coalescible_tasks = schema.COALESCIBLE_TASKS
    coalescible_key_columns = schema.COALESCIBLE_KEY_COLUMNS

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__network_uuid = static_values['network_uuid']
        # Read with .get(): a row written before version 3 has no
        # node_uuid key at all. The upgrade steps below insert one, but
        # only for rows which actually travel through upgrade().
        self.__node_uuid = static_values.get('node_uuid')
        self.__floating_address = static_values.get('floating_address')
        self.__inner_address = static_values.get('inner_address')

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'network_uuid': self.network_uuid,
            'node_uuid': self.node_uuid,
            'tasks': self.tasks
        })

    @classmethod
    def _upgrade_step_1_to_2(cls, static_values: dict[str, Any]) -> None:
        # A deliberate no-op. The 1-to-2 bump only added the optional
        # floating_address and inner_address fields, both of which
        # default to None on the model and are read here with .get(),
        # so a version-1 record needs no rewriting to become a
        # version-2 one.
        #
        # The method must exist anyway. BaseObject.upgrade() resolves
        # each step with a bare getattr(self, step) and no default
        # (shakenfist/baseobject.py), so a missing step raises
        # AttributeError rather than the UpgradeException the branch
        # below that getattr is written to raise. Until this method
        # existed, loading a version-1 NetOp row crashed instead of
        # upgrading -- narrow, because cluster operations are hard
        # deleted thirty seconds after going terminal, but real during
        # a rolling upgrade.
        pass

    @classmethod
    def _upgrade_step_2_to_3(cls, static_values: dict[str, Any]) -> None:
        # Version 3 added node_uuid, which records the node a per-node
        # operation was targeted at. An operation written before the
        # bump predates the per-node coalescing key, so there is
        # nothing to recover the value from; None is both the model
        # default and the correct answer.
        static_values['node_uuid'] = None

    # Static values
    @property
    def network_uuid(self):
        return self.__network_uuid

    @property
    def node_uuid(self):
        return self.__node_uuid

    @property
    def floating_address(self):
        return self.__floating_address

    @property
    def inner_address(self):
        return self.__inner_address

    # API
    def external_view(self):
        retval = super().external_view()
        retval.update({
            'network_uuid': self.network_uuid,
            'node_uuid': self.node_uuid,
            'floating_address': self.floating_address,
            'inner_address': self.inner_address,
        })
        return retval

    # Tasks
    def dispatch_task(self, task):
        if task not in schema.model_tasks:
            self.log.warning(f'Task {task} not in {schema.model_tasks}')
            raise NoSuchTask(self, task)

        n = Network.from_db(self.network_uuid)
        if not n:
            self.log.warning(f'Network {self.network_uuid} missing')
            raise NoSuchNetwork(self)

        try:
            self.__getattribute__(f'_{task.name}')(n)

        except CreateVXLANInterfaceFailed as e:
            self.log.warning(
                'Failed to create VXLAN interface, will retry')
            mariadb.set_cluster_operation_error(
                str(self.uuid), ErrorReport.from_exception(e))
            self.state = NetOp.STATE_ERROR

        except EnsureMeshFailed as e:
            # ensure_vxlan_mesh fails when this node has no vxlan interface
            # for the network -- a benign race against concurrent
            # interface create/teardown (``bridge fdb show`` reports
            # ``Cannot find device``). The per-hypervisor ensure_mesh
            # fan-out made it common: an op fanned to a node can run while
            # that node's interface is still coming up or has just drained.
            if n.state.value not in n.ACTIVE_STATES:
                # The network is being torn down; its interface is gone for
                # good. The mesh op is moot -- terminalise it quietly (no
                # traceback) rather than retrying.
                mariadb.set_cluster_operation_error(
                    str(self.uuid), ErrorReport.from_exception(e))
                self.state = NetOp.STATE_ERROR
            elif self.defer_with_backoff(
                    reason='ensure_mesh failed, vxlan interface not ready'):
                # Active network: the interface is most likely still being
                # created. Retry with backoff rather than erroring loudly;
                # any op depending on this one (e.g. instance create) waits
                # through the retries.
                return
            else:
                # Active network, retries exhausted -- treat as a real
                # failure, but log at warning level so the expected race
                # does not trip the post-test stable-log check.
                self.log.warning(
                    'ensure_mesh still failing after retries, erroring op')
                mariadb.set_cluster_operation_error(
                    str(self.uuid), ErrorReport.from_exception(e))
                self.state = NetOp.STATE_ERROR

        except Exception as e:
            util_exceptions.ignore_exception('net_op', e)
            mariadb.set_cluster_operation_error(
                str(self.uuid), ErrorReport.from_exception(e))
            self.state = NetOp.STATE_ERROR

    def _network_deploy(self, n):
        # This task is no longer enqueued by any production code path as of
        # phase 6 of `PLAN-network-facade.md`. Any in-flight op at deploy
        # time gracefully transitions to STATE_ERROR via the dispatcher's
        # outer Exception handler, which persists an ErrorReport. The
        # task-enum value stays in the schema for on-disk record
        # compatibility.
        raise InvalidStateForTask(self)

    def _network_destroy(self, n):
        # Phase 6: superseded by network_apply_delete_network_node (task
        # 12). See _network_deploy above for the rationale.
        raise InvalidStateForTask(self)

    def _network_update_dnsmasq(self, n):
        # Phase 6: this misleadingly-named composite task was superseded
        # by the explicit [network_apply_create_network_node,
        # network_ensure_mesh] task list. See _network_deploy above for
        # the rationale.
        raise InvalidStateForTask(self)

    def _network_apply_create_network_node(self, n):
        BridgedVXLanNetwork(n)._apply_create_on_network_node()

    # Defer schedule for delete ops waiting on network interfaces to drain.
    # Roughly nine minutes total before we give up and error the op out --
    # generous enough to absorb concurrent instance deletes even when the
    # cluster is under load, but bounded so a wedged delete eventually
    # surfaces a failure to the caller rather than spinning forever.
    _DELETE_DRAIN_DELAYS = (15, 30, 60, 60, 60, 60, 60, 60, 60, 60)

    def _network_apply_delete_network_node(self, n):
        # If interfaces are still attached the network is mid-drain
        # (delete_all_networks(clean_wait=True) is the common case --
        # the API enqueues this op while the matching instance deletes
        # are still in flight). Defer rather than tear the network down
        # underneath those interfaces; defer_with_backoff re-enqueues
        # the op and returns control to the worker.
        if n.networkinterfaces:
            if not self.defer_with_backoff(
                    delays=self._DELETE_DRAIN_DELAYS,
                    reason='waiting for network interfaces to drain'):
                # Retry budget exhausted -- the worker's outer Exception
                # handler will persist an ErrorReport and set STATE_ERROR.
                raise InvalidStateForTask(self)
            return
        BridgedVXLanNetwork(n)._apply_delete_on_network_node()

    def _network_remove_dnsmasq(self, n):
        BridgedVXLanNetwork(n)._apply_remove_dnsmasq()

    def _network_apply_update_dnsmasq(self, n):
        BridgedVXLanNetwork(n)._apply_update_dnsmasq()

    def _network_apply_remove_dnsmasq(self, n):
        BridgedVXLanNetwork(n)._apply_remove_dnsmasq()

    def _network_remove_nat(self, n):
        BridgedVXLanNetwork(n)._apply_remove_nat()

    def _network_ensure_mesh(self, n):
        BridgedVXLanNetwork(n)._apply_ensure_mesh()

    def _network_add_floating_ip(self, n):
        # Multi-target audit event preserves today's correlation between
        # the wrapped network and the floating-network metadata object. The
        # caller-side affected_objects (interface, instance) are emitted by
        # net_iface_op._interface_float on the dispatch path it owns.
        add_event_multi(
            EVENT_TYPE_AUDIT,
            [n, ('network', FLOATING_NETWORK_UUID)],
            'add floating IP',
            extra={
                'floating': self.floating_address,
                'inner': self.inner_address,
            })
        BridgedVXLanNetwork(n)._apply_add_floating_ip(
            self.floating_address, self.inner_address)

    def _network_remove_floating_ip(self, n):
        add_event_multi(
            EVENT_TYPE_AUDIT,
            [n, ('network', FLOATING_NETWORK_UUID)],
            'remove floating IP',
            extra={
                'floating': self.floating_address,
                'inner': self.inner_address,
            })
        BridgedVXLanNetwork(n)._apply_remove_floating_ip(
            self.floating_address, self.inner_address)
