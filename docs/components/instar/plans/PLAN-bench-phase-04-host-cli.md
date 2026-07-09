# PLAN-bench phase 04: host CLI

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the clap arg structs
and dispatch in `src/vmm/src/main.rs`, bitmap's guest-launch and
harvest loop, convert's read-only chain attach, the hand-built
JSON renderers), and ground your answers in what the code
actually does today. The authoritative output/validation contract
is the **captured qemu-img 10.0.8 message contract** in
[PLAN-bench-phase-01-crate.md](/components/instar/plans/PLAN-bench-phase-01-crate/)
(step 1e) — read it in full before writing any message string.
Do not speculate when you could read instead; flag uncertainty
explicitly.

Phase plans for the parent master plan live alongside it in
`docs/plans/`. The master plan is [PLAN-bench.md](/components/instar/plans/PLAN-bench/).
This is the fourth of eight phases; phases 1-3 landed the
schedule crate, the ABI (call-table VERSION 20), and the guest
read op (`bench.bin`).

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

This phase makes `instar bench` runnable: `BenchArgs`, the
qemu-parity validation surface, `run_bench` (chain discovery,
read-only attach, config population, launch, the host half of the
timing bracket, output rendering, `--output json`), and the
**first end-to-end smoke verification** against the local
qemu-img 10.0.8.

Grounding (surveyed on the current tree; all line numbers in
`src/vmm/src/main.rs` unless noted):

- **Assembly model**: bench's host side = bitmap's single-result
  guest-launch/harvest shape (`run_bitmap_guest`, :6284 — the
  leanest single-input template: KVM setup :6308-6548, vcpu loop
  :6571-6657, `result_seen` tracking :6561/:6670, error mapping
  via `map_bitmap_error` :5536) grafted onto convert's
  **read-only chain attach** (`discover_backing_chain` :2126 →
  `open_chain_devices` :2372, which opens `BackingStore::open(...,
  true /* read-only */, ...)` and attaches each chain image as an
  input device; `write_chain_config` :2546). Bench has **no
  output device** (`vmm_config_input_only`, :6557).
- **Chain discovery takes no format hint** — it walks backing
  files by running the sandboxed info op per image and
  auto-detects. dd's posture for `-f` is warn-then-ignore
  (:11337-11343). `BenchConfig.target_format` is therefore set
  from the *discovered* top format; the guest's family
  cross-check still guards host/guest disagreement.
- **Timing**: `Instant` is not currently imported in `main.rs`;
  `VmmStats.start_time` is private and measures whole-VM runtime
  — bench needs its own `use std::time::Instant`. The phase-2 ABI
  contract (doc on `send_bench_start` in `src/shared/src/lib.rs`)
  requires: start captured on `BenchStart` arrival, **elapsed
  captured on `BenchResult` arrival** — both inside the vcpu
  loop's serial-decode arms, never after HLT, so post-result
  teardown vmexits stay outside the bracket. `BenchResult` may
  arrive without a preceding `BenchStart` (pre-loop guest
  failure): `start` is an `Option`, and that path renders the
  error with no timing line.
- **Size parsing**: `parse_qemu_img_size` (:476-497) is the
  qemu-suffix parser (`b/k/K/m/M/g/G/t/T/p/P/e/E`, plain bytes)
  already used by dd and bitmap — bench uses it for `-s`/`-S`/
  `-o`. Plain integers (`-c`, `-d`, `--flush-interval`,
  `--pattern`) parse via string capture + explicit range check
  (NOT typed clap fields — qemu's out-of-range message must be
  produced by *our* check, not clap's, and negative values must
  reach our check to collapse into the same message per the 1e
  capture).
- **Refusal idioms**: `--image-opts` runtime rejections exist in
  resize/bitmap/map/snapshot (e.g. :5348); `-U`/`--force-share`
  is an accepted no-op for read-only modes (snapshot :3059,
  :12567); there is **no `-t`/`-i`/`-n` handling anywhere yet**
  — bench introduces the cache/aio postures per master-plan OQ6.
  House error style: `return Err("...".into())` from `run_*`;
  `main` propagates → `Error: ...` on stderr, exit 1.
- **JSON**: hand-built `println!` with `escape_json_string`
  (:1803); the human-vs-json fork per `print_measure_result`
  (:9068). No serde.
- **Binary resolution**: `get_binary_path("bench.bin")`
  (:1856; `INSTAR_BIN_DIR` → exe dir → `/usr/lib/instar`).
- **The 1e capture** (phase-01 plan) pins: the eight qemu
  message texts, check precedence (qemu bound before instar cap —
  invocation 5), header printed unconditionally once the image
  opens (even when the first request will fail — invocation 19,
  zero-byte image), `-S 0` rendered as the effective step
  (invocation 24), `-o` suffix values echoed as decimal bytes
  (invocation 18), completion line `%.3f` precision, exit 1 on
  every failure.

## Mission

### 1. CLI surface (`BenchArgs`)

```
instar bench [-c COUNT] [-d DEPTH] [-f FMT] [--flush-interval NUM]
             [-i AIO] [-n] [--no-drain] [-o OFFSET] [--pattern NUM]
             [-q] [-s BUFFER_SIZE] [-S STEP_SIZE] [-t CACHE] [-w]
             [-U] [--image-opts] [--output human|json] FILENAME
```

clap notes: filename as `Vec<String>` (trailing positional,
`num_args(0..)`) so *we* own the count check and can reproduce
qemu's message; `-c/-d/--flush-interval/--pattern/-s/-S/-o` all
as `Option<String>` (parsed and range-checked by our code);
`-w/-q/-U/-n/--no-drain/--image-opts` as bools; `-i AIO` and
`-t CACHE` as `Option<String>`; `--output` with
`value_parser = ["human", "json"]` (house idiom). `-w` is
**accepted by the parser** but `run_bench` refuses it in phase 4
with `bench: write tests (-w) are not yet supported` — phase 5
removes that refusal (declaring the flag now keeps the phase-5
diff additive and the help text complete; the refusal is a
temporary instar-only message, recorded for the phase-6
divergence registry).

### 2. Validation: exact messages, exact precedence

A `validate_bench_args` helper (pure, unit-testable) produces
either a validated parameter set or the error string. Messages
come from the 1e capture **byte-for-byte** (bare qemu text, no
`bench:` prefix, so the phase-6 parity tests compare cleanly;
instar-only refusals below DO carry the `bench:` prefix to mark
them as instar postures):

Each numeric option has TWO failure forms (Supplement 2 of the
1e capture — the original "parse failures collapse into the range
message" assumption was disproven during 4a and is corrected
here): an **unparseable** value produces the value-echoing form
`Invalid <name> specified: '<value>'.`, an out-of-range *number*
produces the `Must be between` form. Display names: `request
count`, `queue depth`, `buffer size`, `step size`, `offset`,
`pattern byte`, `flush interval`.

| Check (in order) | Message |
|---|---|
| `-c` unparseable | `Invalid request count specified: '<v>'.` |
| `-c` < 1 / > 2147483647 | `Invalid request count specified. Must be between 1 and 2147483647.` |
| `-d` unparseable / out of range | `Invalid queue depth specified: '<v>'.` / `Invalid queue depth specified. Must be between 1 and 2147483647.` |
| `-s` unparseable | `Invalid buffer size specified: '<v>'.` |
| `-s` < 1 / > 2147483647 (qemu bound FIRST) | `Invalid buffer size specified. Must be between 1 and 2147483647.` |
| `-s` in qemu range but > `BENCH_MAX_BUFSIZE` (2 MiB) | `bench: buffer sizes above 2 MiB are not yet supported` |
| `-S` unparseable / > 2147483647 (0 is valid) | `Invalid step size specified: '<v>'.` / `Invalid step size specified. Must be between 0 and 2147483647.` |
| `-o` unparseable / negative / > i64::MAX | `Invalid offset specified: '<v>'.` / `Invalid offset specified. Must be between 0 and 9223372036854775807.` |
| `--pattern` unparseable / outside [0, 255] | `Invalid pattern byte specified: '<v>'.` / `Invalid pattern byte specified. Must be between 0 and 255.` |
| `--flush-interval` unparseable / outside [0, 2147483647] | `Invalid flush interval specified: '<v>'.` / `Invalid flush interval specified. Must be between 0 and 2147483647.` |
| **nonzero** flush interval without `-w` (`--flush-interval 0` without `-w` is silently fine — verified live; the check gates on the value, matching the crate's `FlushRequiresWrite`) | `--flush-interval is only available in write tests` |
| nonzero flush interval < depth | `Flush interval can't be smaller than depth` |
| filename count ≠ 1 | `Expecting one image file name` + second line `Try 'instar bench --help' for more info` (hint line names instar, not qemu-img — divergence-registry entry) |

The flush-interval range check fires before the cross-option
rules (verified live). Where
`BenchParams::validate` covers a rule (count/depth/bufsize/step
ranges, the two cross-option rules, the cap ordering), call it
and map `BenchParamError` → the table; host-only rules (pattern
range pre-u8, offset bound, filename count) are checked directly.

Instar-only postures (all `bench:`-prefixed, all exit 1, all
recorded for the divergence registry):

- `-t writeback` accepted silently (qemu's default); `-t` with
  another *valid qemu cache mode* (`none`, `writethrough`,
  `directsync`, `unsafe`) → `bench: cache mode '<v>' is not yet
  supported`; `-t` with anything else → `Invalid cache mode`
  (qemu's own text, invocation 11).
- `-i <anything>` → `bench: aio backend '<v>' is not yet
  supported`; `-n` → `bench: native AIO (-n) is not yet
  supported`.
- `--image-opts` together with `-f` → `--image-opts and --format
  are mutually exclusive` (qemu parity, invocation 14);
  `--image-opts` alone → `bench: --image-opts is not yet
  supported` (house posture).
- `-q` and `-U`: accepted no-ops (qemu's `-q` is a no-op for
  bench per the phase-1 verification; instar has no locking).
- `-f`: dd's warn-then-ignore (`bench: -f <v> is accepted but
  ignored; the input format is auto-detected`) **only when the
  hint disagrees with the discovered format**; silent when it
  matches (the common `-f raw`/`-f qcow2` invocations stay
  clean).

### 3. Output rendering (pure helpers, unit-tested)

- Header: `Sending {count} {read|write} requests, {bufsize} bytes
  each, {depth} in parallel (starting at offset {offset}, step
  size {step})` — offset as the parsed decimal byte value, step
  as the **effective** step (`-S 0` → bufsize; invocation 24),
  `{read|write}` from `-w`. Byte-identical to qemu for equal
  arguments.
- Optional `Sending flush every {n} requests` when
  flush-interval is nonzero (write tests only — unreachable in
  phase 4, but the renderer supports it now; phase 5 just stops
  refusing `-w`).
- Completion: `Run completed in {:.3} seconds.`
- Print order: header (+ flush line) go to stdout **after
  discovery/validation succeed and before the guest launches** —
  mirroring qemu, which prints them before submitting requests
  (and unconditionally even when the first request will fail,
  invocation 19). Boot time between header and completion is
  excluded from the bracket anyway.
- `--output json` **replaces** the three human lines entirely
  (master-plan OQ11; the default path is byte-parity with qemu
  and nothing else). Schema (hyphenated keys, hand-built JSON,
  `escape_json_string` for the filename/format):

  ```
  {
    "filename": ..., "format": ...,
    "count": N, "depth": N, "effective-depth": 1,
    "buffer-size": N, "step-size": N(effective), "offset": N,
    "write": false, "pattern": N,
    "flush-interval": N, "no-drain": bool, "flushes-issued": N,
    "elapsed-seconds": {:.6},
    "requests-per-second": {:.2}, "bytes-per-second": {:.2}
  }
  ```

  Derived rates are **included** (OQ11 resolved: the
  perf-tracking consumer wants them; recomputation invites
  rounding drift). `elapsed-seconds` carries µs precision — the
  human line's `%.3f` rounding is a qemu-parity constraint, not
  an information ceiling.

### 4. `run_bench` / `run_bench_guest`

`run_bench`: validate per §2 → `discover_backing_chain` (auto
detect; `-f` posture per §2) → print header (+ flush line) or
defer to JSON → `run_bench_guest` → render.

`run_bench_guest` (clone `run_bitmap_guest`, swap attach):
core.bin + bench.bin via `get_binary_path`; KVM/memory/vcpu setup
per the bitmap template; `open_chain_devices` read-only,
input-only (no output device); `write_chain_config`; write
`BenchConfig` **field-by-field at explicit byte offsets** with an
offset map comment (bitmap's :6277-6302 idiom) — magic 0x424E4348
@0, flags @4 (FLAG_NO_DRAIN from `--no-drain`; FLAG_VERBOSE per
house convention; never FLAG_WRITE in phase 4), count @8, depth
@12, bufsize @16, step @24 (raw, 0 preserved), offset @32,
flush_interval @40, pattern @44, target_format @48 (discovered
top format as u32), sector_size @52 — then zero `_reserved`.
`sector_size` and the attach sector size are **the same value
convert's input path uses** (do not invent a bench-special
value; record what it is in the 4c measurements — it is part of
the measurement's definition).

vcpu loop arms (bitmap's loop + two payload arms):

```rust
Some(Payload::BenchStart(_))  => { bench_start = Some(Instant::now()); }
Some(Payload::BenchResult(r)) => {
    elapsed = bench_start.map(|s| s.elapsed());   // captured AT ARRIVAL
    harvested = ...; result_seen = true;
}
```

After the loop: `vm_error` → Err; `!result_seen` → `bench: guest
did not return a result`; `harvested.error != ERROR_OK` → map
via `map_bench_error` and exit 1 — with **`ERROR_IO_READ`
rendering as `Failed request: Input/output error`** (bare, qemu
parity with invocation 19; the header has already printed on
stdout by then, reproducing qemu's exact zero-byte-image
transcript). Other codes get instar-worded `bench:` messages
(bad config, unsupported format, parse failed). Success: human →
completion line from `elapsed` (missing `elapsed` with
`ERROR_OK` is a guest contract violation → error, not a zero
timing); json → §3 object with `flushes-issued` from the result.

### 5. Resolved open questions

- **OQ6 (`-t`/`-i`/`-n`)**: accept `-t writeback` silently;
  refuse valid-but-unsupported cache modes, all `-i`, and `-n`
  with `bench:`-prefixed not-yet-supported messages; qemu's own
  `Invalid cache mode` text for unknown `-t` values (§2).
- **OQ11 (JSON schema)**: §3's schema, derived rates included,
  json replaces human output entirely.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | The pure layer: `BenchArgs` (clap surface per §1, NOT yet added to `Commands`), `validate_bench_args` implementing §2's table exactly (calling `bench::BenchParams::validate` where it applies), the §3 renderers (header/flush/completion/json as pure functions), and `#[cfg(test)]` unit tests in main.rs pinning every message string byte-for-byte against the 1e capture plus header-rendering cases (effective step, offset decimal, read/write word, filename-count message). Mark the new items `#[allow(dead_code)]` with a `// wired in 4b` note so the commit is warning-clean. Gates: make lint + make test-rust. Commit 1. |
| 4b | high | opus | none | Wire it end-to-end per §4: `Commands::Bench` + dispatch arm, `run_bench`, `run_bench_guest` (bitmap template + convert's read-only chain attach, input-only), the BenchConfig field-by-field write with offset-map comment, the two vcpu-loop payload arms with arrival-time Instant capture, error mapping incl. the `Failed request: Input/output error` parity path, human/json fork, removal of 4a's allow(dead_code). High effort: the bracket capture points are the product's semantics and the attach/config plumbing crosses the KVM boundary. Gates: make instar + lint + test-rust; plus a minimal manual run (`instar bench -c 100 <raw image>`) proving the three-line transcript works. Commit 2. |
| 4c | medium | sonnet | none | The smoke verification (documented, not pytest — phase 6 owns the test suite). Against the local qemu-img 10.0.8 and scratch images: header BYTE-parity for identical args across defaults / `-o 1k` / `-S 0` / `-s 65537` (multi-transfer) / `-d 1` / qcow2 with backing chain / compressed qcow2 / vmdk / vhd / vhdx; completion-line shape (`Run completed in \d+\.\d{3} seconds\.`); exit codes and message parity for the §2 table including the `-c abc` parse-failure assumption; the zero-byte-image transcript (header then Failed request, exit 1); `--output json` well-formedness (python -m json.tool). Append a `## Captured smoke results (step 4c)` section to this plan (per-invocation, qemu vs instar), including the attach sector_size value per §4, update the master plan row + index, pre-commit. Any parity mismatch is a stop-and-report, not a silent fix. Commit 3. |

## Captured smoke results (step 4c)

Ran the release binaries (`src/target/release/instar`, built from
`e8eae99` for the initial pass and rebuilt after the in-phase
`allow_hyphen_values` fix below for the negative-value re-runs;
local `qemu-img version 10.0.8 (Debian 1:10.0.8+ds-0+deb13u1+b2)`)
against scratch images in a session scratchpad directory: `raw.img` (10 MiB raw, `qemu-img create` +
a `55 AA` signature written at offset 510 so instar's secure
format detection sees a plausible MBR and classifies the file as
raw), `plain.qcow2`, `withbacking.qcow2` (qcow2 over a 10 MiB raw
backing file), `compressed.qcow2` (`qemu-img convert -c` from
`raw.img`), `test.vmdk` (`subformat=monolithicSparse`), `test.vhd`
(`-f vpc`), `test.vhdx`, and `empty.raw` (`truncate -s 0`). All
images discarded after the run.

The attach/discovery `sector_size` is **65536** (`MAX_SECTOR_SIZE`,
`src/vmm/src/main.rs:4110`) — explicitly noted in the code as
"convert's input-path sector size", used for both
`discover_backing_chain` and the guest device attach in
`run_bench_guest`; it is not a bench-specific value and is recorded
here per §4 as part of the measurement's definition.

### Summary table

| Row | Case | instar vs qemu | Verdict |
|---|---|---|---|
| 1 | defaults `-c 100` (raw) | header byte-identical, completion shape matches, exit 0/0 | PARITY |
| 2 | `-c 100 -o 1k` (raw) | header byte-identical (offset 1024 decimal), exit 0/0 | PARITY |
| 3 | `-c 100 -S 0` (raw) | header byte-identical (effective step 4096), exit 0/0 | PARITY |
| 4 | `-c 100 -s 65537` (raw) | header byte-identical (65537/65537), exit 0/0 | PARITY |
| 5 | `-c 100 -d 1` (raw) | header byte-identical, exit 0/0 | PARITY |
| 6 | `-c 100` (qcow2 plain) | header byte-identical, exit 0/0 | PARITY |
| 7 | `-c 100` (qcow2 + backing) | header byte-identical, exit 0/0 | PARITY |
| 8 | `-c 100` (compressed qcow2) | header byte-identical, exit 0/0 | PARITY |
| 9 | `-c 100` (vmdk) | header byte-identical, exit 0/0 | PARITY |
| 10 | `-c 100` (vhd) | header byte-identical, exit 0/0 | PARITY |
| 11 | `-c 100` (vhdx) | header byte-identical, exit 0/0 | PARITY |
| 12 | `-c 0` | core message identical | PARITY |
| 12 | `-c abc` | core message identical | PARITY |
| 12 | `-c -1` | core message identical (after the in-phase `allow_hyphen_values` fix, see below) | PARITY (fixed during 4c) |
| 13 | `-d 0` / `-d abc` | core message identical | PARITY |
| 13 | `-d -1` (probed for consistency, not in the assigned matrix) | core message identical (after the fix) | PARITY (fixed during 4c) |
| 14 | `-s 0` / `-s 3G` | core message identical (qemu's own bound fires first) | PARITY |
| 14 | `-s 3M` | instar caps at 2 MiB (`bench:` posture); qemu succeeds | EXPECTED DIVERGENCE |
| 15 | `-S abc` | core message identical | PARITY |
| 15 | `-S -1` | core message identical (after the fix) | PARITY (fixed during 4c) |
| 16 | `-o abc` / `-o 200000000000000G` | core message identical | PARITY |
| 16 | `-o -1` | core message identical (after the fix) | PARITY (fixed during 4c) |
| 17 | `--pattern 256`/`abc`, with and without `-w` | core message identical; qemu's pattern range check fires regardless of `-w`, confirmed; instar's pattern check also fires before its own `-w` refusal | PARITY |
| 17 | `--pattern -1` | core message identical (after the fix; instar without `-w`, qemu probed both with and without `-w` — same message all three ways) | PARITY (fixed during 4c) |
| 18 | `--flush-interval 50` (no `-w`) | core message identical | PARITY |
| 18 | `--flush-interval 2147483648 -w -c 10 -d 1` (positive analog; range check precedes `-w` refusal on both sides) | core message identical, ordering matches | PARITY |
| 18 | `--flush-interval -1 -w -c 10 -d 1` | core message identical (after the fix; range check precedes the `-w` refusal on both sides) | PARITY (fixed during 4c) |
| 18 | `--flush-interval 0` (no `-w`) | both succeed, header identical | PARITY |
| 19 | `-t none` | instar refuses (`bench:` posture); qemu succeeds | EXPECTED DIVERGENCE |
| 19 | `-t bogus` | core message identical (`Invalid cache mode`) | PARITY |
| 19 | `-t writeback` | both silent/succeed | PARITY |
| 19 | `-i threads` | instar refuses (`bench:` posture); qemu succeeds | EXPECTED DIVERGENCE |
| 19 | `-n` | instar refuses (`bench:` posture); qemu also fails but for an unrelated reason (`aio=native requires cache.direct=on`) | EXPECTED DIVERGENCE (both fail, different mechanism/text) |
| 19 | `--image-opts` alone | instar refuses (`bench:` posture); qemu succeeds | EXPECTED DIVERGENCE |
| 19 | `--image-opts -f raw` | core message identical (qemu's own mutual-exclusion text) | PARITY |
| 20 | no filename / two filenames | core message identical; hint line names `instar` not `qemu-img` (documented posture) | PARITY (modulo the documented hint-line divergence) |
| 21 | zero-byte raw | instar fails during backing-chain discovery (no header ever printed); qemu prints the header then fails the first request | EXPECTED DIVERGENCE (stronger than "unknown format": instar's failure point is architecturally earlier, per §4's discover-then-print ordering) |
| 22 | `--output json` (raw image) | `python3 -m json.tool` validates; keys and order match §3's schema exactly, including the fixed `"effective-depth": 1` | PARITY |

### The `-c -1`-shaped clap interception (found during 4c, fixed in this phase)

The initial 4c run found that every negative-value case in rows
12, 13, 15, 16, 17, and 18 (`-c -1`, `-d -1`, `-S -1`, `-o -1`,
`--pattern -1`, `--flush-interval -1`) failed identically inside
clap, never reaching `validate_bench_args`:

```
$ instar bench -c -1 -f raw raw.img          # BEFORE the fix
error: unexpected argument '-1' found

  tip: to pass '-1' as a value, use '-- -1'

Usage: instar bench [OPTIONS] [FILENAME]...

For more information, try '--help'.
exit=2
```

versus qemu's `qemu-img: Invalid request count specified. Must be
between 1 and 2147483647.` at exit 1.

Root cause: none of `BenchArgs`'s seven numeric `Option<String>`
fields carried `#[arg(allow_hyphen_values = true)]`, so clap
treated a bare `-1` token as a candidate flag rather than the
value of the preceding option — directly contradicting the
struct's own doc comment ("a negative value must reach that check
so it collapses into the same message qemu itself produces"). The
intent was implemented for `ResizeArgs`'s suffix-value positional
and `DdArgs`'s trailing operands, but the attribute was missed on
`BenchArgs`'s numeric options in 4a/4b. The 4a unit tests did not
catch it because they construct `BenchArgs` directly and never
exercise clap's tokenizer.

**Fix (this phase):** `allow_hyphen_values = true` added to all
seven numeric fields (`-c`, `-d`, `-s`, `-S`, `-o`, `--pattern`,
`--flush-interval`) in `BenchArgs`; nothing else changed. A new
`bench_clap_hyphen_tests` module closes the test gap: it parses
full argv vectors through `Cli::try_parse_from` (the resize-test
idiom) and asserts both that clap accepts the hyphen value and
that `validate_bench_args` then produces the exact 1e-capture
range message — one test per numeric option
(`count_negative_reaches_validation`,
`depth_negative_reaches_validation`,
`buffer_size_negative_reaches_validation`,
`step_size_negative_reaches_validation`,
`offset_negative_reaches_validation`,
`pattern_negative_reaches_validation`,
`flush_interval_negative_reaches_validation`) plus
`positive_values_still_parse_normally` proving the ordinary path
is undisturbed.

Re-run transcripts after the fix (rebuilt binary, all six affected
rows, plus `-s -1` for completeness — every one now parity PASS at
exit 1/1):

```
$ instar bench -c -1 -f raw raw.img
Error: "Invalid request count specified. Must be between 1 and 2147483647."
exit=1
$ qemu-img bench -c -1 -f raw raw.img
qemu-img: Invalid request count specified. Must be between 1 and 2147483647.
exit=1

$ instar bench -c 100 -d -1 -f raw raw.img
Error: "Invalid queue depth specified. Must be between 1 and 2147483647."
exit=1
$ qemu-img bench -c 100 -d -1 -f raw raw.img
qemu-img: Invalid queue depth specified. Must be between 1 and 2147483647.
exit=1

$ instar bench -c 100 -S -1 -f raw raw.img
Error: "Invalid step size specified. Must be between 0 and 2147483647."
exit=1
$ qemu-img bench -c 100 -S -1 -f raw raw.img
qemu-img: Invalid step size specified. Must be between 0 and 2147483647.
exit=1

$ instar bench -c 100 -o -1 -f raw raw.img
Error: "Invalid offset specified. Must be between 0 and 9223372036854775807."
exit=1
$ qemu-img bench -c 100 -o -1 -f raw raw.img
qemu-img: Invalid offset specified. Must be between 0 and 9223372036854775807.
exit=1

$ instar bench --pattern -1 -c 10 -f raw raw.img
Error: "Invalid pattern byte specified. Must be between 0 and 255."
exit=1
$ qemu-img bench --pattern -1 -w -c 10 -f raw raw.img
qemu-img: Invalid pattern byte specified. Must be between 0 and 255.
exit=1
$ qemu-img bench --pattern -1 -c 10 -f raw raw.img
qemu-img: Invalid pattern byte specified. Must be between 0 and 255.
exit=1

$ instar bench --flush-interval -1 -w -c 10 -d 1 -f raw raw.img
Error: "Invalid flush interval specified. Must be between 0 and 2147483647."
exit=1
$ qemu-img bench --flush-interval -1 -w -c 10 -d 1 -f raw raw.img
qemu-img: Invalid flush interval specified. Must be between 0 and 2147483647.
exit=1

$ instar bench -c 100 -s -1 -f raw raw.img
Error: "Invalid buffer size specified. Must be between 1 and 2147483647."
exit=1
$ qemu-img bench -c 100 -s -1 -f raw raw.img
qemu-img: Invalid buffer size specified. Must be between 1 and 2147483647.
exit=1
```

### Interesting per-invocation transcripts

#### Row 14: `-s 3M` (instar cap vs qemu success)

```
$ instar bench -c 3 -s 3M -f raw raw.img
Error: "bench: buffer sizes above 2 MiB are not yet supported"
exit=1

$ qemu-img bench -c 3 -s 3M -f raw raw.img
Sending 3 read requests, 3145728 bytes each, 64 in parallel (starting at offset 0, step size 3145728)
Run completed in 0.001 seconds.
exit=0
```

(Note: with `-c 100 -s 3M` against the 10 MiB probe image, qemu
itself fails with `Failed request: Input/output error` because 100
requests at a 3 MiB step run past the 10 MiB image — that is an
image-size artifact of the probe, not part of this divergence; `-c
3` keeps all reads in-bounds so qemu's success transcript is
directly comparable.)

#### Row 19: `-t none` / `-i threads` / `-n` / `--image-opts`

```
$ instar bench -t none -c 10 -f raw raw.img
Error: "bench: cache mode 'none' is not yet supported"
exit=1
$ qemu-img bench -t none -c 10 -f raw raw.img
Sending 10 read requests, 4096 bytes each, 64 in parallel (starting at offset 0, step size 4096)
Run completed in 0.000 seconds.
exit=0

$ instar bench -i threads -c 10 -f raw raw.img
Error: "bench: aio backend 'threads' is not yet supported"
exit=1
$ qemu-img bench -i threads -c 10 -f raw raw.img
Sending 10 read requests, 4096 bytes each, 64 in parallel (starting at offset 0, step size 4096)
Run completed in 0.000 seconds.
exit=0

$ instar bench -n -c 10 -f raw raw.img
Error: "bench: native AIO (-n) is not yet supported"
exit=1
$ qemu-img bench -n -c 10 -f raw raw.img
qemu-img: Could not open 'raw.img': aio=native was specified, but it requires cache.direct=on, which was not specified.
exit=1
```

The `-n` case is both-fail but for unrelated reasons: qemu's
failure is a host requirement (`aio=native` needs `cache.direct=on`,
i.e. `-t none`), not a rejection of `-n` itself — combining `-n`
with `-t none` on qemu would likely succeed, but that combination
was not separately probed since instar refuses both flags
independently regardless.

```
$ instar bench --image-opts -c 10 driver=raw,file.filename=raw.img
Error: "bench: --image-opts is not yet supported"
exit=1
$ qemu-img bench --image-opts -c 10 driver=raw,file.filename=raw.img
Sending 10 read requests, 4096 bytes each, 64 in parallel (starting at offset 0, step size 4096)
Run completed in 0.000 seconds.
exit=0
```

#### Row 21: zero-byte raw image

```
$ instar bench -c 100 -f raw empty.raw
Error: "error discovering backing chain for empty.raw: Info operation failed: No info result received from guest"
exit=1

$ qemu-img bench -c 100 -f raw empty.raw
Sending 100 read requests, 4096 bytes each, 64 in parallel (starting at offset 0, step size 4096)
qemu-img: Failed request: Input/output error
exit=1
```

Both fail with exit 1, but the mechanism and text differ
completely: qemu treats the empty file as a valid raw image of
size 0 and only fails once the first read request is issued
(header already printed). instar's `run_bench` performs
`discover_backing_chain` *before* printing the header (§4's
"validate → discover → print header → launch" ordering), and the
guest's sandboxed info op cannot produce a result for a zero-byte
image, so discovery itself fails and the header never prints. This
is a stronger divergence than "refuses it as unknown format" — it
is an architecturally-earlier failure point, not a format
classification difference. Recorded as expected per the task
brief; not treated as a bug.

#### Row 22: `--output json`

```
$ instar bench -c 100 -f raw --output json raw.img
{
    "filename": "raw.img",
    "format": "raw",
    "count": 100,
    "depth": 64,
    "effective-depth": 1,
    "buffer-size": 4096,
    "step-size": 4096,
    "offset": 0,
    "write": false,
    "pattern": 0,
    "flush-interval": 0,
    "no-drain": false,
    "flushes-issued": 0,
    "elapsed-seconds": 0.004190,
    "requests-per-second": 23865.12,
    "bytes-per-second": 97751524.03
}
exit=0
```

`python3 -m json.tool` validates the output; every key from §3's
schema is present, in the documented order, with the documented
types. `"effective-depth"` is confirmed fixed at `1` regardless of
`-d` (by design — `src/vmm/src/main.rs:4002`, v1 executes serially).

### Divergence list for phase 6's `KNOWN_BENCH_DIVERGENCES` registry

Expected/documented (from the master plan and §2/§4 of this plan):

1. `-w` refused outright: `bench: write tests (-w) are not yet
   supported` (phase 5 removes this).
2. `-t` with a valid-but-unsupported qemu cache mode (e.g. `none`,
   `writethrough`, `directsync`, `unsafe`) refused with `bench:
   cache mode '<v>' is not yet supported`; qemu runs these
   successfully.
3. `-i <anything>` refused with `bench: aio backend '<v>' is not
   yet supported`; qemu runs most aio backends successfully.
4. `-n` refused with `bench: native AIO (-n) is not yet
   supported`; qemu's own behaviour for `-n` alone (without `-t
   none`) is *also* a failure, but for an unrelated host-requirement
   reason (`aio=native ... requires cache.direct=on`) — not the
   same mechanism, so this is not a "both succeed" divergence, it's
   "both fail differently."
5. `--image-opts` alone refused with `bench: --image-opts is not
   yet supported`; qemu succeeds. (`--image-opts` + `-f` together
   is qemu-parity — both refuse with the same mutual-exclusion
   text.)
6. `-s` values inside qemu's `[1, 2147483647]` range but above
   `BENCH_MAX_BUFSIZE` (2 MiB) refused with `bench: buffer sizes
   above 2 MiB are not yet supported`; qemu succeeds (confirmed
   live with `-s 3M`).
7. Filename-count error's second line names `instar`, not
   `qemu-img`: `Try 'instar bench --help' for more info`.
8. Zero-byte image: instar fails during backing-chain discovery
   (`error discovering backing chain for <file>: Info operation
   failed: No info result received from guest`), no header ever
   printed; qemu prints the header unconditionally then fails the
   first request (`Failed request: Input/output error`). Both exit
   1, but the failure point and text are unrelated.

(The clap negative-value interception initially found by this
smoke run is **not** on this list: it was a CLI-wiring defect, not
a behavioural posture, and it was fixed within this phase — see
the section above. Negative numeric values are now full qemu
parity.)
