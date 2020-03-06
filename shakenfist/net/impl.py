# Copyright 2020 Michael Still

import ipaddress
import logging
import random
import re

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
                   ipblock=dbnet['netblock'],
                   physical_nic=config.parsed.get('NODE_EGRESS_NIC'))


class Network(object):
    # NOTE(mikal): it should be noted that the maximum interface name length
    # on Linux is 15 user visible characters.
    def __init__(self, uuid=None, vxlan_id=1, provide_dhcp=False,
                 physical_nic='eth0', nodes=None, ipblock=None):
        self.uuid = uuid
        self.vxlan_id = vxlan_id
        self.provide_dhcp = provide_dhcp
        self.physical_nic = physical_nic

        self.vx_interface = 'vxlan-%s' % self.vxlan_id
        self.vx_bridge = 'br-%s' % self.vx_interface

        self.ipnetwork = ipaddress.ip_network(ipblock, strict=False)

        if nodes:
            self.nodes = nodes
            if config.parsed.get('NODE_IP') in nodes:
                nodes.remove(config.parsed.get('NODE_IP'))
        else:
            self.nodes = []

        if self.provide_dhcp:
            self.dhcp_interface = 'dhcpd-%s' % self.vxlan_id
            self.dhcp_peer = 'dhcpp-%s' % self.vxlan_id
        else:
            self.dhcp_interface = None
            self.dhcp_peer = None

    def __str__(self):
        return 'network(%s, vxid %s)' % (self.uuid, self.vxlan_id)

    def allocate_ip_to_instance(self, instance):
        addr = self.allocate_ip()
        instance.set_network_details(addr, self.subst_dict())

    def allocate_ip(self):
        addresses = list(self.ipnetwork.hosts())[1:]

        for interface in db.get_network_interfaces(self.uuid):
            if interface['ipv4'] in addresses:
                addresses.remove(interface['ipv4'])

        random.shuffle(addresses)
        return addresses[0]

    def subst_dict(self):
        instances = []

        interfaces = list(db.get_network_interfaces(self.uuid))
        for interface in interfaces:
            hostname = db.get_instance(interface['instance_uuid'])
            instances.append(
                {
                    'uuid': interface['instance_uuid'],
                    'macaddr': interface['macaddr'],
                    'ipv4': interface['ipv4'],
                    'name': hostname['name']
                }
            )

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
            'broadcast': self.ipnetwork.broadcast_address,

            'instances': instances
        }

        return retval

    def create(self):
        subst = self.subst_dict()

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

        if self.provide_dhcp and not util.check_for_interface(self.dhcp_interface):
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
                    'ip addr add 192.168.200.1/24 dev %(dhcp_interface)s' % subst,
                    shell=True)

        if self.provide_dhcp:
            self.dhcp = dhcp.DHCP(self)
            self.update_dhcp()

        self.ensure_mesh(self.nodes)

    def update_dhcp(self):
        with util.RecordedOperation('update dhcp', self) as ro:
            self.dhcp.make_config()
            self.dhcp.restart_dhcpd()

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

    def ensure_mesh(self, all_nodes):
        with util.RecordedOperation('ensure mesh', self) as ro:
            for node in self.discover_mesh():
                if node in all_nodes:
                    all_nodes.remove(node)
                else:
                    self._remove_mesh_element(node)

            for node in all_nodes:
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
