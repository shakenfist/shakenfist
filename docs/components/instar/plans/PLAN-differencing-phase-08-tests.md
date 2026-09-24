# PLAN: Differencing phase 8 — tests

Phase 8 of [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/).

## Goal

Close the gap between what the differencing output path is *claimed*
to do and what is *checked automatically*. Phases 3 to 7 each shipped
their own tests, so this phase is much smaller than the master plan
expected in one direction and points somewhere else entirely in the
other: the external parser that was chosen as this plan's oracle has
never been driven against a single image instar wrote, and the
evidence that any of the existing tests can fail lives in scratch
directories that were deleted.

## Planning effort

**Medium**, as [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/) specifies
for this phase. The survey did the expensive part; what remains turns
on one scope decision and a set of mechanical briefs.

## Review effort

**Medium.** Nothing here changes what instar writes or reads, so a
wrong answer costs test coverage rather than correctness. The one
judgement worth a careful read is decision 1, the boundary against
phase 15.

## Scope

**In scope:**

* An automated structural cross-check of instar-written differencing
  children against `vhdiinfo`, in the Python integration suite, with
  `libvhdi-utils` added to the environment that suite runs in.
* A committed falsification harness for the differencing tests: the
  mutations that prove each assertion can fail, as a script in the
  tree rather than as a claim in a pull request comment.
* Whatever input-space gaps step 8a's audit turns up, subject to the
  cap in decision 4.

**Out of scope:**

* Content cross-validation — does instar *compose* a chain the way
  libvhdi composes it. That is phase 15, and it cannot be done before
  phases 11 to 13 give instar a compose path at all.
* Coverage fuzzing of the locator parsers (phase 9) and of the
  compose path (phase 15).
* Any change to what `create` emits. If the audit finds an emitter
  defect, it is filed and referenced, not fixed here — see decision 5.
* Giving created images a real identity ([#566](https://github.com/shakenfist/instar/issues/566)),
  which the master plan defers for an ABI reason that has not changed.

## What the survey found

Surveyed against `99d7d24` (phase 7's merge commit).

**Three of the four things the master plan reserves for this phase
have already shipped.** The master plan's phase 8 text was written
before phases 3 to 7 executed, and each of those phases carried its
own tests rather than deferring them:

| Master plan says phase 8 must… | State on `develop` |
|---|---|
| build the negative identity test against a **third-party** parent, since an instar parent cannot fail it (`PLAN-differencing.md:427`, and again in Future work at `:687`) | Shipped by phase 7. `tests/test_create.py:393` round-trips both formats against third-party fixtures and asserts at `:461` and `:477` that the fixture's identity is non-zero first, so the comparison is not zeros against zeros |
| assert the VHD locator table **structurally only**, because nothing external parses it (`:251`) | Shipped. `src/crates/create/tests/round_trip.rs` carries locator misplacement, platform-code selection, Windows rendering, both length boundaries, non-BMP code-unit cost and NUL termination, with the symmetric set for VHDX. 27 tests in that file |
| carry phase 5's untested `create -f vpc -b` refusal (`:432`) | Already marked discharged in the master plan; `test_create_vpc_fixed_subformat_still_refuses_backing` covers it |
| keep parent-owned and child-owned sectors out of one bitmap byte, per libvhdi defect A (`:444`) | Fixtures landed in phase 2. `tests/manifest.json:228` and `:240` record the defect per fixture and `:264` records the intended composition. The assertions those guard are compositional, so they belong to phase 15 |

Current coverage: 32 tests in `tests/test_differencing.py`, 23
backing- or parent-related tests in `tests/test_create.py`, 27 in
`round_trip.rs`.

**The oracle has never been run against instar's own output.** Phase
1 selected libvhdi as this plan's oracle precisely because qemu-img
reads a differencing child as though the parent were absent. Phase
7's own definition of done asks for `vhdiinfo` on the VHDX child to
report a matching linkage. Nothing automates it:

| Environment | `vhdiinfo` present? | Ever sees a differencing child instar wrote? |
|---|---|---|
| Differential fuzzer, in `instar-build` (`differential-fuzz.yml:70-75`, `src/.devcontainer/Dockerfile:39`) | yes | **no** — `op_create` (`scripts/differential-fuzz.py:1653`) never passes `-b` for vpc or vhdx |
| Python integration suite, on `[self-hosted, debian-13, xl]` (`functional-tests.yml:692`) | **no** — the setup step at `:114-118` installs python3, venv, pip and jq only | creates them, checks them with hand-rolled struct reads |
| Rust tests | n/a — libvhdi appears in `round_trip.rs` only in comments explaining its defects | no |

So every libvhdi verification in phases 5, 6 and 7 was done by hand
in a management session and never committed. That is the same shape
as the defect round 4 of #581's review found in the emitter: the
producers were updated and the consumers were not, and no test
noticed because the test asserted something weaker than the claim.

**The falsification evidence is not reproducible.** Phases 5, 6 and 7
each built a mutation harness, reported a running count in the pull
request, and left the harness in a session scratch directory. There
is no mutation script anywhere in `tools/`, `tests/` or `scripts/`,
so "fifteen mutations, all firing" is today an unverifiable claim
about a deleted file. Two of those rounds also found that a mutation
had silently failed to apply and had been scored as a pass, which is
precisely the failure a committed, self-checking harness prevents.

**One master-plan claim corrected at source.** `:427` and `:687` both
instruct phase 8 to build the third-party identity test. Both now
carry a note that phase 7 discharged it, in the style the plan
already uses for phase 5's debt at `:432`. No other factual claim in
the phase 8 material was wrong.

## Coverage audit

Step 8a. Audited against the worktree at `1010ff4`; no test was added
or changed to produce it.

The path audited is `instar create -f {vpc,vhdx} -b PARENT [-F FMT]
CHILD [SIZE]`. **Sector size is deliberately absent**: `create`
refuses every value but 512 at the host
(`validate_create_args`, `src/vmm/src/main.rs:17957`), so it is not a
CLI dimension at all and its coverage is a Rust-only question that
phase 7's step 7b already owns.

Three states are used:

* **CLI** — a committed Python test drives the case through the real
  binary.
* **Rust** — only `crates/create` covers it, at the planner
  (`tests/round_trip.rs`) or function (`src/lib.rs`) level.
* **—** — nothing covers it.

Two facts about the run count are worth stating once. Every test in
`tests/test_create.py`'s `TestCreateSmoke` — which is where all of the
differencing create tests live — is inherited by
`TestCreateBaselineMatrix`, `TestCreateCrossValidation` and
`TestCreateRoundTripCheck`, so each of the names below runs **four**
times per integration run. And `src/operations/create` is excluded
from `cargo test --workspace`, so `probe_backing` itself has no unit
test anywhere; every parent-format row is covered either through the
CLI or through the `parent_format_matches` / `footer_fallback_applies`
predicates that the operation calls.

### Combinations judged absurd, and why

* **Parent path shape × parent format.** The parent-format refusal
  (`src/operations/create/src/main.rs:910`) runs *before* the path is
  ever rendered — rendering happens inside `plan_vhd` / `plan_vhdx`,
  which a mismatched parent never reaches. So the path dimension only
  needs crossing with the child format, not with the parent's.
* **Parent path shape × `-F` hint, and × SIZE.** Same argument: the
  hint is consumed by the format check and the size by the size check,
  both upstream of any path rendering.
* **A fixed-subformat *child*.** `-f vpc -o subformat=fixed -b` is
  refused at the host before any of these dimensions apply
  (`test_create_vpc_fixed_subformat_still_refuses_backing`, plus
  `round_trip.rs:vhd_fixed_with_backing_is_refused`), so it is one
  cell and not a factor in the cross-product.
* **`-F` values outside {raw, qcow2, vmdk, vpc, vhdx}.** Rejected by
  `create_backing_format_code` before anything else; one cell, not a
  dimension.

### Child format against parent format

| Parent | vpc child | vhdx child |
|---|---|---|
| VHD dynamic | **CLI** `test_create.py:test_create_vhd_and_vhdx_differencing_round_trip` (+ `round_trip.rs:vhd_differencing_round_trips_through_the_parser`) | **CLI** `test_create.py:test_create_vhd_and_vhdx_reject_mismatched_parent_format` (crossed pair, refused) |
| VHD fixed | **CLI** `test_create.py:test_create_vhd_differencing_from_a_fixed_parent` | **—** |
| VHD differencing | **—** | **—** |
| VHDX dynamic | **CLI** `test_create.py:test_create_vhd_and_vhdx_reject_mismatched_parent_format` (crossed pair, refused) | **CLI** `test_create.py:test_create_vhd_and_vhdx_differencing_round_trip` (+ `round_trip.rs:vhdx_differencing_round_trips_through_the_parser`) |
| VHDX differencing | **—** | **—** |

The `VHD fixed` / vhdx-child cell is not wholly unreasoned: the footer
fallback promotes the parent to `Vhd` whatever the target is, so it
lands in the same `detected != required` comparison as the VHD-dynamic
crossed pair, which `lib.rs:a_vhdx_child_takes_a_vhdx_parent_and_nothing_else`
covers as a predicate.

The four differencing-parent cells are the only ones in this table
with no test at any level. `create` refuses a differencing parent in
`probe_backing` (`src/operations/create/src/main.rs:313` for VHD,
`:339` for VHDX), and that refusal *is* tested — but only with a
**qcow2** child, by
`test_differencing.py:test_create_refuses_a_differencing_backing_file`
and `test_create.py:test_create_backing_checks_run_with_explicit_size`.

### Parent formats the probe does not accept as a vpc/vhdx parent

`probe_backing` has explicit arms for `Raw`, `Qcow2 | Vmdk4`, `Vhd` and
`Vhdx`, and a catch-all `_` arm (no size, no identity) covering `Vdi`,
`Qcow1`, `Qed`, `Iso`, `Luks`, `Parallels`, `Bochs`, `Cloop`, `Dmg`,
`Vmdk3` and a VMDK text descriptor. `Unknown` means the header could
not be read at all and is handled separately, at
`src/operations/create/src/main.rs:902`.

| Parent format | vpc child | vhdx child |
|---|---|---|
| raw | **Rust** `lib.rs:a_vpc_child_takes_a_vhd_parent_and_nothing_else` | **Rust** `lib.rs:a_vhdx_child_takes_a_vhdx_parent_and_nothing_else` |
| qcow2 | **CLI** `test_create.py:test_create_differencing_names_a_corrupt_parent_as_corrupt` (second half) | **Rust** `lib.rs:a_vhdx_child_takes_a_vhdx_parent_and_nothing_else` |
| vmdk | **Rust** `lib.rs:a_vpc_child_takes_a_vhd_parent_and_nothing_else` | **Rust** `lib.rs:a_vhdx_child_takes_a_vhdx_parent_and_nothing_else` |
| vdi | **—** | **—** |
| qcow1 | **—** | **—** |
| qed | **—** | **—** |
| iso | **—** | **—** |
| luks | **—** | **—** |
| header unreadable (`Unknown`) | **—** | **—** |

Two qualifications. The five `—` formats are not in the predicate
tests' `EVERY_FORMAT` array, but the refusal they would hit is a single
format-agnostic `detected != required` comparison
(`lib.rs:parent_format_matches`) that the three rows above them do
exercise, and any parent that slipped past it would be refused
immediately afterwards by the `ParentIdentity::None` check. The
`Unknown` row appears to be unreachable from the CLI: a short or empty
backing file reads back as zeros through `BackingStore::read_at`
rather than failing, so `detect_format_from_header` returns its `Raw`
catch-all instead. The *other* route to the same
`ERROR_BACKING_PARSE_FAILED` — right format, unparseable structures —
is covered by
`test_create.py:test_create_differencing_names_a_corrupt_parent_as_corrupt`.

### Parent path shape

The caps differ by format and by relative-versus-absolute, because a
relative path is emitted two UTF-16 code units longer than it was
typed (the `.\` prefix) while an absolute one keeps its bytes. VHD's
binding limit is the 512-byte locator sector, so 254 typed code units
relative and 255 absolute; VHDX's is
`MAX_PARENT_LOCATOR_VALUE_UTF16_BYTES` at 260 code units, so 258
relative and 260 absolute. `docs/quirks.md` and the two
`*_length_boundary` tests carry the arithmetic.

| Shape | vpc child | vhdx child |
|---|---|---|
| relative bare (`parent.vhd`) | **CLI** `test_create.py:test_create_vhd_and_vhdx_differencing_round_trip`, and the locator bytes in `test_create.py:test_create_differencing_absolute_parent_and_absent_hint` | **CLI** same two |
| relative with a subdirectory (`sub/parent.vhd`) | **CLI** `test_create.py:test_create_differencing_relative_parent_in_a_subdirectory` | **CLI** same |
| `./`-prefixed | **Rust** `round_trip.rs:vhd_differencing_relative_locator_paths_are_windows_rendered`, `lib.rs:a_leading_dot_slash_is_replaced_not_doubled` | **Rust** `round_trip.rs:vhdx_differencing_path_key_follows_the_path` |
| repeated separators (`sub//parent.vhd`) | **Rust** `lib.rs:repeated_separators_and_dot_components_collapse` only — no emitter-level case | **Rust** same, and same caveat |
| POSIX absolute | **CLI** `test_create.py:test_create_differencing_absolute_parent_and_absent_hint`, and **externally** by `test_differencing.py:test_libvhdi_reads_a_vhd_child_as_naming_its_parent` (added in review: the `test_create.py` coverage is a substring search plus an `instar info` round-trip, which is instar reading instar) | **CLI** same; no external leg, because `vhdiinfo` prints no parent filename for a VHDX |
| containing a backslash | **CLI** `test_create.py:test_create_differencing_refuses_a_backslash_in_a_relative_parent` | **CLI** same |
| empty | **Rust** `round_trip.rs:vhd_differencing_refuses_an_empty_parent_path`, `lib.rs:an_empty_path_is_refused_before_it_gains_a_prefix` | **Rust** `lib.rs:an_empty_path_is_refused_before_it_gains_a_prefix` only (no `plan_vhdx` case) |
| relative at the cap | **Rust** `round_trip.rs:vhd_differencing_parent_name_length_boundary` (254) | **Rust** `round_trip.rs:vhdx_differencing_parent_path_length_boundary` (258) |
| relative one over the cap | **Rust** same test (255 refused) | **Rust** same test (259 refused) |
| absolute at the cap | **Rust** `round_trip.rs:vhd_differencing_absolute_path_length_boundary` (255) | **Rust** `round_trip.rs:vhdx_differencing_absolute_path_length_boundary` (260) |
| absolute one over the cap | **Rust** same test (256 refused) | **Rust** same test (261 refused) |
| non-BMP characters (two code units each) | **Rust** `round_trip.rs:vhd_differencing_non_bmp_characters_cost_two_code_units` | **Rust** `vhdx` crate's `build_parent_locator_counts_a_non_bmp_character_as_two_code_units` — the builder, not `plan_vhdx` |
| over `MAX_BACKING_FILE_LEN` (1024 bytes) | **Rust** `round_trip.rs:vhd_differencing_distinguishes_the_two_length_limits` | **—** |

The empty case is host-refused before the emitter can see it: without
`-u` the `is_file()` check at `src/vmm/src/main.rs:16962` rejects it
(an empty relative `-b` resolves to the output's own directory), and
with `-u` the probe's unreadable-header arm catches it. That chain is
read from the code rather than measured, and is the one reachability
claim in this audit that has not been run.

The host's *own* 1024-byte check
(`src/vmm/src/main.rs:16982`) is untested from the CLI in either
direction; it is a strictly larger bound than either emitter's, so for
vpc and vhdx it can only ever be reached after the emitter has already
refused.

### `-F` hint

| Hint | vpc child | vhdx child |
|---|---|---|
| absent (which from the CLI means `-u`; `-b` without `-F` and without `-u` is refused at `src/vmm/src/main.rs:18082`) | **CLI** `test_create.py:test_create_differencing_absolute_parent_and_absent_hint` | **CLI** same |
| agreeing with detection | **CLI** `test_create.py:test_create_vhd_and_vhdx_differencing_round_trip` | **CLI** same |
| contradicting detection | **CLI** `test_create.py:test_create_vhd_and_vhdx_reject_mismatched_parent_format` (`-F qcow2` over a VHD parent) | **Rust** `lib.rs:a_hint_the_bytes_disprove_is_refused` |

`-F raw` deserves its own note: it is the one hint that suppresses the
VHD footer fallback, and
`test_create.py:test_create_honours_an_explicit_raw_backing_hint`
drives it through the CLI — but with a **qcow2** child, so the
composed "a fixed VHD parent under `-F raw` is refused for a vpc
child" behaviour exists only as the two predicates
`footer_fallback_applies` and `parent_format_matches` tested
separately.

### SIZE argument

| SIZE | vpc child | vhdx child |
|---|---|---|
| absent (inherit the parent's) | **CLI** `test_create.py:test_create_differencing_refuses_a_size_that_is_not_the_parents` | **CLI** same |
| correct (equals the parent's) | **CLI** same test | **CLI** same test |
| wrong | **CLI** same test | **CLI** same test |

### Totals

66 cells enumerated: **25 CLI**, **24 Rust-only**, **17 with no
coverage at all**.

### The cells with no coverage, and whether a wrong answer there is visible

| Cell | Would a wrong answer be visible? |
|---|---|
| VHD differencing parent → vpc child | **No.** Nothing asserts a vpc child is refused a differencing parent, so an emitter that accepted one would write a structurally valid differencing VHD naming a parent that is itself differencing — a chain instar cannot compose, and no existing test looks at it. The guard is shared with the tested qcow2-child path, so only a *target-specific* regression slips through, but that is exactly the shape of regression this path has had before. |
| VHDX differencing parent → vhdx child | **No**, identically, and with one extra edge: the VHDX refusal is a separate arm (`state.has_parent`) from the VHD one, so the two are not one guard sharing one test. |
| VHD differencing parent → vhdx child | Yes — refused by the format mismatch even if the differencing guard failed, and that mismatch is CLI-tested for a VHD parent. |
| VHDX differencing parent → vpc child | Yes, for the same reason with the formats swapped. |
| vdi / qcow1 / qed / iso / luks parent → vpc child (5 cells) | Yes. Accepting one would need `detected == Vhd`, which those formats cannot produce; and the `ParentIdentity::None` check immediately downstream refuses any parent that yields no identity, so no child could be written. |
| vdi / qcow1 / qed / iso / luks parent → vhdx child (5 cells) | Yes, same argument. |
| header unreadable (`Unknown`) → vpc child | Yes, and the cell looks unreachable from the CLI in any case: a short backing file reads back as zeros rather than failing, so detection returns `Raw`. |
| header unreadable (`Unknown`) → vhdx child | Yes, same. |
| over-`MAX_BACKING_FILE_LEN` path → vhdx child | Yes. The only thing at stake is *which* refusal message is printed (`BackingFileTooLong` versus `ParentNameTooLong`); no image is produced either way, and `plan_vhdx` already refuses at 258/260 code units, far below 1024. |

So of the seventeen, **two qualify under decision 4** — a differencing
VHD parent for a vpc child, and a differencing VHDX parent for a vhdx
child. Both are reachable from the CLI, both would let a wrong answer
through unnoticed, and both are one `subTest` on an existing fixture.
Step 8d decides whether to spend them.

### Assertions that are weaker than they read

Not gaps in the cross-product, but found while reading the tests and
relevant to what 8b and 8d should lean on:

* `test_create_vhd_and_vhdx_differencing_round_trip` proves the VHDX
  `parent_linkage` with `assertIn(linkage.encode('utf-16-le'),
  child_bytes)` — a **whole-file substring search**. It would pass if
  the GUID were written under the wrong key, in a stray second locator
  item, or anywhere else in the file. The VHD arm of the same test is
  structural by comparison (it walks `data_offset` to the dynamic
  header). This is precisely the assertion step 8b's `vhdiinfo`
  cross-check replaces with a parsed one.
* The same test's VHDX arm never asserts the File Parameters
  `HasParent` bit through the CLI, where the VHD arm does assert
  `disk_type == 4`. `round_trip.rs:vhdx_differencing_round_trips_through_the_parser`
  pins `HasParent`, but against a plan built from a constant GUID, not
  from a parent's bytes.
* The locator assertions in
  `test_create_differencing_relative_parent_in_a_subdirectory` and in
  the `-u` leg of `test_create_differencing_absolute_parent_and_absent_hint`
  are whole-file substring searches too. They have real teeth — the
  Windows-rendered bytes appear nowhere else — but they would pass
  with the locator in the wrong slot or carrying the wrong platform
  code. `round_trip.rs:vhd_differencing_platform_code_follows_the_path`
  covers the code itself, so the composite risk is low.
* `test_create_differencing_refuses_a_size_that_is_not_the_parents`
  asserts only `rc == 0` on its "the parent's own size is accepted"
  leg. An emitter that accepted the size and then wrote a *different*
  one into the child would pass. The "omitted" leg does check the
  resulting virtual size, so the property is covered by its neighbour
  rather than by the leg that names it.

## Decisions

1. **The oracle split is structural here, content in phase 15.**
   `PLAN-differencing.md:492-497` gives phase 15 "cross-validation
   against the phase 1 oracle for chains instar wrote and chains it
   did not". Read in place, that sentence is about composition: does
   instar assemble a chain byte-for-byte as libvhdi does. This phase
   takes the different and much cheaper question that the output half
   actually raises — does an independent parser agree the child names
   the parent the emitter intended, with the linkage it intended —
   and leaves the content question where the master plan put it.

   This is the decision most likely to be argued with. The competing
   reading is that phase 15 owns everything oracle-shaped and phase 8
   should not touch libvhdi at all. Against that: phases 11 to 13 are
   the largest remaining block of work in the plan, so waiting means
   six phases of writer output resting on hand-verification that
   nobody can re-run, and an emitter whose output no independent
   parser has ever read is not a tested emitter. The split also falls
   on a real seam — structural checks read the locator and linkage
   fields and never touch the sector-bitmap decoder, so they are
   unaffected by libvhdi defect A, which is the thing that makes the
   compositional assertions delicate.

2. **The check goes in the Python integration suite, not the
   differential fuzzer.** The fuzzer is the wrong shape for it: it
   compares instar against qemu-img, and qemu-img cannot create a
   differencing child in any format at any shipped version, so there
   is no differential pair to draw. The integration suite already
   creates these images through the real CLI, which is the artefact
   worth checking.

   **Corrected during execution: the cost is zero packages, not one.**
   This decision originally said the check cost one apt package on the
   integration runner, reasoning from the apt step at
   `.github/workflows/functional-tests.yml:114-118`. That step belongs
   to the `ci-tooling` job, which never runs the Python suite — it runs
   the test-partition check and the fuzz-tool self-tests.
   `integration-core` runs `make test-container-core`, which runs the
   suite **inside the `instar-build` devcontainer**, and that image
   already installs `libvhdi-utils` (`src/.devcontainer/Dockerfile:39`)
   at the same `20240509` build Debian 13 packages. So the oracle is
   already present where the suite actually runs, and adding the
   package to that apt step would have installed it on a machine that
   never opens an image.

3. **Skip, do not fail, when the oracle is absent.** The suite must
   still pass on a developer machine without `libvhdi-utils`, so the
   new tests use the existing skip machinery rather than a hard
   dependency. This is the repository's established pattern for a
   capability that varies by environment
   (`skip_unless_qemu_supports` in `tests/base.py`).

   The trap it creates is the one this repository has been bitten by
   before: a skipped test and a passing test look identical in a
   green run. So the skip must be *visible*. Implemented as an
   environment variable rather than output parsing:
   `INSTAR_REQUIRE_LIBVHDI` turns the skip into a failure,
   `integration-core` sets it, and the Makefile passes it into the
   container. stestr does not name skipped tests in its output, so
   grepping the teed log could not have worked. Deleting
   `libvhdi-utils` from the devcontainer is what turns the job red.

4. **The audit caps at what it can justify.** Step 8a audits the
   input space and 8d spends the findings, but a tests phase can
   expand without limit. The cap: take a gap only where the untested
   input is *reachable from the CLI* and its absence would let a
   wrong answer through. Anything else is recorded in the step's
   commit message and left.

5. **An emitter defect found here is filed, not fixed.** This phase
   changes no `src/`. If the audit or the oracle finds instar writes
   something libvhdi reads differently than intended, that is an
   issue with the evidence attached and a reference from the test
   that found it, exactly as #583 was handled during #581's review.
   The one exception is a test that is simply wrong, which is fixed
   in place.

6. **The mutation harness is committed and self-checking.** It goes
   in `tools/` as a script, carries the differencing mutations from
   phases 5, 6 and 7, and refuses to score a mutation that did not
   apply — a literal find-and-replace that exits non-zero unless the
   pattern occurs exactly once, never a regex. Both prior rounds that
   used regex substitution silently mis-applied mutations and scored
   them as passes.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | medium | opus | none | Audit only, no test changes. Enumerate the **input space** of the differencing output path and record which shapes are exercised today. The path is `instar create -f {vpc,vhdx} -b PARENT [-F FMT] CHILD [SIZE]`. Dimensions: parent format (VHD dynamic, VHD fixed, VHDX, and each non-parent format the probe rejects); parent path shape (relative bare, relative with subdirectory, `./` prefixed, POSIX absolute, containing a backslash, empty, at and over each length cap); `-F` hint (absent, agreeing, contradicting); size (absent, correct, wrong); and child format against parent format (matched and both crossed pairs). For each cell say which test covers it, naming file and test function, or that none does. Sources: `tests/test_create.py`, `tests/test_differencing.py`, `src/crates/create/tests/round_trip.rs`, and the unit tests in `src/crates/create/src/lib.rs`. Note that `create` refuses every sector size but 512 (`src/vmm/src/main.rs`), so sector size is **not** a CLI dimension and its coverage is Rust-only — phase 7 step 7b covers it. Write the result as a table in `docs/plans/PLAN-differencing-phase-08-tests.md` under a new *Coverage audit* heading. Do not add tests; 8d spends this. Commit subject: "Audit differencing create test coverage." |
| 8b | high | opus | none | Add the libvhdi oracle cross-check to `tests/test_differencing.py`. Create a differencing child through the real CLI against a third-party parent fixture (follow `_copy_diff_parent` at `tests/test_create.py:371`), then run `vhdiinfo` on the child and assert the parent it reports is the parent instar intended: for VHDX the parent locator's path key and the `parent_linkage` GUID against the parent's active-header DataWriteGuid; for VHD the parent filename, which is the **parent unicode name** field — libvhdi never parses the VHD locator table at all (`PLAN-differencing.md:245-249`), so do not assert on VHD locator entries through this oracle. Skip cleanly when `vhdiinfo` is absent, following `skip_unless_qemu_supports` in `tests/base.py` as the pattern. Then make the skip visible per decision 3: add `libvhdi-utils` to the apt step at `.github/workflows/functional-tests.yml:114-118`, and add a check that the oracle tests were not skipped in that job — a skipped oracle must fail CI, not pass quietly. Constraints: assert on structure only, never on composed content (decision 1); libvhdi defect C (over-read past an unterminated parent name, `tests/manifest.json:1456`) means the over-long fixture is **not** a valid input for this oracle, so exclude it and say why in a comment. Cite-or-measure applies to every claim about what `vhdiinfo` prints — run it, paste the output into the commit message, do not describe it from memory. Commit subject: "Cross-check differencing output against libvhdi." |
| 8c | medium | opus | none | Commit the mutation harness per decision 6. Write `tools/mutate-differencing.sh` plus a literal find-and-replace helper that exits non-zero unless the pattern occurs **exactly once** in the target file — no regex, no escaping. Each case: apply one mutation to `src/`, run one named test, require it to **fail**, restore from a copy taken before the edit (never `git checkout <dir>`, which discards uncommitted work in that directory). Report PASS only when the test failed as required, BROKEN when the mutation did not apply or the test command did not match a package — a mutation that cannot apply must never score as a pass. Seed it with the differencing mutations the phases 5 to 7 rounds used: the emitter's relative/absolute key split, the `.\` prefix, separator collapsing, the parent identity plumbing, the footer backward scan, and the parent-format mismatch refusal. Note the package name for the vmm crate is `instar`, not `vmm`. Document it in `docs/testing.md` beside the existing fuzz sections, and state the case count there rather than in a pull request comment so it is checkable later. Commit subject: "Commit the differencing mutation harness." |
| 8d | medium | sonnet | none | Spend 8a's audit, subject to decision 4's cap: add tests only for gaps that are reachable from the CLI and where the absence lets a wrong answer through. Every new test must be accompanied by a case in 8c's harness proving it can fail; a test added without one is not done. If the audit found no qualifying gap, that is a legitimate result — say so in the commit message and add nothing. Commit subject: "Cover the differencing input shapes the audit found." |
| 8e | low | sonnet | none | Documentation and closeout. `docs/testing.md` gains the oracle cross-check alongside the libyal section at `:1029-1045`, stating which environment has `vhdiinfo` and that a skipped oracle fails CI. No phase numbers in documentation per AGENTS.md — link [PLAN-differencing.md](/components/instar/plans/docs/plans/PLAN-differencing/). No `CHANGELOG.md` entry: nothing user-visible changed. **Corrected in review:** the Unreleased section already records comparable developer-facing work (`tools/mermaid-lint.sh` and its workflow, the `skillsaw` hook and CI check), so a committed mutation harness and a newly mandatory CI oracle belong there too. An entry was added. File any issue decision 5 produced and reference it from the test that found it. Commit subject: "Document the differencing oracle cross-check." |

## Risks and mitigations

* **The oracle disagrees with instar and instar is right.** libvhdi
  has three characterised defects in this area already. *Mitigation:*
  8b's brief excludes the fixture that trips defect C and confines
  assertions to fields defect A cannot reach; any new disagreement is
  filed under decision 5 with the measured bytes, not patched around.
  The management session re-runs `vhdiinfo` on one case itself before
  accepting the step, per the master plan's standing warning about
  asserted tool capabilities.
* **The new tests skip silently in CI and the phase ships nothing.**
  This repository has shipped a green workflow that ran no steps
  before. *Mitigation:* decision 3 and step 8b make a skipped oracle
  a CI failure in the job that installs the package, and the
  definition of done requires proving it by running the job with the
  package deliberately absent.
* **`libvhdi-utils` is unavailable or differently versioned on the
  runner.** *Mitigation:* measured, not assumed — Debian 13's
  candidate is `20240509-2+b1`, the same package the devcontainer
  installs. If the runner disagrees, the phase falls back to running
  the check inside `instar-build` and the plan is amended rather than
  the version difference absorbed silently.
* **The phase expands into a general testing project.** *Mitigation:*
  decision 4's cap, and 8d's explicit licence to add nothing.

## Definition of done

* `vhdiinfo` runs against a differencing VHD child and a differencing
  VHDX child that `instar create` produced, in CI, and the run is
  visible as executed rather than skipped.
* Deliberately removing `libvhdi-utils` from the integration job
  makes that job **fail**, and this was run rather than reasoned
  about. Paste the failing run's identifier in the commit message.
* The VHDX assertion compares `parent_linkage` against the parent's
  active-header DataWriteGuid read from the parent's own bytes, not
  against a constant.
* No assertion added by this phase reads composed image content.
* `tools/mutate-differencing.sh` exists, runs from a clean tree,
  restores the tree afterwards (`git status --short` empty), and
  every case reports PASS or BROKEN — never a silent skip.
* Introducing a deliberate typo into one mutation's search pattern
  makes that case report BROKEN, not PASS. Demonstrated, not claimed.
* `docs/testing.md` states the harness's case count, and that count
  matches what the script contains.
* The coverage audit table names a covering test, or states there is
  none, for every cell of the enumerated input space.
* No file under `src/` is modified by this phase, except a comment
  cross-referencing an issue filed under decision 5.
* `make test-rust`, `make lint`, `pre-commit run --all-files` clean,
  and `make test-integration` passes the create and differencing
  suites.

## Back brief

Before 8b writes any assertion, report back with the raw `vhdiinfo`
output for one instar-created VHD child and one instar-created VHDX
child, and the exact field names to be asserted on. The whole phase
rests on what that tool actually prints, this plan was written
without running it, and an assertion written against a remembered
output format is the failure mode the master plan's standing warning
names.

Before 8d adds anything, report 8a's audit table and which cells you
intend to fill. A tests phase that grows without a stated cap is the
thing decision 4 exists to prevent.
