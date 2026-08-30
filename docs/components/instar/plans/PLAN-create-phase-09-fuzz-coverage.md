# Phase 9: coverage-guided fuzzing for `crates/create/` emitters

Master plan: [PLAN-create.md](/components/instar/plans/PLAN-create/) ·
Previous phase: [PLAN-create-phase-08-integration-tests.md](/components/instar/plans/PLAN-create-phase-08-integration-tests/)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the
`src/fuzz/fuzz_targets/` harness shape, how
`fuzz_measure_calc.rs` decodes structured input into a tuple
and dispatches per format, how `instar_fuzz::set_fuzz_input`
+ `build_call_table` back the parser scanners, how
`crates/create/`'s `plan_*` functions return a `MetadataPlan`
that bundles `(byte_offset, bytes)` writes), and ground answers
in what the code does today. Where a question touches on
external concepts (libFuzzer corpus management, the cargo-fuzz
nightly toolchain, the existing `coverage-fuzz.yml` matrix
shape), research as needed. Flag uncertainty explicitly rather
than guessing.

## Status: Not started

## Mission

Stand up a new `cargo-fuzz` (libFuzzer) target,
`src/fuzz/fuzz_targets/fuzz_create_emitters.rs`, that exercises
every public `plan_*` function in `crates/create/`:

- `plan_qcow2`
- `plan_vmdk`
- `plan_vhd` (dynamic + fixed subformats, selected via `opts`)
- `plan_vhdx`

The harness decodes fuzzer bytes into a `(format_selector,
virtual_size, options_packed, backing)` tuple, calls the
matching planner, and asserts a set of structural invariants
on every `Ok(MetadataPlan)` return. A second invariant layer
re-parses the emitted bytes through the matching format's
header parser to confirm the empty image is at least
recognisable as the format the planner claimed to emit.

Adds one `[[bin]]` entry to `src/fuzz/Cargo.toml` plus the
`create = { path = "../crates/create" }` dependency. The
existing `.github/workflows/coverage-fuzz.yml` workflow
auto-discovers the new target via its `cargo metadata`-driven
matrix; no workflow edits required.

## Why this is its own phase

Phases 1-8 made `instar create` work, validated it against
qemu-img on every recorded baseline, and confirmed
writer/reader self-consistency via `instar check`. Phase 9
hardens the emitters against the adversarial input space
libFuzzer explores: pathological cluster_size values,
zero / max virtual_size, never-tested option combinations,
malformed backing-path lengths, and integer-arithmetic edge
cases the unit tests didn't reach.

Splitting from phase 10 (differential fuzzing extension) is
clean:

- **Phase 9** finds *crashes / hangs / panics / overflow*
  in instar's own emitters. Pure-function fuzzing of
  `crates/create/`'s public API.
- **Phase 10** finds *disagreements with qemu-img* for the
  shared subset of `(target, options, size)` triples both
  tools accept. Compares two writers' outputs via an info
  oracle.

Different harnesses, different oracles, different CI
workflows.

## What the survey turned up

### `crates/create/`'s public surface

`src/crates/create/src/lib.rs` exposes four planner functions:

```rust
pub fn plan_qcow2<'a>(opts: &Qcow2CreateOpts<'_>, scratch: &'a mut [u8])
    -> Result<MetadataPlan<'a>, CreateError>;

pub fn plan_vmdk<'a>(opts: &VmdkCreateOpts<'_>, scratch: &'a mut [u8])
    -> Result<MetadataPlan<'a>, CreateError>;

pub fn plan_vhd<'a>(opts: &VhdCreateOpts<'_>, scratch: &'a mut [u8])
    -> Result<MetadataPlan<'a>, CreateError>;

pub fn plan_vhdx<'a>(opts: &VhdxCreateOpts<'_>, scratch: &'a mut [u8])
    -> Result<MetadataPlan<'a>, CreateError>;
```

Each returns `Result<MetadataPlan, CreateError>`, where the
plan bundles:

- `total_metadata_bytes: u64` — sum of all write byte lengths.
- `minimum_file_size: u64` — `max(byte_offset + bytes.len())`.
- An inline array of up to `MAX_METADATA_WRITES` (96)
  `MetadataWrite { byte_offset, bytes: &[u8] }` entries.

VHD-fixed and VHD-dynamic share one planner (`plan_vhd`),
selected via `opts.subformat`. So four planners cover all
five non-raw target formats (raw has no metadata to emit and
is handled host-side; out of scope for fuzzing the create
crate).

### `Qcow2CreateOpts` shape (and the per-format shapes)

```rust
pub struct Qcow2CreateOpts<'a> {
    pub virtual_size: u64,
    pub cluster_size: u32,
    pub refcount_bits: u8,         // {1,2,4,8,16,32,64}
    pub extended_l2: bool,
    pub lazy_refcounts: bool,
    pub compat_v3: bool,
    pub backing: Option<BackingRef<'a>>,
    pub preallocation: qcow2::create::Preallocation,
}

pub struct VmdkCreateOpts<'a> {
    pub virtual_size: u64,
    pub subformat: VmdkSubformat,  // MonolithicSparse / StreamOptimized / 3 deferred
    pub grain_size: u32,           // power of two in 4 KiB..=64 KiB
    pub backing: Option<BackingRef<'a>>,
    pub parent_cid: Option<u32>,
}

pub struct VhdCreateOpts<'a> {
    pub virtual_size: u64,
    pub subformat: VhdSubformat,   // Dynamic / Fixed
    pub block_size: u32,           // power of two in 512 KiB..=256 MiB
    pub backing: Option<BackingRef<'a>>,
}

pub struct VhdxCreateOpts<'a> {
    pub virtual_size: u64,
    pub block_size: u32,           // power of two in 1 MiB..=256 MiB
    pub backing: Option<BackingRef<'a>>,
}
```

All four take a `&mut [u8]` scratch buffer; the largest is
qcow2's `QCOW2_MAX_METADATA_SCRATCH = 32 MiB` (worst case at
cluster_size=512 + extended_l2). Phase 9's harness allocates
one 32 MiB buffer and reuses it across iterations (libFuzzer
calls the harness in a tight loop).

### Existing fuzz target conventions

`fuzz_measure_calc.rs` is the closest analogue. It decodes
42 bytes into a `(target, options, sizes)` tuple, dispatches
to the matching calculator, and asserts a small invariant
set on `Ok` returns. Phase 9 mirrors this shape:

```rust
#![no_main]
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    if data.len() < HEADER_BYTES { return; }
    // ... decode + dispatch + assert ...
});
```

The mock CallTable (`instar_fuzz::build_call_table`) is *not*
used by the create-side harness — `plan_*` functions are
pure (no I/O); the scratch buffer and the option struct are
their only inputs.

### Buffer-assembly for the re-parse invariant

After a successful `plan_*` call, the harness allocates a
`Vec<u8>` of length `plan.minimum_file_size`, zero-fills it,
and copies each `MetadataWrite`'s bytes to its declared
offset. The resulting buffer is the "empty image bytes" that
a parser would see if the planner's writes were applied to a
fresh file.

The harness then calls the matching format's first-stage
parser on the relevant slice:

| Target | Parser entry | Slice |
|--------|--------------|-------|
| qcow2  | `qcow2::QcowHeader::parse(&buf[..512])`        | first sector |
| vmdk   | `vmdk::VmdkSparseHeader::parse(&buf[..512])`    | first sector (sparse) |
| vhd    | `vhd::VhdFooter::parse(&buf[buf.len()-512..])` | last sector (footer) |
| vhdx   | `vhdx::VhdxFileIdentifier::parse(&buf[..512])`  | first sector (file id) |

Only the header sector(s) are parsed — the harness does not
walk L1 / GD / BAT / region tables (those need a CallTable
and would belong in a phase-9b round-trip harness, deferred).
The minimal invariant is: "the planner emits bytes the
matching parser recognises as that format" — which catches
endianness flips, off-by-one offsets, wrong magic, and
truncated headers.

### Memory limit

libFuzzer's default `-rss_limit_mb=2048` caps each iteration
at 2 GiB resident. The qcow2 worst-case scratch is 32 MiB +
a `minimum_file_size` buffer up to `virtual_size + metadata`
bytes — which can exceed memory for adversarial
`virtual_size` near `u64::MAX`. The harness caps
`plan.minimum_file_size` at a fuzz-friendly ceiling
(e.g. 16 MiB) before allocating the re-parse buffer; if the
plan claims to need more, skip the re-parse and only assert
the plan-level invariants.

Without this cap, libFuzzer would OOM on any input where
`virtual_size` is large enough to require a 1 GiB+
`minimum_file_size`, even though the planner's bookkeeping
is correct. The OOM masks coverage signal from smaller cases.

### What we are NOT fuzzing in this phase

- The host `run_create()` orchestrator in `src/vmm/src/main.rs`
  — that's clap parsing + virtio attach + result rendering,
  none of which touch the emitters' pure-function surface.
- The guest binary in `src/operations/create/` — covered
  transitively via the planner fuzzing (the guest only calls
  `plan_*`, then writes the result via `write_output_sector`).
- The protobuf `CreateResultMessage` encoder — standard
  prost-generated code.
- The `-o key=value,...` parser in `src/vmm/src/main.rs` —
  string parsing of CLI args; could be added later as a
  small extra target but not blocking.
- Cross-version baseline comparison — phase 10's
  differential fuzzer.
- The L1 / GD / BAT walk after the header — needs a mock
  CallTable; defer to a phase-9 follow-up if the surface
  proves under-covered.

## Architecture

### Fuzz input layout

```
byte  0       format selector (mod 4): 0=qcow2, 1=vmdk, 2=vhd, 3=vhdx
byte  1       qcow2 refcount_bits selector (mod 8): picks one of
              {1, 2, 4, 8, 16, 32, 64, 0xff (invalid)}
byte  2       qcow2 flag bits: bit 0=extended_l2, bit 1=lazy_refcounts,
              bit 2=compat_v3
byte  3       qcow2 preallocation selector (mod 4): Off/Metadata/Falloc/Full
byte  4       vmdk subformat selector (mod 5)
byte  5       vhd subformat selector (mod 2): Dynamic / Fixed
byte  6       backing_present flag: bit 0=present, bit 1=format hint
              given, bits 4..7=ImageFormat selector when bit 1 is set
byte  7       backing_path_len (mod 130): 0..129 — covers the
              MAX_BACKING_FILE_LEN=1024 boundary by mapping 128 to 1024
              and 129 to 1025 (exercises BackingFileTooLong)
bytes 8-15    virtual_size (u64 little-endian)
bytes 16-19   cluster_size / grain_size / block_size (u32 LE, reused
              per format)
bytes 20-23   vmdk parent_cid (u32 LE; 0 maps to None)
bytes 24..    backing_path bytes (up to backing_path_len, capped to
              data.len() - 24)
```

Total: 24 bytes minimum. Shorter inputs return early.

Notes on the encoding:

- Including the `0xff` refcount_bits selector means libFuzzer
  reaches the `InvalidOption` rejection path in
  `qcow2::create::compute_layout`.
- Non-power-of-two `cluster_size` / `grain_size` /
  `block_size` values are reached naturally because the
  fuzzer mutates the bytes freely. The validation paths
  return `InvalidClusterSize` / `InvalidGrainSize` /
  `InvalidBlockSize`; the harness silently accepts these.
- `backing_path_len > MAX_BACKING_FILE_LEN` exercises the
  `BackingFileTooLong` path explicitly.
- The vmdk `subformat` selector covers all five enum
  variants — three of which (`MonolithicFlat`,
  `TwoGbMaxExtentSparse`, `TwoGbMaxExtentFlat`) return
  `InvalidSubformat`. Exercises those rejection branches.
- The vhdx options struct has no subformat (dynamic-only);
  the selector byte slot is unused for vhdx but stays in
  the layout for harness symmetry.

### Invariants

For every successful `plan_*` return (`Ok(plan)`):

1. **Bookkeeping consistency**: `plan.total_metadata_bytes ==
   sum(w.bytes.len() for w in plan.writes())`.
2. **File-size bound**: every write fits within the declared
   `plan.minimum_file_size`:
   `w.byte_offset + w.bytes.len() <= plan.minimum_file_size`.
3. **Overflow sanity**:
   `plan.total_metadata_bytes.checked_add(plan.minimum_file_size).is_some()`.
   Catches a class of latent overflow where each is small
   but their sum exceeds `u64::MAX`.
4. **Write count bound**: `plan.writes().len() <=
   MAX_METADATA_WRITES`. (The `push()` method enforces this
   on construction; the check makes the contract visible at
   the fuzz boundary.)
5. **Re-parse**: when `plan.minimum_file_size <=
   REPARSE_BUFFER_CAP` (default 16 MiB), allocate a buffer
   of that size, zero-fill, apply every write, then:
   - qcow2: `QcowHeader::parse(&buf[..512])` returns `Some`
     and `parsed.virtual_size == opts.virtual_size`.
   - vmdk: `VmdkSparseHeader::parse(&buf[..512])` returns
     `Some` and the parsed virtual-size matches.
   - vhd: `VhdFooter::parse(&buf[buf.len()-512..])` returns
     `Some` and `parsed.current_size == opts.virtual_size`
     (modulo the documented CHS-rounding divergence — if the
     parser surfaces the CHS-rounded value, the assertion
     allows a tolerance of `<= geometry_round_up(virtual_size)
     - virtual_size`).
   - vhdx: `VhdxFileIdentifier::parse(&buf[..512])` returns
     `Some` (the file identifier doesn't carry virtual_size;
     deeper validation needs the metadata-region walk).

Errors (`InvalidVirtualSize`, `InvalidClusterSize`,
`InvalidBlockSize`, `InvalidGrainSize`, `InvalidSubformat`,
`BackingFileTooLong`, `BackingFileUnsupported`, `Overflow`,
`ScratchTooSmall`, `PreallocationUnsupported`) are silently
ignored — the planner is explicitly allowed to reject; the
fuzz oracle is panic only.

### Cargo manifest additions

```toml
# src/fuzz/Cargo.toml [dependencies]
create = { path = "../crates/create" }   # NEW

[[bin]]
name = "fuzz_create_emitters"
path = "fuzz_targets/fuzz_create_emitters.rs"
doc = false
test = false
```

The `create` crate is not currently in the fuzz manifest
because no prior fuzz target needed it.

### Existing CI workflow integration

`.github/workflows/coverage-fuzz.yml` enumerates targets via
its matrix: `workflow_dispatch.inputs.targets` for manual
runs, auto-discovery for the empty case (the script lists
every `[[bin]]` entry in the fuzz manifest). Adding the new
`[[bin]]` entry is sufficient — no workflow edit needed.
Verify during 9a by reading the script that emits the
matrix; if it hard-codes target names, extend it.

### Smoke runs

The new target's first run should be ~60s locally during 9a:

```
cd src/fuzz
cargo +nightly fuzz build fuzz_create_emitters
cargo +nightly fuzz run fuzz_create_emitters -- \
    -runs=10000 -max_total_time=60 -rss_limit_mb=2048
```

Expected outcome: no crashes within the smoke window. If a
crash appears, treat it as **a real bug** to file and fix
before committing the harness. A crashing harness on first
run probably means the fuzzer found a genuine panic in one
of the `plan_*` functions that the existing unit tests
(`crates/create/tests/round_trip.rs`) didn't reach.

### Corpus seeding

Phase 9 ships with no curated corpus. libFuzzer starts with
an empty seed and discovers inputs on its own. The fuzz
input is structured (24-byte prefix + variable backing path)
so the fuzzer's mutation engine reaches every dispatch
branch within the first few hundred iterations.

`scripts/extract-fuzz-corpus.py` (if it exists; verify
during 9a) could be extended to produce structured seed
inputs by walking phase 7's CREATE_CASES — each case
becomes a known-good 24-byte prefix. Defer to a follow-up
unless 9a's smoke run shows poor initial coverage.

## Open questions

These should be answered during execution; escalate to the
operator rather than guessing.

1. **One combined harness vs four separate ones**. Each
   `plan_*` function has its own option-struct shape; a
   combined harness needs per-format decoding. Master plan
   says "fuzz_create_emitters.rs" (singular). Recommendation:
   single combined harness — cleaner coverage attribution
   per `plan_*` function (libFuzzer's coverage report
   already groups by function), simpler corpus management,
   fewer `[[bin]]` entries to maintain. Matches phase 7's
   existing fuzz_format_detect (single harness, multi-
   format dispatch).

2. **REPARSE_BUFFER_CAP value**. 16 MiB is conservative
   (handles qcow2 1 GiB virtual + metadata comfortably).
   Larger covers more inputs but slows iteration. Smaller
   skips more re-parse invariants but lets the fuzzer
   explore wider input space per second. Recommendation:
   start at 16 MiB; profile during 9a and tune if the
   re-parse invariant is the bottleneck.

3. **VHD-fixed re-parse**. Fixed-VHD files have only a 512-
   byte footer at end-of-file; `VhdFooter::parse` reads the
   last sector. The planner's `minimum_file_size` for fixed
   VHD is `virtual_size + 512`. For 1 MiB virtual that's
   1 MiB + 512 — within the cap. For 1 GiB+ virtual the
   re-parse is skipped per the cap. That's fine — the
   plan-level invariants still hold and the re-parse is
   covered by smaller virtual_size mutations.

4. **vmdk descriptor parse**. `VmdkSparseHeader::parse` only
   confirms the sparse header magic; the descriptor (embedded
   text after the header) carries the virtual_size as a
   string. Deeper round-trip would call
   `vmdk::parse_descriptor` on the descriptor bytes — but
   that needs the descriptor offset from the parsed header.
   Recommendation: defer to a phase-9 follow-up if coverage
   data shows the descriptor path is under-exercised.

5. **vhdx file-identifier parse depth**. The first sector
   only carries the "vhdxfile" magic and a creator string;
   the metadata-region walk (where virtual_size lives) needs
   the BAT and metadata regions parsed via the mock
   CallTable. Recommendation: phase 9's re-parse stops at
   the file identifier — the deeper walk is already
   exercised by `fuzz_vhdx_header` and `fuzz_vhdx_metadata`,
   which feed parser bytes directly.

6. **Sanitizers**. The existing fuzz harnesses run under
   libFuzzer's default address-sanitizer-equivalent. Phase 9
   inherits that; no additional sanitizer config needed.

7. **Nightly CI duration**. Phase 7 of measure used
   `cron: '0 4 * * *'` with 1 h per target. Adding one new
   target adds 1 h to nightly. Acceptable. Verify the
   workflow's `timeout-minutes` ceiling still accommodates
   16 targets × 1 h.

8. **Should backing-format inheritance be exercised**?
   The planner accepts `BackingRef { path, format }` but
   does not consult the format at runtime — it just
   embeds it in metadata strings (qcow2 backing format
   header extension). Recommendation: pass random
   `Option<ImageFormat>` via the backing-flag byte;
   the fuzzer reaches every code path on both arms of
   the `is_some()` branch naturally.

9. **Memory leak in the harness loop**. libFuzzer
   re-enters `fuzz_target!` thousands of times per
   second. The harness allocates a 32 MiB scratch and
   an up-to-16 MiB re-parse buffer per iteration. Both
   should be heap-allocated once via `lazy_static` /
   `OnceLock` and reused. Recommendation: yes — saves
   allocation overhead, lets the fuzzer reach more
   iterations per CPU-second.

## Public surface added in phase 9

In `src/fuzz/Cargo.toml`:

```toml
[dependencies]
create = { path = "../crates/create" }

[[bin]]
name = "fuzz_create_emitters"
path = "fuzz_targets/fuzz_create_emitters.rs"
doc = false
test = false
```

In `src/fuzz/fuzz_targets/fuzz_create_emitters.rs` (new):

```rust
#![no_main]
use libfuzzer_sys::fuzz_target;

// Decoder, dispatch, invariants per the Architecture section.

fuzz_target!(|data: &[u8]| { ... });
```

No instar-side code changes outside the fuzz crate.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | medium | sonnet | none | Create `src/fuzz/fuzz_targets/fuzz_create_emitters.rs` per the "Fuzz input layout" + "Invariants 1-4" sections (the plan-level invariants only — bookkeeping consistency, file-size bound, overflow sanity, write-count bound). Add `create = { path = "../crates/create" }` to `src/fuzz/Cargo.toml [dependencies]` and the matching `[[bin]]` entry. The harness decodes the first 24 bytes into the structured tuple, dispatches to `plan_qcow2 / plan_vmdk / plan_vhd / plan_vhdx` with a heap-allocated 32 MiB scratch reused via `std::sync::OnceLock<Mutex<Vec<u8>>>`, and asserts the four plan-level invariants only on `Ok` returns. Errors are silently ignored. Verify with `cd src/fuzz && cargo +nightly fuzz build fuzz_create_emitters` and then `cargo +nightly fuzz run fuzz_create_emitters -- -runs=10000 -max_total_time=60 -rss_limit_mb=2048`. If crashes appear, **stop and file them as real bugs** before continuing. Read `.github/workflows/coverage-fuzz.yml`'s target-list logic: if it auto-discovers via `cargo metadata`, the new target is picked up automatically; if it hard-codes names, extend the list. Touch only `src/fuzz/Cargo.toml`, the new target file, and (if needed) the workflow. |
| 9b | medium | sonnet | none | Extend `fuzz_create_emitters.rs` with the re-parse invariant (5 in the plan). Add a `REPARSE_BUFFER_CAP` const = 16 MiB. When `plan.minimum_file_size <= REPARSE_BUFFER_CAP`, allocate a `Vec<u8>` of that length (reuse a per-thread cached buffer), apply every `MetadataWrite` to it, then dispatch on `target_sel` to the matching format's header parser: `qcow2::QcowHeader::parse(&buf[..512])`, `vmdk::VmdkSparseHeader::parse(&buf[..512])`, `vhd::VhdFooter::parse(&buf[buf.len()-512..])`, `vhdx::VhdxFileIdentifier::parse(&buf[..512])`. Assert: (a) the parser returns `Some` (the emitted header is recognised as the claimed format); (b) for qcow2 and vmdk, the parsed virtual_size equals `opts.virtual_size`; (c) for vhd, the parsed `current_size` equals `opts.virtual_size` *exactly* (instar emits exact bytes; the CHS-rounded divergence noted in phase 8b is qemu's behaviour, not instar's, so the assertion can be strict here); (d) for vhdx, the file-identifier magic check is the only assertion (deeper virtual_size verification needs metadata-region walking — out of scope). Run the same smoke command. If the re-parse fails on any successful plan, it's a real bug in either the emitter or the parser — file and fix before continuing. Verify the harness exact entry-point names: `qcow2::QcowHeader::parse`, `vmdk::VmdkSparseHeader::parse` (or whatever phase 1 named the sparse header — check `src/crates/vmdk/src/lib.rs:353`), `vhd::VhdFooter::parse` (`src/crates/vhd/src/lib.rs:129`), `vhdx::VhdxFileIdentifier::parse` (`src/crates/vhdx/src/lib.rs:210` — the actual struct name may differ; confirm by reading). |
| 9c | low | sonnet | none | Update `ARCHITECTURE.md`: the existing "Coverage-Guided Fuzzing" subsection lists the fuzz target count; bump from 15 to 16 and add `fuzz_create_emitters` to the enumeration with a one-line description ("exercises plan_qcow2 / plan_vmdk / plan_vhd / plan_vhdx with structured fuzz input + a header re-parse invariant"). Update `CHANGELOG.md` Unreleased / Added with: "Coverage-guided fuzz target for `crates/create/`'s emitters (`fuzz_create_emitters`). Decodes structured input into per-format option tuples, dispatches to each `plan_*` function, asserts plan-level bookkeeping invariants plus a header re-parse round-trip via the matching parser crate. Picked up automatically by the nightly coverage-fuzz workflow. ([phase 9](https://github.com/shakenfist/instar/blob/develop/docs/plans/PLAN-create-phase-09-fuzz-coverage.md))". Mark phase 9 of PLAN-create.md as Complete in the execution table. Run `pre-commit run --all-files`. Touch only ARCHITECTURE.md, CHANGELOG.md, and docs/plans/PLAN-create.md. |

Total: 3 commits.

## Out of scope for phase 9

- Differential fuzzing comparing instar against qemu-img
  (phase 10 — separate workflow, different oracle).
- The L1 / L2 / GD / BAT / metadata-region deep parse on
  the emitted bytes (would need a mock CallTable; defer
  if coverage data warrants).
- Corpus seeding from instar-testdata baselines (defer
  to a follow-up unless 9a's smoke shows poor initial
  coverage).
- Refactoring `crates/create` to expose internal helpers
  (none needed; the public `plan_*` surface is the
  fuzz boundary).
- `-o key=value,...` parser fuzzing (potential follow-up;
  small scope but not blocking).
- Extending the workflow's per-target time budget
  (1 h default is fine).
- VHD CHS-geometry round-trip beyond exact-equality
  (the divergence is qemu-side, not instar-side; instar
  emits exact bytes so the strict assertion holds).

## Success criteria

- `src/fuzz/Cargo.toml` lists the `fuzz_create_emitters`
  `[[bin]]` entry plus the `create` dependency.
- `cargo +nightly fuzz build fuzz_create_emitters` succeeds.
- `cargo +nightly fuzz run fuzz_create_emitters --
   -runs=10000 -max_total_time=60 -rss_limit_mb=2048`
  completes without crashes.
- The nightly CI workflow's auto-discovery picks up the
  new target (manual verification by reading
  `.github/workflows/coverage-fuzz.yml`).
- The re-parse invariant fires on at least one input the
  fuzzer reaches (verifiable via libFuzzer's coverage
  output showing the re-parse branch has hits).
- ARCHITECTURE.md, CHANGELOG.md, and PLAN-create.md
  execution row updated.

## Risks and mitigations

- **Initial fuzz run finds a real panic**. Mitigation:
  9a/9b briefs say "stop and file the bug". The expected
  outcome is no findings because phases 1-8 covered the
  emitters with extensive unit tests and matrix integration
  tests; if the fuzzer does find something, it's high-value
  signal worth pausing for.
- **OOM on adversarial virtual_size**. The plan-level
  assertions on `minimum_file_size` don't allocate; only
  the re-parse step does. The `REPARSE_BUFFER_CAP` gates
  allocation. Mitigation: skip re-parse when over cap.
- **Parser entry-point name drift**. Phase 1 of create may
  have used slightly different struct/method names than
  this plan guesses (`VmdkSparseHeader`, `VhdxFileIdentifier`
  — verify during 9b). Mitigation: 9b's brief says "confirm
  by reading the matching parser source file".
- **CI workflow doesn't auto-discover targets**: if the
  workflow hard-codes the target list, 9a needs to extend
  it. Mitigation: 9a's brief says "read it first".
- **Scratch buffer reuse races**: libFuzzer is single-
  threaded per process (the matrix parallelises across
  processes, not within). A `OnceLock<Mutex<Vec<u8>>>` is
  safe even though it adds a redundant mutex lock per
  iteration. Mitigation: prefer `thread_local!` over
  `Mutex` if the per-iteration lock overhead shows up in
  profiling.

## Bugs to fix

(To be filled in as work progresses.)

## Back brief

Before executing any step, the executing agent should
back-brief: which `plan_*` functions the harness covers,
which invariants fire on each `Ok` return, and what the
re-parse round-trip asserts per format. The reviewer
should verify the harness exercises every public emitter
in `crates/create/` (4 functions) and that the re-parse
covers at least the header-magic + virtual_size readback
for the three formats where the header surfaces it
(qcow2, vmdk, vhd).
