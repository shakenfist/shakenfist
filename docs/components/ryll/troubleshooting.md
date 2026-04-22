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
├── metadata.json       — report type, description, ryll version,
│                         platform, target host/port, timestamp
├── session.json        — FPS, bandwidth, surfaces, uptime
├── channel-state.json  — snapshot of the affected channel
├── traffic.pcap        — recent protocol traffic (pcap format)
└── screenshot.png      — full display surface (Display reports only)
```

### Where reports are saved

- If `--capture <DIR>` is active: `<DIR>/bug-reports/`
- Otherwise: the current working directory

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
