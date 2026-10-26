import copy
from enum import Enum
import time
from typing import Any, Iterable, Optional, Type

from pydantic import BaseModel
from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import ClusterOperation


LOG, _ = logs.setup(__name__)


def is_observational(
        model_class: Optional[Type[BaseModel]],
        tasks: Optional[Iterable[Any]]) -> bool:
    """Decide whether an operation only observes its targets.

    An observational operation reads or checks its targets without
    changing them (a checksum verification, a health check). Its
    cluster_operation_targets rows are stamped observational so that
    the "current state" by-target queries (get_latest and has_pending)
    can ignore them, and a background sweep cannot shadow the
    mutating op a caller is waiting on.

    The classification is declared per *task*, not per operation
    model, because several models carry both kinds of work
    (node_inst_op has health_check_kvm_process and instance_delete).
    A model declares its observational tasks beside target_fields:

        observational_tasks: ClassVar[frozenset[model_tasks]] = frozenset({
            model_tasks.health_check_kvm_process,
        })

    A model that does not declare observational_tasks has none.

    The operation is observational only if it has at least one task
    and *every* task is in that set. One mutating task makes the whole
    operation mutating, so a mixed operation fails conservatively: the
    worst case is that it still shadows the pointer, never that
    mutating work becomes invisible to has_pending. This is also why
    the flag is derived from the tasks rather than passed by each
    caller -- a caller that added a mutating task to a sweep's task
    list could not then forget to clear the flag.

    ``tasks`` is normally the list of serialised task *names* that
    metadata['tasks'] holds after model_dump(mode='json') (the models'
    tasks field_serializer emits ``t.name``). Enum members are
    accepted too and compared by name. A name that is not a member of
    the model's task enum is not observational.
    """
    if model_class is None or not tasks:
        return False
    observational_tasks = getattr(model_class, 'observational_tasks', None)
    if not observational_tasks:
        return False

    observational_names = {t.name for t in observational_tasks}
    return all(
        (t.name if isinstance(t, Enum) else t) in observational_names
        for t in tasks)


def enqueue_cluster_operation(
        object_type: ClusterOperation,
        metadata: dict[str, Any],
        target: Optional[str] = None,
        model_class: Optional[Type[BaseModel]] = None,
        family: str = 'clusteroperation',
) -> None:
    """Create a cluster operation and enqueue its work item.

    Performs a single MariaDB-backed atomic write (cluster_operations
    row, object_states row, work_queue row, and one
    cluster_operation_targets row per target) via
    mariadb.create_and_enqueue_cluster_operation() followed by an
    audit event emission via the eventlog service. Callers are
    the create_and_enqueue() wrappers in
    shakenfist/schema/operations/*_op.py.

    The audit event fan-out produces one target for the operation
    itself plus one per metadata key ending in _uuid (other than
    'uuid' itself); add_event_multi() auto-generates a
    correlation_id across those targets.

    Raises ClusterOperationEnqueueFailed if the write did not happen.
    This used to log and return, which the callers could not tell
    apart from success -- so create_and_enqueue() returned the uuid of
    an operation that did not exist, an API request 200'd with a uuid
    the client then polled forever, and the work was dropped without
    anything retrying it (issue 3631, and the stray vxlans of issue
    3597).
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

    # Whether this op only observes its targets, derived from its tasks
    # so the stored flag cannot disagree with the work the op does. It
    # is stamped on every target row in the same transaction.
    observational = is_observational(model_class, metadata.get('tasks'))

    success, error = mariadb.create_and_enqueue_cluster_operation(
        op_uuid=metadata['uuid'],
        operation_type=object_type_str,
        metadata=metadata,
        created_at=creation_time,
        queue_name=queue_name,
        targets=op_targets,
        observational=observational,
    )

    if not success:
        LOG.with_fields({
            'operation_uuid': metadata['uuid'],
            'operation_type': object_type_str,
            'queue_name': queue_name,
            'targets': [
                f'{ot.name.lower()}:{u}' for ot, u in op_targets],
            'observational': observational,
            'error': error,
        }).error('Failed to enqueue cluster operation')
        raise exceptions.ClusterOperationEnqueueFailed(
            f'{object_type_str} operation {metadata["uuid"]} was not '
            f'enqueued to {queue_name}: {error}')

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
