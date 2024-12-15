import json
import os
import pathlib
import time
from collections import defaultdict
from functools import partial

import pyprctl
from shakenfist_utilities import logs  # noreorder

from shakenfist import cache
from shakenfist import etcd
from shakenfist import instance
from shakenfist import network
from shakenfist.baseobjectmapping import OBJECT_NAMES_TO_CLASSES
from shakenfist.baseobjectmapping import OBJECT_NAMES_TO_ITERATORS
from shakenfist.blob import Blob
from shakenfist.blob import Blobs
from shakenfist.blob import placement_filter
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.networkinterface import interfaces_for_instance
from shakenfist.node import Node
from shakenfist.util import general as util_general


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

        for b in Blobs([partial(placement_filter, config.NODE_NAME)]):
            old_blob_path = os.path.join(config.STORAGE_PATH, 'blobs', b.uuid)
            new_blob_path = Blob.filepath(b.uuid)

            if not os.path.exists(old_blob_path):
                LOG.warning(
                    'Not moving blob %s from %s to %s as it is missing from disk'
                    % (b.uuid, old_blob_path, new_blob_path))
            else:
                LOG.info('Moving blob %s from %s to %s'
                         % (b.uuid, old_blob_path, new_blob_path))
                os.rename(old_blob_path, new_blob_path)

                if b.uuid in relocations:
                    cache_entry = relocations[b.uuid]
                    LOG.info('Relocating image cache entry %s to new blob path %s'
                             % (cache_entry, new_blob_path))
                    os.unlink(cache_entry)
                    os.symlink(new_blob_path, cache_entry)

            count += 1

        if count > 0:
            LOG.info('Resharded %d blobs' % count)

    if start_version != version:
        os.makedirs(os.path.dirname(version_path), exist_ok=True)
        with open(version_path, 'w') as f:
            f.write(json.dumps({'version': version}, indent=4, sort_keys=True))
        LOG.info('Blob datastore upgrade took %.02f seconds'
                 % (time.time() - start_time))


def restore_instances():
    # Ensure all instances for this node are defined and have up to date data.
    networks = []
    instances = []
    for inst in instance.Instances([instance.this_node_filter], prefilter='healthy'):
        instance_problems = []
        inst_interfaces = inst.interfaces
        if not inst_interfaces:
            inst_interfaces = []
        updated_interfaces = False

        for ni in interfaces_for_instance(inst):
            if ni.network_uuid not in networks:
                networks.append(ni.network_uuid)
            if ni.uuid not in inst_interfaces:
                inst_interfaces.append(ni.uuid)
                updated_interfaces = True

        # We do not need a lock here because this loop only runs on the node
        # with the instance, and interfaces don't change post instance
        # creation.
        if updated_interfaces:
            inst.interfaces = inst_interfaces

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

    for network_uuid in networks:
        try:
            n = network.Network.from_db(network_uuid)
            if not n.is_dead():
                LOG.with_fields({'network': n}).info('Restoring network')
                n.create_on_hypervisor()
                n.ensure_mesh()
        except Exception as e:
            util_general.ignore_exception(
                'restore network %s' % network_uuid, e)

    for inst in instances:
        try:
            with inst.get_lock(ttl=120, timeout=120, op='Instance restore',
                               global_scope=False):
                started = ['on', 'transition-to-on',
                           instance.Instance.STATE_INITIAL, 'unknown']
                if inst.power_state not in started:
                    continue

                LOG.with_fields({'instance': inst}).info('Restoring instance')
                inst.create_on_hypervisor()
        except Exception as e:
            util_general.ignore_exception(
                'restore instance %s' % inst, e)
            inst.etcd.enqueue_delete_due_error(
                'exception while restoring instance on daemon restart')

    # Ensure we have a cache of the instances on this machine
    instance_uuids = []
    for inst in instances:
        instance_uuids.append(inst.uuid)
    n = Node.from_db(config.NODE_NAME)
    n.instances = instance_uuids


def startup_tasks():
    # We need to report object versions very early before the resources daemon
    # has started. This code is duplicated from the resources daemon code. Sorry.
    stats = {}
    for obj in OBJECT_NAMES_TO_CLASSES:
        stats['object_version_%s' % obj] = \
            OBJECT_NAMES_TO_CLASSES[obj].current_version
    etcd.put(
        'metrics', config.NODE_NAME, None,
        {
            'fqdn': config.NODE_NAME,
            'timestamp': time.time(),
            'metrics': stats
        })

    LOG.info('Starting')
    pyprctl.set_name('main-v%s' % util_general.get_version())

    # Ensure we have a consistent cache of object states if the cache is entirely
    # absent.
    cache_version = etcd.get_raw('/sf/cache/_version')
    if not cache_version:
        cache_version = {'version': 0}

    if cache_version['version'] != 2:
        # We don't need to step through various upgrades, we just rebuild
        # the entire cache from scratch instead.
        for obj_type in OBJECT_NAMES_TO_ITERATORS:
            with etcd.get_lock('cache', None, obj_type, op='Cache upgrade'):
                by_state = defaultdict(dict)
                for obj in OBJECT_NAMES_TO_ITERATORS[obj_type]([]):
                    by_state[obj.state.value][obj.uuid] = time.time()
                for state in by_state:
                    cache.clobber_object_state_cache(
                        obj_type, state, by_state[state])
        cache_version['version'] = 2
        etcd.put_raw('/sf/cache/_version', cache_version)

    # If you ran this, it means we're not shutting down any more
    n = Node.new(config.NODE_NAME, config.NODE_MESH_IP)
    n.state = Node.STATE_CREATED

    # Log configuration on startup
    for key, value in config.dict().items():
        LOG.info(f'Configuration item {key} = {value}')

    daemon.set_log_level(LOG, 'main')

    # Check in early and often, also reset processing queue items.
    etcd.clear_stale_locks()
    Node.observe_this_node()
    etcd.restart_queues()

    # Ensure the blob data store is the most recent version
    upgrade_blob_datastore()

    restore_instances()
