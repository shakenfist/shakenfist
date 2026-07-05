# PLAN-bitmap phase 07: Python integration tests

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read the actual
code and ground every claim in it — in particular the amend
integration suite `tests/test_amend.py` (the primary template: a
qcow2-only mutation subcommand cross-checked vs `qemu-img`), the
shared base `tests/base.py`, `tests/helpers/info_json.py`, the
`Makefile` `test-integration` target, and the Phase-5 host CLI
(`run_bitmap` and its error messages in `src/vmm/src/main.rs`). Do
not speculate when you could read. Consult the qcow2 spec / `qemu-img
bitmap` for semantics. Flag any uncertainty explicitly.

Phase plans live in `docs/plans/` as
`PLAN-bitmap-phase-NN-<descriptive>.md`. The master plan is
[PLAN-bitmap.md](/components/instar/plans/PLAN-bitmap/); Phases 1–6 are complete —
`instar bitmap` runs end-to-end (50/50 Phase-5 smoke test) and the
crate has a round-trip `tests/` suite. This is the seventh of ten
phases and the **real external-validation heavy hitter**: it
cross-checks `instar bitmap` against the live `qemu-img` oracle with
post-op `qemu-img check`/`info`, including the **bits-set merge**
case the smoke test deferred. One commit per logical change.

## Situation

This phase adds `tests/test_bitmap.py` — the Python integration
suite that validates `instar bitmap` against `qemu-img bitmap` on
real qcow2 images. It is a **tests-only phase** (no library/guest/
host changes), except possibly a small, reusable extension to
`tests/helpers/info_json.py`. The Phase-5 smoke test already proved
the stack works (50/50); this phase makes that a **permanent,
comprehensive, CI-runnable** suite and closes the bits-set merge
gap.

Grounding (verified against HEAD `e78126a` + a live harness probe):

- **`test_amend.py` template** (the closest analog — qcow2-only
  in-place mutation, guest-launched, cross-checked vs qemu-img):
  - Class layout: `TestAmendSmoke(InstarTestBase)` owns the runner
    + KVM guard + `_qemu_create` fixture; `TestAmendCrossValidation`
    installs differential tests via a `setattr` factory loop
    (`test_amend.py:358-360`); `TestAmendRefusals` hand-writes
    error paths; plus baseline/cluster-size classes.
  - `run_instar_amend(self, *args, timeout=120)` (`:91-104`) — a
    **per-subcommand runner defined in the test file** (not
    base.py), returning `(stdout, stderr, returncode)`; 120 s
    timeout because it launches a guest VMM. bitmap does too —
    mirror it.
  - `_require_kvm` (`:106-114`) — `skipTest` when `/dev/kvm` isn't
    R+W; every guest-launching test calls it first; pure host-side
    refusals skip the guard.
  - `_qemu_create(self, path, compat, extra_opts, backing)`
    (`:170-187`) — `qemu-img create -f qcow2 -o compat=…,…`.
  - The differential core (`_make_amend_diff_test`, `:209-334`):
    build `start`, `shutil.copy2` to `a.qcow2` (instar) /
    `b.qcow2` (qemu) / `orig.qcow2`; run instar on A + qemu-img on
    B; **info-equivalence** via `assert_info_equivalent(self, ja,
    jb, 'qcow2', …)` where `ja`/`jb` are live `qemu-img info
    --output=json` on each copy (`_qemu_info_json`, `:340`); post-op
    `run_qemu_img_check(A)` rc 0; data-untouched
    `run_qemu_img_compare(orig, A)` rc 0; gated by a
    `KNOWN_AMEND_DIVERGENCES` registry (`:66`).
  - Refusal pattern: `assertNotEqual(rc, 0)` + `assertIn(substr,
    stderr)`, cross-running qemu-img and recording a divergence
    where qemu is more permissive.
- **`tests/base.py`** (`InstarTestBase(testtools.TestCase)`):
  `get_instar_binary` (`:315`, `INSTAR_BINARY_PATH` env, `skipTest`
  if absent), `run_qemu_img_check(path, output_format=None)`
  (`:458`), `run_qemu_img_compare` (`:650`), `_detect_qemu_version`
  (`:89`). **No generic `run_instar(args)`, no `run_instar_bitmap`,
  no `assertBitmaps`** — the per-subcommand runner lives in the test
  file. **No `/dev/kvm` gate in base** — the `_require_kvm` idiom is
  per-file; copy it.
- **`tests/helpers/info_json.py`**: `assert_info_equivalent(test,
  actual_json, expected_json, target, tmp_path, expected_tmp_path,
  msg)` (`:126`) + `normalise_info_json` (`:85`) strip
  `actual-size`/`dirty-flag`/cache-hint fields and normalise
  `filename`. **It does NOT special-case the `bitmaps` array**
  (which lives at `format-specific.data.bitmaps`, entries `{name,
  granularity, flags}`), so a naive compare is **order-sensitive** —
  if instar and qemu emit bitmaps in different directory order it
  falsely fails. Sort by `name` before comparing (Open question 1).
- **CRITICAL: instar's `info` emits no bitmaps.** Phase-1 step 1e
  (info bitmap listing) was deferred, and `src/operations/info/`
  has no bitmap output. So the info cross-check must be
  **qemu-vs-qemu**: read *both* the instar-produced image A and the
  qemu-produced image B with `qemu-img info --output=json` and
  compare their `bitmaps` arrays — exactly how amend compares two
  qemu reads. Do **not** compare instar-info to qemu-info for
  bitmaps.
- **Runner is stestr** (not pytest). `Makefile` `test-integration`
  (`:583-585`): `cd tests && ../.venv/bin/stestr run
  --exclude-regex test_info_malicious`; depends on `instar` (Docker
  release build) + `test-venv`. Run just this file:
  `cd tests && ../.venv/bin/stestr run test_bitmap`. Guest-launching
  tests need host `/dev/kvm` (available here); the qemu-img oracle
  helpers need no KVM.
- **qemu-img is 10.0.8**; the info bitmaps shape (verified):
  `"bitmaps": [ { "flags": ["auto"], "name": "b0", "granularity":
  65536 } ]` — `flags` `["auto"]` enabled / `[]` disabled /
  `["in-use"]` inconsistent; no bit-count field (bit content is
  invisible to `info` — hence the read-back oracle below).
- **Bits-set seeding IS feasible** (Open question 2, verified live):
  `qemu-img bitmap --add img b` (enabled/auto) → `qemu-io -c "write
  0 128k" img` (dirties exactly those clusters) → `qemu-img bitmap
  --disable img b`. Read the dirty extents back neutrally via a
  `qemu-storage-daemon` NBD export with `bitmaps=[b]` + `qemu-img map
  --output=json --image-opts driver=nbd,…,x-dirty-bitmap=qemu:dirty-
  bitmap:b` (dirty clusters show `"data": false`). The bitmap must be
  **disabled** before a read-only export. instar `--merge` is
  **same-file only** (source is a bitmap *name* in the same image;
  `-b`/`-F` cross-file is refused), matching `qemu-img bitmap --merge
  SOURCE FILE BITMAP` — so the bits-set merge uses one image with two
  bitmaps.

## Mission and problem statement

Add `tests/test_bitmap.py` (mirroring `test_amend.py`) such that:

1. **`TestBitmapSmoke(InstarTestBase)`** — the base with helpers
   copied/adapted from amend: `run_instar_bitmap(*args,
   timeout=120)`, `_require_kvm`, `_qemu_create`, `_qemu_info_json`,
   plus new `_seed_bitmap(img, name, writes)` (add + `qemu-io`
   writes + disable) and `_bitmap_dirty_extents(img, name)` (the
   storage-daemon/QMP/`qemu-img map` read-back oracle — one helper
   that owns the `x-dirty-bitmap` polarity).

2. **`TestBitmapCrossValidation(TestBitmapSmoke)`** — a factory
   matrix (amend's `setattr` loop) over the metadata actions: `add`,
   `add -g <granularity>`, `remove`, `clear`, `enable`, `disable`,
   the ordered `add;disable` sequence, and the empty same-file
   `merge`. Each case: build with `_qemu_create`, copy to A/B, run
   `instar bitmap …` on A and the equivalent `qemu-img bitmap …` on
   B, then assert **`run_qemu_img_check(A)` rc 0** and
   **bitmaps-array equivalence** between `qemu-img info(A)` and
   `qemu-img info(B)` (sorted by name — Open question 1). Cover a
   small **cluster-size matrix** (512, 4 KiB, 64 KiB, 1 MiB — the
   amend cluster-size bug was found this way; Open question 4).

3. **`TestBitmapMergeBits(TestBitmapSmoke)`** — the bits-set
   same-file merge (Open question 2): seed `src` with known dirty
   ranges via `_seed_bitmap`, add an empty `dst`, copy to A/B, run
   `instar bitmap --merge src A dst` and `qemu-img bitmap --merge src
   B dst`, then assert (a) `qemu-img check(A)` rc 0, (b) the
   bitmaps-array metadata equivalent, and (c)
   `_bitmap_dirty_extents(A, 'dst') == _bitmap_dirty_extents(B,
   'dst')` — the actual merged bits match qemu. Keep disjoint,
   overlapping, and into-non-empty-dst variants.

4. **`TestBitmapRefusals(TestBitmapSmoke)`** — `assertNotEqual(rc,
   0)` + `assertIn(substr, stderr)` for every error path, matched to
   the Phase-5 host messages (`src/vmm/src/main.rs`): v2 image
   ("cannot store dirty bitmaps in a qcow2 v2 image"), non-qcow2
   ("not a qcow2 image"), duplicate `--add` ("bitmap already
   exists"), missing `--remove`/`--enable`/`--merge` ("bitmap not
   found" / merge-source), granularity out of range / non-power-of-2
   ("granularity must be a power of two between 512 and 2G"), `-g`
   without `--add` ("granularity only supported with --add"), no
   actions, `--merge` mixed with metadata actions, too many actions/
   sources, and the deferred-flag refusals (`--object`/`--image-opts`
   /`-b`/`-F` "not yet supported"). Where qemu-img is more permissive
   (mixed merge, cross-file `-b`, non-16-bit refcount), assert instar
   refuses and **register the divergence** rather than fail.

5. A **`KNOWN_BITMAP_DIVERGENCES = {}`** registry (mirroring
   `KNOWN_AMEND_DIVERGENCES`) documenting every intentional
   instar-vs-qemu difference (Open question 3).

6. The suite passes under `stestr` / `make test-integration` with
   `/dev/kvm` present; a genuine instar bug it surfaces is **fixed
   in the guest/host** (Phases 4–5), not asserted around.

**Out of scope:** cross-version baselines (Phase 8); coverage +
differential fuzzing (Phase 9 — where random bitmap states are
compared vs qemu-img at scale); docs (Phase 10). No production
changes except a possible small `info_json.py` normalisation helper.

## Open questions

### 1. Comparing the `bitmaps` array (qemu-vs-qemu, order-safe)

instar `info` emits no bitmaps, so compare `qemu-img info
--output=json` on **both** copies. The `format-specific.data.bitmaps`
arrays are order-sensitive; instar's on-disk directory order may
differ from qemu's. **Recommend a targeted
`assert_bitmaps_equivalent(self, json_a, json_b, msg)`** that
extracts `format-specific.data.bitmaps` from each, **sorts by
`name`**, and `assertEqual`s the lists — rather than full
`assert_info_equivalent` (which would also diff allocation-dependent
fields that legitimately differ between two independently-produced
images). Corruption is caught separately by `qemu-img check`. Put the
helper in `test_bitmap.py` (or, if reusable, extend `info_json.py`
to sort the bitmaps array in `normalise_info_json`). Confirm which in
7a.

### 2. Bits-set merge oracle (the read-back)

Verified feasible. `_seed_bitmap(img, name, writes)`: `qemu-img
bitmap --add img name`; for each `(offset, len)` in `writes`,
`qemu-io -c "write <off> <len>" img`; `qemu-img bitmap --disable img
name`. `_bitmap_dirty_extents(img, name)`: launch `qemu-storage-
daemon` with a qcow2 blockdev + an NBD unix export
(`bitmaps=[name]`, `writable=false`) via QMP `block-export-add`,
then `qemu-img map --output=json --image-opts "driver=nbd,server.
type=unix,server.path=…,export=…,x-dirty-bitmap=qemu:dirty-bitmap:
<name>"`, parse the extents (dirty == `"data": false`), and return
the set of dirty ranges; a `finally` quits the daemon (QMP `quit`)
and cleans the sockets. Centralise the `x-dirty-bitmap` polarity in
this one helper. Generous timeouts. The oracle needs **no KVM** (only
the `instar bitmap` call under test does). Confirm the daemon + QMP
plumbing works in 7b before building the matrix on it; if it proves
flaky, fall back to comparing dirty extents only (skip full
byte-compare, which is fragile across differing cluster layouts).

### 3. Known divergences to register

instar intentionally diverges from qemu-img in these v1 spots — put
each in `KNOWN_BITMAP_DIVERGENCES` with a reason, and in refusal
tests assert instar refuses while qemu accepts (record, don't fail):
- **`--merge` mixed with metadata actions** in one invocation
  (instar refuses; qemu applies in order).
- **cross-file `--merge -b/-F`** (instar refuses; qemu supports).
- **non-16-bit `refcount_bits`** (instar refuses
  `ERROR_UNSUPPORTED_REFCOUNT_WIDTH`; qemu supports).
- **`RefcountExhausted` → `ERROR_NO_SPACE`** on a near-full image
  (instar refuses rather than growing the refcount table).
- Any info-formatting difference (n/a here since the info compare is
  qemu-vs-qemu).

### 4. Cluster-size + version matrix

Run the cross-validation matrix across cluster sizes {512, 4096,
65536, 1 MiB} at compat=1.1 (the amend cluster-size `.bss` bug was
found by exactly this). Keep it modest (a few sizes × the core
actions) to bound runtime; Phase 9's differential fuzzer sweeps the
full space. v2 images only appear in refusal tests (bitmaps need v3).

### 5. Runtime + CI shape

The suite launches a guest per mutation (≈ seconds each) plus the
storage-daemon oracle for merge-bits — it will be one of the slower
test files. Keep the matrix tight, reuse fixtures where safe, and
ensure every guest-launching test `_require_kvm`s so it self-skips
without KVM. Confirm it runs green under `make test-integration`'s
stestr and note the approximate wall-clock.

## Step-level guidance

Plan at **high** effort for 7a/7b (the oracle plumbing + the
qemu-vs-qemu comparison are the load-bearing, easy-to-get-subtly-
wrong pieces) and **medium** for 7c. This phase can surface real
Phase-4/5 bugs — review failures as potential product bugs, not test
bugs.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | high | opus | none | Create `tests/test_bitmap.py` with `TestBitmapSmoke(InstarTestBase)` — copy `run_instar_bitmap`/`_require_kvm`/`_qemu_create`/`_qemu_info_json` from `test_amend.py` (adapt the subcommand + positional `FILENAME BITMAP` shape), add `assert_bitmaps_equivalent` (extract `format-specific.data.bitmaps`, sort by name, assertEqual — Open question 1). Then `TestBitmapCrossValidation` with the metadata-action + `add;disable` + empty-merge factory matrix (amend's `setattr` loop, Mission §2) across the cluster-size set (Open question 4): each case builds, copies to A/B, runs instar on A + `qemu-img bitmap` on B, asserts `qemu-img check(A)` rc 0 + `assert_bitmaps_equivalent(qemu-info(A), qemu-info(B))`. Run `cd tests && ../.venv/bin/stestr run test_bitmap` (needs `make instar` + `/dev/kvm`); report pass/fail per case. FIX any real instar bug found (report it; it's a Phase-4/5 fix, not a test workaround). |
| 7b | high | opus | none | Add `_seed_bitmap` + `_bitmap_dirty_extents` (the storage-daemon/QMP/`qemu-img map` read-back oracle, Open question 2) and `TestBitmapMergeBits` — same-file bits-set merge with disjoint / overlapping / into-non-empty-dst variants, asserting `qemu-img check(A)` rc 0, bitmaps-metadata equivalence, and `_bitmap_dirty_extents(A,'dst') == _bitmap_dirty_extents(B,'dst')`. FIRST prove the oracle helper works standalone (seed a bitmap, read its extents, assert they match the writes) before wiring the merge matrix on it. Report the oracle mechanism, any flakiness, and any real merge bug found (fix in `src/operations/bitmap/` — the Phase-4 merge orchestration is the untested-until-now piece). |
| 7c | medium | opus | none | `TestBitmapRefusals` (Mission §4 — every error path matched to the Phase-5 host messages) + `KNOWN_BITMAP_DIVERGENCES` (Open question 3), including the divergence-record pattern where qemu is more permissive. Run the full `test_bitmap` suite green under stestr; report the wall-clock. Mark the phase-7 row complete in `docs/plans/PLAN-bitmap.md` + `index.md`. Full gate: `make instar`, `make lint`, `make test-rust` (unaffected), and the `test_bitmap` stestr run; `pre-commit run --all-files`. Present the commit(s). |

## Management session review checklist

- [ ] `tests/test_bitmap.py` mirrors `test_amend.py`'s structure;
      `run_instar_bitmap`/`_require_kvm` copied correctly; the info
      cross-check is **qemu-vs-qemu** (instar info has no bitmaps).
- [ ] The bitmaps-array comparison is **sorted by name** (order-safe).
- [ ] The bits-set merge oracle works and asserts actual merged bits
      (`_bitmap_dirty_extents`), with the daemon cleaned up in a
      `finally`; the `x-dirty-bitmap` polarity lives in one helper.
- [ ] Refusal tests match the real host messages; intentional
      divergences are **registered**, not failed.
- [ ] Cluster-size coverage is present; every guest test
      `_require_kvm`s.
- [ ] Any real instar bug the suite finds is **fixed in the
      product** (Phase 4/5), reported to the management session — not
      papered over.
- [ ] No production changes beyond a possible small `info_json.py`
      helper; `bitmap.bin`/`core.bin` unchanged.
- [ ] `make instar`/`make lint`/`make test-rust`/the `test_bitmap`
      stestr run/`pre-commit run --all-files` green.
- [ ] Commit messages follow conventions.

## Success criteria

- `tests/test_bitmap.py` cross-checks every action against
  `qemu-img bitmap` with post-op `qemu-img check` clean and
  bitmaps-array equivalence, across a cluster-size matrix.
- The **bits-set same-file merge** is validated bit-for-bit against
  qemu via the read-back oracle (closing the Phase-5 gap).
- Every refusal path is asserted against the real host messages;
  intentional divergences are registered.
- The suite runs green under `make test-integration` (stestr, with
  `/dev/kvm`); any product bug it found is fixed.
- All gates green.

## Back brief

Before executing any step, the executing agent should back brief the
operator — in particular that the info cross-check is **qemu-vs-qemu**
(instar `info` emits no bitmaps), that the bitmaps array must be
**sorted by name** before comparison, that the **bits-set merge is
now testable** via the `qemu-io` seed + storage-daemon read-back
oracle (no longer a gap), and that a real failure here is a **product
bug to fix in Phase 4/5**, not a test to weaken.
