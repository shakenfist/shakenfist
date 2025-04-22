import os

from shakenfist_utilities import logs  # noreorder

from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import etcd
from shakenfist.etcd_schema.operations import node_blob_op as schema
from shakenfist.exceptions import BlobAlreadyBeingTransferred
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.util import general as util_general


LOG, HANDLER = logs.setup(__name__)


class NodeBlobOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.blob_uuid = op.blob_uuid
        self.node_uuid = op.node_uuid
        self.tasks = op.tasks


class NoSuchTask(NodeBlobOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchBlob(NodeBlobOpException):
    def __init__(self, task):
        super().__init__(task, 'blob missing')


class NodeBlobOp(BaseClusterOperation):
    object_type = schema.object_type.name.lower()
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__node_uuid = static_values['node_uuid']
        self.__blob_uuid = static_values['blob_uuid']

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'node_uuid': self.node_uuid,
            'blob_uuid': self.blob_uuid,
            'tasks': self.tasks
        })

    # Static values
    @property
    def node_uuid(self):
        return self.__node_uuid

    @property
    def blob_uuid(self):
        return self.__blob_uuid

    # API
    def external_view(self):
        retval = super().external_view()
        retval.update({
            'node_uuid': self.node_uuid,
            'blob_uuid': self.blob_uuid
        })
        return retval

    # Tasks
    def execute(self):
        try:
            super().execute()
        finally:
            etcd.delete_raw(
                f'/sf/clusteroperations-by-blob/{self.blob_uuid}/{self.node_uuid}')

    def dispatch_task(self, task):
        if task not in schema.model_tasks:
            self.log.warning(f'Task {task} not in {schema.model_tasks}')
            raise NoSuchTask(self, task)

        b = Blob.from_db(self.blob_uuid)
        if not b:
            self.log.warning(f'Blob {self.blob_uuid} missing')
            raise NoSuchBlob(self)

        try:
            self.__getattribute__(f'_{task.name}')(b)
        except Exception as e:
            util_general.ignore_exception('node_blob_op', e)
            self.state = NodeBlobOp.STATE_ERROR

    def _verify_size_and_checksum(self, b):
        locations = b.locations
        if config.NODE_NAME not in locations:
            return

        if not b.verify_size():
            return
        b.verify_checksum(urgent=False)

    def _ensure_local(self, b):
        locations = b.locations
        if config.NODE_NAME in locations and os.path.exists(Blob.filepath(b.uuid)):
            return

        metrics = etcd.get('metrics', config.NODE_NAME, None)
        if metrics:
            metrics = metrics.get('metrics', {})
        else:
            metrics = {}

        if (int(metrics.get('disk_free_blobs', 0)) - int(b.size) <
                config.MINIMUM_FREE_DISK):
            b.add_event(
                EVENT_TYPE_AUDIT, 'cannot replicate blob, insufficient space')
            return

        try:
            b.ensure_local([], wait_for_other_transfers=False)
        except BlobAlreadyBeingTransferred:
            self.defer()
