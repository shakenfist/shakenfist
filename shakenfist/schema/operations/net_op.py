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
current_version = 3


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


# Tasks for which N pending ops sharing a coalescing key can be
# folded into a single execution by the worker (step 4) or skipped at
# enqueue time (step 5):
#
# * ``network_apply_update_dnsmasq`` regenerates the dnsmasq config
#   from current DB state. Six instance starts on the same network
#   each enqueue one of these; the final config covers every lease
#   regardless of how many runs landed.
# * ``network_apply_create_network_node`` is idempotent network-node
#   setup -- running it twice in a row leaves the same state as
#   running it once.
# * ``network_ensure_mesh`` diffs **this** host's FDB against the
#   current set of participating hypervisors, so a later run subsumes
#   an earlier one on the same host.
#
# The remaining tasks are *not* coalescible -- they carry per-op
# parameters (specific floating IPs, specific mac/ip lease pairs)
# or are order-sensitive against an opposite task (remove vs
# update / delete vs create).
#
# ``network_ensure_mesh`` is the one NetOp task which does *node-local*
# work, and it is in this set only because the key below names
# ``node_uuid`` and because of where its per-node enqueues are routed.
# Both conditions are load bearing, and neither alone is enough.
#
# The key half. The fold's SQL is blind to queues --
# ``cluster_operations`` has no queue column -- so the key has to
# distinguish them instead. ``(network_uuid, node_uuid)`` does: a
# cluster-wide operation carries ``node_uuid = None``, which binds
# ``IS NULL`` and therefore matches only the other cluster-wide
# operations, while a per-node operation matches only operations
# carrying its own node's uuid. With the network alone -- the key
# before #3884 -- hypervisor A's mesh op and hypervisor B's were
# indistinguishable, so A's survivor marked B's op ``complete``
# without doing B's work and left B's FDB stale. That is the
# ``test_single_virtual_networks_work`` failure described in
# ``BaseClusterOperation.execute``.
#
# The routing half. Every per-node enqueue of ``network_ensure_mesh``
# goes to ``family='network'`` -- ``Network.ensure_mesh`` and both
# sites in the network maintainer pass it -- and that family is what
# puts the operation on ``{node_uuid}-network-*``, drained by that
# node's own sf-net dispatcher, within which every operation for a
# network hashes to a single worker thread. So a fold can never mark
# complete an operation another thread is executing. The default
# ``clusteroperation`` family routes per-node queues to sf-queues
# instead, which starts one worker per claimed item with no routing
# key at all: two of its workers can hold two operations for the same
# (network, node) at once, and one's fold can flip the other's
# operation to ``complete`` mid-dequeue. A coalescible task on that
# family would be unsafe however wide the key. Both guards refuse it
# -- ``InvalidCoalescibleEnqueue`` below at enqueue time, and
# ``BaseClusterOperation.execute``'s ``key_cannot_distinguish_queue``
# at fold time -- but the reason they do is here. The argument in
# full, and why it stops at sf-net, is the PARTITIONED-WORKER SAFETY
# INVARIANT comment in ``shakenfist/daemons/network/workitem.py``.
COALESCIBLE_TASKS: frozenset[model_tasks] = frozenset({
    model_tasks.network_apply_update_dnsmasq,
    model_tasks.network_apply_create_network_node,
    model_tasks.network_ensure_mesh,
})

# The coalescing key: indexed columns on the ``cluster_operations``
# table which together identify "the same work". Used by both the
# enqueue-side dedup lookup (this module) and the worker-side fold
# (``NetOp.coalescible_key_columns``, which reads this constant), so
# the two can never disagree about what a sibling is. A candidate op
# must match every column.
#
# For NetOp that is the network and the node the operation was
# targeted at. Every op is scoped to exactly one network;
# ``node_uuid`` is the node uuid for a per-node enqueue and ``None``
# for the cluster-wide network-node queue, where it binds ``IS NULL``
# rather than being dropped from the key. The result is strictly
# narrower than the network alone in both directions -- cluster-wide
# operations fold only each other, a hypervisor's node-local work
# folds only its own -- which is what lets ``network_ensure_mesh``
# into COALESCIBLE_TASKS above.
COALESCIBLE_KEY_COLUMNS: tuple[str, ...] = ('network_uuid', 'node_uuid')


def _coalescible_keys(
        network_uuid, node_uuid) -> list[tuple[str, Optional[str]]]:
    """Build the coalescing key for one enqueue.

    One ``(column, value)`` pair per column of
    ``COALESCIBLE_KEY_COLUMNS``, so the enqueue-side dedup below and
    the worker-side fold in ``BaseClusterOperation.execute`` ask the
    database exactly the same question. A column added to the tuple
    without a value here raises ``KeyError`` at the call site rather
    than silently narrowing or widening the key.

    Values are gathered into a dict rather than built positionally so
    the column tuple can be changed in one place without threading a
    new value through every caller. A ``None`` value is a decision, not
    an omission: it binds ``IS NULL`` in both queries, which is how the
    cluster-wide operations fold only each other.
    """
    values = {
        'network_uuid': str(network_uuid),
        'node_uuid': None if node_uuid is None else str(node_uuid),
    }
    return [(column, values[column]) for column in COALESCIBLE_KEY_COLUMNS]


def _reusable_priorities(priority: PRIORITY) -> list[str]:
    """The priorities an enqueue at ``priority`` may reuse an op from.

    Reuse is one-sided. Adopting a pending op which is *more* urgent
    than the caller asked for costs nothing -- the work runs sooner --
    but adopting a less urgent one means adopting its queue.
    ``enqueue_cluster_operation`` names queues
    ``{target}-{family}-{priority}``, so the two sit on different
    queues entirely, and the caller does not merely lose its lane: the
    waiters in ``raise_for_error()`` and the ``runs_after`` dependency
    an instance start hangs off both then block on the slower lane's
    queue-sit tail, which phase 10 measured as the deep one.

    That is not hypothetical for either coalescible task which has two
    enqueue sites. ``Network.ensure_mesh`` enqueues
    ``network_ensure_mesh`` at ``user_facing`` while the network
    maintainer enqueues the same task, for the same network and the
    same node, at ``background`` whenever ``is_mesh_okay()`` reports
    drift -- which is the state a network is in immediately after an
    instance starts elsewhere on it. ``create_on_network_node`` and the
    maintainer straddle ``user_facing``/``background`` for
    ``network_apply_create_network_node`` in the same way, and have
    since before the mesh task became coalescible.

    ``PRIORITY``'s values are ordered, lowest most urgent, so "at least
    as urgent" is a numeric comparison. The result is member *names*,
    because ``cluster_operations.priority`` stores the name.
    """
    return [p.name for p in PRIORITY if p.value <= priority.value]


class model(BaseModel):
    # ``node_uuid`` is deliberately absent from this map, even though
    # it is a model field. ``enqueue_cluster_operation``
    # (``shakenfist/schema/operations/util.py``) iterates
    # ``target_fields`` to write ``cluster_operation_targets`` rows, so
    # listing it here would start recording a NODE target row for every
    # per-node NetOp -- changing what ``has_pending_cluster_operation()``
    # reports for a node and growing a table the cleaner has to keep up
    # with. The coalescing key is declared separately, in
    # ``COALESCIBLE_KEY_COLUMNS``, precisely so it does not have to
    # borrow this map.
    target_fields: ClassVar[dict[str, ObjectType]] = {
        'network_uuid': ObjectType.NETWORK,
    }

    uuid: UUID4
    network_uuid: UUID4
    # The node this operation was targeted at, or None for the
    # cluster-wide network-node queue. Recorded so the fold's key can
    # tell one hypervisor's node-local work apart from another's;
    # ``cluster_operations`` already has an indexed ``node_uuid``
    # column which ``_direct_create_and_enqueue_cluster_operation``
    # populates from the metadata dict, so no migration is involved.
    node_uuid: Optional[UUID4] = None
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
    # ``target`` is queue routing, but for the per-node ``network``
    # family it *is* the node uuid -- ``Network.ensure_mesh`` and the
    # network maintainer both pass a node uuid there. So the value the
    # coalescing key needs is already in hand, and deriving it here is
    # strictly better than adding a ``node_uuid`` parameter: a caller
    # who forgot to pass one would silently produce an operation whose
    # key degrades to the network alone, and the fold would then match
    # it against another node's work. There is no way to forget this.
    node_uuid = None if target == 'networknode' else target

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
    # (op_type, COALESCIBLE_KEY_COLUMNS, task, state), and
    # ``cluster_operations`` has no queue column to filter on. The key
    # therefore has to do the queue's job, and it only does so for a
    # per-node enqueue if it names ``node_uuid``: without that column
    # hypervisor A's op and hypervisor B's op are identical to both
    # dedup paths while doing different work on different hosts, so
    # folding them leaves one host's state unapplied. That is the bug
    # which broke ``test_single_virtual_networks_work`` on the
    # network-facade branch.
    #
    # So the rule is not "coalescible tasks only on the cluster-wide
    # queue" but "coalescible tasks only where the key distinguishes
    # the target, on a dispatcher which partitions by it". A
    # ``networknode`` enqueue qualifies because one elected worker
    # drains that queue; a per-node enqueue qualifies when ``node_uuid``
    # is in the key, this operation has one, and the enqueue is on the
    # ``network`` family so sf-net drains it. (``None`` is a legitimate
    # key value -- it binds ``IS NULL`` and is how the cluster-wide ops
    # fold only each other -- but a NULL node cannot distinguish one
    # hypervisor from another, which is why the per-node case tests the
    # value as well as the column.)
    #
    # Rather than leave that as a convention nobody checks, enforce it
    # here. It costs a set intersection per enqueue and turns a silent
    # class of cross-node state corruption into an immediate, loud
    # failure at the call site that introduced it.
    if target != 'networknode':
        # The key has to name node_uuid, this operation has to have
        # one, and the enqueue has to be going to a dispatcher which
        # partitions its workers by target. That last condition is the
        # ``family``: ``network`` routes to sf-net, which hashes every
        # operation for a network to one worker thread, while the
        # default ``clusteroperation`` routes to sf-queues, which does
        # not partition at all. A node-aware key on a sf-queues queue
        # would let two workers on the same node hold two operations
        # for the same (network, node) at once -- see decision 5 of the
        # phase 11 plan.
        key_distinguishes_target = (
            'node_uuid' in COALESCIBLE_KEY_COLUMNS
            and node_uuid is not None
            and family == 'network')
        misrouted = [t.name for t in tasks if t in COALESCIBLE_TASKS]
        if misrouted and not key_distinguishes_target:
            raise exceptions.InvalidCoalescibleEnqueue(
                f'net_op tasks {misrouted} are declared coalescible, but the '
                f'coalescing key {COALESCIBLE_KEY_COLUMNS} cannot '
                f'distinguish this enqueue\'s target {target!r} from another '
                f'node\'s work. Either drop the task from '
                f'COALESCIBLE_TASKS, or add node_uuid to '
                f'COALESCIBLE_KEY_COLUMNS so the fold can tell the two '
                f'apart, and route the enqueue to the network family so '
                f'sf-net drains it. A node-aware key is only sound on a '
                f'dispatcher which partitions its workers by target: sf-net '
                f'does, sf-queues does not (see the phase 11 plan, decision '
                f'5, and the successor issue it names for the sf-queues '
                f'half). This enqueue is on the {family!r} family.')

    if (len(tasks) == 1
            and tasks[0] in COALESCIBLE_TASKS
            and depends_on is None
            and runs_after is None):
        try:
            existing_uuid = mariadb.find_existing_coalescible_op(
                operation_type=object_type.name.lower(),
                keys=_coalescible_keys(network_uuid, node_uuid),
                task_name=tasks[0].name,
                priorities=_reusable_priorities(priority))
        except exceptions.CoalescingUnavailable:
            # A rolling upgrade against an sf-database predating the V2
            # coalescing RPCs. Insert a new op, which is what a missed
            # dedup always does; the worker-side fold catches the
            # duplicate later if it is still there, and if that is
            # unavailable too the task simply runs twice. Every
            # coalescible task is idempotent by the definition of being
            # coalescible, so the cost is duplicated work for the
            # length of the upgrade and nothing else.
            existing_uuid = None
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
            node_uuid=node_uuid,
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
            'node_uuid': node_uuid,
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
