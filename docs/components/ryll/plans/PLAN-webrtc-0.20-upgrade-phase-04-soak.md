# webrtc-rs 0.20 upgrade — phase 04: soak validation and docs

## Prompt

Close the port. Phases 01–03 landed the code; this phase
establishes that a real browser against a real guest behaves the
way 0.17 did, over minutes rather than seconds, and writes down
what it found.

Before executing any step, read the **Baseline** section of
`docs/plans/PLAN-webrtc-0.20-upgrade-phase-01-prework.md` end to
end — not just its table. The conditions block under it is the
specification for the comparable run, and phase 01 already
recorded two deviations from its own brief (the latency HUD and
the runtime-metrics snapshot loop are GUI-mode-only and do not
exist under `--web`) that this phase inherits. Then read the
browser-session report in
`docs/plans/PLAN-webrtc-0.20-upgrade-phase-02-bump.md:670-736`,
which is the only 0.20 browser evidence that exists so far.

Planning effort: high — not because the work is intricate, but
because most of it cannot be re-run cheaply. A soak measured
under conditions that do not match phase 01's produces a number
with nothing to compare it to, and the guest, the driver cadence
and the sampling method all have to match for the comparison to
mean anything.

This phase is unusual for this project in that its central step
is *operator* work. A browser session cannot be delegated to a
sub-agent, and neither can listening to audio. The step table
marks which steps are which.

## Scope

In:

- Landing #289 (a browser with no video codec gets an
  explanation) and #290 (the video pump stops spinning when
  nothing was negotiated). The master plan already names #289 as
  this phase's gate; see Decision 3 for why #290 comes with it.
- A committed soak harness under `tools/`, reproducing phase
  01's sampling method rather than reinventing it.
- The comparable soak: 20 minutes on the uefi-latency-guest with
  Chromium, under phase 01's exact conditions, compared against
  the 1a/1g numbers.
- A second, deliberately non-comparable session on the XFCE
  desktop guest for the qualitative checks — audio **by ear**,
  input, cursor, viewport resize — which the latency guest
  cannot carry because it has no audio device.
- The Firefox question: why its OpenH264 GMP does not load on
  this host, and a written answer to "is a working OpenH264
  enough, or does ryll need a second codec".
- `RYLL_GATHERING_SOAK=1 make test` on a quiet host.
- Closing out the master plan: results recorded, phase table and
  `docs/plans/index.md` set to Complete, and the two Future-work
  entries this planning session found missing (see survey
  finding 6).

Out:

- **A second video codec.** See Decision 4. If the Firefox
  investigation concludes ryll needs VP8 or VP9, the output of
  this phase is an issue and a paragraph, not an encoder.
- Adopting 0.20's send back-pressure, GSO/GRO batching or SCTP
  receive-window tuning. Already out of scope for the whole
  master plan — "port first, tune later" — and this phase is the
  measurement that a later tuning plan would need as its
  baseline.
- Safari. No Mac is available; the master plan already qualifies
  this with "if a Mac is available".
- Anything about the SPICE side of the stack. A regression that
  reproduces without `--web` is not this phase's.

## What the survey found

The master plan's phase 04 section is accurate in intent and
stale in four specific claims. All four are corrected at source
in the master plan as part of this planning commit, so a later
step does not have to rediscover them.

**1. `run_video_pump` is at `bridge.rs:1576`, not `:644`.** The
master plan's line reference predates phase 02's restructuring.
The claim it supports — that 0.20's three headline changes all
land on that write path — is still true.

**2. "with the latency HUD and runtime metrics captured" cannot
be done.** Both are GUI-mode-only: the auto-snapshot loop is
spawned from `app.rs` and the web shell has no latency HUD.
Phase 01 hit this during 1a and substituted external `/proc`
sampling, recording the substitution under *Deviations from the
step brief*. This phase inherits the substitution, and must,
because sampling the same way is what makes the comparison
valid.

**3. The AGENTS.md / ARCHITECTURE.md item is already done.**
The master plan asks this phase to update both "if the bridge's
task and callback structure changed shape, which phase 02 makes
likely". It did change shape, and phase 02 already wrote it up:
`AGENTS.md:166-221` carries a "WebRTC conventions" section
covering the driver event loop, `BridgeEvents`, `StickySignal`
and the `bridge_replaced` notification. `ARCHITECTURE.md:213-220`
carries the file tree including `bind_addrs.rs`, which phase 03
corrected in place. This phase verifies both still describe the
shipped shape and says so; it should not expect to change them.

**4. Phase 01's "Firefox cannot be the phase-04 viewer on this
host" is superseded.** That conditions block records Firefox 140
ESR failing to establish ICE at all under phase 01's loopback
signalling. Phase 02's session on 0.20 contradicts it directly:
ICE was *fully healthy* — nominated pair, consent refreshing on
schedule — and audio, datachannel, input, cursor and resize all
worked. Only video was missing, for a codec reason
(`PLAN-…-phase-02-bump.md:699-712`, #289). Firefox is a viable
phase-04 viewer; it is video-specific, not transport-specific.

Three further things the survey established that the master plan
does not say:

**5. Phase 01's sampler was never committed.** The Baseline's
conditions describe RSS from `/proc/<pid>/status` and per-thread
CPU from `/proc/<pid>/task/*/stat` every 30 s, with host busy%
per sample, plus a QMP `sendkey` driver every 30 s. None of that
exists in `tools/` — `ls tools/` has no soak or sampling script.
Reproducing the conditions therefore means rewriting the
harness, and any silent difference in *how* it samples changes
the numbers it produces. Decision 2 makes writing it down a step
rather than an accident.

**6. Two deferrals from phase 03 were never recorded anywhere.**
`PLAN-…-phase-03-udp-addrs.md:52` and `:176-182` both say
authenticated TURN (a `--web-ice-server` URL with a username and
credential pair) is "Recorded in Future work". It is not: the
master plan's Future work section does not mention TURN, no
issue exists, and no doc does either. This planning commit adds
it. Phase 03's Definition of done was otherwise met — spot-checks
against the tree confirm the three flags exist with clap help
(`ryll/src/config.rs:196-214`), `host_udp_bind_addrs()` is
literally `UdpBindPolicy::default().resolve()`
(`bind_addrs.rs:371-373`), both docs greps pass and the
"not configurable" sentence is gone.

**7. The lockfile resolves webrtc and rtc at 0.20.3, not the
0.20.2 the manifest declares.** A Renovate patch bump landed
between phase 02's port and its browser session, and phase 02
recorded that. It has not moved since. The soak write-up records
the resolved version, because "0.20" is not a precise enough
statement of what was measured.

**8. The Firefox OpenH264 plugin is present on disk and does not
load.** `~/.mozilla/firefox/lv8it6sq.default-esr/gmp-gmpopenh264/2.6.0/`
contains `libgmpopenh264.so` and the profile prefs record a
successful download. Firefox is 140.14.0esr, one patch newer
than the 140.13.0esr phase 02 tested. So the fresh-profile
hypothesis — that the GMP had simply never been fetched — is
already ruled out; phase 02 checked the same thing. Whatever
stops it loading is a loading failure, not an absence, which is
what step 4d has to characterise.

## Decisions

**1. Two sessions, and only one of them is a measurement.**
The comparable soak reproduces phase 01 exactly: uefi-latency-guest,
Chromium, one QMP `sendkey` every 30 s, `/proc` sampling every
30 s, 20 minutes. The desktop-guest session is qualitative —
audio by ear, input, cursor, resize — and produces no numbers
for the table.

The temptation is to do one richer session and get both. That
would be wrong: phase 01's numbers are a *floor-shape* baseline
on a light workload, and its own conditions block says "Phase 04
must reproduce these conditions to compare against these
numbers". Changing the guest changes the encode load, the repaint
pattern and the audio path all at once, and an RSS difference
would then be unattributable — which is the exact failure the
master plan's "port first, tune later" rule exists to avoid.
Separately, the latency guest has no audio device at all (only
`test-qemu-desktop` adds `intel-hda`), so the audio check could
not happen there even if we wanted it to.

**2. The sampler is committed to `tools/`, not improvised.**
Phase 01's harness was ad hoc and is gone (survey finding 5).
Rewriting it from the conditions prose invites small differences
— a 10 s cadence, RSS from `ps` instead of `/proc/<pid>/status`,
per-process instead of per-thread CPU — each of which quietly
changes the number. Writing it as `tools/web-soak.sh` also makes
the *next* soak cheap, which matters because a tuning plan for
0.20's back-pressure and GSO/GRO work will want exactly this
measurement again. This follows the project rule that anything
longer than a few lines is a script in `tools/` rather than
inline.

**3. #289 and #290 land in this phase, before the soak.**
The master plan already gates the phase on #289 ("Land #289
(tell the viewer) before soaking"). #290 comes with it for a
measurement reason rather than a tidiness one: with no video
codec negotiated, the pump keeps encoding and packetising at
frame rate for output the sender discards, and webrtc-rs logs an
unthrottled `ERROR` per packet from inside the library. A
Firefox session under those conditions produces a CPU number
that is measuring the bug, and a log in which nothing else is
findable. Fixing #290 is a precondition for the Firefox half of
this phase producing usable evidence.

They also share a root cause and a detection point —
`resolve_negotiated_payload_types` already knows there is no
common codec — so fixing one without the other means touching
the same function twice.

**4. The Firefox criterion is "a working OpenH264 gets video",
not "ryll gains a second codec".** This is the decision most
likely to be argued with, and the master plan explicitly leaves
it open: "settle whether a Firefox with a working OpenH264
plugin is enough for this criterion or whether ryll needs a
second codec".

Settling it as *enough*, for three reasons. ryll encodes H.264
only by design, and the encoder is shared with the GUI path — a
second codec is a renderer-side project, not a WebRTC-side one.
The observed failure is a Firefox-side plugin-loading problem on
one host (survey finding 8), not a protocol incompatibility:
Firefox's own H.264 support exists and is shipped, it just is
not loading here. And deciding to add a codec *during* a port's
validation phase reintroduces exactly the attribution problem
the whole master plan is structured to avoid — a video
regression after the soak would then have two candidate causes.

If 4d cannot make the GMP load, the honest output is a
documented finding and an issue proposing VP8, not a codec
written in a hurry at the end of a port. The phase still closes:
its Firefox criterion becomes "Firefox reaches a healthy session
and, when it has no video codec, says so in the page" — which
#289 makes true regardless.

**5. 20 minutes, matching, with one stated escape hatch.**
Phase 01's runs were 20 minutes and both showed RSS climbing
through the run (154→215 MB and 161→197 MB), which its verdict
attributes to ring buffers and caches filling to their caps.
Matching the duration is what makes the endpoint numbers
comparable. If the 0.20 run is still climbing at 20 minutes on a
trajectory the baseline's shape does not predict, extend that
run to 60 minutes and record both — an unbounded leak is worth
more than a tidy comparison, and saying which one happened is
cheap.

**6. A red soak does not silently become a green one.**
If the comparison shows a regression outside noise, this phase
does not fix it inline. It records the numbers, files an issue,
and the master plan's phase table says so. A performance fix
made during the run that measures it is a fix with no
independent measurement.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Fix #289 and #290 together in `shakenfist-spice-webrtc/src/bridge.rs`. `resolve_negotiated_payload_types` (near `:946`, and read #289's body for the full diagnosis) already detects the no-common-video-codec case and only warns. Make that state observable in two ways. First, tell the viewer: the control datachannel already carries server-to-browser messages — find the existing message enum and add a variant naming the condition in operator language ("this browser offered no H.264, so there is no video"), and render it in the web shell under `ryll/src/web/` as visible text over the video area rather than a console log. Follow whatever the shell already does for status text; do not invent a new UI mechanism. Second, stop the waste: `run_video_pump` (`bridge.rs:1576`) must not encode or write when no video payload type resolved. The pumps read the resolved type through an `Arc<AtomicU8>` published after `set_remote_description` (see the phase 02 plan's review follow-up for why it is an atomic and not a plain value), so the pump can check it; pick a sentinel or an `Option`-shaped equivalent that distinguishes "not resolved yet" from "resolved to nothing", because the pump starts before `accept_offer` and must not treat startup as failure. Add a unit test in the style of `loopback_media_flows_when_client_offers_a_narrow_codec_set` (`tests/loopback.rs`) that offers a video section with no H.264 at all and asserts both that the viewer-facing message is sent and that no RTP is written. Do not change the codec registration — `register_default_codecs` is deliberately left alone during the port. |
| 4b | medium | sonnet | none | Write `tools/web-soak.sh`, the harness Decision 2 calls for, and document it in `docs/development.md` beside the existing "Manual verification against a desktop guest" section. It must reproduce phase 01's Baseline conditions exactly — read `docs/plans/PLAN-webrtc-0.20-upgrade-phase-01-prework.md:333-412` first, it is the specification. Arguments: the ryll pid (or a way to find it), a duration defaulting to 20 minutes, a sample interval defaulting to 30 s, and the QMP socket for the keypress driver. Per sample it records: RSS from `/proc/<pid>/status` (`VmRSS`), per-thread CPU from `/proc/<pid>/task/*/stat` (fields 14 and 15, utime and stime, summed across threads), whole-host CPU busy% and load average — phase 01 recorded the host figures per sample precisely so contamination from a shared machine is visible in the record, and this host is shared. Separately it drives one QMP `sendkey` every 30 s; note that phase 01 found a 5 s cadence unusable because the guest's mode-set churn outruns the stream's recovery. Emit CSV plus a summary block matching the Baseline table's rows (RSS start→end, RSS max, CPU as a percentage of one core across the whole run). Have it print a warning at startup that the uefi-latency-guest cycles through eight colours one of which is black, so ~30 s of black every 4 minutes is the guest and not a bug. Must pass `tools/run-shellcheck.sh`. Do not run a soak in this step. |
| 4c | — | operator | — | **Operator step.** The comparable soak. Boot `make test-qemu` (the uefi-latency-guest), start `ryll --web --direct localhost:5900` from a `make build` dev-profile binary with `RUST_LOG=info,shakenfist_spice_webrtc=debug,ryll=debug` — the drop counters only log at debug — and connect Debian Chromium with a fresh profile, `--disable-features=WebRtcHideLocalIpsWithMdns` and `--autoplay-policy=no-user-gesture-required`. Step the guest to teal before starting so the colour cycle begins where phase 01's runs began. Run `tools/web-soak.sh` for 20 minutes. Record the resolved webrtc/rtc version from `Cargo.lock` (0.20.3 today) and the commit SHA alongside the numbers. Also confirm the three log lines `docs/development.md:340-348` names, and that the answer SDP carries at least one candidate and none with an unspecified address. |
| 4d | high | opus | none | **Operator-assisted.** Characterise the Firefox OpenH264 failure and settle Decision 4 in writing. The plugin is present on disk and the profile prefs record a successful download (survey finding 8), so this is a load failure. Start with `about:support` (Media section, GMP plugin state), `about:addons` → Plugins, the browser console with `media.gmp.log.level` turned up, and Firefox's GMP sandbox — Debian's `firefox-esr` packaging and the RDP/Kasm session are both plausible culprits and neither is ryll's. Generate an offer from `about:webrtc` and confirm against the offer SDP rather than against `RTCRtpReceiver.getCapabilities('video')`, which `docs/development.md:361-366` already warns is not evidence. Then either: the GMP loads and Firefox gets video, in which case run the desktop-guest session on Firefox too and record it; or it does not, in which case write down precisely what blocks it, confirm #289's message is what the user sees, and file an issue proposing a second codec with the evidence attached. Either way the outcome is a paragraph in this plan's results section and, if needed, an issue — not an encoder. |
| 4e | — | operator | — | **Operator step.** The qualitative session. `make test-qemu-desktop`, then `ryll --web --direct localhost:5900` and a browser. Check, and record each: video shows the XFCE desktop at the guest's resolution; **audio is audible by ear** — this is the clause inherited from phase 02, where `playback: MODE: 3` proved Opus was negotiated and nobody listened; keyboard and mouse reach the guest; the cursor shape follows; viewport resize propagates. This session produces no numbers and is not compared against the baseline. |
| 4f | low | haiku | none | **Operator-run, agent-recorded.** Run `RYLL_GATHERING_SOAK=1 make test` on a quiet host and record the result. This is the deliberate occasion the 20-iteration invariant-candidate-count check in `accept_offer_answer_carries_all_candidates` (`bridge.rs:2485-2495`) is gated for; `docs/development.md:299-304` explains why it is off by default. If it fails, capture the candidate counts across iterations before concluding anything — host interface churn is the expected false positive and the log distinguishes it. |
| 4g | medium | sonnet | none | Close the plan out. Write a "Results" section into this file carrying the 4c table beside phase 01's 1a/1g columns, the 4d Firefox finding, the 4e qualitative checklist and the 4f result, each with the commit SHA and resolved webrtc version. Verify — do not assume — that `AGENTS.md:166-221` and `ARCHITECTURE.md:213-220` still describe the shipped shape (survey finding 3 says they do; say so explicitly either way, and change them only if they do not). Set this phase to Complete in the master plan's phase table and in `docs/plans/index.md`, and set the master plan's overall status to Complete there too, since this is its last phase. Check the master plan's Success criteria list item by item and note any that this phase could not satisfy, with the reason — Safari has no Mac, and Firefox's outcome depends on 4d. |

Dependencies: 4a and 4b are independent and can run in parallel.
4c depends on both — on 4a because an unfixed #290 would spin
the pump during measurement (Decision 3), on 4b because it is
the harness. 4d depends on 4a for the viewer-facing message.
4e and 4f depend on nothing but the tree building. 4g depends on
all of them.

**Back-brief gate before 4c.** The soak is the one step in this
phase that is expensive to redo — 20 minutes of wall clock, a
booted guest and an attended browser — and a condition that
does not match phase 01 is not discoverable until the numbers
are already wrong. Before starting it, restate the conditions
that will be used and check them against the Baseline's
conditions block line by line.

## Risks and mitigations

- **The measurement is contaminated by the shared host.** This
  machine runs other work; phase 01's 1g run already caught one
  external spike to 29% busy. Mitigation: 4b records host busy%
  and load average per sample, as phase 01 did, so the
  contamination is visible in the record rather than folded into
  the number. If a spike lands inside the window, discard the
  run and repeat it — the harness makes that cheap, which is
  half the point of Decision 2.
- **The conditions drift from phase 01's without anyone
  noticing.** Guest, cadence, browser flags, build profile and
  log level all affect the numbers, and prose is a poor
  specification. Mitigation: the back-brief gate before 4c, and
  4b encoding the sampling half in a script rather than in a
  reader's memory.
- **A regression is found and the phase quietly absorbs it.**
  The pressure at the end of a port is to explain a number away.
  Mitigation: Decision 6 states the rule in advance — record,
  file, and let the phase table say so. The management session
  checks the 4g write-up against the 4c raw CSV rather than
  against 4c's summary.
- **#289's viewer-facing message becomes a second place that
  can be wrong.** A message rendered in the page is a claim
  about negotiation state that can go stale or fire spuriously —
  a viewer that *does* have H.264 must never see it.
  Mitigation: 4a's test asserts both directions, and the
  existing `loopback_media_flows_when_client_offers_a_narrow_codec_set`
  is the negative case — it offers H.264 at browser-chosen
  payload numbers and must stay silent.
- **4a's pump gating misreads startup as failure.** The pumps
  are spawned before `accept_offer`, so "no payload type
  resolved" is the *normal* state for the first moments of every
  session. A naive check would suppress video on every
  connection. Mitigation: the brief calls this out explicitly
  and requires the sentinel to distinguish the two states; the
  management session checks that distinction in the 4a diff, and
  `tests/loopback.rs` passing at all is the regression signal.

## Definition of done

Falsifiable, in the order a reviewer would check them:

- `tools/web-soak.sh` exists, passes `tools/run-shellcheck.sh`,
  and `docs/development.md` explains when to reach for it.
- This file carries a Results section whose table has the same
  rows as phase 01's Baseline table, with the commit SHA and the
  webrtc version resolved in `Cargo.lock` recorded beside it.
- The Results section states, in words, whether the 0.20 numbers
  are within noise of 1a — and if they are not, links the issue
  filed for it.
- A browser session is recorded in which audio was confirmed
  **by ear**, not by `playback: MODE: 3`.
- A browser with no H.264 sees an explanation in the page. A
  unit test asserts it is sent in that case, and
  `loopback_media_flows_when_client_offers_a_narrow_codec_set`
  still passes, asserting it is not sent when a codec did
  negotiate.
- With no video codec negotiated, the video pump writes no RTP —
  covered by the same test, and observable as the absence of
  `Failed to send RTP` in a Firefox session log.
- The Firefox outcome is written down either way: video works,
  or the loading failure is characterised and an issue exists.
- `RYLL_GATHERING_SOAK=1 make test` result is recorded.
- `AGENTS.md` and `ARCHITECTURE.md` have been checked against
  the shipped bridge shape, with the finding stated explicitly
  rather than by silence.
- Every item in the master plan's Success criteria is either
  satisfied or listed as unsatisfied with a reason.
- The master plan's phase table, `docs/plans/index.md` and this
  file all say Complete, and the master plan's overall status in
  `index.md` is Complete.
- `make test`, `make lint` and `pre-commit run --all-files` all
  pass.

## Effort

One day, matching the master plan's estimate, but the shape is
different from the other phases: perhaps two hours of code (4a,
4b), an hour of attended browser time spread across 4c, 4d and
4e, and the rest write-up. The 20-minute soak is wall clock
rather than effort, and 4d is the variance — a GMP that will not
load can absorb an afternoon and still end in "it is Firefox's
packaging, not ours".

The phase is cheap to plan and expensive to redo, which is why
Decision 2 and the back-brief gate exist.

## Results

### 4d — the Firefox H.264 question, settled

Firefox 140.14.0esr on this host offers **no H.264 at any payload
type**, and the cause is not anything ryll does. Established with
`tools/browser-offer-probe.py`, which this step added, against the
provisioned `default-esr` profile:

| Check | Result |
|---|---|
| `RTCRtpSender.getCapabilities('video')` | lists **four** H.264 entries (`42e01f` and `42001f`, packetization-mode 1 and 0) |
| The offer it actually sends | VP8 120, VP9 121, AV1 99, rtx, ulpfec, red — **no H.264 line at all** |
| Same, windowed on a real X display | identical — headless is not the cause |
| Same, `direction: recvonly` | identical — and this is the direction a ryll viewer uses |
| Same, `MOZ_GMP_PATH` forced at the plugin | identical |
| Chromium 151, same probe, as a control | offers H.264 at PT 102, 104, 108, 114, 116, 39, 41, 43 |

So the capability list and the offer disagree, which is exactly the
trap `docs/development.md` already warned about — now demonstrable
in a minute rather than asserted.

Everything static about the installation says it should work. The
plugin is on disk at `gmp-gmpopenh264/2.6.0/libgmpopenh264.so` with
its `.info` declaring both `encode-video[h264]` and
`decode-video[h264]`; `ldd` resolves every library it needs; Debian's
`firefox-esr` defaults `media.gmp-gmpopenh264.enabled` and
`.visible` to true and ships no `policies.json` overriding them;
`media.navigator.video.disable_h264_baseline` is false; and
`libxul.so` still references the plugin by name. `MOZ_LOG=GMP:5`
produced empty logs across all nine child processes, so no GMP
process is launched and rejected — Firefox concludes H.264 is
unavailable before it gets that far.

**Decision 4 stands: this is not ryll's to fix, and ryll does not
gain a second codec because of it.** The failure is inside Firefox's
own GMP provisioning on this host; ryll's H.264-only encoder is
shared with the GUI path, and adding VP8 would be a renderer project
undertaken during a port's validation phase — the attribution
problem the master plan is structured to avoid.

What phase 04 does instead is make the failure legible, which 4a
did: a browser in this state now gets a panel in the page naming
the cause, and the session stops spending CPU on video nobody can
decode.

Two things follow for the rest of the phase. The Firefox criterion
is met in the form Decision 4 stated — Firefox reaches a healthy
session and is told why it has no picture — and **the comparable
soak (4c) must use Chromium**, which is what phase 01's baseline
used anyway.

An unexpected dividend: the regression test added in 4a offers VP8
at PT 120 and Opus at 109 because that is what this probe showed
Firefox actually offering. The test reproduces a real browser's
numbers rather than plausible-looking ones.

### 4c — a condition in the baseline that never worked

Setting up the comparable soak turned up something the survey could
not have caught, because it only shows when you run the thing:
**ryll does not read `RUST_LOG`.** `main.rs:161-169` picks between
`Level::INFO` and `Level::DEBUG` from the `--verbose` flag and builds
a plain `LevelFilter`; there is no `EnvFilter` anywhere, and the only
occurrence of `RUST_LOG` in ryll's history is inside a comment.

Phase 01's Baseline conditions record running with
`RUST_LOG=info,shakenfist_spice_webrtc=debug,ryll=debug` "(the drop
counters only log at debug)". That variable did nothing. The run was
at `INFO`, so the video-pump drops, audio-pump drops and reaper
events rows of the baseline table are all reporting *nothing logged*
rather than *nothing dropped*. Those three rows should not be read
as measurements.

Two docs pages carried the same instruction and have been corrected
to `--verbose`, along with the sentence this phase itself added to
`docs/development.md` in 4b — which had inherited the incantation
from the plan without checking it.

This changes how 4c is run. To stay comparable with what phase 01
*actually* did, the measured session runs at `INFO`, which is what
the baseline had. `--verbose` turns on `debug` for the whole
dependency tree, and webrtc-rs at that level emits enough log to
perturb the very numbers being taken — so the drop counters come
from a short separate session instead.

### 4c — the comparable soak

Two 20-minute runs on `b4a5b4bd`, webrtc and rtc resolved at
**0.20.3** in `Cargo.lock` (the manifest says 0.20.2). Conditions as
phase 01's Baseline: uefi-latency-guest via `make test-qemu`,
Chromium 151 windowed with a fresh profile and
`--disable-features=WebRtcHideLocalIpsWithMdns
--autoplay-policy=no-user-gesture-required`, one QMP `sendkey` every
30 s, `/proc` sampled every 30 s by `tools/web-soak.sh`, dev-profile
binary, encoder confirmed at 1280x800@30fps with one restart in each
run.

| Metric | 1a (0.17.2) | 1g (phase 01 tip) | **4c run 1** | **4c run 2** |
|---|---|---|---|---|
| RSS start → end | 154 → 215 MB | 161 → 197 MB | 158 → 235 MB | 161 → 221 MB |
| RSS max | 226 MB | 197 MB | 235 MB | 225 MB |
| CPU, all threads, whole run | ~1.4% of one core | ~0.9% | 1.83% | 1.95% |
| Video pump drops | 0 (not logged) | 0 (not logged) | 0 (observed) | 0 (observed) |
| Audio pump drops | 0 (not logged) | 0 (not logged) | 0 (observed) | 0 (observed) |
| Reaper events | 0 (not logged) | 0 (not logged) | 0 (observed) | 0 (observed) |
| ryll alive at end | yes | yes | yes | yes |
| Host CPU busy%, mean (max) | 8.3 (9) | 10.4 (29) | 10.0 (12.7) | 8.9 (10.8) |

**Memory is bounded.** Both runs plateau: run 1 sits at exactly
235 MB for its final seven samples, run 2 at 221 MB for its final
three, after the same climb-then-flatten shape phase 01 saw and
attributed to ring buffers and caches filling to their caps. Nothing
is leaking, which is the question a soak exists to answer. Max RSS
is up about 9% on the 0.17 pair's mean (230 MB against 211 MB) —
within the 29 MB spread phase 01's own two runs showed.

**CPU is up, and by more than phase 01 called noise.** The two 0.20
figures (1.83%, 1.95%) are both above both 0.17 figures (1.4%,
0.9%), and they are tighter to each other than the 0.17 pair is.
Mean 1.89% against 1.15%: +0.74 percentage points, or roughly 1.6×.
Phase 01 called a 0.5 pp difference run-to-run noise, so this is not
something to wave through on that precedent. In absolute terms it is
small — under 2% of one core either way, about 23 CPU-seconds per
20 minutes against 14 — and this is deliberately a near-idle
workload, which phase 01 warned is "a floor-shape baseline, not a
stress result".

**But the delta cannot be attributed to webrtc-rs 0.20.** This is
the honest limit of the comparison as the master plan specified it.
The baseline was taken on `ce740e26` (2026-08-13) and this run is on
`b4a5b4bd` (2026-08-22); between them the tree gained not just
phases 02 and 03 but nine days of unrelated development — the
control socket and its verbs, `SurfaceMirror` in headless, the
bug-report work, several dependency bumps. A 0.74 pp CPU difference
across that much change is a fact about the tree, not about the
dependency.

Isolating it would mean measuring `develop` immediately before and
immediately after the phase-02 merge (`be1aa97c`) under these same
conditions — now cheap, since `tools/web-soak.sh` exists. Per
Decision 6 this phase records the number and does not chase it
inline.

### 4c — the bisect: the port did not cause the CPU rise

The comparison above could not attribute its 0.74 pp CPU difference,
because nine days of unrelated development sit between phase 01's
baseline commit and the tip. So the phase measured either side of the
phase-02 merge instead, under the same conditions, back to back.

| | webrtc | CPU, whole run | RSS start → end | RSS max | Host busy% mean (max) |
|---|---|---|---|---|---|
| `1a7b47ed` — before the merge | 0.17.1 | **0.96%** | 150 → 220 MB | 220 MB | 11.7 (17.5) |
| `be1aa97c` — the merge | 0.20.2 | **0.98%** | 145 → 201 MB | 201 MB | 10.2 (13.2) |
| `b4a5b4bd` — tip, run 1 | 0.20.3 | 1.83% | 158 → 235 MB | 235 MB | 10.0 (12.7) |
| `b4a5b4bd` — tip, run 2 | 0.20.3 | 1.95% | 161 → 221 MB | 225 MB | 8.9 (10.8) |

**Across the bump itself, CPU is unchanged** — 0.96% against 0.98%,
a difference far below the 0.5 pp phase 01 called run-to-run noise —
**and memory improved**, 220 MB down to 201 MB peak. The port is not
where the cost appeared.

The rise to ~1.9% therefore happened somewhere between `be1aa97c`
and `b4a5b4bd`: phase 03's binding configuration, the control socket
and its verbs, `SurfaceMirror` in headless, the bug-report work, and
several dependency bumps. Narrowing it further is another bisect over
that range, which `tools/web-soak.sh` now makes routine, and it is
not this plan's to chase — Decision 6 says record and hand off.

Two notes on method, since a soak that cannot be trusted is worse
than none. The first pre-bump run was **discarded**: host busy
averaged 27.8% with two ~100% spikes from unrelated work on this
shared machine, against ~10% on every run reported here. And a
second attempt was lost when its sampler was stopped mid-run; the
retry ran under `nohup`. All four reported runs sit within 8.9–11.7%
mean host busy, which is what makes them comparable to each other
and to phase 01's 8.3–10.4%.

### 4c — observations from the measured sessions

Three things worth recording separately from the numbers.

**The negotiated H.264 payload type was 108, not 102.** Chromium
151 landed on 108 in these sessions. The constant phase 02's review
removed was 102, so on this browser, today, a bridge that still
stamped the constant would send every video packet at a payload type
the sender rejects and the viewer would see a black screen. That fix
is not defensive against a hypothetical browser; it is load-bearing
against the one in front of us.

**Pump drops and reaper events are genuinely zero.** Taken from a
short `--verbose` session rather than from the measured run, per the
finding above. The three drop messages
(`bridge.rs:1725`, `:1856`, `:1927`) only exist on the error path, so
no occurrences means no drops rather than no instrumentation. The one
reaper line is `bridge reaper: woken but bridge is alive;
re-parking` — healthy, not a teardown.

**The `--verbose` log volume justifies keeping it out of the
measurement.** 3,620 lines in about two minutes with it, against 147
lines in twenty minutes without: roughly 250× the rate. Logging that
hard would have shown up in the CPU figure being measured.

**#289's negative case held in a real browser.** Across both
measured runs Chromium negotiated H.264, the no-video notice never
fired, and no `unsupported codec type` line appeared. The guard added
in 4a — that a working viewer is never told its video is broken — is
confirmed outside the unit tests.

### 4e — the qualitative session, and what it found

**Audio is confirmed by ear.** Chrome on macOS, against the XFCE
desktop guest, playing a 440 Hz tone from `speaker-test`. This is
the clause phase 02 deferred — it had established only that Opus
was *negotiated* (`playback: MODE: 3`) and explicitly recorded that
nobody had listened. Server side, 1,193 Opus packets were forwarded
with zero drops at payload type 111.

Arrow keys, and typing generally, confirmed working after the fixes
below.

**4e found three input bugs, all pre-existing rather than port
regressions**, and all of them invisible to the test suite:

1. **Key releases were sent as presses.** The web path handed
   `InputEvent::KeyUp` the make code; the inputs channel writes it
   verbatim, so the guest saw a second press and auto-repeated
   forever. The GUI path has always set the break bit. This was the
   "keyboard going bonkers" report.
2. **Extended scancodes had their bytes reversed** — 19 keys dead,
   including every arrow.
3. **Browser auto-repeat was forwarded**, stacking on the guest's
   own repeat. Latent, but the same failure mode by another route.

The first two are one root cause: the web frontend reimplemented an
encoding that `make_scancode` already owned. Both existing unit
tests asserted the buggy values, one describing `0xE048` as
"wire-format" in a comment — they were written from the
implementation rather than the contract, so they locked the bugs in
rather than catching them.

**A fourth, in the page itself:** `#enable-audio` had no `z-index`
while `<video>` fills the viewport and follows it in the DOM, so
the button was painted over, as was `#status`. Chrome still
delivered the click — the operator confirmed the button worked once
found — so this is a visibility defect rather than an unreachable
control; an earlier draft of this section overstated it as
"unclickable" on the strength of the stacking order alone, which the
operator's session contradicted.

It still matters: clicking that button is the only way to get
sound, because browsers will not autoplay audio unprompted, and a
control nobody can see is one nobody presses. Fixed with two
`z-index` declarations. Deliberately not a layout change — the
letterbox-versus-scale question is #308's and the wider UI is headed
for sfui (#293), and a button should not have to wait for either.

**Firefox on macOS also gets no video**, reported in passing during
this session. That is a correction to Decision 4's reasoning rather
than to its conclusion, and it matters: 4d argued the H.264 gap was
"a Firefox-side plugin-loading problem on one host". Two hosts, two
operating systems, is not one host's misconfiguration — it is
Firefox not offering H.264 for WebRTC in a way ryll can rely on
anywhere. The decision not to add a second codec *during a port*
still stands, for the attribution reason. The case for adding one
afterwards is now materially stronger than 4d made it look, and
whoever picks up that question should start here rather than from
4d's more comfortable framing.

### 4f — the ICE gathering soak

`RYLL_GATHERING_SOAK=1 make test` passes. The 20-iteration
invariant-candidate-count check in
`accept_offer_answer_carries_all_candidates` held: same candidate
count in the answer on every round, and no candidate with an
unspecified address.

Confirmed that the soak actually ran rather than silently falling
back to its three-iteration default — the variable has to survive
into the devcontainer, and a typo there would look exactly like a
pass. The crate's suite takes 10.56 s with it set and 1.60 s
without.

Host load average was 2.3 rising to 3.2 across the run: not the
quiet host the gate asks for, but the check passed anyway, and a
false *pass* is not a failure mode this test has — interface churn
makes it flaky in the failing direction.

## Status

Complete. Every step landed, both operator decisions were taken and
executed: the CPU difference was bisected to somewhere after the
port rather than in it, and web mode can now host a control socket
so the QR-digest scenario tests can reach it.

Two things leave this phase open elsewhere rather than here: Safari
is unexercised for want of a Mac, and Firefox still gets no video —
recorded above, and neither is a port regression.

### 4g — the master plan's success criteria, item by item

| Criterion | Result |
|---|---|
| `webrtc = "0.20.2"` or later in the one manifest, both Renovate rules gone | ✅ manifest says 0.20.2, lockfile resolves 0.20.3, `renovate.json` names webrtc nowhere |
| `make test` passes, including `tests/loopback.rs` and `tests/lifecycle.rs` | ✅ 16 suites green |
| `pre-commit run --all-files` passes | ✅ all four hooks |
| A real browser reaches a real guest with video, audio, input and cursor, and survives a soak | ✅ Chromium on the latency guest (4c, two 20-minute runs) and on the desktop guest with **audio confirmed by ear** (4e) |
| RSS and CPU compared against a 0.17 baseline | ✅ and then bisected, which is what made the comparison mean anything |
| The answer SDP advertises no unspecified-address candidate, and at least one candidate | ✅ asserted every round of the 20-iteration gathering soak (4f) |
| The reaper tears the bridge down on `Failed`, `Disconnected`, `Closed` | ✅ unchanged since phase 02; `wait_for_dead` covered in both crates' tests |
| `docs/configuration.md` and `docs/web-frontend.md` cover the UDP bind address | ✅ both name `--web-media-port` (phase 03) |
| `ARCHITECTURE.md` and `AGENTS.md` reflect the bridge's task and callback structure | ✅ verified, and phase 02 had already written it up. `ARCHITECTURE.md` changed by one word this phase — its `control/` annotation said "Headless control socket", which stopped being true |

**Not satisfied, with reasons — both now tracked rather than left
in this document:**

- **Safari** (#310). No Mac was available to the session; the
  operator checked Chrome on macOS instead, which the master plan
  does not count. Safari remains unexercised.
- **Firefox** (#311). Reaches a healthy session with audio, input
  and cursor, and no video, on both Linux and macOS. This is the
  criterion Decision 4 deliberately reinterpreted rather than met;
  see 4d and its correction under 4e. #311 carries the evidence and
  the second-codec decision.

## What 4e implies for testing

Four input bugs shipped in web mode, and none of them was subtle:
every key stuck down, and nineteen keys did nothing at all. The
question worth answering is not why they existed but why nothing
caught them.

**The loop that would have caught them cannot reach web mode.** ryll
already has the apparatus: the `uncalibrated-sextant` guest,
`shakenfist-visual-digest` decoding a QR-encoded visual digest off
the screen, the `digest_updated` control-socket event behind the
`digest-decode` feature, and Sextant scenario tests written against
that protocol. It is a genuine closed loop — drive input, read back
what the guest actually received.

It is confined to headless by construction. `ryll/src/config.rs:48`
declares `--control-socket` with `requires = "headless"` and
`conflicts_with = "web"`, so no scenario test can observe a `--web`
session. And web mode is the one path that reimplements the
scancode encoding — `docs/multi-mode-parity.md:99` says so plainly:
"available (MVP; browser-side scancode table)". A second
implementation of a wire format, with the verification loop
structurally unable to reach it.

Three tiers, cheapest first:

1. **Done in this phase.**
   `every_key_reaches_the_wire_the_way_the_gui_would_send_it` pins
   plain and extended keys, press and release, against
   `make_scancode` itself. No guest, no browser, runs in CI now, and
   fails on the pre-fix code. It would have caught both wire-format
   bugs.
2. **Lift `conflicts_with = "web"`.** Then an existing-style
   scenario test drives a headless browser at the web URL and
   asserts through `digest_updated` what the guest received —
   closing the loop through the browser, which is the only way to
   catch a bug in `app.js` itself. This needs no new infrastructure,
   only permission for the two flags to coexist, and the separation
   looks deliberate enough to want the operator's agreement first.
3. **Stop reimplementing the encoding.** `make_scancode` is now
   shared, so the encoding is single-source. What remains duplicated
   is the `KeyboardEvent.code` → scancode table in `app.js`, which
   is the part tier 2 would cover.

## Review response

The automated reviewer raised fifteen findings on #312 — eight FIX,
five CONSIDER, two INFO. All fifteen were verified against the tree
before being acted on; none was wrong. Thirteen were fixed on the
branch and two were filed.

Two of them were substantive, and both are the same failure this
phase was already about: a second implementation of something that
already had an owner, and a capability that reports success while
doing nothing.

**The control socket's `send_key` had the identical pair of
scancode bugs the web frontend had.** `handle_send_key` hand-rolled
the encoding — `scancode as u32` straight into `KeyDown`, and
`scancode | 0x80` for the release. For 0xE0-prefixed keys that sent
the prefix byte second and put the break bit on the prefix rather
than the scancode, so every extended key was wrong in both
directions, exactly as `app.js` was. The phase found one instance of
the duplicate-encoding bug and left the other in place, in the very
verb the new doc comment says must go through `make_scancode`. It
now does. The test asserted the buggy values (`0xE04B` / `0xE0CB`),
which is the same "written from the implementation rather than the
contract" failure recorded above for the web tests; it now asserts a
literal *and* `make_scancode`, as the web test does.

This is protocol-visible, so the protocol went to **1.2** with the
correction written into the version history and the `send_key`
section. It is a minor bump: nothing about the envelope, verbs or
field types changes, and no client could have depended on the old
behaviour while also reaching the guest correctly. Checked before
changing it — no ecosystem consumer sends extended scancodes.
kerbside's `press_key` sends 28 and 23 with the release bit applied
client-side, and the loadtest orchestrator sends 0x39; all three are
plain codes, byte-identical across the change. The affordance
kerbside relies on (a logical code that already carries the break
bit) survives, and is now asserted rather than assumed.

**Web mode's control socket could never emit `digest_updated`.** The
poller that decodes the QR digest out of the framebuffer was spawned
inside `run_headless` only, so a client could `subscribe` in web
mode, get a success response, and wait forever. That is precisely
the gap "What 4e implies for testing" says this feature exists to
close, so the feature was landed without the thing that makes it
useful. `spawn_digest_poller` now sits beside `spawn_control_socket`
and both modes call it. Web mode spawns it inside the
`--control-socket` branch: the poller decodes QR on a timer whether
or not anyone is listening, and an ordinary browser session should
not pay for that. Note that neither `make test` nor `make lint`
builds `digest-decode`, so this needed a separate
`cargo clippy -p ryll --features digest-decode --all-targets` to
check at all — the first attempt did not compile.

The rest were documentation and comment corrections, and three of
them were places where this phase's own text was wrong:

- `app.js`'s file header still asserted that extended keys are
  encoded with the prefix in the low byte "matching
  `make_scancode()`" — the exact false statement that produced the
  bug, seventy lines above the corrected comment.
- `make_scancode` carried two stacked doc comments, the stale one
  first, describing the encoding in terms of output byte layout
  while the new one described input logical form.
- `validate_control_socket` was inserted between
  `web_media_bind_policy`'s doc comment and its function, so
  rustdoc silently reassigned eight lines of explanation to the
  wrong function.
- The parity matrix marked the socket "available" for Web while the
  two rows describing what it can do still read "n/a — intrinsic".
- The z-index comment's stacking-order explanation was wrong:
  `#video` is not positioned, so `#status` and `#enable-audio` paint
  above it regardless. Reworded to what was observed — Chrome hid
  them anyway — rather than a mechanism that does not apply.
- `#no-video` had default `pointer-events`, so a 32rem panel whose
  own text promises "mouse ... unaffected and still work" swallowed
  every click in the middle of the screen.
- `tools/web-soak.sh --help` printed `set -euo pipefail` and the
  variable block, because the extraction used a hardcoded line
  range that the header had outgrown.

Two were deferred, both as the reviewer suggested:

- **#313** — ryll ignores `RUST_LOG`, so `--verbose` is
  all-or-nothing. This is the direct cause of the invalidated
  baseline rows recorded above. `EnvFilter` is a few lines, but it
  is a behaviour change to logging with its own testing, and the
  documented workaround is in both places an operator looks. The
  comment at `main.rs` and the paragraphs in `docs/development.md`
  and `tools/web-soak.sh` now name the issue, and say the text goes
  away when it lands.
- **#314** — `no_video_codec` is an `Arc<AtomicBool>` on shared
  state holding a per-bridge fact. Correct for the single viewer
  web mode supports; the fix needs the relay spawn moved below
  `accept_offer`, which touches bridge lifecycle and the error path
  on the one code path this phase soak-tested by hand. Not worth
  that risk for a failure mode that is a missing notice, in a
  configuration web mode does not support. Recorded in the field's
  doc comment.

The reviewer's strongest coverage note stands and is not fixed here:
there is still no end-to-end test that drives a web-mode control
socket, and such a test would have caught both substantive findings.
It is tier 2 in "What 4e implies for testing" and needs a live
guest, so it belongs in kerbside rather than in this branch.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
