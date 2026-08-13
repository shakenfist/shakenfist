import json

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


class TestNodes(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'nodes'
        super().__init__(*args, **kwargs)

    def test_get_node(self):
        # I know this is a bit weird and is just testing if both calls return
        # the same name, but what its _really_ doing is ensuring the get_nodes()
        # call returns at all.
        nodes = self.system_client.get_nodes()
        self.addDetail('nodes', content.text_content(json.dumps(
            nodes, indent=4, sort_keys=True)))
        n = self.system_client.get_node(nodes[0]['name'])
        self.addDetail('n', content.text_content(json.dumps(
            n, indent=4, sort_keys=True)))
        self.assertEqual(nodes[0]['name'], n['name'])

    def test_get_missing_node(self):
        self.assertRaises(
            apiclient.ResourceNotFoundException, self.system_client.get_node,
            'banana')

    def test_cluster_resources(self):
        # Regression test for the /admin/resources endpoint
        # (AdminResourcesEndpoint). It was historically defined but never
        # registered as a route, so it always returned a 404. The CI readiness
        # gate and the scheduler both depend on it accurately reporting which
        # hypervisors are schedulable, so ensure it stays wired up and reports
        # at least one schedulable hypervisor with capacity. A node only
        # appears in per_node once it is active and reporting fresh metrics,
        # which is exactly what prevents the cold-start 507 "No nodes remaining
        # at scheduling stage is_hypervisor" race.
        resources = self.system_client.get_cluster_resources()
        self.addDetail('resources', content.text_content(json.dumps(
            resources, indent=4, sort_keys=True)))
        self.assertIn('total', resources)
        self.assertIn('per_node', resources)
        self.assertGreaterEqual(len(resources['per_node']), 1)
        self.assertGreater(resources['total']['cpu_available'], 0)

    def test_cluster_resources_reservations(self):
        # The resources daemon publishes reservation-adjusted capacity
        # (cpu_schedulable, memory_reserved_mb) and summarize_resources()
        # reports it per node with the same arithmetic admission uses. Raw
        # node_metrics rows are not exposed over REST, so /admin/resources
        # is the surface this asserts against.
        nodes = self.system_client.get_nodes()
        self.addDetail('nodes', content.text_content(json.dumps(
            nodes, indent=4, sort_keys=True)))
        resources = self.system_client.get_cluster_resources()
        self.addDetail('resources', content.text_content(json.dumps(
            resources, indent=4, sort_keys=True)))

        infra_schedulable = []
        plain_schedulable = []
        for node in nodes:
            per_node = resources['per_node'].get(node['uuid'])
            if not per_node:
                # Nodes without fresh metrics (or non-hypervisors) do not
                # appear in per_node.
                continue

            self.assertIn('cpu_schedulable', per_node)
            self.assertGreaterEqual(per_node['cpu_schedulable'], 1)
            self.assertIn('memory_reserved_mb', per_node)
            self.assertGreater(per_node['memory_reserved_mb'], 0)

            if node.get('is_network_node') or node.get('is_database_node'):
                infra_schedulable.append(per_node['cpu_schedulable'])
            else:
                plain_schedulable.append(per_node['cpu_schedulable'])

        # Cluster CI nodes are identically sized VMs, so a hypervisor
        # carrying an infra role (which reserves an extra core) must never
        # offer more schedulable threads than a plain hypervisor. Equality
        # is tolerated: if the guest topology defeats psutil's physical
        # core detection the daemon publishes no reservation fields and
        # every node falls back to the same synthetic sizing, and on very
        # small nodes both sizes can floor at the same value. Skip the
        # comparison entirely on topologies without both kinds of node.
        if infra_schedulable and plain_schedulable:
            self.assertLessEqual(
                max(infra_schedulable), min(plain_schedulable))

    def test_cluster_resources_charges_unbooted_placements(self):
        # A node's cpu_total_instance_vcpus metric counts only *running*
        # libvirt domains and is republished once a minute, so an instance
        # which has been placed but has not booted is invisible to it. If
        # admission trusted that measurement alone, a burst of creates
        # would all see the same idle node, all land on it, and push it
        # well past its hard maximum -- after which every later request
        # naming that node is refused with a 507 (issue 3498). Placement
        # must be charged immediately, which /admin/resources exposes as
        # cpu_committed.
        resources = self.system_client.get_cluster_resources()
        candidates = [
            n for n in self._hypervisor_nodes()
            if resources['per_node'].get(n['uuid'], {}).get(
                'cpu_available', 0) >= 2]
        if not candidates:
            self.skipTest('No hypervisor with two vCPUs of headroom')
        node = max(
            candidates,
            key=lambda n: resources['per_node'][n['uuid']]['cpu_available'])

        # A one vCPU instance with an empty disk and no base image: nothing
        # is downloaded, so this costs the cluster almost nothing, and we
        # deliberately do not wait for it -- the whole point is to read the
        # cluster's view of the node before any domain exists.
        inst = self.test_client.create_instance(
            'unbooted', 1, 128, None, [{'size': 1, 'type': 'disk'}],
            None, None, force_placement=node['name'])
        self.addDetail('instance', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        self.assertEqual(node['uuid'], inst['node'])

        try:
            resources = self.system_client.get_cluster_resources()
            self.addDetail('resources after', content.text_content(json.dumps(
                resources, indent=4, sort_keys=True)))
            per_node = resources['per_node'][node['uuid']]

            # Our instance is placed here and not deleted, so the node's
            # committed total must account for at least its one vCPU
            # whatever else the rest of the suite is doing concurrently.
            self.assertGreaterEqual(per_node['cpu_committed'], 1)

            # The published headroom is what admission will actually
            # honour: the hard maximum less whichever of the measurement
            # and the placement ledger is binding.
            self.assertEqual(
                per_node['cpu_hard_max'] - max(
                    per_node['cpu_measured'], per_node['cpu_committed']),
                per_node['cpu_available'])
        finally:
            self.test_client.delete_instance(inst['uuid'])
