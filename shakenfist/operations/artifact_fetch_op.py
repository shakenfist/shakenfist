import requests

from shakenfist_utilities import logs  # noreorder

from shakenfist.artifact import Artifact
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.schema.operations import artifact_fetch_op as schema
from shakenfist.eventlog import add_event_multi
from shakenfist.exceptions import BlobFetchFailed
from shakenfist.exceptions import BlobMissing
from shakenfist.exceptions import BlobTransferSetupFailed
from shakenfist.exceptions import HTTPError
from shakenfist.exceptions import TooManyMatches
from shakenfist import images
from shakenfist.instance import Instance
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.util import exceptions as util_exceptions


LOG, HANDLER = logs.setup(__name__)


class NodeBlobOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.tasks = op.tasks
        self.url = op.url
        self.instance_uuid = op.instance_uuid


class NoSuchTask(NodeBlobOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class ArtifactFetchOp(BaseClusterOperation):
    object_type = schema.object_type
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__namespace = static_values['namespace']
        self.__url = static_values['url']
        self.__instance_uuid = static_values['instance_uuid']

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'namespace': self.namespace,
            'url': self.url,
            'instance_uuid': self.instance_uuid,
            'tasks': self.tasks
        })

    # Static values
    @property
    def url(self):
        return self.__url

    @property
    def instance_uuid(self):
        return self.__instance_uuid

    @property
    def namespace(self):
        return self.__namespace

    # API
    def external_view(self):
        retval = super().external_view()
        retval.update({
            'namespace': self.namespace,
            'url': self.url,
            'instance_uuid': self.instance_uuid
        })
        return retval

    # Tasks
    def dispatch_task(self, task):
        if task not in schema.model_tasks:
            self.log.warning(f'Task {task} not in {schema.model_tasks}')
            raise NoSuchTask(self, task)

        inst = None
        if self.instance_uuid:
            inst = Instance.from_db(self.instance_uuid)

        try:
            self.__getattribute__(f'_{task.name}')(inst)
        except Exception as e:
            util_exceptions.ignore_exception('artifact_fetch_op', e)

            # A failure here must also drive the instance to an error state.
            # The dependent instance start operation is aborted by the queue
            # dispatcher without ever executing, so nothing downstream will --
            # without this the instance sits in state initial forever
            # (issue 3494).
            if inst:
                inst.enqueue_delete_due_error(f'failed to fetch image: {e}')

            # The op might not be in executing if it has been aborted because
            # the instance start request which created it has been aborted.
            if self.state.value == ArtifactFetchOp.STATE_EXECUTING:
                self.state = ArtifactFetchOp.STATE_ERROR

    def _image_fetch(self, inst):
        try:
            # By ownership. This operation ends in add_index, which ends in
            # delete_old_versions, so it must never land on an artifact
            # belonging to a namespace other than the one it runs for. Both
            # routes which enqueue it resolve by ownership too, so in practice
            # this finds the artifact they already settled on -- but it is the
            # layer where the write actually happens, and the invariant is
            # cheaper to make true here than to keep true by inspection of
            # every caller.
            a = Artifact.owned_from_url_or_new(
                Artifact.TYPE_IMAGE, self.url, namespace=self.namespace)
        except TooManyMatches as e:
            self.add_event(
                EVENT_TYPE_AUDIT,
                (f'too many matches for URL {self.url} in namespace '
                 f'{self.namespace}'))
            raise e

        add_event_multi(
            EVENT_TYPE_AUDIT,
            [self, a],
            (f'URL {self.url} in namespace {self.namespace} maps to artifact '
             f'{a.uuid}.'))

        try:
            images.ImageFetchHelper(inst, a).get_image()
            a.add_event(EVENT_TYPE_AUDIT, 'artifact fetch complete')

        except (BlobFetchFailed, BlobMissing, BlobTransferSetupFailed) as e:
            # Replicating a blob from within the cluster failed -- for
            # example every source node timed out awaiting our transfer
            # connection because this node was too loaded to connect
            # (issue 3494). This is usually transient, so retry with
            # backoff before erroring out.
            msg = str(e)
            if self.defer_with_backoff(reason=msg):
                a.add_event(
                    EVENT_TYPE_AUDIT,
                    'transient blob replication failure, will retry',
                    extra={
                        'message': msg,
                        'defer_count': self.current_defer_count + 1
                    })
                return

            # Unlike an upstream fetch failure, a replication failure does
            # not mean the artifact itself is bad -- other nodes still hold
            # valid copies -- so only error the artifact if it has never had
            # a good version.
            if a.state.value in [Artifact.STATE_INITIAL,
                                 Artifact.STATE_CREATING]:
                a.state = Artifact.STATE_ERROR
                a.error = msg

            if inst:
                inst.enqueue_delete_due_error(
                    f'failed to replicate image to target node: {msg}')

            # The op might not be in executing if it has been aborted
            # because the instance start request which created it has been
            # aborted.
            if self.state.value == ArtifactFetchOp.STATE_EXECUTING:
                self.state = ArtifactFetchOp.STATE_ERROR

        except (HTTPError, requests.exceptions.RequestException,
                requests.exceptions.ConnectionError) as e:
            # Clean common problems to store in events
            msg = str(e)
            if msg.find('Name or service not known'):
                msg = 'DNS error'
            if msg.find('No address associated with hostname'):
                msg = 'DNS error'

            # If the artifact has never successfully downloaded, then we are
            # clearly in an error state. However, if we already have a copy of the
            # artifact and the serving web site is experiencing a transient error
            # we should not mark the entire artifact as in error.
            if (a.state.value in [Artifact.STATE_INITIAL,
                                  Artifact.STATE_CREATING] or
                    msg != 'DNS error'):
                # Transient network/upstream failures are common during
                # OS patch reboots and brief upstream outages. Retry a
                # handful of times before declaring the artifact dead.
                if self.defer_with_backoff(reason=msg):
                    a.add_event(
                        EVENT_TYPE_AUDIT,
                        'transient fetch failure, will retry',
                        extra={
                            'message': msg,
                            'defer_count': self.current_defer_count + 1
                        })
                    return

                a.state = Artifact.STATE_ERROR
                a.error = msg
                if inst:
                    inst.enqueue_delete_due_error(
                        f'failed to fetch image: {msg}')

                # The op might not be in executing if it has been aborted
                # because the instance start request which created it has been
                # aborted.
                if self.state.value == ArtifactFetchOp.STATE_EXECUTING:
                    self.state = ArtifactFetchOp.STATE_ERROR

            else:
                a.add_event(
                    EVENT_TYPE_AUDIT,
                    'updating image failed, using already cached version',
                    extra={'message': msg})
