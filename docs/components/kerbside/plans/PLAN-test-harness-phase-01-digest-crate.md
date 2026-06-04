# Phase 1: Shared visual-digest crate

Part of [PLAN-test-harness.md](/components/kerbside/plans/PLAN-test-harness/). This
phase lives entirely outside kerbside — work lands in a new
`shakenfist/visual-digest-rust` repo and in
`shakenfist/uncalibrated-sextant`. The phase plan lives here
in kerbside per the master plan's single-home rule.

## Goal

Extract the visual on-screen digest format into its own
crate in its own new repo, so that Uncalibrated Sextant
(encoder) and Ryll (future decoder, phase 6) share a single
source of truth. The format must not change on the wire.

Out of scope for phase 1:

- Any change to how Sextant *uses* the digest (the renderer
  call site is unchanged; only the import path moves).
- Ryll integration — phase 6 takes a dependency on what
  this phase produces.
- A second wire-format version (we ship v2 only; v3 is
  future work).
- Generic-event support — the crate ships with Sextant's
  event vocabulary baked in. Future test guests either
  reuse the vocabulary or fork the crate.

## Decisions baked into this plan

These were judgment calls made while drafting the phase
plan rather than questions to ask the operator. Flagged
explicitly so they can be challenged before any code lands.

- **The crate owns Sextant's event vocabulary, not just
  the format primitives.** `Event`, `Phase`, and
  `BootloaderChoice` move into the crate alongside the
  encoder. Rationale: those types are the on-wire
  vocabulary today — `Phase` has stable wire discriminants
  documented in the spec, `Event` variants map 1:1 to TLV
  tags, and `BootloaderChoice` is wire-fixed. Splitting
  them out into a generic primitives layer is possible but
  adds an abstraction without a second consumer to justify
  it. Pure copy first; refactor if a second guest needs
  it.
- **The encoder takes a slice, not a `RingBuffer`.** The
  current `encode(ring: &RingBuffer<256>, ...)` becomes
  `encode(events: &[&Event], ...)`. Sextant materialises
  the slice at the call site (it already does, internally,
  in a `[Option<&Event>; 256]` stack array). This decouples
  the crate from Sextant's specific ring container.
- **The decoder returns typed records, not raw TLV bytes.**
  Returning `Digest { ..., raw_records: Vec<Record> }`
  where `Record` is an enum mirroring `Event` is a much
  better DX for the eventual tempest scenario tests — they
  can write `digest.raw_records.iter().any(|r| matches!(r,
  Record::PasteReceived { correct: true, .. }))` instead
  of byte-fiddling. Unknown tags are surfaced separately
  as `unknown_records: Vec<(u8, Vec<u8>)>` so forward
  compatibility is explicit.
- **Feature flags.** Default features = encoder only,
  `no_std`-safe. `decode` adds the decoder and pulls in
  `alloc`. `qr` adds rqrr + image and the QR-locate helper.
  `serde` adds `serde::Serialize` for the decoded types
  so the CLI can emit JSON. `cli` builds the binary.
- **QR decoder library: rqrr.** Most mature pure-Rust QR
  decoder, maintained, MIT/Apache. No Sextant dependency
  on this — only Ryll and the CLI use it.
- **Sextant consumes via `git = "..."` dep**, not
  crates.io. Crates.io publication is deferred to future
  work (see *Future work* below); it adds release
  overhead without buying us anything until external
  consumers exist, and the digest format is unlikely to
  be useful outside this lineage.
- **Repo name `shakenfist/visual-digest-rust`,** matching
  the established `shakenfist/<thing>-<language>`
  convention for language-specific repos. The crate name
  inside the repo is `shakenfist-visual-digest` (no
  `-rust` suffix — Rust crates are inherently
  single-language, and the suffix would read as noise on
  every `use` line).
- **License Apache-2.0.**
- **CI runs on self-hosted GitHub Actions runners with
  cargo work wrapped in Docker**, following the
  shakenfist/ryll pattern (`ryll/.github/workflows/ci.yml`
  + `ryll/scripts/check-rust.sh` + a `.devcontainer/`
  Dockerfile that owns the toolchain). The Docker wrap
  is load-bearing — the operator runs several Rust
  projects at different toolchain versions and does not
  want a pinned Rust on the host. Self-hosted runners
  inherit the same constraint, so the Dockerfile is the
  source of truth for the crate's toolchain in CI and
  in local dev.
- **Sextant's `docs/visual-digest-format.md` becomes a
  one-line pointer** to the new repo's copy, not a
  deletion. Keeps in-repo grep finds inside Sextant
  pointing somewhere useful.

## Situation

The encoder is at
`shakenfist/uncalibrated-sextant/src/digest.rs` — a
~1040-line file containing all tag constants, the encoder
itself, `event_tlv_bytes` (the single source of truth for
what a given `Event` produces on the wire),
`ChannelHashes` (per-channel rolling CRC32C accumulator),
and host-side unit tests for every event variant plus the
chaining algebra. The encoder is pure: no IO, no clock, no
`&mut Renderer`, no allocator. It writes into a
caller-provided `[u8; 106]`. Its only dependencies are
`crc` (with `default-features = false` for `no_std`) and
Sextant-internal types from `src/event.rs`.

The format spec lives at
`shakenfist/uncalibrated-sextant/docs/visual-digest-format.md`
and is a thorough, normative reference. It names the
source files that are authoritative — keeping the spec in
the new repo means those file references rewrite.

No decoder exists in any repo. The existing host-side
decoder reference is
`shakenfist/uncalibrated-sextant/scripts/digest-payload-smoke.sh`,
a Python script used for QEMU smoke testing. That script
is the closest thing to a decoder-shape oracle for this
phase's Rust decoder.

Sextant has no `cargo test` for QEMU output — its
host-side tests exercise `event_tlv_bytes` and the
chaining math; the QEMU smoke (`scripts/digest-payload-smoke.sh`)
exercises end-to-end behaviour under OVMF. Phase 1 needs
to preserve both gates.

## Mission and problem statement

After phase 1:

- `github.com/shakenfist/visual-digest-rust` exists,
  with a workspace containing one library crate
  (`shakenfist-visual-digest`) and one binary crate
  (`digest-decode`).
- The crate provides:
  - Encoder API equivalent to today's Sextant encoder, but
    decoupled from `RingBuffer` (takes a slice of event
    refs).
  - Decoder API that parses a raw digest payload into a
    typed `Digest` struct.
  - Optional QR locate-and-decode helper behind the `qr`
    feature.
  - Optional `serde::Serialize` impls behind the `serde`
    feature.
- The format spec doc lives in the new repo; Sextant's
  copy becomes a short pointer.
- Uncalibrated Sextant has been migrated to consume the
  crate. Its renderer call site is unchanged in shape; only
  imports move. `make digest-payload-smoke` (the QEMU
  smoke) produces byte-identical output to the pre-migration
  baseline.
- The `digest-decode` CLI takes a PNG, finds the QR,
  decodes the digest, and prints JSON.

## Open questions

None remaining at the time of writing — all initial
operator-facing decisions are locked in the "Decisions
baked into this plan" block above. New questions surfaced
during execution should be added here.

## Execution

Each step is one logical change → one commit. The
"Repo" column shows where the commit lands. Worktree
isolation is recommended where the step modifies an
existing repo destructively.

| Step | Repo | Effort | Model | Isolation | Brief for sub-agent |
|------|------|--------|-------|-----------|---------------------|
| 1a. Bootstrap new repo | visual-digest-rust (new) | low | sonnet | none | Operator creates the empty `shakenfist/visual-digest-rust` repo on GitHub. Then initialise it with: README.md, AGENTS.md, ARCHITECTURE.md, LICENSE (Apache-2.0), `Cargo.toml` workspace, `shakenfist-visual-digest/` (empty `lib.rs`, `Cargo.toml` declaring features `decode`, `qr`, `serde`, `cli` — all off by default; default features = encoder-only, `no_std`-compatible), `digest-decode/` (empty `main.rs`, `Cargo.toml` depending on the library with `decode,qr,serde,cli`), `.devcontainer/Dockerfile` owning the Rust toolchain (copy the shape from `shakenfist/ryll/.devcontainer/`, adjust toolchain version as needed for the no_std target), `scripts/check-rust.sh` that runs rustfmt + clippy inside the Docker image (model on `shakenfist/ryll/scripts/check-rust.sh`), `.github/workflows/ci.yml` on `runs-on: [self-hosted, ...]` labels matching Ryll's pattern, invoking the Docker-wrapped build for: clippy `-D warnings`, rustfmt, `cargo test --workspace --all-features`, `cargo build --no-default-features` to verify the no_std story, `.pre-commit-config.yaml` modelled on `shakenfist/ryll/.pre-commit-config.yaml` (actionlint + hygiene + the Docker-wrapped check-rust hook), `.gitignore`. Lands as one commit in the new repo. |
| 1b. Move format spec doc | visual-digest-rust, then sextant | low | sonnet | none | Copy `shakenfist/uncalibrated-sextant/docs/visual-digest-format.md` into `shakenfist/visual-digest-rust/docs/visual-digest-format.md`. Update every source-file reference inside the doc to point at the new crate layout (`src/digest.rs` → `shakenfist-visual-digest/src/encoder.rs`, etc.). Provenance section needs the new paths. Commit in new repo. Then a second commit in Sextant: replace `docs/visual-digest-format.md` with a one-line pointer ("The visual-digest wire format spec lives in shakenfist/visual-digest-rust/docs/visual-digest-format.md."); update any in-source `//!` comments that referenced the doc path. |
| 1c. Extract encoder | visual-digest-rust | medium | sonnet | worktree | Copy `uncalibrated-sextant/src/digest.rs` content into the new crate, splitting into: `format.rs` (constants, `EncodeError`, `phase_wire`, `choice_wire`, `size_of_record`), `events.rs` (`Event`, `Phase`, `BootloaderChoice` — copy verbatim from Sextant's `src/event.rs` for those three types only, plus `RingBuffer` if needed), `encoder.rs` (the `encode` function + `event_tlv_bytes` + `write_record`), `hashes.rs` (`ChannelHashes`). Change `encode`'s signature to take `events: &[&Event]` instead of `&RingBuffer<256>`; move the ring-walking and stack-buffer materialisation logic to the call site (it stays in Sextant for now). All `pub(crate)` items become `pub`. Bring the existing `#[cfg(test)] mod tests` block over unchanged. Verify: `cargo test --no-default-features` (encoder only) passes, `cargo build --no-default-features` succeeds with `#![no_std]` declared in lib.rs. No behavioural change; only structure. |
| 1d. Golden encoder vectors | visual-digest-rust | medium | sonnet | none | Before any decoder work, lock the encoder against accidental drift. In the new crate's `tests/golden.rs`, construct three event sequences: empty ring, a single keypress, a mixed sequence of one each of every variant. For each, call the new encoder and assert byte-equality against a captured fixture under `tests/golden/`. Capture the fixtures by running the *original* Sextant encoder via a tiny throwaway harness against the same inputs (a one-off binary in the Sextant tree, committed to a scratch branch, the bytes captured and copied across, the branch deleted). Document the capture procedure inline in the test file so it can be re-run if the fixtures ever need refreshing. The fixtures become the load-bearing on-wire compatibility check from this point forward. |
| 1e. Decoder | visual-digest-rust | medium | sonnet | none | New `src/decoder.rs` behind the `decode` feature (requires `alloc`). Public API: `pub fn decode(bytes: &[u8]) -> Result<Digest, DecodeError>`. `Digest` is a struct with: `frame_counter: u32`, `channel_hashes: ChannelHashes`, `raw_records: Vec<Record>`, `unknown_records: Vec<(u8, Vec<u8>)>`, `framebuffer_hash: u32`. `Record` is an enum mirroring `Event` 1:1. `DecodeError` covers: short input, wrong magic, unsupported schema version, malformed TLV, truncated value. Forward compat: unknown tags in raw-record region go into `unknown_records`, not an error. Round-trip test: encode the same three sequences from step 1d, decode them, assert structural equality with the input events. Add a malformed-input test pass (one bad byte at each interesting offset, all should produce a non-panic error). |
| 1f. QR locate and decode | visual-digest-rust | medium | sonnet | none | New `src/qr.rs` behind the `qr` feature (adds `rqrr` and `image` deps). Two entry points: `pub fn decode_qr_rgba(rgba: &[u8], width: u32, height: u32) -> Option<Vec<u8>>` (Ryll's hot path; takes a raw frame, returns the QR's byte-mode payload), `pub fn decode_qr_png(path: &Path) -> Result<Vec<u8>, QrError>` (the CLI's entry point). Test with at least two fixture PNGs captured from Sextant under QEMU (use the existing `scripts/screenshot.sh` in Sextant to grab them). Assert that the bytes returned from the PNG decode round-trip through `decode()` from step 1e and yield a sensible `Digest`. |
| 1g. CLI binary `digest-decode` | visual-digest-rust | low | sonnet | none | Flesh out the empty `digest-decode/src/main.rs`. Argument: one positional PNG path. Behaviour: load PNG, call `qr::decode_qr_png`, call `decode()`, serialise the `Digest` to pretty JSON via `serde_json`, print to stdout. Errors print to stderr with non-zero exit code. Test via a golden fixture: `tests/cli.rs` invokes the binary (via `assert_cmd` or similar) on a fixture PNG, compares stdout to a captured `expected.json`. |
| 1h. Switch Sextant to the new crate | sextant | high | opus | worktree | The riskiest step; must not change wire output. (1) Add `shakenfist-visual-digest = { git = "https://github.com/shakenfist/visual-digest-rust", default-features = false }` to `uncalibrated-sextant/Cargo.toml` (pin to a commit hash for reproducibility). (2) Delete `uncalibrated-sextant/src/digest.rs`. (3) In `uncalibrated-sextant/src/event.rs`, replace the local definitions of `Event`, `Phase`, `BootloaderChoice` with `pub use shakenfist_visual_digest::{Event, Phase, BootloaderChoice}`; keep the rest of `event.rs` (`RingBuffer`, anything else) as-is. (4) Update `uncalibrated-sextant/src/scene.rs::ChannelHashes` usage — `ChannelHashes` now comes from the crate too; if the local file held additional Sextant-specific glue beyond `ChannelHashes`, leave the glue in place and only re-import `ChannelHashes`. (5) Update the renderer's call site to materialise the event slice from the ring buffer before calling `encode()`. (6) Run `cargo build`, `cargo test`, `make digest-payload-smoke`. The QEMU smoke is the load-bearing test: capture its output before the change, run it after, assert byte-identical. (7) Update Sextant's `ARCHITECTURE.md` and `AGENTS.md` to point at the new crate for the format definition. Plan rollback before starting: keep the pre-migration commit hash handy; if the QEMU smoke regresses, revert the migration commit while we debug. |

### Step dependency graph

```
1a → 1b → 1c → 1d → 1e → 1f → 1g
                  ↘   ↘
                   1h (needs 1c, ideally 1d for the safety net)
```

1e/1f/1g sequence is internal to the new crate and could
overlap if briefs are tight. 1h is the only cross-repo
step besides 1b's redirect commit. 1i is a follow-up.

## Agent guidance

This phase plan follows the conventions in `PLAN-TEMPLATE.md`
at the kerbside repo root. The execution model, effort
levels, model-choice guidance, brief-writing standards,
and management-session review checklist all apply
unchanged and are not duplicated here.

Notes specific to phase 1:

- **Cross-repo briefing.** Steps 1a, 1c, 1d, 1e, 1f, 1g
  land in the new `visual-digest-rust` repo. Step 1b
  lands in both. Step 1h lands in Sextant. Brief
  sub-agents with the full path
  `shakenfist/kerbside/docs/plans/PLAN-test-harness-phase-01-digest-crate.md`
  so they know where the plan lives even when their
  commits land elsewhere.
- **Step 1h is the on-wire integrity gate.** Until the
  QEMU smoke is byte-identical, the migration has not
  succeeded. The brief must explicitly include "capture
  the QEMU smoke output before any changes, capture it
  again after, diff." Do not skip even if `cargo test`
  passes — the host-side tests cover `event_tlv_bytes`
  but not the integrated ring-walking + framebuffer-hash
  output.
- **No `#[cfg(test)]` removal during extraction.** Step
  1c brings the existing tests over verbatim. The
  temptation to clean them up while moving is real;
  resist. A separate follow-up commit can refactor them
  later if needed.
- **Step 1a's empty-skeleton commit is the only one
  allowed to land empty.** Every other commit must
  include a working `cargo test` for its scope.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the step and how
the work you intend to do aligns with that step's brief.

## Administration and logistics

### Success criteria

Phase 1 is done when:

- `github.com/shakenfist/visual-digest-rust` is a real
  repo with green CI on its main branch.
- The crate exports the encoder, the decoder, the QR
  helper, and the `digest-decode` CLI binary. Default
  features keep it `no_std`-compatible.
- The format spec doc lives in the new repo and
  Sextant's copy is a pointer.
- Uncalibrated Sextant builds and passes `cargo test`
  and `make digest-payload-smoke` against the new crate,
  with byte-identical QEMU output vs the pre-migration
  baseline.
- Golden encoder fixtures in the new crate's
  `tests/golden/` lock the wire format against silent
  drift.
- `pre-commit run --all-files` is clean in both repos.

### Future work

Items deliberately deferred from phase 1:

- **Generic-event support.** If a second test guest
  wants its own digest schema, factor `Event` etc. out
  of the crate into a `primitives` module and let
  guest-specific event modules layer on top.
- **Schema v3.** Bigger raw-event budget would mean QR
  V7 + ECC bump + region resize on the Sextant side; the
  spec doc already sketches this under its capacity
  section.
- **Crates.io publish.** Deliberately skipped. The
  format is unlikely to be useful outside this lineage,
  release plumbing has a non-trivial overhead, and a git
  dep from Sextant (and later Ryll) is sufficient.
  Revisit if and when an external consumer appears.
- **Sextant `RingBuffer` move.** Left in Sextant since
  the crate's encoder no longer needs it; if a future
  consumer wants a ring container, factor at that point.

### Bugs fixed during this work

(None yet.)
