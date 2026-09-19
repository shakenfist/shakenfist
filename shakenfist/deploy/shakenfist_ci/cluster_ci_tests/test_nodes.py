import json
import time

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

            # The reservation clamps must not engage silently (issue
            # 4201): every node reports whether its published values are
            # a clamp floor or cap rather than the configured
            # reservation's arithmetic. Whether a clamp is engaged is a
            # topology choice, so only consistency is asserted: a node
            # whose thread reservation was clamped is publishing the
            # floor of one schedulable thread.
            self.assertIn('cpu_reservation_clamped', per_node)
            self.assertIn(per_node['cpu_reservation_clamped'], [True, False])
            self.assertIn('memory_reservation_clamped', per_node)
            self.assertIn(
                per_node['memory_reservation_clamped'], [True, False])
            if per_node['cpu_reservation_clamped']:
                self.assertEqual(1, per_node['cpu_schedulable'])

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
        # libvirt domains, so an instance which has been placed but has not
        # booted is invisible to it -- and stays invisible however promptly
        # the metric is republished, which since
        # PLAN-transient-capacity-refusals phase 3 is within about five
        # seconds of the running-domain set changing. If
        # admission trusted that measurement alone, a burst of creates
        # would all see the same idle node, all land on it, and push it
        # well past its hard maximum -- after which every later request
        # naming that node is refused with a 507 (issue 3498). Placement
        # must be charged immediately, which /admin/resources exposes as
        # cpu_committed -- read from the scheduler_node_capacity counters
        # the placement drew down inside its admission transaction.
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
        inst = self.create_instance(
            'unbooted', 1, 128, None, [{'size': 1, 'type': 'disk'}],
            None, None, force_placement=node['name'])
        self.addDetail('instance', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        self.assertEqual(node['uuid'], inst['node'])

        try:
            # A node the capacity reconciler has not written a row for is
            # admitted unguarded (P7): every placement onto it fails open
            # and the node's ledger cannot be trusted. Since
            # PLAN-transient-capacity-refusals phase 1, the elected loop's
            # maintenance pass forces a reconcile within about a minute of
            # any hypervisor's metrics becoming fresh
            # (`_force_capacity_reconcile_if_unguarded()`).
            #
            # That minute is why this waits rather than asserting on the
            # first read. The readiness gate which runs before the
            # functional suite (`tools/ci_wait_schedulable.py`) waits for
            # a node to appear in per_node, which means active and
            # publishing fresh metrics -- it says nothing about a
            # scheduler_node_capacity row, so clearing that gate does not
            # mean the row exists yet. Waiting out a window longer than
            # the warm-up one still catches the regression this phase
            # exists to prevent (a row which never arrives at all) while
            # not failing on the warm-up itself.
            deadline = time.time() + 120
            while True:
                resources = self.system_client.get_cluster_resources()
                per_node = resources['per_node'].get(node['uuid'])
                if per_node is not None and per_node.get(
                        'cpu_committed_row_present', True):
                    break
                if time.time() > deadline:
                    break
                time.sleep(5)

            self.addDetail('resources after', content.text_content(json.dumps(
                resources, indent=4, sort_keys=True)))

            # A hypervisor which drops out of per_node entirely has stale
            # metrics or an overlong queue, which is a different failure
            # from an unguarded one and deserves to say so.
            self.assertIsNotNone(
                per_node,
                'Node %s vanished from /admin/resources per_node while an '
                'instance was placed on it' % node['uuid'])
            self.assertTrue(
                per_node.get('cpu_committed_row_present', True),
                'Node %s is still admitting placements unguarded two '
                'minutes after placement: it has no scheduler_node_capacity '
                'row, so the capacity reconciler has not sized it yet'
                % node['uuid'])

            # Our instance is placed here and not deleted, so the node's
            # committed total must account for at least its one vCPU
            # whatever else the rest of the suite is doing concurrently.
            self.assertGreaterEqual(per_node['cpu_committed'], 1)

            # The same capacity row publishes a disk ceiling and ledger
            # (issue 4208): disk_available alone is headroom, which moves
            # under concurrent load, so it cannot answer "what could this
            # node ever accept?". Our placement charges its 1 GB disk, so
            # the ledger accounts for at least that; the limit is the
            # row's own, never a live-derived fallback.
            self.assertIsNotNone(per_node['disk_limit_gb'])
            self.assertGreaterEqual(per_node['disk_committed_gb'], 1)
            self.assertGreaterEqual(
                per_node['disk_limit_gb'], per_node['disk_committed_gb'])

            # The published headroom is what admission will actually
            # honour: the hard maximum less whichever of the measurement
            # and the placement ledger is binding.
            self.assertEqual(
                per_node['cpu_hard_max'] - max(
                    per_node['cpu_measured'], per_node['cpu_committed']),
                per_node['cpu_available'])
        finally:
            self.test_client.delete_instance(inst['uuid'])

    def _safe_delete_instance(self, instance_uuid):
        """Delete an instance, tolerating if it's already gone."""
        try:
            self.system_client.delete_instance(instance_uuid)
        except apiclient.ResourceNotFoundException:
            pass

    def test_cluster_resources_measured_drops_after_delete(self):
        # Regression coverage for PLAN-transient-capacity-refusals-phase-03
        # ("metrics on change"). The CPU pre-filter charges
        # max(cpu_measured, cpu_committed): cpu_committed is released
        # inside the delete transaction, but cpu_measured used to be
        # published on a flat 60 s cadence, so a node kept refusing new
        # work for up to a minute after its last instance was deleted even
        # though the ledger had already cleared. sf-resources now
        # republishes within about 5 s of the active-domain set changing,
        # which this asserts against the same /admin/resources fields
        # test_cluster_resources_reservations() does above -- raw
        # node_metrics rows are not exposed over REST.
        resources = self.system_client.get_cluster_resources()
        candidates = [
            n for n in self._hypervisor_nodes()
            if resources['per_node'].get(n['uuid'], {}).get(
                'cpu_available', 0) >= 1]
        if not candidates:
            self.skipTest('No hypervisor with a vCPU of headroom')
        # The emptiest, not the first, for the same reason
        # test_cluster_resources_charges_unbooted_placements above picks
        # that way: the suite runs several tests at once, and the node with
        # the most headroom is the one least likely to have a sibling's
        # instance arrive on it while this test is watching.
        node = max(
            candidates,
            key=lambda n: resources['per_node'][n['uuid']]['cpu_available'])

        # What the node measured before this test put anything on it.
        # Taken here rather than after the create, because the rise below
        # has to be measured against a reading which certainly predates
        # the new domain.
        idle_measured = resources['per_node'][node['uuid']]['cpu_measured']

        # The instance is pinned so the node this test watches is known
        # in advance -- the pin is the assertion's subject (which node's
        # cpu_measured to read), not a workaround for capacity. The node
        # was already chosen above for having room for one more vCPU, so
        # the pin is not doing any capacity work either.
        cpus = 1
        inst = self.create_instance(
            'metrics-drop', cpus, 128, None, [{'size': 1, 'type': 'disk'}],
            None, None, force_placement=node['name'])
        self.addCleanup(self._safe_delete_instance, inst['uuid'])
        self.addDetail('instance', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        self.assertEqual(node['uuid'], inst['node'])

        # cpu_measured counts running libvirt domains, not placements
        # (that is what test_cluster_resources_charges_unbooted_placements
        # above exercises), so the domain has to actually start before a
        # baseline read of it means anything.
        self._await_instance_create(inst['uuid'])

        # ...and starting is not enough either. _await_instance_create()
        # returns as soon as the instance reaches 'created', which
        # Instance.create() sets immediately after power_on(), while the
        # figure read here has to travel through a five second domain poll,
        # a publish, and the scheduler's SCHEDULER_CACHE_TIMEOUT cache. A
        # baseline taken before that arrives is the node's *idle*
        # measurement, the threshold below it is one the node can never
        # return to, and the test then fails at its deadline for the
        # opposite of the reason it exists.
        #
        # So wait for the rise first. That wait is not overhead: a
        # measurement which follows a domain starting is the same claim as
        # one which follows a domain going away, so this asserts the
        # phase's behaviour in the other direction, and says so distinctly
        # when it is publish-on-start that is broken.
        baseline_measured = self._await_cpu_measured(
            node['uuid'], lambda measured: measured >= idle_measured + cpus,
            'rise to at least %d, including the %d vCPUs of an instance '
            'which has started' % (idle_measured + cpus, cpus))
        self.addDetail('resources before delete', content.text_content(
            json.dumps(self.system_client.get_cluster_resources(),
                       indent=4, sort_keys=True)))

        # self.system_client uses ASYNC_PAUSE, so this blocks until the
        # instance's state is 'deleted' -- the 20 s poll below starts
        # counting from that point, not from when the request was issued.
        self.system_client.delete_instance(inst['uuid'])

        # A drop by the instance's vCPUs rather than a drop to zero,
        # because the node is not assumed to be otherwise idle -- this
        # suite's cluster is shared and the node may be carrying load this
        # test did not create. It does not make the assertion immune to a
        # sibling *arriving* mid-window, which would raise cpu_measured by
        # its own vCPUs and could hold the value above the threshold; the
        # emptiest-node choice above reduces that, and it remains the one
        # known flake source here.
        self._await_cpu_measured(
            node['uuid'], lambda measured: measured <= baseline_measured - cpus,
            'fall to at most %d, by the %d vCPUs of a deleted instance'
            % (baseline_measured - cpus, cpus))
        self.addDetail('resources after delete', content.text_content(
            json.dumps(self.system_client.get_cluster_resources(),
                       indent=4, sort_keys=True)))

    # 20 s, not 5: the claim under test is "seconds, not a minute", and
    # this leaves margin for a loaded CI node rather than being the
    # tightest bound that could pass -- four 5 s domain polls plus a
    # publish plus the scheduler's cache. Before this phase either wait
    # could take 60 s.
    CPU_MEASURED_DEADLINE_SECONDS = 20
    CPU_MEASURED_POLL_SECONDS = 5

    def _await_cpu_measured(self, node_uuid, predicate, expectation):
        """Wait for a node's published cpu_measured to satisfy predicate.

        Returns the value which satisfied it. ``expectation`` completes
        the sentence "cpu_measured did not ..." in the failure message, so
        a broken publish-on-start and a broken publish-on-delete do not
        report identically.
        """
        deadline = time.time() + self.CPU_MEASURED_DEADLINE_SECONDS
        measured = None
        while True:
            resources = self.system_client.get_cluster_resources()
            per_node = resources['per_node'].get(node_uuid)
            self.assertIsNotNone(
                per_node,
                'Node %s is absent from /admin/resources per_node'
                % node_uuid)
            measured = per_node['cpu_measured']
            if predicate(measured):
                return measured

            if time.time() > deadline:
                break
            time.sleep(self.CPU_MEASURED_POLL_SECONDS)

        self.fail(
            'Node %s publishes cpu_measured %r, which did not %s within '
            '%ds. The measurement should follow the running-domain set '
            'within seconds, not within a minute.'
            % (node_uuid, measured, expectation,
               self.CPU_MEASURED_DEADLINE_SECONDS))
