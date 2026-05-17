import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.constants import get_object_class
from shakenfist.daemons import daemon
from shakenfist import mariadb
from shakenfist.exceptions import InvalidStateException
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import get_all_network_queues
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


class Job(util_concurrency.Job):
    def __init__(self, name):
        super().__init__()
        self.name = name

        self.abort_path = f'/run/sf/net-{name}.abort'
        daemon.clear_abort_path(self.abort_path)

    def execute(self):
        LOG.info('Starting network worker')
        was_previously_idle = False

        # NOTE(mikal): there's really nothing stopping us from processing a bunch
        # of these jobs in parallel with a pool of workers, but I am not sure its
        # worth the complexity right now. Are we really going to be changing
        # networks that much?
        while daemon.check_abort_path(self.abort_path):
            for queue_name in get_all_network_queues():
                jobname_workitem = mariadb.dequeue_work_item(queue_name)
                if jobname_workitem:
                    break

            if not jobname_workitem:
                if not was_previously_idle:
                    util_concurrency.set_thread_name('idle')
                    LOG.debug('This network thread is now idle')
                    was_previously_idle = True
                time.sleep(0.2)

            else:
                jobname, workitem = jobname_workitem
                util_concurrency.set_thread_name(jobname)
                LOG.debug(
                    f'This network thread is now processing job {jobname}')

                try:
                    self._cluster_operation_execute(queue_name, workitem)
                finally:
                    mariadb.resolve_work_item(queue_name, jobname)

    def _cluster_operation_execute(self, queue_name, workitem):
        op_type = workitem.get('operation_type')
        op_uuid = workitem.get('operation_uuid')
        op = get_object_class(op_type).from_db(op_uuid)

        if not op:
            self.log.error('Operation not found')
            return

        op.queue_name = queue_name
        op.current_defer_count = workitem.get('defer_count', 0)

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
            dep_op = get_object_class(dep['op_type']).from_db(dep['op_uuid'])
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

        # We're good to go!
        start_time = time.time()
        op.execute()
        op.add_event(
            EVENT_TYPE_USAGE, 'execution duration',
            extra={
                'seconds': time.time() - start_time
            })
