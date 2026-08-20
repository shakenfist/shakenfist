# Notifications system

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

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, and data flow. Consult `AGENTS.md`
for build commands, project conventions, code organisation,
and a table of protocol reference sources. Key references
include `shakenfist/kerbside` (Python SPICE proxy with
protocol docs and a reference client),
`/srv/src-reference/spice/spice-protocol/` (canonical SPICE
definitions, particularly `spice/protocol.h` for
`SpiceMsgNotify`), `/srv/src-reference/spice/spice-gtk/`
(reference C client; see `spice-channel.c` for how
`SPICE_MSG_NOTIFY` is handled today by the canonical
client), and `/srv/src-reference/qemu/qemu/` (server-side
SPICE in `ui/spice-*`; this is what emits the "input
channel is unencrypted" warning the operator currently
never sees).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be
named for the master plan, in the same directory as the
master plan, and simply have `-phase-NN-descriptive`
appended before the `.md` file extension. Tracking of these
sub-phases should be done via a table in this master plan
under the *Execution* section.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Ryll currently surfaces three categories of "something
the operator should know" through ad-hoc, non-uniform UI:

1. **Protocol gaps**. The `warn_once!` registry at
   `shakenfist-spice-protocol/src/logging.rs` records each
   distinct unhandled feature the server uses. The count
   is exposed as a colour-coded button (the *Gaps* badge)
   at `ryll/src/app.rs:1582` with a hover tooltip and a
   click-to-open list popup. `--pedantic` mode writes a
   bug-report zip per gap on top of this surface. A
   registry observer pattern exists for additional
   consumers (see `AGENTS.md` design decision 19).

2. **Bug-report status messages**. After a bug-report zip
   is written, a transient status string appears at
   `ryll/src/app.rs:1610` for 5 seconds beside the
   *Report* button, then fades. There is no scrollback —
   if the operator misses it, the message is gone.

3. **SPICE protocol notifications**. The SPICE wire
   format defines `SPICE_MSG_NOTIFY` (opcode 7, common
   across every channel) carrying `time_stamp (u64)`,
   `severity (u32: info / warn / error)`, `visibility (u32:
   low / medium / high)`, `what (u32)`, and a UTF-8
   message. QEMU's SPICE server emits these for things
   the operator genuinely needs to know — most prominently
   "Channel <name> is insecure" warnings on each
   channel when TLS isn't being used. **Ryll does not
   parse `SPICE_MSG_NOTIFY` at all today**; a grep across
   `ryll/src/` confirms no handler exists. Those messages
   currently fall through to the per-channel
   `log_unknown_once` path on whichever channel they
   arrive on, where they appear once in the trace logs
   and then nowhere. virt-viewer doesn't surface them
   either, so this is a real ryll differentiator if we
   land it.

The three categories share a shape (timestamp, severity,
short text, source, possibly a hyperlink to detail) and
should share a single in-app surface — a side panel of
notifications with read/unread state, severity icons, and
mark-as-read affordances — fed by a small unified store.
A bell icon with a filled dot when there are unread items
sits in the status bar (or hamburger area, after
[PLAN-hamburger-menu.md](/components/ryll/plans/PLAN-hamburger-menu/)).

This plan deliberately **does not** include channel
disconnect / reconnect events as a notification source,
even though they fit the shape: they're already surfaced
through `ChannelEvent::Disconnected` and the existing
"Disconnected" status, and rolling them in would expand
scope. Notification sources can grow later; let's not
bake everything in on day one.

## Mission and problem statement

Build an in-app notifications system with three
producers and one consumer surface.

**Producers**:

* The `warn_once!` registry observer (Gaps), via the
  existing `register_gap_observer` hook in
  `shakenfist-spice-protocol/src/logging.rs`.
* The bug-report writer, replacing the transient status
  message at `app.rs:1610`.
* A new `SPICE_MSG_NOTIFY` parser, wired into every
  channel handler that can receive it (main, display,
  inputs, cursor, playback, usbredir, webdav).

**Consumer surface**: a side panel mirroring the existing
*Traffic* viewer pattern at `app.rs:1622`, listing
notifications in chronological order (newest first),
with per-entry severity badge, source channel, message,
relative timestamp, and per-entry / mark-all-read /
clear-all controls. A bell icon in the status bar shows
a filled dot whenever `unread_count > 0`.

**Store**: an in-memory ring buffer (recommend cap of
500 entries, plenty of headroom and bounded memory) with
read/unread state, indexed by insertion order. Lives
behind `Arc<Mutex<NotificationStore>>` so producers can
push from any tokio task and the egui loop can read
without contention.

**Severity model**: align with SPICE's three-level
scheme (Info / Warn / Error). Gaps map to Warn,
bug-report success maps to Info, bug-report failure
maps to Error. SPICE's `visibility` field is a
secondary axis (low / medium / high); we record it but
do not initially distinguish it in the UI — see Open
question 4.

When the hamburger plan
([PLAN-hamburger-menu.md](/components/ryll/plans/PLAN-hamburger-menu/)) lands
first, the bell sits next to the hamburger icon at the
right of the status bar; the existing *Gaps* badge is
replaced by the bell (gaps become notifications, so the
badge is redundant); the bug-report transient at
`app.rs:1610` is removed (it becomes a notification too).

## Open questions

1. **Notification store cap**. 500 entries with
   FIFO eviction recommended. SPICE_MSG_NOTIFY arrives
   sparingly (a handful per session); gaps cap at 50 in
   --pedantic via the existing observer; bug reports are
   manual. 500 is generous. Open: should low-visibility
   SPICE notifications count toward the cap or be capped
   separately? Recommend single shared cap.

2. **Auto-mark-read on side-panel open**. When the
   operator opens the panel, do all visible
   notifications become read, or only those they scroll
   past? Recommend: opening the panel marks everything
   read on close (not on open — gives the operator a
   chance to see what was unread). Alternative: leave it
   manual, require explicit *Mark all read*.

3. **Persistence**. In-memory only, or also written
   into bug-report zips so a captured session preserves
   the notification log? Recommend: include a
   `notifications.json` in bug reports starting at
   Phase 4. Trivially implementable on top of the
   serde-friendly store.

4. **SPICE visibility field**. SPICE notifications carry
   a low / medium / high visibility hint. Should
   low-visibility notifications skip the bell flash
   (still recorded, but no unread-dot)? Recommend yes
   for first cut — it matches the protocol's intent
   (low-visibility is "informational, not urgent").

5. **Notification content for SPICE channel-insecure
   warnings**. Each channel emits its own warning when
   TLS is not in use, so the operator could see N
   "channel is insecure" notifications in quick
   succession. Should we deduplicate within a session?
   Recommend yes — the second occurrence of the same
   `(severity, visibility, what, message)` tuple within
   30 seconds is folded into the existing entry's count
   field, like `[3×]`.

6. **Bell placement**. After PLAN-hamburger-menu lands,
   the right-edge cluster is `[hamburger] [Gaps]`. The
   Gaps badge gets replaced by the bell here — same
   slot, same colour-coded unread-dot style. Recommend.

7. **Severity icons / colours**. Info = grey or default,
   Warn = amber, Error = red. The bell's dot picks up
   the highest-severity unread item's colour. Recommend
   so the operator can triage at a glance.

8. **Click-through from the bell**. Click opens the
   side panel. Recommend.

9. **Tests for the SPICE_MSG_NOTIFY parser**. The
   message format is short (5 fields plus a string).
   Test vectors come from spice-protocol's `protocol.h`
   definition and from a small captured pcap of a QEMU
   session with TLS off; the parser should accept both
   little-endian and big-endian severity values
   correctly (SPICE is little-endian on the wire). Mark
   this in Phase 2 brief.

## Execution

Five phases plus inline docs.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Store and ring buffer | [PLAN-notifications-phase-01-store.md](/components/ryll/plans/PLAN-notifications-phase-01-store/) | Complete (a16f6781) |
| 2. SPICE_MSG_NOTIFY parser and channel plumbing | [PLAN-notifications-phase-02-spice-notify.md](/components/ryll/plans/PLAN-notifications-phase-02-spice-notify/) | Complete (b3e520b1) |
| 3. Migrate Gaps and bug-report status as sources | [PLAN-notifications-phase-03-existing-sources.md](/components/ryll/plans/PLAN-notifications-phase-03-existing-sources/) | Complete (3780be03) |
| 4. GUI: bell, side panel, mark-read | [PLAN-notifications-phase-04-gui.md](/components/ryll/plans/PLAN-notifications-phase-04-gui/) | Complete (ed3f91db) |
| 5. Docs and bug-report serialisation | [PLAN-notifications-phase-05-docs.md](/components/ryll/plans/PLAN-notifications-phase-05-docs/) | Complete (a2d35d8b) |

**Phase 1 — Store and ring buffer (high effort, opus)**.
Define `NotificationEntry { id: u64, when: SystemTime,
severity: NotificationSeverity, source:
NotificationSource, message: String, count: u32,
visibility: Option<SpiceVisibility>, read: bool }`,
`NotificationSeverity { Info, Warn, Error }`,
`NotificationSource { Gap, BugReport, Spice {
channel: ChannelType }, Internal }`. Build
`NotificationStore` as a 500-entry `VecDeque` with
`push`, `mark_read`, `mark_all_read`, `iter_unread`,
`unread_count`, and `clear`. Apply the 30-second
deduplication rule from Q5 inside `push`. Live behind
`Arc<Mutex<NotificationStore>>`. Pure-Rust unit tests
covering: dedup window, eviction at cap, severity
ordering, mark-read state transitions, serde round-trip
(for the bug-report serialisation in Phase 5). No
plumbing into channels yet.

**Phase 2 — SPICE_MSG_NOTIFY parser (high effort,
opus)**. Add `parse_msg_notify(payload: &[u8]) ->
io::Result<MsgNotify>` to
`shakenfist-spice-protocol/src/messages.rs` next to the
existing `Ping` / `SetAck` parsers. Wire format from
`/srv/src-reference/spice/spice-protocol/spice/protocol.h`:
`time_stamp(u64), severity(u32), visibility(u32),
what(u32), message_len(u32), message(utf8 message_len
bytes, NUL-terminated)`. Constant
`shakenfist_spice_protocol::common_messages::NOTIFY = 7`
already exists or needs to be added — check first; add
if missing with a unit-test fixture. In every channel
handler that has a `match msg_type { ... }` statement
(main_channel, display, inputs, cursor, playback,
usbredir, webdav), add a `common_messages::NOTIFY` arm
that parses, builds a `NotificationEntry` with
`source: Spice { channel: <this channel's
ChannelType> }`, and pushes to the store.
Common-message handling is currently per-channel; this
phase consciously **does not** refactor that into a
shared helper, because each channel's match arm is
already cluttered and the cost of duplicating one new
arm seven times is small. Refactor as Future work if
the per-arm code grows. Test vectors: hand-crafted
buffers exercising each severity / visibility
combination plus an empty message and a
500-byte message. Do not write integration tests
against a live QEMU yet — Phase 4 will do that
end-to-end.

**Phase 3 — Migrate existing sources (medium effort,
sonnet)**. Replace the *Gaps* badge at
`app.rs:1582` and the bug-report transient at
`app.rs:1610` with notification-store pushes. Use the
existing `register_gap_observer` hook in
`shakenfist-spice-protocol/src/logging.rs` to feed
gaps as `NotificationEntry { severity: Warn, source:
Gap, ... }`. The `--pedantic` writer continues to write
zips on top — that's an orthogonal concern. The
bug-report writer (currently calling
`set_bug_status_message` or similar) emits an
`Info` notification on success and an `Error` on
failure. Remove the colour-coded count button and the
transient status slot from the layout block (those
features are now expressed through the bell that
arrives in Phase 4). Until Phase 4 lands, the bell
slot is empty and the operator has no UI for the
store — that is acceptable for this phase because the
data is still in tracing logs and bug-report zips, and
Phase 3 lands as one PR with Phase 4 closely behind.

**Phase 4 — GUI: bell, side panel, mark-read (medium
effort, sonnet)**. Add a bell icon (`🔔` or custom)
to the status-bar right-edge cluster (or to the
hamburger menu's leftmost slot, depending on whether
PLAN-hamburger-menu has landed by then — re-read the
status of that plan when Phase 4 begins). The bell
shows a small filled dot — coloured by the
highest-severity unread item — when `unread_count > 0`,
and is plain when there are no unread items. Click
opens a `egui::SidePanel::right("notifications")`
mirroring the Traffic viewer at `app.rs:1622`. Each
entry renders: severity icon, source label
(`SPICE/main`, `Gap`, `BugReport`), relative
timestamp, message text. Per-entry actions: dismiss
(mark read and remove from view). Header actions:
*Mark all read*, *Clear all*. Closing the side panel
calls `mark_all_read` per Q2's recommendation. Manual
test: connect headfully to QEMU with `-spice
disable-ticketing=on,plaintext-channel=all` (or
similar), confirm the channel-insecure notifications
arrive and surface in the panel.

**Phase 5 — Docs and bug-report serialisation (low
effort, sonnet)**. Update `README.md`,
`ARCHITECTURE.md`, and `AGENTS.md`. ARCHITECTURE.md
gains a *Notifications* section describing the
producers/consumer model. AGENTS.md gains a design
decision number for "Notifications go through the
unified store, not direct UI calls" and lists the
recommended pattern for adding a new source. README.md
mentions the new GUI surface and the SPICE
notifications it surfaces. Add `notifications.json` to
the bug-report zip writer (per Q3) — the store already
serde-serialises after Phase 1.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never
in the management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean
and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step
   with the brief from the plan, at the recommended
   effort level and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's
   summary describes what it intended, not necessarily
   what it did.
4. **Fix or retry** if the output is wrong. Diagnose
   whether the brief was insufficient (improve it) or
   the model was too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied
   with the result.

Use `isolation: "worktree"` for sub-agents working on
Phase 2 (the SPICE_MSG_NOTIFY parser touches every
channel handler and is genuinely risky; an isolated
worktree means a botched edit can be discarded). Phases
1, 3, 4, 5 can work directly in the main tree.

### Planning effort

Master plan: high effort.

Phase 1 plan: high effort. The store API needs careful
thought — wrong choices here propagate everywhere.

Phase 2 plan: high effort. Wire format research, plus
the per-channel match-arm rollout.

Phase 3 plan: medium effort. Mechanical migration with
clear precedent.

Phase 4 plan: medium effort. Egui plumbing with the
Traffic viewer as a clear precedent.

Phase 5 plan: low effort. Doc edits.

### Step-level guidance

Each phase plan should include a table like this:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**
- **high** — Requires reading multiple files, making
  judgment calls, understanding non-obvious invariants,
  or researching external references.
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief.
- **low** — Purely mechanical changes.

**Model choice:** opus for deep reasoning or
cross-channel architectural work; sonnet for
well-briefed implementation; haiku for purely
mechanical tasks.

**When in doubt, skew to the more capable model.**

**Brief for sub-agent:** A good brief front-loads the
research the planner already did, so the implementing
agent doesn't repeat it.

### Management session review checklist

After a sub-agent completes, the management session
should verify:

- [ ] The files that were supposed to change actually
      changed.
- [ ] No unrelated files were modified.
- [ ] The code builds (`pre-commit run --all-files`).
- [ ] Tests pass (`cargo test --workspace`).
- [ ] The changes match the intent of the brief.
- [ ] Commit message follows project conventions.

## Administration and logistics

### Success criteria

* `pre-commit run --all-files` passes (rustfmt, clippy
  with `-D warnings`, shellcheck, gitleaks, bidi).
* `cargo test --workspace` passes, including the new
  store and parser tests.
* `SPICE_MSG_NOTIFY` is parsed on every channel and
  the QEMU "channel is insecure" warning surfaces in
  the notifications panel when TLS is off.
* Gaps and bug-report status messages have been
  migrated; the colour-coded *Gaps* button and the
  bug-report transient status slot are gone from the
  status bar.
* The notifications side panel renders entries with
  severity, source, timestamp, and message; *Mark all
  read* / *Clear all* / per-entry dismiss work; closing
  the panel marks everything read.
* The bell icon shows a coloured unread-dot when
  `unread_count > 0`; click opens the panel.
* Bug-report zips include `notifications.json`.
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` are
  updated.
* `docs/plans/index.md` lists this plan as *Complete*.

### Future work

* **Channel lifecycle as a source**. Disconnects and
  agent-connect / agent-disconnect events would fit the
  notifications shape; deliberately deferred.
* **Filtering**. Per-severity / per-source filters on
  the side panel.
* **Persistence across sessions**. Writing a small
  notifications log to `~/.local/share/ryll/` so the
  operator can review what happened in the last
  session. Out of scope.
* **Common-message helper refactor**. Phase 2 adds a
  `NOTIFY` match arm to seven channel handlers. If
  more common messages get added (each channel handles
  ACK / PING / DISCONNECT etc. independently today),
  factor those into a `handle_common_message` helper.
  Out of scope here.
* **Click-to-detail for SPICE notifications**. The
  `what` field carries an enum identifier
  (`SPICE_NOTIFY_WHAT_*`) we could use to link to
  documentation. Out of scope.
* **Severity threshold for bell flash**. Q4
  recommended low-visibility skips the bell; we may
  want a configurable severity threshold too (e.g.
  Info-level notifications never flash the bell).

### Bugs fixed during this work

(To be filled in as the work proceeds.)

### Discoveries during this work

* **Phase 2 smoke test, 2026-04-26**: against `make
  test-qemu` (a default `-spice
  port=5900,disable-ticketing=on,plaintext-channel=all`
  config) only the **main** channel receives NOTIFY
  messages. Master plan Q5's premise that "each channel
  emits its own [insecure] warning" is wrong for QEMU;
  the SPICE server emits a single notification per
  affected channel **on the main channel**, with the
  affected channel name in the message text (e.g.
  `keyboard channel is insecure`). Phase 2's per-channel
  NOTIFY arms still earn their keep against non-QEMU
  SPICE servers (Kerbside proxy, future servers) and
  against any future QEMU notifications that aren't
  insecure-channel reports, but typical QEMU sessions
  will produce a small number of `Spice { channel: Main,
  what: N }` entries with distinct messages — never
  exercising cross-channel dedup. Phase 4's GUI should
  render these as separate entries in the side panel
  with the message text doing the per-affected-channel
  disambiguation.

* **Phase 1 planning, 2026-04-26**: the master plan's
  premise that "Ryll does not parse SPICE_MSG_NOTIFY at
  all today" is wrong. `shakenfist-spice-protocol` already
  defines a `Notify` wire-format parser
  (`messages.rs:182`) and a `NotifySeverity` enum
  (`constants.rs:389`), and `ryll/src/channels/main_channel.rs`
  (around line 520) already matches `main_server::NOTIFY`,
  parses the message, and routes it by severity into
  `tracing::warn!` / `info!`. The other six channels still
  drop the message. Phase 2's brief should be revised
  before that phase is planned: the wire-format parser
  exists, the main-channel handler exists, and Phase 2's
  real scope is (a) push the existing main-channel
  handler's parsed `Notify` into the notification store,
  (b) add the same NOTIFY arm to display / inputs /
  cursor / playback / usbredir / webdav, (c) tighten
  `Notify::visibility` from raw `u32` to
  `Option<SpiceVisibility>`. Phase 1 reuses the existing
  `NotifySeverity` rather than defining a parallel
  `NotificationSeverity`.

### Documentation index maintenance

When this master plan is created, add a row to
`docs/plans/index.md` *Master plans* table with today's
date, a link to this plan, the one-line intent
"In-app notifications surface for protocol gaps,
bug-report status, and SPICE_MSG_NOTIFY messages
that ryll currently drops on the floor", initial status
*Not started*, and links to each phase plan file as
they are written. Add an entry to `docs/plans/order.yml`.
Phase files are not added to `order.yml`. When all
phases are complete, mark *Complete*.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
