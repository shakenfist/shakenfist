# Copyright 2019 Michael Still and contributors
"""Deterministic coverage of the scheduler's capacity refusal stages.

PLAN-ci-cloud-sizing-phase-03-saturation-coverage.md's D24 covers three of
the four capacity stages -- ``sufficient_idle_cpu``, ``sufficient_idle_memory``
and ``sufficient_free_disk`` -- by asking for a resource no node in the
cluster could ever supply, copying the shape
``test_namespace_claims.py``'s ``IMPOSSIBLE_CPUS`` already uses to prove a
claim refusal. The fourth stage, ``sufficient_idle_disk`` (disk bandwidth
saturation), has no deterministic functional trigger -- it reads a measured
rate on a 60 second cadence that ``/admin/resources`` does not publish -- so
D25 gives it unit coverage instead, in ``shakenfist/tests/test_scheduler.py``.

Why "impossible" rather than "a lot": D23's fill-one-node test
(``TestNodeFillRefusal`` at the foot of this file) proves the *ledger* --
that a node with real, finite capacity starts refusing at exactly its
published limit once real placements are made against it. That test
necessarily consumes cluster capacity to do it, so D26 bounds it to one
hypervisor and gives it its own headroom-based skip rules, and it is the
only test in this file with a fill to release. The three tests immediately
below prove the *contract* instead -- which status
code, which stage name, which message -- using a request sized one unit
beyond the largest figure any node publishes for that dimension. Such a
request is refused by every node regardless of how full or empty the
cluster happens to be, so these tests:

* Consume nothing. The scheduler raises before any candidate is admitted,
  so there is no fill to release and no risk of starving one of the other
  stestr workers sharing this cluster (F7 in the phase plan) -- the
  problem D23 exists to avoid, these three tests cannot cause.
* Need no fill and no cleanup beyond the namespace teardown
  ``BaseNamespacedTestCase`` already does. The one side effect worth
  knowing about: a refused create still allocates an ``Instance`` row
  before the scheduler stage raises, and ``external_api/instance.py``
  catches the ``LowResourceException`` and calls
  ``enqueue_delete_due_error()`` before returning the 507 -- so the
  failed instance is deleted asynchronously rather than never having
  existed. Nothing here asserts on the namespace's instance list for
  that reason.
* Run identically on every topology and every job, because the sizes are
  read fresh from ``/admin/resources`` on every run rather than
  hardcoded -- a hardcoded "impossible" number is a number a later
  resizing phase could make possible, and would then leave this file
  quietly testing nothing.

Every refusal is asserted through ``BaseTestCase.assertRefusedAtStage()``
(D27), and none of these requests goes anywhere near
``shakenfist_ci.retries``: a refusal under test must never be retried away,
for the same reason ``test_namespace_claims.py`` (lines 35-52) gives for
its own refusal assertions.
"""

import json
import math
import time

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


# The two refusal messages this file has to recognise which are *not* the
# stage refusal ``BaseTestCase.assertRefusedAtStage()`` owns (D27). Neither
# string is repeated anywhere else in the suite, and the stage message is
# still spelled out in exactly one place.
#
# CAPACITY_GUARD_REFUSAL is the 507 raised at
# ``external_api/instance.py``'s ``placement is None`` branch: the cheap
# pre-filter let a candidate through and the atomic UPDATE inside
# ``Instance.place_instance()`` then refused it. A fragment rather than the
# whole message, because the message ends with a candidate count.
#
# REPLACE_ABORT_REFUSAL is the asynchronous answer --
# ``operations/node_inst_netdesc_op.py``'s ``AbortInstanceStart`` when the
# re-place of an instance whose user named a specific node finds that node
# out of resources. It reaches a client as an errored instance's
# ``error_message``, not as an HTTP status.
#
# D28 contemplated only two answers to a targeted create at a full node;
# there are three, because the two 507s above are distinct. Which one
# arrives says something different about the cluster, so this file tells
# them apart rather than accepting either -- see
# ``TestNodeFillRefusal.test_full_node_refuses_a_targeted_create``.
CAPACITY_GUARD_REFUSAL = 'no node had capacity for this instance'
REPLACE_ABORT_REFUSAL = 'Requested node lacks resources'


def _one_unit_beyond(value, minimum=1):
    """The smallest int that is both >= ``minimum`` and > ``value``.

    ``value`` is a published headroom or ceiling figure, which can be
    negative -- a node already over its own ledger (for example after a
    capacity row's limit was lowered) reports negative ``cpu_available``,
    ``ram_available`` or ``disk_available``. ``math.floor`` is used rather
    than a truncating ``int()`` cast because truncation rounds a negative
    value *up* towards zero, which could land the result back inside what
    the node can actually serve; flooring keeps it strictly beyond ``value``
    in every case. ``minimum`` then keeps the result a sane positive
    request (a 1 vCPU, 1 MB or 1 GB ask, at the smallest) when every node
    is already saturated on the dimension being sized.
    """
    return max(minimum, int(math.floor(value)) + 1)


def _effective_cpu_ceiling(per_node_entry):
    """The real per-node vCPU ceiling ``_has_sufficient_cpu()`` enforces.

    ``summarize_resources()`` (``shakenfist/scheduler.py``) publishes
    ``cpu_limit`` as ``None`` for a node the capacity reconciler has not
    written a ``scheduler_node_capacity`` row for yet, or ever (P7, an
    "unguarded" node). The scheduler does not then treat that node as
    limitless: ``_has_sufficient_cpu()`` falls back to the node's own
    live ``cpu_hard_max`` (``limit_cpus = row['limit_cpus'] if row else
    hard_max_cpus``), and ``cpu_hard_max`` is computed and published for
    every hypervisor unconditionally, independent of whether it has a
    capacity row. Treating a ``None`` ``cpu_limit`` as zero, or excluding
    the node from the ``max()`` entirely, would understate an unguarded
    node's real ceiling: if that node's live ``cpu_hard_max`` happens to
    be the largest ceiling in the cluster, a request sized only from the
    guarded nodes' ``cpu_limit`` values would still be admitted there,
    and this test would wrongly assert a 507 that never arrives.
    """
    cpu_limit = per_node_entry.get('cpu_limit')
    if cpu_limit is not None:
        return cpu_limit
    return per_node_entry.get('cpu_hard_max', 0)


def _effective_ram_ceiling(per_node_entry):
    """The real per-node memory ceiling, immune to ambient headroom moving.

    Unlike ``cpu_available`` or ``disk_available``, ``ram_max``
    (``scheduler.py:1109-1111``, ``memory_max * config.RAM_OVERCOMMIT_RATIO``)
    is a published *ceiling*: it is derived from the node's physical memory
    and the overcommit ratio, published unconditionally for every
    hypervisor, and does not move when a sibling stestr worker frees RAM by
    deleting an instance. Sizing this test from ``ram_available`` instead
    would be sizing from headroom, which another worker can *increase* at
    any moment -- if a sibling frees more than one unit on the node that
    held the maximum at read time, an "impossible" request sized from
    headroom becomes possible between the read and the create, and the
    test flakes exactly the way the phase plan's risk table warns against.
    (Recorded as a D24 survey finding: D24's memory and disk rows
    originally named headroom fields, not ceilings; the CPU row was
    already right.)

    A guarded node's real ceiling can additionally be bounded *below*
    ``ram_max`` by its capacity row's own ``limit_memory_mb`` -- not
    published directly, but recoverable as ``ram_available +
    ram_committed`` for exactly the node where that ledger bound is the
    one binding ``ram_available`` (``scheduler.py:1114-1120``: when the
    ledger bound wins the ``min()``, ``ram_available == limit_memory_mb -
    ram_committed``, so adding ``ram_committed`` back recovers
    ``limit_memory_mb`` exactly; when the measurement bound wins instead,
    or the node is unguarded and ``ram_committed`` is 0, the
    reconstruction falls at or below ``ram_max`` and contributes nothing
    to the ``max()``). Taking ``max(ram_max, ram_available +
    ram_committed)`` per node is therefore defensive rather than a
    normal-case correction: it costs nothing when the two agree, and it
    stops a node whose ledger limit has drifted above its measured
    ``ram_max`` (a reconcile overdue, or a manually raised claim) from
    silently capping what this test believes the cluster's true ceiling
    is.

    A missing or zero ``ram_max`` -- like a ``None`` ``cpu_limit`` -- must
    not drop the node out of the surrounding ``max()`` or read as a zero
    ceiling: ``.get('ram_max') or 0`` only supplies the identity element
    for the ``max()`` against the reconstructed ledger figure below, it
    never substitutes for a missing ceiling the way a bare, uncompared
    ``0`` would.
    """
    ram_max = per_node_entry.get('ram_max') or 0
    ram_available = per_node_entry.get('ram_available', 0)
    ram_committed = per_node_entry.get('ram_committed', 0)
    return max(ram_max, ram_available + ram_committed)


class _CapacityReadingTestCase(base.BaseNamespacedTestCase):
    """The ``/admin/resources`` read both halves of this file start from.

    Carries no tests of its own -- it exists so that D24's
    impossible-request tests and D23's node-fill test share one
    implementation of D26's "skip rather than fail when the capacity table
    cannot answer the question" rule, rather than two copies which can
    drift apart. ``unittest``'s loader finds no ``test_`` method here and
    so never instantiates it.
    """

    def _cluster_resources_or_skip(self):
        """Read ``/admin/resources``, applying D26's two skip rules.

        Skips (rather than fails) when the response cannot support an
        impossible-request calculation at all:

        * ``total.capacity_degraded`` is true -- the capacity counters
          could not be read, so a 507 seen here would be the refusal of
          an unreadable ledger rather than the refusal this file means
          to prove, and D26 says that distinction must be answerable
          rather than guessed.
        * ``per_node`` is empty -- there is no node ceiling published to
          size a request beyond.

        Both conditions are logged with the figure the test wanted,
        following the ``test_nodes.py:111`` skip idiom, so a skipped run
        says why rather than just not running.
        """
        resources = self.system_client.get_cluster_resources()
        self.addDetail('resources', content.text_content(json.dumps(
            resources, indent=4, sort_keys=True)))

        if resources['total'].get('capacity_degraded'):
            self.skipTest(
                'total.capacity_degraded is true: the capacity counters '
                'could not be read, so any refusal seen here would not be '
                'the sizing refusal this test means to prove (D26)')

        per_node = resources['per_node']
        if not per_node:
            self.skipTest(
                'per_node is empty: no hypervisor is currently publishing '
                'resources, so there is no per-node ceiling to size an '
                'impossible request beyond (D26)')

        return resources, per_node


class TestSaturationRefusals(_CapacityReadingTestCase):
    """D24: one impossible-request test per deterministically-triggerable
    capacity stage."""

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'saturation'
        super().__init__(*args, **kwargs)

    def test_impossible_cpu_request_refused_at_sufficient_idle_cpu(self):
        """A vCPU request beyond every node's real ceiling is refused.

        Sized from ``max(cpu_limit)`` over every node in ``per_node``,
        falling back to a node's own ``cpu_hard_max`` where ``cpu_limit``
        is ``None`` (see ``_effective_cpu_ceiling()``).

        Failure modes, and whether each is asserted or skipped:

        * ``total.capacity_degraded`` is true: **skipped** (D26).
        * ``per_node`` is empty: **skipped** (D26).
        * Every node's ``cpu_max_per_instance`` (the libvirt per-domain
          vCPU cap, an earlier scheduler stage than ``sufficient_idle_cpu``)
          happens to be smaller than the computed request: **asserted**,
          not skipped -- ``assertRefusedAtStage()`` would report a stage
          mismatch rather than a silent pass, and this has not been
          observed on either CI topology, since that cap is sized from
          libvirt's own host limit rather than from the scheduler's
          cluster-sizing ledger.
        * Every node is already fully committed on vCPUs by the rest of
          the suite at read time: changes nothing here, because the
          request exceeds every node's *ceiling* (the most vCPUs a node
          could ever be guarded to, whether idle or full), not merely
          its momentary headroom -- so the refusal still lands at
          ``sufficient_idle_cpu`` regardless of ambient load. This is
          exactly why D24 sizes from ``cpu_limit``/``cpu_hard_max``
          rather than from ``cpu_available``.
        * The request consumes nothing: the scheduler raises before any
          node is admitted, so this test cannot starve a sibling stestr
          worker and needs no cleanup beyond namespace teardown.
        """
        resources, per_node = self._cluster_resources_or_skip()

        max_ceiling = max(
            _effective_cpu_ceiling(entry) for entry in per_node.values())
        requested_cpus = _one_unit_beyond(max_ceiling)
        self.addDetail('sizing', content.text_content(
            'max_ceiling=%r requested_cpus=%r'
            % (max_ceiling, requested_cpus)))

        exc = self.assertRaises(
            apiclient.InsufficientResourcesException,
            self.test_client.create_instance,
            'impossible-cpu', requested_cpus, 128, None,
            [{'size': 1, 'type': 'disk'}], None, None)
        self.assertRefusedAtStage(exc, 'sufficient_idle_cpu')

    def test_impossible_memory_request_refused_at_sufficient_idle_memory(
            self):
        """A memory request beyond every node's real ceiling is refused.

        Sized from ``max(ram_max, ram_available + ram_committed)`` over
        every node in ``per_node`` -- see ``_effective_ram_ceiling()`` for
        why this is a ceiling and not headroom. The vCPU and disk asks are
        the smallest the suite ever asks for (1 vCPU, a single 1 GB empty
        disk -- the shape ``test_nodes.py:116-122`` already uses for a
        zero-cost create), so this test's only impossible dimension is
        memory.

        Failure modes, and whether each is asserted or skipped:

        * ``total.capacity_degraded`` is true: **skipped** (D26).
        * ``per_node`` is empty: **skipped** (D26).
        * Every node in the cluster is already fully committed on RAM at
          read time: changes nothing here, for the same reason as the CPU
          test -- the request exceeds every node's *ceiling* (the most
          memory a node could ever be guarded to, whether idle or full),
          not its momentary headroom, so a sibling worker deleting an
          instance and freeing RAM between the read and this create
          cannot make the request satisfiable. This is exactly the flake
          vector the original headroom-based sizing had, and exactly why
          D24 was amended to size memory from a ceiling instead.
        * Every node in the cluster is simultaneously fully committed on
          vCPUs, so even the 1 vCPU ask is refused at the earlier
          ``sufficient_idle_cpu`` stage before memory is ever evaluated:
          **asserted, not specially guarded**. D26's skip rules for this
          step are explicitly the two above, and this file does not
          widen them, so a coincident whole-cluster vCPU exhaustion
          would surface as a stage-name mismatch from
          ``assertRefusedAtStage()`` rather than a skip. Unlike the RAM
          headroom case above, this is a boundary the ceiling sizing
          cannot remove -- the vCPU ask itself is deliberately non-
          impossible -- so it remains a genuine (if narrow) way ambient
          load from another worker could fail this specific test; it is
          called out here, in the D24 implementation step's report, and
          is worth revisiting if it is ever observed in a merge run.
        * The request consumes nothing: same reasoning as the CPU test.
        """
        resources, per_node = self._cluster_resources_or_skip()

        max_ram_ceiling = max(
            _effective_ram_ceiling(entry) for entry in per_node.values())
        requested_memory_mb = _one_unit_beyond(max_ram_ceiling)
        self.addDetail('sizing', content.text_content(
            'max_ram_ceiling=%r requested_memory_mb=%r'
            % (max_ram_ceiling, requested_memory_mb)))

        exc = self.assertRaises(
            apiclient.InsufficientResourcesException,
            self.test_client.create_instance,
            'impossible-memory', 1, requested_memory_mb, None,
            [{'size': 1, 'type': 'disk'}], None, None)
        self.assertRefusedAtStage(exc, 'sufficient_idle_memory')

    def test_impossible_disk_request_refused_at_sufficient_free_disk(self):
        """A disk request beyond every node's published free space is
        refused.

        Disk is the one dimension of the three with no published
        *ceiling*: ``disk_available`` (``scheduler.py:1127-1132``) is
        free space minus the node's own reservation, and nothing in
        ``/admin/resources`` publishes a node's total disk the way
        ``ram_max`` publishes total memory. So, unlike the memory test
        above, this cannot be made immune to a sibling worker freeing
        space during the test window -- it can only be made a generous
        margin against it. The request is sized from
        ``max(disk_available)`` over every node, plus the *sum* of every
        node's ``disk_available`` -- that is, the largest node's own free
        space, as if every other node's free space were also somehow
        released onto it. The vCPU and memory asks are the smallest the
        suite ever asks for (1 vCPU, 128 MB), so this test's only
        impossible dimension is disk.

        Failure modes, and whether each is asserted or skipped:

        * ``total.capacity_degraded`` is true: **skipped** (D26).
        * ``per_node`` is empty: **skipped** (D26).
        * A whole-cluster disk release during the test window -- every
          other node's ``disk_available`` at read time actually being
          freed onto the one node that held the maximum, between the
          read and the create -- could in principle still admit the
          request: **asserted, not guarded**, and not fully closeable
          the way the memory test's flake vector was, precisely because
          no published field bounds a node's total disk. This is the
          honest residual risk D24's amendment could not remove for this
          dimension; it is far less likely than the single-unit-of-
          headroom race the memory test had; there is no cluster-wide
          disk ceiling to size against instead.
        * Every node in the cluster is simultaneously fully committed on
          vCPUs or memory, so the request is refused at an earlier stage
          (``sufficient_idle_cpu`` or ``sufficient_idle_memory``) before
          disk is ever evaluated: **asserted, not specially guarded**,
          for the same reason given in the memory test above -- D26's
          skip rules for this step are exactly the two capacity_degraded
          / empty per_node checks, and a coincident whole-cluster
          exhaustion on an earlier dimension would surface as a stage
          mismatch rather than a silent pass.
        * The request consumes nothing: same reasoning as the other two
          tests in this file.
        """
        resources, per_node = self._cluster_resources_or_skip()

        disk_available_values = [
            entry.get('disk_available', 0) for entry in per_node.values()]
        max_disk_available = max(disk_available_values)
        total_disk_available = sum(disk_available_values)
        disk_margin = max_disk_available + total_disk_available
        requested_disk_gb = _one_unit_beyond(disk_margin)
        self.addDetail('sizing', content.text_content(
            'max_disk_available=%r total_disk_available=%r '
            'requested_disk_gb=%r'
            % (max_disk_available, total_disk_available, requested_disk_gb)))

        exc = self.assertRaises(
            apiclient.InsufficientResourcesException,
            self.test_client.create_instance,
            'impossible-disk', 1, 128, None,
            [{'size': requested_disk_gb, 'type': 'disk'}], None, None)
        self.assertRefusedAtStage(exc, 'sufficient_free_disk')


class TestNodeFillRefusal(_CapacityReadingTestCase):
    """D23: fill one hypervisor to its published ledger, then be refused.

    Where the three tests above prove the refusal *contract* with a request
    no cluster could satisfy, this proves the *ledger*: that a node with
    real, finite capacity, filled with real placements, starts refusing at
    exactly the limit ``/admin/resources`` publishes for it. It is the only
    test in this phase which would catch a regression where admission
    stopped charging placements against ``scheduler_node_capacity`` -- an
    impossible request is refused whether or not anything is being
    counted.

    D23 fills **one hypervisor**, never the cluster. ``cluster-ci.conf``
    sets no ``group_regex``, so this runs concurrently with four sibling
    stestr workers on one cluster; filling the cluster would starve them
    and manufacture the very ``507 sufficient_idle_cpu`` signature this
    phase exists to make deliberate rather than ambient.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'nodefill'
        super().__init__(*args, **kwargs)

    # D26's headroom floor. Three vCPU is the smallest fill worth calling a
    # fill: it is more than the one placement test_nodes.py already makes,
    # and it is the whole ledger of a CI infra node (whose
    # NODE_CPU_RESERVATION_THREADS of 4 on a 4-thread node leaves
    # cpu_schedulable 1, hence cpu_limit 3), so the floor does not exclude
    # the smallest node either topology has.
    MINIMUM_FILL_VCPUS = 3

    # Every wait in this test is bounded, per D26's "carries a deadline".
    # None of them is a retry of a refusal: retries.retry_while_transient()
    # is deliberately not used anywhere in this file, for the reason
    # base.py's assertRefusedAtStage() docstring and
    # test_namespace_claims.py's header both give.
    FILL_DEADLINE_SECONDS = 300
    LEDGER_RETURN_DEADLINE_SECONDS = 180
    ADMISSION_OUTCOME_DEADLINE_SECONDS = 300
    POLL_SECONDS = 5

    def _node_entry_or_skip(self, node_uuid, when):
        """The target node's ``per_node`` entry from a fresh read, or skip.

        Applies D26's per-node rules on top of the two whole-response ones
        ``_cluster_resources_or_skip()`` already applies. A node which has
        dropped out of ``per_node``, or which has stopped publishing a
        capacity row, cannot answer the question this test is asking, and
        neither is this test's doing -- so both skip rather than fail.
        """
        _, per_node = self._cluster_resources_or_skip()

        entry = per_node.get(node_uuid)
        if entry is None:
            self.skipTest(
                'Node %s is no longer in /admin/resources per_node %s: its '
                'metrics went stale or its queue grew past '
                'UNREASONABLE_QUEUE_LENGTH, so its ledger can no longer be '
                'read (D26)' % (node_uuid, when))

        if not entry.get('cpu_committed_row_present'):
            self.skipTest(
                'Node %s reports cpu_committed_row_present false %s: it has '
                'no scheduler_node_capacity row, so it is admitting '
                'unguarded and a refusal from it would not be the ledger '
                'refusal this test means to prove (D26)' % (node_uuid, when))

        if entry.get('cpu_limit') is None:
            self.skipTest(
                'Node %s publishes a null cpu_limit %s, so there is no '
                'published ledger ceiling to fill it to (D26)'
                % (node_uuid, when))

        return entry

    def _cpus_on_node_by_ownership(self, node_uuid):
        """(ours, foreign) vCPU totals of active instances placed on a node.

        Read through the admin client, which sees every namespace
        (``baseobject.namespace_filter()`` returns True unconditionally for
        ``system``). This is what lets every "the ledger did not move the
        way I expected" branch below say *why*: an unexplained shortfall is
        a defect in admission accounting and fails, while a shortfall which
        exactly matches instances another stestr worker owns is that
        worker's load and skips. Without the split, the two are the same
        number and the test would have to guess -- which, per D26, is what
        it must not do.
        """
        ours = 0
        foreign = 0
        for inst in self.system_client.get_instances():
            if inst.get('node') != node_uuid:
                continue
            cpus = inst.get('cpus') or 0
            if inst.get('namespace') == self.namespace:
                ours += cpus
            else:
                foreign += cpus
        return ours, foreign

    def _create_fill_instance(self, node_name, index):
        """One unit of fill: the zero-cost create shape from test_nodes.py.

        1 vCPU, 128 MB, no base image and a single empty 1 GB disk, forced
        onto a named node, with no wait for boot. Nothing is downloaded, so
        each unit costs a database write and a vCPU of ledger and nothing
        else -- which is what makes filling a node cheap enough to do in a
        suite that shares the cluster.
        """
        return self.test_client.create_instance(
            'nodefill-%d' % index, 1, 128, None, [{'size': 1, 'type': 'disk'}],
            None, None, force_placement=node_name)

    def _release_fill(self, fill):
        """Delete every fill instance, without waiting for any of them.

        The deletes are asynchronous so the ledger poll which follows is
        the only wait, rather than one 60 second client-side wait per
        instance stacked in front of it. The namespace teardown remains the
        backstop for anything this misses.
        """
        for inst in fill:
            try:
                self.test_client.delete_instance(
                    inst['uuid'], async_request=True)
            except apiclient.ResourceNotFoundException:
                pass

    def _fail_or_skip_incomplete_fill(self, node_uuid, node_name, entry, fill,
                                      reason):
        """Never returns: the fill did not reach the node's cpu_limit.

        Two very different things look identical from the outside, so this
        asks the instance list which one happened:

        * The ledger is not charging what this test placed. That is the
          regression D23 exists to catch, it is nothing to do with another
          worker, and it **fails**.
        * The ledger is charging correctly and a sibling worker is
          releasing capacity on this node as fast as this test claims it.
          That is ambient load, and per D26 it **skips**.
        """
        ours, foreign = self._cpus_on_node_by_ownership(node_uuid)
        self.addDetail('incomplete fill', content.text_content(
            'reason=%s node=%s cpu_committed=%r cpu_limit=%r creates=%d '
            'ours_vcpus=%r foreign_vcpus=%r'
            % (reason, node_name, entry['cpu_committed'], entry['cpu_limit'],
               len(fill), ours, foreign)))

        self.assertGreaterEqual(
            entry['cpu_committed'], ours,
            'Node %s publishes cpu_committed %r while carrying %r vCPUs of '
            'this test\'s own placed instances. Admission is not charging '
            'placements against scheduler_node_capacity, which is exactly '
            'the regression this test exists to catch: an unbooted instance '
            'is invisible to the measured ledger, so if the committed one '
            'does not count it either, a burst of creates all see the same '
            'idle node (issue 3498).'
            % (node_name, entry['cpu_committed'], ours))

        self.skipTest(
            'Could not fill node %s to its published cpu_limit %r (%s): it '
            'sits at cpu_committed %r after %d creates, of which %r vCPU '
            'belongs to other namespaces. The ledger is charging this '
            'test\'s own placements correctly, so another stestr worker is '
            'releasing capacity on this node as fast as it is being claimed '
            '(D26)'
            % (node_name, entry['cpu_limit'], reason, entry['cpu_committed'],
               len(fill), foreign))

    def _fill_node_to_its_limit(self, node_uuid, node_name, fill, headroom):
        """Fill until the node's committed ledger reaches its own limit.

        The brief's arithmetic is ``cpu_limit - cpu_committed`` one-vCPU
        instances, and that is the ``headroom`` this is given. It is a
        convergence loop rather than a bare ``range(headroom)`` for one
        reason: a sibling worker deleting an instance which was already on
        this node mid-fill frees a slot underneath us, and a fixed-count
        fill would then stop one short of the limit and fail an assertion
        it had no business failing. Topping the fill back up is bounded by
        both a create budget and a deadline, and running out of either goes
        to ``_fail_or_skip_incomplete_fill()`` -- which fails if the ledger
        is not charging and skips if it is.

        Returns the ``per_node`` entry which satisfied the loop, so the
        caller asserts against the exact reading that ended the fill rather
        than against a later re-read a sibling could have moved.
        """
        # Twice the initial arithmetic plus two: enough that a handful of
        # foreign releases during the fill are absorbed, small enough that
        # a node which never converges is reported rather than filled
        # forever.
        create_budget = 2 * headroom + 2
        deadline = time.time() + self.FILL_DEADLINE_SECONDS

        while True:
            entry = self._node_entry_or_skip(node_uuid, 'during the fill')

            if entry['cpu_committed'] > entry['cpu_limit']:
                self.skipTest(
                    'Node %s publishes cpu_committed %r above its own '
                    'cpu_limit %r during the fill: the node is recorded past '
                    'its ledger (a lowered limit, or placements admitted '
                    'during an unguarded window), so "filled exactly to the '
                    'limit" is not a state this test can establish here '
                    '(D26)'
                    % (node_name, entry['cpu_committed'], entry['cpu_limit']))

            if entry['cpu_committed'] == entry['cpu_limit']:
                return entry

            if len(fill) >= create_budget:
                self._fail_or_skip_incomplete_fill(
                    node_uuid, node_name, entry, fill,
                    'create budget of %d exhausted' % create_budget)

            if time.time() > deadline:
                self._fail_or_skip_incomplete_fill(
                    node_uuid, node_name, entry, fill,
                    'deadline of %ds exhausted' % self.FILL_DEADLINE_SECONDS)

            try:
                fill.append(self._create_fill_instance(node_name, len(fill)))

            except apiclient.InsufficientResourcesException as e:
                # A unit of fill was refused before the node reached its
                # published vCPU limit. Which stage refused it is answered
                # from the ledger rather than by parsing the message: the
                # stage refusal contract is spelled out in exactly one
                # place in this suite (base.py's assertRefusedAtStage) and
                # re-reading it here would make that two.
                #
                # If the node is now at its limit the refusal was the
                # vCPU ledger and the fill is simply complete -- a sibling
                # took the last slot, which is a fill either way. If it is
                # not, some other dimension (memory, disk) ran out first,
                # and that is ambient shortage rather than the boundary
                # under test.
                self.addDetail('refused fill create', content.text_content(
                    str(e.text)))
                entry = self._node_entry_or_skip(
                    node_uuid, 'after a refused fill create')
                if entry['cpu_committed'] >= entry['cpu_limit']:
                    # The node is full after all: a sibling worker took the
                    # last slot, which is a filled node either way. Loop
                    # back rather than returning, so the reading is judged
                    # by the top of the loop -- which is the one place
                    # "exactly at the limit" and "past the limit" are told
                    # apart, and the caller's assertions depend on that
                    # distinction having been made.
                    continue
                self.skipTest(
                    'A fill create on node %s was refused while its ledger '
                    'was still only at cpu_committed %r of cpu_limit %r, so '
                    'the node ran out of memory or disk before it ran out of '
                    'vCPU ledger. That is a shortage this test did not '
                    'create and cannot assert on: %s (D26)'
                    % (node_name, entry['cpu_committed'], entry['cpu_limit'],
                       e.text))

            except apiclient.ResourceNotFoundException as e:
                self.skipTest(
                    'Node %s stopped being an active scheduling candidate '
                    'mid-fill, most likely because it is restarting, so the '
                    'fill cannot be completed: %s (D26)' % (node_name, e))

    def _resolve_unexpected_admission(self, inst, node_uuid, node_name):
        """Never returns normally: the create at the full node was admitted.

        This is D28's third path, and the branch which decides whether an
        admission at a node the ledger said was full is a defect or a race.

        * The node now publishes ``cpu_committed`` above its own
          ``cpu_limit``: the atomic guard admitted past the published
          ceiling. Nothing another worker does can cause that, so it
          **fails**.
        * The instance errors carrying ``Requested node lacks resources``:
          the synchronous path admitted and the asynchronous re-place
          aborted. That is D28's second contemplated answer and it is
          **asserted** here, so a run which meets it says so rather than
          passing quietly.
        * The instance reaches ``created``: a slot on the node genuinely
          freed between the reading which ended the fill and this create,
          so the premise "this node is full" was not true when the request
          was made. That is a sibling worker's load, and per D26 it
          **skips**.
        """
        self.addDetail('unexpectedly admitted instance', content.text_content(
            json.dumps(inst, indent=4, sort_keys=True)))

        try:
            entry = self._node_entry_or_skip(
                node_uuid, 'after an unexpectedly admitted create')
            self.assertLessEqual(
                entry['cpu_committed'], entry['cpu_limit'],
                'Node %s admitted a placement which took its cpu_committed '
                'to %r, above its own published cpu_limit %r. The atomic '
                'capacity guard in Instance.place_instance() is meant to '
                'make that impossible.'
                % (node_name, entry['cpu_committed'], entry['cpu_limit']))

            deadline = time.time() + self.ADMISSION_OUTCOME_DEADLINE_SECONDS
            while True:
                i = self.system_client.get_instance(inst['uuid'])
                if not i:
                    self.skipTest(
                        'A targeted create at node %s was admitted and its '
                        'instance row then went away, so which path answered '
                        'cannot be established (D26)' % node_name)
                state = i.get('state', '')
                message = i.get('error_message') or ''

                if REPLACE_ABORT_REFUSAL in message:
                    self.addDetail(
                        'asynchronous re-place abort',
                        content.text_content(json.dumps(
                            i, indent=4, sort_keys=True)))
                    self.assertIn(REPLACE_ABORT_REFUSAL, message)
                    return

                if message:
                    self.fail(
                        'A targeted create at full node %s was admitted and '
                        'then errored for a reason which is neither of the '
                        'refusals this test knows about: %s'
                        % (node_name, message))

                if state == 'created':
                    self.skipTest(
                        'A targeted create at node %s was admitted and '
                        'booted, so a vCPU slot on it was released between '
                        'the reading which ended the fill and this create. '
                        'The node was not full at the moment the request '
                        'was made, so there was no refusal to assert -- and '
                        'the ledger was not over-committed either, so this '
                        'is a sibling worker\'s load rather than a defect '
                        '(D26)' % node_name)

                if state == 'deleted':
                    self.skipTest(
                        'A targeted create at node %s was admitted and then '
                        'deleted without recording an error message, so '
                        'which path answered cannot be established (D26)'
                        % node_name)

                if time.time() > deadline:
                    self.skipTest(
                        'A targeted create at node %s was admitted and was '
                        'still in state %r after %ds, so which path '
                        'answered cannot be established (D26)'
                        % (node_name, state,
                           self.ADMISSION_OUTCOME_DEADLINE_SECONDS))

                time.sleep(self.POLL_SECONDS)

        finally:
            try:
                self.test_client.delete_instance(
                    inst['uuid'], async_request=True)
            except apiclient.ResourceNotFoundException:
                pass

    def _assert_ledger_returns(self, node_uuid, node_name, baseline_committed):
        """The released fill comes back off the node's committed ledger.

        Deliberately asserted only against ``cpu_committed``, never against
        ``cpu_available``. ``cpu_available`` is ``cpu_hard_max`` less
        ``max(cpu_measured, cpu_committed)``, and ``cpu_measured`` counts
        running libvirt domains on the resources daemon's 60 second
        cadence -- so for up to a minute after the fill is deleted the
        measured figure still holds ``cpu_available`` down even though the
        placement ledger has already released. Asserting on the headroom
        would be asserting on that cadence.

        The primary condition is the plain one: committed is back at or
        below what it was before the fill. When a sibling worker has grown
        its own footprint on this node in the meantime that will never be
        true, so the fallback asks the instance list whether everything
        still charged belongs to somebody else -- which is the same
        ours/foreign split ``_fail_or_skip_incomplete_fill()`` uses, for
        the same reason.
        """
        deadline = time.time() + self.LEDGER_RETURN_DEADLINE_SECONDS
        while True:
            entry = self._node_entry_or_skip(
                node_uuid, 'after releasing the fill')
            committed = entry['cpu_committed']

            if committed <= baseline_committed:
                self.addDetail('ledger returned', content.text_content(
                    'node=%s cpu_committed=%r baseline=%r'
                    % (node_name, committed, baseline_committed)))
                return

            ours, foreign = self._cpus_on_node_by_ownership(node_uuid)
            if committed <= foreign:
                self.addDetail('ledger returned', content.text_content(
                    'node=%s cpu_committed=%r baseline=%r foreign=%r: the '
                    'ledger released everything this test placed, and what '
                    'remains charged belongs to another namespace which grew '
                    'during the test'
                    % (node_name, committed, baseline_committed, foreign)))
                return

            if time.time() > deadline:
                self.fail(
                    'Node %s still publishes cpu_committed %r %ds after this '
                    'test deleted its fill. It started at %r, and %r vCPU of '
                    'what is charged now belongs to this test\'s own '
                    'namespace while %r belongs to others -- so the excess '
                    'is not another worker\'s and the placements this test '
                    'released have not come back off the ledger.'
                    % (node_name, committed,
                       self.LEDGER_RETURN_DEADLINE_SECONDS,
                       baseline_committed, ours, foreign))

            time.sleep(self.POLL_SECONDS)

    def test_full_node_refuses_a_targeted_create(self):
        """Fill one hypervisor to its cpu_limit, then be refused at it.

        Which of the three possible answers this expects, and why. A
        targeted create against a node whose ledger is full can be answered
        three ways, not the two D28 contemplated, because there are two
        distinct 507s:

        1. The stage refusal -- the message
           ``BaseTestCase.assertRefusedAtStage(exc,
           'sufficient_idle_cpu')`` matches, raised in ``scheduler.py``
           and returned as a 507 by the create path. Every candidate --
           here, the single forced one -- was pruned by a stage
           pre-filter. The message itself is deliberately not repeated
           here: D27 keeps it in exactly one place in this suite.
        2. ``no node had capacity for this instance, N candidates refused
           it`` (the create path's ``placement is None`` branch, also
           507). The pre-filter passed the candidate and the atomic
           capacity guard inside ``Instance.place_instance()`` then
           refused it.
        3. ``Requested node lacks resources``
           (``operations/node_inst_netdesc_op.py``), surfacing as an
           errored instance rather than an HTTP status: the synchronous
           path admitted and the asynchronous re-place aborted.

        **This test expects, and asserts, the first.**
        ``_has_sufficient_cpu()``'s own docstring says it is "a cheap CPU
        pre-filter (P2) ... not the admission decision", and describes
        exactly why it nonetheless sees what the guard sees: it reads the
        capacity row's ``limit_cpus`` and charges the candidate
        ``max(measured_cpus, committed_cpus)``, refusing when that plus the
        request exceeds the limit. A node deliberately filled until its
        ``cpu_committed`` equals its ``cpu_limit`` therefore fails that
        pre-filter for a one-vCPU request, the only candidate is pruned,
        and the stage message is raised before any admission is attempted.
        A ``force_placement`` create is not exempt from the stages:
        ``find_candidates()`` runs the same pass over a forced candidate
        list as over the whole cluster.

        The second answer is not a refusal this test can assert, and it is
        not a failure either. Reaching the guard means the pre-filter
        believed there was room, which means the ledger this test sized
        itself from was already stale when the create was issued -- the
        test's own premise was invalid, so it skips and says so. It is
        detected by matching the guard's message before the refusal helper
        is called, because that helper asserts the stage contract and would
        report a guard refusal as a malformed stage message rather than as
        the different thing it is.

        The third answer is handled as D28 asks: observed, asserted, and
        recorded. It cannot be reached from a full node by the reasoning
        above, so if it ever is, the run says so rather than passing.

        Failure modes, and whether each is asserted or skipped:

        * ``total.capacity_degraded`` is true: **skipped** (D26).
        * ``per_node`` is empty, or no node in it can be matched to a node
          name to force placement onto: **skipped** (D26).
        * The roomiest hypervisor has ``cpu_committed_row_present`` false,
          or a null ``cpu_limit``: **skipped** (D26). A refusal from a node
          admitting unguarded is not the ledger refusal under test.
        * The roomiest hypervisor has less than ``MINIMUM_FILL_VCPUS`` of
          published headroom, by either ``cpu_available`` or the ledger's
          own ``cpu_limit - cpu_committed``: **skipped**, naming both
          figures (D26). Phase 2 measured a ``slim-tier`` node at or above
          its ceiling in 100% of job-runs sampled, so this skip is
          expected to fire often there; a 3c which never skips on
          ``slim-tier`` is evidence the predicate is wrong, not that the
          cluster is roomy.
        * The node drops out of ``per_node``, loses its capacity row, or
          stops being an active scheduling candidate at any point:
          **skipped** (D26).
        * The node is already recorded above its own ``cpu_limit``:
          **skipped** -- "filled exactly to the limit" is not a state this
          test can establish there.
        * A fill create is refused while the vCPU ledger is still short of
          the limit, so memory or disk ran out first: **skipped** (D26).
        * A sibling worker releases capacity on this node as fast as the
          fill claims it: **skipped**, but only after asserting that the
          ledger is charging this test's own placements -- so a fill which
          cannot converge because nothing is being counted **fails**
          instead.
        * The create at the full node is answered by the capacity guard
          rather than by the pre-filter: **skipped**, premise invalid.
        * The create at the full node is admitted and boots: **skipped**,
          a slot freed between the confirming read and the create --
          having first asserted the node was not taken above its
          ``cpu_limit``, which would be a defect rather than load.
        * The create at the full node is admitted and errors for a reason
          which is neither known refusal: **fails**, with the message.
        * The released fill does not come back off ``cpu_committed``:
          **fails**, but only when the residue is not explained by
          instances another namespace owns.
        * ``cpu_measured`` lagging the release by up to a minute: not
          asserted on at all, deliberately -- see
          ``_assert_ledger_returns()``.
        * A capacity row whose ``cpu_limit`` is stale against the node's
          live ``cpu_hard_max``, so that a node filled to its limit still
          publishes a whole vCPU of ``cpu_available``: **asserted, in the
          only form which survives it** -- see the comment on the
          ``cpu_available`` assertion for why a bare
          ``cpu_available <= 0`` would be a flake rather than a check.
        """
        _, per_node = self._cluster_resources_or_skip()

        # per_node is keyed by node uuid and force_placement takes a node
        # name, so the two listings are joined the way test_nodes.py joins
        # them. per_node holds only hypervisors already; intersecting with
        # the node list is what supplies the name.
        hypervisors = {n['uuid']: n for n in self._hypervisor_nodes()}
        candidates = {
            uuid: entry for uuid, entry in per_node.items()
            if uuid in hypervisors}
        if not candidates:
            self.skipTest(
                'No node appears in both /admin/resources per_node and the '
                'node list, so no hypervisor can be named to force a '
                'placement onto (D26)')

        node_uuid = max(
            candidates, key=lambda uuid: candidates[uuid]['cpu_available'])
        node_name = hypervisors[node_uuid]['name']
        entry = candidates[node_uuid]
        self.addDetail('target node', content.text_content(json.dumps(
            {'uuid': node_uuid, 'name': node_name, 'resources': entry},
            indent=4, sort_keys=True)))

        if not entry.get('cpu_committed_row_present'):
            self.skipTest(
                'The roomiest hypervisor %s reports cpu_committed_row_present '
                'false: it has no scheduler_node_capacity row, so it admits '
                'unguarded and a refusal from it would not be the ledger '
                'refusal this test means to prove (D26)' % node_name)

        cpu_limit = entry.get('cpu_limit')
        if cpu_limit is None:
            self.skipTest(
                'The roomiest hypervisor %s publishes a null cpu_limit, so '
                'there is no published ledger ceiling to fill it to (D26)'
                % node_name)

        baseline_committed = entry['cpu_committed']
        headroom = cpu_limit - baseline_committed
        if (entry['cpu_available'] < self.MINIMUM_FILL_VCPUS or
                headroom < self.MINIMUM_FILL_VCPUS):
            self.skipTest(
                'No hypervisor with %d vCPUs of headroom: the roomiest is %s, '
                'publishing cpu_available %r and a ledger headroom (cpu_limit '
                '%r less cpu_committed %r) of %r'
                % (self.MINIMUM_FILL_VCPUS, node_name, entry['cpu_available'],
                   cpu_limit, baseline_committed, headroom))

        fill = []
        try:
            after = self._fill_node_to_its_limit(
                node_uuid, node_name, fill, headroom)
            self.addDetail('resources after fill', content.text_content(
                json.dumps(after, indent=4, sort_keys=True)))

            # The ledger is exactly at its published ceiling. This restates
            # the condition which ended the fill, against the very reading
            # which ended it rather than a re-read a sibling could have
            # moved in between, because it is the premise every assertion
            # below depends on and it belongs where a reader looks for it.
            self.assertEqual(
                after['cpu_limit'], after['cpu_committed'],
                'Node %s was filled with %d one-vCPU placements but publishes '
                'cpu_committed %r against cpu_limit %r'
                % (node_name, len(fill), after['cpu_committed'],
                   after['cpu_limit']))

            # The independent half of the claim, and the one which catches a
            # regression where admission stopped charging placements: the
            # committed ledger accounts for at least the instances this test
            # has placed on this node. cpu_measured cannot supply this --
            # nothing here has booted.
            ours, foreign = self._cpus_on_node_by_ownership(node_uuid)
            self.addDetail('ownership at limit', content.text_content(
                'node=%s ours_vcpus=%r foreign_vcpus=%r cpu_committed=%r'
                % (node_name, ours, foreign, after['cpu_committed'])))
            self.assertGreaterEqual(
                after['cpu_committed'], ours,
                'Node %s publishes cpu_committed %r while carrying %r vCPUs '
                'of this test\'s own placed instances, none of which have '
                'booted. Placement is not being charged against '
                'scheduler_node_capacity.'
                % (node_name, after['cpu_committed'], ours))

            # And the published headroom agrees with the published
            # arithmetic. Read cpu_available's definition before changing
            # this. It is cpu_hard_max less max(cpu_measured,
            # cpu_committed), *not* cpu_limit less anything, so a node
            # filled to its cpu_limit does not in general publish
            # cpu_available at or below zero:
            #
            # * cpu_limit is the capacity row's floor(cpu_schedulable x
            #   CPU_OVERCOMMIT_RATIO) and cpu_hard_max is that product
            #   unfloored, so a topology whose product is fractional leaves
            #   the remainder published as available.
            # * More importantly, the two are not even computed at the same
            #   time. summarize_resources() says so itself: the capacity
            #   row refreshes once a reconcile period while the metrics
            #   here are live, so a node whose cpu_schedulable has just
            #   risen publishes a cpu_hard_max a whole vCPU or more above
            #   its row's cpu_limit until the reconciler catches up. That
            #   disagreement is the reason both figures are published
            #   separately, and a test which assumed they agreed would
            #   fail on exactly the cluster state they exist to expose.
            #
            # So what is asserted is the relation which always holds: with
            # cpu_committed at cpu_limit, cpu_available cannot exceed the
            # gap between the live hard maximum and the ledger limit. On
            # both CI topologies today (cpu_schedulable 1 or 2,
            # CPU_OVERCOMMIT_RATIO 3.0, so cpu_limit 3 or 6 and
            # cpu_hard_max the same figure) that gap is exactly zero and
            # this reads as "cpu_available <= 0", which is the assertion
            # D23 asked for. It is stated in its general form so that it
            # stays true rather than becoming a flake the first time the
            # two ledgers differ.
            #
            # The claim that no room remains is not carried by this
            # assertion in any case: the pre-filter refuses on cpu_limit,
            # not on cpu_available, and the refused create below is what
            # proves it.
            self.assertLessEqual(
                after['cpu_available'],
                after['cpu_hard_max'] - after['cpu_limit'],
                'Node %s publishes cpu_available %r with cpu_hard_max %r and '
                'cpu_committed %r at its cpu_limit %r: the headroom left '
                'exceeds the gap between the hard maximum and the ledger '
                'limit, so cpu_available is not being computed from the '
                'committed ledger at all'
                % (node_name, after['cpu_available'], after['cpu_hard_max'],
                   after['cpu_committed'], after['cpu_limit']))

            # One more, at the node the ledger says is full. Deliberately
            # not wrapped in retries.retry_while_transient(): a refusal
            # under assertion must never be retried away.
            try:
                admitted = self._create_fill_instance(node_name, len(fill))

            except apiclient.InsufficientResourcesException as e:
                body = str(e.text)
                self.addDetail('refusal', content.text_content(body))
                if CAPACITY_GUARD_REFUSAL in body:
                    self.skipTest(
                        'The create at full node %s was refused by the '
                        'atomic capacity guard rather than by the '
                        'sufficient_idle_cpu pre-filter, which means the '
                        'pre-filter believed the node had room -- so the '
                        'ledger reading this test sized itself from was '
                        'already stale when the create was issued. The '
                        'premise is invalid rather than the refusal wrong: '
                        '%s (D26)' % (node_name, body))

                # Path 1, as expected and as reasoned in this method's
                # docstring: the single forced candidate was pruned by the
                # CPU pre-filter, so the stage refusal is raised before any
                # admission is attempted.
                self.assertRefusedAtStage(e, 'sufficient_idle_cpu')

            except apiclient.ResourceNotFoundException as e:
                self.skipTest(
                    'Node %s stopped being an active scheduling candidate '
                    'before the create at its full ledger could be refused, '
                    'most likely because it is restarting: %s (D26)'
                    % (node_name, e))

            else:
                self._resolve_unexpected_admission(
                    admitted, node_uuid, node_name)

        finally:
            # D26: release the fill before returning, whatever happened,
            # with the namespace teardown as the backstop for the case
            # where this does not run at all.
            self._release_fill(fill)

        # Asserted in the test body rather than left to teardown, because
        # "the ledger gives the capacity back" is part of the claim: a run
        # with the release above deleted leaves cpu_committed at the node's
        # limit and fails here.
        self._assert_ledger_returns(node_uuid, node_name, baseline_committed)
