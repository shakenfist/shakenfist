import json
import time

from testtools import content

from shakenfist_ci import accounting
from shakenfist_ci import base
from shakenfist_ci import retries
from shakenfist_ci import sizing
from shakenfist_client import apiclient


# How long a reading below the structural minimum is waited out before it is
# believed. The CI sizing baseline measured a contiguous prefix of 9 to 14
# samples -- 135 to 210 seconds -- at the start of every one of its 204
# job-runs in which every node's capacity row read absent at once; separately,
# a node which has not yet published fresh metrics is missing from
# /admin/resources altogether. Both make a young cluster read smaller than it
# is and both resolve on their own, so the assertion must not be able to fire
# inside that window. Comfortably longer than the longest prefix measured and
# far shorter than the job timeout, at the cost that a genuinely undersized
# cloud takes this long to say so.
#
# Deliberately the same number as base.CLUSTER_HEADROOM_WAIT and not
# derived from it: that one is how long a 507 is waited out before a
# create is believed to have failed, this one is how long a small
# reading is waited out before the topology is believed. They agree
# today by coincidence of scale rather than by construction, so they
# are free to move apart -- but a change to either is worth a look at
# the other, which is why each now names the one it is not.
STRUCTURAL_MINIMUM_WAIT = 420

# The node states which can run instances, from
# shakenfist.constants.NODE_ACTIVE_STATES (which is also what
# prefilter='active' resolves to). Repeated here rather than imported because
# this suite runs from an installed shakenfist_ci package against a remote
# cluster and cannot import the server's constants module.
NODE_ACTIVE_STATES = frozenset({'initial', 'created', 'degraded'})

# How long to wait for the capacity reconciler to write the cluster_capacity
# row before believing it is absent. The elected loop's maintenance pass
# forces a reconcile within about a minute of hypervisors becoming fresh, so
# this is several times that.
CLUSTER_CAPACITY_ROW_WAIT = 300

# The closed set of reasons /admin/resources gives for a node missing from
# per_node, from shakenfist.scheduler.EXCLUSION_REASONS. Clients match on these
# exact strings, so a new one is an API change this test should notice.
EXCLUSION_REASONS = frozenset({
    'queue too long', 'stale metrics', 'no metrics', 'metrics unreadable'})

# How long a hypervisor's published measurement is given to agree with
# the domains libvirt has on it, in test_node_accounting_agrees_with_libvirt.
# sf-resources looks at the domain set every DOMAIN_POLL_INTERVAL_SECONDS
# (5) and publishes on a change plus one follow-up poll, so a change is
# published within about 10 seconds and the interval tick (60) is the
# worst case for a publish which somehow missed the change. This is many
# times that, because the wait is not only for the publish: the cluster is
# shared, so a round is also spent whenever a sibling's instance starts or
# stops on the node mid-sample. Short enough that a node which really has
# drifted says so well inside the job timeout.
LIBVIRT_RECONCILIATION_WAIT = 180
LIBVIRT_RECONCILIATION_POLL = 5

# The memory each instance that test asks for. Small, because what the
# instances are for is to move the accounting rather than to run
# anything, and two of them have to fit on one node beside whatever the
# rest of the suite is doing.
ACCOUNTING_INSTANCE_MEMORY_MB = 128

CLUSTER_CAPACITY_COUNTERS = [
    '%s_%s' % (kind, dim)
    for kind in ('total', 'claimed', 'unclaimed_used')
    for dim in ('cpus', 'memory_mb', 'disk_gb')]


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

    def test_cluster_resources_capacity_surface(self):
        # /admin/resources publishes the cluster_capacity ledger and the
        # reason each unusable node is missing from per_node. Neither is
        # visible anywhere else over REST.
        #
        # The ledger row is written by the capacity reconciler. A young
        # cluster reads row_present false until the forced reconcile has run
        # (see test_cluster_resources_reservations), so wait for the row the
        # same way that test waits for a node's row: poll the endpoint
        # itself, since there is no other signal to wait on.
        deadline = time.time() + CLUSTER_CAPACITY_ROW_WAIT
        while True:
            nodes_before = self.system_client.get_nodes()
            resources = self.system_client.get_cluster_resources()
            nodes_after = self.system_client.get_nodes()
            capacity = resources.get('cluster_capacity') or {}
            if capacity.get('row_present') or time.time() > deadline:
                break
            time.sleep(5)

        self.addDetail('nodes before', content.text_content(json.dumps(
            nodes_before, indent=4, sort_keys=True)))
        self.addDetail('nodes after', content.text_content(json.dumps(
            nodes_after, indent=4, sort_keys=True)))
        self.addDetail('resources', content.text_content(json.dumps(
            resources, indent=4, sort_keys=True)))

        self.assertIn('cluster_capacity', resources)
        self.assertIs(
            True, capacity.get('row_present'),
            'cluster_capacity has no row %d seconds into the run, so the '
            'capacity reconciler has not written one'
            % CLUSTER_CAPACITY_ROW_WAIT)
        self.assertIs(False, capacity.get('degraded'))

        def is_int(value):
            return isinstance(value, int) and not isinstance(value, bool)

        for name in CLUSTER_CAPACITY_COUNTERS:
            self.assertTrue(
                is_int(capacity.get(name)),
                '%s is not an int: %r' % (name, capacity.get(name)))
        for dim in ('cpus', 'memory_mb', 'disk_gb'):
            name = 'unallocated_%s' % dim
            self.assertTrue(
                is_int(capacity.get(name)),
                '%s is not an int: %r' % (name, capacity.get(name)))
            self.assertEqual(
                capacity['total_%s' % dim]
                - capacity['claimed_%s' % dim]
                - capacity['unclaimed_used_%s' % dim],
                capacity[name],
                '%s is not total - claimed - unclaimed_used: %s'
                % (name, json.dumps(capacity, sort_keys=True)))
        updated_at = capacity.get('updated_at')
        self.assertTrue(
            isinstance(updated_at, (int, float))
            and not isinstance(updated_at, bool),
            'updated_at is not a number: %r' % (updated_at,))

        # Every active hypervisor is either schedulable (in per_node) or
        # explained (in excluded), and never both. Non-hypervisors with
        # unusable metrics also appear in excluded, so the nodes list is
        # what says which entries are owed.
        #
        # get_nodes() and get_cluster_resources() are separate requests, so
        # a node can change state between them: a node which has just
        # joined, or just been marked missing, is judged by a state the
        # resources response did not see. Only nodes which are active
        # hypervisors in the list read both before and after the resources
        # request are held to the rule, since those cannot have changed
        # state across it (barring a flap, which the cluster does not
        # produce on a healthy run). A node outside that set is not
        # asserted about either way.
        def active_hypervisors(node_list):
            return {
                n['uuid'] for n in node_list
                if n.get('state') in NODE_ACTIVE_STATES
                and n.get('is_hypervisor')}

        expected = (active_hypervisors(nodes_before)
                    & active_hypervisors(nodes_after))
        self.assertGreaterEqual(
            len(expected), 1, 'No active hypervisor was stable across the '
            'resources request')

        per_node = set(resources['per_node'])
        excluded = set(resources.get('excluded', {}))
        self.assertIn('excluded', resources)
        for node_uuid in sorted(expected):
            self.assertEqual(
                1, int(node_uuid in per_node) + int(node_uuid in excluded),
                'Active hypervisor %s must be in exactly one of per_node '
                'and excluded. In per_node: %s. In excluded: %s'
                % (node_uuid, node_uuid in per_node, node_uuid in excluded))

        for node_uuid, entry in resources['excluded'].items():
            self.assertIn(
                entry.get('reason'), EXCLUSION_REASONS,
                'Node %s is excluded for a reason outside the published '
                'set: %r' % (node_uuid, entry))

    def test_cluster_topology_meets_the_structural_minimum(self):
        """The deployed cluster is big enough for this suite to mean anything.

        Four tests in this directory read a topology precondition and
        skip when it is not met: test_network_lifecycle.py:53 needs two
        hypervisors which are not the network node, test_scheduler.py's
        test_affinity and
        test_binary_affinity_prefers_the_tagged_node each need
        three nodes, and test_database_tier.py needs two sf-database
        instances. A skip reports as a pass, so a topology edit which
        removes capacity fails nothing -- it silently stops proving
        scheduler affinity and network teardown while every job stays
        green. This test is where that is asserted instead.

        It deliberately fails rather than skipping, for the reason
        database_tier.py's own _database_nodes() gives:
        skipping would turn the assertion into a silent no-op, which is
        the same vacuous pass this check exists to prevent -- and a
        check which skips on precisely the topologies it was written to
        catch would be worse than not having it. Both cluster topologies
        clear every bound asserted here (27 and 24 of ledger against a
        floor of 24), so a cluster reading below one of them is either
        smaller than its topology was sized to be or is not reporting its node
        roles, and both are findings rather than a configuration to skip
        on.

        There is one reading it does not apply to, and it is not a
        cluster. cluster-ci.conf is not run only against cluster
        topologies: the weekly "Debian 13 single machine" lane runs it
        against localhost, on purpose, to
        find whatever in this suite breaks on one node. Every minimum
        here is unmeetable there, so sizing.is_single_machine() carves
        that case out and this test skips on it -- the one skip it
        allows itself. It does not reopen the vacuous pass: the roster
        this reads does not shrink when a node goes quiet, so one node
        in it means one node ever joined rather than one node left
        alive. A cluster which lost members still reports them, still
        reads below the minimums, and still fails. Nor does a roster
        which has not finished growing get the skip: the reading is only
        believed once two consecutive polls agree on it
        (sizing.classify_reading()). That carve-out is
        checked in both directions by
        shakenfist/tests/test_ci_structural_minimum.py, which reads the
        workflows and asserts every topology running cluster-ci.conf is
        one of the two this applies to or the one it skips.

        Database-node count is the one precondition not asserted:
        slim-primary deploys exactly one, so test_database_tier.py's
        skip there is by design.

        The bounds and the ledger arithmetic live in
        shakenfist_ci/sizing.py, so shakenfist/tests/
        test_ci_structural_minimum.py exercises their boundaries on
        every pull request -- cluster CI only runs in the merge queue,
        where a bug in this arithmetic would be expensive to find.
        """
        # A young cluster reads smaller than it is, so an unmet minimum
        # is treated as transient until STRUCTURAL_MINIMUM_WAIT has
        # passed, at which point the last reading is handed back and
        # asserted on as it stands. Nothing else is transient here: a
        # topology which is really too small stays too small, so this
        # cannot wait a real failure out into a pass.
        deadline = time.time() + STRUCTURAL_MINIMUM_WAIT

        # classify_reading() needs the previous poll's status to confirm
        # a single-machine reading rather than believe it on sight.
        last = {'status': None}

        def _reading():
            try:
                nodes = self.system_client.get_nodes()
                resources = self.system_client.get_cluster_resources()
            except Exception as e:
                # The poll starts while the cluster is still settling, so
                # a read which fails says nothing about the topology --
                # the same judgement retries.wait_for_capacity() makes
                # about a poll which raises. It is waited out too, and is
                # what the failure reports if it is still the last thing
                # seen at the deadline.
                last['status'] = sizing.READING_UNREADABLE
                return sizing.READING_UNREADABLE, {
                    'error': '%s: %s' % (e.__class__.__name__, e)}

            status, violations = sizing.classify_reading(
                resources.get('per_node') or {}, nodes,
                previous=last['status'])
            last['status'] = status
            return status, {'nodes': nodes, 'resources': resources,
                            'violations': violations}

        status, reading = retries.retry_while_transient(
            _reading, sizing.TRANSIENT_READINGS, deadline,
            interval=base.CAPACITY_POLL_INTERVAL)

        for detail in ('nodes', 'resources', 'violations', 'error'):
            if detail in reading:
                self.addDetail(
                    'structural minimum %s' % detail,
                    content.text_content(json.dumps(
                        reading[detail], indent=4, sort_keys=True,
                        default=str)))

        if status == sizing.READING_SINGLE_MACHINE:
            self.skipTest(
                'This is a single-machine deployment -- one node holding '
                'every role -- not a cluster, so the structural minimum '
                'has nothing to say about it. scheduled-tests.yml runs '
                'cluster-ci.conf against the localhost topology on '
                'purpose; see this test\'s docstring.')

        if status == sizing.READING_UNREADABLE:
            self.fail(
                'The cluster topology could not be read at all in %d '
                'seconds, so the structural minimum could not be '
                'checked: %s' % (STRUCTURAL_MINIMUM_WAIT, reading['error']))

        if status == sizing.READING_UNCONFIRMED_SINGLE_MACHINE:
            # Only reachable if the one single-machine reading was the
            # last poll before the deadline, after something else: a
            # roster which changed shape at the very end of the wait is
            # not one this test can vouch for either way.
            self.fail(
                'The cluster read as a single machine only on the last '
                'poll before the %d second deadline, so it could not be '
                'confirmed as one, and it is not a cluster meeting the '
                'structural minimum either.' % STRUCTURAL_MINIMUM_WAIT)

        if status != sizing.READING_MET:
            self.fail(
                'The deployed topology is below the structural minimum '
                'every cluster CI topology meets, still so after %d '
                'seconds of waiting for node metrics and capacity rows '
                'to populate: %s'
                % (STRUCTURAL_MINIMUM_WAIT,
                   '; '.join(v['message'] for v in reading['violations'])))

    def test_cluster_resources_charges_unbooted_placements(self):
        # A node's cpu_total_instance_vcpus metric counts only *running*
        # libvirt domains, so an instance which has been placed but has not
        # booted is invisible to it -- and stays invisible however promptly
        # the metric is republished, which is within about five seconds of
        # the running-domain set changing. If admission trusted that
        # measurement alone, a burst of creates
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
            # admitted unguarded: every placement onto it fails open and
            # the node's ledger cannot be trusted. The elected loop's
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
            # the warm-up one still catches the regression the forced
            # reconcile exists to prevent (a row which never arrives at
            # all) while not failing on the warm-up itself.
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

    # Daemons whose restart is least disruptive to a shared cluster, most
    # preferred first. The idempotent re-registration below only ever pokes
    # at one of these: if the no-op guard were broken the daemon would be
    # left reading stopped, and the cleanup restarts it. Nothing here is on
    # the API or scheduling path, and the test skips rather than reach for a
    # daemon that is. register.yml registers both on every node.
    SAFE_DAEMONS_TO_REREGISTER = ['transfers', 'cleaner']

    def test_register_daemon_leaves_a_running_daemon_running(self):
        """Re-registering a running daemon must be a true no-op.

        A deploy runs sf-ctl register-daemon on every pass, and with
        restart-on-change the daemon is not restarted when nothing changed,
        so a re-registration that touches the daemon's state is a
        production fault rather than a theoretical one. Registration used
        to be a JSON list on node_attributes maintained beside the
        node_daemon_states row that carries the state, and the two records
        could disagree -- which is how a re-register came to reset a
        running daemon to stopped, where nothing re-asserts it without a
        restart, and the whole node then read degraded. A registration is
        now exactly that state row, so the guard reads the record it
        protects.
        """
        nodes = self.system_client.get_nodes()
        node = None
        for candidate in nodes:
            if self._node_is_local(candidate):
                node = candidate
                break
        if node is None and nodes:
            node = nodes[0]
        self._require_node_exec(node)

        running = {
            key[len('daemon-'):-len('-state')]
            for key, value in node.items()
            if key.startswith('daemon-') and key.endswith('-state')
            and value == 'daemon-running'}
        safe = [d for d in self.SAFE_DAEMONS_TO_REREGISTER if d in running]
        if not safe:
            # Never fall back to another daemon: on a broken guard the
            # cleanup would restart it, and a shared cluster cannot have
            # sf-api or sf-database restarted under its other tests.
            self.skipTest(
                'Node %s reports none of %s running, so there is no daemon '
                'safe to re-register. node=%s'
                % (node.get('name'), self.SAFE_DAEMONS_TO_REREGISTER,
                   json.dumps(node, sort_keys=True)))
        daemon = safe[0]

        self.addCleanup(self._restore_running_daemon, node, daemon)
        # sf-ctl is a venv entry point, on neither PATH nor sudo's
        # secure_path, so it has to be named by its absolute path.
        self._node_exec(
            node, ['/srv/shakenfist/venv/bin/sf-ctl', 'register-daemon',
                   daemon],
            sudo=True)

        after = self.system_client.get_node(node['name'])
        self.addDetail('node_after_reregister', content.text_content(
            json.dumps(after, indent=4, sort_keys=True)))
        self.assertEqual(
            'daemon-running', after.get('daemon-%s-state' % daemon),
            'Re-registering the already-running %s daemon on %s changed its '
            'state. Registration is the node_daemon_states row, so '
            'register_daemon() must see the row and do nothing at all.'
            % (daemon, node['name']))
        self.assertNotEqual(
            'degraded', after.get('state'),
            'Node %s went degraded after an idempotent re-registration of '
            'the %s daemon.' % (node['name'], daemon))

    def _restore_running_daemon(self, node, daemon):
        """Restart daemon on node if it is no longer reporting running.

        Only does anything when the assertion above has already failed, and
        it is here so that a failure does not leave the shared cluster with
        a node stuck degraded for the rest of the run.
        """
        current = self.system_client.get_node(node['name'])
        if current.get('daemon-%s-state' % daemon) == 'daemon-running':
            return
        self._node_exec(
            node, ['systemctl', 'restart', 'sf-%s.service' % daemon],
            sudo=True, check_exit_code=False)

    def _safe_delete_instance(self, instance_uuid):
        """Delete an instance, tolerating if it's already gone."""
        try:
            self.system_client.delete_instance(instance_uuid)
        except apiclient.ResourceNotFoundException:
            pass

    def test_cluster_resources_measured_drops_after_delete(self):
        # Regression coverage for publishing metrics when the running-domain
        # set changes. The CPU pre-filter charges
        # max(cpu_measured, cpu_committed): cpu_committed is released
        # inside the delete transaction, but cpu_measured used to be
        # published on a flat 60 s cadence, so a node kept refusing new
        # work for up to a minute after its last instance was deleted even
        # though the ledger had already cleared. sf-resources now
        # republishes within about 5 s of the active-domain set changing.
        #
        # What is asserted is *when the node looked*, not what it counted.
        # This suite's cluster is shared: other tests' instances start and
        # stop on the watched node while this test runs, so a vCPU count
        # read from it cannot be attributed to this test's instance. Issue
        # 4214 traced every inspected failure of the count-based version of
        # this test to exactly that -- siblings arriving on the node (it
        # read 8 where it expected 0), or one arriving in the same second
        # this instance's domain went away and holding the figure where it
        # was -- with sf-resources publishing the correct value throughout.
        # Instead, each power event records libvirt_returned_at, taken on
        # the node's clock once libvirt's create() or destroy() has
        # returned -- by which point the domain is active or inactive --
        # and each publish records domains_enumerated_at on the same clock.
        # A publish which enumerated at or after the return is one which
        # has seen the change, whatever else is running.
        #
        # What this cannot do is tell a publish triggered by this change
        # from one which happened to arrive in the window anyway: the flat
        # METRICS_PUBLISH_INTERVAL_SECONDS tick, or a sibling changing the
        # node's domain set. Either satisfies a wait, so with publish-on-
        # change broken each wait would still pass perhaps a third of the
        # time on an idle node and more often on a busy one. That is the
        # price of a test siblings cannot fail; the decision to publish on
        # a domain-set change is pinned deterministically by the
        # _should_publish_metrics unit tests in test_daemon_resources.py.
        resources = self.system_client.get_cluster_resources()
        candidates = [
            n for n in self._hypervisor_nodes()
            if resources['per_node'].get(n['uuid'], {}).get(
                'cpu_available', 0) >= 1]
        if not candidates:
            self.skipTest('No hypervisor with a vCPU of headroom')
        # Any node with room would do now that siblings on it cannot fail
        # the assertion; the emptiest is kept so that the create below is
        # the least likely to be refused.
        node = max(
            candidates,
            key=lambda n: resources['per_node'][n['uuid']]['cpu_available'])

        # The instance is pinned so the node this test watches is known
        # in advance -- the pin is the assertion's subject (which node's
        # measurement to read), not a workaround for capacity.
        cpus = 1
        inst = self.create_instance(
            'metrics-drop', cpus, 128, None, [{'size': 1, 'type': 'disk'}],
            None, None, force_placement=node['name'])
        self.addCleanup(self._safe_delete_instance, inst['uuid'])
        self.addDetail('instance', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        self.assertEqual(node['uuid'], inst['node'])

        # 'created' is set immediately after power_on(), so the poweron
        # event has been emitted once this returns -- but not necessarily
        # shipped to sf-database yet, which _libvirt_returned_at() waits
        # out.
        self._await_instance_create(inst['uuid'])
        started_at = self._libvirt_returned_at(inst['uuid'], 'poweron')
        per_node = self._await_measurement_after(
            node['uuid'], started_at,
            'the domain start which returned at %.3f' % started_at)

        # Not a wait condition: an enumeration after create() returned
        # counted this domain, so the figure holds at least its vCPUs
        # whatever siblings are doing. This checks the count path rather
        # than the timing.
        self.assertGreaterEqual(per_node['cpu_measured'], cpus)
        self.addDetail('resources before delete', content.text_content(
            json.dumps(self.system_client.get_cluster_resources(),
                       indent=4, sort_keys=True)))

        # self.system_client uses ASYNC_PAUSE, so this blocks until the
        # instance's state is 'deleted', which is after power_off() has
        # emitted its event (shipped later, as for poweron above).
        self.system_client.delete_instance(inst['uuid'])
        stopped_at = self._libvirt_returned_at(inst['uuid'], 'poweroff')

        # No bound on the figure here: whatever the node now counts
        # belongs to other instances.
        self._await_measurement_after(
            node['uuid'], stopped_at,
            'the domain destroy which returned at %.3f' % stopped_at)
        self.addDetail('resources after delete', content.text_content(
            json.dumps(self.system_client.get_cluster_resources(),
                       indent=4, sort_keys=True)))

    # 20 s, not 5: the claim under test is "seconds, not a minute", and
    # this leaves margin for a loaded CI node rather than being the
    # tightest bound that could pass -- four 5 s domain polls plus a
    # publish plus the scheduler's cache. Before this phase either wait
    # could take 60 s. The publish a wait sees is often not the one on
    # the poll which noticed the change but the follow-up on the poll
    # after: libvirt lists a domain as active before create() returns,
    # so the first publish can enumerate before libvirt_returned_at and
    # not count as following it (issue 4394).
    CPU_MEASURED_DEADLINE_SECONDS = 20
    CPU_MEASURED_POLL_SECONDS = 5

    def _libvirt_returned_at(self, instance_uuid, message):
        """When libvirt returned from this instance's ``message`` power call.

        Exactly one such event is expected: the instance is started once
        and stopped once, and although deleting it calls power_off() more
        than once, only the call which actually stopped the domain records
        the field.

        The event was emitted before the state change the caller waited
        on, but events reach sf-database through each daemon's spool and
        drainer, so it can still be unreadable; reading once failed here
        with an empty list (issue 4919).
        """
        returned = self._await_instance_event_extra(
            instance_uuid, message, 'libvirt_returned_at')
        self.assertEqual(
            1, len(returned),
            'Instance %s should have exactly one %s event carrying '
            'libvirt_returned_at, found %r' % (instance_uuid, message, returned))
        return returned[0]

    def _await_measurement_after(self, node_uuid, since, change):
        """Wait for a node to publish a measurement taken at or after since.

        Returns the node's /admin/resources entry. ``change`` completes the
        sentence "published no measurement following ..." in the failure
        message, so a broken publish-on-start and a broken publish-on-delete
        do not report identically.
        """
        deadline = time.time() + self.CPU_MEASURED_DEADLINE_SECONDS
        per_node = None
        while True:
            resources = self.system_client.get_cluster_resources()
            per_node = resources['per_node'].get(node_uuid)
            self.assertIsNotNone(
                per_node,
                'Node %s is absent from /admin/resources per_node'
                % node_uuid)
            enumerated_at = per_node.get('domains_enumerated_at')
            self.assertIsNotNone(
                enumerated_at,
                'Node %s publishes no domains_enumerated_at' % node_uuid)
            if enumerated_at >= since:
                return per_node

            if time.time() > deadline:
                break
            time.sleep(self.CPU_MEASURED_POLL_SECONDS)

        self.fail(
            'Node %s published no measurement following %s within %ds; its '
            'latest enumerated domains at %.3f and counts cpu_measured %r. '
            'The measurement should follow the running-domain set within '
            'seconds, not within a minute.'
            % (node_uuid, change, self.CPU_MEASURED_DEADLINE_SECONDS,
               per_node['domains_enumerated_at'], per_node['cpu_measured']))

    def _accounting_verdict(self, before, after, entry):
        """Whether a node's domain sample and its per_node entry agree.

        Returns ``comparable``, whether the two readings could be held
        against each other at all; ``agreed``, whether they then did; and
        a ``reason`` saying which.

        Three things make a round incomparable, and none of them is a
        finding. A node absent from ``per_node`` published no measurement
        to compare -- which an active hypervisor is entitled to do, for
        instance with a queue over the exclusion threshold. A domain
        which was listed but could not be read was starting or stopping
        while the sample was taken. And the two samples bracket the
        ``/admin/resources`` read, so a node whose libvirt state moved
        across that read is not judged either: the published figures
        legitimately describe either side of the change. Only a stable
        bracket around a published entry is a comparison, which is why
        the caller counts them.
        """
        if entry is None:
            return {'comparable': False, 'agreed': False,
                    'reason': 'absent from per_node, so it published no '
                              'measurement to compare'}
        if before['unreadable'] or after['unreadable']:
            return {'comparable': False, 'agreed': False,
                    'reason': 'a listed domain could not be read, so the '
                              'sample caught it starting or stopping'}
        if before['domains'] != after['domains']:
            return {'comparable': False, 'agreed': False,
                    'reason': "libvirt's domains changed while the ledger "
                              'was read'}

        mismatches = accounting.accounting_mismatches(before['domains'], entry)
        if mismatches:
            return {'comparable': True, 'agreed': False,
                    'mismatches': mismatches,
                    'reason': 'the published measurement disagrees with '
                              'libvirt: ' + '; '.join(
                                  '%s libvirt %r published %r'
                                  % (m['figure'], m['libvirt'],
                                     m['published'])
                                  for m in mismatches)}

        return {'comparable': True, 'agreed': True, 'reason': None}

    def test_node_accounting_agrees_with_libvirt(self):
        """Each hypervisor's measured accounting matches what it is running.

        Everything else in this file asserts on ``/admin/resources``
        against Shaken Fist's own view of itself. This compares the three
        figures in a ``per_node`` entry which are measurements of the
        hypervisor rather than ledger entries -- ``instances_active``,
        ``cpu_measured`` and ``ram_measured``, all three built in one
        pass over ``get_active_sf_domains()`` by ``sf-resources`` -- with
        what libvirt on that node reports for the same domains. Both CPU
        and memory admission measure a node against those figures, so a
        node whose measurement has drifted from its hypervisor decides
        with a number which is not true. Issue 3772 is the umbrella for
        creates refused 507 while the cluster has room, and the diagnosis
        each time has been a node whose recorded usage did not match what
        it was running -- found from the symptom, a refused create, which
        means a drift which refuses nothing is invisible.

        The comparison runs after a round of pinned creates and a delete,
        so the ledger has had to move in both directions on the node this
        test pins, and it runs over every hypervisor rather than that one
        -- a sibling test's instances are as good a reason for the
        figures to be wrong, and cost nothing extra to check.

        What it cannot do is attribute a disagreement to this test's own
        churn, because the cluster is shared: every other worker's
        instances start and stop on these nodes throughout. So a
        disagreement is not believed on sight. Each node is sampled with
        ``/admin/resources`` read between two libvirt samples, and is
        only judged when those two agree -- a node whose domains moved
        across the read is resampled -- and the published figures are
        then allowed to catch up, because a publish lags a change by a
        poll plus a publish. A node fails only if it disagreed in every
        one of the comparable readings taken over
        LIBVIRT_RECONCILIATION_WAIT, which is many times that lag. A node
        which produced no comparable reading at all fails nothing, since
        an active hypervisor is entitled to be absent from ``per_node``
        (an overlong queue, for one); if that is true of every node the
        test says it proved nothing rather than reporting green.

        The residue is that a node could agree by coincidence, a sibling
        arriving to make a wrong figure right; that is the same trade
        ``test_cluster_resources_measured_drops_after_delete`` makes
        above, and the alternative -- insisting the publish was
        enumerated inside the bracket -- waits up to a publish interval
        on an idle node while demanding nothing change on it, which a
        busy node cannot supply.
        """
        hypervisors = self._hypervisor_nodes()
        for hypervisor in hypervisors:
            self._require_node_exec(hypervisor)
            self._require_node_sudo(hypervisor)

        resources = self.system_client.get_cluster_resources()
        self.addDetail('resources before', content.text_content(json.dumps(
            resources, indent=4, sort_keys=True)))
        candidates = [
            n for n in hypervisors
            if resources['per_node'].get(n['uuid'], {}).get(
                'cpu_available', 0) >= 2
            and resources['per_node'][n['uuid']].get(
                'ram_available', 0) >= 2 * ACCOUNTING_INSTANCE_MEMORY_MB]
        if not candidates:
            self.skipTest(
                'No hypervisor with room for two %d MB instances'
                % ACCOUNTING_INSTANCE_MEMORY_MB)
        node = max(
            candidates,
            key=lambda n: resources['per_node'][n['uuid']]['cpu_available'])

        # Two one vCPU instances with an empty disk and no base image:
        # nothing is downloaded, so this costs the cluster almost
        # nothing. One is kept for the comparison -- so the pinned node
        # has at least one domain of ours and an all-zeroes agreement
        # cannot pass vacuously -- and one is deleted, so the node's
        # accounting has had to release as well as charge before
        # anything is compared.
        kept = self.create_instance(
            'accounting-kept', 1, ACCOUNTING_INSTANCE_MEMORY_MB, None,
            [{'size': 1, 'type': 'disk'}], None, None,
            force_placement=node['name'])
        self.addCleanup(self._safe_delete_instance, kept['uuid'])
        churned = self.create_instance(
            'accounting-churned', 1, ACCOUNTING_INSTANCE_MEMORY_MB, None,
            [{'size': 1, 'type': 'disk'}], None, None,
            force_placement=node['name'])
        self.addCleanup(self._safe_delete_instance, churned['uuid'])
        for inst in (kept, churned):
            self.addDetail('instance %s' % inst['name'], content.text_content(
                json.dumps(inst, indent=4, sort_keys=True)))
            self.assertEqual(node['uuid'], inst['node'])
            self._await_instance_create(inst['uuid'])

        self.system_client.delete_instance(churned['uuid'])
        self._await_instance_deleted(churned['uuid'])

        # Every hypervisor is owed an agreement, including the ones this
        # test placed nothing on. A node drops out of pending the first
        # time its two readings agree; comparisons counts the rounds in
        # which they could be held against each other at all, which is
        # what separates a node that disagrees from one that was never
        # readable.
        pending = {n['uuid']: n for n in hypervisors}
        comparisons = {n['uuid']: 0 for n in hypervisors}
        last = {}
        deadline = time.time() + LIBVIRT_RECONCILIATION_WAIT
        while True:
            for node_uuid, hypervisor in sorted(pending.items()):
                before = self._node_sf_domains(hypervisor)
                entry = self.system_client.get_cluster_resources()[
                    'per_node'].get(node_uuid)
                after = self._node_sf_domains(hypervisor)

                verdict = self._accounting_verdict(before, after, entry)
                last[node_uuid] = {
                    'node': hypervisor.get('name'),
                    'libvirt before': before,
                    'libvirt after': after,
                    'per_node': entry,
                    # How old the publish was when we looked, on the
                    # node's own clock: a positive figure is a
                    # measurement which predates this sample, which is
                    # the innocent explanation for a disagreement.
                    'publish age at sample': (
                        before['sampled_at'] - entry['domains_enumerated_at']
                        if entry and entry.get('domains_enumerated_at')
                        else None),
                    **verdict}
                if verdict['comparable']:
                    comparisons[node_uuid] += 1
                if verdict['agreed']:
                    del pending[node_uuid]

            if not pending or time.time() > deadline:
                break
            time.sleep(LIBVIRT_RECONCILIATION_POLL)

        self.addDetail('libvirt reconciliation', content.text_content(
            json.dumps(last, indent=4, sort_keys=True, default=str)))
        self.addDetail('libvirt comparisons', content.text_content(
            json.dumps(comparisons, indent=4, sort_keys=True)))

        # The pinned instance is still running, so libvirt must have
        # listed a domain for it. Without this a node which reported no
        # domains at all -- a virsh which did not run, for instance --
        # would agree with a node measuring nothing and the comparison
        # would prove nothing. Listed is all that is asked: a domain
        # whose figures could not be read was listed too, and the
        # comparison itself is what the figures are for.
        sampled = last[node['uuid']]['libvirt before']
        self.assertIn(
            kept['uuid'],
            set(sampled['domains']) | set(sampled['unreadable']),
            'Instance %s is placed on node %s and has been created, but '
            "libvirt there lists no domain for it, so this test's own "
            'subject is missing from the sample' % (kept['uuid'], node['name']))

        # A node which never produced a single comparable round is not a
        # failure: it published no entry, or something was starting or
        # stopping on it every time it was looked at. It is also not a
        # pass, so if that is every node the test has proved nothing and
        # says so rather than reporting green.
        self.assertGreater(
            sum(comparisons.values()), 0,
            'No hypervisor could be compared with libvirt at all in %d '
            'seconds, so nothing here was checked'
            % LIBVIRT_RECONCILIATION_WAIT)

        drifted = [n for n in sorted(pending) if comparisons[n]]
        if drifted:
            self.fail(
                "These hypervisors' published accounting disagreed with the "
                'domains libvirt is running on them in every one of the '
                'comparable readings taken over %d seconds, after a pinned '
                'create and delete: %s'
                % (LIBVIRT_RECONCILIATION_WAIT,
                   '; '.join(
                       '%s after %d comparisons (%s)'
                       % (last[n]['node'], comparisons[n], last[n]['reason'])
                       for n in drifted)))
