# Placeable capacity phase 0: decisions and the live-memory baseline

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly and ground
your answers in what the code actually does today. Do not speculate
when you could read instead. Flag uncertainty explicitly.

This is phase 0 of `PLAN-placeable-capacity.md`. Read that master
plan first, and the brief it was lifted from — the "Brief for the
shakenfist/shakenfist plan" in shakenfist/private-ci
`docs/plans/PLAN-placeable-claims-phase-00-decisions.md`. This phase
ships no production code: it produces a decisions document and a
measurement reading that the later phases cite.

## Planning effort

High. This phase is the design: it resolves the master plan's open
questions `Q1`–`Q8` against the code, and its measurement decides
whether the live-memory question is sequenced ahead of the shape
holds (`A`). The review effort is high for the same reason.

## Scope

In:

- Resolve master-plan `Q1`–`Q8` against current `develop`, or
  record which are deferred and why, as numbered decisions this
  phase produces.
- Design and take the sfcbr live-memory-against-ledger measurement
  (placeable-claims phase 0 `D9`), and state its verdict: how much
  of the post-overcommit live-memory refusal share is a genuine
  shortage and how much is the double-subtraction the survey
  confirmed, and therefore whether the live-memory question comes
  before `A`.
- Leave the later phases a decisions document they cite by this
  plan's own `D` numbers.

Out:

- Any production code, schema, API or client change. Those are
  phases 1 to 6.
- Retuning `RAM_OVERCOMMIT_RATIO` or per-host reservations. If the
  measurement shows the host counted twice, that retune is 33fl's,
  made with the per-host-resource-reservations machinery; this phase
  records the finding and hands it over, it does not apply it.
- Deciding hard claim enforcement (`CLAIM_ENFORCEMENT_HARD`), which
  is scheduler-reservations phase 5.

## What the survey found

Grounded against `develop` at `61a14af` by a read-only survey on
2026-11-04. The master plan's Prompt warned that the brief's line
numbers (from `ea39dff7c9`) had drifted. The findings below are that
survey's; **their line citations have since been repointed to
`32de6cec7f`**, the commit this phase's Results were taken at, because
they had drifted again between the two. What each finding says is
unchanged; only where it points.

**F1. The double-subtraction hypothesis is real, and the code
documents it.** The scheduler's three memory sub-checks, in order
(`shakenfist/scheduler.py`, `_has_sufficient_ram`):

- Live free memory (`:760-771`): `available =
  metrics[node]['memory_available'] - reserved`, refuse if
  `available - memory < 0`. `memory_available` is
  `psutil.virtual_memory().available`
  (`shakenfist/daemons/resources/main.py:407-410`); `reserved` is
  the node's `memory_reserved_mb` (`scheduler.py:279-285`, read
  through the method at `:615-617`).
- KSM ceiling (`:775-794`): `(memory_total_instance_actual +
  memory) / memory_max > RAM_OVERCOMMIT_RATIO`. **No reservation
  term.** `memory_max` is `psutil.virtual_memory().total`
  (`main.py:407,409`).
- Committed ledger (`:808-824`): `used_memory_mb (+ memory, less the
  create's own prior placement) > limit_memory_mb`.

The ledger limit is derived in `_derive_cpu_memory_limits`
(`shakenfist/mariadb.py:26668-26671`): `limit_memory_mb = max(0,
floor(memory_max * ram_overcommit_ratio) - memory_reserved_mb)`.
Here `memory_reserved_mb` is subtracted from **physical**
`memory_max`. The live check subtracts the **same** reservation
from `psutil.available`, which already excludes memory the host is
using — so host/reserved memory is charged twice. The docstring at
`mariadb.py:26624-26661` spells the distinction out. At a ratio of
1.0 the ledger limit is `memory_max - reserved`, the KSM ceiling is
`memory_max` (no reservation), and the live check independently
subtracts the reservation from an already-host-adjusted figure.
This is `Q2`, and it is confirmed in the code; the measurement's job
is to quantify it, not to discover it.

**F2. The measurement needs no new code.** The resources daemon
exports every raw input as a per-node Prometheus gauge: it calls
`start_http_server(config.RESOURCES_METRICS_PORT)`
(`main.py:324`, default port 13001, `config.py:592-595`) and sets a
`Gauge` per published metric (`main.py:782-785`). So
`memory_available`, `memory_max`, `memory_reserved_mb`,
`memory_total_instance_actual` (summed guest resident memory,
`main.py:537,593`) and `memory_total_instance_max` (summed guest
max, `main.py:536,592`) are all scrapeable on each hypervisor at
`:13001` over a working day with no probe to write. The same dict
is persisted to the `node_metrics` table (`main.py:787`) and the
derived ledger (`limit_memory_mb`, `used_memory_mb`) lives in
`scheduler_node_capacity` (`mariadb.py:3917-3939`,
`_derive_cpu_memory_limits` `:26618-26671`). `GET /admin/resources`
→ `summarize_cluster_resources()` (`scheduler.py:550-559`, which
delegates to `_summarize_resources()` at `:338`, per-node payload
`:457-545`) gives the derived view but not a bare
`memory_available` or raw `limit_memory_mb` per node — those come
from the gauges or the DB. **Decision follows (`D3`): the
measurement is a scrape-and-analyse task against existing gauges,
not a code change.**

**F3. The capability mechanism is where the master plan said.**
`API_CAPABILITIES` (`shakenfist/external_api/app.py:440-528`) is a
family→tokens dict; `advertised_capabilities()` (`:531-544`)
flattens it and drops `ConditionalCapability` tokens whose predicate
is false. A static token is a string appended to a family list
(example today: `'cluster-capacity'` under `'admin'`, `:448`); a
runtime-gated one is `ConditionalCapability('token', lambda: ...)`
(`:502-503`). Phases 2 and 3 use this for `C`'s and `A`'s capability
strings; nothing in phase 0 touches it.

Nothing in the survey contradicted the master plan. The one thing it
sharpened — that the baseline can be read from existing gauges — is
`D3` below.

## Decisions this phase makes about how it runs

- **D1. Scope is decisions plus one measurement, no code.** The
  deliverable is this plan's successor decisions document and a
  measurement reading, not a change to the tree. The phase is
  "complete" when `Q1`–`Q8` have answers (or recorded deferrals)
  and the live-memory verdict is written.
- **D2. The measurement is taken on sfcbr, over at least one full
  working day**, covering a period that includes a CI-busy stretch
  (the refusals are load-driven), so that live room and ledger room
  are compared under the conditions that produced the baseline.
- **D3. The measurement uses the existing per-node Prometheus gauges
  on `:13001`** (`F2`), scraped into a time series, plus the
  `node_metrics`/`scheduler_node_capacity` rows for the derived
  ledger figures. No probe code is written. 33fl owns node and
  scrape access; the phase asks for it rather than assuming it.
- **D4. The verdict is expressed as two per-node comparisons over
  the window**: host use (`memory_max - memory_available`) against
  the reservation `memory_reserved_mb`, and live room
  (`memory_available - memory_reserved_mb`) against ledger room
  (`limit_memory_mb - used_memory_mb`). Where live room is
  persistently below ledger room by about the reservation, the
  double-subtraction (`F1`) is costing a node its reservation twice;
  the verdict says by how much and on how many nodes.
- **D5. `Q1` (does the live question come first?) is answered from
  `D4` plus the baseline's live share**, not in the abstract. If the
  double-subtraction explains most of the live-memory refusal third,
  the fix is 33fl's retune and `A` proceeds; if a genuine live
  shortage remains at the baseline's share, the live-memory question
  is sequenced ahead of `A` and the master plan's order is amended.
- **D6. `Q3`–`Q7` are each researched against the code before being
  decided** (hold reference format and lifetime; the per-node held
  column's shape; whether `degraded` nodes stop placement; the
  admin-scoped evacuate hold with #1364; the `sf_claim`/`sf-client`
  shape verbs), with the reasoning and the rejected alternative
  recorded, as the brief did for its own decisions. `Q8` (whether
  `C` is retired) is decided from the caller table, which already
  points to keeping it.

The answers to `Q1`–`Q8` themselves are the decisions this phase
*produces*; they are written into the successor decisions document
as it executes, not pre-empted here.

## Step plan

All implementation by sub-agents; the management session reviews and
commits. Each step re-surveys its own citations against current
`develop` (the master plan's standing instruction).

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 0a | high | opus | none | Design the sfcbr live-memory measurement per `D2`–`D4`. Specify the exact gauges to scrape from `:13001` (`memory_available`, `memory_max`, `memory_reserved_mb`, `memory_total_instance_actual`, `memory_total_instance_max`) and the `node_metrics`/`scheduler_node_capacity` columns for the derived ledger (`limit_memory_mb`, `used_memory_mb`), the scrape cadence and window, and the two per-node comparisons of `D4`. Produce the analysis as a script or a documented query set that 33fl can run; it writes no shakenfist code. State what access it needs from 33fl and name the per-host-resource-reservations lever a retune would use. Output: the measurement method section of the decisions document. |
| 0b | high | opus | none | Research and decide `Q3`–`Q7` against current `develop`. For each: read the relevant code (claims/`admit_instance_placement`, `namespace_claims`/`scheduler_node_capacity` schema, the reconciler, node states in `shakenfist/node.py`, `sf_claim`/client-python claim verbs, affinity in `docs/user_guide/affinity.md`), state the decision, the reasoning, and the rejected alternative. Decide `Q8` from the master plan's caller table. Cross-check each decision against placeable-claims phase 0 `D2`/`D3`/`D8` so this plan does not contradict its brief. Output: the decisions section. |
| 0c | high | opus | none | Assembly and `Q1` verdict. Once 0a's measurement has been run by 33fl and its figures are in hand, write the live-memory verdict (`D5`): the two comparisons per node over the window, the share attributable to the double-subtraction vs a genuine shortage, and the answer to `Q1` — proceed to `A`, or sequence the live-memory question first and amend the master plan's order. Then check every `D` against every other and against the master plan's open questions, confirm each of `Q1`–`Q8` is answered or deferred exactly once, and amend the master plan where the verdict moved it. Output: the assembled decisions document and any master-plan amendment. Do not touch code. |

Step 0c depends on 33fl having run 0a's measurement; it is the one
step that cannot complete on planning-host access alone. If the
measurement cannot be scheduled promptly, 0b's decisions and 0a's
method can land first and 0c's verdict follow, but the phase is not
complete until the verdict is written.

## Risks and mitigations

- **The measurement needs host access this session does not have.**
  Mitigation: `D3` makes it a scrape of existing gauges, the lowest
  possible ask; 0a names exactly what it needs so 33fl can run it
  unattended, and the step split lets the code-free decisions land
  without waiting on it. Who checks: the management session confirms
  the figures arrived before marking the phase complete.
- **Deciding `Q4` (the held column) here pre-empts phase 3's
  design.** Mitigation: `Q4` is a master-plan open question, but the
  phase decides the *shape* (a ledger dimension beside `used_*`, as
  the brief's `D3`/transient-capacity `OQ5` already frame it), not
  the migration detail, which stays phase 3's. Who checks: 0b states
  explicitly what it leaves to phase 3.
- **The verdict retunes nothing, so a genuine shortage is left for
  33fl.** Mitigation: that is deliberate (`D1`, out of scope); the
  phase hands 33fl a named lever (per-host reservations) and a
  figure, and records the hand-off so it is not lost.

## Definition of done

1. The decisions document answers or defers each of master-plan
   `Q1`–`Q8` exactly once, each with reasoning and (for a decision)
   the rejected alternative.
2. The live-memory measurement has been run on sfcbr over a window
   of at least one working day that includes a CI-busy stretch, and
   its two per-node comparisons (`D4`) are recorded with figures.
3. The verdict answers `Q1`: either `A` proceeds, or the live-memory
   question is sequenced ahead of it and the master plan's Execution
   ordering is amended to match — checkable by reading the master
   plan's order prose against the verdict.
4. No file under `shakenfist/` (code) changed:
   `git diff --name-only develop HEAD -- shakenfist/ | grep -v '\.md$'`
   is empty.
5. Every `Q3`–`Q7` decision names what it leaves to its implementing
   phase, so no later phase finds the question re-opened.
6. The hand-off to 33fl (the named per-host-reservation lever and
   the measured figure) is recorded where phase 7 and 33fl will find
   it, not dropped.
7. `pre-commit run --all-files` passes from the repository root.

## Back brief

Before executing any step of this phase, back brief the operator on
your understanding of it: that it ships no code, that its measurement
is a scrape of existing per-node gauges confirmed to exist, that its
central judgement is whether the confirmed double-subtraction
explains enough of the live-memory refusal share to let `A` proceed,
and that any ratio or reservation retune it motivates is 33fl's to
apply, not this phase's.

## Results

### The live-memory measurement: method and figures

Taken 2026-11-07 against shakenfist `develop` at `32de6cec7f`. All
code citations below were re-found at that commit; the phase plan's
`F1` line numbers were taken at `61a14af` and have drifted.

#### What changed about the method, and why it matters

`D3` assumed this measurement needed 33fl to scrape each
hypervisor's `:13001` endpoint over a future working day. It does
not. **All five gauges are already in maui's Prometheus** at
`http://maui:9099`, `job="shakenfist"`, one series per node for
`sf-1` through `sf-6`, with history back to 2026-08-06 (93 days).
The resources daemon publishes each stat key as a bare `Gauge`
with no name prefix (`shakenfist/daemons/resources/main.py:784`),
so the series are named exactly `memory_available`, `memory_max`,
`memory_reserved_mb`, `memory_total_instance_actual` and
`memory_total_instance_max`. Confirmed by reading
`/api/v1/label/__name__/values`.

So the measurement is **retrospective**: a window is chosen from
history rather than scheduled into the future. `D2`'s requirement
that it cover a CI-busy working day becomes a selection criterion
instead of a scheduling constraint, and `D3`'s ask of 33fl
collapses to near-nil (see the hand-off below).

What is *not* in Prometheus is the derived ledger. `limit_memory_mb`,
`used_memory_mb`, `limit_cpu` and `used_cpu` are all absent — the
`scheduler_node_capacity` row is persisted to MariaDB and never
exported. Michael's decision is that this measurement runs from
Prometheus history alone, with no cluster or database access. That
is possible for one of the two columns and not the other.

#### The two derived ledger columns

**`limit_memory_mb` is computed exactly.** `_derive_cpu_memory_limits`
(`shakenfist/mariadb.py:26668-26671`) is

```
limit_memory_mb = max(0, floor(memory_max * ram_overcommit_ratio)
                         - memory_reserved_mb)
```

and every input is a gauge. The ratio in force on sfcbr is **1.0**,
set deliberately as an OOM defence in
`33fl/group_vars/allsf.yml:52`, with the reasoning — kolla CI guests
dirty ~16 GB of unique pages each, KSM recovers almost nothing, and
sf-6 took global OOM kills at ~1.5x allocated on 2026-08-04/05 — at
`33fl/group_vars/allsf.yml:17-45`. Confirmed unchanged. The
reservation was also constant across both measurement windows
(`min_over_time != max_over_time` returns no series), so the
derivation is stable within them.

**`used_memory_mb` has only a proxy, and the proxy is biased in a
known direction.** The ledger's figure is
`SUM(instances.memory)` over every *placed, non-deleted* instance
(`_RECONCILE_USAGE_SQL`, `shakenfist/mariadb.py:26872-26899`); the
comment above it at `:26802-26813` states the distinction out loud
— these are allocation ledgers, not measurements, and a
powered-off instance or one placed but not yet defined in libvirt
still holds its reservation there. `memory_total_instance_max` sums
`domain.info()[1]` over *active libvirt domains only*
(`shakenfist/daemons/resources/main.py:528-535, 592`). Therefore:

- The proxy can only **undercount**:
  `used_memory_mb >= memory_total_instance_max` always, because the
  placed set contains the active set and both sum the same nominal
  per-instance figure.
- So **proxy ledger room is an upper bound on real ledger room**.
  Every conclusion below that says "the ledger is the tighter
  constraint" is therefore conservative: the real ledger is tighter
  still. Conclusions that depend on ledger room being *large* would
  not be safe, and none are drawn.
- The error cannot be bounded numerically from Prometheus.
  `instances_total` would be the natural bound, but it is
  incremented inside the same `if active:` branch
  (`main.py:534`), so it never differs from `instances_active` —
  verified over 72 hours, no node where they diverge. Prometheus
  holds **no** count of placed-but-inactive instances, which is
  exactly the population the proxy misses.
- Duplicated `INSTANCE_LOCATION` rows after a lost node
  (`mariadb.py:26819-26828`) are a second, unbounded source of the
  same one-directional error.

No figure below is presented as an exact ledger figure. Where a
ledger quantity appears it is labelled "proxy", and settling its
absolute value needs one `SELECT` against
`scheduler_node_capacity` — the single thing this method cannot do.

#### Window

**2026-11-04T00:00Z to 2026-11-07T00:00Z**, 72 hours, three
consecutive working days (Wed/Thu/Fri), sampled at 300 s. It is
representative because it holds the three highest runner-create
refusal days in the whole 93-day history — 87, 91 and 97 refusals
a day from `increase(conductor_runner_create_refused_total[1d])`,
against 2 to 44 a day through late October — and it sits inside the
latest segment of private-ci
`docs/plans/PLAN-placeable-claims-phase-04a-regression.md:528-568`,
where runner-create refusals ran 69.3 a day and in-job 89.2 a day.

Two checks against window-shopping:

- **A second busy window, 2026-10-28 to 2026-10-31** (72 h, the
  previous refusal peak and a different segment) reproduces every
  conclusion, with the headline figure moving from 27.2% to 27.1%.
- **The busiest single day re-run at 60 s** (2026-11-06, 24 h,
  7911 node-samples) gives 22.5% on the same figure and does not
  change any sign.

Sample completeness in the primary window is 4968 node-samples of
a possible 5190 (95.7%); the gaps are deploys and daemon restarts.

#### `D4` comparison 1: host use against the reservation

`memory_max - memory_available` against `memory_reserved_mb`, MB,
percentiles over the window.

| node | reserved | host use p5/p50/p95 | host use > reserved |
|---|---|---|---|
| sf-1 | 10526 | 8222 / 12888 / 19368 | 61.5% |
| sf-2 | 10526 | 9576 / 11453 / 29233 | 60.9% |
| sf-3 | 6430 | 12890 / 14440 / 33134 | 99.2% |
| sf-4 | 6420 | 8915 / 16668 / 35595 | 98.7% |
| sf-5 | 6389 | 10300 / 15687 / 31872 | 99.3% |
| sf-6 | 6389 | 15665 / 17930 / 34411 | 99.4% |

The reservations are not uniform: sf-1 and sf-2 carry 10.28 GiB
against 6.24-6.28 GiB elsewhere, consistent with the templated
`max(2.0, 10% of RAM)` plus the 4.0 GB infra bump on the two
database-tier nodes
(`shakenfist/deploy/collection/roles/node/templates/config:29`,
default at `roles/node/defaults/main.yml:32`, config key
documented at `shakenfist/config.py:719-727`).

So **host use exceeds the whole reservation on 61% to 99% of
samples**, and at the median on every node. `psutil`'s `available`
has already removed more memory than the reservation is asking to
protect, and the live check at `shakenfist/scheduler.py:760-771`
subtracts it again. `F1` is confirmed as arithmetic.

But the comparison as `D4` words it conflates guest memory into
"host use", and that matters. `process-exporter` runs on all six
nodes (`job="process-exporter"`), so the `kvm` process group's RSS
gives a direct guest-resident figure and the host's own footprint
can be separated:

| node | reserved | host use less guest RSS p5/p50/p95 | exceeds reserved |
|---|---|---|---|
| sf-1 | 10526 | -2790 / 2485 / 4290 | 0.1% |
| sf-2 | 10526 | -3659 / 686 / 3899 | 0.0% |
| sf-3 | 6430 | -5174 / 1430 / 2461 | 1.3% |
| sf-4 | 6420 | -5629 / 818 / 3395 | 1.3% |
| sf-5 | 6389 | -5223 / 3291 / 4489 | 0.6% |
| sf-6 | 6389 | -4586 / 1570 / 3908 | 0.0% |

(Negative values are page cache counted as unavailable net of
guest RSS; they are an artefact of mixing two exporters' notions
of "used" and should be read as "well below the reservation".)

The host's non-guest footprint is **0.7 to 3.3 GiB at the median
against a 6.2 to 10.3 GiB reservation**, and exceeds the
reservation on under 1.5% of samples everywhere. So the double
subtraction is not duplicating a cost the host is actually
incurring; it is adding a second buffer of the reservation's full
size on top of a genuinely-measured host use that is a third to a
quarter of it. That second buffer is not obviously unprincipled —
`33fl/group_vars/allsf.yml:22-25` and
`shakenfist/scheduler.py:796-808` both note that the measurement
lags placement, and a buffer against that lag is what the
reservation is doing in the live check. Whether it is the *right*
buffer is a separate question from whether it is a duplicate.

#### `D4` comparison 2: live room against ledger room

Live room `memory_available - memory_reserved_mb` against proxy
ledger room `limit_memory_mb - memory_total_instance_max`, MB. The
`gap` column is ledger room minus live room, so a **positive** gap
is the plan's predicted signature (live room below ledger room).

| node | live room p5/p50/p95 | proxy ledger room p5/p50/p95 | gap p5/p50/p95 | gap > 0 |
|---|---|---|---|---|
| sf-1 | 34364 / 40844 / 45510 | 29156 / 37348 / 45540 | -11176 / -3533 / 582 | 32.8% |
| sf-2 | 24499 / 42279 / 44156 | 8676 / 37348 / 45540 | -23034 / -6720 / 3013 | 33.3% |
| sf-3 | 24694 / 43388 / 44938 | 4580 / 41444 / 41444 | -27941 / -3571 / -2258 | 0.1% |
| sf-4 | 22220 / 41163 / 48900 | 471 / 33239 / 49623 | -28835 / -3875 / 2349 | 35.7% |
| sf-5 | 25612 / 41794 / 47184 | 4236 / 41100 / 49292 | -28806 / -1734 / 2210 | 16.5% |
| sf-6 | 23074 / 39555 / 41820 | 141 / 37005 / 37005 | -27438 / -3750 / -2496 | 0.6% |

Full gap distribution (MB):

| node | p0 | p5 | p25 | p50 | p75 | p95 | p100 |
|---|---|---|---|---|---|---|---|
| sf-1 | -18245 | -11176 | -6945 | -3533 | 64 | 582 | 2812 |
| sf-2 | -36480 | -23034 | -11564 | -6720 | 2167 | 3013 | 3307 |
| sf-3 | -40155 | -27941 | -13127 | -3571 | -2488 | -2258 | 2010 |
| sf-4 | -43818 | -28835 | -10777 | -3875 | 1585 | 2349 | 2577 |
| sf-5 | -39788 | -28806 | -12696 | -1734 | -766 | 2210 | 3697 |
| sf-6 | -38783 | -27438 | -15040 | -3750 | -3017 | -2496 | 3216 |

#### The figures point the other way, and this needs stating plainly

**The predicted signature is absent. Live room is not persistently
below ledger room by about the reservation; it is persistently
*above* ledger room.** The median gap is negative on all six
nodes, by 1.7 to 6.7 GB, and the p5 gap runs -11 to -29 GB (-44 GB at
p0). The
gap is positive on 0.1% to 36% of samples depending on the node,
and never by anything like the reservation. The second busy window
is the same, more strongly (medians -2.1 to -12.7 GB). This is not
a measurement artefact of the proxy: the proxy can only *overstate*
ledger room, so correcting it would push the gap further negative.

The arithmetic says why. At `ram_overcommit_ratio = 1.0`,

```
ledger room - live room = host_use - used_memory_mb
```

— the two reservations cancel. The gap is therefore not a function
of the reservation at all; it is the difference between what the
host has actually faulted in and what the ledger has committed.
And on sfcbr the second term dominates the first, because guests
are charged at nominal allocation and have not faulted anything
like it. `memory_total_instance_max - memory_total_instance_actual`
is **exactly 0 at every percentile on every node**: nothing
balloons, so the nominal and "actual" gauges are the same number,
as `33fl/group_vars/allsf.yml:39-42` warned.

That identity relocates the reservation's real cost. With
`actual == nominal`, proxy ledger room is the KSM headroom minus
the reservation, exactly:

| node | KSM headroom p5/p50/p95 | proxy ledger room p5/p50/p95 | KSM less ledger, p50 | reserved |
|---|---|---|---|---|
| sf-1 | 39682 / 47874 / 56066 | 29156 / 37348 / 45540 | 10526 | 10526 |
| sf-2 | 19202 / 47874 / 56066 | 8676 / 37348 / 45540 | 10526 | 10526 |
| sf-3 | 11010 / 47874 / 47874 | 4580 / 41444 / 41444 | 6430 | 6430 |
| sf-4 | 6891 / 39659 / 56043 | 471 / 33239 / 49623 | 6420 | 6420 |
| sf-5 | 10625 / 47489 / 55681 | 4236 / 41100 / 49292 | 6389 | 6389 |
| sf-6 | 6530 / 43394 / 43394 | 141 / 37005 / 37005 | 6389 | 6389 |

The reservation is subtracted twice on sfcbr, as `F1` and `Q2`
suspected — but the two places are the **ledger** and the **KSM
ceiling**, not the ledger and the live check. The live check is the
loosest of the three sub-checks almost always.

#### Which sub-check actually refuses

Per node-sample, applying each of `_has_sufficient_ram`'s three
sub-checks (`shakenfist/scheduler.py:753-824`) to a 16384 MB shape
— the size of `12/16384/160`, the shape refused most in the latest
04a segment:

| node | samples | live refuses | KSM refuses | ledger refuses | live is sole refuser |
|---|---|---|---|---|---|
| sf-1 | 829 | 0 | 0 | 0 | 0 |
| sf-2 | 829 | 18 | 23 | 133 | 0 |
| sf-3 | 829 | 6 | 139 | 163 | 0 |
| sf-4 | 828 | 22 | 97 | 123 | 0 |
| sf-5 | 826 | 9 | 138 | 167 | 1 |
| sf-6 | 827 | 10 | 157 | 203 | 0 |
| **all** | **4968** | **65** | **554** | **789** | **1** |

The committed ledger is the tightest of the three on 64% to 100%
of samples per node, and it refuses in 789 of the 790 samples
where any check refuses. The
live check is the sole reason a node is dropped in **1 node-sample
in 4968** (0.02%), and that one sample would have cleared if the
reservation were subtracted once rather than twice. At 60 s
resolution on the busiest day the figure is 2 in 7911.

Counterfactually removing each reservation term:

| node | samples | refusals solely from the ledger's reservation term | % of that node's refusals |
|---|---|---|---|
| sf-1 | 829 | 0 | 0.0% |
| sf-2 | 829 | 93 | 69.9% |
| sf-3 | 829 | 24 | 14.7% |
| sf-4 | 828 | 23 | 18.7% |
| sf-5 | 826 | 29 | 17.3% |
| sf-6 | 827 | 46 | 22.7% |
| **all** | **4968** | **215** | **27.2%** |

(A "reservation-only" refusal is one the ledger refuses while both
the KSM ceiling and the live check admit. Dropping the reservation
from `limit_memory_mb` makes that check identical to the KSM
ceiling, so the counterfactual is exact given the proxy.)

**So the double-subtraction costs roughly 27% of per-node memory
refusals — but through `limit_memory_mb`, not through the live
check.** 215 node-sample refusals against 1. The second window
gives 27.1% and 0; the 60 s day gives 22.5% and 2. The reservation
withholds 46680 MB (45.6 GiB) of `limit_memory_mb` across the six
nodes, and that is the number a retune moves.

#### How much of the live-memory margin the double-subtraction is

| node | live check refuses | would refuse with a single subtraction | reservation as % of median live room |
|---|---|---|---|
| sf-1 | 0.0% | 0.0% | 25.8% |
| sf-2 | 2.2% | 0.0% | 24.9% |
| sf-3 | 0.7% | 0.0% | 14.8% |
| sf-4 | 2.7% | 0.8% | 15.6% |
| sf-5 | 1.1% | 0.0% | 15.3% |
| sf-6 | 1.2% | 0.5% | 16.2% |

The reservation is 15% to 26% of the live margin a node reports,
and removing it would take live-check refusals of a 16 GiB shape
from 0.0-2.7% of samples to 0.0-0.8%. That is the whole of what
the live-side double-subtraction is worth: a few percent of a
check that is almost never binding.

#### One node behaves differently, and one cluster-level fact

**sf-1 never refuses a 16 GiB shape on any of the three checks**,
in either window. It carries the largest reservation (10.28 GiB,
infra bump) and the smallest guest allocation: its proxy ledger
room p5 is 29156 MB where sf-6's is 141 MB. It is the quietest
hypervisor in the fleet by committed memory, so no check binds.
sf-2 shares the same reservation and is the *most* affected by it
(69.9% of its refusals are reservation-only), which is the
expected consequence of a 10.28 GiB subtraction on a node that
does carry load. The other difference is sf-3 and sf-6, whose
proxy ledger room has p50 equal to p95 — long plateaux at a fixed
guest allocation, consistent with static runners rather than
transient CI guests — and whose gap is negative at every
percentile up to p95.

**At no sample in either window did every node refuse the shape.**
826 of 826 complete cluster samples in the primary window, and 865
of 865 in the second, had at least one node that would admit a
single 16 GiB guest. A single-shape create would have been placed
at every moment sampled. The refusals 04a counted must therefore
come from sets of shapes, from the sub-sampling-interval
transients the gauges smooth over, from the proxy's undercount, or
from any combination of the three. The third of those deserves the
most weight and bounds what this paragraph can claim: real
refusals certainly occurred in this window, so where the proxy
says a node always had room, the proxy is loose rather than the
refusals imaginary. This line is therefore not evidence about
fragmentation on its own. It is consistent with 04a's
fragmentation finding (shakenfist/private-ci
`PLAN-placeable-claims-phase-04a-regression.md`, `F8` and `D5`:
374 of 461 in-job refusals in the latest segment are packing
failures) and with the constraint that plan referred inward — a
hold has to defend a slot for a *shape*, and aggregate headroom is
not the binding quantity — but the independent evidence for that
is 04a's, not this measurement's.

#### Method, re-runnable

The script is
`scratchpad/step-0a/measure-live-vs-ledger.py`, standard library
only, read-only, no cluster or database access:

```
python3 -I measure-live-vs-ledger.py \
    --prometheus http://maui:9099 \
    --start 1793750400 --end 1794009600 --step 300 --ratio 1.0
```

`--start`/`--end` are unix seconds; `--ratio` must be set to the
`RAM_OVERCOMMIT_RATIO` in force. It issues six `/api/v1/query_range`
calls (the five gauges plus the `kvm` process group's RSS), joins
them on the step timestamps, drops any timestamp missing a gauge,
and prints the two `D4` comparisons, the gap distribution, the
sub-check attribution, the counterfactuals and the cluster view.
Outputs for the three runs above are beside it as
`run-2026-11-04-to-11-07.txt`, `run-2026-10-28-to-10-31.txt` and
`run-2026-11-06-step60.txt`.

To settle what the proxy cannot, one query is needed:

```sql
SELECT node_uuid, limit_memory_mb, used_memory_mb
  FROM scheduler_node_capacity;
```

That is not required for any conclusion above, because the proxy's
bias runs the conservative way for all of them.

#### What 33fl would need to provide, and the retune lever

**For the measurement: effectively nothing.** `D3` asked 33fl for
node and scrape access. Neither is needed. Everything above was
taken from `http://maui:9099` over plain HTTP from a planning host,
and the only 33fl artefacts read were the repository's own
`group_vars` and `host_vars` files. Recording this as a result:
phase 0's one step that `D1` expected could not complete on
planning-host access alone can in fact complete on it, and `D3`'s
ask is withdrawn. The one residual ask is optional — a read of
`scheduler_node_capacity` to replace the `used_memory_mb` proxy
with the ledger's own figure.

**For a retune, the lever is `NODE_RAM_RESERVATION_GB`** — a
per-node float in GB (`shakenfist/config.py:719-727`), rendered as
`SHAKENFIST_NODE_RAM_RESERVATION_GB` from the `node` role's
`node_ram_reservation_gb` variable
(`shakenfist/deploy/collection/roles/node/templates/config:29`,
default 2.0 at `roles/node/defaults/main.yml:32`, declared in
`roles/node/meta/argument_specs.yml:118`). It is overridable
per-host from the 33fl inventory, exactly as
`33fl/host_vars/sf-1.yml:69` already overrides its CPU sibling.
The measured values in force are 10526 MB on sf-1 and sf-2 and
6389-6430 MB on sf-3 to sf-6.

Two things about that lever, from the figures:

- It is the **ledger's** cost, not the live check's. Lowering it
  raises `limit_memory_mb` one-for-one and relaxes the check that
  is actually binding (27% of refusals), via
  `mariadb.py:26668-26671`. Lowering it also relaxes the live
  check, but the live check is worth 1 node-sample in 4968.
- The alternative retune, raising `RAM_OVERCOMMIT_RATIO` above
  1.0, is **not** recommended by this reading and is out of this
  phase's scope in any case: it is a deliberate OOM defence
  (`33fl/group_vars/allsf.yml:17-45`) and 04a explicitly declined
  to touch it. Removing the reservation from `limit_memory_mb`
  while leaving the ratio alone would make the ledger exactly as
  permissive as the KSM ceiling the same ratio sets, which is a
  far smaller change than moving the ratio.

The hand-off `D1` and the phase's definition-of-done item 6 ask
for, stated once: **the lever is per-host
`node_ram_reservation_gb` in the 33fl inventory, the measured
figure it withholds is 46680 MB (45.6 GiB) of `limit_memory_mb`
across the six sfcbr nodes, and that accounts for 27.2% of
per-node memory refusals of a 16 GiB shape over
2026-11-04 to 2026-11-07.**

#### What this section deliberately does not say

It does not answer `Q1`. The verdict — whether the live-memory
question is sequenced ahead of `A`, and how the "live third" of the
baseline is now divided between margin and shortage — is step 0c's,
and needs these figures plus 04a's refusal-reason counts and `C`'s
shadow reading. What is recorded here is factual: the
double-subtraction `Q2` asks about is real and costs about 27% of
memory refusals, but it costs them in the committed ledger rather
than in the live check, and the live check — the one `Q1` is about
— was the sole reason a node was dropped in 1 node-sample in 4968.

### Decisions this phase produces: `Q3` to `Q8`

Researched against `develop` at `32de6cec7f` on 2026-11-07. Every
citation below was re-found in that tree rather than carried from
the master plan or the brief, because the line numbers in both had
drifted — `_has_sufficient_ram()` is now
`shakenfist/scheduler.py:753`, not `:520`, and
`_claim_capacity_guard()` is `shakenfist/mariadb.py:29927`, not
`:29563`. Four citations had drifted in substance as well as in
line number — two in the master plan's `Q7`, two in the brief — and
each is named where it bears.

`D7` to `D12` answer `Q3`, `Q4`, `Q5`, `Q7` and `Q8` and defer
`Q6`. Each states what it leaves to its implementing phase, so no
later phase finds the question re-opened.

#### `D7` (`Q3`): the hold reference and its lifetime

**Decision. A hold is a row with its own uuid4, and the reference
on `POST /instances` is one new optional body parameter carrying
that uuid and nothing else. A hold is created with its claim and
ends only when the claim is deleted, when the reconciler's expiry
sweep lapses it, or when an operator shrinks the claim below the
sum of its holds — not when its instance is deleted, not on a
preflight abort, and not on a refused create. A create larger than
every hold on every dimension is an unheld create. An explicit
reference that cannot be honoured is a `409` carrying a reason
token; a reference to another namespace's hold is a `404`.**

*The reference is a uuid, resolved namespace-scoped.* A claim
already has no name and is addressed by uuid alone, and
`arg_is_claim_ref()` (`shakenfist/external_api/auth.py:1504-1541`)
spells out why the namespace segment is checked against the
object's own: "a uuid on its own would let a caller address a claim
through any namespace's URL, which would make the namespace segment
decorative". A hold has the same property, so it takes the same
treatment — a uuid that is not a hold of the request's namespace
answers the same `404` a missing hold does, disclosing nothing
about what other namespaces hold. A shape expression
(`cpus=4,memory=8192`) is rejected as a reference because a shape
is not a key: two holds of one claim can share a shape, and `G3`'s
best fit exists precisely because a create and a hold need not be
the same size.

*The parameter is a body key with a handler guard, like every other
one.* `POST /instances` takes flat body parameters
(`shakenfist/external_api/instance.py:718-721`) and the handler is
"a chain of handler guards, one block per body parameter"
(`:745-757`), which is also where
`docs/developer_guide/writing_an_endpoint.md` says a guard for a
new body key belongs. The hold reference is one more block, and it
answers before `Instance.new()` so a refused reference creates
nothing.

*Lifetime: the claim's coverage, not the instance's.* A hold ends
when its claim is deleted (`NamespaceClaim.hard_delete()`,
`shakenfist/namespace_claim.py:387-397`, which "gives the capacity
back inside the same transaction that removes the row"), when the
reconciler's expiry sweep moves it to coverage `expired`
(`shakenfist/mariadb.py:27223-27228`), or under the shrink rule
below. It does **not** end when its instance is deleted: the
release path returns the hold to unconsumed, booked on the node the
instance was last on, which is the brief's `D3` and the reason
three `G2` callers can delete and recreate under one claim.

*An expired claim's holds are released, not displaced.*
Displacement (brief `D8`) keeps a hold inside its claim's aggregate
limits. An expired claim has no aggregate limits to sit inside —
the reconciler sums `claimed_*` from active claims only, which
`_direct_update_namespace_claim()` states in terms
(`shakenfist/mariadb.py:30384-30388`) — so there is nothing for a
displaced hold to be inside, and the honest outcome is release.

*A re-dated claim keeps its holds; a replaced one does not.*
`sf_claim`'s `state=present` path re-dates the existing claim
rather than replacing it
(`shakenfist/deploy/collection/plugins/modules/sf_claim.py:43-47`),
so the 30-day `static-runners` and `static-ci` claims keep their
bookings across every run of the play. Its *lapsed* path creates a
replacement and then deletes the lapsed rows (`:59-67`), and holds
belong to a claim uuid, so the replacement starts with none. The
holder re-books, exactly as it does for a displaced hold under the
brief's `D8`.

*A shrink below `used + held` releases holds rather than being
refused.* The shrink floor today is `used_*` and no further, tested
as a SQL guard so a concurrent create cannot slip under it
(`shakenfist/mariadb.py:30368-30374`, diagnosed by
`_blocked_by_shrink_floor()` at `:30534-30556`). That floor stays
where it is, and a shrink that leaves `sum(holds)` above the new
limit releases holds, most recently booked first, until the claim
fits — each release an event on the claim and named in the update's
reply. This is what the brief's `D3` means by a hold ending because
its claim was "shrunk below it".

*Consumption edges.*

- **A create larger than every hold is an unheld create in a
  claimed namespace.** It charges the claim row as today and is
  placed against node room net of every hold on the node, its own
  namespace's included (`G6`). No hold is split: labelling part of
  a node's room held when it is not, to serve a case whose remedy
  is a larger hold, is the brief's `D3` rejection and it stands.
- **Two creates cannot take one hold.** The hold is taken by a
  guarded `UPDATE` inside the admission transaction, which is the
  same mechanism every other counter there uses
  (`_direct_admit_instance_placement()`,
  `shakenfist/mariadb.py:28750-29038`); the loser of the race falls
  through to the next matching hold, or becomes an unheld create.
- **A displaced hold cannot be consumed.** It is booked on no node,
  so it is not a match for implicit consumption, and an explicit
  reference to one is refused with the `409` below.
- **A hold carries no affinity.** Hard affinity matches the tags of
  instances *already placed on a candidate hypervisor*, in the
  creator's own namespace (`shakenfist/scheduler.py:623-665`;
  `docs/user_guide/affinity.md:134-139`); it is re-evaluated on
  every schedule, restarts included (`affinity.md:148-161`); and
  the first member of a `require_with_tag` group cannot be created
  under the constraint that defines it (`affinity.md:194-208`). A
  booking made before any instance exists therefore has nothing to
  evaluate. Affinity is tested at consumption instead: a hold whose
  node does not satisfy the create's `require_*` constraints is not
  a match, which is the brief's `D3`, and the hold itself never
  carries one, which this decision adds.
- **An explicit reference that cannot be honoured is a `409` with a
  reason token** — consumed, expired, displaced, too small, or
  affinity. `409` is the status this API already gives when "a
  request conflicts with the durable state of a resource"
  (`shakenfist/external_api/auth.py:1378-1382`), and the refusal is
  not a `507` and carries no `Retry-After` because no wait makes
  that hold usable. The brief's `D3` asked for a non-capacity,
  non-transient error naming why; this names the status and says
  the reason is a token rather than prose.

*Rejected: release the hold when its instance is deleted.* It turns
every delete-and-recreate into a race the holder can lose, which is
the thing a hold exists to prevent, and three `G2` callers do
exactly that. The brief's `D3` rejected it and nothing in the code
has changed to reopen it.

*Rejected: raise the shrink floor to `used + held` and answer
`below_usage`.* It would make returning capacity harder than taking
it — an operator would have to find and release each hold before
the claim could shrink — and the API already treats a shrink as the
operator's explicit instruction. The claim-update docstring's "there
is no auto-grow: every change here is one an operator asked for"
(`shakenfist/mariadb.py:30355-30357`, where the `D15` it cites is
scheduler-reservations', not this plan's) is a statement about
growth, not about refusing to give room back.

*Rejected: a name or a shape expression as the reference.* A hold
has no name to give it, and a shape is not unique within a claim.

*Left to the implementing phases.* Phase 4 owns the best-fit
comparator's tie-breaks beyond memory, then vCPUs, then disk; the
wire names of the hold fields on the create's reply and on the
refusal; whether the reason token rides in its own key or in
`error`; and where a consumed hold's uuid is surfaced on the
instance. Phase 3 owns where the hold-taking statement sits in the
transaction's canonical table order. Phase 6 owns the client and
`sf_claim` spelling of the reference (`D11`).

#### `D8` (`Q4`): the per-node held column's shape

**Decision. Holds are stored as one row per hold in a new table —
uuid, claim uuid, namespace, the three dimensions, the booked node
(nullable, where `NULL` means displaced), and the consuming
instance uuid when it has one. That table is the ground truth. The
per-node held dimension is three columns on
`scheduler_node_capacity` — `held_cpus`, `held_memory_mb`,
`held_disk_gb`, `BIGINT NOT NULL DEFAULT 0`, beside `used_*` — and
nothing else: no held column on `namespace_claims` and none on
`cluster_capacity`. The reconciler recomputes the three counters
from the hold rows in the same pass and from the same read as
`used_*`. Claim migration carries no bookings.**

*Why rows, and why also columns.* The node guard is one `WHERE`
clause inside one `UPDATE` (`_claim_node()`,
`shakenfist/mariadb.py:29013-29037`), so `used + held + request <=
limit` needs `held` as a column on the row the guard already locks.
But a column cannot be recomputed from itself: the reconciler is
"the authority on every counter it writes, because it recomputes
each one from ground truth" rather than adjusting it incrementally
(`shakenfist/mariadb.py:27196-27205`), and for `used_*` that ground
truth is the `instance_location` reference rows that
`_RECONCILE_USAGE_SQL` (`:26872`) folds up. Holds need the same
split: rows to recompute from, counters to guard against. The
brief's `D3` asked for exactly this when it said the reconciler
"must recompute `held_*` from hold rows too, or a pass will
overwrite the bookings".

*The counters are also what the live checks narrow against.* The
brief's `D2` requires that every other create's live memory check
count a node's unconsumed holds as if resident.
`_has_sufficient_ram()` already reads the node's capacity row for
its third sub-check (`shakenfist/scheduler.py:808-826`), so a
`held_memory_mb` column on that row is readable from the live check
without a second query. Holds stored only as rows would make the
live check join on the hot path.

*What a consumed hold contributes.* A consumption moves the
create's request from `held_*` to `used_*` and leaves the hold row
at its full size, so the difference stays booked while the instance
runs (brief `D3`). `used + held` therefore never double-counts a
consumed hold, and the reconciler's recompute is `SUM` over the
node's hold rows of the hold's size less the consuming instance's
request, which is zero for an unconsumed hold. That is why the hold
row records the consuming instance and not merely a boolean: the
residue cannot be derived without it. The consumed hold's node
follows its instance on a move, so a later delete returns the hold
to the node the instance was actually on.

*No namespace-side and no cluster-side held counter.* A hold sits
inside its claim's `limit_*`, and a claim's limits are already
inside `cluster_capacity.claimed_*`, so a second held counter on
either row would be a ledger that agrees with the first only until
the next write — the reason `_summarize_resources()` gives for
publishing the singleton rather than recomputing it from the claims
(`shakenfist/scheduler.py:358-373`). The claim-side invariant
`sum(holds) <= limit_*` is tested as a SQL aggregate over the hold
rows inside the claim's own transaction. Claim create and update
are not a hot path, unlike instance admission, so an aggregate
there costs nothing the design has to recover.

*A correction to the brief.* The brief's `D3` says the claim's
limits are validated against the sum of its holds "through the same
aggregate guard (`_claim_capacity_guard()`)". That guard tests
whether the *cluster* can keep the claim — `claimed + requested +
GREATEST(0, unclaimed_used - migrated) <= total`
(`shakenfist/mariadb.py:29927-29972`) — and must not gain a hold
term; its docstring warns twice against adding to the migrated
term. The hold-sum invariant is a different test, in the same
transaction. The brief's intent (validated in SQL, inside the claim
transaction, at creation and update) is honoured; its naming of the
guard is not.

*Reconciler recomputation.* `held_*` for a node is the `SUM` above
over hold rows whose node is that node and whose claim is in
coverage `active`, computed in the pass that already recomputes
usage (`_reconcile_fetch_usage()`, `:27085-27108`, called at
`:27327`) and written with the node's limits. The pass's `removals`
list (`:27360-27368`) is the trigger for re-place-or-displace; see
`D9`.

*One trap worth naming here so phase 3 does not fall into it.*
`held_disk_gb` must not feed `_derive_disk_limit_gb()`
(`:26676-26712`). That limit is `used_disk_gb` plus the headroom
the filesystem reports, so held disk is deliberately outside it;
adding held to the `used` term would raise the limit by the holds
and the guard would then admit them a second time.

*Claim migration carries no bookings.* The migrated-drawdown term
moves a namespace's existing *instance* usage off
`unclaimed_used_*` and onto the claim at claim creation, and is
pinned at zero for a grow (`:29927-29957`). A namespace with no
claim can hold nothing, so there are no hold rows at the only
moment that term is non-zero. The other reading of "migration" — a
placement move — is answered above: a move updates the hold row's
node and touches no `held_*` arithmetic, because a consumed hold's
room is in `used_*`, which the existing floored decrement and
guarded increment already move (`_floored_node_decrement()`,
`:28284-28310`).

*Visibility (`G8`).* `_summarize_resources()` publishes `cpu_held`,
`memory_held_mb` and `disk_held_gb` per node beside the committed
figures it already publishes
(`shakenfist/scheduler.py:457-545`), and nets held room out of
`cpu_available`, `ram_available` and `disk_available`, because
those are the figures a client-side ledger model reads. The fields
go in that shared payload builder, not in the bound
`Scheduler.summarize_resources()` at `:1377`, whose own docstring
records it as retained for the test suite and called by no
production code.

*Rejected: holds as a JSON document on the claim.*
`namespace_claims` doubles as the claim's static-values table and
has no attributes table (`shakenfist/mariadb.py:3955-3983`; there
is no `namespace_claim_attributes` anywhere), the guard cannot sum
a JSON blob per node in SQL, and the reconciler could not recompute
`held_*` without parsing it in Python.

*Rejected: holds as `object_references` rows.* A reference row
carries `relationship_value` and nothing else
(`shakenfist/schema/object_reference.py:7-9,24-28`), so three
dimensions, a consumption state and a consuming instance would
have to be encoded into one string.

*Rejected: a per-namespace held column on the node row.* It would
let the guard answer "held by whom" in SQL, at the cost of a row
per (node, namespace) pair and a second recompute fold. The guard
never needs the holder: a hold binds every create including the
holder's own unheld ones (`G6`), and the consuming create excludes
its own hold by uuid, not by namespace.

*Left to phase 3.* The table's name and exact DDL, its index set,
its entry in `EXPECTED_SCHEMA_VERSIONS`
(`shakenfist/mariadb.py:575-622`) and its place in
`ensure_schema()`'s canonical list; the
`SCHEDULER_NODE_CAPACITY_VERSION` bump from 1 to 2
(`:546`) and the existence-guarded `ADD COLUMN` migration, for
which `_add_missing_namespace_claims_columns()` (`:4117-4144`) and
`_ensure_namespace_claims_schema()` (`:4146-4179`) are the
precedent rather than a specification; the proto fields; and
whether the consumption state is its own column or is read from the
nullable instance uuid alone. This decision settles the shape —
rows plus three node counters, nowhere else — and deliberately not
the migration.

#### `D9` (`Q5`): whether `degraded` nodes stop placement

**Decision. A `degraded` node keeps accepting placements and keeps
accepting holds. The nodes that stop accepting holds are the
complement of `NODE_ACTIVE_STATES` — `error`, `missing`,
`stopping`, `stopped` and `deleted` — plus a node that is not a
hypervisor and a node whose metrics have aged out. The operative
trigger is not a state list: it is the loss of the node's
`scheduler_node_capacity` row, which the reconciler already
computes from exactly those conditions. A node that loses its row
re-places or displaces its unconsumed holds in that pass.**

*`degraded` is active, and the code says so twice.*
`NODE_ACTIVE_STATES` is `{'initial', 'created', 'degraded'}`
(`shakenfist/constants.py:47`), under a comment that reads
"degraded is active: a node with one unhealthy store still runs
instances" (`:38-40`), and
`docs/developer_guide/state_machine.md:400-401` says "a degraded
node is still a scheduling candidate". The state means only that
some registered daemon reports stopping or stopped
(`Node.get_degraded_daemons()`, `shakenfist/node.py:705-715`), and
the registered set includes daemons an instance does not need —
`eventlog`, `checksums`, `cleaner`, `transfers`
(`shakenfist/node.py:246-249`).

*The state that means the hypervisor has actually failed is
`error`, and it is not active.* `Node.INACTIVE_STATES` is
`{deleted, error, missing}` (`shakenfist/node.py:242`), a
resource-health error clears only through `sf-ctl clear-node-error`
(`docs/developer_guide/state_machine.md:402-409`), and
`Node.set_daemon_state()` carries a `no_reconcile` list whose whole
purpose is to stop an errored node being downgraded to degraded —
"which is a schedulable `ACTIVE_STATE`" — the moment one of its
daemons reports stopped (`shakenfist/node.py:637-650`). That
comment names the sf-6 failure: without it the node "would flap
error<->degraded and keep receiving instances". Holds must follow
placement here, because a hold that refused a node placement
accepts would be booked nowhere while creates landed freely, which
inverts what a hold is for.

*The trigger is the capacity row, not a state test.* The
reconciler removes a node's capacity row when the node is not in
`NODE_ACTIVE_STATES`, is a known non-hypervisor, has no metrics row
fresher than `RECONCILE_METRICS_MAX_AGE_SECONDS`, or is not in the
`nodes` table at all (`shakenfist/mariadb.py:27295-27368`, with the
active-state query at `:27313-27321` and `removals` at
`:27360-27364`). That one list already covers every case the
brief's `D8` enumerated and two it did not, and it is recomputed on
the same pass that `D8` asks to recompute `held_*` on — so
re-place-or-displace needs no new condition of its own. The
freshness window is 900 seconds (`:716`, "fifteen minutes absorbs
any skew a working NTP setup produces"), so a momentary metrics gap
does not displace a holder's bookings.

*A correction to the brief.* The brief's `D8` lists the node states
as `missing`, `stopping`, `stopped` and `degraded`
(`shakenfist/node.py:233-236` — still those lines) and leaves
`degraded` to this plan. The list is incomplete in the direction
that matters: it omits `error`, which is the state a failed
hypervisor actually reaches, is the only one of the set that sticks
until an operator clears it, and is the one the sf-6 outage
produced. `D9` adds `error` and `deleted` and takes `degraded` out.
The brief asked the question rather than answering it, so nothing in
it needs to yield.

*Rejected: a new no-new-holds node state.* A lame-duck or
maintenance state is #1364's (`D10`), and minting one here would
ship a state with no operator verb to set it and no evacuate
operation to use it.

*Rejected: holds refuse `degraded`, placement does not.* Two
admission rules for one node, differing on a signal — a stopped
eventlog daemon — that has nothing to do with capacity.

*Rejected: treat the conductor's maintenance drain as a node state.*
It is not one: it stops the conductor creating namespaces, so it
books no holds, which is the brief's `D8` and unchanged by this.

*Left to phase 4.* The re-place-or-displace transaction itself, the
most-recently-booked-first ordering, the event names on the claim,
the metric, and the displaced-hold fields in the claim's view and
in `GET /admin/resources`. Phase 3 owes it only the `held_*`
recompute in the pass whose `removals` list it reuses.

#### `D10` (`Q6`): the admin-scoped hold for evacuate

**Decision. Deferred. #1364 carries no design to design against,
and there is no lame-duck state, no drain state and no evacuate
operation in the tree to attach an admin hold to. The owner is the
##1364 implementation, which the master plan already carries in
Future work rather than as a phase. Phase 3 owes it one thing: the
hold row's claim uuid must be nullable, so an admin hold can later
be booked against an aggregate that is not a namespace claim
without a migration.**

*What #1364 actually says.* Read with `gh issue view 1364` on
2026-11-07: node delete exists, and the residual is "no
lame-duck/maintenance/drain state and no evacuate operation",
rescoped in 2026-06, with one comment pointing at a partial fix in
two old pull requests. There is no proposed state, no proposed
operation and no destination-selection rule.

*And the tree agrees.* A search of `shakenfist/` for lame-duck,
evacuate and drain finds only the log and eventlog drainer threads;
the sole mention of evacuation in the documentation is
`docs/developer_guide/state_machine.md:394`, where a node is
"manually evacuated and removed" by an operator before being
deleted. `PLAN-scheduler-reservations.md:1544-1549` already reached
the same conclusion about the same question — the node-scoped case
that justifies this is an operator's, and "design it then, against
the claims as they stand after phase 5, not before".

*What would settle it.* #1364 shipping a node lifecycle state that
means "accept no new placements" and an evacuate operation that
names its destinations. Until both exist, an admin hold has no
caller and no aggregate limit to be drawn from: a claim is
per-namespace (`shakenfist/mariadb.py:3993-4024`), and an evacuate
books room for instances in namespaces it does not own.

*What this plan must not foreclose.* `held_*` is per node and
dimension-only (`D8`): it does not record who holds the room, so a
cluster-scoped holder needs no column there. The only thing that
ties a hold to a namespace is the hold row's claim uuid, so phase 3
must not make that column the table's primary key or its only
non-null parent. A nullable claim uuid leaves #1364 free to book
against the cluster singleton (`cluster_capacity`,
`shakenfist/mariadb.py:4056-4086`), which is where an admin's
aggregate would have to live. That is a constraint on phase 3's
DDL, not a design for the admin hold.

*Rejected: deciding it now anyway* — an `admin: true` flag on a
hold, or admin holds in the `system` namespace's claim. Both fix
where an evacuate's aggregate room comes from before anything can
say how an evacuate chooses destinations, and the master plan's
"designed with #1364 rather than guessed at here" exists to stop
precisely that.

#### `D11` (`Q7`): expressing shapes, and the stale claim verbs

**Decision. `sf-client` and `sf_claim` express a shape as a
comma-separated `key=value` list — `cpus`, `memory` in MB, `disk`
in GB, `count` — repeated once per shape, which is the idiom both
already use for a long-form networkspec, diskspec and videospec.
The hold reference is a single `--hold <uuid>` option on `instance
create` and a `hold:` key on `sf_instance`. The operator guide's
claim-verbs statement has already been corrected on `develop`; the
stale statements phase 6 must fix are in three other files.**

*The expression, in the repository's own idiom.* `sf-client`
already takes a structured multi-valued argument as a repeatable
option whose value is a comma-separated `key=value` list:
`--networkspec` and `--diskspec`
(`client-python/shakenfist_client/commandline/instance.py:358-364`,
parsed by `_parse_detailed_netspec()` at `:317-331`), documented
for the collection as `size=20,base=debian:11,bus=sata`
(`docs/user_guide/ansible.md:267`) and for video as
`model=qxl,memory=65536` (`:275`). So:

- `sf-client namespace claim create ... --shape
  'cpus=4,memory=8192,disk=200,count=3'`, repeatable, and the same
  option on `claim update`. The units are those the claim's own
  fields already use — `limit_memory_mb` and `limit_disk_gb`
  (`client-python/shakenfist_client/commandline/namespace.py:353-386`)
  — so a shape and a limit are denominated the same way in one
  command.
- `sf_claim` gains `shapes:`, a list of mappings with those four
  keys, each of which may also be written as the `key=value` string
  the collection already accepts elsewhere. Shapes join the set the
  module reconciles: a run whose shape multiset differs from the
  server's is a change, exactly as a differing limit is
  (`LIMIT_FIELDS`,
  `shakenfist/deploy/collection/plugins/modules/sf_claim.py:265`).
- `--hold <uuid>` on `sf-client instance create`, and `hold:` on
  `sf_instance`. Both are sent only against a server that
  advertises the capability, as `_require_claim_verbs()` already
  establishes the pattern for
  (`sf_claim.py:275-302`).

*The stale statement is not where the plan says it is.* The master
plan's `Q7` says the claim verbs are unreleased since client-python
`v0.8.3` and that `docs/operator_guide/scheduler.md` still says
`sf-client` has no claim verbs. Both halves have drifted:

- client-python `v0.8.4` was tagged on 2026-10-28 and published on
  2026-10-31, and it carries all five verbs
  (`client-python/shakenfist_client/apiclient.py:1645-1700`;
  `client-python/shakenfist_client/commandline/namespace.py:251-450`).
  The verbs are released.
- `docs/operator_guide/scheduler.md:445-449` has already been
  corrected: it says claims are administered with `sf-client
  namespace claim` and its `create`, `show`, `list`, `update` and
  `delete` verbs, "which need `shakenfist-client` 0.8.4 or later".

What is still stale, and is therefore what phase 6 fixes, is three
other places that still say no release carries the verbs and tell
the operator to install the client from git:

- `docs/user_guide/ansible.md:24-30`
- `shakenfist/deploy/collection/requirements.txt:16-20`
- `shakenfist/deploy/collection/plugins/modules/sf_claim.py:27-32`
  (the `requirements:` block) and `:275-302`
  (`_require_claim_verbs()`'s failure message)

`_require_claim_verbs()` itself stays. It is a feature probe
against the client that was actually built, and its docstring
already says it "starts passing on its own once a release does
carry the verbs"; only its prose changes, to name 0.8.4 rather than
a git URL.

*Rejected: a positional triple (`--shape 4:8192:200`).* Unreadable
at the point of use, and it cannot gain a fifth key without
breaking every caller. The `key=value` spec idiom already exists
here and already has a parser.

*Rejected: shapes as a JSON string argument.* `sf-client` takes
JSON only where the server's own value is JSON — affinity metadata,
`docs/user_guide/affinity.md:129`. Under `D8` the server stores
shapes as rows, not as a document, so a JSON argument would be a
shape of its own invention.

*Left to phase 6.* The option letters, where the shape parser lives
and whether it is shared with the diskspec parser, the version floor
the collection's `requirements.txt` asks for, and the client's own
`docs/namespace-claims.md` page that the operator guide links to.

#### `D12` (`Q8`): whether `C` is retired once `A` ships

**Decision. The placement dry run (`C`) is kept once `A` ships. It
is a general query, not scaffolding.**

*The caller table says so.* Of the ten callers in the master plan's
Callers (`G2`) table, eight get something from `C` and only the
conductor's runner path and the ci-images builder get little or
nothing. Three of those uses are ones `A` cannot serve at all: the
operator building a cloud by hand wants "would this fit?" before
ten creates, 33fl's `static-runners` wants it as a pre-flight task,
and lame-duck/evacuate wants "fit with node N excluded?" — a
question a hold cannot answer, because booking the room to find out
whether a drain is possible would take the room being asked about.
`A` answers "is the room mine"; `C` answers "is there room", and
the second question does not stop being asked once the first is
answerable.

*And it is the core's only read-only entry point.* `C` writes
nothing, so it cannot strand capacity, deadlock admission or
desynchronise the reconciler (master plan, phase 2), which is what
makes it safe for private-ci to score in shadow and safe for a
client-side ledger model to poll. The brief's `D1` puts `C` first
for the same reason.

*Rejected: retire `C` once `A` ships.* It would leave the eight
advisory callers nothing, remove the only way to ask the question
without taking the room, and leave the shared set-placement core
with a single consumer — which would make phase 1's extraction
pointless in retrospect.

*Left to the implementing phases.* Phase 2 owns `C`'s route,
request and reply shapes and its capability token; phase 6 owns its
client verb. Neither re-opens whether it exists.

#### Cross-check against the placeable-claims brief

Each decision above was read against the brief's `D2` (what a hold
promises), `D3` (consuming a hold) and `D8` (a node that leaves
service).

- **`D7` against brief `D3`.** Consistent, and narrower in four
  places the brief left open: the reference is a uuid resolved
  namespace-scoped, the unhonourable explicit reference is a `409`
  with a reason token, an expired claim's holds are released rather
  than displaced, and a shrink releases holds rather than being
  refused. The brief's rejections (exact matching, first fit,
  release on delete, a silently falling-back reference) are
  untouched.
- **`D8` against brief `D3`.** Consistent on substance, with one
  correction stated in the decision: the hold-sum invariant is a
  new SQL aggregate in the claim transaction, not a term in
  `_claim_capacity_guard()`, which tests cluster totals and whose
  docstring warns against adding to its migrated term. The brief's
  intent is honoured; its naming of the guard is not. `D8` also
  supplies the column the brief's `D2` narrowings need on the live
  check's own hot path.
- **`D9` against brief `D8`.** `D9` answers the question the brief
  explicitly delegated, and extends the brief's state list with
  `error` and `deleted` while taking `degraded` out. The brief's
  re-place-or-displace rule, its displacement semantics and its
  four rejections stand unchanged; `D9` only gives the rule a
  concrete trigger that the reconciler already computes.
- **`D10` against brief `D8`.** The brief listed a lame-duck node
  among the cases but marked it "not built"; the deferral agrees
  and names the one DDL constraint that keeps it cheap later.
- **`D11` and `D12`.** Neither touches the brief's `D2`, `D3` or
  `D8`. `D12` agrees with the brief's `D1`.

No decision here contradicts the brief. The two corrections — the
guard named in brief `D3`, and the state list in brief `D8` — are
both places where the brief's own text points at this plan for the
answer, so neither document needs to yield; both are recorded so
the brief's readers are not misled by them.

### The `Q1` and `Q2` verdict

Written 2026-11-07 against `develop` at `32de6cec7f`. The inputs are
the measurement subsection above, `D7`–`D12`, and shakenfist/private-ci
`docs/plans/PLAN-placeable-claims-phase-04a-regression.md` (`F8` and
`D5`), read at that repository's `origin/master`.

`D13` answers `Q1`, `D14` answers `Q2`, and `D15` records the
constraint placeable-claims phase 4a referred inward to this plan. The
measurement subsection deliberately stopped short of both verdicts
("What this section deliberately does not say"); this is where they are
made, so each of `Q1` and `Q2` is answered exactly once in this
document.

#### `D13` (`Q1`): `A` proceeds, live memory does not come first

**Decision. The master plan's order stands unamended: measure, then
`C`, then `A`. The live-memory question is not sequenced ahead of `A`,
because live memory is no longer refusing at anything like the share
`Q1`'s test was written against — it has fallen from a third to a few
percent, and in 0a's per-sample attribution the live check was the
sole reason a node was dropped once in 4968 node-samples. The question
is deferred rather than dismissed: phase 2's shadow counts, which `C`
was already going to produce, re-apply `Q1`'s own test before phase 3
is written, and `D14`'s retune is the one foreseeable event that could
reopen it.**

*`Q1`'s test, applied to the same quantity in three windows.* `Q1` asks
whether live-memory refusals run "at anything like the baseline's live
share", and names the baseline figures. Memory-stage node-drops by
reason:

| Family | Window | KSM | Ledger | Live | Live share |
|---|---|---|---|---|---|
| in-job | baseline, to 2026-10-17 | 32 | 12 | 24 | 35.3% |
| in-job | 2026-10-30 to 11-06 | 937 | 377 | 56 | 4.1% |
| in-job | 2026-11-01 to 11-06 | 538 | 281 | 11 | 1.3% |
| runner-create | baseline, to 2026-10-17 | 109 | 115 | 229 | 50.6% |
| runner-create | 2026-10-30 to 11-06 | 514 | 228 | 76 | 9.3% |
| runner-create | 2026-11-01 to 11-06 | 468 | 179 | 65 | 9.1% |

The baseline rows are the master plan's own (`Q1`: 24 of 68 in-job and
229 of 453 runner-create memory node-drops were live). The 7-day rows
are placeable-claims phase 4a's `F8`; the 5-day rows are its "Why each
node was dropped, latest segment" table. Disk-bandwidth drops are
excluded from every row, because `Q1`'s denominator is memory
node-drops. Both later windows are given because they are not the same
window and the share is worth reading at each: 4a's own narrative
figure — `insufficient memory` fell from 138 in the middle segment
to 11 in the latest — belongs to the 5-day rows.

A 35.3% live share becomes 4.1% and then 1.3%; a 50.6% share becomes
9.3% and then 9.1%. The absolute counts of the other two reasons rose
by an order of magnitude over the same period. That is `Q1`'s test
failing to trigger, on `Q1`'s own terms, on either of the later
windows.

*Why the baseline and the later windows disagree, which matters more
than the arithmetic.* They are not equally good reads of the same
thing. The baseline's
post-change window is **1.31 days**, and it ends on 2026-10-17 — the
day private-ci's phase 1 stopped emitting the `Error in main loop` line
that `tools/measure-placement-refusals.py` joined the runner-create
family on. Phase 4a's `F3` and `F5` establish that every reading taken
after that date read **zero** runner-create refusals where the truth
was first eight and later seventy a day, until 4a repaired the join.
So the baseline's runner-create row is the last one the old instrument
could produce, taken over a day and a third, and the later rows are
seven and five days taken with the repaired instrument and cross-checked
against `conductor_runner_create_refused_total` to within 2%. Where
they disagree, the later ones are the better measurement, and this
verdict rests on them.

*What 0a's measurement adds, and it is the independent half.* Phase
4a's counts are of refusals the scheduler actually emitted; 0a's are of
what each sub-check would have done to a 16384 MB shape at every
node-sample in 72 hours. The two are methodologically independent and
agree: the committed ledger refuses in 789 of the 790 node-samples
where anything refuses, and the live check is the sole refuser in 1 of
4968 (2 of 7911 at 60 s on the busiest day). The live check is the
loosest of the three sub-checks almost always.

*Which of those conclusions survive the `used_memory_mb` proxy, and
which do not.* 0a's ledger figures use `memory_total_instance_max` as a
proxy for `used_memory_mb`, and the proxy can only undercount, so proxy
ledger room is an **upper** bound on real ledger room. For this verdict
that bias runs the safe way on every load-bearing figure:

- **Robust.** The live check's own refusal count (65 of 4968) is
  computed from gauges alone and does not touch the proxy at all.
- **Robust.** "Live is the sole refuser in 1 of 4968" is an upper
  bound: correcting the proxy can only make the ledger refuse *more*
  often, which can only reduce the set of samples in which live refuses
  alone. The same holds for the ledger being the tightest of the three.
- **Not robust, and not relied on here.** `D14`'s 27.2% is an upper
  bound for the same reason (see `D14`), and 0a's "at no sample did
  every node refuse the shape" is explicitly not treated as evidence
  by 0a itself.

No conclusion in `D13` needs ledger room to be large, which is the
direction in which the proxy would mislead.

*What would falsify this.* Stated so phase 2 and phase 3 can check it
rather than inherit it:

- **Phase 2's shadow counts are the re-test.** If `C` answers "fits"
  and the create minutes later is refused on `insufficient memory` at
  anything like 24 in 68 or 229 in 453, `Q1` is reopened and the
  live-memory question is sequenced ahead of phase 3. That is the test
  `Q1` wrote and the master plan's phase 2 already commits to reading
  those counts before phase 3.
- **`D14`'s retune is the one foreseeable event that could flip this.**
  Lowering `node_ram_reservation_gb` raises `limit_memory_mb`
  one-for-one and relaxes the check that is actually binding. Relieve
  the binding check and whichever check is next becomes binding; on
  sf-4 and sf-6 the live check already refuses a 16 GiB shape on 2.7%
  and 1.2% of samples. So a reservation retune on sfcbr must be
  followed by a re-read of these shares before phase 3 is written, and
  the retune's own effect is the reason `D13` defers the question
  instead of closing it.
- **Moving `RAM_OVERCOMMIT_RATIO` off 1.0** invalidates the algebra
  `D14` rests on (the two reservation subtractions no longer cancel),
  so the whole `Q1`/`Q2` reading would need retaking. 33fl's reasoning
  against moving it stands and nothing here asks for it.
- **A different cluster or a different shape.** Everything above is
  sfcbr, at ratio 1.0, against a single 16 GiB shape, with
  `memory_total_instance_max - memory_total_instance_actual` measuring
  exactly zero at every percentile. A deployment where guests balloon,
  or where host non-guest use approaches the reservation, would read
  differently.

*Rejected: sequence the live-memory question ahead of `A`.* It would
buy a narrowing worth about one node-sample in five thousand, and it
would do so ahead of the mechanism that addresses the refusals actually
being counted — which are allocation-cap refusals and, in 374 of 461
in-job cases in phase 4a's latest segment, packing failures. Phase 4a's
`D5` is explicit that waiting does not defragment and that the only
mechanism in sight that can defend a large-shape slot is a hold. Making
the smallest of the three memory constraints the gate on the largest
would invert the priority the measurement establishes.

*Rejected: close `Q1` outright rather than deferring it to phase 2.*
One of the falsifiers above is something this plan's own hand-off can
cause — `D14`'s retune — and one risk is not in that list at all,
because 0a cannot see it: a transient inside the 300 s sampling
interval, which the gauges smooth over, is exactly what the live check
would catch and what a retrospective reading cannot count. `C`'s
shadow counts cost nothing extra to read, and they are the only
instrument that sees a live refusal at the moment a create meets
it.

*Left to the implementing phases.* Phase 2 owns what the shadow
comparison records and how a "fits, then refused live" event is
counted; it does not re-open whether `C` comes before `A`. Phase 3 owns
reading those counts before it is written, which the master plan
already requires.

#### `D14` (`Q2`): what the double-subtraction costs, and where

**Decision. The host's reservation is subtracted twice on sfcbr, as
`F1` said — but the two places are the committed ledger
(`limit_memory_mb`) and the KSM ceiling, not the ledger and the live
check. At `RAM_OVERCOMMIT_RATIO = 1.0` the live check's and the
ledger's reservation terms cancel algebraically, so the live/ledger
margin `Q2` asks about was never a function of the reservation at all.
The cost is about 27% of per-node memory refusals of a 16 GiB shape, an
upper bound, incurred through `limit_memory_mb`; the live-side cost is
worth one node-sample in 4968. The second subtraction is an extra
buffer of the reservation's size rather than a duplicate of a cost the
host is incurring, and whether it is the right buffer is 33fl's
judgement, not this plan's. The retune is handed to 33fl with one
lever, `node_ram_reservation_gb`, and it is not a gate on `A`.**

*Where the arithmetic actually lands.* 0a's identity is the whole
answer: at ratio 1.0, `ledger room - live room = host_use -
used_memory_mb`. The reservation appears on both sides and cancels.
What remains is the difference between what the host has faulted in and
what the ledger has committed, and on sfcbr the second term dominates,
because guests are charged at nominal allocation and
`memory_total_instance_max - memory_total_instance_actual` is exactly
zero at every percentile on every node. So live room sits
**persistently above** ledger room — median gap negative on all six
nodes by 1.7 to 6.7 GB — which is the opposite of the signature `D4`
predicted. Correcting the `used_memory_mb` proxy would push the gap
further negative, so the finding is not an artefact of the proxy.

With `actual == nominal`, proxy ledger room is exactly the KSM headroom
less the reservation on every node. That is the second subtraction,
and it is between the ledger and the KSM ceiling.

*What it costs.* 215 of 790 refusing node-samples (27.2%) are ones the
ledger refuses while both the KSM ceiling and the live check admit,
which is to say refusals that exist only because of the reservation
term in `limit_memory_mb`. A second 72-hour window gives 27.1% and a
60 s re-run of the busiest day 22.5%. **This figure is an upper
bound**, for the same reason `D13`'s sole-refuser count is one: a truer
`used_memory_mb` tightens both the ledger and the reservation-free
ledger it is compared against, so some of those 215 would be refused
either way. The one figure the proxy does not touch is the capacity
withheld — 46680 MB (45.6 GiB) of `limit_memory_mb` across the six
nodes — because that is the sum of the reservations themselves.

*And what it does not cost.* The live-side half of the double
subtraction is worth 1 node-sample in 4968: removing it takes
live-check refusals of a 16 GiB shape from 0.0-2.7% of samples to
0.0-0.8%. So `Q2`'s question "how much of the live third is margin and
how much is shortage" has a different answer than it anticipated: by
the time it is asked there is almost no live third left (`D13`), and
the margin the double subtraction creates sits almost entirely in a
check that is not the live one.

*It is not a duplicate of a cost actually incurred.* Separating guest
RSS out of `memory_max - memory_available` with `process-exporter`, the
host's own non-guest footprint is 0.7 to 3.3 GiB at the median against
a 6.2 to 10.3 GiB reservation, and exceeds the reservation on under
1.5% of samples on every node. So the ledger's subtraction is not
re-charging memory the live check has already removed; it is adding a
second buffer the size of the whole reservation on top of a genuinely
measured host use a third to a quarter of it. That buffer has a
rationale — both `33fl/group_vars/allsf.yml:22-25` and
`shakenfist/scheduler.py:796-808` note that the measurements lag
placement — so "duplicate" is the wrong word for it and "is it the
right buffer?" is a tuning question rather than a correctness one.

*The hand-off, and why it is not a gate.* The lever is the per-host
`node_ram_reservation_gb` override in the 33fl inventory
(`NODE_RAM_RESERVATION_GB`, `shakenfist/config.py:719-727`, templated
at `shakenfist/deploy/collection/roles/node/templates/config:29`), and
the figure it withholds is the 46680 MB above. Raising
`RAM_OVERCOMMIT_RATIO` is **not** the recommended lever: it is a
deliberate OOM defence, placeable-claims phase 4a declined to touch it,
and dropping the reservation from `limit_memory_mb` would make the
ledger exactly as permissive as the KSM ceiling the same ratio already
sets, which is a far smaller change. The retune is 33fl's (`D1`, out of
scope) and it does **not** gate `A`: it relaxes the binding check,
which makes placement easier, and nothing in `A`'s design depends on
its value. `D13`'s third falsifier is the one coupling in the other
direction — if the retune lands, the live shares must be re-read before
phase 3.

*Rejected: treating the 27% as a reason to defer `A` until the retune
lands.* The retune relieves saturation of the allocation ledger; it
does not defragment, and `D15` is why. A node whose `limit_memory_mb`
rose by 6 GB is still a node the ranker will spread a 4 GB guest onto
before a 16 GB one arrives.

*Rejected: recording `Q2` as "not established", which is where the
master plan left it.* It is established in both directions now: the
double subtraction exists and is quantified, and the place `Q2`
expected to find it is not where it is.

*Left to 33fl and the implementing phases.* 33fl owns whether and by
how much to retune, and owns re-reading `D13`'s shares afterwards. One
optional ask remains open from 0a: a single `SELECT node_uuid,
limit_memory_mb, used_memory_mb FROM scheduler_node_capacity` would
replace the proxy with the ledger's own figure and turn both of the
bounds above into exact figures. No conclusion in `D13` or `D14` waits
on it.

#### `D15`: a hold defends a slot for a shape, not an aggregate

**Decision. This plan carries the constraint placeable-claims phase 4a
`D5` referred inward: a hold must defend a slot for a *shape* on a
*node*, not a quantity in an aggregate, because the scheduler's node
ranking is deliberately spreading allocation away from the nodes a
large shape needs. Phase 3 owns it — it is a constraint on the node
guard and on what a hold row is — and phase 4 inherits the half about
keeping a booking across re-placement. Nothing here asks for a change
to ranking, and nothing here is in tension with shakenfist#3937.**

*What the ranker does today, re-read at `32de6cec7f`.* `find_candidates()`
buckets each surviving candidate by the worse of two pressures: its
CPU load normalised by schedulable threads, and its committed-memory
fraction, in coarse 0.25-wide bands —

```
bucket = max(
    math.floor(normalised / 0.25),
    math.floor(ram_fraction / 0.25))
```

at `shakenfist/scheduler.py:1310-1312`, with the memory fraction read
from the `scheduler_node_capacity` row the capacity guard itself draws
down (`:1301-1307`) rather than from the lagging metrics, and the
reasoning in the comment at `:1290-1300`. That is shakenfist#3937
(merged 2026-08-28), which closed shakenfist#3636 — "scheduler RAM
gates race placement and ranking ignores RAM, funnelling large VMs onto
the most-committed node". A node near its allocation cap now ranks
worst.

*Both behaviours are correct, and they pull apart.* Ranking by the
worse of two pressures spreads allocation across the fleet, which is
exactly right for the OOM kills #3636 was filed about: it is what stops
a burst funnelling onto the node that already carries the RAM-heavy,
CPU-idle guests. It is also what converts aggregate headroom into
per-node fragments, and a 16 GiB guest needs its 16 GiB on one node.
Six nodes each 80% allocated at `RAM_OVERCOMMIT_RATIO = 1.0` have tens
of gigabytes free between them and no room for a single
`12/16384/160`. Phase 4a measured that this is what is happening: 374
of 461 in-job and 330 of 347 runner-create refusals in its latest
segment are fragmentation, both surviving its pessimistic reading. So
OOM safety and large-guest admission are both served correctly and in
opposite directions, and nothing in the scheduler reconciles them.
**This is not a defect in #3937 and not an argument for changing
ranking**; it is the reason a reconciling mechanism has to exist
somewhere else, and `A` is that mechanism.

*The consequence for this plan, stated as a design constraint.* A hold
that reserved only ledger headroom in aggregate would not keep a node
able to accept the shape the hold was taken for: the ranker would
refill any gap that opened with whatever create asked next, and the
holder would arrive to find its room spread into fragments. So:

- A hold is booked **onto a node, for one shape**. `D8` already stores
  it that way — one row per hold carrying the three dimensions and the
  booked node — and `D15` is why that storage shape is load-bearing
  rather than merely convenient. A per-claim aggregate held counter
  would not satisfy this constraint, which is a second, independent
  reason `D8` rejects a namespace-side and a cluster-side held column.
- The node guard term is what defends the slot against the ranker.
  `G6`'s `used + held + request <= limit` for **every** create,
  including the holder's own unheld ones, is exactly the mechanism:
  the ranker may still prefer the held node, but the guard refuses the
  create that would eat the booking. The ranker is left alone and the
  guard does the reconciling.
- Displacement must not quietly become an aggregate. When a node leaves
  service, a displaced hold is booked on no node (`D9`, phase 4), and
  while displaced it defends nothing — which is why phase 4's rule is
  re-place first and report the displacement, rather than falling back
  to an aggregate reservation that would look like a hold and not
  behave like one.

*What this does not claim.* It does not claim ranking is wrong, that
#3937 should be revisited, or that a comment belongs on #3636 — phase
4a deliberately left that issue alone, and so does this. It also does
not claim `C` can satisfy the constraint: a dry run defends nothing,
by construction, which is `D12`'s point about it writing nothing.

*Left to the implementing phases.* Phase 3 owns the node guard term and
the hold row's DDL, and must not weaken either into an aggregate.
Phase 4 owns re-placement and displacement, and owes this decision only
that a displaced hold is reported rather than silently kept as an
aggregate. Phase 2 owes it nothing.

#### Consistency pass

*Each of `Q1`–`Q8` is answered or deferred exactly once in this
document.*

| Question | Where | Verdict |
|---|---|---|
| `Q1` | `D13` | Answered: `A` proceeds; re-tested by phase 2's shadow counts |
| `Q2` | `D14` | Answered: real, ~27%, in the ledger and the KSM ceiling |
| `Q3` | `D7` | Answered |
| `Q4` | `D8` | Answered |
| `Q5` | `D9` | Answered |
| `Q6` | `D10` | Deferred to #1364, with one DDL constraint on phase 3 |
| `Q7` | `D11` | Answered |
| `Q8` | `D12` | Answered |

The measurement subsection records `Q2`'s figures and says in terms
that it does not answer `Q1`; `D14` is where `Q2`'s verdict is drawn,
so neither question has two answers.

*Every `D` against every other.* `D1`–`D6` govern how the phase runs
and `D7`–`D15` are what it produced.

- `D1` (no code) holds: nothing in `D13`–`D15` changes the tree
  outside `docs/`.
- `D2`/`D3`/`D4` were written for a prospective scrape. 0a found the
  gauges already in maui's Prometheus and took the measurement
  retrospectively, withdrawing `D3`'s ask of 33fl. That is a recorded
  amendment in 0a's subsection, not a contradiction: `D2`'s working-day
  and CI-busy requirement became a window-selection criterion and was
  met (the three highest runner-create refusal days in 93), and `D4`'s
  two comparisons were taken as specified. `D4`'s expectation about
  what they would show was wrong, which is a result rather than a
  conflict.
- `D5` is satisfied exactly as written: `Q1` is answered from `D4` plus
  the baseline's live share, not in the abstract. `D5`'s first branch
  ("the fix is 33fl's retune and `A` proceeds") is the one taken, with
  one correction to its premise — the double-subtraction does not
  explain the live-memory refusal third, because that third has
  collapsed for reasons unrelated to the reservation. `D5`'s second
  branch would have amended the master plan's order; it is not taken,
  so the order is unamended.
- `D6` is satisfied by `D7`–`D12`, each with its reasoning and
  rejected alternative, and each naming what it leaves to its
  implementing phase. `D13`–`D15` follow the same form.
- `D7` and `D15` agree: `D7`'s "a hold carries no affinity, and
  affinity is tested at consumption" is compatible with defending a
  slot for a shape, because the slot is a node plus three dimensions
  and affinity is a property of the create, not of the booking.
- `D8` and `D15` agree and reinforce: `D15` is a second, independent
  reason for `D8`'s rejection of a namespace-side or cluster-side held
  counter.
- `D9` and `D15` agree: a displaced hold defends nothing, which `D9`
  already treats as a reportable outcome rather than a silent one.
- `D13`/`D14` against `D10`, `D11`, `D12`: no interaction. `D12`'s
  reason for keeping `C` is strengthened by `D13`, which gives `C` a
  second job — carrying `Q1`'s re-test — but `D12` does not depend on
  it.
- `D14` against `D8`'s warning that `held_disk_gb` must not feed
  `_derive_disk_limit_gb()`: both are about not letting a reservation
  or a hold be counted twice in a derived limit. No conflict; the
  memory and disk limits are derived by different functions.

*One ambiguity resolved.* `D7` quoted an unqualified `D15` from
`shakenfist/mariadb.py:30355-30357` ("there is no auto-grow"), which is
another plan's decision number carried in a code comment. With this
plan now having its own `D15`, that citation has been reworded to name
its source rather than read as a cross-reference inside this document.

*No contradiction was left unresolved.*

#### Definition-of-done item 3: the master plan's ordering

Read the master plan's Execution ordering prose against `D13`: it
needs **no amendment**. Recording that explicitly, because it is a
result and not a non-answer.

- The phase table's order (0, 1, 2, 3, 4, 5, 6, 7) stands.
- "The order follows placeable-claims phase 0's `D1` and `D9`: `C`
  first, `A` second, on one core, and the live-memory measurement
  before either admission change" stands, and is now a statement about
  work that has been done rather than work planned.
- The phase 0 bullet's conditional — "its answer to `Q1`/`Q2` decides
  whether the live-memory question is sequenced ahead of `A`" — has
  been rewritten in the master plan to record the answer, which is the
  only ordering prose this verdict moves. It moves it by resolving a
  condition, not by changing a sequence.
- The phase 2 bullet already requires private-ci's shadow counts to be
  read before phase 3. `D13` makes that requirement load-bearing
  rather than merely useful, and the master plan now says so.
