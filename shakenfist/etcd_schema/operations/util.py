import copy
import time

from shakenfist_utilities import logs                 # noreorder
from shakenfist_utilities import random as sf_random  # noreorder

from shakenfist.config import config
from shakenfist import etcd


LOG, _ = logs.setup(__name__)


def base_mutations(object_type, metadata, target=None):
    if not target:
        target = metadata['node_uuid']

    # Cleanup some enumisms in the metadata
    # object_type = object_type.name
    # metadata['priority'] = metadata['priority'].name

    creation_time = time.time()
    job_name = f'{creation_time}-{sf_random.random_id()}'
    queue_name = (
        f'/sf/queue/{target}-clusteroperation-{metadata["priority"]}'
    )
    work_item = {
        'operation_type': object_type,
        'operation_uuid': metadata['uuid']
    }

    # Create the cluster operation, enqueue it, place it into the per-blob
    # cache, and emit an event about that all in a single etcd transaction.
    mutations = [
        {
            'path': f'/sf/{object_type}/{metadata["uuid"]}',
            'original_data': None,
            'new_data': metadata
        },
        {
            'path': f'/sf/attribute/{object_type}/{metadata["uuid"]}/state',
            'original_data': None,
            'new_data': {
                'value': 'queued',
                'update_time': creation_time
            }
        },
        {
            'path': f'{queue_name}/{job_name}',
            'original_data': None,
            'new_data': work_item
        }
    ]

    correlation_id = sf_random.random_id()
    tasks_str = ', '.join(metadata['tasks'])
    msg = f'{object_type} operation created with tasks {tasks_str}'

    objs = [(object_type, metadata['uuid'])]
    for key in metadata:
        if key.endswith('_uuid'):
            objs.append((key.replace('_uuid', ''), metadata[key]))

    extra = copy.deepcopy(metadata)
    extra['op_uuid'] = extra['uuid']
    del extra['uuid']
    extra['op_type'] = object_type

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


def enqueue(mutations, job_name, queue_name, work_item):
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
