# PLAN: Differencing phase 9 — coverage fuzzing of the locator parsers

Phase 9 of [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/).

## Goal

Discharge the master plan's success criterion that `crates/vhd` and
`crates/vhdx` "parse parent locator structures and are clean under the
new fuzz targets, including the adversarial fixtures from phase 2".

The survey narrows that considerably. The *emit* side is already
fuzzed — phases 5 and 6 extended `fuzz_create_emitters` as they went —
and the phase 2 adversarial fixtures are already seeded into the
corpus. What is left is the *read* side: the VHD parent-locator
parsers are reached by no fuzz target at all, and the VHDX one is
reachable only through a path nobody has measured.

## Planning effort

**Medium**, as [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/) specifies
for this phase. The pattern is well established — there are 40
existing targets to copy — and the survey has already done the
expensive part. The single judgement worth arguing about is decision 3.

## Review effort

**Medium.** Nothing in this phase changes what instar reads or writes;
a wrong answer costs fuzz coverage, not correctness. The exception is
step 9e, which may touch `src/crates/vhd/src/lib.rs`, and decision 7
bounds that to a docstring.

## Scope

**In scope:**

* A buffer-based fuzz target driving the VHD parent-locator read path:
  `VhdParentInfo::parse`, the eight-entry locator table, the
  `locator_defect` placement rules, `preferred_locator`'s precedence
  and ambiguity logic, and `decode_name`.
* Measuring, rather than assuming, whether the existing
  `fuzz_vhdx_metadata` target reaches `vhdx::parse_parent_locator`,
  and adding a direct VHDX target only if it does not.
* Corpus routing so the phase 2 adversarial locator fixtures reach the
  new target, and a hand-built minimal differencing seed so the target
  is not cold on a machine with no testdata.
* Proving the target can fail, by planting a policy defect and
  watching it get caught.
* The three stale-figure defects the survey found in the fuzz
  plumbing, all in files this phase already edits.

**Out of scope:**

* Fuzzing the compose path. That is phase 15 and cannot be done
  before phases 11 to 13 give instar a compose path at all.
* The differential fuzzer's silent degradation
  ([#590](https://github.com/shakenfist/instar/issues/590)). Same word
  in the name, different program: that is `scripts/differential-fuzz.py`
  cross-checking against libyal tools, not coverage-guided fuzzing of
  a parser.
* Fixing any parser defect the fuzzing finds, unless it is in code
  phases 3 to 7 added — see decision 6.
* The plan-reference cleanup in docstrings
  ([#592](https://github.com/shakenfist/instar/issues/592)) beyond the
  single line this phase makes stale (decision 7).

## What the survey found

Surveyed against `c416abd` (phase 8's merge commit). The master plan's
one-line description of this phase — "coverage fuzzing of the locator
parsers" — is still accurate, but four of its unstated assumptions
were not.

**1. The emit side is already fuzzed; this phase is read-side only.**
The master plan was written before phases 5 and 6 executed, and each
extended the fuzz targets as it went rather than deferring to here:

| Commit | Phase | Target | Lines |
|---|---|---|---|
| `ac9a227` | 3 | `fuzz_vhdx_metadata` | +6 |
| `ee07a2f` | 5 | `fuzz_create_emitters` | +10 |
| `d04ed54` | 5 review | `fuzz_create_emitters` | +23 |
| `1bd8679` | 6 | `fuzz_create_emitters` | +6 |

`fuzz_create_emitters` now feeds `parent_unique_id`,
`parent_timestamp` and `parent_data_write_guid` from the corpus and
drives both differencing emitters
(`src/fuzz/fuzz_targets/fuzz_create_emitters.rs:80-84`, `:164-203`,
`:264`). `ac9a227` passes a 1 MiB metadata region length specifically
"so that the parent locator staging path stays reachable"
(`src/fuzz/fuzz_targets/fuzz_vhdx_metadata.rs:57-61`). Nothing in this
phase should re-fuzz the emitters.

**2. The VHD read path is reached by no fuzz target.** Grepped for
each function rather than for the crate:

| Function | Location | Fuzzed today |
|---|---|---|
| `VhdParentInfo::parse` | `src/crates/vhd/src/lib.rs:1018` | no |
| `VhdParentLocatorTable::parse` | `:836` | no |
| `VhdParentLocator::parse` | `:589` | no |
| `locator_defect` | `:707` | no |
| `preferred_locator` | `:885` | no |
| `VhdParentInfo::decode_name` | `:1064` | no |

`fuzz_vhd_footer` calls only `VhdFooter::parse`,
`VhdDynamicHeader::parse`, `compute_checksum` and the geometry
helpers (`src/fuzz/fuzz_targets/fuzz_vhd_footer.rs:6-31`).
`fuzz_vhd_bat` calls only `VhdState::init` (`:19`). Neither reaches
the parent fields: `VhdDynamicHeader::parse` and `VhdParentInfo::parse`
are deliberately separate entry points, because — as the crate's own
docstring says — most callers should not "pay for parent fields none
of them read" (`src/crates/vhd/src/lib.rs:974-977`).

The crate already knows this is the gap. `VhdParentInfo::parse`'s
docstring ends: "No I/O and no allocation: this is the entry point
phase 9 points a fuzz target at" (`:1016-1017`).

**3. The VHDX read path is reachable but unmeasured.**
`vhdx::parse_parent_locator` (`src/crates/vhdx/src/lib.rs:1015`) is
called from `parse_metadata` (`:1134`, at `:1435`), and
`fuzz_vhdx_metadata` calls `parse_metadata` directly at offsets
`[0, 0x10000, 0x30000]` with a 1 MiB length
(`src/fuzz/fuzz_targets/fuzz_vhdx_metadata.rs:62-71`). So the path
exists. Whether libFuzzer *reaches* it — a locator item requires a
well-formed region table, a metadata table, and an item whose offset
clears `METADATA_ITEMS_MIN_OFFSET` (`src/crates/vhdx/src/lib.rs:513`)
— is a coverage question, and nobody has run the coverage report.
Step 9a answers it with a number instead of a judgement.

**4. The corpus is already correct; only the routing is missing.**
All fifteen phase 2 differencing fixtures are in `tests/manifest.json`,
including the six adversarial locator images:

```
custom/audit/vhd-diff-locator-{dotdot,etc-passwd,overlong,unc,url,conflicting}.vhd
```

all typed `vpc`, plus `vhdx-diff-{parent,child}` typed `vhdx`.
Seeding is driven by the manifest, not by a directory walk
(`:516-535`), and routing is by detected format through
`FORMAT_TO_TARGETS` (`:27-38`). A new target therefore gets these
fixtures **only** by being added to that map — there is no fallback.

**Corrected during execution — this paragraph originally claimed the
six adversarial fixtures were already seeded into four targets, and
that was wrong.** Step 9b checked the corpus rather than the manifest
and found none of them in any target directory. They are 4,198,912
bytes (4,201,984 for `conflicting`) against a `MAX_SEED_SIZE` of
4,194,304 (`scripts/extract-fuzz-corpus.py:55`), and `copy_seed`
refuses any untruncated file above it (`:68`). They miss by about
4.5 KB of footer and header overhead on a 10 MiB nominal disk.
Confirmed independently by content hash and by
`find src/fuzz/corpus -type f -size +4190k` returning nothing.

The class is wider than these six: **43 of the 208 manifest fixtures,
21%, are dropped this way**, across luks, qcow2, raw, vhdx, vmdk and
vpc, and nothing reports which — `copy_seed` returns `False` and the
caller counts it as `skipped` alongside genuinely absent files. Filed
as [#593](https://github.com/shakenfist/instar/issues/593). Step 9d
takes the part this phase needs, per decision 10; the rest is out of
scope.

The lesson is the one this plan lineage keeps relearning: membership
in the manifest was checked, membership in the corpus was inferred.
Grep for the thing itself.

**5. Every consumer of these parsers outside the crates is a test.**
`VhdParentInfo::parse`, `decode_name`, `preferred_locator` and
`vhdx::parse_parent_locator` are called only from
`src/crates/create/tests/round_trip.rs`. No operation calls any of
them; that is phase 11's job. This is worth stating plainly because it
is the obvious objection to this phase — fuzzing a parser with no
production consumer — and the answer is in the next finding.

**6. There is a production consumer, and it is a second decoder of the
same bytes.** The `info` op prints `backing file:` for a differencing
VHD, but does not use `VhdParentInfo` at all: it decodes the raw
512-byte parent-name field itself with
`shared::decode_utf16_field_nul_terminated`, gated on
`VhdDynamicHeader::parse` returning `Some`
(`src/operations/info/src/main.rs:902-911`). So two independent
decoders read the same attacker-controlled field, one of them in a
`no_main` guest binary that cannot run `cargo test` at all. The
comment at `:902-910` records that the gate exists precisely because
without it "an image with `disk_type = 4` and an arbitrary
`data_offset` gets 512 bytes of unrelated file content decoded as
UTF-16BE and printed". That is the kind of claim a fuzz target should
be holding down.

**7. Three stale figures in the fuzz plumbing**, all in files this
phase edits anyway, none of them about differencing:

* `.github/workflows/coverage-fuzz.yml:128` hardcodes `N_TARGETS=32`
  when there are 40 targets. It is used only on the manual-dispatch
  path with no explicit target list, where it caps per-target duration
  at `450 * 60 / N_TARGETS`; at 32 it computes a cap 25% too generous
  for the 450-minute budget, so a "fuzz everything for a long time"
  dispatch can overrun into the 480-minute job timeout.
* `docs/testing.md:1252-1256` says "With the current 27 targets that
  gives the 17 deep targets ~24 min each versus ~17 min under an even
  split." Measured against the tree by running the script itself:
  40 targets, 15 fast at 300s, 25 deep at **900s**, versus 675s under
  an even split. Every figure in that sentence is wrong.
* `docs/testing.md` carries seven mangled phrases from a botched
  find/replace — "the PLAN-s worknapshot", "the PLAN-c workheck-repair",
  "the PLAN-q workcow2-write-infrastructure" — at lines 265, 984, 994,
  1013, 1154, 1165 and 1175. Introduced by `f415ba9` ("Move
  AGENTS/ARCHITECTURE detail into docs/."), which was scrubbing plan
  references and truncated the surrounding words.

**8. A new target has six registration points, two of them
hand-maintained lists that nothing checks.** Registering
`fuzz_vhd_parent` means touching all of:

1. `src/fuzz/fuzz_targets/fuzz_vhd_parent.rs` — the target itself.
2. `src/fuzz/Cargo.toml` — a `[[bin]]` stanza.
3. `.github/workflows/coverage-fuzz.yml:196-236` — the hardcoded
   `TARGETS=(...)` array used whenever no explicit target list is
   given, which is every nightly and every post-merge run. **A target
   missing from this list is never fuzzed in CI at all**, and nothing
   compares it to the tree.
4. `tools/ci/fuzz-tier.sh:34` — `FAST_TIER`, whose comment asks the
   reader to "keep this list in sync with `src/fuzz/Cargo.toml`" with
   nothing enforcing it. Omission is safe here — an unlisted target
   defaults to the deep tier — so this one is a cost question, not a
   correctness one.
5. `scripts/extract-fuzz-corpus.py:27-38` — `FORMAT_TO_TARGETS`, with
   no fallback: an unlisted target gets `fuzz_format_detect`'s seeds
   and nothing else.
6. `docs/testing.md:1100-1104` — the count sentence and the table.

Checked rather than assumed: the workflow's array and the tree agree
exactly today, 40 for 40. So this is a trap for the next person, not
an existing defect, and decision 9 says what to do about it.

The target count itself is *not* drifted: `src/fuzz/fuzz_targets/`,
the `[[bin]]` count in `src/fuzz/Cargo.toml` and the table in
`docs/testing.md` all say 40. Nothing in the master plan's phase 9 row
or the `index.md` row was factually wrong, so no correction at source
is needed this phase — which is a first for this plan.

## Decisions

**1. One new VHD target, not one per function.** `fuzz_vhd_parent`
drives the whole read path from a single dynamic-header buffer:
`VhdParentInfo::parse`, then the locator table it returns, then
`preferred_locator`, then `decode_name`. Splitting it per function
would multiply corpus and nightly budget for surfaces that share their
entire input. The existing targets follow the same shape — one target
per *entry point*, not per function.

**2. Buffer-based, not CallTable.** These parsers do no I/O by
construction; the crate's module docs open with a section titled
"Parent locators do no I/O" (`src/crates/vhd/src/lib.rs:7`). A
CallTable target would add a mock I/O layer that the code under test
never calls, costing fuzzer throughput for nothing. `VhdImageBounds`
is a plain descriptor and is synthesised from the fuzz input instead.

**3. The target asserts invariants, not merely the absence of a
panic.** This is the decision most worth arguing with, so here is the
reasoning. These parsers are safe `no_std` Rust reading constant
offsets inside lengths already checked, so a panic-only target would
very likely find nothing and would then be indistinguishable from a
target that reaches nothing at all — which is exactly the unearned
green phase 8 was about. The interesting failures here are *policy*
failures, and they are assertable:

* `preferred_locator` never returns an entry whose `defect` is
  `Some(_)`. That is the whole point of `locator_defect`, and the
  crate's own tests assert it only for hand-built cases
  (`:3322 preferred_locator_skips_malformed_entries`).
* A duplicate platform code whose values disagree resolves to
  ambiguous, never to a winner (`:3406`).
* `decode_name` returns `Some(n)` with `n <= dst.len()`, and writes
  nothing beyond `n`.
* Parsing is deterministic: the same bytes parsed twice give the same
  answer, which catches any accidental dependence on uninitialised
  buffer contents.
* No returned offset or length escapes the `VhdImageBounds` it was
  parsed against — the property `locator_defect` exists to enforce
  (`:707`), stated once at the boundary instead of per rule.

A crash is still a finding; these assertions are what make a
*non*-crash informative.

**4. The new target goes in the fast tier.** It is buffer-based with a
small fixed input shape and will saturate quickly, like
`fuzz_create_emitters` which it most resembles. Measured cost, by
running `tools/ci/fuzz-tier.sh plan 27000 300` over the real target
list: adding it to the fast tier moves the deep targets from 900s to
888s each (−1.3%); adding it to the deep tier would move them to 865s
(−3.9%). Either is negligible, and the fast tier is the honest
classification.

**5. Whether VHDX gets its own target is decided by measurement, not
now.** Step 9a produces a coverage report for `fuzz_vhdx_metadata`
against the phase 2 fixtures. If `parse_parent_locator` shows a
non-zero hit count, VHDX is already covered and step 9c is a corpus
note rather than a new target. If it shows zero, step 9c adds
`fuzz_vhdx_parent` in the same shape as `fuzz_vhd_parent`. Guessing
either way would be guessing about libFuzzer's ability to synthesise a
valid region table, which is not a thing to have an opinion about.

**6. A parser defect found here is fixed only if phases 3 to 7 wrote
it.** Anything older is filed and referenced, on the same rule phase 8
used. The difference from phase 8 is that fuzzing can produce a
genuine memory-safety or panic finding, and a panic in a guest binary
parser is not a "file it and move on" class of bug — so the rule has
one exception: a reachable panic or an out-of-bounds read is fixed
here whatever wrote it, and the fix gets its own commit.

**7. The one docstring this phase invalidates gets updated; the rest
of [#592](https://github.com/shakenfist/instar/issues/592) does not.**
`src/crates/vhd/src/lib.rs:1016-1017` says the function is "the entry
point phase 9 points a fuzz target at". Once the target exists that
sentence is both a plan reference in landed code and stale, so it
becomes a reference to `fuzz_vhd_parent` by name. Every other plan
reference in these crates — and there are many, including several
pointing at `PLAN-differencing-phase-01-pin.md` — is left to #592.

**8. The three stale figures are fixed here.** They are in
`docs/testing.md` and `coverage-fuzz.yml`, both of which this phase
edits to register a new target, and leaving a wrong "27 targets"
sentence immediately above a row this phase adds would be worse than
the scope cost of fixing it. The `N_TARGETS=32` fix derives the count
rather than restating it, so it cannot drift again.

**9. Add a CI guard that the two hand-maintained lists match the
tree.** Survey finding 8 describes exactly the failure phase 8 spent
three rounds refusing: a green run that proves nothing, because a
target absent from `coverage-fuzz.yml`'s array is never executed and
no check would say so. A new fuzz target that CI silently never runs
is worse than no target, because the row in `docs/testing.md` claims
coverage that does not exist. The guard is a short script under
`tools/ci/` — the repo convention forbids more than about five lines
inline in a workflow step — asserting that the `[[bin]]` names in
`src/fuzz/Cargo.toml`, the workflow's `TARGETS` array, and the files
in `src/fuzz/fuzz_targets/` are the same set. `FAST_TIER` is checked
as a subset, not for equality, because omission there is a deliberate
default rather than an error.

This is scope growth beyond "fuzz the locator parsers" and it is
taken deliberately: this phase is the one that adds the first new
target since those lists drifted apart in the workflow's duration
cap, and a guard written now costs one step, where discovering the
omission later costs a nightly run that measured nothing.

**10. Step 9d builds a reshaping seed extractor, not a bigger cap.**
Survey finding 4's correction leaves the six adversarial fixtures
unreachable by any amount of routing. Three fixes were available:
raise `MAX_SEED_SIZE`, add a `HEADER_ONLY_TARGETS` truncation, or
emit a seed in the shape the target actually consumes. The third is
right and the other two are not.

Raising the cap fixes these six and leaves the other 37 dropped
fixtures for someone else to rediscover; it is also a global change
made for a local reason. A truncation is closer but still wrong: the
target treats `data[..1024]` as the dynamic header, and a real VHD
puts the `cxsparse` cookie at `data_offset`, not at zero, so a
header-prefix copy of a whole image is not a valid input to this
target at all — it would sit in the corpus looking like coverage and
contributing none. (The 9b brief asserted the opposite, that the
locator data would be cut off by truncation; that was wrong in its
own way — `data_offset` is 512 in all six and the platform data lies
in 1536..5632, so any truncation at or above 8 KiB keeps the whole
structure. Both statements were reasoning about the file rather than
about the target's input shape.)

So 9d follows `extract_snapshot_parse_seed`, which sets exactly this
precedent for qcow2 snapshot tables: read the footer's `data_offset`,
emit the 1024-byte dynamic header followed by the platform-data
region. A few KB rather than 4 MiB, in the target's own input shape,
and independent of the cap. The wider defect is filed as
[#593](https://github.com/shakenfist/instar/issues/593) and is not
fixed here.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | medium | sonnet | none | **Measure what the existing targets reach.** Inside the `instar-build` devcontainer (`make instar-devcontainer` if the image is missing — note it is pruned daily by `docker-image-prune.timer`, so expect to rebuild), seed the corpus with `python3 scripts/extract-fuzz-corpus.py --testdata ../instar-testdata`, then run `cargo fuzz coverage fuzz_vhdx_metadata` and `cargo fuzz coverage fuzz_vhd_bat` from `src/fuzz` after a 300s run of each. Report the hit counts for `vhdx::parse_parent_locator` (`src/crates/vhdx/src/lib.rs:1015`), `vhd::VhdParentInfo::parse` (`src/crates/vhd/src/lib.rs:1018`) and `vhd::VhdParentLocatorTable::parse` (`:836`). Expect zero for both `vhd` functions — neither target calls them, this is the control that proves the measurement works. Change no tracked file; this step produces a number, not a diff. **This is the back-brief gate: stop and report before 9b.** |
| 9b | high | opus | worktree | **Add `fuzz_vhd_parent`.** New file `src/fuzz/fuzz_targets/fuzz_vhd_parent.rs` plus a `[[bin]]` stanza in `src/fuzz/Cargo.toml` (copy the shape of `fuzz_vhd_footer`, which is buffer-based and takes no CallTable). Require `data.len() >= 1024`; treat `data[..1024]` as a dynamic header and synthesise a `VhdImageBounds` from the tail so bounds vary with the corpus. Call `vhd::VhdParentInfo::parse(header, &bounds)`; on `Some`, walk `info.locators.entries`, call `info.locators.preferred_locator(None)` and again with a `VhdImageWindow` built from the input, and call `info.decode_name(&mut dst)` into a 768-byte buffer (the worst-case UTF-8 expansion of the 512-byte UTF-16BE field, per `:981`). Assert the five invariants in decision 3 of this plan; a parse returning `None` is a valid outcome, not a failure. Register it in **all four** of the places survey finding 8 lists: the `TARGETS=(...)` array in `.github/workflows/coverage-fuzz.yml:196-236` (without this the target is never fuzzed in CI at all), `FAST_TIER` in `tools/ci/fuzz-tier.sh:34`, and both `'vpc'` and `'vhd'` in `FORMAT_TO_TARGETS` (`scripts/extract-fuzz-corpus.py:36-37`) with no entry in `HEADER_ONLY_TARGETS` — the dynamic header sits at `data_offset`, typically 512, so a truncation policy that keeps only a header prefix would cut it off. Constraints: the fuzz crate is not `no_std` but the crates under test are; do not add dependencies. |
| 9c | medium | sonnet | none | **VHDX, conditional on 9a.** If 9a measured a non-zero hit count for `parse_parent_locator`, add no target: instead add a `HEADER_ONLY_TARGETS` comment recording the measured figure so the next reader does not re-ask, and say so in the commit. If it measured zero, add `fuzz_vhdx_parent` in the same shape as 9b driving `vhdx::parse_parent_locator(item)` directly on a fuzz-derived item slice, asserting that a returned `parent_linkage()` is within the item and that `is_vhdx_locator_type()` agrees with the locator type GUID; register it in `Cargo.toml`, the workflow's `TARGETS` array, `FAST_TIER`, and `FORMAT_TO_TARGETS['vhdx']` -- all four, per survey finding 8. |
| 9d | medium | sonnet | none | **Add a minimal differencing seed.** `create_minimal_seeds` in `scripts/extract-fuzz-corpus.py:335` builds a `disk_type=2` (fixed) VHD footer at `:416-423`; there is no differencing seed, so a run with no testdata starts cold on exactly the structure this phase fuzzes. Add one: a 512-byte footer with `disk_type=4`, a 1024-byte `cxsparse` dynamic header with a non-zero parent unique id at `+40` and a UTF-16BE parent name at `+64`, and one populated `W2ru` locator entry at `+576` pointing at in-bounds data. Write it to the `fuzz_vhd_parent` corpus dir (and `fuzz_vhdx_parent` if 9c created one). Follow the existing single-quote / 120-column Python style in that file. |
| 9e | high | opus | worktree | **Run it, and prove it can fail.** Run `fuzz_vhd_parent` for at least 600s against the seeded corpus inside the devcontainer, and report the coverage hit counts for the six functions in survey finding 2 — all must be non-zero, which is the criterion that separates this from a target that runs and reaches nothing. Then plant a policy defect and confirm the target catches it within 60s: remove the `defect.is_none()` condition from `preferred_locator`'s entry filter (`src/crates/vhd/src/lib.rs:885`), run, observe the assertion fire, restore from a copy — **not** with `git checkout`, which discards uncommitted work in the directory. Paste both the coverage figures and the planted-defect crash into the commit message. Triage anything the run finds under decision 6. Also update the docstring at `:1016-1017` per decision 7. |
| 9f | medium | sonnet | none | **Guard the registration lists.** Write `tools/ci/check-fuzz-targets.sh` asserting that three sets are equal: the `.rs` basenames in `src/fuzz/fuzz_targets/`, the `[[bin]]` `name =` values in `src/fuzz/Cargo.toml`, and the entries of the `TARGETS=(...)` array in `.github/workflows/coverage-fuzz.yml`. Check `FAST_TIER` in `tools/ci/fuzz-tier.sh` as a *subset* only -- an unlisted target legitimately defaults to the deep tier. Exit non-zero naming the offending target and which list it is missing from. Wire it into the `ci-tooling` job of `.github/workflows/functional-tests.yml` alongside the phase 8 guards ("Test the mutation harness verdicts", "Test the literal replace helper") -- copy their step shape. Falsify it before claiming it works: delete one entry from the workflow array, confirm the script exits non-zero and names that target, then restore from a copy. Follow the shellcheck-clean style of `tools/ci/test-replace-once.sh`. |
| 9g | medium | sonnet | none | **Documentation and the three stale figures.** In `docs/testing.md`: add the new target row(s) to the table at `:1104`, update the count sentence at `:1101` ("40 targets" → the new count), and rewrite the tiering sentence at `:1252-1256` with the measured figures — 40 targets, 15 fast at 300s, 25 deep at 900s, 675s under an even split — recomputed for whatever 9b and 9c actually added. Repair the seven mangled phrases at lines 265, 984, 994, 1013, 1154, 1165 and 1175 ("the PLAN-s worknapshot" → "the snapshot work", and the two equivalents), naming the work rather than the plan file so the repair does not reintroduce a plan reference. In `.github/workflows/coverage-fuzz.yml:128`, replace `N_TARGETS=32` with a count derived from `src/fuzz/Cargo.toml`; keep it under five lines or move it to `tools/ci/` per the repo convention. Add a `CHANGELOG.md` Unreleased entry. |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| The target runs, panics never, and reaches nothing — an unearned green of exactly the kind phase 8 existed to refuse. | 9e's done-criterion is a coverage hit count per function, not "the run was clean", plus a planted defect that must be caught. Both are pasted into the commit message. The management session re-runs the planted-defect check rather than trusting the report. |
| The fuzzer finds a real parser bug and the phase grows without limit. | Decision 6 bounds it: only phases 3–7 code is fixed here, except a reachable panic or OOB read, which is fixed whatever wrote it and gets its own commit. Anything else is filed. |
| 9a cannot build the devcontainer, because `docker-image-prune.timer` removes `instar-build` daily and `make instar` only rebuilds `instar-release`. | The 9a brief says so. Run `make instar-devcontainer` in the foreground, not backgrounded — this host has killed two background builds for low memory. |
| The new fast-tier target displaces deep-target time in the nightly run. | Measured, not estimated: 900s → 888s per deep target, −1.3%. Decision 4 records both the measurement and the command that produced it. |
| The `VhdImageBounds` synthesised in 9b is unrealistic, so `locator_defect` refuses everything and the interesting rules never execute. | It is exactly what 9e's per-function hit counts detect: `locator_defect` at `:707` is one of the six functions that must come back non-zero. |
| Mangled-text repairs in `docs/testing.md` reintroduce the plan references `f415ba9` was removing. | 9g's brief says to name the work, not the plan file, and the done criteria grep for both the mangled phrases and for `PLAN-*.md` in the repaired lines. |

## Definition of done

* `src/fuzz/fuzz_targets/fuzz_vhd_parent.rs` exists, is registered in
  `src/fuzz/Cargo.toml`, and `cargo fuzz build` succeeds in the
  devcontainer.
* A coverage report shows a **non-zero** hit count for each of
  `VhdParentInfo::parse`, `VhdParentLocatorTable::parse`,
  `VhdParentLocator::parse`, `locator_defect`, `preferred_locator` and
  `decode_name`. The figures are in the commit message.
* Removing the `defect.is_none()` condition from `preferred_locator`
  makes `fuzz_vhd_parent` fail within 60 seconds. Run, not reasoned
  about; the output is in the commit message.
* After `python3 scripts/extract-fuzz-corpus.py --testdata
  ../instar-testdata`, the locator structure of all six
  `custom/audit/vhd-diff-locator-*.vhd` fixtures is present in
  `src/fuzz/corpus/fuzz_vhd_parent/` — as reshaped seeds, not whole
  images, which the 4 MiB `MAX_SEED_SIZE` refuses. Each seed round
  trips: parsing it with `VhdParentInfo::parse` yields the same
  platform codes and paths as parsing the fixture it came from.
* `tools/ci/fuzz-tier.sh is-fast fuzz_vhd_parent` exits 0.
* The VHDX question is answered with a number: either a measured
  non-zero hit count for `parse_parent_locator` recorded in a comment,
  or a new target.
* `grep -c '^\[\[bin\]\]' src/fuzz/Cargo.toml`, the number stated in
  `docs/testing.md`, and the row count of its target table all agree.
* `grep -nE 'PLAN-[a-z] work' docs/testing.md` returns nothing, and no
  line repaired in 9g names a `PLAN-*.md` file.
* `.github/workflows/coverage-fuzz.yml` contains no hardcoded target
  count; the value is derived.
* `tools/ci/check-fuzz-targets.sh` exits 0 on the tree, and exits
  non-zero naming the target when one entry is deleted from the
  workflow's `TARGETS` array. Demonstrated, not claimed.
* The new target appears in the workflow's `TARGETS` array, so a
  nightly run actually executes it.
* The tiering paragraph in `docs/testing.md` matches what
  `tools/ci/fuzz-tier.sh plan 27000 300 <targets>` actually prints.
* `src/crates/vhd/src/lib.rs` no longer says "phase 9" anywhere.
* `make lint`, `make test-rust`, `make check-binary-sizes` and
  `pre-commit run --all-files` are clean.

## Back brief

**Before 9b writes a line, report 9a's measurement**: the hit counts
for `parse_parent_locator`, `VhdParentInfo::parse` and
`VhdParentLocatorTable::parse`, and which of the two branches of
decision 5 that puts step 9c on. The whole shape of the VHDX half of
this phase rests on that number, and it is cheap to get and expensive
to be wrong about — this plan asserts the path is reachable but
explicitly does not claim it is reached.

**Before 9e plants its defect**, confirm the restore mechanism is a
file copy. Phase 8 hit this: a directory-wide `git checkout` to undo a
mutation discards uncommitted work in that directory, and in a
worktree carrying a half-finished fuzz target that is expensive.
