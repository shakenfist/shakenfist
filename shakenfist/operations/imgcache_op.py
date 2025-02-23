import os
import time
from uuid import uuid4

from shakenfist_utilities import logs  # noreorder

from shakenfist.storage.blob import Blob
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.etcd_schema.operations import imgcache_op as schema
from shakenfist.eventlog import add_event_multi
from shakenfist.exceptions import BlobDeleted
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.util import general as util_general


LOG, HANDLER = logs.setup(__name__)


class ImageCacheOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.node_uuid = op.node_uuid
        self.blob_uuid = op.blob_uuid
        self.cache_path = op.cache_path
        self.transcode_description = op.transcode_description


class NoSuchTask(ImageCacheOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchBlob(ImageCacheOpException):
    def __init__(self, op, task):
        super().__init__(op, 'blob missing for task {task}')


class NoSuchCachedImage(ImageCacheOpException):
    def __init__(self, op, task):
        super().__init__(op, 'cached image missing for task {task}')


class ImageCacheOp(BaseClusterOperation):
    object_type = schema.object_type.name.lower()
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values)

        self.__node_uuid = static_values['node_uuid']
        self.__blob_uuid = static_values['blob_uuid']
        self.__cache_path = static_values['cache_path']
        self.__transcode_description = static_values['transcode_description']

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
            'blob_uuid': self.blob_uuid,
            'cache_path': self.cache_path,
            'transcode_description': self.transcode_description,
            'tasks': self.tasks
        })

    # Static values
    @property
    def node_uuid(self):
        return self.__node_uuid

    @property
    def blob_uuid(self):
        return self.__blob_uuid

    @property
    def cache_path(self):
        return self.__cache_path

    @property
    def transcode_description(self):
        return self.__transcode_description

    @property
    def tasks(self):
        return self.__tasks

    # Tasks
    def dispatch_task(self, task):
        if task not in schema.model_tasks:
            self.log.warning(f'Task {task} not in {schema.model_tasks}')
            raise NoSuchTask(self, task)

        b = Blob.from_db(self.blob_uuid)
        if not b:
            raise NoSuchBlob(self, task)
        if b.state.value != Blob.STATE_CREATED:
            raise NoSuchBlob(self, task)

        if not os.path.exists(self.cache_path):
            raise NoSuchCachedImage(self, task)

        try:
            self.__getattribute__(f'_{task.name}')(b)
        except Exception as e:
            util_general.ignore_exception('imgcache_op', e)
            self.state = ImageCacheOp.STATE_ERROR

    def _archive_transcode(self, b):
        try:
            transcode_blob_uuid = str(uuid4())
            transcode_blob_path = Blob.filepath(transcode_blob_uuid)
            util_general.link_or_copy(self.cache_path, transcode_blob_path)
            st = os.stat(transcode_blob_path)

            transcode_blob = Blob.new(
                transcode_blob_uuid, time.time(), time.time())
            transcode_blob.size = st.st_size
            transcode_blob.state = Blob.STATE_CREATED
            transcode_blob.observe()
            transcode_blob.verify_checksum(locks=[])

            if b.add_transcode(self.transcode_description, transcode_blob_uuid):
                transcode_blob.request_replication()
                add_event_multi(
                    EVENT_TYPE_AUDIT,
                    [b, transcode_blob],
                    'recorded transcode',
                    extra={
                        'description': self.transcode_description
                    }
                )
                transcode_blob.ref_count_inc(b)
            else:
                # We get a false back if someone else beat us and
                # has already recorded the same transcoding. In
                # that case just delete our attempt.
                add_event_multi(
                    EVENT_TYPE_STATUS,
                    [b, transcode_blob],
                    'lost the transcode race!')
                transcode_blob.state = Blob.STATE_DELETED

        except BlobDeleted:
            add_event_multi(
                EVENT_TYPE_STATUS,
                [b, transcode_blob],
                'transcode blob deleted, perhaps parent blob was reaped?')
