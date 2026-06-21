# PLAN-amend phase 07: cross-version baselines

## Prompt

Before responding to questions or discussion points in this
document, explore both repos thoroughly. Read the baseline
generator (`instar-testdata/scripts/generate-baselines.py` — its
`CREATE_CASES`/`RESIZE_CASES`, `generate_create_baseline`/
`generate_resize_baseline`, the `COMMANDS` dispatch, the
`qemu-img-binaries/` matrix loop), the `expected-outputs/` layout,
the consuming baseline-matrix test in `tests/test_resize.py`, the
info-JSON normaliser (`tests/helpers/info_json.py`), and the
`instar-testdata` Makefile. Ground every claim in what the code
actually does. Where a question touches `qemu-img amend`'s CLI or
the per-version `qemu-img info --output=json` shape, confirm
against the binaries. Flag uncertainty rather than guessing.

Phase plans live in `docs/plans/` named
`PLAN-amend-phase-NN-<descriptive>.md`. The master plan is
[PLAN-amend.md](/components/instar/plans/PLAN-amend/); phases 1–6 are landed and
`tests/test_amend.py` already does the *live* differential against
the installed qemu-img. This is the seventh of nine.

**This phase spans TWO repos.** The baseline generator and the
generated `expected-outputs/` live in the separate testdata repo
`/srv/kasm_profiles/mikal/vscode/src/shakenfist/instar-testdata`
(remote `git@gitlab.home.stillhq.com:private/instar-testdata.git`,
default branch `main`, **protected**). The consuming test lives in
the instar worktree (`amend` branch). Be explicit in every step
about which repo and branch a change targets.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained.

## Situation

Phase 6's `tests/test_amend.py` validates `instar amend` against
the *locally-installed* `qemu-img` (10.0.8) live. This phase adds
the project's standard **cross-version baselines**: recorded
`qemu-img info --output=json` outputs for each amend case across
the full qemu-img version matrix (6.0.0–10.2.0, 80 statically-built
binaries already present at `instar-testdata/qemu-img-binaries/
x86_64/`), plus a baseline-matrix test that runs `instar amend`,
runs the *installed* `qemu-img info`, and compares against the
recorded baseline for that version. This is the same mechanism
every other subcommand uses (`create`, `resize`, `map`, …).

Distinct from phase 6: phase 6 runs `qemu-img amend` live at test
time; phase 7 records `(qemu create → qemu amend → qemu info)`
once, per version, and the test substitutes `instar amend` for the
middle step and compares the resulting info JSON to the recording.
Its added value over phase 6 is version-matrix coverage (catching a
qemu version whose amend or info JSON differs) without needing
`qemu-img amend` at test time.

The grounding the implementer builds on (verified across both
repos):

- **Generator** `instar-testdata/scripts/generate-baselines.py`:
  `RESIZE_CASES` (tuples `(case_name, start_size, end_spec,
  create_opts, prealloc)`) and `generate_resize_baseline()` are the
  closest template. The resize generator: `qemu-img create -f FMT
  [-o …] <tmp> <size>` → `qemu-img resize … <tmp> <end>` →
  `qemu-img info --output=json <tmp>` → writes
  `expected-outputs/resize-info-json/<target>/<version>/<case>.{stdout.txt,
  stderr.txt,meta.json}`, substituting the tmp path with
  `$FILENAME`. The `meta.json` records each stage's return code and
  timeout flag. The `COMMANDS` dict + `main()` dispatch select the
  per-command generator and its `output_types`; the version loop
  comes from `get_qemu_binaries()` over `qemu-img-binaries/<arch>/
  <version>/qemu-img`. The `instar-testdata` Makefile has
  `baselines-<op>` targets that call `generate-baselines.py
  --command <op> --no-commit`.
- **Consuming test** `tests/test_resize.py`:
  `TestResizeBaselineMatrix` with `_baseline_root(target)` →
  `expected-outputs/resize-info-json/<target>`,
  `_baseline_version_dir()` (picks the `<version>/` dir matching the
  installed qemu-img, major.minor prefix, else the newest),
  `_baseline_stdout()`/`_baseline_meta()`, and a factory
  `_make_resize_baseline_test(target, case)` that **skips** when the
  baseline meta shows qemu rejected create/op or there's no
  comparable JSON or the case is a `KNOWN_*_DIVERGENCE`, else:
  `instar create` → `instar resize` → installed `qemu-img info
  --output=json` → `assert_info_equivalent(actual, baseline, …)`.
  Tests are installed via `setattr`. **Note: this consumes the raw
  `<target>/<version>/` dirs directly — it does NOT use base.py's
  profile machinery — so no `base.py`/`version-map.json` changes are
  required.**
- **Normaliser** `tests/helpers/info_json.py`:
  `assert_info_equivalent` / `normalise_info_json` strip
  `actual-size`, `dirty-flag`, cache-hint fields, and per-target
  divergences (qcow2 strips nothing extra), and substitute the
  filename. qcow2's `compat`/`lazy-refcounts`/`refcount-bits` are
  **not** stripped (they're the point). Phase 6 already showed
  instar's amended-image info matches qemu's.
- **Amend's twist vs resize.** Resize's baseline test builds the
  start image with `instar create`. **Amend cannot** — upgrade
  cases need a *v2* start image and `instar create` is v3-only
  (`build_header`). So both the generator and the consuming test
  must build the start image with **`qemu-img create`** (the
  generator already shells qemu; the consuming test must shell
  `qemu-img create` for its start image, then run `instar amend`).
- **Push reality.** `instar-testdata` `main` is protected; the
  established pattern is an op-specific baseline branch
  (`measure-baselines`, `snapshot-baselines`). Generated baselines
  go on an **`amend-baselines`** branch; **pushing it (and merging
  to `main`) is operator-gated** — the agent generates and commits
  locally; the operator pushes (the `GITLAB_TESTDATA_PUSH_TOKEN`
  needs Maintainer/access_level=40 for protected `main`, and the
  user wants to be involved in pushes).

## Mission and problem statement

After this phase:

1. **Generator (`instar-testdata`).** `generate-baselines.py` gains
   `AMEND_CASES` (qcow2-only) and `generate_amend_baseline()`,
   wired into `COMMANDS`/`main()` and a `baselines-amend` Makefile
   target. For each case it runs `qemu-img create -f qcow2 -o
   <create_opts> <tmp> 1M` → `qemu-img amend -f qcow2 -o
   <amend_opts> <tmp>` → `qemu-img info --output=json <tmp>`, and
   writes `expected-outputs/amend-info-json/qcow2/<version>/
   <case>.{stdout.txt,stderr.txt,meta.json}` with the tmp path
   substituted to `$FILENAME`. `meta.json` records `create`/`amend`/
   `info` return codes + timeout flags + the option lists.

2. **Generated baselines (`instar-testdata`, `amend-baselines`
   branch).** Running the generator over the 80-version matrix
   produces `expected-outputs/amend-info-json/qcow2/<version>/…` for
   every version, committed on `amend-baselines`. (Expectation: the
   info JSON is identical across versions — amend sets
   compat/lazy, which are version-independent — so the baselines
   are near-uniform; that's fine, the test reads the version it
   needs.)

3. **Consuming test (`instar`, `amend` branch).** A
   `TestAmendBaselineMatrix` in `tests/test_amend.py` mirroring
   `TestResizeBaselineMatrix`: `_baseline_*` helpers over
   `expected-outputs/amend-info-json/qcow2/<version>/`, and a
   factory that, per case, **skips** when the baseline meta shows
   qemu rejected create/amend (or no JSON, or a known divergence),
   else: `qemu-img create` the start image → `instar amend` → run
   the installed `qemu-img info --output=json` → `assert_info_equivalent`
   against the recorded baseline. It also skips cleanly without
   `/dev/kvm` and when no baseline exists for the installed version.

4. **Push (operator-gated).** The `amend-baselines` branch is
   pushed to `instar-testdata` and merged to `main` by the operator;
   the instar-side commit notes the dependency.

5. The instar suite (`make test-integration`) discovers the new
   baseline tests; they pass against the local testdata
   (`amend-baselines` checked out) and skip gracefully where a
   baseline is absent. `make instar`/binaries unaffected.

Out of scope: docs/CHANGELOG (phase 9); fuzzing (phase 8). The
`AMEND_CASES` reuse phase 6's success cases (refusal cases produce
no comparable info and are covered by phase 6's refusal suite — the
generator records qemu's rejection in `meta.json` and the test
skips them).

## Open questions

### 1. Start-image creation: `qemu-img create`, in both generator and test

Confirmed by the v2 constraint: `instar create` cannot emit v2, so
**both** the generator and the consuming test build the start image
with `qemu-img create` (the generator already does; the consuming
test must too — a deviation from resize's `instar create`). The
test then runs `instar amend` and compares its qemu-info to the
recording. Confirm this is acceptable (it slightly reduces
coverage of `instar create`, but `instar create` has its own
baselines; amend's baseline is about the *amend* step).

### 2. Do amend baselines need profiles / version-map.json?

`TestResizeBaselineMatrix` consumes the raw `<target>/<version>/`
dirs directly, not base.py's `profiles/` machinery. Recommendation:
mirror resize — **raw per-version dirs only**, no
`detect-profiles.py` / `version-map.json` / `base.py
COMMAND_OUTPUT_DIRS` change required. (Optionally run
`detect-profiles.py` for consistency with other ops, but the test
does not need it.) Confirm.

### 3. `AMEND_CASES` for the generator (confirm before 7a)

Reuse phase 6's success matrix, in the generator's tuple shape
`(case_name, create_opts_list, amend_opts_list)`:
`upgrade-plain` (`[compat=0.10]` → `[compat=1.1]`),
`upgrade-with-lazy` (`[compat=0.10]` → `[compat=1.1,lazy_refcounts=on]`),
`downgrade-plain` (`[compat=1.1]` → `[compat=0.10]`),
`lazy-on` (`[compat=1.1]` → `[lazy_refcounts=on]`),
`lazy-off` (`[compat=1.1,lazy_refcounts=on]` → `[lazy_refcounts=off]`),
`noop` (`[compat=1.1]` → `[compat=1.1]`). The backing cases are
omitted from baselines (the recorded backing path would embed a
tmp filename; phase 6 covers backing preservation live) — confirm,
or include them with a `$FILENAME`-substituted backing path if the
generator's path substitution handles it.

### 4. Branch + push mechanics

Recommendation: generate + commit on an `instar-testdata`
**`amend-baselines`** branch (cut from `main`); leave the worktree
on that branch so the instar test sees the baselines locally; the
operator pushes the branch and merges to `main`. The agent does
**not** push to protected `main`. Confirm the branch name and that
the push is operator-gated.

### 5. Is the version-matrix generation worth the runtime?

Generating 6 cases × 80 versions × (create+amend+info) ≈ 1,440
qemu invocations — minutes, not hours, with the static binaries.
Recommendation: generate the full matrix (the convention). If a
subset is preferred for speed, the generator already supports
`--version <v>`; but the recorded matrix is the deliverable.
Confirm full-matrix generation.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | high | opus | none | In the **testdata repo** `/srv/kasm_profiles/mikal/vscode/src/shakenfist/instar-testdata` (on a fresh `amend-baselines` branch cut from `main`): add `AMEND_CASES` (Open question 3) and `generate_amend_baseline(binary, version, case, output_root, …)` to `scripts/generate-baselines.py`, modelled closely on `generate_resize_baseline`. Pipeline: `qemu-img create -f qcow2 -o <','.join(create_opts)> <tmp> 1M` → `qemu-img amend -f qcow2 -o <','.join(amend_opts)> <tmp>` → `qemu-img info --output=json <tmp>`; write `<output_root>/amend-info-json/qcow2/<version>/<case>.{stdout.txt,stderr.txt,meta.json}` with the tmp path substituted to `$FILENAME` (reuse the resize generator's substitution helper) and a `meta.json` recording `create_return_code`/`amend_return_code`/`info_return_code` + timeout flags + `create_opts`/`amend_opts` + `command='amend'` + `qemu_version`. Wire `amend` into the `COMMANDS` dict (output type `amend-info-json`, format json) and the `main()` dispatch loop (iterate `AMEND_CASES['qcow2']`, call the generator per version). Add a `baselines-amend` Makefile target mirroring `baselines-resize`. Do NOT generate yet (7b). Validate the script parses and `--help`/a `--command amend --version 10.0.0 --dry-run`-style smoke (or a single-version run) produces the right paths; do NOT commit yet. This is a testdata-repo change — keep it separate from the instar worktree. opus: the generator dispatch/output wiring must exactly match the existing conventions or the test can't find the baselines. |
| 7b | medium | sonnet | none | In the **testdata repo** on `amend-baselines`: run the full generation — `scripts/generate-baselines.py --command amend --no-commit` (all 80 versions). Verify the output: `expected-outputs/amend-info-json/qcow2/<version>/` exists for every version with `.stdout.txt`/`.meta.json` per case; spot-check a `stdout.txt` is valid qcow2 info JSON with the expected `compat`/`lazy-refcounts`, and that `noop`/refused cases recorded sensible meta. Then `git add expected-outputs/amend-info-json && git commit` on `amend-baselines` (message per CLAUDE.md, `Signed-off-by`). Do NOT push (operator-gated, Open question 4). Report the version count and any case where qemu rejected the amend (recorded rc != 0). |
| 7c | medium | sonnet | none | In the **instar worktree** (`amend` branch): add `TestAmendBaselineMatrix(TestAmendSmoke)` to `tests/test_amend.py`, mirroring `TestResizeBaselineMatrix` in `tests/test_resize.py`. `_baseline_root()` → `self._testdata_root / 'expected-outputs' / 'amend-info-json' / 'qcow2'`; copy resize's `_baseline_version_dir`/`_baseline_stdout`/`_baseline_meta`. Factory `_make_amend_baseline_test(case)`: `self._require_kvm()`; load `_baseline_meta`, `skipTest` if absent or `create_return_code`/`amend_return_code`/`info_return_code` != 0 or `('qcow2', case_name)` in `KNOWN_AMEND_DIVERGENCES`; then in a tempdir, `qemu-img create -f qcow2 -o <create_opts> <path> 1M` (NOT instar create — Open question 1), `self.run_instar_amend('-f','qcow2', *(-o per amend_opt), path)` (assert rc==0), run the installed `qemu-img info --output=json <path>`, and `assert_info_equivalent(self, actual_info, baseline_stdout, 'qcow2', tmp_path=str(path), msg=…)`. Install via `setattr` over the AMEND_CASES (reuse the phase-6 case list, mapping to the generator's create/amend opts). Validate: ensure `instar-testdata` is on `amend-baselines` (so the baselines are present), then run the amend suite via the venv stestr (`cd tests && INSTAR_BINARY_PATH=… ../tests/.venv/bin/stestr run test_amend`) — the new baseline tests must pass (or skip cleanly with a clear reason). Do NOT run cargo. |
| 7d | low | sonnet | none | In the **instar worktree**: update `docs/plans/PLAN-amend.md` phase-7 row status; if the generator/test surfaced a real divergence, register it in `KNOWN_AMEND_DIVERGENCES` + the master plan. `make instar` (binaries unchanged), `pre-commit run --all-files` (clean), re-run the amend suite. Stage and present a single instar-side commit for steps 7c–7d (the testdata commit from 7b lives in the other repo). The commit body must note that it depends on the `instar-testdata` `amend-baselines` branch being pushed/merged (operator-gated). Do not push either repo. |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. **Two repos are in play** — every step's brief
names the repo and branch. After each step the management session
reads the actual changed files (in the right repo), confirms no
unrelated files/repos changed, runs the named gates, and then
commits/retries/upgrades. The push of the `instar-testdata`
`amend-baselines` branch to protected `main` is **operator-gated** —
the agent generates and commits locally only. The management review
re-runs the amend suite (with `instar-testdata` on `amend-baselines`)
and spot-reads a generated baseline + the consuming test.

### Model and effort notes

- 7a is opus: the generator's `COMMANDS`/`main()` dispatch and the
  exact output-path layout must match conventions or the test can't
  locate baselines.
- 7b (run the generator), 7c (mirror resize's baseline test), 7d
  are well-defined; sonnet suffices.
- When in doubt, skew to the more capable model.

### Management session review checklist

After the steps:

- [ ] Read the generator addition — `generate_amend_baseline`
      matches `generate_resize_baseline`'s structure, writes the
      correct `amend-info-json/qcow2/<version>/` layout with
      `$FILENAME` substitution, and the `meta.json` records all
      three stage return codes.
- [ ] The generated baselines exist for the full version matrix;
      a spot-checked `stdout.txt` is valid info JSON with the right
      `compat`/`lazy-refcounts`.
- [ ] The consuming test creates the start image with `qemu-img
      create` (not `instar create`), runs `instar amend`, and
      `assert_info_equivalent`s against the recording — and skips
      cleanly on missing baseline / qemu-rejected meta / no kvm.
- [ ] The amend suite passes (management re-runs it with
      `instar-testdata` on `amend-baselines`).
- [ ] `make instar`/binaries unchanged; `pre-commit` clean.
- [ ] Changes are correctly split across the two repos; the
      instar commit notes the testdata-branch dependency; nothing
      was pushed.

## Administration and logistics

### Success criteria

Phase 7 is complete when:

* `instar-testdata` (`amend-baselines` branch) has
  `generate-baselines.py` amend support + a `baselines-amend`
  target + generated `expected-outputs/amend-info-json/qcow2/
  <version>/…` for the full version matrix, committed (push
  operator-gated).
* `tests/test_amend.py` has a `TestAmendBaselineMatrix` that
  compares `instar amend` output against the recorded per-version
  baselines via `assert_info_equivalent`, skipping cleanly on
  missing baselines / qemu-rejected cases / no kvm.
* The amend suite passes against the local testdata; `make instar`/
  binaries unaffected; `pre-commit` clean.
* Any real divergence is registered; the instar commit notes the
  operator-gated testdata push/merge.

### Future work created by this phase

- Phase 8: fuzzing (coverage + differential) of the amend planner.
- The operator pushes/merges the `instar-testdata` `amend-baselines`
  branch to `main` so CI sees the baselines.
- If amend output ever diverges by qemu version, the recorded
  matrix localises which versions; revisit the normaliser if a new
  qcow2 info field appears.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not** added to
`docs/plans/order.yml`. The master plan links to it from its
Execution table (already present).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan, including the two-repo split
and the operator-gated testdata push.
