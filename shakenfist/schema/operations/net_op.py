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

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist import mariadb
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
# * ``network_apply_create_network_node`` is idempotent network-node
#   setup -- running it twice in a row leaves the same state as
#   running it once.
#
# The remaining tasks are *not* coalescible -- they carry per-op
# parameters (specific floating IPs, specific mac/ip lease pairs)
# or are order-sensitive against an opposite task (remove vs
# update / delete vs create).
#
# ``network_ensure_mesh`` deliberately is NOT in this set, even
# though it looks like the best candidate in the file: it is
# idempotent and it only depends on the current snapshot of
# participating hypervisors. The problem is that it is the one NetOp
# task that does *node-local* work -- ``_apply_ensure_mesh`` diffs
# **this** host's FDB -- and the coalescing key is
# ``COALESCIBLE_KEY_COLUMNS``, i.e. the network alone. There is no
# column on ``cluster_operations`` recording which node an op was
# targeted at (``target`` is queue routing, and NetOp's model has no
# ``node_uuid``), so "same network" is the finest grain the fold can
# express -- ``COALESCIBLE_KEY_COLUMNS`` below is a one element
# tuple -- and it cannot tell hypervisor A's mesh op apart from
# hypervisor B's. Declaring it coalescible therefore let the network
# node's survivor mark every other hypervisor's pending mesh op
# ``complete`` without doing their work, leaving their FDBs stale --
# the ``test_single_virtual_networks_work`` failure described in
# ``BaseClusterOperation.execute``. That was inert while the fold's
# join was broken (#3878) and would have gone live with the fix; see
# #3884 for the multi-column key that would let it back in.
COALESCIBLE_TASKS: frozenset[model_tasks] = frozenset({
    model_tasks.network_apply_update_dnsmasq,
    model_tasks.network_apply_create_network_node,
})

# The coalescing key: indexed columns on the ``cluster_operations``
# table which together identify "the same work". Used by both the
# enqueue-side dedup lookup (this module) and the worker-side fold
# (``NetOp.coalescible_key_columns``, which reads this constant), so
# the two can never disagree about what a sibling is. A candidate op
# must match every column.
#
# For NetOp that is the network alone: every op is scoped to exactly
# one network, and nothing in the key distinguishes the node an op was
# targeted at. That is the limitation which keeps
# ``network_ensure_mesh`` out of COALESCIBLE_TASKS (see the note
# above) and which #3884 is about.
COALESCIBLE_KEY_COLUMNS: tuple[str, ...] = ('network_uuid',)


def _coalescible_keys(network_uuid) -> list[tuple[str, str]]:
    """Build the coalescing key for one enqueue.

    One ``(column, value)`` pair per column of
    ``COALESCIBLE_KEY_COLUMNS``, so the enqueue-side dedup below and
    the worker-side fold in ``BaseClusterOperation.execute`` ask the
    database exactly the same question. A column added to the tuple
    without a value here raises ``KeyError`` at the call site rather
    than silently narrowing or widening the key.
    """
    values = {'network_uuid': str(network_uuid)}
    return [(column, values[column]) for column in COALESCIBLE_KEY_COLUMNS]


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
    # Enqueue-side dedup: if this enqueue is a single coalescible task
    # on a network that already has an equivalent pending op in the
    # queue, return that op's uuid instead of inserting a duplicate.
    # All callers' ``raise_for_error`` waits then block on the same
    # op and the worker runs it once. ``depends_on`` and
    # ``runs_after`` are skipped here -- an op with dependencies is
    # intentionally distinct from a bare reconciliation enqueue, and
    # collapsing them would erase the ordering constraint the caller
    # encoded. See ``BaseClusterOperation.execute``'s cross-op fold
    # (worker-side dedup, step 4) for the safety net that catches the
    # race where two callers both miss the lookup.
    #
    # Neither the enqueue-side dedup below nor the worker-side fold in
    # ``BaseClusterOperation.execute`` keys on the queue: the dedup
    # query and ``claim_coalescible_siblings`` both match on
    # (op_type, network_uuid, task, state), and ``cluster_operations``
    # has no queue column to filter on. That is only sound while every
    # coalescible task lives on the single cluster-wide network-node
    # queue, where one elected worker drains everything and two
    # enqueues for the same network really are the same work.
    #
    # A coalescible task on a per-node queue (``target=<node_uuid>``)
    # would break that: hypervisor A's op and hypervisor B's op look
    # identical to both dedup paths, but do different work on
    # different hosts, so folding them leaves one host's state
    # unapplied. That is the bug which broke
    # ``test_single_virtual_networks_work`` on the network-facade
    # branch, and it is why ``network_ensure_mesh`` is not in
    # COALESCIBLE_TASKS (see the note there).
    #
    # Rather than leave that as a convention nobody checks, enforce it
    # here. It costs a set intersection per enqueue and turns a silent
    # class of cross-node state corruption into an immediate, loud
    # failure at the call site that introduced it.
    if target != 'networknode':
        misrouted = [t.name for t in tasks if t in COALESCIBLE_TASKS]
        if misrouted:
            raise exceptions.InvalidCoalescibleEnqueue(
                f'net_op tasks {misrouted} are declared coalescible, which '
                f'is only sound on the cluster-wide networknode queue, but '
                f'this enqueue targets {target!r}. Either drop the task from '
                f'COALESCIBLE_TASKS or give the fold a key which '
                f'distinguishes the target node (see issue #3884).')

    if (len(tasks) == 1
            and tasks[0] in COALESCIBLE_TASKS
            and depends_on is None
            and runs_after is None):
        existing_uuid = mariadb.find_existing_coalescible_op(
            operation_type=object_type.name.lower(),
            keys=_coalescible_keys(network_uuid),
            task_name=tasks[0].name)
        if existing_uuid is not None:
            LOG.with_fields({
                'existing_op_uuid': existing_uuid,
                'network_uuid': network_uuid,
                'task': tasks[0].name,
            }).info('Enqueue-side dedup: reusing existing pending op')

            # Returning early here means enqueue_cluster_operation() --
            # and with it the audit event it emits on the operation and
            # every object the metadata references -- never runs. Emit
            # the equivalent event ourselves so the reuse is visible in
            # the network's event stream rather than only in a daemon
            # log, mirroring the 'coalesced sibling ops' event the
            # worker-side fold emits on its survivor.
            eventlog.add_event_multi(
                EVENT_TYPE_AUDIT,
                [(object_type.name.lower(), existing_uuid),
                 ('network', str(network_uuid))],
                'enqueue-side dedup: reused pending op',
                extra={
                    'requested_task': tasks[0].name,
                    'existing_op_uuid': existing_uuid,
                    'op_type': object_type.name.lower(),
                })
            return object_type, existing_uuid

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
