# Copyright 2026 Michael Still and contributors
from shakenfist_ci import base
from shakenfist_ci import repeated_delete


class TestRepeatedDelete(repeated_delete.RepeatedDeleteTestsMixin,
                         base.BaseNamespacedTestCase):
    """A repeated DELETE on the multi-node topologies.

    The test body is shared with the smoke suite, which is where it
    first runs; this subclass keeps it running against a cluster, where
    the delete has network cleanup on a hypervisor which is not the
    network node to do as well.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'repeatdel'
        super().__init__(*args, **kwargs)
