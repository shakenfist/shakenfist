from shakenfist_utilities import logs  # noreorder

from shakenfist import mariadb
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist.eventlog import add_event_multi
from shakenfist.schema.operations import net_iface_ip_op as schema
from shakenfist.network.bridged_vxlan_network import BridgedVXLanNetwork
from shakenfist.network.network import Network
from shakenfist.network.interface import NetworkInterface
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.operations.error_report import ErrorReport
from shakenfist.util import exceptions as util_exceptions


LOG, HANDLER = logs.setup(__name__)


class NetIfaceIPOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.network_uuid = op.network_uuid
        self.interface_uuid = op.interface_uuid


class NoSuchTask(NetIfaceIPOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchNetwork(NetIfaceIPOpException):
    def __init__(self, op):
        super().__init__(op, 'network missing')


class NoSuchNetworkInterface(NetIfaceIPOpException):
    def __init__(self, op):
        super().__init__(op, 'network interface missing')


class InvalidStateForTask(NetIfaceIPOpException):
    def __init__(self, op):
        super().__init__(op, 'network not in a state which allows this task')


class NetIfaceIPOp(BaseClusterOperation):
    object_type = schema.object_type
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__network_uuid = static_values['network_uuid']
        self.__interface_uuid = static_values['interface_uuid']
        self.__ip = static_values['ip']

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'network_uuid': self.network_uuid,
            'interface_uuid': self.interface_uuid,
            'ip': self.ip,
            'tasks': self.tasks
        })

    # Static values
    @property
    def network_uuid(self):
        return self.__network_uuid

    @property
    def interface_uuid(self):
        return self.__interface_uuid

    @property
    def ip(self):
        return self.__ip

    # API
    def external_view(self):
        retval = super().external_view()
        retval.update({
            'network_uuid': self.network_uuid,
            'interface_uuid': self.interface_uuid,
            'ip': self.ip
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
        if not n:
            self.log.warning(
                f'Network interface {self.interface_uuid} missing')
            raise NoSuchNetworkInterface(self)

        try:
            self.__getattribute__(f'_{task.name}')(n, ni)
        except Exception as e:
            util_exceptions.ignore_exception('net_iface_ip_op', e)
            mariadb.set_cluster_operation_error(
                str(self.uuid), ErrorReport.from_exception(e))
            self.state = NetIfaceIPOp.STATE_ERROR

    def _interface_defloat(self, n, ni):
        # Multi-target audit event preserves the correlation that today's
        # Network.remove_floating_ip(... affected_objects=[ni, ('instance',
        # ni.instance_uuid)]) emits, plus the wrapped network and the
        # floating-network metadata object.
        add_event_multi(
            EVENT_TYPE_AUDIT,
            [ni, ('instance', ni.instance_uuid), ('network', str(n.uuid)),
             ('network', FLOATING_NETWORK_UUID)],
            'remove floating IP',
            extra={'floating': self.ip, 'inner': ni.ipv4})
        BridgedVXLanNetwork(n)._apply_remove_floating_ip(self.ip, ni.ipv4)
