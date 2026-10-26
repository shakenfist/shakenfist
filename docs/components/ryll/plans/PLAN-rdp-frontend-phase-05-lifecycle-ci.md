# RDP frontend phase 5: lifecycle and CI

Master plan: [PLAN-rdp-frontend.md](/components/ryll/plans/PLAN-rdp-frontend/).

**Planning effort:** high. The master plan's guidance plans
phases 3 to 6 at medium effort "except that phase 5's reconnect
work should be planned at high effort if it touches the session
orchestrator". It does: Mikal chose SPICE auto-reconnect for
`--rdp` (decision 1), which no server mode has today, and
takeover needs the RDP server's connection handling rebuilt
(decision 5). Review steps 1, 3 and 4 at high effort too.

## Prompt

Read the master plan's *Prompt* first; everything there
applies. For this phase in particular:

- The RDP side is still IronRDP **0.13.0** (`ironrdp-server`
  0.13.0, `ironrdp-acceptor` 0.10.0). `make fetch` puts its
  source under `.cargo-cache/registry/src/index.crates.io-*/` in
  the worktree, and every IronRDP file and line cited below is
  relative to `ironrdp-server-0.13.0/` there. Read it; never
  build or run anything from inside it. Re-check every IronRDP
  claim a brief relies on before relying on it.
- The host's FreeRDP is 3.15.0 (`xfreerdp3`). Debian 13, which
  the CI runners and the smoke image use, packages the same
  series; the lane prints the version it ran.
- Builds run in Docker through the Makefile, never with a host
  toolchain. QEMU, Xvfb and FreeRDP for the smoke lane run in a
  container of their own (decision 7), never installed on the
  host by a step.
- The test guest for the lane is the UEFI latency guest
  (`make test-qemu`'s `testdata/uefi-latency-guest.qcow2`, 640
  KiB): any keypress advances a fixed eight-colour cycle, one
  step of which is black (`docs/development.md:477`).

## Scope

In scope:

- **SPICE auto-reconnect** in `--rdp`: while the SPICE session
  is down the RDP listener and any connected client stay up,
  the client sees the last frame darkened, and ryll retries
  with the GUI's backoff and ticket rules; it exits as today
  once retrying is forbidden or exhausted.
- **Takeover:** a new *authenticated* RDP connection to the same
  session disconnects the one being served (open question 4).
- **RDP client disconnect and reconnect**, which already works
  (phase 4's manual test), proved by the loopback test.
- **Ctrl+C**, which already shuts `--rdp` down cleanly
  (survey 4), kept working through this phase's restructure and
  proved with a client connected.
- An **`rdp-smoke` lane**: FreeRDP under Xvfb against the UEFI
  latency guest, in CI as an advisory workflow and locally as
  `make rdp-smoke`.
- Documentation of the user-visible behaviour and of the lane.

Out of scope:

- SPICE reconnect in `--web` and `--headless`, which leave a dead
  session up or exit today. The shared retry rules this phase
  moves (decision 3) make it cheaper later; recorded in *Future
  work*.
- A text overlay explaining the outage (decision 2).
- RDP auto-reconnect cookies (master plan *Future work*).
- Joining the smoke tier, and turning the `rdp` feature on in
  release builds (open question 8). Both wait for the lane's
  flake record (decision 9).
- The operator guide and parity column, which are phase 6's.
- mstsc: no Windows machine is available, as in phases 2 to 4.

## What the survey found

Checked on 2026-10-26 against the tree at `9e9e82b` and
`ironrdp-server` 0.13.0. The master plan's phase 5 section is
right about what is wanted and wrong about what exists: items
1, 2, 6 and 7 below correct it. This planning commit makes those
corrections at their source, in the master plan's phase 5
section and Execution row and in the `docs/plans/index.md` row,
so a later step need not.

### Ryll side

1. **No server mode reconnects SPICE.** `--rdp` ends when the
   SPICE connection task returns (`ryll/src/rdp.rs:359-366`): it
   stops the RDP server, which drops the client and closes the
   port. `--web` keeps serving HTTP over a dead session
   (`ryll/src/main.rs:931-990`); its "reaper"
   (`ryll/src/web/lifecycle.rs`) reaps WebRTC bridges, not SPICE
   sessions, so there is no `--web` reconnect pattern to follow.
   `--headless` exits.
2. **The GUI's retry rules are GUI-only.**
   `ryll/src/app/reconnect.rs` holds the backoff
   (`RECONNECT_BACKOFF_SECS = [1, 4, 16]`, `:13`), the ticket
   rules (`ReconnectPolicy::forbid_retry`, `:95`: single-use
   tickets and `ticket-valid-until`), the five-minute flap rule
   and the pure `ReconnectState::on_disconnect` transition. All
   are `pub(super)` inside `mod app`, which is compiled only
   with the `gui` feature (`ryll/src/main.rs:1-2`), and
   `ModalVariant` carries GUI wording.
3. **Each `run_connection` call consumes its channels.**
   `rdp.rs:283` moves `event_tx`, `session_state`, `input_rx`,
   `usb_rx`, `webdav_rx`, `resize_rx`, `virtual_disks`,
   `share_dir` and `playback` into the call. The RDP handles
   hold the other ends (`SessionHandles.input_tx`, `resize_tx`,
   and `mouse_mode`, a receiver of the first `SessionState`'s
   watch), so a second attempt needs per-attempt channels
   behind handles that stay fixed. The event bus, mirror, cursor
   relay, `MemoryClipboard` and `AudioRelay` are not consumed
   and can outlive an attempt.
4. **Ctrl+C already works.** `rdp.rs:270-274` bridges
   `SHUTDOWN_REQUESTED` to the cancel flag, and the `select!`
   at `:359` stops the RDP server first (`server.shutdown()`,
   `:373`, which sends `ServerEvent::Quit` and sets `stopping`
   so no new client is accepted), joins it, then cancels the
   renderer and the control socket. What is missing is proof
   with a client connected, and keeping it working once the
   server is restructured.
5. **The mirror does not reset between sessions.**
   `SurfaceMirror::apply_event` ignores `SessionInitialized`
   (`shakenfist-spice-renderer/src/surface_mirror.rs:480-493`).
   A new session's `SurfaceCreated` for the primary surface
   replaces it, but secondary surfaces of the old session would
   linger.

### IronRDP side

6. **`RdpServer::run` serves one connection at a time and does
   not watch the listener meanwhile.** Its loop `select!`s on the
   event channel and `listener.accept()`, then awaits
   `self.run_connection(stream)` inline (`server.rs:936-990`). A
   second client sits in the listen backlog (1024,
   `server.rs:48`) until the first leaves, so takeover cannot be
   built on `run`. `run_connection` is public (`server.rs:719`),
   takes any `AsyncRead + AsyncWrite` stream, and resets its own
   per-connection state, so a caller can own the listener.
   `ConnectionHandler::on_accept` and `on_disconnected` are
   called only by `run`, not by `run_connection`.
7. **The display handler's `updates()` is the first call after
   authentication in both security modes.** `client_loop` calls
   it first (`server.rs:1236`), after the acceptor has finished
   CredSSP (NLA) or the credential validator (TLS). Only the
   first `size()` comes before any credential;
   `request_initial_size()` and the encoder's `size()` follow
   authentication but precede `updates()`. (Corrected by step 1,
   which also found that `updates()` is called again after every
   reactivation, so only its first call marks authentication, and
   that input the acceptor queued reaches the input handler just
   before it.)
   This corrects `SessionProvider`'s doc comment
   (`shakenfist-spice-rdp/src/lib.rs:150-156`), which says IronRDP
   offers no hook after CredSSP: there is none for credentials,
   but `updates()` marks the moment.
8. **`ServerEvent::Quit` during a connection ends that
   connection only.** In `dispatch_server_events` it returns
   `RunState::Disconnect` (`server.rs:1102-1105`); the server
   stops only when `run`'s own `select!` receives it.
9. **Each `RdpServer` has its own event channel.** The clipboard
   and sound factories get its sender once (`set_sender`), so with
   one server per connection a message queued by a leaving
   connection's channel backend dies with that connection's
   server. That closes phase 4's stale-wave hazard (its *Future
   work*: a wave or clipboard message queued as one connection
   ends can drop a client waiting in the backlog), which
   takeover would otherwise have made common.

### CI side

10. **No CI lane runs a QEMU guest.** `web-smoke`
    (`tools/web-smoke.sh`, run by `build-linux` in `ci.yml:179-183`
    and the merge tier's Linux leg) starts `ryll --web` against a
    stub `.vv` pointing at `127.0.0.1:1` and checks only that it
    binds and exits on SIGTERM. The master plan's "modelled on
    `web-smoke` ... against the local QEMU test server" therefore
    asks for something `web-smoke` does not do. The nearest
    functional lane is `proxmox-functional.yml`: advisory,
    path-filtered, its own workflow, driven by a Python
    standard-library script whose oracles are ryll's log lines
    (`tools/proxmox-smoke.py`).
11. **No build target makes an `rdp` binary.** `make build` and
    `make release` leave the `rdp` feature off
    (`Makefile:196-222`); only `test` and `lint` turn it on. The
    lane needs a target that builds `-p ryll --features rdp`.
12. **The devcontainer has no QEMU, OVMF, Xvfb or FreeRDP**
    (`.devcontainer/Dockerfile:27-46`). Whether the self-hosted
    runners expose `/dev/kvm` to a container is unknown.

Nothing else in the master plan's section was found wrong.

## Decisions

1. **`--rdp` reconnects SPICE automatically** (Mikal's choice
   at planning time). After a session has been established and
   then lost, ryll retries with the GUI's rules: backoff of 1,
   4 and 16 seconds, no retry for a single-use ticket or past
   `ticket-valid-until`, and no retry budget for a session that
   lost its connection again within five minutes of exhausting
   one. When retrying is forbidden or exhausted, `--rdp` exits
   as it does today, with the last error. A first attempt that
   never reached a session also exits as today, matching the
   GUI's `ConnectFailed`, which does not retry either: it is
   usually a mistyped port.
2. **During an outage the client keeps its connection and sees
   the last frame at half brightness.** The display handler
   sends one whole-surface update of the darkened frame when the
   session goes down, and the next real frame replaces it. A
   frozen frame alone looks like a hang. A text overlay would
   need a font rasteriser in the RDP crate, which is not worth
   one message. Input and resize requests made during the outage
   are dropped and counted, not queued for a session that does
   not exist yet. The clipboard and audio relays outlive the
   attempt, so clipboard text set during the outage reaches the
   guest's agent once it reconnects.
3. **The retry rules move out of the GUI with no behaviour
   change.** The backoff, the ticket check and the flap rule
   become a module outside `mod app` that both the GUI and
   `--rdp` call. The GUI keeps its modal and its wording. The
   moved tests move unchanged, so a diff of the test bodies
   shows the GUI is untouched.
4. **Takeover is per session, at authentication.** A connection
   takes over when its display handler's `updates()` is first
   called (survey 7): that is after authentication in both
   security modes, and before the new client is sent any pixels.
   It takes over only the connection serving the *same*
   `SessionHandles` (by `Arc::ptr_eq`), so a provider serving
   several sessions does not let one user kick another. The old
   connection is sent `ServerEvent::Quit`, its held keys are
   released as on any disconnect, and from the moment of
   takeover it no longer forwards input or takes damage from the
   mirror. This answers open question 4 as the master plan
   recommended.
5. **One IronRDP server per connection, behind our own accept
   loop.** This is the decision a reviewer is most likely to
   question, since it rebuilds phase 2's server. `RdpServer::run`
   cannot accept a second client while one is connected
   (survey 6), so takeover is impossible on it. The alternative
   that keeps `run`, disconnecting the current client whenever a
   new TCP connection arrives, would let anyone who can reach
   the port kick the user off without a password. So
   `shakenfist-spice-rdp` binds the listener itself (keeping the
   port fallback, `SO_REUSEADDR` and readiness behaviour `serve`
   has now), builds an `RdpServer` for each accepted stream, and
   runs it with `run_connection` on the server thread's
   `LocalSet`. `ConnectionLog`'s work moves into that loop,
   because `run_connection` does not call the connection handler
   (survey 6). Per-connection servers also give each connection
   its own event channel, which closes phase 4's stale-wave
   hazard (survey 9).
6. **Bounded pre-authentication work.** Besides the connection
   being served, at most one unauthenticated connection runs its
   handshake at a time. Others wait in the listen backlog,
   unaccepted, as every client waits today. A connection that
   has not reached `updates()` within 30 seconds of accept is
   dropped. Without these, a stream of half-open connections
   would each hold a TLS handshake and an encoder.
7. **The smoke lane is an advisory workflow of its own.** A new
   `.github/workflows/rdp-smoke.yml` runs nightly against
   `develop`, which builds the flake record, and on
   `workflow_dispatch`. Its `pull_request` trigger is
   path-filtered to the workflow file alone, so it runs on the
   pull request that adds it and on later edits to the lane,
   but not on pull requests that touch the RDP code (Mikal's
   choice at the back brief, to keep `l` runners free). A new
   workflow cannot be dispatched or scheduled until it is on
   `develop`, so without that trigger its first run would come
   after merge. It is advisory for the reason `docs/ci.md` gives
   for `proxmox-functional.yml`: a required check that is
   path-filtered never reports on other pull requests. It runs
   on `[self-hosted, vm, debian-13-docker,
   l]` and inside an image of its own (`tools/rdp-smoke/Dockerfile`:
   Debian 13 with `qemu-system-x86`, `ovmf`, `freerdp3-x11`,
   `xvfb`, `xdotool`, `x11-apps` for `xwd`, and `python3`), so
   the build devcontainer stays lean. QEMU starts with
   `-accel kvm -accel tcg` so it works with or without
   `/dev/kvm`. `make rdp-smoke` runs the same image locally.
8. **The lane's oracle is the guest's colour, seen by
   FreeRDP.** The driver, `tools/rdp-smoke.py`, uses only the
   Python standard library, as `tools/proxmox-smoke.py` does,
   and reads the screen with `xwd`. It checks, in order:
   1. ryll authenticates FreeRDP (ryll's log line, with a fixed
      password from the driver);
   2. a frame arrives: the FreeRDP window's centre turns
      non-black within a deadline;
   3. input reaches the guest: an `xdotool key` changes the
      centre colour. This covers the whole path, client to ryll
      to SPICE to guest and back, so no ryll log line can make
      it pass;
   4. client reconnect: FreeRDP is killed and restarted, and
      check 3 passes again;
   5. takeover: a second FreeRDP connects, the first exits, and
      check 3 passes through the second;
   6. SPICE reconnect: QEMU is stopped and restarted on the same
      port; the client stays connected, the centre goes dark,
      and check 3 passes again within the backoff budget;
   7. Ctrl+C: SIGINT to ryll with the client connected; ryll
      exits 0 within 5 seconds and FreeRDP is disconnected.

   As in `proxmox-smoke.py`, each check prints a PASS or FAIL
   summary line, and a failure caused by the environment (QEMU
   not booting, Xvfb not starting) is reported as a harness
   failure, not a ryll defect.
9. **Joining the smoke tier waits for a flake record, and the
   criterion is set now:** fourteen consecutive green nightly
   runs, with no rerun needed. Moving the lane into `ci.yml`'s
   smoke tier, and open question 8's release-build question,
   are then proposed in a later pull request. Writing the
   criterion down now stops "once it has a flake record" from
   meaning whatever is convenient later.
10. **No new command-line flags.** The retry budget is the GUI's
    and is not configurable. A user who wants `--rdp` to exit on
    the first SPICE loss can use a single-use ticket. Add a flag
    if an operator asks.
11. **`SessionHandles` gains one field for decision 2**: a
    `watch::Receiver<bool>` saying whether the SPICE session is
    up. It is filled at every construction site, as phase 4's
    fields were. A library user with no reconnect passes a
    receiver that is always `true`.

## Step plan

Steps 1 and 2 touch different crates and could run in parallel,
but both run `make test` in the shared tree, so they run in
order. Step 3 needs step 1's server; step 4 needs steps 2 and
3; step 5 needs step 4. Each step is one commit, with the subject
given.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | none | Takeover: our own accept loop, one `RdpServer` per connection, per-session takeover at `updates()`, bounded pre-auth work, Ctrl+C kept; loopback tests for reconnect and takeover. Commit: "Let a new RDP client take over the session." See brief 1. |
| 2 | medium | sonnet | none | Move the GUI's retry rules out of `mod app` with no behaviour change. Commit: "Share the SPICE retry rules beyond the GUI." See brief 2. |
| 3 | high | opus | none | `SessionHandles` outage field, and the display handler darkening the frame while SPICE is down. Needs step 1. Commit: "Darken the RDP client's frame while SPICE is down." See brief 3. |
| 4 | high | opus | none | `--rdp` SPICE auto-reconnect in `ryll/src/rdp.rs`: per-attempt channels behind fixed handles, mirror reset, the shared retry rules. Needs steps 2 and 3. Commit: "Reconnect SPICE under a connected RDP client." See brief 4. |
| 5 | high | opus | none | The `rdp-smoke` lane: image, driver, Makefile targets, workflow, `docs/ci.md`. Needs step 4. Commit: "Add an RDP smoke lane with FreeRDP." See brief 5. |
| 6 | - | - | - | Management session: `make rdp-smoke` ten times in a row, the lane's first CI run, and a short visual check with Mikal watching. Recorded in *Results*. Needs step 5. No commit of its own. See *Step 6*. |
| 7 | medium | sonnet | none | Documentation and the master plan. Needs step 6's results. Commit: "Document RDP takeover, reconnect and smoke lane." See brief 7. |

Every brief also carries the implement-phase rules: leave the
work uncommitted and report which files belong to the step; use
the scratch directory the management session names; run mutants
on copies under `timeout`, counting a timeout as killed, and
never restore with `git checkout`; run `make test`, `make lint`,
`make check-windows` and `pre-commit run --all-files` (after
`git add -N` on new files); no plan, phase or decision citations
in code or in docs outside `docs/plans`; report in under 400
words.

### Brief 1: takeover

Files: `shakenfist-spice-rdp/src/server.rs` (`serve`,
`serve_preferring`, `run_server`, `build_server`,
`ConnectionLog`), `src/display.rs` (`updates`), `src/input.rs`,
`src/lib.rs` (`SessionSlot`, the `SessionProvider` doc comment),
`tests/loopback.rs`.

- Re-read `server.rs:900-990` (`run`), `:700-800`
  (`run_connection`) and `:1222-1240` (`client_loop`) in
  `ironrdp-server` 0.13.0, and confirm survey items 6 to 9 before
  building on them. In particular, check what `run` sets that
  `run_connection` relies on (`local_addr`, the static channel
  set) and how the factories' senders are installed.
- Bind the listener in `run_server` with the options `run` uses
  (`SO_REUSEADDR` on Unix, the same backlog), keeping `serve`'s
  port fallback and its "ready once listening" contract, so
  `RdpServerHandle::local_addr`, `shutdown`, `join`, `stopped`
  and `is_finished` behave as now.
- For each accepted stream, build an `RdpServer` with
  `build_server`'s handlers and run `run_connection` on the
  thread's `LocalSet`. `ConnectionLog`'s work (logging, client
  size reset, held-input release, emptying the slot in TLS mode,
  refusing while stopping) moves to the loop. Decide whether
  each connection gets its own `SessionSlot`; with a provider
  serving several sessions, two connections may serve different
  ones.
- Takeover per decision 4, bounds per decision 6. The old
  connection must stop forwarding input and taking damage at the
  moment of takeover, not when its `Quit` is processed: use a
  per-session generation or an equivalent that the display and
  input handlers check.
- `shutdown()` sends `Quit` to every live connection and stops
  the accept loop.
- Fix the `SessionProvider` doc comment per survey 7.
- Loopback tests, each with IronRDP's client as in
  `tests/loopback.rs`:
  - a client disconnects, a second connects and is served;
  - a second client with the right password takes over: the
    first sees its connection end, the second gets frames and its
    input arrives;
  - a second client with the wrong password does not disturb the
    first, which still gets frames;
  - a connection that never authenticates is dropped after the
    deadline (make the deadline injectable so the test is fast);
  - `shutdown()` with a client connected ends it and `join`
    returns within its timeout.
- Mutations, each killed by a named test: no takeover (the old
  connection keeps running); takeover on accept rather than at
  `updates()`; takeover ignoring which session it is; no
  pre-auth deadline; the old connection still forwarding input
  after takeover; `shutdown` not reaching live connections.

### Brief 2: shared retry rules

Files: `ryll/src/app/reconnect.rs`, a new module outside
`mod app` (for example `ryll/src/reconnect_policy.rs`, declared
in `main.rs` without a `gui` gate), and the GUI's call sites.

- Move `RECONNECT_BACKOFF_SECS`, `MAX_RECONNECT_ATTEMPTS`, the
  ticket rule (`ReconnectPolicy` and `forbid_retry`) and the
  five-minute flap rule into the new module, returning a
  GUI-free reason (single-use ticket, ticket expired at T)
  that the GUI maps to its `ModalVariant`. Leave
  `ReconnectState`, `ModalVariant` and the modal wording in
  `mod app`.
- The moved tests move with their bodies unchanged apart from
  paths; say so in the report and list any that had to change.
- `make build` must still build the GUI, and `make lint` and
  `make test` pass with the `rdp` feature and without `gui`
  (`cargo build --no-default-features -p ryll` through the
  devcontainer, as `docs/development.md` describes for the slim
  build).
- Mutations: the backoff values; the single-use rule; the expiry
  rule. Each must be killed by a test in the new module.

### Brief 3: outage frame

Files: `shakenfist-spice-rdp/src/lib.rs` (`SessionHandles`),
`src/display.rs`, every place a `SessionHandles` is built
(`grep -rn 'SessionHandles {' --include=*.rs`), including tests and
`ryll/src/rdp.rs` (pass an always-`true` receiver there for now;
step 4 wires it).

- Add the field per decision 11, documented in the struct's
  style.
- When it turns `false`, the display handler sends one
  whole-surface bitmap of the current primary surface at half
  brightness, without changing the mirror. When it turns `true`,
  the next update is the whole surface, as after connect, so no
  darkened pixels survive. Handle a resize arriving with the new
  session, which phase 2's guest-driven resize path already
  turns into `DisplayUpdate::Resize`.
- Unit tests: darkening halves each channel and leaves alpha
  alone; a `false` produces exactly one darkened update; `true`
  produces a whole-surface update from the mirror; a client
  connecting during an outage is shown the darkened frame.
- Mutations: no darkening; darkening written into the mirror;
  no whole-surface update on recovery.

### Brief 4: SPICE auto-reconnect

Files: `ryll/src/rdp.rs`; the renderer's `SurfaceMirror` if it
needs a reset method; nothing in `ryll/src/app/`.

- Read `docs/session-lifecycle.md` (*Reconnection*, *Auto-
  reconnect with backoff*, *Connection attempts that fail before
  a session exists*) and step 2's module first.
- Restructure `run_rdp` so a supervisor task owns the SPICE
  attempts. Each attempt gets fresh `input`, `resize`, `usb`,
  `webdav` channels and a fresh `SessionState`; long-lived
  relays forward the RDP handles' `input_tx` and `resize_tx`
  into the current attempt, dropping and counting events while
  none is up, and forward each attempt's mouse-mode and agent
  watches into the receivers the RDP handles and the control
  socket hold. The event bus keeps one `mpsc` receiver; each
  attempt gets a clone of its sender. `virtual_disks` and
  `share_dir` are cloned per attempt. The clipboard, audio relay,
  cursor relay and mirror outlive attempts.
- Before an attempt's session starts, reset the mirror so no
  surface of the old session survives (survey 5), and publish
  "down" then "up" on decision 11's watch at the right moments:
  down when the session is lost, up when the new session's
  first primary surface arrives.
- Retry per decision 1 using step 2's rules, logging each
  attempt and its outcome. Exit as today when retrying is
  forbidden or exhausted, or when the first attempt fails. The
  control socket's `status` verb reports SPICE as down during
  an outage.
- Ctrl+C during an outage or a backoff wait exits promptly.
- Make the supervisor testable without a SPICE server: inject
  the function that runs one attempt, and unit-test the
  sequence (lost, backoff, success; lost three times then exit;
  single-use ticket exits at once; first-attempt failure exits;
  Ctrl+C during a wait).
- Mutations: no mirror reset; input forwarded to a dead
  attempt instead of dropped; the watch never returning to
  `true`; retrying a single-use ticket; retrying after a
  first-attempt failure.

### Brief 5: the smoke lane

Files: new `tools/rdp-smoke/Dockerfile`, `tools/rdp-smoke.py`,
`.github/workflows/rdp-smoke.yml`; `Makefile`; `docs/ci.md`.

- Read `tools/proxmox-smoke.py`, `tools/web-smoke.sh`,
  `.github/workflows/proxmox-functional.yml` and `docs/ci.md`'s
  *The Proxmox lane* and *Build network isolation* first, and
  follow their patterns: summary lines, harness-versus-ryll
  failures, oracles named next to the source line they must
  stay in step with, the workflow's comments explaining why it
  is advisory.
- Makefile: a target that builds `ryll` with the `rdp` feature
  through the devcontainer offline, as `release` does; a target
  that builds the smoke image; and `rdp-smoke`, which runs the
  driver in the smoke image with the built binary, the guest
  image and the OVMF files mounted. Add them to `.PHONY` and the
  help text.
- Driver checks per decision 8, in that order, with QEMU per
  decision 7 (the guest from `make test-qemu`'s command line,
  SPICE on a port the driver picks, its own PID and QMP paths),
  ryll `--rdp --rdp-port 0 --rdp-password <fixed> --direct`,
  and `xfreerdp3 /cert:ignore` under Xvfb. The driver must
  clean up every process it started, on success and on failure,
  and must never leave QEMU running.
- Workflow per decision 7: nightly at an hour the other
  scheduled workflows do not use (check every `cron:` under
  `.github/workflows/`), `workflow_dispatch`, and `pull_request`
  filtered to `.github/workflows/rdp-smoke.yml` only. Upload the driver's logs.
  Do not add the job to `ci.yml` or any gate.
- `docs/ci.md`: a section *The RDP smoke lane* beside *The
  Proxmox lane*, covering what it checks, why it is advisory,
  decision 9's criterion for joining the smoke tier, and a row
  in *Workflow inventory*.
- Prove each check can fail: run the lane against a ryll built
  with a deliberate break (no input forwarding; no takeover; no
  SPICE retry) and confirm the right check fails. Record the
  commands and outcomes; mutated binaries are built from a copy
  of the tree.

### Brief 7: documentation and the master plan

Files: `docs/session-lifecycle.md`, `docs/configuration.md`'s
RDP section, `shakenfist-spice-rdp/README.md` if its usage
example builds `SessionHandles`, `docs/plans/PLAN-rdp-frontend.md`,
this plan.

- `docs/session-lifecycle.md`: how `--rdp` behaves when SPICE is
  lost (what the client sees, the retry rules shared with the
  GUI, when it exits), takeover, and Ctrl+C. Say that `--web`
  and `--headless` do not reconnect.
- `docs/configuration.md`: one paragraph on takeover and
  reconnect where the RDP flags are described.
- Master plan: open question 4 gets an *Answer (phase 5)* citing
  *Results*; the phase 5 section notes what shipped.
- This plan: *Results* and *Execution notes* from the
  management session's records; tick the definition of done
  only for items the management session has checked.

## Step 6: the management session's checks

- `make rdp-smoke` ten times in a row on the Kasm host, each
  run under `timeout`. Record passes, failures and run times in
  *Results*. Any failure is diagnosed before the pull request
  opens.
- The lane's first CI run on the pull request: link, result,
  run time, whether the runner had `/dev/kvm`.
- With Mikal watching, FreeRDP against the desktop guest
  (`make test-qemu-desktop` with its own PID, QMP and OVMF
  variables paths, as phase 4 did): stop and restart the guest's
  QEMU and confirm the darkened frame and the recovery; connect a
  second FreeRDP and confirm the takeover; reconnect. Record what
  he saw.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| `run_connection` relies on state `run` sets up, so a server that never calls `run` misbehaves subtly. | Brief 1 re-reads both functions before building on them; the loopback tests cover connect, reconnect, takeover and shutdown; the management session reviews the diff against `server.rs:900-990`. |
| Two connections to one mirror split its damage, and the new client misses a region. | Takeover stops the old connection taking damage at once (decision 4), and a new connection starts with the whole surface; brief 1's mutation of "old connection still active" must be killed. |
| A relay misses a watch update after reconnect, leaving the RDP side in the wrong mouse mode. | Brief 4's relays forward every value; the smoke lane's check 6 exercises input after a SPICE reconnect; the management session checks a mouse click after reconnect in step 6. |
| Moving the retry rules changes the GUI's behaviour. | Moved tests keep their bodies (brief 2), and the management session diffs them. |
| The lane is flaky (Xvfb start-up, FreeRDP timing, TCG speed). | Generous deadlines, harness failures classified apart from ryll failures, ten local runs before the PR, and decision 9's criterion before it gates anything. |
| The runners have no `/dev/kvm` and TCG makes the guest too slow. | `-accel kvm -accel tcg`; the guest is a 640 KiB UEFI app. Step 6 records which the runner used and the run time. |
| Debian 13's FreeRDP differs from the host's 3.15.0. | The lane prints its FreeRDP version; step 6 compares. |
| A half-open connection flood exhausts the server. | Decision 6's bounds and the pre-auth deadline, with a loopback test. |

## Definition of done

- [x] `grep -n 'server.run()' shakenfist-spice-rdp/src/server.rs`
      finds nothing, and `grep -n 'run_connection'` there finds
      the per-connection call.
- [x] The loopback tests for reconnect, takeover, a
      wrong-password second client, the pre-auth deadline and
      shutdown with a client connected pass under `make test`.
- [x] `grep -rn 'RECONNECT_BACKOFF_SECS' ryll/src` finds the
      constant outside `ryll/src/app/`, and `cargo build
      --no-default-features -p ryll` (through the devcontainer)
      compiles that module.
- [x] `ryll/src/rdp.rs`'s supervisor has unit tests for each
      sequence in brief 4, and they pass.
- [x] Every mutation listed in briefs 1 to 4 is killed, and
      every deliberate break in brief 5 fails the check it
      should; the scripts' paths and counts are in *Execution
      notes*.
- [x] `make rdp-smoke` passed ten times in a row on the Kasm
      host, recorded in *Results*.
- [ ] `rdp-smoke.yml` ran green on the pull request, with its
      link in *Results*, and is in no gate's `needs:` list
      (`grep -n 'rdp' .github/workflows/ci.yml` finds no job).
- [x] `docs/ci.md` lists `rdp-smoke.yml` in *Workflow
      inventory* and states decision 9's criterion.
- [x] Open question 4 has an answer citing *Results*.
- [x] `make build`, `make test`, `make lint`, `make
      check-windows`, `make package-check` and `pre-commit run
      --all-files` pass.
- [x] `git grep -n -i 'phase 5\|decision [0-9]\|survey item' -- shakenfist-spice-rdp shakenfist-spice-renderer/src ryll/src tools .github docs ':!docs/plans'`
      finds nothing.

## Results

**Local smoke runs, 2026-10-26 (AEDT), Kasm host with `/dev/kvm`.**
`make rdp-smoke` ran ten times in a row, each under `timeout
1200`, at HEAD 9d178c0 with the image and build warm. All ten
passed 7/7, in 32 or 33 s each; the driver itself took 18.0 to
18.4 s. FreeRDP 3.15.0, QEMU 10.0.13 (Debian 13). A typical
run's notes: takeover 4.1 s, the old client exiting as the new
one authenticated; spice-reconnect 4.2 s, the centre darkening
from #960000 to #490000 0.6 s after QEMU stopped and the new
guest's frame arriving 1.4 s after it restarted, after one SPICE
attempt; Ctrl+C exiting 0 in 0.11 s. Nothing was left running
after the ten runs.

**First CI run.** Pending: recorded once the pull request's
`rdp-smoke.yml` run finishes.

**Visual check with Mikal, 2026-10-27 (AEDT).** FreeRDP 3 on
Mikal's desktop against `ryll --rdp` (port 3391) against the
XFCE desktop guest (`make test-qemu-desktop` with SPICE on 5931
and private PID, QMP and OVMF variables paths). He confirmed:
the desktop and input worked in client A; when the guest's QEMU
was stopped the frame went to half brightness and A stayed
connected; once QEMU was restarted, ryll's fourth attempt (after
the 1, 4 and 16 s waits, two refused) reconnected and the new
guest appeared at full brightness with input working; ryll
logged 221 input events dropped during the outage; a second
client B took over, A's window closed and B worked; after B was
closed cleanly a third client C connected and worked; SIGINT to
ryll with C connected stopped ryll in 0.10 s and closed C.

## Execution notes

- **Back brief, 2026-10-26 (AEDT).** Mikal confirmed decisions 5
  and 2, asked to be pinged for step 6's visual check, and gave
  permission to push and open the pull request once the
  definition of done passes. He chose a nightly-only lane over
  running it on every pull request that touches the RDP code;
  decision 7 and brief 5 are amended to match, keeping a
  `pull_request` trigger on the workflow file alone so the lane
  can run before it merges.
- **Step 1** (Opus, high effort) corrected survey item 7 at its
  source (above). It gave each connection its own `SessionSlot`,
  so a client still authenticating cannot overwrite or clear the
  served client's session, and made takeover clear the old
  connection's slot, which its display and input handlers check
  on every use, rather than add a generation counter. The
  pre-auth deadline is a new public `RdpConfig::handshake_timeout`
  field with an exported `DEFAULT_HANDSHAKE_TIMEOUT` of 30 s. It
  also corrected `docs/configuration.md`'s "a second client waits"
  sentence, which takeover made false. Six mutants, all killed:
  `/tmp/claude-1000/-srv-kasm-profiles-mikal-vscode-src-shakenfist-ryll/a49e0447-bf0e-486b-a6d9-a7d93594be3f/scratchpad/p05/step1/mutate.py`
  (session scratchpad, not kept).
- **Step 2** (Sonnet, medium effort) put the rules in
  `ryll/src/reconnect_policy.rs`: `next_step()` returns a retry
  delay or a reason to give up, and the GUI's
  `ReconnectState::on_disconnect` now calls it. The management
  session checked the state machine case by case against the old
  code. Eight mutants, all killed (`.../p05/step2/mutate.sh`).
- **Step 3** (Opus, high effort) named the field
  `SessionHandles.spice_up`. A closed watch keeps its last value,
  so a caller with no reconnect drops the sender of a `true`
  watch. Recovery resets the stream on any change that ends at
  `true`, so an outage too short for a poll to see still resends
  the whole surface. For step 4 it noted that a surface appearing
  in the mirror while the watch is `false` is sent darkened, so
  the mirror reset belongs just before the new session starts.
  Eight mutants, all killed (`.../p05/step3/mutate.py`).
- **Step 4** (Opus, high effort) gave each attempt its own
  channels and connected them to the fixed ones the RDP server
  and control socket hold through a `SessionLink`. Input and
  resize requests are dropped and counted while no session
  exists, rather than queued. The first attempt is routed to from
  the start, as before, so a client's early desktop-size request
  still reaches the guest. It went outside the brief to fix
  `run_connection` in the renderer: an early return, or dropping
  its future, left the spawned channel tasks running. The GUI
  never noticed, because it drops each attempt's runtime; `--rdp`
  runs every attempt on one runtime. A drop guard now aborts
  them, and has its own test. The management session's review
  made running out of retries always exit non-zero, since
  `run_connection` usually returns `Ok` and the run would
  otherwise exit 0 after failing to reconnect. Twelve mutants,
  all killed (`.../p05/step4/mutate.py`). A live check against
  QEMU on port 5921 saw a loss, a refused retry, a reconnect with
  a new surface, and Ctrl+C exiting in 0.03 s. It also found that
  `make test-qemu` takes its port and paths as make arguments,
  not environment variables, which steps 5 and 6 rely on.
- **Step 5** (Opus, high effort) scheduled the lane at 14:40 UTC.
  It took OVMF from the smoke image rather than mounting the
  host's, since runners need not have it. It added two oracles
  beyond decision 8: every colour FreeRDP shows must match the
  guest's own QMP `screendump`, which also tells a guest that
  never booted (a harness failure) from a ryll defect; and the
  lane fails if ryll prints the password anywhere but its launch
  line. Both kept. There is no fake ryll to run the driver
  against, as `tools/test-proxmox-smoke.sh` has, because there is
  no cheap stand-in for an X server and an RDP client; instead
  `tools/test-rdp-smoke.py` unit-tests its pure parts from
  pre-commit. `release-rdp` shares `target/release` with
  `release` and copies its binary out; every local consumer of
  `target/release/ryll` rebuilds through `release` first, so the
  sharing is harmless. Deliberate breaks, each built from a copy
  of the tree: dropping every input event failed `input`;
  removing the old connection's `lose()` failed `takeover`; a
  supervisor that gives up after any session failed
  `spice-reconnect`. Each time every earlier check passed
  (`.../p05/step5/breaks.py`). Local runs took 32 to 38 s warm,
  and one without `/dev/kvm` also passed.
- **Step 7** (Sonnet, medium effort) ran alongside step 6
  rather than after it: the user-facing docs did not depend on
  the run record, and the management session wrote *Results* and
  ticked the definition of done itself. It found and corrected a
  sentence in `docs/session-lifecycle.md` saying ryll never
  auto-reconnects, already wrong for the GUI's backoff.
- **Step 6** was run by the management session; see *Results*.
  The visual check needed the desktop guest image downloaded
  again, as phase 4's worktree had gone.

## Future work

- Reporting a red nightly of the RDP smoke lane, as `fuzz.yml`
  files an issue for its failures; today the run history is the
  only record.
- Sending an RDP client's resize request made during an outage
  again once SPICE is back; it is dropped today.

- SPICE reconnect for `--web` and `--headless`, on the retry
  rules this phase shares.
- A text overlay saying why the frame is dark, if users find the
  darkened frame unclear.
- Promoting the lane into the smoke tier, and open question 8,
  once decision 9's criterion is met.

## Back brief

Before executing any step of this plan, back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with it. Gates that need Mikal's answer
before the steps that depend on them:

- Decision 5 (one IronRDP server per connection, our own accept
  loop) and decision 2 (a darkened frame, no overlay); confirm
  before step 1.
- Decision 7: a new advisory workflow that books an `l` runner
  on each pull request touching the RDP code or the renderer,
  and nightly.
- Mikal available to watch step 6's visual check.
- Whether this session may push and open the pull request once
  the definition of done passes.
