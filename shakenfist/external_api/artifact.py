from flask_jwt_extended import jwt_required

from shakenfist.artifact import Artifact, Artifacts
from shakenfist import baseobject
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.config import config
from shakenfist import db
from shakenfist import logutil
from shakenfist.tasks import FetchImageTask


LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


def arg_is_artifact_uuid(func):
    def wrapper(*args, **kwargs):
        if 'artifact_uuid' in kwargs:
            kwargs['artifact_from_db'] = Artifact.from_db(
                kwargs['artifact_uuid'])
        if not kwargs.get('artifact_from_db'):
            LOG.with_field('artifact', kwargs['artifact_uuid']).info(
                'Artifact not found, missing or deleted')
            return api_base.error(404, 'artifact not found')

        return func(*args, **kwargs)
    return wrapper


class ArtifactEndpoint(api_base.Resource):
    @jwt_required
    @arg_is_artifact_uuid
    def get(self, artifact_uuid=None, artifact_from_db=None):
        return artifact_from_db.external_view()


class ArtifactsEndpoint(api_base.Resource):
    @jwt_required
    def get(self, node=None):
        retval = []
        for i in Artifacts(filters=[baseobject.active_states_filter]):
            b = i.most_recent_index
            if b:
                if not node:
                    retval.append(i.external_view())
                elif node in b.locations:
                    retval.append(i.external_view())
        return retval

    @jwt_required
    def post(self, url=None):
        # The only artifact type you can force the cluster to fetch is an
        # image, so TYPE_IMAGE is assumed here.
        db.add_event('image', url, 'api', 'cache', None, None)

        # We ensure that the image exists in the database in an initial state
        # here so that it will show up in image list requests. The image is
        # fetched by the queued job later.
        a = Artifact.from_url(Artifact.TYPE_IMAGE, url)
        db.enqueue(config.NODE_NAME, {
            'tasks': [FetchImageTask(url)],
        })
        return a.external_view()


class ArtifactEventsEndpoint(api_base.Resource):
    @jwt_required
    # TODO(andy): Should images be owned? Personalised images should be owned.
    def get(self, artifact_uuid):
        return list(db.get_events('artifact', artifact_uuid))
