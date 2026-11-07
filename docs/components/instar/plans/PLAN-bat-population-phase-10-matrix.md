# Phase 10: cross-version matrix and derived-profile validation

Phase 10 of [PLAN-bat-population.md](/components/instar/plans/PLAN-bat-population/). Read its
Situation (in particular "A standing caution about the baseline tooling",
which is this phase's reason to exist) and the phase 10 paragraph of
"Sequencing rationale". Also read
[the phase 2 plan](/components/instar/plans/PLAN-bat-population-phase-02-fixtures/)'s decision 5,
which moved both pieces of work here and says why, and
[the phase 3 plan](/components/instar/plans/PLAN-bat-population-phase-03-host-create/)'s
decision 5, which moved the monolithicFlat and preallocation create cases
here for the same reason. This file assumes them.

Two repositories are in play. `instar-testdata` owns the generator, the
baselines and the derived profiles, and takes direct commits to `main`
rather than merge requests. `instar` owns the mirror of the generator's
case list and the tests that consume the baselines. The ordering between
them is decision 3, and it is the part of this phase most likely to go
wrong.

Planning effort: medium, as the master plan sets for phase 10 ("only
tests"). The work is running established tooling and reading its output;
what needed judgement was deciding how to generate a handful of baselines
without rewriting thousands, and that is settled in decisions 1 and 2.

Review effort: high. The master plan's caution is that this exact
procedure has produced silently wrong derived profiles twice, so the
review that matters is of the validator and of the before/after file
counts, not of the baseline bytes.

## What the survey found

Surveyed on 2026-11-06 against `instar` `develop` at `56a6303c` (phase 9
merged as `3b74ba8d`, #877) and `instar-testdata` `main` at `d2f9467719`.
The master plan's phase 10 premises hold, but the survey found four
things the paragraph does not say, and two of them change the shape of
the phase. Corrections to the master plan's phase 10 paragraph and to the
`index.md` row are made in this planning commit, so a later step does not
redo them.

### The generator cannot be aimed at one image or one case

`instar-testdata/scripts/generate-baselines.py` takes `--version`,
`--manifest`, `--arch`, `--all-images`, `--output-type`, `--command` and
`--no-commit` (`:3100-3136`). There is no `--image` and no `--case`
filter, and nothing skips an output that already exists: the per-version
loop walks every image `load_test_images` returns (`:1004-1016`) and
overwrites. So "add baselines for phase 2's five fixtures" is today the
same command as "regenerate every safe image's baselines", and
"add the fixed VHDX create cases" is the same command as "rewrite all
108 create baselines for all 80 versions".

That matters twice over:

* **The baselines are a long way behind the manifest.** `instar`'s
  `tests/manifest.json` holds 218 images, 124 of them `safety: safe`.
  `expected-outputs/check-human/raw/10.0.0/` holds 38, and
  `expected-outputs/qemu-img-json/raw/10.0.0/` holds 71; the
  `check-human` `version-map.json` is stamped `2026-08-29`. A plain
  `--command check` run would therefore add roughly 86 images' baselines
  across 80 versions as a side effect, un-skipping tests across the
  suite that have never run. That is issue #845's territory, not this
  phase's (see *Out of scope*).
* **Regenerating create baselines rewrites 400 files to no purpose.**
  qemu-img writes a random VMDK `cid` on every create, so the five
  `vmdk` create cases' `stdout` differs on every run and across every
  version (measured: `create-info-json/vmdk/10.0.0/1M-default.stdout.txt`
  vs `10.0.1`'s differ in exactly one line, `"cid"`). A full
  `--command create` run would change 400 vmdk baseline files and then
  all 80 create profiles, with no information in the diff.

Decision 1 adds the two filters.

### `create-info-json`'s 80 profiles are the VMDK `cid`, and are harmless

`expected-outputs/create-info-json/` has 80 profiles for 80 versions --
no deduplication at all -- while `check-human`, `map-human`, `map-json`,
`measure-human` and `measure-json` each have exactly one profile for all
80. Both looked like the "silently wrong profiles" failure mode and
neither is:

* The single-profile types are genuine. Sampled `6.0.0` against `10.2.0`
  for three images in each of `check-human`, `map-human` and
  `measure-json`: byte-identical every time. qemu-img's `check`, `map`
  and `measure` output really has not moved across those images in nine
  releases.
* The 80 create profiles are the `cid`. `detect-profiles.py`'s
  `hash_version_multi_bucket` (`:203-236`) deliberately hashes only
  `return_code` and `timed_out` out of each `meta.json`, so timestamps
  are excluded -- but it hashes `stdout` whole, and the five vmdk cases'
  `stdout` carries the random `cid`. Confirmed by computing the hash for
  `10.0.0` and `10.0.1` directly and diffing every contributing part: the
  only differences are those five files, in that one field.

Nothing consumes the redundancy incorrectly -- `instar`'s
`tests/helpers/info_json.py:39` already strips `cid` and `parent-cid` for
vmdk before comparing -- so this is recorded in Future work, not fixed
here. It does mean the validator must accept 80 create profiles as
correct, and that adding a sixth vmdk create case (monolithicFlat) will
not make it worse.

### The two historical profile failures, and what a validator must check

Both are in `instar-testdata`'s history, both are fixed, and both
describe the check this phase should automate:

* `ec01915058` ("detect-profiles: fix preserve-baseline corruption"):
  the preserve-hand-maintained-baselines path compared ids at mismatched
  granularity, so it matched nothing, flagged every image as hand
  maintained, and stamped one stale snapshot into every regenerated
  profile -- 41 of 44 images wrong while `raw/` stayed correct. Its
  verification was "regeneration now produces profiles byte-identical to
  `raw/` for 154/154 sampled checks".
* `03c379a08a` ("Stop the profile copy silently dropping outputs"):
  `copy_multi_bucket_version_to_profile` flattened buckets under bare
  filenames, so `create-info-json`'s five buckets all contributing a
  `1G-default` left only the last in sorted order. 36 files in, 25 out,
  no warning. The fix qualifies a basename with its bucket only for
  output types where basenames collide, which is why `create-info-json`
  profiles hold `vhdx-16M-fixed.stdout.txt` while `map-json` profiles
  hold bare image ids.

Neither check is committed anywhere. There is no validator script, no
`make` target for one, and `instar-testdata` has no CI of any kind (no
`.github/`, no `.gitlab-ci.yml`, no pre-commit config), so a validator
here is a tool a human or an agent runs, not a gate. Decision 4 defines
it.

### Smaller findings, all verified

* **`CREATE_CASES` holds none of this plan's output.** Both copies --
  `instar-testdata/scripts/generate-baselines.py:404` and its mirror at
  `instar/tests/test_create.py:2572` -- are identical today and have no
  vhdx `fixed` case, no vmdk `monolithicFlat` case, and no non-qcow2
  preallocation case. `vhdx`'s comment still reads "vhdx is dynamic-only
  in instar", which phase 7 made false. `vhd` already has `1M-fixed` and
  `16M-fixed`, so the fixed-subformat baseline shape is established.
* **`test_create_cases_match_baselines` asserts set equality in both
  directions** (`instar/tests/test_create.py:2741-2774`), which is what
  makes the cross-repo ordering a real problem rather than a cosmetic
  one. It skips only when a target has no baselines at all, and `vhdx`
  has 80 versions of them.
* **`TestProfileSelectionMatrix` hardcodes six expected info profile
  names** (`instar/tests/test_version_detection.py:268-276`), so a
  shifted info profile boundary breaks it. Measured whether phase 2's
  fixtures would shift one: `qemu-img info --output=json` on
  `vhdx-fixed-16M.vhdx` is identical for 6.0.0 and 7.2.19, and identical
  again for 8.0.0, 10.0.0 and 10.2.0, differing between them only by the
  `children` block qemu added in 8.0.0 -- an *existing* boundary
  (`profile-8-0-0`). So no new boundary is expected. Verify, do not
  assume: step 10d runs `detect-profiles.py --dry-run` and compares the
  profile list before and after.
* **The `$FILENAME` normalisation will not cover a monolithicFlat
  baseline.** `generate_create_baseline` substitutes the literal tmp
  path (`:1636-1639`). A monolithicFlat create writes a second file whose
  name is the stem plus `-flat.vmdk`, which that literal will not match,
  so the baseline would embed a host-specific path; and the generator
  deletes only `tmp_file` (`:1676`), so the extent leaks into tmp on
  every version. Both are fixed in step 10a.
* **`info` and `check` baselines are flat; `map`, `measure`, `dd`,
  `resize` and `create` are bucketed.** `expected-outputs/check-human/`
  and `qemu-img-{human,json}/` hold `raw/<version>/<image>.*` where
  `raw` means "all formats"; `map-*`, `measure-*` and `create-info-json`
  hold `<format-or-target>/<version>/...` where a sibling `raw/` means
  "the raw format". The word means two different things in the same
  tree. `detect-profiles.py`'s own docstring (`:9-34`) documents only the
  first shape and is stale.
* **There is no `baselines-create` or `baselines-map` Makefile target**,
  although three other targets' comments cite "the same policy as
  `baselines-create`" (`Makefile:197`, `:209`, `:223`). Those two
  commands have only ever been run by hand. Step 10a adds them.
* **Phase 2's five fixtures are registered and ready.** All five are in
  `instar/tests/manifest.json` as `safety: safe`, `run_in_ci: true`,
  with sha256 values, and none has a baseline anywhere under
  `expected-outputs/`. All 329 LFS objects in the testdata clone are
  materialised.

## Decisions

1. **Add `--image` and `--case` filters to the generator rather than
   mass-regenerate.** Both repeatable, both matching exactly (no
   globbing), both erroring if a given id or case name matches nothing --
   a silent no-match filter would produce a green run that generated
   nothing, which is the hardest kind of failure to notice: the command
   succeeds, prints per-version progress, and leaves the tree
   unchanged.
   `--image` filters the manifest-derived image list; `--case` filters
   the per-target case list for `--command create` (and is accepted for
   any case-driven command, so `resize` and `dd` inherit it). This is the
   decision that makes the phase possible at all: without it, "add five
   fixtures' baselines" is a ~10,000-invocation run that un-skips
   suite-wide tests, and "add ten create cases" is a 400-file no-op diff.
   The alternative -- generate everything and accept the fallout -- was
   rejected because the fallout is exactly issue #845, which deserves its
   own phase with its own review.

2. **Four commands get fixture baselines: `info`, `check`, `map`,
   `measure`.** These are the four phase 2 named as skipping the new
   fixtures (`tests/test_info_safe.py:75-82`, `test_map.py`'s and
   `test_measure.py`'s "no baseline meta" skips). `dd`, `resize`,
   `amend`, `bitmap`, `snapshot-list`, `rebase`, `commit` and `compare`
   are not added: they are case-driven or qcow2-only matrices, not
   per-image ones, and nothing in `instar` reads a per-image baseline for
   them for a VHDX source.

3. **`instar-testdata` pushes only once the `instar` branch is finished,
   and the red window is accepted, not engineered around.** Because
   `test_create_cases_match_baselines` asserts equality in both
   directions and `tools/ci/prepare-testdata.sh` clones testdata `main`
   unpinned, there is no ordering that keeps `develop` green: baselines
   first fails `missing_from_mirror`, mirror first fails
   `missing_from_disk`. Phase 2's decision 5 already accepted this. So:
   every testdata commit is made locally and left unpushed until the
   `instar` pull request is written and verified against the local
   testdata tree; then testdata is rebased and pushed, then the `instar`
   PR's CI is run (it is green, because it carries the matching mirror)
   and merged. `develop` is red for one queue cycle, about 76 minutes,
   and only for other people's PRs. The rejected alternative was a
   `PENDING_BASELINE_CASES` escape hatch in the instar test: it trades a
   76-minute window for a permanent hole in the only guard that catches
   mirror drift.

4. **The validator asserts byte-identity between each version's source
   buckets and the profile its `version-map.json` names.** One new
   script, `instar-testdata/scripts/validate-profiles.py`, for every
   output type: read `version-map.json`, and for each mapped version and
   each file in each of that version's source bucket directories, assert
   the profile directory holds a byte-identical file under the name
   `detect-profiles.py` would have given it (bare, or bucket-qualified
   where `output_type_needs_bucket_prefix` says the type qualifies).
   Report, per output type, versions checked, files compared, mismatches,
   files present in the profile with no source, and sources with no
   profile file. Exit non-zero on any of the last three. The two
   historical failures are each caught by one of those counts: the
   `03c379a08a` dropped-output bug by "sources with no profile file", the
   `ec01915058` stale-snapshot bug by "mismatches". It takes
   `--output-type` and prints its totals, because the totals in a commit
   body are the evidence the regeneration was sound. The deliberate
   exception is the hand-maintained `luks-v1`/`luks-v2` goldens, which
   have no `raw/` source by design -- the script knows them by that
   property (no source file anywhere), not by name.

5. **`preallocation=metadata` gets no create case; `falloc` and `full`
   do.** The metadata refusal for vmdk, vpc and vhdx is already pinned
   across all 80 builds by phase 1's `tests/golden/qemu-create-pins.json`,
   and a baseline whose `create_return_code` is non-zero makes
   `instar`'s matrix test skip, so the files would buy nothing. `falloc`
   and `full` are added for all three formats, at 1M, matching the
   qcow2 cases' size discipline -- their whole point is that qemu
   accepts them and the resulting image is indistinguishable from the
   default, which is a claim a cross-version baseline can hold and a pin
   cannot.

6. **Three fixed VHDX create cases, at the shapes phase 1 pinned.**
   `16M-fixed` and `17M-fixed` (whole blocks, and a partial last block)
   with `subformat=fixed`, and `16M-fixed-bsz-off` with
   `subformat=fixed,block_state_zero=off`. Capped at 17M for the same
   disk-economy reason the `vhd` fixed cases are capped at 16M: these are
   dense files written 80 times. `block_state_zero=on` is not a separate
   case -- it is the default, so `16M-fixed` already covers it.

7. **No `KNOWN_WRITER_DIVERGENCES` entries are added up front.** Phase 7
   landed `create -f vhdx -o subformat=fixed` tested against every fixed
   pin case "with nothing masked", so instar is expected to match qemu
   byte for byte on all three new vhdx cases. If a new case does
   diverge, that is a finding to report to the management session before
   it is tabled -- it means phase 7 left a gap -- not a line to add
   quietly. The same applies to monolithicFlat, which phase 3 tested
   against the host qemu-img.

## Step plan

Step 10g was added mid-phase, with the operator's agreement, for the
defect 10c's validator found in `detect-profiles.py`; it is listed
after 10f in the table but runs before it, because 10f documents the
procedure and 10g changes it. Steps 10a to 10d and 10g work in the
`instar-testdata` clone at
`/srv/kasm_profiles/mikal/vscode/src/shakenfist/instar-testdata`, which
takes direct commits to `main`; rebase before committing, because the
fuzz automation moves `main` daily. **No step pushes.** Steps 10e and
10f work in this worktree. 10b and 10d both run the generator and both
touch `expected-outputs/`, so they cannot run concurrently; 10c only
reads it. Each step is its own commit.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 10a | high | opus | none | In `instar-testdata`, make the generator aimable and fix two monolithicFlat hazards. **(1) `scripts/generate-baselines.py`:** add `--image ID` (repeatable, `action='append'`) and `--case NAME` (repeatable). `--image` filters the list `load_test_images` returns (`:1004-1016`) to exactly those `id` values; `--case` filters the per-target case lists the `create` branch of the main loop walks (and any other case-driven command that iterates a `(case_name, ...)` tuple list) to exactly those case names. Both match exactly -- no globbing, no prefix matching. If any supplied id or case name matches nothing, print what was asked for and what is available and `sys.exit(1)`: a filter that silently matches nothing produces a successful run that generated no files, which is the failure this filter exists to make impossible. Document both in the module docstring's Options and Examples blocks. **(2) same file, `generate_create_baseline` (`:~1600-1680`):** the `$FILENAME` substitution replaces only the literal `str(tmp_file)` (`:1636-1639`), so a monolithicFlat descriptor's `children[*].info.filename` -- which is the tmp stem plus `-flat.vmdk` -- survives into the baseline as a host path. Substitute the stem-derived flat extent name too (to `$FILENAME-flat` or similar; pick one and say so in a comment), and extend the cleanup at `:1676` to unlink any sibling file the create produced, so the extent does not leak into tmp on all 80 versions. Verify by creating a monolithicFlat image by hand with `qemu-img-binaries/x86_64/10.2.0/qemu-img` and checking what `info --output=json` prints for both filename fields. **(3) `Makefile`:** add `baselines-create` and `baselines-map` targets calling the generator with `--command create` / `--command map` and `--no-commit`, in the style of `baselines-resize` (`:198`) including its "deliberately not wired into the umbrella `baselines:` target" comment -- three other targets' comments already cite a `baselines-create` that does not exist (`:197`, `:209`, `:223`). Do not add them to the `baselines:` aggregate. Verification you must run and report: `python3 -I scripts/generate-baselines.py --command create --version 10.2.0 --case 1M-default --no-commit`, then `git status --porcelain expected-outputs | wc -l`, and show that the number of touched files is exactly the number of buckets holding a `1M-default` case (expect 5 buckets x 3 files = 15, minus any whose content is unchanged -- vmdk's will change because of the random cid). Then `git checkout -- expected-outputs`. Also run `--command info --version 10.2.0 --image vhdx-fixed-16M --no-commit` and show that exactly 3 files appear, under `expected-outputs/qemu-img-*/raw/10.2.0/`. Leave everything uncommitted; report both file counts, the flat-extent filename before and after substitution, and any place you found a case list the `--case` filter does not reach. ≤400 words. |
| 10b | medium | sonnet | none | In `instar-testdata`, add this plan's create cases and generate only those baselines. Requires 10a. **(1) `scripts/generate-baselines.py`, `CREATE_CASES` (`:404`):** add to `vhdx` -- `('16M-fixed', '16M', ['subformat=fixed'])`, `('17M-fixed', '17M', ['subformat=fixed'])`, `('16M-fixed-bsz-off', '16M', ['subformat=fixed,block_state_zero=off'])`; add to `vmdk` -- `('1M-monolithic-flat', '1M', ['subformat=monolithicFlat'])`; add to each of `vmdk`, `vhd` and `vhdx` -- `('1M-prealloc-falloc', '1M', ['preallocation=falloc'])` and `('1M-prealloc-full', '1M', ['preallocation=full'])`. Ten new cases in total. Do **not** add a `preallocation=metadata` case (decision 5). Replace the stale `vhdx` comment "vhdx is dynamic-only in instar; qemu defaults match" with one that says fixed is supported since the fixed-subformat work and that these cases are capped at 17M because the files are dense. **(2)** Generate exactly those cases across all 80 builds: `python3 -I scripts/generate-baselines.py --command create --case 16M-fixed --case 17M-fixed --case 16M-fixed-bsz-off --case 1M-monolithic-flat --case 1M-prealloc-falloc --case 1M-prealloc-full --no-commit`. Note that the three prealloc case names are shared by vmdk, vhd and vhdx, so one `--case` covers all three targets. **(3)** Run `python3 -I scripts/detect-profiles.py --no-commit` and record the before/after profile count for `create-info-json` (expect 80 both times -- decision/finding: the random VMDK `cid` prevents dedup). **(4)** Three of the six distinct new case names already exist for another target -- `16M-fixed` for `vhd`, and `1M-prealloc-falloc` and `1M-prealloc-full` for `qcow2` -- and `--case` is a cross-target filter, so the run also re-measures those three existing case-instances at all 80 versions: about 720 files whose only change is `meta.json`'s `generated_at`. Prove they are unchanged where it matters (every re-run twin's `stdout.txt` and `stderr.txt` byte-identical to what git has) and then `git checkout --` their paths, so the commit holds the new cases and nothing else. If any twin's `stdout` or `stderr` did change, stop and report: the generator is not deterministic for that case and that is a finding. Then check the new baselines: confirm `git status --porcelain` lists only additions under the ten new cases' paths plus regenerated profiles; confirm the `1M-monolithic-flat` baseline carries `$FILENAME-flat` and no host path (grep it for `/srv/`, `/tmp` and your username); confirm `meta.json`'s `create_return_code` is 0 for all ten on 10.2.0 and note any version where it is not, with the stderr. **(5)** `git add` and commit (do not push -- decision 3) with a message naming the ten cases, the profile counts, and the file count added. Report: the ten case names, added file count, any non-zero `create_return_code` by version, and the before/after `create-info-json` profile counts. ≤350 words. |
| 10c | high | opus | none | In `instar-testdata`, write the derived-profile validator. Independent of 10b and 10d, but run it after both. New file `scripts/validate-profiles.py`, plus a `validate-profiles` Makefile target. Contract: for each output type under `expected-outputs/` (or just the one given by `--output-type`), read `version-map.json`, and for every version in `version_to_profile`, for every file in every source bucket directory for that version, assert the mapped profile directory holds a byte-identical file. Import the naming rules from `detect-profiles.py` rather than reimplementing them -- `output_type_needs_bucket_prefix` and whatever builds the destination name -- so the validator cannot drift from the generator; if that needs a small refactor in `detect-profiles.py` to expose them, do it, and keep `detect-profiles.py`'s behaviour identical. Report per output type: versions checked, files compared, byte mismatches, profile files with no source, and source files with no profile file. Exit non-zero if any of the last three is non-zero. The one legitimate exception is the hand-maintained `luks-v1`/`luks-v2` goldens, which exist only in `profiles/` by design: recognise them by having no source file in any bucket for any version, not by name, and count them in a separate "hand-maintained" line rather than as an error. Read the two commit messages that describe the failures this must catch before you start: `git log -1 ec01915058` (stale snapshot stamped into every profile; 41 of 44 images wrong while `raw/` was right) and `git log -1 03c379a08a` (bucket flattening dropped 11 of 36 files silently). Prove it catches both: temporarily corrupt one profile file's bytes and confirm a mismatch is reported; temporarily delete one and confirm "source files with no profile file" catches it; restore both and show `git status` clean afterwards. Note that `create-info-json` legitimately has 80 profiles for 80 versions (random VMDK `cid`) and `check-human` / `map-*` / `measure-*` legitimately have one -- neither is an error. `instar-testdata` has no CI and no pre-commit config, so this is a tool, not a gate; say so in the module docstring. Commit (do not push); report the full per-output-type table from a clean run, and the two deliberate-corruption results. ≤400 words. |
| 10d | medium | sonnet | none | In `instar-testdata`, generate per-image cross-version baselines for phase 2's five fixtures, and only those. Requires 10a; run after 10b. The five image ids are `vhdx-fixed-16M`, `vhdx-fixed-16M-bsz-off-data`, `vhdx-fixed-17M`, `vhdx-fixed-17M-bsz-off-data`, `vhdx-dynamic-131041M-block-1M`. **(1)** Record the current profile list for every output type you are about to touch: `python3 -I scripts/detect-profiles.py --dry-run > /path/to/scratch/profiles-before.txt`. **(2)** For each of `info`, `check`, `map`, `measure` (decision 2), run the generator across all 80 builds with the five `--image` filters and `--no-commit`. **(3)** Re-run `detect-profiles.py --no-commit` and diff the profile list against `profiles-before.txt`. The expectation, measured during planning, is that no profile boundary moves: these fixtures' `qemu-img info` output changes only at the 8.0.0 `children` boundary, which already exists. **If any boundary does move, stop and report before committing** -- `instar`'s `tests/test_version_detection.py:268-276` hardcodes six expected info profile names and step 10e would have to change them. **(4)** Sanity-check the new baselines: confirm each of the five images now has files under the expected directories (`qemu-img-{human,json}/raw/<version>/`, `check-{human,json}/raw/<version>/`, `map-{human,json}/vhdx/<version>/`, `measure-{human,json}/vhdx/<version>/__<target>`), note for each image and command any version whose `meta.json` records a non-zero return code, and say plainly whether `qemu-img check` refuses the 17M fixtures on any version (instar #797 predicts it may -- that is a finding, not a failure). **(5)** Run `scripts/validate-profiles.py` from 10c over everything and paste its table. **(6)** Commit (do not push) with a message naming the five images, the four commands, the added file count, and the validator totals. Report: added file count per output type, the profile-list diff (or "no change"), the non-zero-return-code table, and the validator output. ≤400 words. |
| 10e | high | opus | none | In the `instar` worktree, mirror the new create cases and consume the new fixture baselines. Requires 10b and 10d committed locally in the testdata clone; point the suite at it with `INSTAR_TESTDATA_PATH` (or whatever `tests/base.py` reads -- check) so this step's tests see the uncommitted-upstream baselines. **(1) `tests/test_create.py:2572`:** add the same ten entries 10b added to `CREATE_CASES`, in the same order, and update the stale `vhdx` comment to match. The mirror and the generator must be identical -- `test_create_cases_match_baselines` (`:2741`) asserts set equality in both directions. Add no `KNOWN_WRITER_DIVERGENCES` entries up front (decision 7): phase 7 and phase 3 tested these shapes against qemu with nothing masked, so if a generated baseline test fails, that is a gap in a landed phase and a finding to report to the management session -- do not mask it. **(2)** Run the whole `TestCreateBaselineMatrix` class and report every new test's result individually, including skips and why. **(3)** Confirm the five phase 2 fixtures now run rather than being absent or skipped in `tests/test_info_safe.py`, `tests/test_map.py` and `tests/test_measure.py`. The three modules behave differently and the counts you report must match: `test_map` and `test_measure` raise `self.skipTest('no baseline meta: ...')` per scenario, so report a before/after **skip** count for the five ids; `test_info_safe` instead only builds a scenario where the baseline file already exists, so the five ids generate no test at all today and what you report is a before/after **scenario** count. Phase 2's plan calls all three "skips", which is right for two of them. If any of the five now runs and *fails*, that is the most important thing in your report: say which image, which command, and paste both outputs. **(4)** If and only if 10d reported a moved profile boundary, update `TestProfileSelectionMatrix.MATRIX` (`tests/test_version_detection.py:268-276`) to the measured names and say which rows changed and why. **(5)** Run `pre-commit run --all-files` and the three affected test modules plus `test_create.py` and `test_version_detection.py` via `tests/.venv/bin/stestr` with `INSTAR_BINARY_PATH` set; replay any VHDX contention flake at `--concurrency 1` before reporting it. Leave uncommitted; report test names and counts, the skip-count deltas, and any failure in full. ≤400 words. |
| 10g | high | opus | none | In `instar-testdata`, fix the defect 10c's validator found, which the operator added to this phase's scope. `detect-profiles.py` builds `raw_image_ids` from bare source filenames (`:572-581`, `f.stem.rsplit('.', 1)[0]`) but reads candidate ids from *profile* filenames (`:596-605`), and for `create-info-json` -- the only bucket-qualified output type -- those are qualified (`vhdx-16M-fixed` against a raw id of `16M-fixed`). All 46 ids mismatch, so all 7360 profile `.txt` files are classified as hand maintained, and a regeneration prints "Preserving 7360 manually-maintained baseline(s)" and restores the previous profile contents over the fresh copy: a raw change never reaches the profiles, and `instar`'s `tests/test_create.py` reads the profiles. This is the same mismatched-id-granularity bug as the first of the two historical failures, reintroduced by the fix for the second. **The fix:** make both sides use the one naming rule 10c extracted -- build `raw_image_ids` by running each source file through `profile_filename(f, bucket_prefix)` and stripping the `.stdout`/`.stderr`/`.meta` suffix, so a bucket-qualified type compares qualified ids on both sides. Do not special-case `create-info-json` and do not strip prefixes off profile names; the point is that one function decides the name. **Verification, all of which must be in your report:** (1) `luks-v1` and `luks-v2` are still preserved -- they are the only genuinely hand-maintained ids and they have no source file anywhere, so the fix must not touch them; show the "Preserving N" line before and after, where N goes from 7360+56 to 56. (2) On a scratch copy of the repository, perturb one byte of one existing raw create `stdout.txt`, run `detect-profiles.py`, and show that before the fix `validate-profiles.py` reports a mismatch (the stale profile won) and after the fix it is clean (the fresh copy won). This is the whole point of the change; a report without it is not evidence. (3) Regenerate the real `create-info-json` profiles with the fix in place and confirm the only changes are profile hashes and timestamps -- raw and profiles are consistent today, so a content change would mean the fix is wrong. (4) `make validate-profiles` still reports 0 mismatches, 0 orphans either way, across every output type, with a compared count no lower than 163932. (5) `python3 -I scripts/detect-profiles.py --dry-run` groups versions identically before and after; compare the version-to-profile *grouping*, not the hash strings, which legitimately move. Commit (do not push). |
| 10h | high | opus | none | In `instar-testdata`, close the second defect of the same family, which the operator also added to this phase's scope. **The defect:** `detect-profiles.py` keys `preserved_baselines` by *old profile directory name* (`:591-605`), but a profile's name changes whenever its boundary moves, so the lookup misses and the hand-authored goldens are dropped. Measured: deleting one version's raw baselines renames `profile-6-0-0` to `profile-6-0-1`, and `qemu-img-human`'s luks goldens go from 28 files to 24. `detect-profiles.py` auto-commits unless `--no-commit`, so the `WARNING` it prints scrolls past. **And the validator does not catch it:** `validate-profiles.py` reports the hand-maintained count but asserts nothing about it, so that run still exits 0. **Two changes, both required.** (1) Re-key the preserved content by **version** rather than by profile name: each old profile directory has a `versions.json` listing the versions it covered, so build `{version: {filename: content}}` from them, and when writing a new profile restore from the representative version whose files were just copied (falling back to any other version in the group that has content). Versions are stable identifiers; profile names are derived and move. Keep the property the existing comment protects -- the same hand-maintained id may legitimately hold different content for different qemu versions -- which per-version keying preserves exactly, and which the current per-profile-name keying only approximates. (2) Make `validate-profiles.py` fail on a dropped golden. Measure the right invariant before you encode it: establish whether every profile of an output type is expected to hold the same set of hand-maintained ids, and report what you measured. If it is, assert it, so a profile missing an id its siblings have is a reported problem and a non-zero exit. **Verification, all of which must be in your report:** (a) reproduce the 28-to-24 drop on a scratch copy with the current code, then show the re-keyed code keeps all 28 across the same boundary move; (b) show the hardened validator exits non-zero on a tree where a profile is missing a golden, and names it; (c) `make validate-profiles` over the real tree still reports 0 mismatches, 0 orphans either way, 56 hand-maintained, with at least 163932 files compared; (d) `detect-profiles.py --dry-run` groups versions identically before and after, comparing the grouping rather than the hash strings; (e) a real regeneration changes no profile `.txt` or `.meta.json` content, only hashes and timestamps. Commit (do not push). |
| 10f | medium | sonnet | none | In the `instar` worktree, document the procedure this phase automated. **(1) `docs/testing.md`:** document how to add a baseline for one image or one create case now that the generator takes `--image` and `--case`, and that the derived profiles must be validated with `instar-testdata`'s `scripts/validate-profiles.py` after any regeneration, with the reason (the two historical silent failures, described by their shape, not by commit hash). Keep it short and link rather than duplicate. **(2) `docs/testing.md` again:** note that the create-case list is mirrored in two repositories and that the two must land together, and what the symptom of drift is (`test_create_cases_match_baselines` failing in one direction or the other). **(3) `CHANGELOG.md`:** one entry for the new cross-version coverage -- fixed VHDX create cases, monolithicFlat, non-qcow2 preallocation, and the five VHDX fixtures' per-image baselines. **(4)** Do not touch `docs/format-coverage.md`, `docs/create.md` or `docs/format-internals.md`: phase 12 owns the cross-cutting pages. Do not add plan, phase or decision numbers or `PLAN-*.md` references to any code, comment or user-facing doc. Check whether `AGENTS.md` states a convention this changes -- "how to add a baseline" is a convention, so if it says anything about regenerating baselines, it needs one line and a link to `docs/testing.md`, not a copy. Run `pre-commit run --all-files`. Leave uncommitted; report every file changed and the exact `AGENTS.md` wording you added, if any. ≤300 words. |

## Risks and mitigations

* **The `--image` or `--case` filter silently matches nothing, and a
  green run generates no baselines.** It would be invisible: the
  generator prints per-version progress and exits 0 either way.
  Mitigation:
  decision 1 requires both filters to exit non-zero on a no-match, and
  10a's verification is a file count, not a successful exit. The
  management session re-runs one filtered invocation and checks
  `git status --porcelain | wc -l` itself.
* **A regeneration moves an info profile boundary and breaks the
  hardcoded matrix.** `tests/test_version_detection.py:268-276` names
  six profiles. Planning measured that phase 2's fixtures should not
  move a boundary, but measured on one fixture. Mitigation: 10d captures
  the profile list before and after and stops rather than committing if
  it changed; 10e has an explicit conditional step to update the matrix
  with measured names.
* **The red `develop` window outlasts one queue cycle.** Decision 3
  accepts about 76 minutes. If the `instar` PR needs a second round the
  window doubles, and other people's PRs fail meanwhile for a reason
  that is not theirs. Mitigation: nothing is pushed to testdata until
  10e has run green locally against the local testdata tree; the push
  and the PR are a single management-session action; and if the PR does
  go red, reverting the testdata commit is one `git revert` and a push,
  which is the documented fallback, not a judgement call.
* **The validator passes because it checks nothing.** A validator that
  resolves names wrongly reports "0 mismatches" over an empty
  comparison, which looks exactly like success -- and this phase exists
  because this family of bug has shipped twice. Mitigation: 10c must
  report files-compared counts (an empty comparison is visible as a
  zero), and must demonstrate a caught corruption and a caught deletion.
  The management session repeats one of the two deliberate corruptions.
* **A new create case diverges from qemu and gets masked.** The cheapest
  way to make `TestCreateBaselineMatrix` green is a
  `KNOWN_WRITER_DIVERGENCES` entry, which would silently retract part of
  phase 7 or phase 3. Mitigation: decision 7 forbids adding entries in
  this phase, and 10e's brief says a divergence is a finding to escalate.
* **The monolithicFlat baseline embeds a host path and is unusable on
  another machine.** Mitigation: 10a fixes the substitution and verifies
  it by hand on one version; 10b greps the generated baseline for `/tmp`
  and for the username before committing.

## Definition of done

Each item is a command and its expected result. Testdata commands run in
the `instar-testdata` clone, instar commands in this worktree with
`INSTAR_BINARY_PATH` set.

* `python3 -I scripts/generate-baselines.py --command info --version
  10.2.0 --image no-such-image --no-commit` exits non-zero and names the
  unmatched id. The same with `--case no-such-case` does likewise.
* `python3 -I scripts/generate-baselines.py --command create --version
  10.2.0 --case 16M-fixed --no-commit` touches two files and no others:
  `create-info-json/vhdx/10.2.0/16M-fixed.meta.json` and
  `create-info-json/vhd/10.2.0/16M-fixed.meta.json`. Both targets have a
  case of that name -- `--case` filters by name across targets -- and
  only each `meta.json` changes, because the re-measured `stdout.txt` and
  `stderr.txt` come out byte-identical. (This criterion originally read
  "only under `.../vhdx/10.2.0/`, and exactly three of them", which was
  wrong on both counts; it was written before the survey's twin-name
  finding was understood.)
* `make validate-profiles` exits 0, and its output shows a non-zero
  files-compared count for every output type, zero mismatches, zero
  profile files with no source, and zero source files with no profile
  file (the hand-maintained luks goldens are counted on their own line).
* Deliberately corrupting one byte of one profile `stdout.txt` makes
  `make validate-profiles` exit non-zero and name that file; deleting one
  makes it exit non-zero and name that file. `git status --short` is
  clean after restoring both.
* Each of the ten new create cases has 80 versions x 3 files under
  `expected-outputs/create-info-json/<target>/<version>/`, and
  `grep -rl "$(whoami)\|/tmp/" expected-outputs/create-info-json` matches
  nothing.
* Each of the five phase 2 fixtures has baselines for `info`, `check`,
  `map` and `measure` at all 80 versions, and
  `python3 -I scripts/detect-profiles.py --dry-run` reports the same
  profile list as before this phase, for every output type -- or, if it
  does not, `tests/test_version_detection.py`'s `MATRIX` names the new
  profiles and that test passes.
* `stestr run test_create.TestCreateBaselineMatrix` passes with no new
  `KNOWN_WRITER_DIVERGENCES` entry, and
  `test_create_cases_match_baselines` passes rather than skips.
* `tests/test_info_safe.py`, `tests/test_map.py` and
  `tests/test_measure.py` each run, rather than skip, every scenario for
  the five fixture image ids, and pass.
* `tests/test_version_detection.py` passes.
* `make instar`, `make lint`, `pre-commit run --all-files` and the full
  integration suite pass with 0 failures. (The suite is zero-fail on
  healthy testdata; any failure is this phase's until bisected against
  testdata `main`.)
* `docs/testing.md` states how to generate a baseline for one image and
  one case, and that profiles are validated after regeneration; no
  plan-file reference, phase number or decision number appears in any
  code, comment or user-facing document.

## Execution notes

* **Back brief (Michael Still, 2026-11-07, Australia/Brisbane).** All
  four gates answered with the recommendation: decision 1 adds the
  `--image` and `--case` filters rather than mass-regenerating;
  decision 3's ~76-minute red window on `develop` is accepted rather
  than adding a pending-cases escape hatch to
  `test_create_cases_match_baselines`; decision 2's four commands
  (`info`, `check`, `map`, `measure`) stand. Gate 4 was already
  settled before the run -- the phase 9 convert defect is filed as
  #879. On the push question Michael chose "push testdata, then open
  the pull request", so this session has that permission for this
  phase and does not ask again at the end.
* **Before 10a.** The testdata clone was 30 commits behind
  `origin/main` (the fuzz automation moves it daily) and was
  fast-forwarded to `88bf8566e1`. All five phase 2 fixtures are
  materialised, not LFS pointers.
* **10a.** Implemented as planned. `--case` reaches all eight
  case-driven commands rather than only `create`, because the agent
  found every such command's config carries exactly one key ending in
  `_cases` and keyed the filter on that; the management session
  confirmed the one-key property holds for all thirteen commands and
  that the five commands with no case list are exactly the five
  `--image` applies to, less `measure`. One judgement call kept:
  `measure` has both axes, so a lone `--case` would have rewritten
  every source image's baseline as a side effect; a single filter now
  selects the sub-mode it names. Kept because that is what step 10d
  needs, and because the alternative is the silent over-generation
  this step exists to prevent.
* **10a verification (management session).** All four refusal paths
  exit 1 with the tree unchanged: unknown image id, unknown case name,
  `--image` on `create`, `--case` on `info`. `python3 -m py_compile`
  clean; `make -n baselines-create baselines-map` resolves; the three
  over-length lines in the changed files all pre-date the step
  (`git blame`). `instar-testdata` has no pre-commit config, no CI and
  no test suite, so these are the repository's only available checks.
* **Scope added: step 10g (Michael Still, 2026-11-07).** 10c's
  validator found a live latent defect in `detect-profiles.py`'s
  preserve-hand-maintained path: `create-info-json` profile filenames
  are bucket-qualified and its raw ids are not, so all 46 ids mismatch
  and all 7360 profile files are treated as hand authored and restored
  over every fresh copy. The management session confirmed the set
  arithmetic independently (46 of 46 profile ids absent from the raw
  id set). It is the same mismatched-granularity bug as the first of
  the two historical failures, reintroduced by the fix for the second.
  Nothing is wrong today only because the ten new cases had no prior
  profile file and the three re-measured twins were restored
  unchanged. Offered as fix-now, file-it, or fix-in-a-follow-up;
  Michael chose to fix it in this phase, because the validator this
  phase builds is the safety net both earlier instances lacked.
* **Scope added: step 10h (Michael Still, 2026-11-07).** Step 10g's
  agent found, and the management session reproduced, a second defect
  of the same family: `preserved_baselines` is keyed by old profile
  directory name, so a moved boundary drops the hand-authored goldens
  (measured: `qemu-img-human`'s luks files 28 to 24 when one version's
  raw baselines go away and `profile-6-0-0` becomes `profile-6-0-1`),
  and the new validator still exits 0 because it prints the
  hand-maintained count without asserting it. Offered as
  harden-the-validator-only, harden-and-fix-the-keying, or file-both;
  Michael chose to do both. 10g's agent judged the keying to need a
  design choice and so left it; the choice taken here is to key by
  version, because versions are stable identifiers and profile names
  are derived from them.
* **Correction to step 10e, made at source.** The brief asked for a
  before/after skip count in three modules. Only two of them skip:
  `test_map` and `test_measure` raise `skipTest` per scenario
  (`tests/test_map.py:330`, `tests/test_measure.py:733`), but
  `test_info_safe` builds a scenario only where the baseline file
  exists (`tests/test_info_safe.py:80`), so the five fixtures produce
  no test at all today and the measurable delta there is a scenario
  count, not a skip count. Phase 2's plan calls all three "skips".
* **Sequencing departure.** The plan notes 10c only reads
  `expected-outputs/`, which left it looking parallelisable with 10b.
  It is not: 10c adds a Makefile target, may refactor
  `detect-profiles.py`, commits to the same worktree, and its
  deliberate-corruption proof writes into `expected-outputs/`. All
  four testdata steps therefore ran in sequence.
* **Correction to step 10b, made at source.** Three of
  the six distinct case names 10b adds already exist for another
  target: `16M-fixed` (vhd), `1M-prealloc-falloc` and
  `1M-prealloc-full` (qcow2). `--case` filters by name across targets,
  by design, so 10b's run also re-measures those three twins at all 80
  versions. The plan's original wording -- "confirm `git diff --stat`
  touched only the ten new cases' files" -- was therefore false as
  written. 10b now proves the twins' `stdout` and `stderr` are
  byte-identical and restores them, so the commit holds only the new
  cases.
* **10b.** As planned. Ten cases, 4882 files. The management session
  re-verified the substance rather than the plumbing: the `16M-fixed`
  baseline is byte-identical to an independent `qemu-img create` run,
  and that run's file length is 25,165,824 -- the committed fixture's
  length exactly. The preallocation no-op phase 3 landed against one
  host qemu now holds byte-for-byte on all 80 builds: 320 vhd and vhdx
  `1M-default` versus `1M-prealloc-*` pairs compared, 0 differ. Two
  judgement calls kept: the agent reverted timestamp-only churn
  `detect-profiles.py` wrote into other output types, and restored the
  three re-measured twins after proving their `stdout`/`stderr`
  unchanged, keeping 240 timestamp-only files out of the diff.
* **10c.** As planned, plus the finding that became 10g. The validator
  imports `detect-profiles.py`'s naming rules rather than restating
  them, which needed one behaviour-neutral extract
  (`profile_filename()`); the management session confirmed the extract
  is faithful (`f.parent.parent.name` resolves to the same bucket the
  old `bucket_dir.parent.name` did) and reproduced the clean run
  (163,932 files compared, 0 problems), the corruption proof and the
  deletion proof. Five output types report "not a profiled output
  type"; they have no `profiles/` directory at all, so that is accurate
  and not a gap.
* **10d.** As planned, and the headline result is stronger than the
  step was asked to prove: not merely the version grouping but the
  version-to-profile *names* are identical for all 12 output types to
  what they were before the fixtures landed, checked against git. So
  `MATRIX` needed no change. `map-human` exits 1 for all five fixtures
  at every version ("File contains external, encrypted or compressed
  clusters"); that is qemu's behaviour for VHDX in human map mode --
  the two pre-existing VHDX images already do it and a live run
  reproduces it while `map --output=json` succeeds -- so the baseline
  records it and the human scenarios skip on it. `qemu-img check` does
  **not** refuse the 17M fixtures at any version, which open issue #797
  had left as a possibility.
* **10e.** Two rounds. The first reported three divergences as new
  instar defects; all three had instar and qemu transposed, and the
  management session measured each before accepting any of it. In the
  measured direction they are the VHDX source-scanner divergence
  already registered for `qemu-vhdx`, whose existing wording predicts
  exactly what was measured: with `block_state_zero=on` every payload
  BAT entry is a zero state, so instar counts none and qemu counts all
  (instar `required` 327,680 against qemu's 17,104,896 on a 16 MiB
  fixed image), and instar reports the extent as zero and not data
  where qemu reports data. The cause of the inversion is worth knowing
  and is now documented: `test_map` and `test_measure` pass instar's
  output as `assertEqual`'s first argument, so testtools labels it
  "reference". Taken at face value the phase would have filed two bugs
  against correct behaviour and left the real divergences untabled.
  Second round tabled them in the measured direction, plus the vhd
  preallocation cases under the pre-existing CHS-rounding divergence,
  and one genuine new defect as a known check failure. One change
  beyond the brief kept: `tests/helpers/info_json.py` mirrors 10a's
  sibling rule, without which the monolithicFlat baseline compares a
  placeholder against a host path; committed separately, with tests.
* **10e: a real defect found, not fixed here.** `instar check` refuses
  a monolithicFlat VMDK that instar itself created ("This image format
  (vmdk) does not support checks"), while checking every other vmdk
  subformat and while `qemu-img check` passes the same file. Confirmed
  by the management session on both an instar-written and a
  qemu-written flat image. Phase 3 added flat create without a check
  path. Recorded in the master plan's Future work; `gh issue create`
  was denied in this session, so it has no issue number.
* **10f.** As planned. The documentation example commands were not run
  by the agent, so the management session ran both: each exits 0 and
  touches only timestamp metadata. `AGENTS.md` gained nothing, on the
  grounds that it says nothing about baselines today and the procedure
  belongs in `docs/testing.md`.
* **10g.** As planned, and verified from both sides on scratch copies:
  with the pre-fix script a perturbed raw baseline never reaches the
  profile and the validator fails; with the fix it propagates and the
  validator is clean. The agent found a second defect of the same
  family and reported it rather than extending its own fix, which is
  what let the scope question reach Michael; that became 10h.
* **10h.** As planned. The boundary-move experiment reproduced from
  both sides (28 goldens kept where the old code left 24), and the
  restored content is the correct per-version variant -- the renamed
  `profile-6-0-1` received the early-version golden, not the 8.0.0 one
  -- which is the substantive gain of keying by version. The
  hand-maintained invariant was measured rather than assumed: all
  seven profiles of both affected types hold all four golden files, and
  the "applies only from version N onward" worry cannot arise, because
  an id with a generated source for even one version is never
  classified hand-authored. The strong invariant was encoded, per
  filename rather than per id.
* **A management-session error, corrected.** The step 10h plan commit
  was made with `git add -A` while step 10e was working in the same
  worktree, so it swallowed 10e's ten-line mirror hunk. The commit was
  unpushed and on top, so it was split: the plan commit holds only the
  plan file and the mirror change went back to the working tree for
  10e's own commit. Staging in that worktree was by explicit path
  thereafter.
* **Definition of done, one criterion corrected at source.** The
  `--case 16M-fixed` file-count criterion was wrong as written: it
  named only the vhdx bucket and expected three files. `--case`
  filters by name across targets and vhd has a case of that name too,
  and only the two `meta.json` files change because the re-measured
  outputs are byte-identical. Corrected above; the intent -- that the
  filter is surgical -- holds.
* **The instar-testdata revision this branch requires.** The six
  testdata commits landed directly on `main` as
  `88bf8566e1..f73d43670a`. Two of them are what these tests assert
  against: `a78c06a2eb` adds the ten create cases' baselines, which
  `test_create_cases_match_baselines` demands in both directions, and
  `049305c62d` adds the five fixtures' info, check, map and measure
  baselines. `tools/ci/prepare-testdata.sh` clones testdata's default
  branch shallow and unpinned, so a run resolves whatever `main` holds
  at the time; there is no revision to pin here, which makes the
  ordering load-bearing in both directions. Reverting the testdata
  commits therefore requires reverting this branch's `CREATE_CASES`
  hunk in the same change, or `develop` goes red on
  "CREATE_CASES entries with no baseline".
* **A review round, 2026-11-07.** The automated reviewer raised 1
  fix, 4 document, 7 consider. Two of its items found false claims
  this phase's own fixtures created: `docs/measure.md` and
  `docs/quirks.md` both said a fixed VHDX source is the case where
  instar and qemu-img agree, which `block_state_zero=on` makes untrue,
  and the CHANGELOG claimed the new baselines are compared against
  every version's profile where a run compares exactly one. A third
  found a real over-match in the new sibling-placeholder rule. The
  review also reported the monolithicFlat `check` gap as untracked; it
  is tracked, as #822, filed by phase 3 -- so no issue needed filing
  and the earlier report of a denied `gh issue create` was about a
  defect already on record.
* **A pre-existing breach noted, not touched.** Landed code and docs
  cite `PLAN-*.md` files in 95 places across 34 files outside
  `docs/plans/`, against the repository's own rule: `docs/quirks.md`
  (25), `src/crates` (11) and `scripts/differential-fuzz.py` (11) lead.
  This phase added none and cleaned up none.

## Back brief

Before executing any step, back brief the operator on this understanding
of the plan. The gates that need an answer:

1. **Decision 1, the generator filters** -- the one most likely to be
   argued, because it is new tooling in a phase the master plan scoped as
   "only tests". Confirm adding `--image` and `--case`, or say to
   mass-regenerate and absorb issue #845's fallout into this phase.
2. **Decision 3, the accepted red window on `develop`.** Confirm the
   testdata-push-then-instar-PR ordering and the ~76-minute window, or
   ask for the `PENDING_BASELINE_CASES` escape hatch instead.
3. **Decision 2, four commands for the fixture baselines.** Confirm
   `info`, `check`, `map`, `measure`, or name others.
4. **Nothing outstanding on the phase 9 convert bug.** It is filed as
   #879 and recorded in the master plan's Future work. It is not this
   phase's to fix; confirm it stays out of scope.

## Out of scope

* **Generating baselines for the ~86 safe manifest images that have
  none.** `check-human` baselines 38 of 124 safe images; `qemu-img-json`
  baselines 71. That is issue #845's territory and a phase of its own:
  it un-skips tests across the whole suite, each of which may have a real
  failure behind it, and that review has nothing to do with fixed VHDX.
  Decision 1's filters are what keep this phase out of it. Recorded in
  the master plan's Future work.
* **The `create-info-json` 80-profile redundancy.** Caused by qemu-img's
  random VMDK `cid`; harmless because every consumer strips `cid`.
  Normalising it (or excluding the field from the profile hash) would
  change 80 profiles' contents for a disk-space win. Recorded in Future
  work.
* **The phase 9 `convert -O vhdx --subformat fixed` partial-last-block
  bug (#879).** Recorded in the master plan's Future work by phase 9;
  it needs a fix of its own. This phase does not add a create or
  convert case at that geometry, because the case would fail for a known
  reason.
* **Fuzzing (phase 11) and the cross-cutting documentation pages (phase
  12).** 10f touches only `docs/testing.md`, `CHANGELOG.md` and, if a
  convention changed, one line of `AGENTS.md`.
* **`detect-profiles.py`'s stale module docstring**, which documents only
  the flat `raw/<version>/` layout and not the bucketed one. 10c may fix
  it in passing if it refactors that file; it is not a required outcome.
