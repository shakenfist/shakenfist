# Phase 00a: Load-aware ordering and system reservations

## Context

This is phase 00a of
[`PLAN-scheduler-reservations.md`](PLAN-scheduler-reservations.md):
a set of **static quick wins** that land ahead of the phase 0
research pass because sfcbr is hurting today. Observed
2026-07-17: a CI burst stacked three 16 GB VMs onto the 12-core
network+database node (load ~15) while two idle 24-core nodes
sat ~90% free.

The root causes, confirmed in code:

1. **Load bucketing ignores machine size.** Candidates are
   bucketed by `math.floor(cpu_load_1)` — raw load, not
   normalised by core count (`scheduler.py:447-468`). An idle
   24-core node (load 0.5) and a struggling 12-core node
   (load 0.9) share bucket 0.
2. **Uniform random selection ignores machine size.** Within
   the winning bucket, `random.shuffle` gives a 12-core node
   the same burst share as a 24-core node
   (`scheduler.py:470-476`).
3. **Node roles are invisible.** `is_network_node` /
   `is_database_node` are published into `node_metrics`
   (`daemons/resources/main.py:64-66`) but never read by the
   scheduler. There is no CPU analogue of
   `RAM_SYSTEM_RESERVATION`, and no extra RAM held back for
   MariaDB / the network daemons.
4. **CPU admission never rejects.** The hard check is
   allocated vCPUs vs `cpu_max × CPU_OVERCOMMIT_RATIO`
   (default 16, OpenStack folklore), so a 12-core node admits
   192 vCPUs. RAM always binds first; the CPU ratio is
   decorative for CI-shaped (large, correlated) workloads.

One subtlety constrains the fix: metrics are up to ~60 s stale
(resources daemon refresh interval) and the scheduler caches
for `SCHEDULER_CACHE_TIMEOUT` (5 s), so an entire CI burst
schedules against one frozen snapshot. The floor()-bucket +
shuffle is *accidentally* the only thing preventing a burst
from stacking onto a single "best" node. Naive fine-grained
load ranking would make bursts **worse**. Every change below
preserves spreading within a band of similar nodes.

Everything in this phase is an ordering / soft-preference or
published-metric change. Nothing here is throwaway: the
ordering terms translate mechanically into the ORDER BY /
tie-break surface of the reservation model (master plan open
questions 6-7), and the published reservation-adjusted
capacity fields are exactly what the phase 2 conditional
INSERT's WHERE clause will reference. The static demand
constants chosen here are the degenerate case of the learned
demand model (master plan open question 13).

No schema changes, no API changes, no new daemons. Callers
(`external_api/instance.py:787-798`,
`external_api/admin.py:80`,
`operations/node_inst_netdesc_op.py:153`) are untouched — the
`find_candidates()` interface is unchanged and all three
benefit automatically.

## Key references in the existing code

- `shakenfist/scheduler.py` — `_has_sufficient_cpu()`
  (`:114-130`, the overcommit admission check),
  `_has_sufficient_ram()` (`:132-165`, consumes
  `RAM_SYSTEM_RESERVATION` directly from config), the load
  bucketing (`:447-468`), the final shuffle (`:470-476`),
  `summarize_resources()` (`:478-544`, duplicates the
  admission formulas for the admin API and **must stay
  consistent**).
- `shakenfist/daemons/resources/main.py` — role flags into
  metrics (`:62-67`), CPU counts via libvirt (`:69-76`),
  loadavg (`:84-92`), memory (`:94-106`), per-instance vCPU /
  memory totals (`:273-277`).
- `shakenfist/util/libvirt.py` — `get_cpu_map()` (`:114`)
  wraps `getCPUMap()`, which returns **logical CPUs
  (threads)**. `cpu_max` is therefore a thread count.
- `shakenfist/config.py` — the scheduler options block
  (`:179-201`): `SCHEDULER_CACHE_TIMEOUT`,
  `CPU_OVERCOMMIT_RATIO`, `RAM_OVERCOMMIT_RATIO`,
  `RAM_SYSTEM_RESERVATION`, `MINIMUM_FREE_DISK`. New knobs
  land here.
- `shakenfist/tests/test_scheduler.py` — 354 lines,
  metrics-dict driven via the mock helper
  (`set_node_metrics_same()`); easy to extend with
  heterogeneous-cluster cases. Note `node1_net` is excluded
  in fixtures only because it is marked *not a hypervisor* —
  there is no role awareness to preserve.
- `tools/sfcbr-capacity.sh` in the 33fl repo — per-node
  load-per-core plus infra-role tags; the validation probe
  for this phase.
- Intel hybrid topology (P/E cores) is readable from
  `/sys/devices/cpu_core/cpus` and `/sys/devices/cpu_atom/cpus`
  (absent on non-hybrid parts).

## Decisions (from the management-session discussion)

- **D1 — rank by load per schedulable thread, in coarse
  buckets, keeping randomised selection within a bucket.**
  Bucket key: `cpu_load_1 / cpu_schedulable` quantised to
  0.25-wide bands. Loadavg counts runnable tasks and logical
  CPUs run them, so threads (not physical cores) is the
  semantically matching denominator; both counts are
  published either way. Coarse bands + randomised selection
  preserve burst spreading among genuinely similar nodes
  (see the staleness subtlety above).
- **D2 — core-denominated system reservations, computed and
  published by the resources daemon,** not the scheduler. The
  daemon knows the role flags and the CPU topology; the
  scheduler stays dumb and the future conditional-INSERT
  WHERE clause just references a column that is already
  there. Policy: reserve `CPU_SYSTEM_RESERVATION` (default 1)
  physical cores on every hypervisor for the OS, plus
  `CPU_INFRA_ROLE_RESERVATION` (default 1) additional cores
  if the node carries **any** cluster-wide daemon role
  (network node or database node — one extra core total, not
  one per role). Reserved cores convert to threads via
  `ceil(cpu_threads / cpu_cores)` threads per core
  (conservative on hybrid parts where threads-per-core is an
  average). RAM gets the symmetric treatment:
  `RAM_SYSTEM_RESERVATION` stays as-is (2 GB, every node) and
  a new `RAM_INFRA_ROLE_RESERVATION` (default 4.0 GB) is
  added for infra-role nodes; the daemon publishes the
  combined `memory_reserved_mb` and the scheduler consumes
  the published value instead of reading config directly.
- **D3 — headroom-weighted selection within the winning
  bucket.** Replace the uniform `random.shuffle` with a
  weighted shuffle where a node's weight is its load headroom
  toward a target:
  `max(0.1, SCHEDULER_TARGET_LOAD × cpu_schedulable − cpu_load_1)`.
  A 24-core node then draws roughly twice the burst share of
  a 12-core node even from a stale snapshot, and the "target
  CPU load per hypervisor" concept from open question 13
  enters as an ordering input with zero learning machinery.
  `SCHEDULER_TARGET_LOAD` (per schedulable thread) defaults
  to 0.75 pending step 00a-1 measurement. Use the
  Efraimidis-Spirakis A-Res method (`key = u**(1/w)`, sort
  descending) so the *whole list* is weighted-shuffled
  without replacement — callers take `candidates[0]` but
  fall back down the list on failure, so the tail order
  matters too.
- **D4 — `CPU_OVERCOMMIT_RATIO` stays thread-denominated but
  narrows to schedulable threads, with a measured default.**
  Back-brief outcome (2026-07-17): since loadavg counts
  against logical CPUs, accounting and admission stay
  thread-denominated for now — the ratio keeps meaning
  "vCPUs per logical CPU", only the base narrows from all
  threads to schedulable threads and the default drops from
  folklore 16 to the measured value (3.0 — see the
  Measurements appendix; today's viable packing on plain
  nodes is 2.3-3.0 vCPUs/thread with RAM binding first, so
  3.0 preserves normal operation while biting early on
  reserved-core infra nodes). Admission becomes
  `cpu_total_instance_vcpus + requested > cpu_schedulable × ratio`.
  This is the only user-visible semantic change in the phase;
  it gets its own commit and a release note (a cluster
  already past the new cap refuses *new* schedules until it
  drains; existing VMs are untouched). A per-core
  redefinition is deferred until there is evidence
  thread-counting misranks (revisit alongside open question
  13's demand model).
- **D5 — track P-cores vs E-cores, don't act on them.**
  Publish `cpu_cores_performance` / `cpu_cores_efficiency`
  when the sysfs paths exist; omit otherwise. No scheduling
  arithmetic consumes them — SF doesn't pin vCPUs, the kernel
  scheduler decides core-type placement, and load-per-thread
  already captures contention. An "effective cores" weighting
  is deferred until OTel-era measurements justify a
  coefficient.
- **D6 — mixed-version tolerance.** Metrics rows written by a
  not-yet-upgraded resources daemon lack the new fields. The
  scheduler must fall back per-node to the old arithmetic
  (`cpu_max` threads, config-read RAM reservation, weight 1)
  when `cpu_schedulable` is absent. The exposure window is
  one deploy roll (rows older than 120 s are already
  ignored), but the scheduler must not crash or misrank
  during it.

## Step-level guidance

Steps are sequential and dependent (00a-3 and 00a-4 consume
fields 00a-2 publishes; 00a-4's default comes from 00a-1).
Isolation `none` throughout — the management session reviews
and commits each step before the next. One commit per step.

Step 00a-1 is a **management-session task**, not a sub-agent
task: it needs live sfcbr access and operator judgement about
an acceptable saturation point.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 00a-1 — measure sfcbr demand | n/a | management session | none | Sample `node_metrics` on sfcbr across several CI bursts (via `tools/sfcbr-capacity.sh` in 33fl and/or direct `sf-ctl` / DB queries): per node, record `cpu_load_1`, `cpu_total_instance_vcpus`, `cpu_max`, core count, roles. Compute the achieved demand-per-vCPU (`load / allocated vCPUs`) at points where load-per-thread crosses ~0.75-1.0. Outputs, recorded in a **Measurements** appendix to this plan: (a) the chosen `CPU_OVERCOMMIT_RATIO` per-core default; (b) confirmation or revision of `SCHEDULER_TARGET_LOAD = 0.75`; (c) the observed demand-per-vCPU range, as the seed constant for open question 13. If burst sampling is impractical quickly, proceed with the hypothesis values and mark the appendix provisional — do not block 00a-2/00a-3. |
| 00a-2 — resources daemon: topology + reservations | medium | opus | none | In `daemons/resources/main.py` `_get_stats()`: publish `cpu_cores` (`psutil.cpu_count(logical=False)`), `cpu_threads` (`psutil.cpu_count(logical=True)`; keep `cpu_max` as-is for compatibility), and on Intel hybrid parts `cpu_cores_performance` / `cpu_cores_efficiency` parsed from `/sys/devices/cpu_core/cpus` and `/sys/devices/cpu_atom/cpus` (CPU-list format, e.g. `0-15`; omit both fields when the paths are absent). Compute `cpu_cores_reserved = CPU_SYSTEM_RESERVATION + (CPU_INFRA_ROLE_RESERVATION if config.NODE_IS_NETWORK_NODE or config.NODE_IS_DATABASE_NODE else 0)`, `threads_per_core = ceil(cpu_threads / cpu_cores)`, `cpu_schedulable = max(1, cpu_threads - cpu_cores_reserved * threads_per_core)`, `cpu_cores_schedulable = max(1, cpu_cores - cpu_cores_reserved)`; publish all of these. Publish `memory_reserved_mb = int((config.RAM_SYSTEM_RESERVATION + (config.RAM_INFRA_ROLE_RESERVATION if infra-role else 0)) * 1024)`. Add to `config.py` next to the existing scheduler options (`:179-201`): `CPU_SYSTEM_RESERVATION: int = Field(1, ...)`, `CPU_INFRA_ROLE_RESERVATION: int = Field(1, ...)`, `RAM_INFRA_ROLE_RESERVATION: float = Field(4.0, ...)` with descriptions saying cores (not threads) and GB, and that infra-role means network node or database node. Unit tests: reservation arithmetic for HT (2 threads/core), non-HT, and hybrid (24 threads / 16 cores → ceil = 2) topologies; infra-role vs plain hypervisor; sysfs absent → no P/E fields; `cpu_schedulable` floor of 1. Guard the sysfs parsing with try/except (log and omit on parse failure). Commit subject: `resources: publish CPU topology and system reservations.` |
| 00a-3 — scheduler: load-per-thread ordering + weighted selection | high | opus | none | In `scheduler.py`, replace the load bucketing (`:447-468`): per candidate, `denom = metrics.get('cpu_schedulable') or metrics.get('cpu_max', 1)` (D6 fallback), `normalised = cpu_load_1 / denom`, bucket key `math.floor(normalised / 0.25)`. Keep the existing lowest-bucket-wins structure and event shape; extend `load_detail` with `cpu_load_1`, `cpu_schedulable`, `normalised_load`, `bucket`. Replace the final `random.shuffle` (`:470-476`) with an Efraimidis-Spirakis weighted shuffle over the winning bucket: `weight = max(0.1, config.SCHEDULER_TARGET_LOAD * denom - cpu_load_1)`, sort by `random.random() ** (1.0 / weight)` descending; record per-node weights in the final-candidates audit event. Add `SCHEDULER_TARGET_LOAD: float = Field(0.75, ...)` to `config.py` (description: target sustained load per schedulable thread used for selection weighting). Unit tests (extend `tests/test_scheduler.py`, seed `random` for determinism): (a) **sfcbr regression case** — a 12-thread node at load 9 plus two 24-thread idle nodes: the loaded node must not be in the winning bucket even though floor(raw load) logic would have needed load ≥ 1.0 differences; (b) heterogeneous idle nodes land in the same bucket (spreading preserved); (c) weighted selection: over many seeded draws, a node with 2× headroom wins first place roughly 2× as often (assert a loose ratio band, not exact); (d) D6 fallback: a metrics row without `cpu_schedulable` schedules without error using `cpu_max`; (e) existing tests still pass unmodified where behaviour is unchanged. Commit subject: `scheduler: rank by load per schedulable thread.` |
| 00a-4 — admission + summarize_resources on reserved capacity | medium | opus | none | In `scheduler.py` `_has_sufficient_cpu()` (`:114-130`): `hard_max_cpus = metrics.get('cpu_schedulable', metrics.get('cpu_max', 0)) * config.CPU_OVERCOMMIT_RATIO` — the ratio stays thread-denominated (D4) but multiplies schedulable threads, falling back to the old all-threads count for un-upgraded rows (D6; slightly generous during the roll, which is the safe direction). Change the `CPU_OVERCOMMIT_RATIO` default in `config.py` from 16 to 3.0 (measured, see appendix) and rewrite its description to say vCPUs per schedulable logical CPU (thread). In `_has_sufficient_ram()` (`:132-165`): `reserved = metrics.get('memory_reserved_mb', config.RAM_SYSTEM_RESERVATION * 1024)`; subtract that instead of the direct config read. Update `summarize_resources()` (`:478-544`) to use identical arithmetic for `cpu_available` and `ram_max_per_instance` so the admin resources API agrees with admission. Update rejection-reason dicts to include the new fields (`cpu_cores_schedulable`, `memory_reserved_mb`) so audit events stay diagnosable. Unit tests: admission flips at the schedulable-thread boundary; infra-role node admits less than an identical plain node; RAM check honours published reservation and falls back to config; `summarize_resources` matches `_has_sufficient_cpu` for the same inputs. Add a release note (`docs/release_notes/` if present, else the changelog convention in the repo) covering the ratio semantic change and the drain caveat. Commit subject: `scheduler: admit against reserved, core-denominated capacity.` |
| 00a-5 — docs + validation | medium | sonnet | none | Documentation: update `docs/operator_guide/` with a scheduler page (create if missing) covering the ordering model (load per schedulable thread, coarse buckets, headroom-weighted selection), the reservation knobs (`CPU_SYSTEM_RESERVATION`, `CPU_INFRA_ROLE_RESERVATION`, `RAM_SYSTEM_RESERVATION`, `RAM_INFRA_ROLE_RESERVATION`), the new `CPU_OVERCOMMIT_RATIO` semantics with the migration note, and `SCHEDULER_TARGET_LOAD`. Touch `ARCHITECTURE.md` (scheduler section) and `AGENTS.md` (point at the new metrics fields). Validation: extend or add a `deploy/shakenfist_ci/cluster_ci_tests` assertion that a node's published metrics include `cpu_schedulable` and `memory_reserved_mb` and that an infra-role node reports reduced schedulable capacity. Post-deploy sfcbr validation is a management-session task: run a CI burst, confirm via `tools/sfcbr-capacity.sh` that the network+DB node no longer takes a disproportionate share; record the observation in this plan. Commit subject: `docs, tests: load-aware scheduling and system reservations.` |

## Step ordering and dependencies

- **00a-1 first** (or in parallel with 00a-2); its outputs
  gate only 00a-4's default value. If measurement stalls,
  00a-4 proceeds with hypothesis values and the appendix is
  marked provisional.
- **00a-2 before 00a-3 and 00a-4** — both consume the
  published fields (though both carry D6 fallbacks, tests
  target the new fields).
- **00a-3 and 00a-4 are independent of each other** but run
  sequentially, one commit each. 00a-3 is the higher-risk
  change (ordering behaviour); land and soak it first.
- **00a-5 last.**

## Success criteria

- The sfcbr incident shape is a regression test: a loaded
  small node ranks below idle large nodes even when every
  raw load is in today's bucket 0.
- Burst spreading is preserved: similar idle nodes share a
  bucket and weighted selection distributes across them
  (seeded statistical test, loose bounds — no flaky
  assertions).
- An infra-role (network / database) node publishes and is
  scheduled against reduced CPU and RAM capacity; a plain
  hypervisor loses exactly one core and 2 GB.
- `summarize_resources()` and admission use identical
  arithmetic (asserted by test, not inspection).
- A metrics row from an un-upgraded resources daemon
  schedules correctly via the fallbacks (no crash, no
  misrank beyond old behaviour).
- `CPU_OVERCOMMIT_RATIO` default is either measured or
  explicitly marked provisional in the Measurements
  appendix; the semantic change has a release note.
- P/E core counts appear in `node_metrics` on hybrid
  hardware and are absent (not zero, not crashing) elsewhere;
  nothing consumes them yet.
- `pre-commit run --all-files` passes.
- Post-deploy: a real sfcbr CI burst observed via
  `tools/sfcbr-capacity.sh` no longer concentrates on the
  network+DB node; observation recorded here.

## Back brief

Before executing, back-brief the operator: confirm the
decision set (D1-D6), especially (a) threads vs cores as the
load denominator, (b) the one-extra-core-total (not per-role)
infra reservation, (c) `RAM_INFRA_ROLE_RESERVATION = 4.0` GB
default, (d) the 0.25 bucket width and `SCHEDULER_TARGET_LOAD
= 0.75` placeholder, and (e) whether 00a-1 measurement happens
before or in parallel with implementation.

**Back-brief outcomes (2026-07-17):** (a) threads confirmed as
the accounting and load denominator "at least for now", since
loadavg counts against logical CPUs — D4 revised accordingly
(track cores, decide on threads); (b) confirmed; (c) 4.0 GB
confirmed as a configurable default; (d) confirmed, and
`SCHEDULER_TARGET_LOAD = 0.75` upgraded from placeholder to
measured (appendix); (e) measurement ran first — appendix
filled from live sfcbr snapshots. Implement all of 00a, then
soak on sfcbr.

## Review checklist for the management session

Standard checklist from the master plan, plus:

- [ ] The weighted shuffle is without replacement over the
      whole winning bucket (callers walk the list on
      failure) — not a single weighted draw.
- [ ] No behaviour change for the forced-candidate path
      (`placed_on`): reservations still apply to admission,
      ordering is irrelevant there.
- [ ] Audit `load_detail` / final-candidates events carry at
      least as much diagnostic detail as before (raw load,
      normalised load, bucket, weight per node).
- [ ] All three `Scheduler()` call sites still behave (no
      interface change leaked).
- [ ] The D6 fallbacks are per-node, not global — a mixed
      snapshot (some upgraded rows, some not) must not poison
      ranking for upgraded nodes.
- [ ] mypy coverage on the touched scheduler code is at least
      as good as before.
- [ ] Randomised tests are seeded; no statistical flake
      surface in CI.

## Measurements (appendix, filled by step 00a-1)

Sampled 2026-07-17 from live sfcbr snapshots during active CI
(`tools/sfcbr-capacity.sh` plus per-node libvirt / loadavg
probes). Point-in-time samples, not a tracked burst peak; the
burst figure below is estimated from the 2026-07-17 incident.

| node | CPU | cores/threads | roles | vCPUs alloc | load1 | load15 |
|------|-----|---------------|-------|-------------|-------|--------|
| sf-1 | Ryzen 5 3600 | 6/12 | N+D+H | 36 | 4.66 | 12.23 |
| sf-2 | Ryzen 5 3600 | 6/12 | D+H | 0 | 0.75 | 4.54 |
| sf-3 | Ryzen 5 3600 | 6/12 | H | 28 | 4.51 | 6.21 |
| sf-4 | Ryzen 5 3600 | 6/12 | H | 32 | 7.21 | 7.57 |
| sf-5 | i9-12900 | 16/24 | H | 0 | 0.17 | 7.16 |
| sf-6 | i9-12900 | 16/24 | H | 9 | 1.05 | 5.14 |

Findings:

- **Achieved packing today**: busy plain nodes run 2.3-3.0
  allocated vCPUs per thread with RAM binding first. The
  incident node (sf-1, N+D+H) at 3.0 vCPUs/thread is the
  crush case the reservations are designed to prevent.
- **Chosen `CPU_OVERCOMMIT_RATIO` (per schedulable thread,
  D4): 3.0.** Preserves today's viable packing on plain nodes
  (where RAM continues to bind first) while capping infra
  nodes meaningfully once reserved cores are subtracted: sf-1
  (2 reserved cores → 8 schedulable threads) caps at 24
  vCPUs vs 36 allocated at measurement time and 192 under
  the old default.
- **`SCHEDULER_TARGET_LOAD` (per schedulable thread): 0.75**
  confirmed. sf-1 at load15/threads ≈ 1.0 was degraded; the
  incident at ~1.25 sustained was an outage-shaped problem.
- **Observed demand-per-vCPU** (`cpu_load_1 /
  allocated_vcpus`): 0.12-0.35 in steady CI; burst peak
  estimated ~0.6 from the incident (load ~15 on ~24
  allocated vCPUs). Seed constant for open question 13's
  expected-demand model: ~0.33 steady / 0.6 conservative.
- **P/E sysfs validated on real hardware**: i9-12900 nodes
  report `/sys/devices/cpu_core/cpus` = `0-15` and
  `/sys/devices/cpu_atom/cpus` = `16-23`; the paths are
  absent on the AMD nodes (D5's omit-when-absent behaviour
  is correct).
- Incidental: sf-5 was NOT-READY (`/readyz` failing, zero
  VMs, host reachable) at measurement time — unrelated to
  this plan, flagged to the operator.

## Post-deploy observation (2026-08-27)

The last outstanding item of this phase: a real sfcbr CI burst,
observed to check that placement no longer concentrates on the
network+database node. The burst was a full day of ordinary CI
load, and the observation ran for an hour of it.

**The criterion is met.** Placement does not concentrate on
sf-1.

### What was measured, and why three ways

A single snapshot is not an answer to this question, and the
first one taken was actively misleading: at that instant sf-1
held 14.0% of the cluster's committed vCPU against 7.7% of its
hard-max capacity, or 1.82x its share, which reads like the
original incident. Three things had to be separated before that
number meant anything.

*Stock from flow.* sfcbr carries two populations. Eighteen
instances were ephemeral conductor runners in `sfcbr-*`
namespaces, minutes old; eight were static runners placed five
days earlier. Only the first says anything about the scheduler
as it behaves now. (The external instance view publishes no
`created_at`, so age came from `agent_system_boot_time`, which
every live instance had.)

*Small samples from real bias.* Counting only ephemeral
instances at that first instant gave sf-1 1.90x its share --
which is one extra four-vCPU runner on the smallest node in the
cluster. Accumulating distinct placements over the hour instead
moved it to 1.32x, and counting only placements made *during*
the window -- excluding the opening stock entirely -- to 0.84x.

*The ranking from the outcome.* The scheduler publishes its own
working in `schedule have lowest cpu load` and `schedule final
candidates` events, which say what it saw and what it kept.

### The numbers

Thirty-five placements were made during the hour, excluding the
stock present when it started. As a multiple of each node's
share of cluster hard-max capacity, where 1.00x is proportional:

| node | roles | schedulable | hard max | placements | vCPU | share |
|------|-------|-------------|----------|------------|------|-------|
| sf-1 | N+D+H | 6 | 18 | 3 | 7 | 0.78x |
| sf-2 | D+H | 8 | 24 | 5 | 11 | 0.92x |
| sf-3 | H | 10 | 30 | 4 | 24 | 1.60x |
| sf-4 | H | 10 | 30 | 2 | 8 | 0.53x |
| sf-5 | H | 22 | 66 | 8 | 27 | 0.82x |
| sf-6 | H | 22 | 66 | 13 | 40 | 1.21x |

Read that table with its sample size in mind. Thirty-five
placements across six nodes is not enough to pin any single
node's share, and watching it accumulate makes that obvious: the
node sitting above parity moved from sf-6 (1.61x at n=21) to
sf-3 (1.69x at n=32) as the window grew, which is what
small-sample noise looks like rather than a bias.

What does not move is the node this phase is about. sf-1's share
was 0.69x, 0.84x, 0.73x, 0.82x and 0.78x at n = 21, 23, 26, 32
and 35 -- below proportional at every point, on a node which was
89% filled by commitment when the window opened. The
network+database node is not being concentrated on, and the bulk
of new work lands on the two large hypervisors. That is the
behaviour this phase was for.

Two of its other success criteria are evidenced by the same
events rather than by inspection. The infra-role reservation is
live: sf-1 publishes `cpu_schedulable` 6 where the same-sized
sf-3 and sf-4 publish 10. And the load-per-thread ordering reads
the typed columns phase 1 added, not the fallback --
`cpu_schedulable_from_fallback` is `false` on every node in
every decision observed.

### One finding this raises, for phase 6

The load stage narrows to the lowest occupied bucket *before*
headroom weighting, so it can discard nodes with far more room
than the ones it keeps. In one observed decision the survivors
were sf-1 (weight 3.67) and sf-4 (6.02), while sf-5 (9.55) and
sf-6 (7.64) were eliminated for being in bucket 1 rather than
bucket 0. Across 60 decisions on 30 live runners, 8 (13%)
discarded a node whose headroom weight was higher than the best
survivor's, and 34 left only a single survivor.

The mechanism is that `cpu_load_1` measures activity, not
occupancy. A node packed with idle CI runners looks emptier than
a busy one with room to spare -- and sf-1, at 89% of its hard
max by commitment, sat in bucket 0 with the lowest normalised
load in the cluster.

This is not concentrating placement today, because the capacity
filters reject sf-1 for most runner sizes before ranking ever
runs: it has six schedulable threads and, at the time of
observation, two available vCPUs. So the risk is contained by
the capacity filter rather than removed by the ranking, which is
a weaker guarantee than it looks and worth knowing before the
ranking is reworked. Recorded here rather than fixed: phase 6
owns the ranking model, and D6's open question about whether a
preference may bid against a hard ceiling is the same question
in a different coat.

### Method

An hour of sampling every two minutes, recording each instance
uuid the first time it was seen along with the node it landed
on, so that what is counted is placements rather than the same
stock counted repeatedly. Sixty-one distinct instances were
seen; 35 of them were ephemeral runners placed after the window
opened, and those are the table above.

Read-only, against the live cluster: `tools/sfcbr-capacity.sh`
in the `33fl` repository (not this one, which is what this
plan's step 00a-5 brief implies), `/admin/resources` through the
client for the per-node ledger, and the instances' own
scheduling events for the ranking detail.
