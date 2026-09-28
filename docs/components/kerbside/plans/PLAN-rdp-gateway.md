# RDP out: a transcoding gateway for SPICE consoles

## Prompt

Before acting on this plan, read the proxy's connection path
(`rust/kerbside-proxy/src/listen.rs`, `session.rs`,
`backend.rs`, `relay.rs`), the gRPC contract
(`kerbside/rpc/kerbside.proto`), console tokens
(`kerbside/consoletoken.py`), the `.vv` endpoints in
`kerbside/api.py`, and `docs/network-ports.md`. Read the
prerequisite: `PLAN-spice-performance.md` and its phase 3
plan, `PLAN-spice-performance-phase-03-transcoding.md`, and
whatever that spike wrote up in `docs/performance/`. This
plan builds on that spike's output and does not repeat its
research.

The external claims below cite IronRDP, `qemu-rdp` and the
reference clones under `/srv/src-reference/` (qemu
30e8a06b64, libvirt) as read on 2026-09-27. IronRDP moves
quickly, so re-check each claim before building on it. Ground
every claim in the code rather than in this document, and
flag uncertainty rather than guessing.

## Situation

This plan was written on 2026-09-27 as a statement of
direction, not a schedule. Its status is `Proposed`: nothing
here is committed to, and it may never be built. It is
written down so that potential users can see where Kerbside
could go, and so that decisions made elsewhere in the
meantime do not accidentally rule it out.

### The problem

Kerbside serves SPICE: TLS on `VDI_SECURE_PORT` (5900 by
default), plus a plaintext port that exists only to redirect
(`docs/network-ports.md`). Some client networks, such as
corporate and hotel networks and customer sites, allow
outbound RDP on 3389 but not arbitrary TCP. A user on such a
network cannot reach a Kerbside console at all today.

A transcoded session loses features, but it is better than
no session. This plan is about **reachability**, not
performance. That distinction matters when reading the
prerequisite's verdict (below).

### This is a fallback, not a change of direction

`docs/index.md` pitches Kerbside as the way to *avoid*
transcoding: HTML5 transcoders lose SPICE's richer features
and high-resolution desktops. Nothing here changes that.
SPICE relay stays the default and the recommended path, and
RDP out is an explicit, opt-in fallback for clients that
cannot speak SPICE to us. Any documentation this plan
produces must say so.

### What Kerbside does today

- **Kerbside relays and never renders.** The proxy parses
  SPICE framing, runs each message through the firewall, and
  forwards it (`relay.rs`). Nothing in the release build
  decodes a pixel.
- **Authorisation is per SPICE channel.**
  - `session::handle_connection` records the channel with
    `RegisterChannel`, decrypts the ticket and asks the
    daemon with `AuthorizeConnection`, whose request carries
    the token, `connection_id`, `channel_type` and
    `channel_id` (`kerbside.proto`).
  - On success it dials the backend and relays.
  - Session termination works by `session_id` through a
    `CancellationToken` registry (`session.rs`).
- **The credential is a Kerbside console token.**
  `consoletoken.create_token` mints a 48-character
  alphanumeric token and a 12-character `session_id`, with a
  lifetime of `CONSOLE_TOKEN_DURATION`. The proxy `.vv`
  endpoint (`ConsolesProxyVirtViewer` in `api.py`) embeds it
  as the SPICE password, along with `PUBLIC_FQDN`, the public
  ports, the CA certificate and `PROXY_HOST_SUBJECT`.
- **Listeners are fixed.** `listen::run_secure` and
  `run_insecure` bind the two SPICE ports. There is no
  per-protocol listener abstraction.

### What the prerequisite provides

Phase 3 of `PLAN-spice-performance.md` is a spike in which
Kerbside terminates the display channel with Ryll's renderer
and re-emits it as SPICE. This plan depends on it for four
things:

1. **A terminating SPICE client inside Kerbside's build.**
   This means Ryll's `shakenfist-spice-renderer`, trimmed
   by a slim feature set to the display decode path, with
   the openh264 licensing question answered or fenced off.
2. **A damage stream.** The renderer's `ChannelEvent`s are
   applied to a `SurfaceMirror`, and damage rectangles are
   accumulated from them. An RDP server consumes exactly
   that: a framebuffer and the rectangles that changed.
3. **Measured costs:** CPU per transcoded session, and
   whether the renderer draws accurately on real guests.
4. **A lossless backend leg.** This is MULTI_CODEC with no
   CODEC_* bits and PREFERRED_COMPRESSION LZ4, so the picture
   is compressed lossily once, on the client leg.

It does not provide terminating the *other* channels. The
spike terminates display only and relays cursor, inputs,
audio and USB. An RDP client speaks one protocol on one
connection, so an RDP gateway must terminate **every**
channel it supports: main (for the agent, mouse mode and
monitor configuration), display, inputs, cursor and
playback. The master plan's "WebRTC out" mode has the same
shape. The two should share the full-termination machinery
rather than each growing their own.

**Reading the spike's verdict.** The spike is argued on
latency and image quality. A no-go because relay with
`streaming-video=all` is good enough does **not** kill this
plan, whose case is reachability. A no-go because the
renderer is inaccurate on real guests, or because
transcoding costs too much CPU, does, or at least resizes it.
Phase 1 below starts by making that call explicitly.

### The RDP side

- **IronRDP** (Devolutions, Rust, MIT or Apache-2.0) has
  `ironrdp-server`, 0.13.0 at the time of writing, described
  as an "extendable skeleton for implementing custom RDP
  servers".
  - A server implements `RdpServerDisplay`, which pushes
    display updates, and `RdpServerInputHandler`, which
    receives keyboard and mouse events.
  - It depends on the cliprdr (clipboard), rdpsnd (audio
    out), displaycontrol (resize), rdpdr (device
    redirection) and rdpei crates.
  - EGFX (the graphics pipeline, which carries H.264) is an
    optional feature.
  - Its README lists only bitmap updates with RDP 6.0
    compression, and TLS 1.2/1.3 security. The crate's
    dependency list suggests more than that, so what works
    end to end with mstsc needs checking, not assuming.
  - It uses tokio and tokio-rustls 0.26, as the proxy
    already does.
- **`qemu-rdp`** (Marc-André Lureau, MIT, 0.1.1 on
  crates.io) is an out-of-process RDP server for qemu's
  `-display dbus`, built on IronRDP.
  - It advertises RemoteFX, text clipboard, Opus audio
    playback, resize through displaycontrol, and TLS/CredSSP
    authentication.
  - libvirt launches it for `<graphics type='dbus'/>` since
    11.1 (`libvirt/docs/formatdomain.rst:6898-6901`, with
    `src/qemu/qemu_rdp.c`).
  - It is the closest precedent: the same RDP server
    stack, fed from qemu's D-Bus display rather than from a
    SPICE client.
- **Why not attach to D-Bus like `qemu-rdp` does?**
  - `org.qemu.Display1` (`ui/dbus-display1.xml`) exports
    everything an RDP server needs: console scanout and
    updates, keyboard, mouse, clipboard and audio.
  - But it is only reachable on the hypervisor host.
    Kerbside reaches OpenStack and oVirt consoles only
    through the SPICE port the cloud exposes, and neither
    cloud will give it a D-Bus socket.
  - SPICE-to-RDP inside Kerbside is the version that works
    against every source Kerbside supports. D-Bus-to-RDP is
    `PLAN-spice-performance.md` phase 4's territory (Son of
    SPICE). If that spike says go, an RDP output on that
    helper is a natural follow-on, but not this plan.
- **Not VNC.** RFB is much simpler to serve, but a network
  that blocks SPICE's port almost always blocks 5900 as
  well. VNC out solves the reachability problem for almost
  no one, so it is Future work at most.

### Feature mapping

| Feature | SPICE side | RDP side | Plan |
|---------|------------|----------|------|
| Display | display channel via the renderer | bitmap updates (RDP 6.0), then RemoteFX | Phase 2 |
| Keyboard | inputs channel, Set 1 scancodes (`docs/spice/scancodes.md`) | fast-path scancode events, also Set 1 with an extended flag | Phase 2 |
| Unicode key events | none | `TS_UNICODE_KEYBOARD_EVENT`, sent by mobile clients | Open question 6 |
| Mouse | client mode needs vdagent or a tablet; server mode is relative | absolute pointer | Phase 2, open question 5 |
| Cursor shape | cursor channel | pointer update PDUs | Phase 2 |
| Clipboard | vdagent over main | cliprdr | Phase 4 (text first) |
| Audio out | playback channel | rdpsnd | Phase 4 |
| Resize, monitors | vdagent monitors config | displaycontrol | Phase 4 |
| Microphone | record channel | audio input (AUDIO_INPUT) | Future work |
| USB, smartcard, file transfer | usbredir, smartcard, webdav | rdpdr and friends | Not planned |
| Multiple monitors | several display channels | multi-monitor layouts | Future work |

## Mission and problem statement

Let a user whose network allows only outbound RDP open a
Kerbside console with a stock RDP client (mstsc, FreeRDP,
Remmina, the Microsoft Remote Desktop apps). They get a
usable desktop with keyboard, mouse, cursor, text clipboard,
audio playback and resize. They connect through the same
brokering, authorisation, audit and termination paths as a
SPICE session, and nothing on the hypervisor changes.

Specifically:

1. Decide, from the prerequisite spike's results, whether
   the approach is viable, and record why.
2. Build a gateway that terminates a SPICE session on its
   backend leg and serves RDP on its client leg.
3. Integrate it with the control plane: an RDP listener,
   credentials derived from console tokens, `.rdp` file
   generation, audit and session termination.
4. Map the virtual channels that are worth mapping.
5. Test it in CI with a real RDP client against the
   direct-qemu harness.
6. Document it as a fallback, with its costs and gaps.

## Open questions

1. **Where does the gateway run?**
   - **Recommended:** a separate binary in the Rust tree
     (`rust/kerbside-rdp-gateway/`), launched one process
     per session. The prerequisite's own risk section already
     calls for a sandboxed per-session worker for anything
     that decodes guest-influenced images. It gives:
     - seccomp confinement;
     - CPU accounting;
     - a clean kill on overload or termination;
     - a crash that takes down one session, not the proxy.
   - The alternatives are a module inside `kerbside-proxy`
     behind a cargo feature (simpler, but it puts decoders
     next to every session's TLS keys) or a separate
     repository (clean, but it duplicates the packaging and
     release machinery for no gain).
   - The open part is who accepts the TCP connection. Either
     the proxy accepts, authenticates and hands the socket
     to a worker, or the worker is spawned per connection
     and asks the proxy to authorise it. Accepting in the
     proxy keeps the listener, TLS configuration and gRPC
     client in one place.
2. **RDP security mode.**
   - Plain TLS ("Enhanced RDP Security" without NLA) carries
     the username and password in the Client Info PDU,
     inside TLS. It is simplest, and IronRDP documents it.
   - NLA (CredSSP) authenticates before the session starts,
     and modern clients prefer it; some deployments require
     it. With NTLM, the server must verify against the
     plaintext secret. Kerbside has that secret: the token
     is stored in the database.
   - `qemu-rdp` claims CredSSP, so IronRDP has at least some
     server-side support. Whether it is complete enough is
     phase 1's to find out.
   - Recommendation: support both. Default to NLA, with
     plain TLS as a per-source or per-deployment option.
3. **What are the credentials?**
   - The natural mapping is username `<session_id>` and
     password `<token>`, both from `create_token`. Tokens
     are already stored, expire, are audited and are
     revocable, and 48 characters is well within RDP's
     limits.
   - `mstsc` cannot take a plaintext password from a `.rdp`
     file: it stores passwords encrypted with DPAPI,
     per user. So the user copies the token into a prompt.
     The web UI and the API must show it, which the SPICE
     path never had to do.
   - Is that acceptable, or do we want a shorter one-time
     code for typing, exchanged server-side for the token?
4. **How does an RDP connection map onto SPICE channels
   for authorisation and audit?** An RDP session arrives as
   one TCP connection. The gateway then opens main,
   display, inputs, cursor and playback to the backend
   itself. Two options:
   - authorise once, with a new `channel_type` value such
     as `rdp`, and register the backend channels the
     gateway opens under the same `session_id`;
   - add a dedicated `AuthorizeRdpSession` RPC.

   Either changes the contract hash (`proxy-architecture.md`,
   "The gRPC contract handshake"). The firewall policy's
   `permitted_channels` should decide which RDP virtual
   channels the gateway enables: no clipboard if the agent
   channel is not permitted, and so on.
5. **Mouse mode.**
   - RDP pointers are absolute. SPICE absolute input needs
     client mouse mode, which the server grants only when
     the guest runs vdagent or has a tablet device.
   - In server mode, the gateway would have to synthesise
     relative motion from absolute positions and hide the
     client's local cursor. That is the familiar "two
     cursors" experience.
   - Is server mode supported (degraded) or refused? A
     cloud image without vdagent is common.
6. **Keyboard edge cases.** RDP clients send a keyboard
   layout id and can send Unicode key events that have no
   scancode. Do we map Unicode events through a keymap (and
   which one), or drop them and say so?
7. **Codecs and licensing.**
   - Bitmap updates with RDP 6.0 compression are universally
     supported and are the first target. RemoteFX is next.
   - IronRDP's QOI codecs are an IronRDP extension that
     Microsoft clients do not speak.
   - H.264 through EGFX (AVC420/444) needs the same patent
     licensing decision the prerequisite raises for openh264.
   - The MS-RDP* specifications are published under
     Microsoft's Open Specifications Promise. Confirm that
     covers a server implementation shipped in the wheels
     before release.
8. **RD Gateway.** Some networks that "allow RDP" actually
   allow only RD Gateway over HTTPS on 443, not 3389. RD
   Gateway (MS-TSGU, over RPC-over-HTTP or WebSocket) is a
   substantial protocol of its own. This plan assumes 3389
   and puts RD Gateway in Future work. Is 3389 enough for
   the networks we care about?
9. **Placement and capacity.** RDP sessions cost CPU that
   relayed SPICE sessions do not. The prerequisite's risk
   section asks how a saturated node behaves and how a
   multi-node deployment places sessions. This plan inherits
   that answer and must not invent a second one.

## Execution

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Go/no-go from the spike, and an RDP prototype outside Kerbside | | Proposed | |
| 2. The gateway: display, keyboard, mouse and cursor | | Proposed | |
| 3. Control plane: listener, credentials, `.rdp` files, audit, termination | | Proposed | |
| 4. Virtual channels: text clipboard, audio playback, resize | | Proposed | |
| 5. CI: an RDP client against the direct-qemu harness | | Proposed | |
| 6. Documentation: operator guide, ports, and the fallback framing | | Proposed | |
| 7. Push audit | | Proposed | |

Nothing starts until `PLAN-spice-performance.md` phase 3 has
reported. Phases 2 and 3 can run in parallel once phase 1
says go. Phase 4 needs both. Phase 5 can start once phases 2
and 3 have merged, and should, so that phase 4 lands against
a working lane.

**Phase 1** is a decision followed by a time-boxed
prototype.
- **The decision.** Read the spike's write-up and decide
  whether its findings on renderer accuracy, CPU per
  session and the worker sandbox support this plan. Record
  that decision, and the reasoning, here.
- **The prototype**, if the decision is go. Build a
  standalone binary outside Kerbside: Ryll's client stack,
  headless, connected to a qemu from the direct-qemu harness,
  feeding a `SurfaceMirror` into an `ironrdp-server`
  `RdpServerDisplay`, with `RdpServerInputHandler` mapped to
  Ryll's inputs channel.
- **What the prototype must show:**
  - that mstsc, FreeRDP and Remmina connect and render;
  - keyboard and mouse work, in both mouse modes;
  - the latency and CPU cost of the extra hop;
  - how far IronRDP's server really goes on NLA, RemoteFX
    and the virtual channels.
- Its output answers open questions 1, 2 and 5 with
  evidence, and ends with phase plans for 2 to 6.

**Phase 2** productises the prototype's display and input
path as the gateway binary.
- **Backend leg.** A lossless backend display leg, exactly
  as the spike built it. Main, inputs and cursor are
  terminated too, rather than relayed.
- **Client leg.** Damage is sent as RDP bitmap updates,
  paced by the client socket. This reuses the spike's
  pacing (write when unsent bytes are under the low-water
  mark, and otherwise keep accumulating damage), so content
  that has been superseded is never sent.
- **Refusals.** It refuses what the spike refused: a second
  display channel, and draw or image types the renderer
  does not implement. For RDP that means refusing the
  session, not falling back to relay, since there is no
  SPICE client to relay to. The refusal carries a reason
  the user sees, through an RDP error info code.

**Phase 3** wires the gateway into the control plane.
- An RDP listener: a new `VDI_RDP_PORT` and
  `PUBLIC_RDP_PORT`, off unless configured. TLS comes from
  the proxy's existing certificate.
- The authorisation path chosen in open question 4, with
  the contract change and regenerated stubs.
- Audit events for connect, authorisation, refusal and
  disconnect, matching the SPICE path's wording.
- Registration in the session-termination registry, so
  that `TerminateSession` kills RDP sessions too.
- A `.rdp` endpoint next to the proxy `.vv` one, for
  example `/console/proxy/<source>/<uuid>/console.rdp`.
  It sets `full address`, `username`, the NLA setting and
  the display defaults. The web UI shows the token to type,
  per open question 3.
- Byte and session metrics labelled by protocol, so that
  RDP sessions are visible separately in Prometheus.

**Phase 4** adds the virtual channels in order of value.
- Text clipboard, both ways, between vdagent and cliprdr.
- Audio playback, from the playback channel to rdpsnd.
- Resize, from displaycontrol to a vdagent monitors
  config, falling back to a fixed size without an agent.
- Each is gated by the session's firewall policy, as
  decided in open question 4.

**Phase 5** adds a CI lane.
- FreeRDP (`xfreerdp` under Xvfb, or `sdl-freerdp`)
  connects through Kerbside to the direct-qemu harness's
  Uncalibrated Sextant guest.
- Assertions use the existing oracle: the on-screen QR
  digest read from a client screenshot, and the serial
  drain for input events.
- Start it advisory and path-filtered, like `rust.yml`.
  Decide whether it joins the smoke tier only after it has
  a flake record, and follow `docs/testing.md` for any
  required-check change.

**Phase 6** documents it.
- A `docs/use-cases/` page, or a section of the existing
  pages, explaining when to use RDP out, what it loses and
  what it costs.
- The new port in `docs/network-ports.md`, and the new
  settings in `docs/configuration.md`.
- A paragraph in `docs/index.md` that squares "Kerbside
  avoids transcoding" with an opt-in transcoding fallback.
- `ARCHITECTURE.md` changes, because a per-session gateway
  worker is a new component.

**Phase 7** is the push audit, below.

<!-- shared-block: plan-push-audit-phase v3 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did.
<!-- shared-block-end -->

## Agent guidance

Phases follow `PLAN-TEMPLATE.md`'s sub-agent execution
model: implementation by sub-agents, review and commits in
the management session. Phases 1, 2 and 3 turn on protocol
semantics (SPICE mouse modes, RDP security negotiation, the
gRPC contract and token lifecycle) and are planned at high
effort. Phases 4, 5 and 6 follow patterns the earlier phases
set, and are planned at medium effort. Rust builds run in
Docker, per the proxy's Makefile, never with a native
toolchain on the host.

## Future work

- **RD Gateway (MS-TSGU) over 443**, for networks that allow
  HTTPS and not 3389 (open question 8). A browser client
  through the master plan's WebRTC out is the other answer
  for HTTPS-only networks. Which one fits better depends on
  whether users there have an RDP client at all.
- **H.264 through EGFX**, once the codec licensing decision
  is made.
- **Microphone** (the record channel to RDP audio input),
  and **multiple monitors**.
- **RDP out from the Son of SPICE helper**, if
  `PLAN-spice-performance.md` phase 4 says go. That helper
  would feed an RDP server from D-Bus directly, as `qemu-rdp`
  does, with no SPICE decode in between.
- **VNC out**, only if a real network is found that allows
  VNC and not SPICE.
- **Auto-reconnect.** RDP clients reconnect with a cookie
  after a network blip. Map that onto a token that is still
  valid, rather than making the user type it again.

## Bugs fixed during this work

None yet.

## Back brief

Before executing any step of this plan, back brief the
operator on your understanding of the plan and how the work
you intend to do aligns with it.
