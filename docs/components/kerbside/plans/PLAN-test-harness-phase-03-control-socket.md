# Phase 3: Control socket on Ryll

Part of [PLAN-test-harness.md](/components/kerbside/plans/PLAN-test-harness/). Implementation
lands in `shakenfist/ryll`; the plan file lives here in
`kerbside/docs/plans/` per the master plan's single-home rule.

## Goal

Give Ryll's `--headless` mode a Unix-socket control interface so
external drivers (the latency loadtest port in phase 4, the
Sextant scenario tempest test in phase 7, ad-hoc debugging tools,
and an eventual SPICE MCP server) can drive a SPICE session from
outside the Ryll process. The interface speaks line-delimited
JSON over a Unix socket: clients send requests, Ryll replies, and
Ryll also emits unsolicited subscribed events (latency samples,
agent connect/disconnect, paste completions, and — once phase 6
lands — `digest_updated`).

This is the highest-effort step in the master plan and the
long-lead item. Getting the verb set and event shape wrong is
expensive to undo because phase 4 immediately depends on the v1
contract, phase 6 layers `digest_updated` on top, and the future
MCP server reuses the same socket.

Out of scope for phase 3:

- Digest decoding and the `digest_updated` event (phase 6).
- Mouse and USB-redirection verbs. The latency loadtest and
  Sextant scenarios in scope today don't need them; add when a
  test that needs them arrives.
- Authentication / encryption on the socket. Unix-socket file
  permissions are the security boundary for v1; if cross-host
  control is ever needed, that's a separate design.
- Multi-client concurrency. v1 accepts one client at a time;
  additional connect attempts get an immediate close.
- A non-headless mode of the control socket. The GUI mode of
  Ryll keeps its own input loop; the control socket is a
  headless-mode feature only.
- Replacing `--cadence` / `--paste-text` / `--latency-file`
  with control-socket equivalents. The existing flags keep
  working in parallel.

## Decisions baked into this plan

These were judgment calls made while drafting the phase plan
rather than questions to ask the operator. Flagged explicitly so
they can be challenged before code lands.

- **Transport**: Unix domain socket. Path supplied by a new
  `--control-socket <path>` CLI flag on Ryll's headless mode.
  File mode `0600` on bind. Not a TCP socket; the file
  permissions are load-bearing security.
- **Framing**: Line-delimited JSON (NDJSON). One JSON value per
  newline-terminated line, sent in either direction. No length
  prefix, no message envelopes beyond the JSON itself.
- **Concurrency**: One client at a time for v1. A second
  `accept()` while a client is connected gets a synthetic `busy`
  response and an immediate close.
- **Request shape**:
  ```json
  {"id": <integer or string>, "method": "<verb>", "params": {...}}
  ```
- **Response shape**:
  ```json
  {"id": <matching>, "ok": true,  "result": {...}}
  {"id": <matching>, "ok": false, "error": {"code": "...", "message": "..."}}
  ```
- **Event shape** (server-initiated, no `id`):
  ```json
  {"event": "<name>", "data": {...}}
  ```
- **Handshake**: First request after connect MUST be `hello`.
  Subsequent requests before hello are answered with
  `{"ok": false, "error": {"code": "no_hello_yet", ...}}`. The
  hello body carries the client's name + protocol version; the
  reply carries the server's name, supported version, the verb
  set, and the event set.
- **Subscriptions**: Explicit opt-in per event. `subscribe`
  takes a list of event names, returns the subset the server
  agreed to. Unrecognised events are quietly ignored (forward
  compat — a client newer than the server can still ask for
  `digest_updated` before phase 6 lands).
- **Backpressure**: A bounded per-client event queue (size 256).
  If the client falls behind enough to fill it, the server drops
  the oldest queued events and emits a `dropped` event once
  drain catches up, with a count of how many were dropped. The
  server never blocks SPICE channel producers on the client.
  Implementation pattern: tap the existing event mpsc with a
  `tokio::sync::broadcast` channel mirroring the `--web` mode's
  approach (`shakenfist_spice_renderer/src/session.rs::run_web`
  around line 561 is the existing precedent).
- **Module home**: New `shakenfist-spice-renderer/src/control/`
  module. The socket task lives alongside `run_headless` because
  it integrates with the existing tokio `select!` loop and the
  `input_tx` / `event_rx` channels.
- **Verb set v1**: `hello`, `status`, `send_key`, `paste`,
  `screenshot`, `subscribe`, `unsubscribe`.
- **Event set v1**: `latency`, `agent_connected`,
  `paste_completed`, `paste_failed`, `dropped`.
- **Protocol versioning**: `protocol_version` is a string. v1 is
  `"1.0"`. Hello with a mismatched major version is rejected
  with a clear error; minor mismatches are accepted.

## Situation

Ryll's headless mode lives at
`shakenfist-spice-renderer/src/session.rs::run_headless()`
(lines 405–609). Its run-time shape today:

- Constructs two mpsc channels: `(event_tx, event_rx)` size
  1024, `(input_tx, input_rx)` size 256.
- Spawns three tasks: `connection_handle` (main session
  orchestrator), optional `cadence_handle` (the 2-second
  keypress loop), optional `paste_handle` (one-shot paste).
- Main loop is a `tokio::select!` draining `event_rx` until
  connection closes or cancel fires.

Input pipeline today:
- `InputEvent` constructed in `run_headless` (e.g. line 479 for
  KeyDown/KeyUp, line 494 for PasteText).
- Sent via `input_tx`.
- Consumed by `InputsChannel::run()` in
  `shakenfist-spice-renderer/src/channels/inputs.rs:531`,
  which writes the corresponding `inputs_client::KEY_DOWN` /
  `KEY_UP` over the SPICE wire.

Event pipeline today:
- Channel handlers (`main_channel.rs::run`, `display.rs`, etc.)
  produce `ChannelEvent`s.
- Sent to `event_tx` (size 1024).
- Headless `run_headless` drains `event_rx` and updates
  `HeadlessStats`. `Latency { sample_ms }` produced in
  `main_channel.rs:1032` on PING/PONG return path.
- `--web` mode already taps `event_rx` via a broadcast channel
  (see `session.rs` around line 561). That pattern is what the
  control socket should reuse.

SurfaceMirror today:
- Lives at `shakenfist-spice-renderer/src/surface_mirror.rs`.
- Holds the live RGBA framebuffer as
  `HashMap<(channel_id, surface_id), DisplaySurface>` with
  `pixels: Vec<u8>` per surface.
- `--web` wraps it in `Arc<tokio::sync::Mutex<SurfaceMirror>>`
  (`main.rs:568`). Headless does **not** instantiate a mirror
  today.
- A control-socket `screenshot` verb needs a mirror in headless.
  Same wrap pattern as `--web`.

Serde / JSON precedent:
- `serde` and `serde_json` are already in `ryll/Cargo.toml`.
- `notification.rs::NotificationEntry` derives Serialize +
  Deserialize.
- No NDJSON precedent in the codebase. This phase introduces it.

Ryll planning conventions:
- Ryll has its own `PLAN-TEMPLATE.md`, `PUSH-AUDIT.md`,
  `docs/plans/` tree.
- Per the master plan's single-home rule, this phase's plan
  lives in **kerbside**, not in ryll. Commit messages in ryll
  for phase 3 work must reference the path
  `shakenfist/kerbside/docs/plans/PLAN-test-harness-phase-03-control-socket.md`
  so the trail back is obvious.

## Mission and problem statement

After phase 3:

- `ryll --headless --control-socket /tmp/ryll.sock --url
  spice://...` binds a Unix socket at the supplied path with
  mode `0600`, accepts a single client, and drives the headless
  SPICE session in response to NDJSON requests.
- The verb set covers everything the latency loadtest needs
  (phase 4) and everything the first Sextant scenario test
  needs (phase 7) except for digest assertions, which layer on
  in phase 6 via a new `digest_updated` event without changing
  the protocol envelope.
- The `--web` mode's broadcast tap is reused for the
  per-client event stream, so the control socket cannot stall
  the SPICE channel producers.
- A small Python example client in `ryll/examples/` shows the
  socket in use end-to-end against a running test target.
- Rust integration tests in Ryll spawn `--headless
  --control-socket`, connect a mock client over the socket,
  drive a basic flow, and assert the protocol envelope and
  verb behaviour.
- The protocol is documented in
  `ryll/docs/control-socket-protocol.md` and the README points
  at it.
- The existing `--cadence` and `--paste-text` flags still work
  unchanged.

## Open questions

These need answers before or during the phase, but do not block
writing this plan:

- **Screenshot payload format**: `png` (smaller, easy to view)
  or `rgba` (raw, faster on the Ryll side, easier on the client
  side). Default plan: `png` (the `image` crate is already a
  dep). Reconsider if PNG encode CPU shows up as a hot path in
  phase 7. Resolve at start of step 3e.
- **Should `paste` block its response on completion or return
  immediately?** Today `--paste-text` returns a
  `PasteCompleted` / `PasteFailed` ChannelEvent later. Default
  plan: return immediately with `{"ok": true}` and emit a
  `paste_completed` event for subscribers. Reconsider if a real
  caller needs synchronous semantics; trivial to add a
  `paste_sync` variant later.
- **Cancellation tokens through the socket?** A client that
  disconnects mid-`paste` should not leave the synthetic
  keypresses running. Resolve in step 3c — likely a per-action
  cancellation token tracked by request id.
- **What event names exactly?** The plan freezes the set listed
  in the decisions block above. If step 3a's design doc surfaces
  a different shape, that supersedes — but discuss before
  changing.

## Execution

Each step is one logical change → one commit on a feature
branch of Ryll. Per the master plan's cross-repo discipline:
phase 3 commits land in `shakenfist/ryll`, not kerbside; this
plan's file lives in kerbside but the work is elsewhere.

| Step | Repo | Effort | Model | Isolation | Brief for sub-agent |
|------|------|--------|-------|-----------|---------------------|
| 3a. Protocol design doc | ryll | low | sonnet | none | Land `ryll/docs/control-socket-protocol.md` documenting the wire format (NDJSON over Unix socket), the hello handshake, the request / response / event envelopes, the v1 verb set (`hello`, `status`, `send_key`, `paste`, `screenshot`, `subscribe`, `unsubscribe`), the v1 event set (`latency`, `agent_connected`, `paste_completed`, `paste_failed`, `dropped`), the error model, the subscription semantics (explicit opt-in, unknown events quietly ignored for forward compat), and the bounded queue / drop-oldest-with-`dropped`-event backpressure rule. Include exact JSON examples for every verb and event. Mark the doc clearly as protocol version 1.0 and explain the major/minor version negotiation rule. No code in this step; the doc is the contract the later steps implement against. |
| 3b. Socket scaffolding: CLI + bind + hello | ryll | medium | sonnet | worktree | Add `--control-socket <path>` to Ryll's clap CLI (only valid in `--headless` mode; reject if combined with the GUI mode). In `shakenfist-spice-renderer/src/control/mod.rs` and `protocol.rs`, define the request / response / event types using serde derive, and a small `Server` that binds a Unix socket at the supplied path with mode `0600`, accepts a single connection, and runs an NDJSON read / write loop. Implement `hello` (returns server name, protocol version `"1.0"`, supported_methods, supported_events) and `status` (returns connected-to-SPICE flag, agent_connected flag, list of known surfaces). All other methods return `{"ok": false, "error": {"code": "not_implemented", ...}}` for now. Concurrent accept while a client is connected → write a single `busy` JSON line and close. Wire the socket task into `run_headless`'s tokio runtime so it shuts down cleanly when the SPICE session ends. |
| 3c. Input verbs: send_key, paste | ryll | medium | sonnet | worktree | Implement `send_key` (params: `scancode: u16`, `state: "down"\|"up"\|"press"`) by translating to `InputEvent::KeyDown` / `KeyUp` and pushing onto `input_tx`. Implement `paste` (params: `text: string`, `char_delay_ms: Option<u32>`) as a `InputEvent::PasteText` push; response returns immediately `{"ok": true}` and the per-character progress comes via the `paste_completed` / `paste_failed` events in step 3d. Per-request cancellation: track a `tokio_util::sync::CancellationToken` per in-flight long-running action keyed by request id; on client disconnect, fire every token so synthetic keypresses stop. |
| 3d. Event subscription + the latency event | ryll | high | opus | worktree | This is the architectural pivot. Replace `run_headless`'s direct `event_rx.recv()` drain with a producer that fans events into a `tokio::sync::broadcast` channel (capacity ~1024). The existing headless stats consumer subscribes to it. The control socket's per-client subscription state holds a `broadcast::Receiver` and a bounded queue (size 256). Implement `subscribe` / `unsubscribe` (params: `events: Vec<String>`; returns the actually-subscribed subset). Map `ChannelEvent::Latency` → `event: "latency"` and `ChannelEvent::AgentConnected` → `event: "agent_connected"`; emit `paste_completed` / `paste_failed` from the existing handlers' events. Implement the queue overflow rule: drop oldest, emit one `dropped` event with count once the queue drains. Verify the existing `--cadence` / `--paste-text` flows still work with the new fan-out. This step is high effort because the event-loop refactor touches an architecture invariant the rest of headless depends on; the sub-agent must keep the `--web` mode's existing broadcast tap working too. |
| 3e. Screenshot verb | ryll | high | opus | worktree | Add a `SurfaceMirror` instance to `run_headless` (mirroring the `--web` mode's wrap: `Arc<tokio::sync::Mutex<SurfaceMirror>>`); apply `ChannelEvent`s that mutate the mirror through it in the existing select! arm. Implement `screenshot` (params: `surface_id: Option<u32>` defaulting to the primary, `format: Option<"png"\|"rgba">` defaulting to `"png"`). On request, lock the mirror, snapshot the requested surface's pixels (clone the Vec<u8>), drop the lock, encode to PNG via the `image` crate if requested, base64-encode, and respond with `{"width", "height", "format", "data_base64"}`. Document the cost: at 1024×768 the clone is 3 MB, the PNG encode is ~7 ms, the base64 inflates to ~4 MB string. For now that's acceptable; raw-bytes-out-of-band can come later if it hurts. |
| 3f. Tests and Python example | ryll | medium | sonnet | worktree | Rust integration tests under `ryll/tests/` (or a renderer test module if simpler) that spawn `--headless --control-socket` against a small fake SPICE source already used elsewhere in Ryll's test harness, connect a tokio client over the socket, exercise hello / status / send_key / paste / subscribe(latency) / screenshot, and assert the responses. A Python example at `ryll/examples/control-socket-demo.py` shows the same flow from outside Rust — used as a copy-paste starter for the phase 4 loadtest port. |
| 3g. Docs: ARCHITECTURE / AGENTS / README | ryll | low | sonnet | none | Update `ryll/ARCHITECTURE.md` (new section describing the control surface), `ryll/AGENTS.md` (note the new module home and protocol doc), and `ryll/README.md` (a short paragraph in the headless-mode section pointing at the protocol doc and the example client). Note in `kerbside/docs/console-sources.md` that the static source driver pairs with Ryll's control socket for end-to-end direct-qemu testing — this kerbside doc edit lands as an additional small commit on this same kerbside `test-harness` branch, since it's a cross-repo bookmark rather than implementation work in Ryll. |

### Sequencing notes

- 3a writes the contract. Don't start 3b–3g until 3a is reviewed.
- 3b is the smallest piece of working server. It must land
  before 3c, 3d, 3e, 3f because they all extend it.
- 3c and 3d can be partially interleaved but 3d's broadcast
  refactor is best done before 3c if possible — otherwise the
  `paste_completed` / `paste_failed` events in 3c won't have a
  delivery mechanism. Recommended order: 3a → 3b → 3d → 3c → 3e
  → 3f → 3g.
- 3e is independent of 3c / 3d once 3b is in.
- 3f's tests should evolve incrementally — write the test for
  each verb as the verb lands, not all at the end.
- 3g lands last so the docs reflect what actually shipped.

**Branch and PR shape in Ryll**: All phase 3 commits land on a
single Ryll feature branch (recommend `test-harness-control-socket`).
The operator opens the PR off Ryll's `main` once the chain is
green. Sub-agents should follow Ryll's own commit-message
conventions (rustfmt, clippy `-D warnings`, the wider Co-Authored-By
shape Ryll already uses), but every commit message must also
include a `Plan:` line pointing at
`shakenfist/kerbside/docs/plans/PLAN-test-harness-phase-03-control-socket.md`
so the cross-repo trail isn't lost.

## Agent guidance

This phase plan follows the conventions in `PLAN-TEMPLATE.md` at
the kerbside repo root. The execution model, effort levels,
model-choice guidance, brief-writing standards, and management-
session review checklist all apply unchanged and are not
duplicated here. Phase plans should fill in the per-step tables
described there.

Notes specific to phase 3:

- **Cross-repo briefing.** Every step lands in `shakenfist/ryll`,
  not kerbside. Sub-agents must consult Ryll's own
  `PLAN-TEMPLATE.md`, `PUSH-AUDIT.md`, `AGENTS.md`, and
  `ARCHITECTURE.md` for build commands and house style — but the
  *plan they are following* is the one in this kerbside file.
  Include both pointers in the brief.
- **Existing `--cadence` and `--paste-text` flows must keep
  working.** Step 3d's event-loop refactor is the place this is
  easiest to break. The phase-1-style "capture-before /
  capture-after" technique applies: before refactoring, run
  `--cadence` against a known SPICE source and confirm the
  latency probe logs continue; after, the same probe must still
  log under the new fan-out.
- **One client at a time.** Sub-agents should not be tempted to
  generalise to multi-client in v1. Mutexing the socket task
  with an `AtomicBool` for "client present" is the entire
  concurrency story until a use-case demands more.
- **The protocol doc (step 3a) is the load-bearing artefact.**
  Spend disproportionate time getting it right. Phase 4 reads
  it to write the loadtest port; phase 6 reads it to layer
  `digest_updated` in; a future MCP server reads it to design
  its mapping. If the protocol envelope changes after phase 4
  lands, every downstream consumer has to follow.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the step and how the work
you intend to do aligns with that step's brief.

## Administration and logistics

### Success criteria

Phase 3 is done when:

- `cargo test --workspace` in Ryll passes on the
  test-harness-control-socket branch. `clippy -D warnings` and
  `rustfmt` are clean.
- A `ryll --headless --control-socket /tmp/ryll.sock --url
  spice://<test-target>` started against a real SPICE source
  accepts a Python client connection, completes a hello / status
  / subscribe(latency) / send_key flow, and the client receives
  at least one `latency` event in under five seconds.
- The Python example at `ryll/examples/control-socket-demo.py`
  runs end-to-end against the same test target without manual
  intervention.
- The Rust integration tests cover every verb in the v1 set and
  every event in the v1 set.
- `ryll/docs/control-socket-protocol.md`, `ARCHITECTURE.md`,
  `AGENTS.md`, and `README.md` are updated. The kerbside
  `docs/console-sources.md` has a cross-repo bookmark in the
  static-source section.
- The Ryll PR is open against Ryll's `main` and tagged for
  review by the operator. The management session does not open
  the PR.

### Future work

Items deliberately deferred from phase 3:

- **Mouse and USB-redirection verbs.** Add when a test needs
  them.
- **Authentication and encryption** on the socket. File
  permissions are the security boundary for v1.
- **Multi-client concurrency.** Single-client v1 is sufficient
  for every phase in the master plan.
- **Out-of-band binary transport for screenshots.** Base64 in
  NDJSON is fine until it isn't.
- **Synchronous `paste_sync` verb.** Easy to add later if a
  caller needs it.
- **A non-headless control socket** (the GUI mode driving its
  own input loop). Out of scope.

### Bugs fixed during this work

(None yet.)
