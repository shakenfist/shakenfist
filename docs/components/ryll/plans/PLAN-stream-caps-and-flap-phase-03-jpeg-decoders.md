# Phase 3 — Fast JPEG decode

Phase 3 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the
master plan and the relevant source files; ground your
answers in what the code actually does today. For platform
APIs (ImageIO, WIC, VA-API) and the third-party crates this
phase introduces (`mozjpeg`, `objc2-image-io`, `windows`,
`libloading`), research as needed to give a confident answer.
Flag any uncertainty explicitly rather than guessing.

## Goal

Replace the pure-Rust `jpeg-decoder` crate (currently called
from `shakenfist-spice-renderer/src/channels/display.rs::decode_mjpeg_frame`)
with a platform-optimal MJPEG decoder selected at runtime:

- **macOS** → ImageIO (`CGImageSource*`) — uses Apple Silicon's
  dedicated media block.
- **Windows** → WIC (`IWICBitmapDecoder` with
  `GUID_ContainerFormatJpeg`) — part of the OS; uses hardware
  via the codec stack where the driver supports it.
- **Linux** → VA-API (probed via `dlopen` of `libva.so.1` and
  `libva-drm.so.2`) with `libjpeg-turbo` via the `mozjpeg`
  crate as the always-available fallback.
- **Other** → vendored `libjpeg-turbo` via `mozjpeg`, or the
  pure-Rust `jpeg-decoder` as a last resort.

A single binary per OS that adapts to whatever is available at
runtime. Active backend is surfaced in bug reports
(`mjpeg_decoder_backend: "ImageIO"` etc.) so a report
identifies the path that ran.

## Scope

In scope:

- New `JpegDecoder` trait + `best_for_platform()` selector in
  `shakenfist-spice-compression/src/jpeg.rs` (new module).
- Per-platform backend implementations:
  - `JpegDecoderRsDecoder` — wraps the existing pure-Rust path
    (`jpeg-decoder` crate); preserves current behaviour as the
    universal fallback.
  - `MozJpegDecoder` — `mozjpeg` crate with the vendored
    libjpeg-turbo build feature so we don't add a runtime
    system dependency anywhere. Used as the cross-platform
    SIMD-accelerated baseline.
  - `ImageIoDecoder` — macOS only. `objc2-image-io` bindings.
  - `WicDecoder` — Windows only. `windows` crate with the
    `Win32_Graphics_Imaging` feature.
  - `VaapiDecoder` — Linux only. **`libloading::Library::new("libva.so.1")`
    at startup** (not link-time dependency), probes for
    `VAProfileJPEGBaseline` + `VAEntrypointVLD`; returns
    `None` from `try_new()` if anything is missing so the
    selector falls through to mozjpeg.
- Replace `decode_mjpeg_frame(...)` call sites in `display.rs`
  with `self.jpeg_decoder.decode(...)`. The selected backend
  is stored on `DisplayChannel` at construction time.
- New per-stream snapshot field `mjpeg_decoder_backend: String`
  exposing the active backend's name.
- New aggregate fields on `DisplaySnapshot`: per-call duration
  ring (min/max/mean) so bug reports show the decoder's actual
  performance over the session.
- Unit tests per backend that don't require the OS framework
  (round-trip via `mozjpeg::Compress` for the cross-platform
  baseline; `#[cfg(target_os = "...")]` for OS-specific
  backends with a small embedded fixture JPEG).
- Per-platform manual smoke test as step 3H.

Out of scope:

- H.264 / VP8 / VP9 video codecs — that's phase 6 (multi-codec).
- Removing `jpeg-decoder` from `Cargo.toml`. We keep it as the
  last-resort fallback; the trait object lets us swap freely.
- NVDEC, V4L2 M2M JPEG, or other rare Linux hardware paths.
  These can be added later as additional dlopen-probed backends
  if a real user needs them.
- A unified "decoder" trait that also covers video codecs. The
  call shape differs enough (stateless per-frame for JPEG;
  stateful with reference frames for H.264) that two traits is
  the right answer.

## Open questions

- **Q1 (decide now): which Rust ImageIO binding to use?**
  Candidates:
  - `objc2-image-io` (part of the `objc2` family, active,
    type-safe). **Prefer this.**
  - `core-graphics` + raw `extern "C"` to ImageIO via
    `image-io-sys`. Older approach.
  - `image-rs` re-exports — not actually a binding.

  Decision: try `objc2-image-io` first; fall back to direct
  `extern "C"` calls if the binding is missing what we need
  (sometimes the case for `CGImageSourceCreateImageAtIndex`
  options dictionaries).

- **Q2 (decide now): vendored libjpeg-turbo for `mozjpeg`?**
  The `mozjpeg-sys` crate supports `with-simd` (default) and
  builds libjpeg-turbo from source if pkg-config doesn't find
  it. **Decision: enable the vendored build path so the
  produced binary has no runtime libjpeg-turbo dependency.**
  Bigger binary, simpler deployment.

- **Q3 (decide in step 3E): which DRM render node to use for
  VA-API?** `/dev/dri/renderD128` is the typical first GPU.
  Should we enumerate `/dev/dri/renderD*` and pick the best?
  Probably overkill — pick `renderD128`, fall through to
  mozjpeg if it doesn't yield a JPEG-capable VA display. The
  step plan can revisit if multi-GPU systems show up in
  practice.

- **Q4 (open): does `MozJpegDecoder` belong in
  `shakenfist-spice-compression` (where the LZ4/GLZ/LZ
  decoders live) or directly in the renderer crate?** The
  compression crate is the natural home — it's already where
  the existing `decompress_spice_lz4` and friends live, and
  the per-platform decoders go alongside. **Decision: yes,
  add `jpeg` module to `shakenfist-spice-compression`.** A
  `jpeg` Cargo feature defaults on; OS-specific backends gate
  on `cfg!(target_os = ...)`.

- **Q5 (decide in step 3E): how does `VaapiDecoder` handle the
  case where libva loads but no JPEG profile is present?**
  Return `None` from `try_new()`. The selector cascades to
  mozjpeg. The probe must be cheap (one query call, no actual
  decode) so it doesn't add startup latency.

## Design notes

### Architecture

```
                           runtime probe order (left to right)
  macOS    → ImageIoDecoder → MozJpegDecoder → JpegDecoderRsDecoder
  Windows  → WicDecoder     → MozJpegDecoder → JpegDecoderRsDecoder
  Linux    → VaapiDecoder*  → MozJpegDecoder → JpegDecoderRsDecoder
  other    →                  MozJpegDecoder → JpegDecoderRsDecoder

  * dlopen-probed at startup; only selected if libva loads and
    VAProfileJPEGBaseline / VAEntrypointVLD is supported.
```

The selector runs once at session start (i.e. in
`DisplayChannel::new`). The chosen backend is stored as
`Arc<dyn JpegDecoder>` on the channel. We do not re-probe
mid-session — a backend that worked at startup is assumed to
keep working.

### Trait

```rust
// shakenfist-spice-compression/src/jpeg.rs (new)
pub trait JpegDecoder: Send + Sync {
    /// Decode `data` (a full JPEG byte stream including SOI/EOI
    /// markers, or with DHT injection already performed by the
    /// caller — same input shape as today's `decode_mjpeg_frame`)
    /// into RGBA pixels. Returns `None` on decode failure.
    fn decode(&self, data: &[u8]) -> Option<DecodedJpeg>;

    /// Human-readable name surfaced in bug reports.
    /// E.g. "ImageIO", "WIC", "VA-API", "libjpeg-turbo",
    /// "jpeg-decoder".
    fn name(&self) -> &'static str;
}

pub struct DecodedJpeg {
    pub rgba: Vec<u8>,
    pub width: u32,
    pub height: u32,
}

pub fn best_for_platform() -> Arc<dyn JpegDecoder> { ... }
```

`DecodedJpeg` is a small concrete type rather than a tuple so
each backend can add an explicit width/height check before
allocating the RGBA buffer (defends against runaway sizes from
malformed JPEGs).

### Replacement of `decode_mjpeg_frame`

Today (`display.rs:88-134`):

```rust
pub(crate) fn decode_mjpeg_frame(data: &[u8]) -> Option<(Vec<u8>, u32, u32)> {
    // ... jpeg-decoder code ...
}
```

After phase 3:

```rust
// The function moves into the trait (as JpegDecoderRsDecoder's
// implementation of decode()). The call site in display.rs's
// STREAM_DATA handler becomes:
match self.jpeg_decoder.decode(frame_data) {
    Some(DecodedJpeg { rgba, width, height }) => { ... },
    None => { ... },
}
```

`DisplayChannel::new` takes `Arc<dyn JpegDecoder>` as a new
constructor arg (or computes it internally — see step 3A).

### Per-platform notes

#### `MozJpegDecoder`

```rust
use mozjpeg::Decompress;

impl JpegDecoder for MozJpegDecoder {
    fn decode(&self, data: &[u8]) -> Option<DecodedJpeg> {
        let decomp = Decompress::new_mem(data).ok()?;
        let mut rgba = decomp.rgba().ok()?;
        let w = rgba.width() as u32;
        let h = rgba.height() as u32;
        let pixels: Vec<u8> = rgba.read_scanlines().ok()?;
        Some(DecodedJpeg { rgba: pixels, width: w, height: h })
    }
    fn name(&self) -> &'static str { "libjpeg-turbo" }
}
```

The `mozjpeg` crate's API has shifted across versions —
implementing-agent should pin the version explicitly and check
the actual API in `Cargo.lock`.

#### `ImageIoDecoder` (macOS)

```rust
// Roughly: build CFData from &[u8], CGImageSourceCreateWithData,
// CGImageSourceCreateImageAtIndex, draw into a CGBitmapContext
// configured for RGBA premultiplied, read out the byte buffer.
// objc2-image-io provides safer wrappers but at the end of the
// day this is a CoreGraphics dance.
```

Color space caveats: ImageIO returns BGRA on macOS by default
when you draw into a generic RGB context. The bitmap context
should be `kCGImageAlphaNoneSkipFirst` for opaque JPEGs (and
the output rearranged to RGBA). Test this carefully — colour
channel swaps are easy to ship and never notice until someone
files a "everything's blue" bug.

#### `WicDecoder` (Windows)

```rust
// IWICImagingFactory::CreateDecoderFromStream with
// GUID_ContainerFormatJpeg, IWICBitmapDecoder::GetFrame(0),
// IWICFormatConverter to GUID_WICPixelFormat32bppRGBA, then
// IWICBitmapSource::CopyPixels into a Vec<u8>.
```

The `windows` crate provides full WIC bindings; need the
`Win32_Graphics_Imaging` feature in `Cargo.toml`. COM init
required (`CoInitializeEx` once per thread). Since
DisplayChannel runs in a tokio task, this needs care — either
init COM lazily on first use with a thread-local guard, or run
WIC decode on a `spawn_blocking` thread that has its own COM
init.

#### `VaapiDecoder` (Linux, dlopen)

```rust
// 1. Library::new("libva.so.1") + Library::new("libva-drm.so.2")
//    via libloading.
// 2. dlsym for vaInitialize, vaGetDisplayDRM,
//    vaQueryConfigProfiles, vaQueryConfigEntrypoints,
//    vaCreateConfig, vaCreateContext, vaCreateSurfaces,
//    vaCreateBuffer, vaBeginPicture, vaRenderPicture,
//    vaEndPicture, vaSyncSurface, vaDeriveImage, vaMapBuffer,
//    vaUnmapBuffer, vaDestroyImage, vaDestroyBuffer,
//    vaDestroyContext, vaDestroyConfig, vaDestroySurfaces,
//    vaTerminate, vaErrorStr.
// 3. open("/dev/dri/renderD128") for vaGetDisplayDRM.
// 4. vaQueryConfigProfiles, scan for VAProfileJPEGBaseline.
//    None if absent.
// 5. vaQueryConfigEntrypoints(JPEGBaseline), scan for
//    VAEntrypointVLD. None if absent.
// 6. (Optional) decode a tiny embedded test JPEG to confirm
//    end-to-end. None if it fails.
// 7. Return Some(VaapiDecoder { ... }) holding the loaded
//    libraries (must outlive the decoder) + VADisplay handle.
```

This is the highest-risk backend by a wide margin. The
`VABufferType`, `VAPictureParameterBufferJPEGBaseline`,
`VASliceParameterBufferJPEGBaseline`, and
`VAHuffmanTableBufferJPEGBaseline` structures need to be
populated by parsing the JPEG header ourselves (libva doesn't
do that — VA-API is a low-level command-buffer interface). A
known-good reference is **ffmpeg's
`libavcodec/vaapi_decode.c`** and **chromium's
`media/gpu/vaapi/vaapi_jpeg_decoder.cc`**.

If `VaapiDecoder` proves too complex within the phase budget,
**land the trait + mozjpeg without it and treat VA-API as a
follow-up.** Better to ship a 3-4× speed-up across all
platforms than to block on the hardest case. This is captured
as Q5 in the open questions and as a decision point in step
3E's brief.

### Snapshot additions

`StreamSnapshot` adds:

```rust
/// Name of the active MJPEG decoder backend chosen at
/// session start. One of "ImageIO", "WIC", "VA-API",
/// "libjpeg-turbo", "jpeg-decoder". Identical for all
/// streams in the same session.
pub mjpeg_decoder_backend: String,
```

`DisplaySnapshot` adds:

```rust
/// Min/max/mean of MJPEG decode duration over the most
/// recent N decodes (cap MJPEG_RECENT_DURATIONS_CAP, like
/// the existing decode_recent_* fields for non-stream image
/// decode). Zero when no MJPEG frame has been decoded.
pub mjpeg_decode_recent_min_us: u32,
pub mjpeg_decode_recent_max_us: u32,
pub mjpeg_decode_recent_mean_us: u32,
/// Total MJPEG decodes attempted since session start, and
/// how many returned None.
pub mjpeg_decode_total_count: u64,
pub mjpeg_decode_failed_count: u64,
```

Per-stream `last_decode_duration_us` already exists for
MJPEG (set in step 1F of phase 1) — that one stays; the new
aggregate fields complement it.

## Execution step table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3A | medium | sonnet | none | **Trait + selector + replace call site (pure-Rust backend only).** Create `shakenfist-spice-compression/src/jpeg.rs` with the `JpegDecoder` trait and `DecodedJpeg` struct exactly as in *Design notes / Trait*. Implement `JpegDecoderRsDecoder` that wraps the existing pure-Rust path — pull the body of `decode_mjpeg_frame` (currently in `shakenfist-spice-renderer/src/channels/display.rs:88-134`) into this impl. The old `decode_mjpeg_frame` function moves to a private free function in `jpeg.rs` or becomes the impl directly. `best_for_platform()` initially returns `Arc::new(JpegDecoderRsDecoder::new())`. In `display.rs`: add `jpeg_decoder: Arc<dyn JpegDecoder>` field on `DisplayChannel`, construct in `DisplayChannel::new` via `shakenfist_spice_compression::jpeg::best_for_platform()`, replace the `decode_mjpeg_frame(frame_data)` call in the STREAM_DATA handler with `self.jpeg_decoder.decode(frame_data)`. Update `mjpeg_decoder_backend` on `StreamSnapshot` (copy `self.jpeg_decoder.name().to_string()` into the snapshot at stream-create time so each stream entry shows the active backend). Existing MJPEG decode behaviour must be preserved bit-for-bit. Verify `make build && make test && make lint`. |
| 3B | high | opus | none | **`MozJpegDecoder` baseline.** Add `mozjpeg` (with the vendored `with-simd` build) to `shakenfist-spice-compression/Cargo.toml` under a new `mozjpeg` feature, defaulted on. Implement `MozJpegDecoder` per *Design notes / `MozJpegDecoder`*. Check the actual `mozjpeg` crate API by reading `Cargo.lock`/`cargo doc` — the `Decompress::new_mem` → `rgba()` → `read_scanlines()` sketch is illustrative, not literal. Update `best_for_platform()` to prefer `MozJpegDecoder` over `JpegDecoderRsDecoder` everywhere. Add a round-trip unit test that constructs a 16×16 RGBA pattern, JPEG-encodes it via `mozjpeg::Compress`, decodes via `MozJpegDecoder`, and asserts the output is within a JPEG-lossy tolerance (e.g. per-channel max diff ≤ 20, mean ≤ 5). Verify `make build && make test && make lint`. **Why opus:** new C dep, new crate, version-skew risk on the `mozjpeg` API, and the round-trip test must distinguish encoder-induced loss from a real decode bug. |
| 3C | high | opus | none | **`ImageIoDecoder` (macOS).** Add `objc2-image-io` (and any required `objc2-core-foundation` / `objc2-core-graphics` companions) to `shakenfist-spice-compression/Cargo.toml` under `[target.'cfg(target_os = "macos")'.dependencies]`. Implement `ImageIoDecoder` using `CGImageSourceCreateWithData` → `CGImageSourceCreateImageAtIndex` → draw into a `CGBitmapContext` configured for RGBA (NOT the default BGRA — `kCGImageAlphaPremultipliedLast \| kCGBitmapByteOrder32Big`) → `CGBitmapContextGetData` to extract pixels. Update `best_for_platform()` to prefer `ImageIoDecoder` on macOS. macOS-only unit test using a small embedded JPEG fixture (5–10 KB of red/green/blue swatches encoded once, hex-pasted into the test) asserting decoded RGBA matches expected pixels within tolerance. The `objc2-image-io` API is the documented surface; if it lacks something needed, drop to direct `extern "C"` declarations of the missing functions (an `image-io-sys` style block at the bottom of the file). Verify `make build && make test && make lint` on a macOS dev host or CI matrix — the smoke test in step 3H is what actually proves this works. **Why opus:** ImageIO has subtle colour-space and alpha gotchas (e.g. JPEG-with-Adobe-CMYK is decoded as CMYK unless you force conversion; alpha bits in the context must match the source format); getting it wrong ships a "all images are blue" bug. |
| 3D | high | opus | none | **`WicDecoder` (Windows).** Add `windows` crate with features `Win32_Graphics_Imaging`, `Win32_System_Com` to `shakenfist-spice-compression/Cargo.toml` under `[target.'cfg(target_os = "windows")'.dependencies]`. Implement `WicDecoder` using `CoInitializeEx` (handled lazily via `thread_local!`), `CoCreateInstance(CLSID_WICImagingFactory)`, `IWICImagingFactory::CreateDecoderFromStream(IWICStream from `data`, GUID_ContainerFormatJpeg, decode-on-load)`, `IWICBitmapDecoder::GetFrame(0)`, `IWICImagingFactory::CreateFormatConverter`, convert to `GUID_WICPixelFormat32bppRGBA`, `IWICBitmapSource::CopyPixels` into a `Vec<u8>`. Update `best_for_platform()` to prefer `WicDecoder` on Windows. Windows-only unit test analogous to 3C using an embedded JPEG fixture. **CRITICAL — COM threading:** WIC requires the thread to be in COM apartment. Decode runs on tokio worker threads, which are not COM-initialised. Option A: `CoInitializeEx(COINIT_MULTITHREADED)` via a `thread_local!` initialised-on-first-use on the worker. Option B: dispatch decode to a `spawn_blocking` pool with COM init in the closure. Option A is lower-latency; pick that unless WIC objects misbehave across `.await` points (they shouldn't — they're not Send guarantees but they hold no async state). Document the choice in the code comments. Verify `make build && make test && make lint` — the Windows lint step requires a Windows CI runner, which the CI platform matrix work (`PLAN-ci-platform-matrix.md`) was supposed to add but hasn't yet. If that's not in place, smoke-test on a real Windows host as 3H instead. **Why opus:** COM threading is a category of bug that's hard to reproduce and harder to debug; the format converter dance is easy to get wrong in subtle ways. |
| 3E | high | opus | worktree | **`VaapiDecoder` (Linux, dlopen).** Add `libloading` to `shakenfist-spice-compression/Cargo.toml` under `[target.'cfg(target_os = "linux")'.dependencies]` (NOT under default features — the dep is small and only needed on Linux). Implement `VaapiDecoder` per *Design notes / `VaapiDecoder` (Linux, dlopen)*. The function-pointer surface to dlsym is large — define a struct that holds all the function pointers and a `Library` keeping the .so loaded. `try_new()` returns `None` for any failure: missing libva.so.1, missing libva-drm.so.2, no `/dev/dri/renderD128`, no `VAProfileJPEGBaseline`, no `VAEntrypointVLD`, JPEG-header-parse failure on the embedded test JPEG, vaapi error on the test decode. The selector logs at INFO which backend was selected and why (so a bug report's console log explains "VA-API rejected because VAProfileJPEGBaseline missing"). Reference implementations to crib from: ffmpeg's `libavcodec/vaapi_decode.c` and chromium's `media/gpu/vaapi/vaapi_jpeg_decoder.cc`. **Use isolation = worktree** so if this step turns out to be more than a phase-3-sized piece of work, we discard cleanly and ship 3A-3D + the mozjpeg fallback. Smoke-test on a Linux dev host (your laptop or a known-VA-API VM). Verify `make build && make test && make lint`. **Why opus + worktree:** highest implementation risk in this phase. Parsing JPEG SOF/DHT/DQT into the libva struct surface is fiddly; getting the surface allocation, picture buffer, slice buffer right requires careful study of the ffmpeg/chromium prior art. Worktree isolation lets us back out if needed. |
| 3F | medium | sonnet | none | **Aggregate decode-duration tracking + snapshot.** Add a bounded ring of recent MJPEG decode durations (microseconds) to `DisplayChannel`, capped at MAX_RECENT_DECODES (the existing constant — keep it consistent with non-stream decode). On every `self.jpeg_decoder.decode(...)` call in the STREAM_DATA handler, time the call with `Instant::now()` and push the duration into the ring. In `update_snapshot`, compute min/max/mean and write `snap.mjpeg_decode_recent_*`. Also track and surface `mjpeg_decode_total_count` and `mjpeg_decode_failed_count` as aggregate counters. Extend `test_display_snapshot_serialises` to populate non-zero values and assert each field appears in the JSON. Note: per-stream `last_decode_duration_us` is unchanged (step 1F already sets it for the STREAM_REPORT path; this step adds aggregate visibility across streams). Verify `make build && make test && make lint`. |
| 3G | low | haiku | none | **Docs touch-up.** Update `ARCHITECTURE.md` if it has a "decoders" section to list the new per-platform JPEG decoders and the fallback chain. Add a one-sentence note to `AGENTS.md` about the new system-library dependencies (none required at runtime thanks to dlopen + vendored libjpeg-turbo, but `libva-dev` is useful at runtime on Linux for VA-API to engage). If `README.md` has a "performance" section, mention the per-platform optimisation. Verify `pre-commit run --all-files`. |
| 3H | — | — | — | **Per-platform smoke tests (+ deferred phase-2 LZ4 verification).** Operator-driven. On each of macOS, Windows, and Linux, run a fresh ryll session against a real spice-server with a workload that exercises BOTH MJPEG (drag a window, scroll a video player, etc.) AND static UI (scroll a long text document, browse a wiki page — the kind of workload the server picks LZ4 for). File a Display bug report and verify: (a) `mjpeg_decoder_backend` shows the expected platform-native value (not "jpeg-decoder"); (b) `mjpeg_decode_recent_mean_us` is well under the prior pure-Rust baseline (target: ≤30 ms at 2048×1152 on macOS Apple Silicon; ≤40 ms on Windows with WIC; ≤40 ms on Linux with mozjpeg, ≤20 ms with VA-API); (c) `recent_decodes` contains at least one `image_type: "Some(Lz4)"` entry (the phase 2 LZ4 verification deferred from step 2C — see *Background* note below). If LZ4 still doesn't appear despite a static-UI workload, that's a real signal worth investigating (e.g. server may need explicit `PREF_COMPRESSION` from phase 7 to prefer LZ4). Writeup goes into the master plan's *Bugs fixed during this work* if anything went wrong. |

> **Background on the combined LZ4 verification.** Phase 2
> landed the cap advertisement and the LZ4 decoder unit tests
> but step 2C (manual confirmation of an end-to-end LZ4 decode
> against a real server) didn't fire — test session 002b ran
> with the cap on, no `LZ4` warnings, but zero
> `image_type: "Some(Lz4)"` entries in `recent_decodes`
> because the user's workload was drag/video-heavy rather
> than static-UI-heavy, and they never managed to drive the
> session long enough on a workload that would have surfaced
> LZ4 because of the MJPEG-decode unresponsiveness that phase
> 3 fixes. With phase 3 lifting the unresponsiveness, a
> sustained static-UI workload becomes feasible, and 3H is
> the natural place to retro-confirm 2C.

Commits: one per step (3A through 3G). 3H is operator
verification, not a code change.

## Test plan

Automated (added by 3A–3F):

- `JpegDecoder` round-trip via `mozjpeg::Compress` →
  `MozJpegDecoder::decode` → assert RGBA within
  JPEG-lossy tolerance (3B).
- macOS-only: round-trip via embedded JPEG fixture →
  `ImageIoDecoder::decode` → assert RGBA matches expected
  swatches within tolerance (3C, gated by
  `#[cfg(target_os = "macos")]`).
- Windows-only: analogous test for `WicDecoder` (3D, gated by
  `#[cfg(target_os = "windows")]`).
- Linux-only: `VaapiDecoder::try_new()` returns `None`
  gracefully in environments without libva (3E). End-to-end
  decode test requires hardware; skip in CI, run via 3H.
- `test_display_snapshot_serialises` extended for the new
  aggregate MJPEG decode fields (3F).
- `JpegDecoderRsDecoder` continues to pass whatever tests the
  current `decode_mjpeg_frame` is covered by (probably none —
  this is a behaviour-preserving refactor in step 3A).

Manual (3H):

- macOS: dogfood session against the test VM, drag windows
  for 30 seconds, file Display bug report. Confirm
  `mjpeg_decoder_backend: "ImageIO"`,
  `mjpeg_decode_recent_mean_us` ≤ 30 ms, and the previously-
  laggy drag is now responsive.
- Windows: same workflow, expect `WicDecoder`. WIC
  performance is more variable than ImageIO; measure.
- Linux with a VA-API-capable GPU: confirm `VaapiDecoder`
  was selected (look for "VA-API" backend name in the
  snapshot). Without one, confirm graceful fall-through to
  `libjpeg-turbo`.
- Linux without VA-API (e.g. headless server): confirm
  `libjpeg-turbo` selected.

## Documentation impact

`ARCHITECTURE.md` may need a new section describing the JPEG
decoder selection logic; phase 10 (docs) covers
`STREAM_REPORT`, LZ4, the codec set, etc. so it can include
the JPEG decoders too. Step 3G is the minimal in-phase update.

## Success criteria

- `shakenfist-spice-compression::jpeg::best_for_platform()`
  exists and returns the documented backend chain per OS.
- `DisplayChannel::jpeg_decoder` is selected at construction
  and used for every MJPEG stream frame.
- `StreamSnapshot::mjpeg_decoder_backend` reflects the active
  backend in bug reports.
- `DisplaySnapshot::mjpeg_decode_recent_min/max/mean_us` and
  total/failed counts appear in `channel-state.json`.
- On macOS Apple Silicon dogfooding (the session-002b
  workload), `mjpeg_decode_recent_mean_us` drops from
  ~76–175 ms to ≤30 ms, and the drag-unresponsiveness
  symptom resolves.
- `make build && make test && make lint && pre-commit run
  --all-files` clean on Linux (the development host). Windows
  and macOS gates verified via 3H if CI doesn't cover them.

## Back brief

Before executing any step of this phase, the implementing
sub-agent should back-brief the operator with their
understanding of the step, the files they intend to touch,
and any deviations from the brief.

## Session 006 follow-up — still-image JPEG wiring gap

Walking 3H against the existing 006 bundles revealed a gap in
the original phase 3 wiring: only the **MJPEG stream-data**
path was switched to `self.jpeg_decoder` (display.rs:1362).
The **still-image JPEG** path at `display.rs:2160` still
called `image::load_from_memory_with_format(...)`, which uses
the pure-Rust `jpeg-decoder` crate underneath.

The 006a-c bundles measured this directly: ~160 still-image
JPEG decodes per 10-minute session at **~263 ms median per
1920×1472 frame on a Mac that has ImageIO available** — 8×
slower than the per-platform target. The "drag is laggy"
symptom phase 3 set out to fix is still present for any
workload that triggers still-image JPEG sends (which is what
happens on a guest where MJPEG streams don't engage — i.e.
every guest we currently test against).

Fix landed: `ImageType::Jpeg` now routes through
`self.jpeg_decoder.decode(...)`, sharing the same
`Arc<dyn JpegDecoder>` the MJPEG stream-data path uses.

This invalidates the original 3H smoke (which assumed MJPEG
streams would be the verification vector) and replaces it
with a still-image JPEG smoke session, documented in
`ryll-test-sessions/manual-test-instructions/007.md`:

- One shared guest (Debian 11 QXL, 1920×1440, 64 MiB VRAM)
- Three client OSes connect in turn (macOS, Linux/Kasm,
  Windows/Surface Go)
- Workload: scroll a JPEG-heavy Wikipedia page in Firefox
  for ~2 minutes per run
- Bundles tagged `007a` / `007b` / `007c`

Baseline reference from 006a (pre-fix, macOS):
median 263 ms, p95 266 ms. Per-platform post-fix targets:

| Tag | Backend (expected) | Median target | p95 target |
|-----|--------------------|---------------|------------|
| 007a | ImageIO  | ≤30 ms | ≤50 ms |
| 007b | mozjpeg  | ≤40 ms | ≤80 ms |
| 007b | VA-API   | ≤20 ms | ≤40 ms |
| 007c | WIC      | platform-dep — Surface Go is slow hardware, "better than pure-Rust" is the bar |

## Deferred VA-API decode path

Moved here from the `VaapiDecoder` docstring in
`shakenfist-spice-compression/src/jpeg.rs`, which had grown a
roadmap paragraph that was planning material rather than a
description of what the code does. The code now carries a
one-line pointer to this section instead.

`VaapiDecoder::try_new()` probes for VA-API and, on success,
`best_for_platform()` selects it — but `decode()` delegates to an
embedded `MozJpegDecoder`. Wiring up real VA-API decode needs:

- **A wider dlsym surface.** The probe binds `vaGetDisplayDRM`,
  `vaInitialize`, `vaTerminate`, `vaErrorStr`,
  `vaMaxNumProfiles`, `vaQueryConfigProfiles`,
  `vaMaxNumEntrypoints` and `vaQueryConfigEntrypoints`. Decode
  additionally needs `vaCreateConfig`, `vaCreateContext`,
  `vaCreateSurfaces`, `vaCreateBuffer`, `vaBeginPicture`,
  `vaRenderPicture`, `vaEndPicture`, `vaSyncSurface`,
  `vaDeriveImage`, `vaMapBuffer`, `vaUnmapBuffer`,
  `vaDestroyImage`, `vaDestroyBuffer`, `vaDestroyContext`,
  `vaDestroyConfig` and `vaDestroySurfaces`.
- **Our own JPEG header parse.** VA-API is a command-buffer
  interface and does not parse JPEG. The SOF/DHT/DQT segments
  have to be read out to populate
  `VAPictureParameterBufferJPEGBaseline`,
  `VAIQMatrixBufferJPEGBaseline`,
  `VAHuffmanTableBufferJPEGBaseline` and
  `VASliceParameterBufferJPEGBaseline`.
- **Reference implementations.** ffmpeg's
  `libavcodec/vaapi_mjpeg.c` and chromium's
  `media/gpu/vaapi/vaapi_jpeg_decoder.cc`.

Two things in the current code exist to make this landing safe
rather than silently unsound, and both must be revisited as part
of the work:

- `unsafe impl Send`/`Sync for VaapiDecoder` is sound only
  because `decode(&self, ..)` never touches libva. That is now
  enforced by `vaapi::LibvaHandles`, whose fields are private to
  the `vaapi` submodule and reachable only through `&mut self`.
  A real decode path has to widen those accessors, and the
  correct answer at that point is an internal
  `Mutex<VADisplay>` — libva thread-safety is driver-dependent
  (Intel iHD, Mesa and friends each implement their own locking
  policy), so we own the synchronisation rather than trusting
  whichever driver is loaded.
- `name()` reports `"VA-API (probed, mozjpeg fallback)"`. Drop
  the parenthetical when the delegation goes away; the tests
  assert only the `"VA-API"` prefix so they survive the change.
