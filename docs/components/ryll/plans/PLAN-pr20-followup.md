# PR #20 follow-up fixes

## Situation

PR #20 (hhding, merged as ea7b45c) added clipboard support,
MJPEG video streaming, modifier key forwarding, disconnect
handling, keepalive timeout, and several bug fixes. This plan
tracks the follow-up work identified during review and by the
automated reviewer.

Items are grouped by priority. The privacy fix (item 1) was
landed immediately in the same commit as this plan file. The
remaining items are ordered by severity.

## Verified correct (no action needed)

These items were flagged by the auto-reviewer but have been
manually verified against the SPICE protocol specification:

- **Protocol constant changes (auto-review item 2):** Agent
  opcodes 107/108/109/110 verified correct against
  spice-protocol/spice/enums.h. The old values (108-111)
  were off-by-one.
- **Agent data header offset (auto-review item 3):** VDAgentMessage
  header is protocol(u32) + type(u32) + opaque(u64) +
  size(u32) = 20 bytes. Reading size at offset 16 is correct.
  The old code at offset 12 was reading into the opaque field.

## Immediate fix (landed with this plan)

1. **Clipboard content logged at info level (auto-review item 4,
   our review).**
   Clipboard text was logged with the first 16 characters
   visible at `info!` level in both `poll_host_clipboard()`
   and the `VD_AGENT_CLIPBOARD` handler. Clipboard content
   may contain passwords or sensitive data. Fixed by logging
   only byte counts at `info!` level, with no content
   included. Two locations in `src/channels/main_channel.rs`.

## Should fix

2. **Disconnect event fires for any channel (auto-review item 8).**
   `self.connected = false` and the disconnect dialog now
   trigger on ANY channel disconnect, including Usbredir and
   Webdav. A USB device disconnect or WebDAV channel close
   would show the disconnect dialog even though Main and
   Display are still active. Fix: restore the condition to
   only trigger the disconnect dialog for critical channels
   (Main, Display), or at minimum exclude Usbredir/Webdav
   which have their own lifecycle.
   Location: `src/app.rs:601-609`.

3. **Potential panic in DHT JPEG parsing (auto-review item 1).**
   `inject_dht()` trusts segment length fields from JPEG data.
   The `while` loop in `inject_dht` computes `seg_len` from
   `u16::from_be_bytes` but the bounds check
   `i + seg_len <= jpeg.len()` is only present in
   `extract_dht_segments`, not in `inject_dht`'s APP0/APP1
   skip loop. A malformed JPEG from the server could panic.
   Fix: add bounds checks in `inject_dht` matching those in
   `extract_dht_segments`.
   Location: `src/channels/display.rs:67-75`.

4. **Modifier keys may send duplicate events (auto-review
   #9).** The explicit modifier tracking sends KeyDown/KeyUp
   for Ctrl/Shift/Alt based on `i.modifiers` changes. If
   `key_to_scancode()` also handles modifier keys (e.g.
   `Key::LControl`), the guest would receive duplicate events,
   which could cause stuck modifiers. Fix: check whether
   `key_to_scancode` handles modifiers; if so, filter them
   out of the Key event loop, or skip modifiers in the
   explicit tracking path.
   Location: `src/app.rs:755-786`.
   Note: this code was also added by PR #22 (same author,
   same implementation). The fix needs to land in one place.

## Should consider

5. **ClipboardReceived event is dead code (auto-review item 5).**
   `ChannelEvent::ClipboardReceived { text }` is defined and
   handled in `app.rs`, but nothing sends it. The
   main_channel clipboard handler writes directly to the host
   clipboard via arboard. Remove the dead variant and its
   handler, or route clipboard data through it instead of the
   direct arboard call (which would be architecturally
   cleaner — keeping side effects in the UI thread).
   Location: `src/channels/mod.rs:77-80`, `src/app.rs`.

6. **std::process::exit(0) in disconnect dialog (auto-review
   #6, our review).** Abrupt exit without cleanup — no
   destructors, no Drop, no buffer flush. Could corrupt
   capture files or bug reports in progress. Replace with
   graceful shutdown (e.g.
   `ctx.send_viewport_cmd(egui::ViewportCommand::Close)`
   or a flag the run loop checks).
   Location: `src/app.rs:1907`.

7. **Clipboard polling creates new Clipboard every 500ms
   (auto-review item 7, our review).** `poll_host_clipboard`
   creates a fresh `arboard::Clipboard` on each poll. On
   X11/Wayland this involves repeated system calls. Consider
   caching the instance as a MainChannel field. Note: arboard
   instances can become stale on some platforms, so this may
   need platform-specific handling.
   Location: `src/channels/main_channel.rs:653-656`.

8. **build_tcp_frame silently drops oversized packets
   (auto-review item 10).** The guard returns an empty Vec with
   no logging when `ip_payload_len > 65515`. Add a `warn!`
   log so silent data loss in captures is diagnosable.
   Location: `src/capture.rs:128-130`.

9. **Missing CMYK/YCCK handling in MJPEG decoder
   (auto-review item 11).** `decode_mjpeg_frame` returns None
   for unsupported pixel formats with no logging. Add a
   `warn!` in the catch-all branch.
   Location: `src/channels/display.rs:102-116`.

## Test coverage gaps

The auto-reviewer identified missing test scenarios. These
should be addressed as part of the follow-up work or as
dedicated test-writing tasks:

- Unit tests for `extract_dht_segments` and `inject_dht` with
  valid, malformed, and DHT-less JPEG data.
- Unit tests for `decode_mjpeg_frame` with various pixel
  formats (RGB24, L8, edge cases).
- Tests for agent data message chunking (messages larger than
  2042 bytes).
- Tests for clipboard grab/request/data round-trip
  serialisation.
- Tests for the keepalive timeout triggering disconnect.
- Tests for modifier key state tracking (transitions and
  edge cases).
- Tests for stream create/data/destroy lifecycle.

## Administration

### Tracking

Items 2-9 can be addressed in any order. Each is a standalone
fix that can be its own commit or PR. Items 2, 3, and 4 are
the highest priority because they affect correctness.

### Context

This plan was created as part of the follow-up to merging PR
#20. The auto-reviewer's full analysis is available on the PR
page at https://github.com/shakenfist/ryll/pull/20.
