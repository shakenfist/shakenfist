# Copyright 2020 Michael Still
import copy
import os
import random
import time
from functools import partial
from uuid import uuid4

from shakenfist_utilities import logs  # noreorder

from shakenfist import baseobject
from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist.constants import get_object_class
from shakenfist import etcd
from shakenfist import instance
from shakenfist import ipam
from shakenfist.network import interface
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectWithOperations as dbowo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.net_op \
    import create_and_enqueue as net_create_and_enqueue
from shakenfist.schema.operations.net_op \
    import model_tasks as net_tasks
from shakenfist.schema.operations.net_macaddr_ip_op \
    import create_and_enqueue as nmi_create_and_enqueue
from shakenfist.schema.operations.net_macaddr_ip_op \
    import model_tasks as nmi_tasks
from shakenfist.schema.operations.node_net_op \
    import create_and_enqueue as nn_create_and_enqueue
from shakenfist.schema.operations.node_net_op \
    import model_tasks as nn_tasks
from shakenfist.eventlog import add_event_multi
from shakenfist.exceptions import CannotAssignFloatingGateway
from shakenfist.exceptions import CongestedNetwork
from shakenfist.exceptions import DeadNetwork
from shakenfist.managed_executables import dnsmasq
from shakenfist.node import Node
from shakenfist.node import Nodes
from shakenfist.schema.object_types import ObjectType
from shakenfist.util import general as util_general
from shakenfist.util import network as util_network
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


class Network(dbowo):
    object_type = ObjectType.NETWORK
    initial_version = 2
    current_version = 8

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
            in_memory_only = self.state.value == dbo.STATE_DELETED
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
        for ni in interface.NetworkInterfaces(
                [partial(interface.network_uuid_filter, static_values['uuid'])],
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

    @classmethod
    def _upgrade_step_5_to_6(cls, static_values):
        etcd.put('attribute/network', static_values['uuid'], 'hosteddns', {})

    @classmethod
    def _upgrade_step_6_to_7(cls, static_values):
        ...

    @classmethod
    def _upgrade_step_7_to_8(cls, static_values):
        # State migration to MariaDB is now handled by sf-ctl migrate-state-to-mariadb
        ...

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
        op_type, op_uuid = net_create_and_enqueue(
            n.uuid,
            [net_tasks.network_deploy],
            PRIORITY.user_waiting,
            runs_after=[n.last_cluster_operation],
            request_id=util_general.get_request_id())
        n.set_last_cluster_operation(op_type, op_uuid)

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
            'vxlan_id': self.__vxid,
            'last_cluster_operation': self.last_cluster_operation
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
        self._add_item_in_attribute_list('networkinterfaces', str(ni.uuid))

    def remove_networkinterface(self, ni):
        if ni.ipv4:
            self.remove_dhcp_lease(ni.ipv4, ni.macaddr)
        self._remove_item_in_attribute_list('networkinterfaces', str(ni.uuid))

    def _update_floating_gateway(self, gateway):
        original_routing = self.routing
        original_gateway = original_routing.get('floating_gateway')
        if original_gateway == gateway:
            return True
        if original_gateway:
            return False

        if not original_routing:
            original_routing = None
            updated_routing = {
                'floating_gateway': gateway
            }
        else:
            updated_routing = copy.copy(original_routing)
            updated_routing['floating_gateway'] = gateway

        return etcd.replace('attribute/network', self.uuid, 'routing',
                            original_routing, updated_routing)

    def assign_floating_gateway(self):
        fn = floating_network()
        floating_gateway = fn.ipam.reserve_random_free_address(
            self.unique_label(), ReservationType.GATEWAY, '')
        if self._update_floating_gateway(floating_gateway):
            return
        fn.ipam.release(floating_gateway)

        if not self.floating_gateway:
            raise CannotAssignFloatingGateway()

    def unassign_floating_gateway(self):
        floating_gateway = self.floating_gateway
        if not floating_gateway:
            return
        fn = floating_network()
        fn.ipam.release(floating_gateway)
        self._update_floating_gateway(None)

    @property
    def _vx_veth_inner(self):
        return 'veth-%06x-i' % self.vxid

    def subst_dict(self):
        # NOTE(mikal): it should be noted that the maximum interface name length
        # on Linux is 15 user visible characters, we therefore use hex for vxids
        # where they appear in an interface name. Note that vxid does not appear
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

            'netns': str(self.uuid),

            'ipblock': self.ipblock,
            'netmask': self.netmask,
            'router': self.router,
            'broadcast': self.broadcast,

            'dhcp_start': self.dhcp_start,
            'provide_nat': self.provide_nat,
        }

        # Hosted DNS entries
        if self.provide_dns:
            retval['hosted_dns'] = self._db_get_attribute('hosteddns', {})
        else:
            retval['hosted_dns'] = {}

        return retval

    def is_okay(self):
        """Check if network is created and running."""
        last_op = self.last_cluster_operation
        if last_op and last_op.get('op_type'):
            op = get_object_class(last_op.get('op_type')).from_db(
                last_op.get('op_uuid'), suppress_failure_audit=True)
            if op and op.state.value not in [op.STATE_COMPLETE,
                                             op.STATE_ABORT,
                                             op.STATE_ERROR,
                                             op.STATE_DELETED]:
                # There is an incomplete operation so we assume this network
                # is ok for now.
                return True

        if not self.is_created():
            self.add_event(EVENT_TYPE_STATUS, 'network not ok, is not created')
            return False

        if not config.NODE_IS_NETWORK_NODE:
            return True

        if self.provide_dhcp or self.provide_dns:
            if not self.is_dnsmasq_running():
                self.add_event(
                    EVENT_TYPE_STATUS, 'network not ok, dnsmasq not running')
                return False

        return True

    def is_created(self):
        """Attempt to ensure network has been created successfully."""

        # The floating network always exists, and would fail the vx_bridge
        # test we apply to other networks.
        if self.uuid == FLOATING_NETWORK_UUID:
            return True

        subst = self.subst_dict()
        if not util_network.check_for_interface(subst['vx_bridge'], up=True):
            self.add_event(
                EVENT_TYPE_STATUS, f'{subst["vx_bridge"]} is not up')
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

    def _not_on_floating_network(func):
        # Some calls don't make sense on the floating network and are ignored
        def wrapper(*args, **kwargs):
            # The first argument is "self"
            if args[0].uuid == FLOATING_NETWORK_UUID:
                return
            return func(*args, **kwargs)
        return wrapper

    @_not_on_floating_network
    def create_on_hypervisor(self):
        self.add_event(EVENT_TYPE_AUDIT, 'creating network on hypervisor')
        with self.get_lock(op='create_on_hypervisor', global_scope=False):
            if self.is_dead():
                raise DeadNetwork('network=%s' % self)
            util_concurrency.create_vxlan_interface(self.vxid, self.mesh_nic)

    @_not_on_floating_network
    def create_on_network_node(self):
        if self.state.value == dbo.STATE_DELETED:
            self.add_event(
                EVENT_TYPE_AUDIT, 'refusing to create deleted network on network node')
            return
        self.add_event(EVENT_TYPE_AUDIT, 'creating network on network node')

        with self.get_lock(op='create_on_network_node', global_scope=False):
            if self.is_dead():
                raise DeadNetwork('network=%s' % self)

            util_concurrency.create_vxlan_interface(self.vxid, self.mesh_nic)
            util_concurrency.create_network_namespace(self.uuid)

            subst = self.subst_dict()

            if not util_network.check_for_interface(subst['vx_veth_outer']):
                util_network.create_interface(
                    subst['vx_veth_outer'], 'veth',
                    'peer name %(vx_veth_inner)s' % subst)
                util_concurrency.execute(
                    'ip link set %(vx_veth_inner)s netns %(netns)s' % subst)

                # Refer to bug 952 for more details here, but it turns out
                # that adding an interface to a bridge overwrites the MTU of
                # the bridge in an undesirable way. So we lookup the existing
                # MTU and then re-specify it here.
                subst['vx_bridge_mtu'] = util_network.get_interface_mtu(
                    subst['vx_bridge'])
                util_concurrency.execute(
                    'ip link set %(vx_veth_outer)s master %(vx_bridge)s '
                    'mtu %(vx_bridge_mtu)s' % subst)

                util_concurrency.execute(
                    'ip link set %(vx_veth_outer)s up' % subst)
                util_concurrency.execute(
                    'ip link set %(vx_veth_inner)s up' % subst,
                    netns=self.uuid)
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
                util_concurrency.execute(
                    'ip link set %(egress_veth_outer)s master %(egress_bridge)s '
                    'mtu %(egress_bridge_mtu)s' % subst)

                util_concurrency.execute(
                    'ip link set %(egress_veth_outer)s up' % subst)
                util_concurrency.execute(
                    'ip link set %(egress_veth_inner)s netns %(netns)s' % subst)

            if self.provide_nat:
                # We don't always need this lock, but acquiring it here means
                # we don't need to construct two identical ipmanagers one after
                # the other.
                try:
                    if not self.floating_gateway:
                        self.assign_floating_gateway()

                    fn = floating_network()
                    subst.update({
                        'floating_router': fn.ipam.get_address_at_index(1),
                        'floating_gateway': self.floating_gateway,
                        'floating_netmask': fn.netmask
                    })
                except CongestedNetwork:
                    self.state = self.STATE_ERROR
                    self.error = 'Unable to allocate floating gateway IP'
                    return

                addresses = list(util_network.get_interface_addresses(
                    subst['egress_veth_inner'], netns=subst['netns']))
                self.log.with_fields({
                    'addresses': addresses,
                    'current_address': subst['floating_gateway']}).debug(
                        'Egress veth has these addresses')
                if not subst['floating_gateway'] in list(addresses):
                    util_network.add_address_to_interface(
                        self.uuid, subst['floating_gateway'], subst['floating_netmask'],
                        subst['egress_veth_inner'])

                needs_default_route = True
                default_routes = util_network.get_default_routes(self.uuid)
                if default_routes == [subst['floating_router']]:
                    needs_default_route = False
                elif default_routes:
                    for default_route in default_routes:
                        if default_route == subst['floating_router']:
                            needs_default_route = False
                        else:
                            util_network.delete_default_route(
                                self.uuid, default_route)

                if needs_default_route:
                    util_network.add_default_route(
                        self.uuid, subst['floating_router'])

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
                util_concurrency.execute(
                    'ip link delete %(vx_bridge)s' % subst)

            if util_network.check_for_interface(subst['vx_interface']):
                util_concurrency.execute(
                    'ip link delete %(vx_interface)s' % subst)

    # This method should only ever be called when you already know you're on
    # the network node. Specifically it is called by a queue task that the
    # network node listens for.
    def delete_on_network_node(self):
        with self.get_lock(op='Network delete', global_scope=False):
            subst = self.subst_dict()

            if util_network.check_for_interface(subst['vx_veth_outer']):
                util_concurrency.execute(
                    'ip link delete %(vx_veth_outer)s' % subst)

            if util_network.check_for_interface(subst['egress_veth_outer']):
                util_concurrency.execute(
                    'ip link delete %(egress_veth_outer)s' % subst)

            if os.path.exists('/var/run/netns/%s' % str(self.uuid)):
                util_concurrency.execute('ip netns del %s' % str(self.uuid))

            self.ipam.state = self.ipam.STATE_DELETED
            self.state = self.STATE_DELETED

        # Ensure that all hypervisors remove this network. This is really
        # just catching strays, apart from on the network node where we
        # absolutely need to do this thing.
        for n in Nodes([], prefilter='active'):
            nn_create_and_enqueue(
                n.uuid,
                self.uuid,
                [nn_tasks.network_destroy],
                PRIORITY.user_facing,
                request_id=util_general.get_request_id())

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
        is_running = d.is_running()
        if not is_running:
            self.add_event(EVENT_TYPE_STATUS, 'dnsmasq is not running')
        return is_running

    def remove_dhcp_lease(self, ipv4, macaddr):
        if not self.provide_dhcp and not self.provide_dns:
            return

        if config.NODE_IS_NETWORK_NODE:
            with self.get_lock(op='Network update DnsMasq', global_scope=False):
                d = self._get_dnsmasq_object()
                d.remove_lease(ipv4, macaddr)
        else:
            op_type, op_uuid = nmi_create_and_enqueue(
                self.uuid, macaddr, ipv4, [nmi_tasks.remove_dhcp_lease],
                PRIORITY.user_facing)
            self.set_last_cluster_operation(op_type, op_uuid)

    def update_dnsmasq(self):
        if not self.provide_dhcp and not self.provide_dns:
            return

        if config.NODE_IS_NETWORK_NODE:
            with self.get_lock(op='Network update DnsMasq', global_scope=False):
                d = self._get_dnsmasq_object()
                d.restart()
        else:
            net_create_and_enqueue(
                self.uuid,
                [net_tasks.network_update_dnsmasq],
                priority=PRIORITY.user_facing_high_io
            )

    def remove_dnsmasq(self):
        if not self.provide_dhcp and not self.provide_dns:
            return

        if config.NODE_IS_NETWORK_NODE:
            with self.get_lock(op='Network remove DnsMasq', global_scope=False):
                d = self._get_dnsmasq_object()
                d.terminate()
                d.state = dnsmasq.DnsMasq.STATE_DELETED
        else:
            net_create_and_enqueue(
                self.uuid,
                [net_tasks.network_remove_dnsmasq],
                priority=PRIORITY.user_facing
            )

    def enable_nat(self):
        if not config.NODE_IS_NETWORK_NODE:
            return
        util_concurrency.enable_nat(
            self.uuid, self.network_address, self.netmask, self.vxid)

    def remove_nat(self):
        if config.NODE_IS_NETWORK_NODE:
            if self.floating_gateway:
                self.unassign_floating_gateway()

        else:
            net_create_and_enqueue(
                self.uuid,
                [net_tasks.network_remove_nat],
                priority=PRIORITY.user_facing
            )

    def update_dns_entry(self, name, value):
        if not self.provide_dns:
            return

        with self.get_lock_attr('hosteddns', 'Update hosted DNS entry'):
            entries = self._db_get_attribute('hosteddns', {})
            entries[name] = value
            self._db_set_attribute('hosteddns', entries)

        if config.NODE_IS_NETWORK_NODE:
            with self.get_lock(op='Network update DnsMasq', global_scope=False):
                d = self._get_dnsmasq_object()
                d.restart()
        else:
            net_create_and_enqueue(
                self.uuid,
                [net_tasks.network_update_dnsmasq],
                priority=PRIORITY.user_facing_high_io
            )

    def remove_dns_entry(self, name):
        if not self.provide_dns:
            return

        with self.get_lock_attr('hosteddns', 'Remove hosted DNS entry'):
            entries = self._db_get_attribute('hosteddns', {})
            if name in entries:
                del entries[name]
                self._db_set_attribute('hosteddns', entries)

        if config.NODE_IS_NETWORK_NODE:
            with self.get_lock(op='Network update DnsMasq', global_scope=False):
                d = self._get_dnsmasq_object()
                d.restart()
        else:
            net_create_and_enqueue(
                self.uuid,
                [net_tasks.network_update_dnsmasq],
                priority=PRIORITY.user_facing_high_io
            )

    @_not_on_floating_network
    def ensure_mesh(self):
        # Determine which IPs should be on this mesh and where
        instances = []
        for ni_uuid in self.networkinterfaces:
            ni = interface.NetworkInterface.from_db(ni_uuid)
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

        added, removed = util_concurrency.ensure_vxlan_mesh(
            self.uuid, self.vxid, node_ips)
        if removed:
            self.add_event(EVENT_TYPE_MUTATE, 'remove mesh elements',
                           extra={'removed': removed})
        if added:
            self.add_event(EVENT_TYPE_MUTATE, 'add mesh elements',
                           extra={'added': added})

    # NOTE(mikal): this call only works on the network node, the API
    # server redirects there.
    def add_floating_ip(self, floating_address, inner_address, affected_objects):
        affected_objects.append(self)
        affected_objects.append(('network', FLOATING_NETWORK_UUID))
        add_event_multi(
            EVENT_TYPE_AUDIT, affected_objects, 'adding floating ip',
            extra={
                'floating': floating_address,
                'inner': inner_address
            })
        util_concurrency.add_floating_ip(
            str(self.uuid), floating_address, inner_address)

    # NOTE(mikal): this call only works on the network node, the API
    # server redirects there.
    def remove_floating_ip(self, floating_address, inner_address, affected_objects):
        affected_objects.append(self)
        affected_objects.append(('network', FLOATING_NETWORK_UUID))
        add_event_multi(
            EVENT_TYPE_AUDIT, affected_objects, 'remove floating ip',
            extra={
                'floating': floating_address,
                'inner': inner_address
            })
        util_concurrency.remove_floating_ip(str(self.uuid), floating_address)

    # NOTE(mikal): this call only works on the network node, the API
    # server redirects there.
    def route_address(self, floating_address):
        self.add_event(
            EVENT_TYPE_AUDIT, 'routing floating ip to network',
            extra={'floating': floating_address})
        subst = self.subst_dict()
        subst['floating_address'] = floating_address
        util_concurrency.execute(
            'ip route add %(floating_address)s/32 dev %(vx_bridge)s' % subst)

    # NOTE(mikal): this call only works on the network node, the API
    # server redirects there.
    def unroute_address(self, floating_address):
        self.add_event(
            EVENT_TYPE_AUDIT, 'unrouting floating ip to network',
            extra={'floating': floating_address})
        subst = self.subst_dict()
        subst['floating_address'] = floating_address
        util_concurrency.execute(
            'ip route del %(floating_address)s/32 dev %(vx_bridge)s' % subst)


class Networks(dbo_iter):
    base_object = Network

    def __iter__(self):
        for _, static_values in self.get_iterator():
            if static_values['uuid'] == str(FLOATING_NETWORK_UUID):
                continue

            n = Network(static_values)
            if not n:
                continue

            out = self.apply_filters(n)
            if out:
                yield out


# Convenience helpers
def networks_in_namespace(namespace):
    return Networks([partial(baseobject.namespace_filter, namespace)])


def floating_network():
    fn = Network.from_db(FLOATING_NETWORK_UUID, suppress_failure_audit=True)
    if not fn:
        Network.new(network_uuid=FLOATING_NETWORK_UUID,
                    vxid=0,
                    netblock=config.FLOATING_NETWORK,
                    provide_dhcp=False,
                    provide_nat=False,
                    provide_dns=False,
                    namespace=None,
                    name='floating')
        fn = Network.from_db(FLOATING_NETWORK_UUID)
    return fn
