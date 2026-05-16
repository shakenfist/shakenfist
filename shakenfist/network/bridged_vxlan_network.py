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

Phase 2 implements ``_apply_ensure_mesh`` only; the other ``_apply_*``
methods (floating IP, dnsmasq, lifecycle, ...) move across in later phases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shakenfist_utilities import logs  # noreorder

from shakenfist import instance
from shakenfist.config import config
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
