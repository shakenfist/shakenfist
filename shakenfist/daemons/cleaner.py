import errno
import etcd3
import json
import os
import pathlib
import random
from shakenfist_utilities import logs
import shutil
import time

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.blob import Blob, Blobs
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.eventlog import EVENT_TYPE_AUDIT
from shakenfist import instance
from shakenfist import node
from shakenfist import upload
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt
from shakenfist.util import process as util_process


LOG, _ = logs.setup(__name__)


class Monitor(daemon.Daemon):
    def _delete_instance_files(self, instance_uuid):
        instance_path = os.path.join(
            config.STORAGE_PATH, 'instances', instance_uuid)
        if os.path.exists(instance_path):
            shutil.rmtree(instance_path)

        # And possibly an apparmor profile?
        libvirt_profile_path = '/etc/apparmor.d/libvirt/libvirt-' + instance_uuid
        if os.path.exists(libvirt_profile_path):
            os.unlink(libvirt_profile_path)
        libvirt_profile_path += '.files'
        if os.path.exists(libvirt_profile_path):
            os.unlink(libvirt_profile_path)

    def _update_power_states(self):
        with util_libvirt.LibvirtConnection() as lc:
            try:
                seen = []

                # Active VMs have an ID. Active means running in libvirt
                # land.
                for domain in lc.get_sf_domains():
                    instance_uuid = domain.name().split(':')[1]
                    log_ctx = LOG.with_fields({'instance': instance_uuid})
                    log_ctx.debug('Instance is running')

                    inst = instance.Instance.from_db(instance_uuid)
                    if not inst:
                        # Instance is SF but not in database. Kill to reduce load.
                        log_ctx.warning('Destroying unknown instance')
                        self._delete_instance_files(instance_uuid)
                        util_process.execute(
                            None, 'virsh destroy "sf:%s"' % instance_uuid)
                        util_process.execute(
                            None, 'virsh undefine --nvram "sf:%s"' % instance_uuid)
                        continue

                    inst.place_instance(config.NODE_NAME)
                    seen.append(domain.name())

                    db_state = inst.state
                    if db_state.value == dbo.STATE_DELETED:
                        # NOTE(mikal): a delete might be in-flight in the queue.
                        # We only worry about instances which should have gone
                        # away five minutes ago.
                        if time.time() - db_state.update_time < 300:
                            continue

                        attempts = inst.enforced_deletes_increment()
                        if attempts > 5:
                            # Sometimes we just can't delete the VM. Try the big
                            # hammer instead.
                            self._delete_instance_files(instance_uuid)
                            util_process.execute(
                                None, 'virsh undefine --nvram "sf:%s"' % instance_uuid)
                            inst.add_event(
                                EVENT_TYPE_AUDIT, 'enforced delete via alternate method')
                        else:
                            inst.delete()

                        log_ctx.with_fields({'attempt': attempts}).warning(
                            'Deleting stray instance')
                        continue

                    state = lc.extract_power_state(domain)
                    inst.update_power_state(state)
                    if state == 'crashed':
                        if inst.state.value in [dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED]:
                            util_process.execute(
                                None, 'virsh undefine --nvram "sf:%s"' % instance_uuid)
                            inst.state.value = dbo.STATE_DELETED
                        else:
                            inst.state = inst.state.value + '-error'

            except lc.libvirt.libvirtError as e:
                LOG.debug('Failed to lookup running domains: %s' % e)

            try:
                # Inactive VMs just have a name, and are powered off
                # in our state system.
                all_libvirt_uuids = []
                for domain in lc.get_all_domains():
                    domain_name = domain.name()
                    all_libvirt_uuids.append(domain.UUIDString())

                    if not domain_name.startswith('sf:'):
                        continue

                    if domain_name not in seen:
                        instance_uuid = domain_name.split(':')[1]
                        log_ctx = LOG.with_fields({'instance': instance_uuid})
                        inst = instance.Instance.from_db(instance_uuid)
                        log_ctx.debug('Inspecting absent instance')

                        if not inst:
                            # Instance is SF but not in database. Kill because
                            # unknown.
                            log_ctx.warning('Removing unknown inactive instance')
                            self._delete_instance_files(instance_uuid)
                            try:
                                # TODO(mikal): work out if we can pass
                                # VIR_DOMAIN_UNDEFINE_NVRAM with virDomainUndefineFlags()
                                domain.undefine()
                            except lc.libvirt.libvirtError:
                                util_process.execute(
                                    None, 'virsh undefine --nvram "sf:%s"' % instance_uuid)
                            continue

                        db_state = inst.state
                        if db_state.value in [dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED]:
                            # NOTE(mikal): a delete might be in-flight in the queue.
                            # We only worry about instances which should have gone
                            # away five minutes ago.
                            if time.time() - db_state.update_time < 300:
                                continue

                            self._delete_instance_files(instance_uuid)
                            try:
                                # TODO(mikal): work out if we can pass
                                # VIR_DOMAIN_UNDEFINE_NVRAM with virDomainUndefineFlags()
                                domain.undefine()
                            except lc.libvirt.libvirtError:
                                util_process.execute(
                                    None, 'virsh undefine --nvram "sf:%s"' % instance_uuid)

                            inst.add_event(EVENT_TYPE_AUDIT, 'deleted stray instance')
                            if db_state.value != dbo.STATE_DELETED:
                                inst.state.value = dbo.STATE_DELETED
                            continue

                        inst.place_instance(config.NODE_NAME)

                        db_power = inst.power_state
                        log_ctx.debug(
                            'Instance expected power state %s, actually off' % db_power)
                        if not os.path.exists(inst.instance_path):
                            # If we're inactive and our files aren't on disk,
                            # we have a problem.
                            inst.add_event(EVENT_TYPE_AUDIT, 'instance files missing')
                            if inst.state.value in [dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED]:
                                inst.state.value = dbo.STATE_DELETED
                            else:
                                inst.state = inst.state.value + '-error'

                        elif not db_power or db_power['power_state'] != 'off':
                            inst.update_power_state('off')
                            inst.add_event(EVENT_TYPE_AUDIT, 'detected poweroff')

            except lc.libvirt.libvirtError as e:
                LOG.debug('Failed to lookup all domains: %s' % e)

            # libvirt on Debian 11 fails to clean up apparmor profiles for VMs
            # which are no longer running, so we do that here. Note that this list
            # of UUIDs is _libvirt_ UUIDs, not SF UUIDs and includes _all_ VMs
            # defined on the hypervisor. SF _does_ however set the libvirt UUID
            # to match the SF UUID in libvirt.tmpl.
            libvirt_profile_path = '/etc/apparmor.d/libvirt'
            if os.path.exists(libvirt_profile_path):
                for ent in os.listdir(libvirt_profile_path):
                    if not ent.startswith('libvirt-'):
                        continue
                    if len(ent) not in [44, 50]:
                        continue

                    entpath = os.path.join(libvirt_profile_path, ent)
                    st = os.stat(entpath)
                    if time.time() - st.st_mtime < config.CLEANER_DELAY * 2:
                        continue

                    u = ent.replace('libvirt-', '').replace('.files', '')
                    if (u not in all_libvirt_uuids and
                            not os.path.exists(os.path.join(
                                config.STORAGE_PATH, 'instances', u))):
                        if os.path.isdir(entpath):
                            shutil.rmtree(entpath)
                        else:
                            os.unlink(entpath)
                        LOG.info(
                            'Removed old libvirt apparmor path %s' % entpath)

    def _clear_old_libvirt_logs(self):
        if not os.path.exists(config.LIBVIRT_LOG_PATH):
            return

        # Collect all valid instance UUIDs (that is, instances that have not
        # been hard deleted).
        all_instances = []
        for i in instance.all_instances():
            all_instances.append(i.uuid)

        # Now delete all libvirt log files which look like a SF instance, but
        # where the instance doesn't exist.
        for ent in os.listdir(config.LIBVIRT_LOG_PATH):
            if not ent.startswith('sf:'):
                continue

            uuid = ent.split(':')[1].split('.')[0]
            if uuid in all_instances:
                continue

            LOG.debug('Removing stale libvirt log %s' % ent)
            os.unlink(os.path.join(config.LIBVIRT_LOG_PATH, ent))

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
        all_node_blobs = n.blobs

        p = pathlib.Path(blob_path)
        for entpath in p.glob('**/*'):
            entpath = str(entpath)
            try:
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
            # a different library for compaction as our primary library does
            # not support it.
            c = etcd3.client()
            c.put('/sf/compact',
                  json.dumps({'compacted_at': time.time()}))
            _, kv = c.get('/sf/compact')
            c.compact(kv.mod_revision, physical=True)
            c.defragment()
            LOG.info('Compacted etcd')

        except Exception as e:
            util_general.ignore_exception('etcd compaction', e)

    def run(self):
        LOG.info('Starting')

        # Delay first compaction until system startup load has reduced
        last_compaction = time.time() - random.randint(1, 20*60)
        last_missing_blob_check = 0
        last_stale_upload_check = time.time() + 150
        last_libvirt_log_clean = 0

        while not self.exit.is_set():
            # Update power state of all instances on this hypervisor
            self._update_power_states()

            self._maintain_blobs()

            if time.time() - last_missing_blob_check > 300:
                self._find_missing_blobs()
                last_missing_blob_check = time.time()

            if time.time() - last_stale_upload_check > 300:
                self._remove_stale_uploads()
                last_stale_upload_check = time.time()

            # Perform etcd maintenance, if we are an etcd master
            if config.NODE_IS_ETCD_MASTER:
                if time.time() - last_compaction > 1800:
                    LOG.info('Compacting etcd')
                    self._compact_etcd()
                    last_compaction = time.time()

            # Cleanup libvirt logs, but less frequently
            if time.time() - last_libvirt_log_clean > 1800:
                self._clear_old_libvirt_logs()
                last_libvirt_log_clean = time.time()

            self.exit.wait(60)

        LOG.info('Terminated')
