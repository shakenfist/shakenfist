# PLAN-rebase-commit phase 09: commit integration tests + baselines

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase thoroughly. Read the existing
phase 8 commit smoke tests in `tests/test_commit.py`, the
phase 5 rebase tests + baseline matrix in `tests/test_rebase.py`
(the structural twin), the `assert_info_equivalent` helper in
`tests/helpers/info_json.py`, the cross-version baseline
generator at
`/srv/kasm_profiles/mikal/vscode/src/shakenfist/instar-testdata/scripts/generate-baselines.py`
(`generate_rebase_baseline`, the `REBASE_CASES` table, the
`COMMANDS['rebase']` dispatch entry, and the per-version loop
in `main`), the recorded baselines at
`instar-testdata/expected-outputs/rebase-info-json/qcow2/<version>/`
for the artefact shape (`<case>.stdout.txt`,
`<case>.stderr.txt`, `<case>.meta.json`), and the master plan
at `docs/plans/PLAN-rebase-commit.md`. Ground your answers in
what the code actually does today.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-rebase-commit-phase-NN-<descriptive>.md`. The master
plan is [PLAN-rebase-commit.md](/components/instar/plans/PLAN-rebase-commit/). This
phase is the ninth of twelve.

I prefer one commit per logical step. The step table below
identifies four steps; this phase can land step by step or as
a single consolidated commit.

## Situation

Phase 8 step 8e already shipped the smoke layer for
`instar commit` — `tests/test_commit.py` carries
`TestCommitErrorPaths` (five host-side rejection contracts)
and `TestCommitSuccessPaths` (qcow2 implicit `-b`, qcow2
explicit `-b`, `-q` suppresses success line, JSON envelope
shape, and a vmdk smoke gated as `skipTest`). Those are the
structural twins of phase 5's 5a–5c work.

Phase 9 adds the cross-version comparison layer on top of
the smoke tests — the structural twin of phase 5's 5d–5f
work:

1. **Baseline generation** in `instar-testdata` mirroring
   `generate_rebase_baseline`: a new `generate_commit_baseline`
   that builds backing + overlay fixtures, optionally seeds
   the overlay with a known sector pattern, runs `qemu-img
   commit`, and records both the post-commit overlay info JSON
   and the post-commit backing info JSON. Cases tagged for
   qcow2 and vmdk (the two formats qemu-img commit supports).

2. **`TestCommitBaselineMatrix`** in `tests/test_commit.py`:
   factory-generated test per `(target, case)` pair that
   builds the same fixtures the generator built, runs `instar
   commit`, then asserts the post-commit info JSON (overlay
   and backing) matches the version-pinned baseline via
   `assert_info_equivalent`. Mirrors `TestRebaseBaselineMatrix`
   exactly.

3. **`TestCommitRoundTrip`** in `tests/test_commit.py`: for
   every supported `(format, case)` pair, build two byte-
   identical overlay+backing pairs, commit one with `instar
   commit` and the other with `qemu-img commit`, then assert
   the resulting info JSONs (both overlay and backing) are
   equivalent after the whitelist normalisation. Mirrors
   `TestRebaseRoundTrip`.

4. **Wrap-up**: master plan updated to mark phase 9 complete
   with the shipping commit hashes, and the phase plan's
   "Future work" / "Bugs fixed" sections filled in with
   anything that surfaced.

The relevant infrastructure this phase builds on:

- **`instar-testdata` generator** (`scripts/generate-baselines.py`).
  Already has the `COMMANDS` dispatch table, the per-version
  loop in `main`, the `--command` CLI arg, the path-
  normalisation helpers (`_normalise` substituting `$BASE` /
  `$NEXT` / `$FILENAME`), the cleanup convention, and the
  artefact triple (`<case>.stdout.txt`, `<case>.stderr.txt`,
  `<case>.meta.json`). Phase 9 adds a new
  `'commit'` entry to `COMMANDS`, a new `COMMIT_CASES` table,
  a new `generate_commit_baseline` function modelled on
  `generate_rebase_baseline`, and a per-case branch in the
  generator's main loop.

- **`tests/test_rebase.py` matrix machinery**. The
  `TestRebaseBaselineMatrix` class with `_baseline_root`,
  `_baseline_version_dir`, `_baseline_stdout`,
  `_baseline_meta`, the `_make_rebase_baseline_test` factory,
  and the cross-baseline drift audit
  (`test_rebase_cases_match_baselines`). Phase 9 ports the
  same shape to a `TestCommitBaselineMatrix` class — one
  module-level mirror of `COMMIT_CASES`, one factory, one
  drift audit, one factory loop at module bottom.

- **`assert_info_equivalent`** in `tests/helpers/info_json.py`.
  Normalises across qemu-img versions (whitelist of fields
  that differ, `tmp_path` rewriting). Used unchanged.

- **Phase 8 commit guest + host CLI**. Working today:
  `instar commit FILENAME` and `instar commit -b BASE
  FILENAME` both succeed against fresh qcow2 fixtures
  (`b7dc9c7` fixed the output-bounce bug). The vmdk implicit-
  `-b` resolution path is gated by the info-vmdk-backing-file
  follow-up — phase 9's vmdk baseline cases use explicit `-b`
  to sidestep this, matching the workaround the smoke test
  uses.

## Mission and problem statement

After phase 9 lands:

1. `instar-testdata/scripts/generate-baselines.py` carries:
   - A `COMMIT_CASES` table keyed by target format (`qcow2`,
     `vmdk`) with curated case shapes (see open question 2).
   - A `generate_commit_baseline` function that runs the
     full create→seed→commit→info pipeline and records the
     standard artefact triple.
   - A new `'commit'` entry in `COMMANDS` with
     `output_types={'commit-overlay-info-json': 'json',
     'commit-backing-info-json': 'json'}`, `targets=['qcow2',
     'vmdk']`, and a `commit_cases` reference.
   - A `--command commit` dispatch arm in `main` that loops
     over `(target, version, case)` and calls
     `generate_commit_baseline`.

2. `tests/test_commit.py` carries:
   - A module-level `COMMIT_CASES` table that mirrors the
     generator's. The drift audit
     `test_commit_cases_match_baselines` catches divergence.
   - `TestCommitBaselineMatrix` with `_baseline_root`,
     `_baseline_version_dir`, `_baseline_overlay_stdout`,
     `_baseline_backing_stdout`, `_baseline_meta` helpers.
   - One factory `_make_commit_baseline_test` and a
     module-bottom loop that produces one test method per
     `(target, case)`.
   - `TestCommitRoundTrip` with `_assert_round_trip` driver
     and per-format `_qcow2_overlay` / `_vmdk_overlay`
     fixture factories.

3. The recorded baselines live at
   `instar-testdata/expected-outputs/commit-overlay-info-json/<target>/<version>/`
   and
   `instar-testdata/expected-outputs/commit-backing-info-json/<target>/<version>/`.
   Each carries `<case>.stdout.txt`, `<case>.stderr.txt`,
   and `<case>.meta.json`.

4. `make instar` builds clean, `make lint` is clean,
   `pre-commit run --all-files` is clean, `make test-rust`
   passes, and `make test-integration tests/test_commit.py`
   runs the smoke + matrix + round-trip suite (success-path
   tests are skipTest'd cleanly when KVM is unavailable
   inside CI; matrix tests skip when the local qemu-img
   version doesn't match any recorded baseline).

5. The execution-table row for phase 9 in
   `PLAN-rebase-commit.md` is marked Complete with the
   shipping commit hashes.

## Open questions

### 1. Should we record both overlay AND backing info JSONs?

**Yes.** A commit's observable state lives in *both* files
after the operation:

- The overlay's L2 + refcount entries are zeroed (its
  `actual-size` shrinks).
- The backing's clusters are populated with the overlay's
  data (its `actual-size` grows; its allocated-clusters list
  changes).

Recording only one side leaves the other invariant
unverified. The baseline tree therefore carries two parallel
output buckets:
`expected-outputs/commit-overlay-info-json/...` and
`expected-outputs/commit-backing-info-json/...`. The matrix
tests assert both.

Cost: 2× artefacts per case. The recorded JSONs are small
(<1 KB each), so the total disk + git churn is bounded.

### 2. What's the `COMMIT_CASES` matrix shape?

Working choice — mirror rebase's shape but specialise for
commit's semantics:

```python
COMMIT_CASES = {
    'qcow2': [
        # Empty-overlay cases: every L2 entry is zero.
        # The guest walks L1, finds nothing, reports
        # clusters_committed=0. Both backing and overlay
        # should be byte-identical to their pre-commit state
        # (modulo qcow2 metadata writes the planner emits).
        ('1M-empty-implicit',  '1M',  None),
        ('1M-empty-explicit',  '1M',  'base.qcow2'),
        ('64M-empty-implicit', '64M', None),

        # Seeded overlay: write a known 64 KiB pattern at
        # offset 0 via `qemu-io -c 'write -P 0xab 0 64k'`
        # before the commit. The pattern lands in the
        # backing at the same offset after commit; the
        # overlay's first L2 slot is zeroed.
        ('1M-seeded-implicit',  '1M',  None,         'seed-64k'),
        ('1M-seeded-explicit',  '1M',  'base.qcow2', 'seed-64k'),
        ('64M-seeded-implicit', '64M', None,         'seed-64k'),
    ],
    'vmdk': [
        # vmdk monolithicSparse: implicit -b resolution is
        # gated by the info-vmdk-backing-file follow-up, so
        # every vmdk case uses an explicit -b for the
        # baseline matrix. (Round-trip tests use the same
        # workaround.)
        ('1M-empty-explicit',  '1M',  'base.vmdk'),
        ('1M-seeded-explicit', '1M',  'base.vmdk', 'seed-64k'),
    ],
}
```

Tuple shape: `(case_name, overlay_size, explicit_base_or_None,
[seed_spec])`. `explicit_base_or_None=None` means implicit
`-b`. `seed_spec='seed-64k'` means write a 64 KiB `0xab`
pattern at offset 0 via `qemu-io` before the commit; absent
means leave the overlay empty.

vmdk has only 2 cases (vs 6 for qcow2) because the implicit-
`-b` case is blocked and the cluster-size dimension doesn't
apply.

### 3. How do we seed the overlay with known data?

Working choice: **`qemu-io -c 'write -P 0xab 0 64k' overlay`**.
qemu-io ships with qemu-img in every shipped version
6.0.0..10.2.0. Records as a single sub-step in
`generate_commit_baseline` between the create and commit
steps. If `qemu-io` is missing or the write fails, the meta
records the failure and the matrix test skips that case.

Alternative considered: `dd if=/dev/urandom`. Rejected — the
pattern needs to be deterministic so the comparison is
stable across runs.

Alternative considered: `qemu-img dd`. Rejected — the
phase-8 instrumentation work surfaced that `qemu-img dd`
truncates the overlay to a raw file in some versions.

### 4. Cross-repo coordination with `instar-testdata`

Same pattern as phase 5: the baseline generation lands in
the sibling `instar-testdata` repo, *not* in `instar`. The
generator runs once locally to produce all
`(version × target × case)` artefacts. The artefacts are
then committed to `instar-testdata` and pulled into instar's
CI via the existing testdata download/cache machinery.

Open subquestion: does the existing CI matrix-test
infrastructure need updates to find the new
`commit-overlay-info-json` / `commit-backing-info-json`
buckets?

Working choice: **no.** The matrix tests resolve paths from
`<testdata>/expected-outputs/<bucket>/<target>/<version>/...`
at test runtime; the bucket names just need to exist in
the resolved tree. The testdata download/cache machinery
walks `expected-outputs/` recursively, so new top-level
buckets land automatically.

### 5. Round-trip: compare info JSON or raw bytes?

Working choice: **info JSON, both overlay and backing.**

Raw-byte comparison is overspecific: qcow2 metadata layout
choices (refcount block ordering, cluster ordering within a
new L2 table, header extension byte ordering) can legitimately
differ between writers without affecting observable
semantics. instar and qemu-img don't claim byte-identical
output; they claim equivalent observable state.

The two info JSONs (overlay + backing) cover:
- Format / virtual size / cluster size (sanity).
- Allocated-clusters count (instar wrote the same data).
- backing-filename (instar preserved the chain or didn't,
  consistent with qemu-img's choice).
- dirty / corrupt flags (commit left the file in a clean
  state).

That's the observable contract we care about. Raw-byte
divergence is acceptable.

### 6. JSON normalisation for commit output

Reuse `assert_info_equivalent` unchanged. The whitelist
already covers `actual-size`, timestamps, qemu-version
strings, and absolute path rewriting. No commit-specific
fields need new entries.

The `tmp_path` arg gets the overlay path; the matrix test
also substitutes `$BASE` (the backing path) before passing
the strings to `assert_info_equivalent`, matching the rebase
matrix's pattern.

### 7. Skipping when KVM isn't available

The success-path tests (smoke, matrix, round-trip) all need
`/dev/kvm`. Working choice: **let them fail loud rather
than auto-skip.** CI runs with KVM; local-dev runs without
KVM fail clearly. The smoke tests in 8e follow this
convention; phase 9 matches.

(Phase 5's matrix tests do skip cleanly when the recorded
baseline's `*_return_code` is non-zero, but not on KVM
absence. Phase 9 inherits both behaviours.)

### 8. Manifest hash drift

`instar-testdata` ships a manifest with hashes of every
recorded artefact. Adding new commit baselines bumps the
manifest. Working choice: **regenerate the manifest in the
same commit as the new baselines** so a single PR carries
both. The generator's existing manifest-update hook covers
this if we use the same per-command dispatch arm; if not,
add a one-line manifest regen step to the wrap-up.

### 9. -d (drop) and -p (progress) — defer

The master plan defers `-d` and `-p`; phase 9's matrix
doesn't exercise them. If a future plan ships `-d`, the
baseline cases can be extended trivially (one new tuple
field).

## Execution

The phase plan recommends four steps. Each step is small
enough to review independently; consolidating into one or
two commits at the end is also fine.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | high | opus | none | Extend `instar-testdata/scripts/generate-baselines.py` with `COMMIT_CASES`, `generate_commit_baseline`, the `'commit'` entry in `COMMANDS` (`output_types={'commit-overlay-info-json': 'json', 'commit-backing-info-json': 'json'}`, `targets=['qcow2', 'vmdk']`), and the `main` dispatch arm. Generate the full matrix (every shipped qemu-img version × qcow2/vmdk targets × COMMIT_CASES). Commit the script change + the generated baselines to `instar-testdata` as two commits (mirroring phase 5's `c10c499d9` script + `3e9c11f3b` baselines split). Update the testdata manifest. |
| 9b | high | opus | none | `TestCommitBaselineMatrix` in `tests/test_commit.py`: module-level `COMMIT_CASES` mirror, `_baseline_root` / `_baseline_version_dir` / `_baseline_overlay_stdout` / `_baseline_backing_stdout` / `_baseline_meta` helpers, `test_commit_cases_match_baselines` drift audit, `_make_commit_baseline_test` factory, and the module-bottom factory loop. Each generated test builds the same fixtures the generator built (via `qemu-img create` + optional `qemu-io` seed), runs `instar commit`, then asserts both the overlay and backing post-commit info JSONs match the version-pinned baselines. Mirrors `TestRebaseBaselineMatrix` shape line-for-line. |
| 9c | medium | sonnet | none | `TestCommitRoundTrip` in `tests/test_commit.py`: `_assert_round_trip` driver that builds byte-identical overlay+backing pairs A and B, runs `instar commit` on A and `qemu-img commit` on B with matching flags, then compares both the overlay and backing info JSONs via `assert_info_equivalent`. Plus `_qcow2_overlay` and `_vmdk_overlay` fixture factories. Mirrors `TestRebaseRoundTrip` shape. |
| 9d | low | sonnet | none | Pre-commit clean. Master plan updated to mark phase 9 complete with shipping commit hashes (instar-side + testdata-side). Document anything that surfaced during 9a–9c in this plan's "Future work created by this phase" / "Bugs fixed" sections. |

## Agent guidance

### Execution model

Same model as phases 1–8: implementation work runs in the
management session unless explicitly delegated. The model
guidance in the step table reflects what a sub-agent would
need *if* this work were delegated; the management session
should also use opus when working on steps 9a and 9b because
the cross-repo coordination + factory machinery + matrix
plumbing benefits from the larger context.

### Planning effort

The master plan flagged this phase as **medium effort**.
Within the phase, 9a is high (the generator is the
load-bearing change and the baseline generation takes a few
minutes per version × case across 80 versions); 9b is high
(the factory + JSON comparison is fiddly); the rest are
medium-low.

### Step ordering

Strict dependency: 9a → 9b → 9c → 9d. 9c can interleave with
9b since they touch different classes, but the natural review
order is 9b (matrix — consumes 9a's baselines) then 9c
(round-trip — doesn't depend on 9a but reuses the fixture
factories 9b sets up).

### Management session review checklist

After each step:

- [ ] The files that were supposed to change actually
      changed.
- [ ] No unrelated files modified.
- [ ] `make instar` builds, `make lint` is clean.
- [ ] `make test-rust` passes (the existing tests
      shouldn't regress).
- [ ] `pre-commit run --all-files` clean.
- [ ] For 9a: the generator's `--command commit` dispatch
      produces non-empty `stdout.txt` files for every
      `(version, target, case)` triple where qemu-img
      commit succeeds. Cases where qemu-img rejects (e.g.
      qcow2 features unsupported in older versions) record
      meta with non-zero return code and an empty stdout
      — the matrix test skips those cases.
- [ ] For 9b: the drift audit
      (`test_commit_cases_match_baselines`) passes,
      catching any future divergence between the
      `COMMIT_CASES` mirror and the on-disk baselines.
- [ ] For 9c: round-trip tests pass on qcow2 cases; vmdk
      round-trips are skipped with a clear message until
      the implicit-`-b` follow-up lands.

## Administration and logistics

### Success criteria

Phase 9 is complete when:

* The `instar-testdata` repo carries the new
  `commit-overlay-info-json` and `commit-backing-info-json`
  buckets, populated for every shipped qemu-img version ×
  COMMIT_CASES entry.
* `tests/test_commit.py` carries `TestCommitBaselineMatrix`
  and `TestCommitRoundTrip` and the drift audit.
* `make instar`, `make lint`, `make test-rust`,
  `pre-commit run --all-files`, and
  `make test-integration tests/test_commit.py` all pass.
* The execution-table row for phase 9 in
  `PLAN-rebase-commit.md` is marked Complete with the
  shipping commit hashes.

### Future work created by this phase

Anticipated; the implementation may surface more.

- **Implicit-`-b` resolution for vmdk**. The current vmdk
  baseline cases all use explicit `-b` because the host
  info operation doesn't expose vmdk monolithicSparse's
  `parentFileNameHint` via `backing_file`. Once that's
  fixed (tracked separately under PLAN-info's vmdk follow-
  ups), add `vmdk` implicit-`-b` cases to `COMMIT_CASES`
  and the round-trip suite, and drop the skipTest gate in
  the existing vmdk smoke test from phase 8e.
- **Intermediate-image commit baselines**. When the
  intermediate-image commit deferred work from phase 8
  lands, extend `COMMIT_CASES` with deep-chain entries
  (`overlay → intermediate → base`, `-b base.qcow2`).
- **Backing byte-for-byte invariants**. The matrix
  currently records info JSON only. If a future plan wants
  a tighter "instar commit produced byte-identical backing
  bytes to qemu-img commit" assertion, the generator could
  additionally record a SHA-256 of the post-commit backing
  file. Out of scope for v1 — info JSON is the right
  contract.

### Bugs fixed during this work

- **Per-case subdirectory in the generator's fixture build**
  (`instar-testdata/scripts/generate-baselines.py`, shipped
  in `1f2cc83b1`). qemu-img commit's `-b BASE` flag walks
  the chain and compares BASE against each entry's
  canonicalised path. With the rebase generator's case-
  name-in-filename convention (`{target}-{case_name}-base.qcow2`),
  no `-b basename` value could ever match the chain entry's
  canonicalised path because the chain entry stores the same
  case-name-prefixed filename. The fix carves a per-case
  subdirectory (`{target}-{case_name}/`) so the backing
  can be named `base.<ext>` verbatim; `-b base.qcow2`
  canonicalised against `cwd=case_dir` then matches the chain
  entry canonicalised against the overlay's directory (both
  resolve to the same absolute path). Per-case isolation also
  prevents cross-case file collisions inside the shared
  `tmp_dir`.

### Vmdk matrix + round-trip tests gated as skipTest

The vmdk matrix and round-trip tests skipTest when
`instar commit` returns non-zero. Root cause is the same
info-vmdk-backing-file gap the phase 8e smoke test gates on:
instar's host pre-check refuses every explicit `-b` for
vmdk because the host info operation doesn't expose vmdk
monolithicSparse's `parentFileNameHint` via `backing_file`,
and the resolved-`-b`-against-recorded-parent comparison in
`run_commit` therefore concludes the user is naming a new
(non-parent) backing. The vmdk baselines and round-trip
fixtures are still recorded — they'll start passing
unchanged once the info-vmdk follow-up lands.

### Documentation index maintenance

Not added to `docs/plans/order.yml` — phase plans live
alongside the master plan but only the master plan is
indexed.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
