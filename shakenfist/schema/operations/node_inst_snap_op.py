from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_serializer
from pydantic import UUID4
from pydantic import ValidationError
from shakenfist_utilities import logs  # noreorder

from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import _convert_deps
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.baseclusteroperation import dependency
from shakenfist.schema.operations.util import base_mutations
from shakenfist.schema.operations.util import enqueue


LOG, HANDLER = logs.setup(__name__)


object_type = ObjectType.NODE_INST_SNAP_OP
initial_version = 1
current_version = 1


class model_tasks(Enum):
    instance_snapshot = 1


class snapshot(BaseModel):
    disk: dict
    artifact_uuid: UUID4
    blob_uuid: UUID4
    thin: bool


class model(BaseModel):
    uuid: UUID4
    node_uuid: UUID4
    instance_uuid: UUID4
    snapshots: list[snapshot]
    priority: PRIORITY
    request_id: Optional[str]
    tasks: list[model_tasks]
    depends_on: Optional[list[dependency]]
    runs_after: Optional[list[dependency]]
    version: int = Field(ge=initial_version, le=current_version)

    @field_serializer('priority')
    def serialize_priority(self, priority: PRIORITY, _info):
        return priority.name

    @field_serializer('tasks')
    def serialize_tasks(self, tasks: list[model_tasks], _info):
        return [t.name for t in tasks]


def create_and_enqueue(node_uuid, instance_uuid, snapshots, tasks, priority,
                       request_id=None, depends_on=None, runs_after=None):
    operation_uuid = str(uuid4())

    try:
        runs_after_as_deps = _convert_deps(runs_after)
        m = model(
            uuid=operation_uuid,
            node_uuid=node_uuid,
            instance_uuid=instance_uuid,
            snapshots=snapshots,
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
            'instance_uuid': instance_uuid,
            'snapshots': snapshots,
            'priority': priority,
            'request_id': request_id,
            'tasks': tasks,
            'depends_on': depends_on,
            'runs_after': runs_after,
            'version': current_version
        }).error(f'etcd schema validation error: {exc}')
        raise exc

    mutations, job_name, queue_name, work_item = \
        base_mutations(object_type, m.model_dump(mode='json'))
    enqueue(mutations, job_name, queue_name, work_item)
    return object_type, operation_uuid
