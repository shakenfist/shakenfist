# PLAN-check-repair phase 07: corrupt fixtures + cross-version baselines

## Prompt

Before responding to questions or discussion points in this
document, explore both repos thoroughly and ground your answers
in what the code actually does today. This phase is **cross-repo**:

- **instar** worktree (this branch): `tests/manifest.json` (the
  shared image registry; existing `check-qcow2-*` entries at
  ~lines 808-848 are the template), `tests/base.py`
  (`_load_manifest`, `verify_image_hash`, `get_image`), and the
  existing detection tests `TestCheckCorruptImages` in
  `tests/test_check_formats.py` (load fixtures by manifest id).
- **instar-testdata** sibling repo (`../instar-testdata`, on
  `main`, GitLab remote `git@gitlab.home.stillhq.com:private/instar-testdata.git`):
  `custom/check-validation/create-corrupt-images.py` (the fixture
  generator — `parse_qcow2_header`, `read_l1_entry`,
  `read_l2_entry`, `write_be16`/`write_be64`, `create_base_image`,
  per-fixture `create_*`), the four existing fixtures
  (`qcow2-clean-with-data`, `-leaked-cluster`, `-overlapping-clusters`,
  `-refcount-zero`), `scripts/generate-baselines.py` (the
  per-qemu-version capture; `--command check`) and
  `scripts/detect-profiles.py` (dedup into profiles), the 80
  `qemu-img-binaries/` (6.0.0 → 10.2.0), and the `Makefile`
  `baselines-check` target.

The parent master plan is
[PLAN-check-repair.md](/components/instar/plans/PLAN-check-repair/). This is phase 7 of
eleven — the **test-data** phase that phase 8's
`tests/test_check_repair.py` consumes. No instar source changes;
`check.bin` is byte-identical. The success metric for repair is
**post-repair `qemu-img check` cleanliness, not byte-identity**
(master-plan open question 5).

I prefer one commit per logical change. This phase produces **two
commits in two repos**: one on `instar-testdata` `main` (fixtures
+ generator + any captured baselines, pushed to its GitLab
remote) and one on the instar `check-repair` branch (the
`manifest.json` registrations). Do not auto-commit/push either
without my go-ahead.

## Situation

`check --repair` (phases 4–6) is wired end-to-end; what it lacks
is a fixture set exercising every repair and refuse path, plus
the cross-version detection baselines instar's test suite expects.
The existing `custom/check-validation/` already demonstrates the
exact technique — create a clean image with `qemu-img`/`qemu-io`,
then surgically corrupt it via `struct`-packed writes at offsets
computed from the parsed header — and four fixtures already cover
some detection cases. This phase extends that set for **repair**.

### The fixture set

Each fixture must be **verified empirically**: `qemu-img check`
must report the intended condition, and for the repairable ones
`qemu-img check -r all` (or `-r leaks`) must turn it **clean**
(the phase-8 oracle). Reuse existing fixtures where they already
fit; add the new ones to `create-corrupt-images.py`.

| Fixture | Condition | instar repair behaviour (phase 8 asserts) |
|---------|-----------|--------------------------------------------|
| `qcow2-leaked-cluster` (exists) | refcount > 0, no L2 reference | `--repair=leaks` reclaims → clean |
| `qcow2-refcount-zero` (exists) | referenced cluster, refcount 0 | `--repair=all` raises 0→1 → clean |
| `qcow2-refcount-too-high` (**new**) | once-referenced cluster, refcount inflated (e.g. 2) | `--repair=all` lowers →1 → clean (also exercises COPIED) |
| `qcow2-stale-copied` (**new**) | COPIED set on a cluster whose refcount the recount makes 1 (or an inflated-refcount + COPIED case qemu flags) | `--repair=all` reconciles COPIED → clean. **Construct empirically** — the corruption qemu actually flags is COPIED-set-on-shared (refcount > 1), which in a snapshot-free image means a manually-inflated refcount; confirm `qemu-img check` reports it |
| `qcow2-overlapping-clusters` (exists) | two L2 entries → one host cluster | structural corruption (`corruptions > 0`) → instar **refuses** the all tier, exit 2, image byte-identical |
| `qcow2-corrupt-bit-set` (**new**) | `INCOMPAT_CORRUPT` (offset 72, bit 1) set on an otherwise-sound image | instar **refuses** (the conservative `corruptions == 0` gate), `FLAG_REPAIR_INCOMPLETE`, image byte-identical. Documents the known limitation: instar cannot clear a pre-existing corrupt bit (qemu-img can) |
| `qcow2-snapshot-leak` (**new**) | internal snapshot + post-snapshot COW divergence (false leaks) | instar **refuses** all repair (phase-4 snapshot guard), image + snapshot intact |
| `qcow2-compressed-leak` (**new**) | zlib-compressed image (`qemu-img convert -c`), optionally with a leak | all tier **refused** (`uses_compression`), leaks tier safe no-op; no corruption |

Construction notes for the new ones (reuse the script's helpers):
- **refcount-too-high**: like `create_refcount_zero` but write `2`
  (or higher) into the refcount entry instead of `0`.
- **corrupt-bit-set**: read the 8 bytes at
  `INCOMPATIBLE_FEATURES_OFFSET = 72`, OR in `0x02`, write back.
- **snapshot-leak**: `qemu-img create` → `qemu-io write` →
  `qemu-img snapshot -c s1` → `qemu-io write` (diverge) — no hex
  edit needed (the divergence creates real snapshot-owned clusters
  the active L1 no longer references).
- **compressed-leak**: `qemu-img convert -c` from a clean image.
- **stale-copied**: inflate a refcount AND ensure COPIED stays set
  on that entry; verify `qemu-img check` flags it.

### Cross-version baselines — scope honestly

`generate-baselines.py --command check` runs all 80 qemu-img
binaries against each fixture and captures stdout/stderr/rc;
`detect-profiles.py` dedups into profiles. For these corruption
fixtures the detection output is expected to be **stable across
the matrix** (a leak is a leak), so expect ~1–2 profiles — unlike
the snapshot-list baselines, which drifted. The matrix capture is
therefore mostly *confirmation* of stability plus a reference for
phase 8; the **load-bearing deliverables are the fixtures and the
`qemu-img check -r`-clean verification.** Capture the baselines
(it is what the phase name promises and reuses the pipeline), but
do not over-invest if the profile count is trivially 1.

### What this phase does NOT do

- No instar source change; `check.bin` byte-identical.
- No `test_check_repair.py` (that is phase 8 — this phase only
  produces and registers the data it will consume).
- No exotic qcow2 format-variant fixtures (non-16-bit refcount
  widths, tiny clusters, v2) beyond what is needed to cover the
  repair paths — a `refcount_order` variant that end-to-end
  exercises sub-byte repair is **noted as future work** (the
  sub-byte paths are already unit-tested in phases 2–3).
- The snapshot/compression-aware recount fixtures (real shared
  clusters) — those belong with the deferred snapshot-aware tier.

## Open questions

### 1. Register the new fixtures in `manifest.json`?

**Resolved: yes.** The existing `check-qcow2-*` fixtures are in
`tests/manifest.json` (ids, paths, tags, sha256) and the tests
load them via `get_image` with hash verification. The new repair
fixtures follow the same pattern — hash-pinning matters more here
because a silently-changed corrupt fixture could mask a repair
bug. Add `repair-leaks` / `repair-all` / `repair-refuse` tags so
phase 8 can select by tier.

### 2. Capture `qemu-img -r` *output* per version, or just verify clean?

**Resolved: verify clean (+ capture the plain `check` detection
baseline).** The phase-8 oracle is "after `instar check --repair`,
`qemu-img check` is clean" — run against the system qemu-img, not
a captured per-version repair transcript. So phase 7 captures the
cross-version **detection** baseline (`--command check`, no `-r`)
for parity reference, and *verifies* (does not necessarily
archive per-version) that `qemu-img check -r` cleans each
repairable fixture. Archiving qemu's repair output per version is
unnecessary and brittle (allocation choices differ).

### 3. Where does the corrupt-bit fixture's "refuse" leave parity?

**Resolved: documented limitation, not a bug.** instar refuses
corrupt-bit-set images (phase-5 conservative gate); qemu-img -r
clears them. The fixture exists to test instar's *refusal* (no
corruption, `INCOMPLETE`), and the gap is recorded as future work
(lifting the gate to act on structurally-sound corrupt-flagged
images).

## Execution

| Step | Effort | Model | Repo | Brief for sub-agent |
|------|--------|-------|------|---------------------|
| 7a | high | opus | instar-testdata | Extend `custom/check-validation/create-corrupt-images.py` with the new fixtures: `qcow2-refcount-too-high`, `qcow2-stale-copied`, `qcow2-corrupt-bit-set`, `qcow2-snapshot-leak`, `qcow2-compressed-leak` (see the table + construction notes; reuse `parse_qcow2_header` / `read_l1_entry` / `read_l2_entry` / `write_be16` / `write_be64` / `create_base_image`). Add a `create_*` function each and call them from `main`. After generating, **verify each fixture empirically** and print a report: `qemu-img check <fixture>` must report the intended condition (leaks / corruptions / refcount-errors / COPIED / corrupt-bit), and for the repairable ones (`refcount-too-high`, `stale-copied`, plus the existing `leaked-cluster` and `refcount-zero`) `qemu-img check -r all <copy>` must exit 0/clean on a COPY (don't mutate the committed fixture). For the refuse fixtures, record what `qemu-img check` reports. Update the script's header docstring listing every fixture and its condition. Do NOT commit. Opus: the stale-COPIED and refcount-too-high hex injections must produce conditions qemu actually flags — iterate against `qemu-img check` output until each fixture is genuinely the intended corruption. |
| 7b | medium | sonnet | instar-testdata | Cross-version detection baselines. Run `make baselines-check` (or `scripts/generate-baselines.py --command check --no-commit`) so the new fixtures get captured across the qemu-img matrix, then `scripts/detect-profiles.py` to dedup. Report the resulting profile count per fixture (expect ~1–2). If the matrix run is prohibitively slow or the fixtures are not yet wired into the baseline image list, wire them in following the existing `check-validation` entries and note what was needed. Do NOT commit. (If the profile count is trivially 1 for every fixture, say so — the value is confirmation; do not pad the capture.) |
| 7c | medium | sonnet | instar | Register the new fixtures in `tests/manifest.json`, mirroring the existing `check-qcow2-*` entries (~lines 808-848): `id` (`check-qcow2-refcount-too-high`, `-stale-copied`, `-corrupt-bit-set`, `-snapshot-leak`, `-compressed-leak`), `path` (`custom/check-validation/<file>.qcow2`), `format` `qcow2`, an appropriate `safety`, `run_in_ci`, `description`, `tags` (include `check-validation` plus one of `repair-leaks` / `repair-all` / `repair-refuse`), and the real `sha256` (compute from the generated fixture). Keep the JSON valid and the existing entries untouched. Do NOT commit. |
| 7d | medium | sonnet | both | Verify + present TWO commits. (i) Confirm the fixtures exist and load: from the instar worktree, point `INSTAR_TESTDATA_PATH` at `../instar-testdata` and run the existing `TestCheckCorruptImages` detection tests plus a quick manual `instar check` on each new fixture to confirm instar's detection matches the intended condition; and confirm `qemu-img check -r all` cleans the repairable fixtures (on copies). (ii) Confirm `tests/manifest.json` parses and the `verify_image_hash` path resolves each new id (the hashes match the committed fixtures). (iii) Present two commits for my approval: one on `instar-testdata` `main` (generator + new fixtures + any captured baselines; message describing the fixture set and the empirical verification), and one on the instar `check-repair` branch (`manifest.json` registrations). Note the `instar-testdata` commit pushes to its GitLab remote — do not push without my go-ahead. |

## Agent guidance

### Execution model

Sub-agents do the work; the management session reviews the
generated fixtures (actually run `qemu-img check` on them), the
manifest diff, and the baseline profile counts, then commits.
7a touches the testdata repo and is the high-judgment step (the
hex injections must produce genuinely-flagged corruptions). The
testdata repo is a normal clone (not a worktree) on `main`.

### Model and effort notes

- **7a is high-effort opus**: constructing corruptions that qemu
  actually flags (especially stale-COPIED and refcount-too-high)
  requires iterating against `qemu-img check` and understanding
  the qcow2 refcount/COPIED layout.
- **7b/7c/7d are medium sonnet**: running the baseline pipeline,
  editing JSON, and the verify/commit ritual.

### Management session review checklist

- [ ] Each new fixture: `qemu-img check` reports the intended
      condition (run it yourself, don't trust the script log).
- [ ] Each repairable fixture: `qemu-img check -r all` on a copy
      exits clean.
- [ ] `instar check` on each fixture detects the intended
      condition (matches qemu's classification).
- [ ] `manifest.json` is valid; new ids resolve; sha256 matches
      the committed bytes; existing entries untouched.
- [ ] Baseline profile counts captured and reported (stability
      confirmed).
- [ ] Two commits, correct repos; `instar-testdata` not pushed
      without go-ahead.
- [ ] No instar source change (`check.bin` byte-identical).

## Administration and logistics

### Success criteria

* New corrupt fixtures exist in `instar-testdata/custom/check-validation/`,
  each verified by `qemu-img check` (condition) and — for the
  repairable ones — `qemu-img check -r all` (cleans).
* The fixtures are registered in `tests/manifest.json` with
  correct hashes and repair-tier tags.
* Cross-version detection baselines captured (profile counts
  reported).
* Two commits prepared (instar-testdata + instar); nothing pushed
  without approval.

### Future work created by this phase

- **Format-variant fixtures** (non-16-bit refcount widths, tiny
  clusters, v2 compat) to end-to-end exercise sub-byte repair.
- **Real-shared-cluster fixtures** (snapshots / packed
  compression) once the snapshot/compression-aware recount lands.
- **Lifting the corrupt-bit-set refusal** so instar can clear a
  structurally-sound corrupt flag like qemu-img -r.

### Findings during this work

- **End-to-end repair validated (first real proof).** Phases 4–5
  were only smoke-tested on healthy/refuse cases; here all four
  repairable fixtures were repaired by `instar check --repair` and
  then declared **clean by `qemu-img check`**: `leaked-cluster`
  (leaks tier), `refcount-zero` (all, raise 0→1),
  `refcount-too-high` (all, lower 2→1), `stale-copied` (all,
  refcount+COPIED). The refuse fixtures (`corrupt-bit-set`,
  `snapshot-leak`, `compressed-leak`) were left byte-identical.
- **`overlapping` is a *partial* repair, not byte-identical.** The
  fixture has an overlap (structural) *and* a leak. `--repair=all`
  refuses the all tier (`corruptions > 0`) but the leaks tier
  safely reclaims the genuine leak, leaving the overlap untouched;
  `qemu-img check` afterward shows the overlap remains and the
  leak is gone (not made worse), and instar reports "Repair did
  not complete" + exit 2. The phase table's "image byte-identical"
  for this row was corrected to "partial repair".
- **`corrupt-bit-set`: plain `qemu-img check` is silent (qemu
  10.0.8).** It does not re-flag `INCOMPAT_CORRUPT` on a read-only
  check; `qemu-img info` shows `corrupt: true` and write-open is
  refused. instar reads the bit directly and refuses, so the
  fixture still tests the intended refuse path; the manifest
  description notes the qemu quirk.
- **Manifest convention: no `sha256`.** The existing
  script-generated `check-validation` qcow2 entries carry
  `skip_qemu_img: true` and **no** hash (regeneration across qemu
  versions is not byte-identical), so the new entries follow that
  pattern rather than the plan's assumed sha256-pinning.

### Deferred in this phase

- **The 80-version `qemu-img check` baseline capture (step 7b) was
  not run.** Per the plan's own scoping note, corruption detection
  is stable across the matrix (a leak is a leak), the capture is
  ~1-profile and low-value, and phase 8's real oracle is the live
  system `qemu-img`. The fixtures + the `qemu-img check -r`-clean
  verification (done directly against qemu 10.0.8) are the
  load-bearing deliverables. Running the full matrix capture
  remains an optional follow-up if cross-version detection drift
  is ever suspected.

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`order.yml`. The master plan's phase-7 row is updated to "Landed"
once both commits are in.

### Back brief

Before executing any step, back brief the operator on your
understanding — especially that the fixtures must be empirically
verified against `qemu-img check` (+ `-r`), the success metric is
post-repair cleanliness not byte-identity, and the work spans two
repos with two separate commits.
