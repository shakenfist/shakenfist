from shakenfist_utilities import logs  # noreorder

from shakenfist.schema.operations import net_op as schema
from shakenfist.exceptions import DeadNetwork
from shakenfist.exceptions import EnsureMeshFailed
from shakenfist.network.network import Network
from shakenfist.network.interface import NetworkInterface
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.util import general as util_general


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
    object_type = schema.object_type.name.lower()
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__network_uuid = static_values['network_uuid']

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

    # API
    def external_view(self):
        retval = super().external_view()
        retval.update({
            'network_uuid': self.network_uuid
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

        except EnsureMeshFailed as e:
            if n.state.value in n.ACTIVE_STATES:
                # This should not happen with an active network
                util_general.ignore_exception('net_op', e)

            self.state = NetOp.STATE_ERROR

        except Exception as e:
            util_general.ignore_exception('net_op', e)
            self.state = NetOp.STATE_ERROR

    def _network_deploy(self, n):
        if n.is_dead():
            raise InvalidStateForTask(self)

        n.create_on_network_node()
        n.ensure_mesh()

    def _network_destroy(self, n):
        if n.networkinterfaces:
            wo = []
            for ni_uuid in n.networkinterfaces:
                ni = NetworkInterface.from_db(ni_uuid)
                if ni:
                    wo.append(ni)
            self.defer(waiting_on=wo)
            return

        try:
            n.delete_on_network_node()
        except DeadNetwork as e:
            self.log.with_fields({
                'exception': e
            }).warning('Attempted destroy on a dead network')

    def _network_update_dnsmasq(self, n):
        n.create_on_network_node()
        n.ensure_mesh()

    def _network_remove_dnsmasq(self, n):
        n.remove_dnsmasq()

    def _network_remove_nat(self, n):
        n.remove_nat()
