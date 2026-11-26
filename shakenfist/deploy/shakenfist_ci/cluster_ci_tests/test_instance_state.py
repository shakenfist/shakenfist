# Copyright 2026 Michael Still and contributors
from shakenfist_ci import base
from shakenfist_ci import instance_state


class TestInstanceDeleteRemovesHostState(
        instance_state.InstanceDiskStateTestsMixin,
        base.BaseNamespacedTestCase):
    """The multi-node run of the hypervisor instance state assertion.

    The test body comes from InstanceDiskStateTestsMixin and also runs in
    the smoke suite, where a break is cheap to find; this class is what
    runs it on the topologies where the chosen hypervisor is a machine
    the suite is not running on, so every read of its disk and its
    libvirt goes over the mesh.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'instdiskstatecluster'
        super().__init__(*args, **kwargs)
