# Copyright 2019 Michael Still

import faulthandler
import os
import psutil
import setproctitle
import signal
import subprocess
import time

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.daemons import shim
from shakenfist import etcd
from shakenfist import instance
from shakenfist.ipmanager import IPManager
from shakenfist import logutil
from shakenfist import network
from shakenfist.networkinterface import interfaces_for_instance
from shakenfist.node import Node
from shakenfist.util import general as util_general
from shakenfist.util import process as util_process
from shakenfist.util import network as util_network


LOG, HANDLER = logutil.setup('main')


def restore_instances():
    # Ensure all instances for this node are defined and have up to date data.
    networks = []
    instances = []
    for inst in instance.Instances([instance.this_node_filter,
                                    instance.healthy_states_filter]):
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
                LOG.with_object(n).info('Restoring network')
                n.create_on_hypervisor()
                n.ensure_mesh()
        except Exception as e:
            util_general.ignore_exception(
                'restore network %s' % network_uuid, e)

    for inst in instances:
        try:
            with inst.get_lock(ttl=120, timeout=120, op='Instance restore'):
                started = ['on', 'transition-to-on',
                           instance.Instance.STATE_INITIAL, 'unknown']
                if inst.power_state not in started:
                    continue

                LOG.with_object(inst).info('Restoring instance')
                inst.create_on_hypervisor()
        except Exception as e:
            util_general.ignore_exception(
                'restore instance %s' % inst, e)
            inst.etcd.enqueue_delete_due_error(
                'exception while restoring instance on daemon restart')


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

    LOG.info('Starting...')
    setproctitle.setproctitle(
        daemon.process_name('main') + '-v%s' % util_general.get_version())

    # If you ran this, it means we're not shutting down any more
    n = Node.new(config.NODE_NAME, config.NODE_MESH_IP)
    n.state = Node.STATE_CREATED

    # Log configuration on startup
    for key, value in config.dict().items():
        LOG.info('Configuration item %s = %s' % (key, value))

    daemon.set_log_level(LOG, 'main')

    # Check in early and often, also reset processing queue items.
    etcd.clear_stale_locks()
    Node.observe_this_node()
    etcd.restart_queues()

    def _start_daemon(d):
        DAEMON_PROCS[d] = subprocess.Popen(
            ['/srv/shakenfist/venv/bin/sf-daemon-shim', d])
        LOG.with_field('pid', DAEMON_PROCS[d].pid).info('Started %s' % d)

    # Resource usage publisher, we need this early because scheduling decisions
    # might happen quite early on.
    _start_daemon('resources')

    # If I am the network node, I need some setup
    if config.NODE_IS_NETWORK_NODE:
        # Bootstrap the floating network in the Networks table
        floating_network = network.Network.from_db('floating')
        if not floating_network:
            floating_network = network.Network.create_floating_network(
                config.FLOATING_NETWORK)

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
            ipm = IPManager.from_db('floating')
            subst['master_float'] = ipm.get_address_at_index(1)
            subst['netmask'] = ipm.netmask

            # We need to copy the MTU of the interface we are bridging to
            # or weird networking things happen.
            mtu = util_network.get_interface_mtu(config.NODE_EGRESS_NIC)

            util_network.create_interface(
                subst['egress_bridge'], 'bridge', '', mtu=mtu)

            util_process.execute(None,
                                 'ip link set %(egress_bridge)s up' % subst)
            util_process.execute(None,
                                 'ip addr add %(master_float)s/%(netmask)s '
                                 'dev %(egress_bridge)s' % subst)

            util_process.execute(None,
                                 'iptables -A FORWARD -o %(egress_nic)s '
                                 '-i %(egress_bridge)s -j ACCEPT' % subst)
            util_process.execute(None,
                                 'iptables -A FORWARD -i %(egress_nic)s '
                                 '-o %(egress_bridge)s -j ACCEPT' % subst)
            util_process.execute(None,
                                 'iptables -t nat -A POSTROUTING '
                                 '-o %(egress_nic)s -j MASQUERADE' % subst)

    def _audit_daemons():
        running_daemons = []
        for proc in DAEMON_PROCS:
            running_daemons.append(proc)

        for d in shim.DAEMON_IMPLEMENTATIONS:
            if d not in running_daemons:
                _start_daemon(d)

    if not config.NODE_IS_EVENTLOG_NODE:
        del shim.DAEMON_IMPLEMENTATIONS['eventlog']

    _audit_daemons()
    restore_instances()

    running = True
    shutdown_commenced = None
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
