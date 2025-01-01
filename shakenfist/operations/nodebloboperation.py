import os

from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import etcd
from shakenfist.etcd_schema.operations import nodebloboperation as schema
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.util import general as util_general


class NodeBlobOperationException(BaseOperationException):
    def __init__(self, task, message):
        super().__init__(message)
        self.task_type = task.object_type
        self.task_uuid = task.uuid
        self.blob_uuid = task.blob_uuid
        self.node_uuid = task.node_uuid


class NoSuchTask(NodeBlobOperationException):
    def __init__(self, task):
        super().__init__(task, 'no such task')


class NoSuchBlob(NodeBlobOperationException):
    def __init__(self, task):
        super().__init__(task, 'blob missing')


class NodeBlobOperation(BaseClusterOperation):
    object_type = schema.object_type
    initial_version = schema.initial_version
    current_version = schema.nbo_current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values)

        self.__blob_uuid = static_values['blob_uuid']
        self.__tasks = static_values['tasks']

    # Static values
    @property
    def blob_uuid(self):
        return self.__blob_uuid

    @property
    def tasks(self):
        return self.__tasks

    # Tasks
    def execute(self):
        try:
            self.state = self.STATE_EXECUTING
            for t in self.tasks:
                self.dispatch_task(t)
            self.state = self.STATE_COMPLETE
        finally:
            etcd.delete_raw(
                f'/sf/clusteroperations-by-blob/{self.blob_uuid}/{self.node_uuid}')

    def dispatch_task(self, task):
        if task not in schema.model_tasks:
            raise NoSuchTask(task)

        b = Blob.from_db(self.blob_uuid)
        if not b:
            raise NoSuchBlob(task)

        try:
            self.__getattribute__(f'_{task.name}')(b)
        except Exception as e:
            util_general.ignore_exception(
                f'{self.object_type} task {self.uuid} failed: {e}')

    def _verify_size_and_checksum(self, b):
        locations = b.locations
        if config.NODE_NAME not in locations:
            return

        if not b.verify_size():
            return
        b.verify_checksum(urgent=False)

    def _ensure_local(self, b):
        try:
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

            b.ensure_local([], wait_for_other_transfers=False)

        finally:
            b.remove_replication_request(*self.unique_label())
