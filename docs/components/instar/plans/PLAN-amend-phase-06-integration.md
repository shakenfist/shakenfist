# PLAN-amend phase 06: Python integration tests

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read the existing
Python integration tests (`tests/test_resize.py`, `test_rebase.py`,
`test_commit.py`), the shared base class (`tests/base.py`), the
info-JSON comparison helpers (`tests/helpers/info_json.py`), and the
`make test-integration` / stestr wiring. Ground every claim in what
the code actually does — read it, don't guess. Where a question
touches `qemu-img amend`'s CLI surface or what `qemu-img info
--output=json` reports for qcow2 (`compat`, `lazy-refcounts`,
`refcount-bits`), confirm against qemu-img directly. Flag
uncertainty rather than guessing.

Phase plans live in `docs/plans/` named
`PLAN-amend-phase-NN-<descriptive>.md`. The master plan is
[PLAN-amend.md](/components/instar/plans/PLAN-amend/); phases 1–5 (ABI, planner, guest op,
host CLI, rust round-trip tests) are landed and `instar amend` runs
end-to-end. This is the sixth of nine.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what changed
and why.

## Situation

Phases 1–5 built and unit/round-trip-tested the planner, guest op,
and host CLI. Phase 4's management review already ran `instar
amend` against real `qemu-img` fixtures by hand and confirmed
info-equivalence with `qemu-img amend`. This phase makes that
verification **automated and permanent**: `tests/test_amend.py`,
the live differential integration suite.

The contract: for each case, create a qcow2 image with `qemu-img`,
copy it, amend copy A with `instar amend` and copy B with `qemu-img
amend`, and assert the two are equivalent via `qemu-img info
--output=json` (normalised), that `qemu-img check` passes on the
instar output, and that `qemu-img compare` shows the guest data is
unchanged (amend touches only header metadata). Plus refusal tests
(instar refuses the same structurally-impossible amends qemu does)
and a `KNOWN_AMEND_DIVERGENCES` registry for any accepted
difference.

This is the **live** differential against the locally-installed
`qemu-img` (qemu-img 10.0.8 here). The cross-*version* baselines
(recorded expected outputs across the qemu version matrix) are
phase 7.

The grounding the implementer builds on (verified on the `amend`
branch):

- **Base class** `tests/base.py` (`InstarTestBase`): `get_instar_binary()`
  resolves `INSTAR_BINARY_PATH` env or `src/target/release/instar`
  and `skipTest`s if absent. Helpers return `(stdout, stderr,
  returncode)` tuples: `run_instar_info(image, output_format=...)`,
  `run_qemu_img_info(image)`, `run_qemu_img_check(image)`,
  `run_qemu_img_compare(a, b, strict=...)`. `qemu-img` is invoked
  directly as the oracle (no wrapper); there is no kvm/qemu-img
  skip guard in the base — individual mutation test classes add
  their own (see `test_resize.py` around the resize-needs-guest
  skip).
- **Test-file shape** (`tests/test_resize.py` is the closest
  template): a top-of-file `*_CASES` dict keyed by format with
  tuples `(case_name, create_opts, op_opts, …)`; a
  `KNOWN_*_DIVERGENCES` dict keyed by `(format, case_name)` →
  reason string, consulted in the test factory; a base
  `Test*Smoke(InstarTestBase)` class with `run_instar_<op>(*args,
  timeout=…)` helpers (mutation ops use timeout 120 because they
  spin up the guest VMM); and factory-generated matrix tests
  installed via `setattr(Class, name, factory(...))`.
- **Image creation in tests**: `qemu-img create -f qcow2 -o …` via
  `subprocess.run` (see `test_rebase.py`'s base-image creation) and
  `instar create` via `run_instar_create`. **For amend, use
  `qemu-img create`** for all fixtures: amend must be tested on
  real v2 *and* v3 images, and `instar create` cannot emit v2
  (`build_header` is v3-only). `qemu-img create -o compat=0.10`
  makes a real v2; `-o compat=1.1[,lazy_refcounts=on]` a v3;
  `-o compat=…,backing_file=base.qcow2,backing_fmt=qcow2` adds a
  backing reference (qemu requires `backing_fmt` with
  `backing_file`).
- **Info comparison** `tests/helpers/info_json.py`:
  `assert_info_equivalent(test, actual_json, expected_json, target,
  tmp_path=…, expected_tmp_path=…, msg=…)` parses both, runs
  `normalise_info_json` (strips `actual-size`/`dirty-flag`/cache
  hints/per-target divergences, substitutes the filename), and
  asserts equality with a readable diff. This is the load-bearing
  comparison: run `qemu-img info --output=json` on the
  instar-amended and the qemu-amended images and pass both through
  it. qcow2 info exposes `compat`, `lazy-refcounts`, `refcount-bits`
  under `format-specific.data`.
- **Runner** `make test-integration` (`Makefile`): `cd tests &&
  ../.venv/bin/stestr run --exclude-regex test_info_malicious`.
  stestr auto-discovers `test_*.py`; a new `tests/test_amend.py`
  with an `InstarTestBase` subclass is picked up. `.stestr.conf`
  has `test_path=.`. Tests are factory-generated, no pytest markers.
- **Phase-4 finding to encode**: instar's amended images are
  `qemu-img info`-identical to `qemu-img amend`'s (verified by
  hand), even though on *upgrade* instar writes `header_length=104`
  and omits the optional feature-name-table extension (qemu writes
  112 + the table). That difference is **not** visible to
  `info`/`check`/`compare`, so the differential tests should pass
  without a divergence entry — but the registry exists to capture
  anything that does surface across the case matrix.

## Mission and problem statement

After this phase, `tests/test_amend.py` automatically verifies
`instar amend` against `qemu-img amend`. Structure:

1. **`AMEND_CASES`** (qcow2-only) — tuples `(case_name,
   create_opts, amend_opts)`:
   - `upgrade-plain`: create `compat=0.10` → amend `compat=1.1`.
   - `upgrade-with-backing`: create `compat=0.10,backing_file=…,
     backing_fmt=qcow2` → amend `compat=1.1` (backing preserved).
   - `upgrade-with-lazy`: create `compat=0.10` → amend
     `compat=1.1,lazy_refcounts=on`.
   - `downgrade-plain`: create `compat=1.1` → amend `compat=0.10`.
   - `downgrade-with-backing`: create `compat=1.1,backing_file=…,
     backing_fmt=qcow2` → amend `compat=0.10`.
   - `lazy-on`: create `compat=1.1` → amend `lazy_refcounts=on`.
   - `lazy-off`: create `compat=1.1,lazy_refcounts=on` → amend
     `lazy_refcounts=off`.
   - `noop`: create `compat=1.1` → amend `compat=1.1` (No change).

2. **Differential cross-validation** (the core matrix test): for
   each case — `qemu-img create` the start image; copy it (A and
   B); `instar amend -f qcow2 -o <amend_opts> A`; `qemu-img amend
   -f qcow2 -o <amend_opts> B`; then:
   - `assert_info_equivalent(qemu-img info A, qemu-img info B, …)`
     (unless `(format, case_name)` is in `KNOWN_AMEND_DIVERGENCES`,
     in which case assert the documented difference instead).
   - `qemu-img check A` returns 0.
   - `qemu-img compare` (original-vs-A) reports identical — amend
     does not change guest data.
   - For `upgrade-with-backing`/`downgrade-with-backing`, assert
     `instar info --output=json A` (or `qemu-img info`) still shows
     the backing filename.
   - For `noop`, assert `instar amend` printed "No change." and
     exited 0.

3. **Refusal tests** (separate, not differential — these produce no
   comparable image): instar must refuse, with a clear non-zero
   exit + stderr message, the structurally-impossible amends, and
   `qemu-img amend` should refuse them too (assert both fail;
   document any divergence):
   - downgrade (`compat=0.10`) of a v3 image with **compression**
     (`qemu-img create -o compat=1.1,compression_type=zstd` or a
     compressed convert) → `ERROR_DOWNGRADE_BLOCKED_FEATURE`.
   - downgrade of a v3 image with **`refcount_bits != 16`**
     (`-o compat=1.1,refcount_bits=64`) → `ERROR_DOWNGRADE_REFCOUNT_WIDTH`.
   - downgrade of a v3 image with **`extended_l2=on`** →
     `ERROR_DOWNGRADE_BLOCKED_FEATURE`.
   - `lazy_refcounts=on` against a **v2** image →
     `ERROR_LAZY_REQUIRES_V3` (qemu also refuses: "lazy_refcounts
     only supported with compatibility level 1.1 and above").
   - an unsupported `-o` key (e.g. `cluster_size=…`) → instar
     rejects host-side before launching ("not supported").

4. **A `/dev/kvm` skip guard** on the amend test classes (amend
   launches a guest VM), mirroring resize's guest-needs-kvm skip,
   so the suite degrades gracefully where kvm is unavailable.

5. `make test-integration` discovers and runs the suite; it passes
   against the installed `qemu-img`. (No rust/binary changes —
   `make instar`, `core.bin`, `amend.bin` unaffected.)

Out of scope: cross-version baselines (phase 7); docs/CHANGELOG
(phase 9).

## Open questions

### 1. Fixture creation: `qemu-img create` (confirm before 6a)

Recommendation: **`qemu-img create` for every fixture.** It makes
real v2 (`compat=0.10`) and v3 (`compat=1.1`) images — `instar
create` cannot emit v2 — and qemu-created images are exactly what
amend faces in production, making the differential meaningful.
Confirm. (`instar create` is still fine for any *v3-only* helper if
convenient, but `qemu-img` is the baseline.)

### 2. Is `KNOWN_AMEND_DIVERGENCES` expected to be empty?

Phase 4 found instar's amended images `qemu-img info`-identical to
qemu's, and `assert_info_equivalent` already normalises
`actual-size`/`dirty-flag`/etc. So the registry may legitimately be
**empty**. The implementer should run the matrix and add an entry
*only* for a difference that actually surfaces (with a reason and a
specific assertion of the expected difference), not pre-populate
speculatively. Confirm this posture. (If `qemu-img compare`
original-vs-amended ever shows a data difference, that's a real bug
to fix in phases 2–3, not a divergence to register.)

### 3. Does `instar amend` need the same `-o` as `qemu-img amend`?

Both take `-f qcow2 -o compat=…,lazy_refcounts=…`. Confirm `instar
amend`'s `-o` accepts a single comma-joined list and/or repeated
`-o` (phase 4 used `clap::ArgAction::Append`), and that the test
passes options identically to both tools so the comparison is
apples-to-apples.

### 4. Refusal cross-check strictness

For refusals, assert **instar** exits non-zero with the expected
mapped message (the load-bearing assertion). Also run `qemu-img
amend` on the same case and assert it *also* fails, to confirm
instar isn't over-refusing something qemu accepts — but if qemu's
behaviour differs (e.g. qemu silently performs a refcount_bits
change that instar refuses as out-of-scope), record it in
`KNOWN_AMEND_DIVERGENCES` rather than failing the test. Confirm.

### 5. Running the suite in review without full testdata

`tests/test_amend.py` is self-contained (it creates its own
fixtures with `qemu-img`; no manifest/testdata images). But stestr
*discovery* imports all `test_*.py`; if a sibling module needs
`INSTAR_TESTDATA_PATH` at import time, discovery may require it
present. Confirm whether the amend tests can be run in isolation
(e.g. `stestr run test_amend`) without testdata, and note the exact
invocation for the management review's verification run. The full
suite is exercised in CI / `make test-container`.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | Create `tests/test_amend.py`. Read `tests/base.py`, `tests/test_resize.py`, `tests/test_rebase.py`, and `tests/helpers/info_json.py` first to copy the exact idioms. Define `AMEND_CASES` (the 8 cases above) and an empty `KNOWN_AMEND_DIVERGENCES = {}`. Add a `TestAmendSmoke(InstarTestBase)` base class with `run_instar_amend(*args, timeout=120)` and a `_skip_without_kvm(self)` helper (mirror resize's guest-needs-kvm skip — `skipTest` if `/dev/kvm` is not readable/writable). Implement a fixture helper `_qemu_create(self, path, compat, extra_opts=(), backing=None)` that shells `qemu-img create -f qcow2 -o compat=<compat>[,<extra>][,backing_file=…,backing_fmt=qcow2] <path> 1M` and asserts rc==0. Implement the **differential cross-validation factory** `_make_amend_diff_test(case)`: in a `tempfile.TemporaryDirectory`, create the start image per `create_opts`, copy it to A and B (`shutil.copy2`), run `run_instar_amend('-f','qcow2', *[ '-o',o for o in amend_opts ], A)` (assert rc==0 unless the case is a known refusal — these are differential success cases), run `qemu-img amend -f qcow2 -o <joined amend_opts> B` (assert rc==0), then `run_qemu_img_info(A)`/`(B)` with `output_format='json'` and `assert_info_equivalent(self, info_A, info_B, 'qcow2', tmp_path=str(A), expected_tmp_path=str(B), msg=case_name)` — unless `(‘qcow2’, case_name)` in `KNOWN_AMEND_DIVERGENCES`; assert `run_qemu_img_check(A)` rc==0; and `run_qemu_img_compare(orig, A)` reports identical. Install the matrix via `setattr`. For the `noop` case additionally assert the instar stdout contained "No change.". Validate by running the amend tests through the venv stestr (see Open question 5 for the invocation) and confirm they pass; do NOT run cargo. |
| 6b | medium | sonnet | none | Add the refusal + backing-assertion tests to `tests/test_amend.py` as a `TestAmendRefusals(TestAmendSmoke)` class. For each refusal case (downgrade-compressed, downgrade-refcount-bits-64, downgrade-extended-l2, lazy-on-against-v2, unsupported-o-key): create the appropriate v3/v2 fixture with `qemu-img create` (compression: create then `qemu-img convert -c` or `-o compression_type=zstd` if supported by the installed qemu — otherwise skip that one with a clear reason), run `run_instar_amend(...)`, assert rc != 0 and the stderr contains the expected mapped message substring (from `map_amend_error` — e.g. "v3-only", "refcount", "lazy_refcounts only", "not supported"); also run `qemu-img amend` on the same case and assert it too fails, OR record a `KNOWN_AMEND_DIVERGENCES`-style note if qemu's behaviour differs. Add explicit backing-preservation assertions for the upgrade-with-backing / downgrade-with-backing cases (parse `run_instar_info(A, output_format='json')` and assert the backing filename survived). Run the matrix against the installed qemu-img; if a differential case actually diverges from qemu, add a documented `KNOWN_AMEND_DIVERGENCES` entry with a specific expected-difference assertion (do NOT pre-populate speculatively — Open question 2). Validate via the venv stestr run. |
| 6c | low | sonnet | none | Update `docs/plans/PLAN-amend.md`: mark the phase-6 row status, and if any divergence was registered, add a one-line note to the relevant master-plan Open question / "Bugs fixed" or "Future work" section. Do NOT add this phase file to `order.yml`; do NOT touch `usage.md`/`CHANGELOG` (phase 9). |
| 6d | low | sonnet | none | From the worktree root: `make instar` (expect `core.bin`/`amend.bin` unchanged — no rust changes); run the amend integration tests via the venv stestr (the invocation confirmed in 6a) and confirm all pass; `pre-commit run --all-files` (the Python hooks — black/flake8/etc. if configured — plus the rust hooks must pass). Stage and present a single commit for steps 6a–6c with the CLAUDE.md message convention. Do not push. (A full `make test-integration` is exercised by the management review / CI; this step runs the amend subset.) |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. After each step the management session reads the
actual changed files, confirms no unrelated files changed, runs the
named gates, and then commits/retries/upgrades. The sandbox **denies
direct `cargo`** (irrelevant here — Python only) but `make instar`,
the venv stestr, `qemu-img`, and `/dev/kvm` are available. **The
management review re-runs the amend integration suite itself** and
spot-reads the test bodies to confirm the assertions are real (a
test that creates images and "passes" without actually comparing
instar-vs-qemu is worse than none).

### Model and effort notes

- 6a and 6b are Python test authoring against the well-established
  `test_resize.py`/`test_rebase.py` patterns; sonnet at medium
  effort suffices given the helper names and idioms in the briefs.
- 6c, 6d are mechanical.
- When in doubt, skew to the more capable model — but an integration
  test failure is self-announcing and low-risk to land and fix.

### Management session review checklist

After the steps:

- [ ] Read the test bodies — confirm the differential test actually
      runs BOTH `instar amend` and `qemu-img amend` and compares,
      and that refusal tests assert a non-zero exit + the expected
      message (not a vacuous pass).
- [ ] No unrelated files modified; only `tests/test_amend.py` and
      the master-plan status row (+ any registered divergence).
- [ ] The amend integration suite runs and passes against the
      installed `qemu-img` (management re-runs it).
- [ ] `make instar` builds; `core.bin`/`amend.bin` unchanged.
- [ ] `pre-commit run --all-files` clean (Python + rust hooks).
- [ ] `KNOWN_AMEND_DIVERGENCES` entries (if any) each have a reason
      and a specific assertion; none were added to silence a real
      bug (a `qemu-img compare` data difference is a bug, not a
      divergence).

## Administration and logistics

### Success criteria

Phase 6 is complete when:

* `tests/test_amend.py` differentially validates `instar amend`
  against `qemu-img amend` across the case matrix (upgrade,
  downgrade, lazy toggle, backing-preserving, no-op), asserting
  info-equivalence + `qemu-img check` clean + `qemu-img compare`
  data-identical, and refuses the structurally-impossible cases
  with mapped messages.
* The suite is discovered and run by `make test-integration` and
  passes against the installed `qemu-img`; it skips cleanly without
  `/dev/kvm`.
* `KNOWN_AMEND_DIVERGENCES` documents any real, accepted difference
  (expected: empty or minimal).
* `make instar` and the binaries are unaffected; `pre-commit
  run --all-files` clean.

### Future work created by this phase

- Phase 7: cross-version baselines (`AMEND_CASES` in
  `instar-testdata/scripts/generate-baselines.py`,
  `expected-outputs/amend-info-json/`, testdata push) — reuses this
  phase's case definitions.
- Any divergence registered here informs phase 7's baseline
  expectations.
- If a differential case surfaces a real planner/guest bug, fix it
  in phases 2–3 and note it in the master plan's "Bugs fixed"
  section.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not** added to
`docs/plans/order.yml`. The master plan links to it from its
Execution table (already present).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
