import json
import time

from testtools import content

from shakenfist_ci import base


class TestNetworkPlumbingLifecycle(base.BaseNamespacedTestCase):
    """A network's host plumbing must appear only where needed and be
    fully removed once it is not.

    A virtual network's VXLAN plumbing (br-vxlan-<hex>, where <hex> is the
    network's vxlan id) is created on a node when that node first hosts an
    instance on the network, and always on the network node (for
    DHCP/NAT). Deleting the last instance on a hypervisor must tear that
    plumbing back down, and deleting the network must remove it
    everywhere. Plumbing stranded on a drained hypervisor is a leak, and a
    different leak class from the floating IP host state -- this test
    exists to catch it.

    Precise invariant: a network is present on a node iff that node hosts
    an instance on it, or the node is the network node.

    The per-node assertions run over the mesh via the node-exec helper.
    The test skips loudly if the nodes cannot be reached, or if the
    cluster lacks two hypervisors distinct from the network node (which it
    needs to keep the presence assertions sharp). See
    docs/plans/PLAN-ci-node-exec-assertions.md.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'netlifecycle'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()

        self.network_node = self._network_node()
        self._require_node_exec(self.network_node)

        # Two hypervisors that are *not* the network node: after teardown
        # the network must vanish from both while still present on the
        # network node, which we cannot distinguish if an instance lands
        # on the network node itself (it always carries the network).
        candidates = self._hypervisor_nodes(exclude_network_node=True)
        if len(candidates) < 2:
            self.skipTest(
                'Need at least two hypervisors distinct from the network '
                'node; cluster reports %d.' % len(candidates))
        self.hypervisors = candidates[:2]
        for node in self.hypervisors:
            self._require_node_exec(node)

        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def _vx_bridge_name(self):
        return 'br-vxlan-%06x' % self.net['vxlan_id']

    def _network_present_on(self, node):
        return self._vx_bridge_name() in self._node_link_names(node)

    def _await_network_presence(self, node, present, timeout=180):
        # Network node reconcile (bridge teardown when the last instance
        # leaves) is asynchronous, so poll rather than checking once.
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._network_present_on(node) == present:
                return
            time.sleep(5)
        self.fail(
            'Timed out waiting for network %s to be %s on node %s'
            % (self.net['uuid'], 'present' if present else 'absent',
               node['name']))

    def _create_instance_on(self, node):
        inst = self.test_client.create_instance(
            'netlifecycle', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ],
            None, None, force_placement=node['name'])
        self.addDetail(
            'instance on %s' % node['name'],
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self.assertIsNotNone(inst['uuid'])
        # The scheduler must have honoured the requested placement, else
        # the presence assertions below would be testing the wrong nodes.
        self.assertEqual(node['uuid'], inst['node'])
        self._await_instance_ready(inst['uuid'])
        return inst

    def test_network_plumbing_lifecycle(self):
        node_a, node_b = self.hypervisors

        # Before any instance the network's plumbing exists on neither
        # hypervisor.
        self.assertFalse(
            self._network_present_on(node_a),
            'network present on %s before any instance' % node_a['name'])
        self.assertFalse(
            self._network_present_on(node_b),
            'network present on %s before any instance' % node_b['name'])

        inst_a = self._create_instance_on(node_a)
        inst_b = self._create_instance_on(node_b)

        # Hosting an instance brings the network's plumbing up on each
        # hypervisor.
        self._await_network_presence(node_a, True)
        self._await_network_presence(node_b, True)

        # Deleting the instances drains the plumbing from the hypervisors,
        # while the network node keeps it (the network still exists).
        self.test_client.delete_instance(inst_a['uuid'])
        self.test_client.delete_instance(inst_b['uuid'])
        self._await_instance_deleted(inst_a['uuid'])
        self._await_instance_deleted(inst_b['uuid'])

        self._await_network_presence(node_a, False)
        self._await_network_presence(node_b, False)
        self._await_network_presence(self.network_node, True)

        # Deleting the network removes it everywhere, including the network
        # node.
        self.test_client.delete_network(self.net['uuid'])
        self._await_network_presence(self.network_node, False)
        self._await_network_presence(node_a, False)
        self._await_network_presence(node_b, False)
