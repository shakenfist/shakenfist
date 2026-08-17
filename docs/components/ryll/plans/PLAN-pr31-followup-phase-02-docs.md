# Phase 2 — PR 31 follow-up: docs sweep

Parent: [PLAN-pr31-followup.md](/components/ryll/plans/PLAN-pr31-followup/).

## Goal

Document the user-visible and architectural pieces PR 31
introduced that currently exist only in the code:

- The Reconnect button in the disconnect dialog
  (user-visible).
- Window persistence (size and position restored across
  launches, user-visible).
- The reconnect flow (architectural — how the GUI tears
  down channel state and spawns a fresh tokio runtime).
- The mouse-mode negotiation model (architectural — two
  modes, the request-loop guard, and the post-reboot
  recovery path).

No code changes. Pure documentation phase. Output is a
reader who can answer "how does Reconnect work?" or
"why does the cursor jump after a guest reboot?" from
the docs alone.

## Scope

In scope:

- Two new bullets in `README.md`'s Features list
  (Reconnect, Window persistence).
- A new `### Mouse-Mode Negotiation` subsection inside
  the existing `## SPICE Protocol` section of
  `ARCHITECTURE.md`.
- A new top-level `## Reconnection` section in
  `ARCHITECTURE.md`, placed after `## Graceful Shutdown`.
- A short cross-link from the existing inline
  mouse-mode mention at
  `ARCHITECTURE.md:100-109` to the new
  Mouse-Mode Negotiation subsection (so readers landing
  on either reference find the deeper explanation).

Out of scope (other phases / future PRs):

- Any code change. The polish items (cancellation token
  for `reconnect`, cursor-hide overlay handling,
  clipboard CRLF normalisation, log-line three-arm
  match) are Phase 3+.
- API doc-comments in source files. The existing
  function-level rustdoc on `reconnect()` and
  `maybe_request_client_mouse_mode` is sufficient and
  was reviewed during the PR 31 merge.
- Updating the changelog or release notes. These docs
  describe behaviour that has already shipped on
  `develop`; release notes for PR 31 itself are tracked
  separately.

## Grounding — what's there today

### README.md

Today's Features list is at
[`README.md:5-33`](https://github.com/shakenfist/ryll/blob/develop/README.md#L5-L33) — 29 bullets,
each one line, alphabetical-ish but really order-of-arrival
with new features appended. Reconnect and persistence are
not present. The list ends with the in-app notifications
panel bullet from the PR 47 work.

There is no top-level Reconnect or Persistence section
elsewhere in the README. Searching the file for
`reconnect`, `persistence`, `persistent` returns no hits.

### ARCHITECTURE.md

The closest existing mention of the mouse-mode model is at
[`ARCHITECTURE.md:100-109`](https://github.com/shakenfist/ryll/blob/develop/ARCHITECTURE.md#L100-L109),
inside a longer bullet describing the input channel:

> Mouse events are dispatched based on the server's mouse
> mode: client mode (2) sends absolute MOUSE_POSITION,
> server mode (1) sends relative MOUSE_MOTION. At session
> startup, ryll requests client mode via
> MOUSE_MODE_REQUEST if the server supports it

This is correct as far as it goes, but it predates
`10f19477` and PR 31 — it does not mention the
post-reboot recovery path, the
`mouse_mode_request_pending` guard, or the wire-format
shape (`flags16`, two little-endian bytes).

There is no existing section describing reconnect.
Searching for `reconnect` in `ARCHITECTURE.md` returns no
hits.

The two natural anchors for new sections are:

- `## SPICE Protocol` (line 120) with subsections
  Connection Sequence, Message Format, Channel Types.
  Adding `### Mouse-Mode Negotiation` as a fourth
  subsection puts it next to the protocol material it
  belongs with.
- `## Graceful Shutdown` (line 774) with subsection
  Unbuffered capture I/O. Following it with a new
  top-level `## Reconnection` keeps lifecycle
  documentation grouped — shutdown → reconnection in
  reading order.

### Code under documentation

The reconnect implementation is
[`ryll/src/app.rs::reconnect`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L634-L751)
(118 LOC). It:

1. Creates fresh mpsc channels for events, input,
   usb, webdav, resize.
2. Resets all per-session UI state — surfaces,
   cursor, statistics, mouse mode, error / dialog
   flags, traffic buffers, channel snapshots,
   bandwidth tracker, USB / WebDAV state.
3. Allocates a fresh `Arc<Notify>` for repaint
   wake-ups, a fresh `ByteCounter`, fresh
   `TrafficBuffers`, fresh `ChannelSnapshots`, fresh
   `VolumeControl`.
4. Spawns a new `std::thread::spawn` containing a new
   `tokio::runtime::Runtime`, which `block_on`s
   `run_connection(...)` with `self.config`,
   `self.reconnect_virtual_disks`,
   `self.reconnect_share_dir`, `self.enable_paste`,
   `self.notifications`, and the freshly created
   per-session pieces.

Two facts to capture in the architecture text:

- The previous attempt's runtime is **not** explicitly
  stopped. PR 31 review item 6 (Should consider) is
  the cancellation-token follow-up that addresses
  this; the docs should mention the current behaviour
  truthfully and cross-link to that follow-up so
  readers know it is tracked.
- Some state is preserved across reconnects on
  purpose: the parsed CLI config, the chosen virtual
  disk list, the chosen shared folder, the
  notifications store, and the paste-as-keystrokes
  toggle. This is the "what survives a reconnect"
  question a reader would ask first.

The mouse-mode model is implemented in
[`ryll/src/channels/main_channel.rs`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/main_channel.rs),
specifically:

- `parse_mouse_mode_payload` (line 32) — u16 read.
- `should_request_client_mouse_mode` (line 46) — pure
  predicate.
- `build_mouse_mode_request_payload` (added in Phase 1)
  — u16 write.
- `MainChannel::mouse_mode_request_pending` field
  (line 106).
- `INIT` handler clearing branch (after the
  `parse_mouse_mode_payload` call when the new
  mode is CLIENT).
- `MOUSE_MODE` handler at line ~389, which
  re-evaluates `maybe_request_client_mouse_mode` on
  every server-initiated mode change.
- `maybe_request_client_mouse_mode` itself (line 667).

Window persistence is one Cargo feature flag at
[`ryll/Cargo.toml:16`](https://github.com/shakenfist/ryll/blob/develop/ryll/Cargo.toml#L16):

```toml
eframe = { version = "0.29", features = ["persistence"] }
```

Ryll itself has no persistence-related code; eframe
handles serialisation to a per-app directory under the
platform's config dir (`~/.local/share/ryll/` on
Linux, `%APPDATA%\ryll\` on Windows, `~/Library/Application
Support/ryll/` on macOS — these locations follow
egui's defaults; we do not override them). The
restored state is the egui memory snapshot, which
includes window size/position and any docked-panel
layout state; not arbitrary application state, since
ryll has not opted in to per-struct serde via
`#[serde(default)]` or the like.

## Design

### README — Features list additions

Two new bullets, appended near the existing connection /
session bullets (the order is lossy in the existing list
but several connection-related items cluster around
"TLS support" and "Cursor rendering" near
[`README.md:15-17`](https://github.com/shakenfist/ryll/blob/develop/README.md#L15-L17)). Place the
new bullets right after the TLS-support line for grouping.

```
- **Reconnect on disconnect** — When a session ends
  unexpectedly, the disconnect dialog offers a Reconnect
  button that drops all per-session state and re-attempts
  the SPICE handshake against the same target without
  exiting the application. Preserves the configured
  virtual disk list, shared folder, paste-as-keystrokes
  toggle, and notification history; resets statistics,
  traffic buffers, and per-channel state.
- **Window persistence** — Window size and position are
  restored across launches via eframe's `persistence`
  feature, which writes the egui memory snapshot to the
  platform's per-app config directory
  (`~/.local/share/ryll/` on Linux,
  `~/Library/Application Support/ryll/` on macOS,
  `%APPDATA%\ryll\` on Windows).
```

Sequence chosen so a reader skimming Features sees
"reconnect — what it does for *this* session" before
"persistence — what survives *across* sessions".

### ARCHITECTURE — `### Mouse-Mode Negotiation`

New `### Mouse-Mode Negotiation` subsection inside
`## SPICE Protocol`, placed immediately after
`### Channel Types` (so it sits with the rest of the
protocol material, before the more visual
`## Image Types and Compression` section that
follows).

Content to cover, in this order:

1. The two modes and what each implies for input
   dispatch (one-liner each).
2. The wire format on both directions
   (`SpiceMsgMainMouseMode` from server is two
   little-endian u16s — supported_modes then
   current_mode; `SpiceMsgcMainMouseModeRequest` to
   server is one little-endian u16 with the requested
   mode flags). Reference `spice.proto`'s
   `flags16` declaration.
3. The negotiation flow:
   - INIT: server announces supported + current; client
     calls `maybe_request_client_mouse_mode`.
   - MOUSE_MODE: server pushes a state change; client
     re-evaluates (covers post-reboot recovery).
4. The `mouse_mode_request_pending` guard. Why it
   matters: a server that lists CLIENT as supported but
   refuses to switch would otherwise produce a request
   loop. Cleared when MOUSE_MODE arrives with
   current=CLIENT.
5. Pointer to the unit tests at
   `ryll/src/channels/main_channel.rs` for the
   parser, the predicate, and the encoder. Helps a
   future reader who is changing the mouse-mode code
   know which tests guard which property.

The existing inline mention at lines ~100-109 changes
to a brief "see Mouse-Mode Negotiation below" pointer,
so the input-channel bullet does not get out of sync
with the canonical text again.

### ARCHITECTURE — `## Reconnection`

New top-level section following `## Graceful Shutdown`,
preceding `## Statistics and Instrumentation`. Use the
following structure:

1. **Trigger**: short paragraph — the disconnect dialog
   fires when the server closes the main channel or any
   secondary channel reports an unrecoverable error.
   Reconnect is a user gesture, not automatic.
2. **What is recreated**: bulleted list of the fresh-each-
   reconnect pieces (mpsc channels, runtime, notify,
   byte counter, traffic buffers, channel snapshots,
   bandwidth tracker, volume control, repaint bridge).
3. **What survives**: bulleted list of the preserved-
   across-reconnect pieces (parsed CLI config, virtual
   disks, share dir, paste-as-keystrokes toggle,
   notifications history, egui context).
4. **Threading and runtime**: short paragraph noting
   that each reconnect spawns a fresh
   `std::thread::spawn` containing a new
   `tokio::runtime::Runtime`. The previous attempt's
   runtime is not explicitly stopped — the previous
   socket eventually times out and the runtime
   collapses with no live tasks. This is correct in the
   common case (server is reachable; the previous
   handshake either failed or completed) but accumulates
   threads if Reconnect is spammed against an
   unresponsive server. Cross-link to
   [PLAN-pr31-followup.md](/components/ryll/plans/PLAN-pr31-followup/)
   "Should consider" item 6 (cancellation token).

The "what survives" list is the answer most readers
arrive looking for. Put it before the threading
discussion so it is not gated behind a paragraph about
internals.

### Cross-linking

After both sections land, the existing
`### Channel Types` row for the Inputs channel and the
existing inline input-channel bullet at
ARCHITECTURE.md:100-109 should both contain a "see
Mouse-Mode Negotiation" link. Otherwise a reader on a
mouse bug starts reading at "Channel Types", finds a
one-line description, and stops — they need a path to
the deeper section.

Likewise, a reader on `reconnect()` source code who
greps for `reconnect` in ARCHITECTURE.md should land on
the new section. The new section is the only mention,
so this works without further cross-linking.

## Implementation steps

1. **README**: add the two Features bullets at the
   chosen position (after the TLS-support line).
   Rationale: connection-lifecycle items cluster there
   already.
2. **ARCHITECTURE — Mouse-Mode Negotiation**: insert
   the new `### Mouse-Mode Negotiation` subsection
   immediately after `### Channel Types`. Use the
   structure described in the Design section above.
   Replace the existing inline mouse-mode sentence at
   line 100-109 with a short pointer to the new
   subsection.
3. **ARCHITECTURE — Reconnection**: insert the new
   `## Reconnection` top-level section immediately
   after `## Graceful Shutdown`. Use the structure
   described in the Design section above. Make sure
   the cross-link to PLAN-pr31-followup.md item 6
   is intact and points at the right item.
4. **Run the link-check / markdown lint** — pre-commit
   includes the markdown link-check hook. Running
   `pre-commit run --all-files` covers it.
5. **Verify the index by reading the rendered docs**:
   the docs/ directory is exported into shakenfist's
   docs site; the import preserves the markdown
   verbatim, so no separate render step is needed —
   but visually scan the diff for heading levels and
   list indentation in case an editor mangled them.
6. **Update the master plan**: strike through items
   3, 4, and 5 in
   [PLAN-pr31-followup.md](/components/ryll/plans/PLAN-pr31-followup/)'s
   "Should fix" section, with one-line pointers to
   this phase plan. Remove the bundling guidance for
   docs from the Administration > Tracking
   subsection (the "One PR for items 3-5 (docs sweep)"
   bullet has been honoured).

## Acceptance criteria

- `pre-commit run --all-files` passes (markdown link
  check + bidi/zero-width unicode + secrets, the
  hooks that exercise text files).
- A reader looking for "how does Reconnect work" can
  find the answer by greping `reconnect` in either
  README.md or ARCHITECTURE.md and following one
  link.
- A reader looking for "what does mouse-mode CLIENT
  mean and why does it sometimes flip" can find the
  answer by greping `mouse mode` in ARCHITECTURE.md
  and reading one section.
- The diff covers exactly three files: `README.md`,
  `ARCHITECTURE.md`, and
  `docs/plans/PLAN-pr31-followup.md`. No source
  changes.
- The READMEs/AGENTS.md/CLAUDE.md project rule is
  honoured — README updated.
- The CLAUDE.md instruction to update ARCHITECTURE.md
  when changes warrant it is honoured.

## Verification — does the doc actually read well

Per the project's docs etiquette, after the rewrite:

1. Read both updated sections cold (close the editor,
   reopen, scroll past the surrounding text). Does each
   section answer the question its heading promises
   without depending on context the reader cannot see?
2. Search README.md for `reconnect` and `persistence`
   case-insensitively. Confirm the two new bullets are
   the only hits and the surrounding bullets read in a
   sensible order.
3. Search ARCHITECTURE.md for `reconnect` and
   `mouse mode` case-insensitively. Confirm the
   inline-and-link pattern works — the small mention
   stays in the input-channel bullet but it links
   forward.
4. Skim the `## Reconnection` "what survives" list
   against the actual `RyllApp::reconnect` source.
   Anything in the list must correspond to a field
   that reconnect does **not** reset; anything left
   off must correspond to a field that reconnect
   **does** reset. This is the highest-stakes accuracy
   check in the whole phase; getting it wrong creates
   false confidence in lifecycle behaviour.

If step 4 finds drift between the doc and the code,
the doc loses — update it to match the code, do not
"correct" the code as part of this phase.

## Out of scope — explicitly

- The cancellation-token fix for `reconnect()` (master
  plan item 6) is not part of this phase. The new
  Reconnection section in ARCHITECTURE.md will note
  the current behaviour and cross-link to the
  follow-up.
- The cursor-hide overlap fix, clipboard CRLF
  normalisation, and the MOUSE_MODE log-line
  three-arm match (master plan items 7, 8, 9) are
  similarly Phase 3 polish.
- Anything that requires a screenshot in the docs.
  The existing README/ARCHITECTURE convention is
  text-only.

## Update of master plan on completion

When this phase merges to develop:

- Strike through items 3, 4, 5 in the "Should fix"
  section of
  [PLAN-pr31-followup.md](/components/ryll/plans/PLAN-pr31-followup/) with
  one-line notes pointing to this phase plan and the
  landed PR.
- Update the index entry for the master plan to add
  this phase to the Phases column.
- The Administration > Tracking subsection's
  "One PR for items 3-5 (docs sweep)" bullet has been
  honoured; rewrite the bullet to "Phase 2 landed on
  YYYY-MM-DD" so future readers can find the
  resulting PR / commit quickly.

After this phase, only Phase 3 (the Should consider
items) remains on the master plan.
