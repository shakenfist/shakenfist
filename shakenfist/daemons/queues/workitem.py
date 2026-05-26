import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.constants import get_object_class
from shakenfist.daemons import daemon
from shakenfist import mariadb
from shakenfist.exceptions import InvalidStateException
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


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

        try:
            self._cluster_operation_execute()
        finally:
            mariadb.resolve_work_item(self.queue_name, self.jobname)
            LOG.debug(
                f'This worker thread is finished executing job {self.jobname}')

    def _cluster_operation_execute(self):
        op_type = self.workitem.get('operation_type')
        op_uuid = self.workitem.get('operation_uuid')
        op = get_object_class(op_type).from_db(op_uuid)

        if not op:
            self.log.error('Operation not found')
            return

        op.queue_name = self.queue_name
        op.current_defer_count = self.workitem.get('defer_count', 0)
        op.dispatcher_batch_size = self.batch_size

        # Ensure our dependencies are met.
        for dep in op.depends_on:
            dep_op = get_object_class(dep['op_type']).from_db(dep['op_uuid'])
            if not dep_op:
                op.add_event(
                    EVENT_TYPE_AUDIT,
                    'cancelling operation, as dependency does not exist',
                    extra={
                        'dep_object_type': dep['op_type'],
                        'dep_object_uuid': dep['op_uuid']
                    })
                op.state = BaseClusterOperation.STATE_ERROR
                return

            dep_op_state = dep_op.state.value
            if dep_op_state in [BaseClusterOperation.STATE_ERROR,
                                BaseClusterOperation.STATE_DELETED,
                                BaseClusterOperation.STATE_ABORT]:
                op.add_event(
                    EVENT_TYPE_AUDIT,
                    'aborting operation, as dependency is unsuitable',
                    extra={
                        'dep_object_type': dep_op.object_type,
                        'dep_object_uuid': dep_op.uuid,
                        'dep_object_state': dep_op_state
                    })
                try:
                    op.state = BaseClusterOperation.STATE_ABORT
                except InvalidStateException:
                    op.add_event(EVENT_TYPE_AUDIT, 'failed to abort operation')
                return

            if dep_op_state in [BaseClusterOperation.STATE_INITIAL,
                                BaseClusterOperation.STATE_QUEUED,
                                BaseClusterOperation.STATE_PREFLIGHT,
                                BaseClusterOperation.STATE_EXECUTING]:
                # Dependency not yet ready, we should defer
                op.defer(waiting_on=[dep_op])
                return

        # Ensure that we are running after any runs_after requirements.
        for dep in op.runs_after:
            dep_op = get_object_class(dep['op_type']).from_db(
                dep['op_uuid'], suppress_failure_audit=True)
            if not dep_op:
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
            if dep_op_state in [BaseClusterOperation.STATE_INITIAL,
                                BaseClusterOperation.STATE_QUEUED,
                                BaseClusterOperation.STATE_PREFLIGHT,
                                BaseClusterOperation.STATE_EXECUTING]:
                # Dependency not yet ready, we should defer
                op.defer(waiting_on=[dep_op])
                return

        # Ensure we haven't been aborted or something
        if op.state.value != BaseClusterOperation.STATE_QUEUED:
            return

        # We're good to go!
        start_time = time.time()
        op.execute()
        # One end-of-op event carries both the queue-wait time and the
        # execution duration. See the matching block in
        # ``shakenfist/daemons/network/workitem.py`` for the rationale
        # (combining halves the eventlog gRPC cost on the critical path).
        extra = {'seconds': time.time() - start_time}
        if op.created_at is not None:
            extra['wait_seconds'] = start_time - op.created_at
            extra['defer_count'] = op.current_defer_count
            extra['queue_name'] = self.queue_name
        op.add_event(EVENT_TYPE_USAGE, 'execution duration', extra=extra)
