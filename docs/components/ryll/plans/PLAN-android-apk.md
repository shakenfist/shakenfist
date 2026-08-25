# Android APK Port of Ryll

> **Status: concept plan.** This is an exploratory design document.
> Phase files have not been written and no implementation work has
> begun. The execution table below is provisional. Treat the open
> questions as blockers to firming up the phase plans.

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
TLS/RSA, LZ/GLZ compression, Android platform APIs, winit /
android-activity / cargo-apk), research as needed to give a
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
be done via the table in the Execution section of this
document.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Ryll is a pure-Rust SPICE VDI test client built on eframe 0.29
+ egui, with a tokio async runtime, cpal audio, and a workspace
of protocol / compression / usbredir sub-crates. It currently
ships as packaged binaries for Linux x86_64, macOS aarch64, and
Windows x86_64 (see `PLAN-packaging.md`).

There is no Android build today. The primary motivation for
adding one is to turn commodity Android TV hardware — in
particular the **Google TV Streamer (2024)** — into a viable
ryll endpoint: a cheap, HDMI-attached thin client that can
connect to a shakenfist VM or kerbside-proxied session without
having to ship dedicated hardware.

### Target hardware

The **Google TV Streamer (2024)** is the principal target:

| Property | Value |
|----------|-------|
| SoC | MediaTek MT8696 |
| CPU | 4× ARM Cortex-A55 @ 2.0 GHz |
| GPU | PowerVR GE9215 @ 850 MHz |
| RAM | 4 GB |
| Storage | 32 GB |
| Hardware decode | H.264 / H.265 / VP9 / AV1 |
| OS | Google TV (Android 14) |
| Networking | Wi-Fi 5, Bluetooth 5.1, Gigabit Ethernet |
| Sideload | Supported via `adb install` |

Secondary targets (same APK should install and run, but UX is
a future-work concern):

- Chromecast with Google TV 4K (Amlogic S905X3, 2 GB RAM,
  Android 10/12)
- Chromecast with Google TV HD (Amlogic S805X2, 2 GB RAM,
  Android 12)
- Generic Android phones and tablets (arm64-v8a, Android 10+)

Pre-Google-TV Chromecast dongles (generations 1–3) are
**out of scope** — they run a locked-down Chrome-OS-derived
firmware with no app runtime.

### What the existing Rust code already gets us

Components that should port to Android with little or no
change:

- **Rust toolchain and workspace.** Pure Rust end to end.
  `cargo-ndk` / `cargo-apk` can cross-compile to
  `aarch64-linux-android`.
- **SPICE protocol stack.**
  `shakenfist-spice-protocol` / `-compression` / `-usbredir`
  are pure Rust and have no platform dependencies beyond
  `std`.
- **Network transport.** Tokio + `tokio-rustls` over TCP.
  Both support Android.
- **Audio playback.** `cpal` 0.15 has an AAudio backend. The
  existing channel in `src/channels/playback.rs` should
  continue to work once the AAudio backend is selected.
- **USB backend.** `nusb` is pure Rust and works on Android,
  but Android's USB host API requires a Java/Kotlin
  permission prompt we don't have infrastructure for yet.

### What will not port cleanly

Platform-specific bits that will need attention:

- **eframe / winit entrypoint.** `src/main.rs` calls
  `eframe::run_native(...)` with desktop-shaped
  `NativeOptions` / `ViewportBuilder`. Android needs an
  `android_main(app: AndroidApp)` entrypoint from the
  `android-activity` crate. How deep this rewrite goes
  depends on whether eframe 0.29's winit integration can
  accept an externally supplied `AndroidApp`, or whether we
  need to bypass eframe and drive egui + winit directly.
  This is the single biggest unknown.
- **`/proc/self/stat` metrics.** `src/metrics.rs` reads
  Linux-only `/proc` paths for CPU accounting. Android's
  `/proc` layout is similar but SELinux restrictions may
  block access. Easiest path for MVP: gate behind
  `#[cfg(not(target_os = "android"))]` and expose zeros.
- **Hardcoded `/tmp/ryll.log`.** Replace with Android's
  app-specific files directory via JNI, or accept an
  env-var override injected by the activity wrapper.
- **Clipboard (`arboard`).** No Android backend exists. For
  MVP, either drop clipboard sync on Android or wire a thin
  JNI bridge to `android.content.ClipboardManager`.
- **`ctrlc` signal handler.** `ctrlc` is cross-platform but
  SIGINT-on-Android is meaningless — the activity lifecycle
  handles termination. Gate off or repurpose to wake the
  event loop.
- **`--capture` feature.** Pulls in `openh264`, `mp4`,
  `pcap-file`, `etherparse`. Not a user-facing feature and
  heavyweight to port. Disable via feature gate on Android
  (same pattern as Windows in `PLAN-packaging.md`).
- **`--headless` mode.** Meaningless on Android. Disable
  via `#[cfg]` gates.

### Input model

The Google TV Streamer ships with only a remote control (D-pad
+ voice + a handful of buttons). A remote is a catastrophic UX
for a mouse-driven SPICE desktop. Realistic options:

1. **Require a paired Bluetooth keyboard and mouse.** Zero
   engineering effort; the BT HID events arrive as normal
   key/pointer events through winit. Clearest MVP.
2. **Software pointer driven by D-pad.** Acceptable last
   resort for "oh, I forgot my keyboard" but slow and
   unpleasant. Deferred.
3. **Phone-as-trackpad.** Companion app or web page; out of
   scope.

For phones/tablets, touch-to-pointer mapping via winit is
free. A touch-first UI (gesture scrolling, on-screen
keyboard integration) is future work.

## Mission and problem statement

Produce a sideloadable APK of ryll that runs on Android 10+
(API 29+, arm64-v8a), with primary QA on a Google TV Streamer
running Android 14. MVP scope:

1. Launch the app on a Google TV Streamer and see an egui
   window (not a black screen, not a crash).
2. Open a `.vv` connection file (or accept connection details
   through an on-device UI) and connect to a SPICE server.
3. Display the remote desktop with correct colour and cursor.
4. Accept keyboard and pointer input from paired Bluetooth
   peripherals and forward it through the SPICE inputs
   channel.
5. Play back remote audio through AAudio.
6. Sideload cleanly via `adb install` onto a stock Google TV
   Streamer with developer mode enabled.

Out of MVP scope (tracked in Future work):

- USB redirection.
- Clipboard sync.
- Capture mode.
- WebDAV folder sharing (may work — the port channel is pure
  Rust — but needs verification and probably UI changes).
- TV-specific launcher metadata, Leanback integration,
  voice search.
- Google Play Store / F-Droid / Google TV catalog listings.
- Release signing.
- x86_64 Android emulator build (nice for CI; not a target
  user platform).

## Open questions

Each of these needs to be resolved before the corresponding
phase plan can be written.

1. **Can eframe 0.29 accept an `AndroidApp`?** Or do we need
   to drop eframe and drive egui + winit + wgpu directly? The
   difference between "add a feature flag and an
   `android_main` shim" and "rewrite `main.rs` and the render
   loop around raw winit" is roughly a factor of five in
   effort. **Investigate early in Phase 1.**

2. **Minimum API level?** AAudio requires API 26 (Android 8),
   `nusb` needs API 28 (Android 9), `android-activity`
   `GameActivity` wants API 28+. **Proposed: 29 (Android 10)
   min, 34 (Android 14) target.** Google TV Streamer ships
   with 14 so the target is comfortable.

3. **`cargo-apk` vs `xbuild` vs hand-written Gradle.**
   - `cargo-apk` is the easy path but has been lightly
     maintained and is stuck on older `ndk-glue`.
   - `xbuild` is more modern, handles signing, cross-platform
     packaging, but adds a new dependency and conventions.
   - `cargo-ndk` + a small Gradle project gives the most
     control and matches what every "real" Android project
     does, at the cost of having to own the Java/Kotlin side.
   **Proposed: start with `cargo-apk` for the MVP bring-up;
   migrate to a Gradle + `cargo-ndk` project once we need
   features `cargo-apk` can't provide (signing automation,
   TV manifest metadata, custom `Activity` subclasses).**

4. **Input model for the MVP.** Require BT keyboard/mouse,
   or ship D-pad pointer navigation in phase 1? **Proposed:
   require BT peripherals for MVP; document it loudly.
   D-pad pointer goes into future work.**

5. **Clipboard sync in MVP?** **Proposed: drop on Android
   for MVP.** Revisit via JNI bridge as a follow-up. The
   SPICE agent clipboard channel is cosmetic for most test
   scenarios.

6. **USB redirection in MVP?** Even on Android TV, USB host
   mode is often present via a USB-A port on the device. But
   `nusb` + the Android permission flow + the lack of
   `libusb` means meaningful integration work. **Proposed:
   out of MVP. Revisit once the display pipeline is stable.**

7. **Signing.** Debug-sign the MVP APKs (Android will refuse
   to sideload unsigned APKs). Real release signing is
   future work when we have a distribution channel.
   **Proposed: debug sign for MVP.**

8. **Distribution.** Attach the APK as a GitHub Release
   artifact, alongside the existing `.deb` / `.rpm` /
   `.tar.gz` / `.zip`. No store listings in MVP. **Proposed:
   release artifact only.**

9. **CI hardware.** GitHub's `ubuntu-latest` runners can
   cross-compile for Android fine. Do we want an emulator
   smoke test in CI, or only a build job? Emulators on
   GitHub runners are slow and flaky. **Proposed: build job
   only for MVP. Smoke testing is manual on real hardware.**

10. **Architecture coverage.** Ship arm64-v8a only (covers
    every target Android TV device and every Android phone
    of the last 5+ years), or also armeabi-v7a and x86_64?
    **Proposed: arm64-v8a only. Revisit if a user asks.**

11. **`/proc` metrics on Android.** Gate off, or implement
    via the Android-specific APIs (`Process.myTid()`,
    `Debug.threadCpuTimeNanos()` through JNI)? **Proposed:
    gate off for MVP; reintroduce via JNI if anyone wants
    in-app metrics on Android.**

12. **Lifecycle handling.** Android aggressively suspends
    backgrounded apps. How does the SPICE connection
    survive a screen-off / app-backgrounded event — do we
    disconnect and reconnect, or try to keep the tokio
    runtime alive via a foreground service? **Proposed:
    MVP disconnects on `onPause` and reconnects on
    `onResume`.** A foreground service is future work.

## Execution

Phase files are **not yet written**. The breakdown below is a
provisional sketch; expect it to change once the open
questions resolve.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Toolchain + boot an empty window | PLAN-android-apk-phase-01-toolchain.md | Not written | — |
| 2. Portability shims (`#[cfg]` gating) | PLAN-android-apk-phase-02-shims.md | Not written | — |
| 3. Android entrypoint (eframe vs bare winit) | PLAN-android-apk-phase-03-entrypoint.md | Not written | — |
| 4. Display + activity lifecycle | PLAN-android-apk-phase-04-display.md | Not written | — |
| 5. Input (BT keyboard/mouse → SPICE inputs) | PLAN-android-apk-phase-05-input.md | Not written | — |
| 6. Audio (AAudio via cpal) | PLAN-android-apk-phase-06-audio.md | Not written | — |
| 7. TV-specific manifest + launcher | PLAN-android-apk-phase-07-tv.md | Not written | — |
| 8. CI build + release artifact | PLAN-android-apk-phase-08-ci.md | Not written | — |
| 9. Docs | PLAN-android-apk-phase-09-docs.md | Not written | — |
| 10. Push audit | PLAN-android-apk-phase-10-push-audit.md | Not written | — |

### Phase 1: Toolchain + boot

Stand up the Android build toolchain (`rustup target add
aarch64-linux-android`, NDK install, `cargo-apk` configured)
and produce a minimal APK that launches, shows an egui
"Hello, ryll" window on a Google TV Streamer, and exits
cleanly. No SPICE, no audio — proves the eframe/winit/egui
story works at all. **This phase's answer to open question
(1) decides everything downstream.**

### Phase 2: Portability shims

Audit every `#[cfg(unix)]` / `target_os = "linux"` site and
add the corresponding `#[cfg(target_os = "android")]` arms.
Feature-gate `capture` and `headless` off on Android. Stub
`/proc` metrics. Replace `/tmp/ryll.log` with the activity's
cache dir. Drop `arboard` on Android or wire a no-op
backend. Land this as a standalone change that still builds
on the three existing platforms.

### Phase 3: Android entrypoint

Replace (or augment) `main.rs` so that on Android the library
exposes `android_main(AndroidApp)` from the `android-activity`
crate, plumbs the `AndroidApp` into winit's event loop, and
constructs the existing `RyllApp` from the normal eframe
lifecycle callbacks. If eframe can't accommodate this, this
phase drops eframe and replaces it with `egui-winit` +
`egui-wgpu` on desktop **and** Android. That's a bigger
change and would warrant its own sub-plan.

### Phase 4: Display + lifecycle

Wire the SPICE display channel's texture uploads into the
wgpu surface that winit hands us. Make the connection
survive `onPause` / `onResume` cleanly — for MVP this can
mean "disconnect and reconnect", but the implementation has
to not leak tokio tasks or the ring-buffer capture thread.

### Phase 5: Input

Map Android `KeyEvent` scancodes to SPICE scancodes in the
same shape as the existing `key_to_scancode` in
`src/channels/inputs.rs`. Map `MotionEvent` to SPICE pointer
events. Handle BT HID mouse wheel. For touch-only devices,
synthesise pointer events from single-finger touches, leave
gesture handling to future work.

### Phase 6: Audio

Verify cpal's AAudio backend works with the existing Opus
decode + resampler pipeline in `src/channels/playback.rs`.
Audio is the most likely component to "just work"; this
phase is primarily a test-and-tune pass.

### Phase 7: TV-specific manifest + launcher

Add `android.software.leanback` to `AndroidManifest.xml`,
declare `CATEGORY_LEANBACK_LAUNCHER`, ship a TV banner
image, and make sure the app shows up in the Google TV
launcher's apps row. Minimal — mostly manifest edits.

### Phase 8: CI build + release artifact

Extend `.github/workflows/release.yml` with an
`android-build` job on `ubuntu-latest`. Produce
`ryll-{version}-arm64-v8a.apk` and attach it to the GitHub
Release. Document the sideload workflow in
`docs/installation.md`. Debug-signed for MVP.

### Phase 9: Docs

- `docs/portability.md` — add an Android row to the
  supported-platforms table and a section explaining build
  requirements and device targets.
- `docs/installation.md` — Android section with
  `adb install` instructions and the "enable developer
  mode on Google TV" recipe.
- `README.md` — mention Android in the supported-platforms
  line.
- `ARCHITECTURE.md` — no changes expected; the core
  architecture is platform-agnostic.
- `AGENTS.md` — add Android build commands.

### Phase 10: Push audit

Work `PUSH-AUDIT.md` over the accumulated diff of phases 1-9
against `develop` — the whole port as one change, not the
last phase's diff alone, because the `#[cfg]` gating from
phase 2 is threaded through everything the later phases add.
Findings land as their own PR against `develop`, recorded
here under an *Items deferred from the push audit* heading —
the shape `PLAN-web-frontend.md` uses for its *Items deferred
from the post-Phase-N pre-push audit* sections, minus the
phase number, because this phase audits the whole plan rather
than a range of it. This plan is not complete until each one
is fixed or declined in writing. If
the audit finds nothing, record that here in one sentence.

Phases 1-9 have merged by the time this phase runs, so
`develop...HEAD` is empty on the audit branch and the
accumulated diff has to be assembled from the phases' merge
commits. Record each phase's merge commit in the `Merged`
column of the *Phase order* table above as that phase lands;
*Two ways this runbook is invoked* in `PUSH-AUDIT.md` has the
rest.

## Administration and logistics

### Success criteria

We will know the MVP has landed when:

* `cargo apk build --release --target aarch64-linux-android`
  produces a signed APK from a clean checkout.
* `adb install -r ryll-*.apk` succeeds on a Google TV
  Streamer running stock Google TV (Android 14).
* The launcher shows a ryll tile; tapping it opens the app.
* The app reads a `.vv` file (delivered via `adb push` and
  an `Intent.ACTION_VIEW`) and connects to a kerbside
  endpoint.
* The display channel renders the remote desktop at native
  resolution and correct colour.
* A paired Bluetooth keyboard and mouse generate keystrokes
  and pointer motion that the SPICE server sees.
* Remote audio plays through AAudio.
* Backgrounding and re-foregrounding the app reconnects
  cleanly and does not leak tokio tasks (verified via
  `adb shell dumpsys meminfo`).
* `pre-commit run --all-files` still passes on the existing
  three platforms.
* `cargo test --workspace` still passes on the existing
  three platforms.
* `docs/portability.md` and `docs/installation.md` are
  updated.
* The CI workflow produces a signed APK artifact on tag
  push.

### Future work

* **Phone and tablet UX.** Touch-first controls, on-screen
  keyboard toggle, gesture scrolling, portrait/landscape
  rotation handling, status-bar integration.
* **D-pad pointer.** For TV users without BT peripherals.
* **USB redirection on Android.** `nusb` + USB host
  permission intent + a device picker.
* **Clipboard sync.** JNI bridge to
  `android.content.ClipboardManager`.
* **MediaCodec for streaming-video.** Today ryll decodes
  MJPEG and VP8 in software. Routing streaming-video
  through Android's hardware decoders would materially
  help battery and performance on low-spec devices.
* **Foreground service** to keep the SPICE connection
  alive across app backgrounding.
* **Audio record channel** (microphone). Needs
  `RECORD_AUDIO` permission; defer until someone asks.
* **Play Store listing**, F-Droid publishing, Google TV
  catalog inclusion.
* **Release signing automation** with a keystore held as a
  GitHub Actions secret.
* **x86_64 Android emulator CI smoke test.**
* **armeabi-v7a / x86_64 architecture builds** if demand
  emerges.
* **Leanback-native UI** for TV navigation instead of
  retrofitting a desktop UI.
* **Chromecast receiver mode.** A separate plan — see the
  discussion in the chat that spawned this plan. Running a
  ryll app that can *also* accept tab casts from Chrome on
  the LAN (via openscreen / shanocast) on the same Google
  TV Streamer is a natural pairing.

### Bugs fixed during this work

(None yet — no implementation has started.)

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan. Because
this is a concept plan, the first "step" is to resolve the
open questions above — especially (1), the eframe / winit
/ Android integration question — before any phase plan is
written.
