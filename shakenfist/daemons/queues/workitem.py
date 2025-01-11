import os
import time
import uuid

import flask
from shakenfist_utilities import logs  # noreorder

from shakenfist import blob
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import instance
from shakenfist import network
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.artifact import Artifact
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist import eventlog
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.clusteroperationmapping import OPERATION_NAMES_TO_CLASSES
from shakenfist.tasks import ArchiveTranscodeTask
from shakenfist.tasks import DeleteNetworkWhenClean
from shakenfist.tasks import DestroyNetworkTask
from shakenfist.tasks import HypervisorDestroyNetworkTask
from shakenfist.tasks import PreflightAgentOperationTask
from shakenfist.tasks import QueueTask
from shakenfist.tasks import SnapshotTask
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt


LOG, _ = logs.setup(__name__)


class Job(util_concurrency.Job):
    def __init__(self, queue_name, jobname, workitem):
        super().__init__()

        self.queue_name = queue_name
        self.jobname = jobname
        self.workitem = workitem

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
            if self.queue_name.find('-clusteroperation-') == -1:
                self._old_style_execute()
            else:
                self._cluster_operation_execute()

        finally:
            etcd.resolve(self.queue_name, self.jobname)
            LOG.debug(
                f'This worker thread is finished executing job {self.jobname}')

    def _old_style_execute(self):
        libvirt = util_libvirt.get_libvirt()
        self.log.info('Processing job')

        inst = None
        task = None
        try:
            for task in self.workitem.get('tasks', []):
                self.log = self.log.with_fields({'task': task})

                # Tasks should log with the request id of the API request that
                # caused them, if there was in fact one.
                request_id = task.request_id()
                try:
                    if request_id:
                        flask.request.environ['REQUEST_ID'] = request_id
                    else:
                        if 'REQUEST_ID' in flask.request.environ:
                            del flask.request.environ['REQUEST_ID']
                except RuntimeError:
                    ...

                if not QueueTask.__subclasscheck__(type(task)):
                    raise exceptions.UnknownTaskException(
                        'Task was not decoded: %s' % task)

                for t in [SnapshotTask]:
                    if isinstance(task, t):
                        inst = instance.Instance.from_db(task.instance_uuid())
                        break

                if inst:
                    self.log = self.log.with_fields({'instance': inst})

                self.log = self.log.with_fields({
                    'task_name': task.name()
                })
                self.log.info('Starting task')

                if isinstance(task, SnapshotTask):
                    snapshot(inst, task.disk(), task.artifact_uuid(),
                             task.blob_uuid(), task.thin())

                elif isinstance(task, DeleteNetworkWhenClean):
                    # This is a historical concept, it turns out the network node
                    # now just defers the delete task until there are no interfaces,
                    # so we don't need this at all.
                    etcd.enqueue('networknode', DestroyNetworkTask(
                        task.network_uuid()))

                elif isinstance(task, HypervisorDestroyNetworkTask):
                    n = network.Network.from_db(task.network_uuid())
                    n.delete_on_hypervisor()

                elif isinstance(task, ArchiveTranscodeTask):
                    if not os.path.exists(task.cache_path()):
                        continue

                    try:
                        b = blob.Blob.from_db(task.blob_uuid())
                        if not b:
                            continue
                        if b.state.value != dbo.STATE_CREATED:
                            continue

                        transcode_blob_uuid = str(uuid.uuid4())
                        transcode_blob_path = blob.Blob.filepath(
                            transcode_blob_uuid)
                        util_general.link_or_copy(
                            task.cache_path(), transcode_blob_path)
                        st = os.stat(transcode_blob_path)

                        transcode_blob = blob.Blob.new(
                            transcode_blob_uuid, time.time(), time.time())
                        transcode_blob.size = st.st_size
                        transcode_blob.state = blob.Blob.STATE_CREATED
                        transcode_blob.observe()
                        transcode_blob.verify_checksum(locks=[])

                        if b.add_transcode(task.transcode_description(),
                                           transcode_blob_uuid):
                            transcode_blob.request_replication()
                            eventlog.add_event_multi(
                                EVENT_TYPE_AUDIT, [b, transcode_blob],
                                'recorded transcode',
                                extra=task.transcode_description())
                            transcode_blob.ref_count_inc(b)
                        else:
                            # We get a false back if someone else beat us and
                            # has already recorded the same transcoding. In
                            # that case just delete our attempt.
                            eventlog.add_event_multi(
                                EVENT_TYPE_STATUS, [b, transcode_blob],
                                'lost the transcode race!')
                            transcode_blob.state = blob.Blob.STATE_DELETED

                    except exceptions.BlobDeleted:
                        eventlog.add_event_multi(
                            EVENT_TYPE_STATUS, [b, transcode_blob],
                            'transcode blob deleted, perhaps parent blob was reaped?')

                elif isinstance(task, PreflightAgentOperationTask):
                    preflight_agent_operation(task.agentop_uuid())

                else:
                    self.log.error('Unhandled task was dropped')

                self.log.info('Task complete')

        except libvirt.libvirtError as e:
            util_general.ignore_exception('Livirt Error in queue worker', e)
            if inst:
                inst.enqueue_delete_due_error('Instance task failed: %s' % e)

        except exceptions.InstanceException as e:
            self.log.info('Instance Error: %s', e)
            if inst:
                inst.enqueue_delete_due_error('Instance task failed: %s' % e)

        except Exception as e:
            # Logging ignored exception - this should be investigated
            util_general.ignore_exception('queue worker', e)
            if inst:
                inst.enqueue_delete_due_error('Failed queue task: %s' % e)

    def _cluster_operation_execute(self):
        op_type = self.workitem.get('operation_type')
        op_uuid = self.workitem.get('operation_uuid')
        op = OPERATION_NAMES_TO_CLASSES[op_type].from_db(op_uuid)

        if not op:
            self.log.error('Operation not found')
            return

        # Ensure our dependencies are met.
        for dep in op.depends_on:
            dep_op = OPERATION_NAMES_TO_CLASSES[dep['op_type']].from_db(
                dep['op_uuid'])
            if not dep_op:
                op.add_event(
                    EVENT_TYPE_AUDIT,
                    'cancelling operation, as dependency does not exist',
                    extra={
                        'dep_object_type': dep_op.object_type,
                        'dep_object_uuid': dep_op.uuid
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
                op.state = BaseClusterOperation.STATE_ABORT
                return

            if dep_op_state in [BaseClusterOperation.STATE_INITIAL,
                                BaseClusterOperation.STATE_QUEUED,
                                BaseClusterOperation.STATE_PREFLIGHT,
                                BaseClusterOperation.STATE_EXECUTING]:
                # Dependency not yet ready, we should defer
                etcd.enqueue(self.queue_name, self.workitem, delay=15)
                return

        # Ensure that we are running after any runs_after requirements.
        for dep in op.runs_after:
            dep_op = OPERATION_NAMES_TO_CLASSES[dep['op_type']].from_db(
                dep['op_uuid'])
            if not dep_op:
                # Not fatal because otherwise a missing cluster operation
                # could cause the entire cluster to stop being able to manage
                # a given object.
                op.add_event(
                    EVENT_TYPE_AUDIT,
                    'warning, runs_after dependency is missing',
                    extra={
                        'dep_object_type': dep_op.object_type,
                        'dep_object_uuid': dep_op.uuid
                    })
                continue

            dep_op_state = dep_op.state.value
            if dep_op_state in [BaseClusterOperation.STATE_INITIAL,
                                BaseClusterOperation.STATE_QUEUED,
                                BaseClusterOperation.STATE_PREFLIGHT,
                                BaseClusterOperation.STATE_EXECUTING]:
                # Dependency not yet ready, we should defer
                etcd.enqueue(self.queue_name, self.workitem, delay=15)
                return

        # We're good to go!
        op.execute()


def snapshot(inst, disk, artifact_uuid, blob_uuid, thin=False):
    a = Artifact.from_db(artifact_uuid)
    if a.state.value == Artifact.STATE_DELETED:
        # The artifact was deleted before the queued blob creation occurred
        return

    try:
        b = blob.snapshot_disk(disk, blob_uuid, thin=thin)
    except exceptions.BlobDependencyMissing:
        return

    if a.state.value == Artifact.STATE_DELETED:
        # The artifact was deleted while we were creating the blob, just delete
        # the blob too.
        b.state = blob.Blob.STATE_DELETED
        return

    if inst.state.value == instance.Instance.STATE_DELETED:
        # If the instance we were snapshotting has been deleted by the time we
        # finish the snapshot, then just delete the blob.
        b.state = blob.Blob.STATE_DELETED
        return

    try:
        a.add_index(b.uuid)
        a.state = Artifact.STATE_CREATED
    except exceptions.BlobDeleted:
        if a.state.value != Artifact.STATE_DELETED:
            a.state = Artifact.STATE_ERROR
    except exceptions.InvalidStateException:
        b.ref_count_dec(a)


def preflight_agent_operation(agentop_uuid):
    agentop = AgentOperation.from_db(agentop_uuid)
    if not agentop:
        return

    if not agentop.state.value == AgentOperation.STATE_PREFLIGHT:
        return

    for command in agentop.commands:
        if command['command'] == 'put-blob':
            b = blob.Blob.from_db(command['blob_uuid'])
            if not b:
                agentop.error = 'preflight failure, blob missing: %s' % command['blob_uuid']
                return
            b.ensure_local([])

    agentop.state = AgentOperation.STATE_QUEUED
