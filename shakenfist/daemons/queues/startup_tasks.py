import json
import os
import pathlib
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import instance
from shakenfist import locks
from shakenfist import mariadb
from shakenfist.network import network
from shakenfist.blob import Blob
from shakenfist.config import config
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
            inst.etcd.enqueue_delete_due_error(
                'exception while restoring instance on daemon restart')

    # Ensure we have a cache of the instances on this machine
    instance_uuids = []
    for inst in instances:
        instance_uuids.append(inst.uuid)
    n = Node.from_db(config.NODE_NAME)
    n.instances = instance_uuids


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
    for key, value in config.model_dump().items():
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

    restore_instances()
