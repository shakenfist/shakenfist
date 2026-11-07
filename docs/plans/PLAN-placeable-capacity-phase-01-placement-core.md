# Placeable capacity phase 1: extract the shared set-placement core

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly and ground
your answers in what the code actually does today. Do not
speculate when you could read instead. Flag uncertainty
explicitly.

This is phase 1 of `PLAN-placeable-capacity.md`. Read that master
plan's "Phase 1: extract the shared set-placement core" section
first, and phase 0's `D13` and `D15`
(`PLAN-placeable-capacity-phase-00-decisions.md`), which constrain
what this phase may assume. This phase changes production code on
the instance create path and the start-redirect path, and promises
no behaviour change on either.

Citations below are into `develop` at `90d164035d` unless stated
otherwise.

## Planning effort

High, as the master plan specifies: this is a behaviour-preserving
refactor of a correctness-critical path, and the generalisation it
performs is the thing both later primitives are built on, so a
wrong abstraction here is paid for twice. The review effort is
high for the same reason.

## Scope

In:

- A shape value type, so placement can be evaluated for a
  requested shape rather than only for an `Instance` row.
- Converting the scheduler's filter, affinity and ranking stages
  to read that shape.
- One all-or-nothing multiset entry point that walks several
  shapes through those stages, each shape's tentative charge
  visible to the next.
- Routing both existing production callers through it at one
  shape, with no behaviour change: same candidate ordering
  distribution, same exceptions, same audit events.
- Unit tests for the multiset path, and mutation testing to prove
  they would catch its removal.

Out:

- Any API, capability string, client method or schema change. The
  dry run (`C`) is phase 2; holds (`A`) are phases 3 and 4.
- Any production caller at more than one shape. There is none
  until phase 2.
- Gang create (`B`). Out of scope for the whole plan.
- Changing what any filter decides, what any stage is called, or
  what any event contains. This phase moves where the arithmetic
  lives, not what it concludes.
- The claim guard. `find_candidates()` is claim-blind today — the
  claim is enforced in `admit_instance_placement()` — and stays
  claim-blind here.

## What the survey found

The master plan's phase 1 section was written on 2026-11-04. Its
central factual claim has since been falsified by another plan,
and the correction materially changes what this phase is.

**`F1`. The duplicated placement walk is already gone.** The
master plan says `POST /instances` and preflight "run two
placement walks that *must change together*". They did when it
was written. They do not now: scheduler-reservations phase 5
extracted the walk as `Instance.place_on_candidates()`
(`shakenfist/instance.py:1549`) in commit `036f255758` on
2026-11-05 — one day after this master plan was written — and
routed both callers through it (`shakenfist/external_api/
instance.py:1235`, `shakenfist/operations/
node_inst_netdesc_op.py:270`). The comment the master plan quotes
no longer exists in the tree; `grep -rn 'must change together'
shakenfist/` returns nothing. The sharing is documented, too:
`docs/operator_guide/scheduler.md:363-366` already states that
"there is a single shared placement walk
(`Instance.place_on_candidates()`) that both the create path and
the start-redirect operation use". That commit deliberately left
each caller its own candidate construction and its own
after-handling (an HTTP response here, an operation redirect
there), which is the right split and is not changed here.

This phase is therefore **not** the de-duplication the master plan
describes. What is left is the part that was always the point: the
placement pipeline evaluates one `Instance`, and both `C` and `A`
need it to evaluate a multiset of requested shapes. The master
plan's phase 1 section has been corrected at source as part of this
phase's planning commit, so a later reader does not plan against
the stale sentence.

**`F2`. The pipeline is `Instance`-bound end to end.**
`Scheduler.find_candidates(inst, candidates=None)`
(`shakenfist/scheduler.py:914`) reads exactly six things from the
instance: `inst.cpus`, `inst.memory`, `inst.disk_spec`,
`inst.affinity`, `inst.namespace` (inside
`_affinity_neighbour_skip()`, `:193`) and `inst.uuid` (the same
function's self-skip, and every audit event's subject). It also
wraps the whole decision in `inst.attribute_memo()` (`:930`) so
the nine attribute reads cost one row fetch, and reaches
`instance.placement_filter(node, inst)` in the CPU and RAM
filters (`:729`, `:814`) to avoid charging a reschedule twice.
Nothing else about the instance is used. That is a short enough
list to replace with a value type.

**`F2a`, found while executing step 1a.** `F2` enumerated what
`find_candidates()` reads from the instance but not who else calls
the predicates it reads them in. Five helpers in
`shakenfist/tests/test_scheduler.py` call them directly with a real
`Instance`: `HasSufficientCPUPredicateTestCase._call` (`:2446`),
`CpuCeilingAgreementTestCase._admits` (`:2607`),
`HasSufficientRAMPredicateTestCase._call` (`:2699`),
`HasSufficientDiskPredicateTestCase._call` (`:2834`) and
`HasIdleDiskBandwidthPredicateTestCase._call` (`:2881`). Twelve tests
across three of them fail on the converted signature; the other two
pass by accident rather than by design — `_admits` because its
fixture sets `used_cpus=0`, which short-circuits the `placed_on`
comparison, and the bandwidth helper because that predicate never
reads its shape argument at all. `D8` has been amended to say what
this means.

**`F3`. The filters that would need to see a tentative charge
read five different figures across two ledgers.** For a multiset
this matters more than it looks: charge only the committed ledger
and ten shapes all pass the *measured* checks against one
snapshot, which is issue 3636's failure mode one level up. The
five, with the filter that reads each:

| Figure | Source | Read by |
|--------|--------|---------|
| `used_cpus` | capacity row | `_has_sufficient_cpu` (`:684`) |
| `cpu_total_instance_vcpus` | metrics | same filter, `max()` of the two |
| `memory_available` | metrics | `_has_sufficient_ram` live check (`:753`) |
| `memory_total_instance_actual` | metrics | same filter, KSM ceiling |
| `used_memory_mb` | capacity row | same filter, committed ledger |
| `disk_free_instances` | metrics, **in bytes** | `_has_sufficient_disk` (`:829`) |

`_has_reasonable_queue_state()` (`:602`) and
`_has_idle_disk_bandwidth()` (`:857`) are load shedding rather
than capacity — they say a node is momentarily busy, not that it
cannot host the shape — so there is nothing for a tentative charge
to debit in either. The capacity rows arrive as a dict of row
dicts from `_capacity_by_node()` (`:288`), read fresh on every
decision, with a `degraded` flag that must keep its present
meaning: an absent row means an unguarded node that must not be
refused on a ledger which does not exist (`P7`).

**`F4`. There is no shape abstraction to reuse, and the house
idiom for one is clear.** `grep` for a placement shape type finds
nothing. Value types in this codebase are `NamedTuple`
(`shakenfist/mariadb.py` carries seven, including
`SchedulerNodeCapacityRead` at `:27859`) or
`@dataclass(frozen=True)` (`shakenfist/resource_health.py:45`).
The import direction is also settled: `scheduler.py` imports
`instance` (`:13`) and `instance.py` imports no scheduler, so a
shape type in `scheduler.py` creates no cycle.

**`F5`. The candidate ordering ends in a weighted random
shuffle,** so "no behaviour change" cannot mean reproducible
output. `find_candidates()` returns every surviving candidate
ordered best bucket first, and within a bucket shuffled by
Efraimidis–Spirakis A-Res weighted sampling
(`scheduler.py:1362-1368`). Two calls with identical inputs
legitimately differ. Behaviour preservation is therefore a claim
about the candidate *set*, the bucket tiers, the exceptions and
the events — not about list order.

**`F6`. The event stream is a documented diagnostic interface.**
`find_candidates()` emits ten `add_event_multi()` calls directly
plus one or two per stage from `_log_and_raise_on_error()`
(`:876`, called at nine stages), and
`docs/operator_guide/scheduler.md:1086-1092` tells an operator to
pick the create-path pass by looking for the `schedule inputs`
event whose `forced_candidates` is false, then read that pass's
`schedule have highest affinity`. A refactor that reshapes or
re-subjects those events breaks a written procedure.

**`F7`. Preflight already uses the pipeline as a fit test and
throws the answer away.** `node_inst_netdesc_op.py:193` calls
`s.find_candidates(inst, candidates=[config.NODE_UUID])` purely
for its exception, returning `None` on success. That is a dry run
against one node by another name, and it is the one existing
caller whose shape the new core most directly serves.

**`F8`. An in-flight sibling phase edits the same create-path
hunks.** scheduler-reservations phase 5a (`In progress`, planned
in the `shakenfist-wt-sched-p05a` worktree, not yet implemented)
will edit `shakenfist/external_api/instance.py:1261-1277`, the
branch where the walk has exhausted its candidates, to publish 403
for an all-claim refusal. That is within a few lines of the create
path this phase touches. Neither change needs the other; they
conflict textually if both are open at once.

Nothing else in the master plan's phase 1 section was wrong. The
section's "all or nothing", "each shape's tentative charge visible
to the next" and "existing single-instance callers routed through
it" all describe work that still needs doing, and `GET
/admin/resources`, `API_CAPABILITIES` and the client surface are
correctly phase 2's.

## Decisions

Numbered `D1` onward; these are this phase's own numbers. Master
plan and phase 0 decisions are cited as such.

**`D1`. The core is denominated in a shape, not in an in-memory
`Instance`.** Add a frozen value type — `PlacementShape`, a
`NamedTuple` in `shakenfist/scheduler.py` per `F4` — carrying
`cpus`, `memory_mb`, `disk_gb`, `affinity`, `namespace`,
`instance_uuid` (optional, for the affinity self-skip) and
`placed_on` (optional, for the reschedule charge). One factory,
`from_instance()`, is the only place that reads an `Instance`.

The rejected alternative is tempting and cheap: `baseobject.py`
supports `in_memory_only=True` objects, so each shape could be a
throwaway `Instance` and `find_candidates()` would not change at
all. It is rejected on three grounds. `add_event_multi()`
suppresses in-memory-only objects, so a dry run built this way
would silently emit no events while the create path emits
twenty — two different code paths for the same decision, with
`F6`'s documented procedure true of only one. Minting an instance
uuid per speculative evaluation is the shape of the leak the
`baseobject.py:195-201` comment records as issue 3532. And a hold
in phase 3 is a row, not an instance, so the fake would have to be
built out of a shape anyway; the shape is the real abstraction and
the `Instance` is the special case.

**`D2`. Every figure a filter reads is charged tentatively, both
ledgers and the measurements.** All six rows of `F3`'s table, in
their own units — `disk_free_instances` is bytes, so its debit is
`disk_gb * GiB`. The overlay is a copy taken for the duration of
one multiset decision and discarded with it; neither the metrics
snapshot nor the capacity rows are mutated in place, because the
`Scheduler` instance outlives the call and other decisions read
the same dicts. A node with no capacity row stays uncharged and
unguarded on the ledger terms, exactly as `P7` requires, and is
still charged on the measured terms.

**`D3`. The core ranks with the existing ranker, unchanged, and
does not pack.** Each shape is placed on the head of its own
freshly ranked candidate list, with the charges from earlier
shapes applied. The ranker deliberately spreads allocation away
from the most committed nodes (`scheduler.py:1310`, shakenfist
#3937), which phase 0's `D15` records as correct and as the reason
aggregate headroom fragments. A dry run that packed instead would
answer "fits" for an assignment the real scheduler would never
choose, which is the one thing a dry run must not do.

**`D4`. Shapes are walked largest-first, and a negative answer is
a lower bound rather than a proof.** Order by memory, then
vCPUs, then disk, descending — first-fit-decreasing. Greedy
assignment can fail where some other assignment would have
succeeded; this phase states that rather than implying
optimality. It costs nothing for `C`, which the master plan
already makes advisory by construction, and phase 3 inherits the
statement to reason about for `A`, where a false refusal refuses a
claim the cluster could have met. Exact search is rejected: it is
NP-hard in the general case, and more to the point the real
scheduler is greedy too, so an exact answer would describe a
placement that would never happen.

This is the decision most likely to be argued with, and the
argument against it is real: for `A` a wrong "no" is a refused
claim, and "advisory by construction" is phase 2's licence, not
phase 3's. The answer is that a greedy core which matches the
scheduler is honest about the cluster, where an exact core would
be accurate about a cluster we do not run; and that the remedy if
phase 2's shadow counts show greedy refusing real-world sets is to
reorder shapes, not to search, which this decision leaves open.

**`D5`. Events are addressed by an explicit target list, and a
core given no targets emits none.** The instance path passes
exactly today's `related_objects` — the instance plus each forced
candidate node — so every event in `F6` keeps its subject, name,
order and payload. Phase 2's dry run will pass no targets and
write nothing, which is also what keeps a speculative query out of
the event log and off the database-load budget that
`PLAN-database-load-reduction` spent eight phases recovering.

**`D6`. Affinity does not see tentative set-mates in this
phase.** Capacity is charged across the set; affinity is not,
because the hard filter (`:623`) and the scorer both read placed
`Instance` objects through `_placed_instances()` (`:666`) and a
shape has no row to be a neighbour of. The core's docstring says
so. Synthesising neighbour records now is rejected: it would fork
`_affinity_neighbour_skip()`'s predicate, which
`docs/developer_guide/coding_rules.md:7` ("Never restate a
visibility predicate") exists to prevent, and no caller is known
to need it yet. Phase 2's shadow scoring is where we find out
whether one does — the eight-instance `kerbside-patches` topology
is the candidate — and this is recorded in the master plan's
Future work rather than left in a plan nobody re-reads.

**`D7`. `find_candidates(inst, candidates=None)` keeps its name
and signature.** The multiset entry point is added beside it, and
`find_candidates()` becomes the one-shape wrapper: it builds the
shape, enters `inst.attribute_memo()` (so the database-load win at
`:930` is not lost — the memo belongs to the instance, not to the
core), and calls the core with today's event targets. Both
production callers are unchanged at their call sites. The fake at
`shakenfist/tests/test_external_api.py:35` also keeps working.

**`D8`. Behaviour preservation is proved in two different ways,
because `F5` rules out the obvious one.** At step 1a the entire
existing test suite passes, and the only edit permitted to a test
file is the construction of the argument passed to a converted
predicate — one line per helper, as `F2a` names them. No assertion,
no expected value, no test name and no fixture may change, and any
other test edit in that step's diff is a behaviour change until
argued otherwise. At step 1b, the new multiset tests assert
candidate *sets*, bucket tiers, exception types and stages, and
event sequences; never list order.

*Amended during execution.* This decision first read that no test
file may change at all, which `F2a` showed to be unachievable:
converting a private predicate's parameter changes the type its
direct callers must pass, and five of those callers are test
helpers. The rule's purpose survives the amendment — a test edit
still signals a behaviour change — because the amendment admits
only argument construction, which carries no assertion. Reverting
to the original rule would have meant leaving the predicates
`Instance`-bound, which is the phase.

**`D9`. The multiset path carries production traffic from the
first commit.** Both callers route through it at one shape rather
than the core sitting dormant behind a phase-2 caller. A
generalisation that only ever runs at `n=1` in production until
phase 2 would have its interesting case first exercised by a new
API; this way the walk, the overlay and the all-or-nothing unwind
are on the create path from the day they land, at the arity where
their behaviour is already known.

## Step plan

Steps run in sequence. Every one of them runs `tox` and
`pre-commit` in the same worktree, so none can run beside another,
and each depends on its predecessor's code.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | **The shape type, and the filters converted to read it.** Read this phase plan's `F2`, `F3`, `D1` and `D8` first. In `shakenfist/scheduler.py`: add `PlacementShape` as a `NamedTuple` (`cpus`, `memory_mb`, `disk_gb`, `affinity`, `namespace`, `instance_uuid=None`, `placed_on=None`) with a `from_instance(inst)` factory that sums `disk_spec` exactly as `find_candidates()` does at `:942-945` and `_has_sufficient_disk()` does at `:830-836` — note those are two copies of the same sum today, and the factory becomes the one copy. Convert `_has_sufficient_cpu` (`:684`), `_has_sufficient_ram` (`:753`), `_has_sufficient_disk` (`:829`), `_has_idle_disk_bandwidth` (`:857`), `_satisfies_hard_affinity` (`:623`) and the module-level `_affinity_neighbour_skip` (`:193`) to take a shape in place of `inst`. The two `instance.placement_filter(node, inst)` calls (`:729`, `:814`) become a comparison against `shape.placed_on`; preserve the "a reschedule onto the same node is not charged twice" behaviour exactly. `find_candidates()` keeps its name and signature (`D7`), builds a shape via the factory, and keeps `inst.attribute_memo()` around the whole decision. Do **not** add the multiset walk, change any stage name or literal, change any event's name, subject, order or payload, or touch `place_on_candidates()`. Do not restate `_affinity_neighbour_skip()`'s predicate anywhere (`docs/developer_guide/coding_rules.md:7`). Checks: `tox -epy3` and `pre-commit run --all-files` from the repository root, and `shakenfist/tests/test_scheduler.py` (2974 lines) must pass **with no edits to any test file** — if a test needs changing, stop and report which and why rather than changing it. Commit subject: `Denominate placement in a requested shape.` |
| 1b | high | opus | none | **The multiset core.** Read `F3`, `F5`, `D2`, `D3`, `D4`, `D5`, `D9` first, and step 1a's diff. Add one entry point beside `find_candidates()` that takes a list of `PlacementShape`, an optional forced candidate list, and an explicit event-target list, and returns a per-shape assignment (shape index to ordered candidate list, or to the chosen node) all-or-nothing: if any shape has no candidate left at any stage, the whole call raises, with the same exception classes and `stage=` values the single-shape path raises today (`_log_and_raise_on_error`, `:876`). Walk shapes largest-first by memory, then vCPUs, then disk (`D4`). After each shape is assigned, apply its tentative charge to a per-call overlay covering all six figures in `F3`'s table, in their own units — `disk_free_instances` is bytes, so debit `disk_gb * GiB` — and rank the next shape against the overlay. Copy the dicts; never mutate `self.metrics` or the rows from `_capacity_by_node()` in place, because the `Scheduler` outlives the call. A node with no capacity row stays uncharged on the ledger terms and charged on the measured terms (`P7`). `find_candidates()` becomes the one-shape caller (`D9`), passing exactly today's event targets. Tests in `shakenfist/tests/test_scheduler.py`: a two-shape set that fits; a two-shape set where the second shape fits only because the first was charged elsewhere; a set that fails all-or-nothing and raises with the right stage; a set whose total exceeds one node but fits the cluster; the bytes-versus-GiB debit; and a case proving an absent capacity row is charged on measurement but not on the ledger. Assert candidate *sets*, tiers, exception types and stages, and event sequences — never list order (`F5`, `D8`). Checks as 1a, plus the full `tox` default envlist. Step 1a left one thing for you: the `schedule inputs` event publishes the raw `disk_spec` list and the shape carries only the summed `disk_gb`, so the wrapper still reads `inst.disk_spec` for that one field. If the core emits that event, add `disk_spec` to `PlacementShape` as an optional field defaulting to `None` and have `from_instance()` fill it, so a dry-run shape in phase 2 can leave it unset rather than inventing one. Do not drop the field from the event: `F6` makes it a documented diagnostic. Commit subject: `Place a multiset of shapes all or nothing.` |
| 1c | medium | sonnet | none | **Prove the new tests would catch the core's removal.** Use the `mutation-check` skill's method against the step 1b code only. At least these five mutations, each reverted from a copy and never with `git checkout`: delete the tentative charge entirely; charge the ledger figures but not the measured ones (`F3`'s real failure mode); charge disk in GiB against a bytes figure; make the all-or-nothing unwind return the partial assignment instead of raising; reverse the largest-first shape ordering. For each, run `shakenfist/tests/test_scheduler.py` under `timeout 900` and record which test failed and on which assertion. A mutation that no test catches is a finding: report it with the test that should have caught it, and do not write the test yourself. Write the script and its output to the scratch directory this step is given, and report its full path. Report counts only; change no file under `shakenfist/`. Commit subject: none — this step's output is a report, and the management session records it in the plan's Results. |
| 1d | medium | sonnet | none | **Document the core where the scheduler is already documented.** `docs/operator_guide/scheduler.md:363-370` describes the shared placement walk; add to that passage, in its voice, that the filter pipeline in front of the walk now evaluates a requested shape and can evaluate several all-or-nothing, and that the only production caller today asks for one. Do not document an API, a capability or a dry run: none exists yet, and `docs/plans/` is the only place a plan phase may be named (the `Check documentation cites no plan phase numbers` pre-commit hook enforces this). Check `:1086-1092`'s diagnostic procedure is still literally true after steps 1a and 1b and say so, or fix it if it is not. Leave `AGENTS.md` and `ARCHITECTURE.md` alone: no convention and no component boundary changed. Checks: `pre-commit run --all-files`, and the `Check documentation links and anchors resolve` hook must pass. Then fix the two prose citations step 1a made stale, both found during its review and neither touched by it, because `D8` confined that step to argument construction. `shakenfist/deploy/shakenfist_ci/retries.py:53` says "`_has_sufficient_disk()` sums the 'size' of each disk which has one" — that sum is now `PlacementShape.from_instance()`, and the point of the sentence is that the CI helper counts disk the way the scheduler does, so repoint it rather than rewriting it. `shakenfist/tests/test_scheduler.py:1235`'s docstring explains that the placement attribute is read once because the self-charge check "runs for every candidate carrying a non-zero used_cpus" and the decision memoises the row; it is now read once by construction, at the top of the decision. Fix the explanation and leave the `assertLessEqual(gia.call_count, 1)` assertion alone — its bound already covers the all-zero-ledger cluster, where the count went from 0 to 1. Commit subject: `Document shape-denominated placement.` |

## Risks and mitigations

- **A behaviour change hides inside a refactor of the create
  path.** This is the phase's whole risk. Mitigated by `D8`'s two
  proofs — step 1a may not edit a test, and step 1b's tests assert
  sets rather than order — and by the management session reading
  both diffs rather than the reports. The reviewer's specific job
  is the two `placement_filter()` conversions and the disk sum
  de-duplication, which are the three places where "same
  arithmetic" is easy to claim and easy to get wrong.
- **The event stream drifts and takes `F6`'s documented procedure
  with it.** Mitigated by step 1d checking
  `scheduler.md:1086-1092` against the code, and by a step 1b test
  asserting the event sequence of a one-shape call. The management
  session additionally greps the diff for every changed
  `add_event_multi()` argument list before committing 1a.
- **A textual conflict with scheduler-reservations phase 5a
  (`F8`).** Both touch `external_api/instance.py` within a few
  lines. Not a correctness risk: neither change needs the other.
  Mitigated by `D7` to the point of disappearing — keeping
  `find_candidates()`'s name, signature and return value means
  this phase does not edit `shakenfist/external_api/` at all, and
  definition-of-done item 9 is what holds it to that. If a step
  finds it must edit that file, the sequencing becomes Michael's
  call and the step stops and says so rather than deciding.
- **The overlay charges a figure the real guard does not, and the
  dry run becomes optimistic or pessimistic by construction.**
  Mitigated by `F3`'s table being the complete enumeration of what
  the filters read — derived by reading each filter, not by
  guessing — and by step 1c mutating the ledger-only case
  specifically, because that is the plausible mistake.
- **Greedy refuses sets the cluster could host (`D4`).** Not
  mitigated in this phase, deliberately: it is recorded as a lower
  bound, and phase 2's shadow counts are the instrument that would
  show it happening on real traffic before `A` depends on it.

## Definition of done

1. `PlacementShape` exists in `shakenfist/scheduler.py` and is the
   only way the filter, affinity and ranking stages learn a
   requested shape: `grep -n 'inst\.' shakenfist/scheduler.py`
   returns hits only inside `find_candidates()`'s wrapper layer
   and `PlacementShape.from_instance()`.
2. Step 1a's only change under `shakenfist/tests/` is argument
   construction in the five helpers `F2a` names, per the amended
   `D8`: `git show <1a sha> -- shakenfist/tests/` adds and removes
   one line per helper and touches no assertion, expected value,
   fixture or test name.
3. A multiset entry point exists, and both production callers
   reach the filters through it. `grep -rn 'find_candidates('
   shakenfist/ --include=*.py | grep -v tests/` still shows the
   same four call sites across two callers that it shows today:
   `external_api/instance.py:1178` and `:1180`,
   `operations/node_inst_netdesc_op.py:193` and `:260`.
4. A test fails if the tentative charge is removed, and a
   different test fails if only the measured figures are left
   uncharged. Step 1c names both tests and the assertion each
   failed on.
5. The disk debit is in bytes. A test asserts a shape of `n` GB
   debits `n * GiB` from `disk_free_instances`, and step 1c's
   GiB-for-bytes mutation is caught.
6. No stage literal and no event name changed:
   `git diff develop..HEAD -- shakenfist/scheduler.py | grep -E
   "^[-+].*(_log_and_raise_on_error\(|add_event_multi\()" ` shows
   no change to any stage string or event name, and the nine
   stages of `_log_and_raise_on_error()` are still nine.
7. `docs/operator_guide/scheduler.md:1086-1092`'s procedure is
   still literally performable: the create-path pass still
   publishes `schedule inputs` with `forced_candidates` false, and
   still publishes `schedule have highest affinity`. Checked by
   reading the code, and asserted by a test.
8. `tox` and `pre-commit run --all-files` pass from the
   repository root.
9. No API, capability string, client method or schema changed,
   and `shakenfist/external_api/` is untouched entirely:
   `git diff --name-only develop..HEAD -- shakenfist/external_api/`
   is empty. `D7` keeps `find_candidates()`'s name, signature and
   return value, so the create path needs no edit — which also
   removes `F8`'s conflict with scheduler-reservations phase 5a
   rather than merely managing it.

## Back brief

Before executing any step, back brief the operator on your
understanding of this phase: that the de-duplication it was
commissioned to perform was done by another plan two days after
this one was written (`F1`), so the phase is now a
generalisation of the single-instance pipeline rather than an
extraction; that it promises no behaviour change on the live
create path and proves that by refusing to edit a test in step 1a;
that the multiset path carries production traffic at one shape
from the first commit; and that a greedy "does not fit" is
recorded as a lower bound rather than a proof.

Two design choices and one logistic need Michael's answer before
step 1a runs. `F8`'s sequencing question against
scheduler-reservations phase 5a is **not** among them: `D7` and
definition-of-done item 9 keep this phase out of
`shakenfist/external_api/` entirely, so the two branches do not
touch the same file.

- **`D4`'s lower bound.** Greedy assignment, stated as advisory,
  with reordering rather than search as the remedy if phase 2's
  counts show it refusing real sets — or an exact search now, at
  the cost of describing placements the scheduler would not make.
- **`D6`'s affinity blindness.** A set's own members are charged
  for capacity but are not neighbours for affinity. Accept it and
  let phase 2's shadow scoring say whether any caller minds, or
  settle it now at the cost of a synthetic-neighbour path through
  a predicate `coding_rules.md` says not to restate.
- **Whether this session may push and open the pull request** when
  the steps are done.

## Results

### Step 1c: do the multiset tests bite?

Step 1b's tests are only worth what they catch, so each of the five
mutations the step plan names was applied to `shakenfist/scheduler.py`
in turn and the scheduler suite run against it. **All five were
caught; none survived.** The run was taken twice — once by the step,
once re-run independently by the management session — and both
produced the figures below.

The suite was run as `.tox/shared/bin/python -m unittest
shakenfist.tests.test_scheduler` under `timeout 900`: 154 tests,
baseline clean. Each mutation was applied to the live file by string
replacement from a pristine copy held outside the worktree, and
restored from that copy in a `finally` rather than with
`git checkout`. The worktree compared byte-identical afterwards and
`git status --short` was empty.

| Mutation | Tests failed | First failure |
|----------|--------------|---------------|
| `charge()` made a no-op | 8 | `test_a_charge_reranks_the_node_it_was_charged_to` |
| Committed row charged, measured figures not | 6 | `test_a_disk_charge_excludes_a_set_mates_node` |
| Disk debited in GB against a bytes figure | 2 | `test_a_disk_charge_excludes_a_set_mates_node` |
| All-or-nothing unwind returns the partial assignment | 21 | `KeyError: 0` at `return assignment[0]` |
| Largest-first shape ordering reversed | 1 | `test_shapes_are_walked_largest_first` |

Three readings worth keeping. The mutation that matters most is the
second — charging the ledger and forgetting the measurements is the
realistic mistake, and `F3` exists because of it — and it is caught by
six tests including `test_the_measured_charge_excludes_a_set_mates
_node`, which was written for it. The thinnest margin is the third, at
two tests, both of them aimed at the disk debit rather than incidental.
The fourth's 21 failures are mostly collateral: every single-shape
caller indexes `assignment[0]` and gets a `KeyError` when the unwind
returns early, which is a loud way to be caught but not a precise one
— what makes it a real catch is that
`test_a_set_bigger_than_the_cluster_raises_rather_than_part_placing`
is among the failures.

The script is re-runnable with no arguments and lives in this
session's scratch directory as `step-1c/run_mutations.py`, with a log
per mutation beside it. It is not committed: it mutates a working
tree, which is not a thing to leave in the repository.

### Step 1d: is the documented diagnostic procedure still true?

Yes, unchanged. `docs/operator_guide/scheduler.md:1080-1100` tells an
operator to pick the create-path scheduling pass by finding the
`schedule inputs` event whose `forced_candidates` is false, and then to
read that pass's `schedule have highest affinity`. Both events are
still emitted, each from exactly one place in the file
(`shakenfist/scheduler.py:1255` and `:1530`), `forced_candidates` is
still published beside the shape's figures (`:1263`), and both now sit
inside the per-shape walk. `find_candidates()` passes one shape, so for
both production callers that is still once per pass. The two code
commits moved these events; they changed no name and no field.

One consequence is worth writing down before a later phase meets it.
The events are emitted once per *shape*, so a multi-shape call that is
given event targets publishes several `schedule inputs` events whose
`forced_candidates` are all the same, and "pick the pass whose
`forced_candidates` is false" stops identifying a single pass. The dry
run cannot cause this, because it is given no event targets and so
publishes nothing. A hold evaluation that events against a claim can,
so whichever phase builds that should decide whether a shape index
belongs in the payload, and amend the operator procedure if it does.
