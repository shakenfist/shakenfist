# PLAN: CI cloud sizing phase 7 -- push audit

Planning effort: medium. Review effort: high.

## Why this phase exists

[PLAN-ci-cloud-sizing.md](PLAN-ci-cloud-sizing.md) has run for five
weeks across fourteen merged pull requests in this repository and three
in `shakenfist/actions`. Each was reviewed on its own. Nobody has read
the result as one body of work, and this plan needs that for a reason
particular to its method: it built an instrument, measured a cloud with
it, reshaped the cloud against those measurements, and then armed a
merge-blocking gate from the same instrument's readings. Every one of
those steps trusts the step before it. A fault in the instrument does
not announce itself as a fault -- it announces itself as a number, and
the numbers are now load bearing in three places.

`PUSH-AUDIT.md` is the repository's audit template, normally a pre-push
gate run against `develop...HEAD`. Here it runs retrospectively over
seventeen merges, which changes the baseline but not the questions.

Two precedents matter.
[PLAN-queue-performance-phase-08-push-audit.md](PLAN-queue-performance-phase-08-push-audit.md)
established the retrospective form and recorded afterwards why it still
missed a defect: it asked whether a change was *correct* and never
asked what the corrected code would then *do*.
[PLAN-database-load-reduction-phase-08-push-audit.md](PLAN-database-load-reduction-phase-08-push-audit.md)
applied that lesson by adding a fifth lens for what its plan had
*stopped* doing, because deleted code leaves nothing for a reviewer to
look at. D3 below is the same move for this plan's blind spot, which is
a different one.

## Scope

**In scope.** The code and documentation this plan added or changed,
across the fourteen in-repository ranges and three `shakenfist/actions`
ranges in D1, audited under the `PUSH-AUDIT.md` headings: wave 1
mechanical checks, and wave 2's code quality, test coverage,
documentation and security reviews, plus the fifth lens in D3.

**In scope, unusually.** The `shakenfist/actions` half, audited from
here. See D2; this is the decision most likely to be argued with.

**Out of scope.** Re-litigating the per-PR reviews. Each phase plan
records what its review found and how it was disposed of. The audit may
disagree with a disposition, but it starts by reading what was decided
rather than rediscovering it.

**Out of scope.** The two cluster deploys that reach no probe
([#4367](https://github.com/shakenfist/shakenfist/issues/4367)) and the
hardcoded IP list ([#4320](https://github.com/shakenfist/shakenfist/issues/4320)).
Both are filed, both are structural, and both were declined in writing
by phase 6. An audit finding that re-reports them is reporting the
filing, not a defect.

**Out of scope.** Re-measuring anything. This phase reads what the
instrument recorded and asks whether it can be trusted. If the answer
is no, the remeasure is its own phase and this plan says so.

**Out of scope.** Fixing anything the audit finds, unless it is
blocking or trivial. Like both precedents, a review phase records and
files rather than expanding into the work it discovers.

## What the survey found

The master plan's phase 7 section
([PLAN-ci-cloud-sizing.md](PLAN-ci-cloud-sizing.md), *Phase 7 -- Push
audit*) is three paragraphs. Its instruction to audit the accumulated
diff rather than the last phase's, and to take the baseline from the
`Merged` column, is right and is what D1 does. Four of its factual
claims are wrong or stale, and one of them would have made the phase
unrunnable.

1. **The `Merged` column has a blank cell, which makes the plan
   unauditable as it stands.** `PUSH-AUDIT.md` is explicit: "If a
   plan's `Merged` column is missing or a cell is blank, stop and say so
   -- that is an unauditable plan, and reporting a clean run against an
   empty diff is the failure this range rule exists to prevent." Phase
   6's cell reads `—`. The step 5 closeout that fills it is therefore
   not bookkeeping ahead of the real work; it is the precondition that
   makes the real work possible, and 7a does it first for that reason.

2. **"Most of the change is CI configuration and measurement tooling
   rather than product code, so the wave 2 code-quality and security
   lenses have less to read than usual."** Partly false.
   `shakenfist/scheduler.py` is in three ranges and
   `shakenfist/config.py` in one, and the changes are not cosmetic:
   `078772504` added `cpu_limit` to the per-node payload of
   `/admin/resources`, and `e951ee42d` added `capacity_degraded` to that
   endpoint's response body. A new field on an API response is exactly
   what the security lens exists to read, so the weighting in that
   sentence is backwards for 2d. Corrected at source by this phase's
   planning commit.

3. **"The sizing model, the ledger arithmetic and the band all have to
   say the same thing in `docs/developer_guide/ci.md` as the topology
   files do."** The named file is no longer where that material lives.
   Phase 6's D2 created `docs/developer_guide/ci_cloud_sizing.md` (750
   lines) as the canonical page, and `ci.md` now carries a summary and a
   link. A documentation lens briefed on `ci.md` alone would read the
   summary and miss the page. Corrected at source.

4. **Two plan-attributable merges are absent from the `Merged`
   column.** `ab2158cb2` (#3938, the plan's own creation, 767
   insertions) and `9eaf4af34` (#4271, a claim cross-reference against
   `PLAN-scheduler-reservations.md`, 60 insertions). Both are
   plan-document-only -- no code, no tests, no tooling -- so the code
   lenses correctly skip them. The documentation lens does not: a
   plan-document merge is how a false claim enters the tree, which is
   precisely what #4396 spent its diff removing. D5 routes them.

5. **A line reference in a comment rotted within six hours of being
   verified.** Phase 6's Definition of done item 6 requires that every
   `functional-tests.yml:NNN` reference in
   `tools/ci_headroom_harvest.py` point at what its comment says.
   It did when phase 6 checked it. `cd9a89242` (#4396) then added seven
   lines above the cited region, so `:581` -- "it calls the
   build-smoke-cluster composite action directly" -- now points at
   `cancel-in-progress: true`, and the call it names is at `:587`. The
   second reference, `:440-495`, is approximate rather than wrong. This
   is a worked example for the documentation lens rather than a finding
   to rediscover, and 7a fixes the one line as closeout debris.

Verified rather than assumed, as part of the survey:

* All fourteen in-repository merge commits are ancestors of
  `origin/develop`, checked individually with
  `git merge-base --is-ancestor`.
* The three `shakenfist/actions` commits are merged on that
  repository's `main`: `633c56b31` (#94, 2026-09-23), `e2243a554`
  (#102, 2026-09-26), `a742c99b0` (#120, 2026-09-30).
* Phase 6's Definition of done was spot-checked by running it, not
  reading it. Item 1 (`ci_cloud_sizing.md` exists, 40,067 bytes), item 3
  (`MINIMUM_HYPERVISOR_LEDGER` named, 2 occurrences), item 5 (the
  `defaults\s+to\s+gating` regex returns 0, as required) and item 7
  (the fork-the-topologies grep is empty outside the phase 6 plan) all
  pass. Item 8's two halves were proved in the session that closed
  [#4377](https://github.com/shakenfist/shakenfist/issues/4377). Item 6
  fails, per survey finding 5.
* No leftover worktree or branch for phases 0-6. The phase 6 worktree
  was removed on 2026-10-02 after confirming clean status, no unmerged
  commits, and `merge-base --is-ancestor`.
* Phase 6's deferred work was filed rather than dropped: #4367 (the
  composite-action deploys), #4320 (the hardcoded IP list), #4377 (the
  `actions` half, now closed on evidence).
* The master plan's Execution table and `docs/plans/index.md` agree with
  each other: seven of eight phases Complete, plan In progress. No
  closeout drift to repair, which the `next-phase` skill asks be
  reported as a result in its own right.

## Decisions

### D1 -- The baseline is the plan's merge ranges, and there are seventeen

`git diff develop...HEAD` on this branch contains this plan document
and nothing else. Every command in `PUSH-AUDIT.md` would report success
against it.

In this repository:

| Phase | PR | Merge | Range | Size |
|-------|----|-------|-------|------|
| 0 | #3939 | `d03ab340e` | `d03ab340e^1..d03ab340e` | 3 files, +419/-23 |
| 1 | #3940 | `078772504` | `078772504^1..078772504` | 12 files, +3108/-25 |
| 2 | #4089 | `e951ee42d` | `e951ee42d^1..e951ee42d` | 14 files, +4554/-389 |
| 2 | #4138 | `3546fabed` | `3546fabed^1..3546fabed` | 10 files, +1047/-91 |
| 3 | #4152 | `ead1ccba5` | `ead1ccba5^1..ead1ccba5` | 4 files, +593/-17 |
| 3 | #4170 | `f3b245304` | `f3b245304^1..f3b245304` | 8 files, +2366/-39 |
| 3 | #4186 | `c13d2c6fd` | `c13d2c6fd^1..c13d2c6fd` | 1 file, +16 |
| 3 | #4193 | `210fb4469` | `210fb4469^1..210fb4469` | 4 files, +187/-52 |
| 4 | #4202 | `870a5fbec` | `870a5fbec^1..870a5fbec` | 6 files, +970/-36 |
| 4 | #4289 | `6856aad74` | `6856aad74^1..6856aad74` | 4 files, +244/-20 |
| 5 | #4308 | `de87bcde2` | `de87bcde2^1..de87bcde2` | 10 files, +2522/-59 |
| 5 | #4328 | `704416829` | `704416829^1..704416829` | 9 files, +1299/-82 |
| 6 | #4373 | `174c0b819` | `174c0b819^1..174c0b819` | 13 files, +1830/-596 |
| 6 | #4396 | `cd9a89242` | `cd9a89242^1..cd9a89242` | 5 files, +64/-53 |

19,219 insertions and 1,482 deletions. In `shakenfist/actions`, per D2:

| Phase | PR | Merge | Size |
|-------|----|-------|------|
| 5 | actions#94 | `633c56b31` | 7 files, +465/-14 |
| 5 | actions#102 | `e2243a554` | 6 files, +108/-63 |
| 6 | actions#120 | `a742c99b0` | 5 files, +154/-27 |

**`PUSH-AUDIT.md`'s "once per range" applies to the greps, not to the
judgment agents.** The template says "Run the whole audit once per
range and pool the findings". Read literally against seventeen ranges
and six lenses that is 102 agent runs, which is not what the sentence is
for: it exists to stop an auditor diffing a union that git cannot
express and calling it one pass. The precedent resolved this the same
way -- `PLAN-database-load-reduction-phase-08-push-audit.md`'s steps 8c
to 8g each read all ten of its ranges -- and this plan follows it. The
mechanical greps run per range, because a grep is cheap and a pooled
grep loses which change introduced the hit. Each judgment agent reads
the whole set, because the findings worth having here are about how the
ranges interact.

**Where a later phase rewrote an earlier phase's code, the net state is
what matters for a correctness finding.** Phase 4 reshaped what phase 2
measured; phase 6 rewrote comments phase 2 wrote; #4396 rewrote comments
#4373 wrote. An agent that finds something in an early range must check
the file as it stands on `develop` before reporting it. The range says
what changed; the working tree says what shipped.

### D2 -- The `shakenfist/actions` half is audited here, because there is no audit there to cite

The master plan says the half that landed elsewhere "runs against that
repository's default branch, as part of the pull request that lands it,
and this phase cites that audit rather than re-running it."

**There is no `PUSH-AUDIT.md` in `shakenfist/actions`** -- `gh api
repos/shakenfist/actions/contents/PUSH-AUDIT.md` returns 404. No such
audit ran for #94, #102 or #120, so there is nothing to cite. Those PRs
had automated reviews, which is a different instrument: a review reads a
diff, an audit reads a body of work against a checklist.

So this phase audits those three ranges itself, and files an issue
against `shakenfist/actions` to add the template so the next plan can
cite rather than reach.

This is the decision a reviewer is most likely to argue with, and the
argument against it is good: a push audit is a pre-push gate for the
repository it lives in, and auditing another repository's merged `main`
from here produces findings nobody over there asked for, in a repo whose
conventions this plan does not own.

Three things outweigh it. The 727 insertions in question are the
*invocation* -- `smoke-cluster.yml`, `ci_headroom_launch.sh`,
`ci_headroom_collect.sh`, `ci_headroom_verdict.sh` -- so the instrument
this plan built is half in each repository and auditing one half is
auditing half an instrument. `shakenfist/actions` is consumed at `@main`
with no pin by every cluster job in the ecosystem, so a defect there is
live fleet-wide the moment it merges, untestable before the merge, and
not revertable downstream; that asymmetry is a reason for more review,
not less. And `ci_headroom_verdict.sh` is the code that *fails merges*.
Leaving the only unaudited part of the plan to be the part that blocks
other people's work is the wrong place to economise.

The audit of those ranges is read-only. Any finding is filed as an issue
against `shakenfist/actions`, not fixed from here, and the fix is that
repository's maintainer's call.

### D3 -- A fifth wave-2 lens: the instrument's own failure surface

`PUSH-AUDIT.md`'s four headings ask whether the code that exists is
correct. They do not cover this plan's characteristic risk, which is
not incorrect code but **correct code that measures nothing and says so
in a way nothing distinguishes from health**.

The instrument is built on a deliberate rule, D15 of the master plan:
an instrument that can fail the job it measures changes the failure
surface it exists to measure. Every failure in the probe path is
therefore swallowed on purpose. `ci_headroom_launch.sh` does `mkdir -p
/srv/ci/traces 2>/dev/null || true`; its remote heredoc is wrapped `||
true`; the script ends `exit 0` unconditionally. That rule is right. Its
consequence is that **a broken probe and a healthy cluster produce the
same green job**, and three things downstream now trust the output: the
phase 2 baseline, the phase 4 reshape fitted to it, and the phase 5 gate
fitted to the warn window.

This lens walks that surface. For each place the instrument can fail
silently, it answers: what does the job look like when this fails; what
does the dataset look like; and what, if anything, would ever tell
anybody. "Nothing would" is a legitimate answer and a finding.

The class is not hypothetical, and the known instance is the model for
the brief rather than its content. The traces-directory gate is one
member: narrowing it yields a probe that starts, cannot write, and exits
zero. It survived every test in the repository until a mutation found
it, and the test that now guards it
(`test_every_probed_test_kind_can_write_its_traces`) was written in the
session that closed #4377. One member of a class being fixed is not
evidence the class is empty; it is evidence the class exists.

This is the highest-value brief in the phase and it gets `fable`, which
no other step does. It has to hold two repositories' shell, workflow
YAML, Python and the shape of the banked dataset in mind at once, and
the thing it is looking for is an *absence* -- a failure mode with no
line of code to point at. That is the one kind of step the roster's "has
already defeated opus or is expected to" clause is for.

### D4 -- Wave 1's exit condition is relaxed, and one known flake is named

`PUSH-AUDIT.md` says stop if `pre-commit` or `tox` fails. They run
against the working tree, which here is `develop` plus this plan
document, so a failure is a pre-existing failure on `develop` rather
than something this plan introduced. Record it, check whether the plan's
own ranges are implicated, and continue to wave 2. Stopping would only
be right if this branch were about to be pushed as code.

Name the flake rather than rediscovering it:
`shakenfist.tests.external_api.test_nested_sweep.NestedSweepOffTestCase.test_the_sweep`
fails under parallel stestr and passes in isolation, on refusal-reason
ordering. It failed twice on 2026-10-01 against a diff of comments
only. A wave 1 report that lists it as a failure without re-running it
in isolation has misgraded it.

### D5 -- The two untabled plan-document merges get the documentation lens only

`ab2158cb2` (#3938) and `9eaf4af34` (#4271) are plan documents and
nothing else, so there is no code, test, or security question to ask of
them. They are not retrofitted into the Execution table, which is keyed
by phase and neither is one. They go to 7e, because a plan document is
how this plan's false claims entered the tree and the documentation lens
is the one that reads for that.

### D6 -- #4396 belongs to phase 6, and phase 6's cell records three commits

`cd9a89242` (#4396) landed the documentation half of #4377 a day after
#4373. It is phase 6's deferred work completing, not a new phase, so it
goes in phase 6's `Merged` cell rather than creating a row. That cell
therefore reads `174c0b819` (#4373), `cd9a89242` (#4396) and
`a742c99b0` (shakenfist/actions#120) -- matching how phase 5's cell
already mixes both repositories.

### D7 -- Findings are graded and disposed of in writing

Blocking findings are fixed in this phase; advisory findings are filed
as issues and listed here. A finding that is real but outside this
plan's scope is filed and named as such rather than quietly downgraded.
A finding against `shakenfist/actions` is filed there and never fixed
from here, per D2.

### D8 -- A clean heading is a result, but only alongside what it examined

If a heading finds nothing it says so in one sentence, *with* the list
of what it actually read. The first risk of any retrospective audit is
that it rubber-stamps code which already shipped and passed CI; the
guard is the "what I examined" list, not a quota of findings.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | opus | none | *(Management session, this document.)* Close out phase 6 and register phase 7. Fill phase 6's `Merged` cell per D6, add the phase 7 row, correct the three false claims in the master plan's *Phase 7* section that survey findings 2, 3 and 4 name, and update the `docs/plans/index.md` row so its description says the `Ansible modules` probe landed rather than that it is prepared for the operator to push. Fix the one rotted line reference from survey finding 5 (`tools/ci_headroom_harvest.py:186`, `:581` → `:587`) as closeout debris. Do **not** touch `docs/plans/order.yml`. Run `python3 tools/check-plan-status.py` and `pre-commit run --all-files`. Two commits: the closeout, then the plan. |
| 7b | medium | sonnet | none | **Wave 1.** Run `pre-commit run --all-files` and `tox`, record the actual output, and apply D4: a failure is reported rather than stopped on, and `test_nested_sweep` is re-run in isolation before being graded. Then run the template's four style greps against **each** of the fourteen in-repository ranges in D1 individually -- lines over 120 characters, stray `print(`, new `etcd` references, new `mariadb.get_all_*(` without a `# nopushdown:` tag -- and report the output, not a summary of it. Confirm by evidence rather than assumption that the proto-freshness check does not apply: `git diff --name-only` over all fourteen ranges matches nothing under `protos/` or `shakenfist/protos/`. Three ranges touch markdown with diagrams, so run `tools/mermaid-lint.sh` and **check its exit status directly** -- piping it through `tail` or `grep` reports the filter's status and turns every failure green. Then the style-conformance judgment brief from `PUSH-AUDIT.md`. The Python under audit is three standalone tools (`tools/ci_headroom_probe.py`, `ci_headroom_report.py`, `ci_headroom_harvest.py`) plus `shakenfist/deploy/shakenfist_ci/sizing.py` and `load_budget.py`; `report.py` and `probe.py` run on a CI runner under stock python3 and are standard-library-only by constraint, so check that constraint still holds rather than suggesting a dependency. |
| 7c | high | opus | none | **2a, code quality.** Take 7b's mechanical output as input. Read the fourteen in-repository ranges for duplicated logic, missed abstractions, and the two blocking rules (SQL pushdown, cached FK lists) -- both are likely vacuous here and saying so with evidence is the answer. The real weight is elsewhere. First, the production code the master plan's phase 7 section wrongly says is absent: `shakenfist/scheduler.py` in `078772504` (adds `cpu_limit` to the per-node payload, deliberately with no fallback to `cpu_hard_max`) and `e951ee42d` (adds `capacity_degraded` to the `/admin/resources` response body), and `shakenfist/config.py` in `870a5fbec`. Read those as product code, because they are. Second, the three `tools/ci_headroom_*.py` scripts, where the comment-proportion shared block earns its keep: these files carry very long explanatory comments, some load-bearing (the nested-zip discovery, the unordered-listing incident) and some restating the code. Third, the plan-references-in-code shared block, which this plan is structurally prone to violating -- it is a plan about CI whose artefacts are named after its own phases and decisions. Grep the ranges for `D[0-9]`, `phase [0-9]`, `6d`, `2g` and similar in code, comments, docstrings and test names, and report each as acceptable-because-unbuilt or as a finding. Triage every TODO / `# noqa` / `# type: ignore` the sweep flagged. |
| 7d | medium | sonnet | none | **2b, test coverage.** Review the fourteen in-repository ranges. Seven test modules are in the diff; the question is not quantity but whether the *risky* claims are covered. Specifically: does `shakenfist/tests/test_ci_headroom_harvest.py` cover the unordered-listing defect that `list_runs()` was fixed for -- a listing served oldest-first, and a newest-first one, both producing the right window? Does anything cover the harvest's own loud-failure contract, that an enumeration finding no runs raises *before* `--output` is opened so a stale dataset is not truncated? Does `test_headroom_gate_workflow_seams.py` actually derive call-site shapes from the workflow, or assert literals that a reformat would break? Does `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_saturation.py` assert the refusal contract behaviourally rather than by matching a message string? Shaken Fist prefers functional to unit coverage, so for each behaviour name which `cluster_ci_tests` test exercises it and which has unit coverage only. Flag assertions that test implementation details. Note where a thing is untestable by construction -- the probe needs a live cluster -- and say whether that is acceptable or a gap. |
| 7e | medium | sonnet | none | **2c, documentation.** Check documentation against code across the fourteen in-repository ranges **plus the two plan-document merges in D5** (`ab2158cb2`, `9eaf4af34`). Apply the README, LLM-doc, diagram and plan-phase-reference shared blocks. The canonical sizing page is `docs/developer_guide/ci_cloud_sizing.md`, not `ci.md` -- survey finding 3; read both and check the summary in `ci.md` does not contradict the page. `AGENTS.md` is in the ranges and the shared block says growth there is itself a finding. The plan-phase-reference block forbids "phase N" in `docs/` outside plans directories, which a plan this size is likely to have leaked. Then the specific risk this plan carries: **a number stated in two places with different values.** The master plan's baseline table (`:129`) correctly records `slim-tier` at 12 vCPU as the thing phase 2 measured, while the current ledger is 24; phase 6's D7 annotated rather than rewrote that, and phase 6's done-condition 4 deliberately excluded `docs/plans/`. Verify that annotation is present and that no present-tense statement outside `docs/plans/` says 12. Survey finding 5 gives you one rotted line reference as a worked example of what to look for; 7a has already fixed it, so look for the others rather than re-reporting it. Confirm the plan documents' statuses agree with `docs/plans/index.md` and `tools/check-plan-status.py`. No database schema changed -- confirm that rather than assuming it. |
| 7f | high | opus | none | **2d, security.** The master plan's phase 7 section says this lens has less to read than usual. It is wrong, per survey finding 2, and the live areas are in order: **(1) the `/admin/resources` surface.** `078772504` and `e951ee42d` added `cpu_limit` and `capacity_degraded` to that endpoint. Confirm the authorisation decorator is unchanged and that neither field discloses anything a caller could not already derive; `cpu_limit` is deliberately `None` rather than falling back, so check no caller treats `None` as zero or unbounded. **(2) The probe's own privileges.** The workflow step runs `sudo mkdir -p /srv/ci/traces; sudo chown -R debian:debian /srv/ci/traces` on a CI node, and `/srv/ci` is a mount point over `/dev/vdc`; check the chown target cannot be influenced by anything outside the workflow, and that `-R` cannot be pointed at a wider tree. **(3) What reaches the artifact bundle.** The probe banks `/admin/resources` samples and a filtered Loki census into a ninety-day artifact readable by anyone who can read the repository's Actions output. Does any sample or census entry carry a namespace name, a JWT, a node hostname beyond what is already public, or anything from a log line the filter did not anticipate? This is the finding most worth having in this lens. **(4) Resource exhaustion.** The harvest downloads an unbounded number of artifact zips and unpacks nested zips from a remote source -- check for a cap, a timeout, and whether an unpacked member path is joined to a local directory without being proved to stay inside it (the path-traversal shared block; a zip member name is attacker-controlled in the same way a request parameter is). **(5) SQL and subprocess.** Any f-string SQL or `text()` interpolation in the ranges, and every `subprocess` call in the three tools, which shell out to `gh`. Report findings with severity; critical and high must be fixed before this phase closes. |
| 7g | high | fable | none | **The instrument's failure surface (D3).** This is the plan-specific brief and no template heading covers it. It spans both repositories: read the three `tools/ci_headroom_*.py` scripts here, and `tools/ci_headroom_launch.sh`, `ci_headroom_collect.sh`, `ci_headroom_verdict.sh` and `.github/workflows/smoke-cluster.yml` in `shakenfist/actions` at `main`. Build the list of every way the instrument can fail without failing the job, then answer three questions for each: what the job looks like, what the dataset looks like, and what would ever tell anybody. Start from the swallows and do not stop at them -- `grep -nE '\|\| true\|2>/dev/null\|exit 0\|continue\|except'` across both halves. Known members of the class, as orientation and not as the answer: the traces directory the probe cannot write (fixed, guarded by `test_every_probed_test_kind_can_write_its_traces`); the `event=merge_group` listing GitHub serves unordered, which once made a harvest write zero records and exit zero, and which `list_runs()` now defends against by paginating fully and sorting client-side; the gate's off-switch semantics in `ci_headroom_verdict.sh`, where unset, empty, `0`, `false`, `no` and `off` all mean off, so a typo in the repository variable disarms the gate silently. Then the question the three downstream consumers make urgent: the band was fitted to a warn window the instrument produced, so **if the instrument under-measured during that window, the band is fitted too low and the gate cannot fire.** Say whether anything would detect that, and what. Grade each entry sound, undocumented, or a defect; "sound" must name the mechanism that makes it sound, not the absence of a bug report. |
| 7h | medium | opus | none | **2a-2d and D3 for the `shakenfist/actions` half (D2).** Read-only, against that repository's `main`, over the three ranges in D1's second table. One agent covers all four template lenses for these 727 insertions rather than four agents covering 727 lines each. The substance is `ci_headroom_verdict.sh` (the code that fails merges: check its comparison arithmetic, its withholding conditions, and that every path that cannot decide withholds rather than passes), `ci_headroom_collect.sh` and `ci_headroom_launch.sh` (shell quoting, unquoted expansions, `set -e` behaviour under the `|| true` wrappers, and anything user- or matrix-influenced reaching a command line), and `smoke-cluster.yml` plus `canary.yml` (the seven `test_kind` gates and whether the three widened ones and four untouched ones are still mutually consistent after #120). Also: does that repository have unit tests for the shell, and does its CI run them? `tests/test_ci_headroom_verdict.py` exists; confirm something invokes it. File every finding as an issue against `shakenfist/actions` -- fix nothing from here. Also file the issue that repository has no `PUSH-AUDIT.md`, which is why this step exists. |
| 7i | high | opus | none | *(Management session.)* Grade every finding blocking or advisory, fix the blocking ones in this repository, file the advisory ones and everything from 7h as issues, and write the results into this plan's Findings section and the master plan's *Bugs fixed during this work*. Check every heading names what it examined, per D8. Spot-check two findings per agent against the tree before accepting the report, and record the spot-check. Set both statuses, run `tools/check-plan-status.py` and `pre-commit run --all-files`. |

## Risks and mitigations

* **The audit rubber-stamps code that already shipped and passed CI.**
  Fourteen merged ranges that all went green invite confirmation.
  *Mitigation:* D8 -- every heading names what it examined, and
  "nothing found" is only acceptable alongside that list. 7i checks
  this before writing results, and spot-checks two findings per agent.

* **Seventeen ranges and 19,946 insertions is enough surface to skim.**
  An agent that reads the diffstat and reasons from file names produces
  plausible findings that are not about this code. *Mitigation:* every
  brief names specific files and specific questions rather than
  restating the template heading; 7i's spot-check is the backstop.

* **The net state differs from the ranges.** Phase 4 reshaped what
  phase 2 measured, phase 6 rewrote phase 2's comments, #4396 rewrote
  #4373's. An agent reading only the ranges can report a defect a later
  phase already fixed -- the precedent did exactly this and caught
  itself. *Mitigation:* D1's net-state rule, restated in 7c's brief.

* **D3's lens finds something that invalidates the baseline.** If the
  instrument under-measured during the warn window, the phase 5 band is
  fitted to bad data and the phase 4 reshape was argued from it. That is
  not a small fix. *Mitigation:* that is what the lens is for. It is out
  of scope to re-measure here; this plan files it at high priority and
  says a remeasure is its own phase, rather than downgrading it to
  advisory to keep the phase small.

* **D2 widens the phase into a repository this plan does not own.**
  Findings filed against `shakenfist/actions` may be unwelcome or may
  sit. *Mitigation:* 7h is read-only and files rather than fixes, so the
  cost to that repository is a set of issues its maintainer can close.
  The alternative -- the merge-blocking half of the instrument being the
  only unaudited part of the plan -- is worse.

* **`tools/mermaid-lint.sh` needs a docker daemon.** It is not a
  pre-commit hook for that reason, and a machine without docker reports
  nothing rather than failing. *Mitigation:* 7b checks the exit status
  directly and records whether the tool ran at all; "docker was
  unavailable" is a recorded gap, not a pass.

## Definition of done

Falsifiable, in order. Run them; do not read them.

1. Phase 6's `Merged` cell is non-empty and names `174c0b819`,
   `cd9a89242` and `a742c99b0`. `grep -c '^| 6\.' ` finds the row and
   the cell contains no `—`.
2. `python3 tools/check-plan-status.py` passes, and the master plan's
   Execution table and `docs/plans/index.md` agree with each other.
3. `docs/plans/order.yml` is unchanged by this phase:
   `git diff --exit-code origin/develop -- docs/plans/order.yml`.
4. The master plan's *Phase 7* section no longer claims the change is
   not product code, and names `docs/developer_guide/ci_cloud_sizing.md`
   as where the sizing model lives. It may still mention
   `docs/developer_guide/ci.md`, but only as the page carrying the
   summary and the link.
5. `tools/ci_headroom_harvest.py` has no `functional-tests.yml:NNN`
   reference pointing at the wrong line, checked line by line against
   the file as it stands -- the same check as phase 6's item 6, which
   currently fails.
6. All six lenses (7b wave 1, 7c-7f, 7g, and 7h for the `actions` half)
   have a written result naming what was examined.
7. Wave 1's commands and four style greps have been run against the
   fourteen in-repository ranges with output recorded, not asserted.
8. The proto-freshness check is explicitly recorded as not applicable,
   with the evidence that no range touches `protos/`.
9. `tools/mermaid-lint.sh` has either run with its exit status recorded,
   or its absence recorded as a gap with the reason.
10. Every finding carries a grade (blocking or advisory) and a
    disposition (fixed here, filed as #NNNN, filed against
    `shakenfist/actions` as #NNNN, or declined with a reason in
    writing).
11. No blocking finding is left unresolved. A finding is resolved when
    the defect it names is fixed; a *related* gap the fix reveals but
    does not cause may be filed, provided the disposition table says so
    and grades the remainder advisory in its own right.
12. D3's lens has produced an explicit list of the instrument's silent
    failure modes, each graded sound, undocumented, or a defect, with
    "sound" naming a mechanism.
13. An issue exists against `shakenfist/actions` for the absent
    `PUSH-AUDIT.md`.
14. Two findings per agent have been spot-checked against the tree by
    the management session, and the spot-check is recorded.
15. The master plan's status becomes Complete only if no blocking
    finding remains open, under item 11's definition.
16. If the audit finds nothing, that is recorded in one sentence, per
    the master plan's own instruction for this phase.

## Back brief

Before executing any step of this plan, please back brief the operator
as to your understanding of the plan and how the work you intend to do
aligns with that plan.

Two gates in particular, both cheap to propose and expensive to redo:

* **Before 7h runs, confirm D2.** Auditing another repository's merged
  `main` from here is the judgement call in this plan most likely to be
  wrong, and it is wrong in a way that wastes a whole step rather than
  producing a bad finding. If the operator would rather file the
  missing-template issue and stop there, 7h becomes that one issue and
  the phase is smaller.
* **Before 7i fixes anything, bring the blocking list back.** The
  difference between a blocking and an advisory finding against code
  that has already shipped and is already green is a judgement about
  risk, not a rule. Propose the grading before acting on it.

## Findings

Seven agents ran: wave 1 (7b), the four `PUSH-AUDIT.md` judgment headings
(7c-7f), the D3 instrument lens (7g), and the `shakenfist/actions` pass
(7h). Every heading below names what it examined, per D8.

**The headline: no finding invalidates the plan's conclusions.** The band
is not fitted to under-measured data, and that is shown from the committed
dataset rather than argued. There were no critical or high security
findings. Wave 1 passed.

What the audit found instead has one shape, which three lenses reached
independently: **the instrument records its own failures honestly, reports
them silently, and nothing reads the records.**

### Wave 1 (7b) -- passed

`pre-commit run --all-files` green, all twelve hooks. `tox` green: 5,501
tests, 5,380 passed, 121 skipped, 0 failed, 351s. The four style greps run
against each of the fourteen ranges individually: **zero** lines over 120
characters, zero `etcd` references, zero `mariadb.get_all_*(` without a
`# nopushdown:` tag. 92 `print(` hits, every one in `ci_headroom_probe.py`,
`ci_headroom_report.py` or `ci_headroom_harvest.py` -- the three CLI tools
whose printed output is their product, none of which may import
`shakenfist_utilities.logs`. Correct use, not stray debug output.

**Proto freshness is not applicable, with evidence**: `git diff
--name-only` pooled over all fourteen ranges touches 37 files and none is
under `protos/` or `shakenfist/protos/`, so `tox -e genprotos` was
correctly not run. `tools/mermaid-lint.sh` ran with docker available, exit
status checked directly: 0. Only two files in the repository carry mermaid
diagrams (`ARCHITECTURE.md`, `docs/developer_guide/state_machine.md`) and
both parse.

The named flake, `test_nested_sweep.NestedSweepOffTestCase.test_the_sweep`,
passed under parallel stestr in both the `py3` and `cover` passes. Recorded
per D4 rather than omitted, because a wave 1 report that lists it without
re-running it in isolation has misgraded it.

### A premise of this plan was wrong, and did not reach the tree

The plan, and two of the briefs written from it, said
`ci_headroom_report.py` and `ci_headroom_probe.py` are standard-library-only
by constraint. `probe.py` imports `shakenfist_client.apiclient` and is right
to: it runs on the cluster primary inside the test environment, not on a
bare runner. The stdlib-only pair is `report.py` and `harvest.py`, and each
says so in its own docstring, `harvest.py` explicitly contrasting itself
with `report.py`.

7c checked whether the wrong version had propagated: five statements of the
constraint exist across `ci_cloud_sizing.md`, `ci.md`, `AGENTS.md` and the
three tools, and all five are correct. The error was confined to the plan
documents, which this section is the correction of.

### Blocking findings, all fixed in this phase

**F-B1. `tools/ci_headroom_report.py`'s module docstring stated the opposite
of the code.** It said the per-node maximum committed fraction "is recorded
rather than printed ... the phase which sets them is the phase which should
print it". Phase 5 (`de87bcde2`) then added the printing: it is printed
beside the cluster-wide verdict, raised as a GitHub annotation, and carried
in the step summary, judged against `PER_NODE_BAND_UPPER`. A false claim in
the first 140 lines of the instrument's central file. Fixed.

**F-B2. `GitHubCLI.paginate()`'s page walk had no test.**
`FakeGitHub.paginate()` yields one page and returns, for every path shape,
so every existing test exercised what callers do with a listing and none
exercised how the listing is gathered. `GitHubCLI` appeared nowhere in
`shakenfist/tests/`.

That is half of the 2026-09-08 fix left unheld. The other half -- the
client-side sort -- is covered in three orderings by `RunListingTestCase`.
GitHub's `?event=merge_group` listing genuinely does omit the newest runs
from page one, checked against the live API during this phase, so a run
reachable only on a later page is the ordinary case. Fixed: a new
`PaginationTestCase` with five tests, including one that drives
`list_runs()` end to end with the newest run on page two. The fake now
raises on a refetched page, so a walk that never advances fails instead of
hanging.

**F-B3. A window in which every record is absent reported success
unqualified.** The harvest raises on zero runs and on zero records, but a
window where every bundle lacked a series wrote N records and printed
"Wrote N records".

**The first fix for this was wrong and is worth recording.** Raising on an
all-absent window broke three existing tests, one of them named
`test_a_harvest_which_produced_a_record_is_not_an_error`, and contradicted
the module's own documented principle that a bundle with no series is a
record and not a gap -- a one-run harvest of an expired artifact is a
legitimate question whose answer is the reason in the record. So
`harvest()` now returns the written count *and* the usable count, `main()`
prints both, and an all-absent window gets an explicit warning rather than
a refusal. Two tests pin the accounting. What would make this an assertion
rather than a line of prose is a consumer reading the dataset on a
schedule, which is #4409.

**F-B4. A broken log shipper read as a cluster with no refusals.** With the
census file read successfully but matching no log lines,
`capacity_shortage_drops` is 0 and `refusal_warning` was `False` --
identical to a genuinely clean run. Every job this runs in deploys a
cluster and schedules instances, so the scheduler cannot have been silent:
an empty result means the shipping path stopped or the message forms were
renamed.

`refusal_warning` was already deliberately tri-state for the census that
could not be read; this is the second way it cannot say, and it now reads
null too. Both prose sites that explained the null as "no census was read"
now distinguish the reasons. Two tests, one for the empty census and one
for the converse -- a census that read real lines and found no refusal
must keep saying so.

**The first fix for this was keyed on the wrong field, and the automated
review caught it.** It tested `census['records']`, the count of JSON log
lines. But `records` is incremented before `stage_of()` runs, so the
renamed-message case this finding names -- a LogQL filter that stopped
matching the scheduler's message forms -- leaves whatever else the query
selects counted in `records` while no stage is tallied, and
`capacity_shortage_drops` is summed from the stage tallies alone. The
dangerous half of the defect therefore still read as a clean run. The test
is now `census['stage_events']`, which subsumes the empty case, and the
prose is three-way: no census, no lines, or lines without stages.

Two further things fell out of that, neither of them visible from reading
the first fix:

* **The converse test was passing for the wrong reason.** Its fixture used
  `schedule have highest affinity`, which `stage_of()` returns None for, so
  the census it built was itself in the ambiguous state -- it asserted that
  an observed absence keeps reading as an absence while containing no
  observation. It now uses a real `STAGE_SURVIVED_PREFIX` event.
* **The early `return` silenced the guard half.** `print_verdict()` returned
  as soon as the warning was null, which was harmless while the condition
  implied an empty census, because an empty census has no guard events
  either. Widening the condition made it able to suppress a *collected*
  guard census with real denials -- the reading the comment above that line
  says it exists to prevent, and the stronger of the two kinds of evidence.
  It now falls through. A pre-existing test caught this one.

`RECORD_VERSION` is 4 as a result. The file's own convention is that
additive changes go in place and non-additive ones bump the version, and a
version 3 record carries `False` where a version 4 record carries `null`
for the same observation. The committed baseline is unaffected in fact --
`stage_events` is non-zero on all 204 summarised records in
`records.jsonl` and all 32 in the addendum, so no banked record is in the
ambiguous state -- but a window harvested across the boundary would pool
the two, so the dataset README names it and gives the four lines that
re-derive the version 4 reading from fields every version carries.

### Mechanical fixes, taken because they leave the repository

**F-M1. 34 sites printed plan citations into CI output**, including two in
`$GITHUB_STEP_SUMMARY` and three in GitHub annotations: D3, D4, D5, D7, D8,
D9, D10, D11, D13, D15, D18, "phase 2", "phase 4". A contributor reading a
job log or a PR annotation has no access to the plan that assigns those
letters. Each sentence already stated its reason, so the citations were
removed and the reasons kept. Seven test assertions pinning the old strings
were updated with them.

The first pass found 25, all in `tools/ci_headroom_report.py`, by matching
`D[0-9]` and "phase N". The automated review found four more it had missed
-- `(5f)`, a *step* identifier rather than a decision letter, three of them
in `print_verdict()` and one in the step summary. Rather than take the four
reported, the sweep was redone as an enumeration: every non-docstring
string literal in the repository, parsed out with `ast` so comments and
docstrings are excluded by construction, matched against `D[0-9]`,
`([0-9][a-z])`, `phase [0-9]` and `PLAN-`. That found five more beyond the
reported four:

| Site | Why it is user-visible |
|---|---|
| `ci_headroom_report.py`, the `demand` dimension note | printed in the guard census table |
| `ci_headroom_harvest.py` ×3 | `argparse` help, so `--help` output |
| `test_state_changes.py`, a `(D7)` assertion message | read out of a cluster CI failure, which is F-M2's class exactly |

Two sites were left deliberately, and the distinction is what the sweep is
for. `shakenfist_ci/base.py` and `test_coalescing.py` cite a
`docs/plans/PLAN-*.md` *path*, which a reader can open -- that is the useful
form, not the opaque one. And `ci_headroom_harvest.py`'s `absent_reason`
says "predates phase 1" in a string that is **recorded into every record of
the committed dataset**: changing it would split the data on a condition
two spellings now describe, which is the same class of defect as the
`refusal_warning` boundary above. A citation in banked data is not the same
problem as a citation in a job log.

**F-M2. 20 runtime `skipTest`/`fail` messages in `test_saturation.py` ended
in `(D26)`.** These are what an engineer reads in a cluster CI result. Each
message already stated its own reason in full. Removed; the 26 remaining
mentions are in docstrings and comments and are filed as #4411.

**F-M3. Nine rotted line-number citations repaired.** The survey found one
(`functional-tests.yml:581`, invalidated by `#4396` six hours after phase 6
verified it). 7e found six more. Checking 7e's own list found two further
ones it had not examined. Every repair was verified against the target
rather than taking the reported number:

| Citation | Claimed | Actual |
|---|---|---|
| `sizing.py` `ram_max` | `scheduler.py:1109-1111` | `:1142` |
| `sizing.py` `ram_available` | `scheduler.py:1114-1120` | `:1152` |
| `sizing.py`, `test_nodes.py` affinity tests | `(:128)`, `(:291)` | `:124`, `:282` |
| `test_saturation.py` cap stage | `scheduler.py:643-655` | `:661-670` |
| `test_saturation.py` cap published | `scheduler.py:1058` | `:1073-1074` |
| `test_saturation.py` list prefilter | `external_api/instance.py:485` | `:589`, with `Instance.ACTIVE_STATES` in `shakenfist/instance.py:303` |
| `base.py` 507 contract | `scheduler.py:540`, `instance.py:901-906` | `:527`, `:1146` |
| `test_nodes.py` `_database_nodes()` | `database_tier.py:130-142` | `:137` |
| `test_saturation.py` skip idiom | `test_nodes.py:111`, `:116-122`, `:125` | none of the three |

Since these have now rotted twice in one week, the repair names the symbol
and drops the number wherever a symbol exists, which is durable. One
citation 7e reported as rotted was not: `sizing.py:202`'s
`scheduler.py:1052-1057` for the `is_hypervisor` and
`UNREASONABLE_QUEUE_LENGTH` filter is correct. And one of its sub-claims
was a misreading: `test_saturation.py`'s comment says the
`cpu_max_per_instance` stage is *earlier than* `sufficient_idle_cpu`, not
that it is it. Only the line number was wrong.

**F-M4. `sizing.py` cited a file this repository does not contain.**
`ansible/ci-topology-slim-tier.yml:65` lives only in `shakenfist/actions`.
Every other cross-repository citation in this plan's code names the
repository and flags the unpinned `@main`; this one did neither, so a
reader searches for a file that is not here. Brought into line.

### The two blocking rules, vacuous with evidence (7c)

`git diff <range> -- '*.py' | grep -E '^\+[^+].*get_all_[a-z_]+\('` returns
zero in all fourteen ranges. No range adds a MariaDB call site at all --
every added line matching `mariadb\.` is a `self.mock_mariadb.*` in a unit
test or one docstring sentence -- so the judgment-level pushdown case has
no new caller to reach through. No range touches
`shakenfist/schema/*_attributes.py` or adds an `add_*`/`remove_*` mutator
pair, so the cached-FK-list rule is vacuous. Zero new MariaDB functions and
zero proto changes, so the three-layer pattern raises no question.

The TODO/FIXME/HACK/XXX sweep and the `# noqa` / `# type: ignore` /
`pragma: no cover` sweep are both **empty across all fourteen ranges**, so
there was no triage list to grade.

### The product code, read as product code (7c, 7f)

The plan's phase 7 section claimed this change was not product code.
`shakenfist/scheduler.py` is in three ranges and `shakenfist/config.py` in
one, and two of those added fields to the `/admin/resources` response body.

**`cpu_limit`, published deliberately as `None` rather than falling back:
every consumer handles it, and none collapses the distinction.** Both
lenses enumerated them independently and agreed -- `sizing.py`'s
`effective_cpu_ceiling()` and `hypervisor_ledger()` (whose docstring names
`cpu_limit or 0` as the defect it avoids, because that would read a healthy
cluster as a zero ledger for its first three minutes), `retries.py`'s
`node_available_cpus()`, `ci_headroom_report.py`'s `NodeSample` (which
keeps the fallback *and* counts it), `test_saturation.py` (which skips
rather than guesses), and the scheduler's own pre-filter, which does not
read the API field at all. `ci_headroom_report.py`'s `numeric()` also
rejects `bool`, so a field arriving as `True` reads as absent rather than
as 1.0.

**`capacity_degraded`** preserves its tri-state correctly in
`ci_headroom_report.py` -- absent key is not `False`, and the two are
counted apart. The two cluster-CI consumers collapse absent to false, which
is harmless because the suite ships with the server it tests, but neither
says so. Advisory, not filed separately; it is one comment.

**`config.py`'s help text is accurate, verified against the template.**
`NODE_CPU_RESERVATION_THREADS`'s new "doubling it on network and database
nodes" matches `examples/_shared/site.yml`'s `(1 + ternary(1,0)) * 2`, and
`NODE_RAM_RESERVATION_GB`'s "adding a bump" matches `max(2.0, 10%) + 4.0`.

**The swagger drift phase 1 anticipated did not happen**, and could not:
`admin_resources_get_example` is a literal `{...}`, so there is nothing to
drift. Recorded because it looks like a finding.

### Security (7f) -- no critical, no high

`git diff <range> -- shakenfist/external_api/` is **empty for all fourteen
ranges**, as is `shakenfist/db*/` and `protos/`. The endpoint's
`@api_base.caller_is_admin` decorator is untouched and requires both
`request_namespace() == 'system'` and `ADMIN` scope, so namespace
membership alone does not reach it. Neither new field is a new category of
information: `cpu_hard_max`, `cpu_measured`, `cpu_committed` and
`cpu_committed_row_present` were already published from the same inputs.

**The probe's `sudo chown -R /srv/ci/traces` cannot be redirected.** The
path is a literal; no matrix value, input or repository variable reaches
it. `/srv/ci` is a root-owned mount point over `/dev/vdc`, so nothing
unprivileged can pre-create `traces` there, and GNU `chown -R` defaults to
`-P` and will not follow a symlink it meets. `inputs.base_image_user` is a
YAML literal at all four call sites and the matrix jobs are unreachable
from a fork pull request.

**Nothing sensitive reaches the ninety-day artifact.** The probe writes
four keys and never reads `os.environ` beyond constructing the client;
`SHAKENFIST_KEY` never enters a record. Its `error` key renders
`APIException.args`, which carries the server's error body and not the
`Authorization` header. `summarize_resources()` is keyed by node name and
publishes only scalars -- no namespace, instance or tenant field. The probe
deliberately reduces each roster entry to five keys rather than banking the
full `external_view()`, which would have carried `ip` and
`spice_server_cert_subject`; that instinct is worth recording.

**The Loki census discloses nothing new**, which was not the expected
answer. Its lines do carry namespace names and instance UUIDs, but
`ansible/ci-gather-logs-loki.yml` already puts an *unfiltered*
`{job="shakenfist"}` dump of up to 200,000 entries in the same bundle, so
the census is a strict subset of a stream the bundle already carries, from
a cloud destroyed at end of job.

**The nested-zip extraction is correct, and for a better reason than
validation.** `extract_traces()` never uses an archive-supplied name to
build a path: it iterates three module constants, uses the namelist only
for a membership test, and joins `os.path.basename(constant)`. The classic
traversal has no entry point.

**No SQL of any kind in any range** -- grepped for `text(`, `SELECT`,
`INSERT`, `UPDATE`, `DELETE FROM` and `execute(` across every added line;
the only hits are prose in comments. Two `subprocess` calls, both argv
lists, no `shell=True` anywhere, no `os.system`.

Four low findings filed as #4412.

### Test coverage (7d)

Beyond F-B2, two questions came back clean with their evidence.
`test_headroom_gate_workflow_seams.py` genuinely derives each call site's
shape from the workflow YAML, following `${{ matrix.X.Y }}` into the real
matrix entries and failing loudly on anything it cannot resolve to a
concrete value, rather than pinning literals; its hardcoded
`MEASURED_SHAPES` is correct by design, being the warn window's ground
truth rather than a derived value. `test_saturation.py` asserts the refusal
contract through structured `stage` and `transient` fields and real ledger
arithmetic against a node it actually fills and releases; the two plain
substring matches are the only paths carrying no structured fields to
assert on, and their docstrings say so.

Remaining gaps filed as #4413.

### Documentation (7e)

**The 12-versus-24 trap is clean.** D7's annotation is present at
`PLAN-ci-cloud-sizing.md:128-135`, saying in terms that the baseline table
is a measurement record and not the current state, and pointing at
`ci_cloud_sizing.md` for the live figures. No present-tense "12" survives
outside `docs/plans/`: the two hits in the swept paths are an unrelated
incident report about a 12 vCPU *instance*, and `sizing.py`'s own
explicitly-historical framing immediately above `MINIMUM_HYPERVISOR_LEDGER
= 24`.

`README.md` and `ARCHITECTURE.md` are untouched by every range. `AGENTS.md`
gained exactly one table row, a curated link to the new page, which is the
pattern the shared block wants rather than growth to flag. No range adds an
ASCII box-and-arrow diagram; every fenced block added is a shell command,
an equation or a figures listing. No `phase <number>` reference in any
non-plans markdown file. No schema change in any range, so migration
guidance does not apply -- confirmed by `git diff --name-only` against
`shakenfist/schema/*`, `migrations/` and `protos/database.proto`, not
assumed.

D5's two plan-document-only merges introduced no false claim. `9eaf4af34`'s
"12 to 18 under-cloud vCPU" is a different figure from the 12-to-24
*ledger*, and both are accurately stated in their own homes.

### The instrument's failure surface (7g, D3) -- the lens that earned its place

**The band is not fitted to under-measured data.** Across all 236
summarised records in `docs/plans/data/ci-cloud-sizing-baseline/`:
`samples_failed` is 0; no record has fewer than 20 usable samples (minimum
51); `capacity_degraded_samples` is 0; `census.truncated` is never true;
and **every record's mean sample spacing is 15.000s to within 10µs**. That
last figure is the load-bearing one: it proves no line was dropped
mid-series and no sample ever overran its slot, which are the two modes
that would bias the p90 low. The management session reproduced this
independently from the committed files.

So the modes the instrument's counters can see did not occur in the fit
window. **But nothing would have told anybody if they had**, and the lens
produced the full list of modes graded sound, undocumented or defect. The
four defects became F-B3, F-B4, #4410 and the probe docstring in #4413. The
"undocumented" rows share one cause and are #4409: a deliberate swallow,
honestly recorded, with no consumer.

Three things the counters cannot see, recorded because they bound what the
clean result above means:

* **The ledger itself.** The fraction is `max(cpu_measured, cpu_committed)
  / cpu_limit` and trusts the reconciler's row. A ledger that
  systematically under-counts makes every cluster look emptier and leaves
  no trace in the durable dataset. Server-side fidelity, outside this
  plan.
* **The warm-up exclusion.** 10.6% of samples -- the first 135-210s -- are
  excluded. That prefix almost certainly overlaps venv and pip setup rather
  than test execution, but `sampled_at` is not aligned to the test step's
  start anywhere in the record, so that is inferred from step order rather
  than measured.
* **Coverage.** No record carries the test step's duration, so a series
  covering part of a job is indistinguishable from one covering all of it.
  The banked windows (750-2,565s) are consistent with full coverage and
  nothing asserts it.

**A correction to this plan's own D3 text**, found by the lens: a typo in
the `CI_HEADROOM_GATE` repository variable **arms** the gate, it does not
disarm it. `functional-tests.yml` passes `${{ vars.CI_HEADROOM_GATE !=
'false' }}`, so only the literal `false` turns it off and `off` or `0`
leave it armed; `ci_headroom_verdict.sh` meanwhile accepts
`0|false|no|off|...` as off, so its header over-promises a flexibility the
caller layer does not honour. Both expressions were verified directly. The
hazard is the reverse of what this plan assumed, and it is the safer
direction.

### The `shakenfist/actions` half (7h, D2)

D2's premise held exactly: no `PUSH-AUDIT.md` in that repository, confirmed
by `gh api` (404) and `git ls-tree origin/main`. Audited read-only over the
three ranges; four issues filed there, nothing fixed from here.

**shakenfist/actions#127 is the finding worth the step.** `test_kind` is an
unvalidated `type: string` and all seven dependent steps are equality tests
with no catch-all arm, so `test_kind: ansible_modules` -- an underscore
instead of a hyphen -- skips the traces directory, the probe, the mesh
authorisation, both test steps, the slowest-tests listing and the collect
step. The job deploys a full topology, runs no tests at all, and passes.
`Log input details` echoes seven inputs but not `test_kind`, so the only
evidence is the skip pattern. Same bug class as the traces-directory gate
one level up: there a green run measured nothing, here it tests nothing.
`test_kind` predates the three audited merges, but #120 is what made the
input multi-valued, which is how it surfaced.

Also filed: #128 (a withheld verdict and a probe that produced nothing are
invisible outside the step log -- the other half of #4409), #129 (the Loki
census discards `curl`'s error and never checks the HTTP status), #130 (the
absent `PUSH-AUDIT.md`, with a proposed repository-specific wave 1 and the
two wave-2 headings this audit had to invent: unpinned-`@main` fan-out, and
gate exhaustiveness -- the second produced #127).

**That repository's CI does run its shell tests**, which was a yes/no worth
asking: `python3 -m unittest discover -s tests -t . --verbose` in
`ci.yml`'s `unit-tests` job, with `python3-yaml` installed first because the
test imports it at module scope, plus a `.pre-commit-config.yaml` local
hook. Its 16 cases assert on *resolved* workflow expressions rather than
literals, which is unusually good for shell tests.

Three premises in that step's brief did not hold, and are recorded because
this plan wrote them: `ci_headroom_verdict.sh` contains no ratio arithmetic
at all (no `bc`, no `awk`, no float comparison -- the band maths is all in
the Python half); neither shell script uses `set -e`, so "a `|| true` leaves
no failure path" does not apply, and failures are handled explicitly with
`if ! scp` and `status=$?`; and quoting is clean throughout, with
`ssh_opts` and `report_args` as arrays and the headroom label base64'd
before it crosses `ssh`.

### Mutation testing

Every property claimed above was mutated and the mutation confirmed caught,
naming the right test. Seven mutations, all caught:

| Mutation | Caught by |
|---|---|
| `paginate` stops after page one | `test_a_run_reachable_only_on_a_later_page_is_in_the_window` |
| `paginate` never advances the page | `test_a_short_page_ends_the_walk` |
| `paginate` spends a call past the short page | `test_every_page_is_walked` |
| the usable count counts every record | `test_the_usable_count_counts_only_records_with_samples` |
| the usable count is never incremented | same |
| the empty-census tri-state is removed | `test_a_census_which_read_no_log_lines_cannot_say_there_were_none` |
| the tri-state fires for every census | `test_a_capacity_refusal_is_a_warning_of_its_own` (pre-existing) |

The last is the one worth noting: it confirms the fix did not make every
run's refusal warning unknown, and it was caught by tests that already
existed.

### Spot-checks (D8, two per agent)

| Agent | Checked | Result |
|---|---|---|
| 7b | `ci_headroom_report.py` really is 3,108 lines; `probe.py`'s imports | both confirmed; the line count coincidentally equals a range's insertion count, and the management session's suspicion of conflation was wrong |
| 7c | the docstring-versus-code contradiction; the `(D26)` count | confirmed; 32 occurrences, 23 in `skipTest`/`fail` calls |
| 7d | `FakeGitHub.paginate()` yields one page; `GitHubCLI` absent from tests | both confirmed |
| 7e | `sizing.py`'s `ram_max` citation; the printed decision letters | both confirmed; 16 `print`-embedded citations found by the check, 25 sites in total once annotations and the step summary were included. Nine more were found afterwards by enumerating string literals rather than grepping -- see F-M1 |
| 7f | the zip-extraction allowlist; the untyped `artifact['id']` | both confirmed |
| 7g | the cadence and failed-sample figures over all 236 records; the gate expressions in both layers | both confirmed; its record count of 204 + 32 was right and the management session's 249 was the raw line count |
| 7h | the unvalidated `test_kind` and all seven gates; whether CI runs the tests | both confirmed |

### Dispositions

| Finding | Grade | Disposition |
|---|---|---|
| F-B1 docstring states the opposite of the code | blocking | fixed here |
| F-B2 `paginate()` page walk untested | blocking | fixed here, 5 tests |
| F-B3 all-absent window reports success unqualified | blocking | fixed here, 2 tests |
| F-B4 empty census reads as no refusals | blocking | fixed here, 4 tests; first fix was wrong, see above |
| F-M1 34 printed plan citations | mechanical | fixed here |
| F-M2 20 runtime `(D26)` messages | mechanical | fixed here |
| F-M3 nine rotted line citations | mechanical | fixed here |
| F-M4 cross-repository citation unmarked | mechanical | fixed here |
| no consumer for the instrument's records | advisory | [#4409](https://github.com/shakenfist/shakenfist/issues/4409) |
| warn window not committed, expires December | advisory, deadline | [#4410](https://github.com/shakenfist/shakenfist/issues/4410) |
| ~390 plan citations in comments | advisory | [#4411](https://github.com/shakenfist/shakenfist/issues/4411) |
| harvest hardening, four low security findings | advisory | [#4412](https://github.com/shakenfist/shakenfist/issues/4412) |
| four test gaps, incl. the probe docstring | advisory | [#4413](https://github.com/shakenfist/shakenfist/issues/4413) |
| `report.py` 3,108 lines, no stated reason | advisory | [#4414](https://github.com/shakenfist/shakenfist/issues/4414) |
| unrecognised `test_kind` passes a test-free job | medium | shakenfist/actions#127 |
| withheld verdict invisible outside the step log | medium-low | shakenfist/actions#128 |
| census discards `curl`'s error | low | shakenfist/actions#129 |
| no `PUSH-AUDIT.md` in `shakenfist/actions` | low | shakenfist/actions#130 |

No blocking finding remains open. #4367 and #4320 were out of scope by this
plan's own Scope section and are not re-reported.
