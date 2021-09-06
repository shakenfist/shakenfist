from functools import partial
from flask_jwt_extended import jwt_required

from shakenfist.artifact import (
    Artifact, Artifacts, type_filter as artifact_type_filter)
from shakenfist import baseobject
from shakenfist.external_api import base as api_base
from shakenfist.config import config
from shakenfist import db
from shakenfist.tasks import FetchImageTask


class ImagesEndpoint(api_base.Resource):
    @jwt_required
    def get(self, node=None):
        retval = []
        for i in Artifacts(filters=[
                partial(artifact_type_filter,
                        Artifact.TYPE_IMAGE),
                baseobject.active_states_filter]):
            b = i.most_recent_index
            if b:
                if not node:
                    retval.append(i.external_view())
                elif node in b.locations:
                    retval.append(i.external_view())
        return retval

    @jwt_required
    def post(self, url=None):
        db.add_event('image', url, 'api', 'cache', None, None)

        # We ensure that the image exists in the database in an initial state
        # here so that it will show up in image list requests. The image is
        # fetched by the queued job later.
        a = Artifact.from_url(Artifact.TYPE_IMAGE, url)
        db.enqueue(config.NODE_NAME, {
            'tasks': [FetchImageTask(url)],
        })
        return a.external_view()


class ImageEventsEndpoint(api_base.Resource):
    @jwt_required
    # TODO(andy): Should images be owned? Personalised images should be owned.
    def get(self, url):
        return list(db.get_events('image', url))
