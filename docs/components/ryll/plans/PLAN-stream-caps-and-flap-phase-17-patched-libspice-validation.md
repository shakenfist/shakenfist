# Phase 17 — Patched libspice-server for hypothesis validation

Phase 17 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).

## Goal

Build a Debian package of `libspice-server1` with
`NUM_TRACE_ITEMS` bumped from 8 to 128, install it on ONE
hypervisor, re-run the 006-style workload, and measure
whether stream re-engagement actually improves. The
package is a validation tool first and an operational
artefact second — if it works, the question of cluster-wide
rollout is a separate scope decision.

## Why this exists

Phase 13A identified `NUM_TRACE_ITEMS = 8`
(`server/display-channel-private.h:23-25`) as the binding
constraint on stream re-engagement under OOM pressure;
phase 13C identified the workload-driven OOM rate as the
trigger. A one-line bump on the spice-server side is the
cheapest server-side mitigation imaginable — IF the
hypothesis is right. The only way to know for sure is to
build the patched package and measure.

Three things this is NOT:

- A commitment to ship the patched package cluster-wide.
  That's a separate decision after 17B has data.
- A replacement for upstreaming. The upstream issue should
  be filed in parallel; the local package is for our test
  timeline, not as a permanent fork.
- A replacement for phase 16 (guest-driver alternatives).
  If 17B is positive, phase 16 becomes optional; if 17B is
  negative, phase 16 escalates.

## Scope and sequencing

**Gated on session 006 results.** Do not start 17A until 006
has been analysed and the trace-ring-contention model is
confirmed. The signature is `006d (fullscreen at 64 MiB)`
showing OOM counts substantially lower than `006c (windowed
at 256 MiB)` — that's the workload-shape-beats-VRAM
prediction, which only matters if the trace ring is the
floor. If 006 instead shows command-ring depth dominates
(006d roughly equal to 006c), then patching `NUM_TRACE_ITEMS`
alone won't help and this phase should pivot or be cancelled.

In scope:

- A standalone build recipe (shell script in
  `ryll-test-sessions/bin/` or similar) that pulls Debian's
  `spice-server` source matching the deployed version,
  applies the one-line patch, and produces a `.deb` for
  the matching architecture.
- A documented install + revert procedure for one
  hypervisor (`sf-2` is the suggested test host based on
  005's per-node activity).
- A re-run of the 006a (windowed, 64 MiB VRAM) workload
  against the patched hypervisor, with bundle capture, so
  the result is comparable to 006a's baseline by direct
  qemu-log diff.
- The drafted upstream issue body (was outlined in
  `PLAN-stream-caps-and-flap-phase-13-streaming-intermittency.md`'s
  13A "Implications for 16 / upstream" section). Filing
  upstream is operator action; the draft text lives here.

Out of scope:

- Cluster-wide rollout. That's phase 18 if we decide it's
  worth doing.
- Long-term package-maintenance automation (renovate-bot
  sync, etc.). Same — phase 18 territory.
- Patching qemu's `QXL_COMMAND_RING_SIZE`. Out of scope
  for the validation; the spice-server patch should be
  sufficient if 13A's model is right. If it isn't, file
  a separate investigation.
- Building the patched package from the ryll repo. The
  build recipe lives in `ryll-test-sessions` (the
  test-harness repo) because it's a hypervisor-side test
  artefact, not a ryll-client artefact.

## Patch value rationale

`RED_RELEASE_BUNCH_SIZE = 64`
(`server/image-encoders.h:221`). A single
`display_channel_free_some` cycle can evict up to 64
drawables, each potentially writing into the trace ring.
For the ring to survive one OOM cycle without being fully
overwritten by unrelated draws we need `NUM_TRACE_ITEMS >
RED_RELEASE_BUNCH_SIZE`, so 128 is the natural next power
of two (the existing code uses `ITEMS_TRACE_MASK =
NUM_TRACE_ITEMS - 1`, so the value must remain a power of
two). Going to 256 buys a small additional margin at
roughly 8 extra KB per display channel — defensible if
something downstream wants headroom, but 128 is the
principled choice for "just survive one OOM cycle".

The patch is therefore:

```diff
--- a/server/display-channel-private.h
+++ b/server/display-channel-private.h
-#define NUM_TRACE_ITEMS (1 << 3)
+#define NUM_TRACE_ITEMS (1 << 7)
```

(Verify the exact form in the Debian source before
relying on this — `(1 << 3)` may be `8` literally, or
wrapped in an enum, depending on the version.)

## Step table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 17A | medium | sonnet | none | **Build the patched .deb.** In `ryll-test-sessions`, add a `bin/build-patched-libspice.sh`. Steps the script should automate (or document if non-automatable): (1) Determine the spice-server version installed on the target hypervisor — `ssh sf-2 dpkg -l libspice-server1`. (2) On a build host (the Kasm machine is fine, or a fresh Debian container — pick whichever the operator prefers; `docker run --rm -it -v "$PWD:/work" debian:12 bash` keeps the host clean), `apt-get source spice-server=<version>` to get the source matching the running version exactly. (3) Apply a one-file patch bumping `NUM_TRACE_ITEMS` from `(1 << 3)` to `(1 << 7)` — store the patch as `patches/0001-bump-num-trace-items.patch` adjacent to the build script. (4) `dpkg-buildpackage -us -uc -b` to build the binary `.deb` files (no source `.deb`, no signing). (5) Drop the resulting `libspice-server1_*.deb` (and any companion -dev / -dbg packages we need) into a `built/` directory in `ryll-test-sessions/`, with a README explaining what's in it and how to install. The script should be idempotent — re-running it should produce a fresh build without manual cleanup. Verify the script runs end-to-end by producing a `.deb` for at least one Debian version. Do NOT push the resulting `.deb` to git (binary artefact); add `built/` to `.gitignore` if not already there. |
| 17B | — | — | — | **Operator validation run.** This is manual operator work, NOT a sub-agent task. The operator: (a) installs the patched `libspice-server1` on `sf-2` (`sudo dpkg -i libspice-server1_*.deb`), (b) restarts libvirtd (`sudo systemctl restart libvirtd`) so qemu re-links the library on the next VM spawn, (c) destroys any existing test VMs on sf-2 and creates a fresh one (`./bin/makeinstance.sh 64`) — important because already-running VMs are linked against the *old* library, (d) runs the 006a-equivalent workload on this fresh VM with auto-snapshot enabled, tag `007a-patched`, (e) captures the bundle, (f) reverts by `sudo apt-get install --reinstall libspice-server1` to pull the distro package back. Detailed instructions go into a `007.md` in the test-sessions repo when 17A's build script is ready — write it as part of 17A's commit. The comparison the bundle should reveal: 007a-patched's `display_channel_create_stream` count vs. 006a's, plus 007a-patched's `display_channel_debug_oom` count vs. 006a's (OOM count should be roughly unchanged — the patch doesn't reduce OOM frequency, it reduces the *consequence* of each OOM). The expected result if the trace-ring-contention model is right: stream re-engagements should be substantially higher in 007a-patched than in 006a despite similar OOM counts. |
| 17C | low | haiku | none | **File the upstream issue + write follow-up plan.** Take the writeup paragraph already drafted in `PLAN-stream-caps-and-flap-phase-13-streaming-intermittency.md` (the 13A "Implications for 16 / upstream" section) and turn it into a properly-formatted upstream bug-report at https://gitlab.freedesktop.org/spice/spice/-/issues — operator does the actual filing; this step produces the issue body as a `.md` file in `docs/upstream-issues/spice-trace-ring.md`. If 17B was negative (the patch didn't help), update the issue draft to reflect what we learned instead — possibly mentioning the command-ring as a secondary hypothesis. If 17B was positive, write `PLAN-stream-caps-and-flap-phase-18-libspice-rollout.md` as a stub for the cluster-wide rollout question (proposed-concept status — actual planning happens later, only if the operator decides rollout is worth the maintenance tail). |

## Success criteria

- A reproducible `bin/build-patched-libspice.sh` that
  produces a `libspice-server1*.deb` matching the
  deployed Debian version on sf-*. Verified by at least
  one successful run.
- A 007.md instruction file in ryll-test-sessions
  documenting the install / run / revert procedure.
- Either: bundle `test-session-007a-patched.tar.gz`
  showing measurably better stream re-engagement than
  006a's baseline (success — record numbers in this
  phase plan file), OR: a written falsification with
  the comparison data and a pivot decision.
- Upstream issue body drafted as
  `docs/upstream-issues/spice-trace-ring.md`.

## Open questions

- **Q1: build host choice.** Kasm machine vs. Debian
  container? The container keeps the Kasm host clean and
  matches the deployed Debian version cleanly; the host
  is faster and avoids docker-in-docker concerns. **Decide
  in 17A**; default is `debian:12` container if the sf-*
  hosts are running bookworm (likely).
- **Q2: dev/dbg companions.** Do we need
  `libspice-server-dev` or `libspice-server1-dbgsym`?
  Almost certainly not for production install, but
  worth grabbing once for symbolisation when reading the
  patched binary's behaviour. **Decide in 17A**; default
  is "build only `libspice-server1`, skip companions".
- **Q3: pinning.** Should the operator pin the patched
  package via `apt-mark hold libspice-server1` after
  install to prevent `apt upgrade` clobbering it? **Yes
  for the validation host during the test window.** Note
  in 007.md.

## Cross-references

- `PLAN-stream-caps-and-flap-phase-13-streaming-intermittency.md`
  — the 13A and 13C findings that motivate this phase, and
  the drafted upstream-issue paragraph that 17C will
  formalise.
- `PLAN-stream-caps-and-flap-phase-16-qxl-viability.md` —
  the alternative path if 17B falsifies the trace-ring
  model. 16's relative priority depends on 17B's outcome.
- ryll-test-sessions `006.md` — the workload shape that
  17B's run replicates against the patched library.
- Future `PLAN-stream-caps-and-flap-phase-18-libspice-rollout.md`
  — written only if 17B is positive AND the operator
  decides cluster-wide rollout is worth the maintenance
  tail.
