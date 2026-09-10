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

Why "impossible" rather than "a lot": D23's fill-one-node test (the
companion D23/D26 half of this file, landing in the same module as a later
step) proves the *ledger* -- that a node with real, finite capacity starts
refusing at exactly its published limit once real placements are made
against it. That test necessarily consumes cluster capacity to do it, so
D26 bounds it to one hypervisor and gives it its own headroom-based skip
rules. The three tests below prove the *contract* instead -- which status
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

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


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


class TestSaturationRefusals(base.BaseNamespacedTestCase):
    """D24: one impossible-request test per deterministically-triggerable
    capacity stage."""

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'saturation'
        super().__init__(*args, **kwargs)

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
