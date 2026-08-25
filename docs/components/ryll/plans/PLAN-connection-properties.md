# Connection properties dialog

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
definitions), `/srv/src-reference/spice/spice-gtk/`
(reference C client), and `/srv/src-reference/qemu/qemu/`
(server-side SPICE in `ui/spice-*`).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via the table in the *Execution* section below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Ryll today shows a small amount of connection state in the
status bar (volume, latency, FPS, bandwidth, USB device
description) and exposes feature actions through a hamburger
menu at the right edge of the status bar
([PLAN-hamburger-menu.md](/components/ryll/plans/PLAN-hamburger-menu/), now
landed). What it does **not** show anywhere visible is the
connection's structural metadata: which SPICE host and port
we're talking to, whether TLS is in use, the negotiated
protocol version, the session id, the set of channels that
linked successfully, the capabilities each channel
negotiated, and the current display surface mode (resolution
and pixel format).

Some of this metadata already exists internally:

* Per-display surface dimensions are tracked on
  `DisplaySurface` (`ryll/src/display/surface.rs:15-16`) and
  stored in `app.rs:207`'s
  `HashMap<(u8, u32), DisplaySurface>`.
* Channel handlers consume `LinkReply` (which carries
  `common_caps` and `channel_caps` per channel —
  `shakenfist-spice-protocol/src/link.rs:58-59,136-138`)
  during handshake but do **not** route those caps up to the
  app for display.
* The main channel tracks agent connection state, mouse
  mode, and a session id; the USB and WebDAV channels track
  their own peer-cap fields (`channels/usbredir.rs:366`).

What we cannot do today:

* Tell, from the UI, what protocol version the server
  speaks.
* See which capability bits were negotiated on each
  channel (e.g. `DISPLAY_COMPOSITE`,
  `DISPLAY_SIZED_STREAM`, GLZ image compression, mini
  headers).
* Confirm at a glance which channels are currently linked
  vs reconnecting vs never came up.
* Read the current display surface resolution and pixel
  format without inferring it from window size.

The original trigger for this plan was a smaller idea —
show display mode and resolution in the title bar — but
once we looked at it, the SPICE protocol does **not**
advertise the underlying QEMU display device type (QXL vs
VGA vs virtio-gpu), and most of what the operator actually
wants to see is a richer collection that doesn't fit in a
title bar. A dialog opened from the hamburger menu is the
right home.

## Mission and problem statement

Add a **Connection properties** entry to the hamburger menu
in `ryll/src/app.rs:1922`. Selecting it opens an
`egui::Window` showing the structural state of the current
SPICE connection. The dialog has sections covering server
identity, protocol negotiation, channels, capabilities, and
display surfaces. Each section pulls from data that ryll
already tracks (where available) or that we add the
plumbing for (where not).

The dialog is read-only: no buttons that change connection
state, no editing, no triggers. It is a diagnostic surface
for operators and a stepping stone toward better
bug-report content.

Out of scope for this plan:

* Per-frame statistics (FPS, latency, bandwidth) — those
  belong in the status bar and already live there. The
  dialog may link to them by reference but does not
  duplicate.
* Inferring the QEMU display device type (QXL vs VGA vs
  virtio-gpu). The investigation that prompted this plan
  showed SPICE does not surface this server-side detail
  to clients. A separate effort with a guest agent could
  reach it, but is not part of this work.
* Changing the connection (reconnect button, switch host,
  toggle TLS). The dialog is read-only.

## Open questions

1. **Refresh strategy — live or snapshot?** Most fields
   (host, port, protocol version, negotiated caps) are
   immutable after handshake. A few (active channels,
   surface size, agent connected) can change. Recommend
   *snapshot at open + manual Refresh button*: the dialog
   is opened occasionally, not watched, so reactive
   plumbing is unjustified. Settle this before phase 2.

2. **Where to surface negotiated caps to the app**.
   `LinkReply.common_caps` and `channel_caps` are consumed
   by each channel handler today and not held centrally.
   Two options:
   * (a) Each handler emits a one-shot
     `ChannelEvent::Linked { caps, version, ... }` to the
     app on successful handshake. The app stores it in a
     `HashMap<ChannelKey, LinkInfo>`.
   * (b) The app exposes an `Arc<RwLock<ConnectionInfo>>`
     that handlers write into directly.
   Recommend (a): it matches the existing event-based
   plumbing (e.g. `SurfaceCreated` already flows through
   `ChannelEvent`), keeps handlers loosely coupled, and
   avoids a new shared-mutable surface. Settle in phase 1.

3. **Cap name decoding**. `common_caps` and `channel_caps`
   are `Vec<u32>` bitmasks. Showing them as raw hex is
   cheap but unfriendly; showing names ("MINI_HEADER",
   "DISPLAY_COMPOSITE", "DISPLAY_SIZED_STREAM",
   "PLAYBACK_CELT_0_5_1") is what the operator wants.
   We need a `(channel_type, bit_index) → str` table.
   Source-of-truth is
   `/srv/src-reference/spice/spice-protocol/spice/protocol.h`
   (mirrored in kerbside docs). Recommend generating the
   table by hand from the canonical header — there are
   roughly forty bits across all channels, and the set
   changes rarely. Phase 3 owns this work and includes
   unit tests asserting a few well-known bits decode.

4. **Channel state granularity**. The dialog wants to
   show whether each channel is *linked / reconnecting /
   failed / never attempted*. Today the app tracks a
   subset of this implicitly via running tokio tasks and
   `is_connected()` checks. Recommend extending
   `ChannelEvent` with a `ChannelState` enum and tracking
   it on the app side; this is also useful for future
   diagnostics and reconnect UI. Settle in phase 1.

5. **USB and WebDAV channel inclusion**. These are
   bidirectional VMC port channels with their own cap
   negotiation (`usbredir` has its own `Hello` exchange
   independent of SPICE caps; WebDAV runs `spice-vmc` over
   a port channel). Recommend showing them as channel
   entries with their own cap rendering, but flagged as
   "VMC port channel" rather than mixed in with SPICE
   common/channel cap conventions. Phase 3 details.

6. **Pixel format / colour depth**. SPICE primary surfaces
   carry a format code in `SURFACE_CREATE`
   (`channels/display.rs:693`). We already use it
   internally to pick a renderer path. Recommend showing
   the human-readable name (`32_xRGB`, `16_555`, etc.)
   alongside the resolution.

7. **Title bar resolution stripe?** The original idea was
   to put resolution in the title bar. After this plan
   lands the dialog is the canonical home, but a one-line
   `WxH` in the title bar is still cheap and glanceable.
   Recommend deciding *after* the dialog ships: if
   operators end up opening the dialog repeatedly just to
   read the resolution, that's a signal to add the title
   stripe. Listed in *Future work* below.

## Execution

Five phases. Each phase plan is filled in when its phase
starts.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Connection-info plumbing | PLAN-connection-properties-phase-01-plumbing.md | Not started | — |
| 2. Dialog UI               | PLAN-connection-properties-phase-02-dialog.md | Not started | — |
| 3. Capability name decoding | PLAN-connection-properties-phase-03-cap-names.md | Not started | — |
| 4. Docs                     | PLAN-connection-properties-phase-04-docs.md | Not started | — |
| 5. Push audit               | PLAN-connection-properties-phase-05-push-audit.md | Not started | — |

### Phase 1 — Connection-info plumbing

Route per-channel handshake metadata up to the app. After
this phase, `app.rs` has a `ConnectionInfo` struct (or
similar) populated as channels link, holding:

* Server endpoint: host, port, TLS y/n, proxy chain (if
  routed via kerbside).
* Protocol version: major / minor from `LinkReply`.
* Per-channel `LinkInfo`: channel type, channel id,
  state (`Linking / Linked / Reconnecting / Failed`),
  `common_caps`, `channel_caps`, and connection-time
  metadata (link time, reconnect count).
* Agent connection state and mouse mode (already on app,
  consolidate references).
* Session id from main channel.

New `ChannelEvent` variants emit `Linked { ... }` and
`StateChanged { ... }`. Existing handlers emit them at
the end of handshake and on reconnect.

No UI in this phase — verify by adding debug log lines
that print the captured info and confirming handshake
output is correct against a kerbside or QEMU target.

### Phase 2 — Dialog UI

Add `show_connection_properties_dialog: bool` to `RyllApp`.
Add a "Connection properties…" entry to the hamburger menu
in `app.rs:1922`. Render an `egui::Window` with sections:

* **Server** — host, port, TLS, proxy.
* **Protocol** — version, session id.
* **Channels** — table: type, id, state, link time, common
  caps, channel caps. Caps shown as human names (phase 3)
  with raw hex on hover.
* **Display surfaces** — for each known surface: surface
  id, width × height, pixel format, primary y/n.
* **Agent** — connected y/n, mouse mode, vdagent caps if
  present.

Include a `Refresh` button that re-reads the snapshot from
`ConnectionInfo` and a `Close` button. Dialog is modal-ish
(non-blocking egui window; closes on outside click or
button).

### Phase 3 — Capability name decoding

Add a module (e.g. `shakenfist-spice-protocol::caps`)
exposing `pub fn cap_name(channel_type: u8, common: bool,
bit: u8) -> Option<&'static str>`. Populate from
`/srv/src-reference/spice/spice-protocol/spice/protocol.h`
plus the channel-specific headers. Unit tests assert a
handful of well-known bits decode (e.g. main
`SPICE_COMMON_CAP_AUTH_SPICE` bit 1, display
`SPICE_DISPLAY_CAP_COMPOSITE` bit ?). Wire into the dialog
rendering from phase 2.

USB redir and WebDAV cap rendering: handled here but with
their own decoders. Mark VMC port channels distinctly.

### Phase 4 — Docs

Update `README.md` and `ARCHITECTURE.md` to describe the
dialog. Update `AGENTS.md` if it lists places where new
connection state should be tracked. Add a screenshot to
`docs/` if other dialogs have screenshots (check
convention before adding).

### Phase 5 — Push audit

Work `PUSH-AUDIT.md` over the accumulated diff of phases 1-4
against `develop`, not the last phase's diff alone, so the
plumbing, dialog, decoding and docs changes are audited as
the one change they add up to. Findings land as their own PR
against `develop`, recorded here under an *Items deferred
from the push audit* heading — the shape
`PLAN-web-frontend.md` uses for its *Items deferred from the
post-Phase-N pre-push audit* sections, minus the phase
number, because this phase audits the whole plan rather than
a range of it. This plan is not complete until each is fixed
or declined in writing. If the audit finds nothing, record
that here in one sentence.

Phases 1-4 have merged by the time this phase runs, so
`develop...HEAD` is empty on the audit branch and the
accumulated diff has to be assembled from the phases' merge
commits. Record each phase's merge commit in the `Merged`
column of the *Phase order* table above as that phase lands;
*Two ways this runbook is invoked* in `PUSH-AUDIT.md` has the
rest.

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

Use `isolation: "worktree"` for sub-agents when the
change is risky or experimental. Phase 1 (channel-event
plumbing) is the most invasive and may benefit from
worktree isolation. Phases 2–4 are safe to run in the
main tree.

### Planning effort

* **Master plan** (this file): high effort.
* **Phase 1**: high effort. Touches every channel handler
  and adds new event variants; getting the abstraction
  right matters and the planner must read each handler.
* **Phase 2**: medium effort. egui dialog work follows
  established patterns (bug dialog, disconnect dialog).
* **Phase 3**: medium effort. The work is mechanical
  once the planner has identified the canonical header to
  copy from; planner needs to confirm the source of truth
  and the shape of the table.
* **Phase 4**: low effort. Doc updates only.

### Step-level guidance

Per-phase tables go in the phase plans. As a rough
forecast for budgeting:

| Phase | Effort | Model | Isolation |
|-------|--------|-------|-----------|
| 1     | high   | opus  | worktree  |
| 2     | medium | sonnet | none     |
| 3     | medium | sonnet | none     |
| 4     | low    | haiku  | none     |

### Management session review checklist

After each sub-agent completes, the management session
should verify:

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code builds (`pre-commit run --all-files`).
- [ ] Tests pass (`cargo test --workspace`).
- [ ] The changes match the intent of the brief — not
      just syntactically correct but semantically right.
- [ ] Commit message follows project conventions
      (including the Co-Authored-By line with model,
      context window, effort level, and other settings).

## Administration and logistics

### Success criteria

* `pre-commit run --all-files` passes.
* `cargo test --workspace` passes.
* The hamburger menu has a *Connection properties…*
  entry. Selecting it opens an `egui::Window` populated
  with the sections described above.
* Channel link and state events flow through
  `ChannelEvent`; no channel handler shortcuts the
  abstraction by writing app state directly.
* Capability bits render as human-readable names with
  raw hex available on hover. Unknown bits render as
  `bit N (unknown)` rather than dropping silently.
* Closing the dialog and reopening it shows up-to-date
  state (the `Refresh` button is not strictly required
  for correctness but is included for operator
  convenience).
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` mention
  the dialog where relevant.

### Future work

* **Title-bar resolution stripe**. If operators repeatedly
  open the dialog just to check resolution, add `WxH` to
  the window title. Cheap follow-up once the dialog
  ships.
* **QEMU display device inference**. Distinguishing QXL
  vs VGA vs virtio-gpu requires a guest-agent round-trip
  (e.g. `lspci` over a custom agent channel) — SPICE
  itself does not expose the device type. Worth doing if
  the operator value is high; treat as a separate plan.
* **Live channel state**. If the dialog gets used as a
  monitoring surface (rather than diagnostic), replace
  the snapshot model with reactive updates and remove
  the Refresh button.
* **Export to bug report**. The connection-properties
  snapshot is exactly the kind of thing bug reports
  should embed. Once both this plan and the bug-report
  metadata work are stable, consider auto-attaching a
  serialised snapshot to every bug zip.
* **Graphical channel topology**. A small diagram of
  channels (main → display → cursor → inputs etc.) with
  link state colour-coded. Probably overkill, listed
  here so we don't forget the idea.

### Bugs fixed during this work

(To be filled in as the work proceeds.)

### Documentation index maintenance

When this plan was created, a row was added to
`docs/plans/index.md` *Master plans* table with today's
date (2026-04-28), a link to this plan, the one-line
intent "Connection properties dialog showing server
endpoint, protocol version, channels, negotiated
capabilities, and display surfaces", initial status *Not
started*, and links to the four phase plan files (which
do not yet exist). An entry was also added to
`docs/plans/order.yml`.

Mark *Complete* in `index.md` when phase 5 lands and
every push-audit finding is fixed or declined above.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
