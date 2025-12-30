import copy
import time
from typing import Any, Optional

from shakenfist_utilities import logs                 # noreorder
from shakenfist_utilities import random as sf_random  # noreorder

from shakenfist.config import config
from shakenfist import etcd
from shakenfist import mariadb
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import ClusterOperation


LOG, _ = logs.setup(__name__)


def base_mutations(
        object_type: ClusterOperation,
        metadata: dict[str, Any],
        target: Optional[str] = None
) -> tuple[list[dict[str, Any]], str, str, dict[str, str]]:
    if not target:
        target = metadata['node_uuid']

    object_type_str = object_type.name.lower()

    creation_time = time.time()
    job_name = f'{creation_time}-{sf_random.random_id()}'
    queue_name = (
        f'/sf/queue/{target}-clusteroperation-{metadata["priority"]}'
    )
    work_item = {
        'operation_type': object_type_str,
        'operation_uuid': metadata['uuid']
    }

    # Create the cluster operation and enqueue it in a single etcd transaction.
    # State is stored in MariaDB separately.
    mutations = [
        {
            'path': f'/sf/{object_type_str}/{metadata["uuid"]}',
            'original_data': None,
            'new_data': metadata
        },
        {
            'path': f'{queue_name}/{job_name}',
            'original_data': None,
            'new_data': work_item
        }
    ]

    # Store initial state in MariaDB
    initial_state = State(value='queued', update_time=creation_time)
    mariadb.set_state(object_type, metadata['uuid'], initial_state)

    correlation_id = sf_random.random_id()
    tasks_str = ', '.join(metadata['tasks'])
    msg = f'{object_type_str} operation created with tasks {tasks_str}'

    objs = [(object_type_str, metadata['uuid'])]
    for key in metadata:
        if key.endswith('_uuid'):
            objs.append((key.replace('_uuid', ''), metadata[key]))

    extra = copy.deepcopy(metadata)
    extra['op_uuid'] = extra['uuid']
    del extra['uuid']
    extra['op_type'] = object_type_str

    for ot, ou in objs:
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

    return mutations, job_name, queue_name, work_item


def enqueue(
        mutations: list[dict[str, Any]],
        job_name: str,
        queue_name: str,
        work_item: dict[str, str]
) -> None:
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
