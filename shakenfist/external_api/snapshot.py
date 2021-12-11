from flask_jwt_extended import jwt_required
from functools import partial
import os
import shutil
import time
import uuid

from shakenfist import artifact
from shakenfist.artifact import Artifact, Artifacts
from shakenfist import blob
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
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
    def post(self, instance_uuid=None, instance_from_db=None, all=None,
             device=None, max_versions=0):
        disks = instance_from_db.block_devices['devices']
        if instance_from_db.uefi:
            disks.append({
                'type': 'nvram',
                'device': 'nvram',
                'path': os.path.join(instance_from_db.instance_path, 'nvram'),
                'snapshot_ignores': False
            })

        # Filter if requested
        if device:
            new_disks = []
            for d in disks:
                if d['device'] == device:
                    new_disks.append(d)
            disks = new_disks
        elif not all:
            disks = [disks[0]]
        LOG.with_fields({
            'instance': instance_uuid,
            'devices': disks}).info('Devices for snapshot')

        out = {}
        for disk in disks:
            if disk['snapshot_ignores']:
                continue

            if disk['type'] not in ['qcow2', 'nvram']:
                continue

            if not os.path.exists(disk['path']):
                continue

            a = Artifact.from_url(
                Artifact.TYPE_SNAPSHOT,
                '%s%s/%s' % (artifact.INSTANCE_URL,
                             instance_uuid, disk['device']),
                max_versions)

            blob_uuid = str(uuid.uuid4())
            entry = a.add_index(blob_uuid)

            out[disk['device']] = {
                'source_url': a.source_url,
                'artifact_uuid': a.uuid,
                'artifact_index': entry['index'],
                'blob_uuid': blob_uuid
            }

            if disk['type'] == 'nvram':
                # These are small and don't use qemu-img to capture, so just
                # do them now.
                blob.ensure_blob_path()
                dest_path = blob.Blob.filepath(blob_uuid)
                shutil.copyfile(disk['path'], dest_path)

                st = os.stat(dest_path)
                b = blob.Blob.new(blob_uuid, st.st_size,
                                  time.time(), time.time())
                b.observe()
                a.state = Artifact.STATE_CREATED

            else:
                etcd.enqueue(config.NODE_NAME, {
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
