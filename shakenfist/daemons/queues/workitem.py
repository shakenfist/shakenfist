import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.constants import get_object_class
from shakenfist.daemons import daemon
from shakenfist import mariadb
from shakenfist.exceptions import DatabaseUnavailable
from shakenfist.exceptions import StateWriteFailed
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import DEPENDENCY_DEFER_BUDGET
from shakenfist.operations.baseoperation import DEPENDENCY_FAILED_STATES
from shakenfist.operations.baseoperation import DEPENDENCY_PENDING_STATES
from shakenfist.schema.operations.baseclusteroperation import TERMINAL_OPERATION_STATES
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


# Dependency-wait defer backoff. Most dependency waits clear on the first
# retry, so the previous flat 15 second re-poll dominated instance start
# latency on the user_waiting queues (issue 3863: node_inst_netdesc_op wait
# p50 of 15.78s against 0.77s for never-deferred ops). The schedule matches
# sf-net's (shakenfist/daemons/network/workitem.py), but where sf-net tracks
# back-off depth in a per-worker map, sf-queues jobs are one-shot threads --
# so the depth is derived from the defer_count the work item already
# persists across defers.
INITIAL_DEFER_DELAY = 0.1
MAX_DEFER_DELAY = 15.0
DEFER_DELAY_MULTIPLIER = 2.0


def dependency_defer_delay(defer_count: int) -> float:
    """The defer delay for an operation which has already deferred
    defer_count times: 0.1s doubling to a 15s cap."""
    # Exponent clamp so a pathologically large defer_count cannot overflow
    # float pow; anything past the clamp is far beyond the cap anyway.
    exponent = min(defer_count, 64)
    return min(INITIAL_DEFER_DELAY * (DEFER_DELAY_MULTIPLIER ** exponent),
               MAX_DEFER_DELAY)


class Job(util_concurrency.Job):
    def __init__(self, queue_name, jobname, workitem, batch_size=None):
        super().__init__()

        self.queue_name = queue_name
        self.jobname = jobname
        self.workitem = workitem
        # Size of the dispatcher's dequeue batch that delivered this
        # job. Propagated onto ``op.dispatcher_batch_size`` so the
        # coalescing fold in ``BaseClusterOperation.execute`` can skip
        # its SQL round-trip when the dispatcher just observed an empty
        # queue. ``None`` (the default) is preserved for callers that
        # haven't been updated -- the fold runs conservatively in that
        # case.
        self.batch_size = batch_size

        self.log = LOG.with_fields({
            'queue': self.queue_name,
            'job': jobname
        })

        self.abort_path = f'/run/sf/queues-{jobname}.abort'
        daemon.clear_abort_path(self.abort_path)

    def execute(self):
        util_concurrency.set_thread_name(self.jobname)
        LOG.debug(f'This worker thread is executing job {self.jobname}')

        resolve = True
        try:
            self._cluster_operation_execute()
        except (DatabaseUnavailable, StateWriteFailed):
            # The database went away during the operation lookup or the
            # operation itself, or answered but reported a state write
            # as failed (issue 4273: an InnoDB deadlock whose retries
            # were exhausted). Leave the work item claimed rather than
            # resolving it: the stuck-row reaper re-queues it once the
            # database recovers, exactly as for a worker crash, so the op
            # is retried rather than silently dropped (issue 3716 -- a
            # "Too many connections" storm made this path discard 596
            # queued cluster operations, permanently leaking hypervisor
            # state).
            self.log.warning(
                'Database write failed or service unavailable, abandoning '
                'work item for the stuck-row reaper')
            resolve = False
        finally:
            if resolve:
                try:
                    mariadb.resolve_work_item(self.queue_name, self.jobname)
                except DatabaseUnavailable:
                    # Same recovery path as above: the row stays claimed
                    # and the stuck-row reaper re-queues it.
                    self.log.warning(
                        'Database service unavailable resolving work item, '
                        'leaving it for the stuck-row reaper')
            LOG.debug(
                f'This worker thread is finished executing job {self.jobname}')

    def _cluster_operation_execute(self):
        op_type = self.workitem.get('operation_type')
        op_uuid = self.workitem.get('operation_uuid')
        defer_count = self.workitem.get('defer_count', 0)
        # The failure audit is suppressed because from_db() would write it
        # onto an operation which no longer has a row, so no caller can
        # ever read it back -- the log line below carries the same facts
        # and names the work item which carried them.
        op = get_object_class(op_type).from_db(
            op_uuid, suppress_failure_audit=True)

        if not op:
            # Not an error, and not lost work. A work item exists only
            # because its operation existed: the operation row, its state
            # row and the work item are written in one transaction, and a
            # defer re-enqueues an operation it has just read. So a
            # missing row means the operation has been hard deleted, and
            # the deleted-object sweep only hard deletes an object it has
            # re-read in a final state. The operation therefore ran, or
            # was aborted, and then outlived CLUSTER_OPERATION_RETENTION
            # before this work item was dequeued -- the same race as the
            # "no longer queued" skip below, one retention window later.
            #
            # Still logged, because the age it implies is a real signal:
            # either this queue was a retention window behind, or the
            # work item was re-queued that long after its operation
            # finished. Logged with the operation named, because without
            # that the line said nothing at all and no occurrence of it
            # could be investigated (issue 5056).
            self.log.with_fields({
                'operation_type': op_type,
                'operation_uuid': op_uuid,
                'defer_count': defer_count
            }).warning('Operation not found, it has been hard deleted')
            return

        # A work item the stuck-row reaper re-queued can name an operation
        # which has already run. Its dependencies may since have been
        # cleaned up, and judging them now would abort it after the fact
        # (and error an instance it started).
        #
        # The test is "still queued", not "not terminal": an operation
        # this dispatcher sees in executing has also already run, and
        # this dispatcher has never executed one in any state but queued
        # -- the check this one replaces sat below the dependency walk
        # and required queued before calling execute(), so all the walk
        # could do for a non-queued op was abort or defer an operation
        # which had started. sf-net does run an op dequeued in executing,
        # because there it is a coalescing sibling a survivor folded and
        # did not complete; only NetOps are coalescible and they are
        # never enqueued to an sf-queues queue (issue 4017).
        #
        # ``op.state`` is a property over a GetObjectState round trip, so
        # this check reads it once into a local, and the check below the
        # dependency walk no longer repeats that read when there was
        # nothing to walk. Doing both reads on every dispatch cost this
        # dispatcher two state reads per work item (issue 4760).
        state = op.state
        if state.value != BaseClusterOperation.STATE_QUEUED:
            op.add_event(
                EVENT_TYPE_AUDIT,
                f'skipping op which is no longer queued, it is {state.value}')
            return

        op.queue_name = self.queue_name
        op.current_defer_count = defer_count
        op.dispatcher_batch_size = self.batch_size

        # Ensure our dependencies are met.
        for dep in op.depends_on:
            dep_op = get_object_class(dep['op_type']).from_db(dep['op_uuid'])
            if not dep_op:
                # None here is an absent dependency and nothing else. A
                # read which failed raises DatabaseUnavailable out of
                # from_db() rather than answering None (issue 3373), and
                # execute() above leaves the work item claimed for the
                # stuck-row reaper when it does -- so a database blip no
                # longer cancels every dependent whose edge it happened
                # to interrupt, irreversibly, for a dependency which was
                # alive (issue 4910).
                op.cancel_for_missing_dependency(dep)
                return

            dep_op_state = dep_op.state.value
            if dep_op_state in DEPENDENCY_FAILED_STATES:
                op.abort_for_unsuitable_dependency(dep_op)
                return

            if dep_op_state in DEPENDENCY_PENDING_STATES:
                # A dependency which never goes terminal must not park us
                # forever (issue 3992) -- give up loudly once the defer
                # budget is exhausted.
                if op.current_defer_count >= DEPENDENCY_DEFER_BUDGET:
                    op.abandon_dependency_wait(dep_op)
                    return

                # Dependency not yet ready, we should defer
                op.defer(waiting_on=[dep_op],
                         delay=dependency_defer_delay(op.current_defer_count))
                return

            if dep_op_state != BaseClusterOperation.STATE_COMPLETE:
                # Named, rather than reached by falling off the end of
                # this loop body. The two tuples above do enumerate the
                # whole of BaseClusterOperation's vocabulary, and a
                # derived test holds them to it, but falling through
                # would let a state added later satisfy a fate-sharing
                # edge silently -- the one direction this edge must
                # never fail in. An unknown state waits instead,
                # bounded by the same defer budget as a pending one
                # (issue 4910).
                if op.current_defer_count >= DEPENDENCY_DEFER_BUDGET:
                    op.abandon_dependency_wait(dep_op)
                    return

                op.defer(waiting_on=[dep_op],
                         delay=dependency_defer_delay(op.current_defer_count))
                return

        # Ensure that we are running after any runs_after requirements.
        for dep in op.runs_after:
            dep_op = get_object_class(dep['op_type']).from_db(
                dep['op_uuid'], suppress_failure_audit=True)
            if not dep_op:
                # Absent, not unreadable: see the depends_on walk above.
                # Not fatal because otherwise a missing cluster operation
                # could cause the entire cluster to stop being able to manage
                # a given object.
                op.add_event(
                    EVENT_TYPE_AUDIT,
                    'warning, runs_after dependency is missing',
                    extra={
                        'dep_object_type': dep['op_type'],
                        'dep_object_uuid': dep['op_uuid']
                    })
                continue

            dep_op_state = dep_op.state.value
            if dep_op_state in DEPENDENCY_PENDING_STATES:
                # runs_after is an ordering hint, and a missing dependency
                # already warns and continues rather than making the object
                # unmanageable -- treat an exhausted defer budget the same
                # way (issue 3992).
                if op.current_defer_count >= DEPENDENCY_DEFER_BUDGET:
                    op.add_event(
                        EVENT_TYPE_AUDIT,
                        'warning, gave up waiting for runs_after dependency',
                        extra={
                            'dep_object_type': dep_op.object_type,
                            'dep_object_uuid': str(dep_op.uuid),
                            'dep_object_state': dep_op_state,
                            'defer_count': op.current_defer_count
                        })
                    continue

                # Dependency not yet ready, we should defer
                op.defer(waiting_on=[dep_op],
                         delay=dependency_defer_delay(op.current_defer_count))
                return

            if dep_op_state not in TERMINAL_OPERATION_STATES:
                # The depends_on walk's guard, for the same reason: this
                # edge says "not before", so a state which cannot be
                # shown to have finished has not earned a dispatch.
                # Warned past once the budget is spent, as everything
                # else in this walk is (issue 4910).
                if op.current_defer_count >= DEPENDENCY_DEFER_BUDGET:
                    op.add_event(
                        EVENT_TYPE_AUDIT,
                        'warning, gave up waiting for an unknown '
                        'runs_after dependency state',
                        extra={
                            'dep_object_type': dep_op.object_type,
                            'dep_object_uuid': str(dep_op.uuid),
                            'dep_object_state': dep_op_state,
                            'defer_count': op.current_defer_count
                        })
                    continue

                op.defer(waiting_on=[dep_op],
                         delay=dependency_defer_delay(op.current_defer_count))
                return

        # Ensure we haven't been aborted or something. Every path in the
        # dependency walk above which ends this operation returned
        # rather than falling through to here, so the only writer which
        # can have moved the state since it was read at the top of this
        # method is a concurrent one -- an abort from sf-api, say -- and
        # it only had a window in which to do so if the walk went to the
        # database at all. With no dependencies to walk, nothing has
        # happened in between and the state read above is as fresh as a
        # second read would be (issue 4760).
        if op.depends_on or op.runs_after:
            if op.state.value != BaseClusterOperation.STATE_QUEUED:
                return

        # We're good to go!
        start_time = time.time()
        op.execute()
        # One end-of-op event carries the queue-wait time, the execution
        # duration and the coalescing instrumentation. The payload is
        # built on the operation so that this dispatcher and the one in
        # ``shakenfist/daemons/network/workitem.py`` cannot drift apart
        # on field names -- tools/queue-wait-report.py reads one stream
        # carrying events from both.
        op.add_event(
            EVENT_TYPE_USAGE, 'execution duration',
            extra=op.execution_duration_extra(start_time, self.queue_name))
