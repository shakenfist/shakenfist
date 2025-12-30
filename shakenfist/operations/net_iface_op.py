from shakenfist_utilities import logs  # noreorder

from shakenfist.schema.operations import net_iface_op as schema
from shakenfist.network.network import Network
from shakenfist.network.interface import NetworkInterface
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
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


class NoAllocatedFloatingAddress(NetIfaceOpException):
    def __init__(self, op):
        super().__init__(op, 'interface has not floating address assigned')


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
        if not n:
            self.log.warning(
                f'Network interface {self.interface_uuid} missing')
            raise NoSuchNetworkInterface(self)

        try:
            self.__getattribute__(f'_{task.name}')(n, ni)
        except Exception as e:
            util_exceptions.ignore_exception('net_iface_op', e)
            self.state = NetIfaceOp.STATE_ERROR

    def _interface_float(self, n, ni):
        if n.is_dead():
            raise InvalidStateForTask()

        floating = ni.floating.get('floating_address')
        if not floating:
            self.log.warning(
                'Not floating an interface with no floating address')
            raise NoAllocatedFloatingAddress(self)

        n.add_floating_ip(floating, ni.ipv4, [self, n, ni])
