import copy
import time
from typing import Any, Optional, Type

from pydantic import BaseModel
from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import eventlog
from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import ClusterOperation


LOG, _ = logs.setup(__name__)


def enqueue_cluster_operation(
        object_type: ClusterOperation,
        metadata: dict[str, Any],
        target: Optional[str] = None,
        model_class: Optional[Type[BaseModel]] = None,
        family: str = 'clusteroperation',
) -> None:
    """Create a cluster operation and enqueue its work item.

    Performs a single MariaDB-backed atomic write (cluster_operations
    row, object_states row, work_queue row) via
    mariadb.create_and_enqueue_cluster_operation() followed by an
    audit event emission via the eventlog service. Callers are
    the create_and_enqueue() wrappers in
    shakenfist/schema/operations/*_op.py.

    The audit event fan-out produces one target for the operation
    itself plus one per metadata key ending in _uuid (other than
    'uuid' itself); add_event_multi() auto-generates a
    correlation_id across those targets.
    """
    if not target:
        target = metadata['node_uuid']

    object_type_str = object_type.name.lower()
    creation_time = time.time()
    queue_name = f'{target}-{family}-{metadata["priority"]}'

    # Collect the cluster_operation_targets rows to write for every
    # target declared by the model class. The declaration is a
    # class-level ClassVar mapping metadata field names to ObjectType
    # enum members. Fields whose value on the metadata is None are
    # skipped (the schema permits Optional UUIDs in some ops). These
    # are passed to create_and_enqueue_cluster_operation so they are
    # written in the SAME transaction as the operation itself: a
    # previously separate loop left a window where an enqueued op had
    # no target rows, and a by-target reader (has_pending, is_okay
    # gating, the /clusteroperations listing, the CI await helpers)
    # in that window wrongly concluded nothing targeted the object.
    op_targets: list[tuple[ObjectType, str]] = []
    if model_class is not None:
        target_fields = getattr(model_class, 'target_fields', {})
        for field_name, target_object_type in target_fields.items():
            target_uuid = metadata.get(field_name)
            if target_uuid is None:
                continue
            op_targets.append((target_object_type, target_uuid))

    success, error = mariadb.create_and_enqueue_cluster_operation(
        op_uuid=metadata['uuid'],
        operation_type=object_type_str,
        metadata=metadata,
        created_at=creation_time,
        queue_name=queue_name,
        targets=op_targets,
    )

    if not success:
        LOG.with_fields({
            'operation_uuid': metadata['uuid'],
            'operation_type': object_type_str,
            'queue_name': queue_name,
            'targets': [
                f'{ot.name.lower()}:{u}' for ot, u in op_targets],
            'error': error,
        }).error('Failed to enqueue cluster operation')
        return

    LOG.with_fields({
        'operation_uuid': metadata['uuid'],
        'queue_name': queue_name,
    }).info('Enqueued cluster operation')

    # Build the audit event targets: the operation itself plus
    # every object referenced in the metadata via an _uuid key.
    targets: list[tuple[str, str]] = [
        (object_type_str, metadata['uuid'])
    ]
    for key in metadata:
        if key == 'uuid':
            continue
        if key.endswith('_uuid') and metadata[key] is not None:
            targets.append(
                (key.replace('_uuid', ''), metadata[key]))

    extra = copy.deepcopy(metadata)
    extra['op_uuid'] = extra['uuid']
    del extra['uuid']
    extra['op_type'] = object_type_str

    tasks_str = ', '.join(metadata['tasks'])
    msg = (
        f'{object_type_str} operation created with tasks '
        f'{tasks_str}'
    )
    eventlog.add_event_multi(
        EVENT_TYPE_AUDIT, targets, msg, extra=extra)
