# Phase 6: Testing and documentation

## Overview

Update documentation to describe the new WebDAV folder
sharing feature, verify pcap capture works for the WebDAV
channel, and run the success criteria checklist from the
master plan. No new Rust code in this phase — just docs,
a test script, and verification.

The `test-qemu-webdav` Makefile target was already added
in phase 1. Guest-side end-to-end testing (installing
`spice-webdavd` and `davfs2` in a QEMU VM, mounting the
share, verifying file I/O) is a manual exercise since it
requires a running guest OS. This phase documents the
manual test procedure and updates all project documentation.

## Files changed

| File | Change |
|------|--------|
| `README.md` | Add WebDAV/folder sharing feature, CLI flags, UI description |
| `ARCHITECTURE.md` | Add WebDAV channel section, data flow diagram |
| `AGENTS.md` | Add webdav module to code layout, mention WebDAV patterns |
| `docs/configuration.md` | Add `--share-dir` / `--share-dir-ro` options and examples |
| `docs/plans/PLAN-webdav.md` | Mark phase 6 complete, fill in back brief |

## Detailed steps

### Step 1: Update `README.md`

**Features list** — add a bullet alongside the USB one:

```
- **WebDAV folder sharing** — Share a local directory
  with the guest VM via the SPICE WebDAV channel (folder
  sharing). The guest mounts the share via `spice-webdavd`
  + `davfs2`. Supports read-write and read-only modes.
  Interactive "Folders" panel in the GUI for directory
  selection and share management. CLI flags (`--share-dir`,
  `--share-dir-ro`) for headless/scripted use
```

**GUI features list** — add:

```
- **Folder sharing** — Click "Folders" in the status bar
  for a side panel to select a local directory to share,
  toggle read-only mode, and monitor sharing status with
  elapsed time
```

**Channel support** — update the multi-channel line to
include webdav:

```
main, display, cursor, inputs, usbredir, and webdav
```

**Capture mode** — add `webdav.pcap` to the pcap file list.

**File tree** — add the `webdav/` module:

```
├── webdav/
│   ├── mod.rs           # WebDAV module
│   ├── mux.rs           # Mux protocol (client multiplexing)
│   └── server.rs        # Embedded WebDAV server (dav-server + hyper)
```

And add `channels/webdav.rs` to the channels list.

**Dependencies** — add `dav-server`, `hyper`, `hyper-util`,
`http`, `http-body-util` to the dependencies table.

### Step 2: Update `ARCHITECTURE.md`

Add a new section "## WebDAV Folder Sharing" after the
USB Redirection section. Content:

- Explain that WebDAV uses the SPICE port channel (type 11)
  with SpiceVMC transport (same as usbredir).
- Describe the mux protocol: `[i64 client_id | u16 size |
  data]` framing over the VMC byte stream.
- Describe the per-client architecture: DuplexStream pair,
  hyper HTTP/1.1 framing, dav-server LocalFs backend.
- Include a data flow diagram showing:
  guest → SPICE VMC → demuxer → DuplexStream → hyper →
  dav-server → filesystem → response path back.
- Note the response channel pattern (mpsc from reader
  tasks to main loop, same as usbredir interrupt polling).
- Document the `WebdavServer` struct and its relationship
  to `WebdavChannel`.

Add WebDAV (type 11) to the channel types table.

### Step 3: Update `AGENTS.md`

**Code layout** — add `webdav/` module and
`channels/webdav.rs` to the file tree.

**Design decisions** — add a numbered entry:

```
N. **WebDAV shares local directory via embedded HTTP
   server** — Each mux client gets a `DuplexStream`;
   hyper parses HTTP and dav-server handles WebDAV
   operations. Response data flows back via mpsc channel,
   same pattern as usbredir interrupt polling.
```

### Step 4: Update `docs/configuration.md`

Add a "### Folder Sharing" section after "### USB Device
Redirection":

```
### Folder Sharing

| Option | Default | Description |
|--------|---------|-------------|
| `--share-dir <PATH>` | none | Share a local directory
  with the guest via WebDAV |
| `--share-dir-ro` | false | Make the shared directory
  read-only |
```

Add examples:

```bash
# Share a local directory with the guest
ryll --file test.vv --share-dir /home/user/documents

# Share a directory read-only
ryll --file test.vv --share-dir /home/user/documents \
    --share-dir-ro

# Headless mode with folder sharing
ryll --file test.vv --headless \
    --share-dir /tmp/test-share
```

Add a note about guest requirements:

```
**Guest requirements:** The guest VM needs
`spice-webdavd` installed (from the phodav project) and
the QEMU VM must be configured with a `spiceport` chardev
named `org.spice-space.webdav.0`. See the QEMU
configuration section below.
```

Add a "### QEMU Configuration for Folder Sharing" section
with the QEMU command-line flags and libvirt XML snippet
from the master plan.

### Step 5: Verify success criteria

Walk through each success criterion from the master plan
and verify or note status:

- [x] `--share-dir` CLI flag exists and creates a WebDAV
  server
- [x] `--share-dir-ro` flag restricts to read-only methods
- [x] Mux protocol handles multiple concurrent clients
- [x] Folders UI panel matches USB panel look and feel
- [x] Code passes `pre-commit run --all-files`
- [x] New code follows existing patterns
- [x] Unit tests for mux parsing (15 tests) and WebDAV
  server (6 tests), all passing
- [x] README, ARCHITECTURE, AGENTS updated (this step)
- [x] docs/ updated with configuration (this step)
- [x] WebDAV channel integrates with pcap capture
- [ ] End-to-end test with real guest (manual, deferred to
  first real use)

### Step 6: Mark phase 6 and master plan complete

Update `PLAN-webdav.md` phase table to mark phase 6 as
complete.

## Testing

- `make test` passes.
- `cargo fmt --check` and `cargo clippy -- -D warnings`
  pass.
- Documentation is accurate and consistent across all
  files.

## Back brief

Before executing, please confirm your understanding of:
1. This phase is documentation-only — no Rust code changes.
2. The four documentation files (README, ARCHITECTURE,
   AGENTS, docs/configuration.md) all need WebDAV additions
   in the same style as their existing USB sections.
3. End-to-end guest testing is noted as a manual exercise,
   not automated in this phase.
