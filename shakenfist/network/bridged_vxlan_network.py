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
``_apply_unroute_address`` and ``_apply_remove_nat``. The remaining
``_apply_*`` methods (dnsmasq, lifecycle, ...) move across in later
phases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shakenfist_utilities import logs  # noreorder

from shakenfist import instance
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist.node import Node
from shakenfist.util import concurrency as util_concurrency

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

    def _apply_ensure_mesh(self) -> None:
        """Ensure the VXLAN FDB mesh for the wrapped network is correct.

        Lifted verbatim (modulo ``self`` -> ``self.network``) from the
        original ``Network.ensure_mesh()`` body. The `get_lock` wrapper is
        retained here for now; Phase 8 removes it once the per-node queue
        is the sole serialisation point for in-worker mutations.

        Raises the same typed exceptions as the original implementation:
        ``EnsureMeshFailed`` (from the underlying privexec layer) and
        ``DeadNetwork`` (if the network has been torn down out from under
        us).
        """
        # The original method was decorated with `_not_on_floating_network`
        # which short-circuits when invoked on the floating network. We
        # preserve that semantics inline here rather than copying the
        # decorator definition into this module -- the check is one line
        # and inlining keeps `BridgedVXLanNetwork` independent of the
        # private decorator name in `network.py`.
        if self.network.uuid == FLOATING_NETWORK_UUID:
            return

        with self.network.get_lock(
                op='Network ensure mesh', global_scope=False):
            # Determine which IPs should be on this mesh and where
            instances = []
            for ni in self.network.networkinterfaces:
                if ni.instance_uuid not in instances:
                    instances.append(ni.instance_uuid)

            node_fqdns = []
            for inst_uuid in instances:
                inst = instance.Instance.from_db(inst_uuid)
                if not inst:
                    continue
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
        The existing ``get_lock`` wrapper is preserved (Phase 8 removes it).
        """
        with self.network.get_lock(
                op='Network add floating IP', global_scope=False):
            util_concurrency.add_floating_ip(
                str(self.network.uuid), floating_address, inner_address)

    def _apply_remove_floating_ip(
            self, floating_address: str, inner_address: str) -> None:
        """Remove a floating IP mapping on the network node.

        Lifted from ``Network.remove_floating_ip`` (network.py:923-934). The
        ``affected_objects`` parameter and the multi-target audit event are
        stripped for the same reason as ``_apply_add_floating_ip``. The
        ``inner_address`` argument is retained on the signature for symmetry
        with the add case (the underlying privexec helper only needs the
        floating address, but dispatchers pass both for event-emission
        purposes).
        """
        with self.network.get_lock(
                op='Network remove floating IP', global_scope=False):
            util_concurrency.remove_floating_ip(
                str(self.network.uuid), floating_address)

    def _apply_route_address(self, ip: str) -> None:
        """Add a host route for a floating IP onto the network's vx bridge.

        Lifted from ``Network.route_address`` (network.py:938-947). The
        single-target audit event on the wrapped network is preserved here
        (it is not multi-target, so no dispatcher fan-out is required).
        """
        self.network.add_event(
            EVENT_TYPE_AUDIT, 'routing floating ip to network',
            extra={'floating': ip})
        subst = self.network.subst_dict()
        subst['floating_address'] = ip
        with self.network.get_lock(
                op='Network route address', global_scope=False):
            util_concurrency.execute(
                'ip route add %(floating_address)s/32 dev %(vx_bridge)s'
                % subst)

    def _apply_unroute_address(self, ip: str) -> None:
        """Remove a host route for a floating IP from the network's vx bridge.

        Lifted from ``Network.unroute_address`` (network.py:950-960). As with
        ``_apply_route_address``, the single-target audit event on the
        wrapped network is preserved.
        """
        self.network.add_event(
            EVENT_TYPE_AUDIT, 'unrouting floating ip to network',
            extra={'floating': ip})
        subst = self.network.subst_dict()
        subst['floating_address'] = ip
        with self.network.get_lock(
                op='Network unroute address', global_scope=False):
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
        branch only. The ``get_lock`` wrapper is preserved.
        """
        with self.network.get_lock(
                op='Network remove NAT', global_scope=False):
            if self.network.floating_gateway:
                self.network.unassign_floating_gateway()
