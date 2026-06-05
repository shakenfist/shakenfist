# Phase 9: documentation and CHANGELOG

Master plan: [PLAN-map.md](/components/instar/plans/PLAN-map/) ·
Previous phase: [PLAN-map-phase-08-fuzz-differential.md](/components/instar/plans/PLAN-map-phase-08-fuzz-differential/)

## Status: Complete

Both steps committed. `docs/map.md` shipped at ~252 lines
with real captured human + JSON output from a fresh
fragmented qcow2. Cross-document touch-ups: `docs/index.md`
gains a Map row; `README.md` gains an Allocation Map
section between Commit and Version Compatibility;
`AGENTS.md` gains a `map` operations bullet; the
`PLAN-convert-followups.md` deferred list now reads
`(~~create~~ / ~~map~~ / ~~measure~~ / ~~resize~~ /
snapshot / ~~rebase~~ / ~~commit~~)` — only `snapshot`
remains; `docs/plans/index.md` shows PLAN-map as Complete
(phases 1-9); the master plan's Execution table marks
every phase 1-9 Complete. `ARCHITECTURE.md` already
mentioned phases 7 and 8 from the per-phase docs steps.

## Mission

Land the user-facing and project-meta documentation for
`instar map`:

1. A new `docs/map.md` reference page covering the CLI
   surface, output format, per-format extent classification
   rules, qemu-img divergences, and future-work pointers —
   modelled on `docs/measure.md`.
2. Index, README, AGENTS, and ARCHITECTURE updates so the
   command is discoverable from every entry point a user or
   contributor lands on.
3. Strike `map` from `docs/plans/PLAN-convert-followups.md`'s
   deferred-subcommand list (`snapshot` becomes the sole
   remaining item).
4. Flip the master plan's Execution table to mark phase 9
   Complete, and update `docs/plans/index.md`'s PLAN-map row
   from "Drafted, not started" to "Complete (phases 1-9)".

Phase 9 ships only documentation. No production code, no
test changes, no fuzz harness edits. CHANGELOG entries from
phases 1-8 already cover the implementation work; phase 9
does **not** add a separate "documentation" entry to
CHANGELOG (the documentation is itself the documentation —
no user-visible behaviour change).

## Why this is its own phase

- Documentation is genuinely separate work: it consolidates
  the per-phase prose scattered across phase plans, the
  CHANGELOG entries, and the quirks document into a single
  reference page that a user landing on `docs/map.md` can
  read straight through.
- Splitting from phases 1-8 keeps each implementation phase
  focused on its production change and lets documentation
  be reviewed for *narrative coherence* rather than
  technical accuracy alone.
- The `PLAN-convert-followups.md` strikethrough and the
  master-plan flip-to-Complete are administrative gestures
  that mark the project as done — they belong at the very
  end so the strikethrough state and the master plan's
  status are always honest about the current ship state.

## Architecture

### `docs/map.md` (new, ~200 lines)

Following the structure of `docs/measure.md`:

```
# `instar map` — emit the allocation map of a disk image

## Synopsis           # CLI signature, common options
## Output format      # Human + JSON examples, field semantics
## Per-format extent classification   # qcow2, vmdk, vhd, vhdx, raw
## Known divergences from qemu-img    # Cross-link docs/quirks.md
## Future work        # Cross-link master plan's Future work
## Examples           # 3-5 representative invocations
```

Key content, in order:

1. **Lead paragraph** — one-sentence purpose: emit the
   allocation map of a disk image as a stream of contiguous
   `(start, length, state)` extents covering
   `[0, virtual_size)`, mirroring `qemu-img map`'s output.
   Single-image v1; backing-chain composition deferred.

2. **Synopsis** — clap signature with `[OPTIONS] <INPUT>`,
   then the common options block listing `-f FMT`,
   `--output={human,json}`, `--start-offset`,
   `--max-length`, `--sector-size`. Note `--image-opts` is
   explicitly rejected.

3. **Output format** — both a human and a JSON example
   captured from `instar map` against a known
   fragmented fixture (use one of the existing
   `tests/test_map.py` fixtures or the manual smoke
   image from phase 3a's testing notes). Field-by-field
   table: `start`, `length`, `depth`, `present`, `zero`,
   `data`, `compressed`, `offset`, `filename` — with one
   line each on what they mean and which are emitted
   conditionally.

4. **Per-format extent classification** — short subsection
   per format describing how that format's on-disk
   allocation metadata maps to the three `MapExtentState`
   variants. Cross-link `src/crates/<format>/src/lib.rs`'s
   `map_extents` for the canonical implementation.
   - **qcow2**: L1 → L2 walk; `Hole` for zero L2 entries,
     `ZeroAllocated` for `QCOW2_CLUSTER_ZERO_PLAIN/ALLOC`,
     `Data` for allocated and compressed clusters.
   - **vmdk**: grain directory → grain table; allocated
     grains emit `Data`, sentinel `0xFFFFFFFE` emits
     `ZeroAllocated`, unallocated emits `Hole`.
   - **vhd**: BAT walk; `0xFFFFFFFF` BAT entries emit
     `Hole` (note: instar's report-as-Hole convention vs.
     qemu-img's report-as-ZeroAllocated; documented in
     quirks).
   - **vhdx**: BAT walk skipping interleaved sector-
     bitmap entries; `PAYLOAD_BLOCK_FULLY_PRESENT` →
     `Data`, `ZERO`/`UNMAPPED` → `Hole`,
     `PARTIALLY_PRESENT` → `Data` (per-sector bitmap
     walk is future work).
   - **raw**: single fully-allocated extent covering the
     virtual size (no `SEEK_HOLE` from inside the
     guest).

5. **Known divergences from qemu-img** — a compact list
   linking each item to its `docs/quirks.md` entry:
   - Raw `SEEK_HOLE` not detected.
   - qcow2 compressed clusters reported as
     `compressed: false`.
   - VHD unallocated blocks reported as `present: false`.
   - VHDX `PARTIALLY_PRESENT` blocks reported as
     `data: true`.
   - VMDK multi-extent sources refused host-side.
   - Backing-chain `depth` always 0 (chain composition
     deferred).
   - `--image-opts` rejected.
   - Window-filter clip is byte-level (instar) vs.
     cluster-rounded (qemu-img).
   - JSON `]` followed by a trailing newline (matches
     qemu-img; corrected during phase 6b).

6. **Future work** — five bullets:
   - Backing-chain `depth` composition.
   - Raw `SEEK_HOLE` host-side prepass.
   - VHDX per-sector bitmap walk.
   - VMDK multi-extent descriptor propagation.
   - qcow2 compressed-cluster sub-classification +
     L2 offset stripping.

7. **Examples** — five short invocations:
   - Default human output: `instar map disk.qcow2`.
   - JSON for scripting: `instar map --output=json
     disk.qcow2`.
   - Window slice: `instar map --start-offset=1M
     --max-length=4M disk.qcow2`.
   - Format hint: `instar map -f vmdk disk.vmdk`.
   - Streaming consumer: pipe into `jq` to extract just
     the data extents.

### Cross-document touch-ups

| File | Change |
|------|--------|
| `docs/index.md` | New row in the "Instar-Specific Features" table: `\| [Map](/components/instar/plans/map/) \| instar map - emit the allocation map of a disk image \|`. Insert after the existing `Commit` row (line 50). |
| `docs/usage.md` | Already mentions `qemu-img map` at line 111 in the qemu-img-surface inventory. No change unless the smoke run reveals an inconsistency; phase 9 leaves it. |
| `README.md` | New `### Allocation Map` section between `Image Commit` (line 348) and `Version Compatibility` (line 367). Short — 5–8 lines, one shell example each for human + JSON, cross-link `docs/map.md`. |
| `AGENTS.md` | New `map` bullet in the operations list around line 95 (after the `resize` entry). One paragraph: streaming guest-emitted extents over the serial channel, host renderer matches qemu-img modulo quirks, see `docs/map.md`. |
| `ARCHITECTURE.md` | Already has substantial map coverage (lines 427-471). Phase 9 verifies it mentions phases 7 (`fuzz_map_iter`) and 8 (`op_map` differential) — those were added in steps 7b and 8b respectively, so likely already complete. Pre-flight check during 9b. |
| `docs/quirks.md` | Already complete — every documented divergence has been added in its respective phase (4, 6, 8). No change. |
| `docs/format-coverage.md` | Currently lists detection and conversion-output support per format. `map` is read-only on the source — no row to add. No change. |
| `docs/plans/PLAN-convert-followups.md` | Strike `map` from the deferred list. The Execution table row currently reads `(~~create~~ / map / ~~measure~~ / ~~resize~~ / snapshot / ~~rebase~~ / ~~commit~~)`. Change to `(~~create~~ / ~~map~~ / ~~measure~~ / ~~resize~~ / snapshot / ~~rebase~~ / ~~commit~~)` and add a status-line note pointing at PLAN-map. |
| `docs/plans/index.md` | Update the PLAN-map row (currently says "Drafted, not started") to "Complete (phases 1-9)" with the phase summaries inline, matching the format used by the PLAN-measure row at line 23. |
| `docs/plans/PLAN-map.md` | Mark phase 9 Complete in the execution table; verify the success-criteria section still reflects shipped state; verify the Future work section still names the right deferred items. |

### CHANGELOG handling

The Unreleased / Added section already has phase-by-phase
entries from phases 1-8 (Added each landed). Phase 9 does
**not** add a "phase 9 — documentation" line. The new
`docs/map.md` is itself the documentation, not a user-
visible behaviour change.

If at the time of phase 9 the Unreleased section is being
prepared for an actual version bump, consolidate the
eight phase-by-phase entries into a single
"`instar map` subcommand" paragraph — but that
consolidation is a *release* step, not phase 9 work.
Leave the per-phase entries as-is unless explicitly asked.

### What we are NOT documenting

- Internal Rust API surfaces (the per-parser
  `map_extents` function signatures): documented in
  rustdoc on the function itself; the user-facing
  `docs/map.md` doesn't need to cross-link them. Engineers
  follow `ARCHITECTURE.md` → source.
- The protobuf wire format (`MapExtentMessage`,
  `MapResultMessage`): an implementation detail behind the
  guest-VMM boundary; documented inline in
  `crates/guest-protocol/proto/guest.proto`.
- The fuzz target invariants: documented in the
  `fuzz_map_iter.rs` header comment + this plan series.
- The baseline matrix structure
  (`expected-outputs/map-{human,json}/<bucket>/<version>/`):
  documented in `instar-testdata`'s README, not here.
- The integration-test KNOWN_MAP_DIVERGENCES dict:
  documented inline in `tests/test_map.py`.

The principle is: `docs/map.md` is for the user who runs
`instar map`. Engineers wanting more depth follow
`ARCHITECTURE.md` → source → phase plans.

## Open questions

1. **Should `docs/map.md` include the streaming-vs-buffering
   discussion?** That's an internal architecture
   consideration; the user just sees streaming output.
   **Recommendation**: no. Brief mention in the output-
   format section ("instar emits each extent as it walks
   the source — host memory is O(1) regardless of how
   fragmented the source is") is enough.

2. **Should the per-format extent classification subsection
   include the L1/L2 entry bit layout?** That's deep
   parser territory. **Recommendation**: no. The user
   needs to know *what* extents come out, not *how* the
   parser computes them. Engineers reading source will
   find the bit layout inline.

3. **VHD `present` divergence positioning**: this is a
   genuine semantic disagreement that affects downstream
   consumers (a tool that checks `present` will see
   different answers from the two binaries on the same
   image). Should `docs/map.md` flag it prominently or
   bury it in the Known Divergences list?
   **Recommendation**: list it in Known Divergences with a
   one-line "tool authors: prefer `data` and `zero` over
   `present` for backwards-compatible behaviour" pointer.
   Don't lead with it — the divergence affects a narrow
   class of consumer.

4. **README section placement**: between Commit (line 348)
   and Version Compatibility (line 367), or at the bottom
   of the "Image …" hierarchy after Image Conversion?
   **Recommendation**: between Commit and Version
   Compatibility. That's the chronological order matching
   the plans (rebase, commit, map landed in that
   sequence), and the reader who's just learned about
   commit will naturally continue to map.

5. **AGENTS.md cataloguing depth**: looking at the
   existing entries, `measure` has 3 lines, `create`
   has 8 lines, `resize` has 13 lines. The trend is
   "more is better, especially for the lifecycle-affecting
   operations". `map` is read-only and bounded in scope.
   **Recommendation**: target ~6 lines, matching the
   complexity-to-line ratio of `measure`.

6. **`docs/usage.md` is a static survey of qemu-img
   commands**: it already mentions `qemu-img map` at line
   111 in the consumer-coverage matrix and at line 233 in
   the consumer-API summary. Both are descriptions of
   the *qemu-img* command, not instar. Should phase 9
   add an instar-coverage column? **Recommendation**:
   leave alone. `docs/usage.md` is a survey of the
   landscape, not the instar reference. The reference is
   `docs/map.md` and the README.

7. **PLAN-convert-followups.md strikethrough syntax**:
   the existing strikethroughs use `~~name~~`. Phase 9
   uses the same. **Confirmed**.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | medium | sonnet | none | Create `docs/map.md` following the structure outlined in the Architecture section. Use `docs/measure.md` as the closest template (same shape: Synopsis → Output format → Per-format → Known divergences → Future work → Examples). Target ~200 lines (matches `docs/measure.md`'s length). For the output-format examples, capture real `instar map` output from a small fragmented qcow2 — either reuse the manual smoke fixture or build a fresh one via `qemu-img create -f qcow2 -o cluster_size=65536 t.qcow2 1M && qemu-io -c "write -P 0xab 0 64K" t.qcow2` and run `instar map`. Cite each Known Divergence by section heading in `docs/quirks.md` so the cross-link is durable. Run `pre-commit run --files docs/map.md`. |
| 9b | low | sonnet | none | Cross-document touch-ups per the table in the Architecture section: `docs/index.md` (new Map row), `README.md` (new `### Allocation Map` section between Commit and Version Compatibility), `AGENTS.md` (new `map` bullet after `resize`), `docs/plans/PLAN-convert-followups.md` (strike `map` from the deferred list — change `/ map /` to `/ ~~map~~ /` and add an inline status note pointing at PLAN-map), `docs/plans/index.md` (flip the PLAN-map row from "Drafted, not started" to "Complete (phases 1-9)" with phase summaries inline matching the PLAN-measure row at line 23), `docs/plans/PLAN-map.md` (mark phase 9 Complete in the Execution table, update the row in `docs/plans/index.md` to Complete per the master plan's "When all phases are complete" instruction). Pre-flight check on `ARCHITECTURE.md` lines 427-471 — confirm phases 7 and 8 are already mentioned (added in steps 7b/8b); if missing, add a short sentence each. Run `pre-commit run --all-files`. |

Total: 2 commits.

### Why no high-effort step

Phase 9 is pure documentation consolidation. No new
material to derive; all the technical decisions and
divergences have been pinned by phases 1-8. The work is
prose composition (9a) and a structured set of small edits
across known files (9b). Sonnet with a detailed brief is
the right tool. The Open Questions above pre-decide every
non-obvious framing choice so the sub-agent doesn't have
to interpret.

### Out of scope for phase 9

- Code changes (none required).
- Test changes (none required).
- CHANGELOG consolidation into a single "map subcommand"
  entry (deferred to the actual release prep).
- New benchmarking / performance documentation (no
  benchmarks ship with phase 1-8; the streaming-emission
  approach is described qualitatively).
- Cross-references from format-specific docs
  (`docs/qcow2/*.md` etc.) into `docs/map.md` — those
  docs already cross-link the production code paths
  generically; no new map-specific link is needed.
- Tutorial / cookbook material — `docs/map.md` is a
  reference, not a tutorial. If a tutorial is wanted
  later, that's a follow-up.

## Success criteria

- `docs/map.md` exists, ~150-250 lines, with all seven
  sections enumerated in the Architecture section.
- `docs/index.md`, `README.md`, and `AGENTS.md` each have
  a `map` entry discoverable from the natural reading
  path of a new user.
- `docs/plans/PLAN-convert-followups.md` strikes `map`
  from the deferred list.
- `docs/plans/index.md` shows PLAN-map as Complete
  (phases 1-9).
- `docs/plans/PLAN-map.md`'s Execution table marks
  phase 9 Complete.
- `pre-commit run --all-files` clean (documentation-only
  changes; nothing Rust-side touched).
- All cross-links in `docs/map.md` resolve to existing
  anchors in `docs/quirks.md` and the source tree.

## Risks and mitigations

- **Cross-link rot**: linking to `docs/quirks.md` section
  headings is fragile if a future quirks edit renumbers
  them. Mitigation: link by section title text (which
  GitHub-flavored Markdown renders as a stable anchor)
  rather than by ordinal. Verify each cross-link with a
  local browser render or `markdown-link-check` pass
  during 9a.

- **`docs/map.md` examples drift**: real `instar map`
  output captured at the time of 9a authoring may drift
  if the renderer changes later. Mitigation: the byte-
  exact output is pinned by phase 4a's `MapRenderer` unit
  tests and phase 5's cross-version baselines; a renderer
  change would surface there before the doc looks
  stale. If the doc's example diverges in a future
  iteration, the test failure precedes the doc
  staleness.

- **README placement disrupts existing layout**: the
  README's "Image …" hierarchy currently doesn't have a
  "read-only operation" stratum. Inserting Map between
  Commit and Version Compatibility is the cleanest spot
  but breaks the implicit "mutating operations" theme.
  Mitigation: leave the section heading at "Allocation
  Map" (not "Image Map", which would conflate with
  Image Conversion semantically) — the noun-first form
  signals it's a different operation class.

- **AGENTS.md bullet drift**: the operations list in
  AGENTS.md is the easiest place for a new operation to
  be silently missed. Mitigation: 9b's brief explicitly
  enumerates the file. The pre-commit hook doesn't
  catch missing prose; reviewer responsibility.

- **`PLAN-convert-followups.md` strikethrough syntax
  mismatch**: the file already uses `~~name~~` for the
  five completed subcommands; the brief calls this out
  explicitly so the new `~~map~~` strikethrough is
  consistent.

## Back brief

Before executing step 9a, the sub-agent should back-brief:

- The file being created (`docs/map.md`) and the closest
  template (`docs/measure.md`).
- The seven sections in order and the per-section content
  pinned by the Architecture section.
- The source for the output-format examples (build a fresh
  small fragmented qcow2; do not hand-fabricate the
  output).
- The cross-link policy (link by section title text, not
  ordinal).

Before executing step 9b, the sub-agent should back-brief:

- The table of files being edited (8 files); the exact
  change per file as enumerated in the Architecture
  section.
- The strikethrough syntax (`~~map~~`) and the placement
  in `PLAN-convert-followups.md`.
- The PLAN-map row format in `docs/plans/index.md`
  (matches the existing PLAN-measure row at line 23 for
  the Complete state).

The reviewer should verify:

- `docs/map.md`'s examples come from real `instar map`
  invocation, not paraphrase.
- The README section is between Commit and Version
  Compatibility, not somewhere else.
- The AGENTS.md bullet is the right length for the
  operation's complexity (~6 lines per Open Question 5).
- `docs/plans/PLAN-convert-followups.md`'s deferred
  list reads `(~~create~~ / ~~map~~ / ~~measure~~ /
  ~~resize~~ / snapshot / ~~rebase~~ / ~~commit~~)` —
  six struck, one (`snapshot`) remaining.
- The master plan's Execution table marks every phase
  1-9 Complete.
- `pre-commit run --all-files` was actually invoked, not
  just claimed.
