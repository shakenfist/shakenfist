# PLAN-dd phase 02: Host operand parser, window math, and `run_dd`

Master plan: [PLAN-dd.md](/components/instar/plans/PLAN-dd/)
Previous phase: [PLAN-dd-phase-01-abi.md](/components/instar/plans/PLAN-dd-phase-01-abi/)

## Status: Complete (8909837 refactor, 8b55c6c host impl, 1a2cb91 tests)

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

Implement the host half of `dd`: turn the raw `name=value`
operands captured by phase 1 into a validated request, compute the
input byte-window from the input's virtual size, size the output
accordingly, and launch `convert.bin` with an extended
`ConvertConfig`. After this phase, `instar dd if=IN of=OUT`
(whole-image, `-O raw`) runs end-to-end and is byte-identical to
`qemu-img dd if=IN of=OUT`.

Because the guest does not yet honour the window (phase 3), the
**skip/count cases are not yet correct end-to-end** — the guest
copies the whole image and the host-sized (smaller) output is
truncated. That is the expected intermediate state: phase 2's
correctness for windowed cases is proven by **pure unit tests** of
the parser and window math; the only end-to-end integration test
in this phase is the whole-image raw smoke test, which is correct
even without the guest change.

This phase deliberately does **not** change `convert.bin`
(phase 3/4) and does **not** add non-raw format integration tests
(phase 4).

## Design

### Anti-duplication: extract a shared `execute_convert` core

`run_convert` (`src/vmm/src/main.rs` ≈ 8621–9456) is a single
inline function: validate args → `discover_backing_chain` →
compute output capacity → open/size output → set up guest
memory/KVM/devices → write `ConvertConfig` → run the vCPU loop →
finalise output. `dd` needs everything from "compute output
capacity" onward, differing only in: (a) the output **virtual
size** (`out_vsize`, not the input virtual size), (b) the
`window_start`/`window_end` fields + `FLAG_DD_WINDOW`, (c) dense
output (no `FLAG_SKIP_ZEROS`), and (d) its own argument source.

Duplicating ~500 lines of KVM/vCPU/device-setup into `run_dd`
would be fragile — that code carries subtle invariants (guest load
addresses, config offsets, device MMIO layout) and is exactly the
kind of thing that has caused corruption bugs before. So this
phase **extracts the post-arg-parse body of `run_convert` into a
shared `execute_convert` function**, parameterised by a small
request struct:

```
struct ConvertExecution {
    input: String,
    output: String,
    target_format: u32,          // ImageFormat value
    flags: u32,                  // SKIP_ZEROS / COMPRESS / DD_WINDOW / ...
    output_cluster_bits: u32,
    output_grain_size: u32,
    output_block_size: u32,
    // passphrase / luks / snapshot fields as run_convert has them
    window: Option<(u64, u64)>,  // (window_start, window_end); None for convert
    output_vsize_override: Option<u64>, // dd's out_vsize; None ⇒ input virtual size
    sector_size: u32,
    progress_percent: u32,
}
```

`run_convert` becomes: parse/validate `ConvertArgs` → build a
`ConvertExecution { window: None, output_vsize_override: None }` →
call `execute_convert`. `run_dd` becomes: parse operands → window
math → build a `ConvertExecution { window: Some(..),
output_vsize_override: Some(out_vsize), flags without SKIP_ZEROS,
flags | DD_WINDOW }` → call `execute_convert`. The existing
`convert` integration suite is the regression net for this
refactor — it must stay green with zero diff in behaviour.

`execute_convert` keeps its existing output-capacity logic but
sources the virtual size from `output_vsize_override.unwrap_or(
input_virtual_size)`, and writes `window_start`/`window_end` at
`OPERATION_CONFIG_ADDR + 400/+408` when `window` is `Some`
(else writes zeros).

### Shared helpers to extract (small, clean)

- `parse_output_format(s: &str) -> Result<u32, _>` — the existing
  inline match in `run_convert` (≈ 8623–8636: `raw`→1, `qcow2`→2,
  `vmdk`→3, `vpc`→5, `vhdx`→6). Both callers use it; `run_dd`
  defaults to `raw` (1) when `-O` is absent.
- `compute_output_capacity(target_format: u32, vsize: u64) -> u64`
  — the existing inline format-specific headroom calc
  (≈ 8866–8891: VHDX round-up + 10 MB; structured + 1% + 10 MB;
  raw = vsize). `dd` calls it with `out_vsize`.

### Operand parsing

Add a pure function (host-only, in `src/vmm/src/main.rs` so it can
carry inline unit tests — the `vmm` crate is in
`cargo test --workspace`; see the existing `create_option_tests`
module ≈ line 13875):

```
struct DdParsed {
    input: String,
    output: String,
    bs: u64,             // default 512
    count: Option<u64>,  // in blocks
    skip: u64,           // in blocks, default 0
    input_format: Option<String>,
    output_format: Option<String>,
}
fn parse_dd_operands(operands: &[String], input_format: Option<String>,
                     output_format: Option<String>) -> Result<DdParsed, String>
```

Rules (upstream qemu-img dd parity — see master plan Design
overview matrix):

- Each operand token is split on the **first** `=`. A token with
  no `=`, or a key not in {`if`,`of`,`bs`,`count`,`skip`}, is an
  error ("unrecognized operand `<tok>`"). Last value wins on a
  repeated key (qemu re-parses).
- `bs`/`count`/`skip` values parse via the existing
  `parse_qemu_img_size` (`src/vmm/src/main.rs:475`; 1024-based
  `K/M/G/T/P/E`, bare numbers OK, case-insensitive). `count`/`skip`
  are block counts; `bs` is bytes.
- `bs` **range 1..=INT_MAX** (2147483647). `bs=0` is an error (do
  **not** default to 512). `bs` absent ⇒ 512.
- `skip` absent ⇒ 0. `count` absent ⇒ `None` (whole image).
- `if=` and `of=` are **both mandatory**; either missing is an
  error.
- All parse/validation errors propagate as a non-zero process
  exit (return `Err` from `run_dd`, which `main` turns into a
  non-zero exit, matching the sibling `run_*` convention).

Fidelity note: `parse_qemu_img_size` also treats a trailing `b`
as 512, which upstream `qemu_strtosz` does not. This is a minor
divergence; flag it for the phase-6 differential tests rather than
forking the parser now.

### Window math (pure, unit-testable)

Add a pure function so the math is tested without launching a VM:

```
struct DdWindow { start: u64, end: u64, out_vsize: u64 }
fn compute_dd_window(virtual_size: u64, bs: u64,
                     count: Option<u64>, skip: u64) -> DdWindow
```

Exact upstream semantics (do **not** add bounds-rejection — out-of
-range windows yield empty output, not errors):

- `copy_len = match count { Some(c) => c.saturating_mul(bs).min(virtual_size),
  None => virtual_size }` — `count` clamps **down only**; overflow
  saturates then clamps to `virtual_size`.
- `start = skip.saturating_mul(bs)` (`window_start`).
- `end = copy_len` (`window_end`, an absolute virtual offset).
- `out_vsize = end.saturating_sub(start)`.
- **skip past EOF** ⇒ `start >= end` ⇒ `out_vsize == 0` ⇒ empty
  output, **exit 0** (qemu warns and still succeeds). The guest
  loop `[start, end)` naturally copies nothing when `start >= end`.
- `count == 0` ⇒ `copy_len == 0` ⇒ `end == 0` ⇒ `out_vsize == 0`
  ⇒ empty output.

`run_dd` always sets `FLAG_DD_WINDOW` and writes `start`/`end`
(even for whole-image, where `start=0`, `end=virtual_size`), so the
guest path is uniform and the `count==0`/empty cases are
expressible (this is why the gate flag exists — see phase 1).

### Dense output

`dd` is always dense. `run_dd` builds `flags` **without**
`FLAG_SKIP_ZEROS`; convert's zero-skipping branch
(`src/operations/convert/src/main.rs` ≈ 1596) is thus inactive. No
new flag needed.

### `-f` input format (scoped)

Upstream `-f` forces the input format. instar's
`discover_backing_chain` auto-detects. The implementer should wire
`-f` to force/hint the input format **if** the discovery/open path
accepts a format hint; if it does not, treat `-f` as
accepted-and-validated with auto-detection still authoritative, and
record forcing as a small follow-up (this matches the master
plan's deferral posture and keeps phase 2 focused). Resolve by
reading `discover_backing_chain` / `BackingStore::open`; note the
outcome in the phase's verification.

### Output creation

`run_dd` creates/sizes the output via the same `BackingStore::open`
path `execute_convert` uses, with capacity =
`compute_output_capacity(target_format, out_vsize)`. For
`out_vsize == 0`, an empty output must still be created (the
skip-past-EOF / count=0 cases).

### Intermediate-state caveat (record in code + commit message)

Until phase 3 teaches the guest to honour the window, `dd` with a
non-trivial `skip`/`count` produces a whole-image dense copy
truncated to the host-sized output — i.e. **incorrect** for
windowed cases. This is acceptable only because the branch is not
shipped between phases and phase 3 immediately follows. Do not add
skip/count **integration** tests in this phase (they would fail);
they arrive with phase 3. A brief code comment on `run_dd` should
note the window is host-computed but guest-honoured as of phase 3.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | worktree | Refactor: extract the post-arg-parse body of `run_convert` (`src/vmm/src/main.rs` ≈ 8621–9456) into `fn execute_convert(exec: ConvertExecution, verbose: bool) -> Result<(), Box<dyn std::error::Error>>`, plus the `ConvertExecution` struct (fields per the Design section). Source the virtual size from `exec.output_vsize_override.unwrap_or(input_virtual_size)` for output capacity; write `window_start`/`window_end` at `OPERATION_CONFIG_ADDR + 400/+408` (zeros when `exec.window` is `None`); OR `FLAG_DD_WINDOW` into the flags only when `window` is `Some`. Rewrite `run_convert` to parse `ConvertArgs`, build `ConvertExecution { window: None, output_vsize_override: None }`, and call `execute_convert`. BEHAVIOUR MUST BE IDENTICAL for convert. Verify with `make test-integration` convert tests + a manual convert round-trip. Worktree isolation because this rewrites a hot function. |
| 2b | medium | sonnet | none | Extract `parse_output_format(&str) -> Result<u32, _>` and `compute_output_capacity(target_format: u32, vsize: u64) -> u64` from the now-`execute_convert` code (the inline match ≈ old 8623–8636 and the capacity calc ≈ old 8866–8891). Have `execute_convert` call them. Pure refactor; no behaviour change. (May be folded into 2a if the same agent does both.) |
| 2c | medium | sonnet | none | Add `parse_dd_operands` and `compute_dd_window` (signatures + rules per Design) to `src/vmm/src/main.rs`. Reuse `parse_qemu_img_size` (line 475) for `bs`/`count`/`skip`; enforce `bs` range 1..=2147483647 (reject 0); require `if=`/`of=`; reject unknown/`=`-less operands; last-value-wins. `compute_dd_window` must implement the saturating count-clamp-only + skip-subtract + empty-on-overrun semantics with NO bounds rejection. Both functions pure (no I/O), so they are unit-testable. |
| 2d | high | opus | none | Implement `run_dd` (replace the phase-1 stub): call `parse_dd_operands`; `discover_backing_chain(Path::new(&parsed.input), sector_size, &security_config)` to get `chain.images()[0].virtual_size`; `compute_dd_window(...)`; resolve `-O` via `parse_output_format` (default `raw`/1); build `ConvertExecution` with `window: Some((start,end))`, `output_vsize_override: Some(out_vsize)`, `flags = DD_WINDOW` (and NOT `SKIP_ZEROS`); call `execute_convert`. Handle `-f` per the scoped Design note. Add the intermediate-state code comment. Pick a sensible default `sector_size`/`progress` (mirror convert's defaults). |
| 2e | medium | sonnet | none | Add `#[cfg(test)] mod dd_operand_tests` in `src/vmm/src/main.rs` (mirror `create_option_tests`). Cover: if/of parsing; missing if or of → err; unknown operand → err; `=`-less token → err; `bs=0` → err; `bs` default 512; size suffixes (`bs=1M`, `count=4`); `count` clamp-down (count beyond EOF ⇒ end=virtual_size); `count=0` ⇒ out_vsize 0; `skip` within ⇒ out_vsize = end-start; `skip` past EOF ⇒ out_vsize 0; overflow saturation. Assert on `DdParsed` and `DdWindow` values. |
| 2f | medium | sonnet | none | Integration smoke test: add `run_instar_dd` and `run_qemu_img_dd` helpers to `tests/base.py` (mirror the convert helpers), and `tests/test_dd.py` with ONE test: whole-image `-O raw` (`dd if=IN of=OUT`) byte-identical to `qemu-img dd if=IN of=OUT` on a small qcow2 and a small raw input. Do NOT add skip/count/format tests (phase 3/4). Confirm it passes via `make test-integration`. |

Per the master plan / `PLAN-TEMPLATE.md`, sub-agents implement and
the management session reviews the actual files before each
commit. Suggested commits: 2a(+2b) the refactor (one logical
change, convert unchanged); 2c+2d+2e the dd host implementation +
unit tests; 2f the integration smoke test. The refactor must land
and be verified green before the dd work builds on it.

## Verification

- [ ] `convert` is byte-for-byte unchanged: full convert
      integration suite green; a manual `convert` round-trip
      matches pre-refactor output.
- [ ] `parse_dd_operands` / `compute_dd_window` unit tests pass
      and cover the matrix rows above (`make test-rust`).
- [ ] `instar dd if=IN of=OUT` (whole image, raw) is byte-identical
      to `qemu-img dd` (the phase 2f test).
- [ ] `instar dd` error cases exit non-zero: missing `if`/`of`,
      `bs=0`, unknown operand, bad size string.
- [ ] `make instar` builds; `make lint` clean;
      `make check-binary-sizes` passes (guest unchanged).
- [ ] `pre-commit run --all-files` passes.
- [ ] `-f` behaviour documented (forced vs detected) per the
      Design note.
- [ ] No `convert.bin` / guest source changes in this phase.
- [ ] Commit messages follow conventions (model/context/effort in
      `Co-Authored-By`); the dd commit notes the windowed-cases
      intermediate state.

## Hand-off to phase 3

Phase 3 ([PLAN-dd-phase-03-guest-raw.md](/components/instar/plans/PLAN-dd-phase-03-guest-raw/),
to be written) makes `convert.bin` honour `FLAG_DD_WINDOW`: iterate
the copy loop over `[window_start, window_end)` instead of
`[0, virtual_size)`, address output at `virtual_offset -
window_start`, and resolve the sub-sector window-alignment question
flagged in phase 1 (when `window_start`/`window_end` are not
multiples of the I/O granularity). Phase 3 then adds the
skip/count/partial-block/empty-output **integration** tests
(matrix rows 1–9) against `qemu-img dd` for `-O raw`. The host
side — parser, window math, output sizing, dense flag, `run_dd`
delegating to `execute_convert` — is complete as of phase 2 and
needs no further change for raw.
