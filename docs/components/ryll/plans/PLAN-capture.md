# Capture mode for protocol and display debugging

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, pcap format,
video encoding), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

Consult `AGENTS.md` for build commands, project conventions,
code organisation, and a table of protocol reference sources.
Key references include `shakenfist/kerbside` (Python SPICE
proxy with protocol docs and a reference client),
`/srv/src-reference/spice/spice-protocol/` (canonical SPICE
definitions), `/srv/src-reference/spice/spice-gtk/`
(reference C client), and `/srv/src-reference/qemu/qemu/`
(server-side SPICE in `ui/spice-*`).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via a table like this in this master plan under the
Execution section:

```
| Phase | Plan | Status |
|-------|------|--------|
| 1. ... | PLAN-capture-phase-01-foo.md | Not started |
```

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Debugging SPICE protocol issues and image corruption in
ryll currently relies on ad-hoc `info!` and `debug!` log
messages. This makes it hard to:

- Compare what ryll sends/receives against a reference
  client like virt-viewer or spice-gtk.
- Identify exactly which decoded tiles are corrupt.
- Share captures with others for analysis.

We need structured, opt-in capture of both protocol traffic
and decoded display frames in standard formats that existing
tools can open.

## Mission and problem statement

Add a `--capture <DIR>` flag that, when specified, writes:

1. **Protocol traffic** as a **pcap file** that Wireshark
   can open and decode. One pcap per channel (main, display,
   cursor, inputs) with fake TCP/IP headers so Wireshark's
   TCP dissector works. A SPICE Wireshark dissector plugin
   can then decode the payloads.

2. **Display frames** as an **MP4 video file** with H.264
   encoding. Each decoded draw_copy tile is composited onto
   the surface, and a video frame is emitted after each MARK
   (frame boundary) message. Variable-rate timestamps so
   idle periods don't waste space.

Both outputs are written incrementally (streaming) so they
work for long sessions without unbounded memory growth.

All capture is opt-in via the `--capture` flag. When not
specified, zero overhead is added to the normal code path.

## Technology choices

### Pcap: `pcap-file` + `etherparse`

Both are pure Rust with no system dependencies.

- `pcap-file` writes pcap/pcapng files incrementally.
- `etherparse` constructs fake Ethernet + IPv4 + TCP
  headers with correct checksums, so Wireshark decodes
  the packets as TCP streams.

Each SPICE channel maps to a unique TCP connection
(distinct fake source port). Sent packets use one
direction, received packets the other.

**Alternative considered**: raw link type without TCP
headers. Rejected because Wireshark's SPICE dissector
expects TCP and the fake headers are cheap to construct.

### Video: `openh264` + `mp4`

- `openh264` bundles Cisco's OpenH264 library (BSD
  licensed, auto-built). No system dependency to install.
  Encodes RGBA frames (after conversion to YUV420) to
  H.264 NAL units.
- `mp4` (v0.14, pure Rust) writes H.264 streams into MP4
  containers with per-frame timestamps via `Mp4Writer`.

Variable-rate timestamps are supported — a frame is only
emitted when the display actually changes (after MARK),
not at a fixed FPS.

**Alternative rejected**: `minimp4` — requires `libclang`
for bindgen, which isn't available in the devcontainer.

**Alternative rejected**: `y4m` — huge files and fixed FPS.

**Alternative rejected**: `rav1e` — AV1 encoding too slow.

## Open questions

- ~~Should we record one video per surface, or composite
  all surfaces into a single video?~~ **Resolved**: record
  a single video from the primary surface (surface 0). If
  multiple surfaces are created, log a warning and skip
  video capture for the additional surfaces.
- ~~Should the pcap include the TLS-encrypted bytes or the
  decrypted SPICE payloads?~~ **Resolved**: decrypted
  payloads only. TLS-level traffic is not useful for
  protocol debugging and we only have access to decrypted
  data in the channel handlers anyway.
- ~~Should we emit a video frame for every draw_copy or
  only on MARK boundaries?~~ **Resolved**: MARK-only.
  MARKs are confirmed to arrive from both local QEMU and
  real servers via kerbside. Add `--capture-all-draws`
  later if intermediate tile states are needed.
- ~~Do the video crates build inside our Docker
  devcontainer?~~ **Resolved**: `openh264` (bundled C,
  auto-builds) and `mp4` (pure Rust) both build
  successfully. `minimp4` was rejected because it needs
  `libclang` for bindgen. Using `mp4` crate (v0.14) for
  MP4 muxing instead. `pcap-file` and `etherparse` also
  build fine (both pure Rust).

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Capture infrastructure | [PLAN-capture-phase-01-infra.md](/components/ryll/plans/PLAN-capture-phase-01-infra/) | Complete |
| 2. Pcap packet capture | [PLAN-capture-phase-02-pcap.md](/components/ryll/plans/PLAN-capture-phase-02-pcap/) | Complete |
| 3. Video frame capture | [PLAN-capture-phase-03-video.md](/components/ryll/plans/PLAN-capture-phase-03-video/) | Complete |
| 4. STYLEGUIDE update | (inline) | Complete |

### Phase 1: Capture infrastructure

- Add `--capture <DIR>` CLI flag to `Args` in `config.rs`.
- Create a `capture` module with a `CaptureSession` struct.
- `CaptureSession::new(dir: PathBuf)` creates the output
  directory and initialises writers.
- `CaptureSession` is wrapped in `Arc<Option<CaptureSession>>`
  and passed to all channel constructors.
- When `None`, all capture methods are no-ops (zero overhead).
- Add `capture::is_enabled() -> bool` global check.
- Store the session start timestamp for relative timing.

### Phase 2: Pcap packet capture

- Add `pcap-file` and `etherparse` dependencies.
- Create `capture::PcapWriter` that wraps `pcap_file::PcapWriter`.
- One pcap file per channel: `main.pcap`, `display.pcap`,
  `cursor.pcap`, `inputs.pcap`.
- Each channel gets a `capture_sent(&[u8])` and
  `capture_received(&[u8])` method call in `send()` and the
  read loop.
- Construct fake TCP/IP headers using `etherparse`:
  - Source IP `10.0.0.1`, dest IP `10.0.0.2`.
  - Source port = channel type (1=main, 2=display, etc),
    dest port = 5900.
  - Sent packets: client → server direction.
  - Received packets: server → client direction.
  - Incrementing TCP sequence numbers.
- Payload is the raw mini-header + message bytes
  (decrypted, post-TLS).
- Timestamps from `Instant::now()` relative to session start.

### Phase 3: Video frame capture

- Add `openh264` and `mp4` dependencies (both verified to
  build in the devcontainer).
- Create `capture::VideoWriter` that:
  - Holds the surface pixel buffer reference.
  - On each MARK message, converts the current surface
    RGBA to YUV420, encodes with openh264, muxes into
    MP4 via the `mp4` crate's `Mp4Writer`.
  - Uses real timestamps so the video plays back at the
    actual speed of the session.
- Output file: `display.mp4` in the capture directory.
- The display channel calls `capture.frame(surface_id,
  &pixels, width, height)` after processing a MARK.

### Phase 4: STYLEGUIDE update

Add a "Capture" section to STYLEGUIDE.md documenting:

- The `--capture` flag and what it produces.
- How to add capture points to new channel handlers.
- The convention that capture methods must be no-ops when
  capture is not enabled (zero overhead).
- File naming conventions in the capture directory.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `--capture /tmp/test-capture` produces a directory with
  pcap files that Wireshark can open and display as TCP
  streams with SPICE payloads.
* The same directory contains an MP4 video that plays back
  the session's display changes at correct timing.
* Without `--capture`, there is zero performance overhead.
* `pre-commit run --all-files` passes.
* The capture works with both `--direct` (local QEMU) and
  `--file` (kerbside TLS) connections.
* `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, and
  `STYLEGUIDE.md` are updated.

### Future work

- Wireshark Lua dissector for SPICE mini-header messages
  (pair with the pcap output).
- `--capture-all-draws` flag to emit video frames for
  every draw_copy, not just MARKs.
- Replay mode: read a pcap and replay the display channel
  without a live server.
- Capture cursor shape changes as a separate overlay
  track in the video.

### Bugs fixed during this work

(none yet)

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
