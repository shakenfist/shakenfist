# Phase 4: output formatting

Master plan: [PLAN-map.md](/components/instar/plans/PLAN-map/) ·
Previous phase: [PLAN-map-phase-03-host-cli.md](/components/instar/plans/PLAN-map-phase-03-host-cli/)

## Status: Complete

Streaming `MapRenderer<'a, W: Write>` in `src/vmm/src/main.rs`
writes each extent to stdout (via a `BufWriter` over
`stdout().lock()`) as the `MapExtentMessage` arrives in the
vCPU loop; host memory stays O(1) regardless of how fragmented
the source is. Human and JSON output match `qemu-img map`
byte-for-byte modulo the eight quirks now documented in
`docs/quirks.md`. 21 byte-exact unit tests pin the renderer
against expected output sequences. BrokenPipe on stdout
short-circuits cleanly with exit 0.

## Mission

Replace the phase 3c placeholder renderer
(`format_map_human` / `format_map_json` / `print_map_result`)
with a streaming `MapRenderer` that emits each
`MapExtentMessage` as it arrives during the vCPU loop and
matches `qemu-img map`'s output byte-for-byte for both
`--output=human` and `--output=json`. Two divergences are
documented (not fixed) in `docs/quirks.md`: raw-source
sparseness via `SEEK_HOLE` (not implemented) and qcow2
compressed-cluster reporting (always emitted as
`compressed: false`).

After phase 4, the only thing standing between us and the
cross-version baseline matrix is the testdata sweep in
phase 5. Phase 4's own success criterion is byte-equality
against the system `qemu-img map` for a handful of hand-
crafted fixtures across all five source formats.

## Why this is its own phase

Phase 3 shipped a *working* CLI but with structural-only
output. Phase 4 has two distinct deliverables that are
each non-trivial:

1. **Byte-exact output**: qemu-img's human format uses
   16-char fixed-width columns, special-cases `0` (no
   `0x` prefix), elides hole / zero-allocated extents
   entirely (only `data: true` rows appear), and quotes
   the filename as it was passed on `argv`. The JSON
   format always emits `compressed: false`, omits
   `offset` for non-data extents, separates objects with
   `,\n`, and never indents. Matching these is mechanical
   but every detail is wrong in the phase 3c placeholder.

2. **Streaming emission**: phase 3 buffers extents in a
   `Vec<MapExtentMessage>`. For pathologically
   fragmented sources (17M extents on a 1 TiB qcow2) the
   buffer reaches multi-GiB host memory. Phase 4 swaps
   the buffer for a `MapRenderer` that writes each
   extent to stdout as it arrives in the vCPU loop,
   bringing host memory back to O(1).

Bundling these together is the right grain — the renderer
shape (a struct holding a writer + state) is what enables
both, and the test surface is the same.

## Architecture

### `MapRenderer` struct

```rust
struct MapRenderer<'a, W: Write> {
    writer: &'a mut W,
    output_format: MapOutputFormat,
    /// The argv string the user passed for the source. Used
    /// verbatim in the human-output "File" column. qemu-img
    /// echoes whatever was on the command line (e.g. relative
    /// paths stay relative).
    filename: String,
    /// True after the first extent has been emitted. The JSON
    /// writer uses this to decide whether to prefix the next
    /// extent with `,\n` (subsequent) or write `[` first
    /// (initial).
    first_extent: bool,
    /// Count of extents the renderer has actually written to
    /// the output (human-mode emits only `data: true` rows,
    /// so this can be lower than the count of extents the
    /// guest sent).
    extents_written: u64,
}

enum MapOutputFormat {
    Human,
    Json,
}

impl<'a, W: Write> MapRenderer<'a, W> {
    pub fn new(writer: &'a mut W, output_format: &str, filename: String) -> Self { ... }

    /// Write the format-specific header / opening character.
    /// Called once before any `emit_extent` call.
    pub fn begin(&mut self) -> io::Result<()> { ... }

    /// Write one extent's representation. For human mode this
    /// emits a row only if the extent has `data == true`;
    /// holes and zero-allocated extents are silently skipped.
    /// For JSON mode every extent is emitted.
    pub fn emit_extent(
        &mut self,
        ext: &guest_::MapExtentMessage,
    ) -> io::Result<()> { ... }

    /// Write the format-specific closing character. Called
    /// once after the last `emit_extent` and before printing
    /// any error.
    pub fn finish(&mut self) -> io::Result<()> { ... }
}
```

### Exact human output specification

Header row (4 columns, each left-justified in a 16-char
field; last column has no trailing pad):

```
Offset          Length          Mapped to       File
```

byte-by-byte:
- `Offset` (6) + 10 spaces (= 16 chars) +
- `Length` (6) + 10 spaces (= 16 chars) +
- `Mapped to` (9) + 7 spaces (= 16 chars) +
- `File` (4) + `\n`

Total: 53 bytes.

Data row format:
```
<offset>{pad to 16}<length>{pad to 16}<mapped_to>{pad to 16}<filename>\n
```

- `offset` and `length`: lowercase hex with `0x` prefix
  *unless the value is zero*, in which case just the
  literal `0`. (E.g. `0x10000` for 65536, but `0` for 0.)
- `mapped_to`: same rule as offset/length.
- `filename`: the argv string the user passed, no quoting,
  no resolution. Phase 4 captures this from the
  `MapArgs::input` field.

**Holes and zero-allocated extents are elided** — no
human-mode row is emitted for them. This matches qemu-img:
only extents with `data: true` produce visible rows. The
empty-image case prints only the header line (verified
against `qemu-img map` on a freshly-created qcow2).

### Exact JSON output specification

Opening: `[` (1 byte, no newline).

Per-extent object (always on its own line, no leading
whitespace):

```
{ "start": N, "length": N, "depth": 0, "present": B, "zero": B, "data": B, "compressed": false[, "offset": N]}
```

Field order is fixed: `start, length, depth, present, zero,
data, compressed, offset`. `offset` is emitted only when
`data == true`. `compressed: false` is **always** emitted
(qcow2 compressed-cluster reporting is a documented
divergence; see `docs/quirks.md` follow-up).

Inter-object separator: `,\n` (comma then newline). The
final object has no trailing comma.

Closing: `]` (1 byte). No trailing newline — qemu-img does
not emit one.

Whitespace inside each object:
- Single space after `{` and before `}`.
- Single space after each `:`.
- Single space after each `,` (but the inter-object
  separator is `,\n` so this rule applies only inside the
  object).
- Boolean values lowercase (`true` / `false`).
- Numbers are decimal (never hex, scientific notation, or
  quoted).

### State → triple → field-set mapping

| guest state | present | zero  | data  | emit offset |
|-------------|---------|-------|-------|-------------|
| `"hole"`    | false   | true  | false | no          |
| `"zero"`    | true    | true  | false | no          |
| `"data"`    | true    | false | true  | yes         |

This matches the phase 3c `map_state_triple` table. Phase 4
keeps that helper as the source of truth; the field-set
order changes (`compressed` becomes a constant `false`).

### Streaming pivot in `run_map`

Phase 3b's vCPU loop pushed `MapExtentMessage`s into
`extents: Vec<MapExtentMessage>` and called
`print_map_result(&extents, ...)` after the loop. Phase 4
restructures:

1. **Before the vCPU loop**: open `stdout`, wrap in a
   `BufWriter`, construct `MapRenderer`, call `begin()`.
   The header / opening `[` is emitted before any extent
   message arrives. For the error path (guest fails to
   start, KVM crashes), this is a small wasted output but
   matches the streaming semantic — the user already
   reading the table sees the header before the failure.

   Alternative: defer `begin()` until the first
   `MapExtent` arrives. This avoids the wasted header on
   guest-failure, at the cost of branching on
   `first_extent` inside the loop. Recommendation: defer
   for cleanliness (the header is only valid when at
   least one message arrives) — see open question 1.

2. **In the loop**:
   - On `MapExtent` payload: call
     `renderer.emit_extent(&e)`. Errors propagate.
   - On `MapResult` payload: store + flag (same as
     phase 3b).
   - Other payloads: route via `format_message` when
     verbose.

3. **After the loop**:
   - If `MapResult` had a non-OK error: call
     `map_error_message(code)` and write to stderr. Do
     **not** call `renderer.finish()` — the output may
     be a partial table or JSON open-bracket; the JSON
     consumer will see invalid output but the user gets
     a clear stderr message.
   - If OK: call `renderer.finish()`. For JSON this
     writes `]`; for human this is a no-op.

The renderer holds a `&mut W` so the vCPU loop owns the
underlying stdout lock. `BufWriter` performance: a 17M-
extent run does 17M small writes; flushing per extent
would dominate runtime. The BufWriter flushes on drop
(via `finish()` plus `Drop`) so the user sees the output
in order.

### Tests (Vec<u8>-backed writer)

Unit tests construct `MapRenderer` against a `Vec<u8>`
and assert exact byte sequences. Test set:

1. **Empty extents, human**: header only.
2. **Empty extents, JSON**: `[]`.
3. **Single data extent, human**: byte-for-byte against
   the expected 16-char-column row.
4. **Single data extent, JSON**: byte-for-byte object.
5. **Hole-only extent, human**: header only (hole
   elided).
6. **Hole-only extent, JSON**: one object with
   `present: false, zero: true, data: false,
   compressed: false`, no `offset`.
7. **Mixed extents (data + hole + zero-alloc), human**:
   only the data row appears.
8. **Mixed extents, JSON**: all three objects in order
   with `,\n` separators.
9. **Zero offset / zero length value, human**: the
   literal `0` (not `0x0`).
10. **Large u64 file_offset, human**: lowercase hex.
11. **Large u64 file_offset, JSON**: decimal.
12. **`compressed: false` always emitted in JSON**:
    grep-style assertion across all three states.
13. **Field order in JSON**: regex match on
    `"start":.*"length":.*"depth":.*"present":.*"zero":
    .*"data":.*"compressed"`.
14. **Filename preserved verbatim in human "File"
    column**: pass a path with `..` and a trailing
    space; assert the bytes match exactly.

Each test starts with `MapRenderer::new(&mut buf, ..., "<filename>")`,
calls `begin()`, emits extents, calls `finish()`, then
compares `buf` to expected `&[u8]` literal byte sequences
generated from `qemu-img map` output for a matching
synthetic image.

### Tear-down of phase 3c helpers

Phase 3c shipped:
- `map_state_triple(state) -> (bool, bool, bool, bool)`
- `format_map_human(extents) -> String`
- `format_map_json(extents) -> String`
- `map_error_message(code) -> Option<&'static str>`
- `print_map_result(extents, result, output_format)`

Phase 4 keeps `map_state_triple` (now part of `MapRenderer`)
and `map_error_message` (used by `run_map` for the error-
stderr path). Drops `format_map_human`, `format_map_json`,
and `print_map_result` — their tests in
`map_renderer_tests` get rewritten against `MapRenderer`.

The phase 3c unit tests that pinned structural invariants
(state triple, error messages) survive verbatim. The
format-output tests get replaced with byte-exact
assertions.

### `docs/quirks.md` additions

A new `## map subcommand quirks` section with these
entries:

1. **`--image-opts` is rejected** — same as measure;
   reuse wording.
2. **Backing-chain `depth` is always 0** — chain
   composition is deferred (`PLAN-map.md` follow-up);
   `depth` is always 0 in v1. qemu-img walks the chain
   when present.
3. **Raw source sparseness**: instar reports raw images
   as one fully-allocated `data: true` extent. qemu-img
   walks `SEEK_HOLE` / `SEEK_DATA` on the underlying
   file and reports sparse regions as
   `present: true, zero: true, data: false`. The
   host-side `SEEK_HOLE` prepass is tracked as future
   work.
4. **VHDX partial-present**: instar treats
   `PAYLOAD_BLOCK_PARTIALLY_PRESENT` as `data: true`.
   qemu-img walks the per-sector bitmap and emits
   per-sector extents. Future work.
5. **VMDK multi-extent**: refused with a clear error
   pointing at qemu-img.
6. **qcow2 compressed clusters**: instar emits
   `compressed: false` for every extent, including
   compressed ones. qemu-img emits
   `compressed: true` for compressed-cluster extents.
   The phase 1 walker classifies compressed clusters
   as `Data` with a file offset but does not carry the
   compressed bit through the FFI / protobuf path.
   Future work — extend `MapExtentRecord` /
   `MapExtentMessage` with a `compressed: bool` field.
7. **Trailing newline**: qemu-img emits no trailing
   newline after `]` in JSON mode. instar matches
   this.

## Open questions

1. **Header emission timing**: emit before the first
   extent (cleaner streaming semantics) or after KVM
   confirms the guest started (cleaner error path)?
   Recommendation: **before**. The header is independent
   of whether the guest succeeds; a partial table is no
   worse than a no-table-then-error sequence, and the
   streaming pivot is cleaner.

2. **`BufWriter` flush on error**: if an extent emit
   fails (stdout closed, broken pipe), should the
   renderer swallow the error or propagate? Recommendation:
   propagate, but downgrade `BrokenPipe` to a clean exit
   (the user piped to `head` or similar). Need to handle
   the std-lib pattern for `BrokenPipe`.

3. **Filename column quoting**: qemu-img passes the
   argv string verbatim. What about embedded newlines or
   tab characters? Recommendation: pass verbatim and
   accept the produced output may be confusing —
   qemu-img does the same. A future hardening pass can
   shell-quote suspect strings.

4. **JSON streaming and `--output=json` error paths**:
   if the guest reports `ERROR_HAS_BACKING` after the
   `[` has been written, the user sees a truncated
   `[\n` on stdout plus the error on stderr. Phase 4
   accepts this: JSON consumers should check the exit
   code, not parse stdout speculatively. Document in
   quirks.

5. **Output profile machinery for map**: the master
   plan said "add `map-human` / `map-json` output
   profiles if the baseline matrix reveals version drift".
   Phase 4 verifies against the current system qemu-img
   only (10.0.8 in dev). Phase 5's baseline sweep is
   where we discover whether older qemu-img versions
   diverged in output format. Recommendation: phase 4
   does **not** add the profile machinery; phase 5
   adds it if needed.

6. **Output to a non-terminal**: when stdout isn't a tty
   (piped into `jq`, redirected to a file), no extra
   handling needed — `println!` / `write!` work the same
   way. The `BufWriter` lock-grab is the same.

7. **`extents_emitted` from the guest vs. extents
   actually written**: the guest's `MapResultMessage`
   counts every extent it sent. The renderer's
   `extents_written` may be lower (human mode skips
   holes). The host doesn't reconcile these — they
   serve different purposes (the guest count is for
   the streaming-protocol audit; the renderer count is
   internal). Document in the renderer's doc comment.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | Add the `MapRenderer<'a, W: Write>` struct + `MapOutputFormat` enum to `src/vmm/src/main.rs` per the schema in the Architecture section. Implement `new`, `begin`, `emit_extent`, `finish`. Human mode: 16-char-column rows, hex with lowercase `0x` prefix or literal `0` for zeros, elide non-`data` extents. JSON mode: streaming `[...]` array with `,\n` separators, `compressed: false` always emitted, `offset` only for data extents, no trailing newline. Add ≥14 unit tests in `mod map_renderer_tests` per the "Tests" section using a `Vec<u8>` writer and exact-byte assertions. Generate expected byte sequences by running `qemu-img map` against synthetic fixtures during development (commit the expected bytes inline). Keep `map_state_triple` and `map_error_message` from phase 3c (they will be reused by 4b). The renderer is *unused* by `run_map` until 4b lands — phase 3c's renderer still owns the call site. **High effort because:** byte-for-byte parity has many small traps (the literal `0` rule, the JSON `,\n` separator, the field order, the `compressed: false` always-emitted rule) and each one is invisible to a structural test. Cross-check expected bytes against `qemu-img map --output={human,json}` for the matching synthetic images during 4a. Touches only `src/vmm/src/main.rs`. |
| 4b | medium | sonnet | none | Replace the phase 3c renderer in `run_map`: drop the `extents: Vec<MapExtentMessage>` collector and the `print_map_result(&extents, &result, ...)` call. Construct a `MapRenderer` before the vCPU loop, call `renderer.begin()`. In the loop's `MapExtent` arm call `renderer.emit_extent(&e)`. After the loop, if the result has a non-OK error call `map_error_message(code)` to stderr; otherwise call `renderer.finish()`. Delete the obsolete `format_map_human` / `format_map_json` / `print_map_result` functions. Update the phase 3c `map_renderer_tests` to import from the new `MapRenderer`-based shape (or delete the tests that targeted the removed functions; the byte-exact tests in 4a cover the new contract). Handle `BrokenPipe` cleanly per Open question 2. Run `make instar`, `make lint`, `make test-rust`. The test count should remain ≥ 870 (phase 3c had +18; phase 4a adds ≥14 new; removing the deleted-function tests subtracts some, net should be a small positive). |
| 4c | low | sonnet | none | Add a new `## map subcommand quirks` section to `docs/quirks.md` between the measure subcommand quirks (line 791 area) and the next major section. Cover the seven entries listed in the "docs/quirks.md additions" section above. Each entry follows the format used by the measure section: short heading, two-paragraph body where applicable, cross-reference to PLAN-map.md or the relevant source code. No code changes — pure docs. Run `pre-commit run --all-files`. |
| 4d | low | sonnet | none | Update `ARCHITECTURE.md`'s `operations/map/` entry to mention that phase 4 has shipped: byte-for-byte human / JSON output via the `MapRenderer` struct, streaming emission, documented divergences in `docs/quirks.md`. Update `CHANGELOG.md` Unreleased / Added with one line crediting phase 4. Run `pre-commit run --all-files`. |

Total: 4 commits.

### Out of scope for phase 4

- Cross-version baseline generation (phase 5).
- `map-human` / `map-json` output profile machinery
  (phase 5 if baselines reveal drift).
- Integration tests against real testdata (phase 6).
- Coverage-guided fuzz harness (phase 7).
- Differential fuzz against qemu-img map (phase 8).
- Compressed-cluster reporting fix (future work; the
  walker / FFI / proto path needs a new bit).
- Raw `SEEK_HOLE` host-side prepass (future work).
- VHDX partial-present per-sector walk (future work).
- Backing-chain composition (future work).

## Success criteria

- `MapRenderer<'a, W: Write>` lands in
  `src/vmm/src/main.rs` with `new`, `begin`,
  `emit_extent`, `finish`.
- `run_map` uses `MapRenderer` for output; no
  `Vec<MapExtentMessage>` host-side buffering remains.
- For a hand-crafted fragmented qcow2 fixture:
  - `instar map --output=json FIXTURE` produces a byte-
    identical output to `qemu-img map --output=json
    FIXTURE` (modulo the documented divergences).
  - `instar map --output=human FIXTURE` produces a byte-
    identical output to `qemu-img map --output=human
    FIXTURE`.
- For the same source images:
  - Holes are elided in human output.
  - `compressed: false` appears in every JSON object.
  - No trailing newline after `]`.
- `make instar` builds.
- `make lint` clean.
- `make test-rust` passes; renderer tests assert exact
  byte sequences.
- `pre-commit run --all-files` clean.
- `docs/quirks.md` documents the seven map-specific
  divergences.
- `ARCHITECTURE.md` and `CHANGELOG.md` reflect phase 4.

## Risks and mitigations

- **Byte-for-byte traps**: the literal-`0` rule and the
  `compressed` always-emit rule are both invisible to
  structural tests. Mitigation: 4a writes the expected
  byte sequences inline in the test source, generated
  by running qemu-img during development. Any future
  qemu-img format change will fail the byte-exact tests
  and surface a baseline-update task.

- **Streaming write performance**: 17M extents through
  a `BufWriter<Stdout>` should be no slower than the
  Vec-then-render path. Mitigation: phase 4 doesn't
  micro-optimise; phase 7 / 8 (fuzz / differential) will
  surface any pathological cases.

- **BrokenPipe error spam**: piping `instar map` into
  `head` produces an `EPIPE` after the head closes.
  Mitigation: 4b catches `BrokenPipe` specifically and
  exits 0 (matches what coreutils does for the same
  pattern).

- **Test fragility**: byte-exact tests against the
  current qemu-img will break when qemu-img changes
  format. Mitigation: the cross-version baseline matrix
  in phase 5 handles version-keyed expected outputs;
  phase 4's tests pin only the *current-system*
  output. If qemu-img 11.x changes format, phase 5's
  output-profile machinery absorbs it.

- **`compressed: false` divergence**: emitting the field
  as a literal `false` even for compressed clusters
  produces output that differs from qemu-img for the
  niche compressed-cluster case. Mitigation: documented
  in quirks; the differential fuzzer (phase 8) is
  configured to skip the compressed-cluster paths
  pending the FFI extension.

- **Header before guest failure**: writing the human
  header before the guest has produced any output means
  a guest startup failure leaves `Offset Length Mapped
  to File\n` on stdout. Mitigation: documented in
  quirks; the exit code distinguishes success from
  failure; JSON consumers don't see this case because
  the JSON `begin()` only writes `[` which is also
  invalid by itself (the consumer checks exit code).

## Back brief

Before executing any step, the executing agent should
back-brief: which file is being edited (almost always
`src/vmm/src/main.rs`), which existing function is the
closest template (`MapRenderer` is genuinely new —
nothing in the codebase has the exact shape; the
streaming pattern is closest to how `convert`'s
progress messages drive `send_progress`, but the
renderer is simpler), and which expected byte sequences
in the tests came from running qemu-img map against
which synthetic fixtures. The reviewer should verify no
step bleeds into phase 5 (baselines), phase 6
(integration tests), or phase 7 / 8 (fuzz).
