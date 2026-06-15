# PLAN-check-repair phase 08: integration tests

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase and ground your answers in what
the code actually does. The code this phase touches:

- `tests/base.py` — `run_instar_check` (~line 404; builds the
  `check` command — it does **not** yet pass `--repair`),
  `run_qemu_img_check` (~448), `get_image(id)` /
  `_images_by_id` / `verify_image_hash`, and the `_testdata_root`
  resolution.
- `tests/test_check_formats.py` — `TestCheckCorruptImages`
  (loads the `check-qcow2-*` fixtures by manifest id and asserts
  detection); the InstarTestBase subclass pattern to mirror.
- `tests/test_snapshot.py` — the **mutating-test pattern** to
  reuse: `tempfile.TemporaryDirectory()` + `shutil.copy2(src,
  copy)` so the committed fixture is never mutated, then run the
  instar mutation on the copy and assert with `qemu-img`.
- `tests/manifest.json` — the fixture registry. Repair fixtures
  (phase 7, pushed to instar-testdata): `check-qcow2-leaked`,
  `check-qcow2-refcount-zero`, `check-qcow2-refcount-too-high`,
  `check-qcow2-stale-copied`, `check-qcow2-overlapping`,
  `check-qcow2-corrupt-bit-set`, `check-qcow2-snapshot-leak`,
  `check-qcow2-compressed-leak`, `check-qcow2-clean`.

The parent master plan is
[PLAN-check-repair.md](/components/instar/plans/PLAN-check-repair/). This is phase 8 of
eleven — the integration suite that exercises `check --repair`
end-to-end against the phase-7 fixtures. The behaviours below
were **already verified manually during phase 7** (instar repairs
the four repairable fixtures to `qemu-img check`-clean; refuse
fixtures stay byte-identical; overlapping gets a safe partial
repair) — this phase **codifies** those into
`tests/test_check_repair.py`.

This phase adds a small `base.py` helper and a new test file; no
instar source change, so `check.bin` is byte-identical.

I prefer one commit per logical change. The commit must build,
pass tests, and have a clear message.

## Situation

`check --repair` works (phases 4–6, validated in phase 7) and the
fixtures exist (phase 7, pushed). What is missing is the
regression suite that locks the behaviour in. The harness already
has everything except a way to pass `--repair`: `run_instar_check`
builds `check` with `--output` / `--unsafe-quirks` / `--chain` but
no repair flag. The mutating-test idiom (copy-to-tempdir) is
established in `test_snapshot.py`.

### Behaviours to lock in (all verified in phase 7)

Repairable → instar repairs, then `qemu-img check` is clean and
the guest data survives:
- `check-qcow2-leaked` + `--repair=leaks` → clean.
- `check-qcow2-refcount-zero` + `--repair=all` → clean (raise 0→1).
- `check-qcow2-refcount-too-high` + `--repair=all` → clean (lower).
- `check-qcow2-stale-copied` + `--repair=all` → clean (refcount +
  COPIED).

Refuse / partial (instar must not corrupt):
- `check-qcow2-corrupt-bit-set` + `--repair=all` → **byte-identical**,
  `FLAG_REPAIR_INCOMPLETE` (instar's conservative gate; the JSON
  `repair-incomplete` key is the observable signal).
- `check-qcow2-snapshot-leak` + `--repair` → **byte-identical**,
  snapshot `s1` intact (`qemu-img snapshot -l`), `qemu-img check`
  clean.
- `check-qcow2-compressed-leak` + `--repair=all` → **byte-identical**,
  `qemu-img check` clean, data reads.
- `check-qcow2-overlapping` + `--repair=all` → **partial**: the
  leaks tier reclaims the genuine leak, the structural overlap
  remains (`qemu-img check` still reports it), exit 2,
  `repair-incomplete`. The image must not be made *worse* (no new
  errors beyond the pre-existing overlap).

CLI / safety:
- `--repair` + `--chain` → rejected (non-zero, error message).
- `--repair=all` on a healthy qcow2 → byte-identical, exit 0.
- `--repair` on a raw image → qcow2-only: not repaired (the guest
  only repairs inside `check_qcow2`); confirm no crash / sane
  exit.
- Idempotence: repairing an already-repaired (clean) image is a
  no-op.

### Data-preservation oracle

The fixtures' clean data clusters carry known patterns
(`create-corrupt-images.py`: `0xAA`/`0xBB`/`0xCC`/`0xDD` at
`0`/`64k`/`128k`/`192k`). After repair, `qemu-io -c "read -P 0xAA
0 64k"` (etc.) must still succeed — read the generator to confirm
which clusters each fixture's corruption targets and which
patterns remain guest-visible. `qemu-img check`-clean is the
structural oracle; the pattern reads are the data oracle.

## Open questions

### 1. Extend `run_instar_check`, or build the command inline?

**Resolved: extend `run_instar_check`** with a `repair:
Optional[str] = None` param that appends `--repair={value}` (e.g.
`'leaks'`, `'all'`). It is the shared entry point every check
test uses; inline command-building would duplicate the binary
resolution and timeout handling. Keep the change additive
(default `None` → no flag → existing behaviour byte-identical).

### 2. Mutate the committed fixture, or a copy?

**Resolved: always a copy** (`tempfile.TemporaryDirectory()` +
`shutil.copy2`), mirroring `test_snapshot.py`. Repair writes in
place; the committed fixtures must stay corrupt for repeatable
runs. Byte-identity assertions (`sha256`) compare the copy
before/after.

### 3. `qemu-img compare` for data equivalence, or pattern reads?

**Resolved: pattern reads (`qemu-io read -P`).** `qemu-img
compare` against the *corrupt original* is unreliable (qemu may
refuse to open some fixtures); reading the known patterns from
the repaired image is a direct, robust data oracle. `qemu-img
check` covers structural cleanliness.

### 4. How is `repair-incomplete` observed?

**Resolved: the JSON output.** Phase 6 added the
`repair-incomplete` key to `--output=json`; tests assert on it
for the refuse/partial cases. (The per-counter "Repaired N"
fields are not on the wire — phase 6 finding — so tests assert
post-repair *state*, `qemu-img check`-clean + byte-identity, not
instar's repaired counts.)

## Execution

| Step | Effort | Model | Brief for sub-agent |
|------|--------|-------|---------------------|
| 8a | low | sonnet | Extend `run_instar_check` in `tests/base.py` with a `repair: Optional[str] = None` parameter: when set, append `--repair={repair}` to the command (so `repair='leaks'` → `--repair=leaks`, `repair='all'` → `--repair=all`). Keep it additive — default `None` appends nothing, so every existing call is unchanged. Update the docstring. No other base.py change (the tempdir-copy idiom lives in the test file). |
| 8b | high | opus | Write `tests/test_check_repair.py` as InstarTestBase subclasses (mirror `TestCheckCorruptImages`'s id-based fixture loading and `test_snapshot.py`'s copy-to-tempdir idiom; import `shutil`, `tempfile`, `subprocess`, `json`, `hashlib`). Skip the whole module if `qemu-img`/`qemu-io` are unavailable. Helper: `_repair_copy(self, image_id, repair=None) -> (tmpdir, copy_path, stdout, stderr, rc, sha_before, sha_after)` that resolves the fixture via `get_image`, copies it to a TemporaryDirectory, runs `run_instar_check(copy, output_format='json', repair=repair)`, and records sha256 before/after. Classes + tests: (1) **TestRepairLeaksTier**: `check-qcow2-leaked` + `repair='leaks'` → after-repair `qemu-img check <copy>` exits 0 (clean); the known data pattern still reads (`qemu-io -c 'read -P 0xAA 0 64k'`). (2) **TestRepairAllTier**: `check-qcow2-refcount-zero`, `check-qcow2-refcount-too-high`, `check-qcow2-stale-copied` each + `repair='all'` → `qemu-img check` clean; data patterns read. (3) **TestRepairRefuse**: `corrupt-bit-set` + `all` → sha unchanged AND the JSON `repair-incomplete` is true; `snapshot-leak` + `leaks` → sha unchanged, `qemu-img snapshot -l` still lists `s1`, `qemu-img check` clean; `compressed-leak` + `all` → sha unchanged, `qemu-img check` clean, data reads; `overlapping` + `all` → `qemu-img check` afterward still reports the overlap error but **no new error classes** and the leak is gone, and instar exited 2 with `repair-incomplete`. (4) **TestRepairCli**: `--repair`+`--chain` rejected (build the command via `run_instar_check(..., repair='leaks')` plus a chain arg, or a direct subprocess — assert non-zero + message); `--repair=all` on `check-qcow2-clean` → sha unchanged, instar exit 0; `--repair` on a raw image (create a temp `qemu-img create -f raw`) → no crash, sane exit; idempotence: run `--repair=all` twice on a `refcount-zero` copy, second run leaves it `qemu-img check`-clean and sha-stable. Use `assertEqual`/`assertIn`/`skipTest` like the existing suites; read `create-corrupt-images.py` to confirm exact data offsets/patterns per fixture. Use opus: the suite is the regression gate for the whole feature; the partial-repair (overlapping) and refuse assertions are subtle and must encode the phase-7-verified behaviour exactly. |
| 8c | medium | sonnet | Verify and commit. In a temp venv (`pip install -r tests/requirements.txt`), with `INSTAR_BINARY_PATH=<built instar>` and `INSTAR_TESTDATA_PATH=../instar-testdata`, run `python -m pytest tests/test_check_repair.py -q` and confirm all pass; also re-run `tests/test_check_formats.py` to confirm the `base.py` change did not regress the existing check suite. (No `make instar` needed — no source change; reuse the existing release binary, but rebuild if stale.) Run `make lint` / `pre-commit run --all-files` only over the changed Python files (rust hook skips — avoids re-poisoning ownership). Present ONE commit (8a+8b) on the `check-repair` branch. The message explains: the integration suite for `check --repair` — leaks/all tiers repair the phase-7 fixtures to `qemu-img check`-clean with data preserved; refuse paths (corrupt-bit / snapshot / compression) stay byte-identical; the overlapping case is a verified safe partial repair; plus CLI/idempotence coverage; host-test-only, `check.bin` unchanged. |

## Agent guidance

### Execution model

Sub-agents implement; the management session reviews the test
file, runs the suite itself (in a venv, against the pushed
testdata), and commits. No instar source changes, so this phase
is low-risk to the binary; the risk is test *correctness* (a test
that passes for the wrong reason). The management session should
spot-check at least one repairable and one refuse case by hand.

### Model and effort notes

- **8a is low sonnet**: a one-parameter additive helper change.
- **8b is high opus**: it encodes the whole feature's expected
  behaviour; the partial-repair and refuse assertions must match
  phase-7's verified results exactly, and false-positive tests
  (passing for the wrong reason) are the hazard.
- **8c is medium sonnet**: run the suite + the existing check
  suite, then commit.

### Management session review checklist

- [ ] `run_instar_check(repair=None)` is byte-identical to before
      (existing suites unaffected); `test_check_formats.py` still
      passes.
- [ ] Every test mutates a **copy**, never the committed fixture.
- [ ] Repairable cases assert `qemu-img check`-clean **and** a
      data-pattern read (not just instar's own output).
- [ ] Refuse cases assert **sha256 byte-identity** of the copy.
- [ ] The overlapping case asserts the overlap remains but no new
      error classes appear (not made worse) and exit 2.
- [ ] No test passes for the wrong reason (spot-check by hand).
- [ ] `tests/test_check_repair.py` passes in a venv against the
      pushed testdata; `check.bin` unchanged.

## Administration and logistics

### Success criteria

* `tests/test_check_repair.py` exists and passes, covering both
  repair tiers (→ `qemu-img check`-clean + data preserved), the
  refuse/partial paths (byte-identical / not-worse), and
  CLI/idempotence.
* `run_instar_check` gained an additive `repair` parameter;
  `test_check_formats.py` still passes.
* No instar source change; `check.bin` byte-identical.
* Lands in one commit on the `check-repair` branch.

### Future work created by this phase

- Once the repair counters reach the host (the phase-6 wire
  follow-up), add assertions on the rendered "Repaired N" output.
- Add repair tests for the deferred snapshot/compression-aware
  recount once that tier lands (currently those fixtures test
  refusal).

### Bugs fixed during this work

To be filled in if writing the suite surfaces a behaviour that
disagrees with phase-7's manual verification (which would be a
real regression to investigate, not just a test fix).

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`order.yml`. The master plan's phase-8 row is updated to "Landed"
once the commit is in.

### Back brief

Before executing any step, back brief the operator on your
understanding — especially that the tests assert post-repair
*state* (`qemu-img check`-clean + data reads + byte-identity for
refuse), not instar's own repaired-counter output, and that every
test works on a copy.
