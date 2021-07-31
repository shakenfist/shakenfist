from flask_jwt_extended import get_jwt_identity
from flask_jwt_extended import jwt_required

from shakenfist.artifact import Artifact, LABEL_URL
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.external_api import base as api_base


class LabelEndpoint(api_base.Resource):
    @jwt_required
    def post(self, label_name=None, blob_uuid=None):
        a = Artifact.from_url(
            Artifact.TYPE_LABEL, '%s%s/%s' % (LABEL_URL, get_jwt_identity(), label_name))
        if not a:
            a = Artifact.new(
                Artifact.TYPE_LABEL, '%s%s/%s' % (LABEL_URL, get_jwt_identity(), label_name))

        a.add_index(blob_uuid)
        a.state = dbo.STATE_CREATED
        return a.external_view()

    @jwt_required
    def get(self, label_name=None):
        a = Artifact.from_url(
            Artifact.TYPE_LABEL, '%s%s/%s' % (LABEL_URL, get_jwt_identity(), label_name))
        if not a:
            api_base.error(404, 'label %s not found' % label_name)

        return a.external_view()

    @jwt_required
    def delete(self, label_name=None):
        a = Artifact.from_url(
            Artifact.TYPE_LABEL, '%s%s/%s' % (LABEL_URL, get_jwt_identity(), label_name))
        if not a:
            api_base.error(404, 'label %s not found' % label_name)

        a.state = dbo.STATE_DELETED
