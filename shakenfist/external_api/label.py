# Documentation state:
#   - Has metadata calls: n/a
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: yes
#   - API reference docs exist:
#        - and link to OpenAPI docs:
#        - and include examples:
#   - Has complete CI coverage:
from flasgger import swag_from
from shakenfist_utilities import api as sf_api  # noreorder
from shakenfist_utilities import logs  # noreorder

from shakenfist.artifact import Artifact
from shakenfist.artifact import LABEL_URL
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist.exceptions import LabelHierarchyTooDeep
from shakenfist.external_api import base as api_base
from shakenfist.namespace import namespace_is_trusted
from shakenfist.util.access_tokens import request_namespace


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def _label_url(label_name):
    """Split a label reference into its namespace and its artifact URL.

    A label may be named bare, in which case it lives in the caller's
    own namespace, or as ``<namespace>/<label>``, which names somebody
    else's. That second form is why every caller of this has to
    authorise the namespace it gets back rather than trusting it: the
    namespace in it was chosen by the requestor.
    """
    if '/' in label_name:
        elems = label_name.split('/')
        if len(elems) > 2:
            raise LabelHierarchyTooDeep()
        namespace, label = elems
    else:
        namespace = request_namespace()
        label = label_name
    return (namespace, f'{LABEL_URL}{namespace}/{label}')


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


class LabelEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'label', 'Update a label artifact with a new blob.',
        [
            ('label_name', 'path', 'string', 'The label artifact to update.', True),
            ('blob_uuid', 'body', 'uuid', 'The blob to set as the new version.', True),
            ('max_versions', 'body', 'integer',
             'The maximum number of versions to retain, or zero for the '
             'configured default.', False)
        ],
        [(200, 'The updated artifact.', label_example)],
        requires_admin=True))
    @api_base.log_token_use
    def post(self, label_name=None, blob_uuid=None, max_versions=0):
        namespace, label_url = _label_url(label_name)

        # Resolve by ownership and then authorise creating and modifying
        # apart, exactly as the artifact upload route does. The
        # requires_admin above is swagger prose and enforces nothing, so
        # before this every authenticated caller could name
        # `<namespace>/<label>` and push a version into a label belonging
        # to anybody who merely shared with, or trusted, them -- and
        # add_index ends in delete_old_versions, so the owner's older
        # versions went with it. The operator guide says outright that a
        # non-system namespace should not be able to update a shared
        # artifact.
        a = Artifact.owned_from_url(Artifact.TYPE_LABEL, label_url,
                                    namespace=namespace)
        if a:
            if request_namespace() not in [a.namespace, 'system']:
                return sf_api.error(404, 'namespace not found')
        else:
            if not namespace_is_trusted(namespace, request_namespace()):
                return sf_api.error(404, 'namespace not found')
            a = Artifact.new(Artifact.TYPE_LABEL, label_url, name=label_name,
                             max_versions=max_versions, namespace=namespace)

        a.add_index(blob_uuid)
        a.state = dbo.STATE_CREATED

        # NOTE(mikal): no need to mix instances in here, the artifact is brand
        # new
        a.add_event(EVENT_TYPE_AUDIT, 'create request from REST API')
        return a.external_view()

    @swag_from(api_base.swagger_helper(
        'label', 'Search for a label by name.',
        [
            ('label_name', 'path', 'string', 'The label name to search for.', True)
        ],
        [(200, 'The label artifact, if found.', label_example),
         (404, 'Label not found.', None)],
        requires_admin=True))
    @api_base.log_token_use
    def get(self, label_name=None):
        # _label_url returns a pair, and this route used to hand the
        # whole pair to url_filter, which compares it against a string.
        # Nothing ever matched, so the lookup below always came back
        # empty -- and the 404 was not returned, so the endpoint fell
        # through to an IndexError and a 500. It has answered nothing
        # else since the pair was introduced.
        #
        # Reading resolves by visibility, so a label shared with us or
        # reached through a trust is legible here. Writing does not; see
        # post() and delete().
        _, label_url = _label_url(label_name)
        a = Artifact.from_url(Artifact.TYPE_LABEL, label_url,
                              namespace=request_namespace())
        if not a:
            return sf_api.error(404, 'label %s not found' % label_name)
        return a.external_view()

    @swag_from(api_base.swagger_helper(
        'label', 'Delete a label by name.',
        [
            ('label_name', 'path', 'string', 'The label name to delete.', True)
        ],
        [(200, 'The label artifact, if found.', label_example),
         (404, 'Label not found.', None)],
        requires_admin=True))
    @api_base.log_token_use
    def delete(self, label_name=None):
        # Carried the same pair-into-url_filter bug as get(), and so has
        # also never deleted anything -- it fell through the unreturned
        # 404 to a NameError on the loop variable.
        #
        # Deleting is a mutation, so resolution is by ownership. Seeing
        # a label through a share or a trust is not permission to remove
        # it, which is what requires_artifact_ownership says on the
        # routes that take a uuid.
        namespace, label_url = _label_url(label_name)
        a = Artifact.owned_from_url(Artifact.TYPE_LABEL, label_url,
                                    namespace=namespace)
        if not a or request_namespace() not in [a.namespace, 'system']:
            return sf_api.error(404, 'label %s not found' % label_name)

        a.add_event(EVENT_TYPE_AUDIT, 'delete request from REST API')
        a.delete()
        return a.external_view()
