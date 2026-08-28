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
| 0 | low | sonnet | none | Prerequisite gate, **rewritten 2026-08-29**. Phase 4b must have merged its verbs and the 503 mapping in `STATUS_CODES_TO_ERRORS` (finding 3) to client-python's `develop` -- which it did on 2026-08-28 as `135ab53`. It does **not** need a release: the conductor pip-installs `git+https://github.com/shakenfist/client-python@develop` with `state: latest` and has since 2026-07-12, per phase 4b's finding 8 and decision D7. So the gate is a deploy, not a tag. Verify against the host rather than the playbook, because `state: latest` only re-pulls when the playbook runs: `/srv/shakenfist/private-ci/venv/bin/python -c 'import shakenfist_client.apiclient as a; print(a.Client.create_namespace_claim, a.ServiceUnavailableException)'`. Both names must resolve. The conductor runs on `maui`, so this is `ssh maui` and the command above. If either name does not resolve, the conductor has not been deployed since 2026-08-28 and running `conductor.yml` (`manage.yml`, tag `conductor`) is what opens this phase -- a deploy makes the gate true whatever it says now, so running the playbook is a valid substitute for checking it. Phase 4b's close-out on 2026-08-29 also carries the "deployed venv resolves the verbs" item that used to sit in its own definition of done: it was moved here, because a conductor deploy is this phase's entry gate and not 4b's deliverable. That close-out attempted the check and could not make it -- `ssh` to `maui` is refused from the development host for both `ansible@` and the default user -- so it is genuinely open rather than assumed. Record the version and the date checked here. | Not started -- the gate has not been evaluated; see the brief |
| 1 | medium | sonnet | worktree | (private-ci) Sizing accessor. Add `get_claim_sizes()` to `conductor/db.py` beside `get_cost_observations()` (`:1419`), returning the worst observed `peak_allocated_cpus`, `peak_allocated_ram_mb` and `peak_allocated_disk_gb` grouped by `(repo, job_name)` with no minimum run count, from the same summary table that function reads. Read that function first: the peaks it exposes are already a MAX over summary rows, so this is a re-grouping of the same data, not a new measurement. Write the docstring to say why the key and threshold differ from its sibling -- claims cover the whole namespace and peaks are topology-deterministic, so one observation seeds a claim, whereas a runner-size recommendation needs three runs before it changes anything. Unit tests beside `conductor/tests/test_provisioner_costs.py`'s existing coverage. Commit subject: `conductor: size claims from observed peaks.` | Not started |
| 2 | high | opus | worktree | (private-ci) Claim creation and refusal handling in `conductor/provisioner.py`, per Design and E3/E4/E6. Add the sizing helper (max of `CI_SIZES[ci_size]` and 1.2x the step 1 peaks, ceiling per dimension), the claim request in `create_workers()` between `add_namespace_trust()` and `allocate_network()`, and the three-branch refusal handling. This was checked against sfcbr on 2026-08-27 during phase 4b and needs no re-checking: an over-large claim on a claim-free namespace answers **507**, raised as `InsufficientResourcesException`, with a per-dimension body naming the limit, the current usage and the request. The same request against a namespace which already holds a claim answers 409, because `exists` is evaluated first -- which is what the phase 4a soak recorded and why its "impossible claim" line said 409. The conductor claims on a namespace it has just created, so 507 is the case E6's first branch must catch. Add the metrics from Design. The namespace teardown on refusal must not be able to raise past the loop. Tests: the refusal branches, the sizing floor when no observation exists, and that a refused combination leaves `requested` unchanged. Commit subject: `conductor: claim capacity before starting a runner.` | Not started |
| 3 | medium | sonnet | worktree | (private-ci) Claim deletion in `remove_namespace()` (`provisioner.py:606`), per Design: list the namespace's claims through the client, delete each, count them, catch as broadly as the neighbouring steps and never raise. Place it after `collect_namespace_costs()` and before `delete_all_instances()`, and comment why that position rather than after the instances are gone. Note that `delete_namespace` later in the same function is routinely deferred while network deletes settle (`:705-719`), which is why this cannot be left to the namespace's own `hard_delete()`. Commit subject: `conductor: release capacity claims at teardown.` | Not started |
| 4 | medium | sonnet | worktree | (private-ci) Dashboard surface. Add claim outcomes and claim-size-versus-measured-peak to the conductor dashboard, following the patterns in `conductor/dashboard.py`, `conductor/web.py` and the existing templates. Queue-wait age is already present (`metrics.py:59`) and must not be duplicated. Keep it to what an operator would act on: how many claims were refused for capacity in the last day, and which jobs are claiming furthest from what they measured. Commit subject: `conductor: show what claims are doing.` | Not started |
| 5 | n/a | management session | none | Deploy and observe for at least seven days of normal CI load. Record in the Observations section below: claims created and deleted; capacity refusals per day and whether they correlate with cluster load; transient refusals; `conductor_claims_failed_total`, which must be zero; every `placement admitted over namespace capacity claim` audit event on the cluster with its dimension; the distribution of claim size against measured peak per `(repo, job_name)`; and whether the reconciler reported any drift in `cluster_capacity.claimed_*` that a leaked claim would explain. | Not started |
| 6 | medium | opus | worktree | Close-out, in this repository. Write the observation record into this plan, then answer phase 5's question explicitly: does the data support flipping `CLAIM_ENFORCEMENT_HARD`, and if not, what is still missing. If the anti-starvation question now has data behind it, say what the data says and leave the policy to its own phase. Update the master plan's phase table and `docs/plans/index.md`. Commit subject: `scheduler: record what conductor claims measured.` | Not started |

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

- [ ] Phase 4b is complete and the deployed conductor's client
      carries claim methods and the 503 mapping.
- [ ] Every runner namespace created by the conductor holds a
      claim, or the reason it does not is counted in
      `conductor_claims_refused_total` or
      `conductor_claims_failed_total`.
- [ ] `conductor_claims_failed_total` is zero across the
      observation window.
- [ ] No namespace torn down during the window leaves a claim
      behind: the count of claims on the cluster returns to its
      pre-window baseline, and the reconciler reports no drift in
      `cluster_capacity.claimed_*`.
- [ ] The observation record in this plan states, in numbers:
      claims created, capacity refusals per day, transient
      refusals, over-limit audit events by dimension, and the
      claim-size-to-measured-peak ratio per `(repo, job_name)`.
- [ ] This plan answers, in one paragraph, whether phase 5 should
      flip `CLAIM_ENFORCEMENT_HARD` and on what evidence.
- [ ] No fact about claim sizing, expiry or refusal handling is
      stated differently in this plan, in D18, and in the
      conductor's own documentation.

## Observations

*To be written by step 5. This section is a deliverable, not a
formality: phase 5 reads it.*

## Future work

- **Anti-starvation policy** (D18's 15-minute rule), once this
  phase's window shows whether large-claim jobs actually starve.
- **Claims for `ci-images` and the static runners**, so that the
  conductor's whole footprint is claimed rather than just its
  runners.
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
