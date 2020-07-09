# Copyright 2020 Michael Still

import json
import logging
from logging import handlers as logging_handlers
import os
import re
import requests

from oslo_concurrency import processutils


from shakenfist import config
from shakenfist import db
from shakenfist import dhcp
from shakenfist import db
from shakenfist import util


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)
LOG.addHandler(logging_handlers.SysLogHandler(address='/dev/log'))


def from_db(uuid):
    dbnet = db.get_network(uuid)
    if not dbnet:
        return None

    return Network(uuid=dbnet['uuid'],
                   vxlan_id=dbnet['vxid'],
                   provide_dhcp=dbnet['provide_dhcp'],
                   provide_nat=dbnet['provide_nat'],
                   ipblock=dbnet['netblock'],
                   physical_nic=config.parsed.get('NODE_EGRESS_NIC'),
                   floating_gateway=dbnet['floating_gateway'],
                   namespace=dbnet['namespace'])


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

        with db.get_lock('sf/ipmanager/%s' % self.uuid, ttl=120) as _:
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

    def get_describing_tuple(self):
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

    def create(self):
        subst = self.subst_dict()

        with db.get_lock('sf/net/%s' % self.uuid, ttl=120) as _:
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
            if not os.path.exists('/var/run/netns/%(netns)s' % subst):
                with util.RecordedOperation('create netns', self) as _:
                    processutils.execute(
                        'ip netns add %(netns)s' % subst, shell=True)

            if not util.check_for_interface(subst['vx_veth_outer']):
                with util.RecordedOperation('create router veth', self) as _:
                    processutils.execute(
                        'ip link add %(vx_veth_outer)s type veth peer name %(vx_veth_inner)s' % subst,
                        shell=True)
                    processutils.execute(
                        'ip link set %(vx_veth_inner)s netns %(netns)s' % subst, shell=True)
                    processutils.execute(
                        'brctl addif %(vx_bridge)s %(vx_veth_outer)s' % subst, shell=True)
                    processutils.execute(
                        'ip link set %(vx_veth_outer)s up' % subst, shell=True)
                    processutils.execute(
                        '%(in_netns)s ip link set %(vx_veth_inner)s up' % subst, shell=True)
                    processutils.execute(
                        '%(in_netns)s ip addr add %(router)s/%(netmask)s dev %(vx_veth_inner)s' % subst,
                        shell=True)

            if not util.check_for_interface(subst['physical_veth_outer']):
                with util.RecordedOperation('create physical veth', self) as _:
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
                        'ip link set %(physical_veth_inner)s netns %(netns)s' % subst,
                        shell=True)

            self.deploy_nat()
            self.update_dhcp()
        else:
            admin_token = util.get_api_token(
                'http://%s:%d' % (config.parsed.get('NETWORK_NODE_IP'),
                                  config.parsed.get('API_PORT')),
                namespace='system')
            requests.request(
                'put',
                ('http://%s:%d/deploy_network_node'
                 % (config.parsed.get('NETWORK_NODE_IP'),
                    config.parsed.get('API_PORT'))),
                data=json.dumps({'uuid': self.uuid}),
                headers={'Authorization': admin_token,
                         'User-Agent': util.get_user_agent()})

    def deploy_nat(self):
        if not self.provide_nat:
            return

        subst = self.subst_dict()
        if not self.floating_gateway:
            with db.get_lock('sf/ipmanager/floating', ttl=120) as _:
                ipm = db.get_ipmanager('floating')
                self.floating_gateway = ipm.get_random_free_address()
                db.persist_ipmanager('floating', ipm.save())
                self.persist_floating_gateway()

        # No lock because no data changing
        ipm = db.get_ipmanager('floating')
        subst['floating_router'] = ipm.get_address_at_index(1)
        subst['floating_gateway'] = self.floating_gateway
        subst['floating_netmask'] = ipm.netmask

        with db.get_lock('sf/net/%s' % self.uuid, ttl=120) as _:
            if not subst['floating_gateway'] in list(util.get_interface_addresses(
                    subst['netns'], subst['physical_veth_inner'])):
                with util.RecordedOperation('enable virtual routing', self) as _:
                    processutils.execute(
                        '%(in_netns)s ip addr add %(floating_gateway)s/%(floating_netmask)s '
                        'dev %(physical_veth_inner)s' % subst,
                        shell=True)
                    processutils.execute(
                        '%(in_netns)s ip link set %(physical_veth_inner)s up' % subst, shell=True)
                    processutils.execute(
                        '%(in_netns)s route add default gw %(floating_router)s' % subst,
                        shell=True)

            if not util.nat_rules_for_ipblock(self.network_address):
                with util.RecordedOperation('enable nat', self) as _:
                    processutils.execute(
                        'echo 1 > /proc/sys/net/ipv4/ip_forward', shell=True)
                    processutils.execute(
                        '%(in_netns)s iptables -A FORWARD -o %(physical_veth_inner)s '
                        '-i %(vx_veth_inner)s -j ACCEPT' % subst,
                        shell=True)
                    processutils.execute(
                        '%(in_netns)s iptables -A FORWARD -i %(physical_veth_inner)s '
                        '-o %(vx_veth_inner)s -j ACCEPT' % subst,
                        shell=True)
                    processutils.execute(
                        '%(in_netns)s iptables -t nat -A POSTROUTING -s %(ipblock)s/%(netmask)s '
                        '-o %(physical_veth_inner)s -j MASQUERADE' % subst,
                        shell=True
                    )

    def delete(self):
        subst = self.subst_dict()

        # Cleanup local node
        with db.get_lock('sf/net/%s' % self.uuid, ttl=120) as _:
            if util.check_for_interface(subst['vx_bridge']):
                with util.RecordedOperation('delete vxlan bridge', self) as _:
                    processutils.execute('ip link delete %(vx_bridge)s' % subst,
                                         shell=True)

            if util.check_for_interface(subst['vx_interface']):
                with util.RecordedOperation('delete vxlan interface', self) as _:
                    processutils.execute('ip link delete %(vx_interface)s' % subst,
                                         shell=True)

            # If this is the network node do additional cleanup
            if config.parsed.get('NODE_IP') == config.parsed.get('NETWORK_NODE_IP'):
                if util.check_for_interface(subst['vx_veth_outer']):
                    with util.RecordedOperation('delete router veth', self) as _:
                        processutils.execute('ip link delete %(vx_veth_outer)s' % subst,
                                             shell=True)

                if util.check_for_interface(subst['physical_veth_outer']):
                    with util.RecordedOperation('delete physical veth', self) as _:
                        processutils.execute('ip link delete %(physical_veth_outer)s' % subst,
                                             shell=True)

                if os.path.exists('/var/run/netns/%(netns)s' % subst):
                    with util.RecordedOperation('delete netns', self) as _:
                        processutils.execute('ip netns del %(netns)s' % subst,
                                             shell=True)

                if self.floating_gateway:
                    with db.get_lock('sf/ipmanager/floating', ttl=120) as _:
                        ipm = db.get_ipmanager('floating')
                        ipm.release(self.floating_gateway)
                        db.persist_ipmanager('floating', ipm.save())

    def update_dhcp(self):
        if config.parsed.get('NODE_IP') == config.parsed.get('NETWORK_NODE_IP'):
            self.ensure_mesh()
            subst = self.subst_dict()
            with util.RecordedOperation('update dhcp', self) as _:
                with db.get_lock('sf/net/%s' % self.uuid, ttl=120) as _:
                    d = dhcp.DHCP(self.uuid, subst['vx_veth_inner'])
                    d.restart_dhcpd()
        else:
            admin_token = util.get_api_token(
                'http://%s:%d' % (config.parsed.get('NETWORK_NODE_IP'),
                                  config.parsed.get('API_PORT')),
                namespace='system')
            requests.request(
                'put',
                ('http://%s:%d/update_dhcp'
                 % (config.parsed.get('NETWORK_NODE_IP'),
                    config.parsed.get('API_PORT'))),
                data=json.dumps({'uuid': self.uuid}),
                headers={'Authorization': admin_token,
                         'User-Agent': util.get_user_agent()})

    def remove_dhcp(self):
        if config.parsed.get('NODE_IP') == config.parsed.get('NETWORK_NODE_IP'):
            subst = self.subst_dict()
            with util.RecordedOperation('remove dhcp', self) as _:
                with db.get_lock('sf/net/%s' % self.uuid, ttl=120) as _:
                    d = dhcp.DHCP(self.uuid, subst['vx_veth_inner'])
                    d.remove_dhcpd()
        else:
            admin_token = util.get_api_token(
                'http://%s:%d' % (config.parsed.get('NETWORK_NODE_IP'),
                                  config.parsed.get('API_PORT')),
                namespace='system')
            requests.request(
                'put',
                ('http://%s:%d/remove_dhcp'
                 % (config.parsed.get('NETWORK_NODE_IP'),
                    config.parsed.get('API_PORT'))),
                data=json.dumps({'uuid': self.uuid}),
                headers={'Authorization': admin_token,
                         'User-Agent': util.get_user_agent()})

    def discover_mesh(self):
        mesh_re = re.compile(r'00:00:00:00:00:00 dst (.*) self permanent')

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
                ip = db.get_node(fqdn)['ip']
                if ip not in node_ips:
                    node_ips.append(ip)

            discovered = list(self.discover_mesh())
            LOG.debug('%s: Discovered mesh elements %s' % (self, discovered))
            for node in discovered:
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

    def add_floating_ip(self, floating_address, inner_address):
        LOG.info('%s: Adding floating ip %s -> %s'
                 % (self, floating_address, inner_address))
        subst = self.subst_dict()
        subst['floating_address'] = floating_address
        subst['inner_address'] = inner_address

        processutils.execute(
            'ip addr add %(floating_address)s/%(netmask)s '
            'dev %(physical_veth_outer)s' % subst,
            shell=True)
        processutils.execute(
            '%(in_netns)s iptables -t nat -A PREROUTING '
            '-d %(floating_address)s -j DNAT --to-destination %(inner_address)s' % subst,
            shell=True)

    def remove_floating_ip(self, floating_address, inner_address):
        LOG.info('%s: Removing floating ip %s -> %s'
                 % (self, floating_address, inner_address))
        subst = self.subst_dict()
        subst['floating_address'] = floating_address
        subst['inner_address'] = inner_address

        processutils.execute(
            'ip addr del %(floating_address)s/%(netmask)s '
            'dev %(physical_veth_outer)s' % subst,
            shell=True)
        processutils.execute(
            '%(in_netns)s iptables -t nat -D PREROUTING '
            '-d %(floating_address)s -j DNAT --to-destination %(inner_address)s' % subst,
            shell=True)
