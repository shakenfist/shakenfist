# Copyright 2019 Michael Still and contributors
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


object_type = ObjectType.NET_OP
initial_version = 1
current_version = 2


class model_tasks(Enum):
    network_deploy = 1
    network_destroy = 2
    network_update_dnsmasq = 3
    network_remove_dnsmasq = 4
    network_remove_nat = 5
    network_ensure_mesh = 6
    network_add_floating_ip = 7
    network_remove_floating_ip = 8
    network_apply_update_dnsmasq = 9
    network_apply_remove_dnsmasq = 10
    network_apply_create_network_node = 11
    network_apply_delete_network_node = 12


# Tasks for which N pending ops on the same network can be folded
# into a single execution by the worker (step 4) or skipped at
# enqueue time (step 5):
#
# * ``network_apply_update_dnsmasq`` regenerates the dnsmasq config
#   from current DB state. Six instance starts on the same network
#   each enqueue one of these; the final config covers every lease
#   regardless of how many runs landed.
# * ``network_ensure_mesh`` diffs the FDB against the current set of
#   participating hypervisors. Same property: the result only
#   depends on the current snapshot.
# * ``network_apply_create_network_node`` is idempotent network-node
#   setup -- running it twice in a row leaves the same state as
#   running it once.
#
# The remaining tasks are *not* coalescible -- they carry per-op
# parameters (specific floating IPs, specific mac/ip lease pairs)
# or are order-sensitive against an opposite task (remove vs
# update / delete vs create).
COALESCIBLE_TASKS: frozenset[model_tasks] = frozenset({
    model_tasks.network_apply_update_dnsmasq,
    model_tasks.network_ensure_mesh,
    model_tasks.network_apply_create_network_node,
})


class model(BaseModel):
    target_fields: ClassVar[dict[str, ObjectType]] = {
        'network_uuid': ObjectType.NETWORK,
    }

    uuid: UUID4
    network_uuid: UUID4
    floating_address: Optional[str] = None
    inner_address: Optional[str] = None
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


def create_and_enqueue(network_uuid, tasks, priority, request_id=None,
                       depends_on=None, runs_after=None,
                       target='networknode', family='clusteroperation',
                       floating_address=None, inner_address=None):
    operation_uuid = str(uuid4())

    try:
        runs_after_as_deps = _convert_deps(runs_after)
        m = model(
            uuid=operation_uuid,
            network_uuid=network_uuid,
            floating_address=floating_address,
            inner_address=inner_address,
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
            'network_uuid': network_uuid,
            'floating_address': floating_address,
            'inner_address': inner_address,
            'priority': priority,
            'request_id': request_id,
            'tasks': tasks,
            'depends_on': depends_on,
            'runs_after': runs_after,
            'version': current_version
        }).error(f'schema validation error: {exc}')
        raise exc

    enqueue_cluster_operation(
        object_type, m.model_dump(mode='json'), target=target,
        family=family, model_class=model)
    return object_type, operation_uuid
