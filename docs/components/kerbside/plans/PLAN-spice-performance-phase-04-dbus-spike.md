# Phase 4: Son of SPICE, a D-Bus display feasibility spike

Master plan: [PLAN-spice-performance.md](/components/kerbside/plans/PLAN-spice-performance/).
Planning effort: high, per the master plan. The spike crosses
two repositories (andris and kerbside) and depends on qemu
behaviour that the master plan left untraced.

The master plan's phase 4 section is the brief this plan
refines: its three steps, its design inputs and its output (a
go or no-go, with a proposal for a separate master plan if go)
stand unless this plan says otherwise. What follows grounds it
in the code as of 2026-10-29 and changes the design where the
code disagreed with the sketch.

## Scope

In:
- a qemu platform backend for andris that takes picture,
  damage and cursor from qemu's D-Bus display and sends
  keyboard and mouse back to it, enough for a SPICE client to
  use a guest through Kerbside;
- confirming, at runtime, how promptly and precisely Update
  rectangles arrive for virtio-gpu 2D and std-vga;
- measuring that helper against in-qemu SPICE on the shaped-link
  rig, on the profiles phase 3 used;
- a write-up with a go or no-go, which names the Kerbside
  sources that could use a helper and what each would need.

Out, each with its reason:
- GPU frames (dmabuf scanouts, hardware encode, `gl-mode`). They
  remain design inputs, not work. andris's pipeline compares and
  encodes CPU frames held in a shadow, so a GPU path is a second
  pipeline, not a new source (survey, finding 6).
- QXL. qemu builds it only with SPICE, and its rectangles come
  from spice-server's own `update_area`, so it means nothing
  without spice-server (finding 4).
- Clipboard, audio, resize requests, multiple monitors and
  multiple clients. The master plan's minimal helper is main,
  display, inputs, cursor, LZ4 and one client.
- Changes to Kerbside's proxy or Python code. Kerbside fronts the
  helper exactly as it fronts any SPICE server. If the spike
  finds that it cannot, that is a finding for the write-up.
- Nova, oVirt or Shaken Fist changes to configure a D-Bus
  display. The write-up says what each would need.
- UDP transports, which phase 7 owns.

## What the survey found

The survey read qemu at 73294d096c (11.1.50, 2026-10-28) in
`/srv/nobackups/src-reference/qemu/qemu`, andris at 568a43b
(develop, 2026-10-28), qemu-display at 6ffedb9, libvirt
v12.9.0-rc1, nova c82823f50c, ovirt-engine 0595b9f2fef and the
Shaken Fist and Proxmox (pve-qemu 11.1.3) sources. The master
plan's phase 4 section has been corrected at its source in this
planning commit. Its "not yet traced" claim, and its "build a
minimal helper", are now marked as answered by the survey.

1. **andris already has the seam a D-Bus source needs.**
   - Seven platform traits make up `Platform`
     (`andris/src/platform/mod.rs:543-720`): `DisplayGeometry`,
     `ScreenSource`, `InputSink`, `CursorSource`,
     `DisplayControl`, `Clipboard` and `AudioSource`. None of them
     uses a SPICE type.
   - `x11rb` appears only under `platform/x11/` (about 6,070
     lines).
   - A `--platform synthetic` backend (`platform/synthetic/`,
     about 1,600 lines) already proves that the seam works.
   - Damage handling is generic (`platform/damage.rs`: the
     `DamageHub` and 64-pixel tiles).
   - **Step 2 of the master plan is therefore a new
     `platform/qemu/` in andris, not a new program.** The survey
     estimates 1.5k to 2.5k lines, with converting qemu's push
     into andris's pull as the main design point.
2. **andris paces on an ACK window, not on the socket.**
   - The window is 20 (`server/channel.rs:38`), held at more
     than twice unacked.
   - Frames are at least `--frame-interval-ms` apart (default
     33, minimum 20).
   - `TCP_NOTSENT_LOWAT` is set only on playback.
   - Phase 3 found socket pacing up to two times better than an
     ACK window where the link was the limit. That stays
     andris's Future work item; this spike measures andris as it
     is.
   - andris serves one client at a time. A new connection
     replaces the old one.
   - Auth is a SPICE ticket from a mode-600 file.
   - andris's CI already runs ryll through the kerbside demo
     stack to andris (`.github/workflows/ci.yml:385`,
     `make e2e-kerbside`).
3. **The D-Bus interface covers what the helper needs.**
   - **Picture.** Listener `Scanout`/`Update` carry pixels as
     `ay` (`ui/dbus-display1.xml:341,365`). `Unix.Map`
     `ScanoutMap`/`UpdateMap` share a memfd and then send bare
     rectangles (:484-528).
   - **Input and cursor.** Keyboard takes qnum codes (:171).
     Mouse has absolute, relative and button methods (:223).
     `CursorDefine` carries ARGB (:451).
   - **Registration.** A listener registers with
     `Console.RegisterListener(fd)`, which gets its own
     peer-to-peer connection (`ui/dbus-console.c:259-335`).
   - **Several listeners may attach at once** (xml :68).
   - With `-display dbus,p2p=on`, the controlling client arrives
     through QMP `add_client`, one at a time (`ui/dbus.c:370`).
4. **Update rectangles, per device, from reading.**
   - **No merging, and no 32-pixel columns.** The listener
     neither merges nor splits rectangles, so there is no
     equivalent of `ui/spice-display.c`'s 32-pixel column diff
     (`ui/dbus-listener.c:849`).
   - **virtio-gpu 2D.** `resource_flush` passes the guest's
     rectangle straight to `qemu_console_update`, with no timer
     (`hw/display/virtio-gpu.c:666-689`). Its resources are
     memfd-backed since 9.2, so `Unix.Map` applies.
     - Precision is limited by the guest, which unions each
       commit's damage into one box
       (`drivers/gpu/drm/virtio/virtgpu_plane.c:271,312`).
     - The guest kernel's damage-clip bug from phase 2 matters
       here as it did for spice-server.
   - **std-vga and bochs-display.** These are polled every 30 ms
     against the dirty bitmap. Each poll sends a band of whole
     scanlines at full width (`hw/display/vga.c:1698-1770`).
     - The surface points into VRAM and has no share handle, so
       every update is copied over the socket.
   - **ramfb.** Sends a full Scanout every 30 ms.
   - **QXL.** Depends on SPICE (`hw/display/Kconfig:98`).
   - **virtio-gpu with GL.** Sends the full scanout regardless
     of what the guest flushed (`virtio-gpu.c:631`).
5. **Promptness and backpressure, from reading.**
   - **No backpressure.** `Update`, `UpdateMap` and `Scanout` are
     sent with no reply expected
     (`dbus-listener.c:825,843,866`), so a slow listener does not
     slow the guest. Undelivered messages queue. The survey
     believes, from GLib rather than this tree, that the queue
     is unbounded.
   - **Shedding.** The only shedding is that a new `Scanout`
     drops queued display messages (`dbus_filter`, :1212-1260).
   - **Page flips block.** `ScanoutMap` is a blocking call
     (:574), made after every surface switch. A virtio-gpu 2D
     page flip creates a new surface (`virtio-gpu.c:789-815`), so
     each flip costs a round trip in qemu's main loop. This was
     read from the code, not measured.
   - **Tearing.** On the map path the listener reads a buffer
     the guest may already be overwriting. That is a tearing
     risk, also inferred.
   - **GL mode.** Here `UpdateDMABUF` blocks the console until
     the slowest listener replies (`dbus-listener.c:262`,
     1000 ms timeout). That is one more reason GL is out of this
     spike.
6. **No component handles a GPU frame.**
   - andris has no dmabuf path, and its encoder takes CPU BGRX
     from the shadow.
   - qemu-display's RDP and VNC servers stub their DMABUF
     handlers.
   - The master plan's design inputs (frame on the GPU,
     hardware encode) therefore need a second pipeline whatever
     this spike finds.
7. **Upstream is building exactly this, in other protocols.**
   - **In qemu.** qemu 11.1 ships `tools/qemu-vnc` (3a453266e3,
     about 2,800 lines), a standalone VNC server on this
     interface. It uses `Unix.Map` and accepts p2p fds.
   - **qemu-display.** Marc-André Lureau's qemu-display project
     (MIT, WIP, last commit 2026-09-02) has `qemu-display` zbus
     bindings for every Listener interface, plus `qemu-rdp` on
     IronRDP. On crates.io the bindings are 0.2.1, updated
     2025-07-30, which lags the repository.
   - **libvirt.** libvirt has had `<graphics type='dbus'>`
     since 8.4.0, and RDP through qemu-rdp since 11.1.0, only
     with non-p2p D-Bus (`docs/formatdomain.rst:7178,7216-7246`).
   - **Nobody has a SPICE server on it.** No SPICE server on the
     D-Bus display exists. Neither ryll nor andris has any D-Bus
     code. Ryll's `PLAN-rdp-frontend.md:300-326` already discusses
     attaching the way qemu-rdp does.
8. **Most of Kerbside's sources cannot configure a D-Bus display
   today.**
   - **OpenStack.** Nova's libvirt driver handles only VNC and
     SPICE (`nova/virt/libvirt/driver.py:5130,5149`).
   - **oVirt.** `GraphicsType` is SPICE or VNC
     (`GraphicsType.java:5-8`), and vdsm has no D-Bus.
   - **Shaken Fist.** It writes its own libvirt template
     (`deploy/collection/roles/hypervisor/files/libvirt.tmpl:156-174`),
     so it could add a type.
   - **Proxmox.** It ships qemu 11.1 but reaches a D-Bus display
     only through a VM's raw `args:`.
   - A go therefore helps first the sources we control, and needs
     upstream work in Nova and oVirt before it helps theirs.
9. **The qemu on this host can run the spike.**
   - The host has Debian 13's qemu 10.0.13, which ships
     `ui-dbus.so` (`qemu-system-modules-opengl`).
   - 10.0 has `Unix.Map` and memfd surfaces.
   - It lacks `ScanoutDMABUF2` (10.1) and `SetUIInfo` (not yet
     released), neither of which the spike needs.

## Decisions

1. **The helper is andris with a qemu platform.**
   - Finding 1 makes that a new backend rather than a new
     program.
   - andris already has the SPICE side the master plan's step 2
     asked for: main, display, inputs, cursor, LZ4, one client.
     It also has the kerbside end-to-end lane.
   - Writing a second SPICE server to test D-Bus would test our
     ability to write SPICE servers.
2. **It lands in andris behind a non-default cargo feature,
   `qemu`.**
   - This mirrors kerbside's `transcode`: andris's wheels and
     its default dependency tree do not change during the
     spike.
   - andris's own rules govern the pull request (`AGENTS.md`,
     `PUSH-AUDIT.md`). Per the push-audit shared block, it is
     audited in its own pull request, and this plan's phase 6
     cites that audit.
3. **The spike uses the `qemu-display` bindings crate, pinned.**
   - It is MIT, which is compatible with andris's Apache-2.0. It
     covers every interface the helper needs, and it already
     runs on tokio in qemu-rdp.
   - Step 1 pins crates.io 0.2.1 if that release has the
     `Unix.Map` listener. Otherwise it pins a git revision and
     says why.
   - Writing our own zbus proxies from `dbus-display1.xml`
     stays open for a productised helper, and the write-up
     answers which.
   - This is the decision a reviewer is most likely to dispute:
     a dependency on a single maintainer's WIP crate, for code
     that would sit in front of every session.
   - For a spike it is the right trade. The bindings are thin,
     generated-shaped code, and replacing them later is
     mechanical. Generating our own now would spend the spike's
     time on plumbing that tells us nothing about feasibility.
4. **Attach with `-display dbus,p2p=on` and QMP `add_client`.**
   - On the rig the helper owns its qemu's QMP socket and passes
     qemu one end of a socketpair. No bus daemon sits in the
     path, which is the shape of a helper launched next to its
     qemu.
   - Under libvirt the precedent is the other mode. libvirt's
     `<graphics type='rdp'>` requires a non-p2p `dbus` graphics
     and launches qemu-rdp as an external helper on the VM's bus
     (`docs/formatdomain.rst:7172-7182` in libvirt). Step 1
     reads how libvirt does that, and says whether andris needs
     a bus-address attach mode alongside p2p. It answers by
     reading libvirt rather than by building the integration.
5. **The map path is the main path.**
   - The backend implements `ScanoutMap`/`UpdateMap`, with plain
     `Scanout`/`Update` as the fallback that std-vga needs.
   - It keeps its own copy of the framebuffer and serves
     `capture()` from it. andris pulls while qemu pushes, and the
     copy also bounds the tearing in finding 5 to one snapshot.
   - Step 1 measures what that copy costs.
6. **The step order changes, and step 1 becomes a gate.**
   - The master plan traces Update rectangles before building
     the helper. Reading has answered the "through every
     device's refresh path" half (finding 4).
   - What reading cannot answer is runtime promptness, and
     measuring that needs a listener. So step 1 builds the
     backend's picture half and measures with it.
   - Step 1 then stops for review. If rectangles arrive late or
     imprecise enough to sink the idea, steps 2 to 5 do not run.
   - The thresholds are in step 1's brief.
7. **The comparison is against in-qemu SPICE as Kerbside relays
   it today.**
   - Configs, all through the proxy with the same ryll:
     - `streaming-video=off` and `=all` against stock qemu with
       `-spice`;
     - andris on `-display dbus`, on a virtio-gpu 2D guest.
   - One std-vga row shows the polled path.
   - Profiles and activities are phase 3's, so its tables line
     up with this phase's:
     - LAN and 80 ms / 10 Mbit, each with idle, busy and heavy
       activity;
     - 20 ms / 50 Mbit, 80 ms / 50 Mbit, 80 ms / 5 Mbit and
       150 ms / 2 Mbit, each with busy and heavy activity.
   - Metrics:
     - phase 3's keypress-to-draw latency;
     - host CPU and RSS for qemu plus helper, against qemu alone;
     - quality from `quality.py`.
8. **The verdict has stated criteria.** Go needs all three:
   - **Latency.** The helper is level with or better than the
     better of the two SPICE configs in most cells. It must not
     be worse than `streaming-video=off` in any cell.
   - **Cost.** Host CPU is within a factor of two of in-qemu
     SPICE at idle and under the heavy workload.
   - **Reachable sources.** At least one Kerbside source can
     attach the helper without upstream work in another project.
   - A no-go still says which criterion failed and whether
     anything would change that. The verdict also says what the
     Future work items held for phase 4 (items 2, 6, 7 and 8)
     should do next.

## Steps

andris steps land as an andris pull request, following andris's
`AGENTS.md`, in an andris worktree on a branch named
`qemu-dbus-platform`. Kerbside steps land in this phase's
worktree. Rust builds run through each repository's Makefile in
Docker, never with a host toolchain. Guests run under KVM on
this host. No step touches a live system.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | andris worktree | andris: the qemu platform's picture half, plus the runtime trace. It stops for review. Commit (andris): "Add a qemu D-Bus platform: display and damage." See brief notes. |
| 2 | high | opus | andris worktree | andris: input, cursor and tests. Then the andris pull request with its push audit. Commit (andris): "Drive input and cursor through qemu's D-Bus display." See brief notes. |
| 3 | high | opus | none | kerbside: teach the shaped-link rig a `helper` config, with qemu on `-display dbus` and andris attached behind the proxy, and `ANDRIS_REF` pinned. Commit: "Teach the shaped-link rig to run a D-Bus helper." See brief notes. |
| 4 | high | opus | none | kerbside: run the matrix and write the results page. Commit: "Measure a D-Bus SPICE helper on the shaped-link rig." See brief notes. |
| 5 | high | opus | none | kerbside: the write-up and verdict, plus the master plan updates. Commit: "Write up the D-Bus display spike." See brief notes. |

Order:
- **Step 1 runs first and gates the rest.**
- **Steps 2 and 3 may run in parallel once step 1 is approved.**
  They write to different repositories, and step 3 needs only
  step 1's command line.
- **Step 4 needs both.** It also needs andris's pull request
  merged, or else a pinned andris commit that the results page
  names.
- **Step 5 needs step 4.**

### Brief notes

**Step 1.** Read this plan's survey first. Then:

- **Build the platform's picture half.**
  - Add `platform/qemu/` to andris behind a `qemu` cargo
    feature. Implement `DisplayGeometry` and `ScreenSource`.
  - Attach per decision 4: `--platform qemu --qmp <path>`, or
    whatever andris's `config.rs` conventions suggest.
  - Register a listener with the `Unix.Map` interface
    advertised. Keep a framebuffer copy (decision 5).
  - Feed `Update`/`UpdateMap` rectangles into `DamageHub`.
  - Handle `Scanout` and `ScanoutMap` size changes through the
    existing `start_surface` path.
  - Convert any pixman format other than x8r8g8b8, or refuse it
    with a clear error.
  - Decide whether the 0.2.1 crate has `Unix.Map` (decision 3),
    and say which you pinned.
- **Trace it, on this host's qemu 10.0.13**, with a virtio-gpu
  2D guest and a std-vga guest. Use the shaped-link rig's guest
  image (`tools/shaped-link/` in kerbside). Record:
  - the time from qemu's `virtio_gpu_cmd_res_flush` trace event
    to the listener receiving the rectangle (qemu `-trace`
    against the helper's own monotonic timestamps), p50 and p99,
    idle and under the rig's heavy workload;
  - whether rectangles match what the guest flushed;
  - the framebuffer copy's cost per update;
  - the blocking `ScanoutMap` round trip on page flips;
  - qemu's RSS when the helper stops reading for 10 s, which
    tests the unbounded queue (finding 5).
- **Read libvirt** (`/srv/nobackups/src-reference/libvirt/libvirt`) to
  answer decision 4's libvirt question.
- **The gate.** Recommend stopping if any of these holds:
  - virtio-gpu flush-to-listener p99 above 5 ms while the host
    is otherwise idle;
  - rectangles that are not the guest's damage;
  - qemu RSS growing without bound while the listener stalls,
    with no way for the helper to shed.
  Report the numbers either way.
- **Checks.** Run andris's tests (`make` targets per its
  `AGENTS.md`), clippy with and without `--features qemu`, and
  `pre-commit run --all-files`. Confirm that `cargo tree` without
  the feature has no `zbus`.
- Leave the work uncommitted, and report in at most 600 words.

**Step 2.** On step 1's branch:

- **Implement the rest of the platform.**
  - `InputSink`: map evdev to qnum, using the bindings'
    `keycodemap` or an equivalent table with its source named.
    Handle absolute and relative mouse, choosing from the
    Mouse `IsAbsolute` property. Handle buttons.
  - `CursorSource`, from `CursorDefine` and `MouseSet`.
  - `DisplayControl` and `Clipboard`, which may refuse as
    unsupported.
  - `AudioSource`, which may report no audio.
- **Tests.**
  - Unit tests for the keymap and the framebuffer adapter.
  - An in-process test that plays qemu's side of a p2p
    connection: it sends `ScanoutMap` and `UpdateMap` and
    asserts the captured pixels, and asserts that a
    keypress arrives as the right qnum.
- **Mutation-check** the adapter and the keymap with copies, not
  in place, each mutant under `timeout`.
- **Documentation.** Document the platform in andris's
  `docs/running.md`, including its limits: the feature, 2D
  only, one head, no clipboard or audio.
- **Open the pull request.** Run andris's `PUSH-AUDIT.md` over the
  branch, put its result in the PR body, and open the andris pull
  request if the session has permission to; otherwise stop at the
  push.
- Report in at most 500 words.

**Step 3.**
- **Add a `helper` config** to `tools/shaped-link/`:
  - start qemu with `-display dbus,p2p=on` instead of `-spice`;
  - start andris built at `ANDRIS_REF` (in Docker, like
    `RYLL_REF`) with `--platform qemu`;
  - point the proxy's backend at andris's listener, with a
    ticket;
  - run the same ryll.
- **Keep the old invocation unchanged.** With no new variables
  set, `run-matrix.sh` must produce the same files as before.
- **Add a `GUEST_VGA` variable** (virtio, std) for the std-vga
  row.
- **Sample CPU and RSS for qemu and andris separately.**
- **Smoke test.** Run one short repeat per config at LAN to prove
  the plumbing, and check that ryll draws.
- Leave the work uncommitted, and report in at most 400 words.

**Step 4.**
- **Run** decision 7's matrix, with phase 3's repeat counts.
- **Write `docs/performance/dbus-helper-measurements.md`** in
  the style of `transcoding-measurements.md`:
  - for every profile and config: latency p50 and p95, CPU in
    cores for qemu and the helper, peak RSS, and quality;
  - the qemu, andris, ryll and proxy commits measured;
  - caveats, including crashes, and the spice-server 0.15.2
    connect crash if `all` hits it.
- **Store images and summaries** under
  `docs/performance/dbus-helper/`, as phase 3 did.
- Report in at most 400 words.

**Step 5.**
- **Write `docs/performance/dbus-display-spike.md`.** It covers:
  - the verdict against decision 8's criteria;
  - steps 1 and 4's evidence;
  - the libvirt attach answer;
  - which Kerbside sources could use a helper, and what each
    would need (finding 8);
  - whether the bindings should be ours (decision 3);
  - what a GPU-frame path would add (finding 6);
  - how this relates to qemu-vnc, qemu-rdp and libvirt's RDP
    mode;
  - what the Future work items held for phase 4 do next.
  If go, add a proposal for a separate master plan.
- **Update the master plan's** phase 4 section, the Future work
  items that depended on phase 4, and the `index.md` row.
- Keep docs outside `docs/plans` free of plan and phase
  citations and of people's names.
- Report in at most 400 words.

## Risks and mitigations

- **andris's own plan is between human review and its push
  audit.** A platform landing then is unrelated work in that
  window.
  - Mitigation: decision 2's own pull request carries its own
    audit, so andris's phase 12 audit can treat it as outside
    its range.
  - The back brief asks whether andris should take it now.
- **The bindings crate drifts or stalls.** The pin and decision
  3's exit cover the spike. The write-up answers the productised
  case.
- **Map-path tearing goes unnoticed.** Step 4's quality score
  compares against a lossless reference, so a torn frame that
  persists shows as a lower frozen-frame score. Step 1 states
  what the copy guarantees.
- **The unbounded queue.**
  - A helper that stalls could grow qemu's memory, and in
    production that is a hypervisor process.
  - Step 1 measures it, and the gate stops the spike if nothing
    can shed.
- **The guest kernel's damage-clip bug.**
  - The bug makes virtio-gpu guests report full-plane damage,
    which would flatter neither config.
  - Mitigation: the rig's guest kernel is the one phase 3 used.
    Step 1 checks rectangle precision directly.
- **The comparison is unfair to SPICE's streams.** Every `all`
  run so far sent H.264 that ryll did not decode (ryll#398).
  Step 4 states this, as phase 3 did, rather than presenting
  `all`'s figures as a decoded client's.
  - *Corrected by step 4:* spice-server sent MJPEG (codec 1),
    not H.264, in all 28 of step 4's `all` repeats, with the same
    ryll (0d140b3) and an unchanged proxy, and ryll decoded the
    16 of each repeat's 21 streams it accepted. andris sent H.264
    (codec 3) in all 14 helper repeats, which ryll decoded. Why
    spice-server's codec differed from the earlier pages was not
    established; it is a master plan Future work item, and those
    pages are left as written.
- **The spice-server 0.15.2 connect crash** kills qemu on `all`
  runs. The rig already delays guest activity past the connect.
  Step 4 records any recurrence.
- **A go that nobody can deploy.** Decision 8's third criterion
  makes reachability part of the verdict rather than a footnote.

## Definition of done

- Step 1's results are recorded in the write-up, with numbers:
  - flush-to-listener p50 and p99 for virtio-gpu 2D, idle and
    busy;
  - whether rectangles match the guest's damage;
  - qemu's RSS under a stalled listener;
  - the `ScanoutMap` round trip.
- andris has a `qemu` platform behind a non-default feature.
  - `cargo tree -e normal` without `--features qemu` lists no
    `zbus`.
  - An in-process test drives `ScanoutMap`/`UpdateMap` and a
    keypress.
  - The andris pull request carries its push-audit result.
- `run-matrix.sh` with no new variables produces the same set of
  files as before step 3.
- `dbus-helper-measurements.md` reports the following for every
  profile and config:
  - latency p50 and p95;
  - CPU for qemu and helper separately;
  - peak RSS and quality;
  - the qemu, andris, ryll and proxy commits.
- The write-up gives go or no-go against decision 8's three
  criteria, one by one. It names, for OpenStack, oVirt, Shaken
  Fist and Proxmox, what each would need to attach a helper.
- This phase changes no Kerbside product code:
  `git diff <base>..HEAD --stat -- kerbside rust` is empty.
- The master plan's Execution row records every merge, kerbside
  and andris, in `Merged`.

## Execution notes

- 2026-10-29 (AEDT): at planning, back-brief point 1 was
  answered: the qemu platform lands in andris now, behind its
  `qemu` feature, as its own pull request with its own push
  audit (decision 2).
- 2026-10-29 (AEDT): the rest of the back brief was answered.
  - The pinned `qemu-display` crate is accepted (decision 3).
  - Decision 8's criteria stand as written, with reachability
    a hard criterion.
  - The session runs every step and opens the andris and
    kerbside pull requests. The management session reviews step
    1's gate and stops early only if it fails.
- Step 1's precision check compares the listener's rectangle
  with the guest's flush (qemu's `virtio_gpu_cmd_res_flush`
  trace), not with ideal damage. The rig's guest uses the host
  kernel (6.12) with the stock `drm_kms_helper`, as phase 3 did,
  and that kernel has the sticky `ignore_damage_clips` bug, so
  the guest may flush the full plane. What the guest flushes is
  reported separately.
- 2026-10-29 (AEDT): step 1 landed on andris's
  `qemu-dbus-platform` branch as 9fb1d14, and the management
  session passed its gate. (The branch was rebased onto andris
  develop before it was pushed, so the same patch is c808297 in
  the merged history.)
  - **Promptness.** virtio-gpu 2D flush-to-listener was 0.20 ms
    at p50 and 0.25 to 0.28 ms at p99, idle and busy, against a
    5 ms limit. The host was not idle (load average 5 to 21 on
    16 cores).
  - **Precision.** Every listener rectangle matched the guest's
    flush (145, 1017 and 1017 of each), so qemu does not alter
    the flush. The guest itself always flushes the full
    1280x800 plane, because of the damage-clip bug.
  - **Memory under a stalled listener.** The third condition
    tripped on two paths but not on the rig's: qemu's memory was
    flat during a 10 s stall on the virtio map path without
    flips.
    - On std-vga, and on a guest that flips pages, qemu grew by
      184 to 262 MiB/s and reached 2.0 to 2.8 GiB.
    - A stalled `ScanoutMap` froze a flipping guest for 1 s and
      left that listener on full-frame `Scanout` for good.
    - The helper can shed by dropping and re-registering its
      listener, which frees qemu's queue at once. The gate
      required that no way to shed exist, so it passes.
  - **What the remaining steps carry.** The std-vga row is for
    information only. A productised helper needs a lag
    watchdog, which the write-up states.
  - **The libvirt answer** (decision 4): libvirt owns QMP, so a
    helper there needs either an inherited-fd attach mode
    (`p2p=yes`, through `virDomainOpenGraphicsFD`) or a
    bus-address mode (non-p2p). libvirt launches only qemu-rdp,
    not an arbitrary helper.
  - **Crate pin.** Step 1 pinned qemu-display at git 6ffedb9
    rather than crates.io 0.2.1. The 0.2.1 release links the
    system's libusbredirhost and libusb unconditionally.
  - Step 1's remaining numbers, from its trace: copying a
    full-plane update into the helper's screen cost 0.65 to
    0.73 ms at p50. On a flipping guest the `ScanoutMap` call
    reached the helper 0.22 ms after qemu's update at p50
    (0.52 ms at p99), the helper took 0.93 ms (1.46 ms) to handle
    it, and flush-to-listener p99 rose to 5.2 ms.
- 2026-10-29 (AEDT): step 2 landed as shakenfist/andris#56,
  merged as 9a997a3. Its branch commits were c808297 (display
  and damage), 440b782 (input and cursor), 957453a (deny.toml
  ignores for zbus 5's unmaintained advisories,
  RUSTSEC-2026-0370/0371/0372), f450a3d (review round 1: copy
  the shared screen through raw pointers, row by row) and
  89dc839 (review round 2: the advisories mirrored into
  `.cargo/audit.toml`, EINTR retried and zero-byte sends refused
  in the QMP sendmsg).
  - The automated review ran two rounds and stopped: 3 fix,
    1 document and 5 consider, then 2 fix and 8 consider, both
    fix items aimed at code round 1 added. CI passed on 89dc839.
  - The push audit, in the PR body: style passed, nothing
    blocking. Its medium finding, holding QMP for andris's
    lifetime, was fixed in the second commit. Declined: sharing
    the counted input queue with X11; that qemu does not seal
    its memfds against shrinking, which only qemu itself could
    exploit; a CI test driving a real qemu, since CI has no KVM.
- 2026-10-29 (AEDT): step 3 landed as 177fb7e and step 4 as
  8882314. Step 4 measured andris 440b782, before the review
  rounds; the merged per-update row copy out of shared memory
  (f450a3d) was not measured.
- 2026-10-29 (AEDT): step 5 wrote
  `docs/performance/dbus-display-spike.md`. **Verdict: no-go,
  as measured.**
  - Latency fails: level or better in 9 of 14 cells, but worse
    than streaming off in four (80 ms / 10 Mbit heavy, both
    80 ms / 5 Mbit cells, 150 ms / 2 Mbit busy).
  - Cost passes: at most 1.27 times the cheaper spice-server
    config, idle and heavy.
  - Reachability passes narrowly, on Shaken Fist.
  - The latency failure is andris's stream heuristic, which
    counts frames sent and so never fires once the ACK window
    spaces them over 200 ms, plus its message-counted window. It
    is not structural to the D-Bus display, and the master plan's
    Future work records the andris changes and the re-measure
    that would decide whether the verdict turns.

## Back brief

Before executing any step, back brief the operator. The points
that need an answer:

1. **Is andris the right home now?** Its x11-desktop plan is in
   human review, with its push audit pending. The alternatives
   are a branch that waits for that plan to close, or landing now
   with its own audit (decision 2).
2. **Is the `qemu-display` crate acceptable for the spike**
   (decision 3)?
3. **Are decision 8's go criteria right?** The reachability
   criterion in particular could turn a technically better helper
   into a no-go.
4. **May this session open the andris pull request, and the
   kerbside one?**
