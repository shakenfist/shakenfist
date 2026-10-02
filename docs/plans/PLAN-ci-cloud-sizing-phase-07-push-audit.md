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

*(Written by 7i. Empty until the audit runs.)*
