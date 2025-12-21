from enum import Enum
from typing import List
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel
from pydantic import field_serializer
from pydantic import Field
from pydantic import UUID4
from pydantic import ValidationError
from shakenfist_utilities import logs  # noreorder

from shakenfist.schema.operations.baseclusteroperation \
    import CLUSTER_OPERATIONS
from shakenfist.schema.operations.baseclusteroperation \
    import _convert_deps
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.baseclusteroperation import dependency
from shakenfist.schema.operations.util import base_mutations
from shakenfist.schema.operations.util import enqueue


LOG, HANDLER = logs.setup(__name__)


object_type = CLUSTER_OPERATIONS.node_inst_op
initial_version = 1
current_version = 1


class model_tasks(Enum):
    collect_billing_statistics = 1
    health_check_kvm_process = 2
    instance_delete = 3


class model(BaseModel):
    uuid: UUID4
    # This should be a UUID, but there's some history...
    node_uuid: str
    instance_uuid: UUID4
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


def create_and_enqueue(node_uuid, instance_uuid, tasks, priority, request_id=None,
                       depends_on=None, runs_after=None):
    operation_uuid = str(uuid4())

    try:
        runs_after_as_deps = _convert_deps(runs_after)
        m = model(
            uuid=operation_uuid,
            node_uuid=node_uuid,
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
            'instance_uuid': instance_uuid,
            'priority': priority,
            'request_id': request_id,
            'tasks': tasks,
            'depends_on': depends_on,
            'runs_after': runs_after,
            'version': current_version
        }).error(f'etcd schema validation error: {exc}')
        raise exc

    mutations, job_name, queue_name, work_item = \
        base_mutations(object_type.name.lower(), m.model_dump(mode='json'))
    enqueue(mutations, job_name, queue_name, work_item)
    return object_type, operation_uuid
