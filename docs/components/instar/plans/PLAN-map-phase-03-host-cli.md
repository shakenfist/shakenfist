# Phase 3: host VMM subcommand + clap surface

Master plan: [PLAN-map.md](/components/instar/plans/PLAN-map/) ·
Previous phase: [PLAN-map-phase-02-guest-op.md](/components/instar/plans/PLAN-map-phase-02-guest-op/)

## Status: Complete

`MapArgs` clap struct and `Commands::Map(MapArgs)` arm
shipped in `src/vmm/src/main.rs`; `run_map` writes
`MapConfig` per-field at `OPERATION_CONFIG_ADDR`, attaches
the source read-only as input device 0, runs the vCPU loop,
and decodes streamed `MapExtentMessage` records into a
placeholder renderer (replaced in phase 4). Host-side guards
reject `--image-opts`, VMDK monolithicFlat descriptors via
`peek_is_vmdk_descriptor`, and invalid sector sizes
(non-power-of-2 or outside `[512, MAX_SECTOR_SIZE]`).

## Mission

Ship the host-side CLI surface for the map operation. After
phase 3, `instar map FILENAME` parses arguments, launches
the `map.bin` guest binary from phase 2 with a populated
`MapConfig`, consumes the streamed `MapExtentMessage` records
and the terminating `MapResultMessage`, and emits *valid*
human / JSON output. The polish to byte-for-byte qemu-img
parity (column widths, JSON field ordering, exact hex
formatting, streaming JSON array writer) lands in phase 4.

The wire from CLI → guest → host renderer is the whole
deliverable: command parses, guest runs, host prints
something. The "something" is correct in structure and
content; whether it is byte-identical to qemu-img is phase
4's problem.

## Why this is its own phase

Phase 2 left a guest binary that can be launched but no host
caller. Phase 3 plugs the binary into the existing clap
dispatch table and the `Commands` enum, populates the
`MapConfig` byte layout from a `MapArgs` struct, threads the
serial-channel consumer to recognise the two new payload
variants, and gates the polished renderer behind a small
placeholder so phase 4 can swap in the qemu-img-compatible
formatter without changing any of the plumbing.

Bundling the CLI + minimal-render together avoids an
intermediate state in which `instar map` parses but does
nothing visible — the renderer is small enough to land here
without overflowing the phase. Splitting the renderer out as
phase 4 lets the byte-for-byte work (and its testdata
sweep) be a self-contained polish step.

## Architecture

### CLI surface

`MapArgs` mirrors `qemu-img map`'s surface plus an
instar-specific `--sector-size`:

```rust
#[derive(Args, Debug)]
struct MapArgs {
    /// Source image file. Required.
    input: String,

    /// Source format override (rare; usually auto-detected).
    /// Accepted for parity with qemu-img -f.
    #[arg(short = 'f', long = "format")]
    source_format: Option<String>,

    /// Output format: human (default) or json.
    #[arg(long, default_value = "human", value_parser = ["human", "json"])]
    output: String,

    /// Start emission at this virtual byte offset. Accepts
    /// K/M/G/T suffixes (parsed by parse_memory_size).
    /// Default: 0 (start of image).
    #[arg(long = "start-offset")]
    start_offset: Option<String>,

    /// Stop emission after this many virtual bytes from
    /// --start-offset. Accepts K/M/G/T suffixes. Default:
    /// emit to end of image.
    #[arg(long = "max-length")]
    max_length: Option<String>,

    /// Sector size for source I/O. Default: 65536. Not
    /// part of qemu-img's surface; instar-specific.
    #[arg(long, default_value = "65536")]
    sector_size: u32,

    /// Refused for parity-rejection: qemu-img's --image-opts
    /// descriptor-based source specification is deferred.
    /// Documented in docs/quirks.md.
    #[arg(long = "image-opts")]
    image_opts: bool,
}
```

`Commands::Map(MapArgs)` is appended to the enum after
`Commands::Commit(CommitArgs)`. The dispatch arm in `main()`
goes to `run_map(args, verbose)`.

### `run_map` orchestration

The function mirrors `run_measure`'s structure
(`src/vmm/src/main.rs:8856`) with these changes:

1. **No `--size` mode**: map always reads a source image.
   Reject `args.input.is_empty()` (clap already enforces
   the positional). No size-mode stub file plumbing.
2. **Refuse `--image-opts`** early with the exact qemu-img
   message: `"map: --image-opts is not supported (instar
   accepts FILENAME directly; see docs/quirks.md)"`.
3. **Validate `sector_size`** (power of two, 512 ≤ N ≤
   `MAX_SECTOR_SIZE`).
4. **Resolve window bytes**: `start_offset` and
   `max_length` each parsed via the existing
   `parse_memory_size` helper. Unset values map to 0
   (the guest treats `max_length == 0` as "emit to end").
5. **Refuse `--start-offset >= source_size`** with a
   clear error message — qemu-img returns an error too,
   and the guest's `clip_to_window` would silently emit
   nothing.
6. **Build the `MapConfig` byte layout**, write field-by-
   field to `OPERATION_CONFIG_ADDR` via
   `guest_mem.write_obj`. Magic `0x4D41505F`. Field offsets:
   `0` magic, `4` flags, `8` sector_size, `12`
   input_device_count (1), `16` start_offset (u64),
   `24` max_length (u64), `32..64` reserved (zero).
7. **Load `core.bin` + `map.bin`** via
   `get_binary_path` / `load_guest_binary`.
8. **KVM / VM / guest memory** setup identical to
   `run_measure` (no output device; one read-only input).
9. **Source attach**: open `args.input` with
   `BackingStore::open(_, true, None, false)` (read-only),
   wrap in `VirtioBlockDevice`, attach as device 0.
10. **vCPU + serial setup** identical to `run_measure`.
11. **Run loop**:
    - On `MapExtent` payload: push the record into a
      `Vec<MapExtentMessage>` for the renderer. (Streaming
      JSON writer is phase 4.)
    - On `MapResult` payload: set `map_error = result.error`,
      mark `map_result_seen = true`, store the `result`
      clone in `Option<MapResultMessage>`.
    - Other payloads: log via `format_message` when verbose.
12. **After the loop**: pass the collected extents + result
    to `print_map_result(args, &extents, &result, &output)`.
13. **Error mapping**: if the guest reports
    `ERROR_HAS_BACKING`, exit `1` with a stderr message
    pointing at the chain follow-up. `ERROR_INVALID_OPTION`,
    `ERROR_INVALID_SOURCE`, `ERROR_IO` get matching stderr
    messages.

### Renderer (phase 3 placeholder)

`print_map_result` produces *valid* output of both formats
but does not chase byte-for-byte qemu-img parity. Phase 4
replaces this function with the polished formatter.

```rust
fn print_map_result(
    extents: &[guest_::MapExtentMessage],
    result: &guest_::MapResultMessage,
    output_format: &str,
) {
    if result.error != MAP_RESULT_ERROR_OK {
        let msg = match result.error {
            MAP_RESULT_ERROR_INVALID_SOURCE => "map: source format unrecognised",
            MAP_RESULT_ERROR_INVALID_OPTION => "map: invalid config",
            MAP_RESULT_ERROR_HAS_BACKING => {
                "map: source has a backing/parent reference; \
                 chain composition is deferred (see PLAN-map.md)"
            }
            MAP_RESULT_ERROR_IO => "map: I/O failure walking the source",
            _ => "map: unknown error",
        };
        eprintln!("{}", msg);
        return;
    }

    if output_format == "json" {
        print_map_result_json(extents);
    } else {
        print_map_result_human(extents);
    }
}
```

Phase 3 human renderer (will be replaced in phase 4 to
match qemu-img's column widths exactly):

```
Offset          Length          Mapped to       File
0               0x100000        0x50000         <filename>
0x100000        0x100000                        
```

Phase 3 JSON renderer (will be replaced in phase 4 to
match qemu-img's whitespace + field ordering exactly):

```json
[
  { "start": 0, "length": 1048576, "depth": 0, "present": true,
    "zero": false, "data": true, "offset": 327680 },
  ...
]
```

Mapping from the state string to the `present` / `zero` /
`data` triple matches qemu-img:

| state          | present | zero  | data  | offset emitted |
|----------------|---------|-------|-------|----------------|
| "hole"         | false   | true  | false | no             |
| "zero"         | true    | true  | false | no             |
| "data"         | true    | false | true  | yes            |

`depth` is always 0 in v1 (chain composition deferred).

### Host-side constants

Mirror the measure pattern: declare top-of-file constants
that name the magic / error values so call sites don't
repeat raw hex literals:

```rust
const MAP_CONFIG_MAGIC: u32 = 0x4D41505F;   // "MAP_"
const MAP_RESULT_MAGIC: u32 = 0x4D505253;   // "MPRS"
const MAP_RESULT_ERROR_OK: u32 = 0;
const MAP_RESULT_ERROR_INVALID_SOURCE: u32 = 1;
const MAP_RESULT_ERROR_INVALID_OPTION: u32 = 2;
const MAP_RESULT_ERROR_HAS_BACKING: u32 = 3;
const MAP_RESULT_ERROR_IO: u32 = 4;
```

These go in the same constant block as
`MEASURE_CONFIG_MAGIC` / `MEASURE_RESULT_*`.

### `format_message` already handles the two new payloads

Step 2b of phase 2 added the `MapExtent` and `MapResult`
arms to `format_message`. No further work needed for the
debug-log path; phase 3 just routes `MapResult` through the
explicit renderer instead of the debug logger and
accumulates `MapExtent` payloads in a `Vec`.

## Open questions

1. **Source-format override (`-f`)**: phase 3 accepts the
   flag but ignores it (matches measure's behaviour). The
   guest re-detects the format from the first sector
   regardless. Recommendation: keep the flag in the surface
   for parity with qemu-img, and silently ignore for now;
   phase 4 may surface a warning if `-f` disagrees with the
   detected format.

2. **`--start-offset` alignment**: qemu-img map silently
   clamps `--start-offset` to a cluster boundary on output
   (the extent that contains the offset is emitted in full,
   starting from the cluster boundary). instar's
   guest-side `clip_to_window` clips at the byte level,
   which can produce a leading partial extent that
   qemu-img would not. **Recommendation**: phase 3 accepts
   this divergence and documents it in `docs/quirks.md`.
   The qemu-img semantic can be replicated later if any
   consumer complains.

3. **Empty / zero-extent output**: if the source is zero
   bytes, the guest emits one `MapResult` and no extents.
   The renderer should produce `[]` (JSON) or just the
   header row (human). Edge case worth a unit test.

4. **Source-file metadata fast paths**: qemu-img map opens
   the file via the block-driver layer; instar opens via
   `BackingStore::open`. Both surface the same byte stream
   to the guest. No semantic difference.

5. **Renderer streaming**: phase 3 buffers extents into a
   `Vec<MapExtentMessage>`. For a maximally-fragmented 1
   TiB qcow2 source emitting 17 M extents, the buffer
   reaches ~3 GiB host memory. Acceptable for phase 3 (the
   buffer is one-shot and the OS will swap if needed) but
   not for production. **Recommendation**: phase 4 swaps in
   a streaming writer that emits each extent as it arrives,
   bringing host memory back to constant. Phase 3 keeps the
   Vec for renderer simplicity; the test cases stay small
   enough not to OOM.

6. **`--output=json` with the buffered renderer**: the
   buffered Vec naturally drives the standard "open `[`,
   join with commas, close `]`" pattern. Phase 4's
   streaming writer will produce identical output by
   tracking a `first_extent` boolean in the message-handler
   inside the vCPU loop.

7. **VMDK monolithicFlat source rejection**: phase 3 should
   refuse these the same way measure does
   (`peek_is_vmdk_descriptor`). The phase 2 guest
   refuses them through `VmdkState::init`'s natural
   binary-header rejection, but the resulting
   `ERROR_INVALID_SOURCE` is less helpful than a host-side
   pre-check pointing at `qemu-img map` as an escape hatch.
   Recommendation: include the pre-check in phase 3 (one
   if-statement, copies the measure pattern).

8. **`get_input_device_count` for chain mode**: phase 3
   hard-codes `input_device_count = 1`. The chain
   follow-up will lift this to N; for v1 the host enforces
   the same invariant the guest checks.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | Add `MapArgs` struct after `CommitArgs` in `src/vmm/src/main.rs` per the schema above (input, source_format, output, start_offset, max_length, sector_size, image_opts). Add `Map(MapArgs)` to the `Commands` enum after `Commit(CommitArgs)`. Add the dispatch arm `Commands::Map(args) => run_map(args, verbose),` to the match in `main()` (line 3130 region). Add a stub `fn run_map(_args: MapArgs, _verbose: bool) -> Result<(), Box<dyn std::error::Error>> { Err("map: not yet implemented".into()) }` so the module compiles. Add the `MAP_CONFIG_MAGIC` / `MAP_RESULT_MAGIC` / `MAP_RESULT_ERROR_*` top-of-file constants per the schema above. Run `make instar`, `make lint`, `make test-rust`. `instar map --help` should produce the expected surface. Touches only `src/vmm/src/main.rs`. |
| 3b | high | opus | none | Implement the body of `run_map` per the "run_map orchestration" section. Validate sector_size, refuse --image-opts, refuse VMDK monolithicFlat sources via `peek_is_vmdk_descriptor` (same pattern as run_measure), refuse `--start-offset >= file size`, parse `start_offset` / `max_length` via `parse_memory_size`. Write the MapConfig byte layout at `OPERATION_CONFIG_ADDR` using per-field `write_obj` calls at known offsets (cross-check against the `MapConfig` struct in `src/shared/src/lib.rs`). KVM / VM / guest memory setup identical to `run_measure`; source attach via `BackingStore::open(path, true, None, false)` + `VirtioBlockDevice::new(..., read_only=true)`. Run the vCPU loop, push every `MapExtent` payload into a `Vec<MapExtentMessage>`, stash the `MapResult` payload into an `Option<MapResultMessage>`. After the loop, call `print_map_result(&extents, &result, &args.output)`. Error path: if the result has a non-ok error, the renderer prints to stderr and `run_map` returns `Err(...)` so the process exits non-zero. **High effort because:** this binds together the KVM plumbing, the byte-layout write, the streaming message consumer, and the error-mapping table. Subtle bugs (wrong config offset, wrong message arm, missing source-attach permission) produce silent wrong output in phase 6 integration tests. |
| 3c | medium | sonnet | none | Implement `print_map_result`, `print_map_result_human`, and `print_map_result_json` per the "Renderer (phase 3 placeholder)" section. Use the state-to-(present, zero, data) translation table above. Phase 3 output is *valid* and *correct* but not byte-for-byte qemu-img compatible — phase 4 polishes. Add ≥6 unit tests inside `#[cfg(test)] mod map_renderer_tests` in `src/vmm/src/main.rs` (the existing test module convention): empty extents list renders empty JSON array + header-only human; all-Hole extents render correctly; all-Data extents with file_offset render correctly; mixed states; large file_offset (verify hex formatting); error path produces no stdout output. Run `make lint`, `make test-rust`. Touches only `src/vmm/src/main.rs`. |
| 3d | low | sonnet | none | Update `ARCHITECTURE.md` to add a one-paragraph entry under the host-CLI surface section noting the new `instar map` subcommand: mirrors `qemu-img map` (FILENAME, -f, --output, --start-offset, --max-length), single-image v1, refuses --image-opts and VMDK monolithicFlat, renders a placeholder human / JSON output in phase 3 with the byte-for-byte polish in phase 4. Update `CHANGELOG.md` Unreleased / Added with one line citing the new subcommand. Run `pre-commit run --all-files`. |

Total: 4 commits.

### Out of scope for phase 3

- Byte-for-byte qemu-img output parity (phase 4).
- Streaming JSON array writer (phase 4 — phase 3 buffers).
- Backing-chain composition (master-plan follow-up).
- Snapshot-targeted mapping (`-l SNAPSHOT`, master-plan
  future work).
- `--image-opts` acceptance (rejected here; future work).
- Cross-version baseline generation (phase 5).
- Integration tests against real testdata images (phase 6).
- Fuzz target updates (phase 7).
- `output-profile` infrastructure additions for map
  (phase 5 if baselines reveal version drift).

## Success criteria

- `instar map --help` produces the documented surface
  (FILENAME, -f, --output, --start-offset, --max-length,
  --sector-size, --image-opts).
- `Commands::Map(MapArgs)` lands in the clap enum.
- `run_map` orchestrates the guest launch end-to-end and
  consumes streamed extents + the summary into a renderer.
- `print_map_result` produces valid JSON arrays and valid
  human-readable tables for both `--output=human` and
  `--output=json`.
- Error paths (HAS_BACKING, INVALID_SOURCE, INVALID_OPTION,
  IO) print a clear stderr message and exit non-zero.
- `make instar` builds the full toolchain (the placeholder
  `run_map` stub from 3a is replaced by 3b).
- `make lint` clean.
- `make test-rust` passes; new tests in `mod
  map_renderer_tests` add ≥6.
- `pre-commit run --all-files` clean.
- `ARCHITECTURE.md` and `CHANGELOG.md` updated.
- Running `target/release/instar map fixtures/*.qcow2`
  produces non-error output (informally verified during
  3b; formal coverage lands in phase 6).

## Risks and mitigations

- **MapConfig byte-layout drift between host and guest**.
  The host writes the struct via per-field `write_obj`
  calls at hard-coded offsets; the guest reads via a
  `*const MapConfig` cast. If the offsets don't agree
  the guest reads garbage. Mitigation: step 3b's brief
  directs the sub-agent to cross-check every offset
  against `MapConfig` in `src/shared/src/lib.rs`. A
  field-by-field comment block lists the offsets and
  field widths. (Same approach as `run_measure`.)

- **Streaming consumer drops MapExtentMessage before the
  result**. The vCPU loop must push every extent into the
  buffer regardless of order; only the result is the
  trigger to render. Mitigation: step 3b's brief enforces
  the pattern "extent → push; result → store + flag" with
  no early break.

- **Renderer divergence from qemu-img**. Phase 3 ships an
  unpolished renderer. Mitigation: phase 4 has the full
  byte-for-byte polish and the testdata sweep; phase 3
  just needs valid + correct output. Document the
  placeholder status in the function's doc comment so
  phase 4 finds it.

- **Empty source / zero virtual size**. The renderer must
  not crash on an empty extents vector or a
  `virtual_size == 0` result. Mitigation: step 3c's tests
  cover both cases.

- **`--start-offset` overshoot**. If the user passes
  `--start-offset` larger than the file's virtual size,
  the guest's `clip_to_window` silently emits nothing.
  Phase 3 catches this host-side and returns a clear
  error. Mitigation: step 3b's brief includes the
  pre-check; a unit test in 3c does *not* cover the
  host-side check (it's caught before the renderer).

- **Large-extent buffering host-side**. A pathologically
  fragmented source emits millions of extents. Phase 3
  buffers them all. Mitigation: phase 6 / 7 cover this
  with bounded corpora; phase 4 replaces the buffer with
  a streaming writer.

- **VMDK descriptor source UX**. The guest will reject
  multi-extent VMDK sources via `VmdkState::init`'s
  binary-header parse failure, surfacing
  `ERROR_INVALID_SOURCE`. The user sees "map: source
  format unrecognised". Mitigation: the host pre-check
  (`peek_is_vmdk_descriptor`) produces a more helpful
  error pointing at qemu-img.

## Back brief

Before executing any step, the executing agent should
back-brief: which file is being edited (almost always
`src/vmm/src/main.rs`), which existing function is the
closest template (`run_measure` for run_map,
`print_measure_result` for print_map_result, `MeasureArgs`
for `MapArgs`), and which boundary writes use raw memory
casts (the per-field MapConfig write). The reviewer should
verify no step bleeds into phase 4 (output polish), phase 5
(baselines), phase 6 (integration tests), or phase 7
(fuzz).
