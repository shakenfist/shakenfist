# Phase 5 — next-tier reductions, diagnosed and ratcheted

Master plan: [PLAN-database-load-reduction.md](PLAN-database-load-reduction.md)

**Status: Complete.** Both threads landed and the measured outcome is
recorded below. Phase 4 attribution had been deployed on `sfcbr` for several
days when this was planned, so the next-tier targets were diagnosed from real
per-caller data rather than guessed. This phase splits into two threads:
(a) the individual reductions, filed as GitHub issues so they can be fixed
opportunistically and owned separately; and (b) a durable ratchet — teaching
the nightly infra report to mine per-caller sf-database load so these signals
are watched going forward and cannot silently regress.

## What the attribution data actually said

Measured on the Prometheus at `maui.home.stillhq.com:9099` against the
`database_requests_total{operation, caller_daemon}` counter added in phase 4
(#3473). Total steady-state sf-database load is **~174/s over 24h** (down from
the ~527/s that started this whole effort — phases 1-2 removed ~two thirds).

A 1h snapshot initially made `GetIPAM` look like the dominant cost (~48/s,
~22% of that window). The 24h average corrected this: `GetIPAM` is
workload-correlated and *bursts* under load but averages only ~14/s at rest.
The real steady-state floor is **fixed-rate polling that runs even when the
cluster is completely idle**:

| Operation | 24h steady | Caller(s) | Nature |
|-----------|-----------|-----------|--------|
| Dequeue | 38.8/s | net 19.6, queues 19.2 | fixed-rate poll |
| GetExistingLocks | 19.2/s | queues | lock-check paired with each dequeue |
| GetBlobTransfersForNode | 19.5/s | transfers | fixed-rate poll |
| **idle-poll subtotal** | **~78/s** | | **~45% of load, workload-independent** |
| GetIPAM | 14.4/s (peaks ~48) | cluster | workload-correlated burst |
| GetInstanceAttributes | 15.0/s | spread (cluster 4.2) | mutable |
| GetReferencesFrom | 9.1/s | cluster 5.8, api 3.2 | mutable |

The lesson — recorded here deliberately — is that the ratchet baseline must be
taken from a 24h (or longer) window, never a single busy hour, or it both
overstates bursty operations and would enshrine reducible idle-poll cost as
the defended floor.

## Thread A — the reductions (filed as issues)

Each next-tier reduction is a separate change in a different daemon loop, so
each is a standalone GitHub issue rather than a single monolithic phase:

* **#3499 — queue-worker idle polling (~58/s, highest).** `Dequeue` +
  `GetExistingLocks` fire as a lockstep pair on every fixed tick in the
  `queues` daemon, and `net` polls `Dequeue` the same way. Fix: adaptive
  backoff on empty dequeue, with a latency cap. Backing off the dequeue
  collapses the paired lock check too.
* **#3500 — transfers idle polling (~20/s).** `GetBlobTransfersForNode`
  polls at a fixed rate even with no pending transfers. Fix: back off / wake
  on enqueue.
* **#3501 — cluster IPAM re-reads (~14/s steady, ~48/s burst).** Diagnosed as
  the immutable-tier cache pattern, not loop surgery: the record `get_ipam`
  returns is the static block definition
  (`uuid`/`namespace`/`network_uuid`/`ipblock`/`version`); the mutable
  allocations live in the separate `ipam_reservations` table, and `update_ipam`
  exists only to persist version upgrades. Fix: wire `get_ipam` through the
  phase 2 object cache at the immutable TTL, evicting on `update_ipam`/
  `delete_ipam`.
* **#3502 — cluster sweep attribute/reference re-reads (~10/s, lowest).**
  Mutable reads re-hydrated per-check; fix is batching within the sweep.

**Post-fix addendum (2026-07-28).** The ratchet worked: the nightly report
flagged `GetIPAM`/`cluster` at ~20x its post-#3501 goal even though the
cache fix was deployed. Investigation showed the cluster caller's load was
never cacheable at all — it was ~3,600 *phantom* `object_states` ipam rows
(state row present, `ipams` row gone) re-read every maintenance pass, with
`None` results that the cache deliberately never stores. The phantom rows
were written by in-memory-only IPAM objects leaking their state to MariaDB
(#3532), accumulated because the deleted-object sweep could not drain its
backlog (#3533), and were invisible to every cleanup path until the orphan
reconciliation sweep (#3534). Those three fixes, not further caching, are
what clears the `cluster` caller's `GetIPAM` floor. The same investigation
closed #3502: the sweep's `GetInstanceAttributes`/`GetReferencesFrom` floor
was O(blobs × instances) — `instance_usage_for_blob_uuid()` re-walked every
healthy instance (and every disk's dependency chain) for every blob — and
is now a single `instance_blob_usage()` pass per cleanup loop.

These are deliberately *not* bundled into this phase's PR. #3499 and #3500 are
behaviour changes (backoff trades a little first-task latency for a large load
reduction) and each deserves its own review. The correct order by
value-per-risk is #3499, then #3500, then #3501, then #3502.

## Thread B — the ratchet (this phase's actual deliverable)

Fixing the four issues above is necessary but not sufficient: without a
standing check, load creeps back as new loops are added. Phase 5's own work is
to make per-caller sf-database load a mined, watched signal, consistent with
the existing practice of moving hand-computation into the nightly report
precompute.

* **Where.** The nightly infra report precompute
  (`tools/infra-report-precompute.py` in the private `33fl` ops repo, not this
  repo). This phase's code change lands there; this plan documents the intent
  and the honest baseline it must encode.
* **What it computes.** Per `(operation, caller_daemon)` 24h-averaged QPS from
  `database_requests_total`, plus the cluster total, straight from the same
  Prometheus the numbers above came from.
* **What it flags.** Any caller/operation that climbs materially above the
  recorded baseline, and specifically any regression on the four operations
  the issues target — so a fix that lands and then rots is caught, and a new
  fixed-rate poll added elsewhere shows up as a new high-rate `(operation,
  caller_daemon)` pair.
* **Baseline honesty.** The ratchet records today's 24h numbers as the
  starting baseline *and* the expected post-fix floors for #3499/#3500, so it
  defends the improved target rather than the current wasteful number. Until a
  fix lands, its operation sits at "known-reducible", not "healthy".

## Success criteria

1. The nightly report emits a per-caller sf-database load section with the 24h
   baseline and flags regressions against it.
2. The four reduction issues are filed with attributed numbers and a repro
   query (done: #3499-#3502).
3. When #3499/#3500 land, the report's flagged "known-reducible" items clear
   and the defended floor drops accordingly — verifiable in the nightly output
   rather than by ad-hoc PromQL.

## Out of scope

The individual daemon-loop fixes (#3499-#3502) — tracked separately. mTLS and
OpenTelemetry span propagation, which reuse phase 4's caller-identity plumbing,
remain their own plans.

## Outcome (recorded 2026-08-19)

**Thread A — all four reductions landed and are holding.** #3499, #3500 and
#3501 closed 2026-07-26; #3502 closed 2026-07-29 alongside the #3532/#3533/#3534
phantom-row work described in the addendum above. The code is
`daemon.IdlePollBackoff` (used by the `queues`, `net` and `transfers` loops),
`get_ipam` wired through the phase 2 object cache at the immutable TTL, and
`instance_blob_usage()` replacing the O(blobs x instances) per-blob walk.

The ratchet's own verdict on those fixes, from the nightly per-caller facts:

| Pair | Baseline | Post-fix target | Measured 2026-08-18 | Verdict |
|------|----------|-----------------|---------------------|---------|
| `Dequeue` / `net` | 19.0 | 4.0 | 3.04 | cleared |
| `Dequeue` / `queues` | 18.6 | 4.0 | 3.09 | cleared |
| `GetBlobTransfersForNode` / `transfers` | 19.0 | 4.0 | 3.04 | cleared |
| `GetExistingLocks` / `queues` | 18.5 | 2.0 | 0.20 | cleared |
| `GetIPAM` / `cluster` | 5.5 | 2.0 | 0.02 | cleared |
| `GetIPAM` / `net` | 2.5 | 1.5 | 0.11 | cleared |

That is ~78/s of workload-independent polling reduced to ~9.5/s, and every
`known-reducible` entry in the baseline now reads `cleared` with none left in
`still-reducible`.

**Thread B — the ratchet is live** and has been emitting the per-caller
section nightly since it landed. It has since done its job twice over: it
re-explained an apparent four-pair regression as standing-instance-count
scaling (which is why several baselines now carry a `scaling` block rather
than an absolute ceiling), and it is currently flagging the genuine
regression described below.

**The mission target was met, and then lost.** Cluster-wide steady-state load
reached 89-92/s on 2026-08-05 to 2026-08-07 -- below the master plan's
"under 100 operations per second" success criterion -- and has since climbed
back to ~142/s (2026-08-18) at a *lower* standing instance count than when it
measured 98/s on 2026-08-09, so the climb is not object-count scaling. That
regression, and the residual lines this phase deliberately did not chase, are
the subject of the phases that follow.
