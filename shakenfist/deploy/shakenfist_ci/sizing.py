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
