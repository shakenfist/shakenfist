# PLAN-snapshot phase 11: integration tests

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (`tests/base.py` —
`InstarTestBase`, `COMMAND_OUTPUT_DIRS`, `get_output_profiles`,
`get_profile_for_installed_qemu`, the per-command `run_instar_*`
helpers, the manifest/image plumbing; `tests/test_map.py` as the
freshest baseline-consuming suite — its factory-generated test
methods, skip taxonomy, and `KNOWN_*_DIVERGENCES` pattern;
`tests/test_commit.py` / `tests/test_resize.py` for the
mutating-operation round-trip patterns; `tests/helpers/`; the
`Makefile` `test-integration` target and stestr wiring; the
seven snapshot harnesses under `tools/`; the snapshot fixtures
and baselines landed by phase 10), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-snapshot-phase-NN-<descriptive>.md`. The master plan is
[PLAN-snapshot.md](/components/instar/plans/PLAN-snapshot/). This is phase 11 of
fourteen.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why. Python follows my global
conventions: single quotes, 120-character lines, no trailing
whitespace.

## Situation

Phases 1–10 delivered the snapshot subcommand end-to-end, seven
shell verification harnesses, the truncation fix, and the
cross-version baseline matrix (twelve images × 80 versions,
two profiles, boundary at 9.0.0). Phase 11 adds the **stestr
integration suite** `tests/test_snapshot.py` so CI regressions
surface through the same mechanism as every other subcommand —
plus the JSON goldens deferred from phase 10.

### Relationship to the shell harnesses

The harnesses under `tools/` are **live differential
verification** against the host qemu-img — byte-identity from
identical inputs, 237 assertions, developer-run. The stestr
suite is the **CI regression net**: instar against *frozen*
baselines and structural post-op invariants. They overlap by
design; the suite does not try to port all 237 assertions, it
encodes the master plan's test-matrix table plus the frozen
artefacts phase 10 created. (Whether CI should also run the
harnesses is a phase 14 / CI-config question, noted there.)

### Established facts (verified during planning)

1. **The baseline-consuming pattern**: `test_map.py`
   factory-generates one test per (image, output_type),
   resolves the host qemu version to a profile via
   `get_profile_for_installed_qemu` (major.minor prefix match,
   fallbacks documented in `base.py`), byte-compares instar
   stdout to the profile baseline, with a five-way skip
   taxonomy and a `KNOWN_*_DIVERGENCES` escape hatch.
   `COMMAND_OUTPUT_DIRS` in `base.py` maps command → output
   directory prefix and needs a `'snapshot-list'` entry
   (note: the snapshot baselines use the full type name
   `snapshot-list-human`, so the mapping entry must produce
   that — read how map's `'map'` → `map-human` composition
   works and follow it).
2. **The snapshot-specific profile wrinkle**: instar's list
   output targets the modern (≥9.0) format. On this host
   (qemu-img 10.0.8) the resolved profile is
   `profile-10-0-0` and comparisons run; on a pre-9.0 host
   the resolved profile would be the old format, which instar
   intentionally does not match. The suite must resolve the
   host profile and **skip with a documented reason when it
   is not the newest profile** (`docs/quirks.md` cross-version
   note), rather than failing.
3. **List baselines were generated under `TZ=UTC`** (the DATE
   column is localtime-rendered): every instar list
   invocation in the suite must pin `TZ=UTC` in the
   subprocess environment.
4. **The JSON schema is QMP-shaped and TZ-independent**
   (verified live): kebab-case keys, raw seconds —
   `[{ "id": "1", "name": "vmstate", "vm-state-size":
   1048576, "date": { "seconds": ..., "nanoseconds": 0 },
   "vm-clock": { "seconds": 3661, "nanoseconds": 500000000 },
   "icount": 12345 }]` — so instar-side goldens are fully
   deterministic frozen bytes.
5. **The twelve baselined images** carry manifest tags
   (`snapshots`, plus variant tags) and frozen dates
   (2026-01-01T00:00:00Z); `snap-qcow2-backing-base` is the
   empty-case. The edge fixtures phase 10 built for this
   phase: `snap-qcow2-v3-sixteen` (the v1 cap),
   `snap-qcow2-dupname`, `snap-qcow2-namecollision` (the
   `-a`/`-d` asymmetry pair), `snap-qcow2-longname`,
   `snap-qcow2-vmstate`.
6. **Mutating tests boot a microVM per invocation** (KVM
   required, like every instar test) and must work on
   tempdir copies of fixtures (stestr runs tests
   concurrently — never mutate a testdata file in place).
   The refusal-fixture recipes (zstd, dirty bit, external
   data file) live in the `tools/` refusal harnesses; LUKS
   images already exist in the manifest.

### What phase 11 produces

1. **`tests/test_snapshot.py`** with five test families:

   *(a) List matrix (human)* — factory-generated per baselined
   image: `TZ=UTC instar snapshot -l` byte-equals the
   host-resolved profile baseline, with the map suite's skip
   taxonomy plus the fact 2 old-profile skip. One additional
   test asserts the bare-filename form (`instar snapshot
   FILE`) produces identical output to `-l` (the phase 9 D2
   behaviour).

   *(b) List goldens (JSON)* — per baselined image:
   `instar snapshot -l --output=json` byte-equals
   `tests/golden/snapshot-list/<image-id>.json` (created by
   this phase — see step 11b); plus one structural test
   cross-checking the JSON fields against the parsed human
   columns for `snap-qcow2-vmstate` (id, name, vm-state-size,
   vm-clock seconds, icount), so the goldens cannot drift
   into self-consistent nonsense; plus a schema test pinning
   the QMP key names from fact 4.

   *(c) Mutation round-trips* (tempdir copies; one focused
   test per row of the master plan's test-matrix table):
   - create: `-c` then `qemu-img check` clean; `-c` then
     instar/qemu `snapshot -l` agree modulo the DATE column
     (regex-normalised); second create assigns ID 2 with a
     duplicate name accepted; create on
     `snap-qcow2-v3-sixteen` refused with the
     snapshot-table-full error and the image untouched
     (sha256).
   - delete: `-d` (first/last/sole variants on a 3-snapshot
     tempdir image built with qemu-img at test time) then
     check clean; sole delete leaves header
     `nb_snapshots=0, snapshots_offset=0` (struct-decoded);
     name-only matching pinned on `snap-qcow2-namecollision`
     (`-d 2` removes the snapshot *named* "2"; `-d` by pure
     ID is not-found, exit 1, image untouched).
   - apply: `-a` then check clean; content restoration via
     `qemu-img compare` against a pre-divergence reference
     copy ("Images are identical"); ID-then-name matching
     pinned on `snap-qcow2-namecollision` (`-a 2` applies ID
     2 — the documented asymmetry with delete); post-apply
     write probe (qemu-io write, check stays clean, the
     applied snapshot still restorable).

   *(d) Error paths and qcow2-only enforcement* — all four
   modes against a raw, a vmdk, and a vhdx manifest image
   (refusal, non-zero exit, qemu-parity in substance);
   mutating modes against a LUKS manifest image, an ad-hoc
   zstd image, an ad-hoc dirty-bit image, an ad-hoc
   external-data-file image (recipes lifted from the
   refusal harnesses); not-found `-d`/`-a` exit 1 with image
   untouched; `-U` with a mutating mode refused before file
   access; `--image-opts` rejected; mixed mode flags exit
   non-zero.

   *(e) Empty-table behaviour* — list on the empty-case
   image: empty stdout, exit 0; JSON form emits `[]`.

2. **`tests/golden/snapshot-list/*.json`** — twelve frozen
   goldens generated from the instar binary built at this
   commit, reviewed for sanity (the structural test in (b) is
   the guard), checked in alongside the suite.

3. **`base.py` plumbing**: `'snapshot-list'` in
   `COMMAND_OUTPUT_DIRS`; a `run_instar_snapshot(...)` helper
   matching the existing per-command helper conventions
   (accepting an `env` override so TZ pinning is explicit at
   call sites); nothing else — resist generalising.

4. **Docs/bookkeeping**: master plan phase 11 row → Landed;
   the test-matrix table annotated with which suite covers
   each row; `docs/quirks.md` only if a new divergence
   surfaces (none expected — phases 6–10 documented them
   all).

### What phase 11 does not change

- instar source code: nothing (pure test/docs phase; every
  binary byte-identical). Any instar bug the suite surfaces
  is a finding to report, not absorb.
- The shell harnesses, fixtures, baselines, manifest.

## Mission and problem statement

After phase 11 lands, `make test-integration` runs
`test_snapshot.py` green on this host: the list matrix
compares instar byte-for-byte against the phase 10 profiles,
the JSON goldens freeze the instar extension's schema, the
mutation round-trips prove post-op structural health through
qemu's own tooling, and every documented refusal path is
pinned — all without touching a single testdata file in place.

## Open questions

### 1. How many mutation tests is too many?

Each mutating invocation boots a microVM (~seconds). The
harnesses already provide exhaustive byte-level coverage.
**Working answer: ~25–35 instar invocations across the whole
suite**, one focused test per master-plan matrix row and per
documented behaviour, parallel-friendly via stestr. The suite
should add no more than ~3–4 minutes to `make test-integration`
on this host; report the measured wall time in the back-brief.

### 2. DATE normalisation in create round-trips

Freshly created snapshots embed wall-clock dates, so
instar-vs-qemu listing comparisons in family (c) can't be
byte-exact. **Working answer: regex-replace the DATE column
(`\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}`) with a placeholder on
both sides before comparing** — the same shape the harnesses
use. Everything else stays exact.

### 3. Golden generation: in-phase or pre-generated?

**Working answer: generated by the implementing agent from
the freshly built instar binary, reviewed via the structural
cross-check test, committed with the suite.** They are
instar-side self-baselines (phase 10 open question 1); the
structural test plus the human-baseline matrix keep them
honest. Regeneration instructions go in a comment header
inside each golden? No — JSON files can't carry comments;
put a `tests/golden/snapshot-list/README.md` with the
one-liner regeneration command instead.

### 4. Old-qemu hosts

Fact 2's skip keeps the suite green on pre-9.0 hosts at the
cost of silently reduced coverage there. **Working answer:
accept it** — identical posture to the existing suites' skip
taxonomy, and this host (and CI) run 10.x. The skip message
names the quirks section.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 11a | low | sonnet | worktree | Plumbing. `tests/base.py`: add the `'snapshot-list'` mapping to `COMMAND_OUTPUT_DIRS` (composing to the `snapshot-list-human` output dir — mirror how the existing entries compose with the `-human`/`-json` suffix, reading `get_output_profiles` to confirm); add `run_instar_snapshot(self, *args, env_overrides=None)` following the existing `run_instar_*` helper conventions (timeout, rlimits, stdout/stderr/rc tuple) with explicit env-override support for TZ pinning. No other base.py changes. |
| 11b | medium | sonnet | worktree | List families (a), (b), (e). Write `tests/test_snapshot.py` scaffolding modelled on `test_map.py` (module docstring stating scope + the harness relationship, factory-generated methods, the skip taxonomy, an initially-empty `KNOWN_SNAPSHOT_DIVERGENCES`). Family (a): per baselined image (the twelve, discovered via the manifest `snapshots`/empty-case tags), `TZ=UTC` list vs host-resolved profile, with the fact 2 newest-profile guard (skip + quirks pointer when the host resolves to the old family); the bare-filename-equals-`-l` test. Family (b): generate the twelve goldens into `tests/golden/snapshot-list/` from the built instar binary (plus `README.md` with the regeneration one-liner per open question 3); byte-compare tests; the vmstate structural cross-check (parse JSON, parse the human baseline columns, assert id/name/vm-state-size/vm-clock-seconds/icount coherence); the QMP-key schema test. Family (e): empty stdout + exit 0, and `[]` for JSON, on the empty-case image. Run the new tests via stestr and report counts. |
| 11c | medium | sonnet | worktree | Mutation family (c) per the Situation list — every test on a tempdir copy (`tempfile.TemporaryDirectory`, `shutil.copy2`, never the testdata file; for the backing fixture copy both files preserving the relative path). Use `qemu-img`/`qemu-io` from PATH for the oracle steps (check/compare/write probes; struct-decode the header for the sole-delete assertion with the same `struct.unpack` shapes the harnesses use). DATE normalisation per open question 2. Respect the open-question-1 invocation budget — focused tests, no combinatorial sweeps (the harnesses own that). |
| 11d | medium | sonnet | worktree | Error-path family (d) per the Situation list. Reuse manifest images for raw/vmdk/vhdx/LUKS (pick small ones — check sizes); lift the zstd / dirty-bit / external-data-file recipes from `tools/snapshot-create-refusals.sh` into small test-local helpers (ad-hoc images built in the tempdir with qemu-img from PATH). Every refusal asserts: non-zero exit, image sha256 unchanged, and (where the harness pins one) the documented error text fragment. Include the phase 9 CLI items: `-U`+mutating refusal, `--image-opts` rejection, mixed-flags non-zero, not-found exit codes. |
| 11e | medium | sonnet | worktree | Full verification + commit. `make instar` (binaries byte-identical — assert and report), `make test-rust`, `make test-integration` (the whole suite, not just the new file — measure and report test_snapshot.py's added wall time against the open-question-1 budget), `make lint`, `pre-commit run --all-files`, plus one full harness re-run (`tools/snapshot-cli-parity.sh`) as a sanity gate. Confirm stestr discovery picks the new file without Makefile changes (the exclude-regex only excludes test_info_malicious). Docs: master plan phase 11 row → Landed, test-matrix table annotations. Single commit (suite + goldens + plumbing + docs) per `~/.claude/CLAUDE.md` conventions — Python in the commit follows single quotes / 120-char lines. The message should cover: the five test families, the harness/suite relationship, the JSON goldens as the phase 10 deferral landing, the profile-resolution wrinkle, and the no-instar-code-changes invariant. |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. **Single sonnet agent** — discovery is
pinned, the work is test-writing against established
patterns. Worktree isolation; verify the base is the
`snapshot` branch head first (`git reset --hard snapshot` if
not — every prior phase needed it). KVM is available on this
host; the suite requires it like every instar test.

If the suite surfaces an instar behaviour that contradicts a
baseline or a documented behaviour, STOP and report — that is
a real bug finding (phase 10's truncation finding is the
precedent), not something to encode into the tests.

### Management session review checklist

- [ ] Read the suite; factory pattern and skip taxonomy match
      the house style; no testdata file is ever mutated in
      place.
- [ ] The twelve goldens are sane (spot-read two; the
      vmstate structural test passes).
- [ ] TZ=UTC is pinned on every list invocation.
- [ ] instar binaries byte-identical; `make test-integration`
      green end-to-end; added wall time within budget.
- [ ] Independent spot-check: run `stestr run test_snapshot`
      twice (flakiness probe) and one mutation test under
      `--concurrency 4` alongside others.

## Administration and logistics

### Success criteria

Phase 11 is complete when:

* `tests/test_snapshot.py` + goldens land in one commit and
  `make test-integration` is green with the suite included.
* The list matrix is profile-resolved and byte-exact on this
  host; the JSON goldens are frozen and structurally
  cross-checked.
* Every master-plan test-matrix row is covered by a named
  test (the annotation in the master plan maps them).
* All refusal/error paths assert exit + image-untouched.
* No instar source changes; binaries byte-identical.

### Future work created by this phase

- **Harnesses in CI** — whether `tools/snapshot-*.sh` should
  run in a CI lane (they need a matrix qemu-img and ~minutes)
  is a phase 14 note.
- **Old-profile coverage** — a CI lane with a pre-9.0
  qemu-img would exercise the fact 2 skip path; not worth a
  lane today.

### Bugs fixed during this work

None expected; findings are reported, not absorbed.

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`docs/plans/order.yml`. The master plan's Execution table
already links this file; step 11e flips the row to Landed.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the
work you intend to do aligns with that plan — including the
measured suite wall time and the stestr discovery check.
