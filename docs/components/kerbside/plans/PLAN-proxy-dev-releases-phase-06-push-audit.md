# Proxy dev releases phase 6: push audit

Master plan: `PLAN-proxy-dev-releases.md`. Phases 1 to 5 built the
rolling dev-release pipeline: a path-filtered publish workflow, a
committed dev-inclusive version floor, a proto-hash contract
handshake, the operator documentation for all three, and a weekly
storage monitor bounding the release set. This phase runs
`PUSH-AUDIT.md` over what those phases did *together*, which is the
only view in which their interactions are visible.

Planning effort: medium. The audit's content is prescribed by
`PUSH-AUDIT.md`; the judgment this phase needed was in scoping the
range, and in the three tooling defects the survey turned up.

## Scope

In scope:

* The full `PUSH-AUDIT.md` run — wave 1 mechanical, wave 1 style
  judgment, wave 2 mechanical, and judgment agents 2a, 2b, 2c and
  2d — over the accumulated diff of phases 1 to 3, 4a and 5.
* The three audit-tooling corrections the survey forced, without
  which the run is vacuous or falsely red (decisions 1, 3 and 4).
* Triage of every finding, and its fix or its written declination
  in the master plan.
* The master plan corrections this phase's survey forced (already
  applied in this planning commit — see "What the survey found").

Out of scope:

* Phase 4b (withdrawn 2026-08-18) and phase 4c (the Gerrit
  recheck, outstanding and operator-driven). Neither ships a diff
  in this repository, so neither is auditable here.
* Making the Rust gates a permanent part of `tools/audit/wave1.sh`
  (decision 3), and propagating the range-override fix to
  `shakenfist/development`'s copies of these scripts. Both are
  recorded as future work on the master plan.
* Any behaviour change to the dev-release pipeline itself. If the
  audit finds a bug, this phase fixes it; it does not extend the
  feature.

## What the survey found

Verified 2026-08-29 against `develop` at `fe4bebe`.

**The master plan's phase 6 sketch is accurate on every claim it
makes, and incomplete on one.** Confirmed: the two phase merges are
`14b54f3` (PR #314) and `2e1fd43` (PR #328) and both are ancestors
of `develop`; the derived path set is exactly 40 files; the diff
over that range and path set is 40 files, 4,040 insertions, 94
deletions — the sketch said "40 files and roughly 4,000 added
lines"; `PUSH-AUDIT.md` says `git diff develop...HEAD` in exactly
five judgment briefs, at lines 96, 181, 236, 274 and 381; and
`tools/audit/wave1.sh:37` does hard-code `DIFF_BASE=develop`.

**The incompleteness: `tools/audit/wave2-mechanical.sh:18` carries
the same hard-coded `DIFF_BASE=develop`, and the sketch does not
mention it.** All eight of that script's reports are built from
`git diff "$DIFF_BASE"...HEAD` — TODO/FIXME, new `# noqa`, test
counts, docs touched, broad `except`, new asserts, new
dependencies, new alembic revisions. Run unmodified on a phase 6
branch it prints "(none)" eight times and exits 0, which reads as a
clean bill of health and is in fact an empty diff. `PUSH-AUDIT.md`
line 66 has the same omission: its own accumulated-range warning
names only `wave1.sh:37`. Corrected at source in the master plan's
phase 6 sketch as part of this planning commit; the `PUSH-AUDIT.md`
correction is step 6a.

**`PUSH-AUDIT.md` already anticipates the accumulated-range case.**
Lines 74-80 — added by `10ea413`, the review round on PR #366, four
days after the sketch was written — tell the reader that a branch
whose work has already merged gets "an empty diff and a vacuous
pass", and name a master plan's push-audit phase as the case. So
this is documented behaviour rather than a trap, and the sketch's
"the executor's first correction" framing still holds.

**Nothing mechanically checks the Rust half of the diff.** Both
audit scripts are Python-only: `wave1.sh` runs `tox -eflake8` and
`tox -epy3`, and every style grep in both scripts is filtered to
`*.py`. The range touches `rust/kerbside-proxy/build.rs` (+22) and
`rust/kerbside-proxy/src/main.rs` (+31) — the contract-hash embed
and the `--contract-hash` flag from phase 3, which is the change in
this plan most able to break a running deployment. `rust.yml` is
advisory and path-filtered in CI, so it is not a backstop either.
The Rust tree has a `Makefile` with `lint` and `test` targets that
build in Docker; step 6b runs them.

**Wave 1 will fail with exit 3 on a false positive, and the survey
knows exactly where.** Two `print()` calls are added in the range,
both in `tools/check-pypi-storage.py` — `print(message,
file=sys.stderr)` and `print(report)`. Neither
`kerbside/proxy_supervisor.py` nor `kerbside/rpc/contract.py` adds
one. No file in the repository carries the `audit-allow-print`
marker that `wave1.sh:70-81` looks for, so the check is fatal. It
should not be: that script is a stdout-reporting CLI whose output
`pypi-storage-check.yml` captures and feeds to
`file-pypi-storage-issue.sh`. Decision 4 settles it before the run
rather than during it.

**The other mechanical checks are pre-run and clean.** Over the
range and path set: no bare `except:` added, no added Python line
over 120 characters. `tox` and `docker` are both present on this
host.

**42% of the audited insertions are planning records.** Of the
4,040 insertions, 1,699 across 7 files are `docs/plans/` — the five
phase plans, the master plan and `index.md`. The remaining 33 files
carry 2,341 insertions. Decision 2 settles how the judgment agents
treat them.

## Decisions

**1. The scripts take the range from the environment; nobody
hand-edits `DIFF_BASE`.** `tools/audit/wave1.sh` and
`tools/audit/wave2-mechanical.sh` gain `AUDIT_RANGE` (default
`develop...HEAD`, preserving today's behaviour exactly) and
`AUDIT_PATHS` (default empty). A new `tools/audit/plan-range.sh`
takes the plan's merge commits and prints both, deriving the path
set rather than transcribing it.

The master plan's own suggestion — "edit `DIFF_BASE` locally
without committing it" — is the option rejected here. It cannot be
reviewed, it cannot be reproduced by whoever reads the audit later,
it is one `git add -A` away from being committed by accident, and
most importantly it cannot express the path scoping at all: a bare
`DIFF_BASE=14b54f3^1` still diffs to `HEAD` and still sweeps in
every unrelated merge that landed between the two phase PRs, which
is the specific thing the path set exists to prevent.

**Correction, 2026-08-29, found by step 6a and verified in the
management session: the call-site form this decision originally
specified — `git diff $AUDIT_RANGE -- $AUDIT_PATHS '*.py'` — does not
do what the paragraph above claims.** Git *unions* positive
pathspecs rather than intersecting them, so that command means
"anything in `AUDIT_PATHS`, OR any `*.py` anywhere in the range",
which re-admits exactly the unrelated merges the path set exists to
exclude. Measured on the real range: the intersecting form yields 7
Python files, the union form 46 paths of which 13 are Python.
Exclusion pathspecs (`:!...`) are unaffected — those genuinely
subtract.

(An earlier revision of this paragraph recorded the union figure
as 39, which does not reproduce; corrected 2026-08-29 after the
phase 6 PR review re-measured it. The intersect/union distinction
the decision rests on is unchanged — only the magnitude was
wrong.)

The scripts therefore intersect in two stages instead, via a pair of
helpers each script defines once:

```
audit_paths_for <filename-ere> [exclusion pathspecs...]
audit_diff_for  <filename-ere> [exclusion pathspecs...]
```

`audit_paths_for` runs `git diff --name-only $AUDIT_RANGE --
$AUDIT_PATHS <exclusions>` and filters the resulting list with
`grep -E <filename-ere>`; `audit_diff_for` re-diffs that literal
file list, and produces nothing when the list is empty (a bare
`git diff RANGE --` with no paths would otherwise mean
"everything"). With `AUDIT_PATHS` unset both degrade to today's
behaviour, because an empty positive pathspec list means no
restriction.

This is why step 6a carries a gate. The mechanism was wrong in the
first draft of this plan and the sub-agent caught it by measuring
rather than by trusting the brief; the same measurement is now a
done-criterion.

This is the decision a reviewer is most likely to argue with, on
the grounds that a phase whose job is to *run* an audit should not
be *changing* the audit tooling, and that the changed tooling then
ships in a PR that no audit has looked at. The counter is that the
alternative is a run whose scoping is a claim in a plan file rather
than a command anyone can re-execute, that the edit is small and
additive with the existing behaviour as the default, and that every
future push-audit phase in this repository needs it. The recursion
is real and is recorded under risks.

**2. The judgment agents read the code, not the planning records.**
Steps 6c through 6g pass `':!docs/plans/'` on top of the path set,
so 2a/2b/2d see the 33 non-plan files and 2,341 insertions.
`docs/plans/` content is an append-only historical record: findings
against it would be findings against what was true in August, and
2c's README/AGENTS/ARCHITECTURE discipline blocks do not apply to
it. The one exception is 2c, which additionally gets the master
plan and the five phase plans with a narrow question: does any
factual claim they make about the shipped pipeline disagree with
the code as it stands today? That is the stale-documentation
finding that would otherwise be missed, and it is the only reason
to read them.

**3. Rust gates run in this phase's wave 1, but `wave1.sh` does not
grow them.** Step 6b runs `make -C rust/kerbside-proxy lint test`
alongside `tox -eflake8` and `tox -epy3`. Adding them permanently
to `wave1.sh` would need a sixth exit code, and that script's exit
codes are documented in a table in `PUSH-AUDIT.md` which is itself
derived from `shakenfist/ryll`'s copy — a fleet-wide change that
should be made once, upstream, not forked here. Recorded as future
work.

**4. `tools/check-pypi-storage.py` gets the `audit-allow-print`
marker, in step 6a, before the audit runs.** Deciding a known false
positive up front is cheaper and more honest than discovering it as
a red wave 1 and arguing it away mid-run. The marker's semantics
are coarse — `wave1.sh:74-81` suppresses the check for the *whole*
diff if *any* changed file carries it — so the comment must name
the file's reason, and the survey has already established that
these two calls are the only added prints in the range, so nothing
else is being hidden. Making the marker per-file rather than
per-diff is a `wave1.sh` semantics change, and belongs upstream
with decision 3.

**5. Phase 6 lands as one pull request.** The master plan says
findings "land as their own pull request", which distinguishes them
from the phases 1-5 PRs rather than requiring a second PR for the
fixes. The phase branch carries this plan, the step 6a tooling, and
every fix the audit produces, and lands once — consistent with the
operator's CI-cost policy.

## Step plan

Steps 6c through 6g are independent and run in parallel once 6b
passes. Every brief that names a diff assumes `6a` has landed and
that the agent begins by running:

```
eval "$(tools/audit/plan-range.sh 14b54f3 2e1fd43)"
```

which exports `AUDIT_RANGE` and `AUDIT_PATHS`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | Make the audit scripts able to audit an accumulated range. (1) `tools/audit/plan-range.sh`: new script, `#!/bin/bash`, `set -e`, usage header comment in the style of `tools/file-pypi-storage-issue.sh`. Takes one or more merge SHAs as positional args. For each, `git diff --name-only "$sha^1..$sha"`; union them with `sort -u`; print exactly two lines to stdout, `AUDIT_RANGE='<first-sha>^1..<last-sha>'` and `AUDIT_PATHS='<space-separated paths>'`, both `export`-prefixed so `eval "$(...)"` works. Fail loudly (exit 1, message to stderr) if any SHA is not a commit, if any SHA is not an ancestor of `develop` (`git merge-base --is-ancestor`), or if fewer than one arg is given. (2) `tools/audit/wave1.sh`: replace `DIFF_BASE=develop` at line 37 with `AUDIT_RANGE="${AUDIT_RANGE:-develop...HEAD}"` and `AUDIT_PATHS="${AUDIT_PATHS:-}"`; the `git rev-parse --verify` guard at line 58 becomes a `git rev-parse --verify "${AUDIT_RANGE%%.*}"` check, or simply `git diff --quiet "$AUDIT_RANGE" >/dev/null 2>&1 || true` — whichever keeps the existing "cannot find, skip and advise" behaviour when the range is bogus. Then rewrite the three `git diff "$DIFF_BASE"...HEAD -- <pathspecs>` call sites (lines 66, 74, 102) as `git diff $AUDIT_RANGE -- $AUDIT_PATHS <pathspecs>` — unquoted on both vars, deliberately, so word splitting builds the argument list; add a `# shellcheck disable=SC2086` with a one-line reason above each, since `pre-commit` runs shellcheck. (3) Same treatment for `tools/audit/wave2-mechanical.sh`: `DIFF_BASE=develop` at line 18 and every `git diff "$DIFF_BASE"...HEAD` in the file. (4) In `PUSH-AUDIT.md`, the paragraph at lines 74-80 (NOT inside a shared block — check the `<!-- shared-block:` markers before editing, and do not touch any text that is) currently names only `tools/audit/wave1.sh:37`; rewrite it to say that both scripts hard-code the base, that both now read `AUDIT_RANGE` and `AUDIT_PATHS` from the environment, and that `tools/audit/plan-range.sh` derives them from a plan's merge commits. Keep it to one short paragraph plus the `eval` line; this file is a fleet template and prose growth in it is costly. (5) Add the marker for decision 4: in `tools/check-pypi-storage.py`, a comment near the top reading `# audit-allow-print: this is a reporting CLI -- pypi-storage-check.yml captures its stdout and feeds it to file-pypi-storage-issue.sh.` Verification to run and record: `AUDIT_RANGE`/`AUDIT_PATHS` unset, `tools/audit/wave1.sh` behaves exactly as before on a branch with a real diff; with the phase range exported, both scripts report against 40 files rather than nothing; `tools/audit/plan-range.sh 14b54f3 2e1fd43` prints a path set of exactly 40 entries; `pre-commit run --all-files` passes. Commit subject: "Let the audit scripts audit a merged range." |
| 6b | low | sonnet | none | Wave 1 gates. Run, in order, and report each verbatim: `tools/audit/wave1.sh` with the phase range exported; `make -C rust/kerbside-proxy lint`; `make -C rust/kerbside-proxy test`. The Rust targets build in Docker and are slow on a cold cache — allow for that rather than assuming a hang. Do not fix anything you find; report exit codes and output. If `wave1.sh` exits non-zero, say which check and quote its output, and stop — wave 2 is not worth spending on until wave 1 is green. Expected, from the survey: the `print()` check passes because 6a added the marker, no bare `except:`, no long lines, and both `tox` gates depend on the tree rather than the range so they should be as green as `develop` is. Commit subject: none, this step produces a report, not a diff. |
| 6c | low | sonnet | none | Wave 1 style-conformance judgment. Execute the brief under "Style conformance — judgment portion" in `PUSH-AUDIT.md` (lines 84-131) verbatim, with one substitution: wherever it says `git diff develop...HEAD`, read `git diff $AUDIT_RANGE -- $AUDIT_PATHS ':!docs/plans/'`. Report a short list of violations, or "Style checks passed." |
| 6d | medium | sonnet | none | Wave 2 mechanical plus 2a code quality. First run `tools/audit/wave2-mechanical.sh` with the phase range exported and keep its report. Then execute the 2a brief in `PUSH-AUDIT.md` (lines 166-226) with the same diff substitution as 6c. Note for the duplicated-code question: the range contains four shell scripts that all stamp or verify a version — `tools/stamp-proxy-version.sh`, `tools/stamp-dev-proxy-version.sh`, `tools/verify-wheel-stamping.sh` and `tools/build-proxy-wheel.sh` — and they are the most likely place for copy-paste to have accumulated across phases 1, 2 and 5. Look there first. |
| 6e | medium | sonnet | none | 2b test review. Execute the 2b brief in `PUSH-AUDIT.md` (lines 227-263) with the same diff substitution as 6c. The range adds four test modules — `test_check_pypi_storage.py`, `test_contract.py`, `test_proxy_floor.py`, `test_proxy_supervisor.py`. The specific question worth more than the generic ones: phase 3's contract handshake refuses to launch on a hash mismatch and has an escape hatch, `KERBSIDE_SKIP_CONTRACT_CHECK`. Is the refusal path tested, is the escape hatch tested, and is there a test that would fail if the committed hash in `kerbside/rpc/contract.py` went stale against `kerbside/rpc/kerbside.proto`? |
| 6f | medium | sonnet | none | 2c documentation review. Execute the 2c brief in `PUSH-AUDIT.md` (lines 264-371) with the same diff substitution as 6c — the README, AGENTS.md and ARCHITECTURE.md discipline shared blocks in that brief apply to the 33 non-plan files. Then, separately and additionally (decision 2), read `docs/plans/PLAN-proxy-dev-releases.md` and its five phase plans and answer one narrow question: does any factual claim they make about the shipped pipeline disagree with the code as it stands on `develop` today? Check specifically the publish path filter in `.github/workflows/dev-proxy-wheel.yml` against every prose copy of it (`grep -rn 'stamp-dev-proxy-version.sh' --include='*.md' .`), the version floor in `pyproject.toml` against what the plans say it is, and the monitor's thresholds in `tools/check-pypi-storage.py` against `RELEASE-SETUP.md`. Do not report drift *within* the planning record itself — a phase plan describing what was true when it was written is not a defect. |
| 6g | high | opus | none | 2d security review. Execute the 2d brief in `PUSH-AUDIT.md` (lines 372-436) with the same diff substitution as 6c. The generic SPICE input-validation classes in that brief mostly do not apply — this range touches no packet parsing. What does apply, and should get the attention: the supply-chain surface. A committed `kerbside-proxy>=0.4.0.dev0` floor in `pyproject.toml` means a plain `pip install` of a checkout now resolves a pre-release wheel from PyPI, which is a new trust edge; assess what an attacker who could publish to that project name would gain and what stops them (trusted publishing, the `dev-release` GitHub environment, build provenance attestations, the phase 3 contract hash). Assess the workflow permissions in `dev-proxy-wheel.yml` and `pypi-storage-check.yml` — particularly that `issues: write` is scoped to one job and that no PyPI credential exists in the repository. Assess whether the contract-hash check is a security control or only a compatibility check, and say which, because phases 3 and 4 describe it both ways. **Correction, 2026-08-29: that last clause was overstated and the review refuted it.** 2d checked every description of the check in both phase plans, the master plan and the shipped documentation and found them consistently about skew; the only security-flavoured word anywhere was a single use of "verifies" in `docs/development.md`, which the audit's documentation fix removes. The question was still worth asking and the answer is recorded (a compatibility check, and it cannot be anything else: the check executes the untrusted binary in order to decide whether to trust it), but the inconsistency the brief told the reviewer to expect did not exist. Finally, `tools/file-pypi-storage-issue.sh` interpolates a report file into `gh issue create` — check the quoting on any path where content could reach a shell. |
| 6h | high | opus | none | Triage and close out. Take the reports from 6b through 6g. For each finding: classify blocking or advisory, and fix the blocking ones in this worktree, in their own commits with their own subjects. Then write the result into `docs/plans/PLAN-proxy-dev-releases.md` — every finding fixed or declined **in writing, with the reason**, in the phase 6 row and in a short subsection under the phase 6 sketch. If the audit found nothing, say so in a sentence; that is a result. Set the phase 6 status to `Complete` in both the master plan's Execution table and the `docs/plans/index.md` row, and set the master plan's own status to `Complete` if and only if nothing else in it is outstanding — note that phase 4c, the Gerrit recheck, is operator-driven and may still be open, in which case say so rather than closing the plan. `pre-commit run --all-files` passes. Commit subject for the write-up: "Record the phase 6 push audit findings." |

## Risks and mitigations

* **The tooling this phase changes is not itself audited.** Step 6a
  edits both audit scripts and `PUSH-AUDIT.md`, and those edits
  ship in the same PR as the audit that ran using them — a
  measuring instrument recalibrated by the measurement. Mitigated
  by keeping 6a additive with today's behaviour as the default, by
  the falsifiable before/after check in its brief (unset
  environment must reproduce the old behaviour exactly), and by
  the fact that the range and path set it derives are checked
  independently in this plan against the numbers the survey
  recorded. The management session verifies both, not the
  sub-agent's summary.
* **`AUDIT_PATHS` word-splitting breaks on a path with a space.**
  The design deliberately relies on word splitting to build the
  git argument list. None of the 40 paths contains a space and
  none plausibly will, but a future plan's path set might.
  Mitigated by `plan-range.sh` failing loudly if any derived path
  contains whitespace — add that check; a wrong-but-quiet path set
  is the failure mode that matters here, since it silently narrows
  an audit.
* **The `audit-allow-print` marker suppresses the print check for
  the entire diff, not just the file carrying it.** Decision 4
  accepts this. Mitigated by the survey having already enumerated
  every added `print()` in the range — there are exactly two, both
  in the marked file — so the suppression hides nothing today.
  Checked again by 6b, which quotes the check's output.
* **Wave 2's judgment agents produce plausible-sounding findings
  against code they have not fully understood**, and the
  management session spends the phase arguing with four agents.
  Mitigated by each brief naming the specific question worth
  asking (6d: the four stamping scripts; 6e: the contract-hash
  staleness test; 6g: the supply-chain edge) rather than only
  inheriting the generic template brief, and by 6h being an opus
  step whose job is explicitly triage, not acceptance.
* **The Rust gates fail for reasons unrelated to this range** —
  a toolchain drift in the Docker image, or a clippy lint that
  landed since. Mitigated by 6b reporting rather than fixing, and
  by 6h being allowed to classify such a failure as out of scope
  and file it rather than fix it. This phase audits a range; it
  does not adopt every unrelated red it finds.

## Definition of done

* `tools/audit/plan-range.sh 14b54f3 2e1fd43` prints an
  `AUDIT_PATHS` of exactly 40 paths and an `AUDIT_RANGE` of
  `14b54f3^1..2e1fd43`, and exits non-zero if given a SHA that is
  not an ancestor of `develop` or a path set containing
  whitespace.
* With `AUDIT_RANGE` and `AUDIT_PATHS` unset, `tools/audit/wave1.sh`
  and `tools/audit/wave2-mechanical.sh` produce byte-identical
  output to their pre-6a versions on the same branch. Demonstrated,
  not asserted: run each against `git stash`-ed and restored
  copies, or against the pre-change scripts checked out to a
  temporary path, and diff the output.
* With the phase range exported, `wave2-mechanical.sh` reports
  against 40 files rather than printing "(none)" eight times.
* The scoping intersects rather than unions. Falsifiable: with the
  phase range exported, every file either script reports on is a
  member of `AUDIT_PATHS`. Check it directly —

  ```
  eval "$(tools/audit/plan-range.sh 14b54f3 2e1fd43)"
  comm -13 <(printf '%s\n' $AUDIT_PATHS | sort) \
           <(tools/audit/wave2-mechanical.sh | grep -oE '[a-zA-Z0-9_./-]+\.(py|md|sh|toml|yml|rs|lock)' | sort -u)
  ```

  must print nothing — every path either script reports is a
  member of `AUDIT_PATHS`. That property, not a particular count,
  is what distinguishes intersecting from unioning: a script that
  unions reports paths absent from `AUDIT_PATHS`, and the
  `comm -13` above prints exactly those.
* `tools/audit/wave1.sh` exits 0 over the phase range, and its
  output shows "PASS (suppressed): print() added, exempted by the
  marker in: tools/check-pypi-storage.py" — that is, the marker path
  rather than an empty diff. (Originally written against the message
  "PASS: no raw print() added"; PR review round 2 split the suppressed
  case out into its own message, so that string now means the opposite
  of what this criterion needs.)
* `make -C rust/kerbside-proxy lint` and `make -C rust/kerbside-proxy
  test` both exit 0, or their failures are recorded in the master
  plan as out of scope with the reason.
* All five judgment agents (6c, 6d, 6e, 6f, 6g) have reported, and
  `PUSH-AUDIT.md`'s management session checklist is worked through
  in the master plan write-up.
* Every finding appears in `docs/plans/PLAN-proxy-dev-releases.md`
  with a disposition — fixed (naming the commit) or declined
  (naming the reason). No finding is recorded without one. If there
  were none, one sentence says so.
* No sentence in `PUSH-AUDIT.md` still claims that only
  `tools/audit/wave1.sh` hard-codes the diff base, and no text
  inside a `<!-- shared-block: -->` region in that file is
  modified.
* Phase 6 reads `Complete` in both the master plan's Execution
  table and the `docs/plans/index.md` row, and the master plan's
  own status is either `Complete` or states what remains.
* `pre-commit run --all-files` passes.

## PR review round 1

The automated reviewer raised eleven items against the phase 6 PR
(kerbside#375): three `fix`, five `consider`, three `info`. Every
claim was reproduced before being acted on; the dispositions are
below so a later reader does not have to re-derive them.

Notably, all three `fix` items are in `plan-range.sh` — the tooling
this phase added so that the audit could run at all, and which the
plan records as not having been audited itself. That is the expected
place for them to be, and it is why the round is worth recording
rather than just landing.

Fixed:

* **The base ref was the literal `develop`.** A CI-style checkout has
  only `origin/develop`, so the ancestry guard failed with git's own
  "Not a valid object name" followed by this script blaming ancestry
  — the wrong problem entirely. It now resolves `develop` then
  `origin/develop` and distinguishes "cannot resolve the base branch"
  from "this SHA is not on it".
* **`${AUDIT_RANGE%%.*}` truncated at the first dot.** A dotted ref
  (`v0.5.0..HEAD`) became `v0`, which does not resolve, so both wave
  scripts reported nothing to diff and exited 0 having checked
  nothing. That is the same vacuous-green class as the BRE bug this
  phase exists to have found. Both now strip at `..`.
* **Reversed merge SHAs were accepted silently.** The derived range
  then diffs backwards, so the `^\+` style checks inspect reverted
  content and pass on work they never saw, while the path set still
  looks entirely correct. An ancestry check between the first and
  last SHA now rejects it.
* **An empty derived path set silently widened the audit** (raised as
  `consider`, taken because it is a two-line guard against a silent
  wrong answer). Both wave scripts read an empty `AUDIT_PATHS` as "no
  path restriction", inverting the caller's intent.
* **`AUDIT_PATHS` is glob-expanded as well as word-split**
  (`consider`). The whitespace guard existed for exactly this hazard
  and covered only half of it; the guard now also rejects `*`, `?`
  and `[`.
* **The recorded union figure of 39 does not reproduce** (`consider`).
  Re-measured: 46 paths, 13 of them Python, against the path set's 7.
  The number sat inside an instruction to a future reader, so it is
  corrected above and the done-criterion is restated as the property
  it was standing in for.
* **The new tooling had no tests of its own** (`consider`, taken).
  `kerbside/tests/unit/test_plan_range.py` builds a fixture
  repository and covers the guards above, the intersect-not-union
  property, and the two range forms. It was written against the
  pre-fix scripts first: six of its plan-range cases and two of its
  base-resolution cases fail there, so it cannot pass vacuously.

Declined, with reasons:

* **The `audit-allow-print` marker disables the print check for the
  whole diff, not just the marked file.** Still true, and the
  reviewer is right that this PR is the one that both makes the check
  functional and introduces the repository's only marker. Narrowing
  the suppression per-file is a semantics change to scripts the whole
  fleet copies, and restructuring `ADDED` risks the check this phase
  just brought back to life. Taken instead: the check now names the
  file that granted the exemption, so the suppression is visible in
  the output rather than inferred. The narrowing stays upstream work.
* **A `gh` failure inside the process substitution in
  `file-pypi-storage-issue.sh` is invisible** (`info`). Pre-existing,
  unchanged by this PR, and the worst case is a duplicate tracking
  issue on a weekly cron. Already recorded as future work in the
  master plan.
* **Shell lint and flake8 could not be run in the reviewer's
  environment** (`info`). Both run in CI, which is the authority.

## PR review round 2

Round 2 raised one `fix`, nine `consider` and two `info`, against
three `fix` in round 1. Every `fix` in both rounds has been in the
tooling this phase added rather than in the audited range, which is
the expected shape: the plan says outright that the tooling was not
itself audited.

The theme of the round is worth stating, because it is the same one
the phase exists for. Items 2, 3 and 8 are each a check that reports
success having inspected nothing — the wave scripts defaulting to a
bare `develop` that a fetched PR checkout cannot resolve, a range
whose *tip* is unvalidated so every diff silently yields nothing, and
three wave 2 sections whose `(none)` fallback is dead because a
pipeline ending in `head` always exits 0. None of these were
introduced by this PR, and all three are the BRE bug's failure mode
wearing different clothes.

Fixed:

* **The path guard missed the characters that break its own
  contract** (the round's only `fix`). `git diff --name-only` emits a
  non-ASCII path as `"caf\303\251.py"`, which carries neither
  whitespace nor a glob character, passes the guard, and then matches
  nothing when handed back to git — a silently narrowed audit, the
  exact failure the guard exists to stop. Separately, git does not
  escape a single quote in a path, so one would terminate the quoting
  in the emitted `export AUDIT_PATHS='...'` line and hand the
  remainder to the caller's shell. Now derived under
  `core.quotePath=false`, with `'`, `"` and `\` rejected alongside
  whitespace and globs.
* **Both wave scripts defaulted to a bare `develop`.** In a fetched PR
  checkout — the shape anyone re-running the audit for verification
  is most likely to have — the default resolved to nothing and both
  scripts reported success having skipped every diff-based check. They
  now fall back to `origin/develop` exactly as `plan-range.sh` does.
  Where `develop` exists the default is unchanged, so the phase's
  "default behaviour identical" gate still holds.
* **The base-existence guard validated only the left side of the
  range.** `AUDIT_RANGE='origin/develop..nosuchtip'` made every diff
  emit `fatal: bad revision` and produce nothing, and both scripts
  exited 0 reporting clean. Both ends are now validated, and an
  explicitly-set range that does not resolve is fatal (wave 1 exit 6,
  wave 2 exit 1) rather than advisory: an operator who named a range
  and got a green report over nothing is strictly worse served than
  one who gets an error. The *default* range keeps the original
  tolerance, so this cannot fire on the ordinary path.
* **The BRE fix had no regression test** — the defect with the largest
  blast radius in the phase, pinned by nothing. `wave1.sh` gained
  `AUDIT_SKIP_TOX=1`, which runs wave 1b without a tox cycle, because
  a check nothing can cheaply exercise is how this bug survived six
  weeks in the first place. `Wave1StyleCheckTestCase` copies the real
  script into a fixture repository and asserts exit 3 on an added
  `print()`, exit 4 on an added bare `except:`, exit 0 for a
  pre-existing print, a test-file print and a marked file, and exit 6
  for an unresolvable explicit range. Reintroducing the BRE bug fails
  three of them.
* **Three wave 2 sections could never print `(none)`.** A pipeline's
  status is its last command's, so `... | head -20 || echo "(none)"`
  never fires the fallback and an empty section looks identical to one
  that failed to run. Captured into a variable and branched on, as the
  DOCS section already did.
* **`wave1.sh` printed "PASS: no raw print() added" immediately after
  announcing that prints were found and suppressed**, partly undoing
  the visibility added in round 1. The suppressed case now has its own
  message.
* **`plan-range.sh` aborted with git's error, not its own, on a commit
  with no first parent**, breaking the script's stated fail-loudly-and-
  say-why contract.
* Documentation: `PUSH-AUDIT.md` claimed wave 1 runs a single-quote
  style check it has never had, and still described the long-line
  check as being against `develop`; `docs/plans/index.md`'s phase 6
  cell opened with "planned" under a Complete status; and the master
  plan's phase 4 row had lost the detail that 4b was withdrawn rather
  than delivered.

Declined:

* **The `gh` failure inside the process substitution in
  `file-pypi-storage-issue.sh`** (`info`, raised in both rounds and
  correctly triaged by the reviewer as such). Pre-existing, worst case
  is a duplicate tracking issue on a weekly cron, and it stays on the
  master plan's future-work list.
* **`test_plan_range.py` shelling out to git and bash from the unit
  suite** (`info`). Deliberate: the alternative is asserting against a
  reimplementation of the scripts, which cannot catch the class of bug
  this file exists for. `tox -epy3` already requires git.
* Narrowing the `audit-allow-print` marker to the file carrying it
  stays declined, on round 1's reasoning.

## PR review round 3

Two `fix`, two `document`, two `consider`. Both `fix` items are the
round-2 fixes not carried far enough, which is worth recording
plainly: in each case the previous round fixed the instance the
reviewer demonstrated and not the class it belonged to.

* **`core.quotePath=false` was applied to `plan-range.sh` and not to
  the wave scripts.** Both wave helpers do the same
  filename-to-pathspec round trip internally, on a list derived from
  the range rather than from `AUDIT_PATHS` — so `plan-range.sh`'s
  guard never sees it and cannot help. Demonstrated: a branch adding
  `print("boom")` to `kerbside/café.py` made `wave1.sh` report
  "PASS: no raw print() added" and exit 0. It now exits 3.
* **The alembic section's `(none)` fallback was dead, and round 2 is
  what killed it.** That section previously ended in
  `git diff --name-only ... || echo "(none)"`, which fired correctly;
  rewriting it to call `audit_paths_for`, which ends in `|| true` and
  so always exits 0, made the fallback unreachable. A section that
  prints nothing is indistinguishable from one that failed to run —
  the theme round 2 claimed to have closed.

Both are fixed structurally rather than at the two demonstrated
sites, because fixing the instance is exactly what produced this
round:

* Both helpers now take `-c core.quotePath=false`, and both build
  their pathspec list as a bash array rather than an unquoted string.
  The array form also closes `consider` item 6 — the internal file
  list was glob-expanded as well as word-split — and removes both
  `SC2086` disables, so the hazard is gone by construction rather
  than by a comment asking the next editor to be careful.
* Every `(none)` fallback in `wave2-mechanical.sh` now captures into a
  variable and branches, including the two that still worked. They
  worked only because their pipelines happened to end in a `grep`,
  which is the kind of incidental correctness that this round shows
  does not survive a refactor.

Documentation, both of which had drifted from the code inside this
PR: `PUSH-AUDIT.md`'s exit-code table did not list the new code 6 and
its guard list omitted the quoting-character and no-first-parent
guards; and this plan's own definition of done cited
"PASS: no raw print() added" as evidence of the marker path, which
round 2 made mean the opposite — the criterion was unsatisfiable as
written, so a later reader re-checking the phase against it would
have concluded the phase failed.

`consider` item 5 taken: the wave1 tests inherited `AUDIT_RANGE` and
`AUDIT_PATHS` from the ambient environment, so an operator following
this phase's own documented workflow — export the range, then run a
wave script, which runs the unit suite — would have seen spurious
failures. `_GIT_ENV` now clears both alongside the git config it
already cleared.

Nothing was declined this round beyond the two standing declines
(the `audit-allow-print` narrowing, and the `gh` process-substitution
hole in `file-pypi-storage-issue.sh`).

## Back brief

Before executing any step of this plan, back brief the operator on
your understanding of it and how the work you intend to do aligns
with it.

Gate: **step 6a is cheap to propose and expensive to redo.** It
changes the two scripts every future push-audit phase in this
repository will use, and a wrong `AUDIT_RANGE`/`AUDIT_PATHS`
contract would be inherited by all of them. Show the operator the
diff of `wave1.sh`, `wave2-mechanical.sh` and the `PUSH-AUDIT.md`
paragraph, plus the before/after output proving the default
behaviour is unchanged, and get agreement before 6b runs. The
remaining steps need no gate; they produce reports the management
session reads anyway.
