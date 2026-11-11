# Phase 13: the push audit

Phase 13 of [PLAN-bat-population.md](/components/instar/plans/PLAN-bat-population/), the last
row of its Execution table. Read the `plan-push-audit-phase` shared
block in the master plan before anything else: it defines what this
phase is for and why its scope cannot be derived after the fact. Then
read `PUSH-AUDIT.md`, which is the runbook this phase executes.

This phase audits the accumulated work of phases 1 to 12 and **changes
no code**. Its steps are readers. The management session writes the
record, which is the commit.

Planning effort: medium. The judgement in a push audit is in scoping it
and in triaging what comes back, not in inventing a method -- the
runbook and the scope tooling both exist, and a previous push-audit
phase in this repository has already been through the traps. What this
plan mostly has to get right is the commit list, and that it is honest
about the parts it cannot cover.

Review effort: high, concentrated in one place. An audit that reports
nothing is indistinguishable from an audit that ran over nothing, and
this phase has a live instance of exactly that failure (see the survey).
The review that matters is of the resolved scope, not of the findings.

## Scope

In: the union of the first-parent diffs of the twelve merge commits that
put phases 1 to 12 on `develop`, which resolves to **78 files**; and the
three Python scripts phase 10 landed in `instar-testdata`.

Out: fixing what the audit finds. Findings land as their own pull
request after this one merges, per the shared block and the
`implement-phase` skill. Also out: the 17,271 generated files in
`instar-testdata` (decision 3).

The commit list, which is the whole of this phase's scope and the one
thing a reader should check first:

```
fef62417  phase 1   #809
5c5bba75  phase 2   #819   (instar half; see decision 4)
a8d2f4ca  phase 3   #824
d140aa04  phase 4   #835
52518787  phase 5   #848
aef757d2  phase 6   #853
03269d7f  phase 7   #858
e49b6275  phase 8   #874
3b74ba8d  phase 9   #877
e0b0fe13  phase 10  #881   (instar half; see decision 2)
41f74d40  phase 11  #884
2da0c22e  phase 12  #889
```

Plus, in `instar-testdata`, the recovered range `99c8663c34..f73d43670a`
(decision 2), of which only `scripts/detect-profiles.py`,
`scripts/generate-baselines.py`, `scripts/validate-profiles.py`,
`README.md` and `Makefile` are in scope.

## What the survey found

The master plan's phase 13 row is one line -- "Push audit:
`PUSH-AUDIT.md` over every phase above" -- so there was little to be
stale. The survey's work was establishing that the scope is
constructible, and it found one thing wrong and one thing missing.

### Every recorded merge commit is real, and the record is complete for `instar`

All twelve are two-parent merge commits, all are ancestors of
`origin/develop`, and all name a `bat-population` pull request. The
shared block warns that phases which landed before a plan started
recording them have to be reconstructed from `gh pr list` and
`git rev-list`; none of that is needed here. This is a real result and
the reason this phase can be planned at medium effort.

### The default audit scope is not empty here -- it is wrong, which is worse

`PUSH-AUDIT.md` says a push-audit phase must pass `--commits-file`,
because `develop...HEAD` covers only what the audit branch adds and an
empty range reads as a clean audit. On this machine it is worse than
empty. The local `develop` branch is stale at `e0b0fe13`, four merges
behind `origin/develop`, so the implicit scope resolves to **47 files**
-- drawn from the renovate bumps, a nightly-toolchain bump and the
assurance pull request that landed between phases, and containing almost
none of phases 1 to 11.

An empty scope invites suspicion. A plausible-looking 47-file scope of
unrelated work does not. Measured:

```
audit scope: 47 files (develop...HEAD)                 <- wrong
audit scope: 78 files (--commits-file phase-merges.txt) <- correct
```

### Phase 10's `Merged` cell is missing its `instar-testdata` half

The shared block requires a phase that landed in another repository to
record `<repo> <sha> (#pr)`. Phase 2 did: its cell reads
`instar-testdata d2f9467719 (direct to main); instar 5c5bba75 (#819)`.
Phase 10 did not -- its cell names only `e0b0fe13` (#881), although four
of its steps (10a to 10d) landed work in `instar-testdata`: the aimable
baseline generator, this plan's create cases, the derived-profile
validator and the per-image fixture baselines.

The range is recoverable, which is the good case the shared block hopes
for rather than the "no range is recoverable" case. Six contiguous
commits on 2026-11-07 in that repository, `99c8663c34..f73d43670a`,
sitting cleanly after the 2026-11-06 nightly fuzz-corpus commits and
before the next day's. They match steps 10a to 10d one for one. This
phase records that range in phase 10's `Merged` cell as a correction at
source.

### `instar-testdata` has no `PUSH-AUDIT.md`, so there is no audit to cite

The shared block says a phase landing in another repository is audited
against that repository's default branch as part of the pull request
that lands it, and that this plan's push-audit phase cites that audit
rather than re-running it. Neither half of that is available here.
`instar-testdata` has no `PUSH-AUDIT.md` -- its `audit/` directory holds
`run-qemu-img-audit.py`, a qemu-img capability audit, which is a
different thing entirely -- and that repository takes direct commits to
`main` rather than merge requests, so there was never a pull request to
audit within. The shared block's instruction for a repository with no
runbook is to say so and say what was done instead, which this phase
does, and decision 3 sets what is done instead.

### The instrument already works, and its limits are known

The scope mechanism this phase depends on was built by a previous
push-audit phase in this repository, `PLAN-differencing-phase-18-push-audit.md`,
whose own step 1 was to give the wave scripts an explicit scope.
`tools/audit/audit-scope.sh`, `wave1.sh` and `wave2-mechanical.sh` all
exist, with tests beside them. `audit-scope.sh` is sourced rather than
executed -- running it directly prints nothing and exits quietly, which
cost this survey a wrong answer before the file's own header corrected
it, and which the step briefs therefore state outright.

That phase also recorded what its audit could not reach, and this phase
inherits the same limits: `wave1.sh` has no integration-test step, so the
only Python executed is whatever the test-review step runs itself; and
the mutation harnesses are not run, because a full run needs docker,
testdata and `/dev/kvm` together.

### Scope shape

The 78 files, by area, so the judgement steps can be briefed concretely:

| Area | Files |
|---|---|
| `docs/` | 30 |
| `tests/` | 17 |
| `src/crates/` | 8 |
| `src/operations/` | 5 |
| `src/fuzz/` | 5 |
| `tools/` | 4 |
| `scripts/` | 2 |
| `src/vmm/`, `src/shared/` | 1 each |
| `ARCHITECTURE.md`, `AGENTS.md`, `CHANGELOG.md` | 1 each |
| `.github/`, `.gitignore` | 1 each |

### Corrected at source

Phase 12's row is set to `Complete` with `2da0c22e` (#889), and phase
10's `Merged` cell gains its `instar-testdata` range, in this phase's
first commit.

## Decisions

1. **The audit runs with `--commits-file`, never the default scope, and
   the resolved file count is reported before any check runs.** This is
   `PUSH-AUDIT.md`'s own instruction, and the survey found the default
   resolves to 47 files of unrelated work on this machine rather than to
   nothing. Every step that reads files states the count it saw, so a
   step that silently audited the wrong set is visible in its own
   report.

2. **Phase 10's `instar-testdata` range is recorded in the master plan
   as `99c8663c34..f73d43670a`, and the recovery is described as a
   recovery.** The shared block requires reconstructed ranges to be
   labelled as reconstructed; this one is clean enough to state as fact,
   but how it was derived -- six contiguous commits between two nightly
   fuzz-corpus runs, matching steps 10a to 10d -- goes in the plan so
   the next reader can falsify it rather than trust it.

3. **The audit reads `instar-testdata`'s three scripts and runs its
   validator; it does not read the 17,271 generated files.** That range
   changes 17,276 files, of which 17,271 are generated `expected-outputs`
   data and five are code or documentation. Reading generated data is not
   an audit, and 17,000 files would swamp the phase and the agent's
   context both.

   This is the decision most likely to be argued with, and the argument
   against it is good: the derived profile directories in that
   repository have been silently wrong twice while the raw baselines were
   fine and the suite stayed green, so "the data is generated" is
   precisely not a reason to trust it. The answer is that phase 10 added
   `scripts/validate-profiles.py` for exactly that reason, and the
   useful audit is of the validator and of whether it actually runs --
   not of its inputs one file at a time. So the step reads the three
   scripts as code and *runs* the validator, and reports its exit status
   and what it covers. If the validator turns out not to cover the
   generated set, that is a finding, and a better one than any number of
   hand-read baselines.

4. **Phase 2's `instar-testdata` half is cited, not re-audited.** Its
   cell already records `d2f9467719`, and that work is five fixtures
   plus a generator, superseded in place by phase 10's changes to the
   same scripts, which decision 3 does cover. The phase says so rather
   than implying coverage it does not have.

5. **Findings are recorded and fixed in this phase.** This reverses
   what this plan originally decided, on Mikal's instruction at the back
   brief. The shared block and the `implement-phase` skill both say a
   push audit records its findings and a follow-up pull request resolves
   them, and the reason is that an audit-plus-fixes diff stops being
   reviewable as an audit. That cost was put to him and he chose to
   accept it, so the fixes land here and there is no follow-up pull
   request to wait for.

   Two consequences follow and are handled rather than left implicit.
   This phase therefore *does* change code, so the definition-of-done
   item asserting it changed none is replaced by one bounding what it
   may change. And this phase's own pull request is what marks the plan
   `Complete`; its `Merged` cell stays empty, because a phase cannot
   know its own merge commit while it is being written, and whoever
   next touches the plan fills it in. Two of the three push-audit
   phases already in this repository carry a filled cell and one is
   empty; the filled ones were filled afterwards, which is the same
   thing.

   The audit steps themselves are still readers. Nothing in 2a to 2e
   may change a file: findings come back as reports, the management
   session triages them, and the fixes are made afterwards as their own
   commits so that the audit and its remedies stay separable in the
   history even though they share a pull request.

6. **The four judgement steps run in parallel.** They are read-only, so
   they cannot collide in the worktree, and none consumes another's
   output. This is the one place a push-audit phase is cheaper than an
   implementation phase.

## Step plan

Step 1 runs first and alone: the later steps consume its resolved scope
and its mechanical report. Steps 2a to 2e then run together. Step 3 is
the management session.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | Materialise the audit scope and run the mechanical waves. Write the twelve merge commits from this plan's Scope section, one per line, to `audit-commits.txt` in the session scratch directory (NOT in the worktree). Then run `tools/audit/wave1.sh --commits-file <that file>` and, if wave 1 passes, `tools/audit/wave2-mechanical.sh --commits-file <that file>`. Note that `tools/audit/audit-scope.sh` is *sourced* by those scripts, not executed -- do not try to run it directly, it prints nothing and exits quietly. Report the resolved file count each script prints; it must be 78, and if it is not, stop and say so rather than continuing, because a different count means the scope is wrong. For the test totals, read the `Ran:` and `Failed:` lines rather than trusting the exit code, which is unreliable in this repository. Save both scripts' full output to the scratch directory and give the paths. Change no file in the worktree. |
| 2a | high | opus | none | Wave 2a, code quality, per `PUSH-AUDIT.md`'s 2a section, over the Rust in the audit scope: 8 files under `src/crates/`, 5 under `src/operations/`, 5 under `src/fuzz/`, and one each in `src/vmm/` and `src/shared/`. Resolve the scope with `--commits-file` as step 1 did and state the count you saw. The thing to look for across twelve phases is what the phases did to each other rather than what each did alone: a helper duplicated once two phases had both landed, a constant defined twice, an invariant one phase established and a later one quietly weakened. The VHDX layout work was deliberately consolidated into one shared function during the plan, so check it really is one. Report findings with file and symbol, severity, and the evidence; report "nothing" explicitly if that is the answer. Change no file. |
| 2b | high | opus | none | Wave 2b, tests, per `PUSH-AUDIT.md`'s 2b section, over the 17 files under `tests/` in the audit scope, the crate test modules inside the scope diff, and `src/fuzz/`. Resolve the scope with `--commits-file` and state the count. Five fuzz targets and a differential-fuzzer arm were added across the plan; confirm each is registered everywhere it must be (the `.rs` file, `src/fuzz/Cargo.toml`, `coverage-fuzz.yml`'s `TARGETS`) and that `tools/ci/check-fuzz-targets.sh` passes. The specific risk this plan carries is a guard that cannot fail: phases 10 to 12 added classification tests and structural comparisons whose oracles are themselves in the diff. Pick the three you trust least and say for each whether a regression would actually fail it. You may run tests; you may not change any file. Note `wave1.sh` has no integration-test step, so anything you do not run here is unrun. |
| 2c | high | sonnet | none | Wave 2c, documentation, per `PUSH-AUDIT.md`'s 2c section, over the 30 `docs/` files in the audit scope plus `ARCHITECTURE.md`, `AGENTS.md` and `CHANGELOG.md`. Resolve the scope with `--commits-file` and state the count. Phase 12 swept the cross-cutting pages six commits ago, so the question is not whether they were updated but whether twelve phases left any fact stated two different ways -- the VHDX default block size, which subformats each command writes, and the preallocation behaviour are the three that moved most and are where to look first. Also check no file under `docs/` outside `docs/plans/` cites a source line number or a plan phase, step or decision number; both are pre-commit-enforced, so a hit is a real escape. Change no file. |
| 2d | high | opus | none | Wave 2d, security, per `PUSH-AUDIT.md`'s 2d section, over the parsing and writing paths this plan touched: the VHDX BAT and metadata paths in `src/crates/vhdx/`, the fixed-layout emitters in `src/crates/create/`, and the host-side option and size handling in `src/vmm/`. Resolve the scope with `--commits-file` and state the count. This plan writes attacker-influenced geometry into on-disk structures and reads it back, so the shapes that matter are: an arithmetic overflow in a BAT offset or entry count, a length used before it is bounds-checked, a cast narrowing a u64 to u32, and a guest-side buffer sized from image-supplied values. The crates are `no_std` and must be panic-free, so an unchecked arithmetic operation is a defect even where the value cannot currently reach it. Report with file, function and the input that would reach it. Change no file. |
| 2e | medium | sonnet | none | Audit the `instar-testdata` half. Work in the clone at `../instar-testdata` (branch `main`); do NOT create a worktree there and do NOT commit anything. Scope: the five non-generated files in `99c8663c34..f73d43670a` -- `scripts/detect-profiles.py`, `scripts/generate-baselines.py`, `scripts/validate-profiles.py`, `README.md` and `Makefile`. Read the three scripts as code (Python conventions: single quotes, 120 columns). Then RUN `scripts/validate-profiles.py` and report its exit status, what it printed, and -- the question that matters -- which of the 17,271 generated files under `expected-outputs/` it actually validates, and which it does not look at. Do not read the generated files individually; there are 17,000 of them and that is not an audit. Note this repository has no `PUSH-AUDIT.md`, so there is no runbook to follow and no prior audit to cite; say what you checked and what you could not. |
| 3 | high | — | none | Management session. Triage every finding from 1 and 2a-2e into blocking, fixed-in-this-phase, or filed-as-issue (fix-in-follow-up until decision 5's reversal), record the disposition of each in this plan under "Audit findings", write the "Coverage this audit did not have" section, and commit the record. |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| A step resolves the default scope and audits 47 files of renovate bumps, reporting a clean result | Every reading step is briefed to print the resolved count and to stop if it is not 78. Step 1 stops the phase rather than continuing. The management session checks the count in each of the six reports before triaging a single finding. |
| An agent runs `audit-scope.sh` directly, gets nothing, and proceeds against an empty set | Every brief states that the file is sourced, not executed, and that running it prints nothing. This is in the briefs because it happened during the survey. |
| The 17,271 generated testdata files swamp step 2e | The brief bounds it to five files and forbids reading the generated set, and redirects the effort to the validator's coverage, which is the useful question. |
| The audit reports nothing and that is taken as a pass | The phase records a negative result as a negative result, in one sentence per step, and the "Coverage this audit did not have" section is a required part of the record rather than an optional caveat. |
| A judgement agent fixes what it finds | Every brief says to change no file. The steps are readers; step 3 writes. A step that returns a diff has its diff discarded and its finding triaged. |
| Findings grow into a thirteenth implementation phase | Decision 5 as amended at the back brief: findings are recorded here and fixed here, and every fix must trace to a recorded finding or be reverted. That traceability rule is what bounds the phase, in place of the follow-up pull request the original decision relied on. |

## Definition of done

* `audit-commits.txt` holds exactly the twelve shas in this plan's Scope
  section, and for each, `git rev-list --parents -n1` shows two parents
  and `git merge-base --is-ancestor <sha> origin/develop` succeeds.
* Both mechanical scripts printed `audit scope: 78 files
  (--commits-file ...)`, and the count appears in step 1's report.
* Wave 1's result is recorded with its test totals read from the `Ran:`
  and `Failed:` lines, not from an exit code.
* Each of 2a, 2b, 2c, 2d and 2e produced a report that states the
  resolved file count it saw, and either findings or an explicit
  statement that it found nothing.
* Every finding is recorded under "Audit findings" with a disposition of
  blocking, fixed-in-this-phase, or filed-as-issue -- the middle one
  replacing fix-in-follow-up, which decision 5's reversal removed -- and
  every one filed as an issue carries its number. A master-plan Future
  work entry alone does not satisfy this: Future work records what is
  worth doing, an issue is what somebody is handed, and this plan's own
  findings include a consequence that was recorded in a plan and never
  reached either.
* The master plan's phase 10 row names the `instar-testdata` range, and
  its phase 12 row reads `Complete` with `2da0c22e`.
* The plan states in writing that `instar-testdata` has no
  `PUSH-AUDIT.md` and what was done instead.
* A "Coverage this audit did not have" section exists and names at least
  the integration suite and the mutation harnesses.
* Every file this phase changed outside `docs/plans/` is traceable to a
  recorded finding: for each path in `git diff --name-only
  origin/develop`, either it is a plan file, or the "Audit findings"
  section names the finding it fixes. A fix with no finding behind it is
  scope creep and is reverted.
* Phase 13's row leaves its `Merged` cell empty. A phase cannot know its
  own merge commit while it is being written, and decision 5's reversal
  removed the follow-up pull request that would have filled it, so
  whoever next touches the plan fills it in.

## Execution notes

Run 2026-11-11 (+1100), in one session, on branch
`bat-population-phase-13-push-audit` from `2da0c22e`.

### Back brief answers

| Question | Answer |
|---|---|
| Keep the generated `instar-testdata` baselines out of scope? | Yes, audit the validator instead |
| May step 2e run `validate-profiles.py` in that clone? | Yes |
| Findings: record here, or fix here? | **Fix here** -- see below |
| May this session push and open the pull request? | Yes |

### The departure, and what it cost

The third answer reverses this plan's decision 5, which had findings
recorded here and resolved in a follow-up pull request, as both the
shared block and the `implement-phase` skill prefer. The reason they
prefer it is that an audit-plus-fixes diff stops being reviewable as an
audit. That was put to Mikal with the recommendation against it and he
chose to accept it, so the reversal is recorded in its own commit
rather than applied quietly, and two consequences were handled rather
than left implicit: the definition-of-done item asserting this phase
changes no code was replaced by one requiring every changed file to
trace to a recorded finding, and this phase's own pull request is what
marks the plan `Complete`, there being no follow-up to do it.

The audit steps stayed readers throughout. Nothing in 2a to 2e changed
a file; the fixes were made afterwards, so the audit and its remedies
are separate commits even though they share a pull request.

### Step order

Step 1 ran alone, because the five judgement steps consume its resolved
scope and its mechanical report. 2a, 2b, 2c, 2d and 2e then ran
concurrently -- they are read-only, so they cannot collide in the one
worktree, which is the single way a push-audit phase is cheaper than an
implementation phase. The two fix agents ran concurrently on disjoint
files and were told not to run `pre-commit`, which the management
session ran once over the finished tree instead.

### What the scope check was worth

Every one of the six steps printed `audit scope: 78 files` and said so
in its report. That mattered: on this machine the default scope is not
the empty range `PUSH-AUDIT.md` warns about but 47 files of unrelated
work, because the local `develop` branch is four merges stale. A step
that had silently resolved it would have audited renovate bumps and
reported them clean. One step also noted, correctly, that the line
numbers in the pre-computed added-line extracts are per-commit and
have shifted at HEAD, and re-derived its own from HEAD plus
`git blame`.

## Audit findings

Ran 2026-11-11 (+1100). Scope resolved to **78 files** from the twelve
merge commits, confirmed in both mechanical scripts' banners and in each
of the five judgement steps' reports. Wave 1 passed: `make test-rust`
gave 78 cargo suites, 2,800 tests, 0 failures, and
`verify-info-output-parity` compared 218 images with 0 failures.

The counts below separate what this plan introduced from what it
inherited, because the distinction decides the disposition: a defect in
the plan's own work is a finding and was fixed here, and a defect older
than the plan is an issue to file. `git blame` against the twelve merges
decided each one.

### Fixed in this phase

* **A test that could not fail.** `test_known_failures_are_in_the_matrix`
  in `tests/test_vhdx_fixed_fixtures.py` policed `KNOWN_READ_FAILURES`,
  which is empty by design, so its body reduced to
  `assertEqual([], [])` and it passed against any matrix -- including one
  that had stopped running. The known-failure branch of `_check_read`
  had no coverage at all for the same reason. Added by `fc5e982e` in
  phase 4's merge, so this plan's own. Both checks are now pure
  functions, `unrun_known_failures` and `known_read_problem`, proven
  against literal stale entries in each of the ways they must catch, in
  the shape `known_refusal_problem` in `tests/test_create.py` already
  used for an identically empty dict. Six mutants, each killed by
  exactly the intended test. All six were re-run in the review round, on
  a copy of `tests/` with a control run first so that a mutant could not
  look killed by a broken setup: `unrun_known_failures` returning
  nothing, matching on the image where it must match on the operation,
  and defaulting the matrix lookup to the operation it is testing for;
  and `known_read_problem` accepting a read that now passes, accepting a
  failure with the wrong message, and rejecting the recorded failure it
  must accept.

* **A duplicated host helper.** Phase 8 added
  `open_fixed_vhdx_convert_output`, which re-implemented phase 7's
  `open_fixed_vhdx_output` and inlined both of
  `fixed_vhdx_open_error`'s message shapes with a `convert:` prefix in
  place of `create:`. The plan had already solved this shape:
  `check_vhdx_virtual_size(op, virtual_size)` takes the operation
  prefix and serves five call sites. A second-order cost came with it --
  create's separately-testable error builder had no convert equivalent,
  so the convert wording was untested. Now parameterised by `op`, with
  the messages proven unchanged for both.

  The fix changed `tools/mutate-differencing.sh` as well, which is why
  that file appears in this phase's diff. Collapsing the two open bodies
  into one left the create mutant's search text stale and deleted the
  convert mutant's target, so both of the
  `*-host-vhdx-fixed-efbig-mapping-removed` cases were retargeted at
  the shared body. They now carry identical
  search and replacement text and differ only in the test each names:
  one code path can break, and create's and convert's integration tests
  must each notice it, because sharing the opener would otherwise have
  left convert's filesystem-ceiling refusal resting on create's test.
  `--check-patterns` reported the drift and now reports 153 of 153
  landing.

* **The resize preallocation modes, stated two ways.** This plan added
  text to `docs/create.md` and `docs/quirks.md` saying
  `resize --preallocation falloc|full` allocates the appended region on
  vmdk, vpc and vhdx. Three older tables said only `off` was supported
  there and that `falloc` and `full` were "rejected (planner gap)":
  `docs/resize.md`'s target-format and preallocation-mode tables, and
  `docs/format-coverage.md`'s resize table. The code agrees with the new
  text -- the three resize crates refuse only
  `Preallocation::Metadata`, and `run_resize_nonraw` calls
  `apply_preallocation` for both modes -- so the tables were the stale
  side and are corrected. The stale tables predate the plan; the
  contradiction does not, because the plan added the claim that exposed
  them. `metadata` remains rejected and is left saying so.

  What the tables now advertise is barely tested: `RESIZE_CASES` has no
  preallocation case for vmdk, only `off` for vhdx, and `off` and `full`
  for vhd, so a host-side gate added later would fail no test and these
  tables would go stale again the same way. The review round filed that
  as #898 rather than fixing it here: qemu-img refuses these resizes, so
  there is no baseline to compare against, and the matrix mirrors
  `instar-testdata`'s generator, making it a cross-repository change
  rather than a correction.

* **The block-size range without its constraint.** `docs/convert.md`
  (twice) and `docs/format-coverage.md` gave the VHDX block size as
  1 MiB to 256 MiB without saying it must be a power of two, which
  `vhdx::layout` requires and five other pages state. Added by this
  plan.

* **An `unsafe fn` with no `# Safety` section.**
  `convert_to_vhdx_fixed` in `src/operations/convert/src/main.rs`. One
  was written naming the call-table and buffer invariants it actually
  relies on, bringing it up to the standard
  `src/operations/create/src/main.rs` holds, where all 8 `unsafe fn` are
  documented.

  The review of this pull request corrected the reason first recorded
  here, which was that create documents all of its own "so the new
  function was the outlier". That is true only across files. Within
  convert there are 22 `unsafe fn` and, with this fix, 2 `# Safety`
  sections: the file is the outlier, not the function, and the 20
  undocumented ones include `convert_to_vhdx`, the dynamic sibling
  immediately above. Filed as #897 rather than fixed here, because
  documenting 20 `unsafe fn` is its own piece of work and an audit is
  not the place to do it. The correction is kept visible because a
  misleading rationale in an audit record is what the next audit
  trusts.

### Filed, not fixed

Each of these has a finding behind it, an issue number, and a reason it
is not a fix. The issues were filed during this pull request's review
round: the five findings here were recorded as master-plan Future work
entries first, which this plan's own definition of done does not accept
as a disposition, and one of the five is precisely a consequence that
was recorded in a plan and never carried into an issue.

* **Nothing enforces the differential fuzzer's structural counters**
  (#892). Phase 11 added `STRUCTURAL_OUTCOMES` and `KNOWN_DEFECT_SKIPS` and
  reports them; nothing reads them. A nightly in which every structural
  comparison ended `no_pin_for_geometry` -- a pin set that stopped
  matching, which the code's own comment names as the risk -- logs one INFO
  line and exits 0. Making the counters a threshold anyone fails on is new
  capability, not a defect fix.

* **The structural oracle is wired to `op_convert` only** (#893).
  `op_create`, whose output is this plan's subject, still compares exit
  codes and `qemu-img info`. So the differential fuzzer never checks that
  an instar-*created* fixed VHDX has qemu's structure; that rests on the
  Rust fuzz target, whose oracle is a spec model rather than qemu, and on
  the Python pin tests. Possibly a deliberate phase 11 decision and in any
  case a new arm, not a repair.

* **`instar-testdata`'s profile validator has no gate** (#894). Phase 10
  added `scripts/validate-profiles.py` because that repository's derived
  profile directories had been silently wrong twice while the raw baselines
  were fine. It works -- 960 version-to-profile mappings, 163,932
  comparisons, 0 mismatches, reaching essentially all of the 17,271 files
  the plan generated -- but that repository has no CI, no pre-commit and no
  `tox`, so it runs only when somebody types `make validate-profiles`.
  Phase 10's plan recorded that the repository had no CI; the consequence
  that nothing would ever run the validator it was adding was never carried
  into Future work or an issue. That dropped follow-up is the finding, and
  recording it is its fix.

* **Two `instar-testdata` script defects** (#895), both added by phase 10.
  `generate-baselines.py` validates `--image` against the manifest and then
  silently skips images the per-command loop does not apply to, so
  `--command snapshot-list --image <valid non-qcow2 id>` generates nothing
  and exits 0 -- the exact failure the filter's docstring claims to close.
  And `validate-profiles.py` counts a profile whose name no source produces
  as hand-maintained, so a stale leftover from a deleted case is absorbed
  rather than reported. Both are in a repository that takes direct commits
  to `main` rather than pull requests, so they cannot land in this one.

* **Advisories from the code-quality and security reads** (#896). No named
  constant for the VHDX BAT entry size, where the crate names its three
  other table entry sizes and this plan added six more bare `8`s; four
  call-table mirror constants written as literals where a fifth in the same
  plan uses `shared::`; and `src/crates/vhdx/src/lib.rs` growing 34% to
  9,017 lines with a seam visible in its own banner comments.

### Negative results worth recording

These are the questions the audit asked and answered cleanly. A push
audit that records only what it found is half a result.

* **The format crates ship no `.unwrap()` or `.expect()`.** The plan
  added 63 across four crate source files, and every one is inside a
  `#[cfg(test)]` region: 42 in `src/crates/vhdx/src/lib.rs`, 14 in
  `create`, 5 in `measure`, 2 in `resize/src/vhdx.rs`. The same
  production-versus-test boundary was re-checked at all twelve merge
  commits, so no intermediate state shipped one either. These crates
  are declared panic-free, so this was the plan's sharpest open
  question.

* **No security finding.** The new write path is checked end to end:
  `vhdx::layout` and `fill_fixed_bat` use `checked_*` at every step with
  `u32::try_from` for both narrowings, and `fill_fixed_bat` proves
  `bat_entries * block_size + fixed_data_start` before writing a byte.
  No `as u32` on anything derived from `virtual_size` or `block_size`
  outside tests. No buffer is sized from an image-supplied value -- the
  guest BAT slot is a constant `MAX_SECTOR_SIZE`, and convert's buffers
  are sized from a host-validated sector size -- so nothing can overrun
  into the call table. Zero new host-side `unsafe`. Of the 44 added
  `unsafe` lines, 42 are test code and the two production ones are
  `unsafe fn` declarations, not blocks.

* **The VHDX layout really is one function.** `vhdx::layout` is the
  sole producer of `VhdxLayout`, and every writer and sizer reads
  fields off it rather than recomputing: both guest ops, three crates,
  four host call sites and the fuzz target. No independent region
  arithmetic survives outside the crate. The one direct
  `calculate_bat_layout` call outside it is for a sector size the layout
  does not model and says so.

* **Three guards were proven killable by mutation**, not read and
  trusted: the differential fuzzer's classification scan, the VHDX
  structural comparison, and the two fuzz targets, whose expected
  values are recomputed from the specification rather than from the
  code under test.

* **Documentation is internally consistent on the three facts that
  moved most.** The size-scaled block size, the subformat sets, and the
  `create`/`measure` preallocation behaviour each now read the same on
  every page that states them, checked against the code. No file
  outside `docs/plans/` cites a source line number or a plan phase,
  step or decision number; line-number citations were added during the
  plan and removed before it ended.

### Coverage this audit did not have

* **The Python integration suite was not run.** `wave1.sh` has no
  integration step -- its 2,800 tests are all cargo -- and this
  worktree has no `tests/.venv`. That leaves roughly 2,500 added lines
  of Python test code in `test_create.py`, `test_vhdx_fixed_fixtures.py`,
  `test_vhdx_structure.py` and `test_convert.py` unexecuted here. They
  ran in their own phases and in CI; they did not run in this audit.
  The in-scope Rust test modules *are* covered by wave 1.

  **The exception is this phase's own fixes**, which the review round
  ran rather than leaving to CI, because an audit whose remedies are the
  only unexercised part of the tree is arguing against itself. In a
  throwaway virtualenv built from `tests/requirements.txt`:
  `TestVhdxFixedFixturePins` 9 tests pass in 0.7 s, and the seven
  known-failure guards -- the one that could not fail before, plus the
  six new ones -- pass in 0.006 s. Neither needs docker, `/dev/kvm` or
  the instar binary, which is why they could run here; the rest of the
  module still cannot.
* **The full mutation harnesses were not run.** A full run needs docker,
  testdata and `/dev/kvm` together. Its pattern check does not:
  `tools/mutate-differencing.sh --check-patterns` reports 153 of 153
  landing, re-run in the review round after it had caught this phase's
  own refactor drifting both EFBIG cases. The audit's own mutants were
  run, on copies, in scratch directories.
* **The fuzz targets were not built.** `src/fuzz` is outside the cargo
  workspace, so `make lint` never sees it; `check-fuzz-targets.sh`
  confirms registration, not compilation. `coverage-fuzz.yml` fires on
  `pull_request` for those paths, so CI covers it.
* **`instar-testdata`'s 17,271 generated baselines were not read**, by
  decision. The validator was run instead and its reach measured.
* **The validator proves the derived profiles equal the raw baselines,
  not that the raw baselines are right.** It cannot detect a wrong raw
  baseline, and six output types have no version map and are skipped
  entirely -- none of them in this plan's range.

### Corrections the audit made to this plan's own text

Three claims in this plan or in the briefs written from it were wrong,
and each was caught by the step it was given to:

* **"Five fuzz targets were added across the plan."** One was:
  `fuzz_vhdx_fixed_bat.rs`. The scope table's "5 files under
  `src/fuzz/`" counts three modified targets and `Cargo.toml`.
  Confirmed by `--diff-filter=A` across all twelve merges.
* **"Both documentation rules are pre-commit-enforced."** Only
  `docs-line-references` is. There is no hook for plan-phase citations,
  so that rule rests on review, and the plan should not have implied
  otherwise.
* **One production `unsafe` was attributed to
  `src/crates/vhdx/src/lib.rs`.** It is inside that file's
  `#[cfg(test)] mod tests`, so 42 of the 44 added `unsafe` lines are
  test code and only two are production.

## Back brief

Answer before any step runs:

1. **Is decision 3 right to leave the 17,271 generated files unread?**
   The counter-argument is real: those derived directories have been
   silently wrong twice before. The plan's answer is to audit the
   validator rather than its inputs.
2. **Should step 2e run `validate-profiles.py` at all?** It is a local
   read-only script in another repository's clone, but it is still
   running something rather than reading it, and that clone is one the
   fuzz automation commits to daily.
3. **Is a follow-up pull request the right home for the findings**, or
   should trivially safe ones be fixed in this phase's own pull request?
4. **May this session push and open the pull request** when the record
   is written?

## Out of scope

* Nothing the audit finds is out of scope to fix, by the back brief's
  instruction -- but a fix still needs a finding behind it. Something
  noticed in passing that no audit step reported is filed, not fixed.
* The 17,271 generated `expected-outputs` files in `instar-testdata`
  (decision 3).
* Re-auditing phase 2's `instar-testdata` fixtures (decision 4).
* Writing a `PUSH-AUDIT.md` for `instar-testdata`. The gap is recorded
  here and is worth an issue, but authoring another repository's audit
  runbook is not this phase's job.
* The master plan's Future work list. The audit may add to it; it does
  not work through it.
