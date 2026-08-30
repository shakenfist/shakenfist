import os

from shakenfist_utilities import logs  # noreorder

from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import GiB
from shakenfist import mariadb
from shakenfist.schema.operations import node_blob_op as schema
from shakenfist.exceptions import BlobAlreadyBeingTransferred
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.util import exceptions as util_exceptions


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
    object_type = schema.object_type
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
            mariadb.delete_cluster_operation_target(str(self.uuid))

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
            util_exceptions.ignore_exception('node_blob_op', e)
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
        if config.NODE_NAME in locations and os.path.exists(Blob.filepath(str(b.uuid))):
            return

        if config.NODE_UUID:
            metrics_data = mariadb.get_node_metrics(config.NODE_UUID)
        else:
            metrics_data = None
        if metrics_data:
            metrics = metrics_data.get('metrics', {})
        else:
            metrics = {}

        # This is the local node, so we reserve its own configured floor. NOTE:
        # disk_free_blobs and b.size are both in bytes, so the reservation must
        # be converted to bytes too. Historically this compared bytes against
        # NODE_DISK_RESERVATION_GB's predecessor (a GB number), effectively
        # reserving ~20 bytes -- the floor was almost never enforced here.
        reservation_bytes = config.NODE_DISK_RESERVATION_GB * GiB
        if (int(metrics.get('disk_free_blobs', 0)) - int(b.size) <
                reservation_bytes):
            b.add_event(
                EVENT_TYPE_AUDIT, 'cannot replicate blob, insufficient space')
            return

        try:
            b.ensure_local(wait_for_other_transfers=False)
        except BlobAlreadyBeingTransferred:
            if not self.defer_with_backoff(reason='blob already being transferred'):
                # Blob replication contention is benign, not an operation
                # failure -- match the insufficient-space branch above and
                # let this attempt lapse rather than erroring the op out.
                b.add_event(
                    EVENT_TYPE_AUDIT,
                    'cannot replicate blob, retry budget exhausted while blob '
                    'already being transferred')
                return
