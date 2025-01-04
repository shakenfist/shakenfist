import os
from uuid import uuid4

import psutil

from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.etcd_schema.operations.baseclusteroperation import PRIORITY
from shakenfist.exceptions import LowResourceException
from shakenfist.instance import Instance
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.operations.baseoperation import InvalidPriorityException
from shakenfist import scheduler
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt


class NodeInstanceNetDescOperationException(BaseOperationException):
    def __init__(self, task, message):
        super().__init__(message)
        self.task_type = task.object_type
        self.task_uuid = task.uuid
        self.instance_uuid = task.instance_uuid
        self.node_uuid = task.node_uuid
        self.net_desc = task.net_desc


class NoSuchTask(NodeInstanceNetDescOperationException):
    def __init__(self, task):
        super().__init__(task, 'no such task')


class NoSuchInstance(NodeInstanceNetDescOperationException):
    def __init__(self, task):
        super().__init__(task, 'instance missing')


class AbortInstanceStart(NodeInstanceNetDescOperationException):
    def __init__(self, task):
        super().__init__(task, 'instance missing')


class NodeInstanceNetDescOperation(BaseClusterOperation):
    object_type = 'nodeinstancenetdescoperation'
    initial_version = 1
    current_version = 1

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values)

        self.__instance_uuid = static_values['instance_uuid']
        self.__tasks = static_values['tasks']

    @classmethod
    def new(cls, node_uuid, instance_uuid, net_desc, tasks, priority,
            request_id=None):
        if priority not in PRIORITY:
            raise InvalidPriorityException(priority)

        operation_uuid = str(uuid4())
        NodeInstanceNetDescOperation._db_create(operation_uuid, {
            'uuid': operation_uuid,
            'node_uuid': node_uuid,
            'instance_uuid': instance_uuid,
            'net_desc': net_desc,
            'priority': priority,
            'request_id': request_id,
            'tasks': tasks,
            'version': cls.current_version
        })
        o = NodeInstanceNetDescOperation.from_db(operation_uuid)
        o.state = cls.STATE_INITIAL
        return o

    # Static values
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
    _all_tasks = [
        'instance_preflight'
    ]

    def dispatch_task(self, task):
        if task not in self._all_tasks:
            raise NoSuchTask(task)

        inst = Instance.from_db(self.instance_uuid)
        if not inst:
            raise NoSuchInstance(task)

        try:
            self.__getattribute__(f'_{task}')(inst)
        except AbortInstanceStart:
            inst.state = Instance.STATE_ERROR
            self.state = NodeInstanceNetDescOperation.STATE_ABORT
        except Exception as e:
            util_general.ignore_exception('nodeinstancenetdescoperation', e)
            inst.state = Instance.STATE_ERROR
            self.state = NodeInstanceNetDescOperation.STATE_ERROR

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
            s.find_candidates(inst, self.net_desc,
                              candidates=[config.NODE_NAME])
            return None

        except LowResourceException as e:
            inst.add_event(
                EVENT_TYPE_AUDIT, 'schedule failed, insufficient resources',
                extra={'message': str(e)})

        # Unsuccessful placement, check if reached placement attempt limit
        db_placement = inst.placement
        if db_placement['placement_attempts'] > 3:
            raise AbortInstanceStart('Too many start attempts')

        # Or if the user asked for a specific node which is now at capacity
        if inst.requested_placement:
            raise AbortInstanceStart('Requested node lacks resources')

        # Try placing on another node
        try:
            candidates = []
            for node in s.metrics.keys():
                if node != config.NODE_NAME:
                    candidates.append(node)

            candidates = s.find_candidates(inst, self.net_desc,
                                           candidates=candidates)
            inst.place_instance(candidates[0])

            redirected = NodeInstanceNetDescOperation.new(
                candidates[0], self.instance_uuid, self.net_desc,
                self.tasks, self.priority, self.request_id)
            redirected.enqueue()
            self.state = NodeInstanceNetDescOperation.STATE_ABORT

        except LowResourceException as e:
            inst.add_event(
                EVENT_TYPE_AUDIT, 'reschedule failed, insufficient resources',
                extra={'message': str(e)})
            raise AbortInstanceStart('Unable to find suitable node')
