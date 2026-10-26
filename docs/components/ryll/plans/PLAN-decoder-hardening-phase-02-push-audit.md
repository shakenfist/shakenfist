# Phase 2: Push audit

Parent plan: [PLAN-decoder-hardening.md](/components/ryll/plans/PLAN-decoder-hardening/)

## Goal

Run `PUSH-AUDIT.md` over the whole of the decoder hardening work,
which landed as PR #443 (`a167670`), and resolve or decline every
finding in writing. This is the plan's closing phase.

## Planning effort

**Medium.** The runbook is written, two earlier closing audits in
this repository (`PLAN-idle-cpu-and-latency` phase 6 and
`PLAN-stream-caps-and-flap` phase 18) set the shape, and the range
here is a single merge commit. The judgment is in the triage step,
because three weeks of unrelated work has rewritten half of the
audited files since. The steps carry their own effort levels.

## Scope

In:

- Wave 1 and wave 2 of `PUSH-AUDIT.md` over `a167670^1..a167670`.
- Triage of every finding against current `develop`.
- A fix-or-decline decision for each finding, recorded in the
  master plan.

Out:

- Fixing the findings. Under the `plan-push-audit-phase` shared
  block they land as their own pull request against `develop`;
  this phase decides and records.
- Code that later plans wrote over the audited files: the LZ4
  decoder rewrite, the cursor format work, the RDP renderer prep.
  Those landed under their own plans and, where those plans had
  one, their own push audits. The survey below checked that they
  kept the shared limit, and that check is not repeated.
- #135 (compression fuzz targets) and #436 (per-channel message
  caps). Both are already in the master plan's Future work.

## What the survey found

The master plan's Phase 2 section was written before Phase 1
merged. One of its claims was wrong and is corrected at source in
this branch's planning commit; the rest held.

### Phase 1 is complete

| Check | Result |
|-------|--------|
| Master plan and `index.md` agree | Both `In progress`; Phase 1 set `Complete` in this branch's close-out commit |
| On `develop` | PR #443 merged 2026-10-04 as `a167670`; `git merge-base --is-ancestor a167670 develop` succeeds |
| Issues | #171-#177, #182 and #442 are all closed |
| Leftover branch or worktree | None, local or remote |
| Deferred work | #135 and #436 open and listed under Future work |
| Release | `a167670` is an ancestor of `v0.2.0`, so the breaking `DecompressedImage` change shipped as the minor bump the plan asked for |

Definition-of-done spot checks, against `develop` at `2edb002`:

- `shakenfist-spice-compression/src/limits.rs` defines
  `MAX_IMAGE_DIMENSION`, `MAX_IMAGE_PIXELS` and `rgba_len`.
- `STYLEGUIDE.md` (around line 298), `AGENTS.md` (around line 148)
  and `docs/spice-protocol.md` (around line 554) all point at
  `rgba_len`, and none quotes the caps' values.
- The master plan's `vec![0u8` success criterion still holds. Every
  non-test allocation in the two crates sized from wire dimensions
  goes through `rgba_len` or is bounded by something that did:
  `lz4.rs` (decoded buffer and the 16/24-bit RGBA buffer, both after
  `rgba_len`), `quic.rs` (`output_len`, checked in
  `quic_decode_begin`), `cursor.rs` `decode_cursor_pixels`, and the
  pixmap, crop and clip buffers in `display.rs`, which are bounded by
  a `DecompressedImage` or by an `rgba_len`-checked pixmap. The
  `h264.rs`, `encoder/task.rs` and `surface_mirror.rs` hits are in
  test modules.

That last point is the plan's mission working as intended: the LZ4
decoder was rewritten (`df2f06b`) and the cursor channel gained three
formats (`29391fe`) after the merge, and both size their buffers with
`rgba_len`.

### The audit range is exact

One merge commit holds the whole plan. `a167670^1..a167670` is 22
files, 1 889 insertions and 320 deletions, and every commit in it is
this plan's: the 14 on the `decoder-hardening` branch, plus #457
(`fromcache-end-of-payload`), which merged into that branch and
fixed #442, a bug the plan found. Nothing foreign needs filtering.

### The audited files have drifted unevenly

`git diff --stat a167670 develop` over the 22 files:

| Files | Drift since `a167670` |
|-------|-----------------------|
| `quic.rs`, `glz.rs`, `lz.rs`, `jpeg.rs`, `limits.rs` | None |
| `lib.rs`, `video.rs`, `shakenfist-spice-protocol/src/lib.rs` | Small |
| `lz4.rs` | Rewritten (+946/-, `df2f06b`, then fuzzed in `9364569`) |
| `cursor.rs` | +885 (`29391fe`, mono/4-bit/16-bit cursors) |
| `display.rs` | +2 351 (linear-time framing, clip merging, stream crop, damage tracking) |
| `surface.rs` | +258 (damage rectangles, `33d615b`) |
| Docs (`releasing.md`, `spice-protocol.md`, `STYLEGUIDE.md`, `AGENTS.md`) | Substantial |

So a finding in the first row is live until shown otherwise, and a
finding in `lz4.rs`, `cursor.rs`, `display.rs` or `surface.rs` must
be located in today's code before anyone acts on it. The `lz4.rs`
rewrite was push-audited in its own plan (`c67720c`, PR #487), which
lowers the value of auditing the old `lz4.rs` hunks, but they are
still in the range and are reviewed like the rest.

### One finding already in hand

`DecompressedImage` (`shakenfist-spice-compression/src/lib.rs:118`)
is `#[non_exhaustive]`, which stops a struct literal outside the
crate, but all five fields are `pub`. Anything holding a
`&mut DecompressedImage` can set `pixels` or `width` and break the
length invariant that `new` and `new_glz` enforce, and code inside
the crate can still build one with a literal. Nothing does either
today (`grep -rnE '\.(pixels|width|height) = '` finds only QUIC's
own decoder state). Mission item 3 says the mismatch "cannot be
constructed", which is true of construction from outside the crate
and false of mutation. This goes to the triage step as a finding
like any other; it is not decided here.

### One stale claim in the master plan, corrected

The master plan's Phase 2 section said the audit's findings pull
request "records Phase 1's `Merged` cell and closes this plan out".
Under the `plan-phase-landing` shared block, Phase 1 is closed out
by the first commit of this branch, which also records its
`Merged` cell. The plan closes in the findings pull request if
there are findings, and in this phase's own pull request if there
are none. The section now says so, and links this file.

The master plan's Future work entry for #135 now notes that the LZ4
decoder has a fuzz target (`fuzz_lz4_decode`, `9364569`), so what
remains of #135 is QUIC, LZ, GLZ and the cursor decoder.

## Decisions

1. **The range is `a167670^1..a167670`, and it is used as given.**
   No sub-patches and no filtering, because nothing foreign is in
   it. At 22 files, one agent per judgment area can hold the whole
   patch; the area split phase 18 used for 64 files is not needed.

2. **Agents review the patch as it was, and one triage step maps
   findings onto today's code.** This is the split phase 6 and phase
   18 both used. Asking each of four reviewers to also check
   current `develop` would repeat that work four times and mix two
   questions in each report.

3. **The audit does not widen to the post-merge rewrites.** The
   plan's purpose was to leave a convention that later decoders
   follow, and the survey has checked that they did. Re-auditing
   the new `lz4.rs` and cursor code would be auditing other plans'
   work, one of which already had its own audit.

4. **Findings land in a separate pull request, after this one.**
   That is what the shared block and the runbook say, and it is how
   both earlier closing audits in this repository ran. This branch
   carries the close-out, the plan, and the execution record: the
   findings, their triage, and the decision on each. The findings
   PR carries the fixes and sets the plan `Complete`. If nothing
   needs a code change, this branch sets the plan `Complete`
   itself, and no `Merged` cell is recorded for this phase.

   This is the decision most open to argument. For a 22-file diff
   a reviewer could reasonably ask for the fixes on this branch, in
   one PR, and it would save a CI run. The case for the rule is that
   this PR then reviews as prose, the findings PR as code, and a
   reviewer can approve the audit's judgements without also
   approving their fixes. If the findings turn out to be one or two
   small edits, the management session may raise folding them in
   at Step 5 rather than deciding it now.

5. **The security review is the one that matters, and gets opus at
   high effort.** The plan is a security fix. The question the security agent must
   answer is not "is this code safe" in general but "did each fix
   close its issue, and did any fix open a new path": a run guard
   off by one, an `rgba_len` result multiplied again without a
   check, an inflate bound that overflows, a `warn_once!` key that
   lets a hostile server flood the log.

## Steps

The runbook's sub-agent briefs are used as written, with the
additions below. Step numbers are this phase's; "2a" to "2d" are
the runbook's names for its four judgment agents. Steps 1 to 4 produce findings recorded in this
file, not code. `$PATCH` is
`<scratchpad>/decoder-hardening-audit.patch`, in the scratchpad
directory of the session running the phase; write its absolute path
into each brief.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | low | sonnet | none | Assemble the patch and run wave 1. `git diff a167670^1 a167670 > $PATCH`, then `git diff --shortstat a167670^1 a167670`; it must read **22 files changed, 1889 insertions(+), 320 deletions(-)**, and if it does not, stop and report, because every later step depends on it. Then run `AUDIT_BASE=a167670^1 AUDIT_HEAD=a167670 timeout 3600 tools/audit/wave1.sh` from the worktree root, as a background task, since the Docker build and test cycle is long. Exit codes are tabulated in `PUSH-AUDIT.md`. Two things to know before reading the output. The build, lint and test stages run against the **current tree**, so a failure there is a regression on `develop` today, not a defect in this plan; say which it is. The range-scoped style checks read files **at `a167670`**, so a long-line or unguarded `log_message` hit may already be gone; report each, do not fix any, and mark them for Step 4. On exit 1-3, stop and report; do not go on to Step 2. Report the exit code and every non-pass line. |
| 2 | low | sonnet | none | Run `AUDIT_BASE=a167670^1 AUDIT_HEAD=a167670 tools/audit/wave2-mechanical.sh` and report its output verbatim. Then do the runbook's *Style conformance — judgment portion* against `$PATCH`. Look in particular at: the `display:` and `cursor:` prefixes on every new `warn!`/`warn_once!`; whether each new `warn_once!` key names its decoder and failure the way the existing keys in `display.rs` do; and whether any field changed units without a rename. Report a short list, or "Style checks passed." |
| 3 | medium-to-high | sonnet (2a, 2b, 2c), opus (2d) | none | The four judgment agents from `PUSH-AUDIT.md`, run in parallel, each given `$PATCH` and the Step 2 report, and told the patch is from 2026-10-04: report what the patch shows and do **not** check current `develop`, which is Step 4's job. Use the runbook's briefs, with these additions. **2a (code quality):** the eight fixes went into six decoders; look for a guard written twice that should be one helper (the `rgba_len`-then-`warn` pattern, the run-overshoot check in four QUIC loops), and for comments longer than the guard they explain, per the `comment-proportion` block. Also judge the in-hand finding from this plan's survey: `DecompressedImage`'s fields are `pub`, so the length invariant holds at construction but not under mutation; say whether that is worth closing, and how (private fields with accessors is a breaking change to a published crate). **2b (test review):** each of the eight issues should have a regression test that fails on the old code; map test to issue and name any issue without one. The master plan's success criteria name three that are proven by reasoning instead (#172, #175, #177's short-data case); say whether the reasoning in the test comments holds. **2c (documentation):** `docs/spice-protocol.md`'s "Decode limits" text, `STYLEGUIDE.md`'s "Dimension safety", `AGENTS.md` item 5 and the compression crate's README must agree with each other and with `limits.rs`, and none may quote a cap's value. Also check `docs/releasing.md`'s new version-choice text. **2d (security, opus, high):** for each of #171-#177, #182 and #442, say whether the patch closes it and whether the fix opens anything new. Specifically: the QUIC run guard (`run > end - i`) and the `melcstate < J.len() - 1` guard against `quic.c`/`quic_tmpl.c` in `/srv/src-reference/spice/spice-common/common/`; any arithmetic done on an `rgba_len` result without a further check; `glz_stream_limit`'s `rgba + rgba / 4 + GLZ_STREAM_FIXED_OVERHEAD` and the `take(limit + 1)` inflate; the pixmap `width * 4 <= stride` check's own overflow; `DisplaySurface::blit`'s saturating `copy_width`; whether a hostile server can make any of the new `warn_once!` sites log without bound; and the `pub`-fields finding above, from the attacker's side. Severity per the runbook. |
| 4 | high | opus | none | Triage. Take every finding from Steps 1, 2 and 3, plus the in-hand `DecompressedImage` finding, and classify each against **current `develop`** as `still-present`, `already-fixed` or `moved`. Use the drift table in this plan's survey: findings in `quic.rs`, `glz.rs`, `lz.rs`, `jpeg.rs` and `limits.rs` are live until shown otherwise, since those files are unchanged; findings in `lz4.rs`, `cursor.rs`, `display.rs` and `surface.rs` must be located line by line, because those files were rewritten in large part after the merge. A finding you cannot locate is `already-fixed` only if you can name the commit that fixed it; otherwise it stays `still-present` for a human look. Output one table: finding, source agent, severity, status, current file and line, and for `already-fixed` the fixing commit. |
| 5 | medium | opus | none | Management step, not a sub-agent step. Decide fix-or-decline for each `still-present` finding, and record the outcome in the master plan under a new *Items deferred from the push audit* heading, in the shape `PLAN-stream-caps-and-flap.md` uses (its line 1138). Every declination gives a reason. Record the audit's raw output and the Step 4 table in this file under an *Execution record* heading, summarised where long. Update the master plan's *Bugs fixed during this work* if the audit adds any. If nothing needs a code change, say so in one sentence and set the plan `Complete` in both the Execution table and `index.md` on this branch; otherwise leave it `In progress` and open the findings PR after this one merges. Commit subject: "Record the decoder hardening push audit." |

## Risks and mitigations

- **A clean audit for the wrong reason.** An empty or wrong range
  reads as no findings. Step 1 gates on the exact shortstat, and
  `wave1.sh` exits 6 on an empty explicit range. The management
  session checks the Step 1 report states the 22-file figure before
  starting Step 2.
- **Stale findings.** Four of the audited code files have been
  heavily rewritten since. A finding against old `display.rs` line
  numbers acted on blindly would edit the wrong code. Step 4 exists
  to stop that, and its "name the fixing commit" rule stops a
  finding from being waved away as fixed. The management session
  spot-checks two `already-fixed` claims in the Step 4 table before
  accepting it.
- **Wave 1 fails for reasons that have nothing to do with this
  plan.** The build and tests run on today's tree. Step 1 has to
  say which kind of failure it is; a `develop` regression is
  reported and handled outside this phase, and it does not block
  wave 2 if the range-scoped checks ran.
- **The security agent reviews the old LZ4 decoder at length.**
  That code is gone. Its findings will triage as `moved` or
  `already-fixed` and cost little beyond the agent's time; the
  brief's emphasis on the QUIC, GLZ, pixmap and blit fixes, whose
  code is mostly unchanged, is the mitigation.

## Definition of done

- `git diff --shortstat a167670^1 a167670` reports 22 files, 1 889
  insertions and 320 deletions, and this file records that the audit
  ran over that range.
- This file's execution record holds the wave 1 exit code, the wave
  2 mechanical output (or a summary of it), and the Step 4 triage table.
- Every row of the Step 4 table marked `still-present` appears under
  *Items deferred from the push audit* in the master plan, as fixed,
  to be fixed in the findings PR, or declined with a reason.
  Checkable by counting: the two numbers are equal.
- The master plan's Execution table reads Phase 1 `Complete` with
  `Merged` `a167670`, and Phase 2 linked to this file.
- If the audit found nothing needing code, the master plan and
  `index.md` both read `Complete`, and Phase 2's `Merged` cell is
  empty. Otherwise both read `In progress`.
- `pre-commit run --all-files` passes on each commit.

## Back brief

Before executing any step of this plan, back brief the operator on
the range being audited, the four judgment agents and their models,
and how findings will be landed. Step 5's fold-in question (decision
4) is raised with the operator, not settled by the management session
alone.

## Execution record: 2026-10-25 (AEDT)

### Back brief answers

The operator confirmed the plan as written. Findings land in a
separate pull request after this one (decision 4 stands), and this
session may push this branch and open its pull request once the
definition of done passes.

### Departures from the plan

- The management session assembled the patch itself, because it is
  one command, and checked the gate: `git diff --shortstat
  a167670^1 a167670` reads 22 files, 1 889 insertions, 320
  deletions. The Step 1 sub-agent ran only wave 1.
- Steps 1 and 2 ran in parallel, and Step 3 started before wave 1
  finished. Wave 2 mechanical only reads git, and the plan's risks
  section already says a wave 1 failure on today's tree does not
  block wave 2. Wave 1 then passed, so nothing was discarded.
- Problems that predate the plan were filed as issues rather than
  recorded as findings: #626, #627, #628 and #629. See Step 5.

### Step 1: wave 1, exit 0

`AUDIT_BASE=a167670^1 AUDIT_HEAD=a167670 tools/audit/wave1.sh`
exited 0. pre-commit, rustfmt and clippy, and `cargo test
--workspace` all passed (every `test result` line reads 0 failed).
The `println!`/`eprintln!`, long-line and unguarded `log_message`
checks reported no hits. The println scan takes its directories
from the workspace `members`, which include both audited crates.

### Step 2: wave 2 mechanical, exit 0, and style

- No TODO/FIXME/HACK, no new `#[allow(dead_code)]` and no new
  `unsafe`.
- 31 new `#[test]` functions across 14 Rust files, and 7
  documentation files touched.
- 20 new `.unwrap()`/`.expect()` hits. The code-quality agent
  confirmed all 20 are inside `#[cfg(test)]`.
- The style review found no blocking items, and three advisory
  ones (W1-W3 below).

### Step 3: judgment agents

| Agent | Model | Blocking | Advisory | Notes |
|-------|-------|----------|----------|-------|
| 2a code quality | sonnet, medium | 0 | 10 (Q1-Q10) | |
| 2b tests | sonnet, medium | 1 (T1) | 7 (T2-T8) | Every issue has a regression test |
| 2c docs | sonnet, medium | 0 | 5 (D1-D5) | No cap values quoted; no plan citations in code |
| 2d security | opus, high | — | S1 high, S2 medium, S3-S5 low, S6-S7 info | All nine issues closed |

The security agent's verdict was that #171-#177, #182 and #442 are
all closed. The QUIC run guard matches `quic_tmpl.c` in all four
loops, and the `melcstate` guard matches `quic.c`. No `rgba_len`
result is multiplied again without a check, and every new
`warn_once!` key is a static literal.

The management session reproduced T1 and S1 against the code
before accepting them. For T1: at `a167670`, and still today,
`rgba_len` refuses the test's `width = 1_000_000` before
`row_fits_stride` runs, so the #173 guard is never reached. For
S1: in the LZ and GLZ block-copy path, a back-reference to one
pixel before the image start wraps `src` to `usize::MAX - 3`, and
`src + 4` wraps past the guard.

### Step 4: triage against `develop` at `2edb002`

The 34 raw findings merge into 30 rows: W3 with S5, Q8 with S2,
and Q10 with S6 and the survey's in-hand finding. 29 are still
present, and one, Q7, was fixed by `df2f06b`. The management
session checked Q7 against today's `lz4.rs:76-80`. T1's test is
byte-for-byte unchanged at `display.rs:5259`. Locations are on
`develop`.

| ID | Finding | Severity | Status | Predates plan | Location |
|----|---------|----------|--------|---------------|----------|
| W1 | Pixmap refusal prints `{} x {}`, unlike `{}x{}` elsewhere | advisory | present | no | display.rs:2459 |
| W2 | `pixmap:too_large` key also covers zero and per-side refusals | advisory | present | no | display.rs:2458 |
| W3/S5 | Refusals log with plain `warn!`, not `warn_once!` | low | present | mixed | lz4.rs:82; quic.rs:649, 1596; cursor.rs:624, 628; display.rs:2872 |
| Q1 | QUIC run-overshoot guard pasted four times | advisory | present | no | quic.rs:950, 1034, 1351, 1424 |
| Q2 | `rgba_len`-then-warn repeated at nine sites | advisory | present | no | lz.rs:63, glz.rs:272, lz4.rs:81, … |
| Q3 | "only refuses outside the limits" comment repeated | advisory | present | no | lz.rs:176, glz.rs:531, lz4.rs:167, display.rs:2761, 2801 |
| Q4 | Comments longer than the guards they explain | advisory | present | no | display.rs:363-378, 2449-2455, 2477-2482; quic.rs:643-647 |
| Q5 | Stale "16384x16384x4 = 1 GiB" sentence | advisory | present | partly | surface.rs:14-18 |
| Q6 | LZ4 test calls 16384x16385 "both sides legal" | advisory | present | no | lz4.rs:464 |
| Q7 | LZ4 lost the `row_bytes` checked-multiply rationale | advisory | fixed (`df2f06b`) | no | lz4.rs:76-80 |
| Q8/S2 | Surfaces skip the pixel cap and have no count limit | medium | present | yes | surface.rs:23, 48-67; surface_mirror.rs:102, 157 |
| Q9 | display.rs, jpeg.rs, quic.rs over 1 500 lines | advisory | present | yes | display.rs is now 6 030 lines |
| Q10/S6 | `DecompressedImage` fields are `pub` | info | present | yes | lib.rs:118-127 |
| T1 | #173 test's width is refused by the side cap first | **blocking** | present | no | display.rs:5259-5273 |
| T2 | #174 test is over the pixel cap; no in-cap mismatch | advisory | present | no | display.rs:5296-5320 |
| T3 | No test drives ZlibGlzRgb through `handle_message` | advisory | present | no | display.rs:2595 |
| T4 | QUIC tail run loops never exercised | advisory | present | no | quic.rs:1034, 1424 |
| T5 | No QUIC at-cap or over-pixel-cap header test | advisory | present | no | quic.rs tests |
| T6 | QUIC hand-packed tests reach into decoder internals | advisory | present | no | quic.rs:1745, 1846-1856 |
| T7 | #175 tests tell old from new only by message wording | advisory | present | no | lz.rs:187, glz.rs:542, lz4.rs:456 |
| T8 | No cursor test over the pixel cap with in-cap sides | advisory | present | no | cursor.rs:948-960 |
| D1 | Decode-limits paragraph restates `jpeg.rs` internals | advisory | present | no | docs/spice-protocol.md:539-560 |
| D2 | Docs and `jpeg.rs` disagree on how WIC/ImageIO are bounded | advisory | present | no | spice-protocol.md:545-549; compression README:52-55; jpeg.rs:58-59 |
| D3 | `limits.rs` doc comments quote values and module history | advisory | present | no | limits.rs:6-32 |
| D4 | "Breaking changes" PR heading has no template | advisory | present | no | docs/releasing.md:164 |
| D5 | Guest-driver `QXL_IMAGE_CACHE` claims not checkable in-repo | advisory | present | no | spice-protocol.md:448-451 |
| S1 | LZ/GLZ back-reference one pixel before start panics | **high** | present | yes | lz.rs:133, 151; glz.rs:381, 399 |
| S3 | zlib-GLZ inflate bounded by limit, not declared size | low | present | no | display.rs:393-421 |
| S4 | jpeg-decoder L8 path allocates before its dimension check | low | present | partly | jpeg.rs:234-235, 278 |
| S7 | QUIC `runlen += melcorder` can overflow a 32-bit usize | info | present | yes | quic.rs:665 |

### Step 5: decisions

The decision for each row is recorded in the master plan, under
*Items deferred from the push audit*. In summary:

- 16 rows go to the findings pull request.
- 8 are declined, each with its reason.
- 5 go to issues and leave this plan's scope: the four problems
  that predate it (#626 to #629), plus Q5, which #627 covers.
- Q7 needs no action.

Because rows need code changes, the plan stays `In progress`. The
findings pull request sets it `Complete`.
