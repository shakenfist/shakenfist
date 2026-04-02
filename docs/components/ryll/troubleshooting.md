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
- Unsupported image type (QUIC, JPEG, JPEG_ALPHA are not yet
  implemented)
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

**Symptom:** Key presses in the window don't reach the VM.

**Causes:**
- Inputs channel didn't connect
- Focus not on the ryll window (click on it first)
- The VM's text field may not have focus — use Tab to
  navigate to the input field
- Scancode mapping issue for your keyboard layout

**Solutions:**
1. Click on the ryll window to give it OS-level focus
2. Use Tab to move focus to the VM's input field
3. Check `/tmp/ryll.log` for `app: key` lines to confirm
   egui is receiving key events
4. Check for `inputs: key down:` lines to confirm keys are
   being sent to the server

### Mouse not working

**Symptom:** Mouse cursor visible but clicks don't register.

**Causes:**
- Known issue: mouse clicks through kerbside proxy may not
  produce display responses depending on VM/agent config
- The SPICE agent in the VM may not be running

**Solutions:**
1. Use Tab to navigate instead of mouse clicking
2. Check `/tmp/ryll.log` for `inputs: mouse down:` lines
   to confirm clicks are being sent
3. The `tools/test_click.py` script can test click delivery
   independently of ryll

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

### Monitor network traffic

```bash
# See SPICE traffic (unencrypted only)
tcpdump -i any port 5900 -w spice.pcap
```

### Test with headless mode first

Headless mode eliminates GUI-related issues:
```bash
ryll --file test.vv --headless -v
```

If headless works but GUI doesn't, the issue is in the rendering layer.

## Getting Help

If you can't resolve an issue:

1. Collect verbose logs: `ryll --file test.vv -v 2>&1 | tee debug.log`
2. Note the exact error message
3. Note your OS, Rust version, and how you built ryll
4. Open an issue on the GitHub repository
