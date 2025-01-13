from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist import etcd


class BaseOperationException(Exception):
    def __init__(self, message):
        self.message = message


class InvalidPriorityException(BaseOperationException):
    def __init__(self, task, priority):
        super().__init__(f'{priority} is not a valid priority')


class ReEnqueued(Exception):
    ...


class BaseOperation(dbo):
    # docs/developer_guide/state_machine.md has a description of these states.
    STATE_QUEUED = 'queued'
    STATE_PREFLIGHT = 'preflight'
    STATE_EXECUTING = 'executing'
    STATE_COMPLETE = 'complete'
    STATE_ABORT = 'abort'

    ACTIVE_STATES = {dbo.STATE_CREATED, STATE_QUEUED, STATE_EXECUTING,
                     STATE_COMPLETE}


def get_all_queue_names(node_uuid):
    return (get_node_user_facing_queue_names(node_uuid) +
            get_general_user_facing_queue_names() +
            get_node_background_queue_names(node_uuid) +
            get_general_background_queue_names())


def get_all_user_facing_queue_names(node_uuid):
    return (get_node_user_facing_queue_names(node_uuid) +
            get_general_user_facing_queue_names())


def get_all_background_queue_names(node_uuid):
    return (get_node_background_queue_names(node_uuid) +
            get_general_background_queue_names())


def get_node_user_facing_queue_names(node_uuid):
    return [
        node_uuid,
        f'{node_uuid}-clusteroperation-user_waiting',
        f'{node_uuid}-clusteroperation-user_facing',
        f'{node_uuid}-clusteroperation-user_facing_high_io'
    ]


def get_node_background_queue_names(node_uuid):
    return [
        f'{node_uuid}-clusteroperation-background',
        f'{node_uuid}-background',
        f'{node_uuid}-clusteroperation-background_high_io'
    ]


def get_general_user_facing_queue_names():
    return [
        'any-clusteroperation-user_waiting',
        'any-clusteroperation-user_facing',
        'any-clusteroperation-user_facing_high_io'
    ]


def get_general_background_queue_names():
    return [
        'any-clusteroperation-background',
        'any-clusteroperation-background_high_io'
    ]


class BaseClusterOperation(BaseOperation):
    # Note that cluster operations are created in etcd transactions and don't
    # have .new() methods. They therefore jump straight to queued as an initial
    # state.
    state_targets = {
        None: (BaseOperation.STATE_QUEUED),
        BaseOperation.STATE_QUEUED: (BaseOperation.STATE_EXECUTING,
                                     dbo.STATE_DELETED, dbo.STATE_ERROR,
                                     BaseOperation.STATE_ABORT),
        BaseOperation.STATE_EXECUTING: (BaseOperation.STATE_COMPLETE,
                                        dbo.STATE_DELETED, dbo.STATE_ERROR,
                                        BaseOperation.STATE_ABORT),
        BaseOperation.STATE_COMPLETE: (dbo.STATE_DELETED),
        BaseOperation.STATE_ABORT: (dbo.STATE_DELETED),
        dbo.STATE_ERROR: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: None,
    }

    def __init__(self, static_values):
        super().__init__(static_values['uuid'], static_values.get('version'))
        self.__priority = static_values['priority']
        self.__request_id = static_values.get('request_id')
        self.__depends_on = static_values.get('depends_on')
        self.__runs_after = static_values.get('runs_after')

    @property
    def priority(self):
        return self.__priority

    @property
    def request_id(self):
        return self.__request_id

    @property
    def depends_on(self):
        if not self.__depends_on or len(self.__depends_on) == 0:
            return []
        return self.__depends_on

    @property
    def runs_after(self):
        if not self.__runs_after or len(self.__runs_after) == 0:
            return []
        return self.__runs_after

    # Methods
    def execute(self):
        try:
            self.state = BaseClusterOperation.STATE_EXECUTING
            for t in self.tasks:
                self.dispatch_task(t)
                if self.state.value in [BaseClusterOperation.STATE_ABORT,
                                        BaseClusterOperation.STATE_DELETED,
                                        BaseClusterOperation.STATE_ERROR]:
                    return
            self.state = BaseClusterOperation.STATE_COMPLETE

        except ReEnqueued:
            ...

    def defer(self, delay=15):
        # Re-enqueue this operation for a retry after delay seconds
        self.add_event(
            EVENT_TYPE_STATUS, f'Execution deferred for {delay} seconds')
        work_item = {
            'operation_type': self.object_type,
            'operation_uuid': self.uuid
        }
        etcd.enqueue(config.NODE_NAME, work_item, delay=delay)
        raise ReEnqueued()
