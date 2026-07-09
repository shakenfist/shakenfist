# PLAN-bench phase 06: integration tests

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (`tests/base.py`'s
`InstarTestBase` and runner helpers, `tests/test_bitmap.py` and
`tests/test_map.py` as the structural exemplars, the
`KNOWN_*_DIVERGENCES` registry idiom), and ground your answers in
what the code actually does today. The behavioural contract being
pinned is the one already captured empirically: the phase-01
plan's 1e capture (+ Supplement 2), the phase-04 plan's smoke
results, and the phase-05 plan's write verification — read all
three capture sections before writing a single assertion. Do not
speculate when you could read instead.

Phase plans live alongside the master plan
([PLAN-bench.md](/components/instar/plans/PLAN-bench/)) in `docs/plans/`. This is the
sixth of eight phases; phases 1-5 delivered a working
`instar bench` (reads on all five formats, writes on raw+qcow2)
whose deterministic surface was smoke-verified against qemu-img
10.0.8 but is not yet guarded by the test suite.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

This phase converts the 4c/5c smoke matrices into
`tests/test_bench.py` so the contract survives future changes,
and formalizes `KNOWN_BENCH_DIVERGENCES`. Test-infrastructure
facts (surveyed):

- **Runner**: stestr over testtools (NOT pytest); a new
  `tests/test_bench.py` is auto-discovered via
  `tests/.stestr.conf` — zero registration. `make
  test-integration` runs the suite (16-way on this host; tests
  must be order-free and self-contained —
  `tempfile.TemporaryDirectory` per test). File-scoped run:
  `cd tests && ../.venv/bin/stestr run test_bench`.
- **Base class**: `InstarTestBase` (`tests/base.py:39`);
  `get_instar_binary()` (`:315`, env override then
  `src/target/release/instar`); runner helpers return
  `(stdout, stderr, returncode)` triples; per-subprocess
  timeouts (bitmap uses 120 s for guest launches).
- **KVM guard**: `_require_kvm()` idiom
  (`test_bitmap.py:94-102`) — `skipTest` when `/dev/kvm` is not
  accessible. Every bench test that launches the guest needs it;
  pure-validation tests do not (they fail host-side, pre-launch,
  and are cheap).
- **Message comparison idiom**: substring assertions
  (`assertIn`), never whole-line equality — instar wraps errors
  as `Error: "<msg>"`, qemu prefixes `qemu-img: <msg>`; the core
  text is identical for the parity messages. Header lines ARE
  whole-line byte-comparable (stdout, no wrappers).
- **Divergence registry precedent**: `KNOWN_MAP_DIVERGENCES`
  (`test_map.py:168`) is the richest — `dict[key, (scope,
  reason)]` — and pairs with a `TestMapDivergenceRegression`
  class (`:638`) whose tests assert the divergence STILL EXISTS,
  so a silent fix fails loudly and forces a registry update.
  Bench adopts both halves.
- **Fixtures**: `qemu-img create` + `qemu-io` writes in a
  TemporaryDirectory (`test_dd.py:160-186` pattern-image idiom;
  `test_bitmap.py:158-172` create idiom). qemu-io and qemu-img
  are hard dependencies of the suite. **No Python helper exists
  for the 55AA raw trick** — the test file replicates it:
  `qemu-io -f raw -c 'write -P 0x55 510 1' -c 'write -P 0xaa
  511 1'` on a truncated file (mirror
  `scripts/create-external-data-testdata.sh:46-49`).
- **Speed**: guest launch dominates (~seconds), the request loop
  does not (`-c 100` completes in ~1 ms) — keep counts at 100,
  images ≤ 64 MiB. Budget: ~30 guest-launching tests + ~20
  launch-free validation tests parallelized 16-way is well
  within suite norms.
- **JSON tests**: `json.loads(stdout)` + key asserts
  (`test_measure.py:145` idiom).

## Mission

### 1. Module docstring: the OQ13 decisions, recorded

`tests/test_bench.py` opens with the suite's scope contract:

- Timing values are NEVER asserted — the completion line is
  checked against `^Run completed in \d+\.\d{3} seconds\.$`
  (shape), `elapsed-seconds` in JSON only for presence/type.
- **No cross-version baselines** (OQ13 resolved): the
  deterministic header depends only on arguments, so a baseline
  matrix would record only the uncomparable timing line. There
  is deliberately no `test_bench_baselines.py` and no
  `expected-outputs/bench-*` tree; parity is asserted **live**
  against the host's qemu-img instead.
- Divergences are asserted, not silenced (the map contract): a
  failing cross-validation NOT registered in
  `KNOWN_BENCH_DIVERGENCES` is a real regression; a registered
  divergence that stops diverging fails the regression class.

### 2. `KNOWN_BENCH_DIVERGENCES`

Map-style `dict[str, tuple[str, str]]` (key → (scope, reason)),
with entries — sourced from the 4c/5c captures, wording tightened
during implementation:

1. `depth-serialized` — `-d` echoed in the header for parity but
   executed serially; JSON reports `effective-depth: 1`.
   (Registry-documented; no regression test — it is a semantic
   property, verified via the JSON field test.)
2. `wrap-rule-10-0-8` — instar uses master's
   `% (image_size - bufsize)` wrap; qemu 10.0.8 `% image_size`
   can EIO near EOF (5c raw×(d) captured both live).
   Regression-testable both ways.
3. `cache-modes-refused` — `-t` ≠ writeback refused; qemu runs.
4. `aio-refused` — `-i <any>` refused; qemu runs.
5. `native-aio-refused` — `-n` refused by instar; qemu also
   fails but for its own unrelated reason (both-fail-differently).
6. `image-opts-refused` — `--image-opts` alone refused; qemu
   runs. (`--image-opts -f` mutual-exclusion is parity, not a
   divergence.)
7. `bufsize-cap-2mib` — `-s` in (2 MiB, 2147483647] refused;
   qemu runs.
8. `help-hint-names-instar` — second line of the filename-count
   error says `instar bench --help`.
9. `zero-byte-early-failure` — instar fails at chain discovery
   before any header; qemu prints the header then
   `Failed request`.
10. `write-formats-limited` — `-w` on vmdk/vhd/vhdx refused;
    qemu writes them.
11. `secure-raw-detection` — headerless raw (no MBR/known
    signature) refused as unknown format (instar-wide security
    posture); qemu benches it. (This is why the raw fixtures
    carry the 55AA signature.)

`TestBenchDivergenceRegression` re-verifies the qemu side (or
the instar refusal) for entries 2-7, 9-11 so silent drift is
caught; entries that need `-w` or KVM guard accordingly.

### 3. Test classes and their content

All in one file; a `BenchTestBase(InstarTestBase)` carries the
helpers: `run_instar_bench(*args)`, `run_qemu_bench(*args)`
(both triples, 120 s timeouts), `make_raw_mbr(dir, size)`,
`make_qcow2(...)`, `make_populated_qcow2(...)` (qemu-io
`write -P 0xbb 0 8M`), `make_overlay(...)` (backing with 0xbb),
`make_compressed_qcow2(...)` (compressible content then
`convert -c`; assert compressed clusters exist via check/map),
`sha256(path)`, `header_line(stdout)`,
`COMPLETION_RE`.

- **TestBenchHeaderParity** (KVM; ~11 tests = the 4c rows):
  identical args on identical fixtures, instar vs qemu:
  header line byte-equal, completion shape on both, exit 0/0.
  Rows: raw defaults / `-o 1k` / `-S 0` / `-s 65537` / `-d 1`;
  qcow2 plain / backing / compressed; vmdk; vhd; vhdx.
- **TestBenchValidation** (no KVM needed; ~18 tests): the
  corrected §2 table from the phase-04 plan — for each: instar
  stderr contains the pinned literal, exit 1; where parity is
  claimed, qemu is run with the same argv and its stderr must
  contain the same core text (live parity, the no-baselines
  answer). Rows: echo forms ×7, range forms ×7 (incl. the
  suffix-overflow route for `-s`/`-o`), the 2 MiB cap, the two
  cross-option rules, `--flush-interval 0` without `-w`
  accepted (exit 0), filename count (0 and 2 filenames, both
  lines), `--image-opts -f` mutual exclusion.
- **TestBenchReadBehaviour** (KVM; ~4): `-o` past EOF → header
  on stdout then `Failed request: Input/output error`, exit 1,
  both tools (raw fixture, in-bounds-free window); wrap-window
  test on a small image (the phase-1 10240-byte vector: instar
  exits 0 under the master rule — paired with the divergence
  regression asserting qemu 10.0.8 EIOs); reads on an overlay
  and compressed qcow2 return success (content path exercised).
- **TestBenchWrite** (KVM; ~10 = the 5c matrix, thinned):
  raw `-w` byte-identical to qemu twin (sha256); fresh qcow2 —
  `qemu-img compare` identical + check clean + disk growth;
  populated qcow2 — fast path, no growth; overlay — compare +
  check + the COW spot-byte via qemu-io read (`0xbb` beside the
  window); `--flush-interval 50 -d 1` → JSON `flushes-issued`
  == 2 (and the flush line present on both tools);
  `--no-drain` accepted; cluster-straddling `-s 131072 -o
  32768` on qcow2 — compare + check.
- **TestBenchWriteRefusals** (KVM; ~7): internal snapshot,
  refcount_bits=1, compressed qcow2, vmdk, vhd, vhdx, LUKS —
  refusal message substring + sha256 unchanged.
- **TestBenchJson** (KVM; ~3): full schema key-set + key order
  irrelevant but presence exact; `effective-depth == 1`;
  `write`/`pattern`/`no-drain` reflect args; `elapsed-seconds`
  is a float `> 0`; derived rates present and positive; no
  timing equality anywhere.
- **TestBenchDivergenceRegression** (per §2).

Total ≈ 55 tests, ≈ 35 guest launches (+ qemu twins, which are
near-instant).

### 4. What phase 6 does not do

No baselines (§1). No timing thresholds or perf assertions of
any kind (the perf-tracking consumer is Future work's CI job,
explicitly not this suite). No new fixtures in instar-testdata —
everything is synthesized per-test (the suite must not add push
traffic to the testdata repo for bench). No fuzz targets
(phase 7).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | high | sonnet | none | Write `tests/test_bench.py` in full per Mission §1-§3: module docstring with the OQ13 contract, `KNOWN_BENCH_DIVERGENCES` with the 11 entries, `BenchTestBase` helpers (incl. the in-file 55AA raw builder — two qemu-io writes at 510/511), and the seven test classes. Every pinned literal comes from the capture sections of the phase-01/04/05 plans — copy, don't paraphrase. Iterate with file-scoped stestr (`cd tests && ../.venv/bin/stestr run test_bench`) until 100% pass on this host; report the pass count and wall-clock. High effort for volume and for the discipline of matching capture text exactly; the design is fully specified. Commit 1. |
| 6b | medium | sonnet | none | Full-suite integration: `make test-integration` end-to-end (the whole suite, 16-way — proves bench tests are order-free and add no cross-test interference); the suite is zero-fail on healthy testdata, so ANY failure anywhere is investigated, not waved off. Record the bench-file test count, suite totals, and wall-clock delta in a `## Captured suite results (step 6b)` section here; master plan row → Complete; index update; pre-commit. Commit 2. |

## Captured suite results (step 6b)

Run on 2026-07-07 on the 16-core reference host (healthy
`../instar-testdata` at `a34ca1bf2`, git-lfs content present),
via `make test-integration` from the repo root (stestr, 16-way,
`--exclude-regex test_info_malicious`).

### Bench file (from step 6a, for reference)

- `tests/test_bench.py`: **62 tests in seven classes**
  (`TestBenchHeaderParity`, `TestBenchValidation`,
  `TestBenchReadBehaviour`, `TestBenchWrite`,
  `TestBenchWriteRefusals`, `TestBenchJson`,
  `TestBenchDivergenceRegression`), plus
  `KNOWN_BENCH_DIVERGENCES` with 11 entries.
- File-scoped run (`cd tests && ../.venv/bin/stestr run
  test_bench`): 62/62 pass in ~2.4 s wall-clock.

### Full-suite totals

| Metric | Value |
|--------|-------|
| Ran | **2476** tests (was ~2414 pre-bench; +62 = exactly the bench file) |
| Passed | **1919** |
| Failed | **0** (also 0 expected-fail, 0 unexpected-success) |
| Skipped | 557 |
| Wall-clock | **13m 0s** (`time make test-integration`, includes the instar build check) |
| stestr-reported | 775.5 s; sum of per-test execute time 7319.7 s across 16 workers |

### Order-freedom and interference

All 62 bench tests passed inside the full parallel run, scheduled
across **10 different workers** ({6}-{15}) rather than a single
partition — the file is order-free and introduced no cross-test
interference. No bench test behaved differently under full-suite
parallelism than it did file-scoped in 6a.

### Incidents

**None.** Zero failures anywhere in the suite. The known trap on
this host — convert-compressed timeouts under external parallel
load — did not occur (all `test_convert` compressed rows passed,
worst single test ~3.5 s). The 557 skips were audited and are all
pre-existing families unrelated to bench, none originating in
`tests/test_bench.py`: 201 × `oslo.utils not installed` (optional
dependency), the `KNOWN_MAP_DIVERGENCES` / scanner / writer
divergence registries, baseline rows whose oracle exits non-zero,
measure's documented monolithicFlat-source refusal, and
qemu-side resize-unsupported-format rows.
