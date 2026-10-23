# RDP frontend phase 2: RDP skeleton

Master plan: [PLAN-rdp-frontend.md](/components/ryll/plans/PLAN-rdp-frontend/).

**Planning effort:** xhigh (the master plan asks for high). This
is the phase that turns on RDP security negotiation, IronRDP's
concurrency model and the back-pressure between a slow RDP
client and the SPICE session, and it is where the plan learns
most. No review effort is set by the master plan; review at
high effort, because step 7 changes the `ryll` binary's CLI and
step 1 adds a crate that will be published.

## Prompt

Read the master plan's *Prompt* first; everything there
applies. For this phase in particular:

- The RDP side is IronRDP **0.13.0** from crates.io
  (`ironrdp-server` 0.13.0, `ironrdp-acceptor` 0.10.0,
  `ironrdp-pdu` 0.9.0, released 2026-07-10). Its source is
  `github.com/Devolutions/IronRDP` at tag
  `ironrdp-server-v0.13.0`, and every file and line cited below
  for IronRDP is relative to that tree. Check it out read-only,
  in a directory of its own: it is downloaded code, so read it,
  never run its build scripts or examples from inside it.
  Re-read the cited file before building on a claim.
- Read `ryll/src/main.rs` `run_web` (`:514-990`) end to end: it
  is the shape `run_rdp` copies, with a different transport.
- The renderer pieces this phase drives are the ones phase 1
  landed: `DisplaySurface::take_damage` (`display/surface.rs:540`),
  `SurfaceMirror` (`surface_mirror.rs`), `EventBus`
  (`event_bus.rs`), `PointerTranslator` (`pointer.rs`),
  `PlaybackOutput::Sink` (`playback_sink.rs`) and `run_connection`
  (`session.rs:211`).
- Builds run in Docker through the Makefile, never with a host
  toolchain. The `ryll-dev` image is absent on this host at
  planning time, so the first `make` target rebuilds it.

## Scope

In scope, the master plan's phase 2 as surveyed:

- A new publishable library crate, `shakenfist-spice-rdp`,
  over `ironrdp-server` 0.13.0: a display adapter fed by the
  mirror's damage, an input adapter over the renderer's input
  sender and `PointerTranslator`, credential checking, TLS
  (self-signed or supplied), and a runner that hides IronRDP's
  `!Send` server behind a thread of its own.
- One renderer addition: an `InputEvent` that sets the guest's
  lock keys, so RDP's `Synchronize` reaches the inputs channel.
- `ryll --rdp` behind a default-off `rdp` cargo feature, with
  `--rdp-host`, `--rdp-port`, `--rdp-user`, `--rdp-password`,
  `--rdp-security` and `--rdp-tls-cert` / `--rdp-tls-key`,
  printing the address, credentials and certificate fingerprint
  at launch the way `--web` prints its URL. A control socket is
  allowed, as in `--web`.
- Keyboard with the extended flag, lock keys, mouse buttons and
  the vertical wheel in both mouse modes; relative mouse motion
  where the client sends it.
- `DisplayUpdate::Resize` when the guest changes its own
  resolution, so the stream does not stall (moved here from
  phase 3; see *What the survey found*, item 8).
- A loopback integration test that drives the server with
  IronRDP's own client connector, so the handshake is proven in
  CI without an RDP client.
- A manual test with FreeRDP (and mstsc where available) against
  the desktop guest, with the negotiated security and codec,
  bytes per second and CPU for three workloads recorded in
  *Results*, and open questions 1, 2, 3 and 7 answered in the
  master plan.
- The documentation a new CLI surface needs now: the flags in
  `docs/configuration.md`, the feature in `docs/development.md`
  and `AGENTS.md`, and a line in the parity page's mode table.
  The operator guide, the parity column, `ARCHITECTURE.md` and
  the README link are phase 6.

Out of scope:

- Cursor shapes and client-driven resize (phase 3). This phase
  sends no pointer updates at all; the client draws its own
  arrow.
- Clipboard and audio (phase 4). Guest audio is discarded in
  `--rdp` for now rather than played on the server host.
- Takeover by a new connection, SPICE reconnect handling and
  the `rdp-smoke` lane (phase 5). With IronRDP's own accept
  loop a second client waits in the TCP backlog until the first
  disconnects; decision 4 says why that is accepted for now.
- H.264 through EGFX, multi-monitor, Unicode key events
  (dropped with a `warn_once!`, open question 5).
- Making `--web` and `--headless` mutually exclusive (#601,
  found by the survey; `--rdp` declares its own conflicts).
- Fixing IronRDP's loss of the middle button (survey item 4);
  this phase records it and waits for the next release.

## What the survey found

Surveyed on 2026-10-21 against develop `27f2a2d` and IronRDP
0.13.0 (`ironrdp-server-v0.13.0`, commit `11a0810c`) and
`master` `66bfd60e` (2026-10-20, 502 commits past the tag). The
master plan's phase 2 section is right about the shape of the
work. These items correct or sharpen it; the false claims are
fixed at their source in the master plan's *Situation*, *Open
questions* and *Execution* sections and in the `index.md` row
in this planning commit, so later steps need not redo that.

1. **IronRDP's development branch is `master`, not `main`.**
   The repository's default branch is `master`; `62246b3`,
   which the master plan read, is in its history. The claims
   the plan marks *(main)* are claims about that branch.
2. **0.13.0 cannot offer TLS and NLA together.** The builder's
   security choice is one of `with_no_security`, `with_tls` or
   `with_hybrid` (`crates/ironrdp-server/src/builder.rs:72-98`),
   and `RdpServerSecurity::flag()` advertises `SSL` for the
   first and `HYBRID | HYBRID_EX`, without `SSL`, for the second
   (`server.rs:293-305`). The acceptor intersects that with what
   the client requested and refuses an SSL-only client of a
   hybrid server with `HYBRID_REQUIRED_BY_SERVER`
   (`crates/ironrdp-acceptor/src/connection.rs:414-433`). A TLS
   server accepts a client that asked for `SSL | HYBRID |
   HYBRID_EX`, which is what mstsc sends. The master plan's
   recommendation to "offer Hybrid and TLS together so the
   client chooses" is not available; decision 2 picks TLS.
3. **0.13.0 already has a `CredentialValidator`, and in TLS
   mode it is the check, not an authorisation layer on top.**
   `server.rs:178-194` defines it; `client_accepted`
   (`server.rs:1350-1369`) runs it on the Client Info PDU's
   credentials and answers a rejection with
   `ServerSetErrorInfo` before closing. It is not consulted for
   CredSSP connections, where NTLM uses the pre-loaded
   `Credentials`. Without a validator the acceptor compares the
   Client Info credentials to the pre-loaded pair with plain
   struct equality, *including the `domain` field*
   (`connection.rs:693-713`); mstsc fills a domain and FreeRDP
   may not, so that path is wrong for us and decision 2 uses a
   validator that ignores the domain.
4. **0.13.0 loses the middle button and the horizontal wheel on
   the standard mouse PDU.** `From<MousePdu> for MouseEvent`
   (`crates/ironrdp-server/src/handler.rs`) tests
   `LEFT_BUTTON`, `RIGHT_BUTTON` and `VERTICAL_WHEEL` and turns
   everything else into `Move`; `PointerFlags` has
   `MIDDLE_BUTTON_OR_WHEEL` and `HORIZONTAL_WHEEL`
   (`crates/ironrdp-pdu/src/input/mouse.rs:107-114`) and
   neither is matched. `master` fixes both and reshapes the
   enum into `Move`, `Button { x, y, button, pressed }`,
   `ButtonRel`, scroll variants and `RelMove`. The middle button
   does arrive through the relative-mouse PDU (`MouseRelPdu`)
   and FreeRDP's `ainput` channel, both of which 0.13.0 maps
   fully. The master plan's claim that 0.13.0's mouse events are
   "flat per-button variants plus move and vertical wheel" is
   incomplete: 0.13.0 has `MiddlePressed`, `Button4Pressed`,
   `Button5Pressed`, `Scroll { x, y }` and `RelMove { x, y }`
   too (`handler.rs:17-33`); the gap is in the conversion from
   the standard PDU, not in the enum.
5. **`RdpServer` is `!Send` in 0.13.0 as well.**
   `SoundServerFactory` (`sound.rs:5`) and
   `CliprdrServerFactory` (`clipboard.rs:5`) carry no `Send`
   bound and `RdpServer` holds them boxed (`server.rs:434-456`),
   and `run_connection` clones an `Rc` internally
   (`server.rs:1300`). IronRDP's own README and its end-to-end
   test run the server inside a `tokio::task::LocalSet`
   (`crates/ironrdp-server/README.md`,
   `crates/ironrdp-testsuite-extra/tests/e2e.rs:280-290`). The
   master plan's "check 0.13.0" is answered: it needs a
   `LocalSet` or its own thread. Decision 3 gives it a thread.
6. **0.13.0 advertises relative mouse input.** The server's
   input capability set includes `MOUSE_RELATIVE` and `MOUSEX`
   (`crates/ironrdp-server/src/capabilities.rs`,
   `input_capabilities`), so a client that supports it (FreeRDP
   3's relative-mouse option; mstsc is to be tested) can send
   `MouseRelPdu`, which arrives as `MouseEvent::RelMove`. Open
   question 3's "if the pinned IronRDP offers relative-mouse
   input, try it" is therefore a phase 2 test, not a wish.
7. **IronRDP's framebuffer is seeded only by a full-desktop
   bitmap, and its diff assumes four bytes per pixel.**
   `bitmap_update_framebuffer`
   (`crates/ironrdp-server/src/encoder/mod.rs:318-331`) creates
   the framebuffer when a `BitmapUpdate` is at `(0, 0)` with the
   desktop's size and otherwise only patches an existing one;
   `bitmap_diffs` (`:240-275`) diffs against it with
   `find_different_rects_sub::<4>` and, with no framebuffer,
   treats the whole bitmap as changed. A bitmap that exceeds the
   desktop size is dropped with a warning (`:379-386`). So the
   first update after connect (and after a resize) must be the
   whole surface, and every later rectangle must lie inside the
   current desktop size. `PixelFormat::RgbA32` is honoured by
   the planar encoder (`encoder/bitmap.rs:101-110`) and by
   RemoteFX, which reads channels through the format
   (`encoder/rfx.rs:185-201`); its channel order is `[0, 1, 2,
   3]` = R, G, B, A (`crates/ironrdp-graphics/src/image_processing.rs:168`),
   the same bytes `DisplaySurface` keeps (`surface.rs:37`). No
   swizzle is needed, as the master plan hoped.
8. **A guest-driven resolution change needs `Resize` in this
   phase, not phase 3.** Item 7 means that once the primary
   surface changes size the display adapter must emit
   `DisplayUpdate::Resize` (which deactivates and reactivates
   the connection, `server.rs:1047` and `accept_finalize`
   `:1736-1750`) before any bitmap, or every later update is
   dropped. The master plan put "the guest's answer, a
   `SurfaceCreated` of a new size, becomes `DisplayUpdate::Resize`"
   in phase 3 beside the client-driven resize; the guest-driven
   half moves here, and phase 3 keeps `request_initial_size`
   and `request_layout`. The master plan's phase 3 text is
   adjusted.
9. **`next_update` must be cancel-safe, and the server paces
   it.** `display.rs:244-248` requires it ("used in a
   `tokio::select!`"), and `run_connection`'s display task calls
   `next_update`, encodes and writes, then calls it again
   (`server.rs:1266-1296`). That serial loop *is* the client
   pacing the master plan asked for: while the client is behind,
   damage accumulates in the mirror and the next take reads the
   pixels as they are then, so superseded pixels are never sent.
   qemu-rdp needed a queue because it received bitmaps as
   messages; we read a framebuffer, so decision 5 needs no queue
   of pixels at all.
10. **`RdpServer::run` serves one connection at a time and
    never takes over.** Its accept loop awaits
    `run_connection(stream)` inline (`server.rs:904-996`), so a
    second TCP connection waits in the listen backlog (1024)
    until the first ends; `run_connection` is public and takes
    any `AsyncRead + AsyncWrite` stream, which is how phase 5
    will write its own accept loop for takeover and byte
    counting. `ServerEvent::Quit` ends `run`, and
    `ServerEvent::GetLocalAddr` reports the bound address
    (`server.rs:476-487`), which is how an ephemeral port is
    learned.
11. **The session seam is narrower than the master plan
    describes.** The display and input handlers are fixed when
    the server is built (`builder.rs:100-151`), and
    `run_connection_with` asks the display for its size *before*
    `accept_begin` (`server.rs:802-806`), so the desktop size is
    needed before any credential is seen. A "trait that turns an
    accepted, authenticated connection into the session to
    serve" can only choose *between sessions of one size* in
    0.13.0, or resize after choosing. Decision 6 defines the
    seam as the credential check plus a handle slot the adapters
    read, which is what a one-session process needs and what a
    per-session Kerbside worker would need too.
12. **Dependencies the crate brings, from the manifests.**
    `ironrdp-server` 0.13.0 depends on `tokio-rustls 0.26`
    (ryll's version), `async-trait`, `bytes`, and on
    `ironrdp-tokio` with its `reqwest` feature, which pulls
    `reqwest 0.12` beside ryll's `0.13` (`Cargo.lock`);
    `ironrdp-connector` pulls `sspi 0.21` with `scard`
    (`winscard`, `cryptoki`), `picky =7.0.0-rc.25`, `rand 0.9`
    (already in the lockfile) and an `rsa` release candidate
    beside ryll's `rsa 0.9`. `deny.toml` has
    `multiple-versions = "warn"`, so the duplicates warn rather
    than fail; licences are MIT or Apache-2.0 throughout the
    IronRDP tree, and step 1 runs cargo-deny to be sure. The
    server's default features are `rayon`, `qoi` and `qoiz`
    (`crates/ironrdp-server/Cargo.toml`); QOI and QOIZ are
    IronRDP-private codecs no stock client negotiates, and
    `qoiz` adds `zstd-safe 7` beside ryll's `8`, so decision 1
    turns them off. `sspi`'s default feature is `aws-lc-rs`,
    which the tree already carries through the protocol crate
    (#593), so no new C build is expected; step 1 confirms with
    `cargo tree`.
13. **MSRV.** 0.13.0 needs Rust 1.89 and `master` 1.94. The
    devcontainer installs rustup `stable` at image build time
    (`.devcontainer/Dockerfile:52-53`), which is 1.99.1 today, so
    neither is a toolchain problem. The workspace declares
    `rust-version = "1.88"` (`Cargo.toml:21`) as a floor for
    consumers; decision 1 raises it to 1.89, since a binary
    whose `rdp` feature pulls a 1.89 crate cannot honestly claim
    1.88.
14. **Kerbside PR #581 merged on 2026-10-19.** The master plan
    says it was still open on 2026-10-20. Corrected.
15. **The renderer has no way to set the guest's lock keys.**
    `InputEvent` (`channels/mod.rs:499-538`) has key, mouse and
    paste variants only; the inputs channel sends one
    `KEY_MODIFIERS` (Num Lock on) at open and never again
    (`channels/inputs.rs:296-309`). RDP's `Synchronize` carries
    the client's lock state, with the same bit layout as SPICE's
    `keyboard_modifiers` for Scroll, Num and Caps Lock (MS-RDPBCGR
    TS_SYNC_EVENT `0x1`, `0x2`, `0x4`; protocol
    `constants.rs:545-547`). Step 2 adds the variant.
16. **`--web` and `--headless` are not mutually exclusive.**
    `config.rs:206-213` documents it; nothing enforces it and
    `main.rs:300-310` lets `--web` win. Filed as #601; `--rdp`
    declares `conflicts_with_all` itself (step 7).
17. **Clients on this host.** FreeRDP 3.15.0 is packaged for
    Debian 13 (`freerdp3-x11`, `freerdp3-sdl`) and not installed;
    the Kasm workspace images include `kasmweb/remmina`, a
    FreeRDP-based client in a browser. mstsc needs a Windows
    machine, which is a back-brief question. The desktop guest
    (`make test-qemu-desktop`) is the target; the local andris
    service holds SPICE port 5900, so phase 1 ran the guest on
    5910 with `tools/start-desktop-qemu.sh --spice-port 5910`.
18. **The routing-token claim is unverified.** `ironrdp-pdu`'s
    `nego.rs` parses both `routing_token` and `Cookie:` forms
    (`:169-215`) and the acceptor keeps only the negotiated
    protocol; whether an opaque `loadbalanceinfo` fails to parse
    was not tested, and it does not matter to a one-session
    process. Left as the master plan has it, marked unverified
    there.

## Decisions

1. **IronRDP 0.13.0 from crates.io, slimmed, and the workspace
   MSRV goes to 1.89.** The crate depends on `ironrdp-server =
   { version = "0.13", default-features = false, features =
   ["rayon", "helper"] }`: `rayon` parallelises RemoteFX
   encoding, `helper` gives `TlsIdentityCtx` for PEM files, and
   dropping `qoi`/`qoiz` keeps two private codecs and a second
   `zstd-safe` out of the tree (survey item 12). Nothing from
   `master` is needed for the MVP; the one thing it fixes that
   this phase wants, the middle button (survey item 4), is
   recorded in *Future work* and tracked by an issue step 4
   files, and is re-examined at the next IronRDP release. A git
   pin was rejected: `deny.toml` denies unknown git sources, a
   crate with a git dependency cannot be published, and every
   IronRDP minor has broken the server API, so churn is cheaper
   to absorb one release at a time. All IronRDP-facing code
   lives in `shakenfist-spice-rdp`; `ryll` sees only this
   crate's types. The workspace `rust-version` becomes 1.89
   (survey item 13).
2. **TLS-only by default; NLA on request; one credential pair;
   the domain is ignored.** `--rdp-security tls` (default)
   builds with `with_tls` and a `CredentialValidator` that
   compares username and password and ignores `domain` (survey
   item 3); the server's pre-loaded credentials are left unset
   so the acceptor's struct-equality check never runs.
   `--rdp-security nla` builds with `with_hybrid` and
   `set_credentials`, because CredSSP's NTLM needs the plaintext
   pair; it refuses TLS-only clients, which is IronRDP's
   behaviour and is documented. The username defaults to `ryll`
   and the password to 24 random alphanumeric characters minted
   per launch and printed, both overridable by flag; this is
   `--web`'s per-launch token in RDP clothing. This reverses the
   master plan's "offer both" because 0.13.0 cannot (survey item
   2), and picks TLS over NLA because TLS is the mode every
   client speaks and the only one where our validator sees the
   password. Open question 2 is updated with this evidence and
   the mstsc result from step 8.
3. **The RDP server runs on its own OS thread with a
   current-thread tokio runtime, inside the crate.**
   `RdpServer` and its futures are `!Send` (survey item 5), so
   `shakenfist-spice-rdp::serve` spawns a thread, builds the
   runtime and server there, and returns a `Send` handle with
   `shutdown()` (sends `ServerEvent::Quit`) and `join()`. The
   SPICE side stays on ryll's multi-thread runtime; the two
   share `Arc<tokio::sync::Mutex<SurfaceMirror>>`, mpsc senders
   and watch receivers, all of which work across runtimes.
   RemoteFX encoding then never competes with SPICE decoding for
   a worker. A `LocalSet` on the caller's runtime was rejected
   because it would make the crate's API `!Send` for every
   consumer.
4. **One connection at a time, through IronRDP's own accept
   loop, for now.** `RdpServer::run` is used as is (survey item
   10): a second client waits in the backlog until the first
   disconnects. Takeover, which the master plan's open question
   4 wants, needs our own accept loop around `run_connection`
   and belongs to phase 5 with the rest of the lifecycle work.
   Phase 2 documents the wait. The listener binds `--rdp-host`
   (default `127.0.0.1`, as `--web-host`) and `--rdp-port`: when
   the flag is absent, 3389 is tried by binding and releasing a
   probe listener and, if that fails, port 0 is used and the
   real port is read back with `ServerEvent::GetLocalAddr`;
   when the flag is given, its port is used or the launch fails.
5. **The display adapter is a damage-paced reader of the
   mirror, not a queue of bitmaps.** `MirrorDisplay` implements
   `RdpServerDisplay`: `size()` is the primary surface's size
   (or `--rdp`'s fallback `1024x768` before the first surface,
   corrected by a `Resize` once it exists). `updates()` returns
   `MirrorUpdates`, whose `next_update`:
   - keeps a `VecDeque<DisplayUpdate>` of pending updates and
     returns the front if there is one;
   - otherwise waits one tick of a `tokio::time::interval`
     (`DAMAGE_POLL_INTERVAL`, 16 ms), locks the mirror, and if
     the primary surface's size differs from the last size it
     sent, clears pending damage knowledge, pushes
     `Resize(new size)` and a full-surface bitmap; if the
     surface has not yet been sent in full, pushes a
     full-surface bitmap (survey item 7); otherwise calls
     `take_damage()` and, for a non-empty region, copies the
     bounding box's rows once into a `Bytes` and pushes one
     `BitmapUpdate` per rectangle, sliced from that copy with
     the box's stride and the rectangle's offset, format
     `RgbA32`;
   - drops the lock and returns the front of the deque.
   Cancel safety (survey item 9) holds by construction: the only
   awaits are the tick and the lock, both before any damage is
   taken, and everything taken is in `self` before the function
   returns. A lagged mirror marks every surface damaged (phase 1,
   decision 3), so recovery is the ordinary full-surface path.
   `PointerPosition` is never sent (qemu-rdp's experience,
   master plan); cursor shapes are phase 3. The 16 ms poll was
   chosen over a wake-up from the mirror task because
   `EventBus` exposes none and a bus subscription cannot be
   ordered after the mirror's apply; if step 8's latency numbers
   say the poll costs too much, a `Notify` from the mirror task
   is the follow-up, recorded in *Future work*.
6. **The session seam is a provider trait plus a slot.**
   `SessionHandles { surface_mirror, input_tx, mouse_mode,
   resize_tx, label }` is what the adapters need from a SPICE
   session; `SessionProvider` is `fn session(&self, credentials:
   &Credentials) -> Result<Arc<SessionHandles>, Rejected>`,
   called by the TLS-mode validator (and at connect for NLA,
   with the configured pair), and the chosen handles go into a
   `SessionSlot` the display and input adapters read. ryll's
   `SingleSession` provider holds one pair and one
   `SessionHandles`. The crate's docs say what survey item 11
   found: 0.13.0 needs the desktop size before authentication,
   so a provider with sessions of different sizes must resize
   after choosing or run one process per session, which is what
   the master plan's *Moving to Kerbside* already plans.
7. **Input mapping.** `SpiceInputHandler` implements
   `RdpServerInputHandler` (synchronous, `Send`), holding a
   `PointerTranslator`, the `watch::Receiver<u32>` for the mouse
   mode (read with `borrow()`), the `mpsc::Sender<InputEvent>`
   (`try_send`; a full queue drops the event, counts it and
   warns once, because the handler cannot await) and a wheel
   remainder:
   - `Pressed { code, extended }` →
     `KeyDown(make_scancode(base, false))` with `base = if
     extended { 0xE000 | code } else { code }`, as the GUI
     builds its scancodes; `Released` likewise with `true`.
   - `Synchronize(flags)` → `InputEvent::KeyModifiers(flags.bits()
     & 0x7)`: Scroll, Num and Caps Lock share SPICE's bit layout
     (survey item 15).
   - `Move { x, y }` → `translator.motion(mode, x, y)`; RDP
     coordinates are desktop pixels, which are surface pixels.
   - `LeftPressed` and friends → `translator.button(mode,
     mouse_buttons::LEFT | MIDDLE | RIGHT, pressed, None)`.
   - `VerticalScroll { value }` → wheel clicks: `value` is in
     rotation units, 120 per notch for ordinary wheels; the
     handler accumulates the remainder and emits
     `translator.wheel(clicks)` per whole notch, so a
     high-resolution wheel neither spams nor stalls.
   - `RelMove { x, y }` → in server mode `MouseMotion { dx, dy
     }` directly; in client mode added to the translator's last
     position, clamped to the surface, and sent through
     `motion`. `Scroll { x, y }` uses `y` as vertical units and
     drops `x`.
   - `Button4Pressed`/`Button5Pressed`, `UnicodePressed`,
     `UnicodeReleased` and horizontal scroll → dropped with a
     `warn_once!` each (open question 5; SPICE has no horizontal
     wheel and no mask bit for the side buttons).
8. **ryll wiring mirrors `run_web`.** A default-off `rdp` cargo
   feature on `ryll` (`rdp = ["dep:shakenfist-spice-rdp"]`,
   open question 8), `make lint` and `make test` compile it,
   release builds do not. `run_rdp` builds the same scaffolding
   as `run_web` (`main.rs:514-990`): byte counter, traffic,
   snapshots, pedantic observer, `EventBus`, `SessionState`,
   `run_connection` with `PlaybackOutput::Sink(DiscardPlayback)`
   (a no-op sink; phase 4 replaces it with rdpsnd) and the
   optional control socket with the same `SessionHandles` the
   web path builds, then calls `shakenfist_spice_rdp::serve`
   and waits for Ctrl+C or the server's exit, shutting down in
   `run_web`'s order. `--rdp` conflicts with `--web` and
   `--headless` at parse time. The launch prints, with the
   `audit-allow-println` marker `web/server.rs:439` uses:
   `RDP: <host>:<port>`, `user: <user>`, `password: <password>`,
   `security: tls|nla`, and `certificate SHA-256: <fingerprint>`
   for a generated certificate.
9. **TLS identity.** With `--rdp-tls-cert` and `--rdp-tls-key`
   (both or neither, by clap `requires`), IronRDP's
   `TlsIdentityCtx::init_from_paths` loads them (it also derives
   the public key NLA needs). Without them, `rcgen 0.14`
   generates a self-signed certificate for `--rdp-host` and
   `localhost` at launch, as `web/server.rs:1060` does in tests,
   and the crate prints its SHA-256 fingerprint so a user can
   match what the client shows. The certificate is per launch
   and never written to disk; a stable certificate is what the
   two flags are for, and the operator guide (phase 6) will say
   so.
10. **Testing.** Each adapter has unit tests against the
    renderer's real types (a `SurfaceMirror` with a test surface,
    a `PointerTranslator`, an mpsc). A loopback integration test
    in `shakenfist-spice-rdp/tests/` starts the server with
    `with_no_security` on `127.0.0.1:0`, connects with IronRDP's
    own client connector, completes the handshake, receives the
    seeding bitmap and sends one key, asserting the `InputEvent`
    that reaches the mpsc. The precedent is
    `crates/ironrdp-testsuite-core/tests/server/autodetect.rs`
    (no security) and `crates/ironrdp-testsuite-extra/tests/e2e.rs`
    (the client loop). FreeRDP against the desktop guest is the
    manual test, run by the management session with Mikal, and
    `tools/rdp-measure.sh` (step 8) records bytes per second
    from `ss -tin` and CPU from `/proc/<pid>/stat` for the three
    workloads of open question 7.
11. **What this phase changes in the master plan.** Survey items
    1, 2, 3, 4, 5, 6, 8, 11, 13 and 14 are corrected at their
    source in the planning commit. Open questions 1, 2, 3 and 7
    get their evidence-based answers in the close-out commit of
    step 9, once step 8 has run.

## Step plan

Steps run in order; each depends on the one before only where
stated. Each step is one commit, with the subject given.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | opus | none | New crate skeleton, workspace MSRV, dependency and licence check. Commit: "Add the shakenfist-spice-rdp crate skeleton." See brief 1. |
| 2 | medium | sonnet | none | Renderer: `InputEvent::KeyModifiers`. Commit: "Let a frontend set the guest's lock keys." See brief 2. |
| 3 | high | opus | worktree | Display adapter over the mirror's damage. Needs step 1. Commit: "Feed RDP display updates from the surface mirror." See brief 3. |
| 4 | high | opus | worktree | Input adapter over the pointer translator. Needs steps 1 and 2. Commit: "Translate RDP input into SPICE input events." See brief 4. |
| 5 | high | opus | worktree | Credentials, TLS identity and the server runner on its own thread. Needs steps 3 and 4. Commit: "Serve RDP from a SPICE session." See brief 5. |
| 6 | xhigh | opus | worktree | Loopback integration test with IronRDP's client connector. Needs step 5. Commit: "Prove the RDP handshake with a loopback client." See brief 6. |
| 7 | high | opus | worktree | `ryll --rdp`: feature, flags, `run_rdp`, printing, control socket, Makefile. Needs step 5. Commit: "Add the --rdp mode to ryll." See brief 7. |
| 8 | medium | sonnet | none | Measurement script for the manual test. Needs step 7. Commit: "Measure an RDP session's bytes and CPU." See brief 8. The manual test itself is run by the management session with Mikal and recorded in *Results*. |
| 9 | medium | sonnet | none | Documentation and the master plan's answers. Needs all of the above and the *Results* of step 8. Commit: "Document the RDP skeleton." See brief 9. |

### Brief 1: crate skeleton

- Raise `rust-version` in the workspace `Cargo.toml` (`:21`) to
  `"1.89"` and update its comment: the floor is now set by
  `ironrdp-server`, which `shakenfist-spice-rdp` needs; the
  `as_chunks` reason still holds for the compression crate.
- New crate `shakenfist-spice-rdp` as a workspace member, laid
  out like `shakenfist-spice-webrtc` (`Cargo.toml`, `README.md`,
  `src/lib.rs`): `[package]` fields from the workspace, a
  description ("RDP frontend for the ryll SPICE client: serves a
  SPICE session to stock RDP clients with IronRDP"), `[lints]
  workspace = true`, `publish` left default. Dependencies, each
  with a comment in the style of the webrtc crate's manifest
  saying why it is there and what it pulls in:
  `ironrdp-server = { version = "0.13", default-features =
  false, features = ["rayon", "helper"] }` (decision 1);
  `shakenfist-spice-renderer = { path =
  "../shakenfist-spice-renderer", version = "0.3.0",
  default-features = false }`; `shakenfist-spice-protocol`
  (path, 0.3.0) for the mouse-mode and modifier constants;
  `tokio` with `["rt", "sync", "time", "net", "macros"]`;
  `async-trait = "0.1"` (the IronRDP traits are declared with
  it); `anyhow`, `tracing`, `bytes = "1"`, `rand = "0.9"`,
  `rcgen = "0.14"` (ryll has it as a dev-dependency; cite
  `ryll/Cargo.toml`), `sha2 = "0.10"` (the certificate
  fingerprint; check which `sha2` version the lockfile already
  has and use that major). Dev-dependencies are added by steps
  3, 4 and 6.
- `src/lib.rs` declares the modules the later steps fill
  (`display`, `input`, `auth`, `tls`, `server`) as empty files
  with a module doc comment each, and the crate doc comment:
  what the crate is, that it is the library behind `ryll --rdp`,
  that IronRDP 0.13.0 is the pinned server, and that the server
  runs on a thread of its own because IronRDP's server type is
  not `Send`. Define the shared types the adapters use so steps
  3 and 4 can run in parallel: `pub struct SessionHandles {
  pub surface_mirror: Arc<tokio::sync::Mutex<SurfaceMirror>>,
  pub input_tx: mpsc::Sender<InputEvent>, pub mouse_mode:
  watch::Receiver<u32>, pub resize_tx:
  mpsc::Sender<DisplaySize>, pub label: String }`, `pub struct
  SessionSlot(Arc<std::sync::Mutex<Option<Arc<SessionHandles>>>>)`
  with `set`, `clear` and `current`, and `pub trait
  SessionProvider: Send + Sync { fn session(&self, credentials:
  &ironrdp_server::Credentials) -> Result<Arc<SessionHandles>,
  Rejected>; }` with `pub struct Rejected;`. Document the seam's
  limit from survey item 11 on the trait.
- `README.md` in the webrtc crate's shape: what it is, the
  "reachability fallback, not a better path" framing from the
  master plan, the pinned IronRDP version, and the *Source*
  section.
- Add the crate to `tools/publish-crates.sh`'s order (`:27-31`,
  after `shakenfist-spice-webrtc`) and check whether
  `tools/package-check-verify.sh` and `make package-check`
  (`Makefile:370`) enumerate crates; if they do, add it there.
- Run `make build`, `make lint`, `make package-check`, and
  cargo-deny the way `docs/ci.md` *Reproducing CI locally* says
  (CI's `cargo-deny` job uses `EmbarkStudios/cargo-deny-action`
  over `deny.toml`); if cargo-deny cannot run locally, say so in
  the report and the management session relies on the pull
  request's job. Record in the report: `cargo tree -p
  shakenfist-spice-rdp -e normal,build` (run inside the
  devcontainer), listing every `-sys` crate, every crate that
  now has two versions in `Cargo.lock`, and confirming
  `qoicoubeh`, `zstd-safe 7` and `openh264` are absent. The
  management session copies that into *Results*.
- Do not add any behaviour. Do not add the `rdp` feature to
  `ryll` yet (step 7).

### Brief 2: lock keys

- `shakenfist-spice-renderer/src/channels/mod.rs:499-538`: add
  `InputEvent::KeyModifiers(u16)` with a doc comment: the
  guest's lock-key state as SPICE `keyboard_modifiers` bits
  (`shakenfist_spice_protocol::keyboard_modifiers::{SCROLL_LOCK,
  NUM_LOCK, CAPS_LOCK}`); the inputs channel sends it as
  `KEY_MODIFIERS`, and the server toggles each lock that differs
  from its own record. Update `ChannelEvent`/`InputEvent` match
  arms that must be exhaustive (grep `InputEvent::` across the
  workspace, including `ryll/src/app`, the control socket in
  `shakenfist-spice-renderer/src/control/` and the web relay).
- `channels/inputs.rs`: handle it in the input-event dispatch
  (beside `MouseDown` at `:750`) by calling the existing
  `send_key_modifiers` (`:296-309` shows the open-time call);
  record it with `record_event` like the others, with
  `event_type: "KeyModifiers"`. Keep the open-time Num Lock send
  and reword its comment: it is no longer "the only
  KEY_MODIFIERS ryll sends", a frontend that knows its client's
  lock state can send another.
- Tests beside the existing inputs-channel tests: the variant
  produces one `KEY_MODIFIERS` message with the given bits;
  bits above Caps Lock are passed through unchanged (the
  protocol owns the meaning).
- `docs/control-socket-protocol.md` is unaffected unless the
  control socket enumerates `InputEvent` variants; check and
  say.

### Brief 3: display adapter

- `shakenfist-spice-rdp/src/display.rs`, implementing decision
  5 exactly. Types: `pub struct MirrorDisplay { slot:
  SessionSlot, fallback: DesktopSize }` implementing
  `ironrdp_server::RdpServerDisplay` (`size`, `updates`; leave
  `request_initial_size` and `request_layout` at their
  defaults, phase 3 fills them) and `struct MirrorUpdates`
  implementing `RdpServerDisplayUpdates`. Read the trait docs in
  `crates/ironrdp-server/src/display.rs:236-262` of the pinned
  tree first, and the encoder's seeding and size rules in
  `encoder/mod.rs:240-275, 318-331, 379-386`.
- `size()`: the primary surface's size from
  `SurfaceMirror::primary_surface()` (`surface_mirror.rs:291`),
  or `fallback` when there is no session or surface yet. RDP
  desktop sizes are `u16`; clamp and warn once if a surface is
  larger than `u16::MAX` (it will not be).
- `MirrorUpdates` fields: the slot, `pending:
  VecDeque<DisplayUpdate>`, `sent_size: Option<DesktopSize>`,
  `seeded: bool`, `interval: tokio::time::Interval` with
  `DAMAGE_POLL_INTERVAL = Duration::from_millis(16)` and
  `MissedTickBehavior::Delay`. `next_update` is the algorithm in
  decision 5. A session slot that is empty (no session chosen)
  waits on the tick and returns nothing; a surface that
  disappears (`primary_surface()` is `None` after it existed)
  is treated as "not seeded", so the next surface is sent in
  full. Build each `BitmapUpdate` with `format:
  PixelFormat::RgbA32`, `x`/`y` as `u16`, `width`/`height` as
  `NonZeroU16`, `stride` the bounding box's row length in bytes
  as `NonZeroUsize`, and `data` a `Bytes::slice` of the box copy
  starting at the rectangle's first pixel. `DamageRect` is
  `{left, top, right, bottom}` with `right` and `bottom`
  exclusive (`display/damage.rs:34-39`).
- Never hold the mirror lock across an await other than its own
  acquisition, and never `await` between `take_damage()` and
  pushing into `pending`; write a comment saying why (the trait's
  cancel-safety requirement).
- Tests (`#[tokio::test]`, with `tokio::time::pause` where a
  tick is awaited): with a `SurfaceMirror::with_test_surface`
  (`surface_mirror.rs:336`) the first update is `Resize`-free
  and covers the whole surface; a `blit` to a sub-rectangle then
  yields one bitmap of that rectangle with correct `x`, `y`,
  `stride` and bytes; two separated rectangles yield two
  bitmaps in one take; a surface size change yields `Resize`
  then a full bitmap; `mark_all_damaged` yields a full bitmap;
  dropping a `next_update` future while it waits on the tick,
  then calling again, still yields the damage (cancel safety);
  an empty slot yields nothing within a few ticks. Dev-dependency
  on the renderer stays `default-features = false`.

### Brief 4: input adapter

- `shakenfist-spice-rdp/src/input.rs`: `pub struct
  SpiceInputHandler` implementing
  `ironrdp_server::RdpServerInputHandler` per decision 7. Read
  `crates/ironrdp-server/src/handler.rs` of the pinned tree in
  full first: the enum, and each `From` impl, which is where
  survey item 4's gap is; then `pointer.rs` and
  `channels/inputs.rs:1340-1373` (`make_scancode`) in the
  renderer, and `ryll/src/web/inputs.rs:133-290` for how the
  web relay drives the same translator.
- The handler holds the `SessionSlot` and reads the session's
  `mouse_mode` with `borrow()` on each event (the handler is
  synchronous). With no session chosen, events are dropped and
  counted. `input_tx.try_send`: on `Full`, increment a dropped
  counter and `warn_once!` (`shakenfist_spice_protocol::warn_once`,
  as the renderer uses it) naming the queue; on `Closed`, warn
  once and drop.
- The wheel: `VerticalScroll { value }` adds `value` to an
  `i32` remainder; for each whole 120 in it, emit one
  `translator.wheel(±1)`; `Scroll { x: _, y }` uses `y` the same
  way. Say in a comment that 120 units per notch is MS-RDPBCGR's
  `WheelRotationMask` convention and high-resolution wheels send
  fractions of it.
- Relative motion: `RelMove { x, y }` in server mode becomes
  `InputEvent::MouseMotion { dx: x, dy: y }` sent directly and
  recorded as the translator's new reference by calling
  `motion` with the integrated position is **not** enough, so
  keep the translator out of the server-mode path and send the
  delta. In client mode, add the delta to `translator.last_pos()`
  (or the surface centre when `None`), clamp to the primary
  surface's size read from the mirror with `try_lock` (skip the
  event if the lock is busy, counted), and pass through
  `motion`.
- File a ryll issue titled "RDP: middle click and horizontal
  wheel are lost with IronRDP 0.13.0", citing survey item 4 and
  `master`'s fix, and reference it from a comment at the
  `MiddlePressed` arm so the reader knows the arm is reachable
  only through the relative-mouse PDU and `ainput` today.
- Tests with a real `PointerTranslator`, an mpsc and a watch:
  an extended key press and release produce
  `make_scancode(0xE000 | code, …)`; a plain key produces the
  base; `Synchronize` with Caps and Num produces
  `KeyModifiers(0x6)`; in client mode a move produces
  `MouseMove`, a left press at that position produces
  `MouseDown { button: LEFT, x, y }`; in server mode a move after
  a move produces `MouseMotion` with the delta; `VerticalScroll
  { value: 120 }` produces a button-4 press and release and
  `-240` two button-5 pairs; `60` then `60` produces one pair;
  `RelMove` in server mode produces `MouseMotion` unchanged and
  in client mode a clamped `MouseMove`; `UnicodePressed` and
  `Button4Pressed` produce nothing; a full queue drops and
  counts.

### Brief 5: credentials, TLS and the runner

- `shakenfist-spice-rdp/src/auth.rs`: `pub struct
  SingleSession { expected: Credentials, handles:
  Arc<SessionHandles> }` implementing `SessionProvider`
  (username and password must match; `domain` is ignored, with
  a comment citing survey item 3's reason in plain words: mstsc
  fills it, FreeRDP may not); `pub fn generate_password() ->
  String` (24 alphanumeric characters from `rand`); and
  `struct ProviderValidator { provider: Arc<dyn
  SessionProvider>, slot: SessionSlot }` implementing
  `ironrdp_server::CredentialValidator`: `Accept` and fill the
  slot on success, `Reject` otherwise, never `Err`. Read
  `crates/ironrdp-server/src/server.rs:100-215` of the pinned
  tree for the trait and `ExactMatchCredentialValidator`, which
  this replaces because it compares the domain.
- `src/tls.rs`: `pub struct TlsIdentity { certs, priv_key,
  pub_key, fingerprint: String }` with `from_pem_files(cert,
  key)` over `ironrdp_server::TlsIdentityCtx::init_from_paths`
  (`crates/ironrdp-server/src/helper.rs`), `self_signed(names:
  &[String])` over `rcgen::generate_simple_self_signed`
  (`ryll/src/web/server.rs:1060` shows the 0.14 call) filling
  the same fields (derive `pub_key` the way `helper.rs:22-63`
  does from the certificate's SubjectPublicKeyInfo), `acceptor()
  -> tokio_rustls::TlsAcceptor` (as `helper.rs:65`), and the
  SHA-256 fingerprint of the leaf certificate's DER as
  colon-separated upper-case hex, which is what FreeRDP and
  mstsc show.
- `src/server.rs`: `pub struct RdpConfig { host: IpAddr, port:
  Option<u16>, security: RdpSecurity, identity: TlsIdentity,
  credentials: Credentials, provider: Arc<dyn SessionProvider>
  }`, `pub enum RdpSecurity { Tls, Nla }`, `pub fn serve(config:
  RdpConfig) -> anyhow::Result<RdpServerHandle>` and `pub struct
  RdpServerHandle { local_addr: SocketAddr, … }` with
  `shutdown(&self)` and `join(self) -> anyhow::Result<()>`.
  `serve` resolves the port per decision 4 (probe-bind 3389
  when `port` is `None`, else 0), spawns a named thread
  (`"rdp-server"`) that builds a current-thread runtime, builds
  the server (`RdpServer::builder().with_addr(…)`, then
  `with_tls(acceptor)` plus `with_credential_validator(Some(…))`
  for `Tls`, or `with_hybrid(acceptor, pub_key)` plus
  `set_credentials(Some(creds))` and filling the slot at build
  time for `Nla`; `with_input_handler`, `with_display_handler`,
  `with_connection_handler` logging connect and disconnect with
  the peer, duration and error at `info!` and clearing the slot
  on disconnect in TLS mode), runs it in a `LocalSet`, and
  reports the bound address back over a `oneshot` using
  `ServerEvent::GetLocalAddr` before `serve` returns. Read
  `server.rs:719-996` and `builder.rs` of the pinned tree, and
  `crates/ironrdp/examples/server.rs:395-430` for the
  builder-and-credentials sequence.
- `shutdown` sends `ServerEvent::Quit`; `join` joins the thread
  and returns the server's result. A client that is connected
  when `Quit` arrives: check what `run` does (`server.rs:904-996`,
  the `select!`); if it only breaks between connections, say so
  in the handle's docs and in the step report, since step 7's
  Ctrl+C path then needs a bounded wait, as `run_web` has.
- Unit tests: the validator accepts a matching pair with any
  domain and rejects a wrong password; `self_signed` yields a
  certificate whose fingerprint has 32 colon-separated bytes;
  `serve` with `port: Some(0)` returns an ephemeral address and
  `shutdown` then `join` returns within a timeout; with a port
  held by a test listener and `port: None`, `serve` falls back
  to an ephemeral port.

### Brief 6: loopback test

- `shakenfist-spice-rdp/tests/loopback.rs`. Start the server
  through `serve` with a new `RdpSecurity::None` variant that
  exists only for tests (gate it behind a `test-support` cargo
  feature as the webrtc crate does with its
  `TestPeer`, with a self dev-dependency enabling it), a
  `SingleSession` over a `SurfaceMirror::with_test_surface` of
  640x480 and an input mpsc. Connect with IronRDP's client:
  dev-dependencies `ironrdp-connector`, `ironrdp-session`,
  `ironrdp-tokio`, `ironrdp-pdu`, `ironrdp-graphics` at the
  versions `crates/ironrdp-server/Cargo.toml` of the pinned tree
  names (0.10, 0.9 and so on; check each on crates.io with the
  same date). The precedent for a no-security client is
  `crates/ironrdp-testsuite-core/tests/server/autodetect.rs` and
  for the active-stage loop
  `crates/ironrdp-testsuite-extra/tests/e2e.rs:255-330`; copy
  the shape, not the code.
- Assert: the connection activates; the first decoded output is
  a bitmap covering 640x480 (the seed); after `blit` of a
  16x16 block at (100, 100) on the mirror, the next bitmap
  covers that block; sending a scan-code key event for `0x1E`
  yields `InputEvent::KeyDown(0x1E)` then `KeyUp(0x9E)` on the
  mpsc; `shutdown` ends the server. Use `tokio::time::timeout`
  on every await so a hang is a failure, not a stuck CI job.
- If the no-security path cannot complete the handshake with
  the 0.13.0 client connector (standard RDP security needs RC4
  encryption the client may not do), switch to TLS with the
  self-signed identity and a client TLS connector that skips
  verification (`ironrdp-tls` with its `rustls` feature, as the
  e2e test's dev-dependencies show), and say so in the report;
  that is a plan deviation to record in *Execution notes*.
- `make test` must run it in the devcontainer with no network
  (the Makefile's test target runs offline); check that the
  `--frozen` lockfile has every new dev-dependency.

### Brief 7: ryll --rdp

- `ryll/Cargo.toml`: feature `rdp = ["dep:shakenfist-spice-rdp"]`,
  default off; optional path dependency. `AGENTS.md:233-245`
  says the binary ships four features; it now ships five, so
  update that paragraph and the feature list in
  `docs/development.md:100-149` (step 9 does the prose; this
  step adds the entries so the tree is never wrong).
- `ryll/src/config.rs`, after the `--web` flags (`:206-262`):
  `--rdp` (`conflicts_with_all = ["web", "headless"]`),
  `--rdp-host` (default `127.0.0.1`), `--rdp-port`
  (`Option<u16>`, "default 3389, or an ephemeral port if 3389 is
  in use"), `--rdp-user` (default `ryll`), `--rdp-password`
  (default: generated), `--rdp-security` (`tls` | `nla`,
  default `tls`, a clap `ValueEnum`), `--rdp-tls-cert` and
  `--rdp-tls-key` (each `requires` the other). Doc comments in
  the style of the web flags; the `--rdp` comment says what
  security and credentials default to and that the mode is a
  reachability fallback. `validate_control_socket` (`:371`)
  accepts `--rdp` beside `--web` and `--headless`, and its error
  names all three; extend its tests (`:1150-1215`).
- `ryll/src/main.rs`: dispatch `args.rdp` before `args.web`
  (`:300-352`), with `#[cfg(feature = "rdp")]` and a
  `bail!("this ryll binary was built without the `rdp`
  feature")` otherwise, like the `gui` arm. `run_rdp` copies
  `run_web` (`:514-990`) with these differences: no WebRTC
  state, media policy or ICE; `PlaybackOutput::Sink(Arc::new(
  DiscardPlayback))` where `DiscardPlayback` is a unit struct
  implementing `PlaybackSink` with every default, defined in a
  small `ryll/src/rdp.rs` (`#[cfg(feature = "rdp")]`) beside the
  mode's glue; the control socket wired exactly as web wires it
  (`:808-860`), including the digest poller; then build
  `SessionHandles` from `event_bus.mirror()`, `input_tx`,
  `session_state_rx.mouse_mode`, `resize_tx`, build the
  `TlsIdentity` (files or self-signed for `--rdp-host` and
  `localhost`), `SingleSession`, `RdpConfig`, and call
  `shakenfist_spice_rdp::serve`. Print the launch lines of
  decision 8 with the `audit-allow-println` marker
  (`web/server.rs:439`). Wait for `SHUTDOWN_REQUESTED` (the
  Ctrl+C bridge `run_web` uses) or the connection task ending,
  then `handle.shutdown()`, bounded `join`, and the same
  teardown order as `run_web`: cancel the renderer, join the
  connection task with a 2 s ceiling, cancel the control socket,
  abort the bus tasks.
- `Makefile`: `lint` (`:281-286`) and `test` (`:271-280`) add
  `--features ryll/rdp` so the mode compiles and its tests run
  in CI; `release` (`:212`) and `check-windows` (`:227`) do not
  (open question 8). Update the comments that explain the
  feature lists.
- `make build`, `make test`, `make lint`, `make check-windows`,
  `make package-check` and `pre-commit run --all-files` pass.
  Run `target/release/ryll --rdp --direct 127.0.0.1:1` (a
  non-listening SPICE port, as `tools/web-smoke.sh` does) and
  confirm the RDP listener comes up, the launch lines print,
  and SIGTERM exits cleanly; put the output in the report.

### Brief 8: measurement script

- `tools/rdp-measure.sh PID [SECONDS]` (shellcheck-clean, `set
  -euo pipefail`): samples, every second for `SECONDS` (default
  30), the ryll process's CPU from `/proc/PID/stat` (utime +
  stime, as `tools/measure-idle-cpu.sh` does; read it first and
  share its approach) and the bytes sent on the RDP TCP
  connection from `ss -tin` filtered to the process's listening
  port (passed as a second flag, `--port`), and prints a one-line
  summary: average CPU percent, peak CPU percent, total bytes
  sent, average bits per second. It must not need root. A
  usage comment at the top says the three workloads it is for
  (typing in a terminal, scrolling a browser page, playing a
  video) and that the management session records the numbers in
  the phase plan.
- Add a `Makefile` comment under the desktop-guest target
  (`:525-540`) pointing at the script? No: keep the Makefile
  unchanged; mention the script in `docs/development.md`'s
  manual-verification section in step 9.

### Brief 9: documentation and the master plan

- `docs/configuration.md`: a *RDP mode* table after the web
  options, one row per flag from step 7 with its default, in the
  file's existing table shape; the `--rdp` row says the mode is
  a reachability fallback and that the operator guide is to
  come.
- `docs/development.md`: the `rdp` feature in the feature list
  (`:100-149`), what it pulls in (`ironrdp-server`, `sspi`,
  `picky`, a second `reqwest`) and that it is off in release
  builds for now; in *Manual verification against a desktop
  guest* (`:469-500`), a paragraph on `--rdp` with FreeRDP
  (`xfreerdp3 /v:127.0.0.1:PORT /u:ryll /p:PASSWORD /cert:tofu`,
  or whatever step 8's run showed works) and
  `tools/rdp-measure.sh`.
- `AGENTS.md:233-245`: five features, with `rdp` default-off
  beside `digest-decode`. No other `AGENTS.md` change: the
  conventions did not change.
- `docs/multi-mode-parity.md:21-25`: a fourth row in *The three
  modes* table, `RDP | --rdp | Serves a stock RDP client with
  IronRDP; in progress, display and input only; the column
  lands with the operator guide` and rename the heading to
  *The four modes*. The feature column is phase 6.
- Master plan: fill *Open questions* 1, 2, 3 and 7 with the
  answers from *Results* (which IronRDP and why; the security
  default and what mstsc and FreeRDP negotiated; how server
  mouse mode felt and whether relative mouse worked; the codec
  and the three workloads' numbers), each as an added
  **Answer (phase 2):** paragraph under the question, keeping
  the recommendation text as history.
- No plan, phase or decision citations in code or docs other
  than the plan files.

## Risks and mitigations

- **A stock client refuses the TLS-only server or its
  self-signed certificate.** mstsc warns on an untrusted
  certificate and offers to continue; FreeRDP needs
  `/cert:tofu` or `/cert:ignore`. Mitigation: step 8 records
  the exact prompts, and the printed fingerprint is what the
  user compares. If mstsc's default policy refuses TLS-only
  servers outright, `--rdp-security nla` exists and decision 2
  is revisited; the management session checks the result
  before step 9.
- **RemoteFX encoding is too slow or too hungry.** IronRDP
  encodes on the calling thread (with `rayon` for tiles);
  decision 3's dedicated thread keeps it off the SPICE workers
  but not off the machine. Mitigation: step 8's CPU numbers go
  into open question 7, and planar (the fallback) is tested by
  running FreeRDP with RemoteFX disabled (`-rfx` or `/rfx` off;
  the exact flag from `xfreerdp3 /help`).
- **The 16 ms damage poll adds latency or idle CPU.**
  Mitigation: step 8 measures idle CPU with no client input and
  with the guest idle; a `Notify` from the mirror task is the
  prepared follow-up (*Future work*).
- **The loopback test cannot complete without TLS.** Brief 6
  names the fallback (TLS with a verification-skipping client);
  the management session checks that the test still asserts the
  seed bitmap and the key event, which is its point.
- **`sspi`'s `scard` feature pulls a native dependency.** Step
  1's `cargo tree` and the Windows check (`make check-windows`
  does not build the crate, since the feature is off there, so
  the report must say whether the crate builds for the gnu
  triple at all) tell the management session before step 3
  starts; if a C build appears, the step report names it and
  the plan records it beside #593.
- **IronRDP's accept loop ignores `Quit` while a client is
  connected.** Brief 5 asks the step to check; step 7's Ctrl+C
  path uses a bounded join either way, so the process always
  exits, at worst after the client's disconnect or the 2 s
  ceiling with a logged warning.
- **The crate does not build on Windows or macOS.** The release
  matrix does not enable `rdp`, so this phase cannot break it,
  but `cargo test --workspace` in `release.yml` (`:248`) builds
  every member on every platform, including the new crate and
  its tests. Mitigation: `make check-windows` is extended in
  step 7 only if the crate compiles for the gnu triple; if it
  does not, the step gates the crate's tests behind
  `#[cfg(unix)]` and says so, and the plan records a macOS and
  Windows build of the crate as phase 6 work.
- **Port 5900 is held by andris, so `make test-qemu-desktop`
  cannot be used as is.** Phase 1's workaround
  (`tools/start-desktop-qemu.sh --spice-port 5910`, with the QMP
  socket on a short path under `/tmp`) is reused.

## Definition of done

- [x] `shakenfist-spice-rdp` is a workspace member; `cargo tree
      -p shakenfist-spice-rdp -e normal,build` (recorded in
      *Results*) lists `ironrdp-server v0.13.x` and none of
      `qoicoubeh`, `openh264`, `openh264-sys2`, `cpal`, `nusb`,
      `dav-server`; every `-sys` crate in it is named in
      *Results*.
- [x] The workspace `rust-version` is `1.89`; `grep -n
      'rust-version' Cargo.toml` shows it.
- [x] `grep -n 'KeyModifiers' shakenfist-spice-renderer/src/channels/mod.rs`
      finds the variant and `grep -n 'KeyModifiers'
      shakenfist-spice-renderer/src/channels/inputs.rs` finds
      its dispatch.
- [x] `make build`, `make test` (with `ryll/rdp`), `make lint`,
      `make check-windows` and `make package-check` pass, and
      `pre-commit run --all-files` passes.
- [x] The loopback test in `shakenfist-spice-rdp/tests/` passes
      under `make test`, and fails when the display adapter's
      full-surface seed is removed (checked by mutation in the
      management session, not committed).
- [x] `ryll --rdp --direct 127.0.0.1:1` built with `--features
      rdp` prints the address, user, password, security and
      certificate fingerprint, and exits cleanly on SIGTERM
      (step 7's report shows the output).
- [x] FreeRDP connects to `ryll --rdp` against the desktop
      guest, authenticates, shows the desktop, and keyboard,
      mouse buttons and the wheel drive XFCE in client mouse
      mode; server mouse mode moves the pointer; the negotiated
      security and codec and the three workloads' numbers are in
      *Results*, with the client versions and the date.
- [x] Open questions 1, 2, 3 and 7 in `PLAN-rdp-frontend.md`
      each have an **Answer (phase 2)** paragraph.
- [x] Every `--rdp*` flag in `ryll/src/config.rs` appears in
      `docs/configuration.md` (`grep -o -- '--rdp[a-z-]*'
      ryll/src/config.rs | sort -u` against the same grep on the
      doc); `docs/development.md` lists the `rdp` feature;
      `AGENTS.md` says five features;
      `docs/multi-mode-parity.md`'s mode table has an RDP row.
- [x] `git grep -n -i 'phase 2\|decision [0-9]\|survey item'
      -- shakenfist-spice-rdp ryll/src shakenfist-spice-renderer/src docs ':!docs/plans'`
      finds nothing.
- [x] Two ryll issues exist: #601 (filed by the survey) and the
      middle-button issue step 4 files, both linked from *Future
      work*.

## Results

### Step 1: dependency tree

`cargo tree --frozen -p shakenfist-spice-rdp -e normal,build` in the
devcontainer (957 lines, 2026-10-21), after `make lock` added 103
packages to `Cargo.lock` and removed none:

- `ironrdp-server v0.13.0` is present; `qoicoubeh`, `zstd-safe 7`,
  `openh264`, `openh264-sys2`, `cpal`, `nusb` and `dav-server` are
  absent. `zstd-safe` stays at 8.1.0 only, from ryll's `zip`.
- `-sys` crates in the tree: `aws-lc-sys 0.45.0` (already in the
  workspace through the protocol crate), `cryptoki-sys 0.5.0` (new;
  loads a PKCS#11 module with `libloading`, no C build) and
  `libz-sys 1.1.30` (new).
- **A new C build, which the plan did not expect:** `sspi`'s `scard`
  feature pulls `winscard`, which enables `flate2`'s `zlib` feature
  and so `libz-sys`. In the devcontainer it links the system zlib;
  for the Windows gnu target it compiles the bundled zlib with `cc`.
  Because cargo unifies features, every build that includes this
  crate also moves the renderer's `flate2` onto the C zlib backend.
  That reaches the `ryll` binary only through the `rdp` feature
  (step 7), which release builds do not enable. Recorded beside the
  `aws-lc-rs` note in the master plan's dependency discussion (#593)
  as the second native build the frontend brings.
- Crates that now have two versions: aead 0.5/0.6, ctr 0.9/0.10,
  der 0.7/0.8, md-5 0.10/0.11, num-bigint 0.3/0.4, pem-rfc7468
  0.7/1.0, pkcs1 0.7/0.8.0-rc.4, pkcs8 0.10/0.11, reqwest
  0.12/0.13, rsa 0.9/0.10.0-rc.18, signature 2/3, spki 0.7/0.8.
  `deny.toml` warns rather than fails on these.
- Licences of every added package are within `deny.toml`'s allow
  list (IronRDP MIT OR Apache-2.0; the dalek crates BSD-3-Clause;
  `iso7816-tlv` ISC); no git sources. cargo-deny itself could not
  run locally (not in the devcontainer, and the upstream image pull
  was refused), so advisories are checked by the pull request's job.
- The crate compiles for `x86_64-pc-windows-gnu` (a one-off
  `cargo check` with the Makefile's docker line); `make
  check-windows` checks only `-p ryll` and so does not cover it
  until step 7 enables the feature there.
- `make test`: 1507 passed, 0 failed (the new crate has no tests
  yet).

### Manual test: FreeRDP against the desktop guest

2026-10-21, 10:45 to 11:25 AEDT, on the Kasm host (16 cores, 28
GiB). Guest: `debian-xfce:13` under `tools/start-desktop-qemu.sh`
(q35, 2 vCPUs, 2 GiB, QXL, vdagent) on SPICE port 5910, in client
mouse mode. Server: `ryll --rdp --rdp-host 0.0.0.0 --direct
127.0.0.1:5910`, bound to an ephemeral port because the host's
xrdp holds 3389. Client: `xfreerdp3` (FreeRDP 3.15.0, Debian 13
`freerdp3-x11`) on the same host, `/cert:tofu`, default options,
window 1024x768, which is the guest's resolution.

- **Security negotiated:** the client requested `SSL | HYBRID`
  and the server selected `SSL` (TLS with the username and
  password in the Client Info PDU; the validator accepted
  `ryll` with an empty domain). FreeRDP printed its
  trust-on-first-use certificate warning with the fingerprint
  ryll printed at launch, and connected.
- **Codec negotiated:** RemoteFX (the client offered RLGR1 and
  RLGR3 with `SET_SURFACE_BITS`), so IronRDP's RemoteFX encoder
  carried every bitmap. Colours were right: no channel swap on
  this path. The uncompressed path (survey item 7 and the step 6
  side finding) was not exercised by this client.
- **Input, client mouse mode:** keyboard (a terminal command typed
  and run), left click (opening a terminal from the panel), right
  click (the terminal's context menu), wheel (three lines per
  notch in the terminal, as the GUI does), and middle click (sent
  by FreeRDP through its Advanced Input channel, which IronRDP
  0.13 maps fully) all drove XFCE. The first run found the wheel
  defect fixed in `1d797c2`: FreeRDP's Advanced Input channel
  sends wheel units in the high 16 bits, and ten notches had
  become about 44,000 SPICE clicks.
- **Workloads**, measured with `tools/rdp-measure.sh` over 30 s
  (idle over 20 s) on the ryll process, release build with the
  `rdp` feature. CPU is of the ryll process, 100 percent being
  one core; it includes SPICE decoding and RemoteFX encoding.

  | Workload | Avg CPU | Peak CPU | Bytes sent | Avg rate |
  |---|---|---|---|---|
  | Idle, article page shown, no input | 0.3% | 1.0% | 1.6 kB | 0.6 kbit/s |
  | Typing in a terminal (11 lines in 30 s) | 8.5% | 12.8% | 1.1 MB | 294 kbit/s |
  | Scrolling a Wikipedia article (48 notches in 30 s) | 84.6% | 131.1% | 4.3 MB | 1.12 Mbit/s |
  | Video (Big Buck Bunny 480p VP9, local file, Firefox, 853x480 region) | 248.2% | 298.8% | 417 MB | 109 Mbit/s |

  The same workloads on a debug build, for scale: typing 42.7%
  and 157 kbit/s, scrolling 64.5% (peak 210.7%) and 256 kbit/s.
  IronRDP logged "Encoding bitmap took N ms" warnings (12 to 22
  ms) 53 times during the video. The video rate is the cost of
  RemoteFX on a region that changes entirely about 28 times a
  second; H.264 through EGFX (master plan, open question 7) is
  the answer if video matters.
- **mstsc: not tested.** Mikal has no Windows machine, so the
  mstsc cells of this phase are empty and open question 2's
  answer rests on FreeRDP. Two further FreeRDP runs stand in
  for the security questions mstsc would have answered:
  `--rdp-security nla` with `xfreerdp3 /sec:nla` negotiated
  `HYBRID`, completed CredSSP against the configured pair and
  showed the desktop; and in TLS mode a wrong password was
  logged as "presented wrong credentials" and the client was
  disconnected.
- **Server mouse mode:** the guest was restarted with
  `-machine q35,vmport=off`, `-vga virtio` and `agent-mouse=off`
  (phase 1's recipe; the launcher script has no flag for it), so
  SPICE offered server mode only. The guest came up at 1280x800
  and FreeRDP's window followed, which exercised the Resize path
  on a real client. A scripted sweep of the pointer to the
  top-left corner (28 relative motion messages) followed by a
  click opened the Applications menu, so relative motion and
  buttons work in server mode. FreeRDP's `/mouse:relative:on`,
  with and without `grab:on`, kept sending absolute moves under
  the scripted X pointer; the server never saw the relative-mouse
  PDU (the handler now logs once when it does, `b8f78bd`), so
  that half of open question 3 is unobserved rather than
  answered. A physical mouse under a pointer grab may behave
  differently.
- **Other observations.** Seven of about 980 key events typed
  while Firefox was starting in the guest did not appear (one
  letter in a URL, letters in a `clear`); the inputs channel
  sent every key it was given and the handler counted no drops,
  so the loss is between QEMU's PS/2 queue and the busy guest,
  as it would be for any client, and was not pursued. A 480p
  video streamed from Wikimedia through the guest's user-mode
  network stalled for a minute at a time, which looked like a
  display stall until the SPICE blit rate showed the guest had
  stopped painting; the recorded run used a local copy.

## Execution notes

- **Back brief, 2026-10-21 08:54 AEDT.** Mikal answered the five
  gates: install FreeRDP 3.15.0 on this host (`freerdp3-x11`,
  `freerdp3-sdl`; the install needs a password, so Mikal runs
  it); a Windows machine with mstsc is available and Mikal
  drives it himself for step 8; decision 2 (TLS-only default)
  is confirmed; the desktop guest runs on SPICE port 5910; and
  this session may push and open the pull request when the
  definition of done is checked.
- **Ordering.** Steps 1 and 2 run in sequence in the phase
  worktree, because both run `make` there. Steps 3 and 4 run in
  parallel in their own worktrees once both have landed (4 needs
  2's `KeyModifiers`). Step 5 follows; 6 and 7 run in parallel
  in worktrees after 5; 8 follows 7; the manual test and *Results*
  come before 9.
- The `ryll-dev` image, absent at planning time, was present when
  implementation began (another session built it), so no rebuild
  was needed.
- **FreeRDP** was installed by Mikal from another window
  (`sudo` is not available to this session); `xfreerdp3` and
  `sdl-freerdp3` 3.15.0 are on the path.
- **Step worktrees were made by hand**, with `git worktree add`
  from the phase branch's head, rather than with the agent tool's
  own worktree isolation, which branches from the primary clone's
  checked-out `develop` and so would not have had steps 1 and 2.
  Each step's diff was applied to the phase branch with `git
  apply --index` after review, and the step worktree removed.
  Steps 3 and 4 both added a `[dev-dependencies]` section to the
  crate manifest and a `pub use` to `lib.rs`; the management
  session merged those by hand and re-ran `make lock`.
- **Step 3 departed from decision 5 in two ways**, both because
  IronRDP's server loop demanded it (`server.rs:1290` and
  `:1482-1483` of the pinned tree): a `next_update` that returns
  `None` disconnects the client, so the stream waits instead of
  returning nothing when there is no session or no damage; and a
  resize deactivates and reactivates the connection, which calls
  `size()` and `updates()` again and drops the old stream, so
  `MirrorDisplay` remembers the size it last reported and each new
  stream starts unseeded. `sent_size` is a plain `DesktopSize`,
  not an `Option`.
- **Step 4 filed #604** for the middle button and added four
  behaviours the brief did not name: a wheel reversal starts a
  fresh notch, a zero relative move in server mode sends nothing,
  a different session in the slot resets the pointer state, and a
  missing primary surface counts as a dropped event. All kept.
- **Step 5 found and fixed a gap in step 3's adapter.** IronRDP asks
  the display for its size at `server.rs:813`, before the handshake,
  and again at `:1482` for the encoder. In TLS mode the slot is
  empty at the first ask, so the client learnt the fallback size and
  the encoder the surface's, and the stream (built from the second
  value) never sent the Resize that reconciles them. The display now
  records the size the client was told, the connection handler
  forgets it on each accept, and a TLS client's first update is a
  Resize to the surface. The loopback test asserts this sequence.
  Also from step 5: IronRDP reads `Quit` inside an established
  session too, but only to disconnect that client (`server.rs:1102`),
  so `shutdown` sets a flag the connection handler turns into
  `PostConnectionAction::Stop`; a client stalled in the handshake
  still delays the stop, which is why `join` is bounded. The TLS
  acceptor is built from the process-wide rustls provider rather than
  IronRDP's `make_acceptor`, because both ring and aws-lc-rs are
  compiled into the workspace and rustls' default builder would
  panic; ryll installs ring at start-up.
- **Step 6 used TLS, not no-security** (brief 6's fallback): IronRDP
  0.13's client refuses standard RDP security outright
  (`ironrdp-connector/src/connection.rs:314`) and rejects a server
  that answers an SSL request with no protocol
  (`ironrdp-acceptor/src/connection.rs:422`). No `test-support`
  feature or `RdpSecurity::None` was added. New dev-dependencies:
  `ironrdp-connector` 0.10, `ironrdp-session` 0.11, `ironrdp-tokio`
  0.10, `ironrdp-tls` 0.2 (`rustls`); `ironrdp-session`,
  `ironrdp-tls` and `ironrdp-bulk` are new to the lockfile and reach
  only test builds. The seed mutation in the definition of done was
  run on the step's disposable worktree: with the whole-surface seed
  removed, the loopback test fails with "timed out after 10s waiting
  for the whole surface".
- **Step 6 side finding, for the manual test:** when a client
  supports surface commands but negotiates no codec, IronRDP's
  uncompressed path (`encoder/mod.rs:489-499`) sends our bytes raw
  and labelled only as 32 bpp, which RDP reads as BGRX, so such a
  client would show red and blue swapped. RemoteFX and planar honour
  `PixelFormat::RgbA32`. The manual test records which path each
  client takes before deciding whether the adapter should swizzle.
- **Step 7:** `make build` builds `-p ryll` without features, so the
  smoke run used a debug build with `--features rdp` made with the
  Makefile's docker line. Against `--direct 127.0.0.1:1` the process
  exits as soon as the SPICE connect is refused (the run ends when
  the session ends, as `--web` does), so the listener and SIGTERM
  were shown against a silent TCP listener instead. On Ctrl+C with
  the SPICE handshake stalled, the connection task does not stop
  within the 2 s ceiling and a warning is logged; the renderer does
  not check for cancellation during a stalled link handshake, which
  predates this phase and `--web` shares. `PlaybackSink::on_opus_packet`
  has no default, so `DiscardPlayback` implements it as a no-op.
  `--rdp-host` parses as an `IpAddr`. The workspace tests on the
  combined tree after steps 6 and 7: 1573 passed, 0 failed.
- **The manual test was run by the management session**, with
  the desktop guest on SPICE port 5910 and FreeRDP driven by a
  python-xlib script over the client's X window (XTest clicks and
  keys, pointer warps for motion), with screenshots of the client
  window as the evidence. Its first run found the Advanced Input
  wheel defect, fixed in `1d797c2` with a per-event cap added
  as defence; the fix was re-verified against the guest before
  the measurements. The numbers in *Results* come from a release
  build with the `rdp` feature, made with the Makefile's docker
  line (`make release` does not enable the feature); a debug
  build's numbers are given beside them for scale.
- **The back brief's mstsc answer did not hold:** no Windows
  machine was available when the test came round, so mstsc is
  not tested and NLA and wrong-password checks were done with
  FreeRDP instead.
- **The guest image was downloaded again** (`make
  testdata/debian-xfce-13.qcow2`, 747 MiB) because phase 1's
  worktree had taken its copy with it; the QMP socket had to
  live directly under `/tmp` because the session's scratch
  directory path exceeds a Unix socket's 108-byte limit.
- **Definition of done, checked 2026-10-21 12:40 AEDT** on the
  final tree (`b2db689`): `make build`, `make lint`, `make
  check-windows` and `make package-check` all exit 0 (the
  Windows check prints two pre-existing unused-import warnings
  in `session.rs`, a file this phase does not touch); `make
  test` with `ryll/rdp`: 1574 passed, 0 failed, the loopback
  test among them; `pre-commit run --all-files` passes;
  `rust-version = "1.89"`; `KeyModifiers` is in `channels/mod.rs`
  and dispatched in `channels/inputs.rs`; the eight `--rdp*`
  flags in `config.rs` are the eight in `docs/configuration.md`;
  `docs/development.md` lists `rdp`, `AGENTS.md` says five
  features, the parity table has an RDP row; the citation grep
  over code and docs finds nothing; #601 and #604 are open and
  linked from *Future work*; the seed mutation and the FreeRDP
  items are recorded above. `origin/develop` had moved on by
  five commits (#587, #599); the merge preview shows no
  conflicting hunks, so the branch was not rebased.

## Future work

- **Middle button and horizontal wheel** need IronRDP's next
  release (survey item 4, tracked as #604); re-check
  `From<MousePdu>` in `handler.rs` when it ships and bump.
- **Swizzle for the uncompressed path.** A client that supports
  surface commands but negotiates no codec gets our RGBA bytes
  labelled as 32 bpp, which RDP reads as BGRX (step 6's side
  finding). No tested client took that path; if one does, the
  display adapter should send BGRA with `PixelFormat::BgrA32`,
  which RemoteFX and planar also honour.
- **Relative mouse from a real client** remains unobserved:
  FreeRDP's option produced no relative-mouse PDU under a
  scripted pointer. Try a physical mouse under a pointer grab,
  and look for the server's one-shot log line.
- **Takeover and our own accept loop** (phase 5): wrap the
  accepted stream in a byte counter at the same time, so the
  control socket's `stats` can report RDP bytes the way it
  reports SPICE bytes.
- **A wake-up from the mirror task** instead of the 16 ms poll,
  if step 8's latency numbers ask for it: `EventBus::start`
  would take a `Notify` the mirror task signals after each
  apply.
- **NSCodec** (`ironrdp-server`'s `nscodec` feature) if a
  macOS Windows App test shows it advertises nothing else.
- **`--web` and `--headless` exclusivity** (#601).
- **Headless audio** and the `ryll/Cargo.toml` pruning carried
  over from phase 1's *Future work*.

## Back brief

Before executing any step of this plan, back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with it. Gates that need Mikal's answer
before the steps that depend on them:

- Install FreeRDP on this host (`apt install freerdp3-x11
  freerdp3-sdl`, Debian 13's 3.15.0) for step 8, or use the
  Kasm Remmina workspace instead.
- Whether a Windows machine with mstsc is available for step 8;
  if not, the mstsc cells in *Results* say "not tested" and open
  question 2's answer is FreeRDP-only.
- Decision 2 (TLS-only default) reverses the master plan's
  recommendation; confirm before step 5.
- The desktop guest runs on SPICE port 5910, as in phase 1.
- Whether this session may push and open the pull request at
  the end.
