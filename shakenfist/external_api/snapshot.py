# Documentation state:
#   - Has metadata calls:
#   - OpenAPI complete: yes
#   - Covered in user or operator docs:
#   - API reference docs exist:
#        - and link to OpenAPI docs:
#        - and include examples:
#   - Has complete CI coverage:
from functools import partial

from flasgger import swag_from
from shakenfist_utilities import logs  # noreorder

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


class InstanceSnapshotEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Snapshot an instance.',
        [
            ('instance_ref', 'path', 'uuidorname',
             'The UUID or name of the instance.', True),
            ('all', 'body', 'boolean',
             'Snapshot every disk, rather than only the first.', False),
            ('device', 'body', 'string',
             'Snapshot only this device, for example "vdb".', False),
            ('max_versions', 'body', 'integer',
             'The maximum number of versions to retain for the resulting '
             'snapshot artifacts, or zero for the configured default.', False),
            ('thin', 'body', 'boolean',
             'Take a thin snapshot, which records only the differences from '
             'the backing image. False is currently treated the same as '
             'omitting the parameter: both fall back to '
             'SNAPSHOTS_DEFAULT_TO_THIN.', False)
        ],
        [(200, 'Information about the snapshots taken.', None),
         (404, 'Instance not found.', None),
         (406, 'Instance is not in a state where it can be snapshotted.',
          None)]))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, instance_from_db=None, all=None,
             device=None, max_versions=0, thin=None):
        # Falsiness rather than `is None`, deliberately: the official
        # client has always transmitted `thin: false` when the caller did
        # not ask for thin (the CLI flag defaults to False), so honouring
        # an explicit false here would make SNAPSHOTS_DEFAULT_TO_THIN
        # inert for every shipped client. The absent-versus-false
        # distinction cannot be drawn until phase 4 of
        # PLAN-api-input-validation, alongside a client that omits the
        # key when unset.
        if not thin:
            thin = config.SNAPSHOTS_DEFAULT_TO_THIN

        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'snapshot request from REST API')
        return instance_from_db.snapshot(
            all=all, device=device, max_versions=max_versions, thin=thin)

    @swag_from(api_base.swagger_helper(
        'instances', 'List the snapshots of an instance.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(200, 'Information about the snapshots of an instance.', None),
         (404, 'Instance not found.', None)]))
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
