from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.etcd_schema.operations import node_inst_snap_op as schema
from shakenfist.artifact import Artifact
from shakenfist.blob import Blob
from shakenfist.blob import snapshot_disk
from shakenfist.exceptions import BlobDeleted
from shakenfist.exceptions import BlobDependencyMissing
from shakenfist.exceptions import InvalidStateException
from shakenfist.instance import Instance
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.util import general as util_general


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
    object_type = schema.object_type.name.lower()
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values)

        self.__node_uuid = static_values['node_uuid']
        self.__instance_uuid = static_values['instance_uuid']
        self.__disk = static_values['disk']
        self.__artifact_uuid = static_values['artifact_uuid']
        self.__blob_uuid = static_values['blob_uuid']
        self.__thin = static_values['thin']

        # Convert tasks names back into enum entries
        self.__tasks = []
        for task_name in static_values['tasks']:
            try:
                self.__tasks.append(schema.model_tasks[task_name])
            except KeyError as e:
                self.state = self.STATE_ERROR
                self.add_event(
                    EVENT_TYPE_AUDIT, 'unknown task {task_name}: {e}')
                raise e

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'node_uuid': self.node_uuid,
            'instance_uuid': self.instance_uuid,
            'disk': self.disk,
            'artifact_uuid': self.artifact_uuid,
            'blob_uuid': self.blob_uuid,
            'thin': self.thin,
            'tasks': self.tasks
        })

    # Static values
    @property
    def node_uuid(self):
        return self.__node_uuid

    @property
    def instance_uuid(self):
        return self.__instance_uuid

    @property
    def disk(self):
        return self.__disk

    @property
    def artifact_uuid(self):
        return self.__artifact_uuid

    @property
    def blob_uuid(self):
        return self.__blob_uuid

    @property
    def thin(self):
        return self.__thin

    @property
    def tasks(self):
        return self.__tasks

    # Tasks
    def dispatch_task(self, task):
        if task not in schema.model_tasks:
            self.log.warning(f'Task {task} not in {schema.model_tasks}')
            raise NoSuchTask(self, task)

        inst = Instance.from_db(self.instance_uuid)
        if not inst:
            self.log.warning(f'Instance {self.instance_uuid} missing')
            raise NoSuchInstance(self)

        a = Artifact.from_db(self.artifact_uuid)
        if not a:
            self.log.warning(f'Artifact {self.network_uuid} missing')
            raise NoSuchArtifact(self)
        if a.state.value == Artifact.STATE_DELETED:
            self.log.warning(f'Artifact {self.network_uuid} is deleted')
            raise NoSuchArtifact(self)

        # The blob UUID has been allocated, but the blob object has not yet
        # been created.

        try:
            self.__getattribute__(f'_{task.name}')(inst, a)
        except AbortSnapshot:
            self.state = NodeInstSnapOp.STATE_ABORT
            self.add_event(EVENT_TYPE_AUDIT, 'Snapshot aborted')
        except BlobDependencyMissing:
            self.state = NodeInstSnapOp.STATE_ABORT
            self.add_event(
                EVENT_TYPE_AUDIT, 'Aborted as blob dependency is missing')
        except Exception as e:
            util_general.ignore_exception('node_inst_snap_op', e)
            self.state = NodeInstSnapOp.STATE_ERROR
            inst.state = Instance.STATE_ERROR
            a.state = Artifact.STATE_ERROR

    def _instance_snapshot(self, inst, a):
        b = snapshot_disk(self.disk, self.blob_uuid, thin=self.thin)

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
            a.add_index(b.uuid)
            a.state = Artifact.STATE_CREATED
        except BlobDeleted:
            if a.state.value != Artifact.STATE_DELETED:
                a.state = Artifact.STATE_ERROR
            raise AbortSnapshot()
        except InvalidStateException:
            b.ref_count_dec(a)
            raise AbortSnapshot()
