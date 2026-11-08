# Placeable capacity phase 2: the placement dry run

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly and ground
your answers in what the code actually does today. Do not
speculate when you could read instead. Flag uncertainty
explicitly.

This is phase 2 of `PLAN-placeable-capacity.md`. Read that master
plan's "Phase 2: the placement dry run" section first, its `G1`,
`G2` and `G5` requirements, and phase 0's `D11`, `D12` and `D13`
(`PLAN-placeable-capacity-phase-00-decisions.md`), which constrain
what this phase may assume. This phase adds an API surface and
changes no placement arithmetic.

Citations below are into `develop` at `d20a7d7516` unless stated
otherwise. Citations into `shakenfist/private-ci` are into its
`master` at `dce12f6`.

## Planning effort

High, as the master plan specifies: the phase is small in code and
load-bearing in contract. What it publishes is what every later
caller reads, and the refusal vocabulary it answers in is already
in production use by a consumer that reimplemented it (`F2`), so a
different vocabulary here would be a silent incompatibility rather
than a compile error. The review effort is high for the same
reason.

## Scope

In:

- A stateless placement query built on phase 1's
  `find_candidates_for_shapes()`: given a multiset of requested
  shapes, answer whether they fit now and on which nodes, writing
  no state and publishing no audit events.
- One new REST endpoint, its parameter declaration, and the
  structured shape schema that declaration needs.
- One capability string (`G5`).
- An `apiclient` method and capability probe in client-python,
  which is where every consumer of this reaches the API from.
- Unit tests, OpenAPI registration, and mutation testing to prove
  the tests would catch the query's removal.
- Operator and user documentation for the new surface.

Out:

- Shape holds (`A`). Phases 3 and 4.
- Switching `shakenfist/private-ci`'s shadow fit check from its
  local model to this call, and the soak that re-applies `D13`'s
  test to the result. That is the other repository's work, under
  `PLAN-placeable-claims`, and it gates phase 3 rather than this
  phase (`D9`).
- The `sf-client` and `sf_claim` shape verbs, and the affinity
  expression. Phase 6, per `D11`'s "left to phase 6".
- Any change to what a filter decides, what a stage is called, or
  what any event contains (`G5`). This phase adds a reader of the
  placement pipeline, not a change to it.
- Issue #4842, the string `cpus` 500 at admission. Filed during
  this phase's survey (`F9`) and deliberately not fixed here: it
  is a create-path type error, and a phase that adds a query has
  no business changing what a create does.
- Optimising the placement walk's event-payload construction,
  unless step 2e measures it as hot. The master plan asks for a
  measurement rather than a guess, and `D5` records what the
  measurement decides.
- Rate limiting or quota on the new endpoint beyond the shape-count
  bound in `D10`.

## What the survey found

**`F1`. The consumer this phase was written for already exists,
and already scores itself.** The master plan says "private-ci will
call it in shadow before creating a runner's namespace and score
the answer against what the job's creates actually met minutes
later". That harness is built and merged:
`PLAN-placeable-claims-phase-03-node-fit.md` is `Complete`
(`0513d69`, private-ci #241). `conductor/nodefit.py` rebuilds the
scheduler's per-node eligibility checks from the `per_node`
mapping of `GET /admin/resources`, `conductor/db.py:6012`
(`record_node_fit()`) records each verdict, and
`conductor/db.py:5808` (`get_node_fit_set_agreement()`) scores
them against what the creates actually met. So this phase does not
build a shadow harness. It replaces a client-side reimplementation
with a call, and private-ci's own plan already anticipates that
("becomes a call to it" waits on phase 5, its `F6`). The master
plan's phase 2 section is corrected to say so.

**`F2`. The refusal vocabulary is already pinned by that
consumer.** `conductor/nodefit.py`'s module docstring names the
stages it models, in the order the scheduler applies them and
under the names the scheduler reports: `cpu_max_per_instance`,
`sufficient_idle_cpu`, `sufficient_idle_memory`,
`sufficient_free_disk`, `capacity_guard`. It also names what it
cannot model from `/admin/resources` and therefore does not try
to: the disk-busy stage (`sufficient_idle_disk`, "whose input is
not published"), the demand clause, the claim stage and the
cluster stage, plus the scheduler's ordering of candidates by
load, where it "packs worst-fit on memory instead". Those five
omissions are the whole value of a server-side dry run, and they
are also the reason this phase must answer in exactly those stage
names: a prediction and a refusal are compared label for label by
code that is already deployed.

**`F3`. The event-construction cost the master plan flags is real,
and its arithmetic is confirmed.** `add_event_multi()` returns
immediately when given no targets (`shakenfist/eventlog.py:46`),
so a dry run publishes nothing -- but every `extra=` payload is
built by the caller before the call, so all of it is assembled and
discarded. The master plan's "around fourteen times per shape" is
right, and precisely: on a clean create-path walk a shape reaches
five unconditional direct sites in `_find_candidates_for_shape()`
(`shakenfist/scheduler.py:1358`, `:1418`, `:1634`, `:1747`,
`:1793`) and nine invocations of `_log_and_raise_on_error()`
(`:1155`), which emits exactly one event each, for fourteen. Four
further sites fire conditionally: forced candidates (`:1394`), one
per unknown node in a forced list (`:1408`), a degraded capacity
read (`:1466`) and a narrowing that selected nothing (`:1680`).

**`F4`. Phase 1's deferred return-type question can be closed, and
the answer is "leave it alone".** Phase 1 documented, rather than
enforced, the rule that only the heads of its per-shape candidate
lists are an assignment, on the grounds that "the type to return
is the one phase 2's real consumer wants". The hazard it was
guarding against -- a caller walking past a head, as
`Instance.place_on_candidates()` does when admission refuses it
(`shakenfist/instance.py:1549`) -- cannot arise for a caller that
places nothing. A dry run wants both halves: the head, because
that is the node the set was costed against, and the fallbacks,
because an operator asking why a set fits wants to see the room
behind the answer. `D7` keeps the core's signature and return type
unchanged.

**`F5`. `/admin/resources` already has the per-node shape this
phase would have added, and cannot carry the query.** `GET
/admin/resources` is `scheduler.summarize_cluster_resources()`
(`shakenfist/external_api/admin.py:168`,
`shakenfist/scheduler.py:833`), returning `total`, `per_node`,
`excluded` and `cluster_capacity` (`:621`). `excluded` is already
a per-node reason map, built from the metrics-unavailable reasons
and `_queue_exclusion_reason()`, and its keys are advertised
separately as the capability tokens `cluster-capacity`,
`cluster-resources-excluded` and `cluster-claims`
(`shakenfist/external_api/app.py:440-448`). What it cannot do is
answer this phase's question: a per-node verdict is a verdict
*about a shape*, and a GET with no body has no shape to be about.
`D6` resolves the master plan's "show the per-node answer in `GET
/admin/resources`" accordingly, and that sentence is corrected at
source.

**`F6`. The declaration vocabulary already has the array-of-object
form the request body needs.** `shakenfist/external_api/base.py`
carries `arrayofdict`, `arrayofdiskspec` and `arrayofnetworkspec`
(`:821-829`), each built on a named schema such as
`DISKSPEC_SCHEMA` (`:535`) with `additionalProperties: False`. A
shape array is the same construction. Three mechanical
consequences for the brief: several `'body'` parameters are merged
into one rendered `in: body` schema (`:1300-1328`), so
`test_at_most_one_body_parameter_per_operation`
(`shakenfist/tests/external_api/test_openapi_spec.py:555`) is not
a constraint on how many body fields are declared; a new structure
must be registered in `STRUCTURED_PARAMETERS` (`:109`) or
`test_every_published_structure_or_bound_is_registered` (`:511`)
fails; and the integer fields follow the existing claim-limit
declarations (`shakenfist/external_api/auth.py:1569-1574`), which
use `'unsignedinteger'`.

**`F7`. The real consumer holds a shape with its disks already
summed.** `conductor/nodefit.py:205` and `:417` both say so: a
shape is `(cpus, memory_mb, disk_gb)`, "with its disks summed".
`G1` says a shape is "what `POST /instances` takes: `cpus`,
`memory` and the disk list, matched on the summed disk size".
Both are satisfiable at once, and `D2` does: the body takes a
summed `disk` in GB, and accepts a `disk_spec` diskspec array as
the alternative form, summed server-side by
`mariadb.disk_spec_virtual_gb()` (`shakenfist/mariadb.py:26736`)
-- the same helper `Instance._capacity_claim()` uses
(`shakenfist/instance.py:1198`), so a dry run and a create cannot
disagree about how much disk a shape is.

**`F8`. `D11`'s shape vocabulary includes a key phase 1's core has
no counterpart for.** `D11` fixes the project's shape vocabulary as
`cpus`, `memory` in MB, `disk` in GB and `count`. It was written
for the claim verbs, but a shape should mean one thing across the
API, the CLI and the Ansible module, so `D2` uses those keys.
`find_candidates_for_shapes()` takes a flat list with no
multiplicity, so `count` is expanded by the handler into that many
identical shapes, which is also what makes `D10`'s bound
necessary.

**`F9`. Phase 1's deferred bug had no live home.** Phase 1 recorded
the `POST /instances` string-`cpus` 500 as belonging to
`PLAN-api-input-validation`'s scope. That plan is `Complete` (9 of
9) and never covered the site. Verified still broken on this tree:
the handler passes the body value to `Instance.new()` untyped,
`_capacity_claim()` returns it unchanged, and
`shakenfist/mariadb.py:29644` multiplies it by the `float`
`SCHEDULER_DEMAND_PER_VCPU` (`shakenfist/config.py:647`), which
raises `TypeError: can't multiply sequence by non-int of type
'float'`. Filed as issue #4842, and the master plan's "Bugs fixed
during this work" entry now points at it rather than at a closed
plan.

**`F10`. The one branch in flight does not overlap this phase's
files.** `scheduler-reservations-phase-05a-hard-ceiling` changes
`shakenfist/mariadb.py`, `protos/database.proto`,
`shakenfist/tests/mock_mariadb.py`,
`shakenfist/tests/test_mariadb_capacity_admission.py` and
`docs/plans/index.md`. It touches neither `shakenfist/scheduler.py`
nor `shakenfist/external_api/`, so the only textual conflict is
the index row. Its steps 4 to 6 are gated on a calibration window
that closes on 2026-11-12. What it does do is flip a hard ceiling
at admission, which is a bound this phase's answer predicts --
`R1` carries that.

**`F11`. The other repository's record of this is stale, and is
left alone.** `PLAN-placeable-claims.md:597` and
`PLAN-placeable-claims-phase-03-node-fit.md`'s `F6` both still say
no placement dry run exists and no Shaken Fist plan proposes one.
That was true when they were written on 2026-10-26 and is not now.
Correcting another repository's plan from this phase would put the
change where nobody reviewing placeable-claims would see it, so it
is recorded here for whoever next works that plan.

**`F12`. There is an unattributed rise in exactly the refusals
`D13` watches.** The plan-blockers note for
`claim-coverage-and-sizing` records that "scheduler
`sufficient_idle_memory` refusals rose from 0-1/day to 10-64/day
from 2026-10-25, unattributed". That is the stage `D13`'s
falsification test is written against. This phase does not resolve
it -- it builds the instrument that can, by separating the live
check from the ledger check in a single answer, which
`/admin/resources` cannot. It is one more reason `D9` hands the
soak over promptly rather than holding it inside this phase.

Nothing else the master plan's phase 2 section asserts was found to
be wrong: the capability-string mechanism, the `/admin/resources`
endpoint, the core's charged-head behaviour and
`place_on_candidates()`'s head-walking are all as described.

## Decisions

**`D1`. One new endpoint, `POST /placement`, namespace
authenticated, with the per-node detail admin-only.** A query that
takes a multiset cannot be a GET, and it is a question about the
cluster rather than about an instance that exists, so it does not
belong under `/instances/<instance_ref>` where the converter would
shadow it. Namespace authenticated because `G3`'s whole premise is
that ordinary callers benefit; the caller's own namespace is the
context the answer is computed in.

The per-node split is the part worth arguing about. A tenant
learning which node UUIDs exist, how much room each has and which
filter stage each failed is a cluster-topology disclosure that no
current endpoint gives a non-admin. So a non-admin caller is
answered at set level -- does the whole multiset fit, and if not,
the stage that emptied the list -- and an admin caller
additionally gets the per-shape ordered candidate lists. The real
consumer is unaffected: private-ci's conductor already reads `GET
/admin/resources`, so it holds admin rights today.

**`D2`. The request body is `shapes`, an array of objects with
`cpus`, `memory`, `disk`, `disk_spec`, `count` and `affinity`.**
`cpus` and `memory` (MB) are required; `disk` (GB) and `disk_spec`
(a diskspec array) are alternatives and giving both is a 400;
`count` defaults to 1; `affinity` is the existing affinity
document, in the `prefer_*`/`require_*` form (`G4`). The keys are
`D11`'s, so a shape means the same thing here as it will on the
claim verbs, and `disk_spec` exists so a caller that already holds
a `POST /instances` body can ask about it without summing the
disks itself (`F7`). Summing is `mariadb.disk_spec_virtual_gb()`,
not a second implementation.

**`D3`. The response names the stage, not a boolean.** At set
level: `fits`, and when false, `stage` and `shape` -- the index of
the shape that ran out of candidates. For an admin caller, a
`shapes` array in request order, each entry carrying `node` (the
head, the node that shape was costed against) and `candidates`
(the ordered list behind it). No new stage names are minted
(`G5`); the values are exactly what `_log_and_raise_on_error()`
already reports, which is what makes `F2`'s label-for-label
comparison possible.

**`D4`. A set that does not fit is a 200 with `fits: false`, not a
507.** This is the decision a reviewer is most likely to dispute,
because every other capacity refusal in this API is a 507 and
symmetry is a real argument. It is wrong here: the question "would
these fit" was answered, and answered correctly, so the request
succeeded. A 507 would make a caller's error handling unable to
distinguish "the cluster is full" from "the query failed", which
is precisely the distinction a dry run exists to provide, and it
would put a transient-retry contract (`G7`) on an answer that is
advisory by construction. A 507 remains what a *create* gets. The
endpoint's own failures -- a malformed body, an unreadable metrics
table -- keep their ordinary status codes.

**`D5`. Pass no event targets, and measure before optimising.**
The handler calls `find_candidates_for_shapes()` with
`related_objects=None`, so the walk publishes nothing: a
speculative evaluation stays out of the event log, which is the
behaviour phase 1 built for exactly this caller. The payload
construction still happens (`F3`). Step 2e measures it against a
real multiset; if it is hot, the guard goes once at the top of
`_find_candidates_for_shape()` rather than at fourteen call sites,
as the master plan directs. If it is not, the measurement is
recorded and nothing changes -- a dict built and dropped is not a
defect, and the guard would be a conditional on a correctness-
critical path bought with nothing.

**`D6`. `/admin/resources` is left alone.** The master plan's
instruction to "show the per-node answer" there cannot be followed
literally: a per-node verdict is a verdict about a shape, and that
endpoint takes no shape (`F5`). Publishing something shape-free
instead -- the largest shape each node would currently accept, say
-- would be a second, independently derived answer beside the real
one, which is the failure mode `_summarize_resources()`'s own
comments warn about twice. An operator wanting a per-node answer
asks `POST /placement` with the shape they care about. The master
plan's sentence is corrected rather than silently not done.

**`D7`. Phase 1's core is not changed.** Not its signature, not
its return type, not its stage names, not its events. `F4` settles
the question phase 1 left open in favour of the existing type. If
this phase finds it needs a change there, that is a finding to
report before making it, because phases 3 and 4 are built on the
same core and a change here is paid for by both.

**`D8`. client-python gets a method and a capability probe, not
verbs.** One `apiclient` method taking the shape list and
returning the parsed answer, plus a probe in the style of
`_require_claim_verbs()`, so a client against an older server says
so rather than guessing. The CLI verbs stay in phase 6 with the
rest of `D11`'s expression work. This is a second pull request in
a second repository, recorded in the master plan's Execution table
in the `client-python <sha> (#pr)` form it already specifies.

**`D9`. The soak and `D13`'s re-test are handed to
placeable-claims, and gate phase 3.** `D13` defers the live-memory
question to "phase 2's shadow counts, read before phase 3 is
written". Those counts are produced by private-ci calling this
endpoint and scoring the answer, which is private-ci's code, its
deploy and its soak (`F1`). Holding phase 2 open across another
repository's deploy would make it a multi-week phase whose last
step nobody in this repository can run. So this phase closes when
the endpoint and the client method merge, and the switch, the soak
and the reading are recorded as a `plan-blockers` entry gating
phase 3. Phase 3's planning session reads it there.

**`D10`. A bound on shapes per request, published in the
declaration.** `count` expansion means a short body can ask for a
long walk, and the walk is `O(shapes x candidates)` with fourteen
payload constructions per shape. The total expanded shape count is
capped at 64, declared as a `maximum` on `count` and enforced on
the sum, with a 400 naming the bound. 64 is above every real set
in the callers table -- 33fl's largest is eight runners -- and far
below a cost worth worrying about. Chosen as a published number
rather than a configuration setting: a caller needs to know the
bound to respect it, and nothing yet justifies per-cluster tuning.

## Step plan

Steps 2a, 2c and 2e run in sequence in the shakenfist worktree:
each runs `tox` and `pre-commit` in that tree, and each depends on
2a's code. Step 2b may run beside 2c or 2e only if the management
session is not already running a check in that tree; the simplest
order is sequential. Step 2d is in a different repository and a
different worktree, so it can run in parallel with any of them.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | **The endpoint, its declaration and its tests.** Read this phase plan's `F2`, `F4`, `F6`, `F7` and decisions `D1`-`D4`, `D7` and `D10` first, and `shakenfist/scheduler.py:1221`'s docstring in full. Add `SHAPESPEC_SCHEMA` to `shakenfist/external_api/base.py` beside `DISKSPEC_SCHEMA` (`:535`), with `additionalProperties: False` and the keys `D2` names, and an `'arrayofshapespec'` entry in the type table at `:821-829` mirroring `'arrayofdiskspec'` (`:827`). Add a `PlacementEndpoint` with a `post()`; the module it belongs in is your call between a new `shakenfist/external_api/placement.py` and `admin.py` -- say which and why in your report. Register it in `shakenfist/external_api/app.py` beside the other routes (`:594-606`) as `/placement`, and add one capability token to `API_CAPABILITIES` (`:440`) in whichever family you judge right, with a comment saying what a client learns from it, as the existing tokens do (`:443-448`). Declare parameters with `api_base.swagger_helper()` in the style of the claim limits at `shakenfist/external_api/auth.py:1569-1574`; integers are `'unsignedinteger'`. The handler builds `PlacementShape` values -- expanding `count`, summing `disk_spec` with `mariadb.disk_spec_virtual_gb()` (`shakenfist/mariadb.py:26736`) and never with a second sum of its own -- calls `Scheduler.find_candidates_for_shapes()` with `related_objects=None`, and renders `D3`'s response. Catch what the core raises: `LowResourceException` and `AffinityConstraintUnsatisfiable` both carry `stage=`, and both become a 200 with `fits: false` per `D4`. Do not change anything in `shakenfist/scheduler.py` (`D7`); if you believe you must, stop and report. Tests: a set that fits; a set that does not, asserting the stage and the shape index; a shape refused on hard affinity, asserting the stage differs from a capacity refusal; `count` expansion, asserting a count of 3 is charged three times; `disk` and `disk_spec` giving the same verdict for the same disks; both given at once being a 400; the bound in `D10` being a 400 that names it; a non-admin caller getting the set verdict and no per-node detail; and that no event is published by a dry run. Register the new structure in `STRUCTURED_PARAMETERS` (`shakenfist/tests/external_api/test_openapi_spec.py:109`) and add the capability token to `shakenfist/tests/external_api/test_root.py`'s expectations. Checks: `tox -epy3`, `tox -eflake8`, `tox -emypy`, and `pre-commit run --all-files` from the repository root -- the `Check endpoints authenticate by default` and `Check API parameter locations are derivable` hooks both apply to any new file under `external_api/`. Commit subject: `Answer whether a set of shapes fits now.` |
| 2b | medium | sonnet | none | **Document the query where placement is already documented.** Read `D1`-`D4`, `D6` and `D10`. In `docs/operator_guide/scheduler.md`, extend the passage that describes the placement walk with the new query: what it answers, that it writes nothing and publishes no events, that its answer is advisory and racy by construction, that a refusal is a 200 naming the stage rather than a 507, and that the per-node detail needs admin. Say explicitly that `/admin/resources` does not answer this and why (`D6`), because an operator's first instinct will be to look there. Add the user-facing page the capability and the client method need, following the shape of an existing user guide page; `docs/user_guide/affinity.md` is the closest in kind. Do not name a plan or a phase number anywhere outside `docs/plans/` -- the `Check documentation cites no plan phase numbers` hook enforces it. Leave `AGENTS.md` and `ARCHITECTURE.md` alone unless the component inventory genuinely changed: a new endpoint on an existing daemon is not a new component, and a convention has not changed. Checks: `pre-commit run --all-files`, with `Check documentation links and anchors resolve` passing. Commit subject: `Document the placement query.` |
| 2c | medium | sonnet | none | **Prove 2a's tests would catch the query's removal.** Use the `mutation-check` skill's method against step 2a's code only, carrying forward the mutation script phase 1 left in its own Results section if it is still in the scratchpad. At least these seven mutations, each applied to the live file by replacement from a pristine copy held outside the worktree and restored in a `finally` -- never with `git checkout` on a directory: drop the `count` expansion so a count of 3 places one shape; sum `disk_spec` with a plain `sum()` over the `size` keys instead of `disk_spec_virtual_gb()`; return `fits: true` whenever the core did not raise, discarding the stage; return the per-node detail to a non-admin caller; drop the `D10` bound; pass `related_objects=[the namespace]` so a dry run publishes events; and render the shapes array in largest-first order rather than request order. Run each under `timeout 900`. Record which test failed and on which assertion, and compare the tree byte-for-byte against the pristine copy afterwards to prove nothing was left mutated. A mutation no test catches is a finding: report it with the test that should have caught it and do not write the test yourself. Report counts and paths only; change no file under `shakenfist/`. Commit subject: none -- the management session records this in the plan's Results. |
| 2d | high | opus | worktree | **The client method and its capability probe.** This step works in `shakenfist/client-python`, not in this repository; take a worktree from its default branch. Read `D3`, `D4` and `D8`, and step 2a's merged diff for the request and response shapes. Add one method to `shakenfist_client/apiclient.py` next to the claim methods (`:1645-1700` as of client-python `v0.8.4`) that takes a list of shape mappings, posts them, and returns the parsed answer; a `fits: false` is a successful call that returns an answer, never an exception (`D4`). Add a capability probe in the style of `_require_claim_verbs()` in `shakenfist/deploy/collection/plugins/modules/sf_claim.py:275-302` in the shakenfist repository, so a client against a server that does not advertise the token says so rather than sending a request that 404s. Add no CLI verb and no `sf_claim` key: those are phase 6's, per `D11`. Follow that repository's own test and lint conventions, which are not this one's -- read its `tox.ini` and `.pre-commit-config.yaml` rather than assuming. Report the version the method should first appear in, and do not tag a release. Commit subject: `Add a placement query method.` |
| 2e | medium | sonnet | none | **Measure the dry run's event-payload cost, and decide nothing else.** Read `F3` and `D5`. Measure the cost of one `find_candidates_for_shapes()` call that publishes no events, against a synthetic cluster large enough to be interesting -- at least a dozen nodes and an eight-shape set -- and attribute it: how much of the wall time is spent building `extra=` payloads that `add_event_multi()` then discards at `shakenfist/eventlog.py:46`. Use a profiler rather than wall-clock arithmetic, keep the harness in the scratch directory this step is given, and report its full path. Report the numbers and your reading of them: whether a guard at the top of `_find_candidates_for_shape()` is worth buying a conditional on a correctness-critical path for, at this cost. Do **not** implement the guard, and change no file under `shakenfist/`. Commit subject: none -- the management session records this in the plan's Results, and `D5` says what happens next in each case. |

## Risks and mitigations

**`R1`. A dry run that predicts admission can drift from
admission.** The answer is computed from the scheduler's filters,
but a create is finally admitted by the guarded capacity claim, and
`scheduler-reservations` phase 5a is in flight to flip a hard
ceiling in that guard (`F10`). A dry run that silently stops
matching is worse than no dry run, because a consumer scores
against it. *Mitigation*: the answer is advisory by construction
and documented as such (2b), the `capacity_guard` stage is in the
vocabulary this phase answers in (`F2`), and private-ci's existing
scoring reports a disagreement with the stage that caused it --
which is the detector. The management session checks phase 5a's
merged diff against this phase's handler before phase 2's own pull
request lands, and says in the Execution notes whether anything
moved.

**`R2`. A caller treats an advisory answer as a guarantee.** The
race is inherent: the room can be gone before the next request.
*Mitigation*: `D4`'s 200-with-`fits` makes the answer data rather
than a promise, the documentation says so in the operator guide and
the user guide (2b), and the existing consumer already treats its
own verdict as scoreable rather than authoritative. Checked by the
management session reading 2b's prose for any sentence that could
be read as a reservation -- the word to hunt for is "will".

**`R3`. The endpoint is a cheap request that buys an expensive
walk.** *Mitigation*: `D10`'s published bound, enforced on the
expanded total rather than on the array length, with a test that
the bound is a 400 naming itself (2a) and a mutation proving the
test bites (2c).

**`R4`. The information disclosure in `D1` is drawn in the wrong
place.** Admin-only per-node detail may turn out to be too strict
for a legitimate tenant use, or not strict enough if the set-level
stage name itself leaks more than intended. *Mitigation*: the split
is a decision in this document rather than an accident, the test
suite asserts both sides of it (2a), and relaxing it later is
additive. The back brief asks Michael, because it is a security
boundary and not a style question.

**`R5`. The shape vocabulary diverges from the one phase 6 will
ship.** `D2` takes its keys from `D11`, which was written for the
claim verbs; if phase 6 changes its mind the API and the CLI
disagree about what a shape is. *Mitigation*: `D2` cites `D11` as
its source so the dependency is visible, and phase 6's planning
session is told in the master plan's phase 6 section that the API
keys are already published and are the constraint, not the other
way around.

## Definition of done

1. `POST /placement` exists, is registered in
   `shakenfist/external_api/app.py`, and answers a well-formed
   two-shape body with a 200. `grep -n "'/placement'"
   shakenfist/external_api/app.py` returns one hit.
2. One capability token was added, and it is advertised:
   `shakenfist/tests/external_api/test_root.py` asserts it, and
   `declared_tokens(API_CAPABILITIES)` contains it.
3. No stage name was minted. `git diff develop..HEAD --
   shakenfist/scheduler.py` is empty, and `grep -c
   '_log_and_raise_on_error('` on `shakenfist/scheduler.py` is
   still 10 -- one definition and nine call sites.
4. A set that does not fit is a 200. A test asserts the status
   code is 200, that `fits` is false, and that `stage` is one of
   the names `_log_and_raise_on_error()` reports; and a test
   asserts a 507 is *not* returned.
5. A dry run publishes no events. A test patches
   `add_event_multi` and asserts it was called zero times for a
   complete successful query, and zero times for a refused one.
6. The disk sum has one implementation. `grep -rn "disk_spec"
   shakenfist/external_api/` shows the new handler calling
   `mariadb.disk_spec_virtual_gb()` and no arithmetic of its own.
7. `count` is charged, not merely accepted. A test asserts that a
   single shape with `count: 3` consumes the headroom of three,
   by showing a set that fits at `count: 2` and does not at
   `count: 3` on the same cluster.
8. The `D10` bound is enforced on the expanded total. A request
   whose `count` values sum above 64 is a 400 whose message names
   64, and step 2c's mutation removing the bound is caught.
9. The non-admin answer carries no node identity. A test asserts
   that a non-admin response body, serialised, contains none of
   the fixture's node UUIDs.
10. Step 2c reports at least seven mutations, every one caught,
    with the failing test and assertion named for each, and the
    worktree byte-identical to its pristine copy afterwards.
11. Step 2e reports a measurement with a profiler artefact path,
    and `D5`'s decision is recorded in the Results either way.
12. `tox` and `pre-commit run --all-files` pass from the
    repository root, and `tools/check-plan-status.py` agrees with
    the index after the close-out commit.
13. The client-python method merges, is recorded in the master
    plan's Execution table in the `client-python <sha> (#pr)`
    form, and refuses to call a server that does not advertise the
    capability.
14. `D9`'s handover exists as a `plan-blockers` entry naming what
    private-ci must switch, soak and read, and phase 3's section
    of the master plan points at it.

## Back brief

Before executing any step, back brief the operator on your
understanding of this phase: that the shadow consumer it was
written to serve already exists and already scores itself, so this
phase replaces a client-side reimplementation rather than building
a harness (`F1`); that the stage names it answers in are fixed by
that deployed consumer and are not a design choice (`F2`); that it
adds a reader of the placement pipeline and changes nothing inside
it (`D7`); and that the answer is advisory by construction, which
is why a set that does not fit is a 200 rather than a 507 (`D4`).

Four things need Michael's answer before step 2a runs.

- **`D1`'s admin boundary.** Per-node detail admin-only, with a
  non-admin caller answered at set level -- or per-node detail for
  any authenticated caller, on the grounds that a 507 already
  tells a tenant a stage name and the rest is not much more. This
  is a security boundary, so it is his and not mine (`R4`).
- **`D4`'s status code.** A 200 with `fits: false`, or a 507 for
  symmetry with every other capacity refusal in the API. I have
  argued for the 200 and expect this to be the contested one.
- **`D10`'s bound of 64**, and whether a published constant is the
  right instrument or it should be a configuration setting.
- **Whether this session may push and open the pull requests** --
  two of them, in two repositories (`D8`).

One thing explicitly does not need an answer: whether to fix issue
#4842 here. It is filed, it is a create-path type error, and this
phase's scope says no.
