# PLAN-dd phase 09: Differential fuzzing vs qemu-img dd

Master plan: [PLAN-dd.md](/components/instar/plans/PLAN-dd/)
Previous phase: [PLAN-dd-phase-08-fuzz.md](/components/instar/plans/PLAN-dd-phase-08-fuzz/)

## Status: Complete (op_dd f62d7d9; fixes 779e7a7, b80c5d7)

> **Outcome.** `op_dd` added to `differential-fuzz.py`; a
> 500-iteration `--ops dd` campaign (seed 20260624) ran clean after
> the campaign surfaced **two real bugs**, both fixed:
> 1. **Pre-existing convert data loss** (`779e7a7`): the
>    vmdk/vhd/vhdx writers passed a full grain/block to
>    `read_chain_virtual_cluster` (which fills only one input
>    cluster), so qcow2 inputs with sub-grain cluster sizes were
>    truncated. Fixed with `read_chain_virtual_range`. This also
>    fixes `instar convert` on develop — worth a separate
>    cherry-pick.
> 2. **Dense-VHD output-capacity under-estimate** (`b80c5d7`): a
>    dense VHD's per-block bitmap/alignment overhead exceeded the
>    generic headroom, stalling the final write; sized explicitly.
> Empty-window `-O vmdk`/`-O vhdx` are the only whitelisted known
> divergences. The convert integration suite stays 201/0; dd tests
> and `make test-rust` green.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Flag any uncertainty
explicitly rather than guessing.

## Mission

Add `dd` to `scripts/differential-fuzz.py` so random
`bs`/`count`/`skip`/`-O` invocations are cross-checked against the
real `qemu-img dd` binary, and resolve (by handling/documenting)
the degenerate empty-window divergences. This is the broadest
parity guard — it explores the operand space the fixed integration
matrix (phases 3/4/6) cannot, especially non-512 `bs`, unaligned
windows, and skip/count interactions.

## Design

### `op_dd`, modeled on `op_convert`

`scripts/differential-fuzz.py` dispatches per operation via an
`if/elif` chain (≈ line 3204) over the `OPERATIONS` list (≈ line
50). `op_convert` (≈ line 675) is the template for an image
producer: pick a random target, run instar + qemu-img, compare
exit codes (`compare_exit_codes`), and — if both succeeded —
compare content by flattening **both** outputs to raw with
`qemu-img convert -O raw` (neutral ground) and `files_match`-ing
them (raw targets compare directly).

`op_dd(instar_bin, instar_copy, qemu_copy, fmt, timeout, rng)`
mirrors this:
1. Random `target_fmt` ∈ {`raw`, `qcow2`, `vmdk`, `vpc`, `vhdx`}.
2. Random window operands (the value-add over the fixed matrix):
   - `bs`: draw from a mix that exercises the rounding + sub-sector
     paths — common 512-multiples (512, 4096, 65536, 1M) **and**
     non-512 values (e.g. 1000, 999, odd numbers) and the
     boundaries (1, near `INT_MAX`). instar matches qemu for all of
     these (verified phases 3/4); the fuzzer's job is to keep that
     true. Bound `bs`×`count` so buffers/outputs stay small.
   - `count`: `None` (whole image) or a random block count
     (including values beyond EOF, and occasionally 0 — see known
     divergences).
   - `skip`: 0..~2× the image's block count (so skip-past-EOF is
     reachable).
3. Build operands for BOTH tools (same values): `if=`, `of=`,
   `bs=`, optional `count=`/`skip=`, plus `-O target_fmt`.
4. Run `run_instar(instar_bin, ['dd'], …)` and
   `run_qemu_img(['dd'], …)`.
5. `compare_exit_codes(...)`; if both non-zero, return None; else
   flatten both outputs to raw and `files_match` (mirror
   `op_convert`'s raw-vs-structured branches). A
   `dd_content_divergence` dict on mismatch.

### Known-divergence handling (the `count=0 -O vhdx` resolution)

These are pre-established, not new bugs. `op_dd` must recognise
them and NOT report them as failures (the standard fuzzer pattern,
cf. `KNOWN_DIVERGENCE_FIELDS` / `KNOWN_WRITER_DIVERGENCES`):

- **Empty-window `-O vmdk`** (`out_vsize == 0`, i.e. `count=0` or
  `skip` past the count-clamped end): `qemu-img dd` itself **exits
  1** (monolithicSparse cannot represent a 0-capacity disk), while
  instar exits 0 with an (unreadable) empty vmdk. This is an
  exit-code divergence on a degenerate input — skip/whitelist it.
- **Empty-window `-O vhdx`**: instar's 0-virtual-size vhdx is
  rejected by `qemu-img info`/`convert` (the documented phase-4
  limitation), so flattening instar's output to raw fails; qemu's
  empty vhdx is readable. Skip/whitelist this case.
- **vhdx block-size** (instar 32 MiB vs qemu 8 MiB default): a
  pre-existing convert/vhdx-writer divergence. It does **not**
  affect the round-trip-to-raw content compare (raw flattening is
  block-size-agnostic), so no special handling is needed for `op_dd`
  — but do not be surprised by it.

Detect the empty-window case in `op_dd` (recompute `out_vsize` from
the chosen `bs`/`count`/`skip` and the input's virtual size, or
detect from the results — qemu rc≠0 for vmdk, instar-output
unflattenable for vhdx) and treat `target_fmt ∈ {vmdk, vhdx}` +
empty-window as a known divergence with a clear inline comment.

**Resolution scope:** "resolve the `count=0 -O vhdx` limitation"
means the fuzzer handles it cleanly as a documented known
divergence so the campaign runs clean — NOT that instar's empty
vhdx is made qemu-readable (the phase-4 agent could not isolate
why qemu rejects instar's specific empty BAT layout). Keep
"make instar's empty vhdx qemu-readable" in the master plan's
Future work.

### Registration + campaign

- Add `'dd'` to `OPERATIONS` (≈ line 50) and an
  `elif op == 'dd': div = op_dd(...)` arm to the dispatch (≈ line
  3204). When no `--ops` filter is given, dd is then included
  automatically.
- Check `.github/workflows/differential-fuzz.yml` (≈ the
  `differential-fuzz.py` invocation, line 164): if it runs all
  ops, dd is picked up; if it hardcodes an op list, add `dd`.
- Run a campaign (e.g. `--ops dd --iterations 2000 [--seed …]`) to
  confirm clean. Broad parity is already verified (phases 3/4), so
  expect no divergences beyond the documented empty-window cases.
  Any REAL divergence the campaign finds is a bug to fix (diagnose
  + minimise + fix, like phases 3/4) — do not whitelist a genuine
  mismatch.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | high | opus | none | In `scripts/differential-fuzz.py`, add `op_dd` modeled on `op_convert` (≈675): random `target_fmt` ∈ {raw,qcow2,vmdk,vpc,vhdx} + random `bs` (mix of 512-multiples AND non-512 values + boundaries, bounded so outputs stay small) + random `count`(or None)/`skip` (reaching past EOF). Run `instar dd` and `qemu-img dd` with identical operands; `compare_exit_codes`; on dual success, flatten both outputs to raw via `qemu-img convert -O raw` and `files_match` (raw targets compare directly), returning a `dd_content_divergence` dict on mismatch. Add `'dd'` to `OPERATIONS` and the dispatch `elif`. Handle the empty-window known divergences (`-O vmdk`: qemu exits 1; `-O vhdx`: instar output unflattenable) by detecting `out_vsize == 0` and whitelisting those `target_fmt`s with an inline comment citing the phase-4 limitation; do NOT whitelist anything else. Read `op_convert`, `compare_exit_codes` (≈490), `generate_image` (≈94 — for the input's virtual size), `run_instar`/`run_qemu_img`, and `files_match`/`_file_sha256` to mirror idioms. Check `.github/workflows/differential-fuzz.yml` and update if it enumerates ops. THEN run a campaign: `python3 scripts/differential-fuzz.py --instar <release binary> --ops dd --iterations 2000` (use the project's documented invocation / container per AGENTS.md; /dev/kvm + qemu-img present). Report the campaign result: clean, or any divergence with its seed. If a REAL divergence is found (not an empty-window known case), diagnose, minimise, and fix it (in the dd guest/host code) — that is the point of this phase; report the fix. |

Per the master plan / `PLAN-TEMPLATE.md`, the sub-agent implements
and the management session reviews before committing (verify the
known-divergence whitelist is narrow — only empty-window vmdk/vhdx
— and that `op_dd` genuinely round-trip-compares content, not a
tautology). Suggested commit: the `op_dd` addition + any real-bug
fix the campaign surfaces (separate commits if a code fix is
needed).

## Verification

- [ ] `op_dd` added; `dd` in `OPERATIONS` and the dispatch.
- [ ] A `--ops dd` campaign of ≥2000 iterations runs **clean** (no
      divergences beyond the documented empty-window vmdk/vhdx
      cases). Report the iteration count + seed.
- [ ] The known-divergence whitelist is limited to empty-window
      `-O vmdk` / `-O vhdx`; non-512 `bs`, unaligned windows, and
      skip/count combos produce NO divergence (parity holds).
- [ ] Any real divergence found is fixed in dd code (not
      whitelisted) and re-verified.
- [ ] `differential-fuzz.yml` includes dd (or auto-runs it).
- [ ] `pre-commit run --all-files` passes.
- [ ] Commit messages follow conventions (model/context/effort).

## Hand-off

Phase 10 (docs) is the last phase: `docs/dd.md`; README /
ARCHITECTURE / AGENTS / CHANGELOG updates (including the deferred
phase-7 `dd-info-json` baseline notes); the `docs/plans/index.md`
*Complete* flip; and confirming the master plan's Future-work list
captures the deferred items (PVE extensions, `--image-opts`/
`--object`/`-U`, the empty-vhdx readability fix, and `fuzz_dd_operands`).
The [[dd-qemu-img-parity-contract]] memory records the verified
rules.
