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
numbers (from `ea39dff7c9`) had drifted; these are the current ones.

**F1. The double-subtraction hypothesis is real, and the code
documents it.** The scheduler's three memory sub-checks, in order
(`shakenfist/scheduler.py`, `_has_sufficient_ram`):

- Live free memory (`:520-523`): `available =
  metrics[node]['memory_available'] - reserved`, refuse if
  `available - memory < 0`. `memory_available` is
  `psutil.virtual_memory().available`
  (`shakenfist/daemons/resources/main.py:407-410`); `reserved` is
  the node's `memory_reserved_mb` (`scheduler.py:328-334`).
- KSM ceiling (`:536-546`): `(memory_total_instance_actual +
  memory) / memory_max > RAM_OVERCOMMIT_RATIO`. **No reservation
  term.** `memory_max` is `psutil.virtual_memory().total`.
- Committed ledger (`:569-586`): `used_memory_mb (+ memory, less the
  create's own prior placement) > limit_memory_mb`.

The ledger limit is derived in `_derive_cpu_memory_limits`
(`shakenfist/mariadb.py:26302-26307`): `limit_memory_mb = max(0,
floor(memory_max * ram_overcommit_ratio) - memory_reserved_mb)`.
Here `memory_reserved_mb` is subtracted from **physical**
`memory_max`. The live check subtracts the **same** reservation
from `psutil.available`, which already excludes memory the host is
using — so host/reserved memory is charged twice. The docstring at
`mariadb.py:26264-26293` spells the distinction out. At a ratio of
1.0 the ledger limit is `memory_max - reserved`, the KSM ceiling is
`memory_max` (no reservation), and the live check independently
subtracts the reservation from an already-host-adjusted figure.
This is `Q2`, and it is confirmed in the code; the measurement's job
is to quantify it, not to discover it.

**F2. The measurement needs no new code.** The resources daemon
exports every raw input as a per-node Prometheus gauge: it calls
`start_http_server(config.RESOURCES_METRICS_PORT)`
(`main.py:324`, default port 13001, `config.py:537-538`) and sets a
`Gauge` per published metric (`main.py:782-785`). So
`memory_available`, `memory_max`, `memory_reserved_mb`,
`memory_total_instance_actual` (summed guest resident memory,
`main.py:537,593`) and `memory_total_instance_max` (summed guest
max, `main.py:536,592`) are all scrapeable on each hypervisor at
`:13001` over a working day with no probe to write. The same dict
is persisted to the `node_metrics` table (`main.py:787`) and the
derived ledger (`limit_memory_mb`, `used_memory_mb`) lives in
`scheduler_node_capacity` (`mariadb.py:3822-3837`,
`_derive_cpu_memory_limits` `:27016-27074`). `GET /admin/resources`
→ `summarize_resources()` (`scheduler.py:1138`, per-node payload
`:1300-1321`) gives the derived view but not a bare
`memory_available` or raw `limit_memory_mb` per node — those come
from the gauges or the DB. **Decision follows (`D3`): the
measurement is a scrape-and-analyse task against existing gauges,
not a code change.**

**F3. The capability mechanism is where the master plan said.**
`API_CAPABILITIES` (`shakenfist/external_api/app.py:390-478`) is a
family→tokens dict; `advertised_capabilities()` (`:481-494`)
flattens it and drops `ConditionalCapability` tokens whose predicate
is false. A static token is a string appended to a family list
(example today: `'cluster-capacity'` under `'admin'`, `:398`); a
runtime-gated one is `ConditionalCapability('token', lambda: ...)`
(`:452-453`). Phases 2 and 3 use this for `C`'s and `A`'s capability
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
