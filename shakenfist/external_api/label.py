from functools import partial
from flask_jwt_extended import get_jwt_identity
from shakenfist_utilities import api as sf_api, logs

from shakenfist.artifact import Artifact, Artifacts, LABEL_URL, type_filter, url_filter
from shakenfist.baseobject import active_states_filter, DatabaseBackedObject as dbo
from shakenfist.blob import Blob
from shakenfist.daemons import daemon
from shakenfist.exceptions import LabelHierarchyTooDeep
from shakenfist.external_api import base as api_base


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def _label_url(label_name):
    if '/' in label_name:
        elems = label_name.split('/')
        if len(elems) > 2:
            raise LabelHierarchyTooDeep()
        namespace, label = elems
    else:
        namespace = get_jwt_identity()[0]
        label = label_name
    return (namespace, '%s%s/%s' % (LABEL_URL, namespace, label))


class LabelEndpoint(sf_api.Resource):
    @api_base.verify_token
    @api_base.log_token_use
    def post(self, label_name=None, blob_uuid=None, max_versions=0):
        namespace, label_url = _label_url(label_name)
        a = Artifact.from_url(Artifact.TYPE_LABEL, label_url,
                              max_versions, namespace=namespace,
                              create_if_new=True)
        a.add_index(blob_uuid)
        a.state = dbo.STATE_CREATED
        return a.external_view()

    @api_base.verify_token
    @api_base.log_token_use
    def get(self, label_name=None):
        artifacts = list(Artifacts(filters=[
            partial(type_filter, Artifact.TYPE_LABEL),
            partial(url_filter, _label_url(label_name)),
            active_states_filter
        ]))
        if len(artifacts) == 0:
            sf_api.error(404, 'label %s not found' % label_name)
        return artifacts[0].external_view()

    @api_base.verify_token
    @api_base.log_token_use
    def delete(self, label_name=None):
        artifacts = list(Artifacts(filters=[
            partial(type_filter, Artifact.TYPE_LABEL),
            partial(url_filter, _label_url(label_name)),
            active_states_filter
        ]))
        if len(artifacts) == 0:
            sf_api.error(404, 'label %s not found' % label_name)

        for a in artifacts:
            a.state = dbo.STATE_DELETED
            for blob_index in a.get_all_indexes():
                b = Blob.from_db(blob_index['blob_uuid'])
                b.ref_count_dec()

        return a.external_view()
