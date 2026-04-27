# Phase 2: SPICE_MSG_NOTIFY end-to-end

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
TLS/RSA, LZ/GLZ compression), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

The master plan for this work is
[PLAN-notifications.md](/components/ryll/plans/PLAN-notifications/). Phase 1 is
already complete (commit `a16f6781`); see
[PLAN-notifications-phase-01-store.md](/components/ryll/plans/PLAN-notifications-phase-01-store/)
for the store API this phase pushes into. The master plan
also has a "Discoveries during this work" section that
records corrections to its own Phase 2 brief — read that
first.

## Goal

Wire `SPICE_MSG_NOTIFY` into the Phase 1 notification store
on every channel. At the end of this phase:

* The existing `Notify` parser uses typed fields
  (`NotifySeverity`, `Option<SpiceVisibility>`) instead of
  raw `u32`s, and rejects malformed messages cleanly.
* Every channel handler — main, display, inputs, cursor,
  playback, usbredir, webdav — has a `NOTIFY` arm that
  parses the message and pushes it to a shared
  `Arc<Mutex<NotificationStore>>`.
* `SharedNotifications` is created once in `RyllApp::new`
  (and `run_headless`) and threaded through every channel
  constructor, mirroring how `Arc<TrafficBuffers>` is
  plumbed today.
* No GUI surface exists yet — the bell, side panel, and
  removal of the old *Gaps* badge are Phase 4. Phase 3
  migrates the existing Gap and bug-report producers onto
  the same store.

## Background: what's already in the codebase

The master plan was written assuming `SPICE_MSG_NOTIFY` was
unparsed today. That's wrong. Concretely:

* `shakenfist-spice-protocol/src/messages.rs:182-219`
  defines `Notify { timestamp, severity, visibility, what,
  message }` and a working `Notify::read(&[u8])` parser
  that reads little-endian fields and a length-prefixed
  UTF-8 string.
* `shakenfist-spice-protocol/src/constants.rs:389` defines
  `NotifySeverity { Info, Warn, Error }` with `from_u32`.
  Phase 1 added `Hash, PartialOrd, Ord, Serialize,
  Deserialize` derives.
* `shakenfist-spice-protocol/src/constants.rs:144` defines
  `main_server::NOTIFY = 7`.
* `ryll/src/channels/main_channel.rs:520-542` matches
  `main_server::NOTIFY`, parses, and routes by severity
  into `tracing::warn!` / `info!`.

What's missing:

* The parser returns `severity: u32` and `visibility: u32`
  (raw wire types). Callers convert at every site, leaving
  the parser unaware of what's valid.
* Only `main_server` has a `NOTIFY` constant; the other
  six `*_server` modules do not, so the other six channel
  handlers have no way to match the opcode short of using
  the literal `7`.
* `message_names::*_server` returns `"unknown"` for
  opcode 7 on every channel except `main_server`.
* The other six channel handlers (display, inputs, cursor,
  playback, usbredir, webdav) drop NOTIFY into
  `log_unknown_once` instead of parsing it.
* Nothing pushes into the Phase 1 notification store —
  the store has no producers yet at all.

## Design

### Parser tightening

Change `Notify` in
`shakenfist-spice-protocol/src/messages.rs`:

```rust
#[derive(Debug, Clone)]
pub struct Notify {
    pub timestamp: u64,
    pub severity: NotifySeverity,
    pub visibility: Option<SpiceVisibility>,
    pub what: u32,
    pub message: String,
}

impl Notify {
    pub fn read(data: &[u8]) -> io::Result<Self> {
        // Existing length check expanded: minimum 24 bytes for
        // the five fixed-size fields (8+4+4+4+4). The string
        // length is then validated against the remaining buffer.
        if data.len() < 24 {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "Not enough data for Notify",
            ));
        }
        let mut cursor = Cursor::new(data);
        let timestamp = cursor.read_u64::<LittleEndian>()?;
        let severity_raw = cursor.read_u32::<LittleEndian>()?;
        let visibility_raw = cursor.read_u32::<LittleEndian>()?;
        let what = cursor.read_u32::<LittleEndian>()?;
        let msg_len = cursor.read_u32::<LittleEndian>()? as usize;

        let severity = NotifySeverity::from_u32(severity_raw);
        let visibility = SpiceVisibility::from_u32(visibility_raw);

        if data.len() < 24 + msg_len {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "Notify message body shorter than declared length",
            ));
        }
        let mut msg_bytes = vec![0u8; msg_len];
        cursor.read_exact(&mut msg_bytes)?;
        let message = String::from_utf8_lossy(&msg_bytes)
            .to_string();

        Ok(Notify { timestamp, severity, visibility, what,
                    message })
    }
}
```

Two semantic decisions worth calling out:

* **Severity defaults to `Info` on unknown raw values.**
  This preserves the existing `NotifySeverity::from_u32`
  contract (which already returns `Self`, not `Option`).
  Servers that emit values outside 0–2 are misbehaving;
  the operator is better served by seeing the message at
  Info level than by dropping the notification entirely.
* **Visibility is `Option`, not lossy.** Unknown wire
  values become `None`, recorded as such on the
  notification entry. Phase 4 will treat `None` as
  "unknown — flash the bell" (most conservative).

### Constants and name lookups

In `shakenfist-spice-protocol/src/constants.rs`, add
`pub const NOTIFY: u16 = 7;` to the existing `*_server`
modules that don't have it: `display_server`,
`inputs_server`, `cursor_server`, `playback_server`,
`spicevmc_server` (used by both usbredir and webdav).
`main_server::NOTIFY` already exists.

In `shakenfist-spice-protocol/src/logging.rs`, add a
`NOTIFY => "notify"` arm to each of `display_server`,
`inputs_server`, `cursor_server`, `playback_server`, and
`spicevmc_server` inside `mod message_names`.

The master plan §"Phase 2" called this out explicitly:
do **not** refactor common-message handling into a
shared helper here. The seven NOTIFY arms get
duplicated, which is fine for now and matches how
`SET_ACK` and `PING` are duplicated today.

### Store plumbing

Mirror the existing `Arc<TrafficBuffers>` thread:

1. `RyllApp::new` (`ryll/src/app.rs` around line 423)
   creates `let notifications = SharedNotifications::new(
   Mutex::new(NotificationStore::new()))` next to
   `traffic`.
2. `RyllApp` keeps a `notifications:
   SharedNotifications` field so Phase 4's GUI can read
   it.
3. `run_connection` in `app.rs:2757` takes one extra
   argument and clones it into every channel constructor,
   exactly as it does for `traffic`.
4. `run_headless` (the headless equivalent at
   `app.rs:3027` area) does the same.
5. Every channel handler struct gains a `notifications:
   SharedNotifications` field, set from the constructor
   argument.
6. Channel constructors (`MainChannel::new`,
   `DisplayChannel::new`, `InputsChannel::new`,
   `CursorChannel::new`, `PlaybackChannel::new`,
   `UsbredirChannel::new`, `WebdavChannel::new`) take an
   additional `SharedNotifications` argument as their
   final positional parameter.

The store does not need to be created in tests that
construct individual channels in isolation — those tests
can pass `SharedNotifications::new(Mutex::new(
NotificationStore::new()))` inline.

### Per-channel NOTIFY arms

Every channel's `handle_message` (or inline `match
msg_type` for inputs/playback) gains a `NOTIFY` arm with
the same shape:

```rust
*_server::NOTIFY => {
    let notify = Notify::read(payload)?;
    if settings::is_verbose() {
        logging::log_detail(&format!(
            "severity={:?}, visibility={:?}, what={}, \
             message=\"{}\"",
            notify.severity, notify.visibility, notify.what,
            notify.message,
        ));
    }
    // Tracing log — preserve the per-severity routing the
    // main-channel handler does today, so verbose / log
    // consumers don't lose information when the GUI lands.
    match notify.severity {
        NotifySeverity::Error => warn!(
            "{}: server notify (error): {}",
            <channel-name-string>, notify.message),
        NotifySeverity::Warn => warn!(
            "{}: server notify (warn): {}",
            <channel-name-string>, notify.message),
        NotifySeverity::Info => info!(
            "{}: server notify: {}",
            <channel-name-string>, notify.message),
    }
    let mut entry = NotificationEntry::new(
        notify.severity,
        NotificationSource::Spice {
            channel: ChannelType::<This>,
            what: notify.what,
        },
        notify.message.clone(),
    );
    if let Some(v) = notify.visibility {
        entry = entry.with_visibility(v);
    }
    self.notifications
        .lock()
        .expect("notifications lock poisoned")
        .push(entry);
}
```

Concrete channel-name and `ChannelType` values:

| Handler          | Channel name | ChannelType variant |
|------------------|--------------|---------------------|
| main_channel.rs  | "main"       | `ChannelType::Main` |
| display.rs       | "display"    | `ChannelType::Display` |
| inputs.rs        | "inputs"     | `ChannelType::Inputs` |
| cursor.rs        | "cursor"     | `ChannelType::Cursor` |
| playback.rs      | "playback"   | `ChannelType::Playback` |
| usbredir.rs      | "usbredir"   | `ChannelType::Usbredir` |
| webdav.rs        | "webdav"     | `ChannelType::Webdav` |

The main channel handler's existing arm
(`main_channel.rs:520-542`) is replaced by this same
shape — the tracing routing stays, but the store push is
new.

`Notify::message.clone()` is intentional: the message is
also formatted into the tracing logs above, so the
notification entry needs an owned copy.

### Per-channel attribution vs cross-channel dedup

Master-plan Q5 recommends dedup of the "channel is
insecure" message tuple `(severity, visibility, what,
message)` within 30 seconds. Phase 1's store dedups on
`(source, severity, message, visibility)` where `source`
is `Spice { channel, what }`. The two are not the same:
under Phase 1's rules, the same insecure-channel warning
arriving on display and inputs produces **two** store
entries (different `source`s), not one folded entry.

This phase keeps Phase 1's per-channel attribution.
Reasoning:

* The operator wants to know *which* channels are
  insecure, not just that *some* channel is. A folded
  entry would lose that.
* The blast radius is bounded — at most one entry per
  channel type (≤7 entries) per insecure-config session.
* Phase 1's `[N×]` fold still kicks in correctly for
  *within-channel* repeats (a server resending the same
  warning on the same channel inside the dedup window).

If real-world QEMU sessions show this is too noisy,
Phase 4's GUI work can revisit (e.g. group entries by
`(severity, message)` in the side panel without changing
the store).

### Tests

Unit tests live next to the parser in
`shakenfist-spice-protocol/src/messages.rs`:

| Test | Asserts |
|------|---------|
| `notify_parse_minimum_valid` | 24-byte buffer with empty message parses; severity/visibility decode correctly. |
| `notify_parse_each_severity` | Three buffers, each with severity 0/1/2; produce `Info`/`Warn`/`Error`. |
| `notify_parse_each_visibility` | Three buffers with visibility 0/1/2; produce `Some(Low)`/`Some(Medium)`/`Some(High)`. |
| `notify_parse_unknown_visibility_is_none` | visibility=99 → `visibility == None`. |
| `notify_parse_unknown_severity_defaults_info` | severity=99 → `severity == NotifySeverity::Info` (current behaviour preserved). |
| `notify_parse_with_500_byte_message` | 24-byte header + 500-byte ASCII message parses round-trip. |
| `notify_parse_truncated_header` | 23-byte buffer returns `Err(UnexpectedEof)`. |
| `notify_parse_message_body_shorter_than_declared` | Header claims `msg_len = 100` but only 50 bytes follow → `Err(UnexpectedEof)`. |
| `notify_parse_invalid_utf8_replaced` | Non-UTF8 message bytes are replaced (lossy) — the call still returns `Ok`. (Existing `from_utf8_lossy` behaviour, pinned by test.) |

Integration with the store is covered by the Phase 1
unit tests for the store itself. Phase 2 does not add
end-to-end tests against a live QEMU — that's deferred to
Phase 4 with the GUI.

### Manual verification

After Phase 2 lands and before Phase 3 begins, the
implementer should run ryll against a `qemu-system-x86_64
-spice port=5900,disable-ticketing=on,plaintext-channel=all`
target with `--verbose` and confirm the tracing output
shows `server notify` lines on each connecting channel.
The notifications themselves are not yet visible in the
GUI, so the verification is via log inspection.

## Steps

### Step 1: Parser tightening + constants + name lookups

In `shakenfist-spice-protocol`:

1. Update `messages.rs` `Notify` struct + `read()` per
   §"Parser tightening". Remove `#[allow(dead_code)]` on
   `timestamp` since the field is now read at one site
   (it stays unread by callers, but the struct is more
   widely used after this phase).
2. Add `pub const NOTIFY: u16 = 7;` to each of
   `display_server`, `inputs_server`, `cursor_server`,
   `playback_server`, `spicevmc_server` in
   `constants.rs`.
3. Add `NOTIFY => "notify"` arms to `display_server`,
   `inputs_server`, `cursor_server`, `playback_server`,
   `spicevmc_server` in `logging.rs::message_names`.
4. Add the unit tests from the §"Tests" table to
   `messages.rs`'s existing `#[cfg(test)] mod tests`.
5. Update any existing tests on `Notify` (the file
   already has some) for the new field types.
6. `make build` and `make test` must pass.
7. `pre-commit run --all-files` must pass.

### Step 2: Store plumbing through channel constructors

In `ryll`:

1. In `RyllApp::new`, construct a
   `SharedNotifications` next to the existing `traffic`
   creation (around `app.rs:423`). Store it on `RyllApp`
   as a `notifications: SharedNotifications` field.
2. Add a `notifications: SharedNotifications` parameter
   to `run_connection` (final position) and pass it from
   `RyllApp::new`'s thread spawn site at `app.rs:493`.
3. Add the same parameter to the headless entry-point
   (`run_headless` around `app.rs:3027`); construct a
   fresh `SharedNotifications` there.
4. Add a `notifications: SharedNotifications` parameter
   (final position) to every channel constructor
   (`MainChannel::new`, `DisplayChannel::new`,
   `InputsChannel::new`, `CursorChannel::new`,
   `PlaybackChannel::new`, `UsbredirChannel::new`,
   `WebdavChannel::new`) and store it as a struct field.
5. Pass `notifications.clone()` from `run_connection` at
   each channel-construction site
   (`app.rs:2784`, `:2858`, `:2876`, `:2892`, `:2913`,
   `:2936`, `:2958`).
6. Do **not** add NOTIFY arms in this step — the field
   is plumbed but unused. `#[allow(dead_code)]` on the
   field is acceptable until step 3 lands; alternatively,
   land step 2 and step 3 as one squashed sub-agent run
   (see §"Sub-agent execution table").
7. `make build` must succeed; existing tests must still
   pass.

### Step 3: Per-channel NOTIFY arms

In `ryll/src/channels/`:

1. Replace the existing arm at `main_channel.rs:520-542`
   with the §"Per-channel NOTIFY arms" template (tracing
   routing + store push).
2. Add the same shape arm to the `match msg_type` in
   `display.rs` (around line 687), `inputs.rs`
   (around line 323), `cursor.rs` (around line 158),
   `playback.rs` (around line 444), `usbredir.rs`
   (around line 208), and `webdav.rs` (around line 254).
3. Each arm uses its channel's `*_server::NOTIFY`
   constant and its `ChannelType::*` variant per the
   table in §"Per-channel NOTIFY arms".
4. The existing `log_unknown_once` fallback continues
   to handle other unknown opcodes — only the NOTIFY arm
   is added.
5. `make build` must succeed.
6. `make test` must pass — the store, parser, and
   existing channel tests all remain green.
7. `pre-commit run --all-files` must pass.

## Sub-agent execution table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 (parser + constants + name lookups) | medium | sonnet | none | In `shakenfist-spice-protocol`: tighten `Notify` struct + parser per PLAN-notifications-phase-02-spice-notify.md §"Parser tightening" — `severity: NotifySeverity` (use existing `NotifySeverity::from_u32`, lossy default), `visibility: Option<SpiceVisibility>` (use `SpiceVisibility::from_u32`). Remove `#[allow(dead_code)]` on `timestamp`. Validate `data.len() >= 24 + msg_len` before reading the message body and return `Err(UnexpectedEof)` on shortfall. Add `pub const NOTIFY: u16 = 7;` to `display_server`, `inputs_server`, `cursor_server`, `playback_server`, `spicevmc_server` in `constants.rs`. Add `NOTIFY => "notify"` arms to those same modules in `logging.rs::message_names`. Add the 9 unit tests from the plan's §"Tests" table to `messages.rs`'s existing `#[cfg(test)] mod tests`. Update any existing `Notify` tests in that file for the new field types. Run `make build`, `make test`, `pre-commit run --all-files`. The host doesn't have a recent rustc — Rust toolchain runs inside Docker via the `make` targets. |
| 2+3 (plumbing + per-channel arms) | high | opus | worktree | In `ryll`: thread `SharedNotifications` from `RyllApp::new` and `run_headless` through `run_connection` into every channel constructor, mirroring how `Arc<TrafficBuffers>` is plumbed today (see `app.rs:423` and the `traffic_clone` thread to `run_connection` at `app.rs:493`, then to each channel constructor in `run_connection` at `:2784/:2858/:2876/:2892/:2913/:2936/:2958`). Add a `notifications: SharedNotifications` field to every channel handler struct. Then add a `*_server::NOTIFY` arm to every channel's `handle_message` / inline `match msg_type` per the §"Per-channel NOTIFY arms" template — replace the existing `main_channel.rs:520-542` arm; add fresh arms to `display.rs:687`, `inputs.rs:323`, `cursor.rs:158`, `playback.rs:444`, `usbredir.rs:208`, `webdav.rs:254`. Use the channel-name and ChannelType from the table in the plan. Each arm: parse `Notify::read`, log to tracing per existing main-channel pattern (warn for Error/Warn, info for Info), build `NotificationEntry::new(severity, NotificationSource::Spice { channel, what }, message.clone()).with_visibility(v)` if visibility is Some, push to the store. Use `notify.message.clone()` because the message also lands in the tracing log. Do not refactor common-message dispatch into a shared helper — that's deferred to Future work per the master plan. Run `make build`, `make test`, `pre-commit run --all-files` inside the worktree. The host doesn't have a recent rustc — Rust toolchain runs inside Docker via the `make` targets. Report the worktree path so the management session can review and merge. |

Step 1 lands first so step 2+3 can rely on the new
`Notify` type. Step 2+3 runs in a worktree per the
master plan's recommendation for Phase 2 ("the
SPICE_MSG_NOTIFY parser touches every channel handler
and is genuinely risky") — the worktree gives the
management session a clean revert path if the per-channel
rollout is wrong. Both land in one phase commit.

## Administration and logistics

### Success criteria

* `Notify` parser uses typed fields (`NotifySeverity`,
  `Option<SpiceVisibility>`); test vectors cover each
  severity, each visibility, unknown values, an empty
  message, a 500-byte message, and two truncation cases.
* `*_server::NOTIFY = 7` exists in every channel's
  server-message constant module; `message_names::*_server`
  returns `"notify"` for opcode 7 on every channel.
* `RyllApp` holds a `SharedNotifications`; it is passed
  through `run_connection` and `run_headless` into every
  channel constructor.
* Every channel handler has a `*_server::NOTIFY` arm
  that parses, logs (preserving the existing
  main-channel tracing routing), and pushes to the store.
* `make build`, `make test`, `pre-commit run
  --all-files` all pass.
* Manual smoke test: ryll connected to a
  TLS-disabled QEMU prints `server notify` lines on
  multiple channels in `--verbose` mode (confirms parser
  + arm wiring; the GUI surface is still Phase 4).

### Risks

* **Sweeping per-channel edit.** Seven match statements
  modified by a single sub-agent. Mitigation: worktree
  isolation and the management session inspecting the
  diff before merging.
* **Plumbing churn in `app.rs`.** The `run_connection`
  signature already takes ~14 arguments; this adds one
  more. If the parameter list becomes painful, factor
  later — not in this phase.
* **`Notify` parser API break.** External (in-tree)
  callers of `Notify::read` get type-changed fields. The
  only callsite today is `main_channel.rs:521-522`,
  which is updated in the same phase. Out-of-tree
  consumers don't exist.
* **Verbose-log compatibility.** The new arms preserve
  the existing tracing routing (warn/info per severity)
  so log scrapers and the operator's mental model don't
  break.
* **Lock contention on `Arc<Mutex<NotificationStore>>`.**
  The store is touched on every channel's tokio task and
  on egui's main loop (Phase 4). NOTIFY frequency is
  low (handful per session), so this is not a hot path.
  Phase 1's tests confirm push is O(window) — single
  digits in practice.

### Future work (deferred from this phase)

* **Common-message helper refactor.** Master plan
  §"Future work" item 4. Each channel duplicates
  `SET_ACK`, `PING`, and now `NOTIFY` arms. If the
  duplication keeps growing (or another common message
  lands), factor into a `handle_common_message` helper.
  Out of scope here.
* **Cross-channel folding** of identical insecure-channel
  warnings into a single store entry. Recommendation
  pending Phase 4 GUI feedback — see §"Per-channel
  attribution vs cross-channel dedup".
* **Click-to-detail using `what`.** The SPICE
  `SPICE_NOTIFY_WHAT_*` enum could link to documentation
  in the side panel. Master plan §"Future work" item 4.

### Documentation index maintenance

When this phase lands, update the master plan's
*Execution* table row 2 from "Not started" to "Complete"
with a link to the merge commit.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
