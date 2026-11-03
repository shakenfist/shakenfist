# Copyright 2026 Michael Still and contributors
from shakenfist_ci import base
from shakenfist_ci import dhcp_state


class TestDhcpState(dhcp_state.DhcpStateTestsMixin,
                    base.BaseNamespacedTestCase):
    """The network node dnsmasq state assertion, on one node.

    This is in the smoke suite because it needs only one DHCP network
    and host access to the network node, which the single machine
    topology has, and because the cluster suite runs for the first time
    in the merge queue -- too late to learn that the assertion itself is
    broken.

    The test body lives in DhcpStateTestsMixin; the cluster suite
    subclasses it too, so the multi-node topologies keep their coverage.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'dhcpstate'
        super().__init__(*args, **kwargs)
