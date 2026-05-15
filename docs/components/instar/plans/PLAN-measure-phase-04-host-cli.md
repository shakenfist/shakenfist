# Phase 4: host VMM subcommand and output formatting

Master plan: [PLAN-measure.md](/components/instar/plans/PLAN-measure/) ·
Previous phase: [PLAN-measure-phase-03-guest-op.md](/components/instar/plans/PLAN-measure-phase-03-guest-op/)

## Status: Not started

## Mission

Wire the `Commands::Measure` clap variant, the `run_measure()`
host function, and the output formatters (human + JSON) into
`src/vmm/src/main.rs`. After phase 4 the user can run:

```
instar measure [--size SIZE | FILENAME] -O <fmt> [--output {human,json}]
              [--cluster-size N] [--refcount-bits N] [--extended-l2]
              [--subformat S] [--grain-size N] [--block-size N]
```

and get qemu-img-byte-identical output (where qemu-img also
supports the target). Phase 4 is single-source-device only;
backing-chain composition and `-o key=value,...` parsing stay
out of scope (the `-o` shorthand is phase 5; chain is master-plan
follow-up).

Phase 4 also adds a tight smoke-test suite in `tests/test_measure.py`
that catches obvious regressions. The full cross-version
baseline-pinned tests live in phase 7 once phase 6 has generated
the baselines.

## Why this is its own phase

Phase 3 finished the boundary plumbing but left no way for a
user to invoke it. Phase 4 is purely host-side glue: clap args,
config serialization, output formatting, and CLI dispatch. None
of phase 4's code runs inside the guest, and none of it changes
the call-table ABI. This is the right granularity to ship before
the heavier phase 5 (`-o` parsing), phase 6 (baselines), and
phase 7 (integration tests) build on top of it.

The phase is split into 5 steps so each commit is small and
self-contained: shadow constants + arg surface + stub dispatch,
then printers, then the heavy `run_measure` body, then Python
smoke tests, then docs.

## Architecture

### Shadow magic/flag constants

The existing `src/vmm/src/main.rs` shadows every `*Config` /
`*Result` magic and flag as plain const declarations at the top
of the file so the host can write them into guest memory without
pulling in the no_std crate's traits. Phase 4 adds the same for
measure (matching the values introduced in phase 3a):

```rust
const MEASURE_CONFIG_MAGIC: u32 = 0x4D454153;          // "MEAS"
const MEASURE_CONFIG_FLAG_EXTENDED_L2: u32 = 1 << 0;
const MEASURE_CONFIG_FLAG_LAZY_REFCOUNTS: u32 = 1 << 1;
const MEASURE_CONFIG_FLAG_COMPAT_V3: u32 = 1 << 2;
const MEASURE_CONFIG_FLAG_COMPRESS: u32 = 1 << 3;
const MEASURE_CONFIG_PREALLOC_MASK: u32 = 0b11 << 4;
const MEASURE_CONFIG_PREALLOC_OFF: u32 = 0 << 4;
const MEASURE_CONFIG_PREALLOC_METADATA: u32 = 1 << 4;
const MEASURE_CONFIG_PREALLOC_FALLOC: u32 = 2 << 4;
const MEASURE_CONFIG_PREALLOC_FULL: u32 = 3 << 4;

const MEASURE_RESULT_MAGIC: u32 = 0x4D524553;          // "MRES"
const MEASURE_RESULT_ERROR_OK: u32 = 0;
const MEASURE_RESULT_ERROR_OVERFLOW: u32 = 1;
const MEASURE_RESULT_ERROR_INVALID_OPTION: u32 = 2;
const MEASURE_RESULT_ERROR_INVALID_SIZE: u32 = 3;
```

Place these next to the existing `CONVERT_CONFIG_*` constants
(around line 105 in main.rs as of phase 3's last commit).

### CLI surface

```rust
#[derive(Args, Debug)]
struct MeasureArgs {
    /// Source image file. Mutually exclusive with --size.
    #[arg(conflicts_with = "size")]
    input: Option<String>,

    /// Compute the measure for a hypothetical empty image of this size.
    /// Mutually exclusive with FILENAME.
    /// Accepts suffixes K, M, G, T (parsed by parse_memory_size).
    #[arg(long, short = 's', value_name = "SIZE", conflicts_with = "input")]
    size: Option<String>,

    /// Target output format. Supported: raw, qcow2, vmdk, vpc (VHD), vhdx.
    /// Default: raw (matching qemu-img).
    #[arg(short = 'O', long = "target-format", default_value = "raw")]
    target_format: String,

    /// Source format override (rare; usually auto-detected).
    /// Accepted for parity with qemu-img -f.
    #[arg(short = 'f', long = "format")]
    source_format: Option<String>,

    /// Output format: human (default) or json.
    #[arg(long, default_value = "human", value_parser = ["human", "json"])]
    output: String,

    /// Sector size for source I/O. Default: 65536.
    #[arg(long, default_value = "65536")]
    sector_size: u32,

    // --- per-target qcow2 options ---
    /// qcow2 cluster size in bytes. Power of two in [512, 2 MiB].
    /// Default (when -O qcow2): 65536.
    #[arg(long, default_value = "0")]
    cluster_size: u32,

    /// qcow2 refcount entry width in bits. Must be in {1,2,4,8,16,32,64}.
    /// Default (when -O qcow2): 16.
    #[arg(long, default_value = "0")]
    refcount_bits: u8,

    /// qcow2 extended L2 entries (16-byte with subcluster bitmaps).
    #[arg(long)]
    extended_l2: bool,

    /// qcow2 lazy refcounts. Accepted but does not affect required size.
    #[arg(long)]
    lazy_refcounts: bool,

    /// qcow2 compat level: "0.10" (v2) or "1.1" (v3, default).
    #[arg(long, default_value = "1.1", value_parser = ["0.10", "1.1"])]
    compat: String,

    /// qcow2 compression flag (does not change required; accepted for parity).
    #[arg(long)]
    compress: bool,

    /// qcow2 preallocation mode.
    #[arg(long, default_value = "off",
          value_parser = ["off", "metadata", "falloc", "full"])]
    preallocation: String,

    // --- per-target vmdk options ---
    /// vmdk subformat. Default (when -O vmdk): monolithicSparse.
    #[arg(long, default_value = "",
          value_parser = ["", "monolithicSparse", "streamOptimized",
                          "monolithicFlat"])]
    subformat: String,

    /// vmdk grain size in bytes. Power of two in [4 KiB, 64 KiB].
    /// Default (when -O vmdk): 65536.
    #[arg(long, default_value = "0")]
    grain_size: u32,

    // --- per-target vhd / vhdx options ---
    /// vhd / vhdx block size in bytes. Power of two; vhd: [512 KiB, 2 GiB],
    /// vhdx: [1 MiB, 256 MiB]. Default (when -O vpc): 2 MiB; default (when -O vhdx): 32 MiB.
    #[arg(long, default_value = "0")]
    block_size: u32,
}
```

Notes on the arg shape:
- `FILENAME` is positional and optional because `--size` provides
  an alternative path. Clap's `conflicts_with` enforces mutual
  exclusion. Exactly one of the two must be present; validate at
  the top of `run_measure`.
- `-O target-format` accepts `raw`, `qcow2`, `vmdk`, `vpc`,
  `vhdx`. `vpc` is qemu-img's name for VHD; map to
  `ImageFormat::Vhd` for the wire encoding.
- `-f source-format` is accepted but only validated against the
  parser's detection result if both are present and disagree —
  the simple route is to ignore `-f` entirely in phase 4 and add
  a warning in phase 5 / 10. Document the limitation.
- All per-target `--<option>` flags default to `0` / `""` to
  mean "use the format default". The guest binary substitutes
  defaults from `Qcow2Opts::default()` etc. (phase 1's
  Default impls).
- No `-o key=value,...` parser in phase 4. Each option is its
  own clap flag. Phase 5 will add `-o` on top: it parses the
  comma-separated string and sets the same fields.

### Output formatting

qemu-img-byte-identical:

**Human (`--output human`)**:
```
required size: 327680
fully allocated size: 1376256
```
Two lines, lowercase, single space between words, trailing newline.

**JSON (`--output json`)**:
```
{
    "required": 327680,
    "fully-allocated": 1376256
}
```
Four-space indent (matches `qemu-img`'s `--output=json`). Key
`fully-allocated` is hyphenated even though JSON allows
underscores — match qemu-img exactly. Trailing newline.

For target formats that qemu-img *doesn't* support (vmdk, vpc,
vhdx), instar produces the same format anyway — these are
instar-only outputs (documented in `docs/quirks.md`).

For the `error != 0` case, the host emits a clear error to
stderr and sets the exit code to 1. Error messages:
- `ERROR_OVERFLOW`: "measure: overflow computing target size"
- `ERROR_INVALID_OPTION`: "measure: invalid option for target format"
- `ERROR_INVALID_SIZE`: "measure: source image is unsupported format"

### `run_measure` algorithm

Mirroring `run_check` / `run_info` structure:

1. **Validate args**:
   - Exactly one of `--size` or `FILENAME` is set.
   - `sector_size` is power of two in [512, MAX_SECTOR_SIZE].
   - `target_format` maps to a supported `ImageFormat` (raw,
     qcow2, vmdk, vhd, vhdx).
   - Format-specific option validation (cluster_size power of
     two, etc.) is best left to the guest, but obvious
     local-validatable errors (e.g. `--cluster-size 1000` not
     power of two, `--refcount-bits 7`) get an early host-side
     check so the user gets feedback before VMM startup.

2. **Resolve target format and options**:
   - `target_format` string → numeric `ImageFormat`.
   - Translate `--compat 0.10|1.1` → `FLAG_COMPAT_V3` bit.
   - Translate `--preallocation off|metadata|falloc|full` →
     `PREALLOC_*` bits.
   - Translate `--subformat monolithicSparse|streamOptimized|
     monolithicFlat` → `vmdk_subformat: u8`.
   - Translate (currently absent) VHD subformat default to 0
     (Dynamic); a `--subformat fixed` flag could be added later
     but is not in scope for phase 4 (qemu-img doesn't support
     vhd measure anyway).

3. **Reject VMDK monolithicFlat sources**: same logic the
   convert path uses (`peek_is_vmdk_descriptor`). monolithicFlat
   sources require multi-device chain setup, which phase 4
   doesn't support. Surface a clear error:
   "measure: monolithicFlat source images are not yet supported
   (use convert -f / qemu-img instead)".

4. **Load binaries**: `core.bin` and `measure.bin` via
   `get_binary_path()` and `load_guest_binary()` (existing
   helpers).

5. **Build MeasureConfig** in a `[u8; sizeof::<MeasureConfig>()]`
   buffer using `guest_mem.write_obj(...)` at the right offsets
   (matching the field layout in `shared::MeasureConfig`). This
   is straight-line code mirroring how `run_check` writes
   `CheckConfig` at lines 3430-3431 — but with more fields.

   For `--size` mode: `virtual_size_override = parse_memory_size(s)`.
   For source-image mode: `virtual_size_override = 0`.

6. **Set up the source device**:
   - `--size` mode: no source device needed. Pass a stub
     read-only device backed by an empty file? Actually the
     guest binary handles the override path without ever
     calling `read_input_sector`. Still need *some* device 0
     because the call table's `get_input_capacity(0)` will be
     called during validation. Decision: open `/dev/null` (or
     a 1-byte tmpfile) as device 0. The guest checks
     `virtual_size_override` before touching the device.

     Alternative cleaner approach: set `device_count = 0` in
     the device set and never construct the device. But the
     guest's `core` boot path may expect at least one device.
     Confirm during step 4c by reading
     `src/core/src/main.rs`; the safe answer (always present
     device 0 backed by a tiny tmpfile) avoids edge cases.

   - Source-image mode: open the file as a `BackingStore` and
     wrap in a `VirtioBlockDevice`, single device only. No
     chain.

7. **Run the VM**: existing `run_vmm()` pattern. The main loop
   already decodes `GuestMessage` payloads in the IoOut handler.
   Step 4c adds a new arm matching
   `Some(guest_::GuestMessage_::Payload::MeasureResult(_))` that:
   - Stores the result for printing after the VM exits, **or**
   - Prints immediately and tracks exit code.

   The check operation uses the immediate-print pattern (line
   3590-3609). Match that.

8. **Print result + return exit code**:
   - On `error == ERROR_OK`: call `print_measure_result(msg,
     args.output)` and return `Ok(())`.
   - On non-zero error: print stderr message, return
     `Err(...)`.

### IoOut handler arm

The existing match in the main event loop has arms for
`InfoResult`, `CheckResult`, `CompareResult`. Step 4c adds:

```rust
Some(guest_::GuestMessage_::Payload::MeasureResult(m)) => {
    print_measure_result(&msg, &args.output);
    measure_error = m.error;
}
```

where `measure_error` is tracked outside the loop to influence
the final exit code. The pattern is the same as
`check_passed` (line 3595).

### Smoke tests (`tests/test_measure.py`)

The full test suite ships in phase 7 with cross-version
baselines from phase 6. Phase 4 ships ~6 narrow smoke tests
that confirm the CLI is wired and outputs the right shape:

```python
class TestMeasureSmoke(InstarTestBase):
    def test_size_raw_human(self):
        # instar measure --size 1M -O raw
        # Expect "required size: 1048576\nfully allocated size: 1048576\n"
        ...

    def test_size_raw_json(self):
        # instar measure --size 1M -O raw --output json
        # Expect {"required": 1048576, "fully-allocated": 1048576}
        ...

    def test_size_qcow2_default(self):
        # instar measure --size 1M -O qcow2
        # Expect the values pinned in crates/measure phase 1 fixtures:
        # required=327680, fully-allocated=1376256
        ...

    def test_size_qcow2_with_cluster(self):
        # instar measure --size 1M -O qcow2 --cluster-size 512
        # Expect required=22528, fully-allocated=1071104
        ...

    def test_source_image_qcow2(self):
        # instar measure <some-qcow2-test-image> -O qcow2
        # Expect numeric output (don't pin exact values — phase 7).
        ...

    def test_conflicting_args_rejected(self):
        # instar measure --size 1M somefile.qcow2 -O raw
        # Expect non-zero exit, clear error
        ...

    def test_invalid_cluster_size(self):
        # instar measure --size 1M -O qcow2 --cluster-size 1000
        # Expect non-zero exit
        ...
```

These run as part of `make test-integration` and `make test`.
Pinning `--size 1M -O qcow2` against literal numbers couples
phase 4 to qemu-img 10.x output exactly — which is also what
phase 1's fixture table uses, so a regression there cascades
to phase 4. That coupling is intentional: a CLI break should
not silently pass.

## Open questions

1. **`--size 0`**: qemu-img errors with "Image size must be
   non-negative" actually accepts size=0 and returns
   `required=0, fully-allocated=0`. Recommendation: match the
   actual qemu-img behaviour (accept). Confirm with a test.

2. **`-f` source-format override**: phase 4 accepts the flag
   for parity but ignores it (parsers auto-detect). Should we
   warn if both `-f vmdk` and the detected format disagree, or
   silently ignore? Recommendation: silently ignore in phase 4;
   add a verbose-only warning in phase 5 if any user actually
   notices. qemu-img has the same flag for parity with `info`,
   but for measure it's also effectively unused.

3. **VHD subformat selection**: qemu-img doesn't support VHD
   measure, so there's no compatibility constraint. Phase 4
   defaults to Dynamic (matching convert's default) and does
   not expose a `fixed` flag. If users want fixed-VHD
   measurement, add `--subformat fixed` in a follow-up.

4. **`vpc` vs `vhd` target name**: qemu-img uses `vpc`
   exclusively; instar `convert` accepts `vpc` as the alias.
   Phase 4 accepts `vpc` and maps to `ImageFormat::Vhd`. No
   alternative spelling.

5. **Source-image mode with very large source (1 TiB+ qcow2)**:
   the scanner walks every L1 entry. Performance-wise the
   metadata is ~2 MB for a 1 TB image — handful of seconds.
   No special handling. If it becomes an issue, optimise the
   scan_allocation path later.

6. **`--size` mode with no device**: confirm `core.bin`'s
   boot path tolerates `device_count = 0`. If not, fall back
   to opening a tiny tmpfile. Decide during step 4c.

7. **Format-specific options leaking across targets**: e.g.
   the user passes `--cluster-size 64k` with `-O vmdk`. Clap
   accepts it (no rejection wired); the host could either
   silently ignore or error. Recommendation: silently ignore
   in phase 4 (the qcow2-only option just falls into the
   per-target dispatch and never gets used). Phase 5's `-o`
   parser may want to be stricter.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Add the shadow magic/flag constants (`MEASURE_CONFIG_MAGIC`, `MEASURE_CONFIG_FLAG_*`, `MEASURE_CONFIG_PREALLOC_*`, `MEASURE_RESULT_*`) at the top of `src/vmm/src/main.rs` next to the existing per-op constants. Add `MeasureArgs` struct mirroring the schema above. Add `Commands::Measure(MeasureArgs)` to the `enum Commands`. Add the dispatch arm in `main()` that calls `run_measure(args, verbose)`. Add a stub `fn run_measure(args: MeasureArgs, verbose: bool) -> Result<(), Box<dyn std::error::Error>>` that returns `Err("measure: not yet implemented".into())`. Run `make lint`, `make test-rust`, `pre-commit run --all-files`. Only `src/vmm/src/main.rs` modified. |
| 4b | medium | sonnet | none | Add `fn print_measure_result(msg, output)` and `fn print_measure_result_json(result)` to `src/vmm/src/main.rs`, mirroring the `print_check_result` / `print_check_result_json` pattern. JSON format MUST match qemu-img byte-for-byte: four-space indent, `fully-allocated` key (hyphen), no trailing comma. Human format: two lines, `required size: N` and `fully allocated size: N`. Both formatters take `&guest_::GuestMessage` to match the existing pattern. Run lint / test / pre-commit. Touch only `src/vmm/src/main.rs`. |
| 4c | high | opus | none | Implement the full `run_measure` body. Flow: validate args (exactly one of `--size` or `FILENAME`; sector_size power of two; target_format maps to a supported ImageFormat); resolve target format and options into a MeasureConfig; reject VMDK monolithicFlat sources with a clear error (use the existing `peek_is_vmdk_descriptor` helper); load `core.bin` + `measure.bin`; build the MeasureConfig and write it field-by-field at OPERATION_CONFIG_ADDR using `guest_mem.write_obj` (mirror the existing per-op patterns at `run_check` line 3430+ and `run_convert` for the more elaborate option writing); set up source device(s) — single source for FILENAME mode, a stub for --size mode (open `/dev/null` opened read-only is one option; the guest binary won't read it because `virtual_size_override` short-circuits the scan path, but the core boot path may still call `get_input_capacity(0)` so device 0 must exist); run the VM via the existing run_vmm-style event loop pattern (look at run_check around line 3580-3630 for the IoOut handler usage); add a new `Some(guest_::GuestMessage_::Payload::MeasureResult(m))` arm to that IoOut match that calls `print_measure_result(&msg, &args.output)` and tracks `measure_error = m.error`; after the loop returns, if `measure_error != MEASURE_RESULT_ERROR_OK` emit a clear stderr message and return `Err(...)`. **High effort because:** this function ties together everything from phases 1-3 on the host side. Multiple subtle interactions: clap arg validation, MeasureConfig field layout (must match `shared::MeasureConfig` byte-for-byte — refer to the field offsets), VM lifecycle, IoOut decoding. Read `run_check` carefully as the template; deviate only where measure's needs differ. |
| 4d | medium | sonnet | none | Add `tests/test_measure.py` with a `TestMeasureSmoke(InstarTestBase)` class containing ~7 tests: `--size 1M -O raw` human/json, `--size 1M -O qcow2` default (assert required=327680, fully-allocated=1376256), `--size 1M -O qcow2 --cluster-size 512` (assert required=22528, fully-allocated=1071104 — values from phase 1 fixture rows), source-image happy-path using one of the existing safe-tier qcow2 test images (don't pin exact numbers; just assert the JSON parses and required <= fully-allocated), conflicting `--size 1M FILENAME` exits non-zero, invalid `--cluster-size 1000` exits non-zero. Use the existing `InstarTestBase` helpers (`self.run_instar(...)` etc.; if a helper for measure doesn't exist, follow the pattern from `test_check_formats.py` or `test_compare.py`). Run `make test-integration` if reachable, or document the command for the user to run. Touch only `tests/test_measure.py` (new file). |
| 4e | low | sonnet | none | Update `ARCHITECTURE.md` to mention the new `measure` subcommand (one paragraph mirroring how `convert` is described — CLI surface, output formats, divergences from qemu-img where qemu-img doesn't support a target). Add an `### Added` entry to `CHANGELOG.md` Unreleased noting the new `instar measure` CLI surface (cite the phase plan). Cross-reference `docs/quirks.md` for the future "vmdk/vhd/vhdx measure is instar-only" note (do not yet write `docs/measure.md` — that ships in phase 10 once all options are stable). Run `pre-commit run --all-files`. Touch only `ARCHITECTURE.md` and `CHANGELOG.md`. |

Total: 5 commits.

### Out of scope for phase 4

- `-o key=value,...` parser (phase 5).
- `--snapshot` / `-l SNAPSHOT` source-side snapshot extraction
  (master-plan future work).
- Backing-chain composition (master-plan future work).
- VMDK monolithicFlat source support (rejected with clear
  error; multi-device infrastructure required).
- Cross-version baseline generation (phase 6).
- Cross-version-matrix integration tests (phase 7).
- `docs/measure.md` user guide (phase 10).
- `docs/quirks.md` "subcommands beyond qemu-img" section
  (phase 10 — the divergences are simple enough that phase 4's
  ARCHITECTURE.md mention covers it for now).
- `-f` source-format override is *accepted* but does nothing
  (parser auto-detects; documented limitation).

## Success criteria

- `instar measure --size 1M -O qcow2` produces qemu-img-byte-
  identical output for the `--output=json` case (matching the
  values phase 1 pinned in QCOW2_CASES).
- `instar measure --size 1M -O qcow2` and `-O raw` produce
  the matching human-format output.
- `instar measure <qcow2-image> -O qcow2` runs end-to-end
  and produces a valid result (numeric output, exit code 0).
- VMDK monolithicFlat source returns a clear error.
- Invalid options (cluster_size not a power of two, mutually
  exclusive `--size` + FILENAME) return non-zero exit and a
  clear error.
- `make instar` builds; `make lint` clean;
  `make test-rust` passes; `make test-integration` includes
  `test_measure.py` and passes;
  `pre-commit run --all-files` clean.
- `make check-binary-sizes` still passes (no guest binary
  changes in phase 4).
- `ARCHITECTURE.md` and `CHANGELOG.md` updated.

## Risks and mitigations

- **`MeasureConfig` field offsets disagree between host and
  guest**. Phase 3 placed the struct in `shared/src/lib.rs` so
  both sides use the same layout, but the host writes via
  `guest_mem.write_obj` at hand-specified byte offsets — drift
  is possible. Mitigation: step 4c's brief should call out
  using `std::mem::offset_of!` (stable in recent Rust) or a
  small helper that takes a `MeasureConfig` value, serialises
  it with `bytemuck::bytes_of()` if `MeasureConfig: Pod`, and
  writes the entire struct in one `write_slice` call. Cleanest:
  build a `MeasureConfig` value on the host and write it whole.
  Use whichever approach the existing operations use (some
  write individual fields, some use `write_obj` on the whole
  struct — check `run_convert`).
- **`--size` mode without a source device**: if core's boot
  path requires device 0, the VM will fail in unfamiliar ways.
  Mitigation: step 4c's brief instructs to open a tiny tmpfile
  as a stub device 0 and verify with a `--size` test in 4d.
- **Output format byte-for-byte mismatch with qemu-img**:
  whitespace, newlines, key spelling. Mitigation: smoke tests
  in 4d pin literal expected bytes for the JSON output. The
  fixture table values come from phase 1, where they were
  sourced from live `qemu-img`.
- **`vpc` vs `vhd` confusion**: users typing `-O vhd` instead
  of `-O vpc` get a clap error. Mitigation: clap arg validation
  rejects unknown names early. If we decide to be lenient,
  accept both with an alias.

## Back brief

Before executing any step, the executing agent should
back-brief: which existing operation is the closest template
(`run_check` is the simplest with chain-optional setup;
`run_convert` is the most elaborate for options writing), and
which files are being touched. The reviewer should confirm
no step bleeds into phase 5 (`-o` parsing), phase 6 (baseline
generation), or phase 7 (integration tests against real
testdata images).
