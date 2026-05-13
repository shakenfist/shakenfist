# Phase 01: Auto-snapshot ring buffer at disconnect moment

## Prompt

Before answering questions or making design decisions in this
document, read the relevant ryll source. Key files:
`ryll/src/app.rs` (the `ChannelEvent` handler that routes
disconnects to the modal), `ryll/src/bugreport.rs` (the existing
`BugReport::write_zip` and `write_pedantic` paths),
`shakenfist-spice-renderer/src/snapshots.rs` (per-channel
diagnostic snapshots already plumbed into bug reports), and the
disconnect signal sites listed in the Situation section.
Consult `ARCHITECTURE.md` for channel and event flow, and
`AGENTS.md` for build and test conventions.

This phase is the first phase of `PLAN-session-001-feedback.md`
and gates Phase 02 (the K1 reconnect / keepalive fix). The
goal is **data, not behaviour change** — we add an automatic
bug-report write on disconnect plus a structured cause record,
so the next time the user hits the rcc-30 s timeout we have a
report whose pcap actually contains the run-up to the failure
(the existing session-001 reports captured only post-reconnect
activity).

One commit, self-contained: must build, lint, and pass tests.
Do not bundle unrelated changes.

## Situation

**Disconnect signals already funnel through two `ChannelEvent`
variants** handled centrally at `ryll/src/app.rs`:

- `ChannelEvent::Error(String)` — `app.rs:1091`. Used for
  generic per-channel transport errors (e.g., the inputs read
  loop at `inputs.rs:182`).
- `ChannelEvent::Disconnected(ChannelType)` — `app.rs:1178`.
  Used by every channel on EOF / clean close / our own
  client-side timeout.

Both currently end up flipping `self.connected = false`,
clearing surfaces, and setting `show_disconnect_dialog = true;
disconnect_reason = Some(...)`. This is the **single hook
point** for auto-snapshot.

**Disconnect signal sites** (verified by grep, every channel):

- `main_channel.rs:237` — read loop EOF
- `main_channel.rs:286` — 30 s client-side keepalive timeout
  fires (this is the one most likely to be the culprit per Q2)
- `main_channel.rs:598` — explicit disconnect path
- `display.rs:598` — read loop EOF
- `inputs.rs:171` — read loop EOF
- `inputs.rs:182` — read loop transport error (uses
  `ChannelEvent::Error`)
- `cursor.rs:94`, `playback.rs:477`, `usbredir.rs:143`,
  `webdav.rs:189` — non-critical channels (see filter at
  `app.rs:1199`; their disconnects don't pop the modal today)
- `session.rs:345` — link-establishment error early in the
  session

**Existing per-channel snapshots** (`snapshots.rs:35–104`):
each has `bytes_in` / `bytes_out` and (for display/cursor)
`message_count`, but **none track**:
- last received message timestamp,
- last sent message timestamp,
- ping / pong counts,
- last received PING timestamp.

These are exactly the fields a disconnect-cause analysis needs.

**Bug-report assembly already exists** at
`bugreport.rs:954` (`write_zip`) and
`bugreport.rs:1011` (`write_pedantic`) — both already produce a
zip with `metadata.json` / `session.json` / `channel-state.json`
/ `notifications.json` / `runtime-metrics.json` plus an
optional `traffic.pcap`. We extend the same plumbing rather
than duplicate it.

**The PONG path already runs in every channel** (verified
earlier: `main_channel.rs:533`, `inputs.rs:366`, `display.rs:870`,
`cursor.rs:297`, `playback.rs:549`, `usbredir.rs:236`,
`webdav.rs:282`). Each is the right place to bump per-channel
PING/PONG counters and timestamps.

## Mission and problem statement

When ryll observes a disconnect on **any** channel, automatically
assemble and write a bug-report zip to the configured output
directory **before** showing the disconnect modal (where
applicable) or attempting any reconnect. The zip must contain:

- everything a regular bug report contains, including the
  per-channel pcap covering the run-up to the failure,
- a new `disconnect-cause.json` with the structured record
  detailed under "Approach" below,
- a description distinguishing it from F8-triggered or
  pedantic reports.

Subsequent disconnects within a 60 s cooldown window must not
produce additional zips (avoid disconnect-storm flooding).

**Why every channel, not just critical ones.** In ticket-based
deployments (oVirt, Kerbside), the server validates the SPICE
ticket on every channel link establishment (`reds.cpp:2098`,
`expired = (taTicket.expiration_time < now)`). Tickets are
typically minutes or single-use, so once one expires no new
channel link succeeds. spice-gtk has no per-channel
auto-reconnect path either — its `SPICE_CHANNEL_STATE_RECONNECTING`
at `spice-channel.c:1987` is only the TLS-upgrade dance, not a
retry on transport drop. Consequently a dropped non-critical
channel (cursor / playback / usbredir / webdav) is **permanently
gone for the session** — the user silently loses audio / USB
redirection / etc. with no diagnostic. We want a snapshot for
those too, even though the existing critical-only modal at
`app.rs:1199` stays unchanged in this phase.

Behaviour change is strictly limited to "we now write a zip on
any channel disconnect". The existing modal flow and the manual
reconnect button at `app.rs:3127` are untouched. Surfacing
non-critical drops to the user (e.g., a notification "audio
channel lost — reconnect to restore") is Phase 09's job (F1).

## Approach

### 1. Extend per-channel snapshots with disconnect-relevant fields

Add the following fields to each `*Snapshot` struct in
`shakenfist-spice-renderer/src/snapshots.rs`:

```rust
pub last_recv_ts_secs: Option<f64>,
pub last_send_ts_secs: Option<f64>,
pub ping_recv_count: u32,
pub pong_send_count: u32,
pub last_ping_recv_ts_secs: Option<f64>,
```

Apply to **every channel snapshot**: `MainSnapshot`,
`DisplaySnapshot`, `InputsSnapshot`, `CursorSnapshot`, plus a
new `PlaybackSnapshot`, `UsbredirSnapshot`, `WebdavSnapshot` if
those don't already exist. Without these fields on non-critical
channels the `disconnect-cause.json` for, say, a cursor-channel
drop would have no per-channel data for the channel that
actually dropped — exactly the diagnostic we need.

Check whether snapshot structs exist for the non-critical
channels before defining new ones. If they do, extend in place;
if not, add minimal structs with at least
`bytes_in` / `bytes_out` plus the five disconnect fields, and
plumb them through `ChannelSnapshots` at `snapshots.rs:108`
alongside the existing four.

Each channel's read loop updates `last_recv_ts_secs` on every
parsed message (cheap — one `Instant::now().duration_since(...)`
per message); the send paths update `last_send_ts_secs`; the
PING handlers bump the two counters and `last_ping_recv_ts_secs`.

Time basis: session-relative seconds, matching the existing
`recent_decodes` and `recent_events` timestamps. The session's
start `Instant` is already plumbed (`TrafficBuffers::start` at
`bugreport.rs:206` and the per-channel handlers receive
equivalents).

### 2. Capture keepalive-timeout-fired status

The 30 s client-side keepalive at `main_channel.rs:283` fires
`ChannelEvent::Disconnected(ChannelType::Main)` — same signal
as a real EOF. To distinguish, add a thread-safe flag (e.g.
`AtomicBool` inside `MainSnapshot`) named
`keepalive_timeout_fired` set to `true` immediately before the
event is sent (`main_channel.rs:285`). Other disconnect paths
on main leave it `false`.

Reject the temptation to add a new `ChannelEvent` variant —
the event type's signature is wide and a new variant churns
many call sites. The flag-on-snapshot approach is read at
disconnect-handling time alongside everything else.

### 3. Add `DisconnectCause` and serialise to JSON

In `ryll/src/bugreport.rs`, add a new type:

```rust
#[derive(Debug, Serialize)]
pub struct DisconnectCause {
    pub channel: String,           // "main" / "display" / "inputs" / "error"
    pub error_message: String,     // The raw error or reason string
    pub error_kind: Option<String>, // io::ErrorKind debug string when known
    pub keepalive_timeout_fired: bool,
    pub session_uptime_secs: f64,
    pub per_channel: BTreeMap<String, PerChannelDiagnostics>,
}

#[derive(Debug, Serialize)]
pub struct PerChannelDiagnostics {
    pub last_recv_ts_secs: Option<f64>,
    pub last_send_ts_secs: Option<f64>,
    pub ping_recv_count: u32,
    pub pong_send_count: u32,
    pub last_ping_recv_ts_secs: Option<f64>,
    pub bytes_in: u64,
    pub bytes_out: u64,
}
```

`per_channel` covers every channel ryll runs (main, display,
inputs, cursor, playback, usbredir, webdav). The map is keyed
by channel name and lists every channel that was alive at the
moment of failure, regardless of which one fired the
disconnect. This lets a maintainer compare the dropped channel
against the still-live ones — e.g., did *only* cursor go quiet,
or did everything go quiet at the same time?

### 4. Add `BugReport::write_disconnect`

A sibling of `write_pedantic` at `bugreport.rs:1011`:

```rust
pub fn write_disconnect(
    dir: &std::path::Path,
    cause: DisconnectCause,
    target_host: &str,
    target_port: u16,
    traffic: &TrafficBuffers,
    channel_snapshots: &ChannelSnapshots,
    app_snapshot: &Mutex<AppSnapshot>,
    notifications: &Mutex<NotificationStore>,
    runtime_metrics: RuntimeMetrics,
) -> anyhow::Result<std::path::PathBuf>
```

Filename: `ryll-disconnect-{filename_timestamp}.zip`.
Description: `format!("auto: connection lost on {} ({})",
cause.channel, cause.error_message)`. Otherwise mirrors
`write_pedantic`'s assemble-and-write flow, plus
`zip.start_file("disconnect-cause.json", opts)?` writing
`serde_json::to_string_pretty(&cause)?`.

The pcap selection: include the **critical channel that fired
the disconnect**, not all channels. Matches the existing
per-channel pcap convention (each report carries one channel's
pcap). If `cause.channel == "error"` (raw `ChannelEvent::Error`
without channel attribution), skip the pcap rather than guess.

### 5. Hook into the central event handler

At `app.rs:1091` (`ChannelEvent::Error`) and `app.rs:1178`
(`ChannelEvent::Disconnected`), call a new helper
`self.maybe_write_disconnect_snapshot(channel, msg)`
**before** the existing state-mutation logic — and crucially,
do this for **every** channel, not just the critical-channel
branch. That means:

- For `ChannelEvent::Error(msg)` at `app.rs:1091`, call the
  helper with `channel = "error"` and the message.
- For `ChannelEvent::Disconnected(channel)` at `app.rs:1178`,
  call the helper with the channel name *outside* the
  `match channel` block at `app.rs:1199` that decides modal vs
  non-modal — so cursor / playback / usbredir / webdav
  disconnects also produce snapshots while still **not** popping
  the modal (current behaviour preserved).

The helper:
- checks `last_disconnect_report_at` cooldown (60 s),
- assembles a `DisconnectCause` from current snapshots,
- calls `BugReport::write_disconnect` to the **same output
  directory used by F8 reports** — see "Output directory" below,
- on success, pushes a notification (severity=Info, source=
  BugReport) "Disconnect snapshot saved to {path}" mirroring
  the existing pedantic-report notification (`bugreport.rs`
  near where pedantic notifications are pushed),
- on failure, logs the error at `error!` level but **does not
  block the disconnect modal** — best-effort.

### 6. Output directory

ryll today has three bug-report destinations:

- **F8 reports** computed inline at `app.rs:1510-1514`:
  `<--capture>/bug-reports/` if `--capture` is set, else cwd.
- **Pedantic reports** controlled by `--pedantic-dir`
  (`config.rs:84`, default `./ryll-pedantic-reports`).
- (After this phase) **Auto-disconnect snapshots** — needs a
  destination too.

This phase adds a single `--bug-report-dir <DIR>` flag that
acts as a default for **all three** flavours, while preserving
backwards compatibility:

- New flag in `ryll/src/config.rs` (`Args` struct):

  ```rust
  /// Default directory for bug-report zip files (F8, pedantic,
  /// and auto-disconnect snapshots). Created if missing. Each
  /// flavour can be overridden individually (e.g. by
  /// --pedantic-dir). If neither this nor a flavour-specific
  /// flag is set, the per-flavour fallback applies.
  #[arg(long)]
  pub bug_report_dir: Option<String>,
  ```

- Change `pedantic_dir` from
  `#[arg(long, default_value = "./ryll-pedantic-reports")]
  pub pedantic_dir: std::path::PathBuf,` to
  `#[arg(long)] pub pedantic_dir: Option<std::path::PathBuf>,`
  so we can tell "user explicitly set it" apart from "user
  didn't". Move the historical default into the resolver.

- Plumb both through to `RyllApp` (new optional fields).

- Factor the directory resolution into helpers used by every
  caller that writes a bug report — single source of truth so
  the three paths cannot drift:

  ```rust
  /// Output dir for F8 and auto-disconnect reports.
  fn manual_bug_report_dir(&self) -> PathBuf {
      if let Some(d) = &self.bug_report_dir {
          return PathBuf::from(d);
      }
      match &self.capture {
          Some(cap) => cap.dir.join("bug-reports"),
          None => std::env::current_dir().unwrap_or_else(|_| ".".into()),
      }
  }

  /// Output dir for pedantic reports.
  fn pedantic_bug_report_dir(&self) -> PathBuf {
      if let Some(d) = &self.pedantic_dir {
          return d.clone();
      }
      if let Some(d) = &self.bug_report_dir {
          return PathBuf::from(d);
      }
      PathBuf::from("./ryll-pedantic-reports") // historical default
  }
  ```

  Resolution priority for each flavour, from highest to lowest:

  | Flavour | 1st | 2nd | 3rd |
  |---------|-----|-----|-----|
  | F8 / auto-disconnect | `--bug-report-dir` | `<--capture>/bug-reports/` | cwd |
  | pedantic | `--pedantic-dir` | `--bug-report-dir` | `./ryll-pedantic-reports` |

- Wire these into the existing call sites:
  - F8 caller at `app.rs:1516` switches to `manual_bug_report_dir()`.
  - The auto-disconnect path uses `manual_bug_report_dir()`.
  - The pedantic observer registration at `bugreport.rs:1116`
    (where `dir = Arc::new(config.dir);` is currently captured)
    needs to use the resolved pedantic dir instead. The
    `PedanticConfig` struct's `dir` field now carries the
    *resolved* path, set at the call site in `main.rs:158`.
  - The eager `mkdir -p` at `main.rs:152` becomes unconditional
    on the resolved pedantic path (or, if `--pedantic` is off,
    skipped entirely as today).

Users who pass none of the three flags see the existing
behaviour unchanged: F8 reports → cwd, pedantic reports →
`./ryll-pedantic-reports`. Users who pass `--bug-report-dir`
get a single tidy directory for everything (with the existing
filename prefixes — `ryll-bugreport-...`, `ryll-pedantic-...`,
`ryll-disconnect-...` — distinguishing flavour). Users who pass
both `--bug-report-dir` and `--pedantic-dir` get pedantic in
the explicit override and the rest in the general dir. Update
docs (`docs/configuration.md` if the flag is covered there,
README's CLI section if it lists flags, plus clap-generated
`--help`).

### 6. Cooldown

Add `last_disconnect_report_at: Option<Instant>` to
`RyllApp`. Initialise to `None`. After a successful or skipped
write, update to `Some(Instant::now())`. The 60 s cooldown
applies regardless of which channel fired — a flapping main
channel triggering one zip per second would otherwise drown the
user's disk.

## Open questions

1. **Should auto-disconnect snapshots and F8 reports share an
   output-directory helper?** Yes — factor the directory
   resolution logic at `app.rs:1510-1514` into a method on
   `RyllApp`, and use it from both call sites. Otherwise the two
   paths drift over time. Resolved: yes, do this.

2. **A platform default for the bug-report dir** (e.g.
   `~/Library/Logs/ryll` on macOS,
   `~/.local/share/ryll/bug-reports` on Linux,
   `%LOCALAPPDATA%\ryll\bug-reports` on Windows) would be more
   discoverable than "cwd if no flags". The new
   `--bug-report-dir` flag covers explicit users; a platform
   default would help casual ones. Out of scope for Phase 01;
   add only if real-world feedback shows users are losing
   reports in unexpected cwds.

3. **Does the keepalive-timeout flag need to be cleared on
   reconnect?** Yes — set on every connect that the field is
   `false`, so a subsequent disconnect can correctly report
   whether *that* disconnect was from keepalive timeout. Done
   in the existing `RyllApp::reconnect` path (`app.rs:694`).

4. **Should we capture the `peer_addr` / `local_addr` of the
   socket at disconnect time?** Useful for diagnosing routing
   weirdness, but socket may already be closed by the time the
   handler runs. Defer to Phase 02 if Phase 01 data shows it
   would matter.

## Tasks

- [ ] Extend every channel snapshot in
      `shakenfist-spice-renderer/src/snapshots.rs` with the
      five new fields plus (on `MainSnapshot`)
      `keepalive_timeout_fired: AtomicBool`. Confirm whether
      `Playback` / `Usbredir` / `Webdav` snapshot structs
      already exist; if not, add minimal ones with `bytes_in` /
      `bytes_out` plus the disconnect fields, and plumb them
      through `ChannelSnapshots`.
- [ ] Update each channel's read / send / PING handler to
      maintain the new fields. Specifically:
  - `main_channel.rs` PING handler at line 502, send paths,
    line 285 (set `keepalive_timeout_fired` before emitting
    `Disconnected`).
  - `display.rs:870`, `inputs.rs:366`, `cursor.rs:297`,
    `playback.rs:549`, `usbredir.rs:236`, `webdav.rs:282`
    PING handlers, plus their respective receive/send paths.
- [ ] Add `--bug-report-dir <DIR>` flag in `config.rs` `Args`.
      Change `pedantic_dir` to `Option<PathBuf>` (drop the
      hardcoded default; move it into the resolver). Plumb
      both through to `RyllApp`.
- [ ] Add `RyllApp::manual_bug_report_dir()` and
      `RyllApp::pedantic_bug_report_dir()` helpers with the
      resolution priorities described in section 6. Switch the
      F8 caller at `app.rs:1516`, the auto-disconnect path,
      and the pedantic observer registration at
      `bugreport.rs:1116` (`PedanticConfig::dir` resolved at
      `main.rs:158` before construction) to use them. Update
      the eager `mkdir -p` at `main.rs:152` accordingly.
- [ ] Define `DisconnectCause` and `PerChannelDiagnostics` in
      `bugreport.rs`.
- [ ] Implement `BugReport::write_disconnect` mirroring
      `write_pedantic`.
- [ ] Add `maybe_write_disconnect_snapshot` helper on
      `RyllApp`, hook into `ChannelEvent::Error` and the
      critical-channel branch of `ChannelEvent::Disconnected`,
      with 60 s cooldown.
- [ ] Notification on success ("Disconnect snapshot saved to
      …") + error log on failure.
- [ ] Reset `keepalive_timeout_fired` on reconnect.
- [ ] Unit tests:
  - `write_disconnect` produces a zip whose
    `disconnect-cause.json` deserialises and contains expected
    fields for representative inputs.
  - 60 s cooldown suppresses a second snapshot.
  - `keepalive_timeout_fired` round-trips correctly through
    snapshot capture.
- [ ] Manual integration check (notes only, no test required):
      run ryll against a SPICE target, kill the server, verify
      a `ryll-disconnect-*.zip` lands on disk and the modal
      still appears.
- [ ] Update `ARCHITECTURE.md` (channel-event flow,
      bug-report types) and `AGENTS.md` if any test commands
      change.
- [ ] Update `PLAN-session-001-feedback.md` Execution table
      status for Phase 01.

## Out of scope

- Auto-reconnect or any change to disconnect *handling* — that
  is Phase 02, designed against the data this phase produces.
- Changes to the keepalive strategy itself
  (`main_channel.rs:206`'s 30 s window) — Phase 02.
- Proactive watchdogs or new keepalive timers — Phase 02.
- Platform-aware default bug-report directory (macOS / Linux /
  Windows location conventions). The new `--bug-report-dir` flag
  covers explicit configuration; the no-flag fallback chain is
  unchanged. Revisit if real-world feedback shows users are
  losing reports in unexpected cwds.
- UX changes to the disconnect modal at `app.rs:3119`. The
  current critical-vs-non-critical filter at `app.rs:1199` is
  unchanged: a non-critical channel drop still does **not**
  pop the modal, even though it now writes a snapshot.
  Surfacing the loss to the user (e.g., a notification "audio
  channel lost — reconnect to restore it") is Phase 09's job
  (F1: connection events in the notifications pane).
