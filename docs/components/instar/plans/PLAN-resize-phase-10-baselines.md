# PLAN-resize phase 10: cross-version baselines

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase and the `instar-testdata`
companion repo thoroughly. Read relevant source files,
understand existing patterns (the `generate-baselines.py`
command dispatch, the per-command `*_CASES` matrices, the
`expected-outputs/<output-type>/<format>/<version>/...` layout,
the create / measure precedents), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question
touches on external concepts (qemu-img CLI version differences,
posix preallocation semantics on different filesystems),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

This is a phase plan under `PLAN-resize.md`. Refer to that
master plan for overall context. Phases 1–9 shipped the
planner, the guest binary, the host CLI, and the preallocation
post-pass; phase 10 builds the cross-version baseline harness
that phase 11's integration tests will compare against.

## Mission

Extend `instar-testdata/scripts/generate-baselines.py` with a
`resize` command that, for every installed qemu-img version,
walks a curated matrix of `(target_format, options,
start_size, end_size, preallocation)` tuples and captures:

1. `qemu-img create -f FMT [-o KEY=VAL,…] <tmp> <start_size>`
2. `qemu-img resize -f FMT [--shrink] [--preallocation MODE] <tmp> [+-]<end_size>`
3. `qemu-img info --output=json <tmp>`

…as three artefacts per case in
`expected-outputs/resize-info-json/<target_format>/<version>/`:

- `<case_name>.stdout.txt` — the info JSON after resize.
- `<case_name>.stderr.txt` — create stderr, a `---RESIZE
  STDERR---` separator, resize stderr, a `---INFO STDERR---`
  separator, and info stderr (all paths normalised to
  `$FILENAME`).
- `<case_name>.meta.json` — every exit code, byte length,
  timing flag, and the originating options so phase 11 can
  filter without re-parsing the cases list.

The naming scheme follows the create precedent: descriptive
`<start>-to-<end>-<options>-prealloc-<mode>.{stdout,stderr,meta}`
case names, not opaque hashes. Phase 11 compares instar's post-
resize info JSON against `<case_name>.stdout.txt` for the
*matching qemu-img version*; mismatches outside the documented
divergence whitelist (vhdx file GUIDs, timestamps, tool-version
strings) fail the test.

The master plan's note about `(format, options, start_size,
end_size, preallocation)` hash keys is overridden by the
existing repo convention. Hash keys are illegible in
review; the descriptive form makes it obvious which case
regressed when a baseline changes.

## What the survey turned up

- **`instar-testdata/scripts/generate-baselines.py`** at
  ~1251 lines already has the dispatch shape phase 10 needs:
  `COMMANDS` dict at line 135 maps command-name → config
  (output_types + per-command extras like `create_cases`);
  `CREATE_CASES` at line 214 is the per-target case
  dictionary; `generate_create_baseline()` at line 743 is
  the closest precedent — it runs `create → info`, captures
  three artefacts per case, normalises absolute paths to
  `$FILENAME`, and tolerates non-zero exits as recorded
  baselines (older qemu rejecting `extended_l2=on` for
  example).  `main()`'s per-command branch at line 1018
  drives the loop.
- **`expected-outputs/create-info-json/<target>/<version>/`**
  shows the on-disk layout: 80 version dirs × per-target
  case lists × `{stdout,stderr,meta.json}` triples. Total
  ~20 MiB for create.
- **`Makefile`** at `instar-testdata/Makefile` exposes
  `baselines-{info,check,compare,measure}` targets; `baselines`
  is the umbrella. Notably there is **no `baselines-create`**
  target — create baselines are generated via direct script
  invocation today. Phase 10 adds `baselines-resize` and
  considers whether to backfill `baselines-create` (out of
  scope; defer).
- **qemu-img resize surface**. Verified by reading
  `qemu-img 6.0.0 resize` and `qemu-img 10.2.0 resize`
  help: `--preallocation` has been on resize since at least
  6.0.0 (our oldest shipped binary), and the syntax
  `[+-]SIZE[bkKMGTPE]` is stable across the whole range.
  No version-conditional case gating needed for the
  preallocation flag — older versions that reject a
  specific mode for a specific format just produce a non-zero
  exit, which the baseline records verbatim.
- **qemu-img cannot resize vmdk, vpc (vhd), or vhdx** on
  any version we ship. The driver responds `"Image format
  driver does not support resize"`. instar **does** support
  resize on all three. We still generate baselines for these
  formats — they record the rejection verbatim, document the
  capability gap, and act as a tripwire for the day qemu
  adds support. Phase 11 must handle the cross-tool
  asymmetry: for qcow2 + raw, diff instar's info JSON
  against qemu's matching baseline; for vmdk / vhd / vhdx,
  the baseline's `resize_return_code != 0` is the signal
  to fall back to internal consistency checks (instar
  resize → instar info → instar check) instead of a diff.
- **`COMMANDS['create']['output_types']`** is
  `{'create-info-json': 'json'}`. Phase 10 mirrors with
  `{'resize-info-json': 'json'}` — info-JSON is the only
  cross-version-comparable view (info-human embeds an
  absolute path; resize's own stdout is the trivial
  `"Image resized."` log line; neither is worth a
  baseline).

## Algorithmic design

### `RESIZE_CASES` matrix

Each entry is a 5-tuple:

```python
(case_name, start_size, end_spec, create_opts, prealloc)
```

- `case_name`: filename-safe identifier (`1M-to-64M-default`
  / `64M-to-1M-shrink` / `1M-to-4M-prealloc-full`).
- `start_size`: the initial `qemu-img create` size (`1M`,
  `64M`).
- `end_spec`: passed directly to `qemu-img resize`'s
  `[+-]SIZE` positional. Absolute (`64M`), additive
  (`+63M`), and subtractive (`-32M`) forms are all
  exercised — phase 11 will mirror the same end_spec onto
  `instar resize` so both tools see byte-identical CLI
  arguments.
- `create_opts`: list of `-o KEY=VAL` strings for the
  create step. Encoded into `case_name` for legibility.
- `prealloc`: `None` (no flag), `"off"`, `"metadata"`,
  `"falloc"`, or `"full"`. `None` and `"off"` are kept
  separate because qemu's stdout / stderr differs (off is
  explicitly logged; absent flag is silent) and we want to
  detect drift in either.

Disk economy is enforced by the same rules as create:
- `falloc` / `full` end sizes are capped at 4M so the
  generator footprint stays bounded (each `full` baseline
  materialises the full disk during generation).
- vhd `subformat=fixed` capped at 4M for the same reason
  (resize materialises blocks).
- All other cases use the 1M / 16M / 64M sweep.

#### qcow2 cases (~18)

```python
'qcow2': [
    # default grow sweep — covers L1 grow, refcount grow, neither
    ('1M-to-4M-default',              '1M',  '4M',   [],                       None),
    ('1M-to-64M-default',             '1M',  '64M',  [],                       None),
    ('64M-to-256M-default',           '64M', '256M', [],                       None),
    # cluster_size sweep at the L1-grow boundary
    ('1M-to-64M-cs-512',              '1M',  '64M',  ['cluster_size=512'],     None),
    ('1M-to-64M-cs-4k',               '1M',  '64M',  ['cluster_size=4k'],      None),
    ('1M-to-64M-cs-1M',               '1M',  '64M',  ['cluster_size=1M'],      None),
    # refcount sweep
    ('1M-to-64M-rb-1',                '1M',  '64M',  ['refcount_bits=1'],      None),
    ('1M-to-64M-rb-64',               '1M',  '64M',  ['refcount_bits=64'],     None),
    # extended_l2 (qemu-img >= 5.0)
    ('1M-to-64M-extended-l2',         '1M',  '64M',  ['extended_l2=on,cluster_size=64k'], None),
    # compat sweep
    ('1M-to-64M-compat-v2',           '1M',  '64M',  ['compat=0.10'],          None),
    # lazy_refcounts
    ('1M-to-64M-lazy-refcounts',      '1M',  '64M',  ['lazy_refcounts=on'],    None),
    # additive / subtractive end specs
    ('1M-plus-63M-default',           '1M',  '+63M', [],                       None),
    ('64M-minus-32M-shrink',          '64M', '-32M', [],                       None),  # --shrink applied by harness
    # preallocation modes (small caps for disk economy)
    ('1M-to-4M-prealloc-off',         '1M',  '4M',   [],                       'off'),
    ('1M-to-4M-prealloc-metadata',    '1M',  '4M',   [],                       'metadata'),
    ('1M-to-4M-prealloc-falloc',      '1M',  '4M',   [],                       'falloc'),
    ('1M-to-4M-prealloc-full',        '1M',  '4M',   [],                       'full'),
    # noop (size unchanged) — verifies the format-survives-noop path
    ('64M-to-64M-noop',               '64M', '64M',  [],                       None),
],
```

#### vhd cases (~6)

vhd grow only (shrink unsupported upstream and by phase 4).
Preallocation modes for vhd dynamic produce `qemu-img`
errors on most versions — we record those verbatim.

```python
'vhd': [
    ('1M-to-64M-default',             '1M',  '64M',  [],                       None),
    ('64M-to-256M-default',           '64M', '256M', [],                       None),
    ('1M-to-4M-fixed',                '1M',  '4M',   ['subformat=fixed'],      None),
    ('1M-plus-63M-default',           '1M',  '+63M', [],                       None),
    ('1M-to-4M-prealloc-off',         '1M',  '4M',   [],                       'off'),
    ('1M-to-4M-prealloc-full',        '1M',  '4M',   [],                       'full'),  # often rejected; records the rejection
],
```

#### vhdx cases (~5)

```python
'vhdx': [
    ('1M-to-64M-default',             '1M',  '64M',  [],                       None),
    ('64M-to-256M-default',           '64M', '256M', [],                       None),
    ('1M-to-64M-block-16M',           '1M',  '64M',  ['block_size=16M'],       None),
    ('1M-plus-63M-default',           '1M',  '+63M', [],                       None),
    ('1M-to-4M-prealloc-off',         '1M',  '4M',   [],                       'off'),
],
```

#### vmdk cases (~3)

monolithicSparse grow only. Other subformats are rejected
by both qemu and instar in resize mode (verified empirically
during phase 6).

```python
'vmdk': [
    ('1M-to-64M-default',             '1M',  '64M',  [],                       None),
    ('64M-to-256M-default',           '64M', '256M', [],                       None),
    ('1M-plus-63M-default',           '1M',  '+63M', [],                       None),
],
```

#### raw cases (~7)

raw is the most-used resize target in production. Sweep
every preallocation mode + grow + shrink.

```python
'raw': [
    ('1M-to-64M-default',             '1M',  '64M',  [],                       None),
    ('64M-to-256M-default',           '64M', '256M', [],                       None),
    ('1M-plus-63M-default',           '1M',  '+63M', [],                       None),
    ('64M-to-1M-shrink',              '64M', '1M',   [],                       None),  # --shrink applied by harness
    ('1M-to-4M-prealloc-off',         '1M',  '4M',   [],                       'off'),
    ('1M-to-4M-prealloc-falloc',      '1M',  '4M',   [],                       'falloc'),
    ('1M-to-4M-prealloc-full',        '1M',  '4M',   [],                       'full'),
],
```

**Total: ~39 cases × 80 qemu-img versions ≈ 3,120 baselines**
at ~1.5 KiB each ≈ ~5 MiB total. Well within the
testdata repo's expected footprint.

### New `generate_resize_baseline()` function

Modelled on `generate_create_baseline()` (lines 743–888 of
the existing script). Returns the same result-dict shape so
the `main()` loop's status reporting works uniformly.

```python
def generate_resize_baseline(
    binary: Path,
    version: str,
    case_name: str,
    start_size: str,
    end_spec: str,
    target_format: str,
    options_list: list,
    prealloc: Optional[str],
    output_dir: Path,
    tmp_dir: Path,
    timeout: int = 60,
) -> dict:
    """
    Generate one create → resize → info baseline.

    Pipeline:
      1. qemu-img create -f FMT [-o KEY=VAL,…] <tmp> <start_size>
      2. if (1) succeeded:
         qemu-img resize -f FMT [--shrink] [--preallocation MODE]
                         <tmp> <end_spec>
      3. if (2) succeeded:
         qemu-img info --output=json <tmp>
      4. write three artefacts:
         <case_name>.stdout.txt   = info JSON ('' if not run)
         <case_name>.stderr.txt   = create+resize+info stderr,
                                    separated by markers,
                                    paths normalised to $FILENAME
         <case_name>.meta.json    = exit codes + byte lengths

    The `--shrink` flag is added implicitly when end_spec is
    subtractive (`-N`) or when an absolute end_spec is smaller
    than start_size. The meta records the flag set so phase 11
    can mirror exactly.

    Always deletes the tmp file before returning, even on failure.
    """
```

Implementation notes:

- **`--shrink` inference**. The harness applies `--shrink`
  whenever the requested final size is smaller than the
  starting size. Computing this without parsing qemu's size
  grammar would be brittle, so we evaluate the start and
  end via Python: convert `start_size` and (for absolute /
  additive / subtractive `end_spec`) the resulting final
  size to bytes, compare. Subtractive end_spec implies
  shrink unconditionally.
- **Path normalisation**. Same as `generate_create_baseline`:
  the tmp file path appears in create's "Formatting '...'"
  log, resize's stderr (when it fails), and info's JSON
  `filename` field. Replace with `$FILENAME` so baselines
  are host-portable.
- **Combined stderr layout**:
  ```
  <create stderr lines>
  ---RESIZE STDERR---
  <resize stderr lines>
  ---INFO STDERR---
  <info stderr lines>
  ```
  Markers omitted for steps that didn't run (e.g. if create
  failed, no resize stderr block, no info marker).
- **Meta keys** (new additions beyond create's set):
  - `start_size_str`, `end_spec`, `prealloc` (raw inputs)
  - `applied_shrink_flag` (bool: whether `--shrink` was
    passed to resize)
  - `resize_return_code`, `resize_stdout_bytes`,
    `resize_stderr_bytes`, `resize_timed_out`
  - existing `create_*` and `info_*` keys
- **End-spec passing**. `end_spec` is passed verbatim as
  the resize positional argument (no escaping required;
  `+`/`-` are safe in argv).

### `COMMANDS['resize']` entry

```python
'resize': {
    'output_types': {
        'resize-info-json': 'json',
    },
    'targets': ['qcow2', 'vmdk', 'vhd', 'vhdx', 'raw'],
    # RESIZE_CASES is defined below; reference is patched in
    # after definition.
    'resize_cases': None,
},
```

…with the corresponding patch line `COMMANDS['resize']['resize_cases']
= RESIZE_CASES` after `RESIZE_CASES` is defined, matching
create's pattern.

### `main()` dispatch branch

Insert an `elif command_name == 'resize':` block parallel to
the existing `if command_name == 'create':` block. The body
is structurally identical to create's:

```python
elif command_name == 'resize':
    targets = command_config['targets']
    resize_cases = command_config['resize_cases']

    import tempfile
    tmp_root = Path(tempfile.mkdtemp(prefix=f'resize-baselines-{version}-'))

    try:
        for output_type_name, _ in output_types.items():
            print(f'  Output type: {output_type_name}')

            for target in targets:
                target_dir = (
                    output_root / output_type_name / target / version
                )
                target_dir.mkdir(parents=True, exist_ok=True)

                for case in resize_cases.get(target, []):
                    case_name, start_size, end_spec, opts, prealloc = case
                    total += 1
                    result = generate_resize_baseline(
                        binary, version, case_name, start_size, end_spec,
                        target, opts, prealloc, target_dir, tmp_root,
                    )
                    label = f'{target}/{case_name}'
                    # (identical status reporting to create's block)
    finally:
        # Best-effort tmp cleanup (same as create's block).
        ...
```

### Commit-handling tweak

The existing commit block at line 1188 needs `resize`
added alongside `measure` / `create` so the per-format
subdirs under `expected-outputs/resize-info-json/` get
`git add`ed:

```python
if command_name in ('measure', 'create', 'resize'):
    type_dir = output_root / output_type_name
else:
    type_dir = output_root / output_type_name / 'raw'
```

And the `--no-commit` instructions:

```python
elif command_name == 'resize':
    print(
        f'To commit manually: '
        f'git add {output_root}/resize-* && git commit'
    )
```

### Makefile target

Add `baselines-resize` to `instar-testdata/Makefile`,
mirroring `baselines-create` (which is itself missing — see
"Open questions"):

```make
baselines-resize:
	$(SCRIPTS)/generate-baselines.py --command resize --no-commit
```

Phase 10 does **not** wire `baselines-resize` into the
umbrella `baselines:` target. Resize baselines materialise
real disk blocks on every `full` case for every version
(~80 × 7 raw cases × 4 MiB ≈ 2 GiB transient peak) — the
same reason `baselines-create` was kept out. The umbrella
remains the lightweight cross-version smoke set; resize is
on-demand.

## Test surface

- **Schema regression**: pick one version (e.g. `10.2.0`)
  and re-run `generate-baselines.py --command resize
  --version 10.2.0` after the change. `git diff
  expected-outputs/resize-info-json/` should show only the
  newly added files; no spurious modifications to other
  output types.
- **Path normalisation**: spot-check three of the generated
  `.stdout.txt` files (one per format kind: qcow2, raw,
  vhdx) and confirm no absolute paths leak. The `$FILENAME`
  substitution covers the create log, resize stderr, and
  info JSON's `filename` field.
- **Non-zero exit handling**: at least one case in each
  format (vhd `prealloc-full`, qcow2 `prealloc-falloc` on
  older versions) should produce a non-zero qemu-img exit
  on at least some versions, and the meta should record
  the failure cleanly without the script aborting.
- **`--shrink` inference**: log the inferred shrink flag in
  the meta and verify the qcow2 `64M-to-1M-shrink` case
  has `applied_shrink_flag=True` while
  `1M-to-64M-default` has `applied_shrink_flag=False`.
- **Disk economy ceiling**: time the full pass for one
  version and confirm peak tmp-dir size stays under 100 MiB
  (no runaway full-prealloc cases).

End-to-end coverage (instar vs. qemu-img baseline diff) is
phase 11's job.

## Public API delta

None in `instar` itself. All changes are in `instar-testdata`:

- `scripts/generate-baselines.py` — additions only.
- `Makefile` — one new target.
- `expected-outputs/resize-info-json/` — new directory
  tree, committed in step 10b.

## Open questions

1. **Should `baselines-create` get a Makefile target too?**
   Today it's invoked by direct script call. Phase 10's
   `baselines-resize` follows the same off-umbrella
   convention. *Recommendation*: out of scope for phase 10
   — note in the testdata TODO.md.

2. **Should `end_spec` use a separate `expected_final_size`
   field in the meta?** qemu-img's size grammar is well-
   defined and `end_spec` is enough to reproduce the case.
   Adding `expected_final_size` would let phase 11 sanity-
   check without re-parsing. *Recommendation*: include it.
   Trivial to compute in Python (we already do it for the
   shrink inference) and it makes phase 11's tests more
   resilient to qemu changes.

3. **Should we baseline the `--shrink` failure mode**
   (i.e. resize down without the flag)? Yes — one case per
   shrink-capable format. The meta `resize_return_code`
   records the rejection; phase 11 compares instar's
   matching rejection. Adds 2 cases (qcow2 + raw); not
   listed in the matrix above. *Recommendation*: add
   `64M-to-1M-no-shrink` to qcow2 and raw.

4. **Should the resize stderr separator match the create
   stderr separator's `---INFO STDERR---` marker style?**
   Yes, for consistency. The create script's marker is
   exactly `---INFO STDERR---` (no surrounding blank line
   in `combined_stderr`). Use `---RESIZE STDERR---` and
   `---INFO STDERR---` in that order.

5. **Version-conditional cases**. `extended_l2` requires
   qemu-img ≥ 5.0; `compression_type=zstd` requires ≥ 5.1.
   We don't need to gate the matrix — older qemu rejects
   the option during create, the baseline records the
   non-zero exit, phase 11 skips the comparison if the
   matching version's baseline has a non-zero create rc.
   Same policy as the existing create matrix.

6. **Tmp dir on tmpfs?** Generating 80 versions ×
   ~39 cases × creating files locally could hit the
   default `tempfile.mkdtemp` location (often `/tmp`,
   often tmpfs). For `prealloc-full` cases the disk
   blocks become RAM blocks. The capped 4M ceiling keeps
   peak RAM under ~300 MiB across all cases for a single
   version, which is fine. *Recommendation*: leave as-is;
   add a `--tmp-dir` flag to the generator only if real
   users hit the ceiling.

7. **Per-version generation time**. Each resize baseline
   ≈ 0.3 s. 39 cases × 80 versions ≈ 1000 s ≈ 17 minutes
   end-to-end on the existing testdata host. Comparable to
   the existing `baselines-measure` pass. *Recommendation*:
   no parallelism in step 10b — keep generation
   deterministic.

8. **Case-list bikeshed**. The 18-case qcow2 list above is
   close to the create baseline's 17 cases — same
   `cluster_size` / `refcount_bits` / `compat` / `lazy`
   axes, plus shrink-specific entries. We could expand by
   re-checking every `(cluster_size, end_size)` pair; for
   v1, the curated list is enough to exercise both L1-grow
   and refcount-grow paths. Phase 12's differential fuzzer
   covers the long tail.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 10a | medium | sonnet | worktree | Extend `instar-testdata/scripts/generate-baselines.py` with a `resize` command per the design above. Add `RESIZE_CASES` per the matrix in §Algorithmic design (39 cases + 2 shrink-without-flag rejection cases). Add `generate_resize_baseline()` modelled on `generate_create_baseline()` (existing function at line 743), including the `--shrink` auto-inference (compute final byte size, compare to start; subtractive end_spec implies shrink). Add the `elif command_name == 'resize':` branch in `main()` mirroring the create branch (line 1018). Update the commit / no-commit handling to include `'resize'` in the `(measure, create, resize)` tuple. Add a `baselines-resize` target to `instar-testdata/Makefile` per the design. Smoke-test against a single qemu-img version: `python3 scripts/generate-baselines.py --command resize --version 10.2.0 --no-commit`. Verify all three artefacts exist for every case, that `$FILENAME` substitution worked (`grep -r /tmp expected-outputs/resize-info-json/<format>/10.2.0/` returns empty), and that meta.json captures the three return codes + the inferred `applied_shrink_flag`. Commit the script + Makefile changes; **do not** commit any generated baselines yet — that's step 10b. |
| 10b | low | sonnet | none | Run the full baseline pass: `cd instar-testdata && make baselines-resize`. Generation is mechanical but long (~17 min wall-clock for 80 versions × ~41 cases). Watch the run for warnings/timeouts. After completion, `git diff --stat expected-outputs/resize-info-json/` should show ~3000 new files at ~5 MiB total. Spot-check three baselines (one qcow2, one raw, one vhdx) against running qemu-img manually to confirm round-trip fidelity. Commit the generated baselines with a single commit message summarising versions covered. |

## Out of scope for phase 10

- Phase 11's integration-test harness that diffs instar's
  resize output against these baselines.
- Backfilling a `baselines-create` Makefile target.
- Per-format data-region preallocation parity (master-plan
  Future work — phase 9 already documented the divergence).
- Pruning the qemu-img-version sweep (we currently baseline
  every installed version; if the testdata repo's overall
  footprint becomes a problem we'd add a "interesting
  versions" subset, but not in this phase).
- Adding `resize` to the umbrella `baselines:` target.
- Adding `--tmp-dir` / parallelism flags to the generator.

## Success criteria for phase 10

- `instar-testdata/scripts/generate-baselines.py --command
  resize --version <VERSION>` completes successfully against
  the latest installed qemu-img version with no errors.
- The generated tree under
  `expected-outputs/resize-info-json/` is well-formed:
  every case has the `{stdout,stderr,meta.json}` triple,
  no absolute paths leak, and meta records every exit
  code + `applied_shrink_flag` + `expected_final_size`.
- `baselines-resize` Makefile target works end-to-end
  (single-version smoke + multi-version full pass).
- The full pass produces ~3000 baselines totalling
  ~5 MiB; tmp peak stays under 300 MiB.
- The script change is review-ready: structurally
  parallels the create branch, no copy-paste drift.

## Sub-agent guidance

Read these files before starting any step:

- `instar-testdata/scripts/generate-baselines.py` (the
  whole file — phase 10 mirrors create's pattern at every
  level: the `COMMANDS` dict entry, the `*_CASES` matrix,
  the `generate_*_baseline()` function, the `main()`
  dispatch branch, the commit handling).
- `instar-testdata/expected-outputs/create-info-json/qcow2/10.0.0/`
  (sample the existing layout — the `{stdout,stderr,
  meta.json}` triple per case).
- `instar-testdata/Makefile` (the `baselines-*` targets).
- `docs/plans/PLAN-resize.md` §"Versioning and baseline
  strategy" (the master plan's framing).
- `docs/plans/PLAN-resize-phase-09-preallocation.md`
  (because the matrix exercises every preallocation mode
  the host CLI now supports).

The management session review checklist is the same as
prior phases: a per-step `git diff` review, a one-version
smoke before committing the matrix, the multi-version
pass as a long-running step monitored but not babysat.

Coordinate with the testdata repo's commit policy: each
step commits in its own repo (the instar repo for the
plan file; the instar-testdata repo for the script + the
generated baselines). The plan file commits land in this
worktree (the `resize` branch in `instar-wt-resize`); the
testdata changes land on testdata's `main`. Push order
doesn't matter — phase 11 is what binds them together.
