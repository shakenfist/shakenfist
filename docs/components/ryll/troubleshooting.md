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

**Solutions:**
1. Upgrade to 0.1.2+ which enables TCP keepalive (30 s idle,
   3 probes at 15 s) on all channel sockets
2. If the problem persists, check whether a network appliance
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

# What did the server send in the last 5 seconds before
# you hit F8?
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
session. They were added by the master plan
[`PLAN-video-keeping-up.md`](/components/ryll/../docs/plans/PLAN-video-keeping-up/)
phase 1.

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
  enqueue is non-blocking. Added in phase 2.

In `session.json` (a sibling artefact in the same zip):

- `video_drop_count` — number of display frames dropped
  because the H.264 encoder task's queue was full when the
  egui frame loop tried to enqueue. Zero unless `--capture`
  is active. A non-zero value implicates encoder CPU (or MP4
  write speed) rather than the SPICE pipeline; the egui frame
  loop stays responsive because the enqueue is non-blocking.
  Added in phase 3.
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
  `mean` is biased by batch size. Added in phase 4.

**MP4 finalisation note (phase 3 trade-off).** With phase 3
the MP4 moov atom is written by the encoder task after the
sender drops, not synchronously by `CaptureSession::close()`.
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

## Getting Help

If you can't resolve an issue:

1. Generate a bug report (F12) to capture the current state
2. Collect verbose logs: `ryll --file test.vv -v 2>&1 | tee debug.log`
3. Note the exact error message
4. Note your OS, Rust version, and how you built ryll
5. Open an issue on the GitHub repository with the bug report zip
   and log file attached
