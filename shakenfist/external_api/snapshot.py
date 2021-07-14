import copy
from flask_jwt_extended import jwt_required
from functools import partial
import uuid

from shakenfist import artifact
from shakenfist.artifact import Artifact, Artifacts
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.blob import Blob
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist import logutil

LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


class InstanceSnapshotEndpoint(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None, all=None):
        disks = instance_from_db.block_devices['devices']
        if not all:
            disks = [disks[0]]

        out = {}
        for disk in disks:
            if disk['snapshot_ignores']:
                continue

            if disk['type'] != 'qcow2':
                continue

            a = Artifact.from_url(
                Artifact.TYPE_SNAPSHOT,
                'sf://instance/%s/%s' % (instance_uuid, disk['device']))

            blob_uuid = str(uuid.uuid4())
            blob = instance_from_db.snapshot(blob_uuid, disk)
            blob.observe()
            entry = a.add_index(blob_uuid)

            out[disk['device']] = {
                'source_url': a.source_url,
                'artifact_uuid': a.uuid,
                'artifact_index': entry['index'],
                'blob_uuid': blob.uuid,
                'blob_size': blob.size,
                'blob_modified': blob.modified
            }

            LOG.with_fields({
                'instance': instance_uuid,
                'artifact': a.uuid,
                'blob': blob.uuid,
                'device': disk['device']
            }).info('Created snapshot')
            instance_from_db.add_event('api', 'snapshot %s' % disk,
                                       None, a.uuid)
            if a.state == dbo.STATE_INITIAL:
                a.state = dbo.STATE_CREATED

        return out

    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        out = []
        for snap in Artifacts([partial(artifact.instance_snapshot_filter, instance_uuid)]):
            ev = snap.external_view_without_index()
            for idx in snap.get_all_indexes():
                # Give the blob uuid a better name
                b = Blob.from_db(idx['blob_uuid']).external_view()
                b['blob_uuid'] = b['uuid']
                del b['uuid']

                # Merge it with the parent artifact
                a = copy.copy(ev)
                a.update(b)
                out.append(a)
        return out
