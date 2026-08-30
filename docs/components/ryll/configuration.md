# Configuration

Ryll can be configured via command-line arguments and .vv configuration files.

## Command Line Options

```
ryll [OPTIONS]
```

### Connection Source (one required)

| Option | Description |
|--------|-------------|
| `--url <URL>` | Fetch .vv configuration file from HTTP URL |
| `--file <PATH>` | Load .vv configuration from local file |
| `--direct <CONN>` | Direct connection: `HOST:PORT` or `HOST:PORT:TLS_PORT` |

### Operating Modes

| Option | Default | Description |
|--------|---------|-------------|
| `--headless` | false | Run without GUI (for automated testing) |
| `--cadence` | false | Send automatic keystroke every 2 seconds |

### USB Device Redirection

| Option | Default | Description |
|--------|---------|-------------|
| `--usb-disk <PATH>` | none | Present a RAW disk image as a USB mass storage device (repeatable) |
| `--usb-disk-ro <PATH>` | none | Same as `--usb-disk` but read-only (repeatable) |

### Folder Sharing

| Option | Default | Description |
|--------|---------|-------------|
| `--share-dir <PATH>` | none | Share a local directory with the guest via WebDAV |
| `--share-dir-ro` | false | Make the shared directory read-only |

**Guest requirements:** The guest VM needs `spice-webdavd` installed (from
the phodav project) and `davfs2` for mounting. The QEMU VM must be configured
with a spiceport chardev named `org.spice-space.webdav.0` — see the QEMU
configuration section below.

### Bug Reports and Auto-snapshot Mode

| Option | Default | Description |
|--------|---------|-------------|
| `--bug-report-dir <DIR>` | none | Output directory for manual and auto-snapshot bug reports (falls back to `<capture>/bug-reports/` if `--capture` is set, then current directory) |
| `--auto-snapshot-interval <SECONDS>` | disabled | Enable flight-data-recorder mode: fire a complete bug-report zip every N seconds into `<bug-report-dir>/auto-snapshots/`. Minimum recommended interval is 10 seconds; `0` is rejected at startup |
| `--auto-snapshot-cap <N>` | 20 | Maximum number of auto-snapshot zips to keep on disk; oldest are pruned when capacity is exceeded. A 512 MiB budget for the same directory is enforced alongside it, so the cap you set is an upper bound rather than a promise — a busy session whose zips approach the 50 MiB traffic-ring budget will keep fewer than N |
| `--image-cache-cap-mib <N>` | 256 | Cap the SPICE display image cache at N MiB. The cache holds decoded RGBA frames flagged with CACHE_ME by the server; an LRU evicts oldest entries when the cap is exceeded. Default is enough for typical desktop sessions, but increase it for heavy CACHE_ME workloads (sustained video) or lower it on small-RAM hosts. Must be at least 1 |
| `--glz-dictionary-cap-mib <N>` | 256 | Cap the shared SPICE GLZ decompression dictionary at N MiB. The dictionary holds decoded RGBA entries that the server attached `IMAGE_FLAGS_CACHE_ME` to on Glz / ZlibGlz payloads so cross-frame back-references resolve; without a cap, full-frame ZlibGlzRgb workloads (observed in sessions 003a and 004d-g) grew the dictionary at roughly 30 MiB/s and drove a multi-GiB RSS runaway before the cap existed. An LRU evicts oldest entries when the cap is exceeded. Same trade-off shape as `--image-cache-cap-mib`: lower it on small-RAM hosts, raise it for sustained GLZ-heavy workloads to keep more cross-frame references hot. Must be at least 1 |
| `--pedantic` | false | Write a bug-report zip to `./ryll-pedantic-reports/` (or `--pedantic-dir`) the first time each distinct protocol gap is detected |
| `--pedantic-dir <DIR>` | none | Output directory for pedantic bug reports |

### Web Mode

`--web` runs ryll as a SPICE-to-browser transcoder instead of opening a GUI
window. The operator guide, including reverse-proxy and TLS setup, is
[web-frontend.md](/components/ryll/web-frontend/); this table is the flag reference.

| Option | Default | Description |
|--------|---------|-------------|
| `--web` | false | Run as a SPICE → browser transcoder. Serves a browser shell that consumes the SPICE display via WebRTC, and prints a URL carrying a per-launch random token. Mutually exclusive with `--headless` and the GUI default |
| `--web-host <ADDR>` | `127.0.0.1` | Bind address for the HTTP/HTTPS signalling listener only. Use `0.0.0.0` for LAN access. Has no effect on which addresses WebRTC media binds or advertises — that is `--web-media-addr` |
| `--web-port <PORT>` | 0 (ephemeral) | TCP port for the signalling listener |
| `--web-tls-cert <PATH>` | none | PEM-encoded TLS certificate chain. Serving over HTTPS requires `--web-tls-key` as well |
| `--web-tls-key <PATH>` | none | PEM-encoded TLS private key. Required with `--web-tls-cert` |
| `--web-media-addr <ADDR\|IFACE>` | every non-loopback interface address | Local address, or interface name, to bind the WebRTC media (UDP) sockets to. Repeatable. Naming addresses explicitly also overrides the default exclusion of loopback, which is how a loopback-only host is served (`--web-media-addr 127.0.0.1`). The unspecified addresses (`0.0.0.0`, `::`) and zoneless `fe80::/10` are refused at startup: neither can become an ICE candidate a browser will use |
| `--web-media-port <PORT>` | 0 (ephemeral) | UDP port for the media sockets. Pin it so a firewall rule can name one port instead of the OS's whole ephemeral range. Applies to every bound address; a port already in use fails loudly rather than falling back to an ephemeral one, since a silent fallback would no longer match the firewall rule |
| `--web-ice-server <URL>` | none | STUN or TURN server URL (`stun:host:port`, `turn:host:port`). Repeatable. Empty by default: ryll assumes browser and host share a LAN, so ICE host candidates are usually enough. The scheme must be `stun:`, `stuns:`, `turn:` or `turns:` and is checked at startup, so a URL with the scheme left off fails the launch rather than producing an ICE server that silently does nothing |

### Capture and Debugging

| Option | Default | Description |
|--------|---------|-------------|
| `--capture <DIR>` | none | Write pcap + video capture to directory |
| `-v, --verbose` | false | Enable debug-level logging |
| `--latency-file <PATH>` | none | Write latency measurements to CSV file |

### Examples

```bash
# Connect using a .vv file from a URL
ryll --url https://cloud.example.com/console/vm-123.vv

# Connect using a local .vv file
ryll --file ~/downloads/myvm.vv

# Direct connection to a SPICE server (insecure)
ryll --direct 192.168.1.100:5900

# Direct connection with TLS
ryll --direct 192.168.1.100:5900:5901

# Headless mode for automated testing
ryll --file test.vv --headless

# Performance testing with latency tracking
ryll --file test.vv --cadence --latency-file latency.csv

# Capture protocol traffic and display video
ryll --file test.vv --capture /tmp/capture

# Verbose output for debugging
ryll --file test.vv -v

# Attach a RAW disk image as a USB flash drive
ryll --file test.vv --usb-disk /path/to/image.raw

# Attach a read-only USB disk
ryll --file test.vv --usb-disk-ro /path/to/image.raw

# Headless mode with USB disk
ryll --file test.vv --headless --usb-disk /tmp/test.raw

# Share a local directory with the guest
ryll --file test.vv --share-dir /home/user/documents

# Share a directory read-only
ryll --file test.vv --share-dir /home/user/documents --share-dir-ro

# Headless mode with folder sharing
ryll --file test.vv --headless --share-dir /tmp/test-share

# Enable auto-snapshot mode: fire a bug report every 30 seconds, keep last 20
# (or fewer, if 20 of them would exceed the 512 MiB directory budget)
ryll --file test.vv --auto-snapshot-interval 30

# Custom output directory and rolling cap
ryll --file test.vv --auto-snapshot-interval 30 \
     --auto-snapshot-cap 10 --bug-report-dir /tmp/session-debug

# Pedantic mode: report on protocol gaps
ryll --file test.vv --pedantic
```

## .vv File Format

The .vv (virt-viewer) file is an INI-format configuration file. Ryll supports
the standard virt-viewer format used by `remote-viewer` and other SPICE clients.

### Basic Format

```ini
[virt-viewer]
type=spice
host=192.168.1.100
port=5900
```

### Supported Fields

| Field | Required | Description |
|-------|----------|-------------|
| `host` | Yes | SPICE server hostname or IP address |
| `port` | No* | SPICE insecure port (usually 5900+) |
| `tls-port` | No* | SPICE TLS port for secure connections |
| `password` | No | SPICE password for authentication |
| `ca` | No | Inline PEM CA certificate for TLS verification |
| `host-subject` | No | Server certificate subject; enforced -- the connection fails if the server's certificate subject does not match, and a malformed value is rejected at startup |

### Ticket lifecycle keys

ryll also reads two ticket-related keys with ryll-specific behaviour:

- **`delete-this-file=1`** — the standard "remove this file
  after reading" hint. ryll additionally treats this as a
  signal that the SPICE ticket is **single-use**: any
  reconnect attempt would be rejected by the server, so
  auto-reconnect is suppressed and a "single-use ticket"
  modal is shown instead of the normal retry sequence.
- **`ticket-valid-until=<unix-ts>`** — a ryll extension key
  (no equivalent in remote-viewer). When set, ryll surfaces a
  T-30s warning notification, suppresses auto-reconnect once
  the deadline has passed, and shows a "ticket expired" modal
  with the expiry time.

Both keys degrade gracefully — absent values mean the previous
ryll behaviour. Producer-side documentation lives in the
companion doc
[`console-vv-extensions.md`](https://github.com/shakenfist/kerbside-wt-docs/blob/main/docs/spice/console-vv-extensions.md)
in the kerbside-wt-docs repository, which is the canonical
reference for SPICE deployment authors (Kerbside, oVirt,
custom gateways) who want their .vv output to drive ryll's
reconnect UX correctly.

### Example .vv Files

**Minimal (insecure connection):**
```ini
[virt-viewer]
type=spice
host=192.168.1.100
port=5900
```

**With password:**
```ini
[virt-viewer]
type=spice
host=192.168.1.100
port=5900
password=mysecretpassword
```

**TLS connection with inline CA (as generated by Shaken Fist):**
```ini
[virt-viewer]
type=spice
host=spice.example.com
tls-port=5901
ca=-----BEGIN CERTIFICATE-----\nMIIE...(base64)...\n-----END CERTIFICATE-----\n
```

Note: the `ca=` field contains the PEM certificate inline with `\n`
escape sequences for newlines, not a file path. Either `port` or
`tls-port` (or both) must be specified.

**Full configuration:**
```ini
[virt-viewer]
type=spice
host=spice.example.com
port=5900
tls-port=5901
password=mysecretpassword
ca=-----BEGIN CERTIFICATE-----\nMIIE...\n-----END CERTIFICATE-----\n
host-subject=CN=spice.example.com
```

Note: `host-subject` must match the server certificate's subject
exactly, including attribute count, order, and type -- not just the
`CN` value.

## Keyboard Shortcuts

These shortcuts are available during a GUI session. They are consumed
by ryll and not forwarded to the guest VM.

| Shortcut | Action |
|----------|--------|
| F11 | Toggle the live traffic viewer side panel |
| F12 | Open / close the bug report dialog |
| Escape | Close the bug report dialog, or skip region selection |

## Environment Variables

Currently, ryll does not use environment variables for configuration. All
settings are provided via command-line arguments or .vv files.

## Logging

Ryll uses the `tracing` crate for logging. By default, INFO-level messages
are shown. Use `-v` or `--verbose` for DEBUG-level output.

Log output goes to stderr, so you can redirect stdout for data output:

```bash
ryll --file test.vv --headless 2>debug.log
```

## Latency File Format

When `--latency-file` is specified, ryll writes latency measurements in CSV
format:

```csv
timestamp,latency_ms
1706540400.123,45.2
1706540402.125,43.8
1706540404.127,44.1
```

- **timestamp**: Unix timestamp of the measurement
- **latency_ms**: Time from keystroke to display update in milliseconds

This is primarily useful in cadence mode, where keystrokes are generated at
known intervals.

## USB Device Redirection

Ryll can present a RAW disk image as a USB mass storage device to the
remote VM. The guest OS sees a standard USB flash drive and can partition,
format, mount, read, and write it.

### Creating a Test Image

```bash
# Create a 64MB empty image
dd if=/dev/zero of=test.raw bs=1M count=64

# Optionally format it with a filesystem
mkfs.ext4 test.raw
```

### QEMU Requirements

The SPICE server (QEMU) must have USB redirection enabled:

```
-device qemu-xhci,id=xhci
-chardev spicevmc,id=usbredir1,name=usbredir
-device usb-redir,chardev=usbredir1,id=redir1
```

Use `make test-qemu-usb` to start a pre-configured QEMU instance.

### Usage

```bash
# GUI mode
ryll --direct localhost:5900 --usb-disk test.raw

# Headless mode
ryll --direct localhost:5900 --headless --usb-disk test.raw

# Read-only (guest cannot write to the image)
ryll --direct localhost:5900 --usb-disk-ro test.raw
```

### Notes

- Only the first `--usb-disk` / `--usb-disk-ro` is connected (SPICE
  supports one device per usbredir channel).
- The image file must be at least 512 bytes.
- If the file size is not a multiple of 512, trailing bytes are
  inaccessible (a warning is logged).
- The device auto-connects when the usbredir channel's hello exchange
  completes.
- Use `--capture <DIR>` to capture usbredir protocol traffic to
  `usbredir.pcap` for debugging.

### USB Panel (GUI Mode)

In GUI mode, click the **USB** button in the status bar to open the USB
device management panel. The panel shows:

- **Channel status** — whether a usbredir channel is available from the
  SPICE server.
- **Connected device** — the currently redirected device with elapsed
  connection time.
- **Device list** — all available USB devices (physical and virtual).
  Physical devices are enumerated from the host via nusb. Virtual devices
  come from `--usb-disk` flags and runtime additions. Each device has a
  Connect or Disconnect button.
- **Add Disk...** — opens a native file picker to add a RAW disk image as
  a virtual USB device for the current session. A "Read-only" checkbox
  controls write access. The file is validated (must be a regular file,
  >= 512 bytes).
- **Refresh** — re-enumerates available devices. Useful if USB devices
  were plugged in or removed after the panel was opened.
- **Error display** — connection failures appear in red with a Dismiss
  button and a "Report this as a bug" button that opens the bug report
  dialog pre-populated with the USB error and usbredir channel context.

The panel can be open simultaneously with the Traffic viewer panel.
Devices added via the panel persist for the session but are not saved
across restarts — use `--usb-disk` for persistent configuration.

Physical USB device passthrough requires the host to have accessible USB
devices (appropriate permissions, not claimed by a kernel driver). The
panel enumerates whatever nusb can see — if the list is empty, check
host USB permissions.

## Folder Sharing (WebDAV)

### GUI Panel

Click "Folders" in the status bar to open the Folders panel. It shows:

- **Channel status** — whether the SPICE WebDAV channel is connected.
- **Active share** — the shared directory path, read-only status, and
  elapsed time since sharing started.
- **Share Directory...** — opens a native directory picker to select a
  folder to share. A "Read-only" checkbox controls write access.
- **Stop Sharing** — stops the current share and disconnects all clients.
- **Error display** — errors appear in red with a Dismiss button and
  auto-clear after 10 seconds.

### QEMU Configuration

The QEMU VM must have a spiceport chardev for folder sharing:

```
-device virtio-serial-pci,id=virtio-serial0
-chardev spiceport,name=org.spice-space.webdav.0,id=webdav0
-device virtserialport,chardev=webdav0,name=org.spice-space.webdav.0
```

Or in libvirt domain XML:

```xml
<channel type='spiceport'>
  <source channel='org.spice-space.webdav.0'/>
  <target type='virtio' name='org.spice-space.webdav.0'/>
</channel>
```

Use `make test-qemu-webdav` to start a pre-configured QEMU instance.

### Guest Requirements

The guest needs:
- **spice-webdavd** — daemon that bridges the SPICE channel to a local
  WebDAV endpoint (from the phodav project, `apt install spice-webdavd`)
- **davfs2** — to mount the WebDAV share as a filesystem
  (`apt install davfs2`)
