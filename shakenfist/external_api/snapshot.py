# Documentation state:
#   - Has metadata calls:
#   - OpenAPI complete:
#   - Covered in user or operator docs:
#   - API reference docs exist:
#        - and link to OpenAPI docs:
#        - and include examples:
#   - Has complete CI coverage:
from functools import partial

from shakenfist_utilities import api as sf_api
from shakenfist_utilities import logs

from shakenfist import artifact
from shakenfist import blob
from shakenfist.artifact import Artifacts
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.instance import instance_usage_for_blob_uuid

LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


class InstanceSnapshotEndpoint(sf_api.Resource):
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, instance_from_db=None, all=None,
             device=None, max_versions=0, thin=None):
        if not thin:
            thin = config.SNAPSHOTS_DEFAULT_TO_THIN

        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'snapshot request from REST API')
        return instance_from_db.snapshot(
            all=all, device=device, max_versions=max_versions, thin=thin)

    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None):
        out = []
        for snap in Artifacts([
                partial(artifact.instance_snapshot_filter, instance_from_db.uuid)]):
            ev = snap.external_view_without_index()
            for idx in snap.get_all_indexes():
                # Give the blob uuid a better name
                b = blob.Blob.from_db(idx['blob_uuid'])
                if not b:
                    continue

                bout = b.external_view()
                bout['blob_uuid'] = bout['uuid']
                bout['instances'] = instance_usage_for_blob_uuid(b.uuid)
                del bout['uuid']

                # Merge it with the parent artifact
                a = ev.copy()
                a.update(bout)
                out.append(a)
        return out
