from shakenfist_utilities import logs  # noreorder

from shakenfist import mariadb
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

        n.create_on_network_node()
        n.ensure_mesh()

    def _network_destroy(self, n):
        nis = n.networkinterfaces
        if nis:
            self.defer(waiting_on=nis)
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

    def _network_ensure_mesh(self, n):
        BridgedVXLanNetwork(n)._apply_ensure_mesh()
