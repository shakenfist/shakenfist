# PLAN-resize phase 8: host VMM subcommand

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (KVM, virtio-block, `qemu-img resize` CLI
surface, posix_fallocate, fallocate FALLOC_FL_ZERO_RANGE), research
as needed to give a confident answer. Flag any uncertainty
explicitly rather than guessing.

This is a phase plan under `PLAN-resize.md`. Refer to that master
plan for overall context. Phases 1–7 shipped the planner crate
and the guest binary; phase 8 lands the host VMM subcommand that
actually launches the guest, applies the post-pass `set_len`
based on the result, and renders user-facing output.

## Mission

Add `instar resize` to the host CLI. The subcommand:

1. Parses a clap surface that matches `qemu-img resize`:
   ```
   instar resize [-f FMT] [--shrink] [--preallocation PREALLOC]
                 [--object OBJDEF] [--image-opts] [-q]
                 [--output {human,json}]
                 FILENAME [+-]SIZE[bkKMGTPE]
   ```
   `--object` and `--image-opts` are accepted by clap but reject
   at runtime with `"not yet supported"` (matches phase 1's
   master-plan posture).

2. Parses `[+-]SIZE` with the standard byte / k / M / G / T / P / E
   suffix set. A leading `+` or `-` makes it relative; absent
   prefix means absolute. Relative sizes require probing the
   existing image's `current_virtual_size`.

3. Probes the target file's format via a small host-side
   helper that opens the file, reads sector 0, calls
   `detect_format_from_header`, and runs the matching
   per-format parser to extract `current_virtual_size`. The
   probed size is required for relative-SIZE resolution and is
   also written into `ResizeConfig.current_virtual_size` as the
   cross-check the guest validates against.

4. Routes raw via a host-side shortcut: `file.set_len(new)` +
   (phase 9) the optional preallocation post-pass. No guest
   launch needed. Mirrors `run_create_raw`.

5. For non-raw, opens the output file `O_RDWR`, attaches it as
   the virtio-block output device, populates `ResizeConfig`,
   launches the resize guest binary, waits for the
   `ResizeResultMessage` on the serial channel, post-passes
   `file.set_len(result.file_size_after)` (the planner reports
   the exact post-resize EOF), then renders output.

6. Rejects `--shrink` semantics consistently with the planner:
   if the guest returns `ShrinkBelowAllocated`, surface a
   qemu-compatible error message.

7. Output: terse human line by default — `"Image resized."` —
   matching qemu's success message; `--output=json` produces
   the structured `{action, file_size_before, file_size_after,
   resolved_new_virtual_size, target_format}` form;
   `-q` / `--quiet` suppresses all non-error output.

Preallocation post-pass (`falloc` / `full`) lands in phase 9.
Phase 8 routes the `--preallocation` flag through
`ResizeConfig.flags` so the guest sees it; the host-side
finalisation is a phase-9 follow-up.

## What the survey turned up

- **`Commands` enum** at `src/vmm/src/main.rs:2252-2270` —
  `Commands::Create(CreateArgs)` is the template. Phase 8 adds
  `Commands::Resize(ResizeArgs)`.
- **`CreateArgs` struct** at `src/vmm/src/main.rs:2649-2749` —
  has the `--output` / `-q` flag conventions resize will mirror.
- **`run_create` dispatch** at `src/vmm/src/main.rs:6699-6712`
  is the dispatch template: parse opts → validate → branch
  raw-vs-nonraw → render.
- **`run_create_raw`** at `src/vmm/src/main.rs:7540-7567` is
  the raw-shortcut template: open `O_CREAT|O_TRUNC|O_RDWR`,
  `set_len`, optional preallocation, `sync_all`. Resize opens
  `O_RDWR` (no CREAT, no TRUNC).
- **`run_create_nonraw`** at `src/vmm/src/main.rs:6804-7135`
  is the guest-launch template: load binaries, open output,
  populate config, run KVM loop, harvest result, post-pass.
- **`run_create_guest`** at `src/vmm/src/main.rs:7167-7504`
  does the actual KVM setup + run loop. Decodes the
  `CreateResult` message from the serial port. Resize will
  introduce a parallel `run_resize_guest` decoding the
  `ResizeResultMessage`.
- **`parse_memory_size`** at `src/vmm/src/main.rs:344-361`
  parses K/M/G/T suffixes without the b/P/E. Phase 8 extends
  it (or adds a sibling) for the full qemu-img suffix set
  plus the `[+-]` prefix.
- **`get_binary_path` / `load_guest_binary`** at
  `src/vmm/src/main.rs:1570,8051-8057` load `.bin` files from
  disk at runtime (no `include_bytes!`); resize loads
  `resize.bin` the same way.
- **`render_create_success` / `render_create_success_json`**
  at `src/vmm/src/main.rs:7743-7800` are the rendering
  template (human and json branches; respect `args.quiet`).
- **`validate_create_args`** at
  `src/vmm/src/main.rs:7819-7940+` is the host-side
  pre-flight check template. Resize's pre-flight is much
  smaller — `--object` / `--image-opts` rejection, SIZE
  parseability, file accessibility.
- **`ResizeConfig` / `ResizeResult`** at
  `src/shared/src/lib.rs:2340-2502` (shipped in phase 1b),
  populated by phase 7a's `send_resize_result` plumbing.

## Algorithmic design

### Clap surface

```rust
// src/vmm/src/main.rs

#[derive(Args, Debug)]
struct ResizeArgs {
    /// FILENAME to resize.
    filename: String,

    /// [+-]SIZE[bkKMGTPE]. Leading `+` adds to current size;
    /// `-` subtracts; absent prefix means absolute.
    size: String,

    /// Force the image format detection.
    #[arg(short = 'f', long)]
    format: Option<String>,

    /// Allow shrink. Required when new < current.
    #[arg(long)]
    shrink: bool,

    /// Preallocation mode for newly-added regions.
    #[arg(long, default_value = "off",
          value_parser = ["off", "metadata", "falloc", "full"])]
    preallocation: String,

    /// QEMU user-creatable object (e.g. encryption key).
    /// Not yet supported.
    #[arg(long)]
    object: Option<String>,

    /// Indicates FILENAME is a complete image specification.
    /// Not yet supported.
    #[arg(long)]
    image_opts: bool,

    /// Suppress the "Image resized." line on success.
    #[arg(short = 'q', long)]
    quiet: bool,

    /// Output format.
    #[arg(long, default_value = "human", value_parser = ["human", "json"])]
    output: String,
}
```

Added to the `Commands` enum:

```rust
enum Commands {
    // ... existing variants ...
    Resize(ResizeArgs),
}
```

### `[+-]SIZE` parsing

```rust
enum ParsedSize {
    Absolute(u64),
    Add(u64),
    Subtract(u64),
}

fn parse_resize_size(s: &str) -> Result<ParsedSize, String> {
    let (kind_fn, body): (fn(u64) -> ParsedSize, &str) =
        if let Some(rest) = s.strip_prefix('+') {
            (ParsedSize::Add, rest)
        } else if let Some(rest) = s.strip_prefix('-') {
            (ParsedSize::Subtract, rest)
        } else {
            (ParsedSize::Absolute, s)
        };
    Ok(kind_fn(parse_qemu_img_size(body)?))
}

/// Like `parse_memory_size` but supports the full qemu-img
/// suffix set: b=512, k/K=KiB, M/G/T/P/E (binary).
fn parse_qemu_img_size(s: &str) -> Result<u64, String> { ... }
```

### Format probing

```rust
struct ProbedImage {
    format: ImageFormat,
    current_virtual_size: u64,
}

fn probe_current_size(path: &Path, forced_format: Option<&str>)
    -> Result<ProbedImage, ...>
{
    let mut file = OpenOptions::new().read(true).open(path)?;
    let mut header = [0u8; MAX_SECTOR_SIZE];
    file.read_exact(&mut header[..512])?;
    let format = match forced_format {
        Some("raw")   => ImageFormat::Raw,
        Some("qcow2") => ImageFormat::Qcow2,
        Some("vmdk")  => ImageFormat::Vmdk4,
        Some("vpc")   => ImageFormat::Vhd,
        Some("vhdx")  => ImageFormat::Vhdx,
        Some(other)   => return Err(format!("unknown format: {other}")),
        None => detect_format_from_header(&header, 512, false),
    };
    let size = match format {
        ImageFormat::Raw => file.metadata()?.len(),
        ImageFormat::Qcow2 =>
            qcow2::QcowHeader::parse(&header).ok_or("invalid qcow2 header")?.virtual_size,
        ImageFormat::Vmdk4 =>
            vmdk::Vmdk4Header::parse(&header).ok_or("invalid vmdk header")?.virtual_size,
        ImageFormat::Vhd => {
            // VHD footer at end of file.
            let len = file.metadata()?.len();
            if len < 512 { return Err("file too small for vhd"); }
            file.seek(SeekFrom::Start(len - 512))?;
            let mut footer = [0u8; 512];
            file.read_exact(&mut footer)?;
            vhd::VhdFooter::parse(&footer).ok_or("invalid vhd footer")?.current_size
        }
        ImageFormat::Vhdx => probe_vhdx_virtual_size(&mut file)?,
        _ => return Err("unsupported format"),
    };
    Ok(ProbedImage { format, current_virtual_size: size })
}
```

### Raw shortcut

```rust
fn run_resize_raw(args: &ResizeArgs, new_virtual_size: u64,
                  current_virtual_size: u64)
    -> Result<(), Box<dyn Error>>
{
    let mut file = OpenOptions::new().read(true).write(true).open(&args.filename)?;
    file.set_len(new_virtual_size)?;
    // Phase 9 will layer falloc/full here.
    file.sync_all()?;
    let action = if new_virtual_size > current_virtual_size {
        "grow"
    } else if new_virtual_size < current_virtual_size {
        "shrink"
    } else {
        "noop"
    };
    render_resize_success(args, ImageFormat::Raw, current_virtual_size,
                          new_virtual_size, new_virtual_size, action);
    Ok(())
}
```

### Non-raw guest launch

```rust
fn run_resize_nonraw(args: &ResizeArgs, probed: &ProbedImage,
                     new_virtual_size: u64)
    -> Result<(), Box<dyn Error>>
{
    let core_code   = load_guest_binary(get_binary_path("core.bin"))?;
    let resize_code = load_guest_binary(get_binary_path("resize.bin"))?;

    // Open output O_RDWR.
    let output = BackingStore::open(&args.filename, false, None, true)?;
    let output_capacity = output.size_in_bytes();

    // Build ResizeConfig.
    let mut flags: u32 = 0;
    if args.shrink { flags |= ResizeConfig::FLAG_SHRINK; }
    if args.quiet  { flags |= ResizeConfig::FLAG_QUIET; }
    flags |= encode_preallocation(&args.preallocation);
    if probed.format == ImageFormat::Qcow2 {
        let header = qcow2::QcowHeader::parse(...)?;
        if header.extended_l2 { flags |= ResizeConfig::FLAG_EXTENDED_L2; }
    }
    let config = ResizeConfig {
        magic: ResizeConfig::MAGIC,
        target_format: probed.format as u32,
        flags,
        sector_size: 512,
        current_virtual_size: probed.current_virtual_size,
        new_virtual_size,
        qcow2_cluster_size: 0,    // guest re-reads from existing header
        qcow2_refcount_bits: 0,
        vmdk_subformat: 0,
        vhd_subformat: 0,
        _pad: 0,
        vmdk_grain_size: 0,
        block_size: 0,
        _reserved: [0; 64],
    };

    // Launch guest; wait for ResizeResult.
    let result = run_resize_guest(&core_code, &resize_code, output, &config)?;
    if result.error != ResizeResult::ERROR_OK {
        return Err(map_resize_error(result.error).into());
    }

    // Post-pass: trim/extend the file to the planner-reported
    // exact size.  set_len works in both directions.
    let file = OpenOptions::new().write(true).open(&args.filename)?;
    file.set_len(result.file_size_after)?;
    file.sync_all()?;

    render_resize_success(args, probed.format, probed.current_virtual_size,
                          new_virtual_size, result.file_size_after,
                          result.action_str());
    Ok(())
}
```

### `run_resize_guest`

Mirror `run_create_guest` byte-for-byte except:
- One device (the output) instead of two — no input device
  needed because the guest reads via `read_output_sector`.
- Decode `ResizeResultMessage` instead of `CreateResultMessage`
  in the run loop.
- The `ResizeRunResult` carries `action`, `file_size_before`,
  `file_size_after`, `resolved_new_virtual_size`, `error`.

### Output rendering

```rust
fn render_resize_success(args: &ResizeArgs, format: ImageFormat,
                          old_size: u64, new_virtual_size: u64,
                          new_file_size: u64, action: &str) {
    if args.quiet { return; }
    if args.output == "json" {
        // {"filename":"...","format":"qcow2","action":"grow",
        //  "old_virtual_size":...,"new_virtual_size":...,
        //  "old_file_size":...,"new_file_size":...}
        let s = format!("{{
  \"filename\": \"{}\",
  \"format\": \"{}\",
  \"action\": \"{}\",
  \"old_virtual_size\": {},
  \"new_virtual_size\": {},
  \"new_file_size\": {}
}}",
            json_escape_string(&args.filename),
            format.name(),
            action,
            old_size, new_virtual_size, new_file_size);
        println!("{s}");
    } else {
        // Match qemu's terse line exactly so existing scripts
        // that grep for it keep working.
        println!("Image resized.");
    }
}
```

### Error mapping

```rust
fn map_resize_error(e: u32) -> String {
    match e {
        ResizeResult::ERROR_OK => "ok".into(),
        ResizeResult::ERROR_INVALID_OPTION => "invalid resize option".into(),
        ResizeResult::ERROR_INVALID_NEW_SIZE => "invalid new size".into(),
        ResizeResult::ERROR_SHRINK_WITHOUT_FLAG =>
            "shrinking requires --shrink (would discard data above the new size)".into(),
        ResizeResult::ERROR_SHRINK_BELOW_ALLOCATED =>
            "shrink rejected: allocated data lives above the new size".into(),
        ResizeResult::ERROR_UNSUPPORTED_FORMAT => "format does not support resize".into(),
        ResizeResult::ERROR_UNSUPPORTED_SUBFORMAT => "subformat does not support resize".into(),
        ResizeResult::ERROR_UNSUPPORTED_SHRINK =>
            "shrink not yet supported for this format".into(),
        ResizeResult::ERROR_PREALLOCATION_UNSUPPORTED =>
            "preallocation mode not supported by this format".into(),
        ResizeResult::ERROR_SCRATCH_TOO_SMALL => "image too large for the resize scratch buffer".into(),
        ResizeResult::ERROR_READ_FAILED => "I/O error reading the image".into(),
        ResizeResult::ERROR_WRITE_FAILED => "I/O error writing the image".into(),
        ResizeResult::ERROR_PARSE_FAILED => "the image header could not be parsed".into(),
        ResizeResult::ERROR_HEADER_MISMATCH =>
            "the image's current virtual size changed between host and guest read; \
             retry the operation".into(),
        _ => format!("unknown error code {e}"),
    }
}
```

### Top-level dispatch

```rust
fn run_resize(args: &ResizeArgs) -> Result<(), Box<dyn Error>> {
    if args.object.is_some() {
        return Err("--object is not yet supported".into());
    }
    if args.image_opts {
        return Err("--image-opts is not yet supported".into());
    }

    let probed = probe_current_size(args.filename.as_ref(),
                                    args.format.as_deref())?;
    let parsed_size = parse_resize_size(&args.size)?;
    let new_virtual_size = match parsed_size {
        ParsedSize::Absolute(v) => v,
        ParsedSize::Add(d) => probed.current_virtual_size.checked_add(d)
            .ok_or("size overflow")?,
        ParsedSize::Subtract(d) => probed.current_virtual_size.checked_sub(d)
            .ok_or("size underflow")?,
    };

    if probed.format == ImageFormat::Raw {
        run_resize_raw(args, new_virtual_size, probed.current_virtual_size)
    } else {
        run_resize_nonraw(args, &probed, new_virtual_size)
    }
}
```

In the top-level command dispatch:

```rust
match cli.command {
    // ... existing arms ...
    Commands::Resize(args) => run_resize(&args),
}
```

## Test surface

Unit tests live alongside the new helpers in `src/vmm/src/main.rs`:

- `parse_resize_size` accepts absolute, additive, subtractive
  forms with every suffix.
- `parse_resize_size` rejects empty, negative-absolute, unknown
  suffix.
- `parse_qemu_img_size` covers each suffix's exact multiplier.

End-to-end integration tests land in phase 11. Phase 8 can
optionally include a smoke test that builds + launches +
verifies a raw resize works, but it requires KVM access (root
or kvm group) and is brittle in CI; defer to phase 11's
`tests/test_resize.py`.

## Open questions

1. **Forced format vs auto-detect**. `-f raw` on a non-raw file
   means "treat as raw" (qemu's semantics). qemu allows this
   because raw is structureless. We match: if the user passes
   `-f raw`, the host uses raw semantics regardless of the
   file's actual magic bytes. *Recommendation*: yes, match
   qemu.

2. **What if `current_virtual_size == new_virtual_size`?** The
   planner returns `ResizeAction::NoOp` with zero patches; the
   host emits `"Image resized."` anyway (qemu does too — no
   error, no diagnostic about it being a no-op).

3. **Behaviour when the file doesn't exist**. qemu errors with
   "could not open ...". Match: `OpenOptions::open` returns a
   not-found error and we surface it directly.

4. **`current_file_size` cross-check**. The host's
   `BackingStore::size_in_bytes()` reports the file size before
   any guest activity. The guest's `current_file_size` field in
   the ResizeResult should match. Phase 8 doesn't enforce this
   strictly because the value is informational (the planner
   uses it internally). The phase 11 integration tests verify.

5. **Image-opts URI parser**. `--image-opts` lets qemu accept
   `file=...,driver=...,backing.driver=...` style image
   specifications. We don't support this; rejecting with a
   clear error is the correct posture for v1.

6. **The probe-before-resize race**. Between `probe_current_size`
   and `run_resize_nonraw`, another process could resize the
   file. The guest's `current_virtual_size` cross-check (in the
   ResizeConfig vs the parsed header) catches this and returns
   `HeaderMismatch`. The host then maps that to a "retry the
   operation" message. Acceptable for v1.

7. **Preallocation post-pass deferral**. Phase 8 routes
   `--preallocation` into `ResizeConfig.flags` so the guest
   sees it. The qcow2 metadata mode is already wired in the
   planner (rejected for now; lands in a follow-up). The
   host-side `falloc` / `full` finalisation for raw and for
   non-raw appends lands in phase 9. *Recommendation*: phase
   8's raw shortcut does NOT honour `falloc` / `full`; the
   host returns a clear "phase 9 will implement this" error
   if the user passes them. This keeps phase 8 focused.

8. **Concurrent file access**. The host opens the file
   `O_RDWR` and attaches as a virtio device. The guest writes
   via the call table. We do not take any flock; matches
   qemu's posture (the user is responsible for not running
   concurrent resize ops on the same file).

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | medium | sonnet | none | Add `Commands::Resize(ResizeArgs)` to the `Commands` enum in `src/vmm/src/main.rs` and define the `ResizeArgs` struct with the clap surface documented in the "Clap surface" section above. Add a `parse_qemu_img_size` helper (extends `parse_memory_size` with b/P/E suffixes and case-insensitive K) and a `parse_resize_size` helper that returns `ParsedSize::{Absolute, Add, Subtract}`. Add a `ParsedSize` enum. Wire up the top-level dispatch in the `main` command match to call a stub `run_resize` that returns `"phase 8b lands the real implementation"`. Add unit tests for `parse_qemu_img_size` (covers every suffix at exact multiplier) and `parse_resize_size` (covers absolute / additive / subtractive prefixes plus the empty / unknown-suffix rejection paths). `make instar`, `make lint`, `make test-rust`, `pre-commit run --all-files` clean. |
| 8b | high | opus | worktree | Implement the full `run_resize` in `src/vmm/src/main.rs`. (1) `probe_current_size` opens the file read-only, reads sector 0, runs `detect_format_from_header` (or honours `-f`), parses the matching format's header to extract `current_virtual_size`. For raw the size is the file length; for vhd the footer at file end; for vhdx use `vhdx::VhdxState::init` via a tiny in-process callback (or a simpler ad-hoc parse — copy the parser pattern from `run_create_nonraw`'s `read_backing_virtual_size`). (2) Resolve `[+-]SIZE` against `current_virtual_size`. (3) For raw: `run_resize_raw` does `OpenOptions::new().read(true).write(true).open()` then `set_len(new)` + `sync_all()`. Reject `--preallocation` other than `off` for raw with "phase 9 will implement falloc/full post-pass" (open question 7). (4) For non-raw: `run_resize_nonraw` loads `core.bin` + `resize.bin`, opens the output via `BackingStore::open(_, false, None, true)`, builds `ResizeConfig`, calls a new `run_resize_guest` modelled on `run_create_guest` (one output device, no input device, decode `ResizeResultMessage` in the serial loop). After the guest returns, post-pass `set_len(result.file_size_after)` + `sync_all`. (5) Map `ResizeResult::ERROR_*` to user-facing strings via a `map_resize_error` helper. (6) Reject `--object` / `--image-opts` with a "not yet supported" error before any I/O. The `run_resize_guest` function is the largest piece — model it on `run_create_guest` (around line 7167) but skip the input-device setup entirely. Risky: worktree isolation. |
| 8c | medium | sonnet | none | Output rendering and the global cross-checks. Add `render_resize_success` (human prints `"Image resized."`; json emits the structured form with filename / format / action / old_virtual_size / new_virtual_size / new_file_size). Add `render_resize_error` (human prints the mapped string to stderr; json prints `{"error":"..."}`). Honour `--quiet`. Verify the JSON form parses cleanly with `serde_json::from_str` in a unit test. Add a `--help` smoke test that confirms `instar resize --help` prints the expected synopsis (clap auto-generates; just assert the resulting `Command` builds and validates without panicking). `make lint`, `make test-rust`, `pre-commit run --all-files` clean. |

## Out of scope for phase 8

- Preallocation `falloc` / `full` post-pass (phase 9).
- `Preallocation::Metadata` for qcow2 (deferred — planner
  rejects).
- Cross-version baselines (phase 10).
- End-to-end integration tests against real images (phase 11).
- Differential fuzzing (phase 12).
- Documentation, CHANGELOG, follow-ups (phase 13).

## Success criteria for phase 8

- `cargo build -p instar` clean.
- `make instar` builds with `Commands::Resize` wired in.
- `make lint`, `pre-commit run --all-files` clean.
- `make test-rust` passes; new unit tests for size parsing
  succeed.
- `instar resize --help` prints a sensible synopsis.
- The clap surface accepts the documented flag set; clap
  rejects malformed invocations cleanly.
- Format probing succeeds for raw / qcow2 / vmdk / vhd /
  vhdx files (verified by phase 11 — phase 8's success here
  is just "the code compiles and the dispatch is wired").

## Sub-agent guidance

Read these files before starting any step:

- `src/vmm/src/main.rs:2244-2270` (Commands enum + Cli).
- `src/vmm/src/main.rs:2559-2749` (MeasureArgs + CreateArgs).
- `src/vmm/src/main.rs:344-361` (parse_memory_size).
- `src/vmm/src/main.rs:6699-7135` (run_create dispatch +
  run_create_raw + run_create_nonraw).
- `src/vmm/src/main.rs:7167-7504` (run_create_guest).
- `src/vmm/src/main.rs:7743-7800` (render_create_success).
- `src/shared/src/lib.rs:2335-2502` (ResizeConfig + ResizeResult).
- `src/operations/resize/src/main.rs` (the guest binary phase 7
  shipped; phase 8's host wiring is the other half of that
  contract).

The management session review checklist is the same as prior
phases.
