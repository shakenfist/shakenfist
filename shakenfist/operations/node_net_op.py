from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.exceptions import NodeLockUnavailable
from shakenfist import mariadb
from shakenfist.schema.operations import node_net_op as schema
from shakenfist.network.bridged_vxlan_network import BridgedVXLanNetwork
from shakenfist.network.network import Network
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.operations.error_report import ErrorReport
from shakenfist.util import exceptions as util_exceptions


LOG, HANDLER = logs.setup(__name__)


class NodeNetOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.node_uuid = op.node_uuid
        self.network_uuid = op.network_uuid


class NoSuchTask(NodeNetOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchNetwork(NodeNetOpException):
    def __init__(self, op):
        super().__init__(op, 'network missing')


class NodeNetOp(BaseClusterOperation):
    object_type = schema.object_type
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__node_uuid = static_values['node_uuid']
        self.__network_uuid = static_values['network_uuid']

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'node_uuid': self.node_uuid,
            'network_uuid': self.network_uuid,
            'tasks': self.tasks
        })

    # Static values
    @property
    def node_uuid(self):
        return self.__node_uuid

    @property
    def network_uuid(self):
        return self.__network_uuid

    # API
    def external_view(self):
        retval = super().external_view()
        retval.update({
            'node_uuid': self.node_uuid,
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

        except NodeLockUnavailable as e:
            # This node's own nodelock daemon did not answer. That is a
            # routine, self-healing condition rather than a failure of
            # the work: a rolling restart drops the daemon for a few
            # seconds, and every long lived daemon rides it out in
            # Daemon.wait_for_nodelock() for exactly that reason. The
            # work item path has no such wait, so erroring here turned a
            # six second blip into a terminally errored cluster
            # operation (issue 5058). Spend the generic retry budget
            # instead, as artifact_fetch_op does for its own transient
            # dependency, and only error out once the budget is gone --
            # by which point the daemon really is not coming back.
            #
            # Retrying from the first task is safe because every task on
            # this operation converges on a desired state, which is the
            # same property RESTARTABLE declares.
            #
            # defer_with_backoff() already records the retry on the
            # operation; this event goes on the network as well, because
            # the operation is hard deleted CLUSTER_OPERATION_RETENTION
            # seconds after it ends and the network is what an operator
            # asking "why was this network late onto that hypervisor?"
            # will actually look at.
            msg = str(e)
            if self.defer_with_backoff(reason=msg):
                n.add_event(
                    EVENT_TYPE_AUDIT,
                    'nodelock daemon unreachable, deferring network task',
                    extra={
                        'task': task.name,
                        'node_uuid': str(self.node_uuid),
                        'message': msg,
                        'defer_count': self.current_defer_count + 1
                    })
                return

            self._error_for_task(task, e)

        except Exception as e:
            self._error_for_task(task, e)

    def _error_for_task(self, task, e):
        util_exceptions.ignore_exception('node_net_op', e)
        mariadb.set_cluster_operation_error(
            str(self.uuid), ErrorReport.from_exception(e))
        self.enter_error_state(
            NodeNetOp.STATE_ERROR,
            f'{task.name} task raised {e.__class__.__name__}: {e}')

    def _network_destroy(self, n):
        BridgedVXLanNetwork(n)._apply_delete_on_hypervisor()

    def _network_apply_create_hypervisor(self, n):
        BridgedVXLanNetwork(n)._apply_create_on_hypervisor()
