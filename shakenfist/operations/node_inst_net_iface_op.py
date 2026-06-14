from shakenfist_utilities import logs  # noreorder

from shakenfist.schema.operations.net_iface_op \
    import create_and_enqueue as ni_create_and_enqueue
from shakenfist.schema.operations.net_iface_op \
    import model_tasks as ni_tasks
from shakenfist.schema.operations import node_inst_net_iface_op as schema
from shakenfist.schema.operations.baseclusteroperation import dependency
from shakenfist.exceptions import InvalidLifecycleState
from shakenfist.exceptions import InvalidStateException
from shakenfist.instance import Instance
from shakenfist.network.bridged_vxlan_network import BridgedVXLanNetwork
from shakenfist.network.network import Network
from shakenfist.network.interface import NetworkInterface
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.util import exceptions as util_exceptions


LOG, HANDLER = logs.setup(__name__)


class NodeInstNetIfaceOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.node_uuid = op.node_uuid
        self.instance_uuid = op.instance_uuid
        self.network_uuid = op.network_uuid
        self.interface_uuid = op.interface_uuid


class NoSuchTask(NodeInstNetIfaceOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchInstance(NodeInstNetIfaceOpException):
    def __init__(self, op):
        super().__init__(op, 'instance missing')


class NoSuchNetwork(NodeInstNetIfaceOpException):
    def __init__(self, op):
        super().__init__(op, 'network missing')


class NoSuchInterface(NodeInstNetIfaceOpException):
    def __init__(self, op):
        super().__init__(op, 'network interface missing')


class NodeInstNetIfaceOp(BaseClusterOperation):
    object_type = schema.object_type
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__node_uuid = static_values['node_uuid']
        self.__instance_uuid = static_values['instance_uuid']
        self.__network_uuid = static_values['network_uuid']
        self.__interface_uuid = static_values['interface_uuid']

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'node_uuid': self.node_uuid,
            'instance_uuid': self.instance_uuid,
            'network_uuid': self.network_uuid,
            'interface_uuid': self.interface_uuid,
            'tasks': self.tasks
        })

    # Static values
    @property
    def node_uuid(self):
        return self.__node_uuid

    @property
    def instance_uuid(self):
        return self.__instance_uuid

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
            'node_uuid': self.node_uuid,
            'instance_uuid': self.instance_uuid,
            'network_uuid': self.network_uuid,
            'interface_uuid': self.interface_uuid
        })
        return retval

    # Tasks
    def dispatch_task(self, task):
        if task not in schema.model_tasks:
            self.log.warning(f'Task {task} not in {schema.model_tasks}')
            raise NoSuchTask(self, task)

        inst = Instance.from_db(self.instance_uuid)
        if not inst:
            self.log.warning(f'Instance {self.instance_uuid} missing')
            self.state = NodeInstNetIfaceOp.STATE_ERROR
            return

        n = Network.from_db(self.network_uuid)
        if not n:
            self.log.warning(f'Network {self.network_uuid} missing')
            self.state = NodeInstNetIfaceOp.STATE_ERROR
            return

        ni = NetworkInterface.from_db(self.interface_uuid)
        if not ni:
            self.log.warning(
                f'Network interface {self.interface_uuid} missing')
            self.state = NodeInstNetIfaceOp.STATE_ERROR
            return

        try:
            self.__getattribute__(f'_{task.name}')(inst, n, ni)
        except Exception as e:
            util_exceptions.ignore_exception('node_inst_net_iface_op', e)
            self.state = NodeInstNetIfaceOp.STATE_ERROR
            try:
                inst.state = Instance.STATE_ERROR
            except InvalidStateException:
                self.log.warning(
                    'Could not transition instance to error state, '
                    f'current state is {inst.state}')
            try:
                ni.state = NetworkInterface.STATE_ERROR
            except InvalidStateException:
                self.log.warning(
                    'Could not transition network interface to '
                    f'error state, current state is {ni.state}')

    def _hot_plug_instance_interface(self, inst, n, ni):
        # Reconcile the network onto this node and enqueue the mesh op it
        # needs, then hand the actual libvirt attach off to a follow-up
        # ``attach_instance_interface`` op that depends on the mesh op. We
        # deliberately do NOT block this worker waiting for the mesh op to
        # finish -- the synchronous ``raise_for_error()`` waits this
        # replaced parked a sf-queues worker for up to ``API_ASYNC_WAIT``
        # per op, starving the small pool of slots for short ops (notably
        # agent execute, which shares the ``{node}-clusteroperation-*``
        # queues). Letting the dispatcher defer the attach op on its
        # ``depends_on`` (see the dep check in
        # ``shakenfist/daemons/queues/workitem.py``) returns the worker to
        # the pool immediately and re-checks the dependency cheaply. This
        # mirrors the instance-start/instance-create split in
        # ``node_inst_netdesc_op``.
        BridgedVXLanNetwork(n)._apply_create_on_hypervisor()
        mesh_op = n.ensure_mesh()

        schema.create_and_enqueue(
            self.node_uuid, self.instance_uuid, self.network_uuid,
            self.interface_uuid,
            [schema.model_tasks.attach_instance_interface], self.priority,
            request_id=self.request_id,
            depends_on=[dependency(
                op_type=mesh_op.object_type, op_uuid=mesh_op.uuid)])

    def _attach_instance_interface(self, inst, n, ni):
        # Runs only after the mesh op enqueued by
        # ``_hot_plug_instance_interface`` has reached a terminal state --
        # the dispatcher enforces that via this op's ``depends_on`` (and
        # aborts this op if the mesh op errors). Perform the libvirt attach
        # and then float the interface if required.
        try:
            inst.hot_plug_interface(n, ni)
        except InvalidLifecycleState as e:
            self.log.with_fields({
                'instance': inst.uuid,
                'interface': ni.uuid,
                'instance_state': inst.state.value,
                'placement': inst.placement,
            }).error(
                'Cannot hot plug interface, instance not '
                f'running: {e}')
            raise

        if ni.floating['floating_address']:
            ni_create_and_enqueue(
                n.uuid,
                ni.uuid,
                [ni_tasks.interface_float],
                priority=self.priority,
                request_id=self.request_id)
