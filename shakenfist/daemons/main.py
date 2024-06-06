# Copyright 2019 Michael Still

from collections import defaultdict
import faulthandler
from functools import partial
import json
import os
import pathlib
import psutil
import setproctitle
from shakenfist_utilities import logs
import signal
import subprocess
import time

from shakenfist.baseobjectmapping import (
    OBJECT_NAMES_TO_ITERATORS, OBJECT_NAMES_TO_CLASSES)
from shakenfist.blob import Blob, Blobs, placement_filter
from shakenfist import cache
from shakenfist import config as sf_config
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.daemons import shim
from shakenfist import etcd
from shakenfist import instance
from shakenfist import network
from shakenfist.networkinterface import interfaces_for_instance
from shakenfist.node import Node
from shakenfist.util import general as util_general
from shakenfist.util import process as util_process
from shakenfist.util import network as util_network


LOG, HANDLER = logs.setup('main')


def upgrade_blob_datastore():
    # Upgrades for the actual underlying blob data store
    version_path = os.path.join(config.STORAGE_PATH, 'blobs', '_version')
    if os.path.exists(version_path):
        with open(version_path) as f:
            version = json.loads(f.read())['version']
    else:
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


DAEMON_PROCS = {}


def propagate_signal(signum, _frame):
    # We have a bunch of subprocesses here, so we can't just use the default
    # faulthandler mechanism.
    faulthandler.dump_traceback
    for proc in DAEMON_PROCS:
        try:
            os.kill(DAEMON_PROCS[proc].pid, signum)
        except ProcessLookupError:
            pass


signal.signal(signal.SIGUSR1, propagate_signal)


def main():
    global DAEMON_PROCS

    def _start_daemon(d):
        DAEMON_PROCS[d] = subprocess.Popen(
            ['/srv/shakenfist/venv/bin/sf-daemon-shim', d])
        LOG.with_fields({'pid': DAEMON_PROCS[d].pid}).info('Started %s' % d)

    # This is awkward, but let's verify our configuration before we get any
    # further.
    sf_config.verify_config()

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

    # Start the eventlog daemon very very early because basically everything
    # else talks to it.
    if not config.NODE_IS_EVENTLOG_NODE:
        del shim.DAEMON_IMPLEMENTATIONS['eventlog']
    else:
        _start_daemon('eventlog')

    LOG.info('Starting...')
    setproctitle.setproctitle(
        daemon.process_name('main') + '-v%s' % util_general.get_version())

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
                    cache.clobber_object_state_cache(obj_type, state, by_state[state])
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

    # If I am the network node, I need some setup
    if config.NODE_IS_NETWORK_NODE:
        # Bootstrap the floating network in the Networks table
        network.floating_network()
        subst = {
            'egress_bridge': util_network.get_safe_interface_name(
                'egr-br-%s' % config.NODE_EGRESS_NIC),
            'egress_nic': config.NODE_EGRESS_NIC
        }

        if not util_network.check_for_interface(subst['egress_bridge']):
            # NOTE(mikal): Adding the physical interface to the physical bridge
            # is considered outside the scope of the orchestration software as
            # it will cause the node to lose network connectivity. So instead
            # all we do is create a bridge if it doesn't exist and the wire
            # everything up to it. We can do egress NAT in that state, even if
            # floating IPs don't work.
            #
            # No locking as read only
            fn = network.floating_network()
            subst['master_float'] = fn.ipam.get_address_at_index(1)
            subst['netmask'] = fn.netmask

            # We need to copy the MTU of the interface we are bridging to
            # or weird networking things happen.
            mtu = util_network.get_interface_mtu(config.NODE_EGRESS_NIC)

            util_network.create_interface(
                subst['egress_bridge'], 'bridge', '', mtu=mtu)

            util_process.execute(None, 'ip link set %(egress_bridge)s up' % subst)
            util_network.add_address_to_interface(
                None, subst['master_float'], subst['netmask'], subst['egress_bridge'])

            util_process.execute(None,
                                 'iptables -w 10 -A FORWARD -o %(egress_nic)s '
                                 '-i %(egress_bridge)s -j ACCEPT' % subst)
            util_process.execute(None,
                                 'iptables -w 10 -A FORWARD -i %(egress_nic)s '
                                 '-o %(egress_bridge)s -j ACCEPT' % subst)
            util_process.execute(None,
                                 'iptables -w 10 -t nat -A POSTROUTING '
                                 '-o %(egress_nic)s -j MASQUERADE' % subst)

    def _audit_daemons():
        running_daemons = []
        for proc in DAEMON_PROCS:
            running_daemons.append(proc)

        for d in shim.DAEMON_IMPLEMENTATIONS:
            if d not in running_daemons:
                _start_daemon(d)

    _audit_daemons()
    restore_instances()

    running = True
    shutdown_commenced = None
    warned_locks = {}

    while True:
        time.sleep(5)

        try:
            dead = []
            for proc in DAEMON_PROCS:
                if DAEMON_PROCS[proc].poll():
                    LOG.warning('%s process has exited' % proc)
                    dead.append(proc)

                elif not psutil.pid_exists(DAEMON_PROCS[proc].pid):
                    LOG.warning('%s process is missing' % proc)
                    dead.append(proc)

            for d in dead:
                LOG.with_fields({
                    'pid': DAEMON_PROCS[d].pid,
                    'exit': DAEMON_PROCS[d].returncode
                }).warning('%s is dead' % proc)
                del DAEMON_PROCS[d]

        except ChildProcessError:
            # We get this if there are no child processes
            pass

        n = Node.from_db(config.NODE_NAME)
        if n.state.value not in [Node.STATE_STOPPING, Node.STATE_STOPPED]:
            _audit_daemons()
            Node.observe_this_node()

            # Check if we hold any locks for processes which don't exist any
            # more. That is, a process has ended but left a stray lock.
            locks = etcd.get_existing_locks()
            for lock in locks:
                lock_details = locks[lock]
                if lock_details.get('node') != config.NODE_NAME:
                    continue

                pid = lock_details.get('pid')
                if psutil.pid_exists(pid):
                    continue
                if pid not in warned_locks:
                    LOG.with_fields(lock_details).warning(
                        'Lock held by missing process on this node')
                    warned_locks[pid] = time.time()
                elif time.time() - warned_locks[pid] > 30:
                    LOG.with_fields(lock_details).error(
                        'Lock held by missing process on this node for more '
                        'than 30 seconds')

        elif len(DAEMON_PROCS) == 0:
            n.state = Node.STATE_STOPPED
            return

        else:
            if running:
                shutdown_commenced = time.time()
                for proc in DAEMON_PROCS:
                    try:
                        os.kill(DAEMON_PROCS[proc].pid, signal.SIGTERM)
                        LOG.info('Sent SIGTERM to %s (pid %s)'
                                 % (proc, DAEMON_PROCS[proc].pid))
                    except OSError as e:
                        LOG.warn('Failed to send SIGTERM to %s: %s'
                                 % (proc, e))

            if time.time() - shutdown_commenced > 10:
                LOG.warning('We have taken more than ten seconds to shut down')
                for proc in DAEMON_PROCS:
                    LOG.warning('%s daemon still running (pid %d)'
                                % (proc, DAEMON_PROCS[proc].pid))
                LOG.warning('Dumping thread traces')
                propagate_signal(signal.SIGUSR1, None)
                shutdown_commenced = time.time()

            running = False
