from shakenfist_utilities import logs  # noreorder

from shakenfist import mariadb
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.schema.operations import node_inst_snap_op as schema
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.artifact import Artifact
from shakenfist.blob import Blob
from shakenfist.blob import snapshot_disk
from shakenfist.exceptions import BlobDeleted
from shakenfist.exceptions import BlobDependencyMissing
from shakenfist.exceptions import InvalidStateException
from shakenfist.instance import Instance
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.util import exceptions as util_exceptions


LOG, HANDLER = logs.setup(__name__)


class NodeInstSnapOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.node_uuid = op.node_uuid
        self.instance_uuid = op.instance_uuid
        self.disk = op.disk
        self.artifact_uuid = op.artifact_uuid
        self.blob_uuid = op.blob_uuid
        self.thin = op.thin


class NoSuchTask(NodeInstSnapOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchInstance(NodeInstSnapOpException):
    def __init__(self, op):
        super().__init__(op, 'instance missing')


class NoSuchDisk(NodeInstSnapOpException):
    def __init__(self, op):
        super().__init__(op, 'no such disk')


class NoSuchArtifact(NodeInstSnapOpException):
    def __init__(self, op):
        super().__init__(op, 'no such artifact')


class AbortSnapshot(NodeInstSnapOpException):
    def __init__(self, op, message):
        super().__init__(op, message)


class NodeInstSnapOp(BaseClusterOperation):
    object_type = schema.object_type
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__node_uuid = static_values['node_uuid']
        self.__instance_uuid = static_values['instance_uuid']
        self.__snapshots = static_values['snapshots']

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'node_uuid': self.node_uuid,
            'instance_uuid': self.instance_uuid,
            'snapshots': self.snapshots,
            'tasks': self.tasks
        })

        self.accumulated_artifacts = []
        self.accumulated_blobs = []

    # Static values
    @property
    def node_uuid(self):
        return self.__node_uuid

    @property
    def instance_uuid(self):
        return self.__instance_uuid

    @property
    def snapshots(self):
        return self.__snapshots

    # API
    def external_view(self):
        retval = super().external_view()
        retval.update({
            'node_uuid': self.node_uuid,
            'instance_uuid': self.instance_uuid,
            'snapshots': self.snapshots
        })
        return retval

    # Tasks
    def dispatch_task(self, task):
        if task not in schema.model_tasks:
            self.log.warning(f'Task {task} not in {schema.model_tasks}')
            raise NoSuchTask(self, task)

        inst = Instance.from_db(self.instance_uuid)
        if not inst:
            self.log.warning(f'Instance {self.instance_uuid} missing')
            raise NoSuchInstance(self)

        try:
            self.__getattribute__(f'_{task.name}')(inst)

        except AbortSnapshot:
            try:
                self.state = NodeInstSnapOp.STATE_ABORT
                self.add_event(EVENT_TYPE_AUDIT, 'snapshot aborted')
            except InvalidStateException:
                self.add_event(EVENT_TYPE_AUDIT, 'failed to abort operation')

        except BlobDependencyMissing:
            try:
                self.state = NodeInstSnapOp.STATE_ABORT
                self.add_event(
                    EVENT_TYPE_AUDIT, 'aborted as blob dependency is missing')
            except InvalidStateException:
                self.add_event(EVENT_TYPE_AUDIT, 'failed to abort operation')

        except Exception as e:
            util_exceptions.ignore_exception('node_inst_snap_op', e)
            self.state = NodeInstSnapOp.STATE_ERROR
            inst.state = Instance.STATE_ERROR
            for a in self.accumulated_artifacts:
                a.state = Artifact.STATE_ERROR
            for b in self.accumulated_blobs:
                b.state = Blob.STATE_ERROR

    def _instance_snapshot(self, inst):
        for s in self.snapshots:
            a = Artifact.from_db(s['artifact_uuid'])
            if not a:
                self.log.warning(f'Artifact {s["artifact_uuid"]} missing')
                raise NoSuchArtifact(self)
            if a.state.value == Artifact.STATE_DELETED:
                self.log.warning(f'Artifact {s["artifact_uuid"]} is deleted')
                raise NoSuchArtifact(self)
            self.accumulated_artifacts.append(a)

            # The blob UUID has been allocated, but the blob object has not yet
            # been created.
            b = snapshot_disk(s['disk'], s['blob_uuid'], thin=s['thin'])
            if not b:
                raise AbortSnapshot(self, f'disk path {s["disk"]["path"]} does not exist')
            self.accumulated_blobs.append(b)

            if a.state.value == Artifact.STATE_DELETED:
                # The artifact was deleted while we were creating the blob, just
                # delete the blob too.
                b.state = Blob.STATE_DELETED
                return

            if inst.state.value == Instance.STATE_DELETED:
                # If the instance we were snapshotting has been deleted by the time
                # we finish the snapshot, then just delete the blob.
                b.state = Blob.STATE_DELETED
                return

            try:
                # User-initiated snapshots must always create a new index,
                # even if the disk content is identical to the previous
                # snapshot. Otherwise the blob_uuid returned by POST
                # /snapshot would never appear in GET /snapshot, and the
                # client would block waiting for it.
                a.add_index(b.uuid, force=True)
                a.state = Artifact.STATE_CREATED
            except BlobDeleted:
                if a.state.value != Artifact.STATE_DELETED:
                    a.state = Artifact.STATE_ERROR
                raise AbortSnapshot(self, 'blob was deleted during snapshot')
            except InvalidStateException:
                # Remove any reference that might have been created during
                # the failed add_index. We look up the highest index to
                # determine what index was attempted.
                highest_idx = a._db_get_attribute('highest_index', {'index': 0})
                idx = highest_idx['index']
                if idx > 0:
                    mariadb.remove_relationship(
                        ObjectType.ARTIFACT, a.uuid,
                        RelationshipType.ARTIFACT_INDEX, str(idx).zfill(12),
                        ObjectType.BLOB, b.uuid)
                raise AbortSnapshot(self, 'invalid state during snapshot')
