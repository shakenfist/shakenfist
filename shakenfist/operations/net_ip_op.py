from shakenfist_utilities import logs  # noreorder

from shakenfist import mariadb
from shakenfist.schema.operations import net_ip_op as schema
from shakenfist.network.bridged_vxlan_network import BridgedVXLanNetwork
from shakenfist.network.network import Network
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.operations.error_report import ErrorReport
from shakenfist.util import exceptions as util_exceptions


LOG, HANDLER = logs.setup(__name__)


class NetIPOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.network_uuid = op.network_uuid
        self.ip = op.ip


class NoSuchTask(NetIPOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchNetwork(NetIPOpException):
    def __init__(self, op):
        super().__init__(op, 'network missing')


class InvalidStateForTask(NetIPOpException):
    def __init__(self, op):
        super().__init__(op, 'network not in a state which allows this task')


class NetIPOp(BaseClusterOperation):
    object_type = schema.object_type
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__network_uuid = static_values['network_uuid']
        self.__ip = static_values['ip']

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'network_uuid': self.network_uuid,
            'ip': self.ip,
            'tasks': self.tasks
        })

    # Static values
    @property
    def network_uuid(self):
        return self.__network_uuid

    @property
    def ip(self):
        return self.__ip

    # API
    def external_view(self):
        retval = super().external_view()
        retval.update({
            'network_uuid': self.network_uuid,
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

        try:
            self.__getattribute__(f'_{task.name}')(n)
        except Exception as e:
            util_exceptions.ignore_exception('net_ip_op', e)
            mariadb.set_cluster_operation_error(
                str(self.uuid), ErrorReport.from_exception(e))
            self.state = NetIPOp.STATE_ERROR

    def _route_address(self, n):
        if n.is_dead():
            raise InvalidStateForTask(self)

        BridgedVXLanNetwork(n)._apply_route_address(self.ip)

    def _unroute_address(self, n):
        BridgedVXLanNetwork(n)._apply_unroute_address(self.ip)
