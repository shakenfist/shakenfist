# Copyright 2019 Michael Still

import setproctitle
import time
import os

from oslo_concurrency import processutils

from shakenfist import config
from shakenfist.daemons import daemon
from shakenfist.daemons import external_api as external_api_daemon
from shakenfist.daemons import cleaner as cleaner_daemon
from shakenfist.daemons import queues as queues_daemon
from shakenfist.daemons import net as net_daemon
from shakenfist.daemons import resources as resource_daemon
from shakenfist.daemons import triggers as trigger_daemon
from shakenfist import db

from shakenfist import net
from shakenfist import util
from shakenfist import virt


LOG, handler = util.setup_logging('main')


def restore_instances():
    # Ensure all instances for this node are defined
    networks = []
    instances = []
    for inst in list(db.get_instances(only_node=config.parsed.get('NODE_NAME'))):
        for iface in db.get_instance_interfaces(inst['uuid']):
            if not iface['network_uuid'] in networks:
                networks.append(iface['network_uuid'])
        instances.append(inst['uuid'])

    with util.RecordedOperation('restore networks', None) as _:
        for network in networks:
            try:
                n = net.from_db(network)
                LOG.info('%s Restoring network' % n)
                n.create()
                n.ensure_mesh()
                n.update_dhcp()
            except Exception as e:
                util.ignore_exception('restore network %s' % network, e)

    with util.RecordedOperation('restore instances', None) as _:
        for instance in instances:
            try:
                i = virt.from_db(instance)
                if not i:
                    continue
                if i.db_entry.get('power_state', 'unknown') not in ['on', 'transition-to-on',
                                                                    'initial', 'unknown']:
                    continue

                LOG.info('%s Restoring instance' % i)
                i.create()
            except Exception as e:
                util.ignore_exception('restore instance %s' % instance, e)
                db.update_instance_state(instance, 'error')


def main():
    # Log configuration on startup
    for key in config.parsed.config:
        LOG.info('Configuration item %s = %s' % (key, config.parsed.get(key)))

    util.log_setlevel(LOG, 'main')

    # Check in early and often
    db.see_this_node()

    # Resource usage publisher, we need this early because scheduling decisions
    # might happen quite early on.
    resource_pid = os.fork()
    if resource_pid == 0:
        LOG.removeHandler(handler)
        resource_daemon.Monitor('resources').run()

    # If I am the network node, I need some setup
    if config.parsed.get('NODE_IP') == config.parsed.get('NETWORK_NODE_IP'):
        # Bootstrap the floating network in the Networks table
        floating_network = db.get_network('floating')
        if not floating_network:
            db.create_floating_network(config.parsed.get('FLOATING_NETWORK'))
            floating_network = net.from_db('floating')

        subst = {
            'physical_bridge': 'phy-br-%s' % config.parsed.get('NODE_EGRESS_NIC'),
            'physical_nic': config.parsed.get('NODE_EGRESS_NIC')
        }

        if not util.check_for_interface(subst['physical_bridge']):
            # NOTE(mikal): Adding the physical interface to the physical bridge
            # is considered outside the scope of the orchestration software as it
            # will cause the node to lose network connectivity. So instead all we
            # do is create a bridge if it doesn't exist and the wire everything up
            # to it. We can do egress NAT in that state, even if floating IPs
            # don't work.
            with util.RecordedOperation('create physical bridge', 'startup') as _:
                # No locking as read only
                ipm = db.get_ipmanager('floating')
                subst['master_float'] = ipm.get_address_at_index(1)
                subst['netmask'] = ipm.netmask

                processutils.execute(
                    'ip link add %(physical_bridge)s type bridge' % subst, shell=True)
                processutils.execute(
                    'ip link set %(physical_bridge)s up' % subst, shell=True)
                processutils.execute(
                    'ip addr add %(master_float)s/%(netmask)s dev %(physical_bridge)s' % subst,
                    shell=True)

                processutils.execute(
                    'iptables -A FORWARD -o %(physical_nic)s -i %(physical_bridge)s -j ACCEPT' % subst,
                    shell=True)
                processutils.execute(
                    'iptables -A FORWARD -i %(physical_nic)s -o %(physical_bridge)s -j ACCEPT' % subst,
                    shell=True)
                processutils.execute(
                    'iptables -t nat -A POSTROUTING -o %(physical_nic)s -j MASQUERADE' % subst,
                    shell=True)

    # Network mesh maintenance
    net_pid = os.fork()
    if net_pid == 0:
        LOG.removeHandler(handler)
        net_daemon.Monitor('net').run()

    # Old object deleter
    cleaner_pid = os.fork()
    if cleaner_pid == 0:
        LOG.removeHandler(handler)
        cleaner_daemon.Monitor('cleaner').run()

    # REST API
    external_api_pid = os.fork()
    if external_api_pid == 0:
        LOG.removeHandler(handler)
        external_api_daemon.Monitor('api').run()

    # Triggers
    trigger_pid = os.fork()
    if trigger_pid == 0:
        LOG.removeHandler(handler)
        trigger_daemon.Monitor('triggers').run()

    # Queues
    queues_pid = os.fork()
    if queues_pid == 0:
        LOG.removeHandler(handler)
        queues_daemon.Monitor('queues').run()

    setproctitle.setproctitle(daemon.process_name('main'))
    LOG.info('network monitor pid is %d' % net_pid)
    LOG.info('external api pid is %d' % external_api_pid)
    LOG.info('resources monitor pid is %d' % resource_pid)
    LOG.info('cleaner pid is %d' % cleaner_pid)
    LOG.info('trigger pid is %d' % trigger_pid)
    LOG.info('queues pid is %d' % queues_pid)

    restore_instances()

    procnames = {
        external_api_pid: 'external api',
        net_pid: 'network monitor',
        resource_pid: 'resource monitor',
        cleaner_pid: 'cleaner',
        trigger_pid: 'trigger',
        queues_pid: 'queues'
    }

    while True:
        time.sleep(10)
        wpid, _ = os.waitpid(-1, os.WNOHANG)
        if wpid != 0:
            LOG.warning('Subprocess %d (%s) died'
                        % (wpid, procnames.get(wpid, 'unknown')))

        db.see_this_node()
