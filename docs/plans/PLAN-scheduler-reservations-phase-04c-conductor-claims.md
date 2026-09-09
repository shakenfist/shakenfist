# Scheduler reservations phase 4c: conductor claim integration

## Prompt

Before responding to questions or discussion points in this
document, explore both the shakenfist and private-ci codebases
thoroughly. Read relevant source files, understand existing
patterns (the conductor main loop, the provisioner's runner
lifecycle, the workflow cost tables, Shaken Fist's namespace
claim API and its guarded-UPDATE admission transaction), and
ground your answers in what the code actually does today. Do not
speculate about the codebase when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

The private CI conductor lives in the `shakenfist/private-ci`
repository, checked out beside this one. It is a private
repository today for historical reasons -- it once held secrets
and was not thought interesting -- and is expected to be renamed
and published at some point. Referring to it by name from this
public repository is fine and deliberate; the two systems are a
single design and pretending otherwise is what produced the gap
this phase closes.

Consult `ARCHITECTURE.md` for the Shaken Fist architecture and
`docs/developer_guide/subsystem_internals.md` for the scheduler
capacity counters. On the conductor side, consult its own
`ARCHITECTURE.md` (the cost tables are documented at
`ARCHITECTURE.md:700-820`) and `AGENTS.md`.

<!-- shared-block: plan-file-conventions v1 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section:

  | Phase | Plan | Status |
  |-------|------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

Phase 4 shipped namespace capacity claims: a first-class object,
admin-only REST CRUD at `/auth/namespaces/<namespace>/claims`,
drawdown on every placement, and an advisory ceiling that reports
exceedances as audit events rather than refusing the create.
Phase 4a soaked it against sfcbr and closed out on 2026-08-24.
The feature has been on sfcbr since 2026-08-22 and merged since
2026-08-17.

In that time, no claim has been created by anything other than a
test. The functional suite creates and deletes them
(`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_namespace_claims.py`),
the phase 4a soak exerciser created one and deleted it again, and
that is the whole population. Every namespace on the cluster --
including all ~6 concurrent CI runner namespaces, which are the
workload the feature was designed around -- is unclaimed.

This matters because of how phase 5 is sequenced. D16 makes the
ceiling advisory for one release *specifically* so that
exceedances are observed before they are refused: "the advisory
release admits over-ceiling creates but logs the D9 structured
event so learned footprints calibrate before rejections start".
That calibration window is open now and it is collecting nothing,
because nothing it could calibrate against exists. Phase 5 as
currently written would flip `CLAIM_ENFORCEMENT_HARD` on the
strength of a measurement period that never had a consumer in it.

The conductor contract itself is not missing -- D18 of the phase
0 decisions document specifies it in detail, and the step 3
addendum of 2026-08-13 sharpened its sizing key against real
data. What is missing is any phase that implements it. The master
plan disposes of the work in one subordinate clause of the phase
4 scope stub ("The conductor-side integration (D18) lands in
private-ci once this phase ships"), which is an assumption about
work happening elsewhere rather than a tracked phase, and the
private-ci repository has no plan and no code for it: grepping it
for `namespace_claim`, `capacity claim` or `/claims` returns
nothing at all.

## Mission and problem statement

Make the conductor the reference consumer of namespace capacity
claims, and produce from it the observation record that phase 5
needs before it can responsibly turn the ceiling hard.

Two things follow from that framing, and they are equally the
mission:

1. **The integration.** A claim per runner namespace, sized from
   the workflow cost data the conductor already collects, created
   before the runner is provisioned and deleted when the
   namespace is torn down, with a refusal handled as back-pressure
   rather than as a failure.
2. **The evidence.** A written record, in this plan, of what
   claims did in production: how often creation was refused for
   want of cluster capacity, how often a placement exceeded a
   claim and in which dimension, how the sizing formula's output
   compared to the measured peak, and whether any claim leaked.
   That record is phase 5's input. A phase that lands the code
   and skips the observation has not done the job.

## Scope

In scope:

- Claim creation at runner-namespace creation in
  `conductor/provisioner.py`, sized per D18.
- A sizing accessor in `conductor/db.py` keyed as the step 3
  addendum requires.
- Refusal handling: capacity refusals leave the job queued and
  are counted; transient refusals retry; everything else degrades
  to today's unclaimed behaviour rather than blocking CI.
- Claim deletion during namespace teardown.
- Prometheus counters and a dashboard surface for claim
  outcomes and claim-size-versus-measured-peak.
- A production observation window, and its write-up here.

Out of scope, deliberately:

- **Anti-starvation policy.** D18 proposes that once a queued job
  has waited 15 minutes the conductor stops admitting
  larger-claim jobs ahead of it. That constant was flagged
  provisional in phase 0 and again in the step 3 addendum, on the
  grounds that no deferral data can exist until claims are
  enforced. This phase creates that data; writing the policy
  before reading it would be inventing a number twice.
- **Hard enforcement, and any 403 handling.** That is phase 5.
  The conductor should not grow a code path for a refusal the
  server cannot yet send.
- **Claims for the image builder's namespace** (`ci-images`) and
  for the static runners. Both hold real capacity, and both are
  candidates once the runner path has proved itself; neither is
  needed to answer the question this phase exists to answer.
- **Changing `CI_SIZES` or the sizing recommender**
  (`conductor/sizing.py`). Claim sizing and runner sizing are
  different questions over the same data -- a claim covers the
  whole namespace including the nested cloud a job builds, a
  runner size covers the runner guest alone -- and this phase
  adds the first without touching the second.
- **Any change to Shaken Fist itself**, beyond the client work
  called out as a prerequisite below.

## What the survey found (2026-08-27)

The survey was against `private-ci` at `9cdf000` and shakenfist
at `45332ff81`. Ten findings, of which four change the plan.

**1. The work exists nowhere.** No conductor code, no plan file,
no issue. `docs/plans/` in private-ci holds eight plans and none
of them is about claims. The master plan's phase 4 stub is the
only place the obligation is written down.

**2. The client has no claim support, and the conductor cannot
work around it.** `shakenfist_client/apiclient.py` has no claim
methods (the only `claim` hits are JWT `bound_claims`). That is
phase 9's scope, which this plan renumbers to 4b and moves ahead
of phase 5. The conductor cannot simply issue raw REST instead:
every Shaken Fist call it makes goes through
`conductor/sfclient.py`, which proxies client methods into a
worker thread with a 90-second ceiling (`SF_CALL_TIMEOUT`)
precisely because an unbounded call once wedged the main loop,
stopped the heartbeat and crash-looped the service under the
systemd watchdog. A hand-rolled `requests` call in the conductor
would sit outside that protection, which is the one failure mode
that module exists to prevent. Note that Shaken Fist's own
functional claims test does reach past the public surface, via
`apiclient.Client._request_url()`, with a docstring saying not to
"fix" it onto verbs until a client release exists -- that is a
deliberate stopgap inside the test suite, and phase 4b retires it.
It is not a precedent the conductor can borrow, because the test
suite has no watchdog to trip.

**3. HTTP 503 is not mapped to an exception class.**
`STATUS_CODES_TO_ERRORS` in `apiclient.py:118-127` covers 400,
401, 403, 404, 406, 409, 500 and 507, but not 503 -- and the
claims API answers 503 for both of its retryable refusals
(`no_cluster_capacity` and `conflict`, per `CLAIM_REFUSAL_STATUS`
at `shakenfist/external_api/auth.py:1254-1262`). A 503 still raises a bare
`APIException` carrying `status_code`, so a caller can tell
"retry in a moment" from a durable error by attribute -- but
catching a class reads better than inspecting one, and E6
branches on exactly that distinction. client-python#364 already scopes "the status codes worth
typed exceptions" for phase 4b; 503 is the specific one this
phase's refusal handling depends on, so it is named here rather
than left to that issue's discretion.

**4. D18's "existing deferral mechanics" do not exist.** D18 says
a denied runner "is deferred via the existing deferral mechanics
and retried". The only deferral in the conductor is for namespace
*deletion* -- `provisioner.py:717`, deferring a delete while
network deletes settle. There is no deferral queue for
provisioning. The real analogue is the image-builder quarantine
path in `create_workers()` (`provisioner.py:951-960`), which
`continue`s past a quarantined label and leaves those jobs queued
for a later cycle. That is the shape the refusal handling should
take, and it is simpler than what D18 imagined.

**5. The sizing data exists, keyed more richly than D18 needs.**
`db.get_cost_observations()` (`db.py:1419`) groups by `(repo,
workflow_name, job_name, runner_size)` and requires
`min_runs=3`. D18 as sharpened wants `(repo, job_name)` and
accepts a single generation-2 observation, because peaks are
topology-deterministic. So this phase adds a sibling accessor
rather than reusing that one -- the existing function's key and
threshold are right for *its* consumer (the sizing recommender)
and wrong for this one.

**6. `peak_allocated_*` is the correct denomination.** It is
recorded per teardown at `provisioner.py:328-330`, and
ARCHITECTURE.md:717-719 documents it as the peak concurrent
*allocated* footprint across the whole namespace -- runner plus
whatever nested cloud the job built. Allocated, not measured, is
what a claim needs, because the claim counters are an allocation
ledger over placed instances. Disk is virtual size on both sides,
so the `SCHEDULER_DISK_OVERCOMMIT` factor phase 3 introduced
applies to node admission and does not need to be reproduced in
the claim sizing.

**7. Per-job claim expiry is not available.** D18 proposes expiry
at "about twice the workflow timeout". The conductor does not
know a job's timeout: GitHub's queued-jobs data carries repo,
workflow, job name, labels and URL (`create_workers()`'s
`triggering_job`), not `timeout-minutes`. The longest
`timeout-minutes` in this repository's own workflows is 180. An
expiry set too short is a silent fault -- the claim's
`coverage_state` flips to `expired` while the job is still
running and its instances quietly stop being charged to it -- so
this phase uses a flat, generous expiry as a leak backstop and
relies on explicit deletion for prompt release. See E5.

**8. The namespace backstop is real but slow.** Namespace
deletion does not block on claims: the endpoint checks instances,
networks and artifacts only (`auth.py:343-369`), and
`Namespace.hard_delete()` deletes the namespace's claims through
the object, which is what returns the capacity
(`namespace.py:358-367`). So a leaked claim is eventually
reclaimed. But the conductor routinely *defers* its
`delete_namespace` call while queued network deletes settle
(`provisioner.py:705-719`), and the namespace only reaches
`hard_delete` after the cleaner gets to it. Explicit deletion at
teardown is therefore worth doing on its own merits, not merely
as an optimisation.

**9. One namespace per runner, created inline.**
`create_workers()` creates `sfcbr-<unique>`, adds a namespace key
and the `ci-images` trust, then allocates a network and creates
the instance (`provisioner.py:1041-1120`). The claim belongs
between the trust and the network. The conductor's system client
is a cluster administrator, which the claim endpoints require.

**10. Refusal on creation is the common case, not the edge
case.** Issue #3907 -- whose fix merged into `develop` on the
day this survey was written -- records the functional claims tests failing
three times in one day with 507 because sibling tests in the same
suite held the cluster's CPUs at the wrong instant. Claim
creation is a hard guarded admission against `cluster_capacity`
even while the *ceiling* is advisory, and sfcbr genuinely runs
out of headroom under its own CI load. A conductor that treats a
507 as an error will stop provisioning runners on a busy cluster;
one that treats it as back-pressure gets exactly the behaviour
this whole plan is for. This finding is the reason E6 is written
the way it is.

### Corrections made at source

Per the survey habit, the false claims were corrected where they
live rather than only noted here, in the same commit as this
plan:

- The phase 4 scope stub in `PLAN-scheduler-reservations.md` no
  longer asserts that the conductor integration lands in
  private-ci "once this phase ships"; it points at this phase.
- D18 in `PLAN-scheduler-reservations-phase-00-decisions.md`
  carries a dated correction recording finding 4 (no deferral
  mechanics), finding 7 (no per-job timeout available) and the
  sizing-accessor consequence of finding 5.
- D16 carries a dated note that the advisory window is only
  meaningful once a consumer exists, and that phase 5 is
  therefore gated on this phase rather than on elapsed time.

## Decisions

**E1. This plan lives in shakenfist; the code will land in
private-ci.** The repository convention is that a plan file lives
with the code it plans, and the shared block above says phase
files sit beside their master plan. Those two rules point in
opposite directions for a cross-repository phase, so one has to
give. This plan goes where the master plan is, for three reasons:
a reader working through the scheduler-reservations phases can
read every phase in one place; the index arithmetic in
`docs/plans/index.md` counts it; and the substance of the
document is a Shaken Fist capacity question, publicly useful,
which would be invisible if it were filed inside a private
repository. The implementation PR in private-ci links back here.
This is a deviation from the plan-with-the-code rule and is
recorded as one.

**E2. Phase 4b (client support) is a hard prerequisite.** The
conductor gets claim methods through `shakenfist_client`, not
through hand-rolled REST, for the timeout reason in finding 2.
The client surface is already specified in client-python#364,
including the `PUT` field-mask semantics and the `state` versus
`coverage_state` distinction.
Phase 4b must also add the 503 mapping from finding 3. The
methods needed are create, list, get, update and delete; the CLI
verbs are part of 4b but not needed here.

**E3. One claim per runner namespace, created immediately after
the namespace.** Claims are namespace-scoped, so the namespace
must exist before its claim can be requested, and there is no way
to ask "would this claim be granted?" without asking for it. The
order is therefore: create namespace, add key, add trust, request
claim, then network and instance. On a capacity refusal the
conductor tears the fresh namespace down again and leaves the job
queued -- an empty namespace with no key-bearing resources is
cheap to remove, and leaving it behind would accumulate strays at
exactly the moments the cluster is most loaded.

**E4. Sizing is `max(runner footprint, ceil(1.2 x worst observed
peak_allocated_*))` per dimension, keyed `(repo, job_name)`.**
The 1.2 headroom and the key are D18 as sharpened by the step 3
addendum. The floor is the runner's own footprint from
`CI_SIZES[ci_size]` rather than a "size-label default", which is
the same thing said more precisely: a claim smaller than the
runner about to be started would guarantee an over-limit event on
the very first placement. Where there is no observation for the
key -- a new job, or a cycle with no `triggering_job` attribution
-- the floor is the whole answer. A single observation is enough
to raise the claim above the floor; that is the addendum's
finding that peaks are topology-deterministic, and it is why this
phase does not reuse `get_cost_observations()`'s `min_runs=3`.

**E5. Expiry is a flat six hours, and is a leak backstop rather
than a lifecycle mechanism.** Six hours is twice the longest
`timeout-minutes` in the repository's workflows, which is the
best available reading of D18's "twice the workflow timeout"
given finding 7. The claim is not re-dated as the runner lives;
the normal end of a claim is explicit deletion at teardown, and
the expiry exists only so that a conductor which dies mid-cycle
does not promise cluster capacity forever. Setting it shorter
trades a real failure mode (coverage silently lost under a
long-running job) against a hypothetical one.

**E6. A capacity refusal is back-pressure, an error is a
degradation, and neither stops CI.** Three branches, matching
finding 10:

- `InsufficientResourcesException` (507): the cluster cannot
  promise this claim. Remove the fresh namespace, count it,
  `continue` to the next (label, size) combination, leave the job
  queued for a later cycle. This is the intended steady-state
  behaviour on a busy cluster, not an incident.
- A transient refusal (503 -- `no_cluster_capacity` while the
  reconciler is still building the singleton, or `conflict` after
  the optimistic retry budget): same handling, counted
  separately, because a persistent 503 rate means something is
  wrong with the tier rather than with the cluster's capacity.
- Anything else (400, 409, an unexpected 500, a client timeout):
  log loudly, count it, and **provision the runner anyway with no
  claim**. Its usage still lands in `unclaimed_used` and the
  cluster still accounts for it; the only thing lost is the claim
  itself.

The third branch is the decision most likely to be argued with,
because it means a bug in claim handling degrades silently to
today's behaviour instead of stopping. It is deliberate: this is
the advisory release, the conductor is CI for the whole project,
and an accounting feature must not be able to take CI down while
it is still being calibrated. The counter is what stops it being
silent, and the definition of done requires that counter to be
zero over the observation window before phase 5 proceeds.

**E7. No local claim bookkeeping.** The conductor does not record
claim UUIDs in its own database. At teardown it lists the
namespace's claims and deletes what it finds. This survives a
conductor restart mid-runner, needs no schema change, and cannot
drift from the server's view. It costs one extra API call per
teardown, against a teardown that already makes several.

**E8. The phase is not done when the code lands.** The
observation record described in the mission is a deliverable of
this phase, with a minimum window of seven days of normal CI
load. See the definition of done.

## Design

### Where the claim is created

In `create_workers()` (`provisioner.py:1041`), between
`add_namespace_trust()` and `allocate_network()`. The claim
request needs the namespace name, the three limits from E4, and
the expiry from E5. `triggering_job` -- already popped from
`pending_jobs` a few lines above for logging -- supplies `repo`
and `job_name` for the sizing lookup, and may be `None`, in which
case E4's floor applies.

On refusal, the handling in E6 runs. The `requested` counter is
not incremented for a refused runner, so the cycle's budget is
not consumed by a runner that was never started.

### Where the claim is deleted

In `remove_namespace()` (`provisioner.py:606`), as a new step in
the existing sequence of independently-caught cleanups, after
`collect_namespace_costs()` and before `delete_all_instances()`.
Placing it first releases the claim's unused headroom -- the
difference between its limits and its drawdown -- at the earliest
possible moment, which is the point of prompt release; the
instances that briefly become `unclaimed_used` are deleted by the
very next step. The catch is as broad as its neighbours, for the
reason the function's docstring already gives: an exception
escaping here aborts the caller's whole cleanup pass.

### What is measured

New Prometheus counters in `conductor/metrics.py`, following the
`Counter` conventions already there:

- `conductor_claims_created_total`
- `conductor_claims_refused_total{reason="capacity"|"transient"}`
- `conductor_claims_failed_total` -- the E6 third branch, which
  should stay at zero
- `conductor_claims_deleted_total`

and a gauge or histogram relating claim size to measured peak per
dimension, which is D18's "claim size vs measured peak per
workflow" dashboard item. D18's third dashboard item,
queue-wait age, already exists as `QUEUED_JOB_AGE`
(`metrics.py:59`) and needs nothing.

The Shaken Fist side of the record comes from the audit events
phase 4 already emits: `placement admitted over namespace
capacity claim`, carrying `claim_dimensions`
(`shakenfist/instance.py:1079-1110`). Those are read from the
cluster during the observation window, not reproduced in the
conductor.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent | Status |
|------|--------|-------|-----------|---------------------|--------|
| 0 | low | sonnet | none | Prerequisite gate, **rewritten 2026-08-29**. Phase 4b must have merged its verbs and the 503 mapping in `STATUS_CODES_TO_ERRORS` (finding 3) to client-python's `develop` -- which it did on 2026-08-28 as `135ab53`. It does **not** need a release: the conductor pip-installs `git+https://github.com/shakenfist/client-python@develop` with `state: latest` and has since 2026-07-12, per phase 4b's finding 8 and decision D7. So the gate is a deploy, not a tag. Verify against the host rather than the playbook, because `state: latest` only re-pulls when the playbook runs: `/srv/shakenfist/private-ci/venv/bin/python -c 'import shakenfist_client.apiclient as a; print(a.Client.create_namespace_claim, a.ServiceUnavailableException)'`. Both names must resolve. The conductor runs on `maui`, so this is `ssh maui` and the command above. If either name does not resolve, the conductor has not been deployed since 2026-08-28 and running `conductor.yml` (`manage.yml`, tag `conductor`) is what opens this phase -- a deploy makes the gate true whatever it says now, so running the playbook is a valid substitute for checking it. Phase 4b's close-out on 2026-08-29 also carries the "deployed venv resolves the verbs" item that used to sit in its own definition of done: it was moved here, because a conductor deploy is this phase's entry gate and not 4b's deliverable. **Answered on 2026-08-29.** Both names resolve in the deployed venv on `maui`: `Client.create_namespace_claim` and `ServiceUnavailableException` are present, so the conductor has been deployed since client-python#375 merged and is carrying the claim verbs. Run by Michael on the host, because `ssh` to `maui` is refused from the development host for both `ansible@` and the default user. The client's version string was not captured and is not needed -- the branch install has no meaningful version, which is the point of D7, so what matters is that the names are there. **This phase's prerequisite is met and steps 1 to 5 may start.** | Complete |
| 1 | medium | sonnet | worktree | (private-ci) Sizing accessor. Add `get_claim_sizes()` to `conductor/db.py` beside `get_cost_observations()` (`:1419`), returning the worst observed `peak_allocated_cpus`, `peak_allocated_ram_mb` and `peak_allocated_disk_gb` grouped by `(repo, job_name)` with no minimum run count, from the same summary table that function reads. Read that function first: the peaks it exposes are already a MAX over summary rows, so this is a re-grouping of the same data, not a new measurement. Write the docstring to say why the key and threshold differ from its sibling -- claims cover the whole namespace and peaks are topology-deterministic, so one observation seeds a claim, whereas a runner-size recommendation needs three runs before it changes anything. Unit tests beside `conductor/tests/test_provisioner_costs.py`'s existing coverage. Commit subject: `conductor: size claims from observed peaks.` | Complete |
| 2 | high | opus | worktree | (private-ci) Claim creation and refusal handling in `conductor/provisioner.py`, per Design and E3/E4/E6. Add the sizing helper (max of `CI_SIZES[ci_size]` and 1.2x the step 1 peaks, ceiling per dimension), the claim request in `create_workers()` between `add_namespace_trust()` and `allocate_network()`, and the three-branch refusal handling. This was checked against sfcbr on 2026-08-27 during phase 4b and needs no re-checking: an over-large claim on a claim-free namespace answers **507**, raised as `InsufficientResourcesException`, with a per-dimension body naming the limit, the current usage and the request. The same request against a namespace which already holds a claim answers 409, because `exists` is evaluated first -- which is what the phase 4a soak recorded and why its "impossible claim" line said 409. The conductor claims on a namespace it has just created, so 507 is the case E6's first branch must catch. Add the metrics from Design. The namespace teardown on refusal must not be able to raise past the loop. Tests: the refusal branches, the sizing floor when no observation exists, and that a refused combination leaves `requested` unchanged. Commit subject: `conductor: claim capacity before starting a runner.` | Complete |
| 3 | medium | sonnet | worktree | (private-ci) Claim deletion in `remove_namespace()` (`provisioner.py:606`), per Design: list the namespace's claims through the client, delete each, count them, catch as broadly as the neighbouring steps and never raise. Place it after `collect_namespace_costs()` and before `delete_all_instances()`, and comment why that position rather than after the instances are gone. Note that `delete_namespace` later in the same function is routinely deferred while network deletes settle (`:705-719`), which is why this cannot be left to the namespace's own `hard_delete()`. Commit subject: `conductor: release capacity claims at teardown.` | Complete |
| 4 | medium | sonnet | worktree | (private-ci) Dashboard surface. Add claim outcomes and claim-size-versus-measured-peak to the conductor dashboard, following the patterns in `conductor/dashboard.py`, `conductor/web.py` and the existing templates. Queue-wait age is already present (`metrics.py:59`) and must not be duplicated. Keep it to what an operator would act on: how many claims were refused for capacity in the last day, and which jobs are claiming furthest from what they measured. Commit subject: `conductor: show what claims are doing.` | Complete |
| 5 | n/a | management session | none | Deploy and observe for at least seven days of normal CI load. Record in the Observations section below: claims created and deleted; capacity refusals per day and whether they correlate with cluster load; transient refusals; `conductor_claims_failed_total`, which must be zero; every `placement admitted over namespace capacity claim` audit event on the cluster with its dimension; the distribution of claim size against measured peak per `(repo, job_name)`; and whether the reconciler reported any drift in `cluster_capacity.claimed_*` that a leaked claim would explain. | Complete |
| 6 | medium | opus | worktree | Close-out, in this repository. Write the observation record into this plan, then answer phase 5's question explicitly: does the data support flipping `CLAIM_ENFORCEMENT_HARD`, and if not, what is still missing. If the anti-starvation question now has data behind it, say what the data says and leave the policy to its own phase. Update the master plan's phase table and `docs/plans/index.md`. Commit subject: `scheduler: record what conductor claims measured.` | Complete |

### Implementation notes (2026-09-01)

Steps 1 to 4 are written and land in private-ci on the branch
`scheduler-reservations-phase-04c-conductor-claims`, as four commits
carrying the subjects above. Complete here means the code exists and
its tests pass; it does not mean deployed, and step 5's window has not
opened. The private-ci tree had moved since the survey, so one
reference in the step 1 brief is stale and is corrected here rather
than in the brief: `get_cost_observations()` is at `db.py:1655`, not
`:1419`.

Four things were decided during implementation that the plan did not
settle. Each is recorded because a reviewer would otherwise have to
infer it from the diff.

**A capacity refusal breaks out of the (label, size) combination
rather than continuing within it.** E6 says "`continue` to the next
(label, size) combination", which in the loop as written are two
different statements: `continue` advances to the next runner of the
*same* combination. Break is the reading that matches the sentence and
the intent -- a 507 is a property of the cluster and this
combination's footprint, so the next runner of the same shape would be
refused identically, while a different shape may still fit, which is
why the outer loop carries on.

**An empty exception tuple is warned about at import.** The refusal
classes are looked up defensively, as `CLEANUP_EXCEPTIONS` already is,
because the 503 mapping only arrived in client-python#375. An empty
tuple is a valid `except` clause that never matches, so an older
client silently reclassifies that refusal into E6's third branch. For
a 503 that is a miscounted metric; for a 507 it is the loss of
back-pressure itself, with every refusal counted as a bug in the
conductor and the runner started anyway. That is too quiet to leave to
a counter, so it is said in the startup log.

**Claim outcomes are recorded in a new `claim_events` table.** The
dashboard item the step 4 brief asks for -- "how many claims were
refused for capacity in the last day" -- cannot be answered from a
Prometheus counter, which is cumulative and resets on each of the
several redeploys a day this conductor gets. This is an append-only
audit log and is not the local bookkeeping E7 rules out: it records
what happened to a request, never which claims exist, and nothing
reads it back to decide whether a claim needs deleting. Step 5's
observation record reads the same history.

**The dashboard shape was not agreed first.** The back brief calls
that out as cheap to propose and tedious to redo. It was built to the
step 4 brief's own words -- refusals in the last day, and the jobs
claiming furthest from what they measured -- following the existing
`sizing-section` panel, so it should be cheap to redo if the shape is
wrong.

### Back brief answers

1. **An over-large claim on a claim-free namespace answers 507**,
   raised as `InsufficientResourcesException`, per the 2026-08-27
   check the plan already records. That is what E6's first branch
   catches. The same request against a namespace which already holds
   a claim answers 409, because `exists` is evaluated first; the
   conductor claims on a namespace it has just created, so it never
   meets that path.

2. **The sizing formula was evaluated against real cost data on
   2026-09-08**, from the window's own `claim_events` rows rather
   than from `get_claim_sizes()` directly. This was **not** done
   before the deploy as this item asked: `ssh` to `maui` stayed
   refused for the development host's own keys until the session
   of 2026-09-08 was given `~/.ssh/id_ansible_ed25519`
   explicitly. The deploy therefore went ahead with the formula
   unchecked against real data, which was a real risk taken
   knowingly-by-omission rather than a judgement; it happened to
   be unfounded.

   Three real pairs, taken from the claims the conductor actually
   made:

   | `(repo, job_name)` | Claims | Measured peak | Claim written |
   |---|---|---|---|
   | `shakenfist` / Smoke tests (collection) | 237 | 20 cpu / 32768 MB / 620 GB | 24 / 39322 / 744 |
   | `kerbside` / direct-qemu-lane | 26 | 6 / 12288 / 360 | 8 / 14746 / 432 |
   | `library-utilities` / gitleaks | 25 | 2 / 4096 / 160 | 3 / 4916 / 192 |

   Every dimension is `ceil(1.2 x peak)`, and the `CI_SIZES` floor
   binds in none of the three: at the smoke-test scale the floor is
   irrelevant, and even at `gitleaks`' `s` (2 cpu / 4096 MB /
   100 GB) the peak term is larger on all three dimensions. The
   arithmetic is correct and the claims are not absurd -- the
   footprint asked for is a fifth again of what the job was
   measured using, which is what the formula promises.

   The one case where the floor does bind is a job with no
   observation yet, which is a job's first-ever run.
   `library-utilities / gitleaks` shows both: its first claim was
   the bare `s` floor of 2 / 4096 / 100, and every claim after it
   was the sized 3 / 4916 / 192. See the Observations section for
   why that floor turns out to be too small.

   What this check cannot see, and what the window did see, is
   that the peaks themselves under-state real use. The formula is
   sound; its input is not.

3. **A claim request that outlasts `SF_CALL_TIMEOUT` is E6's third
   branch**, and the namespace is **not** cleaned up. The conductor's
   client wrapper raises `sfclient.SFTimeout` (not the client's own
   `TimeoutException`), which is neither refusal class, so it is
   counted in `conductor_claims_failed_total` and the runner is
   provisioned with no claim -- the deliberate degradation.

   There is a consequence worth stating, because it is an argument for
   E7 that E7 does not make. The server may have created the claim
   before the client gave up, in which case a claim exists that the
   conductor believes failed. Because teardown lists the namespace's
   claims and deletes what it finds rather than deleting UUIDs it
   remembers, that orphan is still released at teardown. Nothing extra
   is needed, and the observable signature is
   `conductor_claims_failed_total` rising while claims appear on the
   cluster -- worth checking for during step 5's window.

## Risks and mitigations

**Claim creation refusals stop CI.** The realistic failure of
this phase. Claim creation is a hard admission even in the
advisory release, and finding 10 shows sfcbr already returns 507
under its own load. If every runner needs a claim and the cluster
is full, no runner starts -- which is arguably correct
back-pressure, but if the sizing is too generous it happens far
short of the cluster actually being full. Mitigated by E4's
1.2 headroom being small, by E6 leaving jobs queued rather than
failing them, and by step 5's observation window being the thing
that decides whether the sizing is right. Checked by the
management session against the refusal counter during the window;
if refusals correlate with anything other than genuine cluster
load, the phase pauses and the formula is revisited before phase
5 reads the data.

**Over-sized claims strangle the cluster quietly.** A claim holds
`cluster_capacity.claimed_*` whether or not the namespace uses
it, so a systematically over-sized claim reduces what everything
else -- including manual test clouds and the image builder -- can
be granted, without any single thing failing. Mitigated by the
claim-size-versus-measured-peak surface in step 4, which exists
precisely to make this visible, and by E5's expiry bounding the
damage from a leak.

**A leaked claim outlives its namespace.** Mitigated three ways:
explicit deletion at teardown (step 3), the six-hour expiry
(E5), and `Namespace.hard_delete()` deleting claims through the
object (finding 8). Checked in step 5 by looking for reconciler
drift.

**The client is out of step with the server.** Rewritten
2026-08-29: the conductor does *not* run a released
`shakenfist_client`. It tracks client-python's `develop` with
`state: latest`, so the skew it is exposed to runs in both
directions and changes shape.

It can be *behind* the branch, between a client merge and the
next conductor deploy -- which is exactly what step 0 checks,
and the reason that check reads the host rather than the
playbook. It can also be *ahead* of a server it talks to, or
carried somewhere unintended by a client change nobody meant for
it: `state: latest` re-pulls on every deploy, so the conductor
inherits whatever `develop` holds at that moment. This is not
hypothetical -- the branch tracking was introduced on 2026-07-12
*because* a released client was behind a server contract and
wedged the main loop overnight, and the fix traded one skew
direction for the other knowingly.

`CLEANUP_EXCEPTIONS` (`provisioner.py:170-179`) is the existing
local mitigation: it builds its tuple with
`getattr(apiclient, name) ... if hasattr(apiclient, name)`,
with a comment saying the newer classes are looked up
defensively because older clients "including the current PyPI
release" predate them. Step 2's refusal handling should follow
it for `ServiceUnavailableException` specifically, which is the
newest name of the three it needs and the one an older venv
would lack -- `APIException` and `InsufficientResourcesException`
have been in the client for years and can be named directly.
A module-level `except` clause built from a missing attribute
fails at import, which on this daemon means a crash-loop under
the systemd watchdog rather than a caught error.

Mitigated further by step 0's gate and by client-python's own CI
gating its `develop`.

**The observation window is quiet.** Seven days of CI might not
include the load pattern that produces interesting refusals. If
the window passes with no capacity refusals at all, that is
itself a finding -- it means the cluster has headroom the
scheduler was already exploiting -- but it does not calibrate the
ceiling. The management session extends the window rather than
declaring the phase done on a null result.

## Definition of done

Ticked against the record in Observations below, which covers the
full seven-day window from 2026-09-02 18:13:46 to 2026-09-09
18:13:46. An earlier version of this list marked four boxes
*(provisional)* against a five-day-23-hour window; all four have
been re-checked against the closed window and none of them
changed sign, though two of the findings behind them changed in
magnitude and are called out in Observations.

- [x] Phase 4b is complete and the deployed conductor's client
      carries claim methods and the 503 mapping. Step 0 verified
      the names resolve; the window then confirmed it in
      operation, since the `cannot signal` startup warning never
      fired across the seven days.
- [x] Every runner namespace created by the conductor holds a
      claim, or the reason it does not is counted in
      `conductor_claims_refused_total` or
      `conductor_claims_failed_total`. The only path that
      provisions a runner without a claim is E6's third branch,
      which has zero rows in `claim_events`; every other
      namespace either holds a claim or was torn down after a
      counted refusal.
- [x] `conductor_claims_failed_total` is zero across the
      observation window. Zero `failed` rows in `claim_events`
      across the entire table, which is the authoritative record
      rather than a counter that resets on redeploy. Zero
      `refused_transient` rows as well.
- [x] No namespace torn down during the window leaves a claim
      behind: the count of claims on the cluster returns to its
      pre-window baseline, and the reconciler reports no drift in
      `cluster_capacity.claimed_*`. Satisfied by a stronger check
      than the one named: at 2026-09-10 05:20, 2038 claims
      created minus 2033 released is exactly the 5 claims
      `sf-client namespace claim list` finds live on the cluster,
      all held by namespaces that still exist. The
      `cluster_capacity.claimed_*` half cannot be read as
      written -- it is a Prometheus gauge, not a log line -- and
      the ledger balancing against the cluster's own claim list
      answers the same question from ground truth. The seven drift
      corrections the reconciler did report are all in
      `scheduler_node_capacity.used_*`, not in `claimed_*`.
- [x] The observation record in this plan states, in numbers:
      claims created, capacity refusals per day, transient
      refusals, over-limit audit events by dimension, and the
      claim-size-to-measured-peak ratio per `(repo, job_name)`.
- [x] This plan answers, in one paragraph, whether phase 5 should
      flip `CLAIM_ENFORCEMENT_HARD` and on what evidence. The
      answer is no; see Observations.

### The item that did not work out as expected

The definition of done above originally carried a seventh item:
that no fact about claim sizing, expiry or refusal handling be
stated differently in this plan, in D18, and in the conductor's
own documentation. **It was written on a false premise.** The
conductor has no documentation of capacity claims at all --
`grep -rn "capacity claim"` across private-ci's `docs/`,
`AGENTS.md`, `ARCHITECTURE.md` and `README.md` returns nothing --
so there is no third statement of those facts to reconcile
against, and the item can be neither met nor failed as written.

It is recorded here rather than left as an unticked box, because
an unticked box reads as work outstanding in this phase and this
is not that. The phase is not blocked on it. Writing the
conductor's claims documentation is real work, but it belongs to
private-ci and to whichever plan next touches that code; the
successor plan `PLAN-claim-coverage-and-sizing.md` is the
natural home, and carries the note.

The general lesson is worth keeping: a consistency check across
three documents needs all three to exist, and this one was
written without checking that the third did.

## Observations

Collected 2026-09-08 and re-collected 2026-09-10 over the closed
window, from two sources which any later session can re-read: the
conductor's `claim_events` table on `maui`
(`/srv/shakenfist/private-ci/conductor.db`, queried from a copy so
the live file is untouched) and Loki, where the conductor logs
under `{job="conductor"}` and the sfcbr nodes ship SF's JSON logs
under `{job="syslog"}`.

**The window is now complete.** `claim_events`' first row is
2026-09-02 18:13:46 AEST, which pins the deploy, so seven days of
load closed at 2026-09-09 18:13:46. Everything below covers
exactly that interval. An earlier version of this section was
written on 2026-09-08 from a five-day-23-hour window; where the
full window changed a finding, the change is called out inline.
Four changed materially and one reading was withdrawn, so the
partial record should not be relied on where the two differ.

Two cautions for whoever re-runs this. Loki's own query log ships
under `{job="syslog"}` from `maui`, so a filter on the text of an
event matches the record of the previous session asking for it;
every query below excludes `caller=metrics.go`, `caller=engine.go`
and `maui loki[`. And the event *message* in `instance.py`
(`Placement admitted over the namespace capacity claim`) is worded
differently from the audit *event* it writes, so searching for the
event text returns nothing.

### Claims created and deleted

| | Window | At 2026-09-10 05:20 |
|---|---|---|
| created (`claim_events`) | 1934 | 2037 |
| created (Loki `Claimed`) | 1934 | 2038 |
| released (Loki `Released N capacity claim(s)`) | 1928 | 2033 |
| difference | 6 | 5 |
| claims live on the cluster | -- | 5 |

**No claim leaked.** The two independent sources agree exactly on
the window's creation count, which is the reason to trust either.
The ledger then balances against ground truth: 2038 created minus
2033 released is 5, and `sf-client namespace claim list` across
all nine namespaces finds exactly 5 live claims, every one held by
an `sfcbr-*` runner namespace that still exists.

This is the substance of the definition-of-done item about
`cluster_capacity.claimed_*`, which cannot be read as that item
was written: `scheduled_tasks.py` publishes `claimed_*` to a
Prometheus gauge rather than a log line, and no Prometheus is
reachable from the development host. Balancing the ledger against
the cluster's own claim list answers the same question from ground
truth, and is the better evidence.

Volume per day:

| Day | Created | Refused |
|---|---|---|
| 2026-09-02 (from 18:13) | 78 | 6 |
| 2026-09-03 | 323 | 107 |
| 2026-09-04 | 272 | 55 |
| 2026-09-05 | 313 | 71 |
| 2026-09-06 | 294 | 6 |
| 2026-09-07 | 269 | 35 |
| 2026-09-08 | 270 | 18 |
| 2026-09-09 (to 18:13) | 115 | 5 |

Volume is 269 to 323 on a full day and does not drop at the
weekend: 2026-09-05 (a Saturday) and 2026-09-06 (a Sunday) sit at
313 and 294, inside the weekday range. The conductor's load is
merge and schedule driven rather than keystroke driven, which is
worth knowing before reading any per-day number as a proxy for
human activity.

### Refusals

**303 capacity refusals, zero transient refusals, zero failures.**
`claim_events` holds only `created` and `refused_capacity` rows
across the entire table -- no `refused_transient` and no `failed` --
so `conductor_claims_failed_total` is zero across the window from
the authoritative record rather than by inference. The
`cannot signal` startup warning never fired, so the deployed client
carried both refusal classes throughout.

Refusals do **not** track claim volume: 2026-09-03 and 2026-09-05
have almost the same number of claims (323 and 313) and very
different refusal counts (107 and 71), while 2026-09-06 has 294
claims and only 6 refusals. What they track is contention between
a large request and what is already placed.

**Every one of the 303 refused on `cpus`**, none on memory or
disk, with a body of the shape
`cpus (limit 68, used 40, requested 39)`. Two things about that
body are worth recording, and neither was visible in the partial
window:

- The cluster's advertised `limit` ranged from **46 to 99 cpus**
  across the 303 refusals, with 68 the mode. sfcbr does not change
  size that much. This is the capacity total moving as nodes
  publish or fail to publish metrics, which is the surface
  [PLAN-transient-capacity-refusals.md](PLAN-transient-capacity-refusals.md)
  was opened against; a refusal issued against a 46-cpu view of an
  otherwise larger cluster is a refusal the claim did not deserve.
- The cluster was **not full** in the median refusal. Fullness at
  refusal (`used / limit`) has a minimum of 0.46, a median of
  **0.70** and a maximum of 0.94. A refusal is usually a large
  claim not fitting in the remaining third, not the last cpu being
  taken -- see the size distribution below.

Refusals concentrate in the jobs that build a nested cluster:

| Repo | Job | Refusals | Claim cpus |
|---|---|---|---|
| shakenfist | Debian 12 tier (collection) / Smoke tests (collection) | 66 | 24 |
| shakenfist | Ansible modules (collection) / Smoke tests (collection) | 49 | 39 |
| shakenfist | Guests (collection) / Smoke tests (collection) | 47 | 32 |
| shakenfist | Ubuntu 24.04 cluster (collection) / Smoke tests (collection) | 39 | 36 |
| shakenfist | Debian 12 cluster (collection) / Smoke tests (collection) | 26 | 39 |
| kerbside-patches | master debian 13 images on debian 12 all-in-one using debian containers | 16 | 17 |
| shakenfist | Node lifecycle (collection) | 16 | 39 |
| kerbside-patches | master debian 13 images on debian 12 all-in-one with no kerbside | 14 | 16 |
| kerbside | oVirt 4.5 on Rocky 8 | 7 | 22 |
| kerbside | OpenStack via Kolla-Ansible master on Debian 12 | 6 | 20 |

Eighteen `(repo, job_name)` pairs were refused at all, requesting
between 8 and 42 cpus. Measured against the cluster total in the
same refusal body, the request was more than **half** the cluster
in 54% of refusals and more than a **third** in 83%; the median
refused request is 52% of the cluster and the smallest is 11%.
So this half of the mechanism is largely working -- the
back-pressure lands on the jobs whose footprint genuinely does not
fit alongside what is already running -- but the 17% tail asking
for a third or less is where the varying `limit` above should be
suspected before contention is.

### Over-limit admissions

**234 `placement admitted over namespace capacity claim` events
across 101 namespaces.** The first is on 2026-09-04, two days
after the deploy; there are none on 2026-09-02 or 2026-09-03.

| Day | Events |
|---|---|
| 2026-09-04 | 23 |
| 2026-09-05 | 36 |
| 2026-09-06 | 39 |
| 2026-09-07 | 48 |
| 2026-09-08 | 58 |
| 2026-09-09 (to 18:13) | 30 |

*Changed by the full window.* The partial record said 167 events
across 76 namespaces, "23 to 48 a day and rising slightly". The
true count is 234 across 101, and the rise is steeper than
"slightly": 23 to 58 over five full days.

**The dimension distribution is where the partial window was
wrong.** It said over-limit admissions were "overwhelmingly
`memory_mb` ... and rarely `cpus` alone". Memory does dominate,
but disk is nearly as prevalent and cpus is involved in almost
half:

| Dimension | Exceeded in |
|---|---|
| `memory_mb` | 223 of 234 |
| `disk_gb` | 208 of 234 |
| `cpus` | 109 of 234 |

| Combination exceeded | Events |
|---|---|
| `disk_gb` + `memory_mb` | 99 |
| `cpus` + `disk_gb` + `memory_mb` | 98 |
| `memory_mb` alone | 15 |
| `disk_gb` alone | 11 |
| `cpus` + `memory_mb` | 11 |

`cpus` is never exceeded alone, which is the grain of truth in the
withdrawn claim, but "rarely `cpus`" was wrong: it is exceeded in
47% of events. The opposite-dimension reading still holds at the
level that matters -- refusals are 100% `cpus` at the *cluster*
scale while over-limit admissions are led by `memory_mb` and
`disk_gb` at the *namespace* scale -- but it is a difference of
emphasis, not the clean split the partial record described.

How far over, as `(used + requested) / limit`:

| Dimension | Median | Maximum |
|---|---|---|
| `memory_mb` | 1.46x | 14.50x |
| `disk_gb` | 1.43x | 33.60x |
| `cpus` | 1.08x | 18.00x |

All three maxima are the same namespace,
`sfcbr-3hZrDS5CTZ0QV1DG`, holding a 2 cpu / 4096 MB / 100 GB claim
while using 32 cpus, 55296 MB and 2960 GB. Its job is
`kerbside-patches / master debian 13 images on debian 13 multinode
using debian containers`, and it has no recorded peak at all: a
floor-only claim on a namespace that went on to build a multinode
cluster. See *Claim size against measured peak* below.

Joining the 101 namespaces back to their jobs. The eight largest
are below; six further jobs contribute one namespace each
(`client-python-k3s / agent context`, `ryll / Fuzz`, `instar /
Mermaid lint`, and three `kerbside` jobs):

| Repo | Job | Namespaces | Events | Claim (cpu/MB/GB) | Recorded peak |
|---|---|---|---|---|---|
| shakenfist | Smoke tests (collection) / Smoke tests (collection) | 63 | 159 | 24 / 39322 / 744 | 20 / 32768 / 620 |
| shakenfist | Debian 12 tier (collection) / Smoke tests (collection) | 11 | 11 | 24 / 63898 / 1128 | 20 / 53248 / 940 |
| kerbside | direct-qemu-lane | 6 | 6 | 8 / 14746 / 432 | 6 / 12288 / 360 |
| library-utilities | gitleaks | 3 | 10 | 3 / 4916 / 192 | 2 / 4096 / 160 |
| kerbside-patches | master debian 13 images on debian 13 (three variants) | 6 | 24 | 2 / 4096 / 100 | *none* |
| kerbside-patches | Lint shell scripts and workflows | 2 | 2 | 3 / 4916 / 192 | 2 / 4096 / 160 |
| actions | Unit tests | 2 | 2 | 3 / 4916 / 192 | 2 / 4096 / 160 |
| kerbside-patches | master debian 13 / ubuntu 24.04 images on debian 12 | 2 | 14 | 17 / 24576 / 672 | 14 / 20480 / 560 |

One job, `shakenfist / Smoke tests (collection)`, is 63 of the 101
namespaces and 159 of the 234 events.

### Claim size against measured peak

The 1.2 multiplier is applied exactly as designed. Across all 1924
sized claims in the window the memory ratio is **uniformly 1.20**;
the cpu ratios spread across 1.2, 1.214, 1.219, 1.222, 1.231,
1.25, 1.333, 1.5 and 2.0 purely because the ceiling is coarse on
small integers (a peak of 2 cpus becomes `ceil(2.4)` = 3, a ratio
of 1.5). No job's claim sits far from 1.2x its own measurement, so
the sizing formula is not mis-firing.

**Ten claims had no observation at all** and fell back to the
`CI_SIZES` floor -- `instar / Mermaid lint`, `kerbside /
Mermaid lint`, `kerbside / Sign release tag with Sigstore`,
`library-utilities / gitleaks` (its first run), `ryll / Fuzz`, and
five runs across three new `kerbside-patches / master debian 13
images on debian 13 ...` variants that appeared during the window.
**Eight of the ten went on to exceed the claim.** The partial
window saw five such claims and three exceedances, and read the
floor as "a real hole" that "matters only for a job's first-ever
run". The full window is worse than that: the three new
`debian 13 on debian 13` variants are *cluster* jobs, and each got
the 2 cpu / 4096 MB / 100 GB lint floor, then used up to 32 cpus.
A new large job's first run is not a rounding error -- it is
unclaimed capacity the size of a nested cloud.

### What the two dimensions together say

The 1.2 multiplier is not the problem. **The peaks feeding it
are**, and the full window shows two distinct ways they are wrong.

**The aggregate is under-measured.** `Smoke tests (collection)`
claims 39322 MB from a recorded peak of 32768, and the over-limit
events show the same job's namespace reaching 69632 MB -- the
recorded peak under-states real use by **2.12x**, not the 1.75x
the partial window suggested. Other jobs are worse: `sanity_checks`
3.00x, the `debian 12 all-in-one` image jobs 3.22x, and several
small jobs 5.00x. No plausible headroom multiplier applied to a
peak that wrong would have covered it.

**In a quarter of cases the claim does not cover a single
instance.** In **24 of the 101 namespaces**, at least one
exceedance had `requested > limit` on its own -- one instance
larger than the entire claim, before any other instance in the
namespace is counted. The clearest shape is a claim of 3 cpus /
4916 MB / 192 GB, sized from a recorded peak of 2 / 4096 / 160,
against which the conductor then requests a 12 cpu / 16384 MB /
400 GB runner. The recorded peak is smaller than the runner the
conductor itself creates.

These are two failure modes, not one. The dominant job
(`Smoke tests`, 63 namespaces) is in the first group only: its
claim covers each instance but not their sum. The small jobs are
in the second: no multiplier on that peak can ever be enough,
because the measurement is not of the thing being sized.

Both are upstream of the formula, in what `get_claim_sizes()`
reads. That is the same surface
`PLAN-claim-coverage-and-sizing.md` was opened against in
private-ci on 2026-09-08, and its sixth finding -- that
`collect_namespace_costs()` cannot see instances hard-deleted
before teardown -- is the mechanism for the first mode. The second
mode, a peak smaller than one runner, is additional evidence for
the same plan and is recorded there.

### Reconciler

**Seven drift corrections**, in two episodes of different
character. The partial window saw only the first and read it as
the whole story.

| When | Node | delta cpus / MB / GB |
|---|---|---|
| 2026-09-05 12:05:42 | `f6b7e913` | -4 / -12288 / -160 |
| 2026-09-05 12:05:42 | `7ce66641` | -4 / -12288 / -160 |
| 2026-09-05 12:53:58 | `963d4df9` | -4 / -12288 / -160 |
| 2026-09-05 21:26:13 | `963d4df9` | +4 / +12288 / +160 |
| 2026-09-09 08:07:13 | `6046afdf` | -6 / -8192 / -320 |
| 2026-09-09 08:07:13 | `963d4df9` | -4 / -12288 / -160 |
| 2026-09-09 08:07:13 | `f6b7e913` | -12 / -36864 / -480 |

The 2026-09-05 four are uniform `4 / 12288 / 160` steps, including
a matched `-` then `+` pair on `963d4df9`: one instance's
footprint counted across a pass boundary, not a lost update.

The 2026-09-09 three are not that. They land in the same
millisecond on three different nodes and they are all negative,
so nothing cancels. Two are whole multiples of the same
`4 / 12288 / 160` unit -- one instance on `963d4df9`, three on
`f6b7e913` -- but `6046afdf`'s `-6 / -8192 / -320` is not a
multiple of it in any dimension, so at least one hypervisor was
over-counting something other than a whole number of the standard
runner. The ledger was over-counting on three hypervisors at once
and the reconciler took it back in a single pass. Over-counting is
the direction that produces refusals the cluster did not deserve,
which connects this to the varying `limit` seen in the refusal
bodies above.

*Withdrawn:* the partial record's "four drift corrections, all on
2026-09-05, each exactly +/-4 cpus / 12288 MB / 160 GB and
appearing as matched `-` then `+` pairs" is true of the first
episode only. Neither episode is evidence of a leaked claim --
these are `scheduler_node_capacity.used_*` counters, not
`claimed_*` -- and the live claim count above is the direct check.

The `Failed to release claims` warnings number **41, across 37
namespaces**, and all are the same benign shape: a 404
`namespace not found` on the GET that precedes the delete, because
the namespace was already gone. A wasted call, not a leaked claim.

### Does phase 5 flip `CLAIM_ENFORCEMENT_HARD`?

**No, not on this data**, and the full window strengthens the
answer rather than softening it.

Hard enforcement across this window would have refused 234
placements spread over 101 namespaces -- 40% more than the partial
window projected -- and the reason would have been wrong in nearly
every case. The claims those placements exceeded were sized from
peak measurements that under-state real use by 2.12x to 5x, and in
24 namespaces by enough that a single instance did not fit. That
is a measurement defect, not a namespace consuming more than it
asked for. Turning the ceiling hard would convert it into CI
outages, and would do it first and hardest to
`shakenfist / Smoke tests (collection)`, which is the project's own
functional suite and 63 of the 101 affected namespaces.

The advisory release is doing exactly what it was built to do: it
found the defect without breaking anything, and it found a second
one -- the `CI_SIZES` floor -- that eight of ten first runs walked
straight through.

What is still missing before phase 5 can proceed:

1. A claim sizing input that tracks real use, so that
   `get_claim_sizes()` reads a peak reflecting what a job
   actually allocates concurrently.
2. A `CI_SIZES` floor that covers a first run of a *cluster* job,
   not just a lint job.
3. A re-run of this measurement afterwards. The number to watch is
   over-limit admissions per day, which must fall to a residue
   attributable to genuine over-consumption before the ceiling is
   worth making hard.

The refusal side needs no such wait. 303 refusals, all on `cpus`,
the median of them asking for 52% of the cluster, is broadly the
mechanism behaving correctly and is not an argument against
enforcement. Two caveats belong on it: 17% of the refusals were
for a third of the cluster or less, and the cluster's advertised
capacity varied between 46 and 99 cpus during the window while the
median refusal happened at only 70% fullness. Some fraction of the
303 is therefore the warm-up artefact
[PLAN-transient-capacity-refusals.md](PLAN-transient-capacity-refusals.md)
addresses rather than genuine contention. That plan should land
before this measurement is re-run, or the re-run will not be able
to tell the two apart either.

The anti-starvation question (D18's 15-minute rule) now has data
behind it: the jobs refused are a small, stable set of eighteen
large-footprint pairs, and they are refused repeatedly --
`Debian 12 tier (collection)` 66 times in seven days. Whether that
constitutes starvation depends on whether those jobs eventually
ran, which this record does not establish, so the policy stays
with its own phase.

## Future work

- **Anti-starvation policy** (D18's 15-minute rule). The window
  has closed and did **not** settle this. It established the
  population -- eighteen `(repo, job_name)` pairs were refused at
  all, and refusals concentrate on a stable handful of
  large-footprint cluster jobs, `Debian 12 tier (collection)` 66
  times in seven days -- but not the outcome, because neither
  `claim_events` nor the SF audit events record whether a refused
  job went on to run. Answering it needs a retry-outcome link the
  conductor does not currently write; that belongs to the policy's
  own phase, and is the first thing it should add.
- **Claims for `ci-images` and the static runners**, so that the
  conductor's whole footprint is claimed rather than just its
  runners. Confirmed still outstanding at window close: of the
  nine namespaces on sfcbr, `ci-images`, `static-ci`,
  `static-runners` and `system` hold no claim.
- **Delegated claim creation**, so a namespace could hold a claim
  without a cluster administrator making it. Named as future work
  by D15 and unchanged by this phase.
- **Publishing private-ci**, at which point this plan's E1
  deviation stops being a trade-off and the implementation PR
  becomes readable from the master plan.

## Back brief

Before implementation starts, the implementing session states
back to the management session:

1. Which status code an over-large claim on a claim-free
   namespace actually returns, checked against a real cluster,
   and therefore which exception class E6's first branch catches.
2. The sizing formula as it will be written, evaluated against
   three real `(repo, job_name)` pairs from the current cost
   data, with the resulting claim compared to the runner
   footprint and to the measured peak.
3. What happens to a runner whose claim request times out at
   `SF_CALL_TIMEOUT` -- which of E6's branches that is, and
   whether the namespace it just created is cleaned up.

Step 4's dashboard shape is worth agreeing before it is built,
being cheap to propose and tedious to redo.
