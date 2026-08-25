# Streaming test automation

**Status: Proposed (concept). No phases drafted yet.**

## Why this exists

The 002 and 003 dogfood cycles produced rich signal but
needed me at a keyboard each time to provision an instance,
SSH a guest, play a video, file bug reports, and commit
session bundles. That cadence is fine for exploratory bring-
up of new features — the human-in-the-loop interpretation
is half the value — but is wrong for regression coverage
once those features stabilise.

Concretely: once phase 6 (H.264) ships and the streaming
heuristic is understood (phase 13), we want CI to catch
"streaming used to work at 1024×768 with this guest config
and now it doesn't" without anyone having to notice.
Today's manual cadence catches that only when someone
remembers to retest the right scenario.

## What would land in CI

The minimum useful thing: a job that boots a known-good
guest, runs ryll's `--headless` mode against it for a fixed
workload, dumps the same channel-state.json our auto-snapshot
mode produces, and asserts on a small set of structural
properties (`streams_created_total >= 1`,
`mjpeg_decode_failed_count == 0`,
`image_cache_bytes <= image_cache_cap_bytes`, etc.).

What this implies:

- A way to provision a SPICE server in CI. Three plausible
  paths:
  - **(a) Real guest in CI.** Run qemu in the CI runner
    with nested KVM. Expensive (slow boot, needs nested
    virt available on the runner, image storage), but
    fully faithful — same code paths the dogfood
    sessions exercise.
  - **(b) Mock/synthetic SPICE server.** ryll's `--web`
    mode already has a synthetic source path for the
    encoder side; the reverse — a synthetic SPICE
    server that emits predetermined draws on a port — is
    the read-side equivalent. Would need writing.
  - **(c) Pcap replay.** We're collecting real session
    pcaps in the test-sessions repo already. A replayer
    that reads a captured server-side pcap and serves
    its draw messages to a client would let us assert
    deterministic behaviour against frozen wire data.
    Tightly tied to ryll's understanding of the
    protocol but exercises real bytes; doesn't need
    qemu at all.
- A workload driver inside the guest. For (a), an
  in-guest agent or cloud-init runcmd that plays a fixed
  video at session start. For (b)/(c), workload is
  embedded in the synthetic source / pcap respectively.
- A structural-properties assertion harness. The
  channel-state.json schema is stable enough that we can
  write a small Python or Rust script that loads a
  snapshot and asserts on N fields, run as the final CI
  step.
- A way to fail loudly without flaking. The streaming-
  heuristic intermittency observed in 002/003 means
  "stream created" can't be a single-run assertion; need
  several runs and a quorum, or pin the workload to a
  scenario that's deterministic enough not to flap.

## What would NOT land in CI

The exploratory, observe-then-interpret workflow stays
manual. CI is for regression coverage of known-good
behaviour, not for hunting down new bugs. Sessions like
003 (which discovered the resolution sensitivity) are
the kind of work that needs a human reading the bundle.

## Suggested first step (when we get there)

Pick option (c) — pcap replay — as the smallest first
slice. We already have a library of captured server-side
pcaps; adapting one of them into a "replay server" that
ryll connects to needs no qemu, no nested virt, no guest
VM image, no CI-runner-VAAPI questions. The harness would
boot ryll-headless, point it at the replay server, and
assert on the resulting auto-snapshot. Even a single
"this pcap should produce ≥1 stream" assertion would
have caught the phase 6 wiring before it landed.

## A different shape: input record-and-replay + dedicated CI hardware

A complementary approach (not alternative — they answer
different questions). **Pcap replay** tests "given THIS
server behaviour, does ryll do the right thing?" — frozen
wire data, deterministic, but doesn't exercise the
server+guest+ryll loop. **Input replay** tests "given
THESE user inputs, does the full client+server+guest
combination produce the expected state?" — exercises the
real loop, closer to production behaviour, but requires
real infrastructure.

Sequencing toward this:

1. **Now-cheap: record inputs to session bundle.**
   ryll already knows every keypress and mouse event it
   sends (the inputs channel handler is the funnel).
   Log them to a JSONL alongside the auto-snapshots.
   Useful immediately for human review of bug reports
   ("the operator clicked here, then typed `top`, then
   …"), even before any replay machinery exists. Tiny
   feature, possibly justifying its own small phase.

2. **Later-medium: open-loop input replay** at wall-clock
   timing, with protocol-level wait points so a click
   doesn't fire until the display channel reports N
   frames received / a specific surface state / an
   image_ready_lag settle. Combined with a known-good
   guest snapshot + dedicated CI hardware (one physical
   box in the homelab running the workload guest; the
   build runner runs ryll itself), this becomes a real
   behavioural CI test that exercises the full loop.
   Assert on `channel-state.json` properties, NOT on
   pixels — sidesteps visual brittleness while still
   testing decode + stream + cache behaviour.

3. **Later-harder: AT-SPI-based semantic replay.** The
   wall-clock + wait-point replay above is brittle when
   the UI shifts. AT-SPI (the Linux accessibility
   framework) lets you reference widgets by label
   ("click the button labelled 'Send'") instead of by
   coordinates. Requires an in-guest helper to expose
   AT-SPI over a channel ryll can reach (extension to
   vdagent, or a tiny standalone TCP daemon, or
   gdbus-over-SSH from outside the guest). Higher
   effort but makes input replay robust AND opens an
   interesting parallel: an **MCP server backed by
   AT-SPI** lets an agent reason about UI state during
   a remote SPICE session. "What's the title of the
   current window?" "Is there a dialog open?" "What
   buttons are visible?" — all answerable without
   screenshot-and-OCR. That's a genuinely interesting
   agent surface, distinct from the visual approach,
   and dovetails with future MCP-tooling work.

The AT-SPI coverage caveats are real: it's great for
GTK/Qt apps, partial for terminals, useless for games
or video players. So input-replay-with-AT-SPI works for
"clicks through a settings dialog" and not for "plays a
YouTube clip." For the video-streaming workloads that
dominate the current ryll dogfood cycles, wall-clock
replay + protocol-level wait points is the right tool;
AT-SPI is for the next class of tests (clipboard,
USB-redirect interaction, vdagent behaviour, future
input-method tests).

### Why a separate stub isn't worth it yet

All three of these approaches are answering "how do we
test ryll programmatically?" — they belong in the same
conceptual home and the decision tree across them
should live in one plan, not three. When (and if) one
of them gets enough commitment to start phasing, the
detail moves to a dedicated plan file then.

## When to plan this in detail

After the 002/003 cycle wraps and phase 13 (streaming
intermittency investigation) has produced a clear
characterisation of which scenarios are deterministic
enough to bet a CI test on. Trying to write CI assertions
against a streaming heuristic we don't fully understand
yet would just produce flaky tests.

## Push audit

Whenever this plan does grow phases, the last of them runs
`PUSH-AUDIT.md` over the accumulated diff of all of them
against `develop`, not the last phase's diff alone. Findings
land as their own PR against `develop`, recorded here under
an *Items deferred from the push audit* heading — the shape
`PLAN-web-frontend.md` uses for its *Items deferred from the
post-Phase-N pre-push audit* sections, minus the phase number,
because this phase audits the whole plan rather than a range
of it. The plan is not complete until each one is fixed or
declined in writing. If the audit finds nothing, record that
here in one sentence.

Those phases will have merged by then, so `develop...HEAD`
is empty on the audit branch and the accumulated diff has to
be assembled from their merge commits. Record each phase's
merge commit alongside its phase as that phase lands — in a
`Merged` column, once these phases are a table — and keep it
out of the `Status` column, which holds one vocabulary term
and nothing else. *Two ways this runbook is invoked* in
`PUSH-AUDIT.md` has the rest.

## Cross-references

- `docs/plans/PLAN-stream-caps-and-flap-phase-13-streaming-intermittency.md`
  — the investigation that has to land before this is
  worth planning in detail.
- `docs/plans/PLAN-ci-platform-matrix.md` — covers
  platform / OS coverage. This plan is orthogonal: it
  covers the *behavioural* test surface, not the
  build/run surface.
- The ryll-test-sessions private repo (`sessions/*.tar.gz`)
  — the corpus that a pcap-replay approach would draw
  from.
