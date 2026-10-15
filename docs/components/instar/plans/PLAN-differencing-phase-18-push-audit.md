# Differencing phase 18: the push audit

## Prompt

Plan phase 18 of `PLAN-differencing.md`, the mandatory push audit.
Phase 17 landed in `60e4381c` (#659), which completes the plan's
substantive work: seventeen phases that pinned the semantics, built
parent-locator parsing, wrote two emitters, closed a silent
parent-ignoring read, built sector-granular composition in the guest
for VHD and VHDX, rolled it out across the chain-walking operations,
settled which operations still refuse, tested and fuzzed the result,
and documented it.

This phase runs `PUSH-AUDIT.md` over all of it. The phase plan is the
deliverable; implementation is a separate ask.

## Planning effort

**High**, and the survey earned it. The audit instrument is broken in
a way that would have produced a green audit of nothing, and the
breakage is not the one the previous push-audit phase in this
repository found and fixed.

Review effort: **medium**. The plan proposes no behaviour change. The
judgement is in Decision 1, which spends a step repairing tooling
before auditing anything, and in Decision 3, which overrides the
runbook's own recommended models.

## Scope

**In scope.** `PUSH-AUDIT.md` run over the union of what put phases 1
to 17 on `develop`, plus the repair to the audit scripts that running
it over a merged plan requires, plus triage and disposition of every
finding.

The range is seventeen merge commits in `instar`, taken from the
master plan's `Merged` column:

```
8b81a0f 1a677c77 42e879f f981374 9a80776 882d098 99d7d24 c416abd
044ad77 036252f c66f8b2 b4ae7fc d677823c 7fef1321 f14554cf d7a4973f
60e4381c
```

plus `01153800`, which is already on this branch: it rewrites comments
phase 4 wrote and is differencing work by content even though it
post-dates phase 17's merge. Nothing else on this branch is in scope —
the close-out commit `09f6ecca` is bookkeeping.

**Out of scope.**

* Fixing anything the audit finds that is not a defect this plan
  introduced. Findings against pre-existing code are reported and
  filed, not fixed here.
* The nine open issues phases 11 to 17 handed over (#628, #632, #641,
  #642, #643, #651, #653, #654, #658). The audit may add to that list;
  it does not work it.
* The remaining `PLAN-*` citations in `src/` that `01153800` left
  behind. They are provenance for measured byte layouts and need the
  measurements inlined or rehomed, which is a documentation task of its
  own; see *Deferred work*.
* `REVIEWS.md`, per the `plan-phase-landing` shared block.

## What the survey found

Three findings, in descending order of how much they change the phase.

### The audit scripts would have audited nothing

Every diff-scoped check in both scripts derives its file set from
`git diff develop...HEAD`:

* `tools/audit/wave1.sh:112` (long lines in changed Rust) and `:140`
  (adversarial fixtures added in the wrong repository).
* `tools/audit/wave2-mechanical.sh:24, 38, 47, 51, 58, 89, 103, 120,
  130` — which is every one of its nine checks.

On a push-audit branch that range does not mean what the checks assume.
Every phase has already merged, so it covers only what this branch
itself adds. Measured on this branch, after the close-out and the
comment cleanup: **3 files, 12 insertions, 9 deletions, one Rust
file** — against the 103 files and 30 Rust files the phase is supposed
to audit. The checks are not vacuous, which would at least look odd;
they are scoped to roughly two hundredths of a percent of the work and
report cleanly on it.

Each script guards on `git rev-parse --verify develop`, which
*succeeds*, so nothing warns. For a plan whose phases have all merged
and which carries no commits of its own the range *is* empty, and the
output is then indistinguishable from a clean audit — the exact hazard
the `plan-push-audit-phase` shared block names, "a diff against the
default branch is empty and would read as a clean audit", sitting
unfixed inside the instrument built to execute that block. This branch
escapes the empty case only by accident, because the close-out commit
happens to ride on it.

The previous push-audit phase in this repository did not catch it.
`PLAN-fuzz-autofix-phase-02-push-audit.md:212-218` records
`wave2-mechanical.sh` as empty and explains it by content — "this plan
added no Rust and the range holds only plan documents" — which was
true and which is why the structural cause went unnoticed. That phase
reconstructed a patch by hand for its judgment agents (its Decision 1)
and left the scripts computing from `develop...HEAD`.

Differencing is where the difference bites. It changed 30 Rust files
and added five fuzz targets, so the checks for new `unsafe` blocks,
new `.unwrap()`/`.expect()`, new `#[test]` counts, VMM-boundary
`unsafe`, and misplaced adversarial fixtures are all checks with
something real to find. Hence Decision 1.

The wave 2 judgment briefs in `PUSH-AUDIT.md` carry the same
expression in prose (2a's says "the diff (`git diff develop...HEAD`)"),
so they mislead a sub-agent in the same direction.

This survey finding was then under-acted on: the first implementation
added the exit-code row to the runbook's table and left all five
briefs saying `git diff develop...HEAD`, which the second review round
caught. Fixing the scripts without fixing the runbook they are
documented in would have left the next push-audit phase reading the
wrong scope from the prose while the scripts resolved the right one.
`PUSH-AUDIT.md` now defines the audit scope once, under *The audit
scope*, says that a push-audit phase must pass `--commits-file` and
why, and each of the five briefs tells its sub-agent to read the
patch the management session hands it rather than recomputing a diff
of its own.

That fix then needed a third round, because the recipe it pointed the
briefs at was itself wrong: `git diff <merge>^1 <merge> > scope.patch`
covers one merge, and run once per merge the `>` truncates, so only
the last survives. Measured on a two-merge scope, the union is 7,733
lines and the hand-rolled recipe produced 4,261 of them with nothing
to say it had -- the same subset-read defect as the original finding,
reintroduced one layer out by the fix for it. The recipe is now
`tools/audit/scope-patch.sh`, which uses the library, writes the
banner to stderr so the patch on stdout stays clean, refuses an
unresolved scope, and is covered by four cases in
`test-audit-scope.sh` including a comparison against the recipe it
replaced.

A fourth round then found the same shape again, in the tests
themselves. Every history-dependent case -- the line-attribution
check the file's own header called decisive, the `scope-patch.sh`
union and truncation comparisons, and the only end-to-end run of a
valid `--commits-file` through a wave script -- took a SKIP branch in
the `ci-tooling` job, which checks out at `fetch-depth: 1`. CI
therefore never ran any of them, and a SKIP that always fires is a
pass standing in for a check that never happened: this phase's
headline defect, inside the tests written to prevent it. Every case is
now built on a generated fixture repository, the scripts that resolve
their own repository root are copied into it, and no suite has a SKIP
branch left. The same round also found that the line-attribution awk
cut any path at its first space (`f2 with space.rs` became `f2`) and
read an added line whose content began with `++ ` as a file header,
dropping it and attributing everything after it to an invented path --
both wrong-but-plausible locations of exactly the kind it was written
to stop reporting, and neither covered by its fixtures.

### The union of merge ranges exists, and is complete

Unlike the fuzz-autofix precedent, which had no range to audit and
reconstructed one commit by commit, this plan's `Merged` column was
filled as each phase landed. Verified row by row: phases 1 to 17 all
carry a cell, and the master plan's claim at `:28-32` that the column is "filled in as
each phase lands, not reconstructed afterwards" is true.

Measured scope:

| | |
|---|---|
| Merge commits | 17 in `instar`, 3 direct commits in `instar-testdata` |
| Patch lines | 54,821 added or removed |
| Distinct files | 103 |
| By area | 39 `docs/`, 30 `src/`, 16 `tools/` and `scripts/`, 7 `tests/`, 4 `.github/workflows/` |

For comparison the fuzz-autofix audit read 7,887 lines. This is 6.9
times that, which is what drives Decision 2.

One trap worth naming because the survey fell into it: phase 2's
`Merged` cell holds four commits across two repositories, and the
`instar` one is last, after a semicolon —
`instar-testdata 77f5f589f0 + 623a30866f + 3eed61bf75 (direct to main);
instar 1a677c77 (#552)`. The first pass of the sizing read the
`instar-testdata` shas, missed `1a677c77`, and silently left 1,764
insertions out of scope. Every other row holds one sha.

### The instrument's other known breakage is already fixed

`tools/audit/inline-script-check.awk` is the one mechanical check with
something to say about a YAML-and-bash diff, and the fuzz-autofix audit
found it broken three ways: `NR` where it meant `FNR`, a `head -20`
truncation, and block tracking that ended at the first blank line. All
three are fixed, each named in the awk's own header comment at `:15-18`,
the last by `7e83a0d0` ("End run-blocks at the first real dedent").
Phase 18 does not need to repair it, which the precedent did as its
step 1.

### Two things inherited rather than discovered

* **Workflow `run:` blocks are unchecked for unquoted variables.**
  `.github/actionlint.yaml:26-29` disables SC1090, SC2046, SC2086 and
  SC2143 for `.github/workflows/*.yml`, and the `shellcheck`
  pre-commit hook is scoped `files: ^(scripts|tools)/`
  (`.pre-commit-config.yaml:56`). Differencing touched four workflows,
  so their `run:` blocks are checked by nothing. The precedent recorded
  this as applying to "every remaining workflow"; phase 18 inherits it.
  Confirmed against both config files, not assumed.
* **The cross-repo obligation is nearly empty.** The shared block says
  a phase that landed in another repository "is audited there". Phase
  2's three `instar-testdata` commits are 14 git-LFS pointer files, 42
  lines, and contain no code, no scripts and no documentation. The
  fixtures' generator is `scripts/create-vhd-testdata.sh`, which landed
  in `instar` and is already inside the range above. So there is
  nothing for a judgment agent to read there; see Decision 5.

### Nothing to correct at source

Unusually for this skill, the survey found no false claim to fix in
the master plan. Phase 18 has no prose bullet in the phase list — it
appears only as the Execution table's last row, as the statement at
`:28-32` that the plan carries the mandatory audit, and as a success
criterion at `:878`. There is no stale description because there is no
description. The `Merged` column it depends on is correct, which the
survey checked rather than assumed.

## Decisions

1. **Repair the scripts before running them, rather than working
   around them in the briefs.** The precedent's approach — reconstruct
   a patch by hand, hand it to the agents, leave the scripts alone —
   produced a correct audit and left the next plan to rediscover the
   same thing. Nine mechanical checks silently reporting clean is a
   defect in a tool whose entire purpose is to be trusted before a
   push, and every future push-audit phase in this repository inherits
   it.

   So step 1 gives both scripts an explicit scope: a `--range` or
   `--commits-file` argument naming what to audit, defaulting to
   today's `develop...HEAD` so the ordinary pre-push use is unchanged.
   It must also *report the resolved file count* and refuse an empty
   set rather than reporting zero findings. "No changed files" and "no
   findings" have been indistinguishable in this tool's output since it
   was written; printing the count is what makes a wrongly-scoped run
   visible, and on this branch the wrong scope is a small set rather
   than an empty one, so the empty-set refusal alone would not have
   caught it.

   This is the one decision a reviewer might argue with: it spends a
   step on tooling inside a phase whose job is to audit, and a push
   audit is not obviously the place to fix the auditor. The argument
   for doing it here is that the alternative is auditing with an
   instrument known to be broken, and that the repair is small, local,
   and testable — `tools/audit/` already carries
   `test-inline-script-check.sh`, so the pattern for a test exists.

2. **Partition the judgment agents by dimension, not by phase.** At
   54,821 lines no agent reads the whole patch well, so it has to be
   split. Splitting by phase is the obvious cut and it is the wrong
   one: the shared block's stated reason for auditing the accumulated
   diff is to catch "what the phases did to each other — the
   duplicated helper that only exists once phases three and six have
   both landed, the doc page that phase two made wrong and phase five
   never revisited". Seventeen per-phase agents reproduce exactly the
   per-phase audit the block forbids.

   Splitting by dimension keeps each agent's cross-phase view intact
   within its own concern, and the dimensions already map onto file
   areas that barely overlap: 2a and 2b read `src/` and `tests/`, 2c
   reads `docs/`, 2d reads the parser and host-resolution paths. Each
   agent gets the union patch restricted to its paths *and* the current
   state of those files, because a patch shows what changed and not
   what the tree now says.

3. **Override the runbook's recommended models for 2a, 2b and 2d.**
   `PUSH-AUDIT.md` specifies sonnet at medium effort for 2a. That
   setting is calibrated for a single pull request's diff. Three of the
   four dimensions here are reading a seventeen-phase accumulation in
   which the findings worth having are structural, so they go to opus
   at high effort. 2c stays on sonnet at high effort: phase 17 swept
   the documentation six commits ago, so its cross-page consistency is
   freshly established and the brief can name exactly what was settled.

   Stating this explicitly because a sub-agent handed the runbook will
   otherwise read its model table as authoritative and this plan's as
   a suggestion.

4. **2d gets the longest brief and the most scepticism.** Differencing
   parses parent locators out of untrusted image headers and the host
   then resolves them into filesystem paths. The plan deliberately
   built adversarial fixtures for this — `vhd-diff-locator-dotdot`,
   `-etc-passwd`, `-unc`, `-url`, `-overlong`, `-conflicting` — which
   means the attack surface is known and the question is whether the
   code's answer to each is still what phase 4 decided. Issue #658,
   filed during phase 17, is a live example of a resolution rule being
   less obvious than it looks.

5. **No agent is spawned for `instar-testdata`.** Its phase 2
   contribution is 14 LFS pointers and nothing else; there is no source
   to review. What is worth checking is on the `instar` side and goes
   into 3b's brief: that every fixture those commits added is still
   referenced by `tests/manifest.json`, and that none has been orphaned
   by later phases the way `vhd-diff-base.vhd` was in `623a30866f`.
   Recorded here so a reader does not read the omission as the
   cross-repo obligation being skipped.

6. **Findings are triaged in the management session, not by the agent
   that found them.** Each agent reports; step 4 assigns every finding
   blocking, advisory-fix-now, or filed-as-issue. Agents that fix what
   they find produce a diff nobody planned, and this phase's whole
   value is the judgement about which findings matter.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | Give `tools/audit/wave1.sh` and `tools/audit/wave2-mechanical.sh` an explicit audit scope. Today every diff-scoped check computes from `git diff develop...HEAD` (wave1 `:112,:140`; wave2-mechanical `:24,38,47,51,58,89,103,120,130`). On a push-audit branch that range holds only what the branch itself adds — measured here as 3 files and one Rust file, against the 103 files and 30 Rust files the phase must audit — and for a push-audit branch carrying no commits of its own it is empty, which reads as a clean audit. Also print the resolved file count before any check runs, since on this branch the wrong scope is a small set rather than an empty one and only the count makes it visible. Add a `--range REV..REV`/`--commits-file PATH` option that supplies the file set, keep `develop...HEAD` as the default so pre-push use is unchanged, and make both scripts exit non-zero with a named error when the resolved file set is empty. A `--commits-file` holds one commit per line and the file set is the union of each commit's diff against its first parent. Add `tools/audit/test-audit-scope.sh` covering: default behaviour unchanged, an explicit range selecting the right files, an empty set refused, and a commits file of merge commits resolving correctly — follow `tools/audit/test-inline-script-check.sh` for shape. Do not change any check's logic, only where its file list comes from. Commit subject: "Scope the audit scripts explicitly." |
| 2 | low | sonnet | none | Materialise the audit scope and run wave 1. Write the seventeen merge commits plus `01153800` (the list is in this plan's Scope) to `audit-scope.txt` in the scratchpad, build `scope.patch` as, for each commit, `git log -1 --format='commit %h %s'` followed by `git diff <sha>^1 <sha>`, and confirm the result is about 54,800 patch lines over 103 files — a materially smaller number means the list was mis-transcribed, which is the failure phase 2's four-sha `Merged` cell invites. Then run `tools/audit/wave1.sh --commits-file audit-scope.txt` and `tools/audit/wave2-mechanical.sh --commits-file audit-scope.txt`. Report both verbatim, including exit codes, and do not fix anything. Note that `make test-rust` inside wave 1 excludes the `no_main` guest binaries and that `-p qcow2` skips the feature-gated create tests; say so rather than reporting the run as total coverage. Commit subject: none — this step produces a report, not a diff. |
| 3a | high | opus | none | Wave 2a, code quality, over `src/` (30 files) and `tools/`. Input: step 2's mechanical report plus `scope.patch` restricted to those paths, and the current state of the files — read both, because the patch says what changed and the tree says what it now means. The question is what seventeen phases did to each other: duplicated logic that exists only because two phases both landed (look hardest at the VHD and VHDX sector-bitmap readers in `src/crates/vhd/src/lib.rs` and `src/crates/vhdx/src/lib.rs`, built one phase apart to the same shape, and at the four independent refusal families in `src/vmm/src/main.rs`), abstractions that should have been lifted into `src/crates/` once the second caller appeared, and dead code left by a later phase changing direction. Triage every TODO, `unwrap`, `expect` and `unsafe` the mechanical script flagged as blocking or advisory, skipping `#[cfg(test)]`. Report findings with file:line; fix nothing. |
| 3b | high | opus | none | Wave 2b, tests, over `tests/` (7 files), the crate test modules in `scope.patch`, and `src/fuzz/`. Five fuzz targets were added across the plan; confirm each still builds into the `src/fuzz` crate's target list and that `tools/ci/count-fuzz-targets.sh` agrees, since that crate is outside the main workspace and drifts silently. Two specific hazards, both from this repository's history and neither visible in a passing run: `make test-container-core` only passes `INSTAR_REQUIRE_LIBVHDI` through from the host, so a missing oracle produces a green 0-failed run rather than a skip — check whether any differencing test's assurance depends on the oracle being present; and a stestr worker killed by an oversized attachment exits 0 having run a fraction of the suite, so read "Ran: N" rather than the exit code. Also confirm every fixture added by `instar-testdata` `77f5f589f0` is still referenced from `tests/manifest.json` and none was orphaned the way `vhd-diff-base.vhd` was. Report; fix nothing. |
| 3c | high | sonnet | none | Wave 2c, documentation, over the 39 `docs/` files in `scope.patch` plus `ARCHITECTURE.md` and `CHANGELOG.md`. Phase 17 (`60e4381c`) swept this six commits ago and settled the load-bearing fact: `convert`, `dd`, `compare`, `bench` and `rebase` compose a differencing child against its parent at sector granularity; `check`, `map` and `measure` refuse one by name; `info` reports the parent without composing. Four pages assert that split and they must not disagree — `ARCHITECTURE.md:131-139`, `docs/chain-discovery.md`, `docs/format-coverage.md` note 18, `docs/map.md`. Your job is what phase 17 did not do: whether the *other* 35 pages in the range still describe behaviour that later phases changed, and whether anything the plan built is documented nowhere. Do not re-litigate the provenance references phase 17 deliberately kept — a verbatim quote of a past release's error message, or a citation of the document where a byte layout was measured, is correct as history; `docs/quirks.md:4193-4200` and `docs/testing.md:2114` are those. Report; fix nothing. |
| 3d | high | opus | none | Wave 2d, security, over the parse and host-resolution paths: parent-locator parsing in `src/crates/vhd/src/lib.rs` and `src/crates/vhdx/src/lib.rs`, chain discovery in `src/vmm/src/chain.rs`, and the backing-path resolution at `src/vmm/src/main.rs:1893`. Everything here parses attacker-controlled bytes out of an image header and some of it turns those bytes into filesystem paths the host opens. The plan built adversarial fixtures naming the known attacks — `vhd-diff-locator-dotdot`, `-etc-passwd`, `-unc`, `-url`, `-overlong`, `-conflicting` — so for each, establish what the code does today and whether it matches what phase 4 decided, by reading the code and running it, not by trusting the test name. Then look for what no fixture covers: unbounded allocation from a length field, integer overflow in a BAT or bitmap bound, a chain depth or cycle with no guard, and symlink or TOCTOU exposure between resolving a parent path and opening it. `VhdxState::init` no longer rejects `has_parent` (phase 14), so every caller refuses or composes on its own — check none of them forgot. Issue #658 is a live example of a resolution rule being subtler than it reads. Report with a severity per finding; fix nothing. |
| 4 | high | — | none | Management session. Triage every finding from 3a-3d into blocking, advisory-fix-now, or filed-as-issue, and record the disposition of each in this plan under an *Audit findings* heading, keeping the evidence rather than a summary — the precedent's findings section is the model. Fix the blocking ones, file the rest, and write this plan's own `Status` and its `docs/plans/index.md` row to `Complete`. Per the `plan-phase-landing` shared block this phase records no `Merged` cell if the audit raises nothing; if it raises findings, they land as a follow-up pull request after this one merges, and that pull request records this phase's merge commit. |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| The repaired scripts change behaviour for ordinary pre-push use, and nobody notices until a real push | Step 1's test asserts the default path is unchanged, and the default stays literally `develop...HEAD`. The option is additive. |
| Step 2 transcribes the commit list wrongly and the audit silently reads a subset | Step 2 checks the materialised patch against 54,821 lines and 103 files before anything reads it. That number is in this plan, measured, and a mis-transcription moves it. This is also why the scripts must refuse an empty set rather than report clean. |
| An agent reports the runbook's recommended model and effort as authoritative and under-reads its dimension | Decision 3 states the override and the reason; each brief carries its own effort and model in the step table, which is what the implementer follows. |
| 3a-3d collectively miss a cross-dimension defect, since the partition is by concern | Step 4 reads all four reports together, and the one question the partition cannot answer — does a documented claim match the code — is already answered elsewhere: phase 17's definition of done required the compose/refuse split to be checked against the refusal sites rather than a sibling document. |
| The audit finds a defect in code a phase deliberately decided | Nine issues are already open from phases 11 to 17 and the plan's Decisions record the choices. Step 4 checks a finding against the deciding phase's plan before calling it a defect; a disagreement with a recorded decision is filed, not fixed. |
| Agents assert plausible-but-wrong format or tool capabilities, which this repository's history says they do | The master plan's standing warning applies: cite-or-measure for every claim about what libvhdi, Hyper-V or qemu accepts, and step 4 re-measures a sample before accepting it. |

## Definition of done

Falsifiable, in the order a reviewer can check them:

* `tools/audit/wave1.sh --commits-file <file>` and
  `tools/audit/wave2-mechanical.sh --commits-file <file>` both accept
  the option, both print the resolved file count before running any
  check, and both exit non-zero with a message naming the problem when
  that count is zero. Checked by running them with a file listing a
  commit that touches nothing, and by confirming the printed count is
  103 for this phase's own scope.
* `grep -c 'develop\.\.\.HEAD' tools/audit/wave1.sh
  tools/audit/wave2-mechanical.sh` is unchanged in meaning: the
  expression survives only as the default, and `tools/audit/test-audit-scope.sh`
  passes.
* `tools/audit/test-audit-scope.sh` runs in CI, not only by hand. Added
  after the first review round pointed out that nothing invoked it:
  a test nothing runs guards nothing, which is the same defect as the
  one this phase exists to fix, one level up. It is a step in the
  `ci-tooling` job beside `test-inline-script-check.sh`, and it builds
  its own fixture repository so it needs no history at that job's
  `fetch-depth: 1` — which is also what retired the two hard-coded
  merge SHAs it used to assert against.
* The materialised `scope.patch` is within 1% of 54,821 patch lines
  over 103 distinct files, and contains `1a677c77` and `01153800`.
* `tools/audit/wave1.sh` exits 0 over that scope, or every non-zero
  exit is recorded in this plan with its disposition.
* All four wave 2 judgment reports exist, each naming the files it
  read, and each finding in them appears in this plan's *Audit
  findings* section with a disposition of blocking, advisory or an
  issue number.
* Every finding disposed of as an issue has a real issue number, and
  `gh issue view` shows it open.
* This plan's `Status` in `PLAN-differencing.md` and its row in
  `docs/plans/index.md` both read `Complete`, and its `Merged` cell is
  empty — it is the one row permitted to omit one.
* `docs/plans/order.yml` is untouched.
* `pre-commit run --all-files` passes.

## Back brief

Confirm before step 1:

* The eleven diff-scoped checks really do read the wrong set on this
  branch. Run `tools/audit/wave2-mechanical.sh` unmodified first and
  report what it says. The expected result is **not** silence: it
  should report against `src/vmm/src/main.rs` alone, because
  `develop...HEAD` here is the 3 files this branch adds rather than the
  103 the phase audits. If it reports against anything like 30 Rust
  files, the range resolves differently than the survey measured and
  this plan's central finding needs rechecking before any code moves.

Gate before step 3a-3d are spawned: report the step 2 output and the
materialised scope size, and wait. Four high-effort opus agents over a
55,000-line patch is the expensive part of this phase and it is cheap
to spend it on the wrong scope.

## Audit findings

Wave 1 exited 0 over the 103-file scope: pre-commit, rustfmt/clippy,
`make instar`, `make check-binary-sizes`, `make test-rust` (2,522 tests
across 78 binaries, 0 failures), `make fuzz-build` and `mermaid-lint` all
passed, and wave 1b's two scope-dependent checks found no long lines and no
adversarial fixture committed to the wrong repository. One advisory: 51
oversized inline `run:` blocks, 35 of them in the four workflows the scope
touched. It took three attempts -- the first two were killed by host memory
pressure from a concurrent full-system backup, mid-`make instar` and
mid-devcontainer-build respectively. Those were environment failures, not
findings; the third ran clean.

Wave 2 mechanical exited 0 and reported 16 TODO/FIXME hits, 325 new
`#[test]` functions, 171 added lines containing `unsafe`, 256 added
`.unwrap()`/`.expect()`, and zero host-side `unsafe`, zero
`#[allow(dead_code)]` and zero misplaced adversarial fixtures.

### Fixed in this phase

Defects this plan introduced, all of them documentation or comments that
are wrong today. Each was verified in the tree by the management session
before being fixed.

| Finding | Site | What was wrong |
|---|---|---|
| CHANGELOG contradicts itself | `CHANGELOG.md` `[Unreleased]` | Two entries said "Nothing reachable from the CLI changes: `instar create -f vpc -b parent.vhd child.vhd` still fails with \"invalid option for target format\"" while a third in the same section said that command now produces differencing children. Written at phases 5 and 6, where they were true; falsified by phase 7, which wired the CLI; never revisited. All three would have shipped in one release's notes. The interim claims are removed -- a release note should describe what ships, not a superseded intermediate state. |
| Live plan citations | `CHANGELOG.md:261,273,291` | Three markdown links to this plan as the source for built work, the only three `[PLAN-` citations in the file. Removed, reasoning inlined. Phase 17 excluded `CHANGELOG.md` from its sweep wholesale, justified by `:829`'s verbatim quote of a past release's error message -- a blanket exclusion earned by one hit, concealing four others. `:829` is untouched; it is correct as history. |
| Falsified refusal rationale | `src/operations/create/src/main.rs:204-209` | Said "Every read path in instar that composes sector data refuses such an image" and "Until parent composition exists (`docs/plans/PLAN-differencing.md`)". Five operations compose, and composition exists -- it is what this plan built. Phase 10 wrote the block; phases 12 and 13 falsified it without touching the file. Replaced with the operative reason, verified: this function records exactly one parent reference and has no notion of a chain behind it, so an overlay on a differencing image would be a two-level chain instar has never authored. A bare "this phase" reference in the same block also went. |
| Falsified refusal rationale | `src/operations/map/src/main.rs:460-462` | Said "the chain follow-up will lift this restriction". The chain follow-up is phases 11 to 17 and it settled the opposite. The VHDX sibling arm 70 lines below was corrected by phase 4 in the same diff; this arm was not, and phase 15 touched the file again without catching it. Now mirrors the sibling and names issue #641. |
| False for VHD | `docs/info.md:77-79` | Claimed `info` "decodes the parent name **and the parent locator entries**". True for VHDX; false for VHD, where no read path consults the locator table at all. Corrected, and the underlying behaviour filed as #661. |
| Stale fuzz-target counts | `docs/testing.md:1357`, `docs/development.md:790`, `docs/security-audits.md:466` | 42, 40 and 40 against an actual 43. The tiering arithmetic at `:1357` computed a sharding threshold from a count one short; recomputed. `docs/testing.md:1427`'s "21 of the 40" is a dated incident narrative and correctly left. |
| Stale guard-group breakdown | `docs/testing.md:1773,1796,1807` | Prose said 6 + 4 + 1 + 3 = 14 where CI enforces 15. Measured: the shared-walk sub-group has two cases, not one -- phase 16 added `vhdx-bat-walk-counts-the-bitmap-slots-at-4k-sectors`, bumped the two figures CI forced, and left the three it did not. The script's own comment at `:736` records that an earlier version said "14 cases guard". |
| Hand-maintained derived numbers | `docs/testing.md:2041` | "the 62 cases name 50 distinct targets (43 Rust, 7 integration)" against 105 cases. Rather than substitute another hand-computed figure, the sentence no longer restates derived numbers -- three independent attempts at the distinct-target count during this audit produced 84, 82 and 94, which is the argument for not writing it down. |
| Overstated claim | `docs/plans/index.md:42` | Said the harness's "case counts are now derived from the script rather than written in prose". The totals are, and CI enforces them; the sub-group breakdown was prose and drifted again. Qualified at source. |

Two one-line fixes to pre-existing code were taken as well, because both
are instances of the defect this phase was convened over -- a check that
reports success having examined nothing:

| Finding | Site | Fix |
|---|---|---|
| CI canary misses the audit fixtures | `tools/ci/prepare-testdata.sh`, `tools/test-package-functional.sh` | The differencing locator fixtures are the only automated proof that the path-traversal references are refused, and `tests/test_differencing.py:546-551` skips rather than fails when a fixture is absent, so a partial testdata checkout removed that proof with CI still green. Adding the fixture to the canary list did **not** fix this, as the first review round pointed out: both canary loops skipped an absent file and failed only when every canary was missing, so the new entry checked pointer-ness and nothing else. The guard is now `tools/ci/check-testdata-fixtures.sh`, called by both scripts, which requires every listed fixture to exist as well as to be materialised -- and that applies to the two pre-existing canaries too, whose consumers (`tests/base.py` `get_adversarial_image`, `tests/test_security.py:38-40`) skip on absence in exactly the same way. `tools/ci/test-check-testdata-fixtures.sh` covers it, including the missing-fixture case that used to pass. |
| False PASS on an empty comparison | `tools/verify-info-output-parity.sh:78` | `BASE="$(git merge-base origin/develop HEAD)"` with no `BASE != HEAD` guard, so on `develop` it built the same tree twice and printed `PASS: instar info output is byte-identical`. Guard added and proven: on `develop` the merge-base is HEAD, so it now exits 2. |

### Filed

| Issue | Finding |
|---|---|
| [#660](https://github.com/shakenfist/instar/issues/660) | TOCTOU between backing-path validation and backing-file open. MEDIUM. The mechanism predates the plan; phase 14 took the commands reaching it on VHD-family input from zero to five. |
| [#661](https://github.com/shakenfist/instar/issues/661) | instar's VHD read path never consults the parent locator table. Fail-closed, so not a vulnerability, but it means the `-conflicting` fixture does not test its own question, a legitimate differencing VHD with a blank name reads as parentless, and ~400 lines of hardened parser plus a fuzz target guard code nothing ships. |
| [#662](https://github.com/shakenfist/instar/issues/662) | VHDX locator `LocatorType` GUID and locator-level defect both unchecked; `VhdState::block_lookup` lacks the VHDX offset-zero and region-overlap guards. |
| [#663](https://github.com/shakenfist/instar/issues/663) | Four checks documented as guards that run nowhere or cannot fail, including `integration-core` having no `Ran: N` floor. |
| [#664](https://github.com/shakenfist/instar/issues/664) | `fuzz_chain_compose` cannot reach the VHD or VHDX compose arms, so the plan's central new code is fuzzed by nothing. |
| [#665](https://github.com/shakenfist/instar/issues/665) | ~720 lines of compose logic duplicated across four layers with no cross-reference NOTE, and nothing pairing a guest refusal raiser with its host renderer. |

### Negative results worth recording

A push audit that reports only findings does not tell a reader which ground
was covered. These were measured, not read.

* **The refusal invariant holds.** Phase 4 established that a refusal must
  never depend on whether the parent file happens to exist, because the
  opposite leaks filesystem existence to whoever supplied the image. Each
  refusal site was run twice over the same image, once with the named
  parent absent and once with a real parent planted at exactly the path the
  image names: `check`, `measure` and `map` gave byte-identical messages and
  rc=1 both times.
* **No filesystem-existence oracle.** Four crafted VHDs naming an existing
  file, a missing file, an existing directory and a missing directory all
  produced identical reasons. An out-of-allowlist absolute reference is
  never probed; a `..` escape is judged from its spelling.
* **Every `VhdState::init` / `VhdxState::init` caller refuses or composes.**
  This is the check phase 14 made necessary by removing `VhdxState::init`'s
  own rejection. All callers enumerated and, where reachable, executed. None
  forgot.
* **Chain depth and cycles: one choke point, guarded.** All 11 walks go
  through `discover_backing_chain`, which runs the circular and depth checks
  before each hop. Measured with a self-referencing VHD, a two-image cycle
  and a 41-deep chain. No bypass found.
* **No unbounded allocation from a length field**, and the padded BAT bound
  is correct including its off-by-one: for `g` groups the last bitmap entry
  sits at `padded - 1` and the check is `>=`.
* **Terminal and bidi injection neutralised** in the parent field this plan
  made user-visible.
* **The libvhdi oracle hazard is real but cheap.** With the oracle present
  and `INSTAR_REQUIRE_LIBVHDI=1`: `Ran: 1273, Passed 1257, Skipped 16,
  Failed 0`, and zero differencing skips. Only 7 of ~100 differencing tests
  touch the oracle, and primary assurance is the recorded `*-composed.raw`
  goldens, which are oracle-independent. CI sets the variable on the right
  job.
* **Three deliberate mutations were all caught**, including one to a
  parent-owned payload sector inside `vhd-diff-parent.vhd`, which proves
  instar genuinely sources those bytes from the parent -- the direct inverse
  of issue #547.
* **No dead code.** All 84 public items the plan added were checked for uses
  outside their defining file; the 21 with none are measured byte-offset
  constants used by their own layout tests.
* **The production `unsafe` surface of this entire plan is seven function
  signatures.** Of the 171 added lines containing `unsafe`, none is under
  `src/vmm/**`, 51 are `#[cfg(test)]` mock call tables, 20 are fuzz
  harness, and 19 of the apparent qcow2 total are a diff-alignment artifact
  blame-verified to pre-plan commits.

### Corrections the sub-agents made to this plan's own briefs

Recorded because a brief that states a wrong premise costs more than one
that states none.

1. The `unsafe` count in step 3a's brief, 77 lines in qcow2, was inflated
   by Myers diff alignment; the net figure is 58.
2. Step 3b's brief said the mutation harness "grew from 26 to 62 cases". 62
   is the phase-13 value, taken from the master plan's prose without
   checking; the sequence is 26, 62, 91, 102, 105.
3. Steps 3a and 3d were both told `discover_backing_chain` lives in
   `src/vmm/src/chain.rs`. It is at `src/vmm/src/main.rs:3087`; `chain.rs`'s
   only in-scope change is a `size_of::<ChainConfig>()` test assertion.
4. Step 3d's brief said issue #625 was still open. It is closed, along with
   #547, #548, #614, #633 and #565. Of the issues this plan handed over,
   only #658 and the six filed above are open.
5. Wave 2c reported that phase 17's "exactly nine hits" no longer held,
   giving fifteen. Phase 17's definition of done excludes `docs/plans/`,
   `CHANGELOG.md` and `docs/development.md` by name; with those exclusions
   the grep returns exactly nine, verified. The agent had excluded only
   `docs/plans/`. It pointed at the right file for the wrong reason.

### Coverage this audit did not have

* Wave 1's 2,522 tests are entirely `make test-rust`, which excludes the
  `no_main` guest binaries by explicit list. `wave1.sh` has no integration
  step, so the only execution of this plan's Python in the audit is wave
  2b's own 1,273-test run.
* The mutation harness's 105 verdicts were not executed -- only proven to
  all still land, with the classifier self-tested. A full run needs docker,
  testdata and `/dev/kvm` together, and CI never runs it either.
* `make test-rust` DOES run the feature-gated qcow2 create tests; the
  Makefile chains `cargo test --release -p qcow2 --features create`. An
  earlier statement in this session that they were skipped was wrong.

## Deferred work

* **The remaining `PLAN-*` citations in `src/`.** `01153800` removed
  the four bare phase citations from the refusal comments in
  `src/vmm/src/main.rs`. A sweep while writing it found 46
  `PLAN-differencing` references across 11 files under `src/`, and more
  than 100 citing other plans. They are not the same class: most cite
  `PLAN-differencing-phase-01-pin.md` as the authority for a measured
  byte layout, so deleting them destroys the provenance of a
  measurement. The fix is to inline the measurement or rehome it to
  `docs/`, which is a documentation task rather than a comment edit.
  Out of scope here; the fleet block is `plan-references-in-code`
  (development#176).

  The audit widened this task twice. A `PLAN-` string match does not see
  the sibling class: `grep -rniE '(^|[^-[:alnum:]])phase [0-9]+[a-z]?\b'
  src/ --include=*.rs --include=*.toml | grep -v 'PLAN-'` returns 364 bare
  numbered-phase comments, so the cleanup as originally scoped would report
  done with those left. And one of them is user-visible rather than a
  comment: `src/vmm/src/main.rs:7034` ships CLI help reading "Phase 3 only
  supports 512 ... phase 5 may relax this".
* **The 4096-byte-sector VHDX limitation is recorded only in plan files.**
  It is live and true -- `instar create --help` still says only 512 is
  supported, and no test claims otherwise, so nothing quietly tests it with
  a 512-byte image. The crate-level substitute is genuinely closed, with
  six tests at 4096 and a mutation case naming the two-chunk-group walk.
  But `grep -rn "4096-byte\|4K-sector" docs/*.md` finds only an unrelated
  mention, and a user-facing limitation living in `PLAN-differencing.md`
  and a phase-16 plan file is the wrong home for it under this project's
  own documentation discipline.
