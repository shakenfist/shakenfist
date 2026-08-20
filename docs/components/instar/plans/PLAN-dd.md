# `instar dd` subcommand

## Status: Complete

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, LUKS, KVM, virtio,
disk image formats), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the overall system structure
(host VMM, KVM guest, call table, device emulation).
Consult `AGENTS.md` for build commands, project conventions,
code organisation, and the security model summary. Consult
`docs/` for format-specific documentation (`docs/qcow2/`,
`docs/raw/`, etc.) and `docs/commentary/` for architectural
decisions and design rationale.

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases is done
via the table in the Execution section below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

instar implements 13 of qemu-img's 15 subcommands. The three
absent commands — `bench`, `bitmap`, and `dd` — are all intended
to be in scope. They share no command-specific machinery with one
another (`dd` is a data-copy op like `copy`/`convert`; `bitmap` is
a qcow2 metadata mutator like `amend`; `bench` is a performance
harness with an open design question about whether benchmarking
*through* the KVM sandbox is meaningful), so they are being
planned as three independent efforts rather than one combined
plan. This is the first of the three: **`dd`**.

`dd` is the natural next build because it is, semantically,
**`convert` with a windowed input**: it reads an input image
(format auto-probed, or forced with `-f`), writes an output image
in a target format (`-O`, default `raw`), and restricts the copy
to a byte window controlled by the dd-style operands `bs=`,
`count=`, and `skip=`. The format writers it needs already exist
inside the `convert` guest binary, and the host-side
config-build / guest-launch lifecycle is already established by
`run_convert`. The new work is therefore concentrated in three
places: an unusual `name=value` operand parser on the host, an
input-windowing + dense-copy path in the guest, and faithful
replication of upstream qemu-img dd's quirks.

## Mission and problem statement

Ship an `instar dd` subcommand that is behaviourally compatible
with **upstream** `qemu-img dd` (qemu.git, verified against
`stable-9.1`/`stable-9.2`/`master`, where the `img_dd` logic is
byte-identical). Concretely:

1. **Operands.** Support exactly the five upstream `name=value`
   operands — `bs=`, `count=`, `skip=`, `if=`, `of=` — and the
   upstream dash-options `-f FMT` (input format) and
   `-O OUTPUT_FMT` (output format). `if=` and `of=` are both
   mandatory; their absence is an error with non-zero exit.
2. **Block / window semantics.**
   - `bs` defaults to **512**, accepts dd-style size suffixes
     (`k/M/G/T/P/E`, 1024-based via `qemu_strtosz`), and is
     range-checked to `1..=INT_MAX`. `bs=0` is rejected (it must
     **not** silently default to 512).
   - `count` selects a number of `bs`-sized input blocks and
     **only ever shrinks** the copy: the copy length is
     `min(input_len, count*bs)`. `count` beyond EOF yields the
     whole input, not a padded/larger output. `count=0` yields a
     0-byte output.
   - `skip` skips `skip*bs` bytes on the **input** before reading;
     the output always starts at byte 0. The output virtual size
     is `copy_len - skip*bs`. `skip` beyond EOF is **not fatal**:
     it warns, creates an **empty** output, and still exits 0.
   - Ordering matters: `count` clamps the size first, **then**
     `skip` subtracts. The skip-beyond-EOF check uses the
     already-count-clamped size.
3. **Output format.** `-O` defaults to **`raw`** (not the input
   format). The copy is **dense** — every block is read and
   written, with no zero/hole detection (unlike `convert`, which
   skips zero clusters by default). Output formats should match
   the set `convert` already emits (raw, qcow2, vmdk, vhd, vhdx),
   created with **default driver options only** — dd sets just the
   image size; no `-o`, compression, backing file, preallocation,
   cluster-size, or encryption.
4. **Last-block handling.** When `bs` does not divide the copy
   length, the final block is short (`copy_len - in_pos`), and the
   output virtual size reflects the exact byte count (subject to
   each output driver's own rounding, e.g. qcow2 to 512).
5. **Verification.** Cross-validate against the real `qemu-img dd`
   binary across the full edge-case matrix (see Design overview),
   with cross-version baselines, coverage-guided fuzzing of the
   operand parser and window math, and differential fuzzing of
   random `bs`/`count`/`skip`/`-O` invocations.
6. **Documentation.** `docs/dd.md`, plus the standard
   `README.md` / `ARCHITECTURE.md` / `AGENTS.md` / `CHANGELOG.md`
   updates and the `docs/plans/index.md` status flip on
   completion.

### Explicitly out of scope (v1)

These are **downstream (Proxmox/PVE) or `convert`-family**
extensions that upstream `qemu-img dd` does **not** have. They are
recorded here so the boundary is unambiguous and deferred to
Future work:

- `osize=`, `isize=` (PVE).
- `seek=` (output skip — no such operand upstream).
- stdin/stdout streaming (`if=-` / `of=-`).
- `-n` (skip target creation), `-l`/`--snapshot`,
  `--target-image-opts`, `-S`, `-c`/compression, `-p`/progress,
  `-W`, `-m`, `-r`/rate-limit, `-B`/`-b` backing,
  `-o preallocation=` and any other `-o` create options.
- `--image-opts` / `--object` / `-U` — pending the operator's
  answer to Open question 4.

## Open questions

These need an operator decision before detailed phase planning.
Each has a recommended default.

**Resolved (2026-06-21):** OQ1 → **(a) reuse/extend `convert.bin`**
(no new guest binary or config magic; `dd` builds an extended
`ConvertConfig` with an input byte-window + dense output). OQ2 →
**full convert format set** (raw + qcow2 + vmdk + vhd + vhdx),
phased raw-first (ph3) then the rest (ph4). OQ3 → **instar-native
error text, matching exit codes**. OQ4 → **defer**
`--image-opts`/`--object`/`-U` to Future work. OQ5 → **replicate**
the count-shrinks-only and skip-past-EOF quirks. The original
option text is retained below for the rationale.

1. **Guest-binary strategy (the load-bearing decision).** dd
   needs `convert`'s format writers (`convert_to_qcow2`,
   `convert_to_vmdk`, `convert_to_vhd`, `convert_to_vhdx`,
   `convert_to_raw`), which today live *inside*
   `src/operations/convert/src/main.rs`, not in a reusable crate.
   Three options:
   - **(a) Reuse/extend the `convert` guest binary** — teach the
     convert op an input-window (`skip`/`count`/`bs`) and a
     dense-output flag, and make `dd` a host-side frontend that
     builds a convert-style config. One copy of the writers; no
     384KB duplication risk; matches "dd is convert-with-a-window".
     Risk: dd's quirks (default-raw, dense, `size - skip*bs`) leak
     into convert's config surface.
   - **(b) Dedicated `dd.bin` sharing a writer module** — extract
     the writers into a `#[path]`-included module (or a `no_std`
     crate) linked by both `convert.bin` and `dd.bin`. Cleanest
     separation; meaningful refactor of a large, hot file; new
     binary must stay under the 384KB cap.
   - **(c) Standalone `dd.bin` duplicating only the needed
     writers** — fastest to a working raw-only dd, but duplicates
     format logic and risks the size cap once qcow2/vmdk/vhd/vhdx
     are added.
   **Recommended: (a)** for the format paths, with raw handled by
   a compact dedicated path (see Open question 2). Revisit (b) if
   the convert config surface gets unwieldy.
2. **v1 output-format scope.** Match `convert`'s full set
   (raw + qcow2 + vmdk + vhd + vhdx) immediately, or ship
   **raw first** (reusing `copy`'s dense sector loop with byte
   windowing, no format writers at all) and add the format paths
   in a follow-on phase? **Recommended: raw first (phase 3), then
   the full convert set (phase 4)** — raw is the `-O` default and
   the common case, and it de-risks the windowing math before
   touching the writers.
3. **Error-message fidelity.** Replicate qemu-img's exact stderr
   strings (e.g. *"Must specify both input and output files"*,
   *"unrecognized operand"*), or use instar-native messages while
   matching **semantics, output sizes, and exit codes**? instar
   elsewhere matches qemu-img semantics but emits its own error
   text/codes. **Recommended: instar-native messages, matching
   exit codes and observable sizes** — tests assert on
   sizes/exit-status/byte-content, not stderr text.
4. **`--image-opts` / `--object` / `-U` scope.** Upstream dd
   accepts these. instar's guest model opens the input read-only
   and has no general blockdev-options / object-secret surface, so
   `--image-opts` and `--object` likely have no meaning here.
   `-U` (force-share) may be a near-no-op given the guest opens a
   private snapshot of the file. **Recommended: defer all three to
   Future work; accept-and-ignore `-U` only if trivial.**
5. **Quirk replication.** Replicate the count-only-shrinks rule
   and the skip-beyond-EOF "empty output, exit 0" behaviour
   exactly? **Recommended: yes** — both are cheap, observable, and
   are the whole point of dd compatibility.

## Design overview

### Architectural shape

dd is a thin windowing layer over the existing convert data path:

```
host: parse `name=value` operands + dash-opts  ->  DdConfig
      (bs, count, skip, output_format, flags, if/of paths)
            |
            v
guest: read input window [skip*bs, skip*bs + copy_len)
       copy_len = min(input_len, count*bs)   (count clamps down only)
       out_vsize = copy_len - skip*bs        (>=0; empty if skip past EOF)
       write DENSE in target format (default raw)
```

### Operand model (host)

The `name=value` operands are alien to the clap flag model the
other subcommands use. The parser must:
- split argv tokens on the first `=`; a token without `=` or an
  unknown key is an "unrecognized operand" error;
- parse `bs`/`count`/`skip` values through the same size-suffix
  arithmetic as qemu (`qemu_strtosz`: 1024-based `k/M/G/T/P/E`);
- enforce `bs ∈ 1..=INT_MAX` (reject 0), `count`/`skip ∈ 0..`;
- require both `if=` and `of=`;
- interleave freely with the dash-options `-f`/`-O` (and any of
  `-U`/`--image-opts`/`--object` admitted by Open question 4).

`-f`/`-O` reuse the format-name resolution that `ConvertArgs`
already uses; `-O` defaults to `raw`.

### Guest windowing + dense copy

The raw path generalises `copy`'s sector loop to a byte window and
removes zero-skipping. The format paths feed the same windowed,
dense byte stream into the convert writers, with the output
virtual size fixed at `copy_len - skip*bs` (computed host-side and
carried in `DdConfig`). The guest must honour the four boundary
behaviours: count-clamp-only, skip-subtracts-after-count, short
final block, and skip-past-EOF ⇒ empty-but-successful.

### Cross-validation matrix (drives phase 6/7/9)

Each row is validated against the real `qemu-img dd`:

1. `bs` not a divisor of size — short final block.
2. `count` beyond EOF — output equals whole input.
3. `count` smaller than image — output `= count*bs`.
4. `count=0` — empty output.
5. `skip` within image — output `= size - skip*bs`, shifted copy.
6. `skip` beyond EOF — empty output, exit 0, warning emitted.
7. `skip` + `count` together — count clamps, then skip subtracts.
8. `bs=0` — rejected, non-zero exit.
9. missing `if=` or `of=` — rejected, non-zero exit.
10. no `-O` — output is **raw**, not the input format.
11. `-O qcow2` with no `-o` — plain default-option qcow2 of size
    `size - skip*bs`.
12. unknown operand / unknown `-O` format — rejected.
13. size suffixes (`bs=1M count=4` ⇒ 4 MiB) — 1024-based.
14. dense output — zero regions written explicitly (qcow2 output
    is less compact than `convert` would make it).

## Execution

Detailed phase plans live in sibling `PLAN-dd-phase-NN-*.md`
files (not yet written; created during detailed planning). The
phase plan for each phase should be authored at the effort level
noted in the table. **Open questions 1–3 must be resolved before
phase 1 is planned**, since they shape the ABI and the guest
strategy.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Shared ABI: extend `ConvertConfig` with the input byte-window (`window_start`/`window_end`) + `FLAG_DD_WINDOW` (in `src/shared/src/lib.rs`, mirrored in `src/vmm/src/main.rs`); register the `Dd` subcommand variant + `run_dd` stub. Reuses `convert.bin` per Open question 1 — no new guest binary or config magic | [PLAN-dd-phase-01-abi.md](/components/instar/plans/PLAN-dd-phase-01-abi/) | Complete (7eadd12) |
| 2. Host operand parser + `run_dd`: `name=value` operands (`bs`/`count`/`skip`/`if`/`of`, reusing `parse_qemu_img_size`) + `-f`/`-O`, validation/exit-code rules, pure `compute_dd_window` math (count-clamps-down, skip-subtracts, empty-on-overrun) → extended `ConvertConfig` (window fields + `FLAG_DD_WINDOW`, dense, `out_vsize` output sizing). Extracts a shared `execute_convert` core so `run_dd` reuses convert's launch path instead of duplicating it. Whole-image raw proven end-to-end; windowed cases land in phase 3 | [PLAN-dd-phase-02-host-operands.md](/components/instar/plans/PLAN-dd-phase-02-host-operands/) | Complete (8909837, 8b55c6c, 1a2cb91) |
| 3. Guest op — raw output: make `convert_to_raw` honour `FLAG_DD_WINDOW` (loop `[window_start, window_end)`, carry-scheme output addressing), add `has_dd_window()`, and size dd raw output to `round_up(out_vsize, 512)`. Fixed `read_raw_sectors`/`read_cluster_sectors` at the root to be byte-accurate for sub-sector windows (grounding wrongly assumed they already were), so any `bs` works. Windowed `dd -O raw` is byte- AND size-identical to `qemu-img dd` (aligned/unaligned skip, short final block, sub-sector bs, empty); matrix cross-validated. Non-raw formats deferred to phase 4 | [PLAN-dd-phase-03-guest-raw.md](/components/instar/plans/PLAN-dd-phase-03-guest-raw/) | Complete (4375324, 814197e) |
| 4. Guest op — format output: window all six structured writers (qcow2[/compressed], vmdk[/compressed], vhd, vhdx) — offset reads by `window_start`, size geometry from `out_vsize` — mirroring phase 3. The careful part is matching qemu-img dd's per-format declared virtual size (qcow2/vmdk/vhdx → `round_up(out_vsize,512)`; vhd → CHS rounding, larger). Validated by round-trip-to-raw vs `qemu-img dd -O <fmt>` (structured files aren't byte-comparable), incl. non-512-end, sub-sector, and empty windows | [PLAN-dd-phase-04-guest-formats.md](/components/instar/plans/PLAN-dd-phase-04-guest-formats/) | Complete (d10e5b1, c3cd396) |
| 5. Rust unit tests: fill the unit gaps from phases 1–4 (operand parsing/window math already covered in phase 2). Highest value: `vhd::chs_rounded_size` against qemu-verified pairs + CHS self-consistency, and the byte-accurate `read_raw_sectors`/`read_cluster_sectors` via a mock call table (regression net for the phase-3 root-cause fix). Plus `parse_output_format`/`compute_output_capacity` | [PLAN-dd-phase-05-rust-tests.md](/components/instar/plans/PLAN-dd-phase-05-rust-tests/) | Complete (44faa76) |
| 6. Integration consolidation: fill the non-windowing matrix rows the per-phase tests skipped (windowing already covered by `TestDdRawWindow`/`TestDdStructuredWindow`) — CLI rejection parity (`bs=0`, bad operand, missing `if`/`of`, unknown `-O`; both tools exit non-zero), the `-O`-defaults-to-raw quirk (assert output format is raw, not the input's), and input-format coverage (vmdk/vhd/vhdx + backing-chain inputs vs `qemu-img dd`) | [PLAN-dd-phase-06-integration.md](/components/instar/plans/PLAN-dd-phase-06-integration/) | Complete (b6cffd2) |
| 7. Cross-version baselines: add `dd` to the testdata `generate-baselines.py` + `baselines-dd` Makefile target exactly like `resize`/`amend` (create fixture → `qemu-img dd` → `qemu-img info`), curated `DD_CASES` targeting the output virtual-size rounding (512 / CHS) + empty windows; consuming test compares instar dd's result info to the qemu baseline per profile | [PLAN-dd-phase-07-baselines.md](/components/instar/plans/PLAN-dd-phase-07-baselines/) | Complete (6ebe645; testdata push pending) |
| 8. Coverage-guided fuzzing: extract `compute_dd_window` into a new `crates/dd` library crate (so it's fuzzable like sibling planners), then add `fuzz_dd_window`, `fuzz_chs_rounded_size`, and `fuzz_dd_read` (read primitives via mock CallTable) to `src/fuzz` + the `coverage-fuzz.yml` target list. `fuzz_dd_operands` intentionally omitted (CLI parsing isn't fuzzed for any command; covered by phase-5 unit tests) | [PLAN-dd-phase-08-fuzz.md](/components/instar/plans/PLAN-dd-phase-08-fuzz/) | Complete (1ddb0c5, 46f8a71) |
| 9. Differential fuzzing: add `op_dd` to `scripts/differential-fuzz.py` (modeled on `op_convert`) — random `bs`/`count`/`skip`/`-O` vs `qemu-img dd`, content-compared by flattening both outputs to raw. Resolves the `count=0 -O vmdk`/`-O vhdx` empty-window cases as documented known divergences; run a ≥2000-iteration campaign clean | [PLAN-dd-phase-09-diff-fuzz.md](/components/instar/plans/PLAN-dd-phase-09-diff-fuzz/) | Complete (f62d7d9; fixes 779e7a7, b80c5d7) |
| 10. Documentation: new `docs/dd.md` (mirroring `docs/amend.md`: synopsis, window semantics, per-format size rounding incl. VHD CHS, the known divergences, examples) + `docs/index.md` link; add `dd` to the operations enumerations in `README.md`/`ARCHITECTURE.md`/`AGENTS.md` + a README usage section; `CHANGELOG.md` (dd Added + the two phase-9 Fixed entries); `ARCHITECTURE.md` `dd-info-json` baselines note; flip `index.md`/master plan to Complete | [PLAN-dd-phase-10-docs.md](/components/instar/plans/PLAN-dd-phase-10-docs/) | Complete |

### Recommended planning effort per phase

- **High effort:** phases 1 (ABI + guest strategy), 2 (operand
  parser + quirk fidelity), 3 and 4 (guest windowing + writer
  reuse — these span the input reader, the convert writers, the
  call table, and the host glue simultaneously, so they need the
  larger context window regardless of reasoning difficulty).
- **Medium effort:** phases 5–10 (well-bounded once the matrix in
  the Design overview is settled), with phase 6's helper design
  worth extra care.

## Agent guidance

This plan follows the standard instar execution model defined in
`PLAN-TEMPLATE.md` (§ Agent guidance): **all implementation work
is done by sub-agents**, never in the management session; the
management session plans, spawns one sub-agent per step, reviews
the actual files (not the agent's summary), fixes/retries, and
commits. Use `isolation: "worktree"` for risky steps.

Each phase plan must include the standard step table:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | What to do, which files, patterns to follow |
| 1b   | high   | opus   | worktree | Why high effort: needs X to do Y |
```

Briefs must front-load the research already captured in this
master plan (the qemu-img dd semantics, the `CopyConfig`/`CallTable`
ABI, the convert writer locations, the 384KB guest-binary cap, the
`no_std` requirement of guest/format code) so the recommended
model can succeed without re-deriving it. **When in doubt, skew to
the more capable model** — see `PLAN-TEMPLATE.md` for the full
effort/model rationale and the management-session review checklist.

## Administration and logistics

### Success criteria

We will know this plan is successfully implemented when:

- `make instar` builds and `make lint` is clean.
- Guest binaries pass `make check-binary-sizes` (384KB limit per
  operation) — particularly important given Open question 1.
- All Rust unit tests pass (`make test-rust`).
- All Python integration tests pass (`make test-integration`),
  including the full 14-row cross-validation matrix against real
  `qemu-img dd`.
- `pre-commit run --all-files` passes.
- `instar dd` matches upstream `qemu-img dd` on operand parsing,
  window/size semantics, default-raw output, dense copying, and
  exit codes (error-message text may diverge per Open question 3).
- Differential fuzzing of `dd` against `qemu-img dd` runs clean.
- Documentation in `docs/` is updated, and `ARCHITECTURE.md`,
  `README.md`, `AGENTS.md`, and `CHANGELOG.md` reflect the new
  subcommand.

### Future work

- The PVE/downstream extensions listed under "Explicitly out of
  scope": `osize=`/`isize=`, `seek=`, stdin/stdout streaming,
  `-n`, `-l`, `--target-image-opts`, and the `-o`/compression/
  backing/preallocation create options.
- `--image-opts` / `--object` / `-U` if Open question 4 defers
  them.
- If Open question 1 chose (a), evaluate extracting convert's
  format writers into a shared `no_std` crate (option (b)) so
  `dd`, `convert`, and any future writer-consumers share one
  implementation.
- The sibling in-scope subcommands `bench` and `bitmap` (separate
  master plans; `bench` pending a design spike on whether
  sandboxed benchmarking is meaningful).
- **`-f` input-format forcing** — `-f` is accepted but
  auto-detection is authoritative; threading a forced format
  through `discover_backing_chain` is deferred (OQ4-adjacent).
- **`fuzz_dd_operands`** — `parse_dd_operands` is unit-tested but
  not coverage-fuzzed (phase 8 omitted it: CLI parsing isn't fuzzed
  for any command, and it would require relocating
  `parse_qemu_img_size` to a lib crate). Add if that calculus
  changes.
- **`count=0 -O vhdx` (empty-window VHDX)** — instar produces a
  0-virtual-size VHDX that `qemu-img info` rejects (no data, exits
  0); qemu's empty VHDX is readable. Phase 9 whitelisted it as a
  known divergence so the differential campaign runs clean; making
  instar's empty VHDX qemu-readable (the difference appears to be
  the empty BAT region layout) remains open. (The empty-window vmdk
  case matches qemu — qemu's own `count=0 -O vmdk` exits 1.)
- **Cherry-pick the convert sub-cluster data-loss fix (`779e7a7`)
  to `develop`** — it fixes shipped `instar convert -O
  vmdk|vpc|vhdx` for qcow2 inputs with sub-grain cluster sizes, not
  just dd. (See Bugs fixed.)

### Bugs fixed during this work

- **convert sub-cluster data loss (`779e7a7`, phase 9).** The
  vmdk/vhd/vhdx output writers passed a full grain/block to
  `read_chain_virtual_cluster`, which fills at most one input
  cluster, so qcow2 inputs whose cluster size is smaller than the
  output grain/block were silently truncated. **Pre-existing on
  develop** (not dd-specific) — `instar convert -O vmdk|vpc|vhdx`
  of a sub-64 KiB-cluster qcow2 dropped data. Surfaced by dd
  differential fuzzing (`op_dd` targets the structured formats;
  `op_convert` only did raw/qcow2). Fixed with
  `qcow2::read_chain_virtual_range`. **Worth cherry-picking to
  develop independently of the dd branch.**
- **Dense-VHD output-capacity under-estimate (`b80c5d7`, phase
  9).** A dense (dd) dynamic-VHD output's per-block sector-bitmap +
  64 KiB alignment overhead exceeded the generic structured
  headroom, so the guest's final write ran past device capacity and
  stalled. `compute_output_capacity` now sizes the VHD case
  explicitly.

### Documentation index maintenance

- `docs/plans/index.md` — a row has been added to the *Master
  plans* table for this plan (status *Not started*); flip it to
  *Complete* when all phases land.
- `docs/plans/order.yml` — an entry has been added for
  `PLAN-dd.md`. Phase files are intentionally **not** listed in
  `order.yml`.

### Back brief

Before executing any step of this plan, the executing session
should back brief the operator on its understanding of the plan
and how the intended work aligns with it.
