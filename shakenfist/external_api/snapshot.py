import copy
from flask_jwt_extended import jwt_required
from functools import partial
import uuid

from shakenfist import artifact
from shakenfist.artifact import Artifact, Artifacts
from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import db
from shakenfist.external_api import base as api_base
from shakenfist import logutil
from shakenfist.tasks import SnapshotTask

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
                '%s%s/%s' % (artifact.INSTANCE_URL, instance_uuid, disk['device']))

            blob_uuid = str(uuid.uuid4())
            entry = a.add_index(blob_uuid)

            out[disk['device']] = {
                'source_url': a.source_url,
                'artifact_uuid': a.uuid,
                'artifact_index': entry['index'],
                'blob_uuid': blob_uuid
            }

            db.enqueue(config.NODE_NAME, {
                'tasks': [SnapshotTask(instance_uuid, disk, a.uuid, blob_uuid)],
            })
            instance_from_db.add_event('api', 'snapshot of %s requested' % disk,
                                       None, a.uuid)

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
                b = Blob.from_db(idx['blob_uuid'])
                if not b:
                    continue

                bout = b.external_view()
                bout['blob_uuid'] = bout['uuid']
                del bout['uuid']

                # Merge it with the parent artifact
                a = copy.copy(ev)
                a.update(bout)
                out.append(a)
        return out
