# Phase 8: differential fuzzing against `qemu-img map`

Master plan: [PLAN-map.md](/components/instar/plans/PLAN-map/) ·
Previous phase: [PLAN-map-phase-07-fuzz-coverage.md](/components/instar/plans/PLAN-map-phase-07-fuzz-coverage/)

## Status: Complete

Both steps committed. `op_map` is wired into the random
operation chain in `scripts/differential-fuzz.py`. The 200-
iteration local smoke (seed=1) initially surfaced 14
divergences all of one shape — vpc unallocated BAT blocks
report `present=false` from instar (true to `0xFFFFFFFF`)
vs `present=true, zero=true` from qemu-img (the
ZeroAllocated convention also used for raw sparse runs).
This matches phase 6's `KNOWN_MAP_DIVERGENCES` entries for
`hyperv-dynamic-vhd` and `virtualpc-vhd`. Added a per-format
`MAP_FIELD_SKIPS` catalogue that skips the `present` field
on `vpc` while keeping `{start, length, zero, data}` active
— catches BAT-walking boundary bugs without flooding on the
documented semantic difference. Documented in
`docs/quirks.md`. Re-run: 200 iterations, 0 divergences.

## Mission

Extend `scripts/differential-fuzz.py` so its random operation
chain includes `map`. For each generated image (where both
binaries support the format), run `instar map --output=json`
and `qemu-img map --output=json` against independent copies,
parse both JSON arrays, and compare the emitted extents
field-by-field. Disagreements are reported as divergences;
the enumerated allowed-divergence categories are skipped at
the format level (raw is skipped entirely, mirroring
`op_info`'s precedent) and at the structural level (`offset`
and `compressed` are excluded from the field comparison
because compressed-cluster reporting drifts).

Phase 8 is the cross-binary safety net. Phase 6 pins
`instar map` against pre-captured `qemu-img map` baselines on
a fixed set of safe-tier images; phase 7 fuzzes the parser
walkers in-process. Phase 8 closes the loop by exercising the
same comparison on the *randomly-generated* image stream that
the existing differential fuzzer already pipes through info /
check / convert / measure / create / resize / rebase / commit.
Format-allocation patterns the curated baselines don't reach
(odd cluster sizes, random small writes, sparse interspersion)
get covered organically.

## Why this is its own phase

- The differential fuzzer is a separate harness from the
  coverage-guided one (different CI workflow, different
  failure mode, different signal type). Keeping the
  comparison logic in `op_map` self-contained keeps it
  reviewable.
- Phase 8 ships only the comparison plumbing — no new image
  generation, no new test surface, no production-code
  changes. If it surfaces a real bug on its first run, that
  bug is filed against the relevant parser or the host
  renderer, not absorbed into this phase.
- Splitting from phase 7 (in-process partition invariant)
  keeps the deterministic structural assertion separate from
  the cross-binary output comparison. The two find different
  bug classes — phase 7 catches walker bugs that produce
  internally-inconsistent extent streams; phase 8 catches
  bugs where the stream is internally consistent but
  disagrees with qemu-img.

## Architecture

### `op_map` function shape

A new `op_map(instar_bin, instar_copy, qemu_copy, fmt,
timeout, rng)` function follows the structural pattern of
`op_info` (the existing JSON-comparison op):

1. Format gate: `if fmt == 'raw': return None`. Raw is a
   documented divergence — instar emits one fully-allocated
   extent; qemu-img walks `SEEK_HOLE`. The phase 7 plan
   discussed this; phase 6's `KNOWN_MAP_DIVERGENCES` already
   marks `raw-sparse-empty` for the same reason. The
   fuzzer's raw images (created by `qemu-img create -f raw`)
   are sparse by default, so this divergence would fire on
   essentially every iteration without the gate.

2. Optional window selection: with probability ~25%, pick a
   `--start-offset` value from `[0, virtual_size / 2]`
   aligned to 64 KiB; with probability ~25%, pick a
   `--max-length` from `[64 KiB, virtual_size]` aligned to
   64 KiB. The same arguments are passed to both binaries.
   Window arguments diversify coverage — phase 6's window
   tests are structural-only (no qemu-img comparison),
   so phase 8 is the first place we cross-check that the
   window-clip behaviour matches qemu-img on arbitrary
   inputs. Stay aligned to 64 KiB to dodge instar's byte-
   level window clip vs. qemu-img's cluster-rounded one
   (the documented quirk in `docs/quirks.md`). The image's
   virtual size comes from `attrs['virtual_size']` parsed
   to bytes via `_resize_parse_qemu_size`.

3. Run both: `run_instar(instar_bin, ['map'], [...,
   '--output', 'json', str(instar_copy)])` and
   `run_qemu_img(['map'], [..., '--output=json',
   str(qemu_copy)])`. Pass the same window args to both.

4. Exit-code comparison via `compare_exit_codes(i_rc, q_rc,
   'map', context_dict)`. Both should succeed on
   fuzzer-generated images (no chain, no compressed
   clusters, no vhdx). A disagreement here is a divergence.

5. If both succeeded (rc == 0), parse both JSON arrays with
   `json.loads`. Parse failure on either side is a
   divergence (`map_json_parse_failure`).

6. Extent-by-extent comparison via a helper
   `compare_map_extents(i_array, q_array, fmt, ...) ->
   Optional[dict]`. The helper:
   - Asserts the two arrays have the same length. Mismatch
     → `map_extent_count_divergence`.
   - For each `(i_ext, q_ext)` pair, compares the
     comparison-relevant fields (see "Field selection"
     below). Mismatch → `map_field_divergence` with the
     extent index, field name, and both values.

7. On a clean comparison return `None`.

### Field selection: what to compare and what to skip

`instar map --output=json` emits per extent:

```
{"start": N, "length": N, "depth": 0,
 "present": bool, "zero": bool, "data": bool,
 "compressed": false, "offset": N}
```

with `offset` omitted when `present == false`. Compare:

- `start` — virtual offset; must match.
- `length` — extent length; must match.
- `present` — backing-store presence; must match.
- `zero` — reads-as-zero; must match.
- `data` — contains-data; must match.

Skip:

- `depth` — always 0 in v1 on both sides (instar refuses
  chains; fuzzer doesn't generate chains). Adds no signal.
- `compressed` — instar emits `false` always; qemu-img
  may emit `true` for compressed-cluster extents. The
  fuzzer doesn't generate compressed clusters (no
  `convert -c`), but skip defensively. Documented in
  `docs/quirks.md` under map quirks.
- `offset` — file offset has the same compressed-cluster
  reporting divergence (instar treats the L2 offset
  directly; qemu-img uses the high-bit-set marker
  convention). Even in the absence of compressed clusters,
  cross-format file-offset reporting is fragile (vhdx and
  vmdk file offsets are derived differently between
  binaries). Skip; the partition invariant from phase 7
  catches the related parser-side bugs.
- `filename` — different paths between the two copies.
  Trivially divergent.

If phase 5's three `map-json` profiles indicate qemu-img's
`{start, length, present, zero, data}` set is reliable across
the matrix (it should be — those are the load-bearing
fields), the comparison is robust. The smoke run in 8a
verifies.

### Format coverage

`FORMATS = ['qcow2', 'raw', 'vmdk', 'vpc']` — the existing
list. `op_map`'s behaviour per format:

| Format | Compare? | Notes |
|--------|----------|-------|
| qcow2  | yes      | Primary signal source. Random cluster sizes (512 / 4096 / 65536 / 262144 / 2097152) exercise the L1/L2 walk under varied geometries. |
| raw    | **skip** | Sparse-vs-allocated divergence; instar treats raw as one extent. |
| vmdk   | yes      | `monolithicSparse` (the only subformat the generator uses); grain-directory / grain-table walk. |
| vpc    | yes      | VHD; tests the BAT walk. The fuzzer doesn't generate vhdx images so the partial-present divergence doesn't fire. |

If the smoke run shows a category of vmdk or vpc inputs that
divergence-floods (e.g. monolithicSparse images with an
unusual grain-directory layout), the format gate is the
escape hatch: extend the early `if fmt in (...)` gate
analogously to raw, documenting the category.

### Image-side scope: no new generation

`generate_image` is **not** extended in this phase. The
existing FORMATS / VIRTUAL_SIZES / DATA_PATTERNS produce a
wide enough random distribution for `map` to exercise the
walkers' allocation paths. Specifically:

- `pattern == 'random'` invokes `_write_random_data` which
  uses `qemu-io write -P <byte> <offset> <size>` to allocate
  random clusters at random offsets — exactly the kind of
  fragmented allocation pattern `map_extents` exists to
  enumerate.
- `pattern == 'sparse'` leaves the image all-zeros (no
  allocations) — the all-hole case.
- `pattern == 'mbr'` writes 512 bytes at offset 510 on raw
  — irrelevant since raw is skipped.
- `pattern == 'zeros'` (curious entry — looks unused in the
  conditional chain at lines 132-138, falls through to
  no-op) — all-zeros, same shape as `sparse`.

Extending the generator with `--backing-file` for chain
sources, or `convert -c` for compressed clusters, would
require corresponding allowed-divergence categories and
expands phase 8's surface considerably. Defer to a separate
follow-up phase if the chain-composition story ever lands.

### Window argument selection

The window picker is small enough to inline in `op_map`
rather than carve out an `_map_option_picker(rng)` helper
(unlike `_resize_option_picker` which spans dozens of
mutually-exclusive flags). Pseudocode:

```python
def _map_window_args(rng, virtual_size_bytes):
    args = []
    if rng.random() < 0.25:
        # Cluster-aligned start-offset, 0..virtual_size/2
        align = 64 * 1024
        half = max(virtual_size_bytes // 2, align)
        start = (rng.randint(0, half) // align) * align
        args += ['--start-offset', str(start)]
    if rng.random() < 0.25:
        align = 64 * 1024
        min_len = align
        max_len = virtual_size_bytes
        length = (rng.randint(min_len, max_len) // align) * align
        args += ['--max-length', str(length)]
    return args
```

Both probabilities at 25% gives ~56% of iterations with no
window, ~19% with start-only, ~19% with length-only, ~6%
with both. The mix exercises the four combinations without
over-emphasizing the window path.

64 KiB alignment is deliberately conservative — instar
clips bytes, qemu-img clips clusters. Documented divergence.
Round to the larger of the two cluster sizes (qcow2 default
65536; vmdk/vhd grain/block sizes vary) so the rounding
never produces an alignment mismatch.

### Allowed-divergence catalogue

Module-scope `KNOWN_MAP_FUZZ_DIFFS` is *not* needed in
v1 — the only allowed divergence is the raw format-level
skip, which the early `if fmt == 'raw': return None`
handles. Image-level allowed divergences (chain refused,
vmdk multi-extent refused, compressed clusters) don't apply
because `generate_image` never produces them.

If the smoke run surfaces a new category (e.g. a
monolithicSparse vmdk variant that diverges on a specific
grain-table layout), it gets added as a module-scope
allowed-divergence dict, mirroring
`KNOWN_DIVERGENCE_FIELDS` at line 53.

### Divergence reporting

Each divergence return-dict carries the fields the existing
divergence-reporter expects. Phase 8 adds three new types:

- `map_exit_code_divergence` (from `compare_exit_codes`).
- `map_json_parse_failure` (one or both sides).
- `map_extent_count_divergence` (extents emitted differs).
- `map_field_divergence` (per-extent field mismatch; carries
  `extent_index`, `field`, `instar_value`, `qemu_value`,
  plus the first ~500 chars of both stdouts for diagnosis).

The full report is written via the existing
`write_divergence_report` flow; GitHub-issue filing via
`file_github_issue` works without changes (it takes
`divergence['type']` and the attrs dict).

### `run_iteration` wiring

Append `'map'` to `OPERATIONS` at line 48. Add an
`elif op == 'map':` arm to `run_iteration` at line 1939
following the structural pattern of `op_info`'s arm. The
`op_map` signature matches all the other ops
(`instar_bin, instar_copy, qemu_copy, fmt, timeout, rng`)
so no extra plumbing is needed.

### CI integration

`.github/workflows/differential-fuzz.yml` invokes
`differential-fuzz.py` with no `--operations` filter, so
adding `'map'` to `OPERATIONS` is sufficient — no workflow
edit. The CI workflow does have a paths filter that
triggers on changes to `scripts/differential-fuzz.py`, so
the phase 8 commit will run the fuzzer on PR push as a
smoke.

### Smoke run during 8a

Local smoke run to verify the integration:

```
python3 scripts/differential-fuzz.py \
    --instar-bin target/release/instar \
    --seed 1 \
    --iterations 200 \
    --workdir /tmp/fuzz-map-smoke
```

Acceptance:

- Completes 200 iterations without throwing.
- `map` appears in the operation chain on a meaningful
  fraction of iterations (`ops` is sampled with
  replacement; with 11 operations the expected count for
  `map` across ~600 op-slots is ~55).
- Zero divergences, *or* every reported divergence is
  traceable to a known-and-documented category that
  warrants extending the catalogue.

If a real bug surfaces (a `map_field_divergence` against an
image the curated phase-6 baselines never saw), file as a
parser or renderer bug under
[PLAN-fuzzing-bugs.md](/components/instar/plans/PLAN-fuzzing-bugs/), fix, then
commit phase 8.

Iteration count of 200 is a 5–10 minute local run — enough
to shake out wiring errors without burning a session. The
nightly CI run executes ~5,000 iterations against many more
seeds, which will surface long-tail issues independently.

## Open questions

1. **Human-format comparison?** The phase plan above
   compares JSON only. `--output=human` is column-aligned
   text whose byte layout drifts across qemu-img versions
   (the phase-5 work captured 1 profile for map-human
   across the matrix, suggesting human is *more* stable
   than JSON — but the underlying compare-to-baseline of
   that profile is the phase-6 test suite's job).
   **Recommendation**: JSON only in v1. Human can be added
   in follow-up if the JSON comparison reaches a stable
   zero-divergence steady-state. The marginal coverage is
   small since both renderers consume the same
   `MapExtentMessage` stream.

2. **Window argument compatibility with all qemu-img
   versions?** `--start-offset` and `--max-length` have
   been on `qemu-img map` since 4.2.0 (well before our
   6.0.0 matrix floor). No version gate needed.
   **Recommendation**: trust the matrix floor.

3. **vpc (vhd) coverage feasibility?** The generator
   produces vpc images via `qemu-img create -f vpc`. The
   VHD spec requires the image size to be a multiple of
   the geometry's CHS computation; qemu-img rounds up
   silently. instar's parser tolerates this. The two
   binaries should agree on the resulting BAT walk.
   **Recommendation**: include vpc in the comparison;
   if smoke shows persistent rounding-related divergence,
   gate it out.

4. **What if qemu-img on the host doesn't support `map`
   at all?** The CI image ships qemu-img ≥ 6.0.0; `map`
   exists. Local runs on older qemu-img may fail.
   **Recommendation**: don't probe; let `op_map` fail loud
   and let the user upgrade. Documented in the script
   header.

5. **Extent-count mismatch interpretation**: if instar
   emits 5 extents and qemu-img emits 6, the per-index
   field comparison falls apart on shifted alignments.
   **Recommendation**: report the count mismatch as a
   distinct divergence type (`map_extent_count_divergence`)
   and bail before per-field comparison. Don't try to
   align mismatched arrays — the divergence is real and
   the first-index mismatch reported is misleading. The
   full JSON stdouts are attached to the divergence
   record for inspection.

6. **Coalescing semantics on the boundary**: does qemu-img
   ever emit two adjacent `Data` extents with different
   file offsets that instar coalesces into one (or vice
   versa)? In principle yes — for qcow2, instar's
   coalescer merges contiguous file offsets; if qemu-img
   splits on something instar doesn't track (e.g. extent
   metadata flags that don't affect the visible extent
   shape), divergence fires. **Recommendation**: leave
   for the smoke run to discover. If it fires, the fix is
   either (a) document the divergence and gate the field,
   or (b) align instar's coalescer to qemu-img's split
   points. The decision depends on which side qemu-img is
   "more right" for downstream consumers — punt the call
   to the bug-fix flow.

7. **Stress on per-iteration runtime**: `map` on a
   maximally-fragmented 1 GiB image emits up to 16 K
   extents and could exceed the iteration's 30-second
   timeout. The fuzzer's image generator caps virtual
   size at 1 GiB and typical patterns produce far fewer
   allocations than that.
   **Recommendation**: trust the existing timeout; if a
   smoke run produces timeouts, document and gate the
   specific pattern.

8. **Should we extend `generate_image` to make backing
   chains?** Tempting (it would cross-check the
   "instar refuses backing" path against the qemu-img
   chain walk) but explicitly out of scope for v1 — the
   error-side comparison is what `op_info` and `op_check`
   already exercise on chain images via the test-suite,
   and adding chain generation to the fuzzer requires
   significant additional plumbing (parent path tracking,
   absolute vs. relative path testing, etc.). **Defer to
   a separate phase** when the chain-composition support
   itself lands.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | medium | sonnet | none | Edit `scripts/differential-fuzz.py`. Add `'map'` to `OPERATIONS` at line 48. Implement `op_map(instar_bin, instar_copy, qemu_copy, fmt, timeout, rng)` per the Architecture section: format gate for raw, optional window args via `_map_window_args(rng, virtual_size_bytes)` (a small local helper; pull virtual_size from the image generator's `attrs` by re-reading `instar_copy` via `os.stat` or by passing `attrs['virtual_size']` through `_resize_parse_qemu_size`). Compare `{start, length, present, zero, data}` per-extent; skip `{depth, compressed, offset, filename}` for the reasons in the Architecture section. Return divergence dicts of types `map_exit_code_divergence`, `map_json_parse_failure`, `map_extent_count_divergence`, `map_field_divergence`. Add the `elif op == 'map':` arm in `run_iteration` (around line 1985, between `commit` and the `else` fall-through). **Smoke run** locally: `python3 scripts/differential-fuzz.py --instar-bin target/release/instar --seed 1 --iterations 200 --workdir /tmp/fuzz-map-smoke`. Acceptance: 200 iterations complete, `map` appears in the chain ~50× (uniform `rng.choice` over the now 10-element OPERATIONS list — note that `convert` and `convert_compressed` are separate entries, so it's actually 10 ops), 0 unexplained divergences. If a real bug surfaces — **stop**, file it, fix it, then commit. `op_map` must not be committed with a known-failing smoke. The signature mirrors the other op functions; `attrs` (which has `virtual_size` as a qemu-img size string like `'16M'`) is *not* in scope at `op_map`'s call site, so the simplest path is for `op_map` to read the file size from disk: `virtual_size_bytes = subprocess.check_output(['qemu-img', 'info', '--output=json', str(qemu_copy)])` parsed to extract `virtual-size`. Alternative: pipe `attrs` through `run_iteration` to the op functions — but that's a wider refactor and unnecessary for one op. Use the qemu-img info probe. |
| 8b | low | sonnet | none | Update `ARCHITECTURE.md` "Differential fuzzing" section to mention that `op_map` is included in the random operation chain with JSON-mode extent-by-extent comparison (one sentence after the existing per-op summary). Update `CHANGELOG.md` Unreleased / Added with a one-paragraph entry. Flip the master plan's Execution table row 8 from "Not started" to "Complete" with a brief smoke-run summary (e.g. "Complete (op_map landed; 200-iteration local smoke, 0 unexplained divergences)"). Flip this plan's Status field likewise. Run `pre-commit run --all-files`. |

Total: 2 commits.

### Why no high-effort step

The harness pattern is established (eight other `op_*`
functions to copy from); the comparison logic is small
(~100 lines including the helper and the `elif` arm); the
allowed-divergence catalogue is empty in v1 (one format-
level skip). The design effort is in this plan — choosing
which fields to compare, which to skip, and how to handle
the window-arg cluster-rounding divergence. The
implementation following the brief is mechanical.

### Out of scope for phase 8

- Human-format comparison (deferred; open question 1).
- Extending `generate_image` to produce chain sources,
  compressed clusters, or vhdx (open question 8; deferred
  to chain-composition support phase).
- Cross-binary comparison against libyal tools
  (`vmdkinfo`, `vhdiinfo`) — those tools have no
  `map`-equivalent output.
- Per-extent file-offset comparison (skipped because of
  compressed-cluster reporting drift).
- Coalescer-alignment refactoring against qemu-img's split
  points (open question 6; depends on what the smoke
  surfaces).
- Differential fuzzing of `--image-opts` (rejected by both
  binaries, no payload to compare).

## Success criteria

- `scripts/differential-fuzz.py` has `'map'` in
  `OPERATIONS` and the matching `op_map` function and
  `elif` arm in `run_iteration`.
- Local 200-iteration smoke run completes cleanly with
  zero unexplained divergences (the only acceptable
  divergence is one whose category is added to the
  documented catalogue in the same commit).
- `pre-commit run --all-files` passes (Python-only
  changes; no Rust touched).
- `ARCHITECTURE.md` and `CHANGELOG.md` mention the new
  op.
- The master plan's Execution table marks phase 8 Complete.

## Risks and mitigations

- **Smoke run uncovers a real bug.** Most likely: a vmdk
  or qcow2 input where instar's `map_extents` coalescer
  splits or merges differently than qemu-img on a corner
  case. Mitigation: treat as a real find — file under
  `PLAN-fuzzing-bugs.md`, fix the parser or coalescer
  before committing phase 8. The phase 7 partition
  invariant catches internally-inconsistent walks; phase
  8 catches walks that are internally consistent but
  cross-binary inconsistent.

- **Cluster-alignment of window args is too aggressive.**
  64 KiB rounding may push `--max-length` past the
  virtual size on small images. Mitigation: clamp
  `--max-length` to ≤ `virtual_size_bytes`; the
  underlying clip behaviour is "stop at virtual_size"
  for both binaries, so excess values are no-ops, but a
  cleaner clamp avoids spurious tracking.

- **Window-arg semantics on past-EOF inputs**: phase 6c
  removed the host-side `--start-offset > file_size`
  check after discovering it was wrong for sparse
  qcow2s. The window picker stays in `[0, virtual_size]`
  so this code path is not exercised here; the integration
  tests in phase 6c already cover the past-EOF case.

- **qemu-img info probe at the start of every `op_map`
  call adds ~50 ms per iteration**: at 200 iterations this
  is 10 seconds — acceptable. At nightly CI's 5,000
  iterations it's 4 minutes — still fine. Mitigation: if
  it becomes an issue, plumb `attrs['virtual_size']`
  through `run_iteration` to the op functions instead.

- **JSON drift between qemu-img versions**: phase 5 found
  3 `map-json` profiles across the qemu-img matrix. The
  CI host's qemu-img is pinned by the devcontainer image,
  so version drift is bounded. Local runs against a
  different qemu-img may see new divergences — document
  the qemu-img version requirement in the script header.

- **`op_map` runs on an image whose virtual size is 1 MiB
  and the window picker selects `--start-offset=64 KiB
  --max-length=64 KiB`**: that's a 64 KiB window —
  potentially zero or one extent emitted. Both binaries
  should agree on the empty / single-extent shape;
  divergence here is real. No mitigation needed; the
  invariant is the same regardless of window size.

## Back brief

Before executing step 8a, the sub-agent should back-brief:

- The file being edited (`scripts/differential-fuzz.py`).
- The closest template op function (`op_info` at line 567
  — JSON comparison; format-gate-then-run-both pattern).
- The fields to compare (`{start, length, present, zero,
  data}`) and the fields to skip and why (`depth` always
  0; `compressed` always false on instar; `offset`
  compressed-cluster fragile; `filename` paths differ).
- The format gate (`raw` skipped entirely; precedent in
  `op_info` line 578).
- The window-arg picker details (25%/25% probabilities,
  64 KiB alignment) and the `qemu-img info` probe used to
  retrieve `virtual_size`.
- The smoke-run command and acceptance: 200 iterations,
  zero unexplained divergences. A real find stops the
  commit and triggers a parser-bug fix flow.

The reviewer should verify:

- The format gate is at the top of `op_map`, before
  either binary is invoked (mirrors `op_info`).
- `compare_exit_codes` is called with the same
  `operation='map'` argument string.
- The per-extent comparison loop bails on length mismatch
  before iterating, not part-way through (open question
  5).
- The window picker clamps `--max-length` to ≤
  `virtual_size`.
- The smoke ran for ≥200 iterations and the captured
  output shows `map` appearing in the operation chain.
