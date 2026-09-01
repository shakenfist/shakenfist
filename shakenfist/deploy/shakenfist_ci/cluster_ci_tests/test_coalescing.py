# Copyright 2026 Michael Still and contributors

import json
import re
import time

from shakenfist_client import apiclient
from testtools import content

from shakenfist_ci import base


# The two messages which mean a coalescing query matched a row. The
# enqueue-side dedup found a pending sibling and reused it; the
# worker-side fold found pending siblings and marked them complete.
# Both are emitted against the network as well as the operation,
# because an operation is hard deleted thirty seconds after it
# completes and takes its events with it (#3864), and the network
# outlives it.
DEDUP_EVENT = 'enqueue-side dedup: reused pending op'
FOLD_EVENT = 'coalesced sibling ops'

# How many instances to start at once on the one network. Each
# interface on a network triggers a network_apply_update_dnsmasq
# enqueue during instance start, and all of them land on the single
# cluster-wide networknode queue which one elected worker drains. Six
# is the number from the original report which motivated the
# queue-performance plan -- six instance starts on one network, each
# enqueueing one update_dnsmasq, serviced strictly serially -- and it
# is deliberately not larger: this test has to buy overlap on a loaded
# CI cluster without adding a materially longer instance-create burst
# to the suite. If it ever stops overlapping reliably, raise this
# before weakening the assertion.
BURST = 6


# The task the per-node test is about. ``network_ensure_mesh`` is the
# only NetOp task which does node-local work, which is why it needs the
# two column ``(network_uuid, node_uuid)`` coalescing key before it can
# be coalesced at all -- with the network alone, one hypervisor's mesh
# op and another's are indistinguishable to both dedup paths while
# doing different work on different hosts. See
# docs/plans/PLAN-queue-performance-phase-11-multi-column-key.md.
# How much longer to keep polling for a worker-side fold once a
# per-node dedup event has been seen. The dedup proves the eventlog
# drainer has already shipped this burst's coalescing events, so a fold
# is at most a batch behind; the full deadline above only matters while
# nothing at all has arrived. Cluster CI runs only in the merge queue,
# where the difference is wall clock nobody gets back.
POST_DEDUP_FOLD_GRACE = 15

MESH_TASK = 'network_ensure_mesh'

# The task ``network_ensure_mesh`` is always paired with on the
# cluster-wide network-node queue. Both cluster-wide enqueues of a mesh
# task send this two task list (``Network.create`` and the interface
# hot-plug path in ``external_api/instance.py``), and neither is a
# single task list, so a fold whose survivor ran only the mesh task
# cannot have come from that queue. This is one of the two ways the
# per-node test tells a per-node fold from a cluster-wide one; see
# ``_per_node_mesh_signals``.
NETWORK_NODE_TASK = 'network_apply_create_network_node'

# The flood ("all zeroes destination") FDB entries a VXLAN interface
# uses for broadcast and unknown-unicast forwarding in a unicast mesh.
# These are precisely what ``_apply_ensure_mesh`` writes and what
# ``Network.is_mesh_okay`` audits, so they are the mesh: if a fold
# marked a sibling complete without doing its work, the missing work is
# a missing line here. Deliberately more tolerant than the cluster's own
# MESH_FLOOD_RE (``shakenfist/util/network.py``) about an interposed
# ``dev`` column, because this one parses output from a foreign host
# whose iproute2 version is not ours to choose.
MESH_FLOOD_RE = re.compile(
    r'^00:00:00:00:00:00\s+(?:dev\s+\S+\s+)?dst\s+(\S+)\b'
    r'.*\bself\b.*\bpermanent\b')


class TestCoalescing(base.BaseNamespacedTestCase):
    """Assert that cluster operation coalescing matches rows on a real cluster.

    This exists because it did not. Coalescing shipped in 2026-05 and was
    dead on arrival: both of its SQL primitives joined an undashed uuid to
    a dashed one and an enum value to an enum name, so neither could ever
    match a row (#3878). It took three months and a hand audit to notice,
    because nothing anywhere asserted that coalescing did anything, and a
    fold which matches nothing is indistinguishable from a fold which is
    switched off.

    The first test's assertion is deliberately "one of the two
    mechanisms fired" rather than a specific one. Coalescing has two
    halves and #3878 broke both at once, so either firing proves the
    join works. Demanding the worker-side fold specifically would be
    demanding a race: the enqueue-side dedup is the common path -- it
    returns the pending op's uuid instead of inserting a second row --
    so a sibling for the fold to find only exists when two callers both
    missed that lookup.

    The second test is about the per-node queues phase 11 opened up. It
    is narrower in what it looks for -- only signals which can have come
    from a ``{node_uuid}-network-*`` queue count -- and it goes on to
    assert that the vxlan mesh is still correct on every participating
    node afterwards, which is the claim a fold rests on and which
    nothing had previously tested. It inherits the race above, and
    handles it by skipping rather than failing when the run proved the
    per-node key matches rows but happened not to produce a fold.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'coalescing'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.243.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

        # The namespaced test client pauses for up to sixty seconds on an
        # async operation, which would serialise the burst below into six
        # sequential instance creates and leave nothing to coalesce. Fire
        # them through a client which does not wait instead.
        self.burst_client = apiclient.Client(
            base_url=self.system_client.base_url,
            namespace=self.namespace, key=self.namespace_key,
            async_strategy=apiclient.ASYNC_CONTINUE)

    def _coalescing_events(self):
        events = self.test_client.get_network_events(self.net['uuid'])
        return events, [
            e for e in events
            if str(e.get('message', '')) in (DEDUP_EVENT, FOLD_EVENT)]

    def test_duplicate_network_work_is_coalesced(self):
        instances = []
        for i in range(BURST):
            instances.append(self.burst_client.create_instance(
                'coalesce-%d' % i, 1, 1024,
                [
                    {
                        'network_uuid': self.net['uuid']
                    }
                ],
                [
                    {
                        'size': 8,
                        'base': base.CLUSTER_CI_IMAGE,
                        'type': 'disk'
                    }
                ], None, None))

        self.addDetail('instances', content.text_content(json.dumps(
            [i['uuid'] for i in instances], indent=4, sort_keys=True)))

        # Events are eventually consistent: the emitting daemons spool
        # locally and a per-process drainer ships them to sf-eventlog in
        # ~100ms batches, and the operations themselves are still being
        # dispatched while this polls. Poll to a deadline rather than
        # reading once -- the same reason test_events.py does.
        #
        # Both coalescing signals are emitted while
        # network_apply_update_dnsmasq is enqueued and dispatched, which
        # happens as each interface is attached -- early in instance
        # create, not at instance ready. Two minutes is therefore
        # generous rather than tight, and the number matters: this is
        # the wait the failure path pays, and cluster CI only runs in
        # the merge queue, so every second of it is merge queue wall
        # clock. Raise BURST before raising this.
        events = []
        coalesced = []
        deadline = time.time() + 120
        while time.time() < deadline:
            events, coalesced = self._coalescing_events()
            if coalesced:
                break
            time.sleep(5)

        self.addDetail('coalescing_events', content.text_content(json.dumps(
            coalesced, indent=4, sort_keys=True)))
        # Which mechanism fired, recorded even on a pass. A shift from
        # one to the other is a real change in behaviour and a human
        # reading a green run should be able to see it without having to
        # make the test fail first.
        counts = {}
        for event in coalesced:
            message = str(event.get('message', ''))
            counts[message] = counts.get(message, 0) + 1
        self.addDetail('coalescing_mechanisms', content.text_content(
            json.dumps(counts, indent=4, sort_keys=True)))

        if not coalesced:
            self.addDetail('network_events', content.text_content(json.dumps(
                events, indent=4, sort_keys=True)))

        self.assertNotEqual(
            0, len(coalesced),
            'Starting %d instances on one network produced neither a '
            '"%s" nor a "%s" event on that network. Either coalescing is '
            'matching no rows -- which is the #3878 defect, and what this '
            'test exists to catch -- or the burst no longer overlaps. '
            'Check the coalesce_outcome field on the operations\' '
            '"execution duration" events to tell those apart.'
            % (BURST, DEDUP_EVENT, FOLD_EVENT))

        # The burst was fired through a client which does not wait, and
        # the assertion above returns as soon as the first coalescing
        # event lands -- which is well before the six instances finish
        # creating. Settle them before returning, because tearDown
        # deletes every instance in the namespace immediately and fails
        # the test outright if any survives its five minute window, and
        # deleting an instance mid-create is a plausible way to get
        # there. Deliberately after the assertion: a failure to start is
        # not this test's subject, and should not be able to mask the
        # coalescing result.
        for instance in instances:
            self._await_instance_create(instance['uuid'])

    def _per_node_mesh_signals(self, events, network_node_name):
        """Split a network's events into per-node mesh coalescing signals.

        Returns ``(folds, dedups)``: the worker-side folds and the
        enqueue-side dedups which can only have happened on a per-node
        ``{node_uuid}-network-*`` queue. Both are evidence that the two
        column key matched a row for node-local work, which is the
        thing phase 11 added and which nothing could do before it.

        A dedup is per-node whenever its ``requested_task`` is the mesh
        task. That path only fires for a single task enqueue, and every
        single task ``network_ensure_mesh`` enqueue in the tree is a
        per-node one (``Network.ensure_mesh`` and both sites in the
        network maintainer all pass ``target=<node uuid>,
        family='network'``); the cluster-wide sites always send the two
        task list.

        A fold is harder, because the event's ``tasks`` is the
        *survivor's* coalescible task list rather than the folded
        siblings'. Two independent facts each settle it, and either is
        enough:

        * the survivor ran the mesh task without
          ``network_apply_create_network_node``, so it was not one of
          the two task cluster-wide enqueues; or
        * the event was emitted by a node which is not the elected
          network node, and the cluster-wide ``networknode-*`` queues
          are only ever drained there (the ``NODE_IS_NETWORK_NODE``
          guard in ``shakenfist/daemons/network/workitem.py``).

        The second is only usable when the API told us which node is
        the network node, hence the guard on ``network_node_name``.
        """
        folds = []
        dedups = []

        for event in events:
            message = str(event.get('message', ''))
            extra = event.get('extra') or {}

            if message == FOLD_EVENT:
                tasks = extra.get('tasks') or []
                if MESH_TASK not in tasks:
                    continue
                if NETWORK_NODE_TASK not in tasks:
                    folds.append(event)
                elif (network_node_name
                        and event.get('fqdn') != network_node_name):
                    folds.append(event)

            elif message == DEDUP_EVENT:
                if extra.get('requested_task') == MESH_TASK:
                    dedups.append(event)

        return folds, dedups

    def _node_by_identifier(self, nodes, identifier):
        """The node dict for a placement identifier, or None.

        ``Instance.placement['node']`` is written by the placement RPC
        and read back through ``Node.from_db()``, which accepts either
        a node uuid or a node name -- and both forms have been in that
        field. Match on both rather than picking one and being subtly
        wrong on a cluster which uses the other.
        """
        if not identifier:
            return None
        for node in nodes:
            if identifier in (node.get('uuid'), node.get('name'),
                              node.get('fqdn')):
                return node
        return None

    def _mesh_flood_ips(self, node, vxid):
        """The flood FDB destinations for a vxid on one node.

        Returns ``(ips, raw_output)``. This is the same read
        ``Network.is_mesh_okay()`` performs on each node, run here from
        the outside over the management mesh, so the test and the
        cluster's own auditor are looking at exactly the same bytes.

        A missing vxlan device is not distinguished from an empty FDB:
        ``bridge`` writes "Cannot find device" to stderr and exits non
        zero, and both cases are "the mesh this node should have is not
        there", which is what the caller is asserting about. The raw
        output is returned so a failure can say which it was.
        """
        out, err = self._node_exec(
            node, ['bridge', 'fdb', 'show', 'brport', 'vxlan-%06x' % vxid],
            sudo=True, check_exit_code=False)

        ips = set()
        for line in out.split('\n'):
            m = MESH_FLOOD_RE.match(line.strip())
            if m:
                ips.add(m.group(1))

        return ips, '%s%s' % (out, err)

    def _assert_mesh_is_correct(self, vxid, participants, timeout=120):
        """Every participant's FDB floods to every other participant.

        This is the assertion the per-node fold has to survive, and it
        is deliberately made against host state rather than against a
        ping. A ping can succeed on a learned FDB entry, which ages out
        -- the mesh is the *permanent* flood entries, and a fold which
        marked a sibling complete without doing its work leaves a hole
        in exactly those.

        Polled to a deadline because the mesh converges asynchronously:
        the caller has already waited for every cluster operation
        against the network to reach a terminal state, so this is
        covering the gap between an op completing and its privexec
        side effects landing, not waiting for work to be scheduled.

        Missing entries fail. Extra entries are recorded but do not,
        because the desired set is computed from placements read at one
        instant while the cluster is still settling; a stale entry is
        the network maintainer's drift to repair and is not something a
        fold can cause.
        """
        deadline = time.time() + timeout
        started = time.time()
        attempts = 0

        while True:
            attempts += 1
            observed = {}
            raw = {}
            wrong = {}

            for node in participants:
                desired = {
                    other['ip'] for other in participants
                    if other['ip'] != node['ip']}
                discovered, raw[node['name']] = self._mesh_flood_ips(
                    node, vxid)
                observed[node['name']] = {
                    'desired': sorted(desired),
                    'discovered': sorted(discovered),
                    'missing': sorted(desired - discovered),
                    'unexpected': sorted(discovered - desired)
                }
                if desired - discovered:
                    wrong[node['name']] = observed[node['name']]

            if not wrong:
                self.addDetail('mesh_flood_entries', content.text_content(
                    json.dumps(observed, indent=4, sort_keys=True)))
                self.addDetail('mesh_convergence', content.text_content(
                    json.dumps({
                        'attempts': attempts,
                        'seconds': round(time.time() - started, 1)
                    }, indent=4, sort_keys=True)))
                return

            if time.time() > deadline:
                break

            time.sleep(5)

        self.addDetail('mesh_flood_entries', content.text_content(
            json.dumps(observed, indent=4, sort_keys=True)))
        self.addDetail('mesh_fdb_raw', content.text_content(
            json.dumps(raw, indent=4, sort_keys=True)))
        self.fail(
            'The vxlan mesh for network %s (vxid %d) is incomplete %d '
            'seconds after every cluster operation against that network '
            'reached a terminal state. These nodes are missing flood FDB '
            'entries for other participants: %s. That is the failure a '
            'coalescing fold causes when running the survivor does not '
            'cover the work of the siblings it marked complete -- see the '
            'fourth risk in '
            'docs/plans/PLAN-queue-performance-phase-11-multi-column-key.md. '
            'The mesh_flood_entries detail has the desired and discovered '
            'sets per node and mesh_fdb_raw has the unparsed bridge output, '
            'which is where a device that is missing entirely shows up. '
            'Whether a per-node fold had happened by this point is in the '
            'per_node_mesh_signals_before_mesh_check detail; without a fold '
            'this is a mesh defect which has nothing to do with coalescing.'
            % (self.net['uuid'], vxid, timeout,
               json.dumps(wrong, sort_keys=True)))

    def test_per_node_mesh_work_is_coalesced(self):
        """A fold on a per-node queue leaves the vxlan mesh correct.

        ``network_ensure_mesh`` is the one NetOp task which does node
        local work. It could not be coalesced at all until the
        coalescing key grew a second column, because with the network
        alone hypervisor A's mesh op and hypervisor B's are identical to
        both dedup paths while doing different work on different hosts,
        so folding them leaves one host's FDB stale. Phase 11 widened
        the key to ``(network_uuid, node_uuid)`` and let the task back
        into COALESCIBLE_TASKS.

        The test therefore has two halves, and the second is the point.
        The first asserts that per-node coalescing matched a row at all,
        because a fold which matches nothing is indistinguishable from a
        fold which is switched off -- that is #3878, and it survived
        three months. The second asserts that the mesh is *correct*
        afterwards on every participating node, because a fold is only
        sound if running the survivor once covers every sibling it
        marked complete, and nothing had ever tested that claim.

        Contention comes from the fan-out rather than from the burst
        size. ``Network.ensure_mesh`` enqueues one operation per
        participating node on every call, and every instance start on
        the network calls it, so BURST starts across N participants
        produce BURST operations on each of N per-node queues -- and
        they are enqueued by BURST different worker threads in as many
        processes as there are hypervisors hosting the burst, which is
        what makes two of them race the enqueue-side dedup lookup and
        leave the worker-side fold something to find.
        """
        nodes = self.system_client.get_nodes()
        self.addDetail('nodes', content.text_content(json.dumps(
            nodes, indent=4, sort_keys=True)))

        # A single hypervisor cluster cannot exercise this at all: the
        # fan-out has one target, the key's second column never
        # distinguishes anything, and the mesh assertion below has no
        # peer to flood to.
        hypervisors = [n for n in nodes if n.get('is_hypervisor')]
        if len(hypervisors) < 2:
            self.skipTest(
                'network_ensure_mesh is per-node work and this cluster has '
                '%d hypervisor(s). A per-node fold needs at least two.'
                % len(hypervisors))

        # The same choice ``base._network_node()`` makes, but taken from
        # the listing already read above rather than a second one. Node
        # dicts are compared by value below, and two listings of the
        # same node differ in their lastseen and daemon state fields --
        # so a node fetched twice would not compare equal to itself.
        network_node = None
        for node in nodes:
            if node.get('is_network_node'):
                network_node = node
                break
        network_node_name = network_node.get('name') if network_node else None

        # Checked before the burst rather than after it, so a cluster
        # without the mesh ssh prerequisite skips in seconds instead of
        # creating six instances first. Every node whose FDB this test
        # reads has to be reachable, including the network node, which
        # participates in every mesh whether or not it hosts anything.
        for node in hypervisors:
            self._require_node_exec(node)
        if network_node:
            self._require_node_exec(network_node)

        # Pin the burst across the hypervisors round robin. Left to the
        # scheduler this test would assert nothing on a run where every
        # instance happened to land on one node, and "assert nothing"
        # is the failure mode the whole of phase 9 was written about.
        instances = []
        create_errors = []
        for i in range(BURST):
            target = hypervisors[i % len(hypervisors)]
            try:
                instances.append(self.burst_client.create_instance(
                    'mesh-coalesce-%d' % i, 1, 1024,
                    [
                        {
                            'network_uuid': self.net['uuid']
                        }
                    ],
                    [
                        {
                            'size': 8,
                            'base': base.CLUSTER_CI_IMAGE,
                            'type': 'disk'
                        }
                    ], None, None, force_placement=target['name']))
            except apiclient.APIException as e:
                # A forced placement onto a node the scheduler will not
                # admit is a capacity outcome on a shared CI cluster,
                # not a coalescing defect. Record it and carry on; the
                # check below decides whether enough of the burst
                # survived for the run to mean anything.
                create_errors.append({
                    'node': target['name'],
                    'error': str(e)
                })

        self.addDetail('instances', content.text_content(json.dumps(
            [i['uuid'] for i in instances], indent=4, sort_keys=True)))
        if create_errors:
            self.addDetail('create_errors', content.text_content(json.dumps(
                create_errors, indent=4, sort_keys=True)))

        if len(instances) < 2:
            self.skipTest(
                'Only %d of %d instances could be created (%s), which is '
                'not enough to contend for one node\'s queue.'
                % (len(instances), BURST, json.dumps(create_errors)))

        # The burst was fired through a client which does not wait, so
        # settle it here. This is not tidying up: the mesh assertion
        # needs every interface placed before the desired FDB set is
        # knowable, and tearDown fails the test outright if an instance
        # is still mid-create when it starts deleting.
        for instance in instances:
            self._await_instance_create(instance['uuid'])

        # Which hypervisors actually ended up hosting the burst, and
        # therefore which nodes participate in this network's mesh. The
        # network node always participates -- it holds the netns side of
        # every network -- whether or not it hosts an instance.
        participants = []
        placements = {}
        for instance in instances:
            refreshed = self.test_client.get_instance(instance['uuid'])
            placements[instance['uuid']] = refreshed.get('node')
            node = self._node_by_identifier(nodes, refreshed.get('node'))
            if node and node not in participants:
                participants.append(node)
        if network_node and network_node not in participants:
            participants.append(network_node)

        self.addDetail('placements', content.text_content(json.dumps(
            placements, indent=4, sort_keys=True)))
        self.addDetail('mesh_participants', content.text_content(json.dumps(
            [n['name'] for n in participants], indent=4, sort_keys=True)))

        hosting = []
        for identifier in placements.values():
            node = self._node_by_identifier(nodes, identifier)
            if node and node not in hosting:
                hosting.append(node)
        if len(hosting) < 2:
            self.skipTest(
                'The burst landed on %d hypervisor(s) (%s), so no mesh op '
                'was enqueued to a second node and there is no cross-node '
                'mesh to verify.'
                % (len(hosting),
                   json.dumps(sorted(
                       str(p) for p in placements.values()))))

        # Wait for the mesh operations themselves before reading host
        # state. Without this the FDB check races the very work it is
        # asserting about and would report a hole which was simply not
        # written yet.
        self._await_network_operations_complete(self.net['uuid'])

        # One cheap read of the coalescing signals before the mesh
        # assertion, recorded and not asserted on. The classification
        # below polls, and polling before the mesh check would hand a
        # hole a fold left up to a minute to be repaired by the network
        # maintainer -- but a mesh failure with no record of whether a
        # fold had even happened by then is undiagnosable, so take the
        # snapshot here and pay one API call for it.
        snapshot = self._per_node_mesh_signals(
            self.test_client.get_network_events(
                self.net['uuid'], limit=1000),
            network_node_name)
        self.addDetail(
            'per_node_mesh_signals_before_mesh_check',
            content.text_content(json.dumps(
                {'folds': snapshot[0], 'dedups': snapshot[1]},
                indent=4, sort_keys=True)))

        # The mesh assertion runs next, as close to the queue draining
        # as the test can manage. The network maintainer audits and
        # repairs a drifted mesh every thirty seconds, so every second
        # spent elsewhere first is a second in which a hole a fold
        # caused could be quietly repaired before this looks.
        self._assert_mesh_is_correct(
            self.net['vxlan_id'], participants)

        # Now the coalescing signals. Read with the API's maximum limit
        # rather than its default of 100: a burst of six instance
        # creates puts several hundred events on the network, newest
        # first, and the coalescing events are among the oldest of them.
        #
        # Events are eventually consistent -- each daemon spools locally
        # and a drainer ships batches to sf-eventlog -- so poll rather
        # than reading once. The work itself is already done by this
        # point, so this is covering the shipping lag and nothing else.
        #
        # Two deadlines, because the common outcome here is the skip
        # below -- dedups but no fold -- and waiting the full minute
        # for a fold which is not coming is merge queue wall clock
        # spent on nothing. Cluster CI only runs in the merge queue,
        # and the test above this one already budgets 120s of its own.
        # Once a dedup has arrived the drainer has demonstrably
        # shipped this burst's coalescing events, so a fold, if there
        # was one, is a batch or two behind rather than a minute.
        folds = []
        dedups = []
        events = []
        deadline = time.time() + 60
        first_dedup_at = None
        while True:
            events = self.test_client.get_network_events(
                self.net['uuid'], limit=1000)
            folds, dedups = self._per_node_mesh_signals(
                events, network_node_name)
            if folds:
                break
            if dedups and first_dedup_at is None:
                first_dedup_at = time.time()
            if first_dedup_at is not None and time.time() > (
                    first_dedup_at + POST_DEDUP_FOLD_GRACE):
                break
            if time.time() > deadline:
                break
            time.sleep(5)

        self.addDetail('per_node_mesh_folds', content.text_content(
            json.dumps(folds, indent=4, sort_keys=True)))
        self.addDetail('per_node_mesh_dedups', content.text_content(
            json.dumps(dedups, indent=4, sort_keys=True)))

        # Recorded on every outcome, because "coalescing fired, just not
        # for the mesh task" and "coalescing fired for nothing at all"
        # are different diagnoses and the difference is invisible once
        # the run is over.
        other = [
            e for e in events
            if str(e.get('message', '')) in (DEDUP_EVENT, FOLD_EVENT)
            and e not in folds and e not in dedups]
        self.addDetail('other_coalescing_events', content.text_content(
            json.dumps(other, indent=4, sort_keys=True)))

        if not folds and not dedups:
            self.addDetail('network_events', content.text_content(json.dumps(
                events, indent=4, sort_keys=True)))
            self.fail(
                'Starting %d instances across %d hypervisors on one network '
                'fans a %s operation out to each of %d participating nodes '
                'per start, but neither a "%s" naming %s nor a "%s" whose '
                'requested_task is %s reached the network\'s event stream. '
                'Per-node coalescing matched no rows at all. Either the two '
                'column key or one of its two guards is refusing the '
                'per-node queue -- read coalesce_outcome on the operations\' '
                '"execution duration" events and look for '
                'key_cannot_distinguish_queue -- or the burst produced no '
                'contention, in which case %d other coalescing events were '
                'recorded on this network and the other_coalescing_events '
                'detail says what they were.'
                % (len(instances), len(hosting), MESH_TASK, len(participants),
                   FOLD_EVENT, MESH_TASK, DEDUP_EVENT, MESH_TASK, len(other)))

        if not folds:
            # The two halves of coalescing race each other by
            # construction: the enqueue-side dedup returns the pending
            # op's uuid instead of inserting a second row, so a sibling
            # for the worker-side fold to find only exists when two
            # callers both missed that lookup. This run proved the
            # per-node key matches rows and the mesh assertion above
            # ran either way, but it did not observe a fold, and
            # failing on a race the test cannot force would buy a merge
            # queue flake rather than a signal.
            self.skipTest(
                '%d per-node "%s" event(s) for %s were recorded, so the '
                '(network_uuid, node_uuid) key is matching rows on a '
                'per-node queue, but no "%s" fold was observed in the same '
                'burst. That fold needs two enqueues to race the dedup '
                'lookup. Raise BURST before weakening this assertion, and '
                'see the per_node_mesh_dedups detail for what was seen.'
                % (len(dedups), DEDUP_EVENT, MESH_TASK, FOLD_EVENT))

        # Reaching here means a fold happened on a per-node queue and
        # the mesh was still complete on every participant afterwards,
        # which is the pair of facts this test exists to establish.
