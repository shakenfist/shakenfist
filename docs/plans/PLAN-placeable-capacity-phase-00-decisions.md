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
