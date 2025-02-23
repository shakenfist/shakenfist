from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.etcd_schema.operations import net_macaddr_ip_op as schema
from shakenfist.network.network import Network
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.util import general as util_general


LOG, HANDLER = logs.setup(__name__)


class NetMacaddrIPOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.network_uuid = op.network_uuid
        self.mac_address = op.mac_address
        self.ip = op.ip


class NoSuchTask(NetMacaddrIPOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchNetwork(NetMacaddrIPOpException):
    def __init__(self, op):
        super().__init__(op, 'network missing')


class InvalidStateForTask(NetMacaddrIPOpException):
    def __init__(self, op):
        super().__init__(op, 'network not in a state which allows this task')


class NetMacaddrIPOp(BaseClusterOperation):
    object_type = schema.object_type.name.lower()
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values)

        self.__network_uuid = static_values['network_uuid']
        self.__mac_address = static_values['mac_address']
        self.__ip = static_values['ip']

        # Convert tasks names back into enum entries
        self.__tasks = []
        for task_name in static_values['tasks']:
            try:
                self.__tasks.append(schema.model_tasks[task_name])
            except KeyError as e:
                self.state = self.STATE_ERROR
                self.add_event(
                    EVENT_TYPE_AUDIT, 'unknown task {task_name}: {e}')
                raise e

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'network_uuid': self.network_uuid,
            'mac_address': self.mac_address,
            'ip': self.ip,
            'tasks': self.tasks
        })

    # Static values
    @property
    def network_uuid(self):
        return self.__network_uuid

    @property
    def mac_address(self):
        return self.__mac_address

    @property
    def ip(self):
        return self.__ip

    @property
    def tasks(self):
        return self.__tasks

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
            util_general.ignore_exception('net_macaddr_ip_op', e)
            self.state = NetMacaddrIPOp.STATE_ERROR

    def _remove_dhcp_lease(self, n):
        if n.is_dead():
            raise InvalidStateForTask()

        n.remove_dhcp_lease(self.ip, self.mac_address)
