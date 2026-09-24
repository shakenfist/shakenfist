# Copyright 2019 Michael Still and contributors
"""Size a request beyond what any node in a cluster could ever serve.

The arithmetic ``cluster_ci_tests/test_saturation.py`` uses to build
PLAN-ci-cloud-sizing-phase-03-saturation-coverage.md's D24
"impossible" requests, kept here rather than in the test which uses it
for the reason ``retries.py`` gives for itself: this module imports
nothing from the rest of the suite and nothing from
``shakenfist_client``, so ``shakenfist/tests/test_ci_saturation.py``
can load it by path and exercise its boundaries directly. The
functional suite is a client of a deployed cluster and cluster CI only
runs in the merge queue, so logic which lives in a test module there
cannot be checked before merging -- and this is the one part of D23/D24
whose correctness does not depend on a live cluster.

Every function here is a pure function of a ``per_node`` entry from
``/admin/resources`` (``scheduler.summarize_resources()``), or of a
single number read from one. The distinction the whole file turns on is
*ceiling* versus *headroom*: a ceiling (``cpu_limit``, ``cpu_hard_max``,
``ram_max``) does not move when ambient load changes, while headroom
(``cpu_available``, ``ram_available``, ``disk_available``) *grows* when
a sibling stestr worker deletes an instance -- so a request sized one
unit beyond headroom can become servable between the read and the
create, which is the flake phase 3 exists to remove rather than cause.

Phase 5's D6 put a second, smaller thing here for the same reason: the
structural minimums a cluster CI topology has to meet
(``MINIMUM_NODES`` and the three constants beside it) and the function
which says which of them a deployed cluster misses. That one reads the
whole ``per_node`` mapping and the ``GET /nodes`` list rather than a
single entry, but it is the same kind of thing -- arithmetic over a
published reading, with no cluster needed to check it -- so
``shakenfist/tests/test_ci_structural_minimum.py`` exercises its
boundaries in ``pre-commit`` instead of in a merge-queue cluster job.
"""

import math


def one_unit_beyond(value, minimum=1):
    """The smallest int that is both >= ``minimum`` and > ``value``.

    The contract is exactly those two properties, and
    ``shakenfist/tests/test_ci_saturation.py`` asserts them directly
    rather than asserting particular return values: a request sized from
    this must exceed ``value`` (or the refusal under test never arrives)
    and must stay a request a cluster would entertain at all.

    ``value`` is a published headroom or ceiling figure, which can be
    fractional (``cpu_hard_max`` is ``cpu_schedulable *
    CPU_OVERCOMMIT_RATIO``, unfloored) and can be negative -- a node
    already over its own ledger, for example after a capacity row's limit
    was lowered, reports negative ``cpu_available``, ``ram_available`` or
    ``disk_available``.

    ``math.floor`` rather than a truncating ``int()`` cast because
    ``floor(value) + 1`` is the *smallest* integer strictly greater than
    ``value``, which is what "one unit beyond" means. Truncation would not
    be unsafe -- it rounds a negative value towards zero, so it returns a
    larger figure which still exceeds ``value`` -- and with ``minimum`` at
    1 the two cannot even be told apart, since every negative ``value``
    floors to at most 0 and is then clamped. (An earlier draft of this
    docstring claimed truncation could land the result back inside what
    the node can serve. It cannot; the flooring is about saying precisely
    what is meant, not about correcting a defect.)

    ``minimum`` keeps the result a sane positive request (a 1 vCPU, 1 MB
    or 1 GB ask, at the smallest) when every node is already saturated on
    the dimension being sized, which is when ``value`` goes negative.
    """
    return max(minimum, int(math.floor(value)) + 1)


def effective_cpu_ceiling(per_node_entry):
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


def effective_ram_ceiling(per_node_entry):
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


# The structural minimum a cluster CI topology has to meet before the
# cluster suite's results mean anything
# (PLAN-ci-cloud-sizing-phase-05-guardrails.md's D6). The first three are
# preconditions tests in ``cluster_ci_tests`` already read and *skip* on,
# which is why they are gathered here rather than left implicit: a
# topology shrunk below one of them does not fail, it quietly stops
# proving the thing the test was written to prove.
#
# Database-node count is deliberately not among them. ``slim-primary``
# deploys exactly one (its primary carries ``database_node`` without
# ``hypervisors``), so ``test_database_tier.py:37`` skips there by
# design and asserting two would fail both of that topology's jobs.

# What ``test_scheduler.py``'s ``test_affinity`` (:128) and
# ``test_binary_affinity_prefers_the_tagged_node`` (:291) each skip
# below. Both count every node ``GET /nodes`` lists, whatever its roles,
# so this counts the same way.
MINIMUM_NODES = 3

# No single test reads this one, which is why it is worth asserting: the
# node count above is satisfied by infrastructure nodes which schedule
# nothing (``slim-primary``'s primary is a database node and not a
# hypervisor), so a topology could hold three nodes while offering the
# scheduler fewer places to put an instance than the affinity tests
# reason about. Both cluster topologies deploy at least three
# hypervisors today.
MINIMUM_HYPERVISORS = 3

# What ``test_network_lifecycle.py:53`` skips below, and one more than
# ``test_stray_vxlan.py:298`` skips below. The network node carries a
# device for every active network whether or not it hosts an instance,
# so both tests need hypervisors which are *not* it to tell a
# teardown apart from a no-op.
MINIMUM_NON_NETWORK_HYPERVISORS = 2

# The total schedulable vCPU ledger, summed across hypervisors. Nothing
# skips on this; it is the figure PLAN-ci-cloud-sizing cares about and
# no existing test reads, and node count alone would not have caught
# ``slim-tier`` at a total of 12 -- that cloud had three nodes and was
# the one the whole plan was written about.
#
# 24 is exactly ``slim-tier``'s total after phase 4 doubled it, so the
# floor sits on the topology phase 4 argued has no slack and any
# reduction to it fails immediately and by name. ``slim-primary`` clears
# it at 27. The deliberate cost is that a future, smaller-on-purpose
# topology cannot be deployed without editing this constant in the same
# change.
MINIMUM_HYPERVISOR_LEDGER = 24


def hypervisor_ledger(per_node):
    """The cluster's total schedulable vCPU ledger, read as admission reads it.

    ``summarize_resources()`` publishes a ``per_node`` entry only for a
    node whose metrics say ``is_hypervisor`` and whose queue is not over
    ``UNREASONABLE_QUEUE_LENGTH`` (``shakenfist/scheduler.py:1052-1057``),
    so every entry in the mapping is already a hypervisor and no
    filtering is done here.

    Each node contributes ``effective_cpu_ceiling()`` rather than its raw
    ``cpu_limit``, and that matters most at exactly the moment this
    figure is first read. Phase 2 measured a contiguous prefix of 9 to 14
    samples -- 135 to 210 seconds -- at the start of every one of 204
    job-runs in which *every* node's capacity row reads absent at once,
    publishing ``cpu_limit: None`` while ``total.capacity_degraded``
    stayed false: an unpopulated table during warm-up, not a failed read.
    The scheduler does not treat those nodes as having no ledger, it
    falls back to their live ``cpu_hard_max``, and so does this. Summing
    ``cpu_limit or 0`` instead would read a healthy cluster as a zero
    ledger for the first three minutes of its life.

    A hypervisor which has published no fresh metrics at all is absent
    from the mapping and contributes nothing. That is a genuine
    under-count and is deliberately not corrected here, because from
    this mapping it is indistinguishable from a node which has been
    removed from the cluster. It is the caller's business to wait it out
    (``retries.retry_while_transient()``), not this function's to guess.
    """
    return sum(effective_cpu_ceiling(entry) for entry in per_node.values())


def is_single_machine(nodes):
    """Whether a reading is a single-machine deployment, not a shrunken cluster.

    ``cluster-ci.conf`` is not only run against cluster topologies.
    ``.github/workflows/scheduled-tests.yml``'s "develop branch on debian
    12 single machine" entry runs it against the ``localhost`` topology,
    deliberately, to find anything in the cluster suite which breaks on
    one node. Every structural minimum here is unmeetable there, so an
    assertion which fails on an unmet minimum has to know the difference
    between a cluster that shrank and a deployment that was never a
    cluster.

    The count is the load-bearing half, and it means more than it looks
    like it does. ``nodes`` is the roster ``GET /nodes`` publishes, and a
    roster does not shrink when a node goes quiet -- that is what
    ``per_node``'s liveness filtering is for, and why the ledger is read
    from ``per_node`` and the counts from here. A node record survives
    its node going away. So a roster of one is not "one node survived",
    it is "one node ever joined", which is a deploy that never built a
    cluster rather than a cluster that lost its members.

    The role check on top is belt and braces, and it is deliberately not
    justified by the topologies: ``slim-tier``'s primary carries
    ``database_node``, ``network_node`` and ``hypervisors`` all at once
    (``ansible/ci-topology-slim-tier.yml:65``), so "no cluster node holds
    every role" would be false if anyone said it. What the check buys is
    narrower and still worth having -- a one-node reading which is *not*
    the single-machine shape is something nobody deployed on purpose, and
    is reported rather than excused.
    """
    nodes = nodes or []
    if len(nodes) != 1:
        return False

    node = nodes[0]
    return bool(node.get('is_hypervisor')
                and node.get('is_network_node')
                and node.get('is_database_node'))


def _violation(requirement, observed, minimum, consequence):
    """One unmet minimum, as a record which can be both counted and printed."""
    return {
        'requirement': requirement,
        'observed': observed,
        'minimum': minimum,
        'message': '%s: %s, below the minimum of %s -- %s' % (
            requirement, observed, minimum, consequence),
    }


def structural_minimum_violations(per_node, nodes):
    """Which of the structural minimums a deployed topology fails to meet.

    ``per_node`` is ``/admin/resources``'s mapping of node uuid to
    published resources; ``nodes`` is the ``GET /nodes`` list. Returns a
    list of violation records -- ``requirement``, ``observed``,
    ``minimum`` and a ``message`` naming what stops being proved -- and
    an empty list when the topology is big enough. What an unmet minimum
    *means* is the caller's decision; this only counts.

    Roles are read from each node record's own ``is_hypervisor`` and
    ``is_network_node`` flags (published by ``Node.external_view()``,
    ``shakenfist/node.py:554``) rather than inferred from presence in
    ``per_node``, because the two say different things. Presence in
    ``per_node`` is a liveness statement -- fresh metrics, and a queue
    under the unreasonable length -- and a hypervisor which is briefly
    missing from the roster has not stopped being a hypervisor. The
    flags are also what ``base._hypervisor_nodes()`` and the four tests
    in this phase's F6 already filter on, so counting them the same way
    means this assertion and those skips cannot disagree about the same
    cluster. The ledger is the one figure which must come from
    ``per_node``, because that is the only place it is published.
    """
    nodes = nodes or []
    per_node = per_node or {}

    hypervisors = [n for n in nodes if n.get('is_hypervisor')]
    non_network_hypervisors = [
        n for n in hypervisors if not n.get('is_network_node')]

    # The ledger is the one figure summed over per_node rather than over
    # the roster, and per_node is a liveness statement: a hypervisor whose
    # metrics have gone stale, or whose queue is over
    # UNREASONABLE_QUEUE_LENGTH, is simply absent from it
    # (shakenfist/scheduler.py:1052-1057). The sum is then a true reading
    # of what the scheduler can see and a misleading one about the
    # topology, and the two are indistinguishable from the total alone.
    # The cluster suite runs five stestr workers with no group_regex while
    # test_saturation deliberately fills a hypervisor, so long queues and
    # late metrics are a condition this suite creates for itself. Naming
    # the gap in the message is the difference between an operator looking
    # at the topology file and looking for a missing node.
    reporting = [n for n in hypervisors if n.get('uuid') in per_node]
    ledger_consequence = (
        'the cluster publishes less schedulable vCPU than the smaller '
        'of the two cluster CI topologies does today')
    if len(reporting) < len(hypervisors):
        ledger_consequence += (
            ', and it is summed over %d of %d rostered hypervisors -- the '
            'rest published no fresh metrics, so this may be a node '
            'missing rather than a topology shrinking'
            % (len(reporting), len(hypervisors)))

    checks = (
        ('nodes', len(nodes), MINIMUM_NODES,
         "test_scheduler.py's test_affinity and "
         'test_binary_affinity_prefers_the_tagged_node both skip below '
         'this, so both would pass vacuously'),
        ('hypervisors', len(hypervisors), MINIMUM_HYPERVISORS,
         'the scheduler has fewer places to put an instance than the '
         'affinity tests reason about'),
        ('non_network_hypervisors', len(non_network_hypervisors),
         MINIMUM_NON_NETWORK_HYPERVISORS,
         'test_network_lifecycle.py skips below this (and '
         'test_stray_vxlan.py below one), so network teardown stops '
         'being checked'),
        ('hypervisor_ledger', hypervisor_ledger(per_node),
         MINIMUM_HYPERVISOR_LEDGER, ledger_consequence),
    )

    return [_violation(*check) for check in checks if check[1] < check[2]]


# What one poll of the deployed topology reads as. The test in
# cluster_ci_tests/test_nodes.py waits out the transient ones and asserts
# on the rest, so the transient set lives beside the statuses rather than
# as a string tuple at the call site, where a misspelt member would make
# the assertion fire on the first poll of a cold cluster and be found
# only in the merge queue.
READING_MET = 'met'
READING_UNMET = 'unmet'
READING_UNREADABLE = 'unreadable'
READING_SINGLE_MACHINE = 'single machine'
READING_UNCONFIRMED_SINGLE_MACHINE = 'unconfirmed single machine'
TRANSIENT_READINGS = (READING_UNMET, READING_UNREADABLE,
                      READING_UNCONFIRMED_SINGLE_MACHINE)


def classify_reading(per_node, nodes, previous=None):
    """What a poll of the deployed topology says, and why.

    Returns a ``(status, violations)`` pair: one of the ``READING_*``
    statuses above, and the list ``structural_minimum_violations()``
    returned (empty unless the status is ``READING_UNMET``). ``previous``
    is the status the last poll classified as, or None on the first.

    A single-machine reading is the only one which ends the wait without
    the minimums being met, so it is not believed on sight. It is
    reported as ``READING_UNCONFIRMED_SINGLE_MACHINE`` -- transient --
    until a second consecutive poll reads the same way. A roster does not
    shrink, but it does grow: ``slim-tier``'s primary alone carries every
    role, so a first poll which caught only the primary registered would
    otherwise skip the assertion on a real cluster, which is the vacuous
    pass it exists to prevent. The confirmation costs a real single
    machine one poll interval.
    """
    if is_single_machine(nodes):
        if previous in (READING_SINGLE_MACHINE,
                        READING_UNCONFIRMED_SINGLE_MACHINE):
            return READING_SINGLE_MACHINE, []
        return READING_UNCONFIRMED_SINGLE_MACHINE, []

    violations = structural_minimum_violations(per_node, nodes)
    return (READING_UNMET if violations else READING_MET), violations
