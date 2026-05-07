from enum import Enum
from typing import ClassVar, Optional
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
from shakenfist.schema.operations.util import enqueue_cluster_operation


LOG, HANDLER = logs.setup(__name__)


object_type = ObjectType.ARTIFACT_FETCH_OP
initial_version = 1
current_version = 1


class model_tasks(Enum):
    image_fetch = 1


class model(BaseModel):
    # NOTE: this schema does not carry an artifact_uuid -- the
    # artifact this op is fetching for is identified by URL and
    # namespace, with a transient artifact lookup happening
    # inside the operation. Only the optional instance_uuid is
    # available here as a target.
    target_fields: ClassVar[dict[str, ObjectType]] = {
        'instance_uuid': ObjectType.INSTANCE,
    }

    uuid: UUID4
    namespace: str
    url: str
    instance_uuid: Optional[UUID4]
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


def create_and_enqueue(namespace, url, instance_uuid, tasks, priority,
                       request_id=None, depends_on=None, runs_after=None,
                       target_node=None):
    operation_uuid = str(uuid4())

    if not target_node:
        target_node = 'any'

    try:
        runs_after_as_deps = _convert_deps(runs_after)
        m = model(
            uuid=operation_uuid,
            namespace=namespace,
            url=url,
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
            'namespace': namespace,
            'url': url,
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
        object_type, m.model_dump(mode='json'), target=target_node,
        model_class=model)
    return object_type, operation_uuid
