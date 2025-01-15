import requests

from shakenfist_utilities import logs  # noreorder

from shakenfist.artifact import Artifact
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.etcd_schema.operations import artifact_fetch_op as schema
from shakenfist.eventlog import add_event_multi
from shakenfist.exceptions import HTTPError
from shakenfist.exceptions import TooManyMatches
from shakenfist import images
from shakenfist.instance import Instance
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.util import general as util_general


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
    object_type = schema.object_type.name.lower()
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values)

        self.__namespace = static_values['namespace']
        self.__url = static_values['url']
        self.__instance_uuid = static_values['instance_uuid']

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

    @property
    def tasks(self):
        return self.__tasks

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
            util_general.ignore_exception('artifact_fetch_op', e)
            self.state = ArtifactFetchOp.STATE_ERROR

    def _image_fetch(self, inst):
        try:
            a = Artifact.from_url(
                Artifact.TYPE_IMAGE, self.url, namespace=self.namespace,
                create_if_new=True)
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
            # TODO(andy): Wait up to 15 mins for another queue process to download
            # the required image. This will be changed to queue on a
            # "waiting_image_fetch" queue but this works now.
            images.ImageFetchHelper(inst, a).get_image()
            a.add_event(EVENT_TYPE_AUDIT, 'artifact fetch complete')

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
                a.state = Artifact.STATE_ERROR
                a.error = msg
                if inst:
                    inst.enqueue_delete_due_error(
                        f'failed to fetch image: {msg}')
                self.state = ArtifactFetchOp.STATE_ERROR

            else:
                a.add_event(
                    EVENT_TYPE_AUDIT,
                    'updating image failed, using already cached version',
                    extra={'message': msg})
