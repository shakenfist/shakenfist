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

**G7: no schema migration; two proto changes, both
additive reply fields.** Nothing here adds a table, an index or a
migration -- stated because every other phase in this plan did
add schema, and a reviewer should be able to confirm the absence
quickly rather than search for it.

There are two proto changes, not one. `shortfall` joins
`CapacityDimensionDetail` (step 1), and the
`ReleaseInstancePlacement` reply gains the post-release counters
(step 2). The second was found late, while checking this plan's
own step 2 brief against the tree: that brief originally said to
take the counters "from the reply already bound as `result`", and
`ReleasePlacementResult` (`mariadb.py:25416-25422`) carries
`success`, `error`, `released` and `clamped` and no numbers at
all. G4's symmetry cannot be had caller-side -- the released
*amounts* can (`instance.py:1500` holds them), the resulting
*counters* cannot.

Both are additive fields on reply messages, so an older client
reading a newer server sees proto3 defaults rather than an error,
and neither needs `tox -e genprotos` output hand-edited. Widening
a reply is a bigger change than reporting a computed value, which
is why step 2 is opus at high effort and not sonnet.

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

- [ ] `grep -c "'/.*events'" shakenfist/external_api/app.py`
      returns 7, having returned 5 today. Both new routes appear
      in the published OpenAPI specification, which is checked by
      `test_openapi_spec.py` rather than by reading the file.
- [ ] A `GET` of `/auth/namespaces/<ns>/claims/<uuid>/events`
      returns the claim's events, and a `GET` of
      `/auth/namespaces/<ns>/events` returns the namespace's --
      including the `namespace claim deleted, capacity returned`
      event that `namespace_claim.py:415-420` has been writing to
      an unreadable destination since phase 4.
- [ ] A placement which draws a namespace past its claim produces
      **two** events, one on the instance and one on the
      namespace, carrying the same dimensions and correlatable by
      claim uuid. Checked by reading both objects' events after
      one create, not by reading the code.
- [ ] Every refused dimension in a `dimensions` or
      `claim_dimensions` list carries a `shortfall` equal to
      `used + requested - limit`, and never a negative one.
      Asserted by a unit test which builds a refusal with
      `used + requested < limit` on one dimension and an
      exceedance on another, and checks both.
- [ ] `instance placement released` and `instance placed` carry
      the same counter vocabulary. Falsifiable by diffing the two
      `extra` key sets in a test: the release event's keys are a
      subset of the placement event's, and today the difference
      is every key but `node`.
- [ ] An unreadable capacity table produces an audit event on the
      scheduling instance which names the degradation, and an
      empty-but-readable table produces **no** such event. Both
      halves asserted -- the second is the one that catches an
      implementation which events unconditionally on an empty
      list, which would fire on every create on a cluster the
      reconciler has not reached yet (P7 says that is a normal
      state, not an error).
- [ ] `ci_headroom_report.py` reports a non-zero guard-refusal
      count for a captured series in which the guard refused, and
      its output is pasted into this plan. Not "the census was
      added" -- run against real data, because the file's own
      history is that its stage census was written against
      assumptions and corrected after a run.
- [ ] `ci_headroom_report.py` still imports nothing outside the
      standard library and still exits zero on malformed input.
      Checked by `grep -n '^import \|^from '` on the file --
      trailing spaces deliberate, since a bare `^import` also
      matches the word "imported." in the module docstring --
      and by running it against a truncated series. Today that
      returns argparse, collections, datetime, json, sys and
      traceback, and nothing else.
- [ ] The share of `events` rows whose message begins
      `schedule ` is recorded as a number in this plan, with the
      window it was measured over, or the plan says the
      measurement could not be run and why.
- [ ] **No guard outcome changed.** The diff touches no
      comparison inside `_direct_admit_instance_placement()`, no
      `_has_sufficient_*()` return value, and no
      `CLAIM_ENFORCEMENT_HARD`. Verified by reading the diff of
      `mariadb.py` and `scheduler.py` in the management session
      and stating so in the close-out.
- [ ] The master plan's phase 7 stub no longer describes the
      three D9 deliverables as unbuilt, and its Execution-table
      name is no longer "Diagnostic-mode rejection logging".
      Both corrected in the planning commit, so this item is
      already true when the phase starts and is listed to stop a
      later step redoing it.
- [ ] `pre-commit run --all-files` passes, including
      `check-plan-status.py` and `check-doc-anchors.py`.

## Future work

Recorded here rather than absorbed, per the survey:

- **A cross-object event query.** Seven per-object endpoints is
  six more than a triage tool wants. `events-by-type` is a
  parameter, not a search. Naming it here because this phase adds
  the sixth and seventh and makes the shape of the problem more
  obvious, not less.
- **Whether a namespace owner may read their own namespace's
  events.** Both new endpoints are admin-gated (G2). The
  narrower gate is the safe default and the wider one is a real
  request an operator will eventually make.
- **`docs/plans/index.md`'s phase arithmetic.** The plan's row
  reads "9 of 14" and this phase does not change it; planning is
  not completing. Noted so the close-out remembers to move it to
  10 and nothing else does it early.

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
