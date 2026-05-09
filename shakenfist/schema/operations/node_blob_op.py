import time
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

from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import _convert_deps
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.baseclusteroperation import dependency
from shakenfist.schema.operations.util import enqueue_cluster_operation


LOG, HANDLER = logs.setup(__name__)


object_type = ObjectType.NODE_BLOB_OP
initial_version = 1
current_version = 1


class model_tasks(Enum):
    verify_size_and_checksum = 1
    ensure_local = 2


class model(BaseModel):
    target_fields: ClassVar[dict[str, ObjectType]] = {
        'blob_uuid': ObjectType.BLOB,
    }

    uuid: UUID4
    node_uuid: UUID4
    blob_uuid: UUID4
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


def create_and_enqueue(node_uuid, blob_uuid, tasks, priority, request_id=None,
                       depends_on=None, runs_after=None):
    operation_uuid = str(uuid4())

    try:
        runs_after_as_deps = _convert_deps(runs_after)
        m = model(
            uuid=operation_uuid,
            node_uuid=node_uuid,
            blob_uuid=blob_uuid,
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
            'blob_uuid': blob_uuid,
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

    # Record that this operation targets the blob in MariaDB so that
    # scheduled_tasks can discover pending blob operations without etcd.
    mariadb.create_cluster_operation_target(
        operation_uuid=operation_uuid,
        operation_type=object_type.value,
        target_object_type=ObjectType.BLOB,
        target_uuid=str(blob_uuid),
        created_at=time.time()
    )

    return object_type, operation_uuid
