# PLAN-create phase 4: qemu-img-style `-o key=value,...` parser

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, KVM, virtio,
disk image formats, qemu-img semantics), research as needed to
give a confident answer. Flag any uncertainty explicitly rather
than guessing.

This is a phase plan under `PLAN-create.md`. Phases 1–3 shipped
the metadata-emitter library, the guest binary, and the host
CLI subcommand with individual option flags. Phase 4 lifts the
qemu-img `-o key=value,...` syntax on top so users can write
`instar create -f qcow2 -o cluster_size=4k,extended_l2=on foo
1G` instead of `--cluster-size 4096 --extended-l2 foo 1G`.

## Mission

Replace phase 3's placeholder-rejection of `-o` with a real
qemu-img-compatible parser:

1. Accept `-o KEY=VAL,KEY2=VAL2,...` and repeated `-o` flags.
2. Recognise the per-target option matrix already supported via
   individual flags (cluster_size, refcount_bits, extended_l2,
   lazy_refcounts, compat, subformat, grain_size, block_size,
   preallocation).
3. Recognise the create-specific keys that have no individual
   flag analogue: `backing_file`, `backing_fmt`, `size`.
4. Apply `-o` values **after** the individual flags so they win
   on conflict (last-wins, matches measure phase 5 and
   qemu-img).
5. Reject unknown keys with a clear error listing the accepted
   keys for the chosen target.
6. Accept-ignore the keys qemu-img exposes that have no effect
   on instar's output (matches measure's policy for parity).
7. Defer `encrypt.*` and `data_file*` keys with explicit
   "not-yet-supported" errors pointing at the appropriate
   future phase.

## What the survey turned up

### Measure's `-o` machinery (`src/vmm/src/main.rs:5540-5776`)

The reference implementation:

- `struct MeasureOptionOverrides { cluster_size: Option<u32>, ... }`
  — every field is `Option<T>` so "user didn't set it" is
  distinguishable from "user set it to the default".
- `fn parse_o_options(target: &str, raw: &[String]) ->
  Result<MeasureOptionOverrides, _>` — single function with a
  per-target whitelist `match` expression.
- Three small helpers: `parse_o_bool` (on/off/true/false/yes/no
  case-insensitive), `parse_o_size_u32` (K/M/G/T suffixes via
  `parse_memory_size`), `parse_o_u8`.
- Apply pattern at the call site:
  `if let Some(v) = overrides.cluster_size { cluster_size = v; }`
  — overrides win unconditionally.
- Per-target reject list for qcow2 (`backing_file`, `backing_fmt`,
  `data_file`, `data_file_raw`, `encrypt.*`) — measure rejects
  these because chain composition and LUKS-aware measurement
  aren't implemented.
- Per-target accept-ignore list for keys that have no size
  effect: vmdk (`adapter_type`, `hwversion`, `toolsversion`,
  `zeroed_grain`), vpc (`force_size`, `force_size_calc`), vhdx
  (`log_size`, `block_state_zero`).
- `raw` target rejects any `-o` because `-o size=N` doesn't
  fit measure's `--size N` semantics. Create is different —
  see open question 1.

### Phase 3 `-o` stub (`src/vmm/src/main.rs:7050-7070`)

`validate_create_args` currently rejects any `-o` option with
a phase-4 pointer:

```rust
if !args.option.is_empty() {
    return Err("create: -o key=value parsing lands in phase 4 …".into());
}
```

Phase 4 removes this block and routes through the new parser.

### Phase 3 individual-flag application

Per-format scalars get extracted from `args` into locals inside
`run_create_nonraw` (~`src/vmm/src/main.rs:6470-6500`):

```rust
let cluster_size = args.cluster_size;
let refcount_bits = args.refcount_bits;
// ...
```

Phase 4 needs to apply `-o` overrides between "args parsed" and
"these locals captured". Cleanest factoring: parse `-o` at the
top of `run_create`, then *mutate `args` in place* with the
overrides before the existing dispatch runs. This keeps the
touch-point inside `run_create_raw` / `run_create_nonraw`
unchanged.

### Existing call sites

The clap stub `-o` argument is already in `CreateArgs`
(`src/vmm/src/main.rs:2670`). No clap surface changes are
needed in phase 4 — only the validator and the parser.

## Public surface added in phase 4

### `CreateOptionOverrides`

```rust
#[derive(Default, Debug)]
struct CreateOptionOverrides {
    // Per-format scalars (mirrors phase 3's per-flag args)
    cluster_size: Option<u32>,
    refcount_bits: Option<u8>,
    extended_l2: Option<bool>,
    lazy_refcounts: Option<bool>,
    compat_v3: Option<bool>,
    vmdk_subformat: Option<u8>,
    grain_size: Option<u32>,
    vhd_subformat: Option<u8>,
    block_size: Option<u32>,
    preallocation: Option<&'static str>,
    // Create-specific keys (no individual flag analogue)
    size: Option<u64>,
    backing_file: Option<String>,
    backing_fmt: Option<&'static str>,
}
```

### `parse_create_o_options`

```rust
fn parse_create_o_options(
    target: &str,
    raw: &[String],
) -> Result<CreateOptionOverrides, Box<dyn std::error::Error>>;
```

Mirrors measure's parser. Per-target whitelist:

**raw**:
- `size=N` (accepted — see open question 1).
- `preallocation=off|falloc` (accepted; `metadata`/`full`
  reject with phase-6 pointer).
- Any other key → "raw output does not support -o <key>".

**qcow2**:
- All of measure's qcow2 keys (cluster_size, compat,
  refcount_bits, extended_l2, lazy_refcounts,
  compression_type, preallocation).
- Plus: `size=N`, `backing_file=PATH`, `backing_fmt=FMT`.
- Reject: `data_file`, `data_file_raw` ("external data files
  are deferred — see PLAN-convert-followups.md").
- Reject: `encrypt.*` ("encrypted create is deferred — see
  PLAN-create.md phase 11 / future work").
- Accept-ignore: none currently (qemu-img's qcow2 has very few
  no-op keys).

**vmdk**:
- `subformat=monolithicSparse|streamOptimized|monolithicFlat`
  (last rejects with phase-5 pointer — same as the individual
  --subformat flag).
- `grain_size=N`.
- `size=N`, `backing_file=PATH`, `backing_fmt=FMT`.
- Accept-ignore: `adapter_type`, `hwversion`, `toolsversion`,
  `zeroed_grain` (same as measure).

**vpc** (VHD):
- `subformat=dynamic|fixed`.
- `size=N`, `backing_file=PATH`, `backing_fmt=FMT`.
  (backing for VHD differencing disks rejects with phase-5
  pointer at host-side — same as the individual `-b` flag does
  today.)
- Accept-ignore: `force_size`, `force_size_calc`.

**vhdx**:
- `subformat=dynamic` (fixed rejects with phase-5 pointer).
- `block_size=N`.
- `size=N`, `backing_file=PATH`, `backing_fmt=FMT`.
  (backing for vhdx rejects in host-side until phase 5.)
- Accept-ignore: `log_size`, `block_state_zero`.

Unknown keys → "create: unrecognised -o key '<key>' for
target <fmt> (accepted keys: ...)".

### Override application

A new helper, called at the top of `run_create`:

```rust
fn apply_create_overrides(
    args: &mut CreateArgs,
    overrides: CreateOptionOverrides,
);
```

Mutates `args` in place. Per-field logic:

- Numeric / string fields: if `Some(v)`, copy into the matching
  `args` field. `args.cluster_size = v.unwrap_or(args.cluster_size)`.
- `extended_l2`, `lazy_refcounts`: if `Some(v)`, set the
  corresponding bool.
- `compat_v3`: maps to `args.compat = if v { "1.1" } else { "0.10" }`.
- `vmdk_subformat`, `vhd_subformat`: map back to the string
  values clap's value_parser accepted (because
  `validate_create_args` re-checks the string).
- `size`: format as decimal-bytes string (e.g. `"1073741824"`)
  and assign to `args.size`. `parse_memory_size` will parse it
  back. This keeps the rest of the pipeline (run_create_raw +
  run_create_nonraw) unchanged.
- `backing_file`: if `Some(p)`, override `args.backing`. **If
  both `-b` and `-o backing_file` were given and they differ,
  the override wins silently** — matches measure's last-wins
  policy.
- `backing_fmt`: same shape as backing_file.
- `preallocation`: copy the string.

After application, `validate_create_args(&args)` runs as before
— catching any post-override invariants the parser couldn't
catch in isolation (e.g. cluster_size=512 with extended_l2
might exceed the guest scratch, which the guest checks at
runtime).

## Open questions

1. **`-o size=N` on raw target.** qemu-img accepts
   `qemu-img create -o size=1G -f raw foo.raw` as an
   alternative to the positional SIZE. measure rejects any
   `-o` for raw because measure has no metadata to write. For
   create, accepting `-o size=N` on raw is a parity win.
   Recommend: accept; the raw short-circuit parses
   `args.size` whether it came from the positional or from -o.

2. **`-b BACKING` vs `-o backing_file=PATH` conflict.**
   qemu-img: last-wins on the command line. instar: also
   last-wins via override application (the override layer
   always runs after the args are parsed, so `-o` always
   wins regardless of order). The user-typed path goes
   through verbatim either way (matches the phase-3d
   embed-user-typed contract).

3. **`-F FMT` vs `-o backing_fmt=FMT` conflict.** Same
   resolution as backing_file — `-o` wins.

4. **`-o size` parsing.** Should accept the same suffix set
   as `parse_memory_size` (K, M, G, T). Recommend: reuse
   `parse_o_size_u32` but bump it to u64 (`parse_o_size_u64`)
   for size, since the existing helper caps at u32::MAX.

5. **`data_file` / `data_file_raw` for qcow2.** qemu-img
   accepts these to specify an external data file for qcow2
   v3 with `INCOMPAT_EXTERNAL_DATA` set. instar's
   `crates/create` doesn't currently emit external-data
   qcow2; measure also rejects these. Recommend: reject in
   phase 4 with "external data files are deferred — see
   PLAN-convert-followups.md / future create work".

6. **`encrypt.format=luks` and friends.** Encrypted create is
   deferred per the master plan. Recommend: reject with a
   "future work" pointer (matches measure).

7. **`compression_type` for qcow2.** qemu-img's
   `compression_type` only affects what header bit is set —
   it doesn't change empty-image bytes for the current
   create scope (we don't emit data clusters at all). measure
   accepts and stores in `compression_used`. For create we
   could accept-ignore, but the cleaner thing is to record
   it in CreateConfig.flags so phase-6's preallocation work
   has the bit ready when needed. Recommend: accept-ignore
   for phase 4 (no CreateConfig.flags bit yet). Document the
   gap.

8. **Shared parser helpers (`parse_o_bool`, `parse_o_size_u32`,
   `parse_o_u8`).** Measure has these as private functions
   inside main.rs with "measure: " hardcoded in error
   messages. Three options for create:
   (a) Duplicate as `parse_create_o_bool` etc. — three small
   functions, no cross-coupling, error prefix is correct.
   (b) Refactor measure's helpers to take a `command_name:
   &str` parameter. One small refactor touching two call
   sites in measure plus all call sites here.
   (c) Factor into a `cli::option_parsers` submodule.
   Recommend: (a) — duplication is acceptable, scope creep
   is the bigger risk. Note (b) as a future cleanup.

9. **Unit-test home.** `src/vmm/src/main.rs` currently has
   no `#[cfg(test)] mod tests` block. Other vmm files
   (stats.rs, version.rs) do. Recommend: add a
   `#[cfg(test)] mod tests` block at the bottom of `main.rs`
   covering `parse_create_o_options` and
   `apply_create_overrides` in isolation. The integration
   tests in `tests/test_create.py` cover the end-to-end
   surface.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Add `CreateOptionOverrides`, `parse_create_o_options`, and the three create-specific parser helpers (`parse_create_o_bool`, `parse_create_o_size_u32`, `parse_create_o_size_u64`, `parse_create_o_u8`) to `src/vmm/src/main.rs`. Place them immediately after measure's `parse_o_options` (~line 5776). The whitelist is documented in the "Public surface" section above — implement it exactly. Do **not** wire it into `run_create` yet; that's step 4b. Add a `#[cfg(test)] mod tests` block at the end of `main.rs` (or in a new file if more natural) with unit tests covering: (1) default empty overrides parse cleanly; (2) every per-target whitelist key parses and sets the right field; (3) unknown key returns a clear error; (4) bad value (e.g. `cluster_size=foo`) errors; (5) last-wins for repeated keys across multiple `-o` strings; (6) raw + `size=1M` works; (7) qcow2 `encrypt.cipher=aes` returns a "future work" error; (8) qcow2 `data_file=x` returns a "deferred" error; (9) vmdk monolithicFlat is parsed as `Some(2)` (the host-side validator will reject it). Run `cargo test -p vmm` and confirm the new tests pass. |
| 4b | medium | sonnet | none | Wire `-o` parsing into `run_create`. Two changes: (1) remove the "phase-4 will implement this" rejection from `validate_create_args` (lines ~7050-7065). (2) At the top of `run_create`, after `validate_create_args(&args)?`, call `parse_create_o_options(&args.target_format, &args.option)?` and then `apply_create_overrides(&mut args, overrides)`. **Note:** `validate_create_args` will need to be called *again* after `apply_create_overrides` so the post-override values are re-checked (e.g. -o cluster_size=1000 should still error even though clap didn't see it). Easiest: factor the validator into two halves — `validate_create_args_static(&args)` (runs first; checks the things -o can't touch like FILENAME-required and -b-without-F-or-u) and `validate_create_args_resolved(&args)` (runs after override application; checks numeric ranges, subformat / format combinations, preallocation modes). The exact split should be informed by reading the existing validator function. Smoke-test by hand: `instar create -f qcow2 -o cluster_size=4096,extended_l2=on /tmp/foo.qcow2 16M` succeeds and `instar info /tmp/foo.qcow2` reports `cluster_size=4096` + `extended_l2=true`. Then: `instar create -f qcow2 -o unknown_key=1 /tmp/foo.qcow2 16M` errors with the unknown-key message. |
| 4c | medium | sonnet | none | Add a small set of integration tests to `tests/test_create.py` (after `TestCreateSmoke`) covering: (1) `-o cluster_size=4096` round-trips through `instar info` reporting cluster_size=4096; (2) `-o extended_l2=on` sets the bit (info reports `extended_l2=true`); (3) `-o size=16M` works as the only size source (no positional SIZE); (4) `-o size=64M` overrides positional `SIZE=16M` (info reports 64M); (5) `-o backing_file=parent.qcow2,backing_fmt=qcow2` as an alternative to `-b -F` (info reports backing-filename=parent.qcow2); (6) `-o cluster_size=4k,refcount_bits=8` (compound `-o` value with two keys works); (7) error: `-o unknown_key=1` returns non-zero with the listed-keys message; (8) error: `-o encrypt.cipher=aes` returns the "future work" message; (9) error: `-o preallocation=metadata` (still gated by phase 6). Eight tests; run them through `make test-integration` to confirm. |
| 4d | low    | sonnet | none | Documentation: (1) `CHANGELOG.md` — add a one-paragraph "Phase 4 of PLAN-create" note under the existing Unreleased section noting that `-o key=value` now works alongside the individual flags. (2) `AGENTS.md` and `ARCHITECTURE.md` — short mentions that the qemu-img-style parser ships in phase 4 (one sentence each). (3) Mark PLAN-create.md's execution table row for phase 4 as Complete. **Do not** touch `docs/usage.md` or `docs/create.md` — those land in phase 11 once the whole subcommand is fully documented for users. |

## Out of scope for phase 4

Reminders so a sub-agent doesn't drift:

- No new `CreateConfig` fields — every key parsed already maps
  to existing fields populated from `CreateArgs`.
- No changes to the create guest binary, `crates/create`, or
  any operation binary.
- No new clap arguments — `-o` is already in the surface from
  phase 3 (placeholder).
- No JSON output changes — the result rendering is unchanged.
- No baseline-driven cross-version tests — phase 7 / 8.
- No relaxation of phase 3's `--sector-size=512` constraint.
- No backing-chain composition or vhdx-as-backing — phase 5.
- No preallocation modes beyond `off` / `falloc` — phase 6.
- No encryption — deferred indefinitely.

## Success criteria

- `make instar` builds cleanly.
- `make lint` clean.
- `make test-rust` passes — the new unit tests in
  `src/vmm/src/main.rs` raise vmm's test count.
- `make test-integration` includes the new
  `tests/test_create.py` `-o` cases and they all pass.
- `pre-commit run --all-files` clean.
- `instar create -f qcow2 -o cluster_size=4096 ...` produces
  a 4 KiB-cluster qcow2 (verify with `instar info` and
  `qemu-img info`).
- `instar create -f qcow2 -o unknown_key=1 ...` errors with
  the unknown-key message listing the accepted keys for qcow2.
- `git diff --stat phase-4-base..HEAD -- src/operations/
  src/crates/ src/shared/ src/core/ crates/` is empty
  (phase 4 is host-CLI-only).

## Bugs fixed during this work

(To be filled in.)

## Back brief

Before executing each step of this phase, please back brief the
operator as to your understanding of the step and how the work
you intend to do aligns with the brief. In particular, flag if
the brief refers to file/line locations that don't match what
you find when you read them (the survey was a snapshot; the
codebase may have moved).
