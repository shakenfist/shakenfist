# Phase 1 — close out the autofix loop: derived trailers and an end-to-end proof

Parent plan: [PLAN-fuzz-autofix.md](/components/instar/plans/PLAN-fuzz-autofix/)

This is the first phase *file* for this plan. Everything before it —
the workflow itself, and the staging fix in PR #509 — was tracked
inline in the master plan under "Remaining work". The master plan now
carries an Execution table, and this file is its only row.

## Goal

Retire the master plan's two remaining work items:

1. The hardcoded `Co-Authored-By: Claude Opus 4.6 (1M context)`
   trailer in the Create PR step, which no longer names the model that
   runs — and the two other Claude automations with the same defect
   and a *different* stale name.
2. The plan's own outstanding success criterion: one run that reaches
   a pull request, proving the loop end to end.

## Planning effort

High. The trailer change is not the one-line edit it looks like: it
requires changing how three separate automations capture Claude's
output, and the capture is load-bearing for their failure reporting.
The end-to-end proof has an ordering constraint that has already
bitten this plan twice.

## Scope

**In scope**

* A tested `tools/ci/` helper that turns a `claude -p --output-format
  json` result into (a) the plain result text and (b) an accurate
  `Co-Authored-By` trailer.
* Switching all three Claude automations to JSON output and the
  helper: `.github/workflows/fuzz-autofix.yml`,
  `.github/workflows/test-drift-fix.yml`, and
  `tools/address-comments-with-claude.sh`. All three were converted;
  the third has since been deleted along with the rest of the retired
  comment addresser, so only the two workflows remain to check.
* Documentation for the helper in `docs/development.md`'s script
  index, and for the derived trailer in `docs/testing.md`.
* Dispatching `fuzz-autofix.yml` against issue #485 once the above is
  on `develop`, and triaging whatever it produces.
* Correcting the false and stale claims the survey found in the master
  plan, and refreshing the `docs/plans/index.md` row.

**Out of scope**

* The rebase-planner bug behind #483, #485 and #492 (writes emitted
  past `total_file_size`). If the autofix run proposes a fix for it,
  that PR is reviewed on its own merits as a separate change; this
  phase is not committed to landing it.
* `coverage-fuzz.yml` hitting its 480-minute job timeout — see
  *Out-of-scope findings* below.
* The complexity-gate gap already documented in a comment at
  `.github/workflows/fuzz-autofix.yml:813`: a tracked file the verify
  build modifies between the gate and the commit is committed without
  being counted against the 3-file limit. It is recorded where a
  reader will meet it; changing it needs a run that has actually
  reached that code, which this phase's step 7 will be the first to
  produce.
* `Signed-off-by: Michael Still` hardcoded in
  `tools/address-comments-with-claude.sh:818`. That one was correct —
  the human owning the automation is the sign-off — and was not stale.
  Moot now that the file is gone.

## What the survey found

Checked against the tree at `7b1afe4`.

### The staging fix is in place and correctly positioned

The master plan's Resolution section holds up. `stage-autofix-changes.sh`
runs at `.github/workflows/fuzz-autofix.yml:302` and `:613`, immediately
after each `Run Claude Code` step (`:244`, `:586`) and upstream of every
gate that reads the index — `Check complexity` (`:322`, `:633`) and
`Verify fix` (`:411`, `:715`). The old downstream `git add -u` is now a
`--tracked-only` call at `:836`. Tests run in the `ci-tooling` job at
`.github/workflows/functional-tests.yml:139`. No correction needed.

### The loop has never been exercised, because nothing is eligible

Six scheduled runs have completed since the staging fix merged, two of
them (2026-08-21 `d0aa5499`, 2026-08-22 `7b1afe45`) with the fix on
`develop`. All six succeeded with every step after `Find eligible
issue` skipped. There are three open `security-audit` issues and none
is eligible:

| Issue | Body | Labels | Verdict |
|-------|------|--------|---------|
| #485 | valid fuzzer JSON | `autofix-failed` | blocked by label |
| #492 | valid fuzzer JSON | `autofix-failed` | blocked by label |
| #483 | hand-written prose | none blocking | correctly rejected by `is_valid_fuzzer_json` (`:100`) |

So the fix is on `develop` and has still never run. Nothing is broken;
there is simply no input. The `workflow_dispatch` path at `:110` checks
only `is_valid_fuzzer_json` and **not** the blocking labels, so a manual
dispatch on #485 exercises the full path without editing any labels.

All three are the same underlying bug, which #483 diagnoses in detail.

### The master plan's trailer claim is false

The master plan says the trailer must stay hardcoded because "the
workflow cannot introspect which model the `claude` CLI resolves to".
Measured on the host at CLI 2.1.238, it can:

```json
"modelUsage": {
  "claude-opus-5": {
    "outputTokens": 4,
    "contextWindow": 1000000,
    "canonicalModel": "claude-opus-5"
  }
}
```

`claude -p --output-format json` reports the resolved model and its
context window. This claim is corrected in the master plan as part of
the planning commit.

### The stale trailer is in three places, disagreeing three ways

| Site | Trailer today |
|------|---------------|
| `.github/workflows/fuzz-autofix.yml:850`, `:861` | `Claude Opus 4.6 (1M context)` |
| `.github/workflows/test-drift-fix.yml:522`, `:535`, `:543` | `Claude Opus 4.5` |
| `tools/address-comments-with-claude.sh:820` | `Claude Opus 4.5` |

All three invoke `claude -p ... --output-format text` with stderr
folded into stdout, so all three need the same capture change. Recent
human commits on this repo use the form `Claude Opus 5 (15M context,
high effort) <noreply@anthropic.com>`.

### The address-comments item is done

The master plan's third remaining-work bullet describes PR #511 as
pending ("it lands after this one"). It merged as `7b1afe4`, and
`tools/address-comments-with-claude.sh:637` now calls the stager in
`--tracked-only` mode. Corrected in the master plan.

### Out-of-scope findings

* **`coverage-fuzz.yml` is timing out, and it costs the corpus —
  issue #519.** Five of the last six scheduled runs were killed by
  `timeout-minutes: 480` at exactly 8h00m of job time, and the step
  they were killed on every time is `Push corpus to instar-testdata`.
  The fuzzing itself completes; what is discarded is the corpus it
  produced. The one run that survived (2026-08-16) did so by 35
  seconds, and its step timings show why the 30 minutes of headroom
  the 450-minute `NIGHTLY_BUDGET_SECONDS` leaves cannot work: setup is
  10-15 minutes and the corpus push measured 22 minutes, because it
  full-clones the LFS-backed testdata repo and copies entries one file
  at a time (`.github/workflows/coverage-fuzz.yml:398`).

  This bears on the present phase more than an unrelated CI flake
  would. Coverage-guided fuzzing depends on the corpus accumulating
  across nights, so a nightly campaign that always restarts from the
  same stale seed is not getting deeper — which is a plausible reason
  no new `security-audit` issue has been filed since #485 and #492,
  and therefore a plausible reason the autofix loop has had no input.
  Fixing it still belongs to
  [PLAN-coverage-fuzzing.md](/components/instar/plans/PLAN-coverage-fuzzing/), not here.

## The measured CLI contract

Step 1 measured `claude -p --output-format json` at CLI 2.1.238 rather
than assuming it, and two of the assumptions this plan was written on
turned out to be wrong. Recorded here because the helper's whole job is
to tolerate these shapes.

| Outcome | exit | stdout | `.result` | `.modelUsage` |
|---------|------|--------|-----------|---------------|
| success | 0 | one valid JSON document | present | one key |
| turn exhaustion (`--max-turns` hit) | 1 | one valid JSON document | **absent** | present |
| CLI or flag error, empty prompt | 1 | **zero bytes** | — | — |
| process killed mid-run | — | **zero bytes**, both streams | — | — |
| unknown `--model` (API error) | 1 | one valid JSON document | present, an error string | present but `{}` |

The consequences, in the order they bite:

1. **Turn exhaustion carries no text at all.** `.result` is absent —
   not null, not empty — and `.errors` holds
   `["Reached maximum number of turns (N)"]` instead. stderr is empty
   too, so a fallback to stderr has nothing to fall back to. This is
   the dominant real outcome for an automation that runs with
   `--max-turns 30`, and under the plan as originally written it would
   have written an empty `claude-output-N.txt` for `Report failure` to
   quote into the issue comment.
2. **`--output-format json` is written atomically at exit.** A run
   killed by a timeout leaves zero bytes on both streams, where
   today's `2>&1 | tee` leaves whatever it had. It also means no live
   output in the CI log for the length of the run.
3. **`.subtype` is not an error signal.** The unknown-model case
   reports `"subtype": "success"` with `"is_error": true` and
   `"api_error_status": 404`. Gate on `.is_error` and the exit status,
   never on `.subtype`.
4. **`.modelUsage` has more than one key when a distinct model ran**
   (a subagent on another model, or a fallback). A same-model subagent
   collapses into the single existing key. It can also be `{}`.
5. **The map key is not always the model name.** For haiku the key is
   `claude-haiku-4-5-20251001` while `.canonicalModel` inside the
   value is `claude-haiku-4-5`.
6. **`--output-format stream-json` requires `--verbose`** under
   `--print`, and refuses with a clear error otherwise.

### Corrections already made

The false claims above are corrected at their source in this same
commit, so a later step does not redo the work: the master plan's
Remaining work section now records that the CLI *can* report its model
and that PR #511 merged, and `docs/plans/index.md` carries the phase
link and a description of why the loop has still never run. Nothing
else in the master plan was found to be wrong.

## Decisions

1. **Derive the trailer at run time from `--output-format json`,
   rather than picking a generic string.** The master plan reached for
   a generic trailer only because it believed introspection was
   impossible. It is not, and a derived trailer is the only option
   that cannot go stale a fourth time.

2. **Emit the canonical model id verbatim — `Claude claude-opus-5
   (1M context)` — not a prettified `Claude Opus 5`.** The id is read
   from `.canonicalModel` inside the `.modelUsage` value, not from the
   map key: contract finding 5 shows the two differ for haiku, and
   `.canonicalModel` is the one without the date suffix. The map key
   is the fallback if `.canonicalModel` is missing. *This is the
   decision a reviewer is most likely to argue with*, because it does
   not match the form human commits on this repo use. Prettifying
   means either a lookup table (`claude-opus-5` → `Opus 5`), which is
   the same staleness this phase exists to remove, or a mechanical
   de-slugging that mangles the cases it has to handle
   (`claude-haiku-4-5-20251001`). The id is unambiguous, machine-
   checkable against the model roster, and honest about what actually
   ran. Human-authored commits are unaffected; only the three
   automations use this path.

3. **Context window is rendered, not raw.** `1000000` → `1M`,
   `200000` → `200K`, anything not a clean multiple → the raw digit
   string. This matches the `(1M context)` / `(200K context)` forms
   already in the history.

4. **One helper with two modes, in `tools/ci/`, with its own test
   script wired into `ci-tooling`.** This follows the precedent the
   master plan itself set for `stage-autofix-changes.sh` and
   `pick-fuzz-artifact.sh`: logic that only runs inside a live
   unattended run cannot be tested there, and every bug in this area
   so far has hidden in inline YAML. Three inline copies would
   re-create exactly the drift the survey found.

5. **Capture `--output-format stream-json --verbose`, not
   `--output-format json`.** *Changed after step 1's measurement; the
   plan originally said plain `json`.* Contract findings 1 and 2 kill
   the plain-JSON design: the dominant outcome carries no text, and a
   killed run carries nothing at all. The line-delimited stream fixes
   both. It can be piped through `tee`, so the CI log keeps live output
   and a killed run still leaves a usable partial file; the assistant
   text reconstructed from it is a *superset* of what
   `--output-format text` gives today, so a turn-exhausted attempt
   still reports what Claude actually said; and the final `result` line
   carries the same `.modelUsage`. The cost is that the helper parses
   JSONL rather than one object, and the raw CI log shows JSON lines
   rather than prose.

6. **Never lose the diagnostics.** `--text` mode prints the
   reconstructed assistant text, and appends a short diagnostic block
   naming `.terminal_reason`, `.subtype`, `.num_turns` and `.errors`
   whenever the final result line reports `.is_error` or is missing
   entirely (a truncated stream). If the stream yields nothing usable
   at all it prints the raw stderr file verbatim, so the pre-flight
   CLI errors of contract row 3 survive. A trailer derived from an
   unusable stream falls back to
   `Co-Authored-By: Claude <noreply@anthropic.com>`.

   *Corrected during step 3:* this plan asserted that
   `fuzz-autofix.yml`'s `Report failure` step quotes
   `claude-output-N.txt` into the issue comment. It does not — it
   quotes `tail -30` of `claude-changes-N.txt` (git status, staged
   diff, stager output), and the derived text file reaches a human
   only through the uploaded run artifact and through
   `Extract commit summary`'s marker grep. The claim *is* true of
   `tools/address-comments-with-claude.sh`, which greps the same file
   for `DISAGREEMENT_START` and `CHANGE_SUMMARY_START` and publishes
   what it finds in the pull request's summary comment. The design is
   unchanged — an empty derived file is still a real loss in both
   places — but the stated reason was wrong for one of the two.

7. **Prove the loop by dispatching on #485, and review the resulting
   PR on its merits.** Manual dispatch bypasses the label gate without
   mutating labels, so the proof is repeatable on demand and does not
   depend on waiting for a cron. If Claude's fix for the rebase
   planner is wrong, the PR is closed and the proof still stands: what
   is being tested here is the machinery, not the model.

8. **The end-to-end proof happens after this PR merges, not within
   it.** `fuzz-autofix.yml` checks out `develop` (`:65`), and
   `test-drift-fix.yml` and the address-comments loop take their
   trusted tools from the default branch for the same reason. This is
   the ordering constraint that made #509 land before #511; ignoring
   it here would test the old code and report a false pass.

## Step plan

Steps 1–6 are one pull request. Step 7 runs only once that PR is on
`develop` (Decision 8). Step 8 is a small follow-up commit.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | low | sonnet | none | Measure the JSON contract before anything depends on it, and file the out-of-scope issue. Run `claude -p` with `--output-format json` under three outcomes and record the exact shape of each in a scratch note: (a) plain success; (b) `--max-turns 1` against a prompt needing several tool calls, i.e. turn exhaustion; (c) a hard failure such as an unreadable prompt file. For each, record whether the process exits non-zero, whether stdout is valid JSON, whether `.result` exists and what it holds, `.subtype`, `.is_error`, and whether `.modelUsage` is present. This needs `--dangerously-skip-permissions` for (b), so run it in the scratchpad directory, not in a source tree. Separately, file a GitHub issue against `coverage-fuzz.yml` recording that the scheduled runs of 2026-08-18 through -21 were cancelled at exactly the 480-minute `timeout-minutes`, that the workflow's own 450-minute budget at `.github/workflows/coverage-fuzz.yml:152` is meant to prevent this, and that the campaign is being truncated nightly; reference `PLAN-coverage-fuzzing.md`. Commit subject: `Record the claude JSON output contract.` (the note goes in the phase plan under a new *CLI contract* heading; the issue is not a commit). |
| 2 | high | opus | none | Write `tools/ci/claude-result.sh` and `tools/ci/test-claude-result.sh`. Input is the JSONL stream from `claude -p --output-format stream-json --verbose` — one JSON object per line. **Read *The measured CLI contract* above first and do not assume anything beyond it.** Two modes, flag-style like `tools/ci/stage-autofix-changes.sh`. `--text <stream.jsonl> --raw-fallback <stderr.txt>`: print the assistant text, reconstructed by concatenating `.message.content[]? \| select(.type=="text") \| .text` over lines whose `.type` is `assistant`, in order. Then, if the last `.type == "result"` line has `.is_error` true, **or there is no result line at all** (a killed run leaves a truncated stream), append a short diagnostic block naming whichever of `.terminal_reason`, `.subtype`, `.num_turns` and `.errors` are present. If neither any assistant text nor any result line can be read, print the `--raw-fallback` file verbatim instead, so the pre-flight CLI errors of contract row 3 still reach the issue comment. `--trailer <stream.jsonl>`: print exactly two lines, `Assisted-By: Claude Code` and `Co-Authored-By: Claude <model> (<window> context) <noreply@anthropic.com>`. Read the **last** `.type == "result"` line; from its `.modelUsage` pick the entry with the greatest `.outputTokens` (contract finding 4 — more than one key is normal when a subagent ran on a different model), and take `.canonicalModel` from the *value*, falling back to the map key (contract finding 5). Window rendering per Decision 3. If there is no result line, or `.modelUsage` is absent or `{}`, emit `Co-Authored-By: Claude <noreply@anthropic.com>`. Robustness the contract demands: check the file is non-empty before invoking `jq` at all, and never let one malformed line abort the run — a stream truncated mid-line is expected, not exceptional. Give the script a header comment in the style of `stage-autofix-changes.sh`: why it exists (three automations carrying three different stale model names, and the master plan's own false claim that introspection was impossible), and what it refuses to do (guess a display name from the id, and trust `.subtype`, which reads `success` on an API error). Tests must be a self-contained bash script with fixture JSONL built inline, no network and no `claude` invocation, exiting non-zero on the first failure; cover a successful stream, a turn-exhausted stream with no `.result`, a truncated stream with no result line, a stream whose last line is malformed, an empty file, `.modelUsage` absent / `{}` / one key / two keys with different `outputTokens`, a value whose `.canonicalModel` differs from its key, and all three window renderings (1000000, 200000, 123456). Wire it into `.github/workflows/functional-tests.yml` in the `ci-tooling` job (job at `:100`) next to `Test the autofix stager` at `:139`. `pre-commit` runs shellcheck over `tools/`. Commit subject: `Derive the Claude trailer instead of hardcoding it.` |
| 3 | high | opus | none | Switch `.github/workflows/fuzz-autofix.yml` to JSON capture. Both `Run Claude Code` steps (`:244`, `:586`) currently do `claude ... --output-format text 2>&1 \| tee ${GITHUB_WORKSPACE}/claude-output-N.txt \|\| true`. Replace with `--output-format stream-json --verbose` (the `--verbose` is mandatory under `--print`; the CLI refuses otherwise), stdout piped through `tee ${GITHUB_WORKSPACE}/claude-stream-N.jsonl` so the run log keeps live output, stderr to `claude-stderr-N.txt`, then `tools/ci/claude-result.sh --text claude-stream-N.jsonl --raw-fallback claude-stderr-N.txt > claude-output-N.txt` and `cat` the derived file. Everything downstream keeps reading `claude-output-N.txt` — in particular `Extract commit summary` (`:789`) greps it for the `COMMIT_SUMMARY_START`/`END` markers, and `Report failure` quotes it — so that filename must not change. Do not disturb the `rm -f` of stale stager state or the `--snapshot` call that precede the invocation; do add `claude-stream-N.jsonl` and `claude-stderr-N.txt` to that `rm -f` list, for the same self-hosted-runner reason the comment there gives. Then replace the two hardcoded trailer blocks (`:849`-`:850` and `:860`-`:861`) with the helper's `--trailer` output for the winning attempt (`steps.result.outputs.attempt`). Add both new files to the `Upload logs` list at `:963`. Note the pipe into `tee` makes the exit status that of `tee`, not `claude` — the step already ends `\|\| true` and nothing reads the status, but do not introduce `pipefail` here. `pre-commit` runs actionlint over workflows. Commit subject: `Name the model that actually wrote the fix.` |
| 4 | medium | sonnet | none | Same change to `.github/workflows/test-drift-fix.yml`, which has the identical shape at `:423`-`:427` (capture) and three trailer sites at `:522`, `:535` and `:543`. It writes a single `claude-output.txt` with no attempt suffix, so the stream is `claude-stream.jsonl` and stderr is `claude-stderr.txt`. Two of the three trailer sites are inside `git commit -m` heredoc-style strings with leading indentation baked in; read them carefully rather than pattern-replacing, and prefer restructuring those to `-F` a message file like the summary_found branch already does, so the trailer can be appended by the helper. Follow whatever step 3 settled on for `fuzz-autofix.yml`, and read that diff first. Commit subject: `Name the model in the test-drift fixes too.` |
| 5 | — | — | — | **Done, and since obsoleted.** It landed as `757b0bd` (*Name the model in the review-comment fixes.*), and the file it changed has since been deleted: the `@shakenfist-bot please address comments` automation was retired in the consistency-audit pull request, so nothing below is executable any more. Kept for the record. ~~Same change to `tools/address-comments-with-claude.sh`. Its invocation is at `:603`-`:609` and differs from the workflows: it uses `> "${claude_output_file}" 2>&1` inside an `if !` so the exit status drives `item_error "Claude execution failed"`, and it runs once per review item with an `-${i}` suffix. Preserve the exit-status check exactly — a Claude that fails must still be an item-level error — and add per-item `claude-stream-${i}.jsonl` and `claude-stderr-${i}.txt`. This one is *not* piped through `tee` today and must not become so: the `if !` depends on `claude`'s own exit status, which a pipe would replace with `tee`'s. Redirect stdout straight to the stream file. The trailer is at `:819`-`:820`, inside a `printf` block that also emits `Signed-off-by: Michael Still`; keep the sign-off, replace only the two Claude lines. The helper lives in `${tools_dir}`, which is the trusted copy checked out from the base branch — resolve it as `${tools_dir}/ci/claude-result.sh` alongside the existing `stager`/`resetter`/`patterns` assignments at `:283`-`:285`, not relative to the work tree. Commit subject: `Name the model in the review-comment fixes.`~~ |
| 6 | medium | sonnet | none | Documentation. In `docs/development.md`, add `tools/ci/claude-result.sh` and `tools/ci/test-claude-result.sh` to the script index (the block at `:824`-`:828`, following the phrasing of the `stage-autofix-changes.sh` entry). In `docs/testing.md`, in the automated-bug-fixes section around `:1274`-`:1296`, add a short paragraph saying the commit trailer names the model the CLI actually resolved to, derived from the run's JSON output, and that a run whose output could not be parsed falls back to an unqualified `Co-Authored-By: Claude`. Do not add anything to `AGENTS.md` or `ARCHITECTURE.md`: no convention and no component boundary changes here. Commit subject: `Document the derived commit trailer.` |
| 7 | high | — | — | **Management session, after the PR above merges.** Dispatch `gh workflow run fuzz-autofix.yml -f issue_number=485` and watch it. Confirm each of: `Find eligible issue` reports found=true; `Run Claude Code (attempt 1)` writes a parseable `claude-result-1.json`; the stager stages tracked edits and the complexity gate sees a non-empty index; the run reaches `Commit, push, and create PR`; the pushed commit's `Co-Authored-By` names the model, not `Opus 4.6`. Then triage: if a PR opened, review it as a normal change against #483's diagnosis and either land it or close it with a reason. If the run fails, the failure mode is the finding — classify it, and say whether it is the staging bug returning (it should not be), the model failing on a genuinely hard bug (expected and acceptable), or a new defect in the JSON capture (a regression from steps 3–5). Commit subject: none; the outcome is recorded in step 8. |
| 8 | medium | sonnet | none | Close out. Record the step 7 outcome in this phase plan under a *Result* heading and in the master plan, set the master plan's Execution row and the `docs/plans/index.md` row to `Complete` if the run reached a PR — and to a stated status with a reason if it did not. Update the master plan's Success criteria section to mark the end-to-end criterion met, and move anything still outstanding into its Future work section rather than leaving the plan In progress for it. Commit subject: `Close out the fuzz autofix plan.` |

## Result

Step 7 needed two dispatches, because the first exposed a workflow
defect the plan had not anticipated.

The first dispatch, run 33219527764 on 2026-08-28, `-f
issue_number=485`, was the first run of this workflow ever to get past
`Find eligible issue`. Both attempts failed verification identically,
on `make test-container-core`: `Error: Test data not found at
.../instar/../instar-testdata`. The workflow had never checked out
`instar-testdata`, so that make target could never pass and no run
could ever have reached `Commit, push, and create PR`. This is a
fourth failure mode; the plan explicitly listed three at step 7 --
the staging bug returning, the model failing on a hard bug, and a
JSON capture regression -- and it was none of them. Not the staging
bug: `stager-rc-1.txt` and `stager-rc-2.txt` were both `0`, the diff
staged cleanly, and the stager correctly ignored 22
`prototypes/*/target/` build artifacts. Not a capture regression: the
capture worked, as done-criterion 5 confirms below. And not the
model: both attempts produced a plausible single-file root-cause fix
with regression tests. It was fixed in PR #530 (merged 2026-08-30 as
`931b5a9`), which added the `Prepare instar-testdata` and `Resparsify
test images` steps that every other test-running workflow already
had, placed before `Construct prompt` so Claude has the fixtures
during its own attempt too.

Done-criterion 5 was confirmed on this live run, not only on step 2's
fixtures. Both attempts of the first dispatch hit `max_turns` at 31
turns of 30, and `claude-output-1.txt` carried the reconstructed
assistant text *plus* the diagnostic block naming `terminal_reason:
max_turns`, `subtype: error_max_turns` and `num_turns: 31` -- exactly
the turn-exhaustion case the step 1 contract measurement caught, and
the case plain `--output-format json` would have written an empty
file for. Steps 2-5 are validated by a real unattended run.

The second dispatch, run 33297854229 on 2026-08-30, `-f
issue_number=485 -f max_turns=40`, ran after PR #530 merged, and is
the proof. Attempt 1 passed verification; `Prepare retry` was
skipped; `Commit, push, and create PR` completed; `Report failure`
was skipped. It opened **PR #533** from branch `autofix/issue-485`,
commit `2d40cb0`.

The decisive check was the trailer on that pushed commit, which
reads `Co-Authored-By: Claude claude-opus-5 (1M context)
<noreply@anthropic.com>`. That is Decision 2's canonical-id form, not
the old hardcoded `Claude Opus 4.6 (1M context)`, and it could only
have been produced by the merged helper -- so it also proves
Decision 8's ordering constraint held.

The fix in PR #533 is itself good, and was reviewed. It bounds the
untrusted `backing_file_offset` and `backing_file_size` in
`src/crates/rebase/src/qcow2.rs`, using version-aware header bounds
from the real `QCOW2_HEADER_LENGTH_V3` and
`V2_HEADER_EXTENSION_OFFSET` constants, handles u64 overflow, adds a
short-overlay check returning `OverlayCorrupt`, adds four regression
tests, and updates `CHANGELOG.md` and `docs/rebase.md`. The bound is
sound because `path_len <= backing_file_size` is enforced before
`slot_end = offset + backing_file_size <= overlay_file_size` is
checked. Landing it remains a separate decision, per Decision 7,
which keeps that judgement apart from whether the machinery worked.

One operational note for whoever picks #533 up. Its CI had not run at
the time of writing, and the reason is not that the workflow failed to
trigger: all five workflow runs exist against the branch on a
`pull_request` event and every one is `action_required`, which is
GitHub waiting for a maintainer to approve workflow runs on a pull
request authored by `app/github-actions`. The pull request reports
`mergeStateStatus: BLOCKED` until someone does. That is a repository
setting rather than a defect, but it does bound what this loop can
ever do unattended: the master plan's success criterion says "issue to
merged PR", and the merge half necessarily waits for a human here.

Two defects were found along the way and deferred, both filed as
issues rather than fixed in this phase. #529 records that the
workflow never runs the crash reproducer, which the master plan's
step 5 lists as verification check 3 -- not a missing line, but a
structural gap: the crash input is not in the issue body, only in the
`coverage-fuzz-logs` artifact of the run named by `.ci_run`, those
artifacts expire after 90 days, and there is no make target that
replays a single input. The master plan's step 5 and its step 7 pull
request body template were corrected to match the implementation, in
the same PR #530. #534 records that every attempt so far has
exhausted its turn budget -- 31/30, 31/30, then 41/40 -- and because
`tools/autofix-prompt-base.txt` asks for the
`COMMIT_SUMMARY_START`/`END` block at the end of the work, a
turn-exhausted attempt never emits it. `summary_found` was false for
PR #533, so its commit body is the fallback "Automated fix for
security-audit issue." and its title the fallback "Fix fuzzer crash:
${ISSUE_TITLE}". Raising the budget from 30 to 40 did not help; the
model did more work and still ran out.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| JSON capture loses the diagnostics that `2>&1 \| tee` gave, so a failed run reports nothing useful into the issue comment. | Decision 5's `--raw-fallback`, and step 2's tests cover the malformed-JSON and empty-file cases explicitly. The management session checks step 3's diff for the fallback wiring before the PR goes up — this is the failure that would only be noticed months later, on a run nobody was watching. |
| `--output-format json` behaves differently on turn exhaustion than on success, and the workflow's most common outcome is turn exhaustion. | **Realised.** Step 1 measured it before step 2 was written, and it was worse than assumed: turn exhaustion omits `.result` entirely and leaves stderr empty. The capture format changed to `stream-json --verbose` in response (Decision 5), and done-criterion 5 checks the case directly. |
| Steps 3–5 are three near-identical edits done by three sub-agents, and drift between them re-creates the defect. | The shared helper is the structural mitigation; on top of it, steps 4 and 5 are briefed to read step 3's diff first, and the management session diffs the three call sites against each other before proposing the commit. |
| The step 7 run produces a bad fix for a real correctness bug and it gets landed on the strength of "the automation worked". | Decision 7 separates the two judgements. The PR is reviewed against #483's written diagnosis, by a human, as an ordinary change. |
| The end-to-end proof is run against un-merged code and reports a false pass. | Decision 8. Step 7 is explicitly gated on the PR being on `develop`, and its first check is that the pushed trailer is not `Opus 4.6` — which is only possible if the merged code ran. |

## Definition of done

Falsifiable, in order:

1. `grep -rn 'Co-Authored-By: Claude Opus' .github/ tools/` returns
   nothing. **Met**, by the steps 1-6 pull request.
2. `tools/ci/test-claude-result.sh` exits 0, and
   `grep -c 'test-claude-result.sh' .github/workflows/functional-tests.yml`
   is at least 1. **Met**, by the same pull request.
3. `grep -c 'output-format text' .github/workflows/fuzz-autofix.yml
   .github/workflows/test-drift-fix.yml` is 0 for both, and both pass
   `--output-format stream-json --verbose`. This read
   `tools/address-comments-with-claude.sh` as a third site; it was
   converted in `757b0bd` and the file has since been deleted with the
   retired comment addresser. **Met**.
4. `tools/ci/claude-result.sh --trailer` on a fixture result line
   whose `modelUsage` value has `canonicalModel` `claude-opus-5` and
   `contextWindow` 1000000 prints exactly
   `Co-Authored-By: Claude claude-opus-5 (1M context) <noreply@anthropic.com>`
   as its second line; on an empty file it prints
   `Co-Authored-By: Claude <noreply@anthropic.com>`. **Met**.
5. `tools/ci/claude-result.sh --text` on a turn-exhausted fixture — a
   stream with assistant text and a result line carrying
   `"subtype":"error_max_turns"` and no `.result` — prints the
   assistant text *and* a block naming `max_turns`. This is the
   regression the contract measurement caught; it is checked
   separately because it is the case the original plan got wrong.
   **Met** on the fixture, and confirmed again on a live run in step
   7 -- see the *Result* section above.
6. `pre-commit run --all-files` passes, including actionlint over the
   two workflows and shellcheck over the two new scripts. **Met**.
7. A `fuzz-autofix.yml` run exists whose `Commit, push, and create PR`
   step completed, and whose pushed commit's `Co-Authored-By` line
   names a model from the current roster. **Met**: run 33297854229 on
   2026-08-30 opened PR #533 with `Co-Authored-By: Claude
   claude-opus-5 (1M context) <noreply@anthropic.com>`.
8. No fact about the trailer is stated differently in
   `docs/development.md`, `docs/testing.md`, and
   `PLAN-fuzz-autofix.md`. **Met**, by the corrections made in PR #520
   and PR #530.
9. The master plan contains no claim that the workflow cannot
   introspect its model, and no description of PR #511 as pending.
   **Met**, by the same corrections.

## Back brief

Before executing any step of this plan, back brief the operator on
your understanding of it and how the work you intend to do aligns.

Two gates within the phase:

* **After step 1, before step 2.** The measured JSON contract decides
  what `--text` mode has to tolerate. Report the three shapes and the
  proposed fallback rule, and get agreement before writing the helper
  — this is cheap to agree and expensive to rework once three call
  sites depend on it.
* **After step 6, before step 7.** Step 7 is an unattended run against
  a live issue that can open a pull request. Confirm the operator
  wants it dispatched, and when.
