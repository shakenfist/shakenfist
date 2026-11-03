# Copyright 2026 Michael Still and contributors
from shakenfist_ci import base
from shakenfist_ci import repeated_delete


class TestRepeatedDelete(repeated_delete.RepeatedDeleteTestsMixin,
                         base.BaseNamespacedTestCase):
    """A repeated DELETE on the single node topology.

    The test body lives in RepeatedDeleteTestsMixin; the cluster suite
    subclasses it too, so the multi-node topologies keep the coverage.
    It is in the smoke suite because the race it asserts about needs
    nothing but one instance, and the cluster suite runs for the first
    time in the merge queue.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'repeatdel'
        super().__init__(*args, **kwargs)
