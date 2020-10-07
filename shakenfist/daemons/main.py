# Copyright 2019 Michael Still

import setproctitle
import time
import os
import psutil

from shakenfist import config
from shakenfist.daemons import daemon
from shakenfist.daemons import external_api as external_api_daemon
from shakenfist.daemons import cleaner as cleaner_daemon
from shakenfist.daemons import queues as queues_daemon
from shakenfist.daemons import net as net_daemon
from shakenfist.daemons import resources as resource_daemon
from shakenfist.daemons import triggers as trigger_daemon
from shakenfist import db
from shakenfist import logutil
from shakenfist import net
from shakenfist import util
from shakenfist import virt


LOG, HANDLER = logutil.setup('main')


def restore_instances():
    # Ensure all instances for this node are defined
    networks = []
    instances = []
    for inst in list(db.get_instances(only_node=config.parsed.get('NODE_NAME'))):
        for iface in db.get_instance_interfaces(inst['uuid']):
            if not iface['network_uuid'] in networks:
                networks.append(iface['network_uuid'])
        instances.append(inst['uuid'])

    with util.RecordedOperation('restore networks', None):
        for network in networks:
            try:
                n = net.from_db(network)
                LOG.withObj(n).info('Restoring network')
                n.create()
                n.ensure_mesh()
                n.update_dhcp()
            except Exception as e:
                util.ignore_exception('restore network %s' % network, e)

    with util.RecordedOperation('restore instances', None):
        for instance in instances:
            try:
                i = virt.from_db(instance)
                if not i:
                    continue
                if i.db_entry.get('power_state', 'unknown') not in ['on', 'transition-to-on',
                                                                    'initial', 'unknown']:
                    continue

                LOG.withObj(i).info('Restoring instance')
                i.create()
            except Exception as e:
                util.ignore_exception('restore instance %s' % instance, e)
                db.enqueue_instance_delete(
                    config.parsed.get('NODE_NAME'), instance, 'error',
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
    config.parsed.parse()
    for key in config.parsed.config:
        LOG.info('Configuration item %s = %s' % (key, config.parsed.get(key)))

    daemon.set_log_level(LOG, 'main')

    # Check in early and often, also reset processing queue items
    db.clear_stale_locks()
    db.see_this_node()
    db.restart_queues()

    def _start_daemon(d):
        pid = os.fork()
        if pid == 0:
            DAEMON_IMPLEMENTATIONS[d].Monitor(d).run()
        DAEMON_PIDS[pid] = d
        LOG.withField('pid', pid).info('Started %s' % d)

    # Resource usage publisher, we need this early because scheduling decisions
    # might happen quite early on.
    _start_daemon('resources')

    # If I am the network node, I need some setup
    if util.is_network_node():
        # Bootstrap the floating network in the Networks table
        floating_network = db.get_network('floating')
        if not floating_network:
            db.create_floating_network(config.parsed.get('FLOATING_NETWORK'))
            floating_network = net.from_db('floating')

        subst = {
            'physical_bridge': util.get_safe_interface_name(
                'phy-br-%s' % config.parsed.get('NODE_EGRESS_NIC')),
            'physical_nic': config.parsed.get('NODE_EGRESS_NIC')
        }

        if not util.check_for_interface(subst['physical_bridge']):
            # NOTE(mikal): Adding the physical interface to the physical bridge
            # is considered outside the scope of the orchestration software as it
            # will cause the node to lose network connectivity. So instead all we
            # do is create a bridge if it doesn't exist and the wire everything up
            # to it. We can do egress NAT in that state, even if floating IPs
            # don't work.
            with util.RecordedOperation('create physical bridge', None):
                # No locking as read only
                ipm = db.get_ipmanager('floating')
                subst['master_float'] = ipm.get_address_at_index(1)
                subst['netmask'] = ipm.netmask

                util.create_interface(subst['physical_bridge'], 'bridge', '')
                util.execute(None,
                             'ip link set %(physical_bridge)s up' % subst)
                util.execute(None,
                             'ip addr add %(master_float)s/%(netmask)s dev %(physical_bridge)s' % subst)

                util.execute(None,
                             'iptables -A FORWARD -o %(physical_nic)s -i %(physical_bridge)s -j ACCEPT' % subst)
                util.execute(None,
                             'iptables -A FORWARD -i %(physical_nic)s -o %(physical_bridge)s -j ACCEPT' % subst)
                util.execute(None,
                             'iptables -t nat -A POSTROUTING -o %(physical_nic)s -j MASQUERADE' % subst)

    def _audit_daemons():
        running_daemons = []
        for pid in DAEMON_PIDS:
            running_daemons.append(DAEMON_PIDS[pid])
        LOG.info('Daemons running: %s' % ', '.join(sorted(running_daemons)))

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
        db.see_this_node()
