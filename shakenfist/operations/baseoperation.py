from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.etcd import enqueue as etcd_enqueue


class BaseOperationException(Exception):
    def __init__(self, message):
        self.message = message


class InvalidPriorityException(Exception):
    def __init__(self, task, priority):
        super().__init__(f'{priority} is not a valid priority')


class BaseOperation(dbo):
    # docs/developer_guide/state_machine.md has a description of these states.
    STATE_QUEUED = 'queued'
    STATE_PREFLIGHT = 'preflight'
    STATE_EXECUTING = 'executing'
    STATE_COMPLETE = 'complete'

    ACTIVE_STATES = {dbo.STATE_CREATED, STATE_QUEUED,
                     STATE_EXECUTING, STATE_COMPLETE}

    state_targets = {
        None: (dbo.STATE_INITIAL, dbo.STATE_ERROR),
        dbo.STATE_INITIAL: (STATE_PREFLIGHT, STATE_QUEUED, dbo.STATE_DELETED,
                            dbo.STATE_ERROR),
        STATE_PREFLIGHT: (STATE_QUEUED, dbo.STATE_DELETED, dbo.STATE_ERROR),
        STATE_QUEUED: (STATE_EXECUTING, dbo.STATE_DELETED, dbo.STATE_ERROR),
        STATE_EXECUTING: (STATE_COMPLETE, dbo.STATE_DELETED, dbo.STATE_ERROR),
        STATE_COMPLETE: (dbo.STATE_DELETED),
        dbo.STATE_ERROR: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: None,
    }


def get_all_queue_names(node_uuid):
    return (get_all_user_facing_queue_names(node_uuid) +
            get_all_background_queue_names(node_uuid))


def get_all_user_facing_queue_names(node_uuid):
    return [
        node_uuid,
        f'{node_uuid}-clusteroperation-USER_WAITING',
        f'{node_uuid}-clusteroperation-USER_FACING'
    ]


def get_all_background_queue_names(node_uuid):
    return [
        f'{node_uuid}-clusteroperation-BACKGROUND',
        f'{node_uuid}-background',
        f'{node_uuid}-clusteroperation-BACKGROUND_HIGH_IO'
    ]


class BaseClusterOperation(BaseOperation):
    def __init__(self, static_values):
        super().__init__(static_values['uuid'], static_values.get('version'))
        self.__node_uuid = static_values['node_uuid']
        self.__priority = static_values['priority']
        self.__request_id = static_values.get('request_id')

    # Static values
    @property
    def node_uuid(self):
        return self.__node_uuid

    @property
    def priority(self):
        return self.__priority

    @property
    def request_id(self):
        return self.__request_id

    # Methods
    def enqueue(self):
        self.state = self.STATE_QUEUED
        etcd_enqueue(
            f'{self.node_uuid}-clusteroperation-{self.priority}',
            {
                'operation_type': self.object_type,
                'operation_uuid': self.uuid
            })

    def execute(self):
        self.state = self.STATE_EXECUTING
        for t in self.tasks:
            self.dispatch_task(t)
        self.state = self.STATE_COMPLETE
