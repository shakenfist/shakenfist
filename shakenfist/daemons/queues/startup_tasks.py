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
from shakenfist.network.interface import interfaces_for_instance
from shakenfist.node import Node
from shakenfist.operations.baseoperation import get_all_node_queues
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import general as util_general
from shakenfist.util import json as util_json


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
    # Ensure all instances for this node are defined and have up to date data.
    networks = []
    instances = []
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

    for inst in instances:
        try:
            with inst.get_lock(timeout=120, op='Instance restore',
                               global_scope=False):
                started = ['on', 'transition-to-on',
                           instance.Instance.STATE_INITIAL, 'unknown']
                if inst.power_state not in started:
                    continue

                LOG.with_fields({'instance': inst}).info('Restoring instance')
                inst.create_on_hypervisor()
        except Exception as e:
            util_exceptions.ignore_exception(
                'restore instance %s' % inst, e)
            inst.enqueue_delete_due_error(
                'exception while restoring instance on daemon restart')

    # Reconcile the recorded instance placements for this node: add any
    # missing INSTANCE_LOCATION references, and remove stale ones. The
    # restore work above can take many minutes, so the instances list
    # gathered at its start is a stale snapshot -- before removing a
    # reference, re-check the instance's authoritative placement so a
    # concurrently placed instance is never unrecorded.
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

    # Reset queues
    for queue in get_all_node_queues(config.NODE_NAME):
        mariadb.restart_work_queue(queue)
        processing, queued, deferred = mariadb.get_work_queue_length(
            queue)
        LOG.with_fields({
            'processing': processing,
            'queued': queued,
            'deferred': deferred,
            'queue': queue
        }).debug('Queue length')

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
