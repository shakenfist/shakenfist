# Copyright 2020 Michael Still

from functools import partial
import ipaddress
import os
import psutil
import random
import re
from uuid import uuid4


from shakenfist import baseobject
from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist.config import config
from shakenfist import db
from shakenfist import dhcp
from shakenfist import etcd
from shakenfist.exceptions import DeadNetwork
from shakenfist import instance
from shakenfist.ipmanager import IPManager
from shakenfist import logutil
from shakenfist import networkinterface
from shakenfist.node import Node
from shakenfist.tasks import (
    DeployNetworkTask,
    UpdateDHCPNetworkTask,
    RemoveDHCPNetworkTask,
    RemoveNATNetworkTask)
from shakenfist import util


LOG, _ = logutil.setup(__name__)


class Network(dbo):
    object_type = 'network'
    current_version = 2
    state_targets = {
        None: (dbo.STATE_INITIAL, ),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_CREATED: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_ERROR: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_DELETED: (),
    }

    def __init__(self, static_values):
        super(Network, self).__init__(static_values.get('uuid'),
                                      static_values.get('version'))

        self.__name = static_values.get('name')
        self.__namespace = static_values.get('namespace')
        self.__netblock = static_values.get('netblock')
        self.__provide_dhcp = static_values.get('provide_dhcp')
        self.__provide_nat = static_values.get('provide_nat')
        self.__vxid = static_values.get('vxid')

        self.egress_nic = static_values.get(
            'egress_nic', config.NODE_EGRESS_NIC)
        self.mesh_nic = static_values.get(
            'mesh_nic', config.NODE_MESH_NIC)

        ipm = IPManager.from_db(self.uuid)
        self.__ipblock = ipm.network_address
        self.__router = ipm.get_address_at_index(1)
        self.__dhcp_start = ipm.get_address_at_index(2)
        self.__netmask = ipm.netmask
        self.__broadcast = ipm.broadcast_address
        self.__network_address = ipm.network_address

    @staticmethod
    def allocate_vxid(net_id):
        vxid = random.randint(1, 16777215)
        while not etcd.create('vxlan', None, vxid, {'network_uuid': net_id}):
            vxid = random.randint(1, 16777215)
        return vxid

    @staticmethod
    def deallocate_vxid(vxid):
        etcd.delete('vxlan', None, vxid)

    @classmethod
    def new(cls, name, namespace, netblock, provide_dhcp=False,
            provide_nat=False, uuid=None, vxid=None):

        if not uuid:
            # uuid should only be specified in testing
            uuid = str(uuid4())

        if not vxid:
            vxid = Network.allocate_vxid(uuid)

        # Pre-create the IPManager
        IPManager.new(uuid, netblock)

        Network._db_create(
            uuid,
            {
                'vxid': vxid,
                'name': name,
                'namespace': namespace,
                'netblock': netblock,
                'provide_dhcp': provide_dhcp,
                'provide_nat': provide_nat,
                'version': cls.current_version
            }
        )

        n = Network.from_db(uuid)
        n.state = Network.STATE_INITIAL
        n.add_event('db record creation', None)

        # Networks should immediately appear on the network node
        db.enqueue('networknode', DeployNetworkTask(uuid))
        n.add_event('deploy', 'enqueued')

        # TODO(andy): Integrate metadata into each object type
        # Initialise metadata
        db.persist_metadata('network', uuid, {})

        return n

    @staticmethod
    def from_db(uuid):
        if not uuid:
            return None

        # NOTE(mikal): we used to lock around this fetch from the database,
        # but I am hoping that moving state into an attribute means that's
        # not nessesary any more.
        static_values = Network._db_get(uuid)
        if not static_values:
            return None

        return Network(static_values)

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        n = {
            'uuid': self.uuid,
            'name': self.__name,
            'namespace': self.__namespace,
            'netblock': self.__netblock,
            'provide_dhcp': self.__provide_dhcp,
            'provide_nat': self.__provide_nat,
            'floating_gateway': self.floating_gateway,
            'state': self.state.value,
            'vxid': self.__vxid,
            'version': self.version
        }

        for attrname in ['routing']:
            d = self._db_get_attribute(attrname)
            for key in d:
                # We skip keys with no value
                if d[key] is None:
                    continue

                n[key] = d[key]

        return n

    # Static values
    @property
    def floating_gateway(self):
        return self._db_get_attribute('routing').get('floating_gateway')

    @property
    def routing(self):
        return self._db_get_attribute('routing')

    @property
    def name(self):
        return self.__name

    @property
    def namespace(self):
        return self.__namespace

    @property
    def netblock(self):
        return self.__netblock

    @property
    def provide_dhcp(self):
        return self.__provide_dhcp

    @property
    def provide_nat(self):
        return self.__provide_nat

    @property
    def vxid(self):
        return self.__vxid

    # Calculated values
    @property
    def ipblock(self):
        return self.__ipblock

    @property
    def router(self):
        return self.__router

    @property
    def dhcp_start(self):
        return self.__dhcp_start

    @property
    def netmask(self):
        return self.__netmask

    @property
    def broadcast(self):
        return self.__broadcast

    @property
    def network_address(self):
        return self.__network_address

    # TODO(andy) Create new class to avoid external direct access to DB
    @staticmethod
    def create_floating_network(netblock):
        fnet = Network.new(uuid='floating',
                           vxid=0,
                           netblock=netblock,
                           provide_dhcp=False,
                           provide_nat=False,
                           namespace=None,
                           name='floating')
        return fnet

    def update_floating_gateway(self, gateway):
        with self.get_lock_attr('routing', 'Update floating gateway'):
            routing = self.routing
            routing['floating_gateway'] = gateway
            self._db_set_attribute('routing', routing)

    def subst_dict(self):
        # NOTE(mikal): it should be noted that the maximum interface name length
        # on Linux is 15 user visible characters, we therefore use hex for vxids
        # where they appear in an interface name. Note that vx_id does not appear
        # in an interface name and is therefore in decimal (as required by) the
        # "ip" command.
        retval = {
            'vx_id': self.vxid,
            'vx_interface': 'vxlan-%06x' % self.vxid,
            'vx_bridge': 'br-vxlan-%06x' % self.vxid,
            'vx_veth_outer': 'veth-%06x-o' % self.vxid,
            'vx_veth_inner': 'veth-%06x-i' % self.vxid,

            'egress_bridge': 'egr-br-%s' % config.NODE_EGRESS_NIC,
            'egress_veth_outer': 'egr-%06x-o' % self.vxid,
            'egress_veth_inner': 'egr-%06x-i' % self.vxid,
            'mesh_interface': self.mesh_nic,

            'netns': self.uuid,
            'in_netns': 'ip netns exec %s' % self.uuid,

            'ipblock': self.ipblock,
            'netmask': self.netmask,
            'router': self.router,
            'broadcast': self.broadcast,
        }
        return retval

    def is_okay(self):
        """Check if network is created and running."""
        # TODO(andy):This will be built upon with further code re-design

        if not self.is_created():
            return False

        if self.provide_dhcp and util.is_network_node():
            if not self.is_dnsmasq_running():
                return False

        return True

    def is_created(self):
        """Attempt to ensure network has been created successfully."""

        subst = self.subst_dict()
        if not util.check_for_interface(subst['vx_bridge'], up=True):
            self.log.warning('%s is not up', subst['vx_bridge'])
            return False

        return True

    def is_dead(self):
        """Check if the network is deleted or being deleted, or in error.

        First, update the object model to the ensure latest configuration. Some
        callers will wait on a lock before calling this function. In this case
        we definitely need to update the in-memory object model.
        """
        return self.state.value in (self.STATE_DELETED, self.STATE_ERROR)

    def _create_common(self):
        subst = self.subst_dict()

        if not util.check_for_interface(subst['vx_interface']):
            with util.RecordedOperation('create vxlan interface', self):
                util.create_interface(
                    subst['vx_interface'], 'vxlan',
                    'id %(vx_id)s dev %(mesh_interface)s dstport 0'
                    % subst)
                util.execute(None, 'sysctl -w net.ipv4.conf.'
                                   '%(vx_interface)s.arp_notify=1' % subst)

        if not util.check_for_interface(subst['vx_bridge']):
            with util.RecordedOperation('create vxlan bridge', self):
                util.create_interface(subst['vx_bridge'], 'bridge', '')
                util.execute(None, 'ip link set %(vx_interface)s '
                                   'master %(vx_bridge)s' % subst)
                util.execute(None, 'ip link set %(vx_interface)s up' % subst)
                util.execute(None, 'ip link set %(vx_bridge)s up' % subst)
                util.execute(None, 'sysctl -w net.ipv4.conf.'
                                   '%(vx_bridge)s.arp_notify=1' % subst)
                util.execute(None, 'brctl setfd %(vx_bridge)s 0' % subst)
                util.execute(None, 'brctl stp %(vx_bridge)s off' % subst)
                util.execute(None, 'brctl setageing %(vx_bridge)s 0' % subst)

    def create_on_hypervisor(self):
        with self.get_lock(op='create_on_hypervisor'):
            if self.is_dead():
                raise DeadNetwork('network=%s' % self)
            self._create_common()

            # TODO(andy): Check with mikal: is this task required here?
            db.enqueue('networknode', DeployNetworkTask(self.uuid))
            self.add_event('deploy', 'enqueued')

    def create_on_network_node(self):
        with self.get_lock(op='create_on_network_node'):
            if self.is_dead():
                raise DeadNetwork('network=%s' % self)

            self._create_common()

            subst = self.subst_dict()
            if not os.path.exists('/var/run/netns/%(netns)s' % subst):
                with util.RecordedOperation('create netns', self):
                    util.execute(None, 'ip netns add %(netns)s' % subst)

            if not util.check_for_interface(subst['vx_veth_outer']):
                with util.RecordedOperation('create router veth', self):
                    util.create_interface(
                        subst['vx_veth_outer'], 'veth',
                        'peer name %(vx_veth_inner)s' % subst)
                    util.execute(
                        None,
                        'ip link set %(vx_veth_inner)s netns %(netns)s' % subst)
                    util.execute(
                        None,
                        'brctl addif %(vx_bridge)s %(vx_veth_outer)s' % subst)
                    util.execute(
                        None, 'ip link set %(vx_veth_outer)s up' % subst)
                    util.execute(
                        None,
                        '%(in_netns)s ip link set %(vx_veth_inner)s up' % subst)
                    util.execute(
                        None,
                        '%(in_netns)s ip addr add %(router)s/%(netmask)s '
                        'dev %(vx_veth_inner)s' % subst)

            if not util.check_for_interface(subst['egress_veth_outer']):
                with util.RecordedOperation('create egress veth', self):
                    util.create_interface(
                        subst['egress_veth_outer'], 'veth',
                        'peer name %(egress_veth_inner)s' % subst)
                    util.execute(None,
                                 'brctl addif %(egress_bridge)s '
                                 '%(egress_veth_outer)s' % subst)
                    util.execute(None,
                                 'ip link set %(egress_veth_outer)s up'
                                 % subst)
                    util.execute(None,
                                 'ip link set %(egress_veth_inner)s '
                                 'netns %(netns)s' % subst)

            if self.provide_nat:
                # We don't always need this lock, but acquiring it here means
                # we don't need to construct two identical ipmanagers one after
                # the other.
                with db.get_lock('ipmanager', None, 'floating', ttl=120,
                                 op='Network deploy NAT'):
                    ipm = IPManager.from_db('floating')
                    if not self.floating_gateway:
                        self.update_floating_gateway(
                            ipm.get_random_free_address(self.unique_label()))
                        ipm.persist()

                    subst['floating_router'] = ipm.get_address_at_index(1)
                    subst['floating_gateway'] = self.floating_gateway
                    subst['floating_netmask'] = ipm.netmask

                with util.RecordedOperation('enable virtual routing', self):
                    addresses = util.get_interface_addresses(
                        subst['netns'],
                        subst['egress_veth_inner'])
                    if not subst['floating_gateway'] in list(addresses):
                        util.execute(None,
                                     '%(in_netns)s ip addr add '
                                     '%(floating_gateway)s/%(floating_netmask)s '
                                     'dev %(egress_veth_inner)s' % subst)
                        util.execute(None,
                                     '%(in_netns)s ip link set '
                                     '%(egress_veth_inner)s up' % subst)

                    default_routes = util.get_default_routes(subst['netns'])
                    if default_routes != [subst['floating_router']]:
                        if default_routes:
                            for default_route in default_routes:
                                util.execute(None,
                                             '%s route del default gw %s'
                                             % (subst['in_netns'], default_route))

                        util.execute(None,
                                     '%(in_netns)s route add default '
                                     'gw %(floating_router)s' % subst)

                self.enable_nat()

        self.update_dhcp()
        self.state = self.STATE_CREATED

    def delete_on_hypervisor(self):
        with self.get_lock(op='Network delete'):
            subst = self.subst_dict()

            if util.check_for_interface(subst['vx_bridge']):
                with util.RecordedOperation('delete vxlan bridge', self):
                    util.execute(None, 'ip link delete %(vx_bridge)s' % subst)

            if util.check_for_interface(subst['vx_interface']):
                with util.RecordedOperation('delete vxlan interface', self):
                    util.execute(
                        None, 'ip link delete %(vx_interface)s' % subst)

    def delete_on_network_node(self):
        with self.get_lock(op='Network delete'):
            subst = self.subst_dict()

            if util.is_network_node():
                if util.check_for_interface(subst['vx_veth_outer']):
                    with util.RecordedOperation('delete router veth', self):
                        util.execute(
                            None, 'ip link delete %(vx_veth_outer)s' % subst)

                if util.check_for_interface(subst['egress_veth_outer']):
                    with util.RecordedOperation('delete egress veth', self):
                        util.execute(
                            None,
                            'ip link delete %(egress_veth_outer)s' % subst)

                if os.path.exists('/var/run/netns/%(netns)s' % subst):
                    with util.RecordedOperation('delete netns', self):
                        util.execute(None, 'ip netns del %(netns)s' % subst)

                if self.floating_gateway:
                    with db.get_lock('ipmanager', None, 'floating', ttl=120,
                                     op='Network delete'):
                        ipm = IPManager.from_db('floating')
                        ipm.release(self.floating_gateway)
                        ipm.persist()
                        self.update_floating_gateway(None)

            self.state = self.STATE_DELETED

        self.remove_dhcp()
        self.remove_nat()

        ipm = IPManager.from_db(self.uuid)
        ipm.delete()

    def hard_delete(self):
        etcd.delete('network', None, self.uuid)
        etcd.delete('vxid', None, self.vxid)
        etcd.delete_all('attribute/network', self.uuid)
        etcd.delete_all('event/network', self.uuid)
        db.delete_metadata('network', self.uuid)

    def is_dnsmasq_running(self):
        """Determine if dnsmasq process is running for this network"""
        subst = self.subst_dict()
        d = dhcp.DHCP(self, subst['vx_veth_inner'])
        pid = d.get_pid()
        if pid and psutil.pid_exists(pid):
            return True

        self.log.warning('dnsmasq is not running')
        return False

    def update_dhcp(self):
        if not self.provide_dhcp:
            return

        if util.is_network_node():
            subst = self.subst_dict()
            with util.RecordedOperation('update dhcp', self):
                with self.get_lock(op='Network update DHCP'):
                    d = dhcp.DHCP(self, subst['vx_veth_inner'])
                    d.restart_dhcpd()
        else:
            db.enqueue('networknode', UpdateDHCPNetworkTask(self.uuid))
            self.add_event('update dhcp', 'enqueued')

    def remove_dhcp(self):
        if util.is_network_node():
            subst = self.subst_dict()
            with util.RecordedOperation('remove dhcp', self):
                with self.get_lock(op='Network remove DHCP'):
                    d = dhcp.DHCP(self, subst['vx_veth_inner'])
                    d.remove_dhcpd()
        else:
            db.enqueue('networknode', RemoveDHCPNetworkTask(self.uuid))
            self.add_event('remove dhcp', 'enqueued')

    def enable_nat(self):
        if not util.is_network_node():
            return

        subst = self.subst_dict()
        if not util.nat_rules_for_ipblock(self.network_address):
            with util.RecordedOperation('enable nat', self):
                util.execute(None,
                             'echo 1 > /proc/sys/net/ipv4/ip_forward')
                util.execute(None,
                             '%(in_netns)s iptables -A FORWARD '
                             '-o %(egress_veth_inner)s '
                             '-i %(vx_veth_inner)s -j ACCEPT' % subst)
                util.execute(None,
                             '%(in_netns)s iptables -A FORWARD '
                             '-i %(egress_veth_inner)s '
                             '-o %(vx_veth_inner)s -j ACCEPT' % subst)
                util.execute(None,
                             '%(in_netns)s iptables -t nat -A POSTROUTING '
                             '-s %(ipblock)s/%(netmask)s '
                             '-o %(egress_veth_inner)s '
                             '-j MASQUERADE' % subst)

    def remove_nat(self):
        if util.is_network_node():
            if self.floating_gateway:
                with db.get_lock('ipmanager', None, 'floating', ttl=120,
                                 op='Remove NAT'):
                    ipm = IPManager.from_db('floating')
                    ipm.release(self.floating_gateway)
                    ipm.persist()
                    self.update_floating_gateway(None)

        else:
            db.enqueue('networknode', RemoveNATNetworkTask(self.uuid))
            self.add_event('remove dhcp', 'enqueued')

    def discover_mesh(self):
        mesh_re = re.compile(r'00:00:00:00:00:00 dst (.*) self permanent')

        stdout, _ = util.execute(
            None, 'bridge fdb show brport %(vx_interface)s' % self.subst_dict())

        for line in stdout.split('\n'):
            m = mesh_re.match(line)
            if m:
                yield m.group(1)

    def ensure_mesh(self):
        with self.get_lock(op='Network ensure mesh'):
            # Ensure network was not deleted whilst waiting for the lock.
            if self.is_dead():
                raise DeadNetwork('network=%s' % self)

            removed = []
            added = []

            instances = []
            for ni in networkinterface.interfaces_for_network(self):
                if ni.instance_uuid not in instances:
                    instances.append(ni.instance_uuid)

            node_fqdns = []
            for inst_uuid in instances:
                inst = instance.Instance.from_db(inst_uuid)
                placement = inst.placement
                if not placement:
                    continue
                if not placement.get('node'):
                    continue

                if not placement.get('node') in node_fqdns:
                    node_fqdns.append(placement.get('node'))

            # NOTE(mikal): why not use DNS here? Well, DNS might be outside
            # the control of the deployer if we're running in a public cloud
            # as an overlay cloud...
            node_ips = set([config.NETWORK_NODE_IP])
            for fqdn in node_fqdns:
                n = Node.from_db(fqdn)
                if n:
                    node_ips.add(n.ip)

            discovered = list(self.discover_mesh())
            self.log.with_fields(
                {
                    'discovered': discovered,
                    'node_ips': node_ips
                }).debug('Discovered mesh elements')

            for n in discovered:
                if n in node_ips:
                    node_ips.remove(n)
                else:
                    self._remove_mesh_element(n)
                    removed.append(n)

            for n in node_ips:
                self._add_mesh_element(n)
                added.append(n)

            if removed:
                self.add_event('remove mesh elements', ' '.join(removed))
            if added:
                self.add_event('add mesh elements', ' '.join(added))

    def _add_mesh_element(self, n):
        self.log.info('Adding new mesh element %s', n)
        subst = self.subst_dict()
        subst['node'] = n
        util.execute(None,
                     'bridge fdb append to 00:00:00:00:00:00 '
                     'dst %(node)s dev %(vx_interface)s' % subst)

    def _remove_mesh_element(self, n):
        self.log.info('Removing excess mesh element %s', n)
        subst = self.subst_dict()
        subst['node'] = n
        util.execute(None,
                     'bridge fdb del to 00:00:00:00:00:00 dst %(node)s '
                     'dev %(vx_interface)s' % subst)

    # NOTE(mikal): this call only works on the network node, the API
    # server redirects there.
    def add_floating_ip(self, floating_address, inner_address):
        self.log.info('Adding floating ip %s -> %s',
                      floating_address, inner_address)
        subst = self.subst_dict()
        subst['floating_address'] = floating_address
        subst['floating_address_as_hex'] = '%08x' % int(
            ipaddress.IPv4Address(floating_address))
        subst['inner_address'] = inner_address

        util.execute(None,
                     'ip link add flt-%(floating_address_as_hex)s-o type veth '
                     'peer name flt-%(floating_address_as_hex)s-i'
                     % subst)
        util.execute(None,
                     'ip link set flt-%(floating_address_as_hex)s-i netns %(netns)s'
                     % subst)
        util.execute(None,
                     '%(in_netns)s ip addr add %(floating_address)s/32 '
                     'dev flt-%(floating_address_as_hex)s-i'
                     % subst)
        util.execute(None,
                     '%(in_netns)s iptables -t nat -A PREROUTING '
                     '-d %(floating_address)s -j DNAT '
                     '--to-destination %(inner_address)s'
                     % subst)

    # NOTE(mikal): this call only works on the network node, the API
    # server redirects there.
    def remove_floating_ip(self, floating_address, inner_address):
        self.log.info('Removing floating ip %s -> %s',
                      floating_address, inner_address)
        subst = self.subst_dict()
        subst['floating_address'] = floating_address
        subst['floating_address_as_hex'] = '%08x' % int(
            ipaddress.IPv4Address(floating_address))
        subst['inner_address'] = inner_address

        if util.check_for_interface('flt-%(floating_address_as_hex)s-o' % subst):
            util.execute(None,
                         'ip link del flt-%(floating_address_as_hex)s-o'
                         % subst)


class Networks(dbo_iter):
    def __iter__(self):
        for _, n in etcd.get_all('network', None):
            if n['uuid'] == 'floating':
                continue

            n = Network.from_db(n['uuid'])
            if not n:
                continue

            out = self.apply_filters(n)
            if out:
                yield out


# Convenience helpers

def inactive_networks():
    return Networks([
                    baseobject.inactive_states_filter,
                    partial(baseobject.state_age_filter, config.CLEANER_DELAY)])


def networks_in_namespace(namespace):
    return Networks([partial(baseobject.namespace_filter, namespace)])
