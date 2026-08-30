from shakenfist_utilities import logs  # noreorder

from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.schema.operations import node_inst_netdesc_op as schema
from shakenfist.schema.operations.baseclusteroperation import dependency
from shakenfist.eventlog import add_event_multi
from shakenfist.exceptions import CapacityAdmissionDenied
from shakenfist.exceptions import ImagesCannotShrinkException
from shakenfist.exceptions import InvalidStateException
from shakenfist.exceptions import AffinityConstraintUnsatisfiable
from shakenfist.exceptions import LowResourceException
from shakenfist.instance import Instance
from shakenfist.network.bridged_vxlan_network import BridgedVXLanNetwork
from shakenfist.network.network import Network
from shakenfist.network.interface import NetworkInterface
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.schema.operations.net_iface_op \
    import create_and_enqueue as ni_create_and_enqueue
from shakenfist.schema.operations.net_iface_op \
    import model_tasks as ni_tasks
from shakenfist import scheduler
from shakenfist.util import exceptions as util_exceptions
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
    object_type = schema.object_type
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__node_uuid = static_values['node_uuid']
        self.__instance_uuid = static_values['instance_uuid']
        self.__net_desc = static_values['net_desc']

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

    # API
    def external_view(self):
        retval = super().external_view()
        retval.update({
            'node_uuid': self.node_uuid,
            'instance_uuid': self.instance_uuid,
            'net_desc': self.net_desc
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
            raise NoSuchInstance(self)

        # NOTE(mikal): an empty net_desc is in fact valid, we do not force
        # instances to always have a network.

        try:
            self.__getattribute__(f'_{task.name}')(inst)
        except AbortInstanceStart as e:
            inst.enqueue_delete_due_error(e.message)

            try:
                self.state = NodeInstNetdescOp.STATE_ABORT
            except InvalidStateException:
                self.add_event(EVENT_TYPE_AUDIT, 'failed to abort operation')
        except Exception as e:
            util_exceptions.ignore_exception('node_inst_netdesc_op', e)
            inst.enqueue_delete_due_error(f'Unhandled error: {e}')
            try:
                self.state = NodeInstNetdescOp.STATE_ERROR
            except InvalidStateException:
                # The operation may already be in a terminal state (for
                # example abort, if a previous execution of this work item
                # aborted it). Raising out of this handler would kill the
                # queue worker thread.
                self.add_event(
                    EVENT_TYPE_AUDIT, 'failed to mark operation as errored')

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
        affinity_failure = False
        affinity_message = ''
        try:
            s.find_candidates(inst, candidates=[config.NODE_UUID])
            return None

        except LowResourceException as e:
            inst.add_event(
                EVENT_TYPE_AUDIT, 'schedule failed, insufficient resources',
                extra={'message': str(e)})
            # Carried out of the except suite deliberately. Python
            # deletes the "as" target when the suite exits (PEP 3110),
            # and the two guards below are dedented back to the try
            # level, so reading e there is a NameError -- which this
            # path would only discover in the merge queue, since it
            # runs under cluster CI and not on a pull request.
            affinity_failure = isinstance(e, AffinityConstraintUnsatisfiable)
            affinity_message = str(e)

        # Unsuccessful placement, check if reached placement attempt limit
        db_placement = inst.placement
        if db_placement['placement_attempts'] > 3:
            if affinity_failure:
                raise AbortInstanceStart(
                    self, 'Too many start attempts, and no node satisfies '
                    'the requested affinity constraints: %s'
                    % affinity_message)
            raise AbortInstanceStart(self, 'Too many start attempts')

        # Or if the user asked for a specific node which is now at capacity
        if inst.requested_placement:
            if affinity_failure:
                raise AbortInstanceStart(
                    self, 'Requested node does not satisfy the requested '
                    'affinity constraints: %s' % affinity_message)
            raise AbortInstanceStart(self, 'Requested node lacks resources')

        # Try placing on another node
        try:
            candidates = []
            for node in s.metrics.keys():
                if node != config.NODE_UUID:
                    candidates.append(node)

            candidates = s.find_candidates(inst, candidates=candidates)

            # The scheduler's list is a preference; the guarded capacity
            # claim inside place_instance() is the admission, so walk the
            # candidates until one takes the instance (D7). Exhausting
            # the list is the same outcome as the scheduler finding no
            # candidates at all, so it is raised as one.
            #
            # This walk (including the P9 demand-only re-walk below)
            # also exists in external_api/instance.py's create path;
            # until phase 5 extracts a shared helper, a semantic change
            # here must be made there too.
            denials = {}

            def place_walk(enforce_demand):
                for candidate in candidates:
                    try:
                        inst.place_instance(
                            candidate, enforce_demand=enforce_demand)
                        return candidate
                    except CapacityAdmissionDenied as e:
                        denials[candidate] = {
                            'failing_stage': e.failing_stage,
                            'dimensions': e.dimensions,
                            'demand_only': e.demand_only,
                        }
                        add_event_multi(
                            EVENT_TYPE_AUDIT, [self, inst],
                            'reschedule candidate refused by capacity guard',
                            extra={
                                'node': candidate,
                                'failing_stage': e.failing_stage,
                                'dimensions': e.dimensions,
                                'enforce_demand': enforce_demand,
                            })
                return None

            target = place_walk(True)

            # The D13 demand term spreads correlated bursts across
            # nodes; it is not a capacity bound. The first pass already
            # gave demand-quiet nodes their preference, so if nothing
            # admitted and at least one candidate was refused on demand
            # alone, walk again with the clause waived rather than
            # aborting a start the cluster has real capacity for.
            if target is None and any(
                    d['demand_only'] for d in denials.values()):
                add_event_multi(
                    EVENT_TYPE_AUDIT, [self, inst],
                    'no candidate admitted and some refused on demand '
                    'alone, waiving demand guard',
                    extra={'candidates': candidates, 'denials': denials})
                target = place_walk(False)

            if target is None:
                add_event_multi(
                    EVENT_TYPE_AUDIT, [self, inst],
                    'reschedule failed, every candidate refused by capacity '
                    'guard',
                    extra={'candidates': candidates, 'denials': denials})
                raise LowResourceException(
                    'No node had capacity for this instance, '
                    f'{len(denials)} candidates refused it')

            # The artifact fetches minted at create time targeted the
            # original placement, so the redirect target's image cache has
            # never been asked for this instance's images. Enqueue fetches
            # for the new node and make the redirected start depend on
            # them, exactly as create time does.
            fetch_dependencies = inst.enqueue_disk_fetches(
                target, self.priority, request_id=self.request_id,
                artifact_event='fetch requested by instance start redirect')

            # Cluster operations are created in database transactions and
            # do not have .new() methods; the schema-layer helper is the
            # only way to mint one.
            schema.create_and_enqueue(
                target, self.instance_uuid, self.net_desc,
                self.tasks, self.priority, self.request_id,
                depends_on=fetch_dependencies or None)
            add_event_multi(
                EVENT_TYPE_AUDIT, [self, inst],
                'instance start redirected to another node',
                extra={'target_node': target})

            try:
                self.state = NodeInstNetdescOp.STATE_ABORT
            except InvalidStateException:
                self.add_event(EVENT_TYPE_AUDIT, 'failed to abort operation')

        except LowResourceException as e:
            # Unlike the two guards above, this raise is inside the
            # except suite, so it can test the exception directly.
            if isinstance(e, AffinityConstraintUnsatisfiable):
                add_event_multi(
                    EVENT_TYPE_AUDIT,
                    [self, inst],
                    'reschedule failed, affinity unsatisfiable',
                    extra={'message': str(e)})
                raise AbortInstanceStart(
                    self, 'No node satisfies the requested affinity '
                    'constraints: %s' % e)

            add_event_multi(
                EVENT_TYPE_AUDIT,
                [self, inst],
                'reschedule failed, insufficient resources',
                extra={'message': str(e)})
            raise AbortInstanceStart(self, 'Unable to find suitable node')

    def _instance_start(self, inst):
        # Reconcile the instance's networks onto this node and enqueue the
        # mesh/dnsmasq ops they need, then hand the actual creation off to a
        # follow-up ``instance_create`` op that depends on those ops.
        #
        # We deliberately do NOT block this worker waiting for the network
        # ops to finish. The synchronous ``raise_for_error()`` wait this
        # replaced parked a sf-queues worker for up to ``API_ASYNC_WAIT``
        # per op, and combined with the per-hypervisor ``ensure_mesh``
        # fan-out (each instance start feeds mesh ops into every node's
        # single-threaded net-worker) it starved the small sf-queues pool
        # of slots for short ops -- notably agent execute, which shares the
        # ``{node}-clusteroperation-*`` queues -- so those ops sat in
        # ``queued`` until the client timed out. Letting the dispatcher
        # defer the create op on its ``depends_on`` (see the dep check in
        # ``shakenfist/daemons/queues/workitem.py``) returns the worker to
        # the pool immediately and re-checks the dependency cheaply.
        if not inst:
            self.add_event(EVENT_TYPE_AUDIT, 'task requires an instance')
            raise AbortInstanceStart(self, 'Task requires an instance')

        if inst.state.value in Instance.TERMINAL_STATES:
            add_event_multi(
                EVENT_TYPE_AUDIT,
                [self, inst],
                'you cannot start an instance in a terminal state')
            raise AbortInstanceStart(self, 'Instance in terminal state')

        with inst.get_lock(op='Instance start', global_scope=False):
            try:
                # ``net_desc`` is per-interface, so an instance with N
                # interfaces on the same network would reconcile it N
                # times. Track which networks we've reconciled in this
                # start so the per-network work (and its mesh/dnsmasq
                # enqueues) happens once. Interface-level work (state
                # flip) stays per-interface.
                network_dependencies = []
                reconciled_network_uuids: 'set[str]' = set()
                for netdesc in self.net_desc:
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

                    if n.uuid not in reconciled_network_uuids:
                        BridgedVXLanNetwork(n)._apply_create_on_hypervisor()
                        mesh_op = n.ensure_mesh()
                        network_dependencies.append(dependency(
                            op_type=mesh_op.object_type, op_uuid=mesh_op.uuid))
                        # dnsmasq lives on the network node only.
                        # Calling ``_apply_update_dnsmasq`` here
                        # directly silently wrote to this
                        # hypervisor's (absent) dnsmasq state and the
                        # actual network-node dnsmasq never learned
                        # about the new lease -- exactly the bug
                        # surfaced by ``test_provided_dns``. Enqueue a
                        # net_op instead so the network node's
                        # dispatcher runs the refresh.
                        dnsmasq_op = n.update_dnsmasq()
                        if dnsmasq_op is not None:
                            network_dependencies.append(dependency(
                                op_type=dnsmasq_op.object_type,
                                op_uuid=dnsmasq_op.uuid))
                        reconciled_network_uuids.add(n.uuid)

                # Hand the actual instance creation off to a follow-up op
                # that the dispatcher defers until the network ops above
                # are terminal.
                schema.create_and_enqueue(
                    self.node_uuid, self.instance_uuid, self.net_desc,
                    [schema.model_tasks.instance_create], self.priority,
                    request_id=self.request_id,
                    depends_on=network_dependencies or None)

            except InvalidStateException as e:
                # This instance is in an error or deleted state. Given the check
                # at the top of this method, that indicates a race.
                inst.enqueue_delete_due_error(
                    'invalid state transition: %s' % e)
                return

    def _instance_create(self, inst):
        # Runs only after the network reconcile ops enqueued by
        # ``_instance_start`` have reached a terminal state -- the
        # dispatcher enforces that via this op's ``depends_on`` (and aborts
        # this op if one of them errors, matching how the image-fetch
        # dependencies on the original start op behave). Float the required
        # interfaces, allocate ports, and start the VM.
        if not inst:
            self.add_event(EVENT_TYPE_AUDIT, 'task requires an instance')
            raise AbortInstanceStart(self, 'Task requires an instance')

        if inst.state.value in Instance.TERMINAL_STATES:
            add_event_multi(
                EVENT_TYPE_AUDIT,
                [self, inst],
                'you cannot start an instance in a terminal state')
            raise AbortInstanceStart(self, 'Instance in terminal state')

        with inst.get_lock(op='Instance create', global_scope=False):
            try:
                # Float any interfaces that asked for a floating IP.
                for netdesc in self.net_desc:
                    ni = NetworkInterface.from_db(netdesc['iface_uuid'])
                    if ni and ni.floating['floating_address']:
                        ni_create_and_enqueue(
                            netdesc['network_uuid'],
                            ni.uuid,
                            [ni_tasks.interface_float],
                            priority=self.priority,
                            request_id=self.request_id)

                # Allocate console and VDI ports
                inst.allocate_instance_ports()

                # Now we can start the instance
                with util_general.RecordedOperation('instance creation', inst):
                    inst.create()

            except InvalidStateException as e:
                # This instance is in an error or deleted state. Given the check
                # at the top of this method, that indicates a race.
                inst.enqueue_delete_due_error(
                    'invalid state transition: %s' % e)
                return

            except ImagesCannotShrinkException as e:
                if inst:
                    inst.enqueue_delete_due_error(f'Image resize failed: {e}')
