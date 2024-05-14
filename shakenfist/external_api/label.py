# Documentation state:
#   - Has metadata calls: n/a
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: yes
#   - API reference docs exist:
#        - and link to OpenAPI docs:
#        - and include examples:
#   - Has complete CI coverage:

from functools import partial
from flask_jwt_extended import get_jwt_identity
from flasgger import swag_from
from shakenfist_utilities import api as sf_api, logs

from shakenfist.artifact import Artifact, Artifacts, LABEL_URL, type_filter, url_filter
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.blob import Blob
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist.exceptions import LabelHierarchyTooDeep
from shakenfist.external_api import base as api_base
from shakenfist.instance import instance_usage_for_blob_uuid


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
    return (namespace, '{}{}/{}'.format(LABEL_URL, namespace, label))


label_example = """{
    "artifact_type": "label",
    "blob_uuid": "ffdfce7f-728e-4b76-83c2-304e252f98b1",
    "blobs": {
        "1": {
            "depends_on": null,
            "instances": [
                "d512e9f5-98d6-4c36-8520-33b6fc6de15f"
            ],
            "reference_count": 2,
            "size": 403007488,
            "uuid": "ffdfce7f-728e-4b76-83c2-304e252f98b1"
        }
    },
    "index": 1,
    "max_versions": 3,
    "metadata": {},
    "namespace": "system",
    "shared": false,
    "source_url": "sf://label/system/debian-11-production",
    "state": "created",
    "uuid": "c9428ea2-a3fa-40cf-9668-61be99bb370a",
    "version": 6
}"""


class LabelEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'label', 'Update a label artifact with a new blob.',
        [
            ('label_name', 'body', 'string', 'The label artifact to update.', True),
            ('blob_uuid', 'body', 'uuid', 'The blob to set as the new version.', True)
        ],
        [(200, 'The updated artifact.', label_example)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.log_token_use
    def post(self, label_name=None, blob_uuid=None, max_versions=0):
        namespace, label_url = _label_url(label_name)
        a = Artifact.from_url(Artifact.TYPE_LABEL, label_url, name=label_name,
                              max_versions=max_versions, namespace=namespace,
                              create_if_new=True)
        a.add_index(blob_uuid)
        a.state = dbo.STATE_CREATED

        # NOTE(mikal): no need to mix instances in here, the artifact is brand
        # new
        a.add_event(EVENT_TYPE_AUDIT, 'create request from REST API')
        return a.external_view()

    @swag_from(api_base.swagger_helper(
        'label', 'Search for a label by name.',
        [
            ('label_name', 'body', 'string', 'The label name to search for.', True)
        ],
        [(200, 'The label artifact, if found.', label_example),
         (404, 'Label not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.log_token_use
    def get(self, label_name=None):
        artifacts = list(Artifacts(filters=[
            partial(type_filter, Artifact.TYPE_LABEL),
            partial(url_filter, _label_url(label_name))
        ], prefilter='active'))
        if len(artifacts) == 0:
            sf_api.error(404, 'label %s not found' % label_name)
        return artifacts[0].external_view()

    @swag_from(api_base.swagger_helper(
        'label', 'Delete a label by name.',
        [
            ('label_name', 'body', 'string', 'The label name to delete.', True)
        ],
        [(200, 'The label artifact, if found.', label_example),
         (404, 'Label not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.log_token_use
    def delete(self, label_name=None):
        artifacts = list(Artifacts(filters=[
            partial(type_filter, Artifact.TYPE_LABEL),
            partial(url_filter, _label_url(label_name))
        ], prefilter='active'))
        if len(artifacts) == 0:
            sf_api.error(404, 'label %s not found' % label_name)

        for a in artifacts:
            a.add_event(EVENT_TYPE_AUDIT, 'delete request from REST API')
            a.state = dbo.STATE_DELETED
            for blob_index in a.get_all_indexes():
                b = Blob.from_db(blob_index['blob_uuid'])
                b.ref_count_dec(a)

        ev = a.external_view()
        ev['instances']: instance_usage_for_blob_uuid(b.uuid)
        return ev
