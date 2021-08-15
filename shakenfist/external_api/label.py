from functools import partial
from flask_jwt_extended import get_jwt_identity
from flask_jwt_extended import jwt_required

from shakenfist.artifact import Artifact, Artifacts, LABEL_URL, type_filter, url_filter
from shakenfist.baseobject import (
    active_states_filter,
    DatabaseBackedObject as dbo)
from shakenfist.external_api import base as api_base


def _label_url(label_name):
    return '%s%s/%s' % (LABEL_URL, get_jwt_identity(), label_name)


class LabelEndpoint(api_base.Resource):

    @jwt_required
    def post(self, label_name=None, blob_uuid=None):
        a = Artifact.from_url(Artifact.TYPE_LABEL, _label_url(label_name))
        a.add_index(blob_uuid)
        a.state = dbo.STATE_CREATED
        return a.external_view()

    @jwt_required
    def get(self, label_name=None):
        artifacts = list(Artifacts(filters=[
            partial(type_filter, Artifact.TYPE_LABEL),
            partial(url_filter, _label_url(label_name)),
            active_states_filter
        ]))
        if len(artifacts) == 0:
            api_base.error(404, 'label %s not found' % label_name)
        return artifacts[0].external_view()

    @jwt_required
    def delete(self, label_name=None):
        artifacts = list(Artifacts(filters=[
            partial(type_filter, Artifact.TYPE_LABEL),
            partial(url_filter, _label_url(label_name)),
            active_states_filter
        ]))
        if len(artifacts) == 0:
            api_base.error(404, 'label %s not found' % label_name)

        for a in artifacts:
            a.state = dbo.STATE_DELETED
