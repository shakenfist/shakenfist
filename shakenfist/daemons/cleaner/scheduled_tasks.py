import json
import os
import shutil
import signal
import time

from shakenfist_utilities import logs                 # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.exceptions import ProcessExecutionError
from shakenfist import instance
from shakenfist import upload
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt


LOG, _ = logs.setup(__name__)


def _delete_instance_files(instance_uuid):
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


def _delete_with_virsh(instance_uuid, inst):
    log_ctx = LOG.with_fields({'instance': instance_uuid})
    try:
        log_ctx.warning('Destroying instance using virsh')
        util_concurrency.execute(
            f'virsh destroy "sf:{instance_uuid}"')
        util_concurrency.execute(
            f'virsh undefine --nvram "sf:{instance_uuid}"')
        _delete_instance_files(instance_uuid)
        log_ctx.warning('Destroying instance using virsh succeeded')
        if inst:
            inst.add_event(
                EVENT_TYPE_AUDIT,  'enforced delete via virsh method succeeded')
            return True

    except ProcessExecutionError:
        log_ctx.warning('Destroying instance using virsh failed')
        if inst:
            inst.add_event(
                EVENT_TYPE_AUDIT, 'enforced delete via virsh failed')
        return False


def _delete_with_kill(instance_uuid, inst):
    log_ctx = LOG.with_fields({'instance': instance_uuid})
    try:
        log_ctx.warning('Destroying instance using SIGKILL')
        stdout, _ = util_concurrency.execute('aa-status --json')
        status = json.loads(stdout)
        profile = f'libvirt-{instance_uuid}'
        for proc in status['processes']['/usr/bin/qemu-system-x86_64']:
            if proc['profile'] == profile:
                os.kill(int(proc['pid']), signal.SIGKILL)

        try:
            util_concurrency.execute(
                f'virsh undefine --nvram "sf:{instance_uuid}"')
        except ProcessExecutionError:
            pass

        _delete_instance_files(instance_uuid)
        log_ctx.warning('Destroying instance using SIGKILL succeeded')
        if inst:
            inst.add_event(
                EVENT_TYPE_AUDIT, 'enforced delete via SIGKILL succeeded')
    except ProcessExecutionError:
        log_ctx.warning('Destroying instance using SIGKILL failed')
        if inst:
            inst.add_event(
                EVENT_TYPE_AUDIT, 'enforced delete via SIGKILL failed')


@util_general.recorded_method
def update_power_states(pet_watchdog=None):
    # The cleaner runs this as a scheduled task from outside its idle() loop,
    # so the base watchdog pet does not fire while we are in here. On a busy
    # node the per-domain loops below perform database and lock operations
    # (notably place_instance) for every instance and can run longer than the
    # 60s systemd watchdog, which would have systemd SIGABRT a busy-but-healthy
    # cleaner mid-operation and strand the placement lock it holds. Pet
    # explicitly per domain. The pet is internally rate-limited (~10s), so
    # calling it every iteration is cheap.
    pet = pet_watchdog or (lambda: None)

    with util_libvirt.LibvirtConnection() as lc:
        try:
            seen = []

            # Active VMs have an ID. Active means running in libvirt
            # land.
            for domain in lc.get_sf_domains():
                pet()
                instance_uuid = domain.name().split(':')[1]
                log_ctx = LOG.with_fields({'instance': instance_uuid})
                log_ctx.debug('Instance is running')

                inst = instance.Instance.from_db(instance_uuid)
                if not inst:
                    # Instance is SF but not in database. Kill to reduce load.
                    if not _delete_with_virsh(instance_uuid, None):
                        _delete_with_kill(instance_uuid, None)
                    continue

                # P5: the cleaner records where a libvirt domain
                # already is, so its placement writes do not enforce
                # the capacity guard -- a guard cannot refuse reality.
                inst.place_instance(config.NODE_UUID, enforce=False)
                seen.append(domain.name())

                db_state = inst.state
                if db_state.value == dbo.STATE_DELETED:
                    # NOTE(mikal): a delete might be in-flight in the queue.
                    # We only worry about instances which should have gone
                    # away five minutes ago.
                    if time.time() - db_state.update_time < 300:
                        continue

                    attempts = inst.enforced_deletes_increment()
                    if attempts > 6:
                        # I give up.
                        pass

                    elif attempts > 4:
                        _delete_with_kill(instance_uuid, inst)

                    elif attempts > 2:
                        _delete_with_virsh(instance_uuid, inst)

                    else:
                        inst.delete()

                    log_ctx.with_fields({'attempt': attempts}).warning(
                        'Deleting stray instance')
                    continue

                state = lc.extract_power_state(domain)
                inst.update_power_state(state)
                if state == 'crashed':
                    if inst.state.value in [dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED]:
                        util_concurrency.execute(
                            f'virsh undefine --nvram "sf:{instance_uuid}"')
                        inst.state = dbo.STATE_DELETED
                    else:
                        inst.state = inst.state.value + '-error'

                elif state == 'paused':
                    # Paused is ambiguous: operators pause instances via the
                    # API, but qemu also pauses a guest when a disk operation
                    # fails (error_policy='stop' in our domain XML, or qemu's
                    # default ENOSPC write handling). The pause reason tells
                    # them apart. A guest paused by an I/O error has broken
                    # backing storage and cannot make progress. There is no
                    # recovery: an errored instance cannot return to created,
                    # so we mark it errored -- terminal, but still snapshottable
                    # and deletable -- exactly as the crashed branch above does,
                    # rather than enqueueing a delete and denying the operator a
                    # chance to salvage. The paused domain is left in place as
                    # forensic state until the operator deletes it.
                    if lc.is_paused_ioerror(domain):
                        if inst.state.value in [dbo.STATE_DELETE_WAIT,
                                                dbo.STATE_DELETED]:
                            # A delete was already in flight. Unlike a crashed
                            # domain (whose qemu has already exited, so an
                            # undefine suffices) an I/O error paused domain
                            # still has a live qemu process, so it must be
                            # destroyed.
                            if not _delete_with_virsh(instance_uuid, inst):
                                _delete_with_kill(instance_uuid, inst)
                            inst.state = dbo.STATE_DELETED
                        elif (inst.state.value not in
                                instance.Instance.ERROR_STATES):
                            # Guard against re-entry: the paused domain lingers
                            # until the operator deletes it, so we observe it
                            # on every poll. Only record the error once -- and
                            # note '<state>-error' -> '<state>-error-error' is
                            # not a valid transition, so re-marking would
                            # raise.
                            disk_errors = lc.extract_disk_errors(domain)
                            errors = ', '.join(
                                f'{dev}: {err}' for dev, err
                                in sorted(disk_errors.items()))
                            inst.add_event(
                                EVENT_TYPE_AUDIT,
                                'instance paused by disk I/O error',
                                extra={'disk_errors': disk_errors})
                            log_ctx.with_fields(
                                {'disk_errors': disk_errors}).warning(
                                'Instance paused by disk I/O error, '
                                'marking errored')
                            # State must move to the error state before error
                            # is set: the error setter rejects a message
                            # unless the instance is already errored.
                            inst.state = inst.state.value + '-error'
                            inst.error = (
                                'instance paused by disk I/O error' +
                                (f' ({errors})' if errors else ''))

        except lc.libvirt.libvirtError as e:
            LOG.debug(f'Failed to lookup running domains: {e}')

        try:
            # Inactive VMs just have a name, and are powered off
            # in our state system.
            all_libvirt_uuids = []
            for domain in lc.get_all_domains():
                pet()
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
                        _delete_instance_files(instance_uuid)
                        try:
                            # TODO(mikal): work out if we can pass
                            # VIR_DOMAIN_UNDEFINE_NVRAM with virDomainUndefineFlags()
                            domain.undefine()
                        except lc.libvirt.libvirtError:
                            util_concurrency.execute(
                                f'virsh undefine --nvram "sf:{instance_uuid}"')
                        continue

                    db_state = inst.state
                    if db_state.value in [dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED]:
                        # NOTE(mikal): a delete might be in-flight in the queue.
                        # We only worry about instances which should have gone
                        # away five minutes ago.
                        if time.time() - db_state.update_time < 300:
                            continue

                        _delete_instance_files(instance_uuid)
                        try:
                            # TODO(mikal): work out if we can pass
                            # VIR_DOMAIN_UNDEFINE_NVRAM with virDomainUndefineFlags()
                            domain.undefine()
                        except lc.libvirt.libvirtError:
                            util_concurrency.execute(
                                f'virsh undefine --nvram "sf:{instance_uuid}"')

                        inst.add_event(EVENT_TYPE_AUDIT,
                                       'deleted stray instance')
                        if db_state.value != dbo.STATE_DELETED:
                            inst.state = dbo.STATE_DELETED
                        continue

                    # P5 again: ground truth, not a scheduling
                    # decision, so the guard is not enforced.
                    inst.place_instance(config.NODE_UUID, enforce=False)

                    db_power = inst.power_state
                    log_ctx.debug(
                        f'Instance expected power state {db_power}, actually off')
                    if not os.path.exists(inst.instance_path):
                        # If we're inactive and our files aren't on disk,
                        # we have a problem.
                        inst.add_event(EVENT_TYPE_AUDIT,
                                       'instance files missing')
                        if inst.state.value in [dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED]:
                            inst.state = dbo.STATE_DELETED
                        else:
                            inst.state = inst.state.value + '-error'

                    elif not db_power or db_power['power_state'] != 'off':
                        inst.update_power_state('off')
                        inst.add_event(EVENT_TYPE_AUDIT, 'detected poweroff')

        except lc.libvirt.libvirtError as e:
            LOG.debug(f'Failed to lookup all domains: {e}')

        # libvirt on Debian 11 fails to clean up apparmor profiles for VMs
        # which are no longer running, so we do that here. Note that this list
        # of UUIDs is _libvirt_ UUIDs, not SF UUIDs and includes _all_ VMs
        # defined on the hypervisor. SF _does_ however set the libvirt UUID
        # to match the SF UUID in libvirt.tmpl.
        libvirt_profile_path = '/etc/apparmor.d/libvirt'
        if os.path.exists(libvirt_profile_path):
            for ent in os.listdir(libvirt_profile_path):
                pet()
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
                        f'Removed old libvirt apparmor path {entpath}')


@util_general.recorded_method
def clear_old_libvirt_logs():
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

        LOG.debug(f'Removing stale libvirt log {ent}')
        os.unlink(os.path.join(config.LIBVIRT_LOG_PATH, ent))


@util_general.recorded_method
def remove_stale_uploads_for_this_node():
    upload.remove_stale_uploads_for_this_node()
