import errno
import os
import pathlib
import random
import time


from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist import instance
from shakenfist import node
from shakenfist import upload
from shakenfist.blob import Blob
from shakenfist.blob import Blobs
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.daemons.cleaner import scheduled_tasks
from shakenfist.daemons import daemon
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


class Monitor(daemon.Daemon):
    def _maintain_blobs(self):
        # Find orphaned and deleted blobs still on disk
        blob_path = os.path.join(config.STORAGE_PATH, 'blobs')
        os.makedirs(blob_path, exist_ok=True)
        cache_path = os.path.join(config.STORAGE_PATH, 'image_cache')
        os.makedirs(cache_path, exist_ok=True)

        active_blob_uuids = []
        for b in Blobs([], prefilter='active'):
            active_blob_uuids.append(b.uuid)
        n = node.Node.from_db(config.NODE_NAME)
        if not n:
            # We have started up enough yet to exist in etcd
            return

        all_node_blobs = n.blobs

        p = pathlib.Path(blob_path)
        for entpath in p.glob('**/*'):
            entpath = str(entpath)
            try:
                if entpath.endswith('/_version'):
                    continue

                if not os.path.isfile(entpath):
                    continue

                st = os.stat(entpath)
                blob_uuid = entpath.split('/')[-1].replace('.partial', '')

                # If we've had this file for more than two cleaner delays...
                if time.time() - st.st_mtime > config.CLEANER_DELAY * 2:
                    if entpath.endswith('.partial'):
                        # ... and its a stale partial transfer
                        LOG.with_fields({'blob': blob_uuid}).warning(
                            'Deleting stale partial transfer')
                        os.unlink(entpath)

                    else:
                        blob_uuid = entpath.split('/')[-1]
                        if (blob_uuid not in active_blob_uuids
                                or blob_uuid not in all_node_blobs):
                            LOG.with_fields({'blob': blob_uuid}).debug(
                                'Removing deleted blob from disk')
                            os.unlink(entpath)
                            cached = util_general.file_permutation_exists(
                                os.path.join(cache_path, blob_uuid),
                                ['iso', 'qcow2'])
                            if cached:
                                os.unlink(cached)
            except FileNotFoundError:
                LOG.debug('File %s disappeared while maintaining blobs'
                          % entpath)

        # Find transcoded blobs in the image cache which are no longer in use
        for ent in os.listdir(cache_path):
            entpath = os.path.join(cache_path, ent)

            # Broken symlinks will report an error here that we have to catch
            try:
                st = os.stat(entpath)
            except OSError as e:
                if e.errno == errno.ENOENT:
                    LOG.with_fields({
                        'blob': ent}).warning('Deleting broken symlinked image cache entry')
                    os.unlink(entpath)
                    continue
                else:
                    raise e

            # If we haven't seen this file in use for more than two cleaner delays...
            if time.time() - st.st_mtime > config.CLEANER_DELAY * 2:
                blob_uuid = ent.split('.')[0]
                b = Blob.from_db(blob_uuid)
                if not b:
                    LOG.with_fields({
                        'blob': ent}).warning('Deleting orphaned image cache entry')
                    os.unlink(entpath)
                    continue

                if b.ref_count == 0:
                    LOG.with_fields({
                        'blob': ent}).warning('Deleting globally unused image cache entry')
                    os.unlink(entpath)
                    continue

                this_node = len(instance.instance_usage_for_blob_uuid(
                    b.uuid, node=config.NODE_NAME))
                LOG.with_fields(
                    {
                        'blob': blob_uuid,
                        'this_node': this_node
                    }).info('Blob users on this node')
                if this_node == 0:
                    LOG.with_fields(
                        {
                            'blob': blob_uuid
                        }).warning('Deleting unused image cache entry')
                    os.unlink(entpath)
                else:
                    # Record that this file is in use for the benefit of
                    # the above time check.
                    pathlib.Path(entpath).touch(exist_ok=True)

    def _find_missing_blobs(self):
        # Find blobs which should be on this node but are not.
        n = node.Node.from_db(config.NODE_NAME)
        if not n:
            return

        for blob_uuid in n.blobs:
            if not os.path.exists(Blob.filepath(blob_uuid)):
                b = Blob.from_db(blob_uuid, suppress_failure_audit=True)
                if b:
                    LOG.with_fields({
                        'blob': blob_uuid}).warning('Blob missing from node')
                    b.drop_node_location(config.NODE_NAME)

    def _remove_stale_uploads(self):
        # Remove uploads which no longer exist in the database.
        uploads = []
        for u in upload.Uploads([]):
            uploads.append(u.uuid)

        upload_path = os.path.join(config.STORAGE_PATH, 'uploads')
        os.makedirs(upload_path, exist_ok=True)
        for upload_uuid in os.listdir(upload_path):
            if upload_uuid not in uploads:
                LOG.with_fields({
                    'upload': upload_uuid}).info('Removing stale upload')
                os.unlink(os.path.join(upload_path, upload_uuid))

    def _compact_etcd(self):
        try:
            # We need to determine what revision to compact to, so we keep a
            # key which stores when we last compacted and we use it's latest
            # revision number as the revision to compact to. Note that we use
            # command lines for compaction as etcd3gw primary library does
            # not support it. It is possible in modern etcd to have it auto
            # compact, but this is just as good and let's us log when it occurs.
            etcd.put_raw('/sf/compact', {'compacted_at': time.time()})

            # [(
            #     b'{"compacted_at": 1706677554.2498026}',
            #     {
            #         'key': b'/sf/compact',
            #         'create_revision': '236163240',
            #         'mod_revision': '594066920',
            #         'version': '13372'
            #     }
            # )]
            n = node.Node.from_db(config.NODE_NAME)
            kv = etcd.get_etcd_client().get('/sf/compact', metadata=True)[0][1]
            etcd.compact(int(kv['mod_revision']))
            n.add_event(EVENT_TYPE_STATUS, 'compacted etcd',
                        extra={'mod_revision': int(kv['mod_revision'])})

        except Exception as e:
            util_general.ignore_exception('etcd compaction', e)

    def _run_inner(self):
        daemon.health_check_privexec()
        last_defer_message = 0

        # Delay first compaction until system startup load has reduced
        last_compaction = time.time() - random.randint(1, 20*60)
        last_missing_blob_check = 0
        last_stale_upload_check = time.time() + 150
        last_libvirt_log_clean = 0

        n = node.Node.from_db(config.NODE_NAME)
        while daemon.check_abort_path(self.abort_path):
            if not self.cluster_stable():
                if time.time() - last_defer_message > 10:
                    LOG.info('Cluster not yet stable, deferring maintenance')
                    last_defer_message = time.time()
                continue

            # Update power state of all instances on this hypervisor
            with util_general.RecordedOperation('update power states', n,
                                                threshold=1):
                self._remove_stale_uploads

            with util_general.RecordedOperation('maintain blobs', n,
                                                threshold=1):
                self._maintain_blobs()

            scheduled_tasks.update_power_states()
            scheduled_tasks.remove_stray_lock_files()

            if time.time() - last_missing_blob_check > 300:
                with util_general.RecordedOperation('find missing blobs', n,
                                                    threshold=1):
                    self._find_missing_blobs()
                    last_missing_blob_check = time.time()

            if time.time() - last_stale_upload_check > 300:
                with util_general.RecordedOperation('remove stale uploads', n,
                                                    threshold=1):
                    self._remove_stale_uploads()
                    last_stale_upload_check = time.time()

            # Perform etcd maintenance, if we are an etcd master
            if config.NODE_IS_ETCD_MASTER:
                if time.time() - last_compaction > 1800:
                    with util_general.RecordedOperation('compact etcd', n,
                                                        threshold=1):
                        self._compact_etcd()
                        last_compaction = time.time()

            # Cleanup libvirt logs, but less frequently
            if time.time() - last_libvirt_log_clean > 1800:
                with util_general.RecordedOperation('libvirt log cleanup', n,
                                                    threshold=1):
                    scheduled_tasks.clear_old_libvirt_logs()
                    last_libvirt_log_clean = time.time()

            self.idle(60)


def main():
    daemon.write_pid_file('cleaner')
    m = Monitor('cleaner')
    m.run()

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    LOG.info('Terminating ourselves')
    raise SystemExit(0)
