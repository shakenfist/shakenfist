from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import etcd
from shakenfist.etcd_schema.operations import node_inst_iface_op as schema
from shakenfist.instance import Instance
from shakenfist.network import Network
from shakenfist.networkinterface import NetworkInterface
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.tasks import FloatNetworkInterfaceTask
from shakenfist.util import general as util_general


LOG, HANDLER = logs.setup(__name__)


class NodeInstIfaceOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.node_uuid = op.node_uuid
        self.instance_uuid = op.instance_uuid
        self.network_uuid = op.network_uuid
        self.interface_uuid = op.interface_uuid


class NoSuchTask(NodeInstIfaceOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchInstance(NodeInstIfaceOpException):
    def __init__(self, op):
        super().__init__(op, 'instance missing')


class NoSuchNetwork(NodeInstIfaceOpException):
    def __init__(self, op):
        super().__init__(op, 'network missing')


class NoSuchInterface(NodeInstIfaceOpException):
    def __init__(self, op):
        super().__init__(op, 'network interface missing')


class NodeInstIfaceOp(BaseClusterOperation):
    object_type = schema.object_type.name.lower()
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values)

        self.__node_uuid = static_values['node_uuid']
        self.__instance_uuid = static_values['instance_uuid']
        self.__network_uuid = static_values['network_uuid']
        self.__interface_uuid = static_values['interface_uuid']

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

    @property
    def tasks(self):
        return self.__tasks

    # Tasks
    def dispatch_task(self, task):
        if task not in schema.model_tasks:
            self.log.warning(f'Task {task} not in {schema.model_tasks}')
            raise NoSuchTask(self, task)

        inst = Instance.from_db(self.instance_uuid)
        if not inst:
            self.log.warning(f'Instance {self.instance_uuid} missing')
            raise NoSuchInstance(self)

        n = Network.from_db(self.network_uuid)
        if not n:
            self.log.warning(f'Network {self.network_uuid} missing')
            raise NoSuchNetwork(self)

        ni = NetworkInterface.from_db(self.interface_uuid)
        if not ni:
            self.log.warning(
                f'Network interface {self.interface_uuid} missing')
            raise NoSuchInterface(self)

        try:
            self.__getattribute__(f'_{task.name}')(inst, n, ni)
        except Exception as e:
            util_general.ignore_exception('node_inst_iface_op', e)
            self.state = NodeInstIfaceOp.STATE_ERROR
            inst.state = Instance.STATE_ERROR
            ni.state = NetworkInterface.STATE_ERROR

    def _hot_plug_instance_interface(self, inst, n, ni):
        inst.hot_plug_interface(n, ni)
        if ni.floating:
            etcd.enqueue(
                'networknode', FloatNetworkInterfaceTask(n.uuid, ni.uuid))
