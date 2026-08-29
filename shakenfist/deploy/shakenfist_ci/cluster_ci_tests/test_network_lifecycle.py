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
        # The scheduler must have honoured the requested placement (resolved
        # synchronously at create time), else the presence assertions below
        # would be testing the wrong nodes.
        self.assertEqual(node['uuid'], inst['node'])
        # The create response is built from the API process's in-memory
        # object; a fresh GET hydrates from the database. The requested
        # placement must survive that round trip, because it is what
        # preflight's honour-or-error guard reads -- when it was silently
        # dropped at the database write, a preflight capacity blip
        # redirected a targeted create to the other hypervisor and the
        # node assertion above flaked (issue 3496).
        fetched = self.test_client.get_instance(inst['uuid'])
        self.assertEqual(node['uuid'], fetched.get('requested_placement'))
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

        # Create both instances up front so they build concurrently on their
        # respective nodes. This test only needs the network plumbing applied,
        # not a booted guest, so we wait for the 'created' state (by which
        # point libvirt has attached the instance's tap to the vxlan bridge)
        # rather than the much slower agent/cloud-init readiness.
        inst_a = self._create_instance_on(node_a)
        inst_b = self._create_instance_on(node_b)
        self._await_instance_create(inst_a['uuid'])
        self._await_instance_create(inst_b['uuid'])

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


class TestNetworkDeleteReleasesFloatingGateway(base.BaseNamespacedTestCase):
    """A deleted network must never still hold a floating gateway.

    A NAT network reserves a gateway address on the floating network for
    the lifetime of the network. The delete path must release that
    reservation before it publishes the "deleted" state, because the
    floating IP reaper considers a gateway reservation whose owning
    network is deleted to be a leak: any window between the two lets a
    reaper pass release the address out from under the still running
    teardown and log a healthy address as leaked. That was github issue
    #3645, which fired roughly fortnightly on a busy CI cluster.

    This needs no host level access, only the API, so it does not use the
    node-exec helpers.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'netfloatgw'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()

        # provide_nat must be true, it is what causes a floating gateway
        # to be reserved at all.
        self.net = self.test_client.allocate_network(
            '192.168.243.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def test_delete_releases_floating_gateway_before_deleted(self):
        n = self.test_client.get_network(self.net['uuid'])
        self.assertIsNotNone(
            n.get('floating_gateway'),
            'NAT network %s never acquired a floating gateway'
            % self.net['uuid'])

        self.test_client.delete_network(self.net['uuid'])

        start_time = time.time()
        while time.time() - start_time < 300:
            n = self.test_client.get_network(self.net['uuid'])
            if n['state'] == 'deleted':
                self.addDetail(
                    'deleted network',
                    content.text_content(
                        json.dumps(n, indent=4, sort_keys=True)))
                self.assertIsNone(
                    n.get('floating_gateway'),
                    'Network %s still held floating gateway %s while in the '
                    'deleted state' % (self.net['uuid'],
                                       n.get('floating_gateway')))
                return
            time.sleep(1)

        self.fail('Network %s was never deleted' % self.net['uuid'])
