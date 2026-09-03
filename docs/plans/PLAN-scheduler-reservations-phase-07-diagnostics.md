# Scheduler reservations phase 7: capacity diagnostics

## Prompt

This is a phase plan under
[PLAN-scheduler-reservations.md](PLAN-scheduler-reservations.md).
The master plan's Prompt section applies unchanged; the decisions
it refers to as D-numbers live in
[PLAN-scheduler-reservations-phase-00-decisions.md](PLAN-scheduler-reservations-phase-00-decisions.md),
the P-numbers in
[PLAN-scheduler-reservations-phase-03-primitive.md](PLAN-scheduler-reservations-phase-03-primitive.md),
the E-numbers in
[PLAN-scheduler-reservations-phase-04a-demand-guard.md](PLAN-scheduler-reservations-phase-04a-demand-guard.md)
and
[PLAN-scheduler-reservations-phase-04c-conductor-claims.md](PLAN-scheduler-reservations-phase-04c-conductor-claims.md),
and the F-numbers in
[PLAN-scheduler-reservations-phase-06-affinity.md](PLAN-scheduler-reservations-phase-06-affinity.md).
This phase's own decisions are numbered G1..G7 so they collide
with none of them.

Planning effort: **high**, and not because the code is hard. The
survey below found that most of what the master plan asked this
phase to build already exists, so the judgement this phase
actually needs is about what is *missing* from a surface that
looks finished -- which is a harder read than an empty file.
Review effort: **medium**; the steps are small and each is
independently falsifiable.

## Situation

The master plan's stub for this phase is one sentence:

> **Phase 7 -- diagnostics.** Failure-path verbose diagnostic
> against the same snapshot, success-path drawdown events,
> ceiling-rejection events (D9). Confirm CI triage tooling reads
> the new events.

That was written on 2026-05-22, before phases 3, 4, 4a and 6
executed. Each of them shipped diagnostics as a side effect of
shipping its own behaviour, because a guard nobody can see the
reasoning of is not reviewable. The result is that the three
deliverables D9 named are substantially built, and the master
plan does not know it.

What is left is not "add diagnostics". It is the specific set of
places where the existing surface is asymmetric, unreadable, or
silently degrades -- and every one of those was found by looking,
not by reading the stub.

## Mission and problem statement

Close the gaps between what the capacity subsystem *knows* and
what an operator or a triage tool can *read*, so that phase 4c's
observation record and phase 5's enforcement decision rest on
evidence somebody can actually fetch.

The load-bearing case is concrete. D9 says the ceiling-rejection
event "is the signal a workflow's declared footprint needs
revision". Phase 4c's whole purpose is to collect that signal
over a soak and write an observation record from it, and phase 5
does not start until that record exists. Today that signal is
written to an object with no REST events endpoint. The plan's
critical path runs through a gap this phase owns.

## Scope

In scope:

- Making claim and namespace capacity events readable over the
  REST API.
- The claim-exceedance event: what it carries, and where it is
  recorded so it survives the claim.
- Symmetry of the placement ledger's two halves in the event
  trail: drawdown is evented richly, release is not.
- Making the capacity read's documented degradation visible in
  the audit trail rather than only in a node's log.
- Measuring what the existing scheduling event volume costs, and
  deciding on that measurement whether a diagnostic *mode* is
  wanted at all (G1).
- Correcting the master plan's phase 7 stub and its name at
  source, per the survey.

Out of scope, and each for a stated reason:

- **Adding scheduler stage events.** There are fifteen already
  and they carry per-node, per-resource reasons. The gap is not
  coverage.
- **Any change to admission behaviour.** This phase must not move
  a counter or change a refusal. Phase 4c is mid-soak; a
  behavioural change lands inside its measurement window and
  spoils the data it is collecting. Every step here is
  observability only.
- **The #3772 507 family.** It belongs to `PLAN-ci-cloud-sizing`,
  which says so, and the master plan's phase 6 stub says so
  again.
- **A generic cross-object event query.** The `events-by-type`
  capability is a query *parameter* on five per-object endpoints,
  not a cross-object search. Building one is a different plan.

## What the survey found (2026-09-03)

The stub's premise is largely false. Verifying it claim by claim
against `develop` at `948055063`:

**"Failure-path verbose diagnostic against the same snapshot" --
shipped, and past the depth D9 asked for.** Every stage of
`find_candidates()` builds a `dropped` dict keyed by node and
publishes it through `_log_and_raise_on_error()`
(`scheduler.py:520-541`), which emits `schedule at stage <name>`
on success and `schedule has no candidates at stage <name>,
aborting` on refusal. The per-node values are structured, not
strings: `cpu_max_per_instance` carries the limit and the request
(`:631-635`), and the three headroom stages carry whatever
`_has_sufficient_cpu()` / `_has_sufficient_ram()` /
`_has_sufficient_disk()` return (`:646-679`). Below the guard,
`instance placement denied` carries `failing_stage` and a
per-dimension `dimensions` list (`instance.py:1253-1260`) whose
entries are `CapacityDimensionDetailDict`
(`mariadb.py:25378-25392`) -- dimension, limit, used, requested,
exceeded, plus the `cpu_load_1` / `expected_demand` split that
issue #3913 added on 2026-08-27.

**"Success-path drawdown events" -- shipped for the drawdown
half, absent for the release half.** `instance placed`
(`instance.py:1284-1298`) carries the node, the request and the
four post-drawdown counters `node_used_cpus`,
`node_used_memory_mb`, `node_used_disk_gb` and
`node_expected_demand`. Its opposite number, `instance placement
released` (`:1527-1530`), carries `{'node': node_uuid}` and
nothing else -- not what was released, not the counters
afterwards. A reader can reconstruct the ledger going up and not
coming down. Note that `release_instance_placement()` already
returns the numbers; they are read for the `clamped` branch three
lines earlier (`:1516-1526`) and then dropped on the floor.

**"Ceiling-rejection events" -- shipped, but on the wrong object
and missing a field.** `_event_claim_over_limit()`
(`instance.py:1145-1176`) emits `placement admitted over
namespace capacity claim` with `claim_dimensions`. D9 asked for
that event to be "on the claim carrying limit, used and
shortfall". It is on the *instance*, and `shortfall` is not among
the `CapacityDimensionDetailDict` keys -- a reader must subtract
`limit` from `used + requested` themselves, per dimension.

**And the gap that matters most: neither claims nor namespaces
have a REST events endpoint.** `app.py` registers exactly five
(`:455`, `:506`, `:523`, `:581`, `:601`) -- artifacts, blobs,
instances, networks, nodes. The claim routes at `:491-493` are
CRUD only. So the existing `namespace claim deleted, capacity
returned` audit event (`namespace_claim.py:415-420`) is already
write-only today: it is deliberately recorded against the
namespace because `hard_delete()` is about to remove the claim's
own events, and no endpoint serves a namespace's events either.
That reasoning is right and the destination is unreachable.

**The capacity read degrades silently, and its own consumer says
so.** `_grpc_get_scheduler_node_capacity()`
(`mariadb.py:26739-26741`) catches `DatabaseUnavailable` and
`grpc.RpcError`, emits a `LOG.warning`, and returns `[]`. That is
a legitimate, explicitly-documented exception to CLAUDE.md's
"not found means genuinely absent" rule -- the docstring at
`:26710-26718` gives the reason (issue #3586's watchdog window)
and it is the right call. But the return is indistinguishable
from an empty table, `_capacity_by_node()` (`scheduler.py:234-252`)
hands that straight on, and nothing writes it to the instance's
event trail. `tools/ci_headroom_report.py` names the same problem
from the far end, in its own docstring: "an unreadable table and
one the reconciler has never populated are indistinguishable from
here; both are worth knowing and neither is an idle cluster."

**"Confirm CI triage tooling reads the new events" -- already
true, and built by a different plan.**
`tools/ci_headroom_report.py:111-113` matches on
`STAGE_SURVIVED_PREFIX = 'schedule at stage '` and
`STAGE_ABORTED_PREFIX`, and censuses every stage string it
observes rather than a fixed list. It came from
`PLAN-ci-cloud-sizing` phase 1, not from here. What has *not*
been confirmed is whether it reads the guard's own events --
`instance placement denied` and the claim exceedance -- which are
below the stage layer it parses.

**There is no diagnostic mode, and nothing is gated.** No
`SCHEDULER_*` config key in `config.py:403-510` gates event
emission; every event above fires on every schedule. The phase's
name in the Execution table, "Diagnostic-mode rejection logging",
describes something that was never built and that the survey
suggests is not wanted. See G1.

**Corrected at source.** Per the skill, the master plan's phase 7
stub and its Execution-table name are corrected in the same
commit as this plan, so the next reader does not re-derive the
above. A later step must not redo it.

## Decisions

**G1: there is no diagnostic mode, and the phase is renamed.**
The Execution table calls this phase "Diagnostic-mode rejection
logging". A mode implies a switch, a switch implies a default,
and a default-off switch means the events are missing exactly
when an operator wants them -- after the incident. Everything the
subsystem emits today is unconditional, has been through four
phases of production and CI use in that form, and is what
`ci_headroom_report.py` was built against. Introducing a gate now
would break that tool's premise and would be a behavioural change
inside phase 4c's soak window. The phase becomes **"Capacity
diagnostics"**, and step 5 measures the volume so the decision to
stay unconditional is one somebody checked rather than one this
plan asserted.

This is the decision most likely to be argued with, so the
argument against is worth stating plainly: fifteen audit events
per schedule is a lot, the events table has a history of being
flooded by exactly this shape of thing (the namespace-key storm),
and "measure it later" is how that happened. The answer is that
step 5 measures it *in this phase* and its finding is a
deliverable -- if the volume is indefensible, the right response
is to make specific events cheaper or coarser, which is a
targeted change, not a global mode switch that turns off the
diagnostics on the very clusters that need them.

**G2: claim events are readable through a claim events
endpoint, and exceedance is recorded on the namespace.** Two
halves, deliberately different.

The endpoint is added because a claim is an object with events
and no way to read them, which is a hole in the REST surface
independent of anything else here. It follows the five existing
endpoints exactly, and is admin-gated like every other claim
verb.

The exceedance event is recorded against the **namespace**, not
the claim, which is a deliberate deviation from D9's literal
words. `namespace_claim.py:412-420` already established the
reasoning for the deletion event and it applies unchanged: a
claim's events die with the claim, and "what happened to my
namespace's capacity" is a question that outlives any particular
claim. A calibration record assembled after a claim was deleted
and recreated -- which is what growing a claim looks like to an
operator who used delete-and-create rather than update -- would
otherwise have a hole in it exactly where the interesting event
was. D9 was written before `NamespaceClaim` existed as an object
with a lifecycle; it named "the claim" to mean "the claim's
accounting", and the namespace is where that accounting is
durable.

That decision forces the endpoint question: a namespace has no
events endpoint either. So the claim endpoint alone is not
enough, and **both** are added. This is why G2 is one decision
rather than two -- choosing the durable destination and choosing
what to expose are the same choice.

**G3: `shortfall` is added to the dimension detail, computed
where the numbers are.** D9 asks for it and the type does not
carry it. It is added to `CapacityDimensionDetailDict` and
populated in the same place `exceeded` is, as `used + requested -
limit` floored at zero, so a reader never subtracts and two
consumers never disagree about the sign convention. Floored
rather than signed because a negative shortfall is headroom, the
detail already carries the three numbers headroom would be
derived from, and a field that means "shortfall" in one row and
"spare" in another is worse than absent.

**G4: the release event carries what the placement event
carries.** `instance placement released` gains the released
amounts and the resulting counters, from the reply the caller
already holds. Symmetry is the whole point: the ledger is only
auditable from events if both directions are legible, and the
asymmetry today is an oversight rather than a decision -- the
numbers are read three lines above and discarded.

**G5: a degraded capacity read is an audit event, not only a log
line.** When `_capacity_by_node()` returns empty, the scheduler
cannot tell an unreadable table from a cluster the reconciler has
not populated, and neither can anyone reading the instance's
events afterwards. The distinction is made where it is known --
in `mariadb.py`, which knows whether it caught an exception --
and surfaced to the scheduler, which events it. The event fires
on the degraded path only, so it costs nothing on a healthy
cluster and is the one new *unconditional* event this phase adds.

Deliberately not a change to the swallow itself: the swallow is
correct and documented, and #3586 is the reason. This makes it
visible, not conditional.

**G6: the CI triage tool learns the guard's events, and the
verification is a run rather than a reading.** `ci_headroom_
report.py` parses the stage layer and stops above the guard, so a
run in which every stage passed and the guard refused every
candidate reads as a clean run with no refusals -- which is
precisely the #3772 shape. It gains a guard-refusal census beside
its stage census. The done-criterion is that it is run against a
real captured series, not that the code was written.

**G7: no schema migration; three proto changes, all
additive reply fields.** Nothing here adds a table, an index or a
migration -- stated because every other phase in this plan did
add schema, and a reviewer should be able to confirm the absence
quickly rather than search for it.

**Corrected in the close-out (2026-09-03): three proto changes,
not the two this decision first counted.** `shortfall` joins
`CapacityDimensionDetail` (step 1), the
`ReleaseInstancePlacement` reply gains the post-release counters
(step 2), and `AdmitInstancePlacementReply` gains `claim_uuid`
(field 14). The second was found late, while checking this
plan's own step 2 brief against the tree: that brief originally
said to take the counters "from the reply already bound as
`result`", and `ReleasePlacementResult`
(`mariadb.py:25416-25422`) carries `success`, `error`, `released`
and `clamped` and no numbers at all. G4's symmetry cannot be had
caller-side -- the released *amounts* can (`instance.py:1500`
holds them), the resulting *counters* cannot. The third was found
later still, by the follow-up pass that closed a gap step 4 left
open: recording the claim exceedance against the namespace (G2)
needs a way to say *which* claim, and nothing on the wire carried
the claim's own uuid back to the caller, so the follow-up widened
the admission reply to add it. Recorded here so a reader does not
find `claim_uuid` in the diff and wonder where it came from.

**Corrected again in the review follow-up (2026-09-04): four
proto changes.** `GetSchedulerNodeCapacityReply` gains `bool
degraded = 2` so the database daemon's own failed read reaches
its clients, and `shortfall` becomes `optional` -- the same
change of kind the demand breakdown fields already made, for the
same reason. See the review follow-up section below.

All four are additive fields on reply messages, so an older
client reading a newer server sees proto3 defaults rather than an
error, and none needs `tox -e genprotos` output hand-edited.
Proto3 defaults are only a safe reading where the default is a
meaning the field can honestly carry, which is why two of these
are `optional` and not plain scalars. Widening a reply is a
bigger change than reporting a computed value, which is why step
2 is opus at high effort and not sonnet.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | Add `shortfall` to the capacity dimension detail (G3). Add the field to `CapacityDimensionDetailDict` (`shakenfist/mariadb.py:25378-25392`), to `CapacityDimensionDetail` in `protos/database.proto` (`:2773-2780`), which is the single message both `dimensions` (`:2804`, `:2923`, `:2974`) and `claim_dimensions` (`:2822`) are repeated fields of -- so one field addition covers every consumer, and populate it wherever `exceeded` is set -- the node-guard detail builder and the claim-guard builder around `mariadb.py:26441-26443`. Value is `max(0.0, used + requested - limit)`; floored, never signed (G3 says why). Update `_dimension_from_proto()` (`:26655-26656`) to carry it back. Regenerate stubs with `tox -e genprotos` -- never run `grpc_tools.protoc` directly, see CLAUDE.md. Do not change any guard's decision: `shortfall` is reported, never tested. Commit subject: "Report the shortfall on a refused capacity dimension." |
| 2 | high | opus | none | Make the release event match the placement event (G4). In `shakenfist/instance.py`, `instance placement released` at `:1527-1530` carries only `{'node': node_uuid}`. It needs two different things from two different places, and the planning survey got this wrong once already, so read both before writing. **The released amounts** are already caller-side: `cpus, memory_mb, disk_gb = self._capacity_claim` at `:1500`, the same triple passed into the RPC. Adding those needs no RPC change. **The resulting counters** are not available at all: `ReleasePlacementResult` (`mariadb.py:25416-25422`) is `success`, `error`, `released`, `clamped` and nothing else, so the reply must be widened to carry the post-release counters the way the admission reply carries the post-drawdown ones. That means `ReleaseInstancePlacementReply` in `protos/database.proto` (`:2837`), the TypedDict, `_direct_release_instance_placement()` where it already holds the updated row, and the gRPC unpacking -- mirroring how `node_used_cpus` and friends reach `AdmitPlacementResult`. Regenerate with `tox -e genprotos`. Then event both, using the placement event's key names (`:1284-1298`) so both halves of the ledger share one vocabulary. Add a unit test asserting the event's `extra` keys. Commit subject: "Record what a placement release returned." |
| 3 | high | opus | none | Add events endpoints for namespaces and namespace claims (G2). Neither exists today: `shakenfist/external_api/app.py` registers events routes only at `:455`, `:506`, `:523`, `:581` and `:601`. Add `/auth/namespaces/<namespace>/events` and `/auth/namespaces/<namespace>/claims/<claim_ref>/events`, mirroring `NodeEventsEndpoint` (`external_api/node.py:159`) as the closest structural match. Both are `caller_is_admin`, matching the sibling claim routes and the reasoning in the comment block at `auth.py:1194-1209`. Read `docs/developer_guide/writing_an_endpoint.md` first: every handler needs a `swag_from(swagger_helper(...))` declaration, a route segment is a `path` parameter and must be `required=True`, and a malformed declaration stops sf-api from starting. `event_type` and `limit` are `query` parameters -- see how the node endpoint declares them, and preserve the `events-by-type` capability behaviour. Add the routes to the capability list around `app.py:329-344` if the existing endpoints are advertised there. Commit subject: "Serve namespace and claim events over the API." |
| 4 | medium | sonnet | none | Record claim exceedance against the namespace (G2). `_event_claim_over_limit()` (`shakenfist/instance.py:1145-1176`) currently events only against the instance. Keep that event exactly as it is -- an instance's own trail should say its placement went over -- and additionally write the same facts against the namespace, using the `eventlog.add_event(EVENT_TYPE_AUDIT, 'namespace', self.namespace, ...)` form already used at `namespace_claim.py:415-420`. Read that call and its comment first; it explains why the namespace rather than the claim is the durable destination, and this step is the second instance of the same pattern. Include the claim uuid in `extra` so the two can be correlated. Do not change the advisory semantics or add a refusal -- `CLAIM_ENFORCEMENT_HARD` is phase 5's to flip, and the docstring at `:1157-1165` explains why this is a warning and not an error. Commit subject: "Record claim exceedance against the namespace." |
| 5 | high | opus | none | Make a degraded capacity read visible (G5). `_grpc_get_scheduler_node_capacity()` (`shakenfist/mariadb.py:26739-26741`) catches `DatabaseUnavailable` and `grpc.RpcError`, logs a warning and returns `[]`, which `_capacity_by_node()` (`shakenfist/scheduler.py:234-252`) cannot tell from an empty table. Do not change the swallow -- it is correct and #3586 is the reason, as the docstring at `:26710-26718` records. Instead give the caller a way to know: the cleanest shape is a sentinel or a second return value distinguishing "read failed" from "no rows", threaded through `_capacity_by_node()` to `find_candidates()`, which emits one audit event naming the degradation before the CPU stage runs. Choose the shape yourself but state the reasoning in the commit message; a `None`-versus-`[]` distinction changes a public accessor's contract, so check every caller of `get_scheduler_node_capacity()` before picking it, and prefer a shape that cannot be silently ignored by a caller that does not care. The event fires only on the degraded path. Add a unit test that forces the failure and asserts the event. Commit subject: "Say so when the capacity counters cannot be read." |
| 6 | high | opus | none | Teach the CI triage tool the guard's events, and run it (G6). `tools/ci_headroom_report.py` censuses scheduler stage events (`:111-113`) and stops above the capacity guard, so a run where every stage passed and the guard refused every candidate reads as clean -- the #3772 shape exactly. Add a guard-refusal census beside the stage census, matching on the `instance placement denied` message and tallying by `failing_stage` and by exceeded dimension, plus a separate tally of `placement admitted over namespace capacity claim`. Honour the file's two hard constraints, both stated in its module docstring: standard library only, no shakenfist import (it runs on the runner under whatever python3 the runner image ships), and it exits zero whatever it finds (D15 of the sizing plan) -- an instrument that can fail the job changes what it measures. Then **run it** against a real captured series from a recent cluster CI job and paste the output into this plan under a "What the tool reported" heading. Commit subject: "Census capacity guard refusals in the CI headroom report." |
| 7 | high | opus | none | Measure the scheduling event volume and record the finding (G1). `find_candidates()` emits up to fifteen audit events per schedule, unconditionally, and `add_event_multi()` (`shakenfist/eventlog.py:35-131`) enqueues one spool payload carrying N objects -- so the fan-out to `events` table rows depends on what `RecordEventBatch` does with that object list, which this plan did not establish and you must. Determine the actual rows-per-schedule cost, then measure the real share: query the events table on sfcbr for the fraction of rows in a recent window whose message begins `schedule `, the way the namespace-key event storm was quantified. Write the finding into this plan under "What the measurement found", and state in one sentence whether it supports G1's decision to stay unconditional or argues against it. Do not change any event emission in this step -- if the measurement argues against G1, that is a finding for the close-out and a follow-up issue, not a change smuggled into a measurement step. Commit subject: "Measure what the scheduling event trail costs." |
| 8 | medium | sonnet | none | Documentation and close-out. Document the new endpoints and the claim exceedance signal in `docs/operator_guide/scheduler.md` -- specifically that an operator calibrating a claim reads the namespace's events, and why not the claim's (G2's reasoning, in one sentence, not the whole argument). Record `shortfall`'s definition where the dimension detail is documented, once, so no two pages state it differently. Check whether `docs/developer_guide/subsystem_internals.md` describes the capacity event surface and update it if it does. Per CLAUDE.md's documentation policy, `ARCHITECTURE.md` and `AGENTS.md` should need no change here -- this phase changes neither a convention nor the shape of the system -- so if you find yourself editing either, stop and say why. Write the close-out section of this plan: what each step actually did, what the two measurements found, and what is left as future work. Commit subject: "Document the capacity diagnostics surface." |

### Step notes

Steps 1 through 5 are independent of each other and could run in
any order; 6 depends on 1 only if the census reports `shortfall`,
and 7 depends on nothing. Step 8 runs last because it writes up
the two measurements.

Steps 1 and 2 both regenerate protobuf stubs, so running them
concurrently in the same tree will conflict in
`shakenfist/protos/`. Run them in sequence, or give one of them
`isolation: "worktree"`; they are otherwise unrelated.

Step 3 is the one worth reviewing hardest despite being
mechanical in shape. `swagger_helper()` validates parameter
declarations at import time and sf-api does not start if one is
malformed (CLAUDE.md, "Parameter declarations are enforced"), so
a wrong declaration here fails the whole API rather than one
endpoint. `test_parameter_declarations.py` catches it in CI, but
the failure mode is worth knowing before writing rather than
after.

## Risks and mitigations

**A step changes admission behaviour inside phase 4c's soak
window.** This is the risk that matters. Phase 4c is collecting
the data phase 5's enforcement decision depends on, and a
behavioural change lands in the middle of that series without
announcing itself. Mitigation: every step's brief says
observability only, steps 1 and 5 say in as many words not to
change a decision or a swallow, and the definition of done
carries an explicit check that no guard's outcome moved. The
management session verifies that check by reading the diff, not
by trusting a summary.

**Step 5 changes a public accessor's contract.**
`get_scheduler_node_capacity()` is called from more than one
place and its documented "returns empty" behaviour is relied on.
Mitigation: the brief requires enumerating callers before
choosing the shape, and prefers a shape a caller cannot silently
ignore. Reviewed in the management session against the caller
list, not against the sub-agent's description of it.

**Step 7's measurement needs sfcbr and may not be runnable.**
The events-table query needs a live cluster. Mitigation: if it
cannot be run, the step records that it could not, and G1 is
carried as an unverified decision into the close-out with the
measurement as future work -- explicitly, the way phase 6 carried
its one unticked criterion, rather than ticked on the strength of
somebody probably having done it.

**The new endpoints expose events across a trust boundary.** A
namespace's events may name instances, nodes and other
namespaces. Mitigation: both endpoints are `caller_is_admin`,
matching the sibling claim routes, whose comment block explains
why claim verbs are cluster-admin operations even though the
resource hangs off a namespace. If review concludes a namespace
owner should read their own namespace's events, that is a
widening to argue for on its own merits and not a default to
fall into.

## Definition of done

Each item is falsifiable, and the commands quoted were run
against `develop` at `948055063` while writing this plan, so the
"today" figures are measurements rather than expectations.

- [x] `grep -c "'/.*events'" shakenfist/external_api/app.py`
      returns 7, having returned 5 today. Both new routes appear
      in the published OpenAPI specification, which is checked by
      `test_openapi_spec.py` rather than by reading the file.
- [x] A `GET` of `/auth/namespaces/<ns>/claims/<uuid>/events`
      returns the claim's events, and a `GET` of
      `/auth/namespaces/<ns>/events` returns the namespace's --
      including the `namespace claim deleted, capacity returned`
      event that `namespace_claim.py:415-420` has been writing to
      an unreadable destination since phase 4.
- [x] A placement which draws a namespace past its claim produces
      **two** events, one on the instance and one on the
      namespace, carrying the same dimensions and correlatable by
      claim uuid. Checked by reading both objects' events after
      one create, not by reading the code.
- [x] Every refused dimension in a `dimensions` or
      `claim_dimensions` list carries a `shortfall` equal to
      `effective_used - limit`, and never a negative one, where
      `effective_used` is `used + requested` on a charged
      dimension and `used` alone on an uncharged one. Stated
      against `effective_used` rather than the flat
      `used + requested - limit` this criterion first carried:
      the demand dimension is built with `charged=False`,
      because the guard never charges the incoming placement on
      demand, so the flat form would have demanded a number
      which disagrees with `exceeded` on exactly that dimension.
      Corrected during step 1, which found the single builder
      `_capacity_dimension()` that every guard funnels through.
      Asserted by a unit test which builds a refusal with
      `used + requested < limit` on one dimension and an
      exceedance on another, and checks both.
- [x] `instance placement released` and `instance placed` carry
      the same counter vocabulary. Falsifiable by diffing the two
      `extra` key sets in a test: every counter name on the
      release event also appears on the placement event, and
      today the difference is every key but `node`. Phrased as
      "every counter name" rather than the plain subset this
      criterion first carried, because step 2 found the subset
      property was unsatisfiable as stated -- the release event
      reports `disk_gb` and `instance placed` did not report it
      at all, so `instance placed` gains it rather than the
      release event dropping a third of the released
      allocation.
- [x] An unreadable capacity table produces an audit event on the
      scheduling instance which names the degradation, and an
      empty-but-readable table produces **no** such event. Both
      halves asserted -- the second is the one that catches an
      implementation which events unconditionally on an empty
      list, which would fire on every create on a cluster the
      reconciler has not reached yet (P7 says that is a normal
      state, not an error).
- [x] `ci_headroom_report.py` reports a non-zero guard-refusal
      count for a captured series in which the guard refused, and
      its output is pasted into this plan. Not "the census was
      added" -- run against real data, because the file's own
      history is that its stage census was written against
      assumptions and corrected after a run.
- [x] `ci_headroom_report.py` still imports nothing outside the
      standard library and still exits zero on malformed input.
      Checked by `grep -n '^import \|^from '` on the file --
      trailing spaces deliberate, since a bare `^import` also
      matches the word "imported." in the module docstring --
      and by running it against a truncated series. Today that
      returns argparse, collections, datetime, json, sys and
      traceback, and nothing else.
- [x] The share of `events` rows whose message begins
      `schedule ` is recorded as a number in this plan, with the
      window it was measured over, or the plan says the
      measurement could not be run and why.
- [x] **No guard outcome changed.** The diff touches no
      comparison inside `_direct_admit_instance_placement()`, no
      `_has_sufficient_*()` return value, and no
      `CLAIM_ENFORCEMENT_HARD`. Verified by reading the diff of
      `mariadb.py` and `scheduler.py` in the management session
      and stating so in the close-out.
- [x] The master plan's phase 7 stub no longer describes the
      three D9 deliverables as unbuilt, and its Execution-table
      name is no longer "Diagnostic-mode rejection logging".
      Both corrected in the planning commit, so this item is
      already true when the phase starts and is listed to stop a
      later step redoing it.
- [x] `pre-commit run --all-files` passes, including
      `check-plan-status.py` and `check-doc-anchors.py`.

## What the tool reported

Step 6's done-criterion is a run, not a diff, so this is what the
tool printed against real data.

**The data.** Cluster CI run 33732626981 (`Functional tests`,
`merge_group`, 2026-09-03), artifact
`bundle-shakenfist-full-debian-12-slim-tier`. The series is that
bundle's `traces/headroom.jsonl`: 114 samples over 1695 seconds
across three hypervisors. The census is the same bundle's
`traces/headroom-census.json`, the Loki `query_range` response
`ci_headroom_collect.sh` collected during the run: 1155 records.

**The first finding is about the collector, not the cluster.** The
census carries zero capacity guard events. It is collected with the
LogQL filter `|~ "schedule (at stage|has no candidates at stage)"`
(`tools/ci_headroom_collect.sh` in `shakenfist/actions`), which
selects the stage messages and nothing below them, so no census CI
collects today can show a guard refusal however many there were. The
new section says exactly that rather than printing a zero:

```
Shaken Fist CI headroom report
==============================
Label:  full-debian-12-slim-tier, run 33732626981

Series
------
  File:              bundle/traces/headroom.jsonl
  Samples:           114 usable, 0 failed (an "error" record), 0 unparseable lines
  Window:            2026-09-03 09:20:56Z to 2026-09-03 09:49:11Z (1695 seconds)
  LEDGER UNREADABLE: 11 of 114 samples had cpu_committed_row_present false
    for every node at once. The capacity read returns an empty
    map for an unreadable table and for an empty one alike, so
    it means no counter was visible at all.
    That is NOT that the cluster was idle.
    Those samples are excluded from the committed CPU figures
    below. Memory is unaffected: it comes from node metrics.

Ledger provenance (D7)
----------------------
  Node-samples with a capacity row (cpu_limit):     309
  Node-samples which fell back to cpu_hard_max:     0
  Fallbacks inside ledger-unreadable samples:       33
  Node-samples with no CPU ledger at all:           0
  The third figure is a failed capacity read, not evidence about
  the two ledgers. Those samples are already excluded from the
  headroom figures, and D7 should read the second line alone.

Cluster-wide headroom
---------------------
                          n     p90    peak   ledger p90 frac peak frac
  committed vCPU        103     9.0    10.0     12.0    0.750     0.833
  committed memory (MB) 114 22200.0 24248.0 107640.0    0.206     0.225
  Fractions are computed per sample and then percentiled, so each
  one is a ratio something actually stood at. Both sides of the CPU
  fraction are summed over the nodes which have a ledger, so it
  cannot exceed 1.0 because a node was missing one.

Committed vCPU, per node
------------------------
  node                                   n p90 peak ledger p90 frac peak frac
  13db082e-b18d-4c7a-b8e3-27af4ce5c2f5 103 5.0  6.0    6.0    0.833     1.000
  8aae58ad-993a-433e-9763-1d28189d1678 103 3.0  3.0    3.0    1.000     1.000
  9cf90efe-c566-405b-a416-5d8195c9aa17 103 2.0  3.0    3.0    0.667     1.000

Committed memory (MB), per node
-------------------------------
  node                                   n    p90   peak  ledger p90 frac peak frac
  13db082e-b18d-4c7a-b8e3-27af4ce5c2f5 114 7168.0 8192.0 35880.0    0.200     0.228
  8aae58ad-993a-433e-9763-1d28189d1678 114 9052.0 9052.0 35880.0    0.252     0.252
  9cf90efe-c566-405b-a416-5d8195c9aa17 114 8028.0 9052.0 35880.0    0.224     0.252

Nodes absent from per_node
--------------------------
  Every node in every roster appeared in that sample per_node.
  Nodes visible in per_node: 3 at fewest, 3 at most, across 114 samples.
  That is what the samples could see, which is not the same as
  how many hypervisors the cluster had.

Refusal census
--------------
  File:              bundle/traces/headroom-census.json
  Log records read:  1155 (1155 were schedule stage events, 0 were capacity guard events, 0 lines unparseable)
  stage                  events aborts dropped                                                            kind
  sufficient_idle_cpu       145      1      30                                                             cpu
  affinity_constraints        1      1       2                   not a stage this report knows; counted anyway
  cpu_max_per_instance      145      0       0                   not a stage this report knows; counted anyway
  is_hypervisor             145      0       0                   not a stage this report knows; counted anyway
  pre_schedule              145      0       0                   not a stage this report knows; counted anyway
  queue_state               143      0       0                   not a stage this report knows; counted anyway
  sufficient_free_disk      144      0       0                                                      disk space
  sufficient_idle_disk      143      0       0 disk BANDWIDTH -- a rate predicate, which sizing cannot address
  sufficient_idle_memory    144      0       0                                                          memory
  Tallied by the stage string observed in the events, never by a
  list held here (D10), so a stage added or renamed in the
  scheduler still appears above.

  Drop reasons, by stage:
    affinity_constraints:
          2  no co-located instance carries a required tag
    sufficient_idle_cpu:
         30  would exceed hard max CPUs

Capacity guard census
---------------------
  NO CAPACITY GUARD EVENTS IN THIS CENSUS.
  Read that as a fact about the query before reading it as a
  fact about the cluster: the census is collected with a LogQL
  filter, and if that filter selects only the scheduler stage
  messages then a guard which refused every candidate leaves
  nothing here to count. The filter must also match
  'instance placement denied'
  and 'placement admitted over namespace capacity claim'
  for this section to mean anything at all.
  This census DID carry 1155 schedule stage events, so the log
  shipping path was healthy and the filter is the difference.

D3 band verdict (PROVISIONAL bounds 0.35 / 0.70)
------------------------------------------------
  p90 committed vCPU / ledger, cluster wide: 0.750
  Verdict: OVERSUBSCRIBED -- above the provisional upper bound of 0.70
  These bounds are PROVISIONAL. Phase 0 set them without any
  distribution to check them against, and phase 2 replaces them or
  defends them. Nothing gates on this verdict: this phase computes
  and prints the band, and phase 5 owns turning it into a guardrail.

  Refusal warning: YES. 30 candidate drops at a capacity stage.
  Per D3 that is a warning in its own right, whatever the ratio
  says: a poll every fifteen seconds cannot see a refusal, which
  begins and ends between samples.
  2 further drops at stages this report does not classify (see the
  census table above). They are not counted in the warning
  either way, because nothing here knows whether they are
  capacity stages -- a scheduler stage added since this tool
  was written lands here.
  Guard refusals: NOT COLLECTED in this census (see the capacity
  guard section). Unknown, not zero.
```

**And the run did refuse.** The guard's events are in the bundle --
127 `instance placement denied` and one `placement admitted over
namespace capacity claim`, in the primary's
`_commands/journalctl-sf-units`, which is the same JSON promtail
ships to Loki. Rebuilding the `query_range` envelope from those
records (unmodified, beyond repairing the `[pid]:` journalctl renders
inside the first token) and re-running gives what a widened collector
filter would have produced:

```
Capacity guard census
---------------------
  Placements refused by the guard: 127

  Refusals by failing stage:
    stage refusals                   what it guards
    node       127 the node's own capacity counters

  Refusals by exceeded dimension. A refusal exceeding two
  dimensions is counted once under each, so the column sums to
  more than the refusal count; "alone" is the subset where that
  dimension was the only one exceeded.
    dimension refusals alone                                                               what it is
    demand         127   127 measured CPU load plus the D13 feedforward estimate -- NOT an allocation

  Of the 127 refusals exceeding the demand dimension:
      111  measured CPU load alone was already over the limit
       16  the D13 feedforward estimate is what carried it over
  Demand is not an allocation, so a refusal here is a rate
  prediction rather than a cloud which ran out of room, and the
  second line is an estimator finding rather than a sizing one.

  No refused dimension carried a shortfall field. That is a
  series written by a build predating it, not a shortfall of
  zero; the three numbers it is derived from are in the events.

  Claim exceedances (ADMITTED, never refused): 1
    These placements SUCCEEDED. CLAIM_ENFORCEMENT_HARD is False, so
    advisory mode admits over a claim on purpose and this is the
    system doing what the operator asked. It is the signal a
    declared footprint needs revising (D9), and it is never added
    to the refusal count above.
    namespace                admitted over claim
    ci-claimaccount-evzkzyho                   1
    Claim dimensions exceeded: cpus x1, disk_gb x1, memory_mb x1

D3 band verdict (PROVISIONAL bounds 0.35 / 0.70)
------------------------------------------------
  p90 committed vCPU / ledger, cluster wide: 0.750
  Verdict: OVERSUBSCRIBED -- above the provisional upper bound of 0.70
  These bounds are PROVISIONAL. Phase 0 set them without any
  distribution to check them against, and phase 2 replaces them or
  defends them. Nothing gates on this verdict: this phase computes
  and prints the band, and phase 5 owns turning it into a guardrail.

  Refusal warning: YES. 30 candidate drops at a capacity stage.
  Per D3 that is a warning in its own right, whatever the ratio
  says: a poll every fifteen seconds cannot see a refusal, which
  begins and ends between samples.
  2 further drops at stages this report does not classify (see the
  census table above). They are not counted in the warning
  either way, because nothing here knows whether they are
  capacity stages -- a scheduler stage added since this tool
  was written lands here.
  Guard refusals: YES. The ledger refused 127 placements, of which 127
  refused something a caller asked for. Whatever the ratio above
  says, a refused placement is a create which did not happen.
  127 of them were refused on the demand dimension ALONE, with
  every allocated dimension inside its limit. That is not a
  cloud which ran out of room; see the split above.
  1 placement was admitted OVER a namespace capacity claim. Advisory mode
  did what the operator asked; this is calibration data, not a
  failure, and it is no part of the refusal counts above.
```

**What that says.** Every one of the 127 refusals is at the `node`
stage on the `demand` dimension **alone**, with cpus, memory_mb and
disk_gb all inside their limits -- the #3813 shape, in a run whose
stage census reports one aborted stage and calls the guard layer
clean. That is the #3772 reading G6 exists to prevent, observed
rather than argued. The split says 111 of them had measured CPU load
already over the limit and 16 were carried over it by the D13
feedforward estimate; the split is computed the way the guard
compares, which since phase 4a does not charge the incoming
placement.

The claim exceedance is reported as its own thing and never added to
the refusal count: namespace `ci-claimaccount-evzkzyho`, all three
dimensions, **admitted**. Advisory mode did what the operator asked.

No dimension in this series carries a `shortfall`: the captured run
predates step 1. The tool says so rather than printing zero, since a
shortfall of zero would read as a refusal that was not actually over.

**Exit status on malformed input is zero**, checked against a
truncated series, a truncated census, two paths that do not exist,
5KB of `/dev/urandom` as both inputs, two empty files, and an
unrecognised argument. All six exit 0. `grep -n '^import \|^from '`
on the tool still returns argparse, collections, datetime, json, sys
and traceback and nothing else.

**Follow-up, deliberately not done here.** Widening the collector's
LogQL filter to match the two guard messages is a change to
`shakenfist/actions`, not to this repository, and until it lands the
new section will print "NOT COLLECTED" on every CI run. It is
recorded under future work rather than smuggled into this phase.

## What the measurement found

Step 7 tests G1. It is a measurement step: no event emission was
changed, and the diff for the step touches this file only.

**The fan-out is one row per event, not one per object.**
`add_event_multi()` builds a single spool payload whose `objects`
key is a list, and enqueues that one payload
(`shakenfist/eventlog.py:113-131`). The drainer ships it through
`mariadb.record_event_batch()`, which reaches
`_direct_record_event_batch()` (`shakenfist/mariadb.py:5533`)
either directly or via the database daemon's `RecordEventBatch`
servicer (`shakenfist/daemons/database/main.py:5394`), which only
rebuilds `EventRecord`s and calls that same function. Its loop is
explicit -- per record, one `INSERT ... IGNORE` into `events`,
then one `INSERT ... IGNORE` into `event_objects` for each entry
of `record.objects`:

```python
    for record in events:
        event_stmt = sa.insert(events_table).prefix_with('IGNORE').values(...)
        conn.execute(event_stmt)
        ...
        for object_type, object_uuid in record.objects:
            obj_stmt = sa.insert(event_objects_table).prefix_with('IGNORE').values(...)
            conn.execute(obj_stmt)
```

So N related objects cost one `events` row and N `event_objects`
rows. `message` lives on the `events` row, so the message-prefix
share below is not multiplied by object count; only the child
table is.

The premise the step was given about the forced path needs one
correction. `node_inst_netdesc_op.py:159` passes
`candidates=[config.NODE_UUID]` -- exactly one node, so two
related objects. The list of "every node except this one" is built
at `node_inst_netdesc_op.py:200`, the redirect after a failed
preflight, and that is the only path which can multiply the child
table by the cluster size. In the measured window it never fired:
all 669 `schedule forced candidates` events carried a candidate
list of length one, matching the single `schedule has no
candidates at stage` event in the same window. Today's fan-out on
sfcbr is therefore at most 2x on `event_objects`, and 1x on
`events`.

**The share is about five percent.** Measured over the 24 hours
and the 7 days ending 2026-09-03T10:04:10Z, on sfcbr.

| quantity | 24h | 7d |
|----------|-----|----|
| `events` rows written to MariaDB | 502,220 | 2,935,059 |
| ...of those, `event_type="audit"` | 263,790 | -- |
| scheduling events (`schedule ...` or `started scheduling`) | 24,339 | 114,732 |
| **share of all `events` rows** | **4.85%** | **3.91%** |
| share of `audit` rows | 9.23% | -- |
| schedules (`started scheduling`) | 1,552 | -- |
| scheduling events per schedule | 15.7 | -- |

Of the 15.7, 14.4 come from `find_candidates()` itself; the
remainder is `RecordedOperation`'s `schedule finished` and the
capacity guard's `schedule candidate refused by capacity guard`.
The 14.4 is the eight stage events plus `started scheduling`,
`schedule inputs`, `schedule initial candidates`, `schedule have
highest affinity`, `schedule have lowest cpu load` and `schedule
final candidates`, plus the 0.43 `schedule forced candidates` per
schedule -- so the fifteen-per-schedule figure in the Situation
section is confirmed rather than revised.

**Method.** Two sources, because no single one answers it. The
denominator is the authoritative MariaDB write count: Prometheus
on maui scrapes `database_events_inserted_total` from both
`sf-database` gateways, and that counter is incremented in
`_direct_record_event_batch()` only after the transaction commits.
The numerator is the Loki event echo on the `sfcbr` tenant,
`{job="shakenfist"}` narrowed to `add_event_multi` records whose
`message` field begins `schedule ` or equals `started scheduling`.
A direct MariaDB query was attempted first and was not available
from this host.

The echo is a sound numerator even though it undercounts the
table as a whole. It is gated by `LOG_EVENTS_TO_LOKI` (on for
sfcbr) and by per-call `suppress_event_logging`; no scheduler call
site suppresses, and `eventlog_spool_dropped_total` increased by
zero over the seven days, so every scheduling event was both
written and echoed. The echo does undercount the denominator --
363,893 echoed against 502,220 written in the same 24 hours --
which is why the denominator is taken from the counter instead.
That gap is the six `suppress_event_logging=True` call sites, and
it cross-checks: 248,260 echoed `audit` lines against 263,790
`audit` rows written is a 5.9% shortfall, accounted for by
`baseobject.py:430`'s suppressed `object created`. Treating that
5.9% as an upper bound on echo loss for scheduling events too
would move the share to 5.1%, not to a different order of
magnitude.

sfcbr is the CI cluster, so this is a create-heavy workload and
close to a worst case for the scheduling share. For scale, its
`events` table held 13.2 million rows at 5.8 inserted per second.

**Verdict on G1.** The scheduling trail is 4.9% of rows written on
a create-heavy cluster -- an order of magnitude below the
two-thirds share that made the namespace-key storm a real problem
-- so the measurement supports G1's decision to leave these events
unconditional.

One latent risk is worth carrying rather than acting on here: the
`node_inst_netdesc_op.py:200` redirect is the one path whose
`event_objects` cost scales with cluster size, and it is rare
today only because preflight almost always succeeds. If a future
change makes redirects common on a large cluster, the child-table
cost is worth re-measuring. That is a note, not a change.

## Close-out (2026-09-03)

**Step 1** added `shortfall` to `CapacityDimensionDetail` and
`CapacityDimensionDetailDict`, computed once in the shared
`_capacity_dimension()` builder as `max(0.0, effective_used -
limit)`, so every dimension in `dimensions` and
`claim_dimensions` reports it and no two consumers can disagree
about the sign convention.

**Step 2** widened `ReleaseInstancePlacementReply` with the
post-release node counters and evented them, alongside the
released amounts already held caller-side, on `instance
placement released`. The release half of the ledger now carries
the same counter vocabulary the drawdown half's `instance
placed` always has.

**Step 3** added the two REST events endpoints:
`/auth/namespaces/<namespace>/events` and
`/auth/namespaces/<namespace>/claims/<claim_ref>/events`, both
admin-gated and mirroring the five existing per-object events
endpoints exactly.

**Step 4**, plus a follow-up pass, recorded the claim-exceedance
facts against the namespace as well as the instance (G2), and
the follow-up added `claim_uuid` to `AdmitInstancePlacementReply`
so both copies of the event can name which claim was actually
drawn down -- the third proto change G7 undercounted, corrected
above.

**Step 5** added `SchedulerNodeCapacityRead`, a `NamedTuple`
carrying a `degraded` flag alongside the rows, threaded from
`get_scheduler_node_capacity()` through `_capacity_by_node()` to
`find_candidates()`, which fires `schedule could not read the
capacity counters` on the degraded path only.

**Step 6** added a guard-refusal census to
`tools/ci_headroom_report.py` and ran it against a real cluster
CI job (see "What the tool reported" above).

**Step 7** measured the scheduling event trail's share of
`events` rows written on sfcbr (see "What the measurement found"
above): 4.85% over 24 hours and 3.91% over 7 days -- an order of
magnitude below the roughly two-thirds share that made the
namespace-key event storm a real problem. **This supports G1's
decision** to leave the trail unconditional rather than gating
it behind a diagnostic mode.

**A follow-up pass**, closing a gap G4's own symmetry check
found, added `disk_gb` to `instance placed`: the release event
already reported it and the placement event did not, so the
placement event gained the field rather than the release event
dropping a third of the allocation it reports.

### The most important finding: the CI collector cannot see any of this yet

`tools/ci_headroom_collect.sh`, in the separate
`shakenfist/actions` repository, filters Loki with
`|~ "schedule (at stage|has no candidates at stage)"`. That
selects the scheduler stage messages and nothing recorded below
them, so **no census CI collects today can contain a capacity
guard refusal, however many occurred.** Step 6's own captured
series proved this directly: its shipped census carried 1155
schedule-stage records and zero guard records, from a run that
had in fact refused 127 placements. Demonstrating the new code
against real data required reconstructing the guard's events
from the same bundle's raw journals rather than reading them
from the collected census. Widening the collector's filter is a
change to `shakenfist/actions`, out of scope for this plan, and
is recorded under Future work below -- no issue has been filed
for it yet.

The reconstructed run is itself worth keeping as a finding: all
127 `instance placement denied` events were at the `node` stage,
all 127 on the `demand` dimension **alone**, with cpus,
memory_mb and disk_gb inside their limits on every one -- the
#3813 shape -- in a run whose stage census reported one aborted
stage and read as a clean run. This is exactly the #3772 reading
G6 exists to catch, observed rather than argued. It is directly
relevant to issue #3772 -- the run's own three-node `slim-tier`
topology is the territory `PLAN-ci-cloud-sizing` covers -- but it
does not close that issue: sizing the CI clouds is that plan's
job, not this one's.

### A test had to be relaxed, and could not be verified by CI failing

Step 2 relaxed
`test_the_reference_lookup_runs_outside_the_release_transaction`
in `shakenfist/tests/test_mariadb_capacity_admission.py`. Its
assertion was a blanket "no `SELECT` executes inside the
transaction," which the new post-release counter read violates
by design: that read is a read-after-our-own-write against a row
the release transaction's own guarded `UPDATE` already locked,
the same pattern the admission transaction already relied on.
The test now asserts the narrower and correct invariant its
admission twin already used -- no read establishes a read view
before the transaction's first write
(`_assert_no_read_before_the_first_write`) -- which the counter
read satisfies and the old blanket assertion never needed to
express. CI's MariaDB is 10.11, which predates
`innodb_snapshot_isolation` being on by default and so cannot
produce the ER_CHECKREAD failure the guarded-update-first
invariant exists to avoid; neither the old assertion nor the new
one would have failed under CI's MariaDB regardless of which one
was correct. This was verified by reading the statement order
the code issues, not by a test turning red and then green.

### Correction: the preflight redirect's candidate count

The step 7 brief's premise about the preflight redirect needed
one correction, already made in "What the measurement found"
above rather than in the frozen Execution table brief itself, per
this plan family's convention of correcting forward rather than
rewriting history: `node_inst_netdesc_op.py:159` passes exactly
one candidate node (`candidates=[config.NODE_UUID]`), not every
node. The list of every other node in the cluster is built at
line 200, the redirect that fires after a failed preflight --
the only path whose `event_objects` cost can scale with cluster
size, and it did not fire in the measured window.

### Definition of done

Every item is ticked and verified against the tree rather than
assumed: `pre-commit run --all-files`, including
`check-plan-status.py` and `check-doc-anchors.py`, was run and
passed clean as part of writing this close-out.

## Review follow-up (2026-09-04)

The automated reviewer raised eight items on PR #4052, four of
them defects. All eight are addressed here; each was checked
against the tree before being acted on, and the four defects were
real.

**Two "not reported" sentinels were missing, and one of them
contradicted a field this phase added.** `shortfall` went onto
the wire as a plain `double`, so an sf-database predating it
returns proto3's zero and every exceeded dimension unpacks as
`shortfall: 0.0` -- a value that field cannot honestly take,
since zero means the dimension was not over. That is exactly the
reading `ci_headroom_report.py` prints "this series predates the
field" to avoid, and it could never fire, because the key was
always present. It is now `optional double`, set only under
`HasField()`, so `CapacityDimensionDetailDict.shortfall` is
`NotRequired` and absence means absence. G7's count of proto
changes rises from three to four.

**The degraded read did not cross the gRPC boundary.** Step 5
added `degraded` to the accessor and the servicer's docstring
recorded, honestly, that the reply had no field to carry it --
so a MariaDB-side failure inside the database tier reached every
other daemon as an empty table, which is a *normal* state (P7)
and reads as one. Since every daemon except `sf-database` gets
here over gRPC, that is the common case rather than the corner
one, and `docs/operator_guide/scheduler.md` overstated what an
operator could see. Rather than narrow the documentation to the
weaker claim, `GetSchedulerNodeCapacityReply` gained `bool
degraded = 2`, additive like the other three: the servicer
forwards the direct read's flag, an exception escaping that read
also reports degraded, and the client carries the reply's flag
instead of assuming `False`. The operator guide now names both
places the read can fail, which is what it should have said.

**The mock built dimension dicts by hand.** Almost all unit
coverage runs through `MockMariaDB`, which constructed its
`dimensions` and `claim_dimensions` entries inline and so never
carried a `shortfall` -- leaving G3's done-criterion unverified
everywhere the guard is actually exercised, and pinning an
exact-dict assertion in `test_instance.py` to a shape production
cannot emit. The mock now calls `mariadb._capacity_dimension()`
(with `charged=False` on demand, as the real clause is), which
also stops the two drifting on `exceeded`. The same hand-built
shape was found twice more, in `_grpc_create_namespace_claim()`
and `_grpc_update_namespace_claim()`; both now use
`_dimension_from_proto()`, so there is exactly one conversion in
each direction.

**The census merged two different shortfalls.** `_note_shortfall()`
was called from both `observe_denial()` and `observe_claim()` into
one dict, printed under the refusal heading -- so a claim
exceedance's shortfall appeared as a refusal on a dimension
nothing refused. That is the precise conflation `GuardCensus`'s
docstring says it exists to prevent. Refusal and claim shortfalls
are now separate dicts printed under their own headings.

The four lesser items are done too: the module docstring's
account of the accessor contract (which this phase falsified),
`no_dimensions` (now `empty_dimensions`, counting only the
readable-but-empty case, and printed rather than dead), two
number-agreement slips in the output, and a duplicated stage-label
expression.

Five tests were added or tightened: shortfall absence across the
wire, the degraded flag across the wire in both directions, the
two shortfall tables staying apart, an empty dimensions list not
being read as malformed, and a release spanning two nodes
reporting no counters. The two exact-dict claim assertions in
`test_instance.py` now include the shortfall the shared builder
produces.

One review suggestion is deliberately **not** taken here:
functional (`shakenfist_ci`) coverage of the two new events
endpoints. `test_namespace_claims.py` routes every request
through a client verb on purpose, and there is no verb for either
endpoint yet -- adding them is a `client-python` change of the
kind phase 4b did, not a review fix to this branch. Recorded
below.

## Future work

Recorded here rather than absorbed, per the survey:

- **Client verbs for the two events endpoints, and the
  functional tests which follow them.** The endpoints are covered
  by unit tests which mock `get_object_events`, so nothing
  exercises the real read against a namespace's or a claim's
  stored events. Cluster CI builds the client from a
  `client-python` checkout at `develop`, so a verb is usable here
  as soon as it merges; the work is a client change plus a test in
  `test_events.py`, not a change to this branch.

- **A cross-object event query.** Seven per-object endpoints is
  six more than a triage tool wants. `events-by-type` is a
  parameter, not a search. Naming it here because this phase adds
  the sixth and seventh and makes the shape of the problem more
  obvious, not less.
- **Whether a namespace owner may read their own namespace's
  events.** Both new endpoints are admin-gated (G2). The
  narrower gate is the safe default and the wider one is a real
  request an operator will eventually make.
- **The census collector's LogQL filter, in
  `shakenfist/actions`.** `tools/ci_headroom_collect.sh` queries
  `|~ "schedule (at stage|has no candidates at stage)"`, which
  stops above the guard, so the new guard census prints "NOT
  COLLECTED" on every CI run until that filter also matches
  `instance placement denied` and `placement admitted over
  namespace capacity claim`. Step 6 proved the parser against the
  run's own shipped records; making CI collect them is a change to
  another repository and wants its own issue -- none has been
  filed yet. Until it lands, the census section is honest about
  being empty rather than reporting zero refusals -- which is the
  behaviour that matters, but it is not the same as the data being
  there.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.

Two gates in particular, both cheap to raise now and expensive to
redo:

1. **G1 and the phase rename.** If the operator wants a
   diagnostic mode after all, that inverts the shape of steps 5
   and 7 and changes what the phase is. Raise it before step 1.
2. **G2's destination for the exceedance event.** Recording
   against the namespace rather than the claim is a deliberate
   deviation from D9's literal words, and it is the decision
   phase 4c's observation record will actually consume. If it is
   wrong, it is wrong before step 3 writes an endpoint shaped
   around it.
