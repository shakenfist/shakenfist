import errno
import os
import pathlib
import time

import schedule
from shakenfist_utilities import logs  # noreorder

from shakenfist import exceptions
from shakenfist import instance
from shakenfist import mariadb
from shakenfist import node
from shakenfist.blob import Blob
from shakenfist.blob import observe_local_blobs
from shakenfist.config import config
from shakenfist.daemons.cleaner import scheduled_tasks
from shakenfist.daemons import daemon
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


def _resilient_job(job_func, *args):
    """Wrap a scheduled task so a failure cannot starve the scheduler.

    schedule.Job.run() only sets last_run and reschedules after job_func
    returns, so a job which raises is left permanently overdue: it sorts
    first in run_pending() and its exception aborts the tick before any
    other due job runs, killing every scheduled task forever (github
    issue 3490). Catching here means a failing task is logged and all
    jobs always reschedule.
    """
    def wrapper():
        try:
            job_func(*args)
        except Exception as e:
            util_exceptions.ignore_exception(
                f'cleaner scheduled task {job_func.__name__}', e)
    return wrapper


class Monitor(daemon.Daemon):
    def _maintain_blobs(self):
        # Find orphaned and deleted blobs still on disk
        blob_path = os.path.join(config.STORAGE_PATH, 'blobs')
        os.makedirs(blob_path, exist_ok=True)
        cache_path = os.path.join(config.STORAGE_PATH, 'image_cache')
        os.makedirs(cache_path, exist_ok=True)

        # This list is used below as a complement set: any blob file on
        # disk whose uuid is absent from it is deleted. That makes an
        # unreadable list actively dangerous rather than merely
        # unhelpful, so skip the pass entirely -- the blobs are still
        # there next time round, and a delayed sweep costs nothing that
        # a wrongly emptied blob store does not cost far more of.
        #
        # It is a set for the same reason: the membership test below runs
        # once per file in the blob store, and the reply that motivated
        # #3638 held on the order of 10^5 uuids.
        try:
            active_blob_uuids = set(mariadb.get_active_blob_uuids())
        except exceptions.DatabaseUnavailable as e:
            LOG.with_fields({'error': str(e)}).warning(
                'Could not read the active blob list, skipping blob '
                'maintenance this pass')
            return

        n = node.Node.from_db(config.NODE_NAME, suppress_failure_audit=True)
        if not n:
            # We have not started up enough yet to exist in the database.
            # That's an expected startup race, not an error, so the lookup
            # failure audit is suppressed above.
            return

        all_node_blobs = set(n.blobs)

        try:
            p = pathlib.Path(blob_path)
            for entpath in p.glob('**/*'):
                self.pet_watchdog()

                entpath = str(entpath)
                if not os.path.isfile(entpath):
                    continue

                # Blob files are named for their UUID, with a .partial
                # suffix during transfer. Anything else in the store
                # (_version markers, the resource health _heartbeat
                # sentinel) is not ours to garbage collect.
                blob_uuid = entpath.split('/')[-1].replace('.partial', '')
                if not util_general.valid_uuid4(blob_uuid):
                    continue

                st = os.stat(entpath)

                # If we've had this file for more than two cleaner delays...
                if time.time() - st.st_mtime > config.CLEANER_DELAY * 2:
                    if entpath.endswith('.partial'):
                        # ... and its a stale partial transfer
                        LOG.with_fields({'blob': blob_uuid}).warning(
                            'Deleting stale partial transfer')
                        os.unlink(entpath)

                    else:
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

                        entdir = os.path.dirname(entpath)
                        if len(os.listdir(entdir)) == 0:
                            LOG.with_fields({'path': entpath}).debug(
                                'Removing now empty directory')
                            os.rmdir(entdir)
        except FileNotFoundError:
            ...

        # Find transcoded blobs in the image cache which are no longer in use
        for ent in os.listdir(cache_path):
            self.pet_watchdog()
            entpath = os.path.join(cache_path, ent)

            # Image cache entries are named for the UUID of their source
            # blob plus a format extension; skip anything else, such as
            # the resource health _heartbeat sentinel. This filter must
            # come before the broken symlink check so a dangling
            # non-object entry is skipped, not deleted.
            blob_uuid = ent.split('.')[0]
            if not util_general.valid_uuid4(blob_uuid):
                continue

            # Broken symlinks will report an error here that we have to catch
            try:
                st = os.stat(entpath)
            except OSError as e:
                if e.errno == errno.ENOENT:
                    LOG.with_fields({
                        'blob': ent}).warning('Deleting broken symlinked image cache entry')
                    try:
                        os.unlink(entpath)
                    except FileNotFoundError:
                        # The entry vanished between listdir and here.
                        pass
                    continue
                else:
                    raise e

            # If we haven't seen this file in use for more than two cleaner delays...
            if time.time() - st.st_mtime > config.CLEANER_DELAY * 2:
                b = Blob.from_db(blob_uuid)
                if not b:
                    LOG.with_fields({
                        'blob': ent}).warning('Deleting orphaned image cache entry')
                    os.unlink(entpath)
                    continue

                this_node = len(instance.instance_usage_for_blob_uuid(
                    b.uuid, node=config.NODE_UUID))
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
        n = node.Node.from_db(config.NODE_NAME, suppress_failure_audit=True)
        if not n:
            # Not in the database yet, or removed from the cluster. Either
            # way there is nothing to check and it isn't an error.
            return

        for blob_uuid in n.blobs:
            self.pet_watchdog()
            if not os.path.exists(Blob.filepath(blob_uuid)):
                b = Blob.from_db(blob_uuid, suppress_failure_audit=True)
                if b:
                    LOG.with_fields({
                        'blob': blob_uuid}).warning('Blob missing from node')
                    b.drop_node_location(config.NODE_NAME)

    def _run_inner(self):
        schedule.every(1).minutes.do(_resilient_job(
            scheduled_tasks.update_power_states, self.pet_watchdog))
        schedule.every(5).minutes.do(_resilient_job(
            scheduled_tasks.remove_stale_uploads_for_this_node))
        schedule.every(5).minutes.do(_resilient_job(observe_local_blobs))

        last_defer_message = 0

        last_missing_blob_check = 0
        last_libvirt_log_clean = 0

        # This is only used to attribute recorded operations to this node, and
        # the node record might not exist yet this early in startup. That's
        # expected, so don't audit the miss -- just retry once a pass until
        # the record shows up.
        n = node.Node.from_db(config.NODE_NAME, suppress_failure_audit=True)
        while daemon.check_abort_path(self.abort_path):
            self.wait_for_nodelock()

            if not n:
                n = node.Node.from_db(
                    config.NODE_NAME, suppress_failure_audit=True)

            if not self.cluster_stable():
                if time.time() - last_defer_message > 10:
                    LOG.info('Cluster not yet stable, deferring maintenance')
                    last_defer_message = time.time()
                self.idle(60)
                continue

            # Pet before the scheduled tasks below; they are not instrumented
            # with their own pets and run before _maintain_blobs's per-loop pets.
            self.pet_watchdog()
            try:
                with util_general.RecordedOperation(
                        'scheduled node operations', None, threshold=10):
                    schedule.run_pending()
            except Exception as e:
                util_exceptions.ignore_exception('node', e)

            with util_general.RecordedOperation('maintain blobs', n,
                                                threshold=1):
                self._maintain_blobs()

            if time.time() - last_missing_blob_check > 300:
                with util_general.RecordedOperation('find missing blobs', n,
                                                    threshold=1):
                    self._find_missing_blobs()
                    last_missing_blob_check = time.time()

            # Cleanup libvirt logs, but less frequently
            if time.time() - last_libvirt_log_clean > 1800:
                with util_general.RecordedOperation('libvirt log cleanup', n,
                                                    threshold=1):
                    scheduled_tasks.clear_old_libvirt_logs()
                    last_libvirt_log_clean = time.time()

            self.idle(60)


def main():
    util_exceptions.install_exception_tracking()
    daemon.write_pid_file('cleaner')
    m = Monitor('cleaner')

    while not daemon.health_check_nodelock():
        LOG.info('Waiting for nodelock daemon to be healthy')
        time.sleep(1)
    LOG.info('nodelock daemon reports healthy')

    m.run()

    daemon.force_clean_exit()
