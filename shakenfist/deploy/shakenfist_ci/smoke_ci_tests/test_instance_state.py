# Copyright 2026 Michael Still and contributors
from shakenfist_ci import base
from shakenfist_ci import instance_state


class TestInstanceDiskState(instance_state.InstanceDiskStateTestsMixin,
                            base.BaseNamespacedTestCase):
    """The hypervisor instance state assertion, on one node.

    This is in the smoke suite because it needs only one UEFI instance
    and host access to the hypervisor it is pinned to, which the single
    machine topology has, and because the cluster suite runs for the
    first time in the merge queue -- too late to learn that the
    assertion itself is broken.

    The test body lives in InstanceDiskStateTestsMixin; the cluster suite
    subclasses it too, so the multi-node topologies keep their coverage.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'instdiskstate'
        super().__init__(*args, **kwargs)
