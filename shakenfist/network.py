# Copyright 2020 Michael Still

from functools import partial
import ipaddress
import os
import random
import re
from shakenfist_utilities import logs
import time
from uuid import uuid4

from oslo_concurrency import processutils

from shakenfist import baseobject
from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT, EVENT_TYPE_MUTATE
from shakenfist.managed_executables import dnsmasq
from shakenfist import etcd
from shakenfist.exceptions import DeadNetwork, CongestedNetwork, IPManagerMissing
from shakenfist import instance
from shakenfist import ipam
from shakenfist import networkinterface
from shakenfist.node import Node, Nodes
from shakenfist.tasks import (
    DeployNetworkTask,
    HypervisorDestroyNetworkTask,
    UpdateDnsMasqNetworkTask,
    RemoveDnsMasqNetworkTask,
    RemoveDHCPLeaseNetworkTask,
    RemoveNATNetworkTask)
from shakenfist.util import network as util_network
from shakenfist.util import process as util_process


LOG, _ = logs.setup(__name__)


class Network(dbo):
    object_type = 'network'
    initial_version = 2
    current_version = 5

    # docs/developer_guide/state_machine.md has a description of these states.
    state_targets = {
        None: (dbo.STATE_INITIAL, ),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_CREATED: (dbo.STATE_DELETED, dbo.STATE_DELETE_WAIT, dbo.STATE_ERROR),
        dbo.STATE_DELETE_WAIT: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_ERROR: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_DELETED: (),
    }

    def __init__(self, static_values):
        self.upgrade(static_values)

        super().__init__(static_values.get('uuid'), static_values.get('version'))

        self.__ipam = ipam.IPAM.from_db(
            static_values['uuid'], suppress_failure_audit=True)
        if not self.__ipam:
            in_memory_only = False
            if self.state.value == dbo.STATE_DELETED:
                in_memory_only = True

            self.__ipam = ipam.IPAM.new(
                static_values['uuid'], static_values['namespace'],
                static_values['uuid'], static_values.get('netblock'),
                in_memory_only=in_memory_only)

        self.__name = static_values.get('name')
        self.__namespace = static_values.get('namespace')
        self.__netblock = static_values.get('netblock')
        self.__provide_dhcp = static_values.get('provide_dhcp')
        self.__provide_nat = static_values.get('provide_nat')
        self.__provide_dns = static_values.get('provide_dns', False)
        self.__vxid = static_values.get('vxid')

        self.egress_nic = static_values.get(
            'egress_nic', config.NODE_EGRESS_NIC)
        self.mesh_nic = static_values.get(
            'mesh_nic', config.NODE_MESH_NIC)

        self.__ipblock = self.ipam.network_address
        self.__router = self.ipam.get_address_at_index(1)
        self.__dhcp_start = self.ipam.get_address_at_index(2)
        self.__netmask = self.ipam.netmask
        self.__broadcast = self.ipam.broadcast_address
        self.__network_address = self.ipam.network_address

    @classmethod
    def _upgrade_step_2_to_3(cls, static_values):
        cls._upgrade_metadata_to_attribute(static_values['uuid'])

    @classmethod
    def _upgrade_step_3_to_4(cls, static_values):
        nis = []
        for ni in networkinterface.NetworkInterfaces(
                [partial(networkinterface.network_uuid_filter, static_values['uuid'])],
                prefilter='active'):
            nis.append(ni.uuid)
        etcd.put('attribute/network', static_values['uuid'], 'networkinterfaces',
                 {
                     'networkinterfaces': nis,
                     'initialized': True
                 })

    @classmethod
    def _upgrade_step_4_to_5(cls, static_values):
        static_values['provide_dns'] = False

    @staticmethod
    def allocate_vxid(net_id):
        reservation = {
            'network_uuid': net_id,
            'when': time.time()
            }

        vxid = random.randint(1, 16777215)
        while not etcd.create('vxlan', None, vxid, reservation):
            vxid = random.randint(1, 16777215)
        return vxid

    @classmethod
    def new(cls, name, namespace, netblock, provide_dhcp=False,
            provide_nat=False, network_uuid=None, vxid=None,
            provide_dns=False):

        if not network_uuid:
            # uuid should only be specified in testing
            network_uuid = str(uuid4())

        if not vxid:
            vxid = Network.allocate_vxid(network_uuid)

        # Pre-create the IPAM
        ipam.IPAM.new(network_uuid, namespace, network_uuid, netblock)

        Network._db_create(
            network_uuid,
            {
                'vxid': vxid,
                'name': name,
                'namespace': namespace,
                'netblock': netblock,
                'provide_dhcp': provide_dhcp,
                'provide_nat': provide_nat,
                'provide_dns': provide_dns,
                'version': cls.current_version
            }
        )

        n = Network.from_db(network_uuid)
        n.state = Network.STATE_INITIAL

        # Networks should immediately appear on the network node
        etcd.enqueue('networknode', DeployNetworkTask(network_uuid))

        return n

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        n = self._external_view()
        n.update({
            'name': self.__name,
            'namespace': self.__namespace,
            'netblock': self.__netblock,
            'provide_dhcp': self.__provide_dhcp,
            'provide_nat': self.__provide_nat,
            'provide_dns': self.__provide_dns,
            'floating_gateway': self.floating_gateway,
            'vxlan_id': self.__vxid
        })

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
    def ipam(self):
        return self.__ipam

    @property
    def floating_gateway(self):
        fg = self._db_get_attribute('routing', {'floating_gateway': None})
        return fg['floating_gateway']

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
    def provide_dns(self):
        return self.__provide_dns

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
        nis = self._db_get_attribute('networkinterfaces', {})
        return nis.get('networkinterfaces', [])

    def add_networkinterface(self, ni):
        self._add_item_in_attribute_list('networkinterfaces', ni.uuid)

    def remove_networkinterface(self, ni):
        if ni.ipv4:
            self.remove_dhcp_lease(ni.ipv4, ni.macaddr)
        self._remove_item_in_attribute_list('networkinterfaces', ni.uuid)

    def update_floating_gateway(self, gateway):
        with self.get_lock_attr('routing', 'Update floating gateway'):
            routing = self.routing
            if routing.get('floating_gateway') and gateway:
                self.log.with_fields({
                    'old_gateway': routing['floating_gateway'],
                    'new_gateway': gateway}).error('Clobbering previous floating gateway')
            routing['floating_gateway'] = gateway
            self._db_set_attribute('routing', routing)

    @property
    def _vx_veth_inner(self):
        return 'veth-%06x-i' % self.vxid

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
            'vx_veth_inner': self._vx_veth_inner,

            'egress_bridge': 'egr-br-%s' % config.NODE_EGRESS_NIC,
            'egress_veth_outer': 'egr-%06x-o' % self.vxid,
            'egress_veth_inner': 'egr-%06x-i' % self.vxid,
            'mesh_interface': self.mesh_nic,

            'netns': self.uuid,

            'ipblock': self.ipblock,
            'netmask': self.netmask,
            'router': self.router,
            'broadcast': self.broadcast,

            'dhcp_start': self.dhcp_start,
            'provide_nat': self.provide_nat,
        }
        return retval

    def is_okay(self):
        """Check if network is created and running."""
        # TODO(andy):This will be built upon with further code re-design

        if not self.is_created():
            return False

        if not config.NODE_IS_NETWORK_NODE:
            return True

        if self.provide_dhcp or self.provide_dns:
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

        self.add_event(EVENT_TYPE_AUDIT, 'creating network on hypervisor')

        with self.get_lock(op='create_on_hypervisor', global_scope=False):
            if self.is_dead():
                raise DeadNetwork('network=%s' % self)
            self._create_common()

    def create_on_network_node(self):
        # The floating network does not have a vxlan mesh
        if self.uuid == 'floating':
            return

        if self.state.value == dbo.STATE_DELETED:
            self.add_event(
                EVENT_TYPE_AUDIT, 'refusing to create deleted network on network node')
            return
        self.add_event(EVENT_TYPE_AUDIT, 'creating network on network node')

        with self.get_lock(op='create_on_network_node', global_scope=False):
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
                util_network.add_address_to_interface(
                    self.uuid, subst['router'], subst['netmask'],
                    subst['vx_veth_inner'])

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
                    fn = floating_network()
                    if not self.floating_gateway:
                        self.update_floating_gateway(
                            fn.ipam.reserve_random_free_address(
                                self.unique_label(), ipam.RESERVATION_TYPE_GATEWAY, ''))

                    subst['floating_router'] = fn.ipam.get_address_at_index(1)
                    subst['floating_gateway'] = self.floating_gateway
                    subst['floating_netmask'] = fn.netmask
                except CongestedNetwork:
                    self.error('Unable to allocate floating gateway IP')

                addresses = list(util_network.get_interface_addresses(
                    subst['egress_veth_inner'], namespace=subst['netns']))
                self.log.with_fields({
                    'addresses': addresses,
                    'current_address': subst['floating_gateway']}).debug(
                        'Egress veth has these addresses')
                if not subst['floating_gateway'] in list(addresses):
                    util_network.add_address_to_interface(
                        self.uuid, subst['floating_gateway'], subst['floating_netmask'],
                        subst['egress_veth_inner'])

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

        self.update_dnsmasq()

        # A final check to ensure we haven't raced with a delete
        if self.is_dead():
            raise DeadNetwork('network=%s' % self)
        self.state = self.STATE_CREATED

    def delete_on_hypervisor(self):
        with self.get_lock(op='Network delete', global_scope=False):
            subst = self.subst_dict()

            if util_network.check_for_interface(subst['vx_bridge']):
                util_process.execute(
                    None, 'ip link delete %(vx_bridge)s' % subst)

            if util_network.check_for_interface(subst['vx_interface']):
                util_process.execute(
                    None, 'ip link delete %(vx_interface)s' % subst)

            if self.floating_gateway:
                fn = floating_network()
                fn.ipam.release(self.floating_gateway)
                self.update_floating_gateway(None)

    # This method should only ever be called when you already know you're on
    # the network node. Specifically it is called by a queue task that the
    # network node listens for.
    def delete_on_network_node(self):
        with self.get_lock(op='Network delete', global_scope=False):
            subst = self.subst_dict()

            if util_network.check_for_interface(subst['vx_veth_outer']):
                util_process.execute(
                    None, 'ip link delete %(vx_veth_outer)s' % subst)

            if util_network.check_for_interface(subst['egress_veth_outer']):
                util_process.execute(
                    None, 'ip link delete %(egress_veth_outer)s' % subst)

            if os.path.exists('/var/run/netns/%s' % self.uuid):
                util_process.execute(None, 'ip netns del %s' % self.uuid)

            self.ipam.state = self.ipam.STATE_DELETED
            self.state = self.STATE_DELETED

        # Ensure that all hypervisors remove this network. This is really
        # just catching strays, apart from on the network node where we
        # absolutely need to do this thing.
        for n in Nodes([], prefilter='active'):
            etcd.enqueue(n.uuid,
                         {'tasks': [
                             HypervisorDestroyNetworkTask(self.uuid)
                         ]})

        self.remove_dnsmasq()
        self.remove_nat()

    def hard_delete(self):
        etcd.delete('vxlan', None, self.vxid)
        super().hard_delete()

    def _get_dnsmasq_object(self):
        return dnsmasq.DnsMasq.new(self, provide_dhcp=self.provide_dhcp,
                                   provide_nat=self.provide_nat,
                                   provide_dns=self.provide_dns)

    def is_dnsmasq_running(self):
        """Determine if dnsmasq process is running for this network"""
        d = self._get_dnsmasq_object()
        return d.is_running()

    def remove_dhcp_lease(self, ipv4, macaddr):
        if not self.provide_dhcp and not self.provide_dns:
            return

        if config.NODE_IS_NETWORK_NODE:
            with self.get_lock(op='Network update DnsMasq', global_scope=False):
                d = self._get_dnsmasq_object()
                d.remove_lease(ipv4, macaddr)
        else:
            etcd.enqueue('networknode',
                         RemoveDHCPLeaseNetworkTask(self.uuid, ipv4, macaddr))

    def update_dnsmasq(self):
        if not self.provide_dhcp and not self.provide_dns:
            return

        if config.NODE_IS_NETWORK_NODE:
            with self.get_lock(op='Network update DnsMasq', global_scope=False):
                d = self._get_dnsmasq_object()
                d.restart()
        else:
            etcd.enqueue('networknode', UpdateDnsMasqNetworkTask(self.uuid))

    def remove_dnsmasq(self):
        if not self.provide_dhcp and not self.provide_dns:
            return

        if config.NODE_IS_NETWORK_NODE:
            with self.get_lock(op='Network remove DnsMasq', global_scope=False):
                d = self._get_dnsmasq_object()
                d.terminate()
                d.state = dnsmasq.DnsMasq.STATE_DELETED
        else:
            etcd.enqueue('networknode', RemoveDnsMasqNetworkTask(self.uuid))

    def enable_nat(self):
        if not config.NODE_IS_NETWORK_NODE:
            return

        subst = self.subst_dict()
        if not util_network.nat_rules_for_ipblock(self.network_address):
            util_process.execute(
                None, 'echo 1 > /proc/sys/net/ipv4/ip_forward')
            util_process.execute(
                None,
                'iptables -w 10 -A FORWARD -o %(egress_veth_inner)s '
                '-i %(vx_veth_inner)s -j ACCEPT' % subst,
                namespace=self.uuid)
            util_process.execute(
                None,
                'iptables -w 10 -A FORWARD -i %(egress_veth_inner)s '
                '-o %(vx_veth_inner)s -j ACCEPT' % subst,
                namespace=self.uuid)
            util_process.execute(
                None,
                'iptables -w 10 -t nat -A POSTROUTING -s %(ipblock)s/%(netmask)s '
                '-o %(egress_veth_inner)s -j MASQUERADE' % subst,
                namespace=self.uuid)

    def remove_nat(self):
        if config.NODE_IS_NETWORK_NODE:
            if self.floating_gateway:
                fn = floating_network()
                fn.ipam.release(self.floating_gateway)
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

        with self.get_lock(op='Network ensure mesh', global_scope=False):
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
                self.add_event(EVENT_TYPE_MUTATE, 'remove mesh elements',
                               extra={'removed': removed})
            if added:
                self.add_event(EVENT_TYPE_MUTATE, 'add mesh elements',
                               extra={'added': added})

    def _add_mesh_element(self, n):
        subst = self.subst_dict()
        subst['node'] = n

        try:
            util_process.execute(None,
                                 'bridge fdb append to 00:00:00:00:00:00 '
                                 'dst %(node)s dev %(vx_interface)s' % subst)
            self.add_event(EVENT_TYPE_MUTATE, 'added new mesh element', extra={'ip': n})
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
            self.add_event(EVENT_TYPE_MUTATE, 'removed excess mesh element',
                           extra={'ip': n})
        except processutils.ProcessExecutionError as e:
            self.log.with_fields({
                'node': n,
                'error': e}).info('Failed to remove mesh element')

    # NOTE(mikal): this call only works on the network node, the API
    # server redirects there.
    def add_floating_ip(self, floating_address, inner_address):
        self.add_event(EVENT_TYPE_AUDIT, 'adding floating ip',
                       extra={
                           'floating': floating_address,
                           'inner': inner_address
                       })
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
        util_network.add_address_to_interface(
            self.uuid, floating_address, '32', 'flt-%(floating_address_as_hex)s-i' % subst)
        util_process.execute(
            None,
            'iptables -w 10 -t nat -A PREROUTING -d %(floating_address)s -j DNAT '
            '--to-destination %(inner_address)s' % subst,
            namespace=self.uuid)

    # NOTE(mikal): this call only works on the network node, the API
    # server redirects there.
    def remove_floating_ip(self, floating_address, inner_address):
        self.add_event(EVENT_TYPE_AUDIT, 'removing floating',
                       extra={
                           'floating': floating_address,
                           'inner': inner_address
                       })
        subst = self.subst_dict()
        subst['floating_address'] = floating_address
        subst['floating_address_as_hex'] = '%08x' % int(
            ipaddress.IPv4Address(floating_address))
        subst['inner_address'] = inner_address

        if util_network.check_for_interface('flt-%(floating_address_as_hex)s-o' % subst):
            util_process.execute(None,
                                 'ip link del flt-%(floating_address_as_hex)s-o'
                                 % subst)

    def route_address(self, floating_address):
        self.add_event(
            EVENT_TYPE_AUDIT, 'routing floating ip to network',
            extra={'floating': floating_address})
        subst = self.subst_dict()
        subst['floating_address'] = floating_address
        util_process.execute(
            None, 'ip route add %(floating_address)s/32 dev %(vx_bridge)s' % subst)

    def unroute_address(self, floating_address):
        self.add_event(
            EVENT_TYPE_AUDIT, 'unrouting floating ip to network',
            extra={'floating': floating_address})
        subst = self.subst_dict()
        subst['floating_address'] = floating_address
        util_process.execute(
            None, 'ip route del %(floating_address)s/32 dev %(vx_bridge)s' % subst)


class Networks(dbo_iter):
    base_object = Network

    def __iter__(self):
        for _, n in self.get_iterator():
            if n['uuid'] == 'floating':
                continue

            try:
                n = Network(n)
                if not n:
                    continue

                out = self.apply_filters(n)
                if out:
                    yield out
            except IPManagerMissing:
                pass


# Convenience helpers
def networks_in_namespace(namespace):
    return Networks([partial(baseobject.namespace_filter, namespace)])


def floating_network():
    floating_network = Network.from_db('floating', suppress_failure_audit=True)
    if not floating_network:
        Network.new(network_uuid='floating',
                    vxid=0,
                    netblock=config.FLOATING_NETWORK,
                    provide_dhcp=False,
                    provide_nat=False,
                    provide_dns=False,
                    namespace=None,
                    name='floating')
    return floating_network
