# Phase 10: differential fuzzer extension for `instar create`

Master plan: [PLAN-create.md](/components/instar/plans/PLAN-create/) ·
Previous phase: [PLAN-create-phase-09-fuzz-coverage.md](/components/instar/plans/PLAN-create-phase-09-fuzz-coverage/)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (how `op_info` /
`op_check` / `op_convert` / `op_measure` are structured in
`scripts/differential-fuzz.py`, how `run_iteration` dispatches
on the `OPERATIONS` list and packages divergences into JSON
reports, how phase 8b's `tests/helpers/info_json.py` normalises
qemu-img-info JSON via a divergence whitelist, how phase 8b's
`KNOWN_WRITER_DIVERGENCES` codifies the instar/qemu writer
gaps), and ground answers in what the code does today. Where
a question touches on external concepts (libFuzzer / cargo-fuzz
mechanics, `qemu-img create` option churn across versions, VHD
CHS-geometry rounding rules), research as needed. Flag
uncertainty explicitly rather than guessing.

## Status: Not started

## Mission

Extend `scripts/differential-fuzz.py` so the existing fuzz-loop's
random operation chain can pick `'create'` as one of the
operations. For each chosen run:

1. Generate a random `(target_format, virtual_size,
   options_list)` triple, biased to avoid the documented
   writer divergences from phase 8b (CHS-rounded VHD, qcow2
   refcount_bits hardcode, qcow2 compat hardcode, zstd
   accept-ignore, vhdx default block_size).
2. Invoke `instar create -f <target> [-o ...] <tmp_a> <size>`
   and the system `qemu-img create -f <target> [-o ...] <tmp_b>
   <size>` in parallel temporary directories.
3. Run `qemu-img info --output=json` on both produced files.
4. Normalise both JSON outputs via the same divergence
   whitelist phase 8b's `tests/helpers/info_json.py` uses
   (inlined into the fuzzer for self-containment).
5. Compare the normalised dicts; report any disagreement as
   a divergence finding.

The existing `differential-fuzz.yml` CI workflow picks up the
new operation through the script's `OPERATIONS` list — no
workflow edit needed.

## Why this is its own phase

The differential fuzzer's existing operations (`info`, `check`,
`convert`, `convert_compressed`, `measure`) all *read* the
fuzzer-generated source image. `create` is the opposite shape:
it writes a *new* image from a synthetic `(target, options,
size)` triple. The differential-fuzz framework supports this
naturally — `op_*` functions are signature-compatible scalars
whose body is free to ignore the `instar_copy` / `qemu_copy`
arguments. But the input-vs-output asymmetry means:

- The fuzzer's existing per-iteration source-image generation
  is wasted on `op_create` iterations (the source isn't read).
  Acceptable — image generation is fast and the chained-op
  loop still exercises other ops on the same image.
- The comparison oracle is `qemu-img info` on both produced
  files (not the source) — same shape as phase 8b's matrix
  surface, but against the *live* system qemu-img rather than
  the frozen baseline matrix.

Splitting from phase 9 keeps the two oracles' design intents
separate:

- **Phase 9** (`fuzz_create_emitters.rs`) finds *panics,
  bookkeeping bugs, parser-side rejection* in instar's pure-
  function emitters. libFuzzer, coverage-guided, no qemu-img
  involvement.
- **Phase 10** (`differential-fuzz.py op_create`) finds
  *disagreements with qemu-img on the shared option subset*.
  Random sampling, byte-level instar create vs qemu-img
  create comparison via `qemu-img info`. Picks up
  combinations the curated phase 8b test matrix doesn't
  exercise.

## What the survey turned up

### `scripts/differential-fuzz.py` shape

1232 lines, five existing ops (`op_info`, `op_check`,
`op_convert` × 2 compress modes, `op_measure`), each with the
signature `(instar_bin, instar_copy, qemu_copy, fmt, timeout,
rng, **kwargs)` returning `Optional[divergence_dict]`. The
divergence dict carries a `'type'` discriminator plus per-type
fields the GitHub issue templater renders.

Module-level constants:

```python
FORMATS = ['qcow2', 'raw', 'vmdk', 'vpc']        # source formats
OUTPUT_FORMATS = ['qcow2', 'raw', 'vmdk', 'vpc'] # convert targets
VIRTUAL_SIZES = ['1M', '4M', '16M', '64M', '256M', '1G']
QCOW2_CLUSTER_SIZES = [512, 4096, 65536, 262144, 2097152]
OPERATIONS = ['info', 'check', 'convert', 'convert_compressed', 'measure']
KNOWN_DIVERGENCE_FIELDS = { ... }  # field-name allowlist
```

`run_iteration` (line 866) generates the source image,
duplicates it into `instar_copy` / `qemu_copy`, picks 2-4 ops
randomly, dispatches each via `if op == '...'` arms (line
890). The dispatch's `else: continue` means an unknown op is
silently skipped; the fuzzer never errors out on an extension
gap.

### Comparison oracle: `qemu-img info` on both files

Phase 8b's matrix surface compares `instar create` output
to the recorded baseline via `qemu-img info --output=json`.
Phase 10's mode is the live counterpart: both create
invocations run at fuzz time, then `qemu-img info` reads
both outputs. The comparison is between two same-tool JSON
documents — JSON shape is identical, only the bytes-being-
described differ.

Phase 8b's `tests/helpers/info_json.py` provides the
canonical normaliser:

- `UNIVERSAL_DIVERGENCE = {'actual-size', 'dirty-flag'}`
- `TARGET_DIVERGENCE['vmdk'] = {'cid', 'parent-cid'}`
- `TARGET_DIVERGENCE['vhdx'] = {'log-size'}`
- `CACHE_HINT_FIELDS = {'refcount-block-cache-size', ...}`
- `NESTED_INFO_DIVERGENCE = {'virtual-size'}` (stripped from
  `children[*].info` only — the wrapping-file physical size
  is writer-layout-dependent)
- `filename` field substituted with `$FILENAME` placeholder
  before comparison

Phase 10's fuzzer inlines a near-copy of this normaliser
rather than importing the tests/helpers module (the fuzzer
and the integration test suite live in separate Python
contexts; adding `sys.path` munging to share would couple
them in a way that bites later).

### Known writer divergences from phase 8b

```
KNOWN_WRITER_DIVERGENCES = {
    ('qcow2', '1G-rb-1'):    'instar hardcodes refcount_bits=16',
    ('qcow2', '1G-rb-8'):    'instar hardcodes refcount_bits=16',
    ('qcow2', '1G-rb-64'):   'instar hardcodes refcount_bits=16',
    ('qcow2', '1G-compat-v2'): 'instar hardcodes compat=1.1',
    ('qcow2', '1G-zstd'):    'instar accept-ignores compression_type=zstd',
    ('vhdx',  '1M-default'): 'instar default block_size differs from qemu',
    ('vhdx',  '64M-default'): 'instar default block_size differs from qemu',
    ('vhdx',  '1G-default'): 'instar default block_size differs from qemu',
    ('vhd',   '1M-default'): 'qemu rounds VHD virtual_size to CHS geometry',
    ('vhd',   '64M-default'): '...',
    ('vhd',   '1G-default'): '...',
    ('vhd',   '1M-fixed'):   '...',
    ('vhd',   '16M-fixed'):  '...',
}
```

The phase 8b test surface keys these by `(target, case_name)`
where `case_name` encodes options. Phase 10 randomises
options, so the key approach doesn't transfer directly.
Instead phase 10 *biases the random picker* away from the
known-divergent option space:

- **Skip target=vhd entirely**. Every vhd combination
  diverges via CHS-geometry rounding (instar emits exact
  bytes, qemu rounds up). The phase 8b surface skips these
  per case; the fuzzer skips at picker level.
- **qcow2 refcount_bits**: pin to 16 (the only round-tripping
  value).
- **qcow2 compat**: pin to 1.1 (the only value instar
  honours).
- **qcow2 compression_type**: never set (default is zlib;
  zstd diverges).
- **vhdx block_size**: always specify explicitly from the
  round-tripping set `{16M, 32M}`; never use the default
  (which diverges from qemu's 32M default).

After the picker bias, every randomly generated `(target,
options, size)` triple is one we *expect* to round-trip
cleanly. A divergence found by phase 10 is therefore a
real finding worth investigating — not a known limitation.

If patterns emerge in nightly runs (e.g. a new vmdk-side
option combination produces consistent divergences), the
operator updates either the picker bias or
`KNOWN_DIVERGENCE_FIELDS` with a comment.

### `qemu-img create` option support across versions

Phase 7's matrix recorded `qemu-img create` behaviour across
80 versions. Some options are version-gated:

- `extended_l2=on` (qcow2): requires qemu-img >= 5.0.
- `compression_type=zstd` (qcow2): requires qemu-img >= 5.1.
- `subformat=streamOptimized` (vmdk): some early 6.x point
  releases reject this.

The fuzzer doesn't know which qemu-img the test host has.
Solution: if `qemu-img create` rejects the option set
(non-zero exit), the iteration reports a divergence only if
instar *succeeded* on the same args (asymmetric
acceptance is a real finding). If both fail, the iteration
is a no-op. If qemu-img fails but instar succeeds, the
divergence is "instar accepts an option qemu rejects" —
arguably a bug (instar should match qemu's gating) but
often a deliberate divergence (instar accept-ignores some
keys). Recommendation: treat as divergence and let the
operator triage.

### Image-size discipline

For `op_create` the *source* image isn't read. The fuzzer's
existing `generate_image()` call still runs (the chained-op
loop on the same image needs a source for the other ops);
its output is just ignored on the `create`-only arm.

For the **create-side size**, the random picker reuses the
existing `VIRTUAL_SIZES = ['1M', '4M', '16M', '64M', '256M',
'1G']` list. With `preallocation=full` or `falloc`, large
sizes mean real disk writes — cap to 1M for those modes to
keep the fuzzer fast.

### Tmp file management

`op_create` allocates two tmp file paths under
`iter_dir / 'create-<target>.<ext>'` and
`iter_dir / 'create-qemu-<target>.<ext>'`. The iteration's
parent `finally: shutil.rmtree(iter_dir)` cleans them up
automatically.

### CI workflow

`.github/workflows/differential-fuzz.yml` invokes the
script with a duration + seed. The `OPERATIONS` extension
is invisible to the workflow — internal random-selection
mechanics. No workflow edit needed.

## Architecture

### `OPERATIONS` list extension

```python
OPERATIONS = ['info', 'check', 'convert', 'convert_compressed',
              'measure', 'create']  # NEW — phase 10
```

### Random option picker

```python
def _create_option_picker(rng):
    """Pick a (target, size_str, options_list) tuple biased
    away from known instar/qemu writer divergences.
    """
    target = rng.choice(['qcow2', 'vmdk', 'vhdx', 'raw'])
    # vhd intentionally excluded — every case diverges via
    # qemu's CHS-geometry rounding.

    if target == 'qcow2':
        # Pick a random subset of the round-tripping options.
        options = []
        cs = rng.choice(QCOW2_CLUSTER_SIZES)
        options.append(f'cluster_size={cs}')
        if rng.random() < 0.3:
            # extended_l2 requires cluster_size to be at
            # least 16k; if the random cluster is smaller,
            # skip the option.
            if cs >= 16384:
                options.append('extended_l2=on')
        if rng.random() < 0.3:
            options.append('lazy_refcounts=on')
        prealloc = rng.choice([None, 'metadata', 'falloc', 'full'])
        if prealloc is not None:
            options.append(f'preallocation={prealloc}')
            # falloc / full write real blocks; cap size.
            if prealloc in ('falloc', 'full'):
                return target, '1M', options
        size = rng.choice(['1M', '16M', '64M'])
        return target, size, options

    if target == 'vmdk':
        subformat = rng.choice(['monolithicSparse', 'streamOptimized'])
        size = rng.choice(['1M', '16M', '64M', '256M'])
        return target, size, [f'subformat={subformat}']

    if target == 'vhdx':
        # Always set block_size explicitly — instar's default
        # diverges from qemu's at sizes <= 1G.
        bs = rng.choice(['16M', '32M'])
        size = rng.choice(['64M', '256M', '1G'])
        return target, size, [f'block_size={bs}']

    # raw
    size = rng.choice(['1M', '16M', '64M', '256M'])
    return target, size, []
```

The picker is deliberately not exhaustive — it covers the
documented round-tripping subset of options. Adding new
round-tripping options as instar's coverage grows is a
one-line picker change.

### `op_create` body

```python
def op_create(instar_bin, instar_copy, qemu_copy, fmt,
              timeout, rng):
    """Create the same image via instar create and qemu-img
    create, compare via qemu-img info JSON.

    instar_copy / qemu_copy / fmt are ignored — create writes
    new files from a randomly picked (target, options, size)
    triple. They're in the signature to match the other op_*
    funcs (the fuzz loop dispatches uniformly).
    """
    target, size_str, options_list = _create_option_picker(rng)

    iter_dir = instar_copy.parent
    ext = {'qcow2': 'qcow2', 'vmdk': 'vmdk', 'vhdx': 'vhdx',
           'raw': 'raw'}[target]
    inst_path = iter_dir / f'create-instar.{ext}'
    qemu_path = iter_dir / f'create-qemu.{ext}'

    # Build the per-tool command. instar uses 'vpc' for VHD —
    # not exercised here but documented for symmetry.
    instar_target = target  # 'vhd' would map to 'vpc'
    qemu_target = target

    inst_args = ['-f', instar_target]
    qemu_args = ['-f', qemu_target]
    for opt in options_list:
        inst_args.extend(['-o', opt])
        qemu_args.extend(['-o', opt])
    inst_args.extend([str(inst_path), size_str])
    qemu_args.extend([str(qemu_path), size_str])

    i_out, i_err, i_rc = run_instar(
        instar_bin, ['create'], inst_args, timeout=timeout)
    q_out, q_err, q_rc = run_qemu_img(
        ['create'], qemu_args, timeout=timeout)

    # Acceptance-symmetry check.
    div = compare_exit_codes(
        i_rc, q_rc, 'create',
        {'target_format': target,
         'size': size_str,
         'options': options_list,
         'instar_stderr': i_err[:500],
         'qemu_stderr': q_err[:500]},
    )
    if div:
        return div
    if i_rc != 0:
        return None  # both failed, nothing to compare

    # qemu-img info both produced files.
    inst_info_out, _, inst_info_rc = run_qemu_img(
        ['info', '--output=json'], [str(inst_path)], timeout=timeout)
    qemu_info_out, _, qemu_info_rc = run_qemu_img(
        ['info', '--output=json'], [str(qemu_path)], timeout=timeout)
    if inst_info_rc != 0 or qemu_info_rc != 0:
        return {
            'type': 'create_info_readback_failure',
            'target_format': target,
            'size': size_str,
            'options': options_list,
            'instar_info_rc': inst_info_rc,
            'qemu_info_rc': qemu_info_rc,
        }

    try:
        inst_json = json.loads(inst_info_out)
        qemu_json = json.loads(qemu_info_out)
    except json.JSONDecodeError as e:
        return {
            'type': 'create_info_json_parse_failure',
            'target_format': target,
            'options': options_list,
            'error': str(e),
        }

    inst_norm = _normalise_create_info(inst_json, target, str(inst_path))
    qemu_norm = _normalise_create_info(qemu_json, target, str(qemu_path))

    if inst_norm != qemu_norm:
        return {
            'type': 'create_info_divergence',
            'target_format': target,
            'size': size_str,
            'options': options_list,
            'instar_normalised': inst_norm,
            'qemu_normalised': qemu_norm,
        }
    return None
```

### Inline normaliser

```python
# Mirrors tests/helpers/info_json.py — kept in sync by hand.
# A divergence here that needs a code fix should also land
# in the tests/helpers/info_json.py copy.
_UNIVERSAL_STRIP = {'actual-size', 'dirty-flag',
                    'refcount-block-cache-size', 'l2-cache-size',
                    'l2-cache-entry-size', 'cache-clean-interval'}
_TARGET_STRIP = {
    'vmdk': {'cid', 'parent-cid'},
    'vhdx': {'log-size'},
    'qcow2': set(),
    'raw': set(),
}
_NESTED_INFO_STRIP = {'virtual-size'}


def _strip_keys(obj, keys):
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            if k in keys:
                del obj[k]
            else:
                _strip_keys(obj[k], keys)
    elif isinstance(obj, list):
        for item in obj:
            _strip_keys(item, keys)


def _substitute_filename(obj, tmp_path):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'filename' and isinstance(v, str) and v == tmp_path:
                obj[k] = '$FILENAME'
            else:
                _substitute_filename(v, tmp_path)
    elif isinstance(obj, list):
        for item in obj:
            _substitute_filename(item, tmp_path)


def _normalise_create_info(obj, target, tmp_path):
    import copy
    result = copy.deepcopy(obj)
    strip = set(_UNIVERSAL_STRIP) | _TARGET_STRIP.get(target, set())
    _strip_keys(result, strip)
    # Nested info: strip the wrapping-file virtual-size only.
    children = result.get('children') if isinstance(result, dict) else None
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                info = child.get('info')
                if isinstance(info, dict):
                    for k in _NESTED_INFO_STRIP:
                        info.pop(k, None)
    _substitute_filename(result, tmp_path)
    return result
```

### Dispatch wiring

In `run_iteration`:

```python
elif op == 'create':
    div = op_create(
        instar_bin, instar_copy, qemu_copy, fmt,
        timeout, rng,
    )
```

## Open questions

1. **Should `op_create` also try the `instar info` -> compare
   path** (i.e., a second oracle that mirrors phase 8c's
   cross-validation surface)? Recommendation: no — the
   numeric comparison via qemu-img info is the strongest
   single oracle. Adding a second comparison doubles
   per-iteration cost for marginal coverage gain.

2. **Acceptance-asymmetry reporting**: if qemu-img rejects an
   option that instar accept-ignores (e.g. an unknown key
   that instar silently treats as a noop), is that a
   divergence? Yes — `compare_exit_codes` reports it. The
   operator can triage whether it's a deliberate instar
   "permissive parsing" choice or a real bug.

3. **Backing-file support**: phase 8b doesn't test backing
   in the matrix surface; phase 5's `TestCreateBackingChain`
   constructs runtime fixtures for backing-specific cases.
   Recommendation: skip backing in phase 10 — needs a parent
   fixture per iteration, and the comparison oracle is
   `qemu-img info` which already exercises the backing-file
   path in the info JSON. Backing-file support can be added
   to the picker as a follow-up once non-backing coverage is
   stable.

4. **Picker coverage gaps**: the current picker leaves
   several option combinations unexercised — vmdk
   grain-size (instar exposes via `--grain-size`, qemu-img
   doesn't have an `-o grain_size=` key), qcow2
   `refcount_bits != 16` (known-divergent, intentionally
   excluded). Tracking these as picker TODOs is fine; they
   don't block phase 10 from shipping.

5. **What if both instar and qemu-img produce the file but
   `qemu-img info` rejects one of them?** That's a real
   bug (instar created a structurally invalid file). The
   `create_info_readback_failure` divergence type surfaces
   it.

6. **Per-iteration timeout**: existing fuzz ops use
   `timeout=30`. instar create + qemu-img create + 2 ×
   qemu-img info = up to 4 sub-second invocations. 30s is
   ample.

7. **VHD inclusion as a follow-up**: every vhd combination
   diverges via CHS rounding. A future enhancement could
   add a *rounded-size* comparison mode for vhd (parse both
   sides' `current_size` and assert `instar_size <=
   qemu_size < instar_size + 64 KiB` rather than strict
   equality). Out of scope for phase 10; the fuzzer skips
   vhd entirely.

8. **Iteration budget allocation**: the existing OPERATIONS
   list now has 6 entries; each iteration picks 2-4 ops.
   `create` therefore averages 2-4/6 ≈ 33-67% of iterations.
   Roughly matches the others. No special bias needed.

9. **JSON dict ordering**: Python dict comparison is
   order-insensitive, so the normalised comparison doesn't
   care about JSON key order. Good — qemu-img info on
   different files may emit keys in different orders.

10. **`pre-commit` impact**: the script edit is pure
    Python; existing `pre-commit run --all-files` runs
    pyflakes / ruff over it. No new dependency.

## Public surface added in phase 10

In `scripts/differential-fuzz.py`:

- `'create'` added to `OPERATIONS`.
- New module-level helpers `_create_option_picker(rng)`,
  `_normalise_create_info(obj, target, tmp_path)`,
  `_strip_keys`, `_substitute_filename`.
- New `op_create(instar_bin, instar_copy, qemu_copy, fmt,
  timeout, rng)` function.
- Dispatch arm in `run_iteration`.

No other instar-side code changes.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 10a | medium | sonnet | none | Edit `scripts/differential-fuzz.py`: add `'create'` to `OPERATIONS`; define `_UNIVERSAL_STRIP` / `_TARGET_STRIP` / `_NESTED_INFO_STRIP` constants and the `_strip_keys` / `_substitute_filename` / `_normalise_create_info` helpers (mirror `tests/helpers/info_json.py` semantics; the comment header should say "Mirrors tests/helpers/info_json.py — keep in sync"); add `_create_option_picker(rng)` per the picker spec above; add `op_create(instar_bin, instar_copy, qemu_copy, fmt, timeout, rng)` next to `op_measure`; add the `elif op == 'create':` dispatch arm in `run_iteration`. Run `pre-commit run --all-files` and `python3 -m py_compile scripts/differential-fuzz.py`. Smoke run: `python3 scripts/differential-fuzz.py --iterations 10 --seed 42 --instar src/target/release/instar --log-dir /tmp/fuzz-logs --workdir /tmp/fuzz-work` to confirm at least a few `'create'`-arm iterations execute without script-side errors. If divergences appear in the 10-iteration window: triage one by one — if it's a real bug, file as a finding and pause; if the picker bias missed an option combination (e.g. an `extended_l2` cluster-size interaction not gated correctly), tighten the picker before continuing. Touch only `scripts/differential-fuzz.py`. |
| 10b | low | sonnet | none | Update `ARCHITECTURE.md`: the existing "Differential Fuzzing" subsection lists the operations the fuzzer covers. Add `create` to that list and mention the dual oracle (instar create vs system `qemu-img create`, comparison via `qemu-img info --output=json` with the same divergence-whitelist normaliser phase 8 uses). Note the random picker biases away from the documented known-divergent option subset (vhd CHS rounding, qcow2 refcount_bits != 16, qcow2 compat=0.10, zstd accept-ignore, vhdx default block_size). Update `CHANGELOG.md` Unreleased / Added with: "Differential fuzzer (`scripts/differential-fuzz.py`) now exercises `instar create` as one of its random operations: creates the same image via instar and the system qemu-img, compares via `qemu-img info --output=json` after normalising through the divergence whitelist. Picks up combinations the curated phase 8 test matrix doesn't exercise. ([phase 10](https://github.com/shakenfist/instar/blob/develop/docs/plans/PLAN-create-phase-10-fuzz-differential.md))". Mark phase 10 of PLAN-create.md as Complete in the execution table. Run `pre-commit run --all-files`. Touch only `ARCHITECTURE.md`, `CHANGELOG.md`, and `docs/plans/PLAN-create.md`. |

Total: 2 commits.

## Out of scope for phase 10

- vhd target coverage (every case diverges via CHS rounding;
  needs a separate rounded-size oracle — follow-up).
- Backing-file support (needs parent-fixture orchestration
  per iteration; defer).
- Negative-path coverage (invalid sizes, conflicting flags
  — better-placed in `tests/test_create.py`'s smoke surface).
- Cross-version baseline replay (phase 7 + phase 8b's
  job).
- Coverage-guided emitter fuzzing (phase 9's job).
- Importing `tests/helpers/info_json.py` directly (inlined
  for self-containment; manual sync via comment).
- libyal cross-validation (no libyal tool emits images).
- LUKS-encrypted create (master-plan future work).

## Success criteria

- `scripts/differential-fuzz.py` recognises `'create'` as a
  valid operation.
- 10-iteration smoke run completes without script-side
  exceptions.
- Any divergences found are either real bugs (filed and
  paused on) or motivate a picker-bias tightening before
  commit.
- `python3 -m py_compile scripts/differential-fuzz.py`
  succeeds.
- `pre-commit run --all-files` passes.
- `ARCHITECTURE.md`, `CHANGELOG.md`, and PLAN-create.md
  execution row updated.

## Risks and mitigations

- **Spurious divergences from picker gaps**. If the random
  picker hits an option combination that diverges in a way
  phase 8b didn't catalogue, the fuzzer reports it. Treat
  as a finding and triage: real bug -> file; documented gap
  in disguise -> tighten the picker. Mitigation: 10a's
  brief says "tighten the picker before continuing" on
  picker-side findings.
- **qemu-img version drift on the CI host**. If the
  workflow's installed qemu-img doesn't support an option
  the picker chose, qemu-img returns non-zero and
  `compare_exit_codes` flags it as instar-only acceptance.
  Mitigation: real finding — the picker should be tightened
  to drop the option, or instar should mirror qemu's
  rejection. Either response is a legitimate fix.
- **JSON parse failure on either side**. The
  `create_info_json_parse_failure` divergence type surfaces
  this; it would mean qemu-img or instar produced an
  unparseable file, which is a high-value finding.
- **Tmp-file leakage on crash**. The iteration's
  `finally: shutil.rmtree(iter_dir)` cleans up regardless.
  No mitigation needed.
- **Normaliser drift between fuzzer and test helper**. The
  inline copy can fall out of sync with
  `tests/helpers/info_json.py`. Mitigation: explicit
  comment at the top of the inline block + a follow-up to
  factor both into a shared utility module if drift bites.

## Bugs to fix

(To be filled in as work progresses.)

## Back brief

Before executing any step, the executing agent should
back-brief: which fuzzer file is being edited, where the
new operation slots in, which existing op_* function is the
closest structural template, how divergences are reported
back to the loop, and how the picker bias documents each
exclusion. The reviewer should verify the script's existing
`OPERATIONS` list expansion and the divergence-reporting
flow continue to work for the new op, and that the
normaliser stays semantically equivalent to phase 8b's
`tests/helpers/info_json.py`.
