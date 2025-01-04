from enum import Enum
from typing import List
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel
from pydantic import UUID4

from shakenfist.etcd_schema.operations.baseclusteroperation import PRIORITY
from shakenfist.etcd_schema.operations.util import base_mutations
from shakenfist.etcd_schema.operations.util import enqueue


object_type = 'nodeinstanceoperation'
initial_version = 1
current_version = 1


class model_tasks(Enum):
    collect_billing_statistics = 1
    health_check_kvm_process = 2


class model(BaseModel):
    uuid: UUID4
    # This should be a UUID, but there's some history...
    node_uuid: str
    instance_uuid: UUID4
    priority: PRIORITY
    request_id: Optional[str]
    tasks: List[model_tasks]
    version: int


def create_and_enqueue(node_uuid, instance_uuid, tasks, priority, request_id=None):
    operation_uuid = str(uuid4())
    m = model(
        uuid=operation_uuid,
        node_uuid=node_uuid,
        instance_uuid=instance_uuid,
        priority=priority,
        request_id=request_id,
        tasks=tasks,
        version=current_version
    )

    mutations, job_name, queue_name, work_item = \
        base_mutations(object_type, m.model_dump())
    enqueue(mutations, job_name, queue_name, work_item)
