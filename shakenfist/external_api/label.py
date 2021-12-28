from functools import partial
from flask_jwt_extended import jwt_required, get_jwt_identity

from shakenfist.artifact import Artifact, Artifacts, LABEL_URL, type_filter, url_filter
from shakenfist.baseobject import active_states_filter, DatabaseBackedObject as dbo
from shakenfist.blob import Blob
from shakenfist.daemons import daemon
from shakenfist.exceptions import BlobDeleted
from shakenfist.external_api import base as api_base
from shakenfist import logutil


LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


def _label_url(label_name):
    return '%s%s/%s' % (LABEL_URL, get_jwt_identity()[0], label_name)


class LabelEndpoint(api_base.Resource):

    @jwt_required
    def post(self, label_name=None, blob_uuid=None, max_versions=0):
        b = Blob.from_db(blob_uuid)
        if not b:
            return api_base.error(404, 'blob not found')
        try:
            b.ref_count_inc()
        except BlobDeleted:
            return api_base.error(400, 'blob has been deleted')

        a = Artifact.from_url(Artifact.TYPE_LABEL, _label_url(label_name),
                              max_versions)
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
            for blob_index in a.get_all_indexes():
                b = Blob.from_db(blob_index['blob_uuid'])
                b.ref_count_dec()
