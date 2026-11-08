# Phase 11: coverage and differential fuzzing

Phase 11 of [PLAN-bat-population.md](/components/instar/plans/PLAN-bat-population/). Read its
phase 11 paragraph of "Sequencing rationale" and the "Constraints that
apply throughout" section, in particular the `no_std` panic-free rule
and the requirement that every qemu-img behaviour claim is cited or
measured. This phase consumes what phases 6, 7 and 8 built: the
streaming BAT filler (`vhdx::fill_fixed_bat`), `create -f vhdx -o
subformat=fixed` and `convert -O vhdx --subformat fixed`.

Planning effort: high. The master plan's paragraph reads as four picker
edits; the survey found that two of the four are blocked on
preconditions it does not mention, and the convert half needs a new
comparison strategy rather than a new picker entry. Deciding what to
fuzz and what the oracle is, is the whole of this phase's judgement.

Review effort: high. A differential fuzzer that reports a known bug on
every iteration is worse than no coverage, because the next person
triages it; and a fuzz target whose invariants are too weak passes
forever while proving nothing. The review that matters is of the
oracles and the exclusions, not of the harness code.

## What the survey found

The master plan's phase 11 paragraph was written before phases 6 to 10
executed. Four of its claims are stale or incomplete, and two of them
are load-bearing. File and line references are against `e0b0fe13`.

### `fuzz_create_emitters` says fixed output reaches no caller

`src/fuzz/fuzz_targets/fuzz_create_emitters.rs:194-201` pins the VHDX
arm to `VhdxSubformat::Dynamic` and `block_state_zero: true`, with this
comment:

```rust
// The dynamic image the create op writes. Fixed
// output reaches no caller yet, and its BAT filler
// is not among the emitters this target checks.
```

The first sentence was true when phase 6 wrote it and is false now:
phase 7 shipped `create -f vhdx -o subformat=fixed` and phase 8 shipped
`convert -O vhdx --subformat fixed`. The second sentence is still true
and is this phase's work. Step 11a replaces the comment along with the
code it describes.

The target asserts five numbered invariants over the returned
`MetadataPlan` (`assert_invariants`, `:~230-290`): bookkeeping
consistency, every write inside `minimum_file_size`, no overflow, a
write-count bound, and no two writes overlapping. None of them says
anything about BAT contents, because no emitter it drives produces a
BAT.

### `fill_fixed_bat` is already reachable from the fuzz crate

`vhdx::fill_fixed_bat` is `pub`
(`src/crates/vhdx/src/lib.rs:4374`) and `src/fuzz/Cargo.toml` already
depends on the `vhdx` crate, so a new target needs no plumbing. Phase 6
left six unit tests beside it (`:5598-5784`) covering qemu pins,
buffer-size independence, the forward map, a mid-chunk start, a partial
last block and the error paths. They pin specific layouts; what a fuzz
target adds is arbitrary layouts crossed with arbitrary index ranges.

### The differential fuzzer excludes the `vhd` create target entirely

`scripts/differential-fuzz.py:1574-1592`, `_create_option_picker`:

```
Excludes:
    vhd target entirely        (CHS-geometry rounding divergence)
```

and `target = rng.choice(['qcow2', 'vmdk', 'vhdx', 'raw'])`. The
divergence is live, not historical: `tests/test_create.py:2658-2665`
still carries five `KNOWN_WRITER_DIVERGENCES` entries reading "qemu
rounds VHD virtual_size to CHS geometry", and the comment below them
records that the preallocation cases inherit it (qemu-img reports
1079296 for a 1M VHD where instar reports 1048576).

So "teach the pickers ... the non-qcow2 preallocation modes" cannot
include vpc's by adding an option: vpc is not a create target in this
fuzzer, and adding it would diverge on virtual-size on every iteration
for a reason that has nothing to do with preallocation. Decision 2
settles what to do.

Also measured in the same function: preallocation is offered only
inside the `qcow2` arm (`:1616-1631`), the `vmdk` arm offers only
`monolithicSparse` and `streamOptimized` (`:1637`), and the `vhdx` arm
offers only `block_size` (`:1641-1647`) — no subformat, no
`block_state_zero`. The master plan is right that all three are
missing.

### The fuzzer hand-mirrors `tests/helpers/info_json.py`, and the mirror has drifted

`scripts/differential-fuzz.py:1504-1505`:

```
# Mirrors tests/helpers/info_json.py — keep in sync by hand. A divergence
# whitelist change in either copy should also land in the other.
```

The field sets are equivalent today: the mirror's
`_CREATE_UNIVERSAL_STRIP` merges the canonical
`UNIVERSAL_DIVERGENCE` and `CACHE_HINT_FIELDS`, which
`info_json.py:208-210` also unions, and the per-target sets match (the
mirror omits a `vpc` key, but it looks up with `.get(target, set())`
and the canonical `vpc` set is empty, so the behaviour is the same).

The filename handling has not kept up. The mirror's
`_create_substitute_filename` (`:1533-1542`) substitutes only a value
exactly equal to `tmp_path`. Phase 10 taught the canonical copy to
recognise the sibling file a create subformat writes beside its output
— `SIBLING_NAME_SUFFIXES`, `_decompose_name` and the
`$FILENAME<suffix>` rule at `tests/helpers/info_json.py:72-160` — and
the mirror has none of it.

That is harmless today, because no caller's picker selects
`monolithicFlat`. (This paragraph first said the mirror's only caller
was `op_create`. It has ten call sites across five ops -- create,
resize, amend, rebase and commit -- which step 11c measured and which
widened its own scope; see the execution notes.) It stops being harmless
the moment this phase adds that pick, and the failure is not subtle:
`op_create` writes the two sides to **different names**,
`create-instar.vmdk` and `create-qemu.vmdk`
(`scripts/differential-fuzz.py:1668-1669`). A monolithicFlat
descriptor names its extent, so the two `info` outputs carry
`create-instar-flat.vmdk` and `create-qemu-flat.vmdk` — different
strings, neither equal to its side's `tmp_path`, so neither is
substituted and every monolithicFlat iteration reports a divergence
that is purely the temp name. Phase 10's rule handles exactly this
shape: it derives the sibling from the primary's own name, so both
sides normalise to `$FILENAME-flat`.

### Convert has no VHDX target, and its oracle is blind to layout

`op_convert` picks `target_fmt = rng.choice(['raw', 'qcow2'])`
(`scripts/differential-fuzz.py:1017`). VHDX is not a convert target at
all, so there is no picker to teach.

The deeper finding is the oracle. For any non-raw target, `op_convert`
flattens **both** outputs to raw with `qemu-img convert -O raw` and
compares the bytes (`:1066-1090`, "neutral ground — neither tool is
favoured"). That is a content comparison, and it is deliberately blind
to the container's layout. Every property the fixed subformat exists to
get right — `LeaveBlocksAllocated`, a BAT entry for every payload
block, `PAYLOAD_BLOCK_ZERO` against `FULLY_PRESENT`, strictly
increasing 1 MiB-aligned offsets, nothing written past the payload
region — survives a raw flatten identically whether instar got it right
or wrong. Adding `vhdx` to that `rng.choice` list would buy no coverage
of this plan's subject.

This is why the master plan says "comparing through the structural
comparator for VHDX", and the survey's correction is about size rather
than direction: it is a new comparison arm in `op_convert`, not a
picker entry. The comparator itself is ready —
`tests/helpers/vhdx_structure.py` exists and is used by five test
modules — and the import mechanism is already proven in this very
file, which does `sys.path.insert` on `tests/` and imports
`helpers.snapshot_readback` (`:41-47`).

### Corrected at source

The master plan's phase 11 paragraph and `docs/plans/index.md`'s phase
11 entry are rewritten by this plan's registration commit to say what
the survey found, so the next reader does not re-derive it.

## Decisions

1. **Delete the hand-mirror; import the canonical normaliser.** Step
   11c removes `_CREATE_UNIVERSAL_STRIP`, `_CREATE_TARGET_STRIP`,
   `_CREATE_NESTED_INFO_STRIP`, `_create_strip_keys`,
   `_create_substitute_filename` and `_normalise_create_info` from
   `scripts/differential-fuzz.py` and calls
   `helpers.info_json.normalise_info_json` instead, by the
   `sys.path`/`helpers.` pattern the file already uses for
   `snapshot_readback`. The alternative — port phase 10's sibling rule
   into the mirror — keeps a comment that asks a human to notice a
   change in another file, which is the mechanism that just failed.
   Importing is the root-cause fix, and it is what makes the
   monolithicFlat pick in 11d safe rather than merely tested.

2. **vpc stays out of the differential create picker; its
   preallocation no-op is covered by the host tests instead.** The CHS
   virtual-size divergence is a real, documented, unfixed difference,
   and nothing in this plan proposes to change instar's rounding. The
   options were to add a vpc arm restricted to CHS-exact sizes, to mask
   `virtual-size` for vpc in the normaliser, or to leave vpc out. This
   phase leaves it out. Masking the field would blind the comparison to
   the one thing most worth catching in a VHD writer, and restricting
   sizes would mean the fuzzer only ever generates the shapes where the
   bug cannot appear — coverage theatre. The non-qcow2 preallocation
   no-op is already asserted directly against the pinned qemu builds by
   `test_vpc_noop_preallocation` and `test_vpc_fixed_noop_preallocation`,
   and phase 10 added `1M-prealloc-falloc` / `1M-prealloc-full` create
   baselines for vhd across all 80 versions. **So this phase's
   preallocation fuzzing covers vmdk and vhdx only, and says so in the
   picker's docstring.** This is the decision most likely to be argued
   with: it means the master plan's "the non-qcow2 preallocation modes"
   lands two-thirds complete. The counter-argument is that a fuzzer
   which cannot reach vpc create at all is a gap worth closing on its
   own merits; it is, and it is recorded in Future work rather than
   bolted onto a phase about fixed VHDX.

3. **Convert gets a structural comparison arm, not a picker entry.**
   Step 11e adds a `vhdx` branch to `op_convert` that compares the two
   outputs through `helpers.vhdx_structure`, and keeps the existing
   raw-flatten path for `raw` and `qcow2`. Both comparisons run for the
   vhdx branch: content equality through the flatten (which proves the
   payload survived) and structural equality through the comparator
   (which proves the layout matches). Reporting which of the two
   diverged is what makes a finding triageable.

4. **No `preallocation=metadata` for non-qcow2 targets.** instar
   refuses it there and so does qemu-img, so a picker that generated it
   would be fuzzing two error paths against each other. This matches
   phase 10's decision 5 for the create cases. `falloc` and `full` are
   generated, and the expectation is that both are no-ops.

5. **The filler gets its own fuzz target.** `fill_fixed_bat` takes a
   layout, a block state, a first index and an output buffer; its input
   space is index ranges crossed with buffer sizes, which has nothing
   in common with `fuzz_create_emitters`' create-option bytes. Folding
   it in would mean one target whose input bytes mean two unrelated
   things. New target `fuzz_vhdx_fixed_bat`, named for what it fuzzes
   rather than for the function.

6. **The fixed convert picker avoids the #879 geometry, by number.**
   `convert -O vhdx --subformat fixed` fails on a partial last block
   holding data (#879, found by phase 9, recorded in the master plan's
   Future work). A picker that generated that geometry would report a
   known bug on every run and train the next reader to ignore the
   fuzzer. Step 11e confines the fixed convert sizes to whole multiples
   of the block size, with a comment naming #879 and saying to remove
   the restriction when it is fixed. Phase 10 made the same call for
   the same reason and did not add a create case at that geometry.

7. **A new fuzz target is registered in the coverage-fuzz workflow,
   because the repository gives no choice; the tier it lands in is the
   real decision, and it is the fast one.** This decision originally
   read "fuzz targets are not added to CI's fuzzing schedule by this
   phase", on the reasoning that the target list and time budget are a
   CI-cost call rather than a correctness one. Step 11b found that
   option does not exist: `tools/ci/check-fuzz-targets.sh` requires the
   filesystem's `fuzz_targets/*.rs`, the cargo bin targets and
   `coverage-fuzz.yml`'s `TARGETS` array to name the same set, and it
   runs in `functional-tests.yml`, so a target absent from the workflow
   is a red CI rather than a deferred cost. What *is* optional is
   `tools/ci/fuzz-tier.sh`'s `FAST_TIER`, which the guard checks as a
   subset: an unlisted target defaults to the **deep** tier. So the new
   target is listed in `FAST_TIER`, beside `fuzz_create_emitters`,
   which is the same shape of bounded pure-function target. That keeps
   the deep tier's budget where it was, which is what the original
   decision was actually trying to protect. Step 11f still documents
   how to run a target by hand.

## Step plan

All steps work in this worktree. 11a and 11b both touch
`src/fuzz/`, and every Rust step needs the Docker build, so no two
Rust steps run concurrently. 11c is a precondition for 11d (the
monolithicFlat pick is unsafe without it) and for 11e (which imports
by the same mechanism). 11d and 11e both edit
`scripts/differential-fuzz.py` and so run in sequence. 11f runs last.
Each step is its own commit.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 11a | high | opus | none | Extend `src/fuzz/fuzz_targets/fuzz_create_emitters.rs` to the fixed VHDX subformat and add the BAT invariants. **(1)** Replace the hardcoded `subformat: VhdxSubformat::Dynamic` / `block_state_zero: true` at `:194-201` with values drawn from the corpus bytes, in the style of the `vhd_sub` and `vmdk_sub` draws above (`:102-110`): fixed and dynamic, and both `block_state_zero` values. Delete the comment at `:197-199` — its first sentence ("Fixed output reaches no caller yet") is false since the fixed create and convert CLI paths landed, and its second is what you are fixing. Write a comment that says what the arm now covers. **(2)** Add invariants to `assert_invariants` (`:223`) for a fixed VHDX, guarded so dynamic plans are unaffected: every payload entry's offset lies inside the payload region; offsets are strictly increasing across payload entries; every offset is a whole number of MiB; every sector-bitmap slot in the emitted region is zero; and the entry count matches the block count the layout implies. (This sentence originally said "for a plan that carries a BAT", bounded the offsets by `minimum_file_size`, and asked for the alignment check on the decoded offset. All three were wrong and the step corrected them rather than working around them -- see the execution notes.) Number them continuing from invariant 5 and give each the one-sentence "what breaking this looks like" comment the existing five have. **(3)** Keep the target panic-free on bad input: a rejected plan must come back as `Err`, never a panic, so any arithmetic you add is checked. **Verification you must run and report:** build with `make lint` and the repo's fuzz build (find how `src/fuzz` is built — it is a Docker path, do not install a Rust toolchain on the host); run the target against its existing corpus for a bounded time under `timeout`; and prove the new invariants can fail by breaking each one in turn in a scratch copy of the vhdx crate (e.g. make one offset non-aligned, make offsets non-monotonic, write a non-zero sector-bitmap slot) and confirming the target reports the invariant you broke and not a different one. Restore from a copy, never with `git checkout <dir>`, and clear any stale build artefacts after restoring. Leave uncommitted. Report: the invariant list, the corpus run's iteration count, and one line per deliberate break naming which assertion fired. ≤400 words. |
| 11b | medium | opus | none | Add a new libfuzzer target `src/fuzz/fuzz_targets/fuzz_vhdx_fixed_bat.rs` for `vhdx::fill_fixed_bat` (`src/crates/vhdx/src/lib.rs:4374`), and register it in `src/fuzz/Cargo.toml` beside the existing targets. `src/fuzz` already depends on the `vhdx` crate, so no plumbing is needed. Read the six unit tests at `src/crates/vhdx/src/lib.rs:5598-5784` first: they pin specific layouts and this target's job is the arbitrary ones. Draw from the corpus bytes a virtual size, a block size, a block state, a first BAT index and an output buffer length, derive the layout with the shared VHDX layout function phase 5 introduced (find it; do not recompute the layout by hand in the target), and call the filler. Assert on success: every entry written parses; payload entries carry the state the call asked for; offsets are strictly increasing and 1 MiB aligned; sector-bitmap slots are zero; the call wrote exactly the number of entries the index range and buffer size imply; and calling across any partition of the same index range into two calls produces byte-identical output to one call (the property phase 6's `fill_fixed_bat_is_the_same_through_any_buffer_size` pins for one layout — generalise it). Assert on failure: the error is one of the declared variants and the output buffer is untouched, as `fill_fixed_bat_errors_leave_the_buffer_untouched` requires. Never panic on any input. **Verification:** build it, run it under `timeout` long enough to be meaningful, report the iteration count and any crash with its `iter_seed`; then mutate the filler in a scratch copy (drop the 1 MiB alignment, skip the last entry, leave a sector-bitmap slot non-zero) and show the target catches each, naming which assertion fired. Restore from a copy. Leave uncommitted. Report ≤350 words. |
| 11c | high | opus | none | In `scripts/differential-fuzz.py`, replace the hand-maintained mirror of `tests/helpers/info_json.py` with an import of it. This is decision 1 and it is a precondition for 11d. **The mirror** is `:1504-1572`: `_CREATE_UNIVERSAL_STRIP`, `_CREATE_TARGET_STRIP`, `_CREATE_NESTED_INFO_STRIP`, `_create_strip_keys`, `_create_substitute_filename`, `_normalise_create_info`, under a comment admitting it must be kept in sync by hand. Its callers are `op_create`, `op_resize`, `op_amend`, `op_rebase` and `op_commit` -- ten call expressions, instar side and qemu side in each. (This row originally claimed `op_create` was the only one; it is not, and the row's definition of done only holds if every call site moves.) **The mechanism already exists in this file:** `:41-47` inserts `tests/` on `sys.path` and imports `helpers.snapshot_readback`; follow it exactly rather than inventing a second pattern. Read `tests/helpers/info_json.py` and use its public normaliser; match its target-name vocabulary (it keys both `vhd` and `vpc`) and check what it expects for the primary path argument. Delete the mirror and its comment. **Before you change anything, prove the swap is behaviour-preserving for what runs today**, because this is a refactor of a comparison function and a silent widening of it would make the fuzzer stop reporting real divergences: capture the normaliser's output for both sides on a sample of each existing create target (`qcow2`, `vmdk`, `vhdx`, `raw`) with the real `qemu-img info --output=json`, through the old code and the new, and show them byte-identical. Where they are not identical, do not "fix" the canonical copy to match the mirror — report the difference and stop; the canonical copy is the reference and a difference is a finding about the mirror. Note in your report whether the canonical copy's extra handling (snapshot date fields, the sibling rule) can reach `op_create`'s inputs at all today. **Verification:** the comparison above; `pre-commit run --all-files`; and a bounded `--ops create` fuzz run under `timeout` at a fixed seed before and after the change, showing the same iteration count and the same divergence count (expect zero). Leave uncommitted. Report ≤350 words. |
| 11d | high | opus | none | Teach `_create_option_picker` in `scripts/differential-fuzz.py` (`:1574`) the shapes this plan added. Requires 11c committed. **(1) vhdx arm** (`:1641-1647`): draw `subformat=fixed` as well as dynamic, and when fixed, draw `block_state_zero=on` and `off`. Keep the existing `block_size` draw, including the `None` case. Note that a fixed VHDX is dense: hold fixed sizes to the small end of the existing list so an iteration does not write a gigabyte, and say so in a comment. **(2) vmdk arm** (`:1637`): add `monolithicFlat`. **(3) preallocation for vmdk and vhdx**: draw `falloc` and `full` as well as none. Do **not** draw `metadata` for these targets (decision 4: both tools refuse it). **(4)** Do **not** add a `vhd` target (decision 2, the CHS virtual-size divergence) — instead update the function's docstring `Excludes:` block to say that vpc create is excluded for that reason *and* that it is therefore the one target whose preallocation no-op the differential fuzzer does not cover, naming the two host tests that do (`test_vpc_noop_preallocation`, `test_vpc_fixed_noop_preallocation`). **(5)** The docstring's existing claims are part of the diff you own: check each against the code and correct any that the last five phases falsified. **Verification you must run and report:** a bounded `--ops create` run under `timeout` at a fixed seed with enough iterations to hit every new shape at least once — report the per-shape hit counts, and if any shape is unreachable say so, because an option that is never drawn is not coverage. Any divergence the run reports is the most important thing in your report: for each, say whether it is a real instar/qemu difference, a normaliser gap, or a picker mistake, and paste both sides. Do not add a divergence whitelist entry to make a run clean — a real divergence here is a finding for the management session. Leave uncommitted. Report ≤400 words. |
| 11e | high | opus | none | Add a VHDX arm to `op_convert` in `scripts/differential-fuzz.py` (`:1009`) that compares structure, not just content. Requires 11c committed. **Why this is not a picker edit:** `op_convert` picks only `['raw', 'qcow2']` (`:1017`), and for any non-raw target it flattens both outputs to raw with `qemu-img convert -O raw` and compares bytes (`:1066-1090`). A raw flatten erases every property the fixed subformat exists to get right, so adding `vhdx` to that list alone would buy no coverage. **(1)** Add `vhdx` to the target choice, with a `--subformat fixed` draw and a dynamic draw. **(2)** For the vhdx target, run both comparisons and report which diverged: the existing raw-flatten content comparison, and a structural comparison through `tests/helpers/vhdx_structure.py`, imported by the `sys.path`/`helpers.` pattern at `:41-47`. Read the comparator first and use what it already offers; if it needs a new accessor, add it there rather than parsing VHDX inline in the fuzzer, and keep its existing callers (five test modules) working. Emit distinct divergence `type` values for a content difference and a structural difference — a single `convert_vhdx_divergence` would make the triage read the payload to find out which. **(3)** Confine the fixed sizes to whole multiples of the block size (decision 6), with a comment naming **#879** — `convert -O vhdx --subformat fixed` fails on a partial last block holding data — and saying to lift the restriction when #879 closes. Verify by hand that the geometries you generate do not hit it: create one at an excluded geometry and confirm it still fails, so the comment is justified rather than inherited. **(4)** (This row originally said qemu-img cannot write VHDX, so no qemu side exists for the fixed convert output. That is wrong -- qemu-img writes VHDX, and the pins are themselves generated with it. The pin oracle is still the right choice, for the reason the execution notes record.) Establish what the two sides actually are before you write the comparison (instar fixed against instar dynamic? against the phase 1 pin? against a qemu-written dynamic VHDX?) and say in your report which oracle you chose and why; if the honest answer is that there is no qemu oracle for this target, say so plainly and make the arm compare instar against the pinned expectations instead — do not invent a comparison that looks symmetric but is not. **Verification:** a bounded `--ops convert` run under `timeout` at a fixed seed, with per-shape hit counts; one deliberately corrupted instar output showing the structural arm reports it and names the field. Leave uncommitted. Report ≤400 words. |
| 11f | medium | sonnet | none | Document the fuzzing this phase added. **(1) `docs/testing.md`:** extend its fuzzing section with the two new targets (`fuzz_create_emitters`' fixed arm and `fuzz_vhdx_fixed_bat`), how to run a target by hand, and the differential fuzzer's new create and convert shapes. State plainly which targets the differential fuzzer does **not** cover and why — vpc create (CHS virtual-size rounding) and the partial-last-block fixed convert geometry (#879) — because an undocumented exclusion reads as coverage. Reference the issue by number; do not cite plan, phase, step or decision numbers or any `PLAN-*.md` file in user-facing documentation. **(2) `CHANGELOG.md`:** one entry for the new fuzz coverage. **(3)** Check whether `AGENTS.md` states a convention this phase changed — "how to add a fuzz target" is a convention, so if it says anything about the fuzz crate it needs one line and a link to `docs/testing.md`, not a copy. Do not touch `docs/format-coverage.md`, `docs/quirks.md` or `docs/format-internals.md`: phase 12 owns the cross-cutting pages. **(4)** Run `pre-commit run --all-files`. Leave uncommitted; report every file changed and the exact `AGENTS.md` wording you added, if any. ≤250 words. |

## Risks and mitigations

* **The refactor in 11c silently widens the comparison.** Replacing a
  normaliser with a different one can make the fuzzer stop reporting
  real divergences, and a clean run looks like success. Mitigation:
  11c's brief requires byte-identical normaliser output across all four
  existing targets before and after, and a fixed-seed run with the same
  divergence count. The management session re-runs one of those
  comparisons itself.

* **A new picker shape reports a real instar bug and the round gets
  spent debugging it.** This is the fuzzer working, but it can consume
  a phase. Mitigation: 11d and 11e are forbidden from adding whitelist
  entries to quieten a divergence; they report it. The management
  session decides whether it is a finding for a follow-up issue or a
  fix inside this phase, and records the decision in the execution
  notes.

* **An option is drawn so rarely that the run never hits it**, and the
  phase reports coverage it does not have. Mitigation: 11d and 11e both
  report per-shape hit counts, and an unreachable shape is a reportable
  failure of the step rather than a footnote.

* **The fixed convert arm's oracle needed settling.** (This risk first
  said qemu-img cannot write VHDX, which is wrong; see the execution
  notes for what the oracle actually compares and why.) qemu-img cannot
  write VHDX, so a symmetric differential comparison may not exist for
  this target. Mitigation: 11e's brief makes naming the oracle a
  required output and explicitly permits the answer "there is no qemu
  side", with the pinned expectations used instead. The management
  session checks that whatever the step built is what its report claims.

* **A fixed VHDX iteration writes its full length to disk.** Fixed
  output is dense by definition and the fuzzer runs many iterations.
  Mitigation: decision for small sizes in the picker, stated in 11d and
  11e's briefs, and the plan-wide constraint that no test allocates a
  test image's data region.

* **A fuzz target hangs rather than crashing.** A mutated loop guard
  hangs by design. Mitigation: every fuzz run and every mutant runs
  under `timeout`, one per run, and a timed-out mutant counts as
  killed.

## Definition of done

Each item is a command and its expected result. Rust builds go through
the Docker path; no Rust toolchain is installed on the host.

* `src/fuzz` builds with every target registered, and
  `fuzz_vhdx_fixed_bat` appears in `src/fuzz/Cargo.toml` and in the
  built target list.
* `grep -n 'Fixed output reaches no caller yet'
  src/fuzz/fuzz_targets/fuzz_create_emitters.rs` matches nothing.
* `fuzz_create_emitters` draws both VHDX subformats and both
  `block_state_zero` values: a bounded run reports a non-zero hit count
  for each of the four combinations.
* Each invariant added in 11a and 11b fails when the property it
  asserts is deliberately broken in a scratch copy, and the failure
  names that invariant rather than a different one. The mutation script
  is committed under the repository's usual scratch-script location or
  its path is recorded in the execution notes, and the running mutant
  count is stated in the commit message.
* `grep -n '_normalise_create_info\|_CREATE_UNIVERSAL_STRIP\|_create_substitute_filename'
  scripts/differential-fuzz.py` matches nothing, and the file imports
  the normaliser from `helpers.info_json`.
* Normalised `qemu-img info --output=json` output for `qcow2`, `vmdk`,
  `vhdx` and `raw` is byte-identical through the pre-11c and post-11c
  code paths.
* A fixed-seed bounded `--ops create` run reports a non-zero hit count
  for each of: vhdx fixed, vhdx dynamic, `block_state_zero` on and off,
  vmdk `monolithicFlat`, and `preallocation` `falloc` and `full` on
  both vmdk and vhdx — and zero divergences, or every divergence
  classified in the execution notes as a real difference, a normaliser
  gap or a picker mistake, with the decision taken about each.
* `_create_option_picker`'s docstring names vpc create as excluded, the
  CHS reason, and the two host tests that cover vpc preallocation
  instead.
* A fixed-seed bounded `--ops convert` run exercises the vhdx target,
  emits distinct divergence types for content and structural
  differences, and reports a structural divergence when one byte of an
  instar fixed output is corrupted, naming the field.
* No generated fixed convert geometry has a partial last block, and
  the restriction's comment names #879.
* `docs/testing.md` says how to run a fuzz target by hand and names
  both uncovered cases (vpc create, and the #879 geometry) as
  deliberate exclusions with their reasons.
* `make instar`, `make lint`, `pre-commit run --all-files` and the full
  integration suite pass with 0 failures, read from the stestr totals
  rather than the exit code (`make test-ci` exits 0 with failures).
* No plan-file reference, phase number, step number or decision number
  appears in any code, comment or user-facing document this phase adds
  or edits.

## Execution notes

Implementation started 2026-11-07 (AEDT, +1100), on `e0b0fe13`.

**Back brief answers.** All four decisions put to the operator were
accepted as planned: vpc stays out of the differential create picker
(decision 2), the fixed convert arm compares against the pinned
expectations rather than inventing a symmetric oracle qemu-img cannot
provide (11e), the new fuzz targets are not wired into the
coverage-fuzz CI schedule (decision 7), and this session may push the
branch and open the pull request once the steps are committed and the
definition of done is checked.

**Step 11a corrected three premises of its own row**, all verified
against the tree by the management session before the step was
committed:

* **The BAT is not among the plan's writes.** `plan_vhdx` returns it
  separately as `VhdxPlan::fixed` for the caller to stream, because at
  the 64 TiB ceiling it is 513 MiB and the planner works in a bounded
  scratch buffer. So the harness keeps `plan.fixed` and fills the BAT
  itself through `vhdx::fill_fixed_bat`, in two bounded windows: one at
  entry 0, which always covers a sector-bitmap slot because the largest
  legal `chunk_ratio` is 4096, and one straddling the last entry the
  image needs, which is the only window that sees the entry-to-padding
  boundary.
* **`minimum_file_size` is the wrong bound.** It is the end of the
  metadata region, and a fixed image's payload blocks start 5 MiB
  further on, so the bound is the payload region
  `[layout.fixed_data_start, fixed.file_length)`.
* **The alignment invariant was unfalsifiable as worded.**
  `BAT_ENTRY_OFFSET_MASK` is `0xFFFF_FFFF_FFF0_0000`, so masking an
  entry yields a whole number of MiB whatever was encoded, and an
  assertion on the decoded offset can never fail. It is asserted on the
  entry's bits instead, which is where a misaligned offset survives.
  Reproduced by the management session: the mutant that writes
  `(offset + 512) | PAYLOAD_BLOCK_FULLY_PRESENT` makes invariant 9 fire
  with "entry 0 (0x800206) encodes offset 0x800200, not a whole number
  of MiB", and `0x800200` masks to `0x800000`, which is aligned -- so
  the original wording would have missed exactly this mutant.

The invariants are numbered 6 to 10 continuing the existing five, and
ordered so that each mutant's own assertion fires first rather than
being pre-empted by the entry-count check.

**The VHDX subformat and `block_state_zero` ride in two spare bits of
the existing `flag_bits` header byte** rather than in a new header byte.
Only the qcow2 arm reads that byte, and it reads bits 0 to 2; a 25th
header byte would have moved where the backing path starts and changed
what every existing corpus entry means.

**Finding, outside this phase's scope: `src/fuzz` is excluded from the
cargo workspace** (`src/Cargo.toml:4`, `exclude = ["fuzz"]`), so
neither `make lint` nor CI's rustfmt and clippy has ever looked at any
fuzz target. The step matched the file's existing hand-wrapped style
rather than relying on a gate that does not exist. Recorded in the
master plan's Future work.

**There was no committed corpus for `fuzz_create_emitters`.** The step
crafted six seeds covering both subformats and both block states at two
block sizes; the corpus directory is gitignored, so they do not appear
in the diff, and the originals are kept with the step's scratch output.

**Step 11b overrode decision 7, and had to.** The decision as
written was not an available option, which the management session
confirmed against the tree: `tools/ci/check-fuzz-targets.sh` requires
the filesystem targets, the cargo bin targets and
`coverage-fuzz.yml`'s `TARGETS` array to name the same set, and it runs
as a step of `functional-tests.yml`, so leaving a new target out of the
workflow turns CI red rather than deferring a cost. `FAST_TIER` in
`tools/ci/fuzz-tier.sh` is the part that is genuinely optional, checked
as a subset, with an unlisted target defaulting to the deep tier. The
target is therefore registered in both, in `FAST_TIER` beside
`fuzz_create_emitters`, so the deep tier's budget is unchanged --
which is what decision 7 was trying to protect. The decision is
corrected at its source above, its out-of-scope bullet is gone, and the
Future work entry that promised the wiring as deferred work has been
removed from the master plan, because the work is done. **This is a
change to something the operator approved in the back brief**, and it
is reported as such rather than absorbed quietly.

**Assertion 7's qemu claim was checked, not taken.** The new target
asserts that a payload block's offset is the one qemu's forward map
gives it, citing `vhdx_block_translate` in `block/vhdx.c`. Verified in
`instar-testdata/qemu-sources/7.0.0/block/vhdx.c:1129`:
`sinfo->bat_idx += sinfo->bat_idx >> s->chunk_ratio_bits`, which is
exactly the `p + p / chunk_ratio` the assertion inverts. The assertion
derives the offset independently of the filler's own arithmetic, so a
map right about its shape and wrong about which block an index belongs
to is still caught.

**A defect in 11a's mutation harness, found by 11b.** The
`clear_artefacts` step cleared a cargo `.fingerprint` directory that
does not exist on this tree, so it had always been a no-op and only
the mtime bump was forcing rebuilds. 11b's first harness lacked even
that and produced one phantom baseline failure from a stale mutant
object -- the same shape as the stale-bytecode trap this session hit
earlier, in a different language. Both harnesses now confirm
`Compiling vhdx` appears in each mutant's log before trusting its
verdict. 11a's five verdicts stand: the management session had already
reproduced one of them independently and watched the right assertion
fire.

**Step 11c found a premise of the management session's own brief
wrong, and it widened the step.** The brief asserted that the mirror's
only caller was `op_create`; it had **ten call expressions across five
ops** -- create, resize, amend, rebase and commit. Confirmed
independently against the pre-change file. The claim was never
measured when the survey was written, which is the exact failure the
step guidance warns about: a wrong premise in a brief comes back
looking like the agent's finding. The step did the full swap rather
than leaving the mirror alive for four ops, which is also the only way
its own definition of done can hold, and its edit script asserted
before writing so the tree was never half-swapped.

**The consequence for the rest of the phase:** the normaliser 11d and
11e rely on is shared by five ops, so any future widening of
`tests/helpers/info_json.py` reaches resize, amend, rebase and commit
too, not just create. The paragraph in "What the survey found" and the
11c row are both corrected above.

**The swap was proved behaviour-preserving over all five ops, not just
create.** A shadow run wrapped the mirror so every real call also ran
the canonical normaliser on the same input and returned the mirror's
result, leaving the run's behaviour unchanged: 1358 normalisations
across qcow2, raw, vhdx and vmdk, byte-identical, zero divergences. A
second comparison drove all 68 distinct shapes the real picker
produces through both code paths with real `qemu-img info` output,
also byte-identical. The before and after `--ops create` runs at a
fixed seed both gave 60 iterations and zero divergences, and
`python3 -m unittest test_helpers` is 40 tests OK either side.

**The canonical copy has three things the mirror lacked, not two.**
The management session predicted the sibling rule and the snapshot
date fields; the third is that `normalise_info_json` sorts the qcow2
`bitmaps` array and each entry's `flags`, making that comparison
order-insensitive. It can only reduce spurious divergences, never mask
a real difference, since bitmap directory order is not part of the
format contract. All three were measured unreachable from these five
ops today: no `snapshots[]` and no `bitmaps` appeared in any of the
1358 calls, and every `filename` equalled its primary path, so the
sibling rule does not fire until a picker selects `monolithicFlat`.

**`pre-commit` runs no Python linter in this repository** -- its seven
hooks are Rust, CI-config and shell checks -- so the step ran flake8 in
a scratch venv as well. It reports four E127s, the same four present
before the change and none on changed lines.

**Step 11d found no divergence, and the shapes are genuinely drawn.**
The step reported 300 iterations at a fixed seed with 883 create draws
and zero divergences, every new shape hit at least 16 times. The
management session reproduced the verdict at an independent seed rather
than replaying the step's: 80 iterations, zero divergences. The sibling
rule was measured firing rather than assumed -- 24 of 24 monolithicFlat
images carried a side-specific extent name in the raw `qemu-img info`
output and all 24 normalised to `$FILENAME-flat`, in three places per
image (the top-level extent, `children.extents.0` and
`format-specific.extents`).

**A qemu-img bug, reproduced by the management session.** The step's
justification for holding fixed sizes to whole multiples of the block
size is not instar's #879 but a defect in the other tool, and it was
re-measured here rather than taken from the report: qemu-img 10.0.13
creates a fixed VHDX with `block_state_zero=off` at a partial-last-block
geometry and then cannot open the file it has just written --
`qemu-img create -f vhdx -o
subformat=fixed,block_state_zero=off,block_size=1M probe.vhdx 4608.5M`
succeeds, and `qemu-img info probe.vhdx` then fails with "VHDX BAT entry
4609 end offset 4841275391 points after end of file (4840751104). Image
has probably been truncated." Both sides would fail the info read-back
at such a geometry, so the comparison would never happen. Recorded in
the master plan's Future work; it is the other tool's bug and is kept
separate from #879, which is instar's.

**Two judgement calls the step flagged, both kept.** The vhdx arm draws
`subformat` from `[None, 'dynamic', 'fixed']`, where `None` preserves
the arm's existing unset-default coverage on the same reasoning that
keeps `block_size`'s `None` -- an unset option is a shape in its own
right, and the one qemu's own default picks. And the fixed size list is
the dynamic arm's minus its largest entry, so an iteration does not
declare a gigabyte of dense file; that is a cost decision, separate
from the partial-last-block one above, and the comment states both
reasons rather than conflating them.

**The step corrected the picker docstring's own vocabulary**, which the
management session had not asked for and has kept: the exclusion said
"vhd target entirely (CHS-geometry rounding divergence)" where the
fuzzer's own target name is `vpc` and the divergence is specifically on
`virtual-size`. It also checked the docstring's three pre-existing
claims against the live tools instead of assuming them, and all three
hold.

**Step 11e found a real instar defect, filed as #883.** `convert -O
vhdx` writing a dynamic image ends the file short of the metadata
region its own region table declares, whenever the conversion allocates
no payload block. Reproduced by the management session from the command
line and by reading the region table directly: a 64 MiB all-zero source
gives a 3,276,800-byte file whose metadata region is declared at offset
3,145,728 with length 1,048,576, ending at 4,194,304 -- 917,504 bytes
past the end. `instar create -f vhdx` is correct at 4,194,304, and a
convert whose source allocates a block is fine. Neither tool's
validation reads a declared region against the file length, so
`qemu-img info` succeeds and `instar check` reports no errors; the
structural comparison this step added is currently the only check that
sees it.

**The operator chose to file it and stop the fuzzer reporting it**,
rather than fix the writer inside a fuzzing phase or land a fuzzer that
reports a known defect every run -- the same treatment this plan gives
#879 and the qemu-img partial-block bug. The suppression is narrow by
construction and the narrowness was verified by the management session,
not just claimed: the predicate requires both a declared region
overrunning the file *and* an all-zero BAT region, and the same file
with a single BAT entry set is still reported. A different parse
failure is still reported, and a corrupted BAT still comes back as a
structural divergence.

**It is a visible skip, not a silent pass.** The run prints
"Comparisons skipped for an already-filed defect (not a pass):
convert_vhdx_truncated_region_issue_883 x35" and records the count in
the run's summary JSON. Reproduced at an independent seed: 60
iterations, zero divergences, 35 skips.

**The cost, which the operator should see rather than discover.** Fixed
structural coverage is intact -- every fixed draw is fully compared.
Dynamic is thin: of 184 dynamic draws in a 200-iteration run, 113 were
skipped for #883 and 57 more parsed cleanly but had no pin for their
geometry, leaving 14 actual structural comparisons. Fixing #883 returns
about 61% of it; the pin gap is separate and is recorded in the master
plan's Future work.

**Two more of the management session's premises were wrong, and both
are corrected above.** qemu-img *does* write VHDX -- the pins are
generated with it -- so the back-brief question about 11e's oracle was
put on a false premise. The answer is unchanged but the reason is
different: instar's fixed convert deliberately writes qemu's *create*
layout rather than its *convert* layout (`docs/quirks.md` records the
difference), so a live convert-against-convert comparison would report
that documented divergence on every iteration. And #879 is
source-format dependent: its repro fails from a qcow2 source and the
same geometry passes from a raw one, so "an excluded geometry still
fails" holds only for some of the eight source formats, though the
whole-multiple restriction is still right.

**One comparator change, flagged because that helper is shared.**
`parse_vhdx` refuses these files, so the suppression condition cannot
be established through the existing API; the step added one public
function, `truncated_inside_region`, and removed nothing. All five
consuming test modules still import names that exist, and
`test_helpers` is 40 OK, reproduced by the management session.

**Mutation harnesses and the running count.** Nine mutants across the
two Rust steps, each killed by the assertion it targets rather than by
a different one firing first. 11a's five -- a wrong entry count, an
offset outside the payload region, non-monotonic offsets, a sub-MiB
offset and a non-zero sector-bitmap slot -- are in
`scratchpad/11a/mutate.py`; 11b's four -- a sub-MiB offset, a skipped
last entry, a filled sector-bitmap slot, and a slot filled as payload
only when it falls at a call's first index -- are in
`scratchpad/11b/mutate.py`, which extends 11a's and reuses its tree
copy, both under this session's scratch root
`/srv/nobackups/claude-tmp/claude-1000/-srv-kasm-profiles-mikal-vscode-src-shakenfist-instar/6a4545b4-9585-4a35-9845-8a8f9c2c83ea/`.
The last of 11b's is the one worth keeping: it is invisible to a single
whole-range call and only the partition property catches it. The
management session reproduced one mutant from each step independently,
confirming `Compiling vhdx` in the log first so the verdict could not
come from a stale object.

**Round 1's fuzz run is unchanged from the branch point.** 60
iterations at seed 2024 give 0 divergences and 4 #883 skips both on
the round's tree and on a pristine checkout of the pre-round head,
which is how the round's changes to the comparison were attributed:
they alter neither count. The 200-iteration figures quoted in
`docs/testing.md` come from a wider run and are untouched.

**The integration suite ran against instar-testdata `f73d43670a`**,
which is the revision phase 10 validated and landed against. Its
`main` had moved two commits further by the time this phase ran -- the
fuzz automation moves it daily -- and those were deliberately not
pulled, because phase 11 changes no baseline and no test expectation
that depends on testdata, so the validated revision isolates this
phase's changes. CI clones testdata's default branch unpinned, so CI
will run against whatever `main` holds then.

**Review round 1 (2026-11-08 AEDT).** The automated reviewer raised 3
fix, 1 document, 7 consider and 1 none on the pull request; all three
fix items and the document item were taken, with six of the seven
consider items and a docstring for the seventh. Two of them changed
more than the review asked for:

* The field-classification guard in
  `tools/ci/test_differential_fuzz_issues.py` was blind to three of
  the four ways the fuzzer builds a divergence -- an f-string `type`,
  a `dict(ctx, type=...)` call, and a field assigned by subscript
  afterwards -- so it had never checked most of the file. It was also
  matching on a two-item suffix allowlist, and the file's 66 record
  types end in nine different words, so the allowlist was the deeper
  cause: it now matches any dict carrying a literal `type`, excluding
  only `inconclusive_` records, which is the same prefix the fuzzer
  itself routes them by when it decides what may file an issue.
  Widening it surfaced twenty-four unclassified fields that predate
  this phase, all now classified; the guard would have caught this
  phase's own omission.
* `truncated_inside_region`'s check that the BAT region lies inside
  the file was removed rather than tested. The mutation run showed it
  could not fail: every input that trips it already raises inside the
  bounds-checked read, which the parse-error branch catches.

**Mutation count after round 1: twenty-four.** Fifteen Python mutants
join the phase's nine Rust ones, each killed by the named test rather
than by another failing first, in
`scratchpad/review-round-1/mutate.py` under this session's scratch
root. They break the classification list and each of its two new shape
fields, each of the scan's four shapes, its type matching and its
exclusion of inconclusive records, the holder-naming convention, the
pin's geometry key, the compared field set, the option-based case
selector, and three properties of the #883 predicate.

Two mutants survived their first run and both were worth the run.
One was the redundant BAT-reachability check above, removed rather
than given a test that could not fail. The other showed that
`vhdx_subformat` in `SHAPE_FIELDS` is redundant with `pin_case` for a
structural divergence, since a pin case's name already carries the
subformat -- but not for a parse failure, which has no pin case
because its geometry was never established. The isolating test is
therefore over two parse failures, which is the only place that field
separates two findings on its own.

**Review round 2 (2026-11-08 AEDT).** 1 fix, 10 consider, 2 none. The
fix item was a documentation error worth having: the new
`docs/testing.md` section credited `fuzz_create_emitters` with holding
each BAT offset to qemu's forward map, which is `fuzz_vhdx_fixed_bat`'s
invariant 7; the emitter target asserts only the state and region
membership. Documenting a stronger oracle than exists is the failure
the plan's own review risk names, so the section now attributes each
invariant to the target that asserts it.

Nine of the ten consider items were taken, all small and all about
code these rounds added. Two are worth naming. The #883 suppression
applied on any parse failure of the right shape, for either subformat
and any operation, while the defect as filed is dynamic `convert`; a
fixed image carries a present entry for every block, so an all-zero
BAT region in one is a finding of its own, and the skip would have
hidden it. And round 1's claim to have swept the ways a field reaches
a divergence was one short: a field can be set by subscript on a
*context* that is later spread in, which is exactly what `op_convert`
does with `context['vhdx_block_size']`. The scan now covers all seven
paths, and a table in the test names one field reachable by each path
and no other, established by auditing the source rather than by
assertion.

The tenth was declined and recorded in the master plan's Future work:
a seeded test that every create-picker option is still reachable. It
guards real ground -- the plan makes "an option that is never drawn is
not coverage" reportable -- but it tests phase 11d's draws rather than
anything these rounds changed, and it is its own task.

**Mutation count after round 2: thirty-three.** Nine more Python
mutants in `scratchpad/review-round-1/mutate-round2.py`, which extends
round 1's set and reruns all of it. Three of round 1's mutants needed
retargeting because round 2 rewrote the code they patched, and one
survived the first round-2 run for a reason worth recording: round 1's
f-string marker field had become reachable by a second path, so the
mutant that blinded the scan to interpolated types no longer failed
any test. That is what the one-field-per-path table fixes.

**The integration suite is flaky on this host at 16 workers, and round
1's single 0-fail run was a lucky sample.** Round 2 was verified
against three full runs of the same tree: 0 failed, then 32, then 4.
Every failure was `Content mismatch at offset 0!` from a `convert`
content comparison, every one was in `tests/test_convert.py`, and the
sets were unstable in both directions -- only 2 of the 32 reappeared
among the 4, and 2 of the 4 were new. All 36 distinct tests pass when
replayed away from a loaded run (32 at concurrency 2, 4 at concurrency
4), and load average was 6 to 10 during the failing runs. A
deterministic regression cannot produce different failure sets from
one tree, and nothing in these rounds touches `convert` or the two
`vhdx_structure` functions `test_convert` uses, so the attribution is
host contention between 16 concurrent qemu conversions.

Two things follow for anyone verifying work here. A single 0-fail run
is weak evidence on this host, and the right response to a cluster of
`Content mismatch at offset 0` failures is to replay the named tests
at low concurrency before believing them -- not to bisect.

**Review round 3 (2026-11-08 AEDT).** 1 fix, 6 consider, 3 none. The
fix item was an off-by-one in 11b, not in either review round:
`MAX_BUFFER_ENTRIES` was 4096 and its comment claimed that cleared the
largest legal `chunk_ratio` so a call from index 0 could reach the
sector-bitmap slot after a whole group. It cannot. The BAT repeats
every `chunk_ratio + 1` entries with the slot last, so at ratio 4096
the slot is index 4096 and a 4096-entry call stops exactly one short.
No single call in the target could ever span a group and its slot.
The constant is now 4097, and the reason is a `const _: () =
assert!(MAX_BUFFER_ENTRIES > MAX_CHUNK_RATIO)` rather than a comment,
so lowering it is a build failure -- verified by lowering it and
reading the error.

Five of the six consider items were taken. Two were the last wide
dimensions of guards these rounds built:

* `truncated_inside_region` accepted *any* region whose extent ran
  past the end of the file. The filed defect is the metadata region
  specifically, so a corrupt region table plus an unallocated BAT was
  being suppressed as #883 -- the exact class of finding the arm
  exists to catch. Now metadata-only. Making that falsifiable needed a
  test with a *third* region, since with only bat and metadata the
  BAT is the only other candidate and an overrunning BAT cannot be
  read at all; the test crafts the third region-table entry.
* The field-classification scan read an interpolated type by its
  literal tail and silently dropped any record whose type it could not
  read -- the one way that guard fails unsafely. An unreadable type is
  now a hard failure. The first version of that test was vacuous,
  which the mutation run caught: it asserted the real file has none,
  so it passed against a detector that found nothing. The detector is
  now a function over source text, tested against fixtures.

The third worth naming: a structural comparison that found no pinned
case, or whose source size could not be probed, returned None with no
record, which reads exactly like a clean comparison.
`STRUCTURAL_OUTCOMES` now counts every comparison by outcome and
reports it in the run summary, on the same reasoning as the #883 skip
counting -- a comparison nobody can see did not happen is not
coverage. The hand-measured coverage figures in `docs/testing.md` are
now reported numbers.

Declined and recorded in the master plan's Future work: biasing the
convert source size toward pinned geometries so the fixed arm is drawn
more than ~8% of the time. It is a real coverage gap, but it shifts
the fuzzer's draw sequence and wants measuring rather than a review
round.

**The outcome counter found something on its first run.** The 60
iterations at seed 2024 that rounds 1 and 2 used as their fuzz
evidence report `convert:skipped_issue_883 x4` and
`convert:no_pin_for_geometry x3` -- and no `compared` at all. That run
exercises the rest of the fuzzer, but the structural arm compared
nothing in it, so quoting its "0 divergences" as evidence that the
comparison still behaved was quoting a comparison that never ran. This
is exactly the blindness the review item described, found by the
counter added to answer it. A convert-only run of 150 iterations at
seed 7 gives `compared x34`, `no_pin_for_geometry x44`,
`skipped_issue_883 x56`, which is the arm working end to end and the
same shape as the hand measurement quoted in `docs/testing.md`.

**Mutation count after round 3: forty-four.** Thirty-four Python
mutants plus the compile-time assertion above, over the phase's nine
Rust ones. Round 3 had to retarget two of the earlier mutants again
and rewrite three of its own, because the first versions were killed
by the wrong test or could not fail at all -- which is twice now that
the mutation run has found a weakness in a test written in the same
round, and the argument for running it before claiming a guard works
rather than after.

## Back brief

Points needing the operator's answer before any step runs:

1. **Decision 2 — vpc stays out of the create picker**, so the
   non-qcow2 preallocation fuzzing covers vmdk and vhdx only. The
   alternative is a vpc arm with `virtual-size` masked, which blinds
   the comparison to the field most worth checking in a VHD writer.
   Accept, or add vpc with masking?
2. **Decision 7 — the new targets are not wired into the CI fuzzing
   schedule** by this phase, only committed and documented. That is a
   CI-cost call rather than a correctness one. Accept, or wire them in?
3. **Decision 6 — the fixed convert picker avoids the #879 geometry**
   rather than generating it and expecting the failure. Accept?
4. **11e's oracle.** (Asked and answered on a false premise: the
   question said qemu-img cannot write VHDX. It can. The answer given --
   compare against the pins -- remains correct for a different reason,
   recorded in the execution notes.) The question as put: qemu-img
   cannot write VHDX, so the fixed convert
   arm may have no symmetric differential comparison. The brief lets
   the step report that and fall back to the pinned expectations. Is
   that the right shape, or should fixed convert fuzzing wait until
   there is a real oracle?
5. **Whether this session may push and open the pull request** when the
   phase is implemented, or should stop at the last commit.

## Out of scope

* **Adding a vpc create target to the differential fuzzer.** Decision
  2. It needs the CHS virtual-size divergence resolved or deliberately
  masked, which is a VHD-writer question with no bearing on fixed
  VHDX. Recorded in the master plan's Future work.
* **Fixing #879** (`convert -O vhdx --subformat fixed` on a partial
  last block holding data). Already recorded in Future work by phase 9;
  decision 6 routes around it and names it.
* **The cross-cutting documentation pages.** Phase 12 owns
  `docs/format-coverage.md`, `docs/quirks.md`,
  `docs/format-internals.md` and `ARCHITECTURE.md`. 11f touches only
  `docs/testing.md`, `CHANGELOG.md` and, if a convention changed, one
  line of `AGENTS.md`.
* **The `PLAN-*.md` citations in landed code.** The repository's own
  rule forbids them and roughly 95 occurrences across 34 files predate
  this plan, including one in `tests/test_measure.py` that phase 10
  edited around. It is a repository-wide consistency gap, not this
  phase's, and it wants its own task.
