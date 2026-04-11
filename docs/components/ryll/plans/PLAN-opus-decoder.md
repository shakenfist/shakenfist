# Replace audiopus with pure-Rust opus-decoder

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
TLS/RSA, LZ/GLZ compression), research as needed to give a
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
be done via a table like this in this master plan under the
Execution section:

```
| Phase | Plan | Status |
|-------|------|--------|
| 1. Message parsing | PLAN-thing-phase-01-parsing.md | Not started |
| 2. Decompression | PLAN-thing-phase-02-decomp.md | Not started |
| ...   | ...  | ...    |
```

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

PR 23 (hhding, merged 2026-04-11 as c416ee1) added audio
playback support to ryll using the `audiopus` crate (0.3.0-
rc.0), a safe wrapper around the C `libopus` library via
`audiopus_sys`. This works but has several problems:

1. **`audiopus` is abandoned.** Last commit: April 2021
   (5+ years ago). The 0.3.0-rc.0 has been an RC for five
   years with no stable release. The vendored C libopus
   source may be missing upstream security fixes.

2. **Native C dependency complicates builds.** `audiopus_sys`
   vendors libopus and builds it via cmake. The vendored
   `CMakeLists.txt` requires cmake >= 3.1, which newer cmake
   versions reject without setting
   `CMAKE_POLICY_VERSION_MINIMUM`. This already required a
   workaround in both CI workflows (`ci.yml`, `release.yml`)
   and the devcontainer Dockerfile.

3. **Cross-platform pain.** The cmake compatibility issue
   particularly affects macOS builds. The `unsafe impl Send
   for SendStream` in playback.rs is partly motivated by the
   C library's thread-safety model. Eliminating the C
   dependency removes one axis of platform concern.

4. **Supply chain risk.** Depending on an abandoned crate
   that vendors and compiles C code is a maintenance
   liability. Any future libopus CVE would require us to
   either fork audiopus_sys or find an alternative.

Meanwhile, a pure-Rust alternative now exists: the
`opus-decoder` crate (v0.1.1, published 2026-03-12,
MIT/Apache-2.0). It is:

- A pure Rust Opus decoder, `#![forbid(unsafe_code)]`, no
  FFI, no C dependencies.
- RFC 8251 conformant (passes all 12 test vectors).
- Supports SILK, CELT, and Hybrid modes; 8/12/16/24/48 kHz;
  mono and stereo; packet loss concealment.
- ~14,800 lines of Rust source.
- Published on crates.io with docs on docs.rs.

The crate is young (708 downloads, one month old) but its
conformance test coverage is strong. For ryll's use case
(decoding Opus packets received over SPICE playback channel),
a decoder is all we need. If we ever write a SPICE server
and need an encoder, we can use a different audio codec since
we'd control the server's codec selection.

### Why not build our own?

We evaluated building a Rust-native Opus implementation from
the RFC 6716 specification. A decoder-only implementation is
tractable (2-4 months for a DSP-experienced developer) but
`opus-decoder` already exists, passes conformance tests, and
is pure safe Rust. Building our own would duplicate effort
for no clear benefit. If `opus-decoder` proves unmaintained
in the future, its pure-Rust codebase would be straightforward
to fork, unlike the C/cmake dependency chain of audiopus.

## Mission and problem statement

Replace the `audiopus` crate with `opus-decoder` in ryll's
playback channel. This eliminates the native C dependency,
the cmake build requirement (for Opus specifically), the
`CMAKE_POLICY_VERSION_MINIMUM` workaround, and the
dependency on an abandoned crate.

The change should be transparent to users: Opus audio
playback must continue to work identically.

## Open questions

1. **Is cmake still needed by other dependencies after
   removing audiopus?** The CI workflows list cmake as a
   general build dependency. We need to verify whether any
   remaining crate (e.g. `aws-lc-sys` via reqwest on some
   platforms) still requires it before removing cmake from
   install steps. If cmake is only needed for audiopus_sys,
   we can remove it from CI and the devcontainer.

2. **Does `opus-decoder` handle all frame sizes correctly?**
   PLAN-pr23-followup.md item 4 notes that the current code
   assumes 10ms frames. `opus-decoder` provides
   `max_frame_size_per_channel()` and
   `MAX_FRAME_SIZE_48K` which should let us allocate
   correctly. Verify during implementation.

3. **Does `opus-decoder` handle mono correctly?**
   PLAN-pr23-followup.md item 3 notes the audiopus decoder
   is hardcoded to stereo. The `opus-decoder` constructor
   accepts a `channels: usize` parameter (1 or 2), so this
   bug can be fixed as part of the migration by passing the
   channel count from the SPICE START message.

## Execution

This is a small, focused change. It does not warrant
separate phase files.

### Step 1: Update Cargo.toml

- Remove the `audiopus = "0.3.0-rc.0"` dependency.
- Add `opus-decoder = "0.1"`.
- Update the comment block above the audio dependencies to
  remove references to audiopus_sys, cmake, and
  CMAKE_POLICY_VERSION_MINIMUM.

### Step 2: Update src/channels/playback.rs

The API mapping is straightforward:

**Old (audiopus):**
```rust
use audiopus::coder::Decoder;
use audiopus::packet::Packet;
use audiopus::MutSignals;

// Construction
let decoder = Decoder::new(
    audiopus::SampleRate::Hz48000,
    audiopus::Channels::Stereo,
)?;

// Decoding
let packet = Packet::try_from(audio_data);
let signals = MutSignals::try_from(&mut pcm[..]);
let samples = decoder.decode(Some(pkt), sig, false)?;
```

**New (opus-decoder):**
```rust
use opus_decoder::OpusDecoder;

// Construction — use channel count from START message
let decoder = OpusDecoder::new(48000, channels as usize)?;

// Decoding — simpler, just slices
let samples = decoder.decode(audio_data, &mut pcm, false)?;
```

Specific changes in playback.rs:

- Replace `use audiopus::coder::Decoder` with
  `use opus_decoder::OpusDecoder`.
- Change `opus_decoder: Option<audiopus::coder::Decoder>` to
  `opus_decoder: Option<opus_decoder::OpusDecoder>`.
- In the START handler, construct with
  `OpusDecoder::new(48000, self.channels as usize)` instead
  of hardcoded `Hz48000`/`Stereo`. This fixes
  PLAN-pr23-followup.md item 3.
- In the DATA handler, replace the `Packet::try_from` /
  `MutSignals::try_from` / `decoder.decode(Some(pkt), sig,
  false)` sequence with a direct
  `decoder.decode(audio_data, &mut pcm, false)` call.
- Size the PCM buffer using
  `OpusDecoder::MAX_FRAME_SIZE_48K * channels` instead of
  the hardcoded `48000 / 100 * 2`. This fixes
  PLAN-pr23-followup.md item 4.
- The decode return value is samples per channel, so
  multiply by channels when slicing: `&pcm[..samples *
  channels]`. This matches the existing `samples * 2`
  pattern but generalises for mono.

### Step 3: Update CI workflows

- In `.github/workflows/ci.yml`: remove the
  `CMAKE_POLICY_VERSION_MINIMUM: "3.5"` env var.
- In `.github/workflows/release.yml`: remove the
  `CMAKE_POLICY_VERSION_MINIMUM: "3.5"` env var.
- Investigate whether `cmake` can be removed from the
  `apt-get install` lines. If other deps still need it,
  leave it but remove the audiopus-specific comments.

### Step 4: Update devcontainer Dockerfile

- In `.devcontainer/Dockerfile`: update the comment that
  says `cmake: Some crate build dependencies (audiopus_sys)`
  to reflect the actual remaining cmake users, or remove
  cmake entirely if no other dep requires it.

### Step 5: Update PLAN-pr23-followup.md

Several items in the PR 23 follow-up plan are affected:

- **Item 3** (Opus decoder hardcoded to stereo): Mark as
  resolved by this work — `opus-decoder` constructor accepts
  a channel count parameter.
- **Item 4** (Opus decode buffer assumes 10ms frames): Mark
  as resolved — using `MAX_FRAME_SIZE_48K` for correct
  sizing.
- **Item 6** (Make audio Linux-only): Update to remove the
  audiopus_sys/cmake references. The `unsafe impl Send`
  issue and cpal thread-safety concern remain and are
  unrelated to the codec crate.
- **Item 12** (audiopus is effectively abandoned): Mark as
  resolved by this work. Replace the options list with a
  note that we migrated to `opus-decoder`.

### Step 6: Update PLAN-packaging.md and phase-02-ci

- Update the build dependency lists in PLAN-packaging.md
  to remove cmake if it's no longer needed, or update the
  comments explaining why it's present.
- Update PLAN-packaging-phase-02-ci.md similarly.

### Step 7: Update documentation

- Update `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` if
  they reference audiopus or the cmake requirement.
- Update `Cargo.toml` comments.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (rustfmt,
  clippy with `-D warnings`, shellcheck).
* New code follows existing patterns: channel handler
  structure, message parsing via `byteorder`, async tasks
  via tokio, event communication via mpsc channels.
* There are unit tests for new logic, and the existing tests
  still pass (`make test`).
* Lines are wrapped at 120 characters, single quotes for
  Rust strings where applicable.
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` have been
  updated if the change adds or modifies channels, message
  types, or compression algorithms.
* Documentation in `docs/` has been updated to describe any
  new features or configuration options.
* If the changes affect SPICE protocol behaviour, the
  relevant documentation in `shakenfist/kerbside/docs/` has
  also been reviewed and updated if needed.
* The `audiopus` crate no longer appears in `Cargo.toml` or
  `Cargo.lock`.
* `CMAKE_POLICY_VERSION_MINIMUM` no longer appears in any
  CI workflow.
* Opus audio decoding continues to work (verified by
  connecting to a SPICE server with audio enabled).
* The build no longer requires cmake for the Opus codec
  (cmake may still be needed for other dependencies).

### Future work

- **Encoder support**: If ryll ever needs to encode audio
  (e.g. for a SPICE record channel or a SPICE server),
  `opus-decoder` is decoder-only. Options at that point:
  a different codec (since we'd control the server), a
  future encoder crate, or the `unsafe-libopus` transpiled
  crate as a stopgap.
- **opus-decoder maturity**: The crate is one month old with
  708 downloads. Monitor for maintenance activity. If it
  becomes abandoned, forking the pure Rust source is
  straightforward — far easier than maintaining a C/cmake
  dependency chain.
- **cpal upgrade**: PLAN-pr23-followup.md item 11 (upgrade
  cpal from 0.15 to 0.17) is independent of this work and
  remains open.
- **Lock-free audio buffer**: PLAN-pr23-followup.md item 10
  (mutex in audio callback) is independent of this work and
  remains open.

### Bugs fixed during this work

This plan also fixes two bugs from PLAN-pr23-followup.md as
a side effect of the migration:

- **Item 3**: Opus decoder hardcoded to stereo — fixed by
  passing `self.channels` to `OpusDecoder::new()`.
- **Item 4**: Opus decode buffer too small for >10ms frames
  — fixed by using `MAX_FRAME_SIZE_48K * channels`.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
