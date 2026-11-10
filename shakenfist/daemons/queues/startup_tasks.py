import json
import os
import pathlib
import threading
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import instance
from shakenfist import locks
from shakenfist import mariadb
from shakenfist.network import network
from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist.config import redacted_config_items
from shakenfist.constants import get_object_class
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.daemons import daemon
from shakenfist.exceptions import InvalidStateException
from shakenfist.network.interface import interfaces_for_instance
from shakenfist.node import Node
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import get_general_background_node_queues
from shakenfist.operations.baseoperation import get_general_user_facing_node_queues
from shakenfist.operations.baseoperation import get_node_background_node_queues
from shakenfist.operations.baseoperation import get_node_user_facing_node_queues
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import general as util_general
from shakenfist.util import json as util_json
from shakenfist.util import libvirt as util_libvirt


LOG, HANDLER = logs.setup('main')


def upgrade_blob_datastore():
    # Upgrades for the actual underlying blob data store
    version_path = os.path.join(config.STORAGE_PATH, 'blobs', '_version')
    if os.path.exists(version_path):
        with open(version_path) as f:
            version = json.loads(f.read())['version']
    else:
        LOG.with_fields({'path': version_path}).info(
            'Blob data store version file missing while resharding')
        version = 1

    start_version = version
    start_time = time.time()

    if version == 1:
        # Version two is sharded.
        version = 2
        count = 0

        relocations = {}
        image_cache_path = os.path.join(config.STORAGE_PATH, 'image_cache')
        os.makedirs(image_cache_path, exist_ok=True)
        for ent in os.listdir(image_cache_path):
            entpath = os.path.join(image_cache_path, ent)
            if os.path.islink(entpath):
                dest = str(pathlib.Path(entpath).resolve())
                if 'blobs' in dest:
                    blob_uuid = dest.split('/')[-1]
                    relocations[blob_uuid] = entpath

        n = Node.from_db(config.NODE_NAME)
        if n:
            for blob_uuid in n.blobs:
                old_blob_path = os.path.join(config.STORAGE_PATH, 'blobs',
                                             blob_uuid)
                new_blob_path = Blob.filepath(blob_uuid)

                if not os.path.exists(old_blob_path):
                    LOG.warning(
                        'Not moving blob %s from %s to %s as it is missing '
                        'from disk' % (blob_uuid, old_blob_path, new_blob_path))
                else:
                    LOG.info('Moving blob %s from %s to %s'
                             % (blob_uuid, old_blob_path, new_blob_path))
                    os.rename(old_blob_path, new_blob_path)

                    if blob_uuid in relocations:
                        cache_entry = relocations[blob_uuid]
                        LOG.info(
                            'Relocating image cache entry %s to new blob '
                            'path %s' % (cache_entry, new_blob_path))
                        os.unlink(cache_entry)
                        os.symlink(new_blob_path, cache_entry)

                count += 1

        if count > 0:
            LOG.info('Resharded %d blobs' % count)

    if start_version != version:
        os.makedirs(os.path.dirname(version_path), exist_ok=True)
        with open(version_path, 'w') as f:
            f.write(util_json.json_dump({'version': version}))
        LOG.info('Blob datastore upgrade took %.02f seconds'
                 % (time.time() - start_time))


def restore_instances():
    # Collect this node's instances so that their networks can be restored,
    # their placements reconciled, and the ones this node was running
    # started again once their networks are back.
    networks = []
    instances = []
    powered_on = []
    for inst in instance.Instances([instance.this_node_filter], prefilter='healthy'):
        instance_problems = []

        # ``inst.interfaces`` queries network_interfaces live, so the
        # cache-sync block that used to maintain a denormalised list
        # here is no longer needed.
        for ni in interfaces_for_instance(inst):
            if ni.network_uuid not in networks:
                networks.append(ni.network_uuid)

        # TODO(mikal): do better here.
        # for disk in inst.disk_spec:
        #     if disk.get('base'):
        #         img = images.Image.new(disk['base'])
        #         # NOTE(mikal): this check isn't great -- it checks for the original
        #         # downloaded image, not the post transcode version
        #         if (img.state in [dbo.STATE_DELETED, dbo.STATE_ERROR] or
        #                 not os.path.exists(img.version_image_path())):
        #             instance_problems.append(
        #                 '%s missing from image cache' % disk['base'])
        #             img.delete()

        if instance_problems:
            inst.enqueue_delete_due_error(
                'instance bad on startup: %s' % '; '.join(instance_problems))
        else:
            instances.append(inst)

            # Which instances this node was running, read now rather than
            # after the network restore below, which can take minutes. An
            # instance still being created is not ours to start: its create
            # holds the instance lock and powers it on itself.
            if (inst.state.value == instance.Instance.STATE_CREATED and
                    inst.power_state.get('power_state') == 'on'):
                powered_on.append(inst)

    # Restore each network sequentially, with a generous per-op
    # deadline. Startup is not a user-facing context so the default
    # ``API_ASYNC_WAIT`` is the wrong yardstick here -- if the cluster
    # is busy bringing other nodes up, individual ops can sit in the
    # queue minutes before they reach a worker. A timeout that fires
    # here drops the exception on the floor (see the except below) and
    # leaves the network restored only partially, which manifests as
    # broken VXLAN connectivity in subsequent tests.
    for network_uuid in networks:
        try:
            n = network.Network.from_db(network_uuid)
            if not n.is_dead():
                LOG.with_fields({'network': n}).info('Restoring network')
                create_op = n.create_on_hypervisor()
                create_op.raise_for_error(timeout=600)
                mesh_op = n.ensure_mesh()
                mesh_op.raise_for_error(timeout=600)
        except Exception as e:
            util_exceptions.ignore_exception(
                'restore network %s' % network_uuid, e)

    # The networks are back, so the instances this node was running can
    # run again. This goes before the placement reconciliation below so
    # that a failure there -- an unreadable node, a database outage --
    # cannot leave the node's instances powered off.
    _restore_powered_on_instances(powered_on)

    # Reconcile the recorded instance placements for this node: add any
    # missing INSTANCE_LOCATION references, and remove stale ones. The
    # network restore and power on above can take many minutes, so the
    # instances list gathered at its start is a stale snapshot -- before
    # removing a reference, re-check the instance's authoritative
    # placement so a concurrently placed instance is never unrecorded.
    #
    # Every write here goes through the placement admission and release
    # RPCs rather than writing reference rows directly, so this
    # reconciliation cannot itself produce the duplicate placement rows
    # the RPCs exist to eliminate: an admission deletes every
    # INSTANCE_LOCATION row for the instance before inserting the one it
    # wrote. These are ground-truth writes -- the placement attribute is
    # the authority and is deliberately left exactly as it is, right down
    # to the placement_attempts count -- so they do not enforce the
    # capacity guard (P5).
    desired = {str(inst.uuid): inst for inst in instances}
    n = Node.from_db(config.NODE_NAME)
    node_uuid = str(n.uuid)
    current = set(n.instances)

    for instance_uuid in set(desired) - current:
        _reconcile_placement(desired[instance_uuid], node_uuid, node_uuid)

    for instance_uuid in current - set(desired):
        inst = instance.Instance.from_db(instance_uuid)
        if inst:
            placement = inst.placement
            placed_on = placement.get('node')
            if (placed_on == node_uuid and
                    inst.state.value not in instance.Instance.TERMINAL_STATES):
                # Placed here after our snapshot was taken; keep it.
                continue
            if (placed_on and
                    inst.state.value not in instance.Instance.TERMINAL_STATES):
                # Live, but somewhere else. Recording it where it
                # actually is removes our stale row as a side effect of
                # the admission's delete-all-then-insert, and moves the
                # capacity charge to the node which is really carrying
                # it.
                _reconcile_placement(inst, placed_on, node_uuid)
                continue

            # Gone, or on its way out. Give the capacity back.
            _release_placement(
                instance_uuid, inst.namespace, inst.cpus, inst.memory,
                mariadb.disk_spec_virtual_gb(inst.disk_spec), node_uuid)
            continue

        # No instance row at all, so there is nothing to read the
        # resource sizes from. Drop the reference row and let the
        # capacity reconciler recompute the counters from ground truth
        # on its next pass rather than guessing at amounts.
        _release_placement(instance_uuid, '', 0, 0, 0, node_uuid)


def _restore_powered_on_instances(instances):
    """Start the domains of the instances this node was running.

    libvirt's autostart flag is what brings instances back after a
    hypervisor reboot, but libvirtd starts its autostart domains while
    libvirtd itself starts -- seconds before this daemon recreates the
    node's br-vxlan-* bridges. A domain on a Shaken Fist network
    therefore fails to start with "Cannot get interface MTU on
    'br-vxlan-...'", and nothing retried it, so a reboot left every
    instance on the hypervisor powered off (issue 4625). libvirt cannot
    see a dependency Shaken Fist creates, so Shaken Fist has to own this
    ordering: this runs after the network restore above, and autostart is
    now only the first attempt rather than the only one.

    A domain which is already running is left alone, so a restart of
    sf-queues or of libvirtd still starts nothing, as before. Each power
    on happens holding the instance lock, as every other power state
    writer does, and on a power state read again inside it, so an
    instance an operator powered off while the networks were being
    restored stays off.

    A failure is reported and not retried here: power_on() has already
    tried several times and recorded what the domain really is. Nothing
    in this path deletes or errors an instance, because a restore which
    could not start an instance is not evidence that the instance is bad
    -- the restore loop which was removed in favour of autostart would
    have error deleted every instance on the node.
    """
    for inst in instances:
        log_ctx = LOG.with_fields({'instance': inst.uuid})
        try:
            with inst.get_lock(op='Instance restore', global_scope=False,
                               node_timeout=120):
                if inst.power_state.get('power_state') != 'on':
                    log_ctx.info(
                        'Instance is no longer powered on, not restoring it')
                    continue

                with util_libvirt.LibvirtConnection() as lc:
                    domain = lc.get_domain_from_sf_uuid(str(inst.uuid))
                    if domain and domain.isActive():
                        continue

                log_ctx.info('Restoring powered on instance')
                inst.power_on()
                inst.add_event(EVENT_TYPE_AUDIT, 'instance restored')
        except Exception as e:
            log_ctx.warning(f'Failed to restore powered on instance: {e}')
            inst.add_event(EVENT_TYPE_AUDIT, 'instance restore failed',
                           extra={'error': str(e)})


def _release_placement(instance_uuid, namespace, cpus, memory_mb, disk_gb,
                       node_uuid):
    """Release one stale placement reference, logging a failed RPC.

    The reply is worth checking for the same reason
    _reconcile_placement() checks its own: an unreachable database is
    not "nothing to release", and a silent failure here leaves the
    counters overstated until the capacity reconciler's next pass with
    no record of why.
    """
    result = mariadb.release_instance_placement(
        instance_uuid, namespace, cpus, memory_mb, disk_gb,
        node_uuid=node_uuid)
    if not result['success']:
        LOG.with_fields({
            'instance': instance_uuid,
            'node': node_uuid,
            'error': result['error']}).warning(
                'Failed to release stale instance placement')


def _reconcile_placement(inst, node_uuid, old_node_uuid):
    """Repair one instance's placement reference rows.

    The placement attribute is already correct here -- this path exists
    to repair the reference rows around it -- so the attribute is
    rewritten byte for byte, without incrementing placement_attempts:
    nothing about this is a new placement attempt. That is also why it
    cannot go through Instance.place_instance(), whose unchanged-node
    early-out would skip the repair entirely.

    The move-to-authoritative-node branch uses this admission form
    rather than releasing just our stale row because the admission's
    delete-all-then-insert guarantees the end state -- a row and a
    charge on the authoritative node -- even when that node has no row
    yet (a pre-cutover placement the seeding migration missed, or a row
    lost to the P1 rollback floor); a local release would repair our
    side and leave the instance charged nowhere. The cost is a
    transient overcount: when the authoritative node already holds its
    row (the common case), it was already charged, and this admission
    charges it again while crediting our node, overcounting it by one
    instance until the next reconcile pass recomputes from ground
    truth. That direction refuses work rather than overcommitting, and
    only follows a queues-daemon restart.
    """
    result = mariadb.admit_instance_placement(
        str(inst.uuid), inst.namespace, node_uuid, inst.cpus, inst.memory,
        mariadb.disk_spec_virtual_gb(inst.disk_spec),
        mariadb.json_dumps(inst.placement),
        old_node_uuid=(old_node_uuid if old_node_uuid != node_uuid else ''),
        enforce=False)
    if not result['success']:
        LOG.with_fields({
            'instance': str(inst.uuid),
            'node': node_uuid,
            'error': result['error']}).error(
                'Failed to reconcile instance placement')


def _restore_instances_in_background():
    try:
        restore_instances()
        LOG.info('Background instance restore complete')
    except Exception as e:
        util_exceptions.ignore_exception('startup instance restore', e)


def _resolve_node_uuid():
    """Populate config.NODE_UUID if not already set.

    This duplicates the logic from daemon.Daemon._resolve_node_uuid()
    because startup_tasks() runs before the daemon's run() method.
    """
    if config.NODE_UUID:
        return

    node_uuid = Node._load_persisted_uuid()
    if not node_uuid:
        n = Node.from_db(config.NODE_NAME)
        if n:
            node_uuid = str(n.uuid)

    if node_uuid:
        config.NODE_UUID = node_uuid
        LOG.with_fields({'node_uuid': node_uuid}).info(
            'Resolved node UUID during startup tasks')


def recover_interrupted_operations(node_uuid):
    """Re-run the operations this node's previous sf-queues was executing.

    A worker which was still running when its sf-queues stopped -- SIGKILLed
    at the end of TimeoutStopSec, abandoned at the end of the worker pool's
    stop grace, or killed in a crash -- left its operation executing and its
    work item claimed. Clearing the claim alone recovers nothing: the next
    worker to dequeue the item finds the operation executing, which only a
    running worker should be, skips it and resolves the item. The operation
    is then executing with no work item, invisible to everything but the
    reaper's orphan pass, which errors it CLUSTER_OP_STUCK_THRESHOLD (30
    minutes) later, long after anything waiting on it has given up (#4543).

    Only this node's sf-queues takes work from this node's own queues, and it
    has not started any workers yet, so an operation still executing behind a
    claimed item there was being run by the process this one replaces, and is
    certainly no longer running. Put it back to queued, which is how a task's
    own defer_with_backoff() retries an operation, so the item re-runs it from
    the top once the claims are cleared. An item already claimed
    CLUSTER_OP_MAX_ATTEMPTS times is instead dropped and its operation errored,
    as the reaper would, so an operation which kills sf-queues cannot keep
    killing it.

    A claim this node left on one of the shared any-* queues is recovered on
    the same basis, but only when claimed_by is this node's uuid. Every
    node's sf-queues drains those queues, so a claim there which names
    another node may be an item that node is running right now. claimed_by
    is only trustworthy because the dequeue RPC now carries the caller's
    identity; before that the database daemon recorded its own node name for
    every claim made over gRPC (#4652). Matching on the uuid rather than the
    name is what keeps such a claim, which may be any node's, from looking
    like the database node's own: it is left to the reaper, which is what
    happened to every shared-queue claim before this.

    This is best effort, item by item: an item which cannot be recovered --
    its operation type no longer exists, say, or the database fails part way
    through -- is logged and left for the reaper, so it cannot stop the rest
    of startup, which restores this node's instances. Failing to list the
    claims at all raises DatabaseUnavailable (the listing is strict, so a
    gRPC error is not read as "no claims"), because the caller must then
    not clear them.
    """
    own_queues = set(get_node_user_facing_node_queues(node_uuid) +
                     get_node_background_node_queues(node_uuid))
    shared_queues = set(get_general_user_facing_node_queues() +
                        get_general_background_node_queues())
    for row in mariadb.list_stuck_work_queue_rows(0, strict=True):
        queue_name = row['queue_name']
        # A claim on one of our own queues is ours whatever claimed_by
        # says -- nothing else drains them while this node is live (the
        # cluster daemon drains them only once the node is deleted). On a
        # shared queue only claimed_by can tell us apart from a live peer.
        if not (queue_name in own_queues or
                (queue_name in shared_queues and
                 row['claimed_by'] == node_uuid)):
            continue
        try:
            _recover_interrupted_operation(row)
        except Exception as e:
            util_exceptions.ignore_exception(
                f'recovering interrupted work item {row["id"]} on '
                f'{row["queue_name"]}', e)


def _recover_interrupted_operation(row):
    payload = row['payload']
    op_type = payload.get('operation_type')
    op_uuid = payload.get('operation_uuid')
    if not op_type or not op_uuid:
        return
    op = get_object_class(op_type).from_db(op_uuid)
    if not op or op.state.value != BaseClusterOperation.STATE_EXECUTING:
        return

    details = {
        'queue_name': row['queue_name'],
        'attempts': row['attempts'],
        'claimed_at': row['claimed_at'],
    }
    try:
        if row['attempts'] >= config.CLUSTER_OP_MAX_ATTEMPTS:
            if not mariadb.delete_work_queue_row(row['id']):
                return
            op.state = BaseClusterOperation.STATE_ERROR
            op.add_event(
                EVENT_TYPE_AUDIT,
                f'errored at sf-queues startup: interrupted after '
                f'{row["attempts"]} claim attempts', extra=details)
        else:
            op.state = BaseClusterOperation.STATE_QUEUED
            op.add_event(
                EVENT_TYPE_AUDIT,
                're-queued at sf-queues startup: the previous sf-queues '
                'stopped while executing it', extra=details)
    except InvalidStateException as e:
        # It went terminal between our read and the write.
        LOG.with_fields({
            'operation_type': op_type,
            'operation_uuid': op_uuid,
        }).warning(f'Cannot recover interrupted operation: {e}')
        return
    LOG.with_fields({
        'operation_type': op_type,
        'operation_uuid': op_uuid,
        'state': op.state.value,
        **details,
    }).warning('Recovered an operation interrupted by sf-queues stopping')


def startup_tasks():
    # Ensure NODE_UUID is resolved before we try to use it. This runs
    # before the daemon's run() method which would normally resolve it.
    _resolve_node_uuid()

    # We need to report object versions very early before the resources daemon
    # has started. This code is duplicated from the resources daemon code. Sorry.
    stats = {}
    for obj in OBJECT_NAMES_TO_CLASSES:
        stats['object_version_%s' % obj] = \
            get_object_class(obj).current_version
    if config.NODE_UUID:
        mariadb.upsert_node_metrics(
            config.NODE_UUID, config.NODE_NAME,
            time.time(), stats)
    else:
        LOG.warning(
            'NODE_UUID is not set, skipping initial metrics upsert')

    version = util_general.get_version()
    util_concurrency.set_thread_name('main-v%s' % version)

    # If you ran this, it means we're not shutting down any more
    n = Node.new(config.NODE_NAME, config.NODE_MESH_IP)
    n.add_event(EVENT_TYPE_AUDIT, f'node is running v{version}')

    # Log configuration on startup
    for key, value in redacted_config_items():
        LOG.info(f'Configuration item {key} = {value}')

    daemon.set_log_level(LOG, 'main')

    # Check in early and often
    locks.clear_stale_locks()

    # Reset the claims this node's previous sf-queues left behind.
    # Recovering interrupted operations reads those claims, so it goes
    # first.
    #
    # This node's own queues are named by its uuid, not its name: looked
    # up by name, they matched no queue, so a claim left by a stopped
    # sf-queues was never cleared (#4543). Nothing else drains them while
    # this node is live, so every claim on one is ours and the reset is
    # unfiltered. The one exception is the cluster daemon draining a
    # deleted node's queues, which only matters if a deleted node comes
    # back under the same uuid.
    #
    # The shared any-* queues are drained by every node's sf-queues, so
    # the reset there clears only claims made under this node's uuid:
    # clearing a peer's claim hands work which is still running to a
    # second worker (#4652). It can, because the dequeue RPC now carries
    # the caller's identity -- before that, claimed_by was the database
    # daemon's node name for every claim made over gRPC. A claim an older
    # sf-queues left on a shared queue, or one an older sf-database
    # recorded, carries a name, so it is still the cluster daemon
    # reaper's to clear once it is CLUSTER_OP_STUCK_THRESHOLD old. So is
    # every claim on a shared queue while sf-database is too old to
    # clear one worker's claims: restart_work_queue_claims_by() does
    # nothing then, rather than clearing them all.
    own_queues = []
    shared_queues = []
    if not config.NODE_UUID:
        LOG.warning('NODE_UUID is not set, cannot recover interrupted '
                    'operations or reset this node\'s queue claims')
    else:
        own_queues = (get_node_user_facing_node_queues(config.NODE_UUID) +
                      get_node_background_node_queues(config.NODE_UUID))
        shared_queues = (get_general_user_facing_node_queues() +
                         get_general_background_node_queues())
        try:
            recover_interrupted_operations(config.NODE_UUID)
        except Exception as e:
            # The claims could not be read, so the operations behind them
            # were not checked. Clearing them anyway strands any which were
            # executing, exactly as #4543 did, so leave both to the reaper.
            util_exceptions.ignore_exception(
                'recovering interrupted operations; leaving this node\'s '
                'claims to the reaper', e)
            own_queues = []
            shared_queues = []

    # The resets are best effort, like the recovery before them. A queue
    # whose reset fails -- the database is unavailable, or too old to
    # narrow the reset -- keeps its claims until the reaper clears them,
    # and must not stop the blob datastore upgrade and instance restore
    # below. An operation recovery moved back to queued on a shared queue
    # then waits behind its claim for the reaper too. That is still better
    # than before recovery reached the shared queues: the next worker
    # would have found the operation executing, skipped it and stranded it.
    unreset = []
    for queue in own_queues + shared_queues:
        try:
            if queue in shared_queues:
                cleared = mariadb.restart_work_queue_claims_by(
                    queue, config.NODE_UUID)
            else:
                mariadb.restart_work_queue(queue)
                cleared = True
        except Exception as e:
            util_exceptions.ignore_exception(
                f'resetting this node\'s claims on {queue}', e)
            cleared = False
        if not cleared:
            unreset.append(queue)

        try:
            processing, queued, deferred = mariadb.get_work_queue_length(queue)
            LOG.with_fields({
                'processing': processing,
                'queued': queued,
                'deferred': deferred,
                'queue': queue
            }).debug('Queue length')
        except Exception as e:
            util_exceptions.ignore_exception(
                f'reading the length of {queue}', e)
    if unreset:
        LOG.with_fields({'queues': unreset}).warning(
            'Could not reset this node\'s claims on these queues, leaving '
            'them to the reaper')

    # Ensure the blob data store is the most recent version
    upgrade_blob_datastore()

    # Restore this node's networks and instances in the background.
    # The network restore enqueues cluster operations on this node's
    # own clusteroperation queues and then waits for them, but the
    # only consumer of those queues is this daemon's worker pool,
    # which starts once startup_tasks() has returned (and systemd
    # only considers the unit started once READY is signalled after
    # that). Blocking here therefore deadlocks startup until
    # TimeoutStartSec kills the daemon whenever the node has running
    # instances -- observed during the first in-flight sfcbr upgrade
    # on 2026-07-12. The thread's waits complete normally once the
    # main loop is consuming; ordering is safe because instance
    # create operations carry depends_on references to the network
    # operations they need.
    restore_thread = threading.Thread(
        target=_restore_instances_in_background, daemon=True,
        name='startup-restore')
    restore_thread.start()
    return restore_thread
