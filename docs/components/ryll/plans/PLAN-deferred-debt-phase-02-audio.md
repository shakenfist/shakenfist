# Phase 2: Audio correctness

Part of [PLAN-deferred-debt.md](/components/ryll/plans/PLAN-deferred-debt/).

## Scope

Fix the bugs that produce audibly wrong audio output.
Four sub-tasks, each a separate commit:

| Step | Description | Source |
|------|-------------|--------|
| 2a | Resampler stereo corruption | PLAN-pr23-followup.md #1 |
| 2b | Resampler underrun buffer pollution | PLAN-pr23-followup.md #2 |
| 2c | I16/F32 closure capture inconsistency | PLAN-pr23-followup.md #5 |
| 2d | Device channel mapping | PLAN-pr23-followup.md #8 |

## Current state

The audio playback pipeline in `ryll/src/channels/playback.rs`
(485 lines) works as follows:

1. The SPICE server sends a `START` message with
   `channels` (typically 2 for stereo), `sample_rate`,
   and `format` (raw PCM or Opus).
2. `start_audio_output()` opens the default audio device
   with the **device's** default config (channel count
   and sample rate), creates a `Resampler` to convert
   from the source rate to the device rate, and starts
   a cpal output stream.
3. Incoming `DATA` messages are decoded (raw PCM or Opus)
   and pushed as `i16` samples into a shared
   `Arc<Mutex<VecDeque<i16>>>` ring buffer.
4. The cpal audio callback locks the buffer, calls
   `Resampler::next_sample()` for each output sample,
   applies volume, and writes to the output.

## 2a. Resampler stereo corruption

### Bug

The `Resampler` struct (playback.rs:77-114) has no
concept of audio channels. It treats the sample buffer
as a flat stream of mono samples. For stereo audio, the
buffer contains interleaved L/R pairs: `[L0, R0, L1,
R1, ...]`. The resampler interpolates between `buffer[0]`
and `buffer[1]` -- which are L and R samples of the same
time slot, not consecutive time points of the same
channel.

This produces corrupted audio where left and right
channel data is blended at the wrong phase.

### Fix

Rewrite `Resampler` to be channel-aware. Add a
`channels` field set at construction time. Change
`next_sample` to return a **frame** (one sample per
channel) rather than a single sample.

The updated `Resampler` should:

1. Store `channels: usize` alongside `ratio` and `pos`.
2. Provide a `next_frame` method that:
   - Computes `idx` and `frac` based on `self.pos` (as
     now, but in units of frames, not samples).
   - For each channel `c` in `0..self.channels`:
     - `a = buffer[idx * channels + c]`
     - `b = buffer[(idx + 1) * channels + c]`
     - Interpolate: `a + (b - a) * frac`
   - Advance `self.pos` by `self.ratio`.
   - Consume whole frames (not individual samples)
     from the front of the buffer:
     `consume = self.pos as usize` frames =
     `consume * channels` samples to pop.
3. Update `write_samples_i16` and `write_samples_f32`
   to call `next_frame()` and write one frame
   (channels samples) per iteration, advancing the
   output pointer by `channels` samples each step.

If the output device has a different channel count from
the source (addressed in step 2d), the frame from the
resampler is the **source** frame and needs to be mapped
to the output channel count. Steps 2a and 2d should be
coordinated; 2a makes the resampler channel-aware, 2d
maps between source and output channel counts.

### Files to modify

- `ryll/src/channels/playback.rs` -- `Resampler`,
  `next_sample` → `next_frame`, `write_samples_i16`,
  `write_samples_f32`, and
  `start_audio_output` (pass `self.channels` to
  Resampler constructor)

### Testing

- `cargo test --workspace` must pass.
- Manual test: play audio through a VM session and
  verify stereo separation is correct (left events
  should be heard from the left speaker).

## 2b. Resampler underrun buffer pollution

### Bug

When the audio buffer is exhausted (underrun), the
`next_sample` method (playback.rs:94-96) pads the shared
`VecDeque<i16>` with zero samples:

```rust
while buffer.len() < idx + 2 {
    buffer.push_back(0);
}
```

These zeros persist in the shared ring buffer and are
later consumed as real audio data by subsequent calls.
This produces audible clicks and corruption after any
underrun event.

### Fix

Return silence directly without modifying the buffer.
In the rewritten `next_frame` (from step 2a), if the
buffer does not contain enough data for interpolation
(i.e. `buffer.len() < (idx + 2) * channels`), return
a frame of zeros without pushing anything into the
buffer or advancing `self.pos`.

```rust
if buffer.len() < (idx + 2) * self.channels {
    return vec![0i16; self.channels];
}
```

This should be implemented as part of step 2a since
`next_frame` replaces `next_sample`. If implemented
separately before 2a, the fix to the current
`next_sample` is:

```rust
if buffer.len() < idx + 2 {
    return 0;
}
```

Remove the `while` loop entirely.

### Files to modify

- `ryll/src/channels/playback.rs` -- `next_sample` /
  `next_frame`

### Testing

- `cargo test --workspace` must pass.
- The fix produces silence on underrun instead of
  corrupted audio. This is the correct behaviour for
  audio playback.

## 2c. I16/F32 closure capture inconsistency

### Bug

In `start_audio_output` (playback.rs:414-444), the F32
branch (line 428) creates a redundant clone of
`self.volume_control`:

```rust
cpal::SampleFormat::I16 => {
    let resampler = resampler.clone();
    // uses buffer, vol from lines 410-411
    device.build_output_stream(...)
}
cpal::SampleFormat::F32 => {
    let vol = self.volume_control.clone(); // redundant!
    let resampler = resampler.clone();
    // uses buffer from line 410, re-cloned vol
    device.build_output_stream(...)
}
```

The I16 branch captures `buffer` and `vol` (from lines
410-411), while the F32 branch captures `buffer` and a
fresh `vol` clone. This compiles because match arms
independently move captured variables. The re-clone is
harmless at runtime (both point to the same underlying
`Arc`) but is a copy-paste inconsistency that could
mislead future readers.

### Fix

Clone `buffer`, `vol`, and `resampler` once before the
match, and let both arms capture the same pre-cloned
variables. Remove the per-arm cloning.

Concretely, lines 410-412 already clone `buffer` and
`vol`. Just remove the redundant `let vol =
self.volume_control.clone()` at line 428 and let the F32
branch use the same `vol` as the I16 branch.

For `resampler`, both arms clone it identically. Move
the clone before the match so only one clone exists.

### Files to modify

- `ryll/src/channels/playback.rs` -- `start_audio_output`

### Testing

- `cargo test --workspace` must pass.
- No behavioural change expected.

## 2d. Device channel mapping

### Bug

`start_audio_output` (playback.rs:403-407) opens the
audio device with the device's default channel count:

```rust
let config = cpal::StreamConfig {
    channels: default_config.channels(),
    sample_rate: default_config.sample_rate(),
    buffer_size: cpal::BufferSize::Default,
};
```

The source channel count (`self.channels`, from the
SPICE START message) is ignored. If the device defaults
to 6 channels (5.1 surround) but the source is stereo,
or vice versa, the audio will be garbled because samples
are written sequentially without any channel mapping.

The `device_channels` variable (line 409) is computed
but only used in a log message (line 458).

### Fix

Force the output stream configuration to match the
source channel count. Replace `default_config.channels()`
with `self.channels as u16` in the `StreamConfig`:

```rust
let config = cpal::StreamConfig {
    channels: self.channels as u16,
    sample_rate: default_config.sample_rate(),
    buffer_size: cpal::BufferSize::Default,
};
```

This tells cpal to open the device with the same number
of channels as the source. Most audio devices and ALSA
will accept stereo even if the device technically
supports more channels. If the device does not support
the requested channel count, cpal will return an error
from `build_output_stream`, which is already handled at
line 445-450.

If we want to be more robust, we could check
`device.supported_output_configs()` to verify the
requested channel count is supported before building the
stream. If not, fall back to the device default and
implement channel mapping (e.g. stereo → 5.1 by
duplicating or zero-padding). However, the simple
approach (force source channels) should work for the
vast majority of setups where the device supports
stereo.

### Files to modify

- `ryll/src/channels/playback.rs` -- `start_audio_output`

### Testing

- `cargo test --workspace` must pass.
- Manual test on a system with non-stereo default output
  (e.g. 5.1 surround) to verify audio plays correctly.

## Items verified as already fixed

The following item from PLAN-pr23-followup.md was found
to already be resolved in the current code:

- **#9: playback_client() should use common_client().**
  `playback_client()` at
  `shakenfist-spice-protocol/src/logging.rs:262` already
  delegates to `common_client().unwrap_or("unknown")`.
  No change needed.

## Commit sequence

The recommended order:

1. **2a + 2b** together: Rewrite `Resampler` to be
   channel-aware with `next_frame`, and fix underrun
   handling at the same time. These changes are
   intertwined -- the underrun fix modifies the same
   method being rewritten for stereo support. A single
   commit is cleaner than two edits to code that is
   being replaced.
2. **2c**: Fix closure capture inconsistency.
3. **2d**: Force device channel count to match source.

Steps 2c and 2d are independent and can be done in
either order. Step 2a+2b should come first since 2d
depends on the resampler being channel-aware.

## Administration

### Back brief

Before executing any step of this plan, please back
brief the operator as to your understanding of the plan
and how the work you intend to do aligns with that plan.
