import contextlib
import json
import os
import shutil
import signal
import time
from uuid import UUID

from shakenfist_utilities import logs                 # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import AGENT_INSTANCE_OFF
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import exceptions
from shakenfist.exceptions import ProcessExecutionError
from shakenfist import instance
from shakenfist import mariadb
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
        # Success does not depend on having an instance to record it
        # against. An unknown domain has none, and returning None here sent
        # its caller on to _delete_with_kill() after a successful destroy.
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


def _sf_instance_uuid(domain):
    """The instance uuid a Shaken Fist domain belongs to, or None.

    The name's suffix is trusted only when it equals the domain's libvirt
    uuid, which libvirt.tmpl sets to the instance uuid. The suffix is used
    to find the instance, as a path under STORAGE_PATH, and in virsh
    command lines, so a domain named "sf:" (which would rmtree the whole
    instances directory) or "sf:garbage" must never get that far. A
    mismatched domain is skipped entirely: no lookup, no delete, no virsh.
    See S6 and D4 in
    docs/plans/PLAN-power-state-correctness-phase-01b-inactive-domains.md.
    """
    domain_name = domain.name()
    if not domain_name.startswith('sf:'):
        return None

    suffix = domain_name[len('sf:'):]
    domain_uuid = domain.UUIDString()
    if not suffix or suffix != domain_uuid:
        LOG.with_fields({
            'domain_name': domain_name,
            'domain_uuid': domain_uuid
        }).warning('Ignoring domain whose name does not match its uuid')
        return None
    return suffix


def _instance_confirmed_absent(instance_uuid):
    """True only if a strict lookup agrees that the instance does not exist.

    Instance.from_db() reports a non-retryable database error as a miss
    (issue #3373), and the callers of this delete a domain and its disks
    on a miss. So before deleting, look again with a lookup which raises
    on any error instead. See S5 and D3 in
    docs/plans/PLAN-power-state-correctness-phase-01b-inactive-domains.md.
    """
    log_ctx = LOG.with_fields({'instance': instance_uuid})
    try:
        row = mariadb.get_instance(UUID(instance_uuid), strict=True)
    except exceptions.DatabaseUnavailable as e:
        log_ctx.warning(
            'Could not confirm that an unknown domain has no instance, not '
            f'deleting it this pass (see issue #3373): {e}')
        return False

    if row:
        # The first lookup was the error. The next pass handles the
        # instance normally.
        log_ctx.warning(
            'Instance lookup missed but a strict lookup found it, not '
            'deleting its domain')
        return False
    return True


@contextlib.contextmanager
def _instance_lock(inst, pet):
    """Hold the instance's node lock with a bounded wait.

    Yields True with the lock held, or False if it could not be taken, in
    which case the caller skips the instance for this pass and the next
    pass retries. Every other writer of a domain's power state holds this
    lock, so a caller which re-reads the domain inside it cannot observe
    a power on, power off or delete half done. See S2 and D2 in
    docs/plans/PLAN-power-state-correctness-phase-01b-inactive-domains.md.
    """
    # A holder killed by the systemd watchdog would strand the lock until
    # sf-nodelock restarts, so pet before taking it.
    pet()
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(inst.get_lock(
                op='Cleaner power state update', global_scope=False,
                node_timeout=2))
        except (exceptions.NodeLockTimeout,
                exceptions.MissingNodeLockSocket) as e:
            LOG.with_fields({'instance': inst.uuid}).debug(
                f'Instance is busy, skipping it this pass: {e}')
            yield False
            return
        yield True


def _active_domain_needs_write(lc, inst, db_state, domain):
    """Whether this pass's reading of an active domain calls for a write.

    When it does not, the caller takes no lock: that is the steady state,
    and S14 in
    docs/plans/PLAN-power-state-correctness-phase-01b-inactive-domains.md
    relies on it costing the database nothing extra.
    """
    state = lc.extract_power_state(domain)
    if state != inst.power_state.get('power_state'):
        return True

    if state == 'crashed':
        return True

    if state == 'paused' and lc.is_paused_ioerror(domain):
        return db_state.value not in instance.Instance.ERROR_STATES

    return False


def _update_active_domain(lc, inst, instance_uuid, domain, log_ctx):
    """Record an active domain's power state, and act on a crashed or I/O
    error paused domain. The caller holds the instance lock, and domain
    was looked up inside it."""
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


# Instance states whose domain is still being built. Creates hold the
# instance lock, so the cleaner could not observe one half done anyway
# (S2 in docs/plans/PLAN-power-state-correctness-phase-01b-inactive-domains.md),
# but skipping them costs nothing.
_BUILDING_STATES = {
    dbo.STATE_INITIAL, instance.Instance.STATE_PREFLIGHT, dbo.STATE_CREATING
}

# A delete-wait instance with a stray powered off domain has its delete
# enqueued again on these counts of its enforced deletes counter, which
# goes up once a pass (about a minute apart), and is given up on at the
# last. See D5.
_STRAY_DELETE_ENQUEUE_ATTEMPTS = (1, 6, 11)
_STRAY_DELETE_ABANDON_ATTEMPT = 16


def _undefine_domain(lc, domain, instance_uuid):
    try:
        # TODO(mikal): work out if we can pass
        # VIR_DOMAIN_UNDEFINE_NVRAM with virDomainUndefineFlags()
        domain.undefine()
    except lc.libvirt.libvirtError:
        util_concurrency.execute(
            f'virsh undefine --nvram "sf:{instance_uuid}"')


def _inactive_domain_inside_lock(lc, instance_uuid, log_ctx):
    """Look an inactive domain up again, with the instance lock held.

    Returns the fresh domain, or None if it has since been undefined or
    started, in which case the caller skips it: a power on or delete
    finished while this pass was running, and the next pass reads the
    domain afresh.
    """
    domain = lc.get_domain_from_sf_uuid(instance_uuid)
    if not domain or domain.isActive():
        log_ctx.debug(
            'Domain is no longer defined and inactive, skipping it this pass')
        return None
    return domain


def _enqueue_stray_delete(inst, log_ctx):
    """Re-enqueue the delete of a delete-wait instance whose domain lingers.

    The queued delete also removes the instance's interfaces and cleans up
    networks no longer used on this node, which a delete done in place
    would skip and leak (S3 and D5). The caller holds the instance lock.
    """
    attempts = inst.enforced_deletes_increment()
    log_ctx = log_ctx.with_fields({'attempt': attempts})
    if attempts in _STRAY_DELETE_ENQUEUE_ATTEMPTS:
        log_ctx.warning('Enqueueing delete of stray powered off instance')
        inst.enqueue_delete()
        inst.add_event(
            EVENT_TYPE_AUDIT, 'stray powered off instance delete enqueued',
            extra={'attempt': attempts})

    elif attempts == _STRAY_DELETE_ABANDON_ATTEMPT:
        log_ctx.error('Giving up on deleting stray powered off instance')
        inst.add_event(
            EVENT_TYPE_AUDIT, 'stray powered off instance delete abandoned',
            extra={'attempt': attempts}, log_as_error=True)


def _update_inactive_domain(lc, domain, instance_uuid, pet):
    """Reconcile one inactive Shaken Fist domain with its instance.

    Every write, delete and enqueued delete below happens holding the
    instance lock, from a state and a domain read again inside it (D2).
    """
    log_ctx = LOG.with_fields({'instance': instance_uuid})
    log_ctx.debug('Inspecting inactive instance')

    inst = instance.Instance.from_db(instance_uuid)
    if not inst:
        if not _instance_confirmed_absent(instance_uuid):
            return

        # Instance is SF but not in database. Kill because unknown.
        log_ctx.warning('Removing unknown inactive instance')
        _delete_instance_files(instance_uuid)
        _undefine_domain(lc, domain, instance_uuid)
        return

    db_state = inst.state
    if db_state.value in _BUILDING_STATES:
        log_ctx.with_fields({'state': db_state.value}).debug(
            'Instance is still being created, skipping it')
        return

    if db_state.value in [dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED]:
        # NOTE(mikal): a delete might be in-flight in the queue.
        # We only worry about instances which should have gone
        # away five minutes ago.
        if time.time() - db_state.update_time < 300:
            return

        with _instance_lock(inst, pet) as locked:
            if not locked:
                return
            if inst.state.value != db_state.value:
                log_ctx.debug('Instance state changed, skipping it this pass')
                return
            domain = _inactive_domain_inside_lock(lc, instance_uuid, log_ctx)
            if not domain:
                return

            if db_state.value == dbo.STATE_DELETED:
                # The global half of the delete has run, and a queued
                # delete would return early, so tear down the local half
                # here. The state is already deleted, and is not written
                # again (S4).
                log_ctx.warning('Deleting stray powered off instance')
                _delete_instance_files(instance_uuid)
                _undefine_domain(lc, domain, instance_uuid)
                inst.add_event(EVENT_TYPE_AUDIT, 'deleted stray instance')
            else:
                _enqueue_stray_delete(inst, log_ctx)
        return

    # P5 again: ground truth, not a scheduling decision, so the guard is
    # not enforced.
    inst.place_instance(config.NODE_UUID, enforce=False)

    if not os.path.exists(inst.instance_path):
        # If we're inactive and our files aren't on disk, we have a
        # problem. Record it once: the domain lingers, so every pass sees
        # it, and '<state>-error-error' (or 'error-error') is not a valid
        # transition (S8, D6).
        if (not db_state.value or
                db_state.value in instance.Instance.ERROR_STATES):
            log_ctx.debug('Instance files missing, already errored')
            return

        with _instance_lock(inst, pet) as locked:
            if not locked:
                return
            if inst.state.value != db_state.value:
                log_ctx.debug('Instance state changed, skipping it this pass')
                return
            if not _inactive_domain_inside_lock(lc, instance_uuid, log_ctx):
                return
            if os.path.exists(inst.instance_path):
                return

            log_ctx.warning('Instance files missing, marking errored')
            # State must move to the error state before error is set: the
            # error setter rejects a message unless the instance is
            # already errored.
            inst.state = db_state.value + '-error'
            inst.error = 'instance files missing'
            inst.add_event(EVENT_TYPE_AUDIT, 'instance files missing')
        return

    # When the database already says off there is nothing to write, and no
    # lock is taken (S14).
    if inst.power_state.get('power_state') == 'off':
        return

    with _instance_lock(inst, pet) as locked:
        if not locked:
            return
        domain = _inactive_domain_inside_lock(lc, instance_uuid, log_ctx)
        if not domain:
            return

        previous = inst.power_state.get('power_state')
        if previous == 'off':
            # A power off finished while we waited for the lock, and
            # recorded itself.
            return

        # The order is load-bearing: callers wait for the event, then read
        # the power state once (D7). The shutoff reason is display only: a
        # powered off domain is off, whatever the reason.
        log_ctx.with_fields({'previous_power_state': previous}).info(
            'Detected power off')
        inst.update_power_state('off')
        inst.agent_state = AGENT_INSTANCE_OFF
        inst.add_event(
            EVENT_TYPE_AUDIT, 'detected poweroff',
            extra={
                'reason': lc.extract_shutoff_reason(domain),
                'previous_power_state': previous
            })


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
            for domain in lc.get_active_sf_domains():
                pet()
                instance_uuid = _sf_instance_uuid(domain)
                if instance_uuid is None:
                    continue
                log_ctx = LOG.with_fields({'instance': instance_uuid})
                log_ctx.debug('Instance is running')

                inst = instance.Instance.from_db(instance_uuid)
                if not inst:
                    if not _instance_confirmed_absent(instance_uuid):
                        continue

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

                # When the database already agrees with libvirt there is
                # nothing to write, and no lock is taken (S14).
                if not _active_domain_needs_write(lc, inst, db_state, domain):
                    continue

                # Otherwise write under the instance lock, from a domain
                # looked up again inside it, so that a power operation which
                # finished while this pass was running is not overwritten by
                # a stale reading (F13, D2 in
                # docs/plans/PLAN-power-state-correctness-phase-01b-inactive-domains.md).
                with _instance_lock(inst, pet) as locked:
                    if not locked:
                        continue

                    domain = lc.get_domain_from_sf_uuid(instance_uuid)
                    if not domain or not domain.isActive():
                        # The second loop's next pass owns an inactive
                        # domain.
                        log_ctx.debug(
                            'Domain is no longer active, skipping it this pass')
                        continue

                    _update_active_domain(
                        lc, inst, instance_uuid, domain, log_ctx)

        except lc.libvirt.libvirtError as e:
            LOG.debug(f'Failed to lookup running domains: {e}')

        try:
            # Defined but not running: a guest which powered itself off, a
            # qemu which died, or a domain a delete left behind. A domain
            # the first loop handled and which went inactive since is in
            # `seen`, and waits for the next pass.
            for domain in lc.get_inactive_sf_domains():
                pet()
                instance_uuid = _sf_instance_uuid(domain)
                if instance_uuid is None:
                    continue
                if domain.name() in seen:
                    continue

                _update_inactive_domain(lc, domain, instance_uuid, pet)

        except lc.libvirt.libvirtError as e:
            LOG.debug(f'Failed to lookup inactive domains: {e}')

        # libvirt on Debian 11 fails to clean up apparmor profiles for VMs
        # which are no longer running, so we do that here. Note that this list
        # of UUIDs is _libvirt_ UUIDs, not SF UUIDs and includes _all_ VMs
        # defined on the hypervisor, active or inactive, including foreign
        # (non-SF) ones. SF _does_ however set the libvirt UUID to match the
        # SF UUID in libvirt.tmpl. The list is fetched here rather than built
        # by the loops above, and if it cannot be fetched the sweep is skipped:
        # an empty list would make every old profile look deletable (S7 and D8
        # in docs/plans/PLAN-power-state-correctness-phase-01b-inactive-domains.md).
        try:
            all_libvirt_uuids = lc.get_all_domain_uuids()
        except lc.libvirt.libvirtError as e:
            LOG.warning(
                f'Failed to list all domains, skipping apparmor sweep: {e}')
            all_libvirt_uuids = None

        libvirt_profile_path = '/etc/apparmor.d/libvirt'
        if all_libvirt_uuids is not None and os.path.exists(libvirt_profile_path):
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
