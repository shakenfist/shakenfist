# Phase 1: headroom instrumentation

Parent plan: [PLAN-ci-cloud-sizing.md](PLAN-ci-cloud-sizing.md).

**Planning effort:** medium, as the master plan specifies. The
arithmetic was settled in phase 0. What keeps this above mechanical
is that the work is cross-repository -- part of it lands in
`shakenfist/actions`, where a change cannot be exercised before it
merges -- and that an instrument which measures the wrong thing is
worse than no instrument, because phase 2 would then reshape the
topologies against it.

Decision numbering continues the plan-wide sequence: phase 0 used
D1-D8, so this phase begins at D9. There is one namespace across
the whole plan, because later phases cite these by number.

## Context

Phase 0 decided what to measure. D3 fixed the *form* of the headroom
band, D5 made memory a genuine second dimension and required refusals
to be counted per stage rather than in aggregate, and D7 made the
unresolved 12-versus-10 ledger discrepancy something this phase's
series has to make answerable by recording both figures rather than
choosing one.

This phase builds the instrument and nothing else. It changes no
topology, gates no job and fails no build. Its output is a per-run
artifact and a printed summary, so that phase 2 has a distribution
to reason about instead of the three hand-collected data points the
master plan was written from.

## Scope

**In scope:**

* A headroom series: `GET /admin/resources` sampled through the
  functional-test step of every cluster job, written as JSONL.
* A refusal census: a count, per scheduler stage, of every candidate
  node dropped during the run -- including on runs that pass.
* A per-run summary computing D3's band form, printed to the job log.
* Publishing the capacity row's `limit_cpus` from
  `summarize_resources()`, which D7 requires and the endpoint does
  not currently expose (D12).
* Documenting the artifact's format, so phase 2 parses a contract
  rather than reverse-engineering a file.

**Out of scope:**

* Any change to a topology file, node count, or node size. That is
  phase 4, and it is gated on phase 3.
* Any gating, threshold enforcement or build failure. Phase 5 owns
  the band as a guardrail; here it is only computed and printed
  (D15).
* Fixing the scheduler defects the census will count. #3565, #3496
  and #3772 stay where phase 0 put them.
* `slim-primary` keeps its unreachable `sf-absent` hypervisor. This
  phase does not touch topology files at all, but D6 requires every
  phase plan to restate it: the phantom is the regression guard for
  the 2026-07-20 absent-node deploy failure and must not be
  "tidied". It never registers as a node, so it will never appear
  in the series -- but it *is* in `ci-inventory.yaml` and
  `ci-topology-facts.json`, so anything iterating those must
  tolerate it.

## What the survey found

Six findings. Three contradict claims this phase inherited, and they
are corrected at source in the planning commit -- in the master
plan's phase 1 section and in phase 0's D3 and D5 -- so the next
reader does not trip over them. Re-check those rather than redoing
them.

**1. Refusals are recorded on every schedule, not only on failures.**
The master plan says the scheduler's per-candidate audit payload
"only reaches the log when a test fails and attaches it, so a green
run tells you nothing". That is wrong.
`Scheduler._log_and_raise_on_error()` (`shakenfist/scheduler.py:348-364`)
writes an audit event at *every* stage, whether or not candidates
survived: `schedule at stage {stage}` when they did, `schedule has
no candidates at stage {stage}, aborting` when they did not, both
carrying `extra['dropped']` as a map of node UUID to a reason dict.
A passing run therefore records its refusals in full. This is the
single fact that keeps the whole phase client-side; without it, D3's
"a refusal in an otherwise-green job is itself a warning" would have
needed a new server-side counter.

**2. There are four capacity stages, and phase 0 named the wrong
one as disk.** The stage strings are bare literals at their call
sites, with no enumeration anywhere in the tree. In order:
`pre_schedule` (`:431`), `is_hypervisor` (`:440`),
`cpu_max_per_instance` (`:454`), `sufficient_idle_cpu` (`:469`),
`sufficient_idle_memory` (`:480`), `sufficient_free_disk` (`:491`),
`queue_state` (`:590`), `sufficient_idle_disk` (`:600`).

D3 and D5 name the refusal set as `sufficient_idle_cpu`,
`sufficient_idle_memory` and `sufficient_idle_disk`, treating the
last as "disk". It is not. `sufficient_idle_disk` wraps
`_has_idle_disk_bandwidth()` (`:329`), a *rate* predicate on
disk-busy delta against an inline threshold of 1200 -- which phase 0
had already established in its inventory row, and then contradicted
in its own decisions. The stage that means "the cluster ran out of
disk" is `sufficient_free_disk`, and it appears nowhere in the plan.
D10 below settles how the census handles this.

**3. `summarize_resources()` cannot answer D7.** The CPU filter
compares against the capacity row's `limit_cpus`
(`_has_sufficient_cpu`, `:187-253`); the endpoint publishes only the
live derivation `cpu_hard_max` (`:765`) and a
`cpu_committed_row_present` boolean (`:760`). Those are precisely
the two figures D7 asks phase 2 to reconcile, and only one is
available. The refusal events *do* carry both in the same reason
dict, so the reconciliation is possible whenever a refusal happens
-- but not on a quiet run. D12 closes this.

**4. Three admission inputs are not published at all.** The disk
bandwidth stage's metric and threshold are absent, so its headroom
cannot be reconstructed from the endpoint even in principle. The
queue-length stage is likewise invisible, and worse:
`summarize_resources()` silently *omits* nodes over
`UNREASONABLE_QUEUE_LENGTH` from `per_node` (`:736-738`), alongside
non-hypervisors and nodes whose metrics are older than 120s. A node
absent from a sample has four possible meanings and the sample does
not say which. The series must therefore record cluster membership
separately, from `GET /nodes`, exactly as
`cluster_ci_tests/test_nodes.py:49-85` already does.

**5. The existing Loki bundle dump cannot serve as the refusal
source.** `actions/ansible/ci-gather-logs-loki.yml:81-92` already
dumps Loki into every bundle, which looked at first like the census
being free. It is not: the query is an unfiltered
`{job="shakenfist"}` with `limit: 5000` and `direction=forward`
over a 21600-second window (`:40-41`), so it returns the first 5000
lines of the *deploy* and stops long before the tests run. The
census needs its own query, filtered to the scheduler events, where
5000 lines is a generous budget rather than an immediate truncation.

**6. Prior art exists for every piece, and the survey found no
existing sampling to reconcile with.** `tools/ci_wait_schedulable.py`
is the working `get_cluster_resources()` client and establishes the
credential and invocation pattern; `tools/queue-wait-report.py` is a
log-stream summariser with a `percentile()` helper at `:289` and a
docstring explaining that `eventlog.add_event_multi` echoes every
event to the log stream (gated by `LOG_EVENTS_TO_LOKI`, default
`True`); `tests/test_ci_claims_headroom.py` is the pattern for
unit-testing CI tooling by loading it by path; and
`cluster_ci_tests/test_scheduler.py:19-34` already reads scheduler
stage events back and filters them by message prefix. Nothing in CI
samples resources today -- this is greenfield.

## Decision items

### D9 -- Two instruments, not one

**Decision:** the headroom series and the refusal census are separate
mechanisms with separate sources, built and reported separately.

**Reasoning:** they cannot substitute for each other and it is
tempting to pretend otherwise. A 15-second poll cannot see a refusal,
which is a point-in-time event that begins and ends between samples;
this is exactly why D3 made "any refusal at all" a warning
independent of the ratio. Conversely the census cannot see a cloud
sitting half-empty for an hour, which is the oversizing case the
whole plan exists to detect. Reporting one number derived from both
would hide which of the two produced it.

### D10 -- The census counts every stage it sees, and the plan's naming is fixed

**Decision:** the census tallies whatever stage strings appear in the
events, reporting all of them, with the four capacity stages
(`sufficient_idle_cpu`, `sufficient_idle_memory`,
`sufficient_free_disk`, `sufficient_idle_disk`) called out
explicitly. D3 and D5 are corrected at source to name four stages
rather than three. The memory stage's three distinct reasons are
reported separately, not summed.

**Reasoning:** the stage names are bare literals with no
enumeration, so any hardcoded list in a parser is a copy that will
drift silently the moment a stage is added or renamed -- and survey
finding 2 shows the plan had *already* drifted before a line of code
was written. Tallying by observed string costs nothing and cannot
fall out of date. Splitting the memory reasons matters because one
of the three is `no memory_max in node metrics`
(`scheduler.py:277-284`), which is missing data, not a shortage; a
census that counted it as a memory refusal would read a stale
metrics row as evidence that the cloud is too small, which is the
precise error this plan exists to avoid making.

### D11 -- The census reads Loki, not the events API

**Decision:** refusals are captured by a dedicated, filtered LogQL
query against the cluster's Loki at the end of the run, not by
enumerating instances over REST.

**Reasoning:** the REST events routes are per-object, take only
`limit` and `event_type`, and have no time filter, so a cluster-wide
"what happened between T1 and T2" is not expressible. Worse, both
507 paths call `enqueue_delete_due_error()`
(`external_api/instance.py:876`, `:945`), so the instances whose
events matter most are not in the live instance list, and
`hard_delete()` drops their events an hour later
(`baseobject.py:702-706`, `CLEANER_DELAY` 3600s). Loki is
time-ranged, cluster-wide, and already deployed in CI. The cost is
that the census depends on the log-shipping path being healthy,
which is recorded as a risk below.

### D12 -- Publish the capacity row's limit from `summarize_resources()`

**Decision:** add the capacity row's `limit_cpus` to the per-node
payload, beside the existing `cpu_hard_max`, and extend the two
tests that pin that payload.

**Reasoning:** D7 instructs this phase to record both ledgers so
phase 2 can reconcile them, and survey finding 3 shows only one is
available. Deriving the second in the probe is not an option: a
second independently-derived ledger beside the real one is how the
two came to disagree in the first place, and the endpoint's own
source comment says as much (`scheduler.py:707-712`). Reading it
from refusal events works only when something is refused, which on
a healthy run is never.

**The counter-argument, for the record:** this is a server-side
change made for CI's benefit, and this phase's scope says the work
is client-side. The answer is that the row's limit is what admission
actually compares against, so an operator reading `/admin/resources`
to understand a 507 needs it for the same reason phase 2 does;
publishing only the derived twin of the real ledger, while
admission uses the real one, is the gap that let a 12-versus-10
discrepancy sit unexplained. It is three lines with existing tests
to extend, and it is inherited scope rather than new ambition.

### D13 -- The poller runs on the primary, and is self-limiting

**Decision:** the poller runs as a background process on the cluster
primary, writing to `/srv/ci/traces/`, with its own maximum duration
derived from the job's `test_timeout_minutes` input.

**Reasoning:** every API call in CI today goes to
`http://localhost:13000` over ssh, and `/etc/sf/sfrc` is already
made world-readable on the primary by
`build-smoke-cluster/action.yml:217-218`, so this needs no new
secret anywhere. `/srv/ci/traces` is already created and chowned
(`smoke-cluster.yml:182-187`) and already collected into the bundle
(`:471-474`), so the artifact path costs nothing.

Self-limiting is not optional: `smoke-cluster.yml:135` sets
`cancel-in-progress: true`, and a cancelled job runs no further
steps, so the stop step is not guaranteed to execute. Nothing tears
the cluster down either -- the under-cloud reaper collects it later
-- so a poller without its own cap would spin on a leaked VM.

**Rejected alternative:** poll from the runner against
`http://${primary}:13000`, which the API does listen on. It is
mechanically simpler -- an ordinary background process, no
backgrounding across ssh -- but it requires copying the cluster's
system key onto the runner, where today only the *under-cloud*
credential lives (`functional-tests.yml:583-586`), and it diverges
from every other API call in CI. The simplicity is not worth the
new credential path.

### D14 -- Both halves of the format contract live in the main repository

**Decision:** the poller and the analyser both live in the main
repository's `tools/`. `shakenfist/actions` gets a thin launcher
carrying no logic, plus the workflow steps.

**Reasoning:** the poller writes a format and the analyser reads it,
and a format contract split across two repositories can only be
changed by coordinated merges. That would be tolerable if both sides
were equally testable, and they are not: a pull request against
`actions` cannot exercise its own composite-action change, because
every consumer pins `@main` and only the post-merge canary runs the
workflow. Keeping both halves in the main repository means the
contract is covered by unit tests that run on an ordinary pull
request, and shrinks the untestable surface to a launcher with no
decisions in it.

**The cost, for the record:** `actions/tools/` is already copied
wholesale to the primary (`build-smoke-cluster/action.yml:91-104`),
so putting the poller there would need no delivery step at all,
where this decision needs one. That is a real convenience being
traded away for testability, and a reviewer could reasonably prefer
the other side of it.

### D15 -- Nothing in this phase can fail a build

**Decision:** every step added to a workflow is `continue-on-error`,
and the analyser exits zero whatever it finds. The band is computed
and printed, never enforced.

**Reasoning:** the point of the phase is to observe CI's failure
surface, and an instrument that can itself fail the job changes the
thing being measured. It would also be measured *during* the
baseline window, so phase 2's distribution would contain failures
caused by the measurement. Phase 5 owns turning the band into a
guardrail, once phase 2 has said what the numbers should be.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Publish the capacity row's limit from `Scheduler.summarize_resources()` in `shakenfist/scheduler.py` (the per-node block begins at `:744`). The row is already in hand: `_capacity_by_node()` is read once at `:713` and `cpu_committed`/`cpu_committed_row_present` are set from it at `:760-767`. Add `cpu_limit` from the row's `limit_cpus`, and set it to `None` when no row exists -- do **not** fall back to `cpu_hard_max`, because the whole point is to let a reader see the two disagree. Extend `test_summarize_resources_publishes_the_counters` (`shakenfist/tests/test_scheduler.py:749`) and `test_summarize_resources_reports_a_node_with_no_row` (`:763`) to pin both the present and absent cases; `mock_mariadb.set_node_capacity(node_uuid, limit_cpus=...)` (`shakenfist/tests/mock_mariadb.py:2733`) seeds a row, and no node has one by default. Update the endpoint's swagger example in `shakenfist/external_api/admin.py` (`admin_resources_get_example`, above `:103`) to match, or the API spec test will disagree with what the server sends. Document the new field in `docs/developer_guide/api_reference/` if `/admin/resources` has a page there. |
| 1b | medium | sonnet | none | Write `tools/ci_headroom_probe.py` in the main repository: a background poller that appends one JSON object per sample to a file given on the command line. Model it closely on `tools/ci_wait_schedulable.py` -- same credential handling (`SHAKENFIST_NAMESPACE`, `SHAKENFIST_KEY`, `SHAKENFIST_API_URL` from a sourced `/etc/sf/sfrc`), same `shakenfist_client.apiclient.Client`, same run-under-the-SF-venv assumption; copy its docstring style, which explains *why* the tool exists. Each sample records: a wall-clock timestamp, the whole `/admin/resources` payload verbatim, and the node roster from `GET /nodes` (`client.get_nodes()`) reduced to uuid, fqdn and the three role booleans. Recording the roster every sample rather than once is deliberate -- survey finding 4 -- because a node vanishing from `per_node` has four possible meanings and only the roster distinguishes "not a hypervisor" from "went stale". Take `--interval` (default 15) and `--max-seconds` (required, no default) arguments; exit cleanly when the cap is reached, because a cancelled job never runs the stop step (D13). Never raise: a failed sample writes a record with an `error` key and the loop continues, since the probe must not be the reason a run has no data. Flush after every line so a killed poller still leaves a readable file. Single quotes, 120 columns, `# Copyright 2019 Michael Still and contributors` header. |
| 1c | high | opus | none | Write `tools/ci_headroom_report.py` in the main repository and its unit tests. It takes the JSONL series from 1b and the Loki census JSON from 1d and prints a summary; it must run on the *runner* under stock `python3` with no third-party imports, so keep it to the standard library. Compute, per job: p90 and peak of committed vCPU cluster-wide and per node, both absolute and as a fraction of ledger, the same for memory, and the D3 band verdict against the provisional bounds 0.35 and 0.70 (phase 0 D3 -- state them as provisional in the output, because phase 2 replaces them). Reuse the `percentile()` implementation from `tools/queue-wait-report.py:289` rather than writing a second one. Three things need judgement and are the reason this step is opus. First, ledger: use the per-node `cpu_limit` added in 1a where present and `cpu_hard_max` where not, and report how many samples fell back, since a run that is entirely fallback tells phase 2 something about D7. Second, node absence: a node in the roster but missing from `per_node` must be classified using the roster (non-hypervisor) and, where it cannot be, reported as unexplained rather than silently dropped -- an all-false `cpu_committed_row_present` across every node means the ledger was unreadable, not that the cluster was idle (`_capacity_by_node()` swallows read failures at `scheduler.py:157-164`). Third, the census: tally by observed stage string, never a hardcoded list (D10), report the four capacity stages explicitly, and split the memory stage's three reasons so that `no memory_max in node metrics` is never counted as a shortage. Exit zero always (D15). Unit tests follow `shakenfist/tests/test_ci_claims_headroom.py`: load the tool by path with `importlib.util.spec_from_file_location` and drive it with fixture series covering an empty file, a truncated final line, a sample carrying `error`, a node absent from `per_node`, and a census with an unknown stage string. |
| 1d | high | opus | none | Wire it up in `shakenfist/actions`. Add `tools/ci_headroom_launch.sh` and `tools/ci_headroom_collect.sh` carrying no analysis logic, and three steps to `.github/workflows/smoke-cluster.yml`: start the poller between `Authorise the primary to reach other nodes over the mesh` (`:195`) and `Run functional tests` (`:237`); after `List slowest tests` (`:316`) and before `Check sf-api drain` (`:391`), stop it, run the Loki census, and print the report. Every added step is `if: always()` on the stop side and `continue-on-error: true` throughout (D15). The launcher scp's `${GITHUB_WORKSPACE}/shakenfist/tools/ci_headroom_probe.py` to the primary and starts it under `nohup` with **all three** file descriptors redirected -- without that the starting ssh hangs waiting for EOF -- passing `--max-seconds` derived from the workflow's `test_timeout_minutes` input, not a constant, since callers pass 45 through 70 (`functional-tests.yml:438-472`). The census is a `query_range` call filtered to `{job="shakenfist"} |= "Added event" |= "schedule at stage"`; do not reuse the existing bundle dump, which is unfiltered with `limit: 5000` and `direction=forward` and never reaches the test window (survey finding 5). Resolve Loki's address the way the gather playbook does rather than assuming `localhost:3100`, which is correct only on the single-node smoke topology. Write both outputs into `/srv/ci/traces/` on the primary so the existing `Gather logs` step collects them (`:471-474`); no new `upload-artifact` step is needed. Observe the repository's conventions: at most about five lines of inline shell in a workflow step, `shellcheck --severity=error` with no new findings at any level, a minimal `permissions:` block, and `tests/test_workflow_references.py`, which requires that any `tools/` script a workflow names exists in the same commit. Be aware that this change cannot be exercised by its own pull request and lands untested; write it to be inert on failure. |
| 1e | medium | sonnet | none | Document the artifact. Add a section to `docs/developer_guide/ci.md` describing what the headroom series and refusal census are, where they land in the bundle, the JSONL record format as a contract phase 2 will parse, and the four capacity stage names with the warning that `sufficient_idle_disk` is disk *bandwidth* and `sufficient_free_disk` is disk *space*. The file currently says nothing about CI topologies at all, so keep this to the probe and do not attempt to document the topologies -- that is phase 6. Do not touch `AGENTS.md` or `ARCHITECTURE.md`: no convention and no component boundary changes here. |
| 1f | low | haiku | none | Set the phase 1 row to `Complete` in the master plan's Execution table and the index arithmetic in `docs/plans/index.md` to `2 of 7`, then run `python3 tools/check-plan-status.py`. Do this only after 1a-1e are reviewed and the operator has confirmed a real CI run produced a non-empty series and census. |

The survey corrections that would otherwise have been a step here
were made in the planning commit, per the note under *What the
survey found*.

## Risks and mitigations

* **The `actions` half lands untested.** A pull request against
  `actions` cannot run its own composite-action change; only the
  post-merge canary does, and it uses the single-node `localhost`
  topology at `tier: smoke`. *Mitigation:* D15 makes every added
  step `continue-on-error`, so the worst failure is a missing
  artifact rather than a broken CI lane; step 1d is briefed to be
  inert on failure; and 1f does not close the phase until the
  operator confirms a real run produced data.
* **The probe perturbs what it measures.** Each poll constructs a
  `Scheduler`, whose `__init__` refreshes metrics unconditionally
  (`scheduler.py:86-95`), costing one database read per active node
  plus an uncached `_capacity_by_node()` -- roughly seven reads per
  poll on `slim-primary`. Worse, `get_active_node_metrics()` writes
  an audit event per poll for any active node with stale metrics
  (`:63-79`), and `degraded` is an active state, so the probe emits
  the most event load exactly when the cluster is unhealthy, which
  is when the failures it exists to observe are happening.
  *Mitigation:* 15 seconds is a starting point and step 1c reports
  sample count, so phase 2 can see the cost it paid; the interval is
  a single argument to change. The management session checks the
  first real run's database load against the in-flight
  database-load-reduction work before the window opens.
* **The census depends on log shipping.** D11 puts refusal counting
  on Loki, so a run whose log shipping is broken produces a series
  with no census beside it. *Mitigation:* the two are reported
  separately (D9) and the report says explicitly when the census is
  absent rather than printing zero refusals, which would be the
  dangerous reading.
* **A poller leaks on a cancelled job.** `cancel-in-progress` means
  the stop step may never run. *Mitigation:* D13's self-limiting cap,
  and the under-cloud reaper collects the instance regardless.
* **Phase 2 is tempted to start before the instrument is trustworthy.**
  *Mitigation:* the definition of done requires a real run's output
  to be inspected, not merely produced.

## Definition of done

Falsifiable, in order:

1. `/admin/resources` publishes a per-node `cpu_limit` reflecting the
   capacity row, `None` when no row exists, and
   `test_summarize_resources_publishes_the_counters` and
   `test_summarize_resources_reports_a_node_with_no_row` both pin it.
2. `tools/ci_headroom_probe.py` and `tools/ci_headroom_report.py`
   exist in the main repository, and the report tool runs under
   stock `python3` with no third-party import.
3. A unit test drives the report tool over a fixture series
   containing an empty file, a truncated final line, a sample with
   an `error` key, a node absent from `per_node`, and a census with
   a stage string the tool has never seen; none of these raises, and
   the unknown stage appears in the output.
4. The report never counts `no memory_max in node metrics` as a
   memory shortage, asserted by a test.
5. No hardcoded list of scheduler stage names exists in either new
   tool, verified by grep.
6. A real CI run on both `slim-primary` and `slim-tier` produces a
   non-empty series and a non-empty census in its bundle, and the
   job log carries the summary. The operator confirms this; it
   cannot be checked from a worktree.
7. No statement in the master plan or in phase 0 still says a green
   run records no refusals, and no statement in either names three
   capacity stages or calls `sufficient_idle_disk` a disk-capacity
   check. *(Done in the planning commit; re-check rather than redo.)*
8. No workflow step added by this phase can fail a job: every one is
   `continue-on-error`, verified by reading the diff.
9. `python3 tools/check-plan-status.py` passes, and
   `pre-commit run --all-files` passes in the main repository.

## What phase 2 inherits

* A documented JSONL contract rather than a file to reverse-engineer.
* Both ledger figures per node, so D7's 12-versus-10 reconciliation
  is a question the data can answer, together with a count of how
  often the fallback was used.
* A refusal census that is honest about the four capacity stages,
  which changes what phase 2 must look for: phase 0's D5 asked for
  cpu, memory and disk counted separately, and the correct
  breakdown is cpu, memory, disk space and disk bandwidth -- the
  last of which sizing cannot address at all.
* An explicit statement, per sample, of which nodes were absent and
  why, so that "the cluster had three hypervisors" is never inferred
  from a sample that merely could not see the other two.
* The knowledge that the provisional band bounds of 0.35 and 0.70
  have never been checked against a distribution, and that phase 2's
  job is to replace them or defend them.

## Back brief

Before executing any step of this plan, back brief the operator on
the understanding of it, and in particular on D12 -- the one change
here that touches the server rather than CI -- and on the step 1d
wiring, which lands in `shakenfist/actions` where it cannot be
tested before it merges and where only the operator can push.
