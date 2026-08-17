# Troubleshooting

Common issues and how to resolve them.

## Connection Issues

### "Connection refused"

**Symptom:**
```
Error: Connection refused (os error 111)
```

**Causes:**
- SPICE server not running
- Wrong host or port
- Firewall blocking connection

**Solutions:**
1. Verify the SPICE server is running:
   ```bash
   # Check if port is listening
   nc -zv <host> <port>
   ```
2. Check firewall rules on server
3. Verify the .vv file has correct host/port

### "Server requires TLS connection"

**Symptom:**
```
Error: Link error: NeedSecured
```

**Cause:** Server only accepts TLS connections, but you connected to the
insecure port.

**Solution:** Use `tls-port` in your .vv file, or specify both ports with
`--direct`:
```bash
ryll --direct 192.168.1.100:5900:5901
```

### "Authentication failed"

**Symptom:**
```
Error: Authentication failed: PermissionDenied
```

**Causes:**
- Wrong password
- No password provided when required
- Password encoding issue

**Solutions:**
1. Verify password in .vv file is correct
2. Check if server requires a password:
   ```bash
   # In QEMU, check -spice options
   ```
3. Try quoting the password if it contains special characters

### TLS Certificate Errors

**Symptom:**
```
Error: invalid peer certificate: UnknownIssuer
```

**Cause:** Server's TLS certificate isn't trusted.

**Solutions:**
1. The `ca=` field in a .vv file contains inline PEM content
   (with `\n` escape sequences), not a file path. Ensure the
   full certificate is included.
2. Ryll accepts hostname mismatches when a custom CA is provided
   (SPICE self-signed certificates typically lack SAN extensions).

## Display Issues

### "Waiting for display..." stays forever

**Symptom:** GUI shows "Waiting for display..." but never shows content.

**Causes:**
- Server isn't sending display data
- Display channel didn't connect properly
- Decompression errors (check verbose output)

**Solutions:**
1. Enable verbose logging:
   ```bash
   ryll --file test.vv -v
   ```
2. Check that the VM has a display configured
3. Look for decompression errors in the log

### Black or corrupted display

**Symptom:** Window appears but content is black or garbled.

**Causes:**
- Image decompression failing (check for WARN lines in log)
- Unsupported image type (JPEG_ALPHA is not yet implemented)
- GLZ cross-frame dictionary corruption

**Solutions:**
1. Enable verbose logging (`-v`) and check `/tmp/ryll.log` for
   decompression errors or "unsupported image type" warnings
2. Look for "no pixels produced" lines which indicate a draw_copy
   was received but could not be decoded
3. GLZ corruption may appear as random wrong pixels in parts of
   the screen — this is a known issue with the cross-frame
   reference handling

### Streaming indicator

The status bar at the bottom of the window has a small
triangle (▶) glyph immediately to the left of the volume
controls. Its colour reflects the live state of the SPICE
display channel's video-stream path (MJPEG / H.264 / VP8 etc.,
i.e. the `STREAM_CREATE` / `STREAM_DATA` / `STREAM_DESTROY`
flow — not the per-frame draw_copy path).

| Colour | State | Meaning |
| --- | --- | --- |
| Grey | Off | No stream is active and none was destroyed in the last 5 s. The guest is either painting through draw_copy or idle. |
| Green | Active | One or more streams are open. Hover for the per-stream codec, dimensions, decoded-frame count, and lifetime. |
| Amber | RecentlyDestroyed | No active stream, but the server tore one down within the last 5 s. Reverts to grey if no new stream replaces it. |
| Red | Flapping | Three or more streams have been destroyed in the last 30 s with mean lifetime under 3 s. A `Warn` notification also fires once per 60 s while the pattern persists. |

What to put in a bug report when the indicator is amber or red:

1. The session pcap (start ryll with `--capture <dir>` so the
   wire traffic is on disk before the symptom appears).
2. A bug report filed from the notification panel while the
   icon is in the relevant colour — the auto-attached display
   snapshot includes `streams_recently_destroyed`, which is
   what the classifier uses.
3. The guest workload that was running (which application's
   video region the server was promoting to a stream).

The thresholds (3 destroys, 30 s window, 3 s mean lifetime,
60 s notification cool-down) are not yet configurable; they
live in `ryll/src/streaming_state.rs` and are tuned for the
flap pattern seen on QXL guests above the resolution cliff
(streams created and torn down within a few seconds, then
never re-engaged — see
[Libvirt / SPICE server recommendations](/components/ryll/libvirt-spice-recommendations/)).
Open an issue if a different workload trips them too easily
or not easily enough.

### JPEG decoder backend selection

MJPEG video streams are decoded by a per-platform JPEG decoder
selected at display-channel startup. The active backend is
exposed in bug reports as `mjpeg_decoder_backend` in the
`channel-state.json` display-channel snapshot. Selection priority:

- **macOS**: ImageIO (Apple Silicon's dedicated media block
  when available)
- **Windows**: WIC (hardware codec support where available)
- **Linux**: VA-API (hardware-accelerated JPEG via libva,
  probed at runtime if installed)
- **Fallback**: libjpeg-turbo via the vendored `mozjpeg` crate
- **Last resort**: Pure-Rust `jpeg-decoder` crate

If MJPEG video is decoding slowly or using excessive CPU,
check the `mjpeg_decoder_backend` field in a bug report (F12,
Display category). If the backend is `jpeg-decoder` (pure Rust)
on a platform that normally supports hardware acceleration, it
indicates the hardware codec was unavailable at startup. On
Linux, install `libva-dev` and ensure a render node exists at
`/dev/dri/renderD128` for VA-API support.

## Input Issues

### Keyboard input not working

**Symptom:** Key presses in the window don't reach the VM, or keys
are sent but the display never updates in response.

**Causes:**
- Inputs channel didn't connect
- Focus not on the ryll window (click on it first)
- The VM's text field may not have focus -- use Tab to
  navigate to the input field
- Scancode mapping issue for your keyboard layout
- Missing display channel capabilities (if COMPOSITE is not
  advertised, the guest QXL driver uses a slow software
  rendering path that can flood the client with uncompressed
  data, making it appear unresponsive)

**Solutions:**
1. Click on the ryll window to give it OS-level focus
2. Use Tab to move focus to the VM's input field
3. Check `/tmp/ryll.log` for `app: key` lines to confirm
   egui is receiving key events
4. Check for `inputs: key down:` lines to confirm keys are
   being sent to the server
5. Enable verbose logging and check that the display channel
   link negotiation includes COMPOSITE in the advertised
   capabilities

### Mouse not working

**Symptom:** Mouse cursor visible but clicks don't register.

**Causes:**
- Network stalls can cause the input channel to fill with mouse motion
  events, which previously caused button press/release events to be
  silently dropped (fixed in 0.1.2 via motion coalescing)
- Opening a UI dialog (bug report, traffic viewer) while a mouse button
  is held could suppress the release event, leaving the server stuck in
  a "button held" state (fixed in 0.1.2 via synthetic releases)
- Known issue: mouse clicks through kerbside proxy may not
  produce display responses depending on VM/agent config
- The SPICE agent in the VM may not be running

**Solutions:**
1. Use Tab to navigate instead of mouse clicking
2. Check `/tmp/ryll.log` for `inputs: mouse down:` lines
   to confirm clicks are being sent
3. Submit a bug report (F12) with category **Input** — the
   channel-state.json will show whether button events are
   reaching the wire
4. The `tools/test_click.py` script can test click delivery
   independently of ryll

### Session becomes unresponsive after idle period

**Symptom:** After leaving the session idle for a few minutes, all
input (keyboard and mouse) stops working. The display may also
freeze.

**Causes:**
- NAT devices, firewalls, or load balancers can silently drop idle
  TCP connections. Prior to 0.1.2 ryll did not set TCP keepalive,
  so idle channel sockets could be dropped without either end
  detecting it
- The SPICE server pings secondary channels only every 300 s; if
  the TCP path is already broken, the ping never arrives
- Versions before 0.1.6 had a client-side deadlock: after
  roughly 7m45s of idle the main channel task blocked forever on
  an internal event queue whose receiver had been dropped, so
  PONGs stopped and the server tore the session down about 45 s
  later

**Solutions:**
1. Upgrade to 0.1.2+ which enables TCP keepalive (30 s idle,
   3 probes at 15 s) on all channel sockets
2. Upgrade to 0.1.6+ which fixes the idle deadlock (commit
   `370d8ce5`; regression-guarded by `make test-k1-idle`)
3. If the problem persists, check whether a network appliance
   between client and server has an unusually short idle timeout

## USB Issues

### Connection drops when attaching a USB device

**Symptom:** All channels disconnect shortly after a USB device is
connected. The QEMU log may show "usbredirparser: error invalid
packet" or an assertion failure in `redirect.c`.

**Causes:**
- Protocol mismatch between ryll and the QEMU usbredirparser version
- Server rejecting a message with unexpected length or type

**Solutions:**
1. Check the QEMU/libvirt log for the exact error message
2. Submit a bug report — the "Report this as a bug" button appears on
   both USB errors (in the USB panel) and generic channel errors (in
   the main display area)
3. Use `--capture <DIR>` to record pcap traffic for analysis

## Performance Issues

### High CPU usage

**Symptom:** ryll uses excessive CPU even when display is static.

**Causes:**
- Should not happen — the render loop polls at 20 FPS when idle
- May indicate a decompression loop or excessive mouse events

**Solutions:**
1. In headless mode, CPU usage should be near zero
2. Check if the server is sending excessive updates

### High latency

**Symptom:** Noticeable delay between input and display response.

**Causes:**
- Network latency
- Server processing time
- Proxy overhead (if using kerbside)

**Solutions:**
1. Use `--cadence --latency-file latency.csv` to measure
2. Compare with direct connection (no proxy)
3. Check network conditions

## Build Issues

### Missing graphics libraries

**Symptom:**
```
error: failed to run custom build command for `eframe`
```

**Cause:** Missing X11/OpenGL development libraries.

**Solution:** Install required dependencies:
```bash
apt-get install -y \
    libxcb-render0-dev libxcb-shape0-dev libxcb-xfixes0-dev libxcb1-dev \
    libx11-dev libxkbcommon-dev libgl1-mesa-dev libegl1-mesa-dev \
    libwayland-dev libssl-dev pkg-config
```

Or use the devcontainer:
```bash
make build
```

### Binary won't run on another machine

**Symptom:**
```
error while loading shared libraries: libxcb.so.1
```

**Cause:** Target machine is missing required libraries.

**Solution:** See [portability.md](/components/ryll/portability/) for details on binary
compatibility.

## Debugging Tips

### Enable verbose logging

```bash
ryll --file test.vv -v 2>&1 | tee debug.log
```

### Check what channels connected

Look for lines like:
```
INFO Connected to main channel successfully
INFO Connected to display channel successfully
INFO Connected to inputs channel successfully
INFO Connected to cursor channel successfully
```

### Replay draw_copy regions

The `scripts/replay_draw_copy.py` script parses verbose ryll logs
and generates an HTML animation showing where each `draw_copy`
operation landed on the surface. This is useful for diagnosing
rendering issues like misplaced tiles or missing regions.

```bash
ryll --file test.vv -v 2>&1 | tee debug.log
python3 scripts/replay_draw_copy.py debug.log -o replay.html
# Open replay.html in a browser — use Play/Step to animate
```

### Monitor network traffic

```bash
# See SPICE traffic (unencrypted only)
tcpdump -i any port 5900 -w spice.pcap
```

### Inspect a `--capture` pcap

`ryll --capture <dir>` writes one pcap per channel plus a
video of the primary surface. `tools/pcap-inspect.py`
parses those pcaps without needing tshark or scapy:

```bash
# What SPICE message types fired during the session?
tools/pcap-inspect.py opcodes <dir>/display.pcap

# Which image codecs dominate the DRAW_COPY traffic?
tools/pcap-inspect.py draw-copy <dir>/display.pcap

# What did the server send in the last 5 seconds of the
# capture, right after the artefact appeared?
tools/pcap-inspect.py timeline <dir>/display.pcap --since-last 5
```

Handy when the user reports a visual artefact that's too
fast to screenshot: capture with `--capture`, reproduce,
then use the pcap to narrow the window before diving into
source.

### Test with headless mode first

Headless mode eliminates GUI-related issues:
```bash
ryll --file test.vv --headless -v
```

If headless works but GUI doesn't, the issue is in the rendering layer.

## Bug Reports

Ryll has a built-in bug report feature that captures a snapshot of
the client's state at the moment you observe a problem.

### When to use bug reports

Use a bug report when you see:
- Display corruption (garbled pixels, wrong colours, missing regions)
- Input not working (keys or mouse not reaching the VM)
- Unexpected cursor behaviour
- Connection issues that are hard to describe

### How to generate a bug report

1. Press **F12** or click the **Report** button in the status bar.
2. A dialog appears with a privacy warning. Review it — reports may
   contain screen contents, typed keystrokes, and protocol traffic.
3. Select the **report type**:
   - **Display** — captures a screenshot, image cache state, and
     display channel traffic.
   - **Input** — captures keyboard/mouse state and recent events.
   - **Cursor** — captures the cursor cache and position.
   - **Connection** — captures session info and main channel traffic.
   - **USB** — captures usbredir channel and device state.
4. Optionally enter a brief **description** of what you observed.
5. Click **Capture**.

For Display reports, after clicking Capture you enter **region
selection mode**: drag a rectangle over the area of corruption.
A red overlay shows your selection.  Press **Escape** to skip and
capture without highlighting a specific region.

### What the zip file contains

```
ryll-bugreport-YYYY-MM-DDTHH-MM-SSZ.zip
├── metadata.json         — report type, description, ryll
│                           version, platform, target,
│                           timestamp (submit), triggered_at
│                           (dialog-open), submitted uptime
│                           and triggered uptime
├── session.json          — FPS, bandwidth, surfaces, uptime
├── channel-state.json    — snapshot of the affected channel
├── traffic.pcap          — recent protocol traffic (pcap format)
├── screenshot.png        — full display surface captured at the
│                           moment the dialog opened (Display
│                           reports only)
├── screenshot-region.png — crop of the submit-time surface at
│                           the selected region (Display reports
│                           only, when a region was drawn)
└── runtime-metrics.json  — process and per-thread CPU / RSS
```

**Mac-specific verification.** If you are debugging the
runtime-metrics output specifically on a Mac, see
[`macos-metrics-verification.md`](/components/ryll/macos-metrics-verification/)
for a step-by-step acceptance-test runbook and a Mach
port-leak soak procedure.

**Timestamps.** `metadata.json` carries two timestamps:
`timestamp` / `session_uptime_secs` are when the zip was
written; `triggered_at` / `triggered_uptime_secs` are when the
user opened the dialog. For short-lived artefacts the gap
between the two is where you should be looking in
`traffic.pcap` — subtract a few seconds from
`triggered_uptime_secs`.

**Two images for Display reports.** `screenshot.png` is
captured the moment you open the dialog (before the artefact
can fade); `screenshot-region.png` is produced after you drag
the region and shows what was on screen at submit time,
cropped to your selection. Compare the two to see what
changed while you were typing the description.

### Where reports are saved

- If `--capture <DIR>` is active: `<DIR>/bug-reports/`
- Otherwise: the current working directory

### Reading display `channel-state.json` for "video not keeping up"

When a Display bug report is submitted because video appears to
stutter or fall behind, these fields in `channel-state.json` tell
you which part of the pipeline was slow without re-running the
session. They come from the master plan
[`PLAN-video-keeping-up.md`](/components/ryll/plans/PLAN-video-keeping-up/).

- `decode_total_count`, `decode_failed_count`,
  `decode_from_cache_count` — cumulative decode counters since
  the channel started.
- `decode_recent_min_us`, `decode_recent_max_us`,
  `decode_recent_mean_us` — min / max / mean decompression
  wall-time in microseconds over the most recent decodes that
  actually invoked the decoder (cache hits and failures are
  excluded). Read alongside `recent_decodes[].decode_duration_us`
  and `.image_type` to see whether GLZ or JPEG is the slow
  format. A mean above a few milliseconds on a busy session
  points at decode CPU as the bottleneck.
- `socket_read_count`, `socket_reads_at_chunk_cap`,
  `socket_max_chunk_bytes` — how often the channel's 256 KB
  socket read returned full (a proxy for "the OS recv buffer
  had bytes waiting when we read"). A high ratio of
  `socket_reads_at_chunk_cap` to `socket_read_count` indicates
  the read loop is not keeping up with the arrival rate; a low
  ratio means the wire / server is the bottleneck, not us.
- `ack_send_count`, `last_ack_send_ts_secs`,
  `recent_ack_intervals_secs` — ACK cadence. A long tail in
  `recent_ack_intervals_secs` (or a gap between
  `last_ack_send_ts_secs` and `triggered_uptime_secs`) means we
  stopped consuming server messages for that long, which
  applies SPICE-level backpressure on the server.
- `writer_dropped_count` — number of pcap-capture packets the
  dedicated writer task's bounded queue rejected because it
  was full. Zero unless `--capture` is active. A non-zero
  value implicates disk speed (or anything else slowing the
  writer task) rather than decode CPU or socket-read pacing;
  the rest of the SPICE pipeline keeps running because the
  enqueue is non-blocking.

In `session.json` (a sibling artefact in the same zip):

- `video_drop_count` — number of display frames dropped
  because the H.264 encoder task's queue was full when the
  egui frame loop tried to enqueue. Zero unless `--capture`
  is active. A non-zero value implicates encoder CPU (or MP4
  write speed) rather than the SPICE pipeline; the egui frame
  loop stays responsive because the enqueue is non-blocking.
- `image_ready_lag_recent_{min,max,mean}_us` and
  `display_mark_lag_recent_{min,max,mean}_us` — microseconds
  spent waiting in the renderer→app mpsc queue, computed
  over a bounded recent window of samples. `image_ready_*`
  covers per-image emissions (high cadence); `display_mark_*`
  covers per-frame-boundary emissions. A high mean here when
  `channel-state.json`'s `decode_recent_max_us` and
  `socket_reads_at_chunk_cap` look healthy implicates the
  egui loop / GUI thread as the bottleneck — typically a
  long-running synchronous operation inside `App::update`
  starving the event drain. `max` is the most informative
  single number; within-batch samples are correlated so
  `mean` is biased by batch size.

**MP4 finalisation trade-off.** The MP4 moov atom is written
by the encoder task after the sender drops, not synchronously by `CaptureSession::close()`.
In practice the encoder task finalises within milliseconds of
close, but a bug report assembled in a very short window
after a disconnect — or in the SIGINT abrupt-shutdown path —
may see an unfinalised (unplayable) `display.mp4`. The pcap
files and the rest of the report are unaffected.

To read the report:

1. `unzip ryll-bugreport-*.zip` and open `channel-state.json`.
2. Check `decode_recent_*` first — if decode dominates, the
   server is sending fine; look at decoder CPU or compression
   format.
3. If decode is fast, check `socket_reads_at_chunk_cap` vs
   `socket_read_count` — a high ratio means the read loop is
   behind; a low ratio means arrival is the bottleneck.
4. Cross-check `recent_ack_intervals_secs` — flat intervals at
   the expected cadence mean SPICE flow control is healthy;
   long gaps mean we paused.

### Live traffic viewer

Press **F11** or click **Traffic** in the status bar to open a side
panel showing SPICE protocol messages in real time.  This is useful
for live debugging without generating a full bug report.

- Use the channel checkboxes to filter by channel (e.g. hide the
  noisy display channel to focus on inputs).
- Click **Pause** to freeze the display for inspection.

## Playback channel observability

The playback (audio) channel now exposes detailed diagnostics in bug
reports to help characterise silent-audio or stuttering symptoms. If
you file a Connection or Playback-typed bug report (F12), the
`channel-state.json` will include a `playback` section with counters
for every stage of the audio pipeline. Use this section to answer
"where did the audio go?"

**Reading playback diagnostics:**

- **`data_packets_received` > 0**: Server is sending audio DATA packets.
  If this is zero mid-session, the server stopped sending (check whether
  audio is muted on the server, or whether the session is in a paused
  state).

- **`data_packets_decoded` (roughly equal to `data_packets_received`)**:
  Audio decoder is keeping up. If decoded is significantly less than
  received, the decoder is failing or too slow; check `data_packets_decode_failed`.

- **`device_callbacks_total` increasing**: CoreAudio (macOS), WASAPI
  (Windows), or ALSA (Linux) is pulling audio samples from the device.
  If this is flat mid-session, the device has stopped requesting audio
  (device-side problem, not ryll).

- **`device_underrun_count` rising**: The audio pipeline ran out of decoded
  samples when the device asked for them, so silence was fed to the speaker.
  Non-zero = buffer starvation. Cross-check with `data_packets_decoded`;
  if decoded is high but underruns are rising, the ring buffer is too small
  or samples are being dropped upstream.

- **`ring_overflow_count` rising**: Decoded samples were dropped because the
  ring buffer was full. This suggests the device clock has stopped or is
  running much slower than the network; the encoder is ahead of the consumer.

- **`current_session: Some(...) vs None`**: When the session is `None`, audio
  was never started (no SPICE_MSG_PLAYBACK_START received) or was stopped
  (SPICE_MSG_PLAYBACK_STOP received). When present, it includes the sample
  rate, channel count, and codec (Opus or raw PCM) the server declared.

**USB and WebDAV analogues:**

For USB redirect issues, the `usbredir` section includes
`redirected_devices` (list of currently-forwarded devices with vendor/product
IDs and per-device byte counts) and `device_connect_total` /
`device_disconnect_total` (connection event counts). For file-share issues,
the `webdav` section includes `http_requests_received` (HTTP request count)
and `active_session_count` (currently-open connections).

## Guest agent diagnostics

The main channel tracks the responsiveness of the guest agent (vdagent) by
sending periodic liveness probes and measuring the round-trip time of replies.
Every 30 seconds, ryll re-sends the guest agent a `VD_AGENT_MONITORS_CONFIG`
message (the display layout); the agent acknowledges with `VD_AGENT_REPLY`.
The lag between send and reply (in microseconds) measures how quickly the
agent can respond to requests. If the agent fails to reply for more than
5 seconds, a Warn notification appears in the status panel. This mechanism
helps diagnose guest agent freezes without log parsing.

**The probe is dormant until the first `VD_AGENT_MONITORS_CONFIG` send.** That
first send happens on session bring-up once display geometry is known, or on
any window resize. In a freshly-connected session with no resize activity (or
in headless mode where geometry is fixed), `agent_request_count` may stay at
zero for a while before the probe starts firing — that does not indicate an
unhealthy agent.

**Agent reply-lag fields in `MainSnapshot` (visible in Connection bug reports):**

| Field | Meaning |
|-------|---------|
| `agent_request_count` | Cumulative liveness probes sent (increments every ~30s when connected). |
| `agent_reply_count` | Cumulative `VD_AGENT_REPLY` messages received. Should equal `agent_request_count` on a healthy agent (off by at most one in flight). |
| `agent_reply_error_count` | Replies with non-zero error code. Should be zero on a healthy agent. |
| `last_agent_reply_ts_secs` | Session-relative seconds of the most recent REPLY. |
| `last_agent_reply_lag_us` | Microseconds between most recent probe send and matched REPLY. Healthy agents reply in well under 100 ms. |
| `recent_agent_reply_lag_us` | Ring of the last 16 reply-lag measurements (in microseconds) for detecting trends. Use min/max/mean to spot when responsiveness degrades. |
| `outstanding_agent_request_count` | Number of probes sent without a matched REPLY yet. Zero on healthy agents; persistently > 0 indicates a stuck or unresponsive agent. |

**Interpreting Warn notifications:**

When `outstanding_agent_request_count > 0` continuously for more than 5
seconds after a probe, a Warn notification fires every 60 seconds (to keep
the notification panel quiet during sustained stalls). The message reads
like `"Guest agent is not replying — last send was 5.3s ago, 1 request
outstanding"` (elapsed time is formatted to one decimal place; `request`
vs `requests` is pluralised by count). The elapsed time is measured against
the most recent send, not the oldest outstanding request, so the actual
silence may be longer than the number reports. This indicates the guest
agent has stopped responding to configuration requests and may require a
reboot or diagnosis on the server side.

## Auto-snapshot mode for intermittent issues

When you're hunting for an intermittent issue (audio that drops silent for 30
seconds, streams that flap between encodings, latency that spikes mid-session),
it's often too late to hit F12 after the symptom passes. **Auto-snapshot mode**
is a "flight-data-recorder" that fires a complete bug-report zip every N seconds
into a rolling subdirectory, capturing whatever was happening at that moment
regardless of whether you noticed a problem in real time.

### When to enable auto-snapshot mode

- **Intermittent audio issues** — audio works fine for minutes, then goes silent
  for 30 seconds, then returns. You can correlate playback counters across
  snapshots before and after the silence to find where the pipeline broke.
- **Stream flapping** — SPICE streams are constantly created and destroyed,
  causing display lag. Auto-snapshot captures stream-state transitions across
  multiple snapshots.
- **Intermittent latency spikes** — responsiveness drops for 10 seconds then
  recovers. The snapshots before, during, and after the spike show CPU usage,
  decode latencies, and buffer states.
- **Mysterious disconnects** — the session drops unexpectedly and you didn't
  see an error message. Auto-snapshots up to the disconnect provide the
  channel state and traffic at the moment before the fault.

### Usage

```bash
# Fire a snapshot every 30 seconds, keep the last 20 zips (~30 MiB at typical sizes)
ryll --file connection.vv --auto-snapshot-interval 30

# Custom cap and output directory
ryll --file connection.vv --auto-snapshot-interval 60 \
     --auto-snapshot-cap 10 --bug-report-dir /tmp/session-debug
```

At session start, an `Info` notification confirms auto-snapshot mode is enabled
with the interval, cap, and target path. The status bar shows
`Auto-snapshot: N/{cap}` while the mode runs; the counter increments every N
seconds. No per-snapshot notifications are sent so the panel doesn't spam.

### Finding and reading auto-snapshot files

Snapshots are written to `<bug-report-dir>/auto-snapshots/` with filenames that
encode the UTC timestamp and session uptime:

```
ryll-auto-snapshot-2026-05-18T20-37-42Z-T+47.3s.zip
```

Each zip is a complete bug-report artefact containing:
- **`channel-state.json`** — all channels merged with full diagnostics (playback
  counters, stream state, latencies, decoding metrics, etc.)
- **`traffic.pcap`** — raw SPICE traffic covering all channels for the ~N-second
  window preceding the snapshot
- **`metadata.json`** — session context (ryll version, platform, target host)
- **`runtime-metrics.json`** — CPU, memory, and FD usage at snapshot time
- **`notifications.json`** — all in-app notifications (channel events, gaps, etc.)
- **Screenshot** — the display surface at snapshot time (if available)

To diagnose an intermittent issue:
1. Run the session with auto-snapshot enabled
2. After reproducing the symptom, review the snapshots around the time it occurred
3. Compare `channel-state.json` across adjacent snapshots to see what changed
4. Use `tools/pcap-inspect.py` on the `.pcap` files to see what traffic flowed

The rolling cap is enforced by age (oldest files pruned first), so disk usage
stays bounded. At the default cap of 20 zips with typical sizes (~1 MiB each),
you'll use ~20 MiB total.

See [auto-snapshot mode](/components/ryll/diagnostics/#auto-snapshot-mode---auto-snapshot-interval)
in the diagnostics guide for more details on what each field means.

## Display image cache pressure

The SPICE server flags certain decoded image frames with `CACHE_ME` to
reduce bandwidth on future repeated use. Ryll caches these decoded RGBA
frames client-side; without a bound, sustained video playback can cause
the cache to grow unbounded. Measured against full-frame ZlibGlzRgb
video, an uncapped cache grew at 30 MiB/s and reached 2.8 GiB in 90
seconds.

The `--image-cache-cap-mib` flag (default 256 MiB) bounds the cache with
an LRU eviction policy: when the total cached bytes exceed the cap, the
least-recently-used entries are evicted until the cap is satisfied. This
is essential for long-running desktop sessions without risk of OOM.

### Interpreting cache statistics in a bug report

When you file a Display bug report (F12), the `channel-state.json`
includes three cache-related fields under the display channel entry:

- **`image_cache_cap_bytes`** — the configured cap in bytes. This
  confirms what cap the session ran under without re-reading the CLI
  invocation. Multiply by 1,048,576 to convert MiB flags (e.g. `256 MiB
  = 268,435,456 bytes`).

- **`image_cache_evictions_total`** — cumulative count of LRU evictions
  since the session started. High counts indicate the workload is
  churning past the cap; compare this across snapshots to see eviction
  rate. If the eviction count is zero but `image_cache_bytes` is steady
  around the cap, the cache is at equilibrium (most accesses hit
  recently-cached entries).

- **`image_cache_evicted_bytes_total`** — cumulative bytes freed by LRU
  evictions since the session started. Correlate with
  `image_cache_bytes` to assess cache pressure: if
  `image_cache_evicted_bytes_total` is much larger than
  `image_cache_cap_bytes`, the workload is heavily churning past the
  cap; if `image_cache_evicted_bytes_total` is small and
  `image_cache_bytes` is steady well below the cap, the workload is
  not pressuring the cache at all.

### Adjusting the cache cap

**Lower the cap on small-RAM hosts.** Ryll's default is 256 MiB,
suitable for typical 8–16 GiB desktop machines. On a 2 GiB or 4 GiB
embedded system, reduce the cap (e.g. `--image-cache-cap-mib 64` or
`--image-cache-cap-mib 128`) to leave more RAM for other processes.
Monitor auto-snapshots to confirm `image_cache_bytes` never exceeds the
cap and evictions are not excessive.

**Raise the cap for heavy CACHE_ME workloads.** If you are running
sustained video playback (e.g. a full-frame animated desktop) and you
see high `image_cache_evictions_total` across auto-snapshots with
`image_cache_bytes` constantly at the cap, the workload is aggressively
churning. Increase the cap (e.g. `--image-cache-cap-mib 512`) so more
frames stay hot in cache, reducing the decode load on the next replay.
This is a trade-off: larger cache = higher RAM cost but potentially
fewer redecompressions of the same frame.

## Glz dictionary pressure

GLZ ("Generic LZ") is a dictionary-based compression variant SPICE uses
on `Glz` and `ZlibGlz` image payloads. Decoding a GLZ-compressed frame
can reference back-window entries from earlier frames the server told
the client to remember (`IMAGE_FLAGS_CACHE_ME` on the originating
Glz/ZlibGlz payload). The shared GLZ dictionary holds those decoded
entries client-side so subsequent back-references resolve.

The GLZ dictionary was originally an unbounded
`Mutex<HashMap<u64, Vec<u8>>>` — entries were appended on every
`CACHE_ME` payload and removed only on explicit server-driven
`inval_*` messages. Workloads where the server never sent `inval_*`
(notably the full-frame ZlibGlzRgb video fallback observed in
sessions 003a and 004d-g) leaked memory at roughly 30 MiB/s and drove
the multi-GiB RSS runaway that motivated capping it. This
also produced one of the more confusing snapshot readings of the
project: a 5 GiB `image_cache_bytes` value against a 256 MiB cap,
because the snapshot then summed the two caches together (see the
schema-change note below).

The `--glz-dictionary-cap-mib` flag (default 256 MiB; see
[configuration.md](/components/ryll/configuration/)) bounds the dictionary with the
same byte-capped LRU as the image cache: when total entry bytes exceed
the cap, oldest entries are evicted until the cap is satisfied.

### Schema change: caches reported separately

Bug reports written by older ryll builds summed two caches into one
set of fields: `image_cache_bytes`, `image_cache_entries`, and
`image_cache_ids` covered the renderer's `BoundedImageCache` together
with the SPICE `GlzDictionary` decompression cache. This made bug
reports ambiguous: a 5 GiB `image_cache_bytes` reading against a
256 MiB cap — which we have observed in practice — actually came
from the GLZ dictionary, not the image cache, but nothing in the
snapshot distinguished the two.

Current builds report the two separately: the `image_cache_*`
fields reflect only the
`BoundedImageCache` (CACHE_ME-flagged decoded RGBA frames). The GLZ
dictionary's state is reported separately under the new
`glz_dictionary_*` fields described below. As a result,
`image_cache_bytes` in a current bug report will be roughly an order
of magnitude smaller than the same field in an older report under an
equivalent workload; to compare against an older report, add
`image_cache_bytes + glz_dictionary_bytes`.

### Interpreting GLZ dictionary statistics in a bug report

When you file a Display bug report (F12), the `channel-state.json`
includes five GLZ-related fields under the display channel entry:

- **`glz_dictionary_bytes`** — current total bytes held by the GLZ
  dictionary. Should always be at or below `glz_dictionary_cap_bytes`.
  A reading well below the cap means the workload is not GLZ-heavy
  (or `inval_*` traffic is keeping the dictionary trimmed); a reading
  pegged at the cap means the LRU is actively recycling entries.

- **`glz_dictionary_entries`** — current entry count. Read alongside
  `glz_dictionary_bytes` to estimate average entry size (a 256 MiB
  reading with ~25 entries implies ~10 MiB per entry, i.e.
  full-frame RGBA payloads — the symptom of the QXL resolution-cliff
  fallback path).

- **`glz_dictionary_cap_bytes`** — the configured cap in bytes,
  mirroring `--glz-dictionary-cap-mib`. Surfaced so a bug report
  tells you what cap the session ran under without re-reading the
  CLI invocation. Multiply MiB flags by 1,048,576 (e.g. `256 MiB =
  268,435,456 bytes`).

- **`glz_dictionary_evictions_total`** — cumulative count of LRU
  evictions since the session started. High counts indicate the
  workload is churning past the cap. Zero with `glz_dictionary_bytes`
  pinned near the cap means the dictionary is at steady-state where
  server-driven `inval_*` keeps it just at the boundary.

- **`glz_dictionary_evicted_bytes_total`** — cumulative bytes freed
  by LRU evictions. If this is much larger than `glz_dictionary_cap_bytes`,
  the workload has churned through many cap-fulls of GLZ entries;
  if it stays small while `glz_dictionary_bytes` is steady, the
  dictionary is not under pressure.

### Adjusting the GLZ dictionary cap

**Lower the cap on small-RAM hosts.** As with the image cache, the
default 256 MiB suits 8–16 GiB desktops. On embedded or low-RAM hosts,
reduce both caps together (e.g. `--image-cache-cap-mib 64
--glz-dictionary-cap-mib 64`). Monitor auto-snapshots to confirm
`glz_dictionary_bytes` never exceeds the cap and that evictions are
not so aggressive they break GLZ back-references (which would show up
as decode failures or visual corruption in the rendered surface, not
as a counter — file a bug report if you see that pattern).

**Raise the cap for sustained GLZ-heavy workloads.** If
`glz_dictionary_evictions_total` is rising fast across auto-snapshots
with `glz_dictionary_bytes` constantly at the cap, the server is
producing more GLZ back-references than the cap can hold hot.
Raise the cap (e.g. `--glz-dictionary-cap-mib 512`) so more entries
stay resident. The trade-off is exactly the same shape as the image
cache: larger dictionary = higher RAM cost but potentially better
decompression locality.

**Reduce GLZ pressure server-side as well.** Many high-RSS sessions
trace back to the QXL streaming-heuristic cliff at 1600+ pixel-wide
guests, where every video frame falls back to a full-frame ZlibGlzRgb
update. See [`libvirt-spice-recommendations.md`](/components/ryll/libvirt-spice-recommendations/)
for the server-side recommendations (`auto_lz` instead of `auto_glz`,
`streaming-video=filter` instead of `all`, `virtio-vga` instead of
`qxl`) that reduce how often the GLZ path fires in the first place.

## Getting Help

If you can't resolve an issue:

1. Generate a bug report (F12) to capture the current state
2. Collect verbose logs: `ryll --file test.vv -v 2>&1 | tee debug.log`
3. Note the exact error message
4. Note your OS, Rust version, and how you built ryll
5. Open an issue on the GitHub repository with the bug report zip
   and log file attached
