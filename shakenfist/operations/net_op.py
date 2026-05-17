from shakenfist_utilities import logs  # noreorder

from shakenfist import mariadb
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist.eventlog import add_event_multi
from shakenfist.schema.operations import net_op as schema
from shakenfist.exceptions import CreateVXLANInterfaceFailed
from shakenfist.exceptions import DeadNetwork
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

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__network_uuid = static_values['network_uuid']
        self.__floating_address = static_values.get('floating_address')
        self.__inner_address = static_values.get('inner_address')

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'network_uuid': self.network_uuid,
            'tasks': self.tasks
        })

    # Static values
    @property
    def network_uuid(self):
        return self.__network_uuid

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
            if n.state.value in n.ACTIVE_STATES:
                # This should not happen with an active network; log but
                # still record the error report before entering STATE_ERROR.
                util_exceptions.ignore_exception('net_op', e)

            mariadb.set_cluster_operation_error(
                str(self.uuid), ErrorReport.from_exception(e))
            self.state = NetOp.STATE_ERROR

        except Exception as e:
            util_exceptions.ignore_exception('net_op', e)
            mariadb.set_cluster_operation_error(
                str(self.uuid), ErrorReport.from_exception(e))
            self.state = NetOp.STATE_ERROR

    def _network_deploy(self, n):
        if n.is_dead():
            raise InvalidStateForTask(self)

        # Route through BridgedVXLanNetwork to avoid re-entrancy: after step 5d
        # Network.create_on_network_node() / ensure_mesh() will enqueue a NetOp,
        # which would deadlock the net-worker if called from within a handler.
        bvn = BridgedVXLanNetwork(n)
        bvn._apply_create_on_network_node()
        bvn._apply_ensure_mesh()

    def _network_destroy(self, n):
        nis = n.networkinterfaces
        if nis:
            self.defer(waiting_on=nis)
            return

        try:
            BridgedVXLanNetwork(n)._apply_delete_on_network_node()
        except DeadNetwork as e:
            self.log.with_fields({
                'exception': e
            }).warning('Attempted destroy on a dead network')

    def _network_update_dnsmasq(self, n):
        # Same re-entrancy guard as _network_deploy above.
        bvn = BridgedVXLanNetwork(n)
        bvn._apply_create_on_network_node()
        bvn._apply_ensure_mesh()

    def _network_apply_create_network_node(self, n):
        BridgedVXLanNetwork(n)._apply_create_on_network_node()

    def _network_apply_delete_network_node(self, n):
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
