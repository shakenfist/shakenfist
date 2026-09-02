import time
from enum import Enum
from types import ModuleType
from typing import Any, Optional, cast

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.operations.error_report import ErrorReport
from shakenfist.schema.operations.baseclusteroperation import PRIORITY


class BaseOperationException(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


class InvalidPriorityException(BaseOperationException):
    def __init__(self, task: str, priority: str) -> None:
        super().__init__(f'{priority} is not a valid priority')


class CannotDeferUnqueued(Exception):
    ...


class BaseOperation(dbo):
    # docs/developer_guide/state_machine.md has a description of these states.
    STATE_QUEUED = 'queued'
    STATE_PREFLIGHT = 'preflight'
    STATE_EXECUTING = 'executing'
    STATE_COMPLETE = 'complete'
    STATE_ABORT = 'abort'

    ACTIVE_STATES = {dbo.STATE_CREATED, STATE_QUEUED, STATE_EXECUTING,
                     STATE_COMPLETE}


def get_all_node_queues(node_uuid: str) -> list[str]:
    return (get_node_user_facing_node_queues(node_uuid) +
            get_general_user_facing_node_queues() +
            get_node_background_node_queues(node_uuid) +
            get_general_background_node_queues())


def get_all_user_facing_node_queues(node_uuid: str) -> list[str]:
    return (get_node_user_facing_node_queues(node_uuid) +
            get_general_user_facing_node_queues())


def get_all_background_node_queues(node_uuid: str) -> list[str]:
    return (get_node_background_node_queues(node_uuid) +
            get_general_background_node_queues())


def get_node_user_facing_node_queues(node_uuid: str) -> list[str]:
    return [
        f'{node_uuid}-clusteroperation-user_waiting',
        f'{node_uuid}-clusteroperation-user_facing',
        f'{node_uuid}-clusteroperation-user_facing_high_io'
    ]


def get_node_background_node_queues(node_uuid: str) -> list[str]:
    return [
        f'{node_uuid}-clusteroperation-background',
        f'{node_uuid}-clusteroperation-background_high_io'
    ]


def get_general_user_facing_node_queues() -> list[str]:
    return [
        'any-clusteroperation-user_waiting',
        'any-clusteroperation-user_facing',
        'any-clusteroperation-user_facing_high_io'
    ]


def get_general_background_node_queues() -> list[str]:
    return [
        'any-clusteroperation-background',
        'any-clusteroperation-background_high_io'
    ]


def get_all_network_queues() -> list[str]:
    return [
        'networknode-clusteroperation-user_waiting',
        'networknode-clusteroperation-user_facing',
        'networknode-clusteroperation-user_facing_high_io',
        'networknode-clusteroperation-background',
        'networknode-clusteroperation-background_high_io'
    ]


def get_node_network_queues(node_uuid: str) -> list[str]:
    return [
        f'{node_uuid}-network-user_waiting',
        f'{node_uuid}-network-user_facing',
        f'{node_uuid}-network-user_facing_high_io',
        f'{node_uuid}-network-background',
        f'{node_uuid}-network-background_high_io',
    ]


class BaseClusterOperation(BaseOperation):
    # Note that cluster operations are created in etcd transactions and don't
    # have .new() methods. They therefore jump straight to queued as an initial
    # state.

    # Tasks whose effect is idempotent against a single target -- i.e. running
    # one op suffices for any number of sibling ops with the same task on the
    # same target. The canonical example is ``network_apply_update_dnsmasq``:
    # the config is regenerated from current DB state, so 6 enqueued ops on
    # the same network produce the same final config as 1. Subclasses
    # override with a frozenset of their schema's ``model_tasks``; the base
    # default is empty (no coalescing).
    #
    # ``coalescible_key_columns`` names the indexed columns on the
    # ``cluster_operations`` table which together identify "the same
    # work" when finding sibling ops -- typically the foreign keys for
    # the target objects (``network_uuid``, ``instance_uuid``,
    # ``node_uuid``). A sibling has to match every column in the tuple,
    # so a two-column key is strictly narrower than a one-column one: a
    # key of ``('network_uuid', 'node_uuid')`` can tell one hypervisor's
    # work on a network apart from another's, which the network alone
    # cannot. The value for each column is read off the operation as an
    # attribute of the same name; an attribute which is ``None`` binds
    # as ``IS NULL``, which narrows the key rather than dropping it --
    # a cluster-wide NetOp has no ``node_uuid``, and folds only the
    # other operations which also have none.
    #
    # It must be non-empty whenever ``coalescible_tasks`` is non-empty;
    # both are read together by the dispatcher (worker-side fold) and by
    # ``create_and_enqueue`` (enqueue-side dedup). The *first* column is
    # also the operation's routing key in the sf-net dispatcher, and is
    # the object the fold's audit event is recorded against.
    coalescible_tasks: 'frozenset[Enum]' = frozenset()
    coalescible_key_columns: tuple[str, ...] = ()

    def execution_duration_extra(
            self,
            start_time: float,
            queue_name: Optional[str]
    ) -> dict[str, Any]:
        """Build the ``extra`` payload for the end-of-op event.

        One end-of-op event carries the queue-wait time, the execution
        duration and the coalescing instrumentation, because both
        dispatchers can see all of it here and a second event would
        double the eventlog cost on the critical path -- which
        profiling identified as the largest per-op overhead the
        queue-performance plan added.

        This lives on the operation rather than in either dispatcher
        because both of them emit it and
        ``tools/queue-wait-report.py`` reads one stream carrying
        events from both. Two copies of the field names is two chances
        for them to drift apart, and a report which silently sees a
        field from only one dispatcher understates whatever it
        measures.

        ``wait_seconds`` and its companions are only populated when
        the op carries a ``created_at`` -- that is, when it was loaded
        from the cluster_operations table rather than constructed
        in-memory outside the dispatch path.
        """
        extra: dict[str, Any] = {'seconds': time.time() - start_time}
        if self.created_at is not None:
            extra['wait_seconds'] = start_time - self.created_at
            extra['defer_count'] = self.current_defer_count
            extra['queue_name'] = queue_name

        if self.coalesce_outcome is not None:
            extra['coalesce_outcome'] = self.coalesce_outcome
        if self.coalesce_seconds is not None:
            extra['coalesce_seconds'] = self.coalesce_seconds
        if self.coalesce_folded is not None:
            extra['coalesce_folded'] = self.coalesce_folded
        return extra

    def _coalescible_target_reference(
            self
    ) -> Optional[tuple[str, str]]:
        """The (object_type, uuid) this operation coalesces against.

        Returns None when the operation declares no coalescing key,
        when the first key column is unset on this instance, or when
        the schema does not say which object type that column names.

        Only the *first* key column is used, deliberately. The event
        this reference is for has to outlive the operation -- which is
        hard deleted thirty seconds after going terminal (#3864) -- and
        the object an operator queries afterwards is the network, not
        the node the work happened to run on.

        The object type is resolved through the schema model's
        ``target_fields`` map -- the same declaration
        ``schema/operations/util.py:enqueue_cluster_operation`` uses to
        write ``cluster_operation_targets`` rows. Nothing is added to
        that map for the extra key columns: a column added there starts
        writing a ``cluster_operation_targets`` row for every operation
        and changes what ``has_pending_cluster_operation()`` reports.
        """
        key_columns = type(self).coalescible_key_columns
        if not key_columns:
            return None
        target_column = key_columns[0]

        target_uuid = getattr(self, target_column, None)
        if target_uuid is None:
            return None

        model_class = getattr(self._schema, 'model', None)
        target_fields = getattr(model_class, 'target_fields', {})
        target_object_type = target_fields.get(target_column)
        if target_object_type is None:
            return None

        return (str(target_object_type), str(target_uuid))

    @classmethod
    def _db_get(cls, object_uuid: str) -> Optional[dict[str, Any]]:
        o = mariadb.get_cluster_operation(object_uuid)
        if not o:
            return None

        if o.get('version', 0) != cls.current_version:
            if not cls.upgrade_supported:
                raise exceptions.BadObjectVersion(
                    f'Unsupported object version - '
                    f'{cls.object_type}: {o}')
        return o

    state_targets: dict[Optional[str], Optional[tuple[str, ...]]] = {  # type: ignore[assignment]
        None: (BaseOperation.STATE_QUEUED,),
        BaseOperation.STATE_QUEUED: (BaseOperation.STATE_EXECUTING,
                                     dbo.STATE_DELETED, dbo.STATE_ERROR,
                                     BaseOperation.STATE_ABORT),
        BaseOperation.STATE_EXECUTING: (BaseOperation.STATE_COMPLETE,
                                        dbo.STATE_DELETED, dbo.STATE_ERROR,
                                        BaseOperation.STATE_ABORT,
                                        BaseOperation.STATE_QUEUED),
        BaseOperation.STATE_COMPLETE: (dbo.STATE_DELETED,),
        BaseOperation.STATE_ABORT: (dbo.STATE_DELETED,),
        dbo.STATE_ERROR: (dbo.STATE_DELETED,),
        dbo.STATE_DELETED: None,
    }

    def hard_delete(self) -> None:
        # Clean up the cluster_operations row, the
        # cluster_operation_errors row (if any), and the per-op
        # cluster_operation_targets rows so none of these tables grow
        # unbounded as the cleaner sweeps terminal-state operations.
        # ``delete_cluster_operation_error`` is idempotent — ops that
        # never failed have no row to remove.
        #
        # The order matters: delete targets first so any concurrent
        # reader of cluster_operation_targets observes the absence and
        # skips this op, rather than dereferencing a stale target row
        # whose op has already gone. The reader is still expected to
        # tolerate a torn read (see scheduled_tasks._process_per_blob_queue,
        # which passes suppress_failure_audit=True for this reason),
        # but ordering removes the easy half of the race.
        uuid_str = str(self.uuid)
        mariadb.delete_cluster_operation_target(uuid_str)
        mariadb.delete_cluster_operation_error(uuid_str)
        mariadb.delete_cluster_operation(uuid_str)
        super().hard_delete()

    def __init__(
            self,
            static_values: dict[str, Any],
            schema: ModuleType
    ) -> None:
        super().__init__(static_values['uuid'], static_values.get('version'))
        self.__priority = PRIORITY[static_values['priority']]
        self.__request_id: Optional[str] = static_values.get('request_id')
        self.__depends_on: Optional[list[dict[str, Any]]] = \
            static_values.get('depends_on')
        self.__runs_after: Optional[list[dict[str, Any]]] = \
            static_values.get('runs_after')

        # ``created_at`` is overlaid onto the static_values dict by
        # ``_cluster_operation_row_to_dict`` in shakenfist.mariadb and is the
        # canonical "this op was first inserted" timestamp. It is the
        # baseline the dispatcher subtracts from execute-start time to
        # report queue-wait latency in the per-op "started executing"
        # event. ``None`` is tolerated for backwards compatibility with
        # any in-flight payload written before the column existed; the
        # dispatcher just skips emitting wait_seconds in that case.
        self.__created_at: Optional[float] = static_values.get('created_at')

        # The schema module this operation was built from. Kept so
        # ``BaseClusterOperation`` can read ``model.target_fields`` and
        # resolve which object a coalescible target column points at,
        # rather than hard-coding one object type per operation class.
        self._schema: ModuleType = schema

        # We only know this if we have been dequeued
        self._queue_name: Optional[str] = None

        # Set by the queue dispatcher from the work_item payload so that
        # defer_with_backoff() knows how many times this operation has
        # already been deferred. Each defer() bumps the counter that gets
        # written into the next work_item.
        self.current_defer_count: int = 0

        # Set by the queue dispatcher to the number of items it dequeued in
        # the same batch as this op. ``None`` means the op was loaded
        # outside the queue dispatch path (e.g. from a unit test or a REST
        # endpoint). The cross-op coalescing branch in ``execute()`` reads
        # this to skip the ``claim_coalescible_siblings`` SQL when the
        # dispatcher just observed an empty queue (``batch == 1`` -- this
        # op is the only ready item), since no sibling could possibly be
        # ready to fold. A new sibling enqueued after our dequeue will be
        # picked up by the next dispatcher cycle with a batch >= 2, where
        # the fold will run.
        self.dispatcher_batch_size: Optional[int] = None

        # Coalescing instrumentation, read by both dispatchers after
        # execute() returns and reported on the per-op 'execution
        # duration' event. ``coalesce_outcome`` says whether the
        # cross-op fold ran or which guard skipped it, because a
        # measurement which cannot tell "the fold ran and found
        # nothing" from "the fold never ran" is how #3878 stayed
        # invisible for three months: zero looked exactly like
        # disabled. ``coalesce_seconds`` is the cost of the
        # claim_coalescible_siblings call, which baseoperation asserted
        # to be ~200 ms under load until phase 9 measured it at a 3.7 ms
        # median over 42 hours of sfcbr.
        # It is measured with a monotonic clock: it is a pure interval,
        # never differenced against a stored timestamp the way
        # ``wait_seconds`` is, so an NTP step during the call must not
        # be able to turn it into a negative number in the report.
        self.coalesce_outcome: Optional[str] = None
        self.coalesce_seconds: Optional[float] = None
        self.coalesce_folded: Optional[int] = None

        # Convert tasks names back into enum entries
        self.__tasks: list[Enum] = []
        for task_name in static_values['tasks']:
            try:
                self.__tasks.append(schema.model_tasks[task_name])
            except KeyError as e:
                self.state = self.STATE_ERROR  # type: ignore[misc]
                self.add_event(
                    EVENT_TYPE_AUDIT, 'unknown task {task_name}: {e}')
                raise e

    @property
    def priority(self) -> PRIORITY:
        return self.__priority

    @property
    def created_at(self) -> Optional[float]:
        return self.__created_at

    @property
    def request_id(self) -> Optional[str]:
        return self.__request_id

    @property
    def depends_on(self) -> list[dict[str, Any]]:
        if not self.__depends_on or len(self.__depends_on) == 0:
            return []
        return self.__depends_on

    @property
    def runs_after(self) -> list[dict[str, Any]]:
        if not self.__runs_after or len(self.__runs_after) == 0:
            return []
        return self.__runs_after

    @property
    def queue_name(self) -> Optional[str]:
        return self._queue_name

    @queue_name.setter
    def queue_name(self, name: str) -> None:
        self._queue_name = name

    @property
    def tasks(self) -> list[Enum]:
        return self.__tasks

    # Methods
    def external_view(self) -> dict[str, Any]:
        tasks = []
        for t in self.__tasks:
            tasks.append(t.name)

        return {
            'operation_type': self.object_type,
            'uuid': str(self.uuid),
            'state': self.state.value,
            'tasks': tasks
        }

    def execute(self) -> None:
        self.state = BaseClusterOperation.STATE_EXECUTING  # type: ignore[misc]

        # Coalescing happens here at the entry to execute() rather than
        # in the dispatcher because both sf-net and sf-queues invoke
        # this method -- doing it once here keeps the behaviour uniform
        # across all op types and both daemons. The two passes are:
        #
        # 1. Within-job: if our own task list repeats a coalescible task,
        #    drop the duplicates. Today's enqueue sites mostly send a
        #    unique task per op so this is defensive, but step 6 of the
        #    plan may surface call sites that legitimately send
        #    ``[update_dnsmasq, update_dnsmasq, ...]``.
        # 2. Cross-op: ask MariaDB to fold any *other* pending ops on
        #    the same target whose entire task list is a single
        #    coalescible task we're about to run. Their state gets
        #    transitioned to ``complete``; when their work_queue row
        #    eventually surfaces the dispatcher's terminal-state branch
        #    drops it cleanly. Logged as a single "coalesced sibling
        #    ops" status event on the survivor and on its coalescing
        #    target.
        coalescible = type(self).coalescible_tasks
        key_columns = type(self).coalescible_key_columns

        if coalescible:
            unique_tasks: list[Enum] = []
            seen_coalescible: set[Enum] = set()
            for t in self.tasks:
                if t in coalescible:
                    if t in seen_coalescible:
                        self.add_event(
                            EVENT_TYPE_STATUS,
                            'within-job: dropped duplicate coalescible task',
                            extra={'task': t.name})
                        continue
                    seen_coalescible.add(t)
                unique_tasks.append(t)
        else:
            unique_tasks = list(self.tasks)

        # Skip the cross-op fold when the dispatcher just dequeued only us
        # (``dispatcher_batch_size == 1``). The ``claim_coalescible_siblings``
        # call almost always returns empty in the uncontended case, so
        # skipping a query which cannot help is free.
        #
        # This guard used to be justified by a "~200 ms under load" cost
        # taken from a CI bundle profile. Phase 9 measured the call on
        # sfcbr over 42 hours and found a 3.7 ms median, a 5.2 ms p90 and a
        # 149.5 ms maximum -- the old figure was measured while #3878 made
        # every one of those queries unmatchable, so it was timing the
        # wrong thing. Keep the guard for tidiness; do not justify anything
        # else with the cost of this call without measuring it again. See
        # "What step 9 measured" in docs/plans/PLAN-queue-performance.md.
        #
        # A ``None`` value (e.g. op loaded outside the queue path) is
        # treated as "we don't know, be conservative" and the fold runs.
        skip_due_to_empty_queue = self.dispatcher_batch_size == 1

        # Skip the cross-op fold when the coalescing key cannot tell
        # this operation's work apart from work on another queue. The
        # fold's SQL is blind to queues -- ``cluster_operations`` has no
        # queue column -- so the key is what has to do it, and there are
        # exactly two ways it can:
        #
        #   * A cluster-wide ``networknode-*`` queue is drained by a
        #     single elected worker, so any sibling the fold finds is one
        #     this same worker would otherwise have processed itself.
        #   * A key which names ``node_uuid`` matches only operations
        #     carrying that node's uuid, every one of which was enqueued
        #     to that node's own ``{node_uuid}-network-*`` queue, drained
        #     by exactly one dispatcher process, within which every
        #     operation for the same network hashes to one worker thread.
        #     The full argument, and why it does not extend to
        #     ``sf-queues``, is the PARTITIONED-WORKER SAFETY INVARIANT
        #     comment in ``shakenfist/daemons/network/workitem.py``.
        #
        # Without one of those, folding marks a sibling on a *different*
        # node's queue complete and leaves that node's apply work undone.
        # That is the bug which broke
        # ``test_single_virtual_networks_work`` on the network-facade
        # branch: with ``Network.ensure_mesh`` fanned out to every
        # participating hypervisor, hypervisor A's worker folded away
        # hypervisor B's pending ensure_mesh and B's FDB was never
        # updated.
        #
        # The node branch tests the queue name as well as the key, and
        # both halves of that name matter. ``enqueue_cluster_operation``
        # builds it as ``{target}-{family}-{priority}``, so a per-node
        # operation on the ``network`` family goes to
        # ``{node_uuid}-network-*``, drained only by that node's
        # net-worker -- which is links (ii) and (iii) of the argument
        # above. The default family is ``clusteroperation``, whose
        # per-node queues are drained by sf-queues instead, and
        # sf-queues starts one worker per claimed item with no routing
        # key at all: link (iv) simply does not exist there. A key which
        # names ``node_uuid`` is necessary for the fold to be safe but
        # is not sufficient, so do not reduce this to a test of the key.
        queue_is_cluster_wide = (
            self.queue_name is not None
            and self.queue_name.startswith('networknode-'))
        node_uuid = getattr(self, 'node_uuid', None)
        key_distinguishes_node = (
            'node_uuid' in key_columns
            and node_uuid is not None
            and self.queue_name is not None
            and self.queue_name.startswith(f'{node_uuid}-network-'))
        key_distinguishes_queue = (
            queue_is_cluster_wide or key_distinguishes_node)

        # Each branch records the guard which decided the outcome, so
        # the guards exist exactly once and the instrumentation cannot
        # report an outcome the code did not take -- which is the class
        # of invisible wrongness this phase exists to remove. See
        # decision 4 of
        # PLAN-queue-performance-phase-09-prove-coalescing.md.
        #
        # 'type_not_coalescible' and 'no_coalescible_tasks' are
        # deliberately different outcomes. The first is "this operation
        # type declares no coalescing at all", which is every cluster
        # operation that is not a NetOp and so the overwhelming
        # majority of them; the second is "this job could have
        # coalesced and happened not to contain anything coalescible".
        # Merging them buries the interesting case under the boring one
        # in the by-queue-class table, and decision 4's whole aim is
        # that "coalescing is doing nothing" stays answerable.
        if not coalescible or not key_columns:
            self.coalesce_outcome = 'type_not_coalescible'
        elif skip_due_to_empty_queue:
            self.coalesce_outcome = 'batch_size_one'
        elif not key_distinguishes_queue:
            self.coalesce_outcome = 'key_cannot_distinguish_queue'
        else:
            survivor_coalescible_tasks = [
                t for t in unique_tasks if t in coalescible]

            if not survivor_coalescible_tasks:
                self.coalesce_outcome = 'no_coalescible_tasks'
            else:
                # Every declared column goes into the key, including the
                # ones which are unset on this operation: ``None`` binds
                # as ``IS NULL``, which is a *narrower* key rather than
                # an absent one. A cluster-wide NetOp has no
                # ``node_uuid``, so it folds only the other cluster-wide
                # operations on its network, exactly as a per-node
                # operation folds only its own node's. Abandoning the
                # fold instead would switch off the only coalescing the
                # cluster currently does the moment the key widens --
                # see decision 8 of
                # docs/plans/PLAN-queue-performance-phase-11-multi-column-key.md
                # A column with no value *at all* is caught upstream, by
                # ``_coalescible_keys``'s ``KeyError``.
                keys: list[tuple[str, Optional[str]]] = []
                for column in key_columns:
                    value = getattr(self, column, None)
                    keys.append(
                        (column, None if value is None else str(value)))

                # Recorded before the call, not after it. If
                # claim_coalescible_siblings raises, a caller which
                # catches and continues would otherwise emit an event
                # with no outcome at all, which the report reads as
                # "from a build predating the instrumentation" rather
                # than as a fold which was attempted.
                self.coalesce_outcome = 'ran'
                coalesce_start = time.monotonic()
                folded = mariadb.claim_coalescible_siblings(
                    operation_type=self.object_type,
                    keys=keys,
                    task_names=[
                        t.name for t in survivor_coalescible_tasks],
                    exclude_op_uuid=str(self.uuid))
                self.coalesce_seconds = time.monotonic() - coalesce_start
                self.coalesce_folded = len(folded)
                if folded:
                    # Emitted against the coalescing target as well as
                    # the operation. An operation is hard deleted thirty
                    # seconds after it reaches a final state and takes
                    # its event_objects rows with it (#3864), so an
                    # event recorded only against the survivor is
                    # unreadable within a minute of the fold happening
                    # -- which is why nothing noticed this event had
                    # never fired at all (#3878). The target outlives
                    # the operation, so the same event on the target is
                    # still there to be queried and asserted on. The
                    # enqueue-side dedup emits against both for the same
                    # reason; see ``net_op.create_and_enqueue``.
                    references: list[Any] = [
                        (str(self.object_type), str(self.uuid))]
                    target_reference = self._coalescible_target_reference()
                    if target_reference:
                        references.append(target_reference)

                    if not self.in_memory_only:
                        eventlog.add_event_multi(
                            EVENT_TYPE_STATUS,
                            references,
                            'coalesced sibling ops',
                            extra={
                                'sibling_count': len(folded),
                                'sibling_uuids': folded,
                                'tasks': [
                                    t.name
                                    for t in survivor_coalescible_tasks],
                            })

        for t in unique_tasks:
            self.dispatch_task(t)  # type: ignore[attr-defined]
            if self.state.value in [BaseClusterOperation.STATE_ABORT,
                                    BaseClusterOperation.STATE_DELETED,
                                    BaseClusterOperation.STATE_ERROR,
                                    BaseClusterOperation.STATE_QUEUED]:
                return
        self.state = BaseClusterOperation.STATE_COMPLETE  # type: ignore[misc]

    def defer(
            self,
            waiting_on: Optional[list[dbo]] = None,
            delay: float = 15.0
    ) -> None:
        if not self.queue_name:
            raise CannotDeferUnqueued(
                'You cannot defer a cluster operation which has not been '
                'queued')

        # Re-enqueue this operation for a retry after delay seconds
        wo: Optional[list[tuple[str, str]]] = None
        if waiting_on:
            wo = []
            for wobj in waiting_on:
                wo.append(wobj.unique_label())

        next_defer_count = self.current_defer_count + 1
        self.add_event(
            EVENT_TYPE_STATUS, f'Execution deferred for {delay} seconds',
            extra={
                'waiting_on': wo,
                'defer_count': next_defer_count,
                'delay': delay
            })
        work_item = {
            'operation_type': self.object_type,
            'operation_uuid': str(self.uuid),
            'defer_count': next_defer_count
        }
        mariadb.enqueue_work_item(
            self.queue_name, work_item, delay=delay)
        self.state = self.STATE_QUEUED  # type: ignore[misc]

    def defer_with_backoff(
            self,
            delays: tuple[int, ...] = (15, 30, 60),
            reason: Optional[str] = None
    ) -> bool:
        """Schedule a retry of this operation with backoff.

        Uses self.current_defer_count to look up the next delay from
        delays. Returns True if a retry was scheduled (and the op is
        now back in STATE_QUEUED), or False if the retry budget has
        been exhausted (in which case the caller should error the op
        out itself).
        """
        if self.current_defer_count >= len(delays):
            self.add_event(
                EVENT_TYPE_STATUS,
                'retry budget exhausted, will not defer further',
                extra={
                    'defer_count': self.current_defer_count,
                    'max_defers': len(delays),
                    'reason': reason
                })
            return False

        delay = delays[self.current_defer_count]
        self.add_event(
            EVENT_TYPE_STATUS,
            'scheduling retry after transient failure',
            extra={
                'defer_count': self.current_defer_count + 1,
                'max_defers': len(delays),
                'delay': delay,
                'reason': reason
            })
        self.defer(delay=delay)
        return True

    def is_outstanding(self) -> bool:
        if self.state.value in [BaseClusterOperation.STATE_ERROR,
                                BaseClusterOperation.STATE_DELETED,
                                BaseClusterOperation.STATE_ABORT,
                                BaseClusterOperation.STATE_COMPLETE]:
            return False
        return True

    @property
    def error_report(self) -> Optional[ErrorReport]:
        """The structured ErrorReport persisted for this op, if any.

        Reads fresh from MariaDB on every access -- no caching --
        because the worker may persist the report after this object
        was loaded from the database. Returns ``None`` if no report
        has been written (the common case for COMPLETE/ABORT ops).
        """
        return mariadb.get_cluster_operation_error(self.uuid)

    def raise_for_error(self, timeout: Optional[float] = None) -> None:
        """Block until terminal, then raise on ERROR.

        Polls until this operation reaches a terminal state (bounded
        by ``timeout`` -- defaults to ``config.API_ASYNC_WAIT``). On
        ``STATE_ERROR`` raises :class:`NetworkOperationFailed` carrying
        the persisted :class:`ErrorReport`. On any other terminal
        state (``COMPLETE``, ``ABORT``, ``DELETED``) returns silently;
        callers that need to distinguish ``ABORT`` from ``COMPLETE``
        inspect ``op.state.value`` explicitly. Raises
        :class:`OperationTimeout` if the deadline elapses without a
        terminal transition.
        """
        refreshed = poll_until_terminal(self, timeout)
        if refreshed.state.value == dbo.STATE_ERROR:
            report = refreshed.error_report
            if report is None:
                # Fallback: error state with no persisted report. This
                # shouldn't happen in practice -- the dispatcher writes
                # the report before flipping state to ERROR -- but a
                # race or an older worker could leave the row missing.
                # Build a minimal report so callers still get a
                # structured failure to branch on.
                report = ErrorReport(
                    code='internal.unknown',
                    message=(
                        f'operation {refreshed.uuid} ended in error '
                        f'state'),
                    details={},
                    origin_class='',
                    traceback='',
                )
            raise exceptions.NetworkOperationFailed(report)


def poll_until_terminal(
        op: 'BaseClusterOperation',
        timeout: Optional[float] = None) -> 'BaseClusterOperation':
    """Block until ``op`` reaches a terminal state, then return it refreshed.

    Polls ``cls.from_db(op.uuid)`` (where ``cls`` is the concrete op
    type) at a 0.1 second cadence so each iteration sees the freshest
    state written by the worker. The terminal state set is
    ``{STATE_COMPLETE, STATE_ABORT, STATE_DELETED, STATE_ERROR}``.

    ``timeout`` defaults to ``config.API_ASYNC_WAIT`` (60 seconds) when
    ``None`` is passed; callers can override for long-running ops.
    Raises :class:`OperationTimeout` if the deadline elapses without
    observing a terminal state.
    """
    if timeout is None:
        timeout = config.API_ASYNC_WAIT
    terminal_states = {
        BaseClusterOperation.STATE_COMPLETE,
        BaseClusterOperation.STATE_ABORT,
        dbo.STATE_DELETED,
        dbo.STATE_ERROR,
    }
    cls = type(op)
    deadline = time.time() + timeout
    while True:
        refreshed = cls.from_db(op.uuid)
        if refreshed is not None and refreshed.state.value in terminal_states:
            return cast('BaseClusterOperation', refreshed)
        if time.time() >= deadline:
            raise exceptions.OperationTimeout(
                f'operation {op.uuid} did not reach terminal state '
                f'within {timeout}s')
        time.sleep(0.1)
