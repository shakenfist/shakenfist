# Copyright 2026 Michael Still and contributors

"""Which long-running jobs sf-net starts on a node.

The stray interface reaper is a cluster-wide sweep with no node-local state,
so running it on every node repeated it once per node (#4072).
"""

from unittest import mock

from shakenfist.daemons.network import main as network_main
from shakenfist.daemons.network import stray_nics
from shakenfist.tests import base


class NodeJobClassesTestCase(base.ShakenFistTestCase):
    def _classes(self, is_network_node):
        with mock.patch.object(network_main.config, 'NODE_IS_NETWORK_NODE', is_network_node):
            return network_main.node_job_classes()

    def test_the_network_node_reaps_stray_interfaces(self):
        self.assertIs(stray_nics.Job, self._classes(True)['stray-nics'])

    def test_other_nodes_do_not(self):
        self.assertNotIn('stray-nics', self._classes(False))

    def test_the_per_node_jobs_run_everywhere(self):
        for is_network_node in (True, False):
            self.assertLessEqual(
                {'fip-reaper', 'maintain', 'mtus', 'net-worker'},
                set(self._classes(is_network_node)))
