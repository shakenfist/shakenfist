# Phase 5: `-o key=value,...` qemu-img-style option parsing

Master plan: [PLAN-measure.md](/components/instar/plans/PLAN-measure/) ·
Previous phase: [PLAN-measure-phase-04-host-cli.md](/components/instar/plans/PLAN-measure-phase-04-host-cli/)

## Status: Not started

## Mission

Phase 4 ships per-target options as individual clap flags
(`--cluster-size N`, `--extended-l2`, `--compat 0.10|1.1`,
etc.). qemu-img exposes the same options through one flag:
`-o cluster_size=N,extended_l2=on,compat=1.1,...`. Phase 5
adds the `-o key=value,...` parser on top of phase 4 so:

```
instar measure --size 1G -O qcow2 -o cluster_size=64k,refcount_bits=8,extended_l2=on
```

works and produces qemu-img-byte-identical output. The
individual flags from phase 4 stay; `-o` values override
them so scripts can mix syntaxes if needed.

## Why this is its own phase

The phase 4 surface is already usable, just not qemu-img-quote
compatible. `-o` adds:

1. A small parser (split-on-comma, then split-on-equals) with
   key/value validation per target format.
2. A precedence rule (`-o` overrides individual flags).
3. Value-form handling: `<size>` (with K/M/G/T suffixes,
   reusing the existing `parse_memory_size` helper), `<bool>`
   (on/off/true/false), `<num>` (decimal u32/u8), and `<str>`
   (enum-matched).
4. Unknown-key rejection (per-target whitelist).

None of this changes the boundary (`MeasureConfig` is
unchanged) or the guest binary; it's purely host-side option
parsing. Splitting from phase 4 keeps each commit reviewable.

## Architecture

### Coexistence with individual flags

Two reasonable rules. Phase 5 picks **rule A**:

- **Rule A (chosen)**: `-o` values *override* individual flag
  values. The user gets predictable behaviour when scripting
  qemu-img-style commands and the individual flags become a
  shorthand that `-o` can refine. Internal precedence is:
  format default → individual flag → `-o key=value`.
- Rule B (rejected): error on any overlap. Stricter but
  hostile to incremental command construction.

Document this in `docs/quirks.md` (deferred to phase 10 along
with the rest of the measure docs).

### Option key surface

Source of truth for which keys are accepted is the table
below, organised by target format. Keys that affect output
size are honoured; keys that don't are accepted-but-ignored
(for qemu-img command-line compatibility) or rejected (where
the user clearly wants something we don't implement).

#### qcow2

| Key | Type | Meaning | Phase 5 action |
|-----|------|---------|----------------|
| `cluster_size` | size | qcow2 cluster size in bytes | honour |
| `compat` | str: "0.10" / "1.1" | v2 vs v3 | honour |
| `refcount_bits` | num | refcount entry width | honour |
| `extended_l2` | bool | extended L2 entries | honour |
| `lazy_refcounts` | bool | postpone refcount updates | accept (no size effect) |
| `compression_type` | str: "zlib" / "zstd" | compression algorithm | accept (no size effect) |
| `preallocation` | str: "off" / "metadata" / "falloc" / "full" | preallocation mode | honour |
| `backing_file` | str | base image path | reject ("not supported by measure") |
| `backing_fmt` | str | base image format | reject |
| `data_file` | str | external data file path | reject |
| `data_file_raw` | bool | external data file is raw | reject |
| `encrypt.*` | various | encryption config | reject ("LUKS measurement is master-plan future work"; phase 5 does not honour `encrypt.format=luks`) |

#### vmdk

| Key | Type | Meaning | Phase 5 action |
|-----|------|---------|----------------|
| `subformat` | str: "monolithicSparse" / "streamOptimized" / "monolithicFlat" | layout choice | honour |
| `grain_size` | size | grain size in bytes | honour (instar extension; qemu-img exposes only the default 64KiB for measure) |
| `adapter_type` | str | virtual disk adapter | accept-ignore |
| `hwversion` | str | hardware version | accept-ignore |
| `toolsversion` | str | VMware Tools version | accept-ignore |
| `zeroed_grain` | bool | zero-fill semantics | accept-ignore |

#### vpc (VHD)

| Key | Type | Meaning | Phase 5 action |
|-----|------|---------|----------------|
| `subformat` | str: "dynamic" / "fixed" | layout choice | honour |
| `force_size` | bool | use disk_size rather than CHS for capacity | accept-ignore (does not affect required/fully_allocated) |
| `force_size_calc` | str | size calculation method | accept-ignore |

#### vhdx

| Key | Type | Meaning | Phase 5 action |
|-----|------|---------|----------------|
| `subformat` | str: "dynamic" / "fixed" | layout choice | honour ("fixed" → reject for now, dynamic-only) |
| `block_size` | size | block size in bytes | honour |
| `log_size` | size | log region size | accept-ignore (instar always uses 1 MiB) |
| `block_state_zero` | bool | initial block state | accept-ignore |

#### raw

`raw` has no creation options. Any `-o` keys with `-O raw`
should error: "raw output does not support -o options".
qemu-img matches this behaviour.

### Parser interface

New helper in `src/vmm/src/main.rs`:

```rust
/// Parsed values for the size-relevant subset of `-o key=value,...`.
/// Each field is `Some(v)` if the user explicitly supplied that key,
/// `None` otherwise. Applied last (after individual clap flags) so
/// `-o` wins on conflict.
#[derive(Default, Debug)]
struct MeasureOptionOverrides {
    cluster_size: Option<u32>,
    refcount_bits: Option<u8>,
    extended_l2: Option<bool>,
    lazy_refcounts: Option<bool>,
    compat_v3: Option<bool>,
    compression_used: Option<bool>,
    preallocation: Option<&'static str>, // "off"/"metadata"/"falloc"/"full"
    vmdk_subformat: Option<u8>,
    grain_size: Option<u32>,
    vhd_subformat: Option<u8>,
    block_size: Option<u32>,
}

/// Parse a vector of `-o key=value,...` strings (clap collects them
/// via action=Append) into a MeasureOptionOverrides for the given
/// target format. Returns an error on unknown keys, invalid values,
/// or unsupported features.
fn parse_o_options(
    target_format: &str,
    raw_options: &[String],
) -> Result<MeasureOptionOverrides, Box<dyn std::error::Error>>
```

The function:

1. Splits each input string on `,`.
2. Splits each piece on `=` (first occurrence only — values
   may contain `=`).
3. Looks up the key in a per-target whitelist.
4. Parses the value according to the key's declared type:
   - `size`: reuse `parse_memory_size` (handles K/M/G/T).
     Bounds-check against u32::MAX for cluster/grain/block
     fields.
   - `bool`: accept `on`/`off`/`true`/`false`/`yes`/`no`,
     case-insensitive.
   - `num`: parse as u32 or u8 depending on the key.
   - `str`: match against the key's value enum (e.g.
     `compat=0.10|1.1`).
5. Returns the populated struct or a clear error including
   the offending key/value.

Reusable value parsers (`parse_o_bool`, `parse_o_size`,
`parse_o_str_enum`) live as private helpers in main.rs.

### Wiring into `run_measure`

Two small changes inside `run_measure`:

1. **Call the parser early** (after arg validation, before
   the format-dispatch block):
   ```rust
   let overrides = parse_o_options(&args.target_format, &args.option)?;
   ```
2. **Apply overrides after the individual-flag-derived
   defaults are computed**:
   ```rust
   // Phase 4 individual-flag derivation:
   let mut cluster_size = args.cluster_size;
   let mut extended_l2 = args.extended_l2;
   // ... etc

   // Phase 5 overrides (last wins):
   if let Some(v) = overrides.cluster_size { cluster_size = v; }
   if let Some(v) = overrides.extended_l2 { extended_l2 = v; }
   // ... etc
   ```

The MeasureConfig write code below stays identical; only the
upstream values change.

### Clap surface change

Add to `MeasureArgs`:

```rust
/// qemu-img-style options as comma-separated key=value pairs
/// (e.g. -o cluster_size=64k,extended_l2=on). Values
/// override the matching individual flags. Repeatable: each
/// invocation contributes more keys.
#[arg(short = 'o', long = "options", action = clap::ArgAction::Append,
      value_name = "KEY=VALUE,...")]
option: Vec<String>,
```

`action = Append` so `-o foo=1 -o bar=2` accumulates into
`["foo=1", "bar=2"]`.

### Error messages

Match qemu-img's tone where reasonable:

- Unknown key: `measure: unrecognised -o key 'X' for target Y`
- Invalid value: `measure: bad value '<v>' for -o key '<k>' (<reason>)`
- Unsupported feature: `measure: -o encrypt.* is not yet supported`
- Raw + any `-o`: `measure: raw output does not support -o options`

Errors are stderr; exit code 1.

## Open questions

1. **Should `-o subformat=fixed -O vpc` be honoured even
   though phase 4 doesn't expose a `--subformat fixed` flag
   for VHD?** Recommendation: yes — `-o` is the qemu-img
   surface, so any size-relevant key it accepts there should
   work here. Phase 1's `measure_vhd` already implements
   `VhdSubformat::Fixed`. Phase 5 closes the gap.

2. **Bare `-o` with no value (i.e. `-o help`)**: qemu-img
   prints the option list and exits. Phase 5 could mimic this
   for parity. Recommendation: defer to phase 10 / future
   work; emit a clear error in phase 5 ("`-o help` is not yet
   supported, see `--help` for available flags").

3. **Multiple `-o` invocations with conflicting keys**:
   `-o cluster_size=1k -o cluster_size=2k`. qemu-img takes
   the last value. Match that.

4. **`encrypt.format=luks` as a size signal**: a LUKS-encrypted
   qcow2 output adds a header (16 MiB by default for v2).
   Phase 1's `measure_qcow2` accepts a
   `luks_header_overhead: Option<u64>`. Phase 5 *could*
   recognise `encrypt.format=luks` and set this to 16 MiB.
   But getting the LUKS header size exactly right needs to
   account for the full encrypt.* config (cipher, iterations,
   slots). Recommendation: reject `encrypt.*` entirely in
   phase 5; revisit as a small follow-up after phase 7 if any
   user actually needs it.

5. **`-o subformat=streamOptimized` and `-O vmdk -o compress=on`**:
   qemu-img makes `-c` and `subformat=streamOptimized`
   roughly equivalent for output. instar's convert exposes
   them as separate flags. Recommendation: honour
   `subformat=streamOptimized` literally and accept-ignore
   anything else; don't try to be clever about implicit
   combinations.

6. **Case-sensitivity of values**: qemu-img is case-sensitive
   on subformat names (`monolithicSparse`, not `MonolithicSparse`
   or `monolithicsparse`). Match that for compatibility,
   except for boolean values (case-insensitive).

7. **`-o help`** without `-O`: error.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | Add `option: Vec<String>` to `MeasureArgs` with `short = 'o'`, `long = "options"`, `action = clap::ArgAction::Append`. Add `MeasureOptionOverrides` struct (default-zero Options for every size-relevant field) and `fn parse_o_options(target: &str, raw: &[String]) -> Result<MeasureOptionOverrides, _>` to `src/vmm/src/main.rs`. Implement the parser per the "Parser interface" section: split on `,`, split each piece on first `=`, look up the key in a per-target whitelist (the four tables above), parse the value with helpers `parse_o_bool`, `parse_o_size` (reuse `parse_memory_size`), `parse_o_str_enum`. Reject unknown keys, encrypt.*, backing_file / backing_fmt / data_file / data_file_raw with clear errors. For `target == "raw"`, reject any `-o` with the dedicated message. Add `#[allow(dead_code)]` on the new struct's Default impl if the linter complains about unused fields in this commit (5b wires them). Run `make lint`, `make test-rust`, `pre-commit run --all-files`. Only `src/vmm/src/main.rs` modified. |
| 5b | low | sonnet | none | Wire `parse_o_options` into `run_measure`: call it after the existing arg validation, build the override struct, then apply each `Some(v)` override after the individual-flag-derived values are computed but before the MeasureConfig is written. The override application is mechanical: for each field in MeasureOptionOverrides, `if let Some(v) = overrides.<field> { local_<field> = v; }` immediately before the corresponding flag bit / config field is set. Run `make instar`, `make lint`, `make test-rust`, `pre-commit`. Manual end-to-end smoke: `instar measure --size 1G -O qcow2 -o cluster_size=64k,refcount_bits=8,extended_l2=on --output=json` should match `qemu-img measure --size 1G -O qcow2 -o cluster_size=64k,refcount_bits=8,extended_l2=on --output=json` byte-for-byte. Only `src/vmm/src/main.rs` modified. |
| 5c | medium | sonnet | none | Extend `tests/test_measure.py` with a new `TestMeasureOptions(InstarTestBase)` class containing ≥10 tests covering: `-o cluster_size=512` matches the `--cluster-size 512` phase-4 test (1M qcow2: required=22528, fully-allocated=1071104); `-o cluster_size=64k` works with the K suffix; `-o refcount_bits=8` (pin against qemu-img output run during sub-agent work; reference `qemu-img measure --size 1M -O qcow2 -o refcount_bits=8 --output=json`); `-o extended_l2=on,cluster_size=64k` honours both keys; `-o lazy_refcounts=on` accepted but does not change required; `-o compression_type=zlib` accepted (no size change); `-o preallocation=metadata` returns required==fully_allocated (matches qemu-img's worst-case sizing per phase 1's metadata-equals-off note); `-o cluster_size=64k -o refcount_bits=8` (two `-o` invocations) combines correctly; `-o cluster_size=64k -o cluster_size=512` last-wins (use the smaller value); `-o unknown_key=1` errors with a recognisable message; `-o encrypt.format=luks` errors with the "not yet supported" message; `-O raw -o cluster_size=64k` errors with the raw message. Pin literal expected bytes for the size-changing cases (sourced from live qemu-img during sub-agent work). Add at the bottom of the existing test_measure.py so the smoke suite stays separate. Run `make test-integration` or equivalent. Only `tests/test_measure.py` modified. |
| 5d | low | sonnet | none | Update ARCHITECTURE.md to note that the measure CLI now accepts `-o key=value,...` for qemu-img parity (edit the existing operations/measure/ bullet — replace the "`-o key=value,...` ships in phase 5" sentence with "Accepts both individual flags and `-o key=value,...` (qemu-img parity); `-o` values override individual flags."). Update CHANGELOG.md Unreleased / Added with one line: "`instar measure` now accepts the qemu-img `-o key=value,...` option syntax in addition to individual flags. (PLAN-measure-phase-05-target-options.md)" Run `pre-commit run --all-files`. Only ARCHITECTURE.md and CHANGELOG.md modified. |

Total: 4 commits.

## Out of scope for phase 5

- `-o help` listing (qemu-img prints the available options
  when given this; phase 5 errors with a clear message).
- `encrypt.*` keys (LUKS-aware measurement is master-plan
  future work).
- `backing_file`, `backing_fmt`, `data_file`, `data_file_raw`
  (require chain or external-data-file support that phase 5
  doesn't build).
- Convert subcommand `-o` parsing (out of scope for the
  measure plan; could be a follow-up plan).
- `docs/quirks.md` documentation of the precedence rule
  (phase 10).
- `docs/measure.md` user guide (phase 10).

## Success criteria

- `instar measure -O qcow2 -o cluster_size=64k,refcount_bits=8,extended_l2=on`
  produces qemu-img-byte-identical output for both human and
  JSON formats.
- `instar measure -O qcow2 -o cluster_size=1G` (clearly oversized
  for cluster_size; the bound is 2 MiB) fails with a clear
  error pointing at the invalid value.
- `instar measure -O raw -o anything=value` errors.
- `instar measure -O qcow2 -o encrypt.format=luks` errors
  with the unsupported-feature message.
- `instar measure -O qcow2 -o cluster_size=64k -o cluster_size=512`
  uses 512 (last-wins).
- `-o` and individual flags both work; `-o` wins on conflict.
- `make instar` builds; `make lint` clean;
  `make test-rust` passes; `make test-integration` includes
  the new tests and passes;
  `pre-commit run --all-files` clean.
- `make check-binary-sizes` still passes (no guest binary
  changes in phase 5).
- ARCHITECTURE.md and CHANGELOG.md updated.

## Risks and mitigations

- **`parse_memory_size` returning u64 but cluster_size /
  block_size / grain_size are u32**: explicit bounds check on
  the parsed value, error on overflow. Easy to miss.
  Mitigation: 5a's tests include a `cluster_size=8G` case
  asserting the bound is enforced cleanly rather than
  truncating.
- **Boolean parsing ambiguity**: `1` and `0` are not qemu-img
  bool forms but a user might try. Recommendation: reject
  anything that isn't the canonical six (on/off/true/false/
  yes/no, case-insensitive). qemu-img uses on/off only;
  staying close to that minimises divergence.
- **Subformat string casing**: qemu-img is case-sensitive
  (`monolithicSparse`). If we accept lower-case, we diverge
  silently. Mitigation: phase 5 matches qemu-img's
  case-sensitivity. Document if there's user pushback.
- **Test breakage from running qemu-img**: 5c's brief
  sources literal expected bytes from live qemu-img during
  sub-agent work. The host has qemu-img 10.x installed
  (verified during phase 1). If a CI environment has an
  older qemu-img, the pinned values may differ; the smoke
  tests already use the same qemu-img-pinned approach and
  the cross-version matrix lives in phase 7.
- **Precedence-rule surprise**: a user passing both
  `--cluster-size 64k` and `-o cluster_size=512` expects one
  to win. Phase 5 chooses `-o` (last-applied). Document
  during phase 5 commit messages and in phase 10's user
  guide.

## Back brief

Before executing any step, the executing agent should
back-brief: which target's key table they're parsing, which
existing helpers (`parse_memory_size`) are being reused, and
which keys are being deliberately rejected vs accept-ignored
vs honoured. The reviewer should verify the parser does not
silently accept keys it should reject and that the
precedence rule is applied uniformly across all override
fields.
