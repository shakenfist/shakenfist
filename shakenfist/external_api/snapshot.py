from flask_jwt_extended import jwt_required
from functools import partial

from shakenfist import artifact
from shakenfist.artifact import Artifacts
from shakenfist import blob
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist import logutil

LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


class InstanceSnapshotEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_ref=None, instance_from_db=None, all=None,
             device=None, max_versions=0, thin=None):
        if not thin:
            thin = config.SNAPSHOTS_DEFAULT_TO_THIN
        return instance_from_db.snapshot(
            all=all, device=device, max_versions=max_versions, thin=thin)

    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
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
                del bout['uuid']

                # Merge it with the parent artifact
                a = ev.copy()
                a.update(bout)
                out.append(a)
        return out
