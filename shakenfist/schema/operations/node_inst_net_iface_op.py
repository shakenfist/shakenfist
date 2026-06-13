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


object_type = ObjectType.NODE_INST_NET_IFACE_OP
initial_version = 1
current_version = 1


class model_tasks(Enum):
    hot_plug_instance_interface = 1
    # hot_plug_instance_interface reconciles the network onto this node and
    # enqueues the mesh op it needs, then hands off to a follow-up op
    # carrying this task and a depends_on on that op. The dispatcher defers
    # the follow-up until the mesh op is terminal, so the worker is never
    # parked blocking on it.
    attach_instance_interface = 2


class model(BaseModel):
    target_fields: ClassVar[dict[str, ObjectType]] = {
        'instance_uuid': ObjectType.INSTANCE,
        'network_uuid': ObjectType.NETWORK,
        'interface_uuid': ObjectType.INTERFACE,
    }

    uuid: UUID4
    node_uuid: UUID4
    instance_uuid: UUID4
    network_uuid: UUID4
    interface_uuid: UUID4
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


def create_and_enqueue(node_uuid, instance_uuid, network_uuid, interface_uuid,
                       tasks, priority, request_id=None, depends_on=None,
                       runs_after=None):
    operation_uuid = str(uuid4())

    try:
        runs_after_as_deps = _convert_deps(runs_after)
        m = model(
            uuid=operation_uuid,
            node_uuid=node_uuid,
            instance_uuid=instance_uuid,
            network_uuid=network_uuid,
            interface_uuid=interface_uuid,
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
            'network_uuid': network_uuid,
            'interface_uuid': interface_uuid,
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
