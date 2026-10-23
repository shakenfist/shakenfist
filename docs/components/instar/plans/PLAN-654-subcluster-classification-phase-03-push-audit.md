# PLAN-654 phase 3: the push audit

Parent plan: [PLAN-654-subcluster-classification.md](/components/instar/plans/PLAN-654-subcluster-classification/)

## Prompt

Plan phase 3 of `PLAN-654-subcluster-classification.md`, the mandatory
push audit. Phase 2 landed in `d42af1f5` (#810), which completes the
plan's substantive work. Phase 1 (`d08841e3`, #801) replaced the ad hoc
readings of host offset zero in the qcow2 chain reader, map, measure and
check with one classifier that mirrors qemu's seven subcluster types,
and refused what qemu calls an invalid entry. Phase 2 stopped the chain
reader treating an external data device as a backing layer, bumped the
chain config to version 4 for the flag that marks one, and refused the
two data-file entries qemu refuses.

This phase runs `PUSH-AUDIT.md` over both. The phase plan is the
deliverable; implementation is a separate ask.

## Planning effort

**Medium.** The audit instrument was repaired by the two push-audit
phases before this one, and the survey confirmed it resolves this
plan's scope correctly. So unlike differencing phase 18, no step here
repairs tooling. The judgement is in Decision 3, which overrides the
runbook's models for two dimensions, and in Decision 4, which gives the
test dimension a measurement to make rather than a diff to read.

Review effort: **medium**, concentrated on Scope and Decision 4.

## Scope

**In scope.** `PUSH-AUDIT.md` run over the union of the two merge
commits that put this plan's phases on `develop`, taken from the master
plan's `Merged` column:

```
d08841e3  Merge pull request #801  (phase 1, and the master plan itself)
d42af1f5  Merge pull request #810  (phase 2)
```

Nothing else is in scope. The master plan and the phase 1 plan were
committed inside #801 (`9bac3c45` is its first commit), and
`git log --first-parent origin/develop -- 'docs/plans/PLAN-654*'` names
only these two merges. This branch's own close-out and planning commits
are bookkeeping.

Triage of every finding, and the record of it in this plan, are in
scope.

**Out of scope.**

* **Fixing findings.** Per the `plan-push-audit-phase` shared block and
  `PUSH-AUDIT.md`'s push-audit section, findings land as their own pull
  request after this one merges. That pull request records this phase's
  merge commit and marks the plan `Complete`. If the audit finds
  nothing, this phase closes itself out instead (step 4).
* **The open issues this plan already handed over**: #794, #795, #804,
  #805, #806, #807 and #808. The audit may add to that list; it does
  not work it.
* **Items the two pull requests' automated reviews raised and the
  phases declined in writing.** They are recorded in each phase plan's
  execution notes. An agent re-raising one needs new evidence, not a
  second opinion.
* **The 23 merged branches still on origin**, this plan's two among
  them. `delete_branch_on_merge` is on, but branches that merge through
  the queue stay behind. That holds for the whole repository, not just
  this plan.

## What the survey found

### The scope is two merges, and the tool resolves it

Both `Merged` cells name merge commits: `git rev-list --parents -n1`
gives three words for each, and both are ancestors of `origin/develop`.
`tools/audit/scope-patch.sh --commits-file` over them resolves
**21 files** and writes **12,474 patch lines**. The two merges'
`--shortstat` are 19 files (+5,397/-1,108) and 14 files (+3,239/-294),
which overlap in 12.

| Area | Files | Churn (+/-) |
|---|---|---|
| `src/crates/qcow2/src/lib.rs` | 1 | +4,136/-890 |
| `src/vmm/src/main.rs` | 1 | +455/-10 |
| `src/shared/src/lib.rs` | 1 | +416/-17 |
| `src/operations/check`, `map`; `src/core/src/serial.rs` | 3 | +89/-65 |
| `tests/` (`helpers/qcow2_subclusters.py`, `test_qcow2_subclusters.py`, `test_check_formats.py`) | 3 | +1,256/-17 |
| `tools/mutate-differencing.sh` | 1 | +222/-34 |
| `docs/` (6 pages outside `plans/`), `CHANGELOG.md` | 7 | |
| `docs/plans/` (this plan's three files, `index.md`) | 4 | |

That is about a quarter of differencing phase 18's 54,821 lines. It
changes the partition argument (Decision 2) but not the conclusion.

### The instrument needs no repair

Differencing phase 18 gave both wave scripts `--range` and
`--commits-file` (`tools/audit/audit-scope.sh`), a printed file count,
and an exit 9 for an explicit scope that resolves to nothing.
Fuzz-autofix phase 2 fixed the inline-script check's line numbers and
truncation. `PUSH-AUDIT.md` now tells a push-audit phase to pass
`--commits-file` and to build the patch with `scope-patch.sh` rather
than by hand. The survey ran `scope-patch.sh` and got the 21 files
above. Nothing in this plan's scope is a workflow file, so the
remaining gap fuzz-autofix recorded, that no shellcheck runs on
workflow `run:` blocks, does not bite here.

### The integration suite reaches the new tests in CI

`make test-container-core` runs every module except `test_convert`,
`test_compare` and `test_info_malicious` (`Makefile:740-741`), so
`test_qcow2_subclusters` and `test_check_formats` run on every pull
request. `tools/mutate-differencing.sh --self-test` and
`--check-patterns` run in the `ci-tooling` job
(`.github/workflows/functional-tests.yml:200,211`). What CI does not
show is how many of those tests *skip*. Phase 2's definition-of-done
run skipped 549 of 1,805, and a fixture built at run time by `qemu-io`
can skip when the host's qemu lacks a feature. Decision 4 turns that
into a measurement.

### Phase 2 moved the harness phase 1's mutation cases run against

The plan added 23 cases and retired 2, so `EXPECTED_CASES` went from
105 to 126. Phase 1's `1fd5cedd` retargeted
`qcow2-wide-subcluster-alloc-at-host-zero-unfilled` and
`qcow2-wide-subcluster-lookup-loses-the-bitmap` at the new refusal,
under new names. Phase 2 moved the crate's `ScChain` test harness to
the device layout the VMM writes: the data device at index 1, the
backing image at 2. Phase 1's cases were shown to kill against the old
layout, and each phase verified only its own new cases. No run has
shown that phase 1's cases still kill on the merged tree. That is the
cross-phase effect the shared block's accumulated-diff rule exists to
catch, so step 2 runs all 23.

### Corrected at source in this branch's planning commit

Recorded here so a later step does not redo the work, and so step 3c
verifies it rather than finding it:

* **Seven open issues this plan filed were missing from the master
  plan's Future work:** #794 and #795 (step 1a), #804 and #805
  (phase 1 execution), and #806, #807 and #808 (step 2a). Each was
  recorded only in a phase plan. The runbook's plan check would have
  found every one. All seven are now Future work entries.
* **Two consider items from #810's review** were noted in the session
  but recorded nowhere. They now sit in Future work: the data-file
  refusal names no device (item 2, folded into the #806 entry because
  #806 is what makes it reachable), and the parent-hint flat VMDK has a
  crate test but no integration test (item 6).
* **The master plan had no `## Status:` line.** Every other master plan
  in `docs/plans/` carries one under its title, and the runbook's plan
  check reads it. The line was added, reading `In progress`.
* **The three Open questions still read as open.** Phase 1 answered all
  three. Each now carries a one-line answer and points at where phase 1
  recorded it.
* **"Bugs fixed during this work" was written in the future tense**
  ("is planned to close"). Both phases have landed, so it now says what
  they closed: #653, #654 and #800 in phase 1, and #792 and #803 in
  phase 2.

### Still to settle: one Future work entry with no issue

The entry "check's allocation count on data-file images" records
`instar check` reporting `3/4097` where `qemu-img check` reports
`3/16`. Unlike its neighbours, it names no issue. #804 is a different
defect: it is about the numerator (L2 tables counted as allocated),
while this entry is about the denominator. Step 4 measures it on
`develop` and either files it or records that it no longer holds.

## Decisions

1. **Audit the union of the two merges, built once by
   `scope-patch.sh`.** Wave 1 and the wave 2 mechanical script take
   `--commits-file`. The four judgment agents get the materialised
   `scope.patch`, restricted to their paths, plus the current state of
   those files, because a patch shows what changed and not what the
   tree now says. Step 2 checks the patch against the survey's
   21 files and 12,474 lines before any agent reads it.

2. **Partition the judgment agents by dimension, not by phase**, as
   differencing phase 18 did, and for the same reason: two per-phase
   agents would reproduce the per-phase audit the shared block forbids.
   At 12,474 lines each dimension can read its whole slice, so there is
   no further split. The runbook's style-conformance judgment agent is
   folded into 3a rather than run separately. Its five questions are
   about call-table, crate and operation boundaries, and 3a is already
   reading exactly that code.

3. **Override the runbook's models for 2a and 2b.** `PUSH-AUDIT.md`
   specifies sonnet at medium effort for code quality and tests, which
   suits one pull request's diff. 3a reads a qcow2 reader that two
   phases rebuilt in sequence, where the second added a variant to the
   first's `ClusterLookup` and a skip to the head of the first's device
   loop. The findings worth having are whether every match arm, render
   site and walker absorbed both, and that is opus work at high effort.
   3b goes to opus at high effort because its main job is to find tests
   that pass without testing anything. This plan began with #654, a
   defect that a reachability probe showed `fuzz_chain_compose` had
   compared zero holes for across 132,908 executions. 3c stays on
   sonnet at high effort. 3d keeps the runbook's opus at high effort.

   This is stated explicitly because a sub-agent handed the runbook
   will otherwise treat its model table as authoritative and this
   plan's as a suggestion.

   This is the decision most open to argument: two opus agents at high
   effort over a 12,000-line patch could be called overkill. The
   argument for it is that the patch is small *because* it is dense.
   Most of the 4,136 lines added to the qcow2 crate are tests of the
   subcluster boundaries, and a sonnet pass at medium effort is the
   setting least likely to notice that one of them can no longer fail.

4. **3b makes measurements, not just readings.** Two questions in its
   dimension cannot be answered from a diff:
   * **Which tests in the two in-scope modules skip, and why.** Phase
     2's run skipped 549 tests across nine modules and nobody broke
     them down. Step 2 runs `test_qcow2_subclusters` and
     `test_check_formats` and keeps the subunit stream. 3b reads it,
     not the summary. It names every skip in those two modules and
     checks the reason, which should be a missing qemu capability.
     Any skip that hides a fixture which never built is a finding.
     3b also reads #810's `test-container-core` log for the same
     modules, so the count is from CI and not only from Kasm.
   * **Whether the 23 mutation cases this plan added still kill on the
     merged tree.** Step 2 runs them. 3b checks that each killed case
     died for the reason its comment gives, not just that it died.

5. **3d's brief names the host side first.** The VMM part of the scope
   is small: 455 added lines in `src/vmm/src/main.rs`. It is also the
   only part outside the sandbox, and it parses what the guest sends:
   a refusal status and a virtual offset, and two new map extent
   states (`zero-at`, `hole-at`, sent by the guest's
   `src/core/src/serial.rs`). Whatever the guest says arrives as untrusted bytes,
   so an unknown status, a status for the wrong marker or an offset
   near `u64::MAX` must not panic the VMM or render misleading text.
   The guest-side questions follow: whether the extent walk is bounded
   for every walker that can reach a flat VMDK's run, and whether #806
   fails closed for every operation, not only the `convert` and
   `compare` that step 2a measured.

6. **Findings are triaged in the management session (step 4), not by
   the agent that found them**, and the fixes land as a follow-up pull
   request after this one merges. An agent that fixes what it finds
   produces a diff nobody planned. The audit pull request carries only
   the record.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2 | medium | sonnet | none | **Materialise the scope and run the mechanical checks; fix nothing.** Work in the phase worktree. (1) Write `d08841e3` and `d42af1f5` one per line to `audit-scope.txt` in your scratch directory and run `tools/audit/scope-patch.sh --commits-file audit-scope.txt > scope.patch`. Confirm stderr reports 21 files and the patch is 12,474 lines (±1%). Anything else means the list is wrong: stop and report. (2) Run `tools/audit/wave1.sh --commits-file audit-scope.txt`, then `tools/audit/wave2-mechanical.sh --commits-file audit-scope.txt`. Keep both outputs whole, with exit codes, and never pipe them through `tail`. Both must print the 21-file count first. (3) Wave 1 runs `make test-rust` after `make instar`, and the two share `src/target/release`. So run `rm -rf src/target/release` and `make instar` before any integration test. (4) Run `tests/test_qcow2_subclusters.py` and `tests/test_check_formats.py` with stestr, saving the subunit stream (`stestr last --subunit > in-scope.subunit`). Report "Ran: N" and the pass, skip, expected-fail and failure counts. A failure that touches no qcow2 data file is replayed with `--serial` before you report it, since host load fails heavy cases on Kasm. (5) Run `tools/mutate-differencing.sh --self-test` and `--check-patterns`. List the 23 case names this plan added (the names added by `git diff d08841e3^1 d42af1f5 -- tools/mutate-differencing.sh`) and run them by name. Report killed or survived for each, with the test that killed it. The harness mutates copies. Never restore `src/` with `git checkout`. (6) Note that `make test-rust` excludes the `no_main` guest binaries and that the `qcow2` crate's `create` tests are feature-gated. Say so rather than reporting the run as total coverage. Report in under 500 words, with paths to the full logs. No commit. |
| 3a | high | opus | none | **Wave 2a, code quality, plus the runbook's style-conformance judgment portion.** Input: step 2's mechanical report; `scope.patch` restricted to `src/` and `tools/`; and the current state of those files. Read both, because the patch says what changed and the tree says what it now means. The question is what the two phases did to each other. Phase 1 built `SubclusterType`, `subcluster_range` and the `RefusedL2Entry` path in `src/crates/qcow2/src/lib.rs`. Phase 2 added `ClusterLookup::DataFileRefused`, `DataFileEntryRefusal`, the `is_data_device()` skip at the head of `read_chain_virtual_cluster`'s device loop, and a bound on the VMDK extent walk. Every `match` on `ClusterLookup` and `RefusedL2Entry` is inside the qcow2 crate, including the walkers `map`, `measure` and `check` link against. Check that each one handles every variant the way qemu would, with no `_ =>` arm swallowing the new one, and that the operations calling those walkers (`map`, `measure`, `check`, `convert`, `compare`, `bench`, `rebase`) all reach a refusal rather than a generic failure. Check that the VMM's eight render sites for the two refusals agree. Look for logic duplicated between the invalid-entry and data-file refusal paths. The qcow2 crate grew by 4,136 lines: the `source-file-size` block asks whether that growth has a seam of its own, such as a test module, and the answer is a judgment, not a gate. Triage every TODO, `unwrap`, `expect` and `unsafe` the mechanical script flagged, skipping `#[cfg(test)]`. Apply the runbook's five style questions (call-table symmetry, `no_std` crates, operation layout, unit-change discipline, inline workflow scripts). Report findings with file:line and blocking or advisory, in under 800 words; fix nothing. |
| 3b | high | opus | none | **Wave 2b, tests.** Input: `scope.patch` restricted to `tests/`, `tools/mutate-differencing.sh` and the crate test modules; step 2's subunit stream and mutation results; and the core CI log for #810's head `fbfaa946` (find the `test-container-core` job with `~/bin/ci-status shakenfist/instar runs --branch 654-subcluster-classification-phase-02-data-device`, then `logs JOB_ID`). Read the whole log. Never use `tail`, because `INSTAR_REQUIRE_LIBVHDI` is a precedent for a green run that skipped its oracle. The main job is to find tests that cannot fail. (1) Name every skip in `test_qcow2_subclusters` and `test_check_formats`, locally and in CI, with its reason. A skip that hides a fixture which never built, or a `qemu-io` invocation that failed silently, is a finding. (2) For each of the 23 mutation cases, confirm the killing test is the one its comment names. A case killed by an unrelated compile error or panic proves nothing. (3) Phase 2 moved the `ScChain` harness to the VMM layout. Check that phase 1's crate tests still build the image shape they describe. (4) Check `EXPECTED_FAILURES` in `tests/test_qcow2_subclusters.py`: each entry should cite an open issue (#806), and none should be expected to fail for a reason the issue does not cover. (5) Find every public function or error path added in scope with no test reaching it from outside the process, per the `functional-test-coverage` block. Report grouped by file in under 800 words; fix nothing. |
| 3c | high | sonnet | none | **Wave 2c, documentation.** Input: the seven files `scope.patch` touches outside `docs/plans/` (`CHANGELOG.md`, `docs/chain-config.md`, `docs/guest-architecture.md`, `docs/qcow2/qcow2-format.md`, `docs/qcow2/qcow2-l1l2-tables.md`, `docs/quirks.md` and `docs/testing.md`), plus `ARCHITECTURE.md` and `docs/format-coverage.md` read in full. The facts that must not disagree between pages: the chain config is version 4; bit 31 of a device's flags is `FLAG_DATA_DEVICE` and the host sets it; a data device sits in the slot right after its owner, or after a flat VMDK's run of extents; an invalid L2 entry and the two data-file entries are refused with qemu's wording; and an extended-L2 unallocated subcluster reads through to the backing file. For each, find every page that states it and check each against the code (`src/shared/src/lib.rs`, `src/vmm/src/main.rs`), not against another page. Check that `docs/testing.md`'s case count matches `EXPECTED_CASES`. Then run the runbook's four plan checks one at a time over `PLAN-654-subcluster-classification.md` and `docs/plans/index.md`. This branch's planning commit already corrected Future work, the `## Status:` line and the Open questions (see this plan's survey), so check those corrections rather than redoing them: grep the master plan for each of #794, #795, #804, #805, #806, #807 and #808. Report in under 600 words; fix nothing. |
| 3d | high | opus | none | **Wave 2d, security, host side first.** (1) What the VMM parses from the guest. The two refusals travel as a `send_error` whose operation string is a marker (`InvalidL2EntryRefusal` and `DataFileEntryRefusal`, `src/shared/src/lib.rs:3203` onwards). The VMM captures them at `src/vmm/src/main.rs:1020-1023` and renders them through `l2_entry_refused()` and `l2_entry_refusal_error()` (`:841-885`). The guest's `src/core/src/serial.rs` also sends two new map extent states, `zero-at` and `hole-at`, which the VMM's `map_state_triple` (`:16724`) parses. The status, virtual offset and state strings all arrive from the guest and are untrusted. Establish by reading and by running what an unknown status, a status sent under the other marker, a truncated message and an offset near `u64::MAX` do. None may panic the VMM or render text that misdescribes the image. (2) `write_chain_device_entries` marks data devices and masks bit 31 out of info-reported flags. Confirm a crafted image cannot make the host mark a device it did not open as a data file, and that external data file paths still pass the same allowlist as backing files. (3) Guest side: `ChainConfig::data_devices_are_owned` is a self-consistency check on a config the host writes. Confirm every reader of a data device goes through it, and that the VMDK extent-walk bound holds for every walker that can reach a flat VMDK's run (convert, compare, map, measure, check, rebase), not only the one phase 2 bounded. (4) #806 says a backing image's own data file is not discovered and reads fail closed. Phase 2 measured `convert` and `compare` only. Establish for every operation that reads such an image whether it fails closed or quietly reads the wrong device or zeros. A quiet wrong answer is at least high. Cite or measure every claim about qemu (qemu v10.0.0 source; static builds in `instar-testdata`). Report each finding with severity, file:line, vulnerability class and a recommended fix, in under 800 words. File any critical or high finding as a `security-audit` issue; fix nothing. |
| 4 | high | — | none | **Management session.** Read all four reports together. Assign every finding blocking, advisory-fix-now, or filed-as-issue, and record each with its evidence under an *Audit findings* heading in this plan. Check a finding against the deciding phase's plan before calling it a defect: a disagreement with a recorded decision is filed, not fixed. Re-measure at least one claim from each report. Measure the Future work entry with no issue (see the survey) and file it or record that it no longer holds. Then close out. If the audit raised findings, the plan stays `In progress` and the fixes land as a follow-up pull request after this one merges; that pull request records this phase's merge commit and sets `Complete`. If it raised none, set this phase's `Status`, the master plan's `## Status:` line and its `docs/plans/index.md` row to `Complete` here, and leave the `Merged` cell empty. Commit subject: "Record the PLAN-654 push audit." |

Steps 3a to 3d read only and can run in parallel once step 2 has
reported. Step 2 writes build output to the shared worktree, so it runs
alone and first.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Step 2 transcribes a SHA wrongly and the audit reads a subset | Step 2 stops unless `scope-patch.sh` reports 21 files and about 12,474 lines. Both numbers were measured during the survey, and a mis-transcription moves them. |
| Host load fails heavy integration cases and a flake is read as a finding | Step 2 replays any failure that touches no qcow2 data file with `--serial` before reporting it. That pattern was measured on Kasm in both earlier phases. |
| The mutation run takes long enough to tempt a subset | All 23 cases run. Phase 2's harness move is the reason the audit runs them, and the cases most at risk are phase 1's, which a subset would be tempted to drop. |
| An agent treats the runbook's model table as authoritative and under-reads | Decision 3 states the override and its reason. Each step row carries its own model and effort, and that is what the implementer follows. |
| A finding re-litigates a choice a phase decided in writing | Step 4 checks each finding against the deciding phase plan. Both phases' execution notes record what review raised and what was declined. |
| An agent asserts a plausible but wrong qemu capability, as this repository's history says they do | 3d's brief requires cite-or-measure, and step 4 re-measures at least one claim per report before accepting it. |

## Definition of done

Falsifiable, in the order a reviewer can check them:

* `scope.patch` was built by `tools/audit/scope-patch.sh` from a file
  naming exactly `d08841e3` and `d42af1f5`, reports 21 files, and is
  within 1% of 12,474 lines.
* `tools/audit/wave1.sh --commits-file` exits 0 over that scope, or each
  non-zero exit is recorded in this plan with its disposition. Both
  scripts printed the 21-file count.
* The two in-scope integration modules ran on a freshly built binary.
  This plan records their "Ran: N" and the count of each outcome, and
  names every skip with its reason.
* All 23 mutation cases this plan added were run on the merged tree.
  This plan records each one as killed, with the killing test, or as
  survived with a disposition.
* All four wave 2 reports exist, and each names the files it read. Each
  of their findings appears under *Audit findings* with a disposition:
  blocking, advisory, or an issue number.
* Every finding disposed of as an issue has a real issue number that
  `gh issue view` shows open.
* `grep -c` on the master plan finds each of #794, #795, #804, #805,
  #806, #807 and #808 inside its Future work section.
* The Future work entry on check's allocation count names an issue, or
  has been removed with the measurement that retired it recorded here.
* If the audit raised no findings: this phase's `Status`, the master
  plan's `## Status:` line and its `docs/plans/index.md` row all read
  `Complete`, and this row's `Merged` cell is empty. If it raised
  findings: all three still read `In progress`, and this plan names the
  follow-up pull request's scope.
* `docs/plans/order.yml` is untouched.
* `pre-commit run --all-files` passes.

## Back brief

Confirm before step 2:

* That models and efforts follow this plan's step table rather than the
  runbook's (Decision 3).

Gate before steps 3a to 3d are spawned: report step 2's output (scope
size, wave 1 exit code, integration counts, mutation results) and wait.
Four high-effort agents are the expensive part of this phase, and if
step 2 shows a mutation case surviving or a fixture skipping, the
briefs should change before they run.

## Audit findings

All four judgment reports came back with no critical or high finding,
and the code reviewers raised no blocking finding. The documentation
review raised four blocking findings, all pages this plan made false.
So the audit has findings: the plan stays `In progress` and the fixes
land as one follow-up pull request after this one merges (Decision 6).

Each finding below says what was re-measured, and by whom. The reports'
full text is not kept here; their scratch artefacts were on Kasm and
are not part of the record.

### For the follow-up pull request

Defects this plan introduced, or made true by changing the code under
them. Labels: **D** documentation, **C** code, **T** tests.

| # | Finding | Source | Verified |
|---|---|---|---|
| D1 | `docs/chain-config.md:322` gives `ChainConfig::VERSION = 3`; the code says 4 (`src/shared/src/lib.rs:5139`). Blocking. | 3c | management session, read |
| D2 | `docs/quirks.md:4859` heads the entry "two related data-file shapes still diverge", but its body (`:4904-4915`) says both are now refused by name. Blocking. | 3c | management session, read |
| D3 | `docs/check.md:31` lists "host-without-ref" as a validated bitmap shape. Phase 1 deliberately stopped reporting it, since qemu accepts it (`src/crates/qcow2/src/lib.rs:1713-1715`). Blocking. | 3c | management session, read |
| D4 | `docs/map.md` predates phase 1's map changes, and the page is outside the audit scope because neither pull request touched it. It says `offset` is omitted when `present` is false, but `hole-at` carries one (`:81`). It documents three states, but the parser emits five (`:100-107`). It never says map refuses invalid and data-file entries. It says standard-L2 `QCOW_OFLAG_ZERO` is not honoured, but `map_state_for` (`src/crates/qcow2/src/lib.rs:2144`) honours it (`:120-127,228-234,277`). Blocking. | 3c | management session, read |
| D5 | `docs/format-coverage.md:872-873` says compressed clusters "remain in the metadata device", but in a data-file image they are refused. | 3c | — |
| D6 | `docs/testing.md:2356` calls the subcluster group "the first of the two groups not about a differencing format", but two more follow. Fix with it the pre-existing "26 writer cases" at `:2475` against twenty-seven at `:2184`. | 3c | — |
| D7 | `docs/qcow2/qcow2-l1l2-tables.md:125-137`: the `get_cluster_type` pseudocode omits the extended-L2 zero-bit rule and the data-file `COPIED` rule that this plan's classifier transcribes, so it contradicts the new text from `:238` onwards. | 3c | — |
| D8 | `docs/vmdk/vmdk-extents.md:258` says a monolithicFlat descriptor with `parentFileNameHint` is rejected. `docs/format-coverage.md:343` says it is followed, and step 2a of phase 2 measured it being followed. Pre-existing text, but this plan's measurement is what proves it wrong. | 3c | management session, read |
| C1 | The two refusals carry different offsets for the same entry. The reader's invalid-entry refusal sends the chunk's virtual offset (`src/crates/qcow2/src/lib.rs:16812`). The data-file refusal, and both walkers, send the cluster's (`:16825-16827`). So `dd` with a mid-cluster window names a different offset from `map`. The reader should build a `RefusedL2Entry` and call `send_refused_l2_entry` (`:18384`): one dispatch point instead of two hand-written calls. | 3a | — |
| C2 | `count_allocated_in_l2_extended` ends with `_ => 0` over `SubclusterType` (`:2132`). The arms should be named, as `map_state_for` does, so a new variant is a compile error and not a silent "not counted". | 3a | — |
| C3 | `read_chain_split_at_granule` (`:16176`) is an `unsafe fn` with no `# Safety` section; its siblings have one. | 3a | — |
| C4 | The VmdkDescriptor extent-walk comment (`:18082-18092`) narrates history ("Before the mark it ran on until..."). Keep the reason, drop the past. | 3a | — |
| C5 | `clip_to_window` (`src/operations/map/src/main.rs:105-110`) repeats the "which states carry an offset" match that `MapExtentState::file_offset()` (`src/shared/src/lib.rs:861`) owns. This plan added `zero-at` and `hole-at` to both, which is the drift risk. | 3a | — |
| C6 | An unrecognised refusal status renders as a sentence about the image: "corrupt qcow2 L2 entry in image with external data file" (`src/vmm/src/main.rs:884`). It should say the status was not recognised. 3d's crafted-frame unit test produced statuses 0 and 22 from truncated frames. That is informational, since only the guest writes the port. | 3d | — |
| C7 | `pub unsafe fn init_chain_qcow2_states` (`src/crates/qcow2/src/lib.rs:18238`) has no callers, and it skips both `segmentation_covers` and `data_devices_are_owned`. Since phase 2 the reader trusts the device marks, so a future caller would bypass the check that makes that safe. Delete it. | 3a | management session: no caller in `src/` |
| C8 | `CHAIN_CONFIG_MAGIC` (`src/vmm/src/main.rs:334`) duplicates `shared::ChainConfig::MAGIC`. Phase 2's D7 removed the version's duplicate for the same skew reason. | 3a | — |
| T1 | `data_file_mid_chain`'s convert and compare entries are `expectedFailure`s, which absorb any failure. The #806 reason says the read fails closed, but `check_convert` asserts `rc == 0` and then the bytes, so a regression to wrong bytes under rc 0 would still count as an expected failure. Pin the current behaviour (rc != 0, no output claimed correct) instead. | 3b | — |
| T2 | The VMM renders both refusals for `dd`, `bench` and `rebase` (`src/vmm/src/main.rs:14871`, `:6268`, `:11104`), but no integration test drives a refused entry through any of the three. | 3b | — |

### Filed as issues

Problems that predate this plan, or lie outside what it changed.

| Issue | Finding | Source | Verified |
|---|---|---|---|
| [#812](https://github.com/shakenfist/instar/issues/812) | qemu refuses an unaligned host cluster offset (`block/qcow2-cluster.c:686-694`) and a zero flag in a pre-v3 image (`:657-664`); instar reads both. `instar map` reports the unaligned cluster as data at offset 328192, exit 0. `instar measure` exits 0. `instar convert` of the v2 image exits 0 and serves zeros for a cluster holding `0x55`. | 3a (unaligned measured; v2 not measured) | management session re-measured both |
| [#813](https://github.com/shakenfist/instar/issues/813) | `TestZstdBackingChain` has never run. Its probe creates a qcow2 on `/dev/null`, which fails with "Image is not in qcow2 format" whatever qemu supports. The two "capability" skips step 2 recorded are this. | 3b | management session re-measured |
| [#814](https://github.com/shakenfist/instar/issues/814) | `check`'s total cluster count is one higher than qemu's on every qcow2 image: `2/8193` against `1/8192` on a plain image. The numerator gap is #804. This is the Future work entry that named no issue, and it is not specific to data files. | step 4, from 3d's note | management session measured |
| [#815](https://github.com/shakenfist/instar/issues/815) | `commit` into a backing file that owns a data file refuses correctly, but blames the overlay. | 3d | — |
| [#816](https://github.com/shakenfist/instar/issues/816) | `map_state_triple` renders an unknown extent state, including the guest's own `"unknown"`, as data. | 3d | — |

New evidence went to two existing issues as comments:
* **#806:** every reading operation fails closed but gives no reason (rebase reports "the overlay's header could not be parsed"). The comment suggests an interim refusal by name at discovery.
* **#807:** `instar compare` reports the mid-chain image as differing from itself. Re-measured by the management session.

### Declined

* **Splitting `src/crates/qcow2/src/lib.rs`** (3a, `source-file-size`).
  This plan grew it by about 1,230 production and 2,836 test lines, and
  3a named two seams that already exist: the I/O-free classifier block
  and the 11,650-line test module. The block makes this a judgment, not
  a gate. A split is its own change and its own review, and it would
  churn every line both phases touched in the same release.
* **The 19-line comment at the head of the device loop** (3a). 3a
  judged it justified as the layer-advance invariant. Its overlap with
  `data_devices_are_owned`'s doc is a sentence.
* **Pinning the four `_COMPRESSED_MAP` expected failures more tightly**
  (3b). `tests/test_map.py`'s `TestMapDivergenceRegression` already
  pins that divergence exactly. No open issue tracks it;
  `docs/quirks.md:1027-1038` records it as future work for `map`.
* **Two `check_data_file` call sites with no mutation case** (3b).
  `metadata_walks_refuse_data_file_entries_as_qemu_does` asserts both,
  so the gap is in demonstration, not coverage.
* **`data_device_idx = 1` in `docs/vmdk/vmdk-extents.md:253` and
  `docs/commentary/architectural-decisions.md:171`** (3c). Both
  describe device 0, a top-level descriptor, for which `idx + 1` is 1.
* **`docs/measure.md:138,189`, "under investigation"** (3c). The
  report did not measure it, it predates this plan, and it is not about
  anything this plan changed.
* **`docs/guest-architecture.md:100`, "phase-7 step-7z"** (3c). It
  predates this plan, so it is #592's backlog.

### Negative results worth keeping

* **Cross-phase fit** (3a): every `match` on `ClusterLookup`,
  `SubclusterType` and `RefusedL2Entry` names every variant, except C2's
  wildcard, which is correct today. All eight VMM render sites go
  through `l2_entry_refused()` and `l2_entry_refusal_error(op)`, and
  every caller of the reader and of the walkers is covered. `check`
  counts these entries as corruptions, as `qemu-img check` does.
* **Mutation cases** (3b): all 23 fail for the behaviour they mutate.
  A build error scores BROKEN rather than PASS, and the baseline
  requires exactly one matching test to pass unmutated. Two cases were
  re-run by hand to read the failure. Phase 1's tests still build the
  shapes they describe under phase 2's harness, because `sc_read`
  asserts its own config passes `segmentation_covers` and
  `data_devices_are_owned`.
* **CI parity** (3b): #810's `integration-core` job ran the same 314
  tests with the same two skips (#813) and the same six expected
  failures as step 2's local run.
* **Hostile guest input** (3d): seven adversarial VMM unit tests, run
  in a scratch copy, all passed (`make test-rust` 2687, 0 failed). They
  covered unknown statuses, `u64::MAX` offsets, crossed markers, near-miss
  marker spellings, 4 MiB of random bytes and a 4,000-byte map state.
  No input panics the VMM, and no guest string reaches rendered output.
* **The data-device flag** (3d): all three chain-config writers go
  through `write_chain_device_entries`, which sets bit 31 only on the
  data files it opened. An image's own flags cannot mark a device.
* **Paths** (3d): the top image's data file and VMDK extents pass the
  same `validate_backing_path` as backing files. A backing image's data
  file is never opened (#806), so no path escapes the allowlist that
  way.
* **The extent walk** (3d): the single VMDK extent walk stops at
  `device_count` and at the first unmarked device. map, measure and
  check do not walk extents, and rebase's reader refuses descriptors.
* **#806 across operations** (3d): every operation that reads the
  mid-chain image fails closed or refuses by name. None serves wrong
  data, except the two already filed: #807 (compare) and #642
  (measure, which reports the top layer whether or not a data file is
  involved).

### Mutation cases on the merged tree

Step 2 ran each case by name on HEAD `29bd35e2`. Each was killed, meaning its named test failed under the mutation, and 3b judged every kill causal. A test that kills two cases appears twice.

| Case | Killed by |
|---|---|
| `qcow2-subcluster-alloc-at-host-zero-not-refused` | `extended_l2_wide_path_refuses_an_alloc_bit_at_host_zero` |
| `qcow2-subcluster-alloc-at-host-zero-not-invalid` | `extended_l2_alloc_bit_at_host_zero_is_invalid` |
| `qcow2-chunk-not-split-at-backing-clusters` | `sc_unallocated_runs_split_at_smaller_backing_clusters` |
| `qcow2-data-device-read-as-the-next-layer` | `sc_issue_792_unallocated_cluster_in_a_data_file_image_reads_the_backing_file` |
| `qcow2-data-device-read-at-the-bottom-of-the-chain` | `sc_issue_792_unallocated_cluster_without_a_backing_file_reads_zeros` |
| `qcow2-data-device-read-by-a-subcluster-descent` | `sc_issue_792_unallocated_subcluster_runs_descend_past_the_data_device` |
| `vmdk-descriptor-extent-walk-unbounded` | `vmdk_descriptor_extent_walk_stops_at_its_last_extent` |
| `chain-config-data-device-at-a-chain-top-accepted` | `init_chain_states_refuses_a_data_device_at_the_top_of_a_chain` |
| `chain-config-data-device-index-on-any-device-accepted` | `init_chain_states_refuses_a_data_device_index_on_a_device_that_cannot_own_one` |
| `chain-config-data-device-index-not-checked` | `init_chain_states_refuses_a_data_device_index_that_misses_the_next_slot` |
| `chain-config-data-device-index-any-later-slot` | `init_chain_states_refuses_a_data_device_index_that_misses_the_next_slot` |
| `chain-config-unclaimed-data-device-accepted` | `init_chain_states_refuses_a_data_device_no_image_claims` |
| `chain-config-qcow2-claims-a-run` | `init_chain_states_refuses_a_second_data_device_after_a_qcow2` |
| `vmm-data-device-written-unmarked` | `write_chain_device_entries_marks_every_data_device_and_nothing_else` |
| `vmm-image-flags-keep-the-data-device-mark` | `write_chain_device_entries_marks_every_data_device_and_nothing_else` |
| `qcow2-data-file-compressed-entry-read` | `sc_data_file_compressed_entry_is_refused_by_name` |
| `qcow2-data-file-host-offset-not-checked` | `sc_data_file_host_offset_that_is_not_the_guest_offset_is_refused` |
| `qcow2-data-file-rules-before-invalid` | `sc_data_file_invalid_entry_is_refused_as_invalid_first` |
| `qcow2-data-file-entry-not-refused` | `sc_data_file_host_offset_that_is_not_the_guest_offset_is_refused` |
| `qcow2-data-file-measure-walk-unchecked` | `metadata_walks_refuse_data_file_entries_as_qemu_does` |
| `qcow2-data-file-map-walk-unchecked` | `metadata_walks_refuse_data_file_entries_as_qemu_does` |
| `vmm-data-file-refusal-not-captured` | `add_byte_captures_the_data_file_entry_refusal` |
| `vmm-data-file-refusal-not-rendered` | `data_file_entry_error_names_qemu_wording_for_each_rule` |

### Fixed in this record

* The master plan's Future work still carried "qemu's data-file offset check", which is #803. Phase 2 closed it, so the entry now records the two host-offset checks qemu applies that #803 did not cover (#812). The entry that named no issue now names #814. #812, #813, #815 and #816 have entries of their own (3c).
* 3c read step 4's "this phase's `Status`" as a line in this file. No phase plan has one; it means this phase's `Status` cell in the master plan's Execution table, which stays `In progress`.

### What this audit did not cover

* The `create`-feature tests in the qcow2 crate, and the `no_main`
  guest binaries, which `make test-rust` excludes.
* The libvhdi oracle cases of the mutation harness. None of the 23 is
  one, and `vhdiinfo` is not installed on Kasm.
* The integration modules outside this plan's two. Phase 2's definition
  of done ran nine.

## Execution notes

* 2026-10-22 (AEDT), back brief. Michael confirmed the step table's
  models over the runbook's (Decision 3). He relaxed the gate before
  3a to 3d: carry on without waiting if step 2 is clean, and stop only
  if a mutation case survives, a fixture skips without a capability
  reason, wave 1 fails or the scope size is off. He also gave
  permission to push and open the pull request once the definition
  of done passes.
* Step 2, on HEAD `29bd35e2`, was clean, so 3a to 3d ran without a
  pause.
  * **Scope:** `scope-patch.sh` reported 21 files and 12,474 lines,
    the survey's figures exactly, and both wave scripts printed the
    21-file count.
  * **Wave 1:** `wave1.sh --commits-file` exited 0. `make test-rust`
    passed 2680 with 0 failed and 3 ignored.
  * **Integration:** on a freshly built binary, the two in-scope
    modules ran 314 tests: 306 passed, 2 skipped, 6 expected
    failures, 0 unexpected successes, 0 failed.
    * Both skips are `TestZstdBackingChain` cases, skipped because
      qemu-img 10.0.13 does not support ZSTD.
    * Two of the expected failures are `data_file_mid_chain` (#806).
      The other four are the compressed-cluster map divergence
      documented in `docs/quirks.md`.
  * **Mutation:** `--self-test` and `--check-patterns` (126 land)
    pass, and all 23 cases fail their named test as required.
* Step 2 corrected this plan in two places, both now fixed at source:
  * The plan added 23 mutation cases, not 21. The net count of 21
    hid two phase 1 cases retargeted under new names.
  * `src/core/src/serial.rs` carries no refusal marker. Its two lines
    are map extent states, sent by the guest. Decision 5 had also
    counted the file as host-side code, but `src/core` is the guest's
    runtime. Decision 5 and 3d's brief now point at the marker in
    `src/shared` and the VMM's capture and parse sites.
* 3a, 3b, 3c and 3d ran in parallel on HEAD `3ae3c117`.
  * The mutation harness edits source in place and rebuilds `instar`.
    Once 3b was running, it was told to re-run cases only in its own
    detached worktree, so it could not change the tree 3a and 3d were
    reading, or the binary 3d was running. 3b confirmed it never ran
    the harness in the shared worktree.
  * 3d's scratch worktree could not be removed completely, because
    Docker left a root-owned `.cargo-cache` in it. It is deregistered
    and lives in the session scratchpad.
* 2026-10-22 (AEDT), step 4. The triage is under *Audit findings*.
  * The management session re-measured each issue it filed except
    #815 and #816, and read each blocking documentation finding against
    the code.
  * 3a's unmeasured v2 zero-flag claim turned out worse than reported:
    `convert` serves zeros, exit 0.
  * The survey's "Future work entry with no issue" is #814, and it is
    not specific to data files.
  * Outcome: findings remain, so per Decision 6 the plan stays
    `In progress`, and the follow-up pull request carries D1 to D8, C1
    to C8, T1 and T2. That pull request records this phase's merge
    commit and sets `Complete`.
* 2026-10-22 (AEDT), after this phase merged as `2679929` (#817).
  * The follow-up pull request fixed all eighteen findings, and the
    plan is `Complete`.
  * C5 added `MapExtentState::advanced_by` beside `file_offset`,
    because a getter cannot rebuild a state.
  * C1 moved the code two mutation cases patch; both were retargeted,
    and the harness still holds 126 cases.
  * T2 found that rebase onto a raw backing file fails as an
    unparseable overlay header. That is #632, so T2's tests use qcow2
    backing files.
