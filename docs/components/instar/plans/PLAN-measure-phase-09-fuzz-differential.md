# Phase 9: differential fuzzer extension for `instar measure`

Master plan: [PLAN-measure.md](/components/instar/plans/PLAN-measure/) ·
Previous phase: [PLAN-measure-phase-08-fuzz-coverage.md](/components/instar/plans/PLAN-measure-phase-08-fuzz-coverage/)

## Status: Not started

## Mission

Extend `scripts/differential-fuzz.py` so the existing
fuzz-loop's random operation chain can pick `'measure'` as
one of the operations. For each chosen run:

- **For target ∈ {raw, qcow2}**: invoke `instar measure -O
  <target>` and `qemu-img measure -O <target>`, parse both
  outputs, and compare `required` + `fully-allocated`
  numerically. Any disagreement is reported as a divergence.
- **For target ∈ {vmdk, vpc, vhdx}**: invoke `instar measure
  -O <target>` (qemu-img doesn't support these), then
  `instar convert -O <target>`, and assert
  `os.path.getsize(out) ∈ [required - cushion,
  fully_allocated + cushion]` (one-output-sector cushion).
  A bound violation is reported as a divergence.

After phase 9, the existing `differential-fuzz.yml` CI
workflow (which runs `python3 scripts/differential-fuzz.py`
on demand) exercises measure alongside info / check /
convert / convert_compressed without any workflow edit —
the script's `OPERATIONS` list expansion is automatic.

## Why this is its own phase

The differential fuzzer has two distinct oracles:

- **Numeric output comparison** (info / measure JSON-numeric
  fields): catches "we agree on the structure but disagree
  on the value" bugs.
- **Content content comparison** (convert raw-flatten +
  SHA-256): catches "we both succeed but produce different
  bytes" bugs.

Measure uses the numeric-comparison oracle for raw/qcow2 and
a *self-consistency* oracle (measure vs convert) for the
three vmdk-family targets qemu-img can't measure. The
self-consistency check is unique to measure — info / check /
compare don't have a comparable bound to assert. Splitting
this work from phase 8 keeps that oracle's design intent
clear.

## Architecture

### `OPERATIONS` list extension

```python
# scripts/differential-fuzz.py

OPERATIONS = [
    'info',
    'check',
    'convert',
    'convert_compressed',
    'measure',                  # NEW — phase 9
]
```

When the fuzz loop picks `'measure'`, it calls the new
`op_measure(instar_bin, instar_copy, qemu_copy, fmt,
timeout, rng)` function (signature matches the existing
`op_info` / `op_check` / `op_convert`).

### `op_measure` body

```python
def op_measure(instar_bin, instar_copy, qemu_copy, fmt,
               timeout, rng):
    """Run instar measure and compare to qemu-img measure (raw/qcow2)
    or instar convert's actual output size (vmdk/vpc/vhdx).
    """
    # Pick a target format.
    target_fmt = rng.choice(['raw', 'qcow2', 'vmdk', 'vpc', 'vhdx'])

    instar_args = ['-O', target_fmt, '--output', 'json',
                   str(instar_copy)]
    i_out, i_err, i_rc = run_instar(
        instar_bin, ['measure'], instar_args, timeout=timeout,
    )

    if target_fmt in ('raw', 'qcow2'):
        # qemu-img supports these targets — numeric comparison.
        qemu_args = ['-O', target_fmt, '--output=json',
                     str(qemu_copy)]
        q_out, q_err, q_rc = run_qemu_img(
            ['measure'], qemu_args, timeout=timeout,
        )

        div = compare_exit_codes(
            i_rc, q_rc, 'measure',
            {'target_format': target_fmt,
             'instar_stderr': i_err[:500],
             'qemu_stderr': q_err[:500]},
        )
        if div:
            return div
        if i_rc != 0:
            return None  # both failed, nothing to compare

        # Parse JSON, compare numeric fields.
        try:
            i_json = json.loads(i_out)
            q_json = json.loads(q_out)
        except json.JSONDecodeError as e:
            return {
                'type': 'measure_json_parse_failure',
                'target_format': target_fmt,
                'error': str(e),
                'instar_stdout': i_out[:500],
                'qemu_stdout': q_out[:500],
            }

        # `bitmaps` field comparison: both sides should agree on
        # presence (instar emits "bitmaps": 0 only for qcow2 v3
        # sources targeting qcow2; phase 7c verified parity).
        # required / fully-allocated must match exactly.
        for key in ('required', 'fully-allocated'):
            if i_json.get(key) != q_json.get(key):
                return {
                    'type': 'measure_numeric_divergence',
                    'target_format': target_fmt,
                    'field': key,
                    'instar_value': i_json.get(key),
                    'qemu_value': q_json.get(key),
                    'instar_stdout': i_out,
                    'qemu_stdout': q_out,
                }

        # Check the bitmaps field is consistent: emitted on
        # both sides or neither.
        i_has_bitmaps = 'bitmaps' in i_json
        q_has_bitmaps = 'bitmaps' in q_json
        if i_has_bitmaps != q_has_bitmaps:
            return {
                'type': 'measure_bitmaps_presence_divergence',
                'target_format': target_fmt,
                'instar_has_bitmaps': i_has_bitmaps,
                'qemu_has_bitmaps': q_has_bitmaps,
                'instar_stdout': i_out,
                'qemu_stdout': q_out,
            }

        return None

    # vmdk / vpc / vhdx: qemu-img can't measure, so self-consistency
    # against instar convert.
    if i_rc != 0:
        # measure failed; can't compare bounds. Not necessarily a
        # bug (e.g. unsupported source format).
        return None

    try:
        i_json = json.loads(i_out)
        required = i_json['required']
        fully_allocated = i_json['fully-allocated']
    except (json.JSONDecodeError, KeyError) as e:
        return {
            'type': 'measure_json_parse_failure',
            'target_format': target_fmt,
            'error': str(e),
            'instar_stdout': i_out[:500],
        }

    # Convert the instar copy to the target format.
    out_path = instar_copy.parent / f'{instar_copy.stem}-meas.{target_fmt}'
    conv_args = ['-O', target_fmt, str(instar_copy), str(out_path)]
    c_out, c_err, c_rc = run_instar(
        instar_bin, ['convert'], conv_args, timeout=timeout,
    )

    if c_rc != 0:
        # convert failed. Skip the bound check; some inputs are
        # genuinely unconvertible. measure-side success doesn't
        # imply convert-side success.
        return None

    actual = out_path.stat().st_size

    # One-output-sector cushion absorbs writer-side alignment
    # artefacts that measure doesn't model (e.g. VHD sector
    # padding flagged in phase 1e).
    cushion = 65536

    if actual < required - cushion:
        return {
            'type': 'measure_below_required_bound',
            'target_format': target_fmt,
            'measured_required': required,
            'measured_fully_allocated': fully_allocated,
            'convert_actual_size': actual,
            'cushion': cushion,
        }
    if actual > fully_allocated + cushion:
        return {
            'type': 'measure_above_fully_allocated_bound',
            'target_format': target_fmt,
            'measured_required': required,
            'measured_fully_allocated': fully_allocated,
            'convert_actual_size': actual,
            'cushion': cushion,
        }

    return None
```

### Dispatch wiring

In the existing `for op in ops:` loop (around line 732 of
`differential-fuzz.py`), add a new arm:

```python
elif op == 'measure':
    div = op_measure(
        instar_bin, instar_copy, qemu_copy, fmt,
        timeout, rng,
    )
```

### Known-divergence accommodation

Phase 7c documented five categories of scanner divergence
(raw SEEK_HOLE, qcow2 overcount on some images, qcow2
backing-chain blind, vhdx full-allocation, vmdk multi-extent,
vhd CHS rounding). The differential fuzzer's random-input
generator can hit any of these:

- **qcow2 → qcow2 with sparse raw input**: instar's raw
  scanner treats every byte as allocated; qemu-img uses
  SEEK_HOLE. instar's `required` will be ≥ qemu-img's.
- **vhdx → qcow2**: instar's vhdx scanner reports fully
  allocated; qemu-img reports actual block state.
- **vmdk → qcow2 with multi-extent**: instar's vmdk scanner
  doesn't propagate the extent map fully.

For phase 9, **report these as divergences** rather than
allowlist them. The point of the differential fuzzer is to
surface real disagreements; the phase 7c skip-list lives in
the test suite because the test suite must stay green, not
because the divergences are benign. The fuzzer's output
feeds the bug-tracking flow.

If the fuzzer becomes too noisy in nightly CI, add an
allowlist of `(source_format, target_format, attribute)`
triples to `KNOWN_DIVERGENCE_FIELDS` (the existing const at
the top of the script). Defer until we see runtime
behaviour.

### CI workflow

`.github/workflows/differential-fuzz.yml` invokes
`python3 scripts/differential-fuzz.py` with whatever
iteration count and seed the user / cron provides. The
script's internal OPERATIONS list expansion changes how
many iterations cover measure but doesn't change the
workflow interface. **No workflow edit needed.**

The workflow's auto-issue creation (existing) will surface
measure findings the same way it surfaces info / check /
convert findings.

## Open questions

1. **Should `op_measure` use the qemu_copy or instar_copy
   for both sides of the comparison?** The other op_* funcs
   use separate copies because some ops mutate the file
   (convert writes output, check reads-only but qemu-img
   could in principle update timestamps). Measure is
   read-only on both sides. Recommendation: use `instar_copy`
   for instar's invocation and `qemu_copy` for qemu-img's
   to keep the test-isolation invariant. Both files start
   from the same source (the existing
   `shutil.copy2(image_path, instar_copy)` / `qemu_copy`
   dance the fuzz loop already does).

2. **Should the round-trip half (vmdk/vpc/vhdx) run convert
   twice — once instar-side, once qemu-img-side — and
   compare the output sizes?** qemu-img convert produces a
   different file size than instar convert (different sparse
   strategy, alignment, etc.). The plan's
   `[required, fully_allocated]` bound is the right oracle
   — it doesn't depend on qemu-img convert. Skip the
   convert-output-size cross-comparison.

3. **`bitmaps` field handling**: instar emits `bitmaps: 0`
   only for qcow2 v3 sources targeting qcow2 (phase 7c).
   qemu-img has the same rule. The comparison code asserts
   that *presence* matches, then compares the numeric
   fields. If they disagree on presence, that's a divergence
   bug; if they disagree on the value (e.g. instar says 0 but
   qemu-img says 1 because the source had a real bitmap),
   that's also a bug — but instar's source scanner doesn't
   inspect bitmap metadata yet, so a non-zero qemu-img
   bitmap count would be a real divergence to file.
   Recommendation: compare the `bitmaps` value too when
   present.

4. **Cushion size**: phase 7d's round-trip tests use 65536
   (one output sector). For vhdx the 1 MiB block alignment
   may dominate. Recommendation: start with 65536; widen to
   `max(65536, block_size)` if the fuzzer produces
   spurious bound violations.

5. **Random target picker bias**: `rng.choice(['raw',
   'qcow2', 'vmdk', 'vpc', 'vhdx'])` weights every target
   equally. The qemu-img-validated half (raw/qcow2) gets
   2/5 of the picks. Acceptable. If we later want more
   qemu-img coverage, bias the choice manually.

6. **`convert_compressed` interaction**: the existing
   convert_compressed op picks compress=True for qcow2. The
   measure op doesn't model compression beyond the existing
   `--compress` flag (which doesn't change `required`).
   Recommendation: skip compression-related comparisons in
   measure; the existing convert_compressed op handles that
   axis.

7. **Source-format conditioning**: some manifest source
   formats produce surprising measure outputs (e.g. VHD
   source with broken CHS geometry). The fuzzer's random
   image generation uses `qemu-img create -f <fmt>` so
   inputs are valid by construction; non-trivial parser
   quirks come from the *data* the fuzzer writes, not the
   on-disk metadata. The phase 7c divergence list applies
   primarily to *real-world* images, not freshly-generated
   ones. Phase 9 may surface different divergences than
   phase 7c did; treat each as a new finding.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | medium | sonnet | none | Edit `scripts/differential-fuzz.py`: add `'measure'` to the `OPERATIONS` list at line 48; add the new `op_measure(instar_bin, instar_copy, qemu_copy, fmt, timeout, rng)` function per the "op_measure body" section above (place it next to `op_convert`); add the dispatch arm in the `for op in ops:` loop around line 732 (`elif op == 'measure': div = op_measure(...)`). Run `pre-commit run --all-files` and `python3 -m py_compile scripts/differential-fuzz.py` to confirm syntactic cleanliness. The fuzzer itself can be smoke-tested via `python3 scripts/differential-fuzz.py --iterations 5 --seed 42 --instar src/target/release/instar --log-dir /tmp/fuzz-logs --workdir /tmp/fuzz-work` (5 iterations to keep the run fast). If a divergence is found within the smoke window, capture the JSON report and decide whether it's a real bug (file an issue per the existing flow) or a known phase 7c category (add to the script's `KNOWN_DIVERGENCE_FIELDS` allowlist with a comment). Touch only `scripts/differential-fuzz.py`. |
| 9b | low | sonnet | none | Update `ARCHITECTURE.md`: the existing "Differential Fuzzing" subsection lists the operations the fuzzer covers (info, check, convert, convert_compressed). Add `measure` to that list and mention the dual oracle: numeric comparison vs qemu-img for raw / qcow2 targets, self-consistency `[required, fully_allocated]` bound vs instar convert for vmdk / vpc / vhdx. Add to `CHANGELOG.md` Unreleased / Added: "Differential fuzzer (`scripts/differential-fuzz.py`) now exercises `instar measure` as one of its random operations: numeric comparison against `qemu-img measure` for raw and qcow2 targets, self-consistency check against `instar convert` output size for vmdk / vpc / vhdx targets. Picked up automatically by the existing differential-fuzz workflow. (PLAN-measure-phase-09-fuzz-differential.md)". Run `pre-commit run --all-files`. Touch only `ARCHITECTURE.md` and `CHANGELOG.md`. |

Total: 2 commits.

## Out of scope for phase 9

- Phase 7c scanner divergences (treated as real findings by
  the fuzzer; not allowlisted preemptively).
- Tightening the round-trip cushion (defer to runtime data).
- libyal cross-validation for measure (the existing libyal
  flow covers info; libyal tools don't have a measure
  equivalent).
- Snapshot-related cases (`-l SNAPSHOT`); master-plan future
  work.
- Encrypted source measurement; master-plan future work.

## Success criteria

- `scripts/differential-fuzz.py` recognises `'measure'` as a
  valid operation.
- The 5-iteration smoke run completes without panic / parse
  error.
- Either no divergences in the smoke window, or any
  divergences found are recorded as new findings (issues
  filed or notes added) before commit.
- `python3 -m py_compile scripts/differential-fuzz.py`
  succeeds.
- `pre-commit run --all-files` passes.
- `ARCHITECTURE.md` and `CHANGELOG.md` updated.

## Risks and mitigations

- **Spurious "below required bound" reports** from the
  vmdk/vpc/vhdx round-trip cushion being too tight.
  Mitigation: 65536-byte cushion matches phase 7d's choice;
  if nightly runs produce spurious findings, widen to
  `max(65536, block_size)` in a follow-up.
- **Bitmaps presence false positives**: instar's
  `peek_is_qcow2_v3` gate (phase 7c) decides whether to
  emit. If the gate disagrees with qemu-img's actual
  emission rule on some edge case (e.g. a corrupted qcow2
  header), the fuzzer reports a divergence. Mitigation:
  report as a finding; it would be a real bug.
- **Random source-image generation hitting a phase 7c
  scanner divergence**: would surface as a measure_numeric_divergence.
  Mitigation: treat as a new finding because the generated
  image is fresh, not a manifest entry; phase 7c's skip-list
  doesn't apply preemptively. If patterns emerge (e.g. many
  failures pointing at sparse raw scanning), add a targeted
  allowlist.
- **JSON parse failure on instar output**: would indicate
  instar printed something unexpected. Mitigation: the
  fuzzer's existing error-reporting machinery captures the
  raw stdout; the new `measure_json_parse_failure` divergence
  type makes it easy to spot.

## Back brief

Before executing any step, the executing agent should
back-brief: which fuzzer file is being edited, where in it
the new operation is added, which existing op_* function is
the closest structural template, and how divergences are
reported back to the loop. The reviewer should verify the
script's existing `OPERATIONS` list expansion and the
divergence-reporting flow continue to work for the new op.
