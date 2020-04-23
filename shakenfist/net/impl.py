# Copyright 2020 Michael Still

import logging
import os
import random
import re
import requests

from oslo_concurrency import lockutils
from oslo_concurrency import processutils

from shakenfist import config
from shakenfist.db import impl as db
from shakenfist import dhcp
from shakenfist import ipmanager
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
                 physical_nic='eth0', ipblock=None):
        self.uuid = uuid
        self.vxlan_id = vxlan_id
        self.provide_dhcp = provide_dhcp
        self.provide_nat = provide_nat
        self.physical_nic = physical_nic

        self.ipmanager = ipmanager.NetBlock(ipblock)
        self.router = self.ipmanager.get_address_at_index(1)
        self.ipmanager.reserve(self.router)
        self.ipmanager.reserve(self.ipmanager.network_address)

    def __str__(self):
        return 'network(%s, vxid %s)' % (self.uuid, self.vxlan_id)

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

            'namespace': self.uuid,
            'in_namespace': 'ip netns exec %s' % self.uuid,

            'ipblock': self.ipmanager.network_address,
            'netmask': self.ipmanager.netmask,
            'router': self.router,
            'broadcast': self.ipmanager.broadcast_address,
        }
        return retval

    def create(self):
        subst = self.subst_dict()

        with lockutils.lock('sf_net_%s' % self.uuid, external=True, lock_path='/tmp/'):
            if not util.check_for_interface(subst['vx_interface']):
                with util.RecordedOperation('create vxlan interface', self) as _:
                    processutils.execute(
                        'ip link add %(vx_interface)s type vxlan id %(vx_id)s '
                        'dev %(physical_interface)s dstport 0'
                        % subst, shell=True)
                    processutils.execute(
                        'sysctl -w net.ipv4.conf.%(vx_interface)s.arp_notify=1' % subst,
                        shell=True)

            if not util.check_for_interface(subst['vx_bridge']):
                with util.RecordedOperation('create vxlan bridge', self) as _:
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
            if not os.path.exists('/var/run/netns/%(namespace)s' % subst):
                with util.RecordedOperation('create netns interface', self) as _:
                    processutils.execute(
                        'ip netns add %(namespace)s' % subst, shell=True)

            if not util.check_for_interface(subst['vx_veth_outer']):
                with util.RecordedOperation('create router veth', self) as _:
                    processutils.execute(
                        'ip link add %(vx_veth_outer)s type veth peer name %(vx_veth_inner)s' % subst,
                        shell=True)
                    processutils.execute(
                        'ip link set %(vx_veth_inner)s netns %(namespace)s' % subst, shell=True)
                    processutils.execute(
                        'brctl addif %(vx_bridge)s %(vx_veth_outer)s' % subst, shell=True)
                    processutils.execute(
                        'ip link set %(vx_veth_outer)s up' % subst, shell=True)
                    processutils.execute(
                        '%(in_namespace)s ip link set %(vx_veth_inner)s up' % subst, shell=True)
                    processutils.execute(
                        '%(in_namespace)s ip addr add %(router)s/%(netmask)s dev %(vx_veth_inner)s' % subst,
                        shell=True)

            if not util.check_for_interface(subst['physical_veth_outer']):
                with util.RecordedOperation('create router veth', self) as _:
                    processutils.execute(
                        'ip link add %(physical_veth_outer)s type veth peer name '
                        '%(physical_veth_inner)s' % subst,
                        shell=True)
                    processutils.execute(
                        'brctl addif %(physical_bridge)s %(physical_veth_outer)s' % subst,
                        shell=True)
                    processutils.execute(
                        'ip link set %(physical_veth_outer)s up' % subst, shell=True)
                    processutils.execute(
                        'ip link set %(physical_veth_inner)s netns %(namespace)s' % subst,
                        shell=True)

            self.deploy_nat()
            self.update_dhcp()
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
        with lockutils.lock('sf_net_%s' % self.uuid, external=True, lock_path='/tmp/'):
            if not '192.168.20.2' in list(util.get_interface_addresses(
                    subst['namespace'], subst['physical_veth_inner'])):
                with util.RecordedOperation('enable virtual routing', self) as _:
                    processutils.execute(
                        '%(in_namespace)s ip addr add 192.168.20.2/24 dev %(physical_veth_inner)s' % subst,
                        shell=True)
                    processutils.execute(
                        '%(in_namespace)s ip link set %(physical_veth_inner)s up' % subst, shell=True)
                    processutils.execute(
                        '%(in_namespace)s route add default gw 192.168.20.1' % subst,
                        shell=True)

            if not util.nat_rules_for_ipblock(self.ipmanager.network_address):
                with util.RecordedOperation('enable nat', self) as _:
                    processutils.execute(
                        'echo 1 > /proc/sys/net/ipv4/ip_forward', shell=True)
                    processutils.execute(
                        '%(in_namespace)s iptables -A FORWARD -o %(physical_veth_inner)s '
                        '-i %(vx_veth_inner)s -j ACCEPT' % subst,
                        shell=True)
                    processutils.execute(
                        '%(in_namespace)s iptables -A FORWARD -i %(physical_veth_inner)s '
                        '-o %(vx_veth_inner)s -j ACCEPT' % subst,
                        shell=True)
                    processutils.execute(
                        '%(in_namespace)s iptables -t nat -A POSTROUTING -s %(ipblock)s/%(netmask)s '
                        '-o %(physical_veth_inner)s -j MASQUERADE' % subst,
                        shell=True
                    )

    def delete(self):
        subst = self.subst_dict()

        with lockutils.lock('sf_net_%s' % self.uuid, external=True, lock_path='/tmp/'):
            if util.check_for_interface(subst['vx_bridge']):
                with util.RecordedOperation('delete vxlan bridge', self) as _:
                    processutils.execute('ip link delete %(vx_bridge)s' % subst,
                                         shell=True)

            if util.check_for_interface(subst['vx_interface']):
                with util.RecordedOperation('delete vxlan interface', self) as _:
                    processutils.execute('ip link delete %(vx_interface)s' % subst,
                                         shell=True)

    def update_dhcp(self):
        if config.parsed.get('NODE_IP') == config.parsed.get('NETWORK_NODE_IP'):
            subst = self.subst_dict()
            with util.RecordedOperation('update dhcp', self) as _:
                with lockutils.lock('sf_net_%s' % self.uuid, external=True, lock_path='/tmp/'):
                    d = dhcp.DHCP(self.uuid, subst['vx_veth_inner'])
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
            subst = self.subst_dict()
            with util.RecordedOperation('remove dhcp', self) as _:
                with lockutils.lock('sf_net_%s' % self.uuid, external=True, lock_path='/tmp/'):
                    d = dhcp.DHCP(self.uuid, subst['vx_veth_inner'])
                    d.remove_dhcpd()
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

        with util.RecordedOperation('discover mesh', self) as _:
            stdout, _ = processutils.execute(
                'bridge fdb show brport %(vx_interface)s' % self.subst_dict(),
                shell=True)

            for line in stdout.split('\n'):
                m = mesh_re.match(line)
                if m:
                    yield m.group(1)

    def ensure_mesh(self):
        with util.RecordedOperation('ensure mesh', self) as _:
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
