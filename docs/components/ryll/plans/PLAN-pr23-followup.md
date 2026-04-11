# PR 23 follow-up: audio playback channel

## Situation

PR 23 (hhding, merged as c416ee1) added a SPICE playback
channel with Opus and raw PCM support, volume control, and a
resampler. Pre-merge fixes addressed magic numbers, ring
buffer cap, bugreport arithmetic, clippy warnings, ALSA
headers in the devcontainer/CI, and cmake compatibility for
the macOS build. This plan tracks the remaining items
identified during manual review and by the automated reviewer.

## Must fix (correctness bugs)

1. **Resampler is channel-unaware (auto-review item 2).**
   The resampler treats the sample buffer as a flat stream of
   mono samples, interpolating between consecutive values.
   For stereo audio, consecutive samples are left/right, not
   sequential time points. Interpolating between L and R
   samples produces corrupted audio. Fix: rewrite the
   resampler to consume `channels` samples at a time and
   interpolate each channel independently. Alternatively,
   use a well-tested resampling library like `rubato` or
   `dasp`.
   Location: `src/channels/playback.rs` `Resampler::next_sample`.

2. **Resampler pads buffer with zeros on underrun
   (auto-review item 3).** When the audio buffer underruns,
   `next_sample` calls `buffer.push_back(0)` to pad. This
   inserts fake zero samples into the shared ring buffer that
   will later be consumed as real data. Fix: return 0
   (silence) directly without modifying the buffer.
   Location: `src/channels/playback.rs:94-96`.

3. **Opus decoder hardcoded to 48kHz stereo
   (auto-review item 5).** ~~The Opus decoder is always
   created with `Hz48000` and `Stereo`, ignoring the
   `self.channels` value from the START message.~~ **Fixed
   by PLAN-opus-decoder.md**: migrated to `opus-decoder`
   crate which accepts `channels` as a constructor parameter,
   so `self.channels` from the START message is now used.

4. **Opus decode buffer assumes 10ms frames
   (auto-review item 6).** ~~The buffer `vec![0i16; 48000 /
   100 * 2]` allocates 960 samples (10ms at 48kHz stereo).~~
   **Fixed by PLAN-opus-decoder.md**: migrated to
   `opus-decoder` crate and sized the buffer using
   `OpusDecoder::MAX_FRAME_SIZE_48K * channels`, which
   covers all valid Opus frame sizes up to 120ms.

5. **I16/F32 closure capture inconsistency
   (auto-review item 7).** In `start_audio_output`, the F32
   branch creates a redundant `vol` clone that shadows the
   outer one, while the I16 branch uses the original. This
   is a copy-paste issue. Fix: clone `buffer` and `vol` once
   before the match, move those clones into whichever closure
   is built.
   Location: `src/channels/playback.rs` `start_audio_output`.

## Should fix

6. **Make audio Linux-only (our review + auto-review item 1).**
   The `unsafe impl Send for SendStream` is only correct on
   Linux (ALSA is thread-safe; CoreAudio and WASAPI are not).
   Long-term fix: gate the entire playback module and its
   deps behind `#[cfg(target_os = "linux")]`, provide a stub
   on other platforms. **Note**: the cmake/audiopus_sys
   concern is resolved by PLAN-opus-decoder.md (migrated to
   pure-Rust `opus-decoder`), but the cpal thread-safety
   issue remains independent of the codec crate.

7. **No graceful shutdown handling (auto-review item 10).**
   The playback channel's `run()` loop doesn't check
   `SHUTDOWN_REQUESTED`. During graceful shutdown it will
   keep running until the tokio runtime is forcefully dropped.
   Fix: add `SHUTDOWN_REQUESTED` check or use `tokio::select!`
   with a shutdown signal, consistent with other channels.

8. **No device channel mapping (auto-review item 8).**
   The code opens the audio device with its default channel
   count but the source is stereo. If the device has a
   different channel count, audio will be garbled. Fix: force
   the output config to match the source, or implement
   channel mapping.

9. **`playback_client()` should use `common_client()`.**
   PR 22 (merged before PR 23) introduced a `common_client()`
   helper for ack/pong name lookup. The `playback_client()`
   function hardcodes the same match arms instead of
   delegating. Fix: use `common_client()` like the other
   `*_client()` functions.

10. **Mutex lock in audio callback (auto-review item 4).**
    The cpal audio callback (real-time thread) locks a
    `Mutex<VecDeque<i16>>` and a `Mutex<Resampler>`. If the
    network thread holds the buffer lock while pushing data,
    the audio callback blocks, causing glitches. Fix:
    consider a lock-free ring buffer (`ringbuf` crate or
    `crossbeam` channel).

## Should consider

11. **`cpal` version 0.15 is outdated.** Current stable is
    0.17.3 (two years newer). The API changed in 0.16 and
    0.17 but migration is typically straightforward. Upgrade
    when convenient.

12. **`audiopus` is effectively abandoned.**
    ~~The crate is a thin safe wrapper around libopus via
    `audiopus_sys`. Last commit: April 2021 (5+ years ago).~~
    **Resolved by PLAN-opus-decoder.md**: migrated to
    `opus-decoder` (pure Rust, `#![forbid(unsafe_code)]`,
    RFC 8251 conformant, no C/cmake dependency). This
    eliminates the abandoned crate, the vendored C code, and
    the cmake compatibility workarounds.

13. **MODE message byte offset verification
    (auto-review item 11).** The MODE handler reads the mode
    from `payload[4..5]` (skipping a 4-byte timestamp). The
    START handler reads channels from `payload[0..3]`. Both
    should be verified against the SPICE protocol spec and
    documented with comments citing the struct definitions.

14. **DATA timestamp skip undocumented
    (auto-review item 12).** `let audio_data = &payload[4..]`
    skips a 4-byte timestamp prefix with no comment. Add a
    brief comment.

15. **Missing `PlaybackChannel` re-export from mod.rs
    (auto-review item 9).** Other channels have `pub use`
    re-exports; playback only has `pub mod`. Minor style
    inconsistency.

16. **Poisoned mutex recovery inconsistency
    (auto-review item 14).** The `into_inner()` recovery
    pattern is applied to one lock site in `record_received`
    but not others in `TrafficBuffers`. Apply consistently.

17. **STATS_BAR_HEIGHT doubled (auto-review item 13).**
    Changed from 10 to 20 to accommodate the volume slider.
    Consider auto-sizing based on content instead.

## Test coverage gaps (auto-review item 15)

- Unit tests for `VolumeControl` (clamping, mute,
  effective_volume).
- Unit tests for `Resampler` (1:1 ratio, 2:1 upsampling,
  fractional ratios, stereo correctness once fixed).
- Unit tests for message parsing (START, MODE, DATA with
  known payloads).
- Integration test for playback channel START/DATA/STOP
  lifecycle.

## Administration

### Tracking

Items 1-5 are correctness bugs that affect audio quality.
Item 6 is a platform-correctness issue. Items 7-10 are
robustness. Items 11-17 are polish. All can be addressed
independently. Items 1-5 should be prioritised because they
produce audibly wrong output.

### Context

This plan was created as part of the follow-up to merging
PR 23. The auto-reviewer's full analysis is available on the
PR page at https://github.com/shakenfist/ryll/pull/23.
