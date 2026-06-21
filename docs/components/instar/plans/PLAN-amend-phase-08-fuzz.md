# PLAN-amend phase 08: fuzzing

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase thoroughly. Read the coverage-fuzz
target you are mirroring
(`src/fuzz/fuzz_targets/fuzz_resize_planners.rs`), the fuzz crate
wiring (`src/fuzz/Cargo.toml`, `src/fuzz/src/lib.rs`), the amend
planner entrypoint (`src/crates/amend/src/qcow2.rs` +
`src/crates/amend/src/lib.rs`), the differential harness
(`scripts/differential-fuzz.py` — its `OPERATIONS` list, the
`op_resize`/`op_create` functions, `_resize_option_picker`, the
`run_iteration` dispatch, `run_instar`/`run_qemu_img`,
`normalize_info_json`, `compare_exit_codes`), and the two CI
workflows (`.github/workflows/coverage-fuzz.yml` target list,
`.github/workflows/differential-fuzz.yml`). Ground every claim in
what the code actually does. Flag uncertainty rather than guessing.

Phase plans live in `docs/plans/` named
`PLAN-amend-phase-NN-<descriptive>.md`. The master plan is
[PLAN-amend.md](/components/instar/plans/PLAN-amend/); phases 1–7 are landed. This is the
eighth of nine.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained.

## Situation

The amend planner (`plan_amend_qcow2`) and the `instar amend` CLI
are implemented and tested by example (phases 5–7). This phase adds
the two fuzzing harnesses every other instar subcommand carries:

1. **Coverage-guided fuzz** of the pure planner —
   `src/fuzz/fuzz_targets/fuzz_amend_planners.rs`, modelled on
   `fuzz_resize_planners.rs`. libFuzzer feeds arbitrary bytes; the
   target derives an `Qcow2AmendOpts` (a synthesised header cluster
   + the four control flags) and calls `plan_amend_qcow2`, asserting
   structural invariants on every `Ok(plan)` and requiring no panic
   on any input.

2. **Differential fuzz** against `qemu-img amend` — an `op_amend`
   in `scripts/differential-fuzz.py`, modelled on `op_resize`. Each
   iteration builds a random qcow2 start image with `qemu-img
   create` (twice — one copy per tool), amends each copy with its
   native tool, and compares exit codes + post-amend `qemu-img info
   --output=json`.

The grounding the implementer builds on (verified):

- **Planner entrypoint** (`src/crates/amend/src/qcow2.rs`):
  `pub fn plan_amend_qcow2<'a>(opts: &Qcow2AmendOpts<'_>, scratch:
  &'a mut [u8]) -> Result<AmendPlan<'a>, AmendError>`.
  `Qcow2AmendOpts` (`lib.rs`) = `{ header_cluster: &[u8],
  cluster_size: u32, set_compat: bool, target_v3: bool, set_lazy:
  bool, lazy_on: bool }`. `AmendPlan` = `{ action: AmendAction
  (NoOp|Amended), resulting_version: u32 (2|3),
  resulting_lazy_refcounts: bool, patches: [AmendPatch; 2] }` with
  `MAX_AMEND_PATCHES = 2` and `patches() -> &[AmendPatch]`.
  `AmendPatch::Write { byte_offset: u64, bytes: &[u8] }` with
  `byte_offset()`/`len()`. `AmendError` is a 10-variant enum (parse,
  dirty, downgrade-blockers, lazy-requires-v3, scratch-too-small,
  overflow, …). The planner parses the header itself; unlike resize
  it takes NO pre-parsed state. **It only ever rewrites the first
  cluster**, so every emitted patch must land within `cluster_size`.

- **`fuzz_resize_planners.rs`** template: `#![no_main]` +
  `use libfuzzer_sys::fuzz_target;`; a fixed-size structured-header
  prefix decoded by manual byte slicing; a thread-local `SCRATCH`
  `Vec<u8>` reused across runs; dispatch then assertions on
  `Ok(plan)` only (errors silently ignored — no panic is the
  baseline property); invariant helpers check patch-count bound,
  `checked_add` offset overflow, within-file bounds, and
  no-overlapping-writes (sorted by offset). No round-trip re-parse.

- **`src/fuzz/Cargo.toml`**: each target is a `[[bin]]` with `name`,
  `path`, `doc = false`, `test = false`. Planner crates are path
  deps (`resize = { path = "../crates/resize" }`, etc.). **No
  `amend` dep yet — must be added.** `src/fuzz/src/lib.rs` already
  wires `mock_send_amend_result` into the mock CallTable, but the
  planner fuzz target does NOT need the CallTable (it calls the pure
  `plan_amend_qcow2` directly, exactly as resize does).

- **`coverage-fuzz.yml`** holds a hard-coded `TARGETS=( … )` array
  (currently 22 targets) iterated in the nightly/PR/push run, and
  builds all targets with `cargo fuzz build`. `fuzz_amend_planners`
  must be added to that array. The fuzz build runs inside the
  `instar-build` Docker image (host has no native Rust toolchain by
  preference); `make fuzz-build FUZZ_TARGET=<t>` / `make fuzz-run`
  wrap it. Corpus lives at `src/fuzz/corpus/<target>/` and is
  pushed to `instar-testdata:custom/fuzz-corpus/<target>/`;
  `scripts/extract-fuzz-corpus.py` seeds it.

- **`differential-fuzz.py`**: `OPERATIONS` is a flat list (line ~50);
  `run_iteration` dispatches via an `if/elif op == …` chain. An op
  is `op_<name>(instar_bin, instar_copy, qemu_copy, fmt, timeout,
  rng) -> None | divergence_dict`. `op_resize`/`op_create` IGNORE
  `instar_copy`/`qemu_copy`/`fmt` and build their own images via a
  `_*_option_picker(rng)` that returns a `(target, …, options)`
  tuple steered AWAY from known divergences. Helpers: `run_instar`,
  `run_qemu_img` (both `(stdout, stderr, rc)`),
  `normalize_info_json`, `compare_exit_codes`, `_file_sha256`,
  per-iteration `iter_dir` (auto-cleaned). `op_rebase`/`op_commit`/
  `op_repair` already launch the guest VMM and assume `/dev/kvm`
  (the workflow passes `--device /dev/kvm`); `op_amend` follows that
  same posture — no special kvm guard.

- **The one documented divergence (phase 6).** `instar amend -o
  compat=0.10` refuses a v3 image carrying the zstd
  *compression* incompatible feature
  (`ERROR_DOWNGRADE_BLOCKED_FEATURE`); qemu-img *accepts* it
  (rewriting `compression_type`). The differential picker must NOT
  generate that combination (mirror `op_create`'s picker, which
  already never emits `compression_type=zstd`). Likewise a downgrade
  of an image with `refcount_bits != 16` is refused by instar; keep
  the start image at the qcow2 default (16) whenever the amend
  target is a downgrade, so both tools agree, OR rely on
  `compare_exit_codes` treating a shared non-zero rc as parity.

## Mission and problem statement

After this phase:

1. **Coverage target.** `src/fuzz/fuzz_targets/fuzz_amend_planners.rs`
   exists, is registered as a `[[bin]]` in `src/fuzz/Cargo.toml`
   (with `amend` added as a path dep), and is listed in
   `coverage-fuzz.yml`'s `TARGETS` array. It builds clean under
   `cargo fuzz build` and runs a short session (≥60s) with no crash.

2. **Differential op.** `scripts/differential-fuzz.py` has
   `'amend'` in `OPERATIONS`, an `op_amend(...)` function and an
   `_amend_option_picker(rng)` (steered away from the compression
   and refcount-width divergences), and an `elif op == 'amend':`
   dispatch arm. `differential-fuzz.py --ops amend --iterations 50`
   runs clean (zero divergences) on a kvm-capable host.

3. **Master plan** phase-8 row marked Complete; any newly
   discovered divergence recorded (in `KNOWN_AMEND_DIVERGENCES` for
   the integration side and noted in the master plan).

4. `make instar` and the guest-binary size check are unaffected
   (fuzz targets are separate bins; the differential script is
   Python); `pre-commit run --all-files` is clean.

Out of scope: docs/CHANGELOG (phase 9); lifting the
compression-downgrade divergence (recompression is out of v1
scope).

## Open questions

### 1. Coverage target — input derivation strategy

Recommendation (confirm): mirror resize's "fixed structured prefix
+ remaining-bytes pool". Use a small prefix (≈8 bytes) to derive:
`cluster_size` (select from a realistic set — 512, 4K, 64K, 1M — by
a byte modulo, so the within-cluster invariant has meaning), and
the four control flags (`set_compat`, `target_v3`, `set_lazy`,
`lazy_on`) from individual bits. Build a `header_cluster` buffer of
`cluster_size` bytes from the remaining fuzz bytes, and **stamp a
valid qcow2 magic + a v2/v3 version byte** (chosen from the bytes)
at the canonical offsets so the planner's header parse frequently
proceeds past `ParseFailed` into the interesting compat/lazy/
extension-relocation logic. (A pure-random cluster almost always
returns `ParseFailed` → shallow coverage.) Confirm this hybrid, or
prefer a fully-`arbitrary`-derived skeleton.

### 2. Coverage target — which invariants are mandatory?

Recommendation (confirm) — assert on every `Ok(plan)`:
- `plan.patches().len() <= MAX_AMEND_PATCHES` (2).
- For each `Write`: `byte_offset.checked_add(len)` does not overflow
  AND `byte_offset + len <= cluster_size as u64` (**amend only
  rewrites the header cluster** — this is the strongest amend-
  specific invariant; if a legitimate `Ok` ever violates it, that is
  a real finding to REPORT, not silence).
- `resulting_version ∈ {2, 3}`.
- No two `Write` patches overlap (sort by offset, mirror resize).
- If `action == NoOp`, `patches()` is empty.

Round-trip re-parse (apply the patches to a cluster copy and confirm
the qcow2 header parser reports `resulting_version`/lazy) is a
STRONGER property but risks false crashes if the planner's success
contract is subtler than assumed. Recommendation: leave round-trip
to the phase-5 unit tests; keep the fuzzer to the no-panic + bounds
set above. Confirm, or include a guarded round-trip.

### 3. Coverage target — corpus seeding

Recommendation (confirm): seeding is optional — libFuzzer will grow
its own corpus, and the magic-stamping in OQ1 gives it a head
start. If we want faster convergence, add a `fuzz_amend_planners`
mapping in `scripts/extract-fuzz-corpus.py` pointing at the existing
qcow2 header/image seed set (amend reads the first cluster, so the
qcow2-header corpus is directly reusable). Confirm whether to add
the seed mapping now or defer.

### 4. Differential op — comparison depth

Recommendation (confirm): match `op_resize` exactly — (a)
`compare_exit_codes` for parity, then (b) on shared success,
`qemu-img info --output=json` on both outputs, normalised via
`normalize_info_json`, dict-compared. OPTIONALLY also run `qemu-img
check` on the instar output (cheap, catches structural damage the
info view misses) — phase 6 did this. Confirm whether to add the
`check` step or keep parity with resize's info-only comparison.

### 5. Differential op — start-image construction

Confirmed by the v2 constraint: build start images with `qemu-img
create` (NOT `instar create`, which is v3-only and can't produce the
v2 upgrade inputs). The picker emits `(create_opts, amend_opts)`;
both tools create from identical `create_opts` then amend from
identical `amend_opts`. Confirm.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | high | opus | none | Add the coverage target. Create `src/fuzz/fuzz_targets/fuzz_amend_planners.rs` modelled on `fuzz_resize_planners.rs` (`#![no_main]`, thread-local `SCRATCH` sized to the max cluster, fixed prefix decoding `cluster_size` + the four flags per OQ1, header-cluster synthesis with qcow2 magic/version stamping, call `plan_amend_qcow2`, assert the OQ2 invariants on `Ok` only, no panic on any input). Add `amend = { path = "../crates/amend" }` to `[dependencies]` in `src/fuzz/Cargo.toml` and a `[[bin]]` block (`name = "fuzz_amend_planners"`, `path = "fuzz_targets/fuzz_amend_planners.rs"`, `doc = false`, `test = false`) placed alphabetically/next to the other planner targets. Add `fuzz_amend_planners` to the `TARGETS=( … )` array in `.github/workflows/coverage-fuzz.yml`. (OQ3: add the corpus seed mapping in `scripts/extract-fuzz-corpus.py` only if confirmed.) Validate via Docker (host has no native cargo): `make fuzz-build FUZZ_TARGET=fuzz_amend_planners` must compile the WHOLE fuzz crate clean, then `make fuzz-run FUZZ_TARGET=fuzz_amend_planners FUZZ_DURATION=60` (or the equivalent `cargo fuzz run … -- -max_total_time=60` inside the `instar-build` container) must run 60s with zero crashes. Report the libFuzzer exec/s and coverage (cov:/ft:) counters. Do NOT run native `cargo`. Do NOT commit. opus: invariant choice and the parse-depth stamping decide whether this target finds bugs or just spins on `ParseFailed`. |
| 8b | high | opus | none | Add the differential op. In `scripts/differential-fuzz.py`: add `'amend'` to `OPERATIONS`; write `_amend_option_picker(rng)` returning `(create_opts, amend_opts)` (qcow2-only) covering upgrade/downgrade/lazy-on/lazy-off/noop, with randomised `cluster_size`/`refcount_bits` BUT steered away from the documented divergences (never emit `compression_type=zstd`; keep `refcount_bits=16` whenever `amend_opts` downgrades to compat=0.10 — see Situation); write `op_amend(instar_bin, instar_copy, qemu_copy, fmt, timeout, rng)` mirroring `op_resize`: ignore the passed copies, build two start images in `iter_dir` via `run_qemu_img(['create'], …)` from identical `create_opts`, amend each (`run_instar(instar_bin, ['amend'], amend_args)` and `run_qemu_img(['amend'], amend_args)`, where `amend_args = ['-f','qcow2'] + flatten(-o opt) + [path]`), `compare_exit_codes`, then on shared success compare normalised `qemu-img info --output=json` (and `qemu-img check` on the instar output if OQ4 confirmed). Return a divergence dict on mismatch, else `None`. Add the `elif op == 'amend': div = op_amend(instar_bin, instar_copy, qemu_copy, fmt, timeout, rng)` arm in `run_iteration`. Validate (needs `/dev/kvm`; run with the bash sandbox DISABLED): `python3 scripts/differential-fuzz.py --instar /srv/kasm_profiles/mikal/vscode/src/shakenfist/instar-wt-amend/src/target/release/instar --ops amend --iterations 50 --timeout 60 --log-dir /tmp/amend-fuzz-logs` — expect 0 divergences. If a divergence fires, capture the full report and DO NOT silence it by loosening the picker without reporting it first. Do NOT commit. opus: a mis-steered picker floods false divergences and a too-loose one misses real ones. |
| 8c | low | sonnet | none | Gates + commit. Confirm `git status` shows only the four expected files (`fuzz_amend_planners.rs`, `src/fuzz/Cargo.toml`, `coverage-fuzz.yml`, `differential-fuzz.py`, plus `extract-fuzz-corpus.py` if OQ3 added it, plus the master plan). Update `docs/plans/PLAN-amend.md` phase-8 row to Complete and register any newly found divergence. Run `pre-commit run --all-files` (clean) and confirm `make instar` + the binary-size check are unaffected (no guest-binary change). Re-run the short differential `--ops amend --iterations 20` smoke as a final confirmation. Stage and present ONE instar-side commit for the whole phase (message per CLAUDE.md, `Prompt:`/`Signed-off-by`/`Assisted-By`/`Co-Authored-By`). Do not push. |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. After each step the management session reads the
actual changed files, confirms no unrelated files changed, runs the
named gates, and then commits / retries / upgrades. The fuzz build
and runs happen inside the `instar-build` Docker image (no native
host cargo); the differential run needs `/dev/kvm` and the bash
sandbox disabled. The management review re-reads the fuzz target's
invariant block and the differential picker's divergence-avoidance
logic, and re-runs the short differential smoke.

### Model and effort notes

- 8a and 8b are opus: both hinge on subtle judgement (which
  invariants are sound; which option combinations are real vs
  documented divergences). A wrong call here produces either false
  crashes/divergences or a harness that exercises nothing.
- 8c is mechanical; sonnet suffices.
- When in doubt, skew to the more capable model.

### Management session review checklist

After the steps:

- [ ] `fuzz_amend_planners.rs` calls `plan_amend_qcow2` on a
      synthesised cluster, asserts only the OQ2 invariants on `Ok`,
      and never panics on malformed input; the whole fuzz crate
      still compiles.
- [ ] The within-cluster invariant (`byte_offset + len <=
      cluster_size`) is asserted and held across the 60s run.
- [ ] `fuzz_amend_planners` appears in `Cargo.toml` AND the
      `coverage-fuzz.yml` `TARGETS` array.
- [ ] `op_amend` builds start images with `qemu-img create`, amends
      with each native tool, and compares exit codes + info JSON;
      the picker never emits the zstd-downgrade or
      refcount-width-downgrade combinations.
- [ ] `--ops amend --iterations 50` reports 0 divergences (or the
      single divergence is genuinely new and was reported, not
      silenced).
- [ ] `make instar` / binary sizes unchanged; `pre-commit` clean.

## Administration and logistics

### Success criteria

Phase 8 is complete when:

* `fuzz_amend_planners.rs` exists, is registered (Cargo.toml +
  workflow), builds clean, and survives a ≥60s fuzz run.
* `op_amend` + `_amend_option_picker` exist, are registered in
  `OPERATIONS` + the dispatch, and `--ops amend` runs clean.
* `make instar` / binary sizes unaffected; `pre-commit` clean.
* The master-plan phase-8 row is Complete; any new divergence is
  registered.

### Future work created by this phase

- Phase 9: docs (`docs/amend.md`, usage, CHANGELOG, ARCHITECTURE/
  README/AGENTS, index status flip to Complete).
- Corpus seeding (OQ3) if deferred; the nightly coverage-fuzz run
  will accumulate an amend corpus and push it to instar-testdata.
- If the differential run ever surfaces a real divergence, register
  it in `KNOWN_AMEND_DIVERGENCES` and the master plan, and decide
  whether it is a bug to fix in the planner (phases 2–3) or an
  accepted divergence.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not** added to
`docs/plans/order.yml`. The master plan links to it from its
Execution table (already present).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan, including the two harnesses
(pure-planner coverage fuzz vs. CLI differential fuzz), the
Docker-only fuzz build, and the divergence-avoidance the
differential picker must encode.
