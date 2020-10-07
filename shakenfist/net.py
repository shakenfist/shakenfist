# Copyright 2020 Michael Still

import os
import psutil
import re


from shakenfist import config
from shakenfist import db
from shakenfist import dhcp
from shakenfist import logutil
from shakenfist import util


LOG, _ = logutil.setup(__name__)


def from_db(uuid):
    dbnet = db.get_network(uuid)
    if not dbnet:
        return None

    n = Network(uuid=dbnet['uuid'],
                vxlan_id=dbnet['vxid'],
                provide_dhcp=dbnet['provide_dhcp'],
                provide_nat=dbnet['provide_nat'],
                ipblock=dbnet['netblock'],
                physical_nic=config.parsed.get('NODE_EGRESS_NIC'),
                floating_gateway=dbnet['floating_gateway'],
                namespace=dbnet['namespace'])

    if dbnet['state'] == 'deleted':
        LOG.withObj(n).info('Network is deleted, returning None.')
        return None

    return n


class Network(object):
    # NOTE(mikal): it should be noted that the maximum interface name length
    # on Linux is 15 user visible characters.
    def __init__(self, uuid=None, vxlan_id=1, provide_dhcp=False, provide_nat=False,
                 physical_nic='eth0', ipblock=None, floating_gateway=None,
                 namespace=None):
        self.uuid = uuid
        self.vxlan_id = vxlan_id
        self.provide_dhcp = provide_dhcp
        self.provide_nat = provide_nat
        self.physical_nic = physical_nic
        self.floating_gateway = floating_gateway
        self.namespace = namespace

        with db.get_lock('ipmanager', None, self.uuid, ttl=120):
            ipm = db.get_ipmanager(self.uuid)

            self.ipblock = ipm.network_address
            self.router = ipm.get_address_at_index(1)
            self.dhcp_start = ipm.get_address_at_index(2)
            self.netmask = ipm.netmask
            self.broadcast = ipm.broadcast_address
            self.network_address = ipm.network_address

            ipm.reserve(self.router)
            db.persist_ipmanager(self.uuid, ipm.save())

    def __str__(self):
        return 'network(%s, vxid %s)' % (self.uuid, self.vxlan_id)

    def unique_label(self):
        return ('network', self.uuid)

    def subst_dict(self):
        retval = {
            'vx_id': self.vxlan_id,
            'vx_interface': 'vxlan-%s' % self.vxlan_id,
            'vx_bridge': 'br-vxlan-%s' % self.vxlan_id,
            'vx_veth_outer': 'veth-%s-o' % self.vxlan_id,
            'vx_veth_inner': 'veth-%s-i' % self.vxlan_id,

            'physical_interface': self.physical_nic,
            'physical_bridge': 'phy-br-%s' % config.parsed.get('NODE_EGRESS_NIC'),
            'physical_veth_outer': 'phy-%s-o' % self.vxlan_id,
            'physical_veth_inner': 'phy-%s-i' % self.vxlan_id,

            'netns': self.uuid,
            'in_netns': 'ip netns exec %s' % self.uuid,

            'ipblock': self.ipblock,
            'netmask': self.netmask,
            'router': self.router,
            'broadcast': self.broadcast,
        }
        return retval

    def persist_floating_gateway(self):
        db.persist_floating_gateway(self.uuid, self.floating_gateway)

    def is_okay(self):
        """Check if network is created and running"""
        # TODO(andy):This will be built upon with further code re-design

        if not self.is_created():
            return False

        if self.provide_dhcp and util.is_network_node():
            if not self.is_dnsmasq_running():
                return False

        return True

    def is_created(self):
        """Attempt to ensure network has been created successfully"""

        subst = self.subst_dict()
        if not util.check_for_interface(subst['vx_bridge'], up=True):
            LOG.withObj(self).warning('%s is not up' % subst['vx_bridge'])
            return False

        return True

    def create(self):
        subst = self.subst_dict()

        with db.get_lock('network', None, self.uuid, ttl=120):
            if not util.check_for_interface(subst['vx_interface']):
                with util.RecordedOperation('create vxlan interface', self):
                    util.create_interface(
                        subst['vx_interface'], 'vxlan',
                        'id %(vx_id)s dev %(physical_interface)s dstport 0' % subst)
                    util.execute(None,
                                 'sysctl -w net.ipv4.conf.%(vx_interface)s.arp_notify=1' % subst)

            if not util.check_for_interface(subst['vx_bridge']):
                with util.RecordedOperation('create vxlan bridge', self):
                    util.create_interface(subst['vx_bridge'], 'bridge', '')
                    util.execute(None,
                                 'ip link set %(vx_interface)s master %(vx_bridge)s' % subst)
                    util.execute(None,
                                 'ip link set %(vx_interface)s up' % subst)
                    util.execute(None,
                                 'ip link set %(vx_bridge)s up' % subst)
                    util.execute(None,
                                 'sysctl -w net.ipv4.conf.%(vx_bridge)s.arp_notify=1' % subst)
                    util.execute(None,
                                 'brctl setfd %(vx_bridge)s 0' % subst)
                    util.execute(None,
                                 'brctl stp %(vx_bridge)s off' % subst)
                    util.execute(None,
                                 'brctl setageing %(vx_bridge)s 0' % subst)

        if util.is_network_node():
            if not os.path.exists('/var/run/netns/%(netns)s' % subst):
                with util.RecordedOperation('create netns', self):
                    util.execute(None,
                                 'ip netns add %(netns)s' % subst)

            if not util.check_for_interface(subst['vx_veth_outer']):
                with util.RecordedOperation('create router veth', self):
                    util.create_interface(
                        subst['vx_veth_outer'], 'veth',
                        'peer name %(vx_veth_inner)s' % subst)
                    util.execute(None,
                                 'ip link set %(vx_veth_inner)s netns %(netns)s' % subst)
                    util.execute(None,
                                 'brctl addif %(vx_bridge)s %(vx_veth_outer)s' % subst)
                    util.execute(None,
                                 'ip link set %(vx_veth_outer)s up' % subst)
                    util.execute(None,
                                 '%(in_netns)s ip link set %(vx_veth_inner)s up' % subst)
                    util.execute(None,
                                 '%(in_netns)s ip addr add %(router)s/%(netmask)s dev %(vx_veth_inner)s' % subst)

            if not util.check_for_interface(subst['physical_veth_outer']):
                with util.RecordedOperation('create physical veth', self):
                    util.create_interface(
                        subst['physical_veth_outer'], 'veth',
                        'peer name %(physical_veth_inner)s' % subst)
                    util.execute(None,
                                 'brctl addif %(physical_bridge)s %(physical_veth_outer)s' % subst)
                    util.execute(None,
                                 'ip link set %(physical_veth_outer)s up' % subst)
                    util.execute(None,
                                 'ip link set %(physical_veth_inner)s netns %(netns)s' % subst)

            self.deploy_nat()
            self.update_dhcp()
        else:
            db.enqueue('networknode',
                       {
                           'type': 'deploy',
                           'network_uuid': self.uuid
                       })
            db.add_event('network', self.uuid, 'deploy',
                         'enqueued', None, None)

    def deploy_nat(self):
        if not self.provide_nat:
            return

        subst = self.subst_dict()
        if not self.floating_gateway:
            with db.get_lock('ipmanager', None, 'floating', ttl=120):
                ipm = db.get_ipmanager('floating')
                self.floating_gateway = ipm.get_random_free_address()
                db.persist_ipmanager('floating', ipm.save())
                self.persist_floating_gateway()

        # No lock because no data changing
        ipm = db.get_ipmanager('floating')
        subst['floating_router'] = ipm.get_address_at_index(1)
        subst['floating_gateway'] = self.floating_gateway
        subst['floating_netmask'] = ipm.netmask

        with db.get_lock('network', None, self.uuid, ttl=120):
            if not subst['floating_gateway'] in list(util.get_interface_addresses(
                    subst['netns'], subst['physical_veth_inner'])):
                with util.RecordedOperation('enable virtual routing', self):
                    util.execute(None,
                                 '%(in_netns)s ip addr add %(floating_gateway)s/%(floating_netmask)s '
                                 'dev %(physical_veth_inner)s' % subst)
                    util.execute(None,
                                 '%(in_netns)s ip link set %(physical_veth_inner)s up' % subst)
                    util.execute(None,
                                 '%(in_netns)s route add default gw %(floating_router)s' % subst)

            if not util.nat_rules_for_ipblock(self.network_address):
                with util.RecordedOperation('enable nat', self):
                    util.execute(None,
                                 'echo 1 > /proc/sys/net/ipv4/ip_forward')
                    util.execute(None,
                                 '%(in_netns)s iptables -A FORWARD -o %(physical_veth_inner)s '
                                 '-i %(vx_veth_inner)s -j ACCEPT' % subst)
                    util.execute(None,
                                 '%(in_netns)s iptables -A FORWARD -i %(physical_veth_inner)s '
                                 '-o %(vx_veth_inner)s -j ACCEPT' % subst)
                    util.execute(None,
                                 '%(in_netns)s iptables -t nat -A POSTROUTING -s %(ipblock)s/%(netmask)s '
                                 '-o %(physical_veth_inner)s -j MASQUERADE' % subst)

    def delete(self):
        subst = self.subst_dict()

        # Cleanup local node
        with db.get_lock('network', None, self.uuid, ttl=120):
            if util.check_for_interface(subst['vx_bridge']):
                with util.RecordedOperation('delete vxlan bridge', self):
                    util.execute(None, 'ip link delete %(vx_bridge)s' % subst)

            if util.check_for_interface(subst['vx_interface']):
                with util.RecordedOperation('delete vxlan interface', self):
                    util.execute(
                        None, 'ip link delete %(vx_interface)s' % subst)

            # If this is the network node do additional cleanup
            if util.is_network_node():
                if util.check_for_interface(subst['vx_veth_outer']):
                    with util.RecordedOperation('delete router veth', self):
                        util.execute(
                            None, 'ip link delete %(vx_veth_outer)s' % subst)

                if util.check_for_interface(subst['physical_veth_outer']):
                    with util.RecordedOperation('delete physical veth', self):
                        util.execute(
                            None, 'ip link delete %(physical_veth_outer)s' % subst)

                if os.path.exists('/var/run/netns/%(netns)s' % subst):
                    with util.RecordedOperation('delete netns', self):
                        util.execute(None, 'ip netns del %(netns)s' % subst)

                if self.floating_gateway:
                    with db.get_lock('ipmanager', None, 'floating', ttl=120):
                        ipm = db.get_ipmanager('floating')
                        ipm.release(self.floating_gateway)
                        db.persist_ipmanager('floating', ipm.save())

    def is_dnsmasq_running(self):
        """Determine if dnsmasq process is running for this network"""
        subst = self.subst_dict()
        d = dhcp.DHCP(self.uuid, subst['vx_veth_inner'])
        pid = d.get_pid()
        if pid and psutil.pid_exists(pid):
            return True

        LOG.withObj(self).warning('dnsmasq is not running')
        return False

    def update_dhcp(self):
        if not self.provide_dhcp:
            return

        if util.is_network_node():
            self.ensure_mesh()
            subst = self.subst_dict()
            with util.RecordedOperation('update dhcp', self):
                with db.get_lock('network', None, self.uuid, ttl=120):
                    d = dhcp.DHCP(self.uuid, subst['vx_veth_inner'])
                    d.restart_dhcpd()
        else:
            db.enqueue('networknode',
                       {
                           'type': 'update_dhcp',
                           'network_uuid': self.uuid
                       })
            db.add_event('network', self.uuid, 'update dhcp',
                         'enqueued', None, None)

    def remove_dhcp(self):
        if util.is_network_node():
            subst = self.subst_dict()
            with util.RecordedOperation('remove dhcp', self):
                with db.get_lock('network', None, self.uuid, ttl=120):
                    d = dhcp.DHCP(self.uuid, subst['vx_veth_inner'])
                    d.remove_dhcpd()
        else:
            db.enqueue('networknode',
                       {
                           'type': 'remove_dhcp',
                           'network_uuid': self.uuid
                       })
            db.add_event('network', self.uuid, 'remove dhcp',
                         'enqueued', None, None)

    def discover_mesh(self):
        mesh_re = re.compile(r'00:00:00:00:00:00 dst (.*) self permanent')

        stdout, _ = util.execute(
            None, 'bridge fdb show brport %(vx_interface)s' % self.subst_dict())

        for line in stdout.split('\n'):
            m = mesh_re.match(line)
            if m:
                yield m.group(1)

    def ensure_mesh(self):
        with db.get_lock('network', None, self.uuid, ttl=120):
            removed = []
            added = []

            instances = []
            for iface in db.get_network_interfaces(self.uuid):
                if not iface['instance_uuid'] in instances:
                    instances.append(iface['instance_uuid'])

            node_fqdns = []
            for inst in instances:
                i = db.get_instance(inst)
                if not i:
                    continue
                if not i['node']:
                    continue

                if not i['node'] in node_fqdns:
                    node_fqdns.append(i['node'])

            # NOTE(mikal): why not use DNS here? Well, DNS might be outside
            # the control of the deployer if we're running in a public cloud
            # as an overlay cloud...
            node_ips = [config.parsed.get('NETWORK_NODE_IP')]
            for fqdn in node_fqdns:
                ip = db.get_node(fqdn)['ip']
                if ip not in node_ips:
                    node_ips.append(ip)

            discovered = list(self.discover_mesh())
            LOG.withObj(self).withField(
                'discovered', discovered).debug('Discovered mesh elements')

            for node in discovered:
                if node in node_ips:
                    node_ips.remove(node)
                else:
                    self._remove_mesh_element(node)
                    removed.append(node)

            for node in node_ips:
                self._add_mesh_element(node)
                added.append(node)

            if removed:
                db.add_event('network', self.uuid, 'remove mesh elements',
                             None, None, ' '.join(removed))
            if added:
                db.add_event('network', self.uuid, 'add mesh elements',
                             None, None, ' '.join(added))

    def _add_mesh_element(self, node):
        LOG.withObj(self).info('Adding new mesh element %s' % node)
        subst = self.subst_dict()
        subst['node'] = node
        util.execute(None,
                     'bridge fdb append to 00:00:00:00:00:00 dst %(node)s dev %(vx_interface)s'
                     % subst)

    def _remove_mesh_element(self, node):
        LOG.withObj(self).info('Removing excess mesh element %s' % node)
        subst = self.subst_dict()
        subst['node'] = node
        util.execute(None,
                     'bridge fdb del to 00:00:00:00:00:00 dst %(node)s dev %(vx_interface)s'
                     % subst)

    def add_floating_ip(self, floating_address, inner_address):
        LOG.withObj(self).info('Adding floating ip %s -> %s'
                               % (floating_address, inner_address))
        subst = self.subst_dict()
        subst['floating_address'] = floating_address
        subst['inner_address'] = inner_address

        util.execute(None,
                     'ip addr add %(floating_address)s/%(netmask)s '
                     'dev %(physical_veth_outer)s' % subst)
        util.execute(None,
                     '%(in_netns)s iptables -t nat -A PREROUTING '
                     '-d %(floating_address)s -j DNAT --to-destination %(inner_address)s' % subst)

    def remove_floating_ip(self, floating_address, inner_address):
        LOG.withObj(self).info('Removing floating ip %s -> %s'
                               % (floating_address, inner_address))
        subst = self.subst_dict()
        subst['floating_address'] = floating_address
        subst['inner_address'] = inner_address

        util.execute(None,
                     'ip addr del %(floating_address)s/%(netmask)s '
                     'dev %(physical_veth_outer)s' % subst)
        util.execute(None,
                     '%(in_netns)s iptables -t nat -D PREROUTING '
                     '-d %(floating_address)s -j DNAT --to-destination %(inner_address)s' % subst)
