# Phase 3: Bug report assembly and zip output

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead.

## Goal

Add a `BugReport` struct that collects all the pieces —
metadata, channel state snapshots, ring buffer pcap traffic,
session statistics, and (for display reports) a PNG
screenshot — and writes them into a single zip file.

At the end of this phase:

1. `BugReport::assemble()` produces a valid zip file
   containing `metadata.json`, `session.json`,
   `channel-state.json`, `traffic.pcap` (when the `capture`
   feature is enabled), and `screenshot.png` (display reports
   only).
2. `RyllApp` exposes a `generate_bug_report()` method that
   gathers all necessary data and calls `BugReport::assemble()`.
3. The zip file is written to the capture directory (if
   `--capture` is active) or the current working directory.
4. No GUI trigger exists yet — that is Phase 4.  The
   `generate_bug_report()` method is callable but not wired
   to any button or shortcut.
5. Unit tests verify zip assembly and contents.

## Design

### New dependencies

```toml
# Zip output for bug reports
zip = "2"

# PNG encoding for display screenshots
png = "0.18"
```

Both are unconditional (not feature-gated).  Bug reports
are always available regardless of `--capture`.  The `png`
crate (v0.18) is already an indirect dependency via `image`.
The `zip` crate is pure Rust with no system dependencies.

### Bug report type enum

```rust
/// Which channel the bug report is about.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum BugReportType {
    Display,
    Input,
    Cursor,
    Connection,
}
```

Each variant maps to a channel name for ring buffer drain
and snapshot selection:

| Type | Channel name | Snapshot struct |
|------|-------------|----------------|
| Display | "display" | `DisplaySnapshot` |
| Input | "inputs" | `InputsSnapshot` |
| Cursor | "cursor" | `CursorSnapshot` |
| Connection | "main" | `MainSnapshot` |

### Report metadata

```rust
/// Top-level metadata written to metadata.json.
#[derive(Debug, Clone, Serialize)]
pub struct ReportMetadata {
    pub ryll_version: String,
    pub platform_os: String,
    pub platform_arch: String,
    pub report_type: BugReportType,
    pub channel: String,
    pub description: String,
    pub region: Option<ReportRegion>,
    pub timestamp: String,
    pub target_host: String,
    pub target_port: u16,
    pub session_uptime_secs: f64,
}
```

```rust
/// Highlighted region for display bug reports.
#[derive(Debug, Clone, Serialize)]
pub struct ReportRegion {
    pub left: u32,
    pub top: u32,
    pub right: u32,
    pub bottom: u32,
}
```

### Timestamp utility

`capture.rs` has a private `chrono_now()` function that
produces ISO-8601 UTC strings without a datetime crate.
This function is needed by both capture metadata and bug
report metadata.

Move the canonical implementation to `bugreport.rs` as
`pub(crate) fn chrono_now() -> String` and update
`capture.rs` to call `crate::bugreport::chrono_now()`.
This eliminates duplication.

A second helper converts the ISO-8601 string to a
filename-safe form (colons → hyphens):

```rust
/// Convert "2026-04-03T12:34:56Z" to
///         "2026-04-03T12-34-56Z" for filenames.
pub(crate) fn filename_timestamp() -> String {
    chrono_now().replace(':', "-")
}
```

### PNG screenshot encoding

For display bug reports, the primary surface (surface 0)
is encoded as a PNG.  The surface pixels are available via
`DisplaySurface::pixels()` which returns `&[u8]` in RGBA
format.

```rust
/// Encode RGBA pixels to PNG bytes in memory.
pub(crate) fn encode_png(
    pixels: &[u8],
    width: u32,
    height: u32,
) -> anyhow::Result<Vec<u8>> {
    use png::BitDepth;
    use png::ColorType;
    use png::Encoder;
    use std::io::Cursor;

    let mut buf = Vec::new();
    {
        let cursor = Cursor::new(&mut buf);
        let mut encoder = Encoder::new(cursor, width, height);
        encoder.set_color(ColorType::Rgba);
        encoder.set_depth(BitDepth::Eight);
        let mut writer = encoder.write_header()?;
        writer.write_image_data(pixels)?;
    }
    Ok(buf)
}
```

### Pcap drain to bytes

The existing `TrafficRingBuffer::drain_to_pcap()` writes to
a file path.  For zip integration, we need to write pcap
data to a `Vec<u8>` in memory.

Add a `write_pcap_to<W: Write>()` method on
`TrafficRingBuffer` and a public
`drain_channel_pcap_bytes()` method on `TrafficBuffers`:

```rust
impl TrafficRingBuffer {
    /// Write all buffered pcap frames to a writer.
    #[cfg(feature = "capture")]
    pub fn write_pcap_to<W: std::io::Write>(
        &self,
        writer: W,
    ) -> anyhow::Result<usize> {
        use pcap_file::pcap::{
            PcapHeader, PcapPacket, PcapWriter,
        };
        use pcap_file::DataLink;

        let header = PcapHeader {
            datalink: DataLink::ETHERNET,
            ..Default::default()
        };
        let mut pcap = PcapWriter::with_header(
            writer, header,
        )?;

        let mut count = 0;
        for entry in &self.entries {
            let packet = PcapPacket::new(
                entry.timestamp,
                entry.pcap_frame.len() as u32,
                &entry.pcap_frame,
            );
            pcap.write_packet(&packet).ok();
            count += 1;
        }
        Ok(count)
    }
}
```

```rust
impl TrafficBuffers {
    /// Drain a channel's ring buffer to pcap bytes.
    /// Returns None if the capture feature is disabled
    /// or the channel name is unknown.
    pub fn drain_channel_pcap_bytes(
        &self,
        channel: &str,
    ) -> Option<Vec<u8>> {
        #[cfg(feature = "capture")]
        {
            let buf = self.buffer_for(channel)?;
            let guard = buf.lock().unwrap();
            let mut output = Vec::new();
            guard.write_pcap_to(&mut output).ok()?;
            Some(output)
        }
        #[cfg(not(feature = "capture"))]
        {
            let _ = channel;
            None
        }
    }
}
```

### Connection metadata in RyllApp

`RyllApp` does not currently store the connection target
(host/port).  The `Config` is consumed by
`run_connection()`.  Bug report metadata needs these values.

Add two fields to `RyllApp`:

```rust
// Connection target for bug report metadata
target_host: String,
target_port: u16,
```

Set them from `config.host` and `config.port` in
`RyllApp::new()` before the config is moved into the
connection thread.

### BugReport struct

```rust
/// A fully assembled bug report ready to write to disk.
pub struct BugReport {
    /// Report type (Display, Input, Cursor, Connection).
    pub report_type: BugReportType,
    /// User-supplied description.
    pub description: String,
    /// Highlighted region (display reports only).
    pub region: Option<ReportRegion>,
    /// Serialised metadata.json content.
    metadata_json: String,
    /// Serialised session.json (AppSnapshot).
    session_json: String,
    /// Serialised channel-state.json.
    channel_state_json: String,
    /// Pcap bytes (None when capture feature disabled).
    pcap_bytes: Option<Vec<u8>>,
    /// PNG screenshot bytes (display reports only).
    screenshot_png: Option<Vec<u8>>,
}
```

```rust
impl BugReport {
    /// Assemble a bug report from the available data.
    pub fn new(
        report_type: BugReportType,
        description: String,
        region: Option<ReportRegion>,
        target_host: &str,
        target_port: u16,
        traffic: &TrafficBuffers,
        channel_snapshots: &ChannelSnapshots,
        app_snapshot: &std::sync::Mutex<AppSnapshot>,
        surface_pixels: Option<(&[u8], u32, u32)>,
    ) -> anyhow::Result<Self>;

    /// Write the bug report as a zip file to `dir`.
    /// Returns the path of the written file.
    pub fn write_zip(
        &self,
        dir: &std::path::Path,
    ) -> anyhow::Result<std::path::PathBuf>;
}
```

`BugReport::new()` does all the serialisation and encoding
synchronously:

1. Lock `app_snapshot`, clone, set `uptime_secs` from
   `traffic.elapsed()`.  Serialise to `session_json`.
2. Lock the relevant channel snapshot, clone, serialise to
   `channel_state_json`.
3. Call `traffic.drain_channel_pcap_bytes(channel_name)` to
   get pcap bytes.
4. If display report and surface pixels provided, call
   `encode_png()` to get screenshot bytes.
5. Build `ReportMetadata`, serialise to `metadata_json`.

`BugReport::write_zip()` creates the zip file:

1. Build filename:
   `ryll-bugreport-{filename_timestamp()}.zip`.
2. Create the file at `dir.join(filename)`.
3. Open a `zip::ZipWriter` on the file.
4. Add `metadata.json` (stored, not compressed — it's
   small).
5. Add `session.json`.
6. Add `channel-state.json`.
7. If `pcap_bytes.is_some()`, add `traffic.pcap` (stored
   — pcap data doesn't compress well).
8. If `screenshot_png.is_some()`, add `screenshot.png`
   (stored — PNG is already compressed).
9. `zip.finish()`.
10. Return the file path.

### RyllApp::generate_bug_report()

```rust
impl RyllApp {
    /// Generate a bug report and write it to disk.
    pub fn generate_bug_report(
        &self,
        report_type: BugReportType,
        description: String,
        region: Option<ReportRegion>,
    ) -> anyhow::Result<std::path::PathBuf> {
        // Get surface pixels for display reports
        let surface_data = if report_type
            == BugReportType::Display
        {
            self.surfaces.get(&0).map(|s| {
                (s.pixels(), s.width, s.height)
            })
        } else {
            None
        };

        // Assemble the report
        let report = BugReport::new(
            report_type,
            description,
            region,
            &self.target_host,
            self.target_port,
            &self.traffic,
            &self.channel_snapshots,
            &self.app_snapshot,
            surface_data,
        )?;

        // Determine output directory
        let output_dir = match &self.capture {
            Some(cap) => cap.dir.join("bug-reports"),
            None => std::env::current_dir()
                .unwrap_or_else(|_| ".".into()),
        };

        report.write_zip(&output_dir)
    }
}
```

### Headless mode

Headless mode does not have a `RyllApp`.  Bug reports
are a GUI feature (Phase 4 adds the button).  However,
the `BugReport` struct itself is usable from any context
that has access to the `TrafficBuffers`,
`ChannelSnapshots`, and `AppSnapshot`.  No changes to
`run_headless()` are needed in this phase.

## Steps

### Step 1: Add zip and png dependencies

Add to `Cargo.toml` `[dependencies]`:

```toml
zip = "2"
png = "0.18"
```

### Step 2: Move chrono_now() to bugreport.rs

1. Copy `chrono_now()` from `capture.rs` to `bugreport.rs`
   as `pub(crate) fn chrono_now() -> String`.
2. Add `pub(crate) fn filename_timestamp() -> String` that
   replaces colons with hyphens.
3. Update `capture.rs` to call
   `crate::bugreport::chrono_now()` instead of its local
   copy.  Remove the local `chrono_now()` function.

### Step 3: Add BugReportType, ReportMetadata, ReportRegion

Add to `bugreport.rs`:

1. `BugReportType` enum with `Serialize` derive.
2. `ReportRegion` struct with `Serialize`.
3. `ReportMetadata` struct with `Serialize`.

### Step 4: Add pcap drain-to-bytes methods

1. Add `TrafficRingBuffer::write_pcap_to<W: Write>()`
   (`#[cfg(feature = "capture")]`).
2. Add `TrafficBuffers::drain_channel_pcap_bytes()` (public,
   handles the cfg gate internally).
3. Remove `#[allow(dead_code)]` from methods that are now
   used.

### Step 5: Add encode_png() helper

Add `pub(crate) fn encode_png()` to `bugreport.rs` that
encodes RGBA pixels to PNG bytes using the `png` crate.

### Step 6: Store connection target in RyllApp

1. Add `target_host: String` and `target_port: u16` fields
   to `RyllApp`.
2. Set from `config.host.clone()` and `config.port` in
   `RyllApp::new()` before config is moved.
3. Initialise to sensible defaults in the constructor.

### Step 7: Add BugReport struct with assemble and write

Add to `bugreport.rs`:

1. `BugReport` struct with the fields listed in the design.
2. `BugReport::new()` — gathers and serialises all data.
3. `BugReport::write_zip()` — creates the zip file on disk.

### Step 8: Add generate_bug_report() to RyllApp

1. Add `RyllApp::generate_bug_report()` method as described
   in the design section.
2. This method gathers surface pixels, calls
   `BugReport::new()`, determines the output directory, and
   calls `BugReport::write_zip()`.

### Step 9: Build and validate

1. `pre-commit run --all-files` must pass.
2. `make build` must succeed.
3. Add unit tests in `bugreport.rs`:
   - `test_encode_png` — encode a small RGBA image and
     verify the output starts with the PNG magic bytes
     (`\x89PNG`).
   - `test_bug_report_metadata_serialises` — create a
     `ReportMetadata`, serialise, and check expected fields.
   - `test_bug_report_assemble_display` — create a
     `BugReport` with display type and sample data, write
     the zip to a temp directory, then verify the zip
     contains the expected files (`metadata.json`,
     `session.json`, `channel-state.json`,
     `screenshot.png`).  Open the zip and verify
     `metadata.json` contains expected fields.
   - `test_bug_report_assemble_input` — same but for Input
     type (no screenshot expected).
   - `test_chrono_now_format` — verify the output matches
     `YYYY-MM-DDTHH:MM:SSZ` pattern.
   - `test_filename_timestamp` — verify colons are replaced
     with hyphens.
4. `make test` — all tests pass.

### Step 10: Update documentation

1. Update `ARCHITECTURE.md` to describe the bug report
   assembly pipeline and zip structure.
2. Update `AGENTS.md` to list `zip` and `png` dependencies
   and note the `BugReport` type in `bugreport.rs`.
3. Update `README.md` to mention that bug reports can be
   generated (the GUI trigger comes in Phase 4).

## Administration and logistics

### Success criteria

* `zip` and `png` are direct dependencies.
* `BugReport::new()` correctly gathers and serialises
  metadata, session state, channel state, pcap traffic,
  and (for display reports) a PNG screenshot.
* `BugReport::write_zip()` produces a valid zip file
  containing the expected files.
* The zip file is written to the capture directory's
  `bug-reports/` subdirectory when `--capture` is active,
  or the current working directory otherwise.
* `chrono_now()` is shared between `bugreport.rs` and
  `capture.rs` without duplication.
* `RyllApp::generate_bug_report()` is callable (tested
  indirectly via `BugReport` unit tests; full integration
  testing happens in Phase 4 when the GUI trigger exists).
* `pre-commit run --all-files` passes.
* `make build` succeeds on the first attempt.
* All unit tests pass.

### Risks

* **PNG encoding performance**: Encoding a 1920x1080 RGBA
  surface to PNG takes ~50ms on modern hardware.  This runs
  on the UI thread (during `generate_bug_report()`), which
  blocks the egui frame.  A single 50ms stutter when
  generating a bug report is acceptable — the user just
  clicked a button and expects a brief pause.  If this
  becomes a problem, the encoding can be moved to a
  background thread in a later phase.

* **Zip file size**: A 1920x1080 PNG is ~2-5 MB depending
  on content.  Pcap data is up to 12.5 MB per channel.
  JSON files are tiny.  Total zip size is typically under
  15 MB — small enough for email attachments and GitHub
  issue uploads.

* **Pcap availability**: When the `capture` feature is
  disabled (non-default), the ring buffer frames are empty
  and `drain_channel_pcap_bytes()` returns `None`.  The
  `traffic.pcap` file is simply omitted from the zip.
  This is acceptable since `capture` is the default
  feature.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
