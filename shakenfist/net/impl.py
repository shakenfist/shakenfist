# Copyright 2020 Michael Still

import ipaddress
import logging
import random
import re
import requests

from oslo_concurrency import lockutils
from oslo_concurrency import processutils

from shakenfist import config
from shakenfist.db import impl as db
from shakenfist import dhcp
from shakenfist import util


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


def from_db(uuid):
    dbnet = db.get_network(uuid)
    if not dbnet:
        return None

    return Network(uuid=dbnet['uuid'],
                   vxlan_id=dbnet['vxid'],
                   provide_dhcp=dbnet['provide_dhcp'],
                   provide_nat=dbnet['provide_nat'],
                   ipblock=dbnet['netblock'],
                   physical_nic=config.parsed.get('NODE_EGRESS_NIC'))


class Network(object):
    # NOTE(mikal): it should be noted that the maximum interface name length
    # on Linux is 15 user visible characters.
    def __init__(self, uuid=None, vxlan_id=1, provide_dhcp=False, provide_nat=False,
                 physical_nic='eth0', nodes=None, ipblock=None):
        self.uuid = uuid
        self.vxlan_id = vxlan_id
        self.provide_dhcp = provide_dhcp
        self.provide_nat = provide_nat
        self.physical_nic = physical_nic

        self.vx_interface = 'vxlan-%s' % self.vxlan_id
        self.vx_bridge = 'br-%s' % self.vx_interface

        self.ipnetwork = ipaddress.ip_network(ipblock, strict=False)

        if self.provide_dhcp:
            self.dhcp_interface = 'dhcpd-%s' % self.vxlan_id
            self.dhcp_peer = 'dhcpp-%s' % self.vxlan_id
        else:
            self.dhcp_interface = None
            self.dhcp_peer = None

    def __str__(self):
        return 'network(%s, vxid %s)' % (self.uuid, self.vxlan_id)

    def allocate_ip(self):
        with lockutils.lock('sf_net_%s' % self.uuid, external=True, lock_path='/tmp/'):
            addresses = list(self.ipnetwork.hosts())[2:]

            for interface in db.get_network_interfaces(self.uuid):
                if interface['ipv4'] in addresses:
                    addresses.remove(interface['ipv4'])

            random.shuffle(addresses)
            return addresses[0]

    def subst_dict(self):
        retval = {
            'vx_id': self.vxlan_id,
            'vx_interface': self.vx_interface,
            'vx_bridge': self.vx_bridge,
            'dhcp_interface': self.dhcp_interface,
            'dhcp_peer': self.dhcp_peer,
            'phy_interface': self.physical_nic,

            'ipblock': self.ipnetwork.network_address,
            'netmask': self.ipnetwork.netmask,
            'router': list(self.ipnetwork.hosts())[0],
            'dhcpserver': list(self.ipnetwork.hosts())[1],
            'broadcast': self.ipnetwork.broadcast_address,
        }
        return retval

    def create(self):
        subst = self.subst_dict()

        with lockutils.lock('sf_net_%s' % self.uuid, external=True, lock_path='/tmp/'):
            if not util.check_for_interface(self.vx_interface):
                with util.RecordedOperation('create vxlan interface', self) as ro:
                    processutils.execute(
                        'ip link add %(vx_interface)s type vxlan id %(vx_id)s '
                        'dev %(phy_interface)s dstport 0'
                        % subst, shell=True)
                    processutils.execute(
                        'sysctl -w net.ipv4.conf.%(vx_interface)s.arp_notify=1' % subst,
                        shell=True)

            if not util.check_for_interface(self.vx_bridge):
                with util.RecordedOperation('create vxlan bridge', self) as ro:
                    processutils.execute(
                        'ip link add %(vx_bridge)s type bridge' % subst, shell=True)
                    processutils.execute(
                        'ip link set %(vx_interface)s master %(vx_bridge)s' % subst,
                        shell=True)
                    processutils.execute(
                        'ip link set %(vx_interface)s up' % subst, shell=True)
                    processutils.execute(
                        'ip link set %(vx_bridge)s up' % subst, shell=True)
                    processutils.execute(
                        'sysctl -w net.ipv4.conf.%(vx_bridge)s.arp_notify=1' % subst,
                        shell=True)
                    processutils.execute(
                        'brctl setfd %(vx_bridge)s 0' % subst, shell=True)
                    processutils.execute(
                        'brctl stp %(vx_bridge)s off' % subst, shell=True)
                    processutils.execute(
                        'brctl setageing %(vx_bridge)s 0' % subst, shell=True)

        if config.parsed.get('NODE_IP') == config.parsed.get('NETWORK_NODE_IP'):
            self.deploy_nat()
            self.deploy_dhcp()
        else:
            requests.request(
                'put',
                ('http://%s:%d/deploy_network_node'
                 % (config.parsed.get('NETWORK_NODE_IP'),
                    config.parsed.get('API_PORT'))),
                data={
                    'uuid': self.uuid
                })

    def deploy_nat(self):
        if not self.provide_nat:
            return

        subst = self.subst_dict()

        with util.RecordedOperation('enable NAT', self) as ro:
            with lockutils.lock('sf_net_%s' % self.uuid, external=True, lock_path='/tmp/'):
                out, _ = processutils.execute(
                    'ip addr show dev %(vx_bridge)s' % subst,
                    shell=True)
                if out.find('inet %(router)s' % subst) == -1:
                    processutils.execute(
                        'ip addr add %(router)s/%(netmask)s dev %(vx_bridge)s' % subst,
                        shell=True)
                    processutils.execute('echo 1 > /proc/sys/net/ipv4/ip_forward',
                                         shell=True)
                    processutils.execute(
                        'iptables -A FORWARD -o %(phy_interface)s '
                        '-i %(vx_bridge)s -j ACCEPT' % subst,
                        shell=True)
                    processutils.execute(
                        'iptables -A FORWARD -i %(phy_interface)s '
                        '-o %(vx_bridge)s -j ACCEPT' % subst,
                        shell=True)
                    processutils.execute(
                        'iptables -t nat -A POSTROUTING -s %(ipblock)s/%(netmask)s '
                        '-o %(phy_interface)s -j MASQUERADE' % subst,
                        shell=True
                    )

    def deploy_dhcp(self):
        if not self.provide_dhcp:
            return

        subst = self.subst_dict()

        if not util.check_for_interface(self.dhcp_interface):
            with lockutils.lock('sf_net_%s' % self.uuid, external=True, lock_path='/tmp/'):
                with util.RecordedOperation('create dhcp interface', self) as ro:
                    processutils.execute(
                        'ip link add %(dhcp_interface)s type veth peer name '
                        '%(dhcp_peer)s' % subst, shell=True)
                    processutils.execute(
                        'ip link set %(dhcp_peer)s master %(vx_bridge)s'
                        % subst, shell=True)
                    processutils.execute(
                        'ip link set %(dhcp_interface)s up' % subst, shell=True)
                    processutils.execute(
                        'ip link set %(dhcp_peer)s up' % subst, shell=True)
                    processutils.execute(
                        'ip addr add %(dhcpserver)s/%(netmask)s dev %(dhcp_interface)s' % subst,
                        shell=True)

        self.update_dhcp()

    def delete(self):
        subst = self.subst_dict()

        with lockutils.lock('sf_net_%s' % self.uuid, external=True, lock_path='/tmp/'):
            if util.check_for_interface(self.dhcp_interface):
                # This will delete the peer as well
                with util.RecordedOperation('delete dhcp interface', self) as _:
                    processutils.execute('ip link delete %(dhcp_interface)s' % subst,
                                         shell=True)

            if util.check_for_interface(self.vx_bridge):
                with util.RecordedOperation('delete vxlan bridge', self) as _:
                    processutils.execute('ip link delete %(vx_bridge)s' % subst,
                                         shell=True)

            if util.check_for_interface(self.vx_interface):
                with util.RecordedOperation('delete vxlan interface', self) as _:
                    processutils.execute('ip link delete %(vx_interface)s' % subst,
                                         shell=True)

    def update_dhcp(self):
        if config.parsed.get('NODE_IP') == config.parsed.get('NETWORK_NODE_IP'):
            with util.RecordedOperation('update dhcp', self) as _:
                with lockutils.lock('sf_net_%s' % self.uuid, external=True, lock_path='/tmp/'):
                    d = dhcp.DHCP(self.uuid)
                    d.make_config()
                    d.restart_dhcpd()
        else:
            requests.request(
                'put',
                ('http://%s:%d/update_dhcp'
                 % (config.parsed.get('NETWORK_NODE_IP'),
                    config.parsed.get('API_PORT'))),
                data={
                    'uuid': self.uuid
                })

    def remove_dhcp(self):
        if config.parsed.get('NODE_IP') == config.parsed.get('NETWORK_NODE_IP'):
            with util.RecordedOperation('remove dhcp', self) as _:
                with lockutils.lock('sf_net_%s' % self.uuid, external=True, lock_path='/tmp/'):
                    d = dhcp.DHCP(self.uuid)
                    d.remove_dhcpd()
                    d.remove_config()
        else:
            requests.request(
                'put',
                ('http://%s:%d/remove_dhcp'
                 % (config.parsed.get('NETWORK_NODE_IP'),
                    config.parsed.get('API_PORT'))),
                data={
                    'uuid': self.uuid
                })

    def discover_mesh(self):
        mesh_re = re.compile('00: 00: 00: 00: 00: 00 dst (.*) self permanent')

        with util.RecordedOperation('discover mesh', self) as ro:
            stdout, _ = processutils.execute(
                'bridge fdb show brport %(vx_interface)s' % self.subst_dict(),
                shell=True)

            for line in stdout.split('\n'):
                m = mesh_re.match(line)
                if m:
                    yield m.group(1)

    def ensure_mesh(self):
        with util.RecordedOperation('ensure mesh', self) as ro:
            instances = []
            for iface in db.get_network_interfaces(self.uuid):
                if not iface['instance_uuid'] in instances:
                    instances.append(iface['instance_uuid'])

            node_fqdns = []
            for inst in instances:
                i = db.get_instance(inst)
                if not i['node'] in node_fqdns:
                    node_fqdns.append(i['node'])

            # NOTE(mikal): why not use DNS here? Well, DNS might be outside
            # the control of the deployer if we're running in a public cloud
            # as an overlay cloud...
            node_ips = [config.parsed.get('NETWORK_NODE_IP')]
            for fqdn in node_fqdns:
                node_ips.append(db.get_node(fqdn)['ip'])

            for node in self.discover_mesh():
                if node in node_ips:
                    node_ips.remove(node)
                else:
                    self._remove_mesh_element(node)

            for node in node_ips:
                self._add_mesh_element(node)

    def _add_mesh_element(self, node):
        LOG.info('%s: Adding new mesh element %s' % (self, node))
        subst = self.subst_dict()
        subst['node'] = node
        processutils.execute(
            'bridge fdb append to 00:00:00:00:00:00 dst %(node)s dev %(vx_interface)s'
            % subst,
            shell=True)

    def _remove_mesh_element(self, node):
        LOG.info('%s: Removing excess mesh element %s' % (self, node))
        subst = self.subst_dict()
        subst['node'] = node
        processutils.execute(
            'bridge fdb del to 00:00:00:00:00:00 dst %(node)s dev %(vx_interface)s'
            % subst,
            shell=True)
