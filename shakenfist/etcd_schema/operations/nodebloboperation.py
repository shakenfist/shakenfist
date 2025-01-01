from enum import Enum
import time
from typing import List
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel
from pydantic import UUID4
from shakenfist_utilities import logs                 # noreorder
from shakenfist_utilities import random as sf_random  # noreorder

from shakenfist.config import config
from shakenfist import etcd
from shakenfist.etcd_schema.operations.baseclusteroperation import PRIORITY


LOG, _ = logs.setup(__name__)


object_type = 'nodebloboperation'
initial_version = 1
current_version = 1


class model_tasks(Enum):
    verify_size_and_checksum = 1
    ensure_local = 2


class model(BaseModel):
    uuid: UUID4
    # This should be a UUID, but there's some history...
    node_uuid: str
    blob_uuid: UUID4
    priority: PRIORITY
    request_id: Optional[str]
    tasks: List[model_tasks]
    version: int


# This is here because sometimes we want to create the operation and enqueue it,
# but cannot create the object because of circular dependencies. Luckily, the
# model ensures we're keeping in sync with the schema.
def create_and_enqueue(node_uuid, blob_uuid, tasks, priority, request_id=None):
    operation_uuid = str(uuid4())
    m = model(
        uuid=operation_uuid,
        node_uuid=node_uuid,
        blob_uuid=blob_uuid,
        priority=priority,
        request_id=request_id,
        tasks=tasks,
        version=current_version
    )

    creation_time = time.time()
    metadata = m.model_dump()
    job_name = f'{creation_time}-{sf_random.random_id()}'
    queue_name = f'/sf/{node_uuid}-clusteroperation-{priority}'
    work_item = {
        'operation_type': object_type,
        'operation_uuid': operation_uuid
    }

    # Create the cluster operation, enqueue it, place it into the per-blob
    # cache, and emit an event about that all in a single etcd transaction.
    mutations = [
        {
            'path': f'/sf/{object_type}/{operation_uuid}',
            'original_data': None,
            'new_data': metadata
        },
        {
            'path': f'/sf/attributes/{object_type}/{operation_uuid}/state',
            'original_data': None,
            'new_data': {
                'value': 'created',
                'update_time': creation_time
            }
        },
        {
            'path': f'/sf/clusteroperations-by-blob/{blob_uuid}/{node_uuid}',
            'original_data': None,
            'new_data': work_item
        },
        {
            'path': queue_name,
            'original_data': None,
            'new_data': work_item
        }
    ]

    correlation_id = sf_random.random_id()
    tasks_str = ', '.join([t.name for t in tasks])
    msg = f'node blob operation created with tasks {tasks_str}'
    for ot, ou in [
            (object_type, operation_uuid),
            ('blob', blob_uuid),
            ('node', node_uuid)]:
        mutations.append(
            {
                'path': f'/sf/event/{ot}/{ou}/{creation_time}',
                'original_data': None,
                'new_data': {
                    'timestamp': creation_time,
                    'event_type': 'audit',
                    'object_type': ot,
                    'object_uuid': ou,
                    'fqdn': config.NODE_NAME,
                    'duration': None,
                    'message': msg,
                    'extra': metadata,
                    'correlation_id': correlation_id
                }
            }
        )

    success, _ = etcd.replace_many_raw(mutations)
    if success:
        msg = 'Enqueued cluster operation'
    else:
        msg = 'Failed to enqueue cluster operation'
    LOG.with_fields({
        'job_name': job_name,
        'queue_name': queue_name,
        'work_item': work_item,
    }).info(msg)
