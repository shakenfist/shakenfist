from shakenfist_utilities import logs  # noreorder

from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import etcd
from shakenfist.etcd_schema.operations import node_inst_netdesc_op as schema
from shakenfist.eventlog import add_event_multi
from shakenfist.exceptions import ImagesCannotShrinkException
from shakenfist.exceptions import InvalidStateException
from shakenfist.exceptions import LowResourceException
from shakenfist.instance import Instance
from shakenfist.network.network import Network
from shakenfist.network.interface import NetworkInterface
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.etcd_schema.operations.net_iface_op \
    import create_and_enqueue as ni_create_and_enqueue
from shakenfist.etcd_schema.operations.net_iface_op \
    import model_tasks as ni_tasks
from shakenfist import scheduler
from shakenfist.util import general as util_general


LOG, HANDLER = logs.setup(__name__)


class NodeInstNetdescOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.instance_uuid = op.instance_uuid
        self.node_uuid = op.node_uuid
        self.net_desc = op.net_desc
        self.tasks = op.tasks


class NoSuchTask(NodeInstNetdescOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchInstance(NodeInstNetdescOpException):
    def __init__(self, op):
        super().__init__(op, 'instance missing')


class InvalidNetdesc(NodeInstNetdescOpException):
    def __init__(self, op):
        super().__init__(op, 'invalid net_desc')


class AbortInstanceStart(NodeInstNetdescOpException):
    def __init__(self, op, message):
        super().__init__(op, message)


class NodeInstNetdescOp(BaseClusterOperation):
    object_type = schema.object_type.name.lower()
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values)

        self.__node_uuid = static_values['node_uuid']
        self.__instance_uuid = static_values['instance_uuid']
        self.__net_desc = static_values['net_desc']

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
            'net_desc': self.net_desc,
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
    def net_desc(self):
        return self.__net_desc

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

        # NOTE(mikal): an empty net_desc is in fact valid, we do not force
        # instances to always have a network.

        try:
            self.__getattribute__(f'_{task.name}')(inst)
        except AbortInstanceStart as e:
            inst.enqueue_delete_due_error(e.message)
            self.state = NodeInstNetdescOp.STATE_ABORT
        except Exception as e:
            util_general.ignore_exception('node_inst_netdesc_op', e)
            inst.enqueue_delete_due_error(f'Unhandled error: {e}')
            self.state = NodeInstNetdescOp.STATE_ERROR

    def _instance_preflight(self, inst):
        state = inst.state.value
        if state in Instance.TERMINAL_STATES:
            inst.add_event(
                EVENT_TYPE_AUDIT,
                ('you cannot preflight an instance in state {state}, '
                 'skipping task'))
            return

        inst.state = Instance.STATE_PREFLIGHT

        # Try to place on this node
        s = scheduler.Scheduler()
        try:
            s.find_candidates(inst, candidates=[config.NODE_NAME])
            return None

        except LowResourceException as e:
            inst.add_event(
                EVENT_TYPE_AUDIT, 'schedule failed, insufficient resources',
                extra={'message': str(e)})

        # Unsuccessful placement, check if reached placement attempt limit
        db_placement = inst.placement
        if db_placement['placement_attempts'] > 3:
            raise AbortInstanceStart(self, 'Too many start attempts')

        # Or if the user asked for a specific node which is now at capacity
        if inst.requested_placement:
            raise AbortInstanceStart(self, 'Requested node lacks resources')

        # Try placing on another node
        try:
            candidates = []
            for node in s.metrics.keys():
                if node != config.NODE_NAME:
                    candidates.append(node)

            candidates = s.find_candidates(inst, candidates=candidates)
            inst.place_instance(candidates[0])

            redirected = NodeInstNetdescOp.new(
                candidates[0], self.instance_uuid, self.net_desc,
                self.tasks, self.priority, self.request_id)
            redirected.enqueue()
            self.state = NodeInstNetdescOp.STATE_ABORT

        except LowResourceException as e:
            add_event_multi(
                EVENT_TYPE_AUDIT,
                [self, inst],
                'reschedule failed, insufficient resources',
                extra={'message': str(e)})
            raise AbortInstanceStart(self, 'Unable to find suitable node')

    def _instance_start(self, inst):
        if not inst:
            self.add_event(EVENT_TYPE_AUDIT, 'task requires an instance')
            raise AbortInstanceStart(self, 'Task requires an instance')

        if inst.state.value in Instance.TERMINAL_STATES:
            add_event_multi(
                EVENT_TYPE_AUDIT,
                [self, inst],
                'you cannot start an instance in a terminal state')
            raise AbortInstanceStart(self, 'Instance in terminal state')

        with inst.get_lock(ttl=900, op='Instance start', global_scope=False):
            try:
                # Ensure networks are connected to this node
                iface_uuids = []
                float_tasks = []
                for netdesc in self.net_desc:
                    iface_uuids.append(netdesc['iface_uuid'])
                    n = Network.from_db(netdesc['network_uuid'])
                    if not n:
                        add_event_multi(
                            EVENT_TYPE_AUDIT,
                            [self, inst, ('network', netdesc['network_uuid'])],
                            f'missing network: {netdesc["network_uuid"]}')
                        inst.enqueue_delete_due_error(
                            f'missing network: {netdesc["network_uuid"]}')
                        raise AbortInstanceStart(self, 'Missing network')

                    if n.state.value != Network.STATE_CREATED:
                        add_event_multi(
                            EVENT_TYPE_AUDIT,
                            [self, inst, n],
                            f'network is not active: {n.uuid}')
                        inst.enqueue_delete_due_error(
                            f'network is not active: {n.uuid}')
                        raise AbortInstanceStart(self, 'Inactive network')

                    # We must record interfaces very early for the vxlan leak
                    # detection code in the net daemon to work correctly.
                    ni = NetworkInterface.from_db(netdesc['iface_uuid'])
                    if ni.state.value not in NetworkInterface.ACTIVE_STATES:
                        add_event_multi(
                            EVENT_TYPE_AUDIT,
                            [self, inst, n, ni],
                            ('you cannot start an instance with an inactive '
                             'network interface.'))
                        inst.enqueue_delete_due_error(
                            'Network interface is inactive')
                        raise AbortInstanceStart(
                            self, 'Inactive network interface')

                    ni.state = NetworkInterface.STATE_CREATED
                    n.create_on_hypervisor()
                    n.ensure_mesh()
                    n.update_dnsmasq()

                    if ni.floating['floating_address']:
                        op_type, op_uuid = ni_create_and_enqueue(
                            n.uuid,
                            ni.uuid,
                            [ni_tasks.interface_float],
                            priority=self.priority,
                            request_id=self.request_id)
                        n.set_last_cluster_operation(op_type, op_uuid)

                # Allocate console and VDI ports
                inst.allocate_instance_ports()

                # Now we can start the instance
                with util_general.RecordedOperation('instance creation', inst):
                    inst.create(iface_uuids)

                # And now float any required interfaces
                for ft in float_tasks:
                    etcd.enqueue('networknode', ft)

            except InvalidStateException as e:
                # This instance is in an error or deleted state. Given the check
                # at the top of this method, that indicates a race.
                inst.enqueue_delete_due_error(
                    'invalid state transition: %s' % e)
                return

            except ImagesCannotShrinkException as e:
                if inst:
                    inst.enqueue_delete_due_error(f'Image resize failed: {e}')
