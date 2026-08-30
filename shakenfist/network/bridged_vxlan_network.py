# Copyright 2019 Michael Still and contributors
"""Worker-only sibling of :class:`shakenfist.network.network.Network`.

`BridgedVXLanNetwork` is the worker-side counterpart of `Network`. While
`Network` is the public, externally-visible object that callers interact with
(and which, after Phase 2f, enqueues operations onto the per-node `network`
queue), `BridgedVXLanNetwork` is the place where host-state mutation actually
happens.

It is instantiated **only** inside the workitem dispatcher
(``shakenfist/daemons/network/workitem.py`` via the ``NetOp`` task handlers
in ``shakenfist/operations/net_op.py``). It wraps an existing `Network`
instance and exposes ``_apply_*`` methods which each implement one piece of
network host-state mutation.

Re-entrancy through the queue is structurally impossible: the only way to
bypass the queue and run a mutation inline is to construct a
`BridgedVXLanNetwork`, and the constructor is only called inside the
dispatcher itself. Public code paths must continue to use `Network` and
its enqueueing methods.

Phase 2 implements ``_apply_ensure_mesh``. Phase 3 adds the floating-IP
and route methods: ``_apply_add_floating_ip``,
``_apply_remove_floating_ip``, ``_apply_route_address``,
``_apply_unroute_address`` and ``_apply_remove_nat``. Phase 4 adds the
dnsmasq lifecycle methods: ``_apply_update_dnsmasq``,
``_apply_remove_dnsmasq`` and ``_apply_remove_dhcp_lease``. The
remaining ``_apply_*`` methods (lifecycle, ...) move across in later
phases.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist.exceptions import CongestedNetwork
from shakenfist.exceptions import DeadNetwork
from shakenfist.exceptions import NotOnNetworkNode
from shakenfist.managed_executables import dnsmasq
from shakenfist.node import Nodes
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.node_net_op \
    import create_and_enqueue as nn_create_and_enqueue
from shakenfist.schema.operations.node_net_op \
    import model_tasks as nn_tasks
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import general as util_general
from shakenfist.util import network as util_network

if TYPE_CHECKING:
    from shakenfist.network.network import Network


LOG, _ = logs.setup(__name__)


class BridgedVXLanNetwork:
    """Worker-side network mutation surface.

    Wraps a `Network` instance and runs host-state mutations on its behalf.
    See the module docstring for the surrounding lifecycle.
    """

    def __init__(self, network: 'Network') -> None:
        self.network = network

    @staticmethod
    def _require_network_node(method_name: str) -> None:
        """Refuse to run a network-node-only ``_apply_*`` on a host that
        is not the elected network node.

        The dnsmasq config, NAT rules, floating-network bindings and
        per-network netns these methods touch only exist on the
        elected network node. When the same method is called from a
        hypervisor (e.g. from a ``node_inst_*`` handler) it silently
        mutates only that host's state, which for network-node-only
        operations is just an absent set of files / rules / namespaces
        -- so the call appears to succeed while the cluster-wide
        effect never lands. Raising here surfaces the bug at the call
        site instead of at test-failure time, in the spirit of the
        ``MARIADB_HOST`` check in :mod:`shakenfist.mariadb` that pins
        direct database access to the database daemon.

        ``method_name`` is included in the message so the offending
        ``_apply_*`` is named in the traceback without forcing the
        helper to introspect its frame.
        """
        if not config.NODE_IS_NETWORK_NODE:
            raise NotOnNetworkNode(
                f'{method_name} must run on the elected network node; '
                'this host is not it. Use the public Network.* method '
                'that enqueues a net_op against the cluster-wide '
                'networknode-* queue.')

    def _apply_ensure_mesh(self) -> None:
        """Ensure the VXLAN FDB mesh for the wrapped network is correct.

        Lifted verbatim (modulo ``self`` -> ``self.network``) from the
        original ``Network.ensure_mesh()`` body. The single-threaded
        net-worker dispatcher is the only caller of this method and
        provides natural serialisation; no explicit lock is required.

        Raises the same typed exceptions as the original implementation:
        ``EnsureMeshFailed`` (from the underlying privexec layer) and
        ``DeadNetwork`` (if the network has been torn down out from under
        us).

        Unlike the dnsmasq / NAT / floating-IP / netns ``_apply_*`` methods
        on this class, this one is **not** network-node-only: every
        hypervisor with an interface on the network maintains its own
        VXLAN FDB entries for the mesh (which is why ``Network.ensure_mesh``
        enqueues the op against the per-node ``network`` family, not the
        cluster-wide ``networknode`` family). The body excludes ``self`` from
        the mesh to avoid duplicate-packet reflection (see bug #859), which
        only makes sense when the method runs on every participating host.
        Consequently there is no ``_require_network_node`` guard here.
        """
        # The original method was decorated with `_not_on_floating_network`
        # which short-circuits when invoked on the floating network. We
        # preserve that semantics inline here rather than copying the
        # decorator definition into this module -- the check is one line
        # and inlining keeps `BridgedVXLanNetwork` independent of the
        # private decorator name in `network.py`.
        if self.network.uuid == FLOATING_NETWORK_UUID:
            return

        # The vxlan device may be missing on this node entirely -- either
        # it has never been created here, or a teardown raced an earlier
        # op. The privexec mesh handler treats a missing device as a
        # benign no-op (correct for the racing-teardown case), which
        # means an ensure_mesh against a missing device silently renders
        # nothing and the network stays dark until the maintain loop's
        # full "not okay" recreate path notices. Materialise the device
        # first instead: a renderer must be able to render from nothing.
        # The create methods below are idempotent and refuse dead or
        # deleted networks, so the teardown race keeps its old benign
        # behaviour.
        subst = self.network.subst_dict()
        if not util_network.check_for_interface(subst['vx_interface']):
            self.network.add_event(
                EVENT_TYPE_AUDIT,
                'vxlan device missing, creating before mesh render',
                extra={'vx_interface': subst['vx_interface']})
            if config.NODE_IS_NETWORK_NODE:
                self._apply_create_on_network_node()
            else:
                self._apply_create_on_hypervisor()

        # Determine which IPs should be on this mesh. The enumeration
        # lives on the wrapped Network (``mesh_desired_node_ips``) so
        # the writer (this method) and the auditor
        # (``Network.is_mesh_okay``) can never disagree about what the
        # mesh should contain.
        node_ips = self.network.mesh_desired_node_ips()

        added, removed = util_concurrency.ensure_vxlan_mesh(
            self.network.uuid, self.network.vxid, node_ips)
        if removed:
            self.network.add_event(
                EVENT_TYPE_MUTATE, 'remove mesh elements',
                extra={'removed': removed})
        if added:
            self.network.add_event(
                EVENT_TYPE_MUTATE, 'add mesh elements',
                extra={'added': added})

    def _apply_add_floating_ip(
            self, floating_address: str, inner_address: str) -> None:
        """Add a floating IP mapping on the network node.

        Lifted from ``Network.add_floating_ip`` (network.py:908-921). The
        ``affected_objects`` parameter and the multi-target audit event are
        intentionally stripped here: event correlation is the dispatcher's
        responsibility under the migrated design, not the apply layer's.
        The single-threaded net-worker dispatcher is the only caller of
        this method and provides natural serialisation; no explicit lock
        is required.

        Declines quietly on a deleted network, mirroring the guard in
        ``_apply_create_on_network_node``: the maintain loop enqueues this
        task at ``background`` priority behind a create, and the network
        (and its netns) can be torn down before the batch is dequeued
        (issue 3962).
        """
        self._require_network_node('_apply_add_floating_ip')

        if self.network.state.value == dbo.STATE_DELETED:
            self.network.add_event(
                EVENT_TYPE_AUDIT,
                'refusing to add floating ip to deleted network',
                extra={'floating': floating_address})
            return

        util_concurrency.add_floating_ip(
            str(self.network.uuid), floating_address, inner_address,
            vxid=self.network.vxid)

    def _apply_remove_floating_ip(
            self, floating_address: str, inner_address: str) -> None:
        """Remove a floating IP mapping on the network node.

        Lifted from ``Network.remove_floating_ip`` (network.py:923-934). The
        ``affected_objects`` parameter and the multi-target audit event are
        stripped for the same reason as ``_apply_add_floating_ip``. The
        ``inner_address`` argument is retained on the signature for symmetry
        with the add case (the underlying privexec helper only needs the
        floating address, but dispatchers pass both for event-emission
        purposes). The single-threaded net-worker dispatcher is the only
        caller of this method and provides natural serialisation; no
        explicit lock is required.
        """
        self._require_network_node('_apply_remove_floating_ip')

        util_concurrency.remove_floating_ip(
            str(self.network.uuid), floating_address)

    def _apply_route_address(self, ip: str) -> None:
        """Add a host route for a floating IP onto the network's vx bridge.

        Lifted from ``Network.route_address`` (network.py:938-947). The
        single-target audit event on the wrapped network is preserved here
        (it is not multi-target, so no dispatcher fan-out is required).
        The single-threaded net-worker dispatcher is the only caller of
        this method and provides natural serialisation; no explicit lock
        is required.

        Declines quietly on a deleted network for the same reason as
        ``_apply_add_floating_ip``: the maintain loop's reconciliation
        batch can be dequeued long after the network (and its
        ``vx_bridge``) has been torn down (issue 3962).
        """
        self._require_network_node('_apply_route_address')

        if self.network.state.value == dbo.STATE_DELETED:
            self.network.add_event(
                EVENT_TYPE_AUDIT,
                'refusing to route address on deleted network',
                extra={'floating': ip})
            return

        self.network.add_event(
            EVENT_TYPE_AUDIT, 'routing floating ip to network',
            extra={'floating': ip})
        subst = self.network.subst_dict()
        subst['floating_address'] = ip
        util_concurrency.execute(
            'ip route add %(floating_address)s/32 dev %(vx_bridge)s'
            % subst)

    def _apply_unroute_address(self, ip: str) -> None:
        """Remove a host route for a floating IP from the network's vx bridge.

        Lifted from ``Network.unroute_address`` (network.py:950-960). As with
        ``_apply_route_address``, the single-target audit event on the
        wrapped network is preserved. The single-threaded net-worker
        dispatcher is the only caller of this method and provides natural
        serialisation; no explicit lock is required.
        """
        self._require_network_node('_apply_unroute_address')

        self.network.add_event(
            EVENT_TYPE_AUDIT, 'unrouting floating ip to network',
            extra={'floating': ip})
        subst = self.network.subst_dict()
        subst['floating_address'] = ip
        util_concurrency.execute(
            'ip route del %(floating_address)s/32 dev %(vx_bridge)s'
            % subst)

    def _apply_remove_nat(self) -> None:
        """Tear down the network node's NAT for the wrapped network.

        Lifted from ``Network.remove_nat`` (network.py:823-834). Today the
        method has a `NODE_IS_NETWORK_NODE` guard and a `not-on-this-node`
        enqueue fallback; under the migrated design the dispatcher only
        ever invokes ``_apply_*`` on the elected network node, so the guard
        is unnecessary here and the body collapses to the network-node
        branch only. The single-threaded net-worker dispatcher is the only
        caller of this method and provides natural serialisation; no
        explicit lock is required.
        """
        self._require_network_node('_apply_remove_nat')

        if self.network.floating_gateway:
            self.network.unassign_floating_gateway()

    def _apply_update_dnsmasq(self) -> None:
        """Restart dnsmasq for the wrapped network on the network node.

        Lifted from the ``if config.NODE_IS_NETWORK_NODE`` branch of
        ``Network.update_dnsmasq`` (network.py:793-806). The
        ``if not self.provide_dhcp and not self.provide_dns: return`` guard
        at the top of the original ``Network`` method stays at the caller
        level (it gates whether the work is even enqueued); the apply
        method assumes work is needed. The single-threaded net-worker
        dispatcher is the only caller of this method and provides natural
        serialisation; no explicit lock is required.
        """
        self._require_network_node('_apply_update_dnsmasq')

        d = self.network._get_dnsmasq_object()
        d.restart()

    def _apply_remove_dnsmasq(self) -> None:
        """Terminate dnsmasq for the wrapped network on the network node.

        Lifted from the ``if config.NODE_IS_NETWORK_NODE`` branch of
        ``Network.remove_dnsmasq`` (network.py:808-822). The state
        transition to ``DnsMasq.STATE_DELETED`` is part of the lifted
        body. As with ``_apply_update_dnsmasq``, the provide-guard remains
        at the caller level. The single-threaded net-worker dispatcher is
        the only caller of this method and provides natural serialisation;
        no explicit lock is required.
        """
        self._require_network_node('_apply_remove_dnsmasq')

        d = self.network._get_dnsmasq_object()
        d.terminate()
        d.state = dnsmasq.DnsMasq.STATE_DELETED

    def _apply_remove_dhcp_lease(self, ipv4: str, macaddr: str) -> None:
        """Release a DHCP lease for ``ipv4``/``macaddr`` on the network node.

        Lifted from the ``if config.NODE_IS_NETWORK_NODE`` branch of
        ``Network.remove_dhcp_lease`` (network.py:780-791). As with the
        other dnsmasq apply methods, the provide-guard stays at the
        caller level. The single-threaded net-worker dispatcher is the
        only caller of this method and provides natural serialisation;
        no explicit lock is required.
        """
        self._require_network_node('_apply_remove_dhcp_lease')

        d = self.network._get_dnsmasq_object()
        d.remove_lease(ipv4, macaddr)

    def _apply_create_on_hypervisor(self) -> None:
        """Set up the local VXLAN interface for the wrapped network.

        Lifted verbatim from ``Network.create_on_hypervisor``
        (network.py:575-589). The original method was decorated with
        ``_not_on_floating_network``; we preserve that semantics inline
        here (matching the pattern in ``_apply_ensure_mesh``). The
        single-threaded net-worker dispatcher is the only caller of this
        method and provides natural serialisation; no explicit lock is
        required.
        """
        if self.network.uuid == FLOATING_NETWORK_UUID:
            return

        subst = self.network.subst_dict()
        self.network.add_event(
            EVENT_TYPE_AUDIT, 'creating network on hypervisor',
            extra={'vx_bridge': subst['vx_bridge'],
                   'vx_interface': subst['vx_interface'],
                   'mesh_nic': self.network.mesh_nic})
        if self.network.is_dead():
            raise DeadNetwork('network=%s' % self.network)
        util_concurrency.create_vxlan_interface(
            self.network.vxid, self.network.mesh_nic)
        self.network.add_event(
            EVENT_TYPE_AUDIT, 'created network on hypervisor',
            extra={'vx_bridge': subst['vx_bridge'],
                   'vx_interface': subst['vx_interface']})

    def _apply_create_on_network_node(self) -> None:
        """Set up the network namespace and routing on the network node.

        Lifted from ``Network.create_on_network_node`` (network.py:591-719).
        The original method was decorated with ``_not_on_floating_network``;
        we preserve that semantics inline here. The Phase 4 late-import
        workaround that constructed a fresh ``BridgedVXLanNetwork(self)``
        to call ``_apply_update_dnsmasq`` becomes a clean
        ``self._apply_update_dnsmasq()`` call, and the ``self.enable_nat()``
        call becomes ``self._apply_enable_nat()``. The
        ``self.assign_floating_gateway()`` helper stays on ``Network``.
        The single-threaded net-worker dispatcher is the only caller of
        this method and provides natural serialisation; no explicit lock
        is required.
        """
        self._require_network_node('_apply_create_on_network_node')

        if self.network.uuid == FLOATING_NETWORK_UUID:
            return

        if self.network.state.value == dbo.STATE_DELETED:
            self.network.add_event(
                EVENT_TYPE_AUDIT,
                'refusing to create deleted network on network node')
            return
        self.network.add_event(
            EVENT_TYPE_AUDIT, 'creating network on network node')

        # Late import: floating_network is defined in network.py, which
        # imports this module via the dispatcher path. Importing at
        # module load would form a cycle.
        from shakenfist.network.network import floating_network

        if self.network.is_dead():
            raise DeadNetwork('network=%s' % self.network)

        util_concurrency.create_vxlan_interface(
            self.network.vxid, self.network.mesh_nic)
        util_concurrency.create_network_namespace(self.network.uuid)

        subst = self.network.subst_dict()

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
                netns=self.network.uuid)
            util_network.add_address_to_interface(
                self.network.uuid, subst['router'], subst['netmask'],
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

        if self.network.provide_nat:
            try:
                if not self.network.floating_gateway:
                    self.network.assign_floating_gateway()

                fn = floating_network()
                subst.update({
                    'floating_router': fn.ipam.get_address_at_index(1),
                    'floating_gateway': self.network.floating_gateway,
                    'floating_netmask': fn.netmask
                })
            except CongestedNetwork:
                self.network.state = self.network.STATE_ERROR
                self.network.error = 'Unable to allocate floating gateway IP'
                return

            addresses = list(util_network.get_interface_addresses(
                subst['egress_veth_inner'], netns=subst['netns']))
            self.network.log.with_fields({
                'addresses': addresses,
                'current_address': subst['floating_gateway']}).debug(
                    'Egress veth has these addresses')
            if not subst['floating_gateway'] in list(addresses):
                util_network.add_address_to_interface(
                    self.network.uuid, subst['floating_gateway'],
                    subst['floating_netmask'],
                    subst['egress_veth_inner'])

            needs_default_route = True
            default_routes = util_network.get_default_routes(
                self.network.uuid)
            if default_routes == [subst['floating_router']]:
                needs_default_route = False
            elif default_routes:
                for default_route in default_routes:
                    if default_route == subst['floating_router']:
                        needs_default_route = False
                    else:
                        util_network.delete_default_route(
                            self.network.uuid, default_route)

            if needs_default_route:
                util_network.add_default_route(
                    self.network.uuid, subst['floating_router'])

            self._apply_enable_nat()

        # The Phase 4 late-import workaround for dnsmasq is no longer
        # necessary: this body now lives inside the worker class, so we
        # can call ``self._apply_update_dnsmasq()`` directly.
        if self.network.provide_dhcp or self.network.provide_dns:
            self._apply_update_dnsmasq()

        # A final check to ensure we haven't raced with a delete
        if self.network.is_dead():
            raise DeadNetwork('network=%s' % self.network)
        self.network.state = self.network.STATE_CREATED

    def _apply_delete_on_hypervisor(self) -> None:
        """Tear down the local VXLAN interfaces for the wrapped network.

        Lifted verbatim from ``Network.delete_on_hypervisor``
        (network.py:721-745). The single-threaded net-worker dispatcher
        is the only caller of this method and provides natural
        serialisation; no explicit lock is required.
        """
        subst = self.network.subst_dict()
        self.network.add_event(
            EVENT_TYPE_AUDIT, 'deleting network on hypervisor',
            extra={'vx_bridge': subst['vx_bridge'],
                   'vx_interface': subst['vx_interface']})

        bridge_present = util_network.check_for_interface(
            subst['vx_bridge'])
        if bridge_present:
            util_concurrency.execute(
                'ip link delete %(vx_bridge)s' % subst)

        interface_present = util_network.check_for_interface(
            subst['vx_interface'])
        if interface_present:
            util_concurrency.execute(
                'ip link delete %(vx_interface)s' % subst)

        self.network.add_event(
            EVENT_TYPE_AUDIT, 'deleted network on hypervisor',
            extra={'vx_bridge': subst['vx_bridge'],
                   'vx_interface': subst['vx_interface'],
                   'bridge_was_present': bridge_present,
                   'interface_was_present': interface_present})

    def _apply_delete_on_network_node(self) -> None:
        """Tear down the network namespace and fan out hypervisor cleanup.

        Lifted from ``Network.delete_on_network_node`` (network.py:747-797).
        The per-node fan-out loop (enqueueing ``node_net_op`` with
        ``network_destroy`` to every active node) is preserved. The
        Phase 4 late-import workarounds for ``_apply_remove_dnsmasq``
        and ``_apply_remove_nat`` collapse into clean
        ``self._apply_X()`` calls since this body now lives inside the
        worker class. The single-threaded net-worker dispatcher is the
        only caller of this method and provides natural serialisation;
        no explicit lock is required.
        """
        self._require_network_node('_apply_delete_on_network_node')

        subst = self.network.subst_dict()

        if util_network.check_for_interface(subst['vx_veth_outer']):
            util_concurrency.execute(
                'ip link delete %(vx_veth_outer)s' % subst)

        if util_network.check_for_interface(subst['egress_veth_outer']):
            util_concurrency.execute(
                'ip link delete %(egress_veth_outer)s' % subst)

        if os.path.exists('/var/run/netns/%s' % str(self.network.uuid)):
            util_concurrency.execute(
                'ip netns del %s' % str(self.network.uuid))

        # Release the floating gateway reservation _before_ the network is
        # marked deleted. The floating IP reaper considers a gateway
        # reservation whose owning network is deleted to be a leak, so
        # leaving the reservation in place across the state transition
        # opens a window (observed at 250ms to 6s on a busy cluster) in
        # which a reaper pass will release the reservation out from under
        # this teardown and log the perfectly healthy address as leaked
        # (issue 3645). Once "deleted" is published the network must
        # therefore hold no floating network resources at all.
        self._apply_remove_nat()

        self.network.ipam.state = self.network.ipam.STATE_DELETED
        self.network.state = self.network.STATE_DELETED

        # Ensure that all hypervisors remove this network. This is really
        # just catching strays, apart from on the network node where we
        # absolutely need to do this thing.
        for n in Nodes([], prefilter='active'):
            nn_create_and_enqueue(
                str(n.uuid),
                self.network.uuid,
                [nn_tasks.network_destroy],
                PRIORITY.user_facing,
                request_id=util_general.get_request_id())

        # The Phase 4 late-import workarounds are no longer necessary:
        # this body now lives inside the worker class, so we can call
        # the sibling apply methods directly on ``self``.
        if self.network.provide_dhcp or self.network.provide_dns:
            self._apply_remove_dnsmasq()

    def _apply_enable_nat(self) -> None:
        """Install the masquerade rules for the wrapped network.

        Lifted from ``Network.enable_nat`` (network.py:873-877). The
        ``if not config.NODE_IS_NETWORK_NODE: return`` guard from the
        original is dropped: this worker class is only ever instantiated
        inside the dispatcher, which only runs on the elected network
        node for the tasks that invoke this method.
        """
        self._require_network_node('_apply_enable_nat')

        util_concurrency.enable_nat(
            self.network.uuid, self.network.network_address,
            self.network.netmask, self.network.vxid)
