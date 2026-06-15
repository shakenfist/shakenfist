# PLAN-check-repair phase 10: differential fuzzing

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase and ground every claim in what the
code actually does — read the source, do not speculate. The code
this phase touches:

- `scripts/differential-fuzz.py` — the differential fuzzer. Key
  shapes to reuse: the `op_*(instar_bin, instar_copy, qemu_copy,
  fmt, timeout, ...)` operation signature; `OPERATIONS` (the op
  registry, validated by `--ops` and dispatched in
  `run_iteration`); `generate_image`; `run_instar` / `run_qemu_img`
  (which return `(stdout, stderr, rc)` and use a timeout sentinel
  picked up by `_is_external_timeout`); `compare_exit_codes` (the
  inconclusive-on-timeout pattern); `op_convert`'s raw-flatten
  oracle (`qemu-img convert -O raw` both sides on neutral ground +
  `files_match`/`_file_sha256`); and the `op_create` precedent for
  an operation that **ignores the passed copies and controls its
  own inputs**.
- `.github/workflows/differential-fuzz.yml` — runs the fuzzer with
  all ops by default; it does **not** enumerate operations, so a
  new entry in `OPERATIONS` is picked up automatically (no CI list
  to edit).
- `../instar-testdata/custom/check-validation/create-corrupt-images.py`
  — the fixture generator (sibling repo, not importable). Its
  corruption primitives (`parse_qcow2_header`, `read_l1_entry`,
  `read_l2_entry`, `set_refcount`, and the per-class
  `create_refcount_zero` / `_too_high` / `_leaked_cluster` /
  `_stale_copied` / `_overlapping`) are the model for the
  self-contained corruptor this phase ports into the fuzzer.
- `src/operations/check/src/main.rs` / `src/vmm/src/main.rs` — the
  repair exit-code mapping (0 clean / 2 errors / 3 leaks / 63
  not-supported) and the `repair-incomplete` JSON key. instar's
  exit code reflects the *detected* corruption even after a clean
  repair (refcount-zero exits 2 but is qemu-clean), so
  completeness is read from `repair-incomplete`, not the exit code
  — the same lesson phase 8 encoded.

The parent master plan is
[PLAN-check-repair.md](/components/instar/plans/PLAN-check-repair/). This is phase 10 of
eleven — **differential** fuzzing of `check --repair`: random
qcow2 corruptions injected into a valid image, repaired by both
instar and qemu-img, with the results compared. Phase 9 was the
in-process **coverage** fuzz of the pure planners; this is the
whole-image, cross-tool counterpart.

I prefer one commit per logical change; the commit must run and
have a clear message. This phase adds a fuzzer operation plus a
corruptor — a Python-only change. No instar source change, so
`check.bin` is byte-identical.

## Situation

`check --repair` ships and is covered by an end-to-end integration
suite (phase 8) and a planner coverage fuzzer (phase 9). What no
test exercises is **adversarial whole-image** repair across the
huge space of corruption × geometry (cluster size, refcount width,
virtual size) — and whether instar's in-place repair agrees with
qemu-img's `-r` on real images. The differential fuzzer is built
exactly for "instar vs qemu-img on a random image": it already
generates images, makes an `instar_copy` and a `qemu_copy`,
dispatches an op per the registry, and reports a structured
divergence dict. Adding a `repair` op slots straight in.

The subtlety — and the whole design problem of this phase — is that
instar's repair is **deliberately more conservative** than
qemu-img's. instar refuses snapshotted, compressed, external-data,
and corrupt-bit-set images, and only partially repairs overlapping
clusters (phases 4–7), whereas `qemu-img check -r all` attempts
more. A naive "both must end clean / both must match" oracle would
fire on every refusal — false positives that bury real findings.
The oracle must distinguish *instar did less on purpose* (expected,
recorded but not a bug) from *instar did something wrong* (a real
divergence).

## The operation design (`op_repair`)

Self-contained, like `op_create`: it **ignores** the passed
`instar_copy` / `qemu_copy` and builds its own controlled input, so
every iteration is a meaningful repair test (the generic generator
often emits zero-allocation images with nothing to corrupt).

Per iteration:

1. **Build a clean qcow2 with known data.** `qemu-img create -f
   qcow2` at a random cluster size (`QCOW2_CLUSTER_SIZES`) and
   virtual size, then write a few known patterns with `qemu-io`
   (mirroring the fixtures' `0xAA/0xBB/0xCC/0xDD` at
   `0/64k/128k/192k`) so there are allocated clusters to corrupt
   and a data oracle to read.
2. **Corrupt it once** with `corrupt_qcow2(rng, base)` (below),
   recording the class. If the chosen class cannot apply (no
   allocated cluster, geometry too small), return `None` — a skip,
   not a divergence.
3. **Fork identical copies.** Copy the single corrupt file to
   `repair-instar.qcow2` and `repair-qemu.qcow2` so both tools
   start from byte-identical corrupt state.
4. **Repair each** at a random tier (`leaks` or `all`): `instar
   check --repair=<tier> --output json` on the instar copy;
   `qemu-img check -r <tier>` on the qemu copy.
5. **Oracle** (below), reading instar's `repair-incomplete` and
   running read-only `qemu-img check --output=json` on the original
   corrupt file and on each repaired copy.

### `corrupt_qcow2(rng, path) -> dict | None`

A self-contained qcow2 corruptor ported from the fixture
generator's primitives (header parse → L1[0] → L2 → data cluster →
refcount entry). Picks one class at random:

- `refcount_zero` — zero a referenced cluster's refcount entry
  (qemu: corruption; repairable by `all`).
- `refcount_too_high` — inflate a referenced cluster's refcount to
  2, clearing its `OFLAG_COPIED` (qemu: leak; repairable by `all`,
  *not* by `leaks`).
- `leaked_cluster` — zero an L2 entry, orphaning its data cluster
  (qemu: leak; repairable by both tiers).
- `stale_copied` — inflate refcount to 2 with `OFLAG_COPIED` set
  (qemu: corruption + leak; repairable by `all`).
- `overlapping` — duplicate L2[0] into L2[1] (qemu: corruption +
  leak; instar does a *partial* repair — leak reclaimed, overlap
  remains).

Returns `{'class': ..., 'offset': ...}` or `None` when the image
has nothing to corrupt. Keep the on-disk arithmetic (big-endian
16-bit refcounts, be64 L1/L2 entries, the offset masks) in a
comment cross-referencing `create-corrupt-images.py` so the two
stay legible together.

### The differential oracle

Read-only `qemu-img check --output=json` yields `corruptions`,
`leaks`, `check-errors` (treat `null` as 0). Measure the **original
corrupt file** (`orig`), and after repair the **instar copy**
(`inst`) and **qemu copy** (`qemu`). Three tiers, in priority order:

1. **Safety — unconditional, the primary signal.** instar must
   never make the image worse:
   - `inst['check-errors'] == 0` — instar must never produce a
     structurally unparseable image, whatever the input.
   - `inst['corruptions'] <= orig['corruptions']` and
     `inst['leaks'] <= orig['leaks']` — instar never introduces a
     new corruption or leak. (Every instar action — reclaim, raise,
     lower, COPIED-reconcile — can only reduce these.)
   This holds whether instar repaired fully, partially, or refused
   (a refusal leaves the image unchanged, so the metrics equal
   `orig` and the `<=` holds). A violation is a real, high-severity
   bug → `repair_safety_divergence`.

2. **Convergence — conditional on instar claiming completeness.**
   When instar reports a *complete* repair (`repair-incomplete ==
   false`) at the `all` tier, instar must have reached clean, the
   way `qemu-img check -r all` does: assert `inst` is qemu-clean
   (`corruptions == leaks == check-errors == 0`). When
   `repair-incomplete == true` (refuse/partial), assert **nothing**
   here — instar deliberately did less; record it under an
   `instar_conservative` counter for visibility (not a divergence,
   not a GitHub issue), mirroring the `inconclusive` pattern. A
   complete-claim that is not actually clean → `repair_completeness_divergence`.

3. **Data equivalence — conditional on both ending clean.** When
   *both* `inst` and `qemu` are qemu-clean after repair, flatten
   both to raw on neutral ground (`qemu-img convert -O raw`) and
   `files_match`. Both repaired the same corrupt image to a clean
   state; refcount/leak repair preserves the L1/L2 reference graph,
   so the guest-visible data must be identical. A mismatch →
   `repair_data_divergence`. (Skip when either side did not reach
   clean — there is no shared ground to compare.)

Timeouts on either tool reclassify to inconclusive via the existing
`_is_external_timeout` / `compare_exit_codes` machinery. If
`qemu-img check` cannot even parse the *original* corrupt file
(no baseline), record inconclusive and skip the asserts.

## Open questions

### 1. Self-contained op, or use the generated copies?

**Resolved: self-contained** (the `op_create` precedent). The
generic generator frequently emits zero-allocation or non-qcow2
images with nothing to repair; building a known qcow2-with-data per
iteration guarantees the op exercises a real repair and gives a
data oracle. The corruption is applied **once** to a single file
that is then forked to both copies, so instar and qemu start
byte-identical (the differential premise).

### 2. How is instar's "complete repair" detected — exit code or
JSON?

**Resolved: the `repair-incomplete` JSON key.** instar's exit code
reflects the *detected* corruption even after a clean repair
(refcount-zero exits 2 but is qemu-clean), so exit 0 is neither
necessary nor sufficient for completeness. `repair-incomplete ==
false` is the contract signal (phase 6) and the same oracle phase 8
relies on. Run instar with `--output json` and parse it.

### 3. Must instar and qemu produce byte-identical images?

**Resolved: no — compare guest data, not bytes.** The two tools may
lay out repaired metadata differently while both being valid and
clean. The data-equivalence tier flattens both to raw (neutral
`qemu-img convert`) and compares *content*, exactly as `op_convert`
does, and only when both reached clean. Byte-level metadata
identity is neither expected nor required.

### 4. What about qemu-img repairing cases instar refuses?

**Resolved: that is expected divergence, not a bug** — the core
design point. The convergence tier asserts only when instar *claims*
completeness; a refusal (`repair-incomplete == true`) suppresses it
and is counted under `instar_conservative`. The safety tier still
applies to the refused image (unchanged ⇒ not worse), so a refusal
is never silently unsafe.

### 5. Does CI / `--ops` need wiring?

**Resolved: only the `OPERATIONS` list + the `run_iteration`
dispatch branch.** `--ops` validates against `OPERATIONS` and the
workflow runs all ops by default with no enumerated list, so adding
`'repair'` to `OPERATIONS` and an `elif op == 'repair'` branch is
the whole wiring.

## Execution

| Step | Effort | Model | Brief |
|------|--------|-------|-------|
| 10a | high | opus | In `scripts/differential-fuzz.py`: add `corrupt_qcow2(rng, path)` (porting the fixture generator's qcow2 parse + the five corruption classes, self-contained, with a cross-reference comment) and `op_repair(instar_bin, instar_copy, qemu_copy, fmt, timeout, rng)` (self-contained per the design: build clean qcow2 + data, corrupt once, fork to two copies, repair each at a random tier, run the three-tier oracle). Register `'repair'` in `OPERATIONS` and add the `elif op == 'repair'` dispatch branch in `run_iteration`. Reuse `run_instar` / `run_qemu_img` / `_file_sha256` / `files_match` / `compare_exit_codes` / the timeout sentinel; do not reinvent them. The deliverable is the **oracle's freedom from false positives** — instar's deliberate refuse/partial behaviour must land in `instar_conservative`, never in a `*_divergence`. Every divergence dict gets a distinct `type` (`repair_safety_divergence` / `repair_completeness_divergence` / `repair_data_divergence`). |
| 10b | medium | sonnet | Run the fuzzer locally against the worktree binary and host qemu-img: a temp venv with `tests/requirements.txt` is not needed (the fuzzer is stdlib-only) — run `python scripts/differential-fuzz.py --ops repair --iterations 3000 --instar <built instar>` (read `main()` for the exact flag names; pass a seed for reproducibility). Confirm: (a) it actually exercises repairs (the summary shows repair iterations, `instar_conservative` is non-zero — refusals are happening — and is **not** counted as divergence); (b) **zero** `repair_*_divergence` results, or, if any fire, triage each: a real instar bug is a finding to surface to the operator (stop and report, do not paper over the oracle); an oracle false-positive is a bug in 10a's oracle to fix. Capture a short summary of iterations run, conservative count, and any divergences. |
| 10c | low | sonnet | Commit. Present ONE commit on the `check-repair` branch with the corruptor + `op_repair` + registration. The message: differential fuzzing for `check --repair` — random qcow2 corruptions repaired by instar and qemu-img and compared by a three-tier oracle (unconditional safety = never-worse + no new check-errors; conditional convergence gated on `repair-incomplete == false`; conditional data-equivalence when both reach clean); instar's deliberate refuse/partial behaviour is recorded as conservative, not divergence; Python-only, `check.bin` unchanged. Update the master-plan phase-10 row to "Landed". No `--ops`/CI edit needed (the `OPERATIONS` entry drives both). |

## Agent guidance

### Execution model

Sub-agents implement 10a; the management session runs the fuzzer
itself (10b) — this is where the oracle's real-world soundness is
proven — and commits (10c). The hazard mirrors phase 9 but inverts:
there the risk was a *false assertion* turning the lane red on a
non-bug; here it is an oracle that flags instar's **intended**
conservatism as a divergence (false positive) or, worse, one so lax
it misses a real "instar made it worse" (false negative). The
safety tier must be strict (`<=` on every metric, `check-errors ==
0`); the convergence/data tiers must be properly gated on
`repair-incomplete` / both-clean.

### Model and effort notes

- **10a is high opus**: the corruptor must produce qemu-recognised
  corruptions across geometries, and the oracle's tier gating is
  the subtle, high-value part.
- **10b is medium sonnet**: run a few thousand iterations, confirm
  conservative-not-divergence, triage anything that fires.
- **10c is low sonnet**: one commit + the plan-row flip.

### Management session review checklist

- [ ] The corruption is applied **once** and forked to both copies
      (instar and qemu start byte-identical).
- [ ] `op_repair` is self-contained (does not depend on the passed
      copies' content) and skips (`None`) when nothing can be
      corrupted — a skip, never a false divergence.
- [ ] Safety tier is unconditional and strict: `check-errors == 0`
      and `corruptions`/`leaks` `<=` original.
- [ ] Convergence tier fires **only** when `repair-incomplete ==
      false` at the `all` tier; refusals/partials go to
      `instar_conservative`.
- [ ] Data tier compares raw-flattened content, only when both
      reached clean.
- [ ] A local run of ≥3000 `--ops repair` iterations shows repairs
      and refusals happening with zero `repair_*_divergence` (or
      every one triaged to a real bug surfaced to the operator).
- [ ] No instar source change; `check.bin` byte-identical.

## Administration and logistics

### Success criteria

* `op_repair` + `corrupt_qcow2` land in `differential-fuzz.py`,
  registered in `OPERATIONS` and dispatched in `run_iteration`.
* A local `--ops repair` run of a few thousand iterations exercises
  real repairs and refusals with no false-positive divergence.
* instar's conservatism is recorded (`instar_conservative`), not
  flagged.
* One commit on the `check-repair` branch; no instar source change,
  `check.bin` byte-identical.

### Future work created by this phase

- When the deferred snapshot/compression-aware recount lands
  (currently those are refused), extend the corruptor with
  snapshot/compressed corruptions and let the convergence tier
  apply to them.
- If the repair counters reach the host wire (phase-6 follow-up),
  add a differential check that instar's reported repaired-counts
  are consistent with the `orig - inst` metric deltas.

### Bugs fixed during this work

To be filled in if the run surfaces a real instar repair defect
(distinct from an oracle false-positive, which is a fuzzer fix).

### Documentation index maintenance

A phase plan, not a master plan: **not** added to `order.yml`. The
master plan's phase-10 row is updated to "Landed" once the commit
is in. (Broader docs — the README/ARCHITECTURE fuzz-target counts,
`docs/testing.md` — are phase 11's remit, which also owes the
phase-9 `fuzz_check_repair` count bump.)

### Back brief

Before executing any step, back brief the operator on your
understanding — especially that the corruption is forked identically
to both copies, that the safety tier is the unconditional primary
signal, and that instar's intended refuse/partial behaviour must be
recorded as conservative rather than flagged as a divergence.
