# Copyright 2020 Michael Still

from functools import partial
import ipaddress
import os
import psutil
import random
import re
import time
from uuid import uuid4

from oslo_concurrency import processutils

from shakenfist import baseobject
from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist.config import config
from shakenfist import db
from shakenfist import dhcp
from shakenfist import etcd
from shakenfist.exceptions import DeadNetwork, CongestedNetwork, IPManagerNotFound
from shakenfist import instance
from shakenfist.ipmanager import IPManager
from shakenfist import logutil
from shakenfist import networkinterface
from shakenfist.node import Node, Nodes, active_states_filter as active_nodes
from shakenfist.tasks import (
    DeployNetworkTask,
    HypervisorDestroyNetworkTask,
    UpdateDHCPNetworkTask,
    RemoveDHCPNetworkTask,
    RemoveNATNetworkTask)
from shakenfist.util import network as util_network
from shakenfist.util import process as util_process


LOG, _ = logutil.setup(__name__)


class Network(dbo):
    object_type = 'network'
    current_version = 2
    state_targets = {
        None: (dbo.STATE_INITIAL, ),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_CREATED: (dbo.STATE_DELETED, dbo.STATE_DELETE_WAIT, dbo.STATE_ERROR),
        dbo.STATE_DELETE_WAIT: (dbo.STATE_DELETED, dbo.STATE_ERROR),
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

        try:
            ipm = IPManager.from_db(self.uuid)
            self.__ipblock = ipm.network_address
            self.__router = ipm.get_address_at_index(1)
            self.__dhcp_start = ipm.get_address_at_index(2)
            self.__netmask = ipm.netmask
            self.__broadcast = ipm.broadcast_address
            self.__network_address = ipm.network_address
        except IPManagerNotFound:
            self.log.warning('IPManager missing for this network')

    @staticmethod
    def allocate_vxid(net_id):
        vxid = random.randint(1, 16777215)
        while not etcd.create('vxlan', None, vxid, {'network_uuid': net_id}):
            vxid = random.randint(1, 16777215)
        return vxid

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

        # Networks should immediately appear on the network node
        etcd.enqueue('networknode', DeployNetworkTask(uuid))

        # TODO(andy): Integrate metadata into each object type
        # Initialise metadata
        db.persist_metadata('network', uuid, {})

        return n

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

    @property
    def networkinterfaces(self):
        with self.get_lock_attr('networkinterfaces', 'Get interfaces'):
            nis_struct = self._db_get_attribute('networkinterfaces')

            if nis_struct.get('initialized', False):
                nis = nis_struct.get('networkinterfaces', [])

            else:
                nis = []
                for ni in networkinterface.NetworkInterfaces(
                    [baseobject.active_states_filter,
                     partial(networkinterface.network_filter, self)]):
                    nis.append(ni.uuid)
                self._db_set_attribute('networkinterfaces',
                                       {
                                           'networkinterfaces': nis,
                                           'initialized': True
                                       })

            return nis

    def add_networkinterface(self, ni):
        self._add_item_in_attribute_list('networkinterfaces', ni)

    def remove_networkinterface(self, ni):
        self._remove_item_in_attribute_list('networkinterfaces', ni)

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

        if self.provide_dhcp and config.NODE_IS_NETWORK_NODE:
            if not self.is_dnsmasq_running():
                return False

        return True

    def is_created(self):
        """Attempt to ensure network has been created successfully."""

        # The floating network always exists, and would fail the vx_bridge
        # test we apply to other networks.
        if self.uuid == 'floating':
            return True

        subst = self.subst_dict()
        if not util_network.check_for_interface(subst['vx_bridge'], up=True):
            self.log.warning('%s is not up', subst['vx_bridge'])
            return False

        return True

    def is_dead(self):
        """Check if the network is deleted or being deleted, or in error.

        First, update the object model to the ensure latest configuration. Some
        callers will wait on a lock before calling this function. In this case
        we definitely need to update the in-memory object model.
        """
        return self.state.value in (self.STATE_DELETED,
                                    self.STATE_DELETE_WAIT,
                                    self.STATE_ERROR)

    def _create_common(self):
        # The floating network does not have a vxlan mesh
        if self.uuid == 'floating':
            return

        subst = self.subst_dict()

        if not util_network.check_for_interface(subst['vx_interface']):
            util_network.create_interface(
                subst['vx_interface'], 'vxlan',
                'id %(vx_id)s dev %(mesh_interface)s dstport 0'
                % subst)
            util_process.execute(None, 'sysctl -w net.ipv4.conf.'
                                 '%(vx_interface)s.arp_notify=1' % subst)

        if not util_network.check_for_interface(subst['vx_bridge']):
            util_network.create_interface(subst['vx_bridge'], 'bridge', '')
            util_process.execute(None, 'ip link set %(vx_interface)s '
                                 'master %(vx_bridge)s' % subst)
            util_process.execute(
                None, 'ip link set %(vx_interface)s up' % subst)
            util_process.execute(
                None, 'ip link set %(vx_bridge)s up' % subst)
            util_process.execute(None, 'sysctl -w net.ipv4.conf.'
                                 '%(vx_bridge)s.arp_notify=1' % subst)
            util_process.execute(
                None, 'brctl setfd %(vx_bridge)s 0' % subst)
            util_process.execute(
                None, 'brctl stp %(vx_bridge)s off' % subst)
            util_process.execute(
                None, 'brctl setageing %(vx_bridge)s 0' % subst)

    def create_on_hypervisor(self):
        # The floating network does not have a vxlan mesh
        if self.uuid == 'floating':
            return

        with self.get_lock(op='create_on_hypervisor'):
            if self.is_dead():
                raise DeadNetwork('network=%s' % self)
            self._create_common()

    def create_on_network_node(self):
        # The floating network does not have a vxlan mesh
        if self.uuid == 'floating':
            return

        with self.get_lock(op='create_on_network_node'):
            if self.is_dead():
                raise DeadNetwork('network=%s' % self)

            self._create_common()

            subst = self.subst_dict()
            if not os.path.exists('/var/run/netns/%s' % self.uuid):
                util_process.execute(None, 'ip netns add %s' % self.uuid)

            if not util_network.check_for_interface(subst['vx_veth_outer']):
                util_network.create_interface(
                    subst['vx_veth_outer'], 'veth',
                    'peer name %(vx_veth_inner)s' % subst)
                util_process.execute(
                    None, 'ip link set %(vx_veth_inner)s netns %(netns)s' % subst)

                # Refer to bug 952 for more details here, but it turns out
                # that adding an interface to a bridge overwrites the MTU of
                # the bridge in an undesirable way. So we lookup the existing
                # MTU and then re-specify it here.
                subst['vx_bridge_mtu'] = util_network.get_interface_mtu(
                    subst['vx_bridge'])
                util_process.execute(
                    None,
                    'ip link set %(vx_veth_outer)s master %(vx_bridge)s '
                    'mtu %(vx_bridge_mtu)s' % subst)

                util_process.execute(
                    None, 'ip link set %(vx_veth_outer)s up' % subst)
                util_process.execute(
                    None, 'ip link set %(vx_veth_inner)s up' % subst,
                    namespace=self.uuid)
                util_process.execute(
                    None,
                    'ip addr add %(router)s/%(netmask)s '
                    'dev %(vx_veth_inner)s' % subst,
                    namespace=self.uuid)

            if not util_network.check_for_interface(subst['egress_veth_outer']):
                util_network.create_interface(
                    subst['egress_veth_outer'], 'veth',
                    'peer name %(egress_veth_inner)s' % subst)

                # Refer to bug 952 for more details here, but it turns out
                # that adding an interface to a bridge overwrites the MTU of
                # the bridge in an undesirable way. So we lookup the existing
                # MTU and then re-specify it here.
                subst['egress_bridge_mtu'] = util_network.get_interface_mtu(
                    subst['egress_bridge'])
                util_process.execute(
                    None,
                    'ip link set %(egress_veth_outer)s master %(egress_bridge)s '
                    'mtu %(egress_bridge_mtu)s' % subst)

                util_process.execute(
                    None, 'ip link set %(egress_veth_outer)s up' % subst)
                util_process.execute(
                    None, 'ip link set %(egress_veth_inner)s netns %(netns)s' % subst)

            if self.provide_nat:
                # We don't always need this lock, but acquiring it here means
                # we don't need to construct two identical ipmanagers one after
                # the other.
                try:
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
                except CongestedNetwork:
                    self.error('Unable to allocate floating gateway IP')

                addresses = util_network.get_interface_addresses(
                    subst['egress_veth_inner'], namespace=subst['netns'])
                if not subst['floating_gateway'] in list(addresses):
                    util_process.execute(
                        None,
                        'ip addr add %(floating_gateway)s/%(floating_netmask)s '
                        'dev %(egress_veth_inner)s' % subst,
                        namespace=self.uuid)
                    util_process.execute(
                        None, 'ip link set  %(egress_veth_inner)s up' % subst,
                        namespace=self.uuid)

                default_routes = util_network.get_default_routes(
                    subst['netns'])
                if default_routes != [subst['floating_router']]:
                    if default_routes:
                        for default_route in default_routes:
                            util_process.execute(
                                None, 'route del default gw %s' % default_route,
                                namespace=self.uuid)

                    util_process.execute(
                        None, 'route add default gw %(floating_router)s' % subst,
                        namespace=self.uuid)

                self.enable_nat()

        self.update_dhcp()

        # A final check to ensure we haven't raced with a delete
        if self.is_dead():
            raise DeadNetwork('network=%s' % self)
        self.state = self.STATE_CREATED

    def delete_on_hypervisor(self):
        with self.get_lock(op='Network delete'):
            subst = self.subst_dict()

            if util_network.check_for_interface(subst['vx_bridge']):
                util_process.execute(
                    None, 'ip link delete %(vx_bridge)s' % subst)

            if util_network.check_for_interface(subst['vx_interface']):
                util_process.execute(
                    None, 'ip link delete %(vx_interface)s' % subst)

    # This method should only ever be called when you already know you're on
    # the network node. Specifically it is called by a queue task that the
    # network node listens for.
    def delete_on_network_node(self):
        with self.get_lock(op='Network delete'):
            subst = self.subst_dict()

            if util_network.check_for_interface(subst['vx_veth_outer']):
                util_process.execute(
                    None, 'ip link delete %(vx_veth_outer)s' % subst)

            if util_network.check_for_interface(subst['egress_veth_outer']):
                util_process.execute(
                    None, 'ip link delete %(egress_veth_outer)s' % subst)

            if os.path.exists('/var/run/netns/%s' % self.uuid):
                util_process.execute(None, 'ip netns del %s' % self.uuid)

            if self.floating_gateway:
                with db.get_lock('ipmanager', None, 'floating', ttl=120,
                                 op='Network delete'):
                    ipm = IPManager.from_db('floating')
                    ipm.release(self.floating_gateway)
                    ipm.persist()
                    self.update_floating_gateway(None)

            self.state = self.STATE_DELETED

        # Ensure that all hypervisors remove this network. This is really
        # just catching strays, apart from on the network node where we
        # absolutely need to do this thing.
        for hyp in Nodes([active_nodes]):
            etcd.enqueue(hyp.uuid,
                         {'tasks': [
                             HypervisorDestroyNetworkTask(self.uuid)
                         ]})

        self.remove_dhcp()
        self.remove_nat()

        ipm = IPManager.from_db(self.uuid)
        ipm.delete()

    def hard_delete(self):
        etcd.delete('vxlan', None, self.vxid)
        etcd.delete('ipmanager', None, self.uuid)
        super(Network, self).hard_delete()

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

        if config.NODE_IS_NETWORK_NODE:
            subst = self.subst_dict()
            with self.get_lock(op='Network update DHCP'):
                d = dhcp.DHCP(self, subst['vx_veth_inner'])
                d.restart_dhcpd()
        else:
            etcd.enqueue('networknode', UpdateDHCPNetworkTask(self.uuid))

    def remove_dhcp(self):
        if config.NODE_IS_NETWORK_NODE:
            subst = self.subst_dict()
            with self.get_lock(op='Network remove DHCP'):
                d = dhcp.DHCP(self, subst['vx_veth_inner'])
                d.remove_dhcpd()
        else:
            etcd.enqueue('networknode', RemoveDHCPNetworkTask(self.uuid))

    def enable_nat(self):
        if not config.NODE_IS_NETWORK_NODE:
            return

        subst = self.subst_dict()
        if not util_network.nat_rules_for_ipblock(self.network_address):
            util_process.execute(
                None, 'echo 1 > /proc/sys/net/ipv4/ip_forward')
            util_process.execute(
                None,
                'iptables -A FORWARD -o %(egress_veth_inner)s '
                '-i %(vx_veth_inner)s -j ACCEPT' % subst,
                namespace=self.uuid)
            util_process.execute(
                None,
                'iptables -A FORWARD -i %(egress_veth_inner)s '
                '-o %(vx_veth_inner)s -j ACCEPT' % subst,
                namespace=self.uuid)
            util_process.execute(
                None,
                'iptables -t nat -A POSTROUTING -s %(ipblock)s/%(netmask)s '
                '-o %(egress_veth_inner)s -j MASQUERADE' % subst,
                namespace=self.uuid)

    def remove_nat(self):
        if config.NODE_IS_NETWORK_NODE:
            if self.floating_gateway:
                with db.get_lock('ipmanager', None, 'floating', ttl=120,
                                 op='Remove NAT'):
                    ipm = IPManager.from_db('floating')
                    ipm.release(self.floating_gateway)
                    ipm.persist()
                    self.update_floating_gateway(None)

        else:
            etcd.enqueue('networknode', RemoveNATNetworkTask(self.uuid))

    def discover_mesh(self):
        # The floating network does not have a vxlan mesh
        if self.uuid == 'floating':
            return

        mesh_re = re.compile(r'00:00:00:00:00:00 dst (.*) self permanent')

        try:
            stdout, _ = util_process.execute(
                None,
                'bridge fdb show brport %(vx_interface)s' % self.subst_dict(),
                suppress_command_logging=True)

            for line in stdout.split('\n'):
                m = mesh_re.match(line)
                if m:
                    yield m.group(1)

        except processutils.ProcessExecutionError as e:
            if time.time() - self.state.update_time > 10:
                self.log.warning('Mesh discovery failure: %s' % e)

    def ensure_mesh(self):
        # The floating network does not have a vxlan mesh
        if self.uuid == 'floating':
            return

        with self.get_lock(op='Network ensure mesh'):
            # Ensure network was not deleted whilst waiting for the lock.
            if self.is_dead():
                raise DeadNetwork('network=%s' % self)

            removed = []
            added = []

            instances = []
            for ni_uuid in self.networkinterfaces:
                ni = networkinterface.NetworkInterface.from_db(ni_uuid)
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
            # as an overlay cloud... Also, we don't include ourselves in the
            # mesh as that would cause duplicate packets to reflect back to us.
            # (see bug #859).
            node_ips = set()
            if config.NETWORK_NODE_IP != config.NODE_MESH_IP:
                # Always add Network node if it is not this node
                node_ips.add(config.NETWORK_NODE_IP)

            for fqdn in node_fqdns:
                n = Node.from_db(fqdn)
                if n and n.ip != config.NODE_MESH_IP:
                    node_ips.add(n.ip)

            discovered = list(self.discover_mesh())
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
                self.add_event2('remove mesh elements',
                                extra={'removed': removed})
            if added:
                self.add_event2('add mesh elements', extra={'added': added})

    def _add_mesh_element(self, n):
        subst = self.subst_dict()
        subst['node'] = n

        try:
            util_process.execute(None,
                                 'bridge fdb append to 00:00:00:00:00:00 '
                                 'dst %(node)s dev %(vx_interface)s' % subst)
            self.add_event2('added new mesh element', extra={'ip': n})
        except processutils.ProcessExecutionError as e:
            self.log.with_fields({
                'node': n,
                'error': e}).info('Failed to add mesh element')

    def _remove_mesh_element(self, n):
        subst = self.subst_dict()
        subst['node'] = n

        try:
            util_process.execute(None,
                                 'bridge fdb del to 00:00:00:00:00:00 dst %(node)s '
                                 'dev %(vx_interface)s' % subst)
            self.add_event2('removed excess mesh element', extra={'ip': n})
        except processutils.ProcessExecutionError as e:
            self.log.with_fields({
                'node': n,
                'error': e}).info('Failed to remove mesh element')

    # NOTE(mikal): this call only works on the network node, the API
    # server redirects there.
    def add_floating_ip(self, floating_address, inner_address):
        self.add_event2('adding floating ip %s -> %s'
                        % (floating_address, inner_address))
        subst = self.subst_dict()
        subst['floating_address'] = floating_address
        subst['floating_address_as_hex'] = '%08x' % int(
            ipaddress.IPv4Address(floating_address))
        subst['inner_address'] = inner_address

        util_network.create_interface(
            'flt-%(floating_address_as_hex)s-o' % subst, 'veth',
            'peer name flt-%(floating_address_as_hex)s-i' % subst)
        util_process.execute(
            None,  'ip link set flt-%(floating_address_as_hex)s-i netns %(netns)s' % subst)
        util_process.execute(
            None,
            'ip addr add %(floating_address)s/32 '
            'dev flt-%(floating_address_as_hex)s-i' % subst,
            namespace=self.uuid)
        util_process.execute(
            None,
            'iptables -t nat -A PREROUTING -d %(floating_address)s -j DNAT '
            '--to-destination %(inner_address)s' % subst,
            namespace=self.uuid)

    # NOTE(mikal): this call only works on the network node, the API
    # server redirects there.
    def remove_floating_ip(self, floating_address, inner_address):
        self.add_event2('removing floating ip %s -> %s'
                        % (floating_address, inner_address))
        subst = self.subst_dict()
        subst['floating_address'] = floating_address
        subst['floating_address_as_hex'] = '%08x' % int(
            ipaddress.IPv4Address(floating_address))
        subst['inner_address'] = inner_address

        if util_network.check_for_interface('flt-%(floating_address_as_hex)s-o' % subst):
            util_process.execute(None,
                                 'ip link del flt-%(floating_address_as_hex)s-o'
                                 % subst)


class Networks(dbo_iter):
    def __iter__(self):
        for _, n in etcd.get_all('network', None):
            if n['uuid'] == 'floating':
                continue
            out = self.apply_filters(Network(n))
            if out:
                yield out


# Convenience helpers
def networks_in_namespace(namespace):
    return Networks([partial(baseobject.namespace_filter, namespace)])
