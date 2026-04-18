# Deferred debt: collating and paying down outstanding work

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, pcap format,
audio resampling), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

Consult `AGENTS.md` for build commands, project conventions,
code organisation, and a table of protocol reference sources.

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
| 1. ... | PLAN-deferred-debt-phase-01-foo.md | Not started |
```

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Every completed master plan in ryll (9 of 9) and every
standalone follow-up plan (4 of 4) carries deferred work:
correctness bugs, robustness gaps, missing test coverage,
code quality improvements, and documented future features.
These items are scattered across 13 separate plan files,
making it difficult to see the full picture, prioritise
effectively, or make coherent progress.

This plan collates all deferred work into a single
sequenced programme that pays down as much of that debt
as possible. It is organised into phases that group
related work, respect dependencies, and minimise
context-switching. Each phase is independently valuable
-- any prefix of phases leaves the codebase better than
it was.

## Scope

This plan covers the following categories of deferred work:

1. **Correctness bugs** -- code that produces wrong output.
2. **Robustness gaps** -- missing error handling, shutdown,
   platform safety.
3. **Code quality** -- dead code, noisy logging, missing
   helpers, inconsistent patterns.
4. **Test coverage** -- modules with zero or minimal tests.
5. **Documentation** -- gaps in README, ARCHITECTURE, and
   STYLEGUIDE.

This plan deliberately **excludes** future feature work
(new capabilities like multiple USB redirections, WebDAV
extensions, new packaging formats, etc.). Those items are
tracked in their respective master plans and represent new
scope rather than debt against existing functionality.

## Source inventory

The following plans contain deferred items addressed here:

| Source plan | Items taken |
|-------------|-------------|
| PLAN-remaining-issues.md | GLZ corruption, mouse clicks, logging, docs |
| PLAN-display-iteration-followups.md | GLZ retry loop, QUIC tests, missing image types, byte-parsing helpers, DrawCopyBase tests |
| PLAN-pr20-followup.md | Disconnect scope, JPEG panic, modifier keys, dead clipboard event, process::exit, clipboard polling, silent drops, MJPEG logging, test gaps |
| PLAN-pr23-followup.md | Resampler stereo, resampler underrun, closure capture, Linux-only gate, shutdown, device channel mapping, common_client, mutex in callback, cpal upgrade, re-export, mutex recovery, STATS_BAR_HEIGHT, MODE/DATA comments, test gaps |

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Display correctness | [PLAN-deferred-debt-phase-01-display.md](/components/ryll/plans/PLAN-deferred-debt-phase-01-display/) | Complete |
| 2. Audio correctness | [PLAN-deferred-debt-phase-02-audio.md](/components/ryll/plans/PLAN-deferred-debt-phase-02-audio/) | Complete |
| 3. Input and session correctness | [PLAN-deferred-debt-phase-03-session.md](/components/ryll/plans/PLAN-deferred-debt-phase-03-session/) | Complete |
| 4. Robustness and safety | [PLAN-deferred-debt-phase-04-robustness.md](/components/ryll/plans/PLAN-deferred-debt-phase-04-robustness/) | Complete |
| 5. Code quality and cleanup | [PLAN-deferred-debt-phase-05-cleanup.md](/components/ryll/plans/PLAN-deferred-debt-phase-05-cleanup/) | Complete |
| 6. Test coverage | [PLAN-deferred-debt-phase-06-tests.md](/components/ryll/plans/PLAN-deferred-debt-phase-06-tests/) | Complete |
| 7. Documentation | (inline — README.md, ARCHITECTURE.md) | Complete |

### Phase 1: Display correctness

Fix the bugs that produce visually wrong output on screen.

**1a. GLZ cross-frame image corruption**
(from PLAN-remaining-issues.md #1)

The GLZ decompressor's cross-frame reference handling
produces incorrect pixels after incremental updates in
ZLIB_GLZ_RGB regions. This is the most visible correctness
bug in ryll.

- Compare Rust GLZ decompressor against the Python reference
  in kerbside line-by-line, focusing on the cross-image
  reference path (image_dist != 0).
- Cross-check against the JavaScript implementation in
  `/srv/src-reference/spice/spice-html5/`.
- Add logging for cross-frame references to diagnose
  which source images are looked up and whether they
  exist in the cache.
- Fix the root cause (likely in image_dist calculation
  or pixel_offset handling).
- Files: `src/decompression/glz.rs`

**1b. GLZ cross-image retry loop optimisation**
(from PLAN-display-iteration-followups.md)

The polling retry loop (20 retries x 5ms = up to 100ms
latency) should be replaced with `tokio::sync::Notify`
or `tokio::sync::watch` so the decompressor wakes
immediately when another channel inserts the referenced
image into the shared dictionary.

- Files: `src/decompression/glz.rs` (CROSS_REF_MAX_RETRIES
  / CROSS_REF_RETRY_MS constants, ~line 210)
- This is independent of the corruption fix but touches
  the same code, so bundling reduces context-switching.

**1c. Missing image type stubs**
(from PLAN-display-iteration-followups.md)

Add stub handlers (with `warn!` logging) for the four
unsupported image types: LzPalette, FromCacheLossless,
Surface, JpegAlpha. These are uncommon but should fail
gracefully rather than silently.

**1d. Potential panic in DHT JPEG parsing**
(from PLAN-pr20-followup.md #3)

`inject_dht()` trusts segment length fields without bounds
checks, unlike `extract_dht_segments`. A malformed JPEG
from the server could panic.

- Add bounds checks in `inject_dht` matching those in
  `extract_dht_segments`.
- Files: `src/channels/display.rs:67-75`

### Phase 2: Audio correctness

Fix the bugs that produce audibly wrong audio output.

**2a. Resampler stereo corruption**
(from PLAN-pr23-followup.md #1)

The resampler treats samples as a flat mono stream,
interpolating between L and R samples instead of between
time-adjacent samples of the same channel. This produces
corrupted stereo audio.

- Rewrite `Resampler::next_sample` to consume `channels`
  samples at a time and interpolate each channel
  independently.
- Alternative: replace with `rubato` or `dasp` crate.
- Files: `src/channels/playback.rs`

**2b. Resampler underrun buffer pollution**
(from PLAN-pr23-followup.md #2)

On underrun, `next_sample` calls `buffer.push_back(0)`
which inserts fake zeros into the shared ring buffer.
These are later consumed as real data.

- Return 0 (silence) directly without modifying the buffer.
- Files: `src/channels/playback.rs:94-96`

**2c. I16/F32 closure capture inconsistency**
(from PLAN-pr23-followup.md #5)

The F32 branch creates a redundant `vol` clone shadowing
the outer one; the I16 branch uses the original.

- Clone `buffer` and `vol` once before the match, move
  into whichever closure is built.
- Files: `src/channels/playback.rs` `start_audio_output`

**2d. Device channel mapping**
(from PLAN-pr23-followup.md #8)

The audio device is opened with its default channel count
regardless of the source format. Force the output config
to match the source, or implement channel mapping.

### Phase 3: Input and session correctness

Fix bugs in keyboard input, mouse interaction, and
connection lifecycle.

**3a. Modifier keys duplicate events**
(from PLAN-pr20-followup.md #4)

Explicit modifier tracking sends KeyDown/KeyUp based on
`i.modifiers` changes, but `key_to_scancode()` may also
handle modifier keys, producing duplicate events and
stuck modifiers.

- Determine whether `key_to_scancode` handles modifiers.
- If so, filter them from the Key event loop or skip
  modifiers in the explicit tracking path.
- Files: `src/app.rs:755-786`
- Note: same code from PR #22; fix lands once.

**3b. Disconnect event scope**
(from PLAN-pr20-followup.md #2)

Any channel disconnect triggers the disconnect dialog,
including USB and WebDAV channel closes.

- Trigger disconnect dialog only for critical channels
  (Main, Display).
- Exclude Usbredir/Webdav which have their own lifecycle.
- Files: `src/app.rs:601-609`

**3c. Mouse clicks through kerbside proxy**
(from PLAN-remaining-issues.md #2)

Mouse clicks do not produce display responses when
connected through kerbside. Confirmed with the Python
test tool. Root cause unknown.

- Test with virt-viewer or Python ryll client to
  confirm whether clicks work through this kerbside
  instance at all.
- If they work, capture wire traffic and compare against
  ryll's output.
- If they don't, the issue is server-side / proxy-side.
- This is an investigation item; the fix depends on
  what the investigation reveals.

### Phase 4: Robustness and safety

Address missing error handling, unsafe platform
assumptions, and ungraceful shutdown paths.

**4a. Abrupt exit in disconnect dialog**
(from PLAN-pr20-followup.md #6)

`std::process::exit(0)` bypasses destructors, Drop
impls, and buffer flushes. Can corrupt capture files or
bug reports in progress.

- Replace with `ctx.send_viewport_cmd(
  egui::ViewportCommand::Close)` or a flag the run
  loop checks.
- Files: `src/app.rs:1907`

**4b. Audio playback shutdown handling**
(from PLAN-pr23-followup.md #7)

The playback channel's `run()` loop doesn't check
`SHUTDOWN_REQUESTED`.

- Add shutdown check or use `tokio::select!` with a
  shutdown signal, consistent with other channels.

**4cd. Dedicated audio thread**
(from PLAN-pr23-followup.md #6, #10)

Replaces the originally separate items 4c (Linux-only
gate) and 4d (mutex in audio callback) with a single
architectural change. Spawn a dedicated `std::thread`
for audio output so the cpal stream never crosses
thread boundaries (removing `unsafe impl Send`), and
use a lock-free ring buffer (`rtrb`) between the
tokio task and the audio thread (eliminating mutex
contention in the real-time callback).

**4e. Silent drop logging**
(from PLAN-pr20-followup.md #8)

- `build_tcp_frame`: add `warn!` when oversized packets
  are silently dropped
  (`src/capture.rs:128-130`).
- Note: `decode_mjpeg_frame` warning was added in
  phase 1 step 1d.

### Phase 5: Code quality and cleanup

Reduce noise, remove dead code, and improve consistency.

**5a. Verbose logging cleanup**
(from PLAN-remaining-issues.md #3)

Demote the following INFO-level messages to DEBUG:
- `display: draw_copy:` per-message details
- `inputs: key down/up:` per-keystroke logging
- `inputs: mouse down:` per-click logging
- `cursor: init/set/move:` per-message logging
- `app: cursor position:` per-event logging
- `app: cursor shape:` per-event logging
- `LZ4 header:` hex dump

**5b. Dead clipboard event removal**
(from PLAN-pr20-followup.md #5)

`ChannelEvent::ClipboardReceived { text }` is defined
and handled but never sent. Either remove the dead
variant and handler, or route clipboard data through it
(architecturally cleaner -- keeps side effects in the
UI thread).

- Files: `src/channels/mod.rs:77-80`, `src/app.rs`

**5c. Clipboard polling optimisation**
(from PLAN-pr20-followup.md #7)

`poll_host_clipboard` creates a fresh `arboard::Clipboard`
every 500ms.

- Cache the instance as a MainChannel field.
- Handle platform-specific staleness if needed.
- Files: `src/channels/main_channel.rs:653-656`

**5d. playback_client() deduplication**
(from PLAN-pr23-followup.md #9)

`playback_client()` hardcodes match arms instead of
delegating to `common_client()`.

**5e. Byte-parsing helper**
(from PLAN-display-iteration-followups.md)

~30 instances of manual
`u32::from_le_bytes([data[offset], ...])` patterns.
Create a `read_u32_le(data, offset)` helper. Low
priority but improves readability.

- Files: display.rs, main_channel.rs, messages.rs

**5f. Minor consistency items**
(from PLAN-pr23-followup.md #13-17)

- Missing `PlaybackChannel` re-export from `mod.rs` (#15)
- Poisoned mutex recovery applied inconsistently (#16)
- STATS_BAR_HEIGHT hardcoded to 20; consider auto-sizing
  (#17)
- MODE message byte offset needs verification comment
  (#13)
- DATA timestamp skip needs documentation comment (#14)

### Phase 6: Test coverage

Add tests for modules with zero or minimal coverage.
This phase is large and can be split across multiple
commits, one per module.

**6a. QUIC decoder tests**
(from PLAN-display-iteration-followups.md)

`src/decompression/quic.rs` (~1500 lines, zero tests).
- Unit tests with known-good QUIC bitstreams.
- Fuzz-style tests with truncated/corrupted bitstreams.

**6b. JPEG parsing tests**
(from PLAN-pr20-followup.md)

- `extract_dht_segments` and `inject_dht` with valid,
  malformed, and DHT-less JPEG data.
- `decode_mjpeg_frame` with various pixel formats.

**6c. Display message parsing tests**
(from PLAN-display-iteration-followups.md)

- `DrawCopyBase::read()` with variable-length clip rects.
- Agent message handling in `main_channel.rs`.

**6d. Audio tests**
(from PLAN-pr23-followup.md)

- `VolumeControl` (clamping, mute, effective_volume).
- `Resampler` (1:1 ratio, 2:1 upsampling, fractional
  ratios, stereo correctness once phase 2a lands).
- Message parsing (START, MODE, DATA with known payloads).
- Playback channel START/DATA/STOP lifecycle.

**6e. Input and clipboard tests**
(from PLAN-pr20-followup.md)

- Agent data message chunking (messages > 2042 bytes).
- Clipboard grab/request/data round-trip serialisation.
- Keepalive timeout triggering disconnect.
- Modifier key state tracking (transitions, edge cases).
- Stream create/data/destroy lifecycle.

### Phase 7: Documentation

Update project documentation to reflect the current
state of the codebase.

**7a. README.md**
(from PLAN-remaining-issues.md #4)

- Mention ZLIB_GLZ_RGB and corrected image type enum
  values.

**7b. ARCHITECTURE.md**
(from PLAN-remaining-issues.md #4)

- Document the image decompression pipeline (LZ, GLZ,
  ZLIB_GLZ_RGB, LZ4, Pixmap, QUIC, MJPEG).
- Document the audio playback pipeline.

**7c. STYLEGUIDE.md**
(from PLAN-remaining-issues.md #4)

- Note the `data_size` prefix rule: LZ_RGB and GLZ_RGB
  have it, LZ4 does not, ZLIB_GLZ_RGB has its own
  8-byte header.

## Deferred feature work not covered by this plan

The following items from master plan "Future work" sections
are explicitly out of scope. They represent new capabilities
rather than debt, and are better addressed by dedicated
plans when their time comes:

- **USB redirection**: multiple simultaneous redirections,
  USB 3.0 bulk streams, 64-bit IDs, device filtering,
  auto-connect, virtual serial/network/HID, other disk
  formats, isochronous transfers, partition table creation
- **USB UI**: hotplug watcher, multi-redirect UI, filtering
  UI, auto-connect rules UI, transfer stats, keyboard
  shortcut, persist disk paths, UsbredirSnapshot for bug
  reports
- **WebDAV**: multiple shared dirs, clipboard file sharing,
  file change notifications, bandwidth throttling, progress
  indication, access control, kerbside proxy support
- **Packaging**: universal macOS binary, MSI installer, AUR,
  Flatpak/Snap, ARM Linux, Homebrew automation, code
  signing, static musl, reproducible builds
- **Bug reports**: replay mode, auto-detect corruption, USB
  reports, remote submission, annotated screenshots,
  multi-region selection
- **Capture**: Wireshark Lua dissector, --capture-all-draws,
  replay mode, cursor overlay track
- **Cursor**: palette-based types, animated trails,
  client-mode override
- **Crate extraction**: crates.io 0.1.0 publishing, ryll
  crate publishing, WebDAV mux extraction, fuzz testing
- **Audio**: encoder support, cpal upgrade (0.15 -> 0.17)

## Administration and logistics

### Success criteria

We will know when this plan has been successfully
implemented because the following statements will be true:

* GLZ cross-frame corruption is fixed or root-caused.
* Audio resampler produces correct stereo output.
* No duplicate modifier key events are sent to the guest.
* Disconnect dialog only fires for critical channel loss.
* `std::process::exit()` is replaced with graceful shutdown.
* Audio playback respects SHUTDOWN_REQUESTED.
* All INFO-level per-message logging is demoted to DEBUG.
* Dead code (ClipboardReceived variant) is cleaned up.
* QUIC decoder has basic test coverage.
* JPEG parsing has tests including malformed input.
* Audio subsystem has unit tests for core types.
* README, ARCHITECTURE, and STYLEGUIDE are updated.
* `pre-commit run --all-files` passes after every phase.
* Each phase is a self-contained, buildable commit.

### Priority guidance

Phases are ordered by impact:

1. **Display correctness** -- most visible to users.
2. **Audio correctness** -- most audible to users.
3. **Input/session** -- affect interactive usability.
4. **Robustness** -- prevent panics and data loss.
5. **Code quality** -- reduce maintenance burden.
6. **Tests** -- prevent regressions.
7. **Docs** -- help future contributors.

Any prefix of this sequence is a valid stopping point.
Phase 3c (mouse clicks through kerbside) is an
investigation that may require server-side changes and
could be deferred independently.

### Relationship to existing plans

This plan does not supersede the source plans. Those plans
remain the authoritative record of when and why each item
was identified. This plan provides a single execution
sequence for addressing them. When an item from this plan
is completed, the corresponding entry in the source plan
should be marked as resolved.

### Bugs fixed during this work

(none yet)

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
