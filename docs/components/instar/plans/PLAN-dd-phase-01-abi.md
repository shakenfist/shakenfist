# PLAN-dd phase 01: ABI — ConvertConfig window fields, `FLAG_DD_WINDOW`, `dd` subcommand registration

Master plan: [PLAN-dd.md](/components/instar/plans/PLAN-dd/)

## Status: Complete (7eadd12)

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

Establish the **ABI surface and CLI registration** for `dd`, with
**zero behaviour change to `convert`** and a `dd` subcommand that
builds, is discoverable in `--help`, and cleanly reports "not yet
implemented" until phase 2 wires the operand parser.

Per the master plan's resolved open questions, `dd` **reuses the
`convert` guest binary** (`convert.bin`): the host `dd` subcommand
will build an *extended* `ConvertConfig` (magic `"CONV"`, same as
convert) carrying an input byte-window plus dense output, and
launch `convert.bin` exactly as `convert` does. There is **no new
guest binary and no new config magic**. This phase adds only the
config fields and the flag that later phases populate and honour.

Concretely, this phase ships:

1. Two new `u64` fields on `ConvertConfig` describing the input
   read window, plus a `FLAG_DD_WINDOW` flag that gates their use.
2. The mirrored flag constant on the VMM side.
3. The `Dd(DdArgs)` `Commands` variant, a minimal `DdArgs`, the
   dispatch arm, and a `run_dd` stub.

It deliberately does **not** change `convert.bin`'s copy loop
(that is phase 3 for raw, phase 4 for formats) and does **not**
parse dd operands (phase 2).

## Design

### Why extend `ConvertConfig` rather than add a `DdConfig`

The reuse decision means the guest that runs is `convert.bin`,
which reads a `ConvertConfig` (magic `0x434F4E56` `"CONV"`) from
`OPERATION_CONFIG_ADDR` (`0x81000`). A `dd` invocation is a
`convert` invocation with (a) an input byte-window and (b) dense
output. Both are expressible as additions to `ConvertConfig`;
introducing a second config struct/magic would mean a second
guest entry path, defeating the reuse. So: **one struct, one
magic, gated by a flag.**

### Current `ConvertConfig` (for reference)

`src/shared/src/lib.rs` (≈ lines 2183–2256). The struct is
**400 bytes**, last field `output_block_size` at **offset 396**.
`OPERATION_CONFIG_MAX_SIZE` is **4096** (`src/shared/src/lib.rs`
≈ line 334), so there is ~3.6 KB of headroom. Existing flags live
in the `impl ConvertConfig` block (`src/shared/src/lib.rs`
≈ lines 2262–2278) **and are duplicated** as
`CONVERT_CONFIG_FLAG_*` consts in `src/vmm/src/main.rs`
(≈ lines 112–120) — any new flag must be added in **both** places.

### New fields and flag

Append after `output_block_size` (offset 396). Offset 400 is
already 8-byte aligned (`396 + 4`), so no padding is needed:

| Offset | Field | Type | Meaning |
|--------|-------|------|---------|
| 400 | `window_start` | `u64` | Inclusive **virtual** byte offset at which the guest begins reading the input. `0` when `FLAG_DD_WINDOW` is clear. |
| 408 | `window_end` | `u64` | Exclusive **virtual** byte offset at which the guest stops reading. `0` when `FLAG_DD_WINDOW` is clear. |

New struct size: **416 bytes** (well under 4096). Doc-comment each
field with its offset, matching the house style of the LUKS/grain
fields above it.

New flag (added in **both** the shared `impl` block and the VMM
const block):

```
FLAG_DD_WINDOW = 1 << 5   // CONVERT_CONFIG_FLAG_DD_WINDOW on the VMM side
```

(`1 << 5` is the next free bit; `1 << 31` is `VERBOSE`.)

### Semantics the fields encode (contract for phases 2–4)

This phase only *defines* the fields; phases 2–4 populate/honour
them. Recording the intended contract here so the ABI is correct:

- **Host computes the window** (it already knows the input virtual
  size — `discover_backing_chain` runs host-side before launch and
  fills `ChainConfig.devices[0].virtual_size`; `convert.bin` reads
  that at `src/operations/convert/src/main.rs` ≈ line 246). For a
  `dd` invocation:
  - `copy_len  = min(virtual_size, count*bs)`   (count clamps down only; absent count ⇒ `virtual_size`)
  - `window_start = skip*bs`
  - `window_end   = copy_len`
  - `out_vsize    = saturating_sub(copy_len, window_start)` — the size the **host** creates the output file at.
- **Guest honours the window** (phases 3/4): when `FLAG_DD_WINDOW`
  is set, the copy loop iterates `virtual_offset` over
  `[window_start, window_end)` instead of `[0, virtual_size)`, and
  addresses output at `(virtual_offset - window_start)`. When the
  flag is clear, behaviour is byte-for-byte today's convert.
- **Dense output** needs no new field: convert is sparse only when
  `FLAG_SKIP_ZEROS` is set (`src/operations/convert/src/main.rs`
  ≈ line 1596: `if skip_zeros && is_all_zeros_ptr(...)`). `dd` is
  dense simply by **not** setting `FLAG_SKIP_ZEROS` — `run_dd` will
  omit it. No `FLAG_DENSE` is introduced.
- **`window_end == 0` with the flag set** means "copy nothing"
  (the `count=0` and skip-past-EOF cases) ⇒ empty output. The flag
  is what disambiguates this from convert's `0/0` "whole image"
  default, which is why the gate flag is required rather than
  inferring intent from the field values.

### Backward-compatibility / safety

- `convert` invocations never set `FLAG_DD_WINDOW`, so the guest
  ignores `window_start`/`window_end` for convert — no behaviour
  change. Guest memory is zero-initialised at VM creation, so the
  new offsets read as `0` for convert even though `run_convert`
  does not write them. (Optional nicety: have `run_convert`
  explicitly zero offsets 400/408 for defensiveness; not required
  for correctness.)
- Adding trailing fields does not move any existing field offset,
  so `run_convert`'s existing field-by-field writes
  (`src/vmm/src/main.rs` ≈ lines 8987–9146) are unaffected.

### Downstream design note (flagged for phase 3, not this phase)

`window_start = skip*bs` need not be a multiple of the convert
loop's `sector_size`/`output_sector_size` (default 65536) — e.g.
`skip=1 bs=512` ⇒ `window_start = 512`. The convert copy loop is
sector-chunked, so phase 3 must decide how to handle sub-sector
window alignment (byte-granular handling, or constraining the
effective I/O granularity for `dd` to `bs`). The byte-offset ABI
chosen here is the most general representation and imposes no
alignment assumption, so this is purely a phase-3 implementation
concern, not an ABI change.

### CLI registration

Mirror `convert`'s wiring in `src/vmm/src/main.rs`:
- `Commands` enum (`Convert(ConvertArgs)` ≈ line 2571) → add
  `Dd(DdArgs)`.
- Dispatch (the `match cli.command` ≈ line 3484) → add
  `Commands::Dd(args) => run_dd(args, verbose),`.
- `DdArgs`: for this phase, keep it minimal but shaped for the
  phase-2 parser — positional/trailing raw operands plus the two
  dash-options dd actually has. Use clap `trailing_var_arg` +
  `allow_hyphen_values` so the `name=value` operands
  (`if=`/`of=`/`bs=`/`count=`/`skip=`) survive as raw strings for
  phase 2 to parse, and surface `-f`/`-O` as `Option<String>`
  (do **not** default `-O` here; the raw default is applied in
  phase 2 so the "no `-O` ⇒ raw" rule lives with the parser).
- `run_dd(args: DdArgs, verbose: bool)` stub: return a clear
  error (e.g. `"dd: not yet implemented"`) with a non-zero exit,
  following the error-return convention of the other `run_*`
  functions. This keeps the binary buildable and the subcommand
  discoverable while phase 2 fills it in.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | In `src/shared/src/lib.rs`, extend `struct ConvertConfig` (≈ lines 2183–2256): append two fields after `output_block_size` (offset 396) — `pub window_start: u64,` (offset 400) and `pub window_end: u64,` (offset 408) — each with an offset doc-comment matching the style of the LUKS/grain fields above. In the `impl ConvertConfig` flags block (≈ lines 2262–2278) add `pub const FLAG_DD_WINDOW: u32 = 1 << 5;` with a doc comment explaining it gates `window_start`/`window_end` and that `dd` is the only setter. Do not touch any existing field or offset. Confirm new total size is 416 and still < `OPERATION_CONFIG_MAX_SIZE` (4096). |
| 1b | low | haiku | none | In `src/vmm/src/main.rs` (≈ lines 112–120, the `CONVERT_CONFIG_FLAG_*` consts), add `const CONVERT_CONFIG_FLAG_DD_WINDOW: u32 = 1 << 5;` mirroring the shared-crate constant, with a one-line comment. This is the duplicated host-side copy of the flag; value must match step 1a exactly. |
| 2a | medium | sonnet | none | In `src/vmm/src/main.rs`, register the `dd` subcommand mirroring `convert`. (a) Add a `Dd(DdArgs)` variant to the `Commands` enum (next to `Convert(ConvertArgs)`, ≈ line 2571) with a `/// dd-style block copy (qemu-img dd compatible)` doc line. (b) Define `struct DdArgs` deriving clap `Args`: a `#[arg(trailing_var_arg = true, allow_hyphen_values = true)] operands: Vec<String>` to capture `if=/of=/bs=/count=/skip=` raw, plus `#[arg(short = 'f', long = "input-format")] input_format: Option<String>` and `#[arg(short = 'O', long = "output-format")] output_format: Option<String>` (NO default — the raw default is applied by the phase-2 parser). (c) Add the dispatch arm `Commands::Dd(args) => run_dd(args, verbose),` in the `match cli.command` block (≈ line 3484). (d) Add `fn run_dd(_args: DdArgs, _verbose: bool) -> Result<(), String>` (match the actual signature/return type used by `run_convert`) returning `Err("dd: not yet implemented".into())`. Do not implement operand parsing — that is phase 2. Verify `cargo build` and `instar dd --help` both work. |
| 3a | low | sonnet | none | Flip the phase-1 row of the master-plan Execution table in `docs/plans/PLAN-dd.md` to link this file, and confirm `docs/plans/index.md` needs no change yet (status stays *Not started* until implementation lands). Mechanical doc edit only. |

All steps are additive and low-risk; `isolation: none` is
appropriate. Per the master plan / `PLAN-TEMPLATE.md`, work is
done by sub-agents and reviewed in the management session before
commit; one commit per logical change (steps 1a+1b+2a form the ABI
change and may be a single commit, since the flag is meaningless
split across crates; step 3a is a doc touch-up).

## Verification

After the sub-agent work, the management session confirms:

- [ ] `ConvertConfig` has `window_start`@400 and `window_end`@408;
      no existing field offset moved; struct size 416 < 4096.
- [ ] `FLAG_DD_WINDOW = 1 << 5` exists in **both**
      `src/shared/src/lib.rs` and `src/vmm/src/main.rs` with equal
      values.
- [ ] `instar dd --help` lists the subcommand; `instar dd if=a of=b`
      exits non-zero with "not yet implemented" (no panic, no VM
      launch).
- [ ] `convert` is unchanged: `make test-integration` convert
      tests pass; a convert round-trip is byte-identical to before.
- [ ] `make instar` builds; `make lint` clean;
      `make check-binary-sizes` passes (guest unchanged, so guest
      binary sizes are identical).
- [ ] `make test-rust` passes.
- [ ] `pre-commit run --all-files` passes.
- [ ] Commit message follows project conventions (model, context
      window, effort level in the `Co-Authored-By` line).

## Hand-off to phase 2

Phase 2 ([PLAN-dd-phase-02-host-operands.md](/components/instar/plans/PLAN-dd-phase-02-host-operands/),
to be written) inherits: a `DdArgs.operands: Vec<String>` of raw
`name=value` tokens, `input_format`/`output_format` options, the
`window_start`/`window_end` fields + `FLAG_DD_WINDOW` to populate,
and the host-side knowledge that `virtual_size` is available from
the discovered backing chain for computing `copy_len`/`out_vsize`
and sizing the output file. The guest still ignores the window
until phase 3, so phase 2's correctness is covered by operand
**parser unit tests**, not end-to-end runs.
