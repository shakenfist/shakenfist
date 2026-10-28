from enum import Enum
from typing import ClassVar
from typing import List
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel
from pydantic import field_serializer
from pydantic import Field
from pydantic import UUID4
from pydantic import ValidationError
from shakenfist_utilities import logs  # noreorder

from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import _convert_deps
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.baseclusteroperation import dependency
from shakenfist.schema.operations.util import enqueue_cluster_operation


LOG, HANDLER = logs.setup(__name__)


object_type = ObjectType.NODE_AOP_OP
initial_version = 1
# Version 2 added instance_uuid, so that the preflight targets the instance
# its agent operation runs on and is cancelled when that instance is
# deleted.
current_version = 2


# All tasks mutate: it promotes, expires or fails an agent operation.
class model_tasks(Enum):
    preflight = 1


class model(BaseModel):
    # The instance is a target as well as the agent operation. A delete
    # walks the operations targeting the instance to cancel them, and a
    # preflight which was not among them ran against an instance being
    # deleted. instance_uuid is optional only for rows written before
    # version 2; create_and_enqueue() always sets it.
    target_fields: ClassVar[dict[str, ObjectType]] = {
        'agentoperation_uuid': ObjectType.AGENTOPERATION,
        'instance_uuid': ObjectType.INSTANCE,
    }

    uuid: UUID4
    node_uuid: UUID4
    agentoperation_uuid: UUID4
    instance_uuid: Optional[UUID4]
    priority: PRIORITY
    request_id: Optional[str]
    tasks: List[model_tasks]
    depends_on: Optional[List[dependency]]
    runs_after: Optional[List[dependency]]
    version: int = Field(ge=initial_version, le=current_version)

    @field_serializer('priority')
    def serialize_priority(self, priority: PRIORITY, _info):
        return priority.name

    @field_serializer('tasks')
    def serialize_tasks(self, tasks: List[model_tasks], _info):
        return [t.name for t in tasks]


def create_and_enqueue(node_uuid, agentoperation_uuid, instance_uuid, tasks,
                       priority, request_id=None, depends_on=None,
                       runs_after=None):
    operation_uuid = str(uuid4())

    try:
        runs_after_as_deps = _convert_deps(runs_after)
        m = model(
            uuid=operation_uuid,
            node_uuid=node_uuid,
            agentoperation_uuid=agentoperation_uuid,
            instance_uuid=instance_uuid,
            priority=priority,
            request_id=request_id,
            tasks=tasks,
            depends_on=depends_on,
            runs_after=runs_after_as_deps,
            version=current_version
        )
    except ValidationError as exc:
        LOG.with_fields({
            'uuid': operation_uuid,
            'node_uuid': node_uuid,
            'agentoperation_uuid': agentoperation_uuid,
            'instance_uuid': instance_uuid,
            'priority': priority,
            'request_id': request_id,
            'tasks': tasks,
            'depends_on': depends_on,
            'runs_after': runs_after,
            'version': current_version
        }).error(f'schema validation error: {exc}')
        raise exc

    enqueue_cluster_operation(
        object_type, m.model_dump(mode='json'), model_class=model)
    return object_type, operation_uuid
