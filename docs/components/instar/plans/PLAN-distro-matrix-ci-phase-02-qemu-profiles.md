# Phase 2: qemu-img version→profile coverage + live version-detection

Master plan: [PLAN-distro-matrix-ci.md](/components/instar/plans/PLAN-distro-matrix-ci/).
Planning effort: **high**. Isolation: none (main tree; new baselines
land in instar-testdata via its own PR).

## Objective

Make the full test suite pass when run against each matrix distro's
**live** qemu-img, or record every legitimate divergence explicitly.
The corrected framing (see below): the profile system already exists
and the info-baseline tests are version-independent, so the work is
**live-oracle correctness + version-string parsing + one harness
profile-selection bug**, not "invent a tolerant comparator" or
"capture a profile per distro".

## Corrected framing (grounding facts)

The original draft assumed a rich per-version baseline system needing
new baselines per distro. The code says otherwise:

- **instar's runtime output adaptation is tiny.** `src/vmm/src/version.rs`
  detects qemu-img's `major.minor` (`detect_qemu_version()` runs
  `qemu-img --version` and parses `qemu-img version X.Y.Z ...`), then
  derives exactly **two** boolean features: `include_child_node`
  (major ≥ 8) and `include_dirty_flag` (≥ 6.1). If qemu-img is absent
  it falls back to `OutputProfile::newest()` (10.0). `--qemu-version`
  overrides detection (`profile_for_version_str`).
- **The testdata has eight empirically-derived qemu output profiles**
  (`expected-outputs/qemu-img-human/version-map.json`: profile-6-0-0,
  6-1-0, 7-2-19, 8-0-0, 8-1-0, 10-0-0, 10-2-0, plus json variants),
  because qemu changed output *within* stable series. But the
  **stdout divergences between adjacent profiles are narrow** — a
  handful of specific images, verified by diffing the baselines:
  - 6-0-0 → 6-1-0: `qcow2-dirty` (the dirty-flag feature; matches
    `include_dirty_flag`).
  - 6-1-0 → 7-2-19: `vhd-d2v-zerofilled`, `vhdx-disk2vhd` (a
    disk_size / allocation reporting change late in the 7.2 series).
  - 8-0-0 → 8-1-0: `parallels-bat-past-eof` (the 8.1.x past-EOF
    open-refuses regression window already recorded in quirks.md).
  - 10-0-0 → 10-2-0: only new fixtures present, no output change.
  Everything else is byte-identical across profiles.
- **The info-baseline tests are qemu-version-independent.**
  `test_info_safe` (and the `--qemu-version` tests for create / resize
  / amend / commit / rebase / bitmap) drive instar with an explicit
  `--qemu-version` per profile and compare to the stored baseline —
  they never call the live qemu-img. They pass identically on every
  distro; the matrix adds no new risk there.

## The real matrix risk

Two things depend on the **live** distro qemu-img:

1. **Version-string parsing.** Both `detect_qemu_version()`
   (`version.rs`) and the harness's `_detect_qemu_version`
   (`tests/base.py:89`) must parse each distro's real
   `qemu-img --version` string, including distro suffixes
   (`qemu-img version 7.2.2 (Debian 1:7.2.2+dfsg-...)`,
   `... (qemu-kvm-...) (rhel 9)`). A parse failure makes instar fall
   back to `newest()` (10.0) — silently wrong output on a distro that
   ships 6.2 or 7.2.

2. **Live-oracle tests.** Many suites use the distro's qemu-img as the
   differential oracle via the `base.py` helpers (`qemu-img info`
   :396, `check` :475, `rebase` :584, `compare` :669, `dd` :1005,
   `convert` :1044) — `test_convert`, `test_compare`, `test_dd`,
   `test_check_*`, `test_map`, `test_measure`, `test_oslo_crossval`,
   etc. These run against whatever qemu-img the distro ships and pick
   the comparison profile from the **detected** version.

### The profile-selection bug the matrix will expose

`_get_profile_for_current_qemu` (`tests/base.py:220-243`) selects a
profile by matching the detected `major.minor` against the **first**
`version_to_profile` key with that `{major}.{minor}.` prefix (insertion
order). It therefore **cannot distinguish 7.2.0 (profile-6-1-0) from
7.2.22 (profile-7-2-19)** — it always picks profile-6-1-0 for any
7.2.x. Debian 12 (bookworm) ships exactly a late 7.2 patch
(7.2.NN, NN ≥ 19 → truly profile-7-2-19), whose live qemu-img output
for `vhd-d2v-zerofilled` / `vhdx-disk2vhd` differs from the
profile-6-1-0 baseline the harness will select. Any live test that
leans on that selection can mismatch on those images. The same class
of ambiguity exists for the 8.1.x window (parallels) if a distro ships
8.1.x. This is a latent harness bug that the single-qemu-version CI
never exercised; the matrix will.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | **Enumerate and bucket.** For every matrix distro, capture the exact `qemu-img --version` string its package ships (from the phase-1 verify containers or the distro archives): Debian 12/13, Ubuntu 22.04/24.04, Fedora latest, Rocky/RHEL 9/10. For each, record: raw version string, parsed `major.minor.patch`, the **true** profile bucket its patch version maps to in `version-map.json`, and the profile `_get_profile_for_current_qemu` **actually** selects (the first-prefix match). Produce a table and flag every row where true≠selected (expected: the Debian 12 7.2.NN row, any 8.1.x row). This table drives 2c/2d. |
| 2b | high | opus | none | **Verify version-string parsing on both sides.** Confirm `version.rs::detect_qemu_version` and `tests/base.py::_detect_qemu_version` both parse each distro's real `--version` string to the right `major.minor` (watch distro suffixes and the epoch form `1:7.2.2+dfsg`). Add a Rust unit test in `version.rs` and a Python test in `test_version_detection.py` with a fixture table of the **real** strings captured in 2a. Fix any parser that mis-reads a real string (root-cause in the parser, per the repo's fix-the-cause rule — do not special-case a distro downstream). |
| 2c | high | opus | none | **Classify and run the live-oracle suites per distro.** Split the qemu-img-consuming tests into (i) `--qemu-version` baseline tests (version-independent — confirm, do not touch) and (ii) live-oracle differential tests (`test_convert`, `test_compare`, `test_dd`, `test_check_*`, `test_map`, `test_measure`, `test_oslo_crossval`, live `test_info`). For (ii), run each against every matrix distro's qemu-img (locally via the phase-1 distro containers with qemu installed, or the phase-3 runner once it exists) and record pass/divergence. Attribute each divergence to: a real instar bug (fix it), a legitimate qemu-version output difference (record as a known divergence — extend the existing `assert_known_oslo_divergence`-style mechanism, not a blanket tolerance), or the 2d selection bug. |
| 2d | high | opus | none | **Fix the profile-selection ambiguity.** Make `_get_profile_for_current_qemu` select by the **full** detected version (major.minor.patch) against `version_to_profile`, not the first `{major}.{minor}.` prefix, so a 7.2.22 host picks profile-7-2-19 and an 8.1.x host picks profile-8-1-0. Keep the major-only and first-entry fallbacks for hosts newer than the baselines (the 10.99 case at base.py:235). Note: `_detect_qemu_version` currently keeps only `(major, minor)` — it must retain the patch level for this to work; thread the patch through. Re-run the affected suites to confirm the flagged distros (Debian 12) now select the right baseline. If instar's own output cannot match a distro's live qemu for a divergent image because `version.rs` only carries two booleans, decide with management whether to widen `version.rs` (add a version gate for the vhd/vhdx disk_size or parallels case) or record it as a documented instar-vs-that-qemu divergence. |
| 2e | medium | sonnet | none | **Baselines only if a genuine gap appears.** If 2a/2c surface a matrix qemu version with no captured profile at all (e.g. a Fedora/Rocky qemu newer than 10.2 with a real output change), generate that profile via the instar-testdata baseline generator (`generate-baselines.py` + the per-version static qemu-img builds; always `--no-commit`; land via a separate instar-testdata PR with the Maintainer-role token — see the testdata_baseline_generator and testdata_push_token memory notes). Do NOT capture baselines from a distro's live qemu-img on a CI runner (non-reproducible). |
| 2f | low | sonnet | none | **Docs.** `docs/testing.md`: how the profile system maps qemu versions to baselines, the two-boolean runtime model, the full-version selection rule, and the portable/version-specific split; note the per-distro version-string quirks found in 2b and any known divergences recorded in 2c. Cross-link `docs/format-coverage.md`'s parity axis if a new divergence is documented there. |

## Acceptance

- A per-distro table (2a): version string → parsed version → true
  profile → selected profile, with mismatches resolved by 2d.
- Both parsers verified against the real distro `--version` strings,
  with regression tests (2b).
- Every live-oracle suite runs against every matrix distro's qemu-img
  and either passes or has a recorded known divergence with a reason
  (2c); no blanket tolerance masking real drift.
- `_get_profile_for_current_qemu` selects by full version; the
  Debian 12 7.2.NN and any 8.1.x rows pick the correct baseline (2d).
- Any genuinely missing profile captured reproducibly in
  instar-testdata via a separate PR (2e).
- `make test` still green on the dev image; `pre-commit` clean.

## Execution results (2026-08-08)

Executed on the develop worktree; host qemu-img 10.0.11 (Debian 13).

- **2a — enumerated** via the new `tools/probe-qemu-versions.sh`. The
  captured strings and their true vs (old) selected profile:

  | Distro | `qemu-img --version` | Parsed | True profile | Old prefix-match |
  |--------|----------------------|--------|--------------|------------------|
  | Debian 12 | `7.2.22 (Debian 1:7.2+dfsg-7+deb12u18+b3)` | 7.2.22 | `profile-7-2-19` | `profile-6-1-0` ✗ |
  | Debian 13 | `10.0.11 (Debian 1:10.0.11+ds-0+deb13u1)` | 10.0.11 | `profile-10-0-0` | `profile-10-0-0` |
  | Ubuntu 22.04 | `6.2.0 (Debian 1:6.2+dfsg-2ubuntu6.31)` | 6.2.0 | `profile-6-1-0` | `profile-6-1-0` |
  | Ubuntu 24.04 | `8.2.2 (Debian 1:8.2.2+ds-0ubuntu1.18)` | 8.2.2 | `profile-8-0-0` | `profile-8-0-0` |
  | Fedora latest | `10.2.2 (qemu-10.2.2-1.fc44)` | 10.2.2 | `profile-10-2-0` | `profile-10-2-0` |
  | Rocky 9 | `10.1.0 (qemu-kvm-10.1.0-17.el9_8.5)` | 10.1.0 | `profile-10-0-0` | `profile-10-0-0` |
  | Rocky 10 | `10.1.0 (qemu-kvm-10.1.0-16.el10_2.2)` | 10.1.0 | `profile-10-0-0` | `profile-10-0-0` |

  **Only Debian 12 (7.2.22) mis-selected** — exactly the predicted
  7.2.19-boundary bug. No matrix distro ships 8.1.x, so the parallels
  8.1 ambiguity never arises. Rocky 10 is only published under the
  `rockylinux/rockylinux` org repo (Docker Official stops at 9); fixed
  in `probe-qemu-versions.sh`, `verify-glibc-floor.sh`, and phase 1's
  plan.

- **2b — parsing verified.** Both parsers already read every real string
  correctly (the Debian epoch `1:7.2` never wins over the leading
  token). Extracted pure functions (`parse_qemu_version` in `base.py`,
  `parse_qemu_version_output` in `version.rs`) and pinned them with
  fixture tests over the captured strings. The Python side now retains
  the **patch** level (`_qemu_version` is a 3-tuple), required for 2d.

- **2d — selection fixed and de-duplicated.** Replaced the first-prefix
  match with `_select_version_match` (full major.minor.patch, highest
  ≤ host). The same buggy selector was duplicated in five baseline-dir
  harnesses (create/resize/commit/amend/bitmap); all now delegate to the
  shared `base.py` helpers. A matrix regression test
  (`TestProfileSelectionMatrix`) asserts every row above against the
  **real** testdata map. 791 selector/baseline tests pass on host qemu
  10.x; the full Rust workspace unit tests pass.

- **2c — PARTIAL on 2026-08-08 (host-only); completed 2026-08-09.** The
  original entry below stands for what it measured, but its deferral of
  the in-container live-oracle runs left the step unfinished; see
  "2c completion" after the 2e entry for the full-matrix results, which
  overturn its "no version.rs widen" conclusion.

- **2c (2026-08-08, host-only) — classification: no functional divergence.**
  The only in-matrix ambiguity (Debian 12, 6-1-0 vs 7-2-19) differs
  between profiles solely in fields the harness normalises: `disk size`
  (a filesystem `st_blocks*512` fact — instar emits 512 KiB uniformly,
  verified by running `instar info` under forced `--qemu-version`) and
  vmdk `cid` (a random nonce stripped by `assert_info_equivalent`). So
  the mis-selection was **benign** (masked by normalisation), which is
  why single-version CI stayed green. The fix is correctness hygiene
  that future-proofs the boundary. **No instar-vs-qemu parity gap
  surfaced, so version.rs stays at two booleans.** Full in-container
  execution of the live-oracle suites against each distro's qemu-img is
  deferred to the phase-3 runner (this host cannot `docker run` the
  matrix directly).

- **2e — no missing profile.** Debian 13's 10.0.11 and Fedora's 10.2.2
  ship patch levels the map doesn't enumerate, but `_select_version_match`
  resolves them (→ profile-10-0-0 / profile-10-2-0) with no output
  change, so no new baselines were generated.

## 2c completion — full matrix live-oracle runs (2026-08-09)

The deferred half of 2c: the full suite run in-container against every
matrix distro's own qemu-img, via the phase-3 runner. (The 2026-08-08
note claimed this host could not `docker run` the matrix; it can, and
does.) Divergences are attributed per 2c's rule, and every failure was
re-run **uncontended** before being called real — running two containers
at once saturates KVM and manufactures timeout failures that mimic
divergences (five such false positives appeared and all cleared).

| Distro | qemu | Ran | Real failures |
|--------|------|-----|---------------|
| Debian 12 | 7.2.22 | 3253 | 21 — 19 map + 1 snapshot + 1 vpc |
| Ubuntu 22.04 | 6.2.0 | 3253 | 21 — same three classes |
| Ubuntu 24.04 | 8.2.2 | 3253 | 2 — 1 snapshot + 1 vpc, **0 map** |
| Debian 13 | 10.0.11 | 3253 | 0 |
| Fedora | 10.2.2 | 3253 | 0 |
| Rocky 9 | 10.1.0 | 3253 | 0 (846 skipped: 785 baseline + 61 no-oracle) |
| Rocky 10 | 10.1.0 | 3253 | 0 (846 skipped, same split) |

Every distro now runs the whole 3253-test suite. The Rocky rows are
the fixed state: the last raw run showed 1 failure on Rocky 9 and 5 on
Rocky 10, of which one (`test_dd`'s `test_input_parallels`) was a
missing capability skip — added — and four were load artifacts that
passed on an idle host.

Attribution:

- **Legitimate qemu-version output differences → phase 2b.** The map,
  snapshot and qcow1→vpc classes are instar emitting its newest-qemu
  output regardless of the emulated version. Ubuntu 24.04 is the useful
  new data point: **zero** map failures at 8.2.2 confirms the `compressed`
  boundary at 8.2 from the live side, independently of the baselines,
  while snapshot and vpc still fail there — so the snapshot boundary is
  **above 8.2**, which the testdata alone could not establish (2b-B's
  blocker). This is the widen-vs-document decision 2c reserved for
  management, and it is phase 2b's subject.
- **Distro capability, not version → fixed here.** RHEL-family qemu-kvm
  is built without the `qed`, `qcow`, `parallels`, `dmg`, `bochs` and
  `cloop` drivers (verified by direct probe on Rocky 9 *and* 10; Debian
  carries all of them). Tests using qemu-img as the oracle for those
  formats had no oracle and failed. The version-profile model cannot
  express this — two hosts can report the same qemu version and disagree
  about which formats open. Added `skip_unless_qemu_supports()` to
  `tests/base.py` and applied it to the seven classes that need such an
  oracle; the instar-only suites (check-refusal, adversarial) keep
  running there, so this costs no Rocky coverage of instar itself.
- **Harness defect, not a divergence → fixed here.** A failing
  comparison of two multi-megabyte image buffers exceeds subunit v2's
  ~4MB packet limit (`ValueError: Length too long`), killing the stestr
  worker; its remaining tests never run and stestr still exits 0 if
  nothing else failed. A Rocky run therefore reported "0 failures"
  having executed **454 of 3253** tests — which is almost certainly what
  the phase-3 note recording "rockylinux:9 full run: 0 failures"
  actually saw. Added `assert_bytes_identical()` (reports sizes and the
  first differing offset, never the buffers) and converted the 21 whole
  image comparisons; the runner now fails on the crash marker, on any
  `N/A` worker, and on a full-run test count far below ~3250, so a
  truncated run can never again be reported as a pass.

- **Host load manufactures failures that look like data corruption.**
  Nine failures across this inventory were load artifacts — two matrix
  containers sharing the KVM host, and later a stray `find /` of mine
  competing with a run. They are not benign-looking: the large
  `test_convert` re-encodes fail under load as
  `Error: "convert operation failed"` and
  `Content mismatch at offset 0!`, which reads as a correctness bug, and
  they abort early (26s) rather than timing out at the ~110s the test
  needs when it passes. Every one of them passed serially on an idle
  host. **Never attribute a matrix failure without an uncontended
  replay** (`--select`); phase 4 must also bound how many matrix entries
  share a runner, or the queue will see these as real.

- **2f — docs.** `docs/testing.md` said "Known divergences: None specific
  to the matrix", which the runs disproved. It now carries the measured
  divergence table with its affected distros, the capability-vs-version
  distinction and how to probe it, the truncation trap, and the
  contention-replay discipline. `AGENTS.md` gained the two rules an agent
  can otherwise violate silently (use `skip_unless_qemu_supports` and
  `assert_bytes_identical`; do not weaken the truncation guard).

## Risks / notes

- **Distro qemu output ≠ upstream-version output.** A distro can
  backport patches that change qemu-img output at a version number
  whose upstream build did not — so a live divergence is sometimes a
  "distro X patched qemu-img" fact to record, not an instar bug.
  Classify before fixing (memory: diffuzz spurious-divergence
  discipline; qemu_capability_claims_sourcing_rule — measure, don't
  assume).
- **instar's two-boolean model may be too coarse for a divergent
  image.** If a matrix distro's live qemu differs from instar on
  `vhd-d2v-zerofilled` / `vhdx-disk2vhd` / `parallels-bat-past-eof`
  and that image is in a live-oracle test, 2d must decide widen-vs-
  document. Prefer documenting a narrow known divergence over adding
  version-specific output code unless a real user-facing parity gap
  is at stake.
- **Debian bookworm's 7.2 patch level drifts** with security updates,
  so 2a's captured version is a snapshot; the full-version selection
  fix (2d) must be robust to any 7.2.NN, not pinned to today's NN.
- Baseline capture is instar-testdata's domain; keep it a separate,
  `--no-commit`-audited PR and re-point instar at the updated testdata.
