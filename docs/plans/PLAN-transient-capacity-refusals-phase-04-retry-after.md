# Phase 4: `Retry-After` and a machine-readable transient refusal

Parent plan:
[PLAN-transient-capacity-refusals.md](PLAN-transient-capacity-refusals.md).

**Planning effort:** medium, as the master plan specifies -- "each
half is small, and the coordination is a version pin between the two
repositories". The halves are indeed small. What the survey found is
that three of the section's load-bearing sentences are no longer true,
and one of them is a reversal rather than a correction: the master
plan says the CI suite turns the new client retry on, and it must not.

This phase continues the plan's decision sequence at **D28**; phases 1
to 3 used D1-D27.

## Context

The master plan's open question 9 asks what the API should say about a
capacity refusal, and answers it: that it is transient, and when to
try again. Today both scheduling `507`s from `POST /instances` are
bare prose. A caller that wants to tell a refusal it should wait out
from one it should not has to pattern-match an English sentence, and
every consumer that does so is one wording change away from breaking.

Phase 3 made the hint honest. Before it, a node kept refusing for up
to a metrics period after its instances were destroyed, so any
`Retry-After` short enough to be useful would have been a lie. Its
closeout measured the drop at 5.3-5.6 s across three topologies, so a
15 s hint now has real margin.

## Scope

In scope:

- A `Retry-After` header and two machine-readable body fields
  (`stage`, `transient`) on the two *scheduling* `507`s from
  `POST /instances`.
- Carrying the scheduler stage on the exception rather than in prose,
  so the handler reads a field.
- Publishing the new contract in the OpenAPI declaration, within what
  the declaration format can express.
- `client-python`: response headers on `APIException`, and an opt-in,
  off-by-default retry for a `507` marked `transient`.
- Teaching `BaseTestCase.assertRefusedAtStage()` the new contract --
  it already names this phase as the single place to change.

Out of scope, deliberately:

- **Turning the client retry on in the CI suite.** The master plan
  says this phase does; D33 reverses it, with the evidence.
- The two `CongestedNetwork` `507`s from address allocation
  (`external_api/instance.py:428`, `:451`). See D29.
- The `409` affinity refusal, which must never be retried, and the
  `404` from `CandidateNodeNotFoundException`.
- Any change to what the scheduler admits, to the capacity ledger, or
  to the refusal *rate*. This phase changes what a refusal says, not
  when one happens.
- A server-side placement queue. That is phase 5's decision.
- Extending the OpenAPI response-declaration format to express
  headers. See D32; an issue is filed instead.

## What the survey found

Five things, four of which change what this phase does.

**1. Every line number in the master plan's phase 4 section is
stale, and one of the claims behind them is wrong.** The section
names "both `507` branches ... (`external_api/instance.py:906`,
`:976-981`)". The two scheduling branches are now at
`external_api/instance.py:944` (`LowResourceException`, the filter
pre-check) and `:1017` (every candidate refused by the capacity
guard). More importantly, "both" is not "all": `POST /instances` has
**four** `507` branches, and the other two are the `CongestedNetwork`
clauses in `_netdesc_allocate_address()` at `:428` and `:451`. The
section does not mention them, so a reader would not know a decision
about them was required. D29 makes it.

**2. `sf_api.error()` is not ours.** The section says "`sf_api.error()`
returns a bare `flask.Response`, so the header is set on the returned
object." The first half is true --
`/home/mikal/.local/lib/python3.13/site-packages/shakenfist_utilities/api.py`
builds `flask.Response(json.dumps({'error': ..., 'status': ...}))`
and returns it -- but `sf_api` is `shakenfist_utilities.api`, pinned
in `pyproject.toml:38` at `shakenfist-utilities==0.8.8`. So the
*header* can indeed be set on the returned object, but the *body* is
constructed in a third repository, and 305 call sites in this one
depend on its shape. The section reads as though extending the body
were a local edit. It is not, unless we choose to mutate the returned
response here. D28 chooses that.

**3. The stage names already exist, in two different vocabularies.**
The filter path raises from `Scheduler._log_and_raise_on_error()`
(`shakenfist/scheduler.py:525-558`) with
`message = f'No nodes remaining at scheduling stage {stage}'`, where
`stage` is a filter name such as `sufficient_idle_cpu`,
`sufficient_idle_memory` or `sufficient_free_disk`. The guard path
has no stage in its message at all -- `:1017` says "no node had
capacity for this instance, %d candidates refused it" -- but every
denial it collected carries `CapacityAdmissionDenied.failing_stage`,
which is one of `cluster`, `claim` or `node`
(`shakenfist/mariadb.py:25796-25808`, `shakenfist/exceptions.py:124-148`).
So the server does not need to invent a stage for either branch; it
needs to stop throwing the one it has away. D30.

**4. The contract already has an owner, and it names this phase.**
`PLAN-ci-cloud-sizing` phase 3a landed
`BaseTestCase.assertRefusedAtStage()`
(`shakenfist/deploy/shakenfist_ci/base.py:1464-1533`), used by four
assertions in `cluster_ci_tests/test_saturation.py`
(`:240`, `:302`, `:371`, `:1177`). Its docstring
says, in as many words, "``PLAN-transient-capacity-refusals.md``'s
phase 4 adds a ``Retry-After`` header and a machine-readable body to
this contract. When it does, this is the single helper to change --
not every test that calls it." The master plan's phase 4 section does
not mention it. Note also that the helper asserts *only* the filter
branch's message; the guard branch at `:1017` does not match its
regexp and no test asserts that branch's contract at all.

**5. The suite must not turn the client retry on.** The master plan
says "Off by default; the suite turns it on, and its phase 2 wrapper
then becomes the *informed* layer over the client's blind one." Three
facts in the tree say otherwise:

- Suite clients are built with `async_strategy=ASYNC_PAUSE`
  (`base.py:175`, `:199-202`), and `_calculate_async_deadline()`
  returns 60 for it (`apiclient.py:180-188`). A blind retry bounded by
  that deadline would spend up to 60 s *inside* each call the phase 2
  wrapper makes.
- The phase 2 wrapper's whole value is that its waits are recorded:
  `waits`, `attempts`, and the per-wait records at
  `base.py:327-380`. Time spent in a blind inner loop is invisible to
  all three, and phase 5 reads exactly those records as its
  denominator. A blind retry would corrupt the number this plan
  exists to produce.
- The wrapper retries under a *fresh* instance name
  (`base.py:334-342`) precisely because the refused instance is
  `enqueue_delete_due_error`'d and its name stays in use. A blind
  client retry replays the identical body.

And decisively: `assertRefusedAtStage()`'s docstring
(`base.py:1495-1503`) forbids routing a refusal assertion through any
retry, because retrying the refusal under test away is backwards from
what a saturation test proves. Four assertions depend on that. If the
retry were on for the suite's clients, those tests would be retrying
their own subject matter. D33.

**One thing the survey checked and found fine:** `client-python`'s
`create_instance()` already computes a deadline and passes it into
`_request_url()` (`apiclient.py:674-681`), so a `507` retry added to
that loop shares a budget with the existing `406` loop for free, with
no new plumbing.

**Corrections made in the planning commit.** The master plan's phase 4
section has been rewritten to carry the current line numbers, to name
all four `507` branches, to say that `sf_api.error()` lives in
`shakenfist-utilities`, to name `assertRefusedAtStage()` as the
contract's owner, and to drop the sentence saying the suite turns the
client retry on. The `docs/plans/index.md` row is updated to match. A
later step should not redo this.

## Decisions

### D28 -- Mutate the response here; do not change `shakenfist-utilities`

Add `TRANSIENT_RETRY_AFTER_SECONDS = 15` and a helper to
`shakenfist/external_api/base.py`:

```python
def capacity_error(message: str, stage: str) -> flask.Response:
    """A 507 which says whether it is worth trying again, and when."""
```

It calls `sf_api.error(507, message, suppress_traceback=True)`, then
rewrites the returned `flask.Response`'s body to `{'error': ...,
'status': 507, 'stage': stage, 'transient': ...}`, and sets
`Retry-After` when the refusal is transient. (The helper was named
`transient_capacity_error()` as first written and as implemented in
step 4a; D35 renamed it when it stopped always producing a transient
refusal.)

The alternative is to add the fields in `shakenfist-utilities` and
lift the pin at `pyproject.toml:38`. Rejected: that makes a
three-repository release train out of a phase whose only intended
cross-repo coupling is the client, and it would offer every
`sf_api.error()` caller in every downstream a field that only means
something for capacity. Doing it locally keeps the whole server-side
change in two files and leaves the other 305 call sites untouched.

The cost is that the body is assembled in two places, so the helper
must re-serialise rather than patch, and a `shakenfist-utilities`
upgrade that changes the body shape would silently drop our fields. A
unit test asserts the *whole* decoded body, so such an upgrade fails
the test rather than shipping.

### D29 -- Only the two scheduling `507`s are marked transient

**Superseded in part by [D35](#d35----the-stage-not-the-exception-class-decides-what-is-transient).** The
boundary this decision draws -- scheduling refusals in, everything else
out -- still holds. What it got wrong is that it treated the whole
filter branch as one fact; D35 splits it by stage.

The two `CongestedNetwork` refusals at `external_api/instance.py:428`
and `:451` stay bare. They are a different fact: the network's address
pool is exhausted, and nothing on the ten-second horizon a
`Retry-After: 15` implies will change that. An instance teardown
returns CPU within seconds (phase 3 measured it); it returns an IP
only after the deletion halo expires, which is a much longer and
differently-shaped wait. Marking them transient with the same constant
would be the kind of number that implies knowledge the server lacks,
which open question 9 explicitly rejects.

They are left as a Future work item rather than silently ignored: an
IP-exhaustion refusal probably *does* deserve its own marker and its
own horizon, derived from the halo, and that is a piece of work with
its own evidence requirement.

The `409` from `AffinityConstraintUnsatisfiable` is untouched and must
stay untouched; the except-clause ordering comment at
`external_api/instance.py:927-930` explains why it is fragile. The
`404` from `CandidateNodeNotFoundException` is not a capacity fact at
all.

### D30 -- Carry the stage on the exception, do not parse the message

`Scheduler._log_and_raise_on_error()` builds both the stage name and
the message (`scheduler.py:525-558`). Give `SchedulerException` a
`stage` attribute set at that raise site, and have the handler read
`e.stage`. The handler must not re-derive the stage by parsing
`str(e)`: that would make the prose load-bearing at exactly the moment
we are adding a structured field so it stops being.

`node_inst_netdesc_op.py:281` also raises a `LowResourceException`, on
the preflight path rather than the API path. It gets a stage too, so
the attribute is never absent; the handler treats a missing or empty
stage as `unknown` rather than raising, because a `507` that fails to
be produced is strictly worse than one with a vague stage.

For the guard branch at `:1017` there is no single stage --
`denials` may hold `cluster`, `claim` and `node` across different
candidates. Publish the constant `capacity_guard` as `stage` there.

The alternative, publishing the distinct set of `failing_stage` values
as a `stages` array, was rejected: the per-candidate detail is already
in the `schedule failed, every candidate refused by capacity guard`
audit event with `denials` in its extra, a caller deciding whether to
retry does not need it, and an array-valued field in an error body is
a much larger thing to commit to publishing than a string. A caller
who wants it can read the events.

### D31 -- `Retry-After: 15`, fixed, and not computed

As open question 9 decided, and now with a measurement behind it. The
server has no pending-release horizon: it does not know which
instances are being deleted, and a computed number would imply it
does. 15 s matches `BaseOperation.defer()`'s default delay
(`shakenfist/operations/baseoperation.py:674-678`), so the number a
client is told to wait and the number the server itself waits before
re-examining deferred work are the same number and are defined once.

Phase 3's closeout measured a node's `cpu_measured` falling 5.3-5.6 s
after a domain is destroyed, so 15 s clears the metrics path with
roughly three times margin. The constant carries a comment saying that
is where it came from, so a later reader can tell a chosen number from
an arbitrary one.

### D32 -- Publish the contract as far as the declaration format allows, and file the gap

`swagger_helper()` renders response declarations as three-tuples,
`(httpcode, description, sample)`, at
`shakenfist/external_api/base.py:784-791`. There is no way to express
a response *header* and no way to attach a body *schema*; `sample`
becomes `examples['application/json']` and that is the whole of it.

This phase therefore:

- replaces the `507` declaration's description at
  `external_api/instance.py:574` with one that names the
  `Retry-After` header and the two new body fields in prose, and
- supplies a `sample` showing a real body, so the shape is published
  machine-readably even though the header is not.

It does **not** extend the tuple format. That format is validated at
import time and a malformed declaration stops `sf-api` from starting
(`CLAUDE.md`, "Parameter declarations are enforced"); changing its
arity touches every endpoint in the API and is a plan of its own, most
naturally a phase of `PLAN-api-input-validation`. An issue is filed
against that plan instead.

This is the decision a reviewer is most likely to argue with, because
it ships a published contract that is deliberately incomplete: the
header a client is meant to read is described in English, not in the
specification. The counter-argument is that the alternative is an
import-time-fatal change to every endpoint's declaration, made as a
side quest inside a phase about capacity refusals, and that is a worse
trade than one prose sentence and a filed issue.

Note for the implementer: `PLAN-api-input-validation`'s D4
(`PLAN-api-input-validation-phase-00-decisions.md:221-233`) decided to
keep the `{'error': ..., 'status': ...}` shape and add "no structured
field-keyed body". That decision is about *validation* failures and
about keying a body by request field; it does not forbid a status-
specific field on a `507`. Say so in the phase plan's own commit
message, and cross-reference it in the helper's docstring, so the two
decisions are not read as contradicting each other.

### D33 -- The client retry ships off, and the CI suite leaves it off

Reversing the master plan's sentence, on the evidence in survey
finding 5. The flag exists, it is per-`Client` and defaults to off, it
is documented, and the CI suite does not set it. The phase 2 wrapper
remains the suite's only capacity wait.

The retry is still worth building. It is what makes the behaviour
available to operators, to `sf-ctl`, to the Ansible collection's
modules and to downstream repositories' suites, none of which have the
phase 2 wrapper. It is simply not for the suite that already has a
better one.

### D34 -- Server first, client second, and the suite helper degrades across client versions

Two PRs in two repositories, server first. The suite reaches the
client from PyPI by default
(`shakenfist/deploy/collection/roles/node/defaults/main.yml:81`,
`client_package: "shakenfist-client"`), so a suite assertion on
response headers is only satisfiable once a client carrying them has
been released.

So `assertRefusedAtStage()` asserts the **body** fields
unconditionally -- they arrive in `APIException.text`, which every
released client already carries -- and asserts the **header** only
when the exception exposes one, via
`getattr(response, 'headers', None)`. The conditional carries a
comment naming the client version that makes it unnecessary, and
removing it is a task on the phase's own Future work, not a silent
loose end.

A reviewer may reasonably dislike a conditional assertion. The
alternative is either a suite that cannot assert the header for a
release cycle, or a client wheel built from source in CI purely to
satisfy one assertion. The conditional is the smallest of the three.

### D35 -- The stage, not the exception class, decides what is transient

Added in the phase's first review round, which found that D29 drew the
line one level too coarsely.

D29 asked which of `POST /instances`' refusals get the marker and
answered "the two scheduling ones". That is right about the boundary
between scheduling and everything else, and wrong *inside* the filter
branch. `LowResourceException` is raised by
`Scheduler._log_and_raise_on_error()` for every stage that empties the
candidate set, and those stages are not one kind of fact:

- `sufficient_idle_cpu`, `sufficient_idle_memory`,
  `sufficient_free_disk`, `sufficient_idle_disk` and `queue_state` are
  momentary shortages of a measured, shared resource. Phase 3 measured
  CPU returning 5.3-5.6 s after a teardown. These are transient.
- `cpu_max_per_instance` fires when the request asks for more vCPUs
  than any node's per-instance maximum. `is_hypervisor` and
  `pre_schedule` fire when the candidate set was empty before any
  resource was measured. Deleting every instance in the cluster would
  not satisfy any of the three. A client that retried one would replay
  a structurally impossible request until its deadline for nothing,
  and the operator guide, written from D29, told a human the same
  untrue thing.

So the discriminator becomes the stage name, in two frozensets in
`external_api/base.py`. The helper is renamed `capacity_error()` --
`transient_capacity_error()` would be a lie for half its callers -- and
decides transience itself from the stage rather than taking a
`transient=` argument, because an argument is a classification that
can be got wrong once per call site.

A non-transient stage still returns a `507` and still publishes its
`stage`; it publishes `transient: false` and no `Retry-After`. The
alternative, dropping those refusals back to a bare
`sf_api.error(507, ...)`, was rejected: the stage is the most useful
thing in the body and is exactly what tells an operator that the fix
is a smaller instance rather than a wait. An explicit `transient:
false` is also a fact a client can act on, where an absent field is
ambiguous between "not retryable" and "a server that predates this
contract" -- both of which a client must treat as non-retryable, but
only one of which is worth reporting.

An unrecognised stage, including the `'unknown'` fallback D30
introduced for a refusal that arrives carrying no stage, is
non-transient. Being wrong in that direction costs one refusal a
client could have retried; being wrong in the other costs a client its
whole deadline.

Because the two sets are hand-written and the stage names live in
`scheduler.py`, `test_capacity_error.py` parses `scheduler.py` and
fails if any stage reaching this helper is in neither set -- a stage
added or renamed later cannot fall silently into the non-transient
default. The check was mutation-tested by adding a stage.

`capacity_guard` stays transient, with a caveat recorded at the set:
it is only honest while `mariadb.CLAIM_ENFORCEMENT_HARD` is `False`.
Today a namespace claim cannot refuse a placement, so every guard
denial is cluster or node capacity. When phase 5 of
PLAN-scheduler-reservations flips that constant, a claim denial
becomes namespace quota exhaustion, which no wait clears, and claim
denials must be split out of the transient set at that point.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | The server-side helper, per D28 and D31. In `shakenfist/external_api/base.py`, add `TRANSIENT_RETRY_AFTER_SECONDS = 15` with a comment citing `BaseClusterOperation.defer()`'s 15.0 default (`shakenfist/operations/baseoperation.py:674-678`) and phase 3's measured 5.3-5.6 s drop, and a `transient_capacity_error(message, stage)` helper. It calls `sf_api.error(507, message, suppress_traceback=True)` -- note `sf_api` is `shakenfist_utilities.api`, a third-party package pinned at `pyproject.toml:38`, so do not try to edit it -- then sets `Retry-After` on the returned `flask.Response` and replaces its data with `json.dumps({'error': message, 'status': 507, 'stage': stage, 'transient': True})`. Docstring records D28's reasoning and cross-references `PLAN-api-input-validation`'s D4 per D32. Unit test asserts the *whole* decoded body and the header, so a `shakenfist-utilities` upgrade that changes the body shape fails here. No callers wired yet. Commit subject: `Add a 507 which says it is transient.` (D35 later renamed this helper `capacity_error()` and made `transient` depend on the stage.) |
| 4b | medium | opus | none | The stage, per D30. Give `SchedulerException` in `shakenfist/exceptions.py` a `stage` attribute (default `''`), set it at the two raise sites: `Scheduler._log_and_raise_on_error()` (`shakenfist/scheduler.py:525-558`, where `stage` is already a local) and `shakenfist/operations/node_inst_netdesc_op.py:281`. Do not change any message text -- `assertRefusedAtStage()` and its four callers in `cluster_ci_tests/test_saturation.py` depend on the current wording, and this phase changes the body, not the prose. `AffinityConstraintUnsatisfiable` is a *subclass* of `LowResourceException` (`exceptions.py:101`) and inherits the attribute; that is fine and must not change the ordering of the except clauses at `external_api/instance.py:927-937`. Unit tests assert the attribute survives the raise for each filter stage name. Commit subject: `Carry the scheduler stage on the exception.` |
| 4c | medium | sonnet | none | Wire both scheduling branches, per D29. In `shakenfist/external_api/instance.py`, replace `sf_api.error(507, str(e), suppress_traceback=True)` at `:944` with the 4a helper passing `getattr(e, 'stage', '') or 'unknown'`, and the `sf_api.error(507, ...)` at `:1017` passing the constant `'capacity_guard'`. Leave the `CongestedNetwork` 507s at `:428` and `:451` alone, the `409` at `:937` alone, and the `404` alone. Then update the `507` response declaration at `:574`: description names the `Retry-After` header and the `stage`/`transient` fields, and the third tuple element becomes a real sample body. Do not change the tuple arity (D32). Run `tox -epy3 -- shakenfist.tests.external_api` and confirm `test_openapi_spec.py` still passes. Commit subject: `Say that a capacity refusal is transient.` |
| 4d | medium | sonnet | none | Client headers, in the `client-python` repository (`/srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python`), on its own branch and PR. Add `headers=None` as a keyword argument to `APIException.__init__` (`shakenfist_client/apiclient.py:64-70`), storing `self.headers = headers or {}`. It must be keyword-with-default: the five positional arguments are constructed at three sites (`apiclient.py:385-387`, `:393-394`, `:408`) and by downstream callers. Pass `r.headers` at the two `_actual_request_url` raise sites; `_authenticate()`'s raise may pass it too. Unit test constructs the exception both ways and asserts the old five-positional form still works. Commit subject: `Carry response headers on APIException.` |
| 4e | high | opus | none | The opt-in retry, in `client-python`. Add a `Client(..., retry_transient_capacity=False)` constructor flag. In `_request_url` (`apiclient.py:412-460`), extend the existing loop: catch `InsufficientResourcesException`, and retry only when the flag is set **and** the decoded body has `transient` truthy -- never on the status code alone, since an old server's 507 carries no marker and an unmarked 507 is not a promise. Sleep the `Retry-After` header's value when present and parseable, else `TRANSIENT_RETRY_DEFAULT = 15`; clamp to something sane so a hostile header cannot park a caller. Bound by the same `deadline` the 406 clause uses, which `create_instance()` already supplies (`apiclient.py:674-681`). Note `_calculate_async_deadline(ASYNC_CONTINUE)` returns -1 (`:180-188`), so under that strategy the deadline is already in the past and no retry happens -- that is correct, not a bug, and the code should say so in a comment rather than special-casing it. Unit tests: off by default; on-but-unmarked does not retry; on-and-marked retries and honours the header; the deadline is respected. Update the client's README/docs for the flag. Commit subject: `Optionally wait out a transient capacity refusal.` |
| 4f | medium | sonnet | none | The suite's contract helper, per D34. In `shakenfist/deploy/shakenfist_ci/base.py`, extend `assertRefusedAtStage()` (`:1464-1533`) to assert `body['transient'] is True` and `body['stage'] == stage` unconditionally, and to assert `Retry-After` only when `getattr(response, 'headers', None)` is non-empty, with a comment naming the client version that makes the conditional removable. Update its docstring: it currently says phase 4 "adds" this; it now records what phase 4 added and the date. Its four callers in `cluster_ci_tests/test_saturation.py` (`:240`, `:302`, `:371`, `:1177`) should need no change -- verify that, and say so. Do **not** enable the 4e client flag anywhere in the suite (D33); if you find yourself wanting to, re-read D33 and report instead. Commit subject: `Assert the transient refusal contract.` |
| 4g | low | haiku | none | Documentation. In `docs/developer_guide/` or `docs/operator_guide/` as the existing structure dictates, document the refusal contract: which 507s are transient, what `stage` and `transient` mean, that `Retry-After` is a fixed 15 s and why it is not computed, and that the client retry exists and is off by default. Link it from the scheduler operator documentation that already describes capacity refusals (`docs/operator_guide/scheduler.md`). Do not restate the numbers in more than one place. Commit subject: `Document the transient refusal contract.` |
| 4h | low | haiku | none | Closeout, as a separate PR after 4a-4g have merged. Set the phase 4 row to `Complete` in the master plan's Execution table and in `docs/plans/index.md` with the merge commits and PR numbers, update the index arithmetic to `4 of 7`, add an `## Outcome` section to this file, and run `python3 tools/check-plan-status.py`. Commit subject: `Close out transient-capacity-refusals phase 4.` |

The survey corrections described at the end of *What the survey found*
were made in the planning commit and are not a step here.

## Risks and mitigations

**A `shakenfist-utilities` upgrade changes the body shape under us.**
As originally written this risk was stated backwards, and 4a's
monkeypatch check found it. The risk was "an upgrade silently drops
the new fields", mitigated by asserting the whole decoded body. That
mitigation cannot work and the failure mode cannot happen: because
D28 specifies *replace, don't patch*, the helper discards the upstream
body entirely and builds its own, so our fields are never derived from
the upstream dict and no rename there can drop them. Renaming
`status` to `status_code` upstream was confirmed to leave all of 4a's
original assertions passing.

The real exposure is the inverse. If the upstream body shape drifts,
this `507` becomes the only error response in the API whose shape
differs from the other 305 `sf_api.error()` call sites, and nothing
notices. Mitigation: `test_sf_api_error_shape_canary` calls
`sf_api.error()` directly and pins *its* body shape by whole-dict
equality, so the drift fails a test here and tells us to re-sync.
The same rename was confirmed to fail that canary and only that
canary. The management session checks the canary exists and that the
assertion is on the whole dict, not on `assertIn`.

**A blind retry ends up in the CI suite anyway.** The master plan
currently instructs it, and a later reader may follow the master plan
rather than this file. Mitigation: D33 is stated here, the master
plan's phase 4 section is corrected in the planning commit, and 4f's
brief tells the sub-agent to report rather than enable. The management
session greps `shakenfist/deploy/shakenfist_ci/` for
`retry_transient_capacity` before approving 4f and expects no hits.

**The guard branch's `capacity_guard` stage is asserted by nothing.**
No test exercises `external_api/instance.py:1017`'s contract today --
`assertRefusedAtStage()`'s regexp only matches the filter branch's
message. Mitigation: this is recorded honestly rather than fixed here.
Provoking the guard branch deterministically needs a concurrent
placement race, which is `test_saturation.py`'s territory and the
sizing plan's phase. It goes to Future work with that reasoning.

**A hostile or broken `Retry-After` parks a client.** Mitigation:
4e clamps the parsed value and falls back to the default on anything
unparseable, and the retry is bounded by the caller's deadline in any
case. Unit test covers a garbage header.

**The two repositories drift out of step.** Mitigation: D34 orders
them server-first and makes the suite assertion degrade rather than
fail against an older client, so there is no window in which `develop`
is red waiting for a release.

## Definition of done

Falsifiable, in order:

1. `shakenfist/external_api/base.py` defines
   `TRANSIENT_RETRY_AFTER_SECONDS = 15` and exactly one helper that
   produces a transient `507`. No other module *sets* the header:
   `base.py:157` is the only assignment. Tests and the functional
   suite's `assertRefusedAtStage()` do name the header and the
   literal `15`, which is the contract being asserted rather than a
   second definition of it -- the suite ships separately from the
   server and deliberately does not import its constants.
2. A unit test decodes the helper's response body and asserts it
   equals `{'error': ..., 'status': 507, 'stage': ..., 'transient':
   ...}` by equality, not by membership, for a transient stage, a
   permanent stage and an unrecognised one. A second test,
   `test_sf_api_error_shape_canary`, calls `sf_api.error()` directly
   and pins its body shape the same way. Changing `sf_api.error()`'s
   body shape makes the canary fail and leaves the first test passing
   -- confirmed by monkeypatching the shape in the test run, not by
   reading the assertion. (This item originally claimed the first
   test would fail. It does not and cannot; see the corrected risk of
   the same name for why, and what the canary protects instead.)
3. `POST /instances` returns `Retry-After: 15` and a body with
   `transient: true` for a transient filter stage and for the
   capacity-guard branch; a `507` with `transient: false`, its stage
   intact and no `Retry-After` for a structural filter stage and for
   the `'unknown'` fallback (D35); and neither field nor header for
   the two `CongestedNetwork` `507`s, the `409` and the `404`.
   Asserted by one test per outcome, so a failure in one does not
   hide another.
4. The filter branch's `stage` equals the scheduler filter name for
   at least `sufficient_idle_cpu`, and deleting the `stage=` argument
   at `scheduler.py:525-558` makes that test fail. The handler
   contains no parse of `str(e)`.
5. The guard branch's `stage` is `constants.CAPACITY_GUARD_STAGE`,
   whose value is `capacity_guard`, and both producers of that stage
   -- the create path and the preflight redirect -- use the constant
   rather than a literal.
6. No message text produced by `_log_and_raise_on_error()` changed:
   `assertRefusedAtStage()`'s regexp still matches, and all four of
   its callers in `test_saturation.py` are byte-for-byte unchanged.
7. `shakenfist/tests/external_api/test_openapi_spec.py` passes, and
   the published `507` description for `POST /instances` names both
   `Retry-After` and `transient`.
8. `APIException('m', 'GET', 'u', 507, 't')` -- five positional
   arguments, no headers -- still constructs. A test asserts it.
9. With `retry_transient_capacity=False` (the default), a `507`
   carrying `transient: true` raises immediately: the test asserts
   exactly one HTTP request was issued.
10. With the flag on, a `507` *without* `transient` raises
    immediately and one carrying it is retried; a test asserts the
    sleep taken came from the `Retry-After` header and not from the
    default.
11. `grep -rn retry_transient_capacity shakenfist/deploy/` returns
    nothing (D33).
12. `assertRefusedAtStage()` asserts the body fields with no
    conditional, and the header behind a `getattr` guard whose comment
    names the client version that retires it. A body missing those
    fields, or not a dict at all, fails as an assertion rather than as
    a `KeyError` escaping the helper -- mutation-tested by removing
    the guard.
13. `docs/plans/PLAN-transient-capacity-refusals.md` nowhere asserts
    that the suite turns the client retry on. The phrase does still
    appear there once, inside the sentence recording that an earlier
    draft said so "and that was wrong" -- a correction a reader
    benefits from, not the claim. A bare grep for the phrase
    therefore matches; read the sentence rather than the grep.
14. An issue exists for the OpenAPI response-declaration format's
    inability to express a response header (D32), referenced from this
    file and from `PLAN-api-input-validation.md`'s Future work. Filed
    as [#4240](https://github.com/shakenfist/shakenfist/issues/4240).
15. `python3 tools/check-plan-status.py` reports agreement.
16. `docs/operator_guide/capacity_refusals.md` says which stages are
    transient and which are not, rather than generalising over the
    filter branch, and its client-retry section opens by saying the
    flag is in no released client.
17. Every stage name `scheduler.py` passes to
    `_log_and_raise_on_error()` without an `exception_class` override
    appears in exactly one of `TRANSIENT_CAPACITY_STAGES` or
    `PERMANENT_CAPACITY_STAGES`, checked by parsing `scheduler.py`
    rather than against a hand-maintained list. Mutation-tested by
    adding a stage: the check fails.
18. `TRANSIENT_RETRY_AFTER_SECONDS` equals
    `BaseClusterOperation.defer()`'s `delay` default, asserted by
    reading the signature. The constant's comment claims the two were
    chosen to match; it no longer claims they are one definition,
    because they are two literals.

## Future work this phase creates

- **A transient marker for address-pool exhaustion.** The two
  `CongestedNetwork` `507`s are left bare by D29. They probably do
  deserve a marker, with a horizon derived from the deletion halo
  rather than from `defer()`. Needs its own evidence about how long
  the halo actually holds an address in practice.
- **Nothing asserts the capacity-guard branch's contract.** See the
  risk of the same name. Best provoked from `test_saturation.py`.
- **Retire `assertRefusedAtStage()`'s header conditional** once the
  minimum client version carries `APIException.headers`. The same
  release fixes the operator guide's client-retry warning, which names
  a branch because no version exists to name.
- **Split claim denials out of the transient marker** when
  PLAN-scheduler-reservations phase 5 sets
  `mariadb.CLAIM_ENFORCEMENT_HARD` to `True` (D35). Until then
  `capacity_guard` is honestly transient; afterwards it covers a
  namespace quota refusal that no wait clears.
- **A header-capable OpenAPI response declaration** (D32), filed as
  [#4240](https://github.com/shakenfist/shakenfist/issues/4240)
  against `PLAN-api-input-validation`.
- **Name the client release in `assertRefusedAtStage()`'s header
  guard.** 4f could not name one: `client-python` has no version
  assigned to the release which will carry `APIException.headers`
  (last release v0.8.3, the change unreleased on its own branch), so
  the comment names the commit and says so rather than inventing a
  number. Replace it with the version once one exists.

## Back brief

Before starting, the implementing session states in its own words:
what the four `507` branches are and which two are being marked; why
the stage is carried on the exception rather than parsed out of the
message; and why the CI suite does not turn the client retry on. If
that third one comes back as "because the master plan says to", stop
and re-read D33 -- the master plan said the opposite until this
phase's planning commit.

**Gate before 4e.** The client retry's interaction with
`_calculate_async_deadline()` and the existing `406` loop is cheap to
propose and expensive to redo. Propose the loop's shape -- where the
catch goes, how the sleep is chosen, what happens under each of the
three async strategies -- and have it agreed before editing
`apiclient.py`.
