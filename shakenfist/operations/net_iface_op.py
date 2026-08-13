from shakenfist_utilities import logs  # noreorder

from shakenfist import mariadb
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist.eventlog import add_event_multi
from shakenfist.schema.operations import net_iface_op as schema
from shakenfist.network.bridged_vxlan_network import BridgedVXLanNetwork
from shakenfist.network.network import Network
from shakenfist.network.interface import NetworkInterface
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.operations.error_report import ErrorReport
from shakenfist.util import exceptions as util_exceptions


LOG, HANDLER = logs.setup(__name__)


class NetIfaceOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.network_uuid = op.network_uuid
        self.interface_uuid = op.interface_uuid


class NoSuchTask(NetIfaceOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchNetwork(NetIfaceOpException):
    def __init__(self, op):
        super().__init__(op, 'network missing')


class NoSuchNetworkInterface(NetIfaceOpException):
    def __init__(self, op):
        super().__init__(op, 'network interface missing')


class InvalidStateForTask(NetIfaceOpException):
    def __init__(self, op):
        super().__init__(op, 'network not in a state which allows this task')


class NetIfaceOp(BaseClusterOperation):
    object_type = schema.object_type
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__network_uuid = static_values['network_uuid']
        self.__interface_uuid = static_values['interface_uuid']

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'network_uuid': self.network_uuid,
            'interface_uuid': self.interface_uuid,
            'tasks': self.tasks
        })

    # Static values
    @property
    def network_uuid(self):
        return self.__network_uuid

    @property
    def interface_uuid(self):
        return self.__interface_uuid

    # API
    def external_view(self):
        retval = super().external_view()
        retval.update({
            'network_uuid': self.network_uuid,
            'interface_uuid': self.interface_uuid
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

        ni = NetworkInterface.from_db(self.interface_uuid)
        if not ni:
            self.log.warning(
                f'Network interface {self.interface_uuid} missing')
            raise NoSuchNetworkInterface(self)

        try:
            self.__getattribute__(f'_{task.name}')(n, ni)
        except Exception as e:
            util_exceptions.ignore_exception('net_iface_op', e)
            mariadb.set_cluster_operation_error(
                str(self.uuid), ErrorReport.from_exception(e))
            self.state = NetIfaceOp.STATE_ERROR

    def _interface_float(self, n, ni):
        if n.is_dead():
            raise InvalidStateForTask(self)

        floating = ni.floating.get('floating_address')
        if not floating:
            # Every enqueue site allocates the floating address (or verifies
            # one exists) before enqueueing this task, so having no address
            # here means a concurrent defloat or interface delete released it
            # between enqueue and execution. The float has been superseded by
            # that later request and skipping it is the correct outcome, not
            # an error.
            self.log.with_fields({
                'instance_uuid': ni.instance_uuid,
                'namespace': n.namespace,
                'interface_state': ni.state.value}).info(
                'Floating address released before interface_float executed; '
                'a defloat or interface delete superseded this float')
            add_event_multi(
                EVENT_TYPE_AUDIT,
                [self, n, ni, ('instance', ni.instance_uuid)],
                'add floating IP superseded by defloat or delete, skipped',
                extra={'inner': ni.ipv4,
                       'interface_state': ni.state.value})
            return

        # Multi-target audit event preserves the correlation that today's
        # Network.add_floating_ip(... affected_objects=[self, n, ni]) emits.
        # The floating-network metadata object is added so the floating-IP
        # ledger sees the assignment too.
        add_event_multi(
            EVENT_TYPE_AUDIT,
            [self, n, ni, ('network', FLOATING_NETWORK_UUID)],
            'add floating IP',
            extra={'floating': floating, 'inner': ni.ipv4})
        BridgedVXLanNetwork(n)._apply_add_floating_ip(floating, ni.ipv4)
