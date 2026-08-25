from shakenfist_utilities import logs  # noreorder

from shakenfist.blob import Blob
from shakenfist.schema.operations import node_aop_op as schema
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.util import exceptions as util_exceptions


LOG, HANDLER = logs.setup(__name__)


class NodeAgentopOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.node_uuid = op.node_uuid
        self.agentoperation_uuid = op.agentoperation_uuid


class NoSuchTask(NodeAgentopOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchAgentOperation(NodeAgentopOpException):
    def __init__(self, op):
        super().__init__(op, 'instance missing')


class NodeAgentopOp(BaseClusterOperation):
    object_type = schema.object_type
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__node_uuid = static_values['node_uuid']
        self.__agentoperation_uuid = static_values['agentoperation_uuid']

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'node_uuid': self.node_uuid,
            'agentoperation_uuid': self.agentoperation_uuid,
            'tasks': self.tasks
        })

    # Static values
    @property
    def node_uuid(self):
        return self.__node_uuid

    @property
    def agentoperation_uuid(self):
        return self.__agentoperation_uuid

    # API
    def external_view(self):
        retval = super().external_view()
        retval.update({
            'node_uuid': self.node_uuid,
            'agentoperation_uuid': self.agentoperation_uuid
        })
        return retval

    # Tasks
    def dispatch_task(self, task):
        if task not in schema.model_tasks:
            self.log.warning(f'Task {task} not in {schema.model_tasks}')
            raise NoSuchTask(self, task)

        aop = AgentOperation.from_db(self.agentoperation_uuid)
        if not aop:
            self.log.warning(
                f'Agent operation {self.agentoperation_uuid} missing')
            raise NoSuchAgentOperation(self)

        try:
            self.__getattribute__(f'_{task.name}')(aop)
        except Exception as e:
            util_exceptions.ignore_exception('node_aop_op', e)
            self.state = NodeAgentopOp.STATE_ERROR
            aop.fail(f'{task.name} task raised an exception')

    def _preflight(self, aop):
        if aop.state.value != AgentOperation.STATE_PREFLIGHT:
            return

        for command in aop.commands:
            if command['command'] == 'put-blob':
                b = Blob.from_db(command['blob_uuid'])
                if not b:
                    self.state = NodeAgentopOp.STATE_ERROR
                    aop.error = ('preflight failure, blob missing: '
                                 f'{command["blob_uuid"]}')
                    return
                b.ensure_local()

                # This agent operation could have been deleted while we copied
                # this blob?
                if aop.state.value != AgentOperation.STATE_PREFLIGHT:
                    return

        aop.state = AgentOperation.STATE_QUEUED
