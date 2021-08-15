# Copyright 2019 Michael Still

import setproctitle
import time
import os
import psutil

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.daemons import external_api as external_api_daemon
from shakenfist.daemons import cleaner as cleaner_daemon
from shakenfist.daemons import queues as queues_daemon
from shakenfist.daemons import net as net_daemon
from shakenfist.daemons import resources as resource_daemon
from shakenfist.daemons import triggers as trigger_daemon
from shakenfist import db
from shakenfist import instance
from shakenfist.ipmanager import IPManager
from shakenfist import logutil
from shakenfist import net
from shakenfist import networkinterface
from shakenfist.node import Node
from shakenfist import util


LOG, HANDLER = logutil.setup('main')


def restore_instances():
    # Ensure all instances for this node are defined
    networks = []
    instances = []
    for inst in instance.Instances([instance.this_node_filter, instance.healthy_states_filter]):
        instance_problems = []
        for ni in networkinterface.interfaces_for_instance(inst):
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

    with util.RecordedOperation('restore networks', None):
        for network in networks:
            try:
                n = net.Network.from_db(network)
                if not n.is_dead():
                    LOG.with_object(n).info('Restoring network')
                    n.create_on_hypervisor()
                    n.ensure_mesh()
            except Exception as e:
                util.ignore_exception('restore network %s' % network, e)

    with util.RecordedOperation('restore instances', None):
        for inst in instances:
            try:
                with db.get_lock(
                        'instance', None, inst.uuid, ttl=120, timeout=120,
                        op='Instance restore'):
                    started = ['on', 'transition-to-on',
                               instance.Instance.STATE_INITIAL, 'unknown']
                    if inst.power_state not in started:
                        continue

                    LOG.with_object(inst).info('Restoring instance')
                    inst.create_on_hypervisor()
            except Exception as e:
                util.ignore_exception('restore instance %s' % inst.uuid, e)
                inst.db.enqueue_delete_due_error(
                    'exception while restoring instance on daemon restart')


DAEMON_IMPLEMENTATIONS = {
    'api': external_api_daemon,
    'cleaner': cleaner_daemon,
    'net': net_daemon,
    'queues': queues_daemon,
    'resources': resource_daemon,
    'triggers': trigger_daemon
}


DAEMON_PIDS = {}


def main():
    global DAEMON_IMPLEMENTATIONS
    global DAEMON_PIDS

    setproctitle.setproctitle(daemon.process_name('main'))

    # Log configuration on startup
    for key, value in config.dict().items():
        LOG.info('Configuration item %s = %s' % (key, value))

    daemon.set_log_level(LOG, 'main')

    # Check in early and often, also reset processing queue items.
    db.clear_stale_locks()
    Node.observe_this_node()
    db.restart_queues()

    def _start_daemon(d):
        pid = os.fork()
        if pid == 0:
            DAEMON_IMPLEMENTATIONS[d].Monitor(d).run()
        DAEMON_PIDS[pid] = d
        LOG.with_field('pid', pid).info('Started %s' % d)

    # Resource usage publisher, we need this early because scheduling decisions
    # might happen quite early on.
    _start_daemon('resources')

    # If I am the network node, I need some setup
    if util.is_network_node():
        # Bootstrap the floating network in the Networks table
        floating_network = net.Network.from_db('floating')
        if not floating_network:
            floating_network = net.Network.create_floating_network(
                config.FLOATING_NETWORK)

        subst = {
            'egress_bridge': util.get_safe_interface_name(
                'egr-br-%s' % config.NODE_EGRESS_NIC),
            'egress_nic': config.NODE_EGRESS_NIC
        }

        if not util.check_for_interface(subst['egress_bridge']):
            # NOTE(mikal): Adding the physical interface to the physical bridge
            # is considered outside the scope of the orchestration software as
            # it will cause the node to lose network connectivity. So instead
            # all we do is create a bridge if it doesn't exist and the wire
            # everything up to it. We can do egress NAT in that state, even if
            # floating IPs don't work.
            with util.RecordedOperation('create physical bridge', None):
                # No locking as read only
                ipm = IPManager.from_db('floating')
                subst['master_float'] = ipm.get_address_at_index(1)
                subst['netmask'] = ipm.netmask

                util.create_interface(subst['egress_bridge'], 'bridge', '')
                util.execute(None,
                             'ip link set %(egress_bridge)s up' % subst)
                util.execute(None,
                             'ip addr add %(master_float)s/%(netmask)s '
                             'dev %(egress_bridge)s' % subst)

                util.execute(None,
                             'iptables -A FORWARD -o %(egress_nic)s '
                             '-i %(egress_bridge)s -j ACCEPT' % subst)
                util.execute(None,
                             'iptables -A FORWARD -i %(egress_nic)s '
                             '-o %(egress_bridge)s -j ACCEPT' % subst)
                util.execute(None,
                             'iptables -t nat -A POSTROUTING '
                             '-o %(egress_nic)s -j MASQUERADE' % subst)

    def _audit_daemons():
        running_daemons = []
        for pid in DAEMON_PIDS:
            running_daemons.append(DAEMON_PIDS[pid])

        for d in DAEMON_IMPLEMENTATIONS:
            if d not in running_daemons:
                _start_daemon(d)

        for d in DAEMON_PIDS:
            if not psutil.pid_exists(d):
                LOG.warning('%s pid is missing, restarting' % DAEMON_PIDS[d])
                _start_daemon(DAEMON_PIDS[d])

    _audit_daemons()
    restore_instances()

    while True:
        time.sleep(10)

        wpid, _ = os.waitpid(-1, os.WNOHANG)
        while wpid != 0:
            LOG.warning('%s died (pid %d)'
                        % (DAEMON_PIDS.get(wpid, 'unknown'), wpid))
            del DAEMON_PIDS[wpid]
            wpid, _ = os.waitpid(-1, os.WNOHANG)

        _audit_daemons()
        Node.observe_this_node()
