# Consistency audit phase 4: review scope and coverage

Master plan:
[PLAN-consistency-audit.md](/components/kerbside/plans/PLAN-consistency-audit/)

**Planning effort:** high. Two of the three judgements in this
phase are permanent: which file types are subject to human review
for the life of the repository, and what to do about the fact
that no review mark this repository has ever recorded was signed.
The third, the order in which 112 files get read, decides whether
the first sessions find anything.

## Scope

**In scope:**

- Issue #227 (`review-coverage`): 77 in-scope files need review
  against a threshold of fewer than 5.
- The `review-scope-completeness` check, which fails today with
  44 orphaned files and has **no issue open against kerbside
  yet**. It is folded in here rather than given a phase of its
  own; see decision 1.
- Configuring commit signing in this clone, and deciding what to
  do about the unsigned marks already in history.
- An ordered work queue and a per-session recipe, so the grind
  can be picked up by any session without re-deriving the order.

**Out of scope:**

- Re-reviewing the 115 files that already carry a mark. Their
  marks are unattested (survey finding 4) and that is worth
  knowing, but re-attesting without re-reading would be a
  signature over a claim nobody checked.
- Teaching the `review-coverage` audit to care about signatures.
  That is an upstream change to `shakenfist/development` and is
  recorded as future work, not done here. See decision 4.
- Issues #370 and #381, which are phase 5.
- Any change to the files being reviewed. A review session
  produces marks and, where it finds something, issues. It does
  not produce fixes; a fix found this way becomes its own change
  with its own review.

## What the survey found

The master plan's phase 4 sketch is **stale in three of its
five factual claims**, and the tree has grown a new failing
check since it was written. All corrections below are also made
at source, in the master plan's phase 4 sketch and in the
`docs/plans/index.md` row, as part of this planning commit.

**1. The backlog is 77 files, not 70.** `tools/review-tracking.sh
status` today: *115 of 192 in-scope files carry a valid review at
HEAD; 77 need review*. The sketch's 70 was measured on
2026-08-29, before phases 2 and 3 and the renovate merges landed
and before `prune-reviews` ran against them.

**2. Issue #227's body is badly stale, and worse than #370's.**
It reads *"0 of 152 in-scope files reviewed at HEAD; 152 need
review"* and lists files that no longer exist -- `.github/
workflows/pr-address-comments.yml`, deleted in phase 2, and
`alembic/env.py`, which moved under `kerbside/migrations/` before
that. It also names `.claude/skills/add-database-migration.md`,
which is now a directory with a `SKILL.md` inside. The audit
files an issue and never refreshes its body, so an open
consistency issue's body is a historical record of the day it was
filed. This is the second instance of the same pathology; #370
has it too. Read the numbers from `tools/review-tracking.sh
status`, never from the issue.

**3. "The remaining bulk is protocol documentation under
`docs/spice/`" is wrong.** `docs/spice/` is 9 of the 77. The
actual distribution is `kerbside/` 25 (15 of them unit tests),
`tools/` 17, `docs/` 17 (9 under `spice/`), 7 repository-root
files including `AGENTS.md`, `ARCHITECTURE.md`, `README.md`,
`PLAN-TEMPLATE.md` and `PUSH-AUDIT.md`, and the remainder spread
across `.claude/`, `.github/workflows/`, `demo/`,
`tempest-plugin/`, `loadtests/` and `tests/`. The sketch's
conclusion -- that the bulk is low-yield reading -- does not
follow from a corrected distribution.

**4. No review mark this repository has ever recorded was
signed.** `git log --format='%h %G? %s' -- REVIEWS.md` returns
`N` for every commit, including the four that added marks:
`742dd06` *reviews: CI workflows and tools*, `08422d6` *review:
test tooling*, `7b67f12` *review: tempest plugin*, `bc30959`
*review: rust proxy churn*. `N` is "no signature", not "cannot
verify" -- the merge commits alongside them show `E` for
GitHub's key, which is the "cannot verify" case, so the two are
distinguishable and this is the former.

The cause is that nothing is configured: `git config --get
gpg.format`, `gpg.x509.program` and `commit.gpgsign` are all
empty in both local and global scope, though `gitsign` is
installed at `/usr/bin/gitsign`. `.claude/CLAUDE.md` and
`AGENTS.md` (line 191) both document the configuration as a
prerequisite; nobody ran it. The sketch called signing "a
prerequisite the phase must check before starting", which is
right, but it assumed the check would pass. The consequence is
larger than a config step: the attestation the whole scheme
rests on is absent for the 115 files that already count as
reviewed.

**5. A new audit check fails, with no issue filed.**
`review-scope-completeness` landed upstream in `d3244d6` on
2026-08-30 19:23 UTC and reports *44 tracked file(s) are out of
review scope only because no include pattern in
`.vscode/review-scope.toml` names them*. No `Consistency: Human
review scope completeness` issue exists against kerbside, open or
closed; the daily run at 06:00 UTC on 2026-08-31 either predated
the check reaching the audit's repository list or has not yet
filed. Either way the failure is real and reproducible locally,
and waiting for the issue to appear before acting on it would be
superstition.

**6. The audit is now 45 checks, and kerbside fails four.** Run
locally against `develop` at `bee839c`: `mermaid-lint-ci` and
`push-audit` (both phase 5), `review-coverage` and
`review-scope-completeness` (both this phase). `llm-context-lint-ci`
**passes**, confirming phase 3 landed; `llm-context-lint` reports
`not_applicable` because skillsaw is not installed in the audit
environment, which is an environment property and not a kerbside
one.

**7. The four files the sketch nominates for front-loading all
exist and are all genuinely unreviewed** -- `kerbside/api.py`
(875 lines), `kerbside/proxy_supervisor.py`, `kerbside/sf_token.py`
and `kerbside/sources/ovirt.py`. That claim survived. So did the
sketch's central framing: this is a human grind, not an
implementation step.

**The size of the grind, measured rather than estimated.** The 77
unreviewed files total 19,948 lines. The 35 files decision 1
brings into scope add roughly 3,000 more, most of it small
configuration and eight Jinja templates. Call it 23,000 lines of
whole-file reading to get from 112 files needing review to fewer
than 5.

## Decisions

**1. Settle the scope before starting the grind, in this phase.**
`review-scope-completeness` gets folded into phase 4 rather than
becoming a phase of its own. The two checks are coupled by
design -- the upstream spec says so directly: *"narrowing
`include` is the cheapest way to make a review-coverage issue
close, and until this check existed nothing noticed a repository
that reached full coverage by shrinking what counted."* Doing the
grind first would mean reading 77 files, closing #227, then
adding 35 files to scope and reopening it. Doing scope first
costs one session and fixes the order.

This is the decision most likely to be argued with, because it
widens the phase past the issue named in the master plan and
makes the coverage number **worse before it gets better**: 77
files needing review becomes 112, and 192 in scope becomes 227.
Both numbers are the honest ones. The alternative is a smaller
number that means less.

**2. Enumerate the file types; do not set `include = []`.** The
spec offers `include = []` -- every tracked file, lean entirely
on `exclude` -- and calls it reasonable for a small repository.
Kerbside is not one: 316 tracked files, a vendored `sfui` tree,
generated protobuf stubs, exported GitHub configuration, two
qcow2 fixtures and a `Cargo.lock`. The exclude list needed to
make `include = []` behave would be longer than the include list
it replaced, and it would give up the property the new check
exists to provide -- that an unfamiliar file type fails loudly
rather than joining the queue in silence.

The candidate configuration, verified against the tree before
this plan was written, adds `*.html`, `*.toml`, `*.ini`, `*.json`,
`*.conf`, `*.cfg`, `*.mako`, `*.svg`, `*.txt`, `*Dockerfile`,
`Makefile` and `*/Makefile` to `include`, names the three
extensionless files (`etc/kerbside.conf.example`,
`demo/kerbside-demo-env`, `tools/run-tempest-tests`) and the
ignore files, and adds five `exclude` entries with reasons:
`.github/exported-config/*` (written by a workflow),
`rust/kerbside-proxy/Cargo.lock` (generated), `docs/schema.html`
(generated), `tests/fixtures/*.qcow2` (binary), and `AUTHORS` and
`LICENSE` (not authored here). That takes orphans to **0**,
in-scope from 192 to **227**, and drops **nothing** that is in
scope today.

The `tests/fixtures/*.qcow2` spelling is deliberate. The obvious
`tests/fixtures/*` also swallows `tests/fixtures/README.md`,
which is prose somebody wrote and should be read.

**3. The Jinja templates are in scope, and they are not
low-value filler.** Eight files under `kerbside/api/templates/`
enter scope under `*.html`. Four of the repository's open
security issues concern the web surface those templates render:
#319 (CSRF on a GET that mints a token), #132 (cleartext backend
credentials disclosed by an endpoint), #134 (`/console/direct`
leaking a hypervisor ticket) and #131 (forgeable JWT). Autoescape
behaviour and any `|safe` filter live in these files and nowhere
else. They are read in tranche 2, not left to the end.

**4. Configure signing now; do not retroactively sign the
existing marks.** Every mark from this phase forward is signed,
which is a `git config` away and is done in step 4b. The 115
existing marks stay as they are. Re-signing them would produce a
signature attesting that somebody read a file at a content
version, when what actually happened is that somebody read it and
the attestation was never captured -- and there is no way now to
tell the two apart from the outside. Re-reading 115 files to
attest them properly is not worth it against re-reading them on
their next natural staleness.

The gap is worth recording upstream rather than only here: the
`review-coverage` audit runs `review-tracking.py status`, which
reads the sidecar and never looks at whether the commit carrying
a mark was signed. A repository can therefore pass the audit
with no attestation at all, which is what kerbside has been
doing. Raising that with `shakenfist/development` is future
work, noted in the master plan, not this phase's job.

**6. Signing configuration belongs in the review account's clone,
not in a development clone.** Review sessions run under a separate
account, so that turning on commit signing does not mean signing
every ordinary development commit. That makes the split structural
rather than a matter of remembering, and it matches the upstream
workflow's note that the dedicated review account's clones never
carry development edits.

This phase got it wrong on the way through. Step 4b originally set
`gpg.format`, `gpg.x509.program`, `commit.gpgsign` and `tag.gpgsign`
in this clone, on the reading that `.claude/CLAUDE.md`'s "signing is
per-clone config, so a fresh clone needs..." applied to whichever
clone was to hand. It applies to the clone that makes review marks.
The settings were reverted; a development clone should have none of
them.

The survey's finding stands unchanged and is worth restating so it is
not read as a consequence of this mix-up: every mark-adding commit on
develop is unsigned (`742dd06`, `08422d6`, `7b67f12`, `bc30959`), so
the 115 files that already count as reviewed carry no attestation.
That is a fact about the commits on the branch, whatever any clone's
configuration says, and it is why the Definition of done asks for
confirmation against a real commit rather than against a config file.

**5. No agent pre-reads a file and hands the human a summary to
mark against.** The review mark asserts that a person read the
file. An agent-written briefing note, however good, becomes the
thing that gets read, and the mark then attests to the summary.
The upstream workflow's line about "Claude Code in the integrated
terminal for questions" is the supported use and the boundary:
answering a question about a file the human is reading is fine;
producing the reading is not.

This is the second decision a reader might argue with, since it
declines the one available speed-up on a 23,000-line grind. The
answer is that the speed-up would empty the marks of meaning,
and the marks are the entire product.

## Step plan

The agent-executable work is steps 4a to 4c. The reviewing
itself is not a sub-agent step -- marks come from the weAudit
VSCode extension, driven by a person -- so it is sequenced below
the table under *The review sessions*.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | Rewrite `.vscode/review-scope.toml` in the phase 4 worktree so `./tools/review-tracking.sh scope-orphans` reports zero orphans, and **stop before committing** -- this needs Michael's ratification (gate 1). The 44 orphans are listed by that command; run it first. Apply decision 2's candidate list: add `*.html`, `*.toml`, `*.ini`, `*.json`, `*.conf`, `*.cfg`, `*.mako`, `*.svg`, `*.txt`, `*Dockerfile`, `Makefile`, `*/Makefile`, `.dockerignore`, `.gitignore`, `*.gitignore` to `include`, plus the three extensionless paths `etc/kerbside.conf.example`, `demo/kerbside-demo-env` and `tools/run-tempest-tests`. Add to `exclude`, each with a one-line reason in the comment block above: `.github/exported-config/*`, `rust/kerbside-proxy/Cargo.lock`, `docs/schema.html`, `tests/fixtures/*.qcow2`, `AUTHORS`, `LICENSE`. Use `tests/fixtures/*.qcow2` and not `tests/fixtures/*`, which would also drop `tests/fixtures/README.md`. Follow the file's existing style: a prose comment block explaining the reasoning, then the two lists. Verify with `scope-orphans` (expect the "every tracked file is either in scope or explicitly excluded" line) and with `status`, and report both numbers: expect **227 in scope, 112 needing review**. If either number differs, report it rather than adjusting patterns until it matches. *Outcome: 227 in scope as predicted, but 104 needing review rather than 112 -- see the note below the tranche table.* |
| 4b | low | sonnet | none | **Nothing to do in this clone.** See decision 6: review sessions run under a separate account in a separate clone, and the signing configuration belongs there. Do not set `commit.gpgsign` in a development clone. The step remains in the table because the phase must still confirm, before tranche 1, that the review clone signs -- `git log --format='%h %G? %s' -1` after the first mark-adding commit, where `N` means it landed with no attestation. `gitsign` authenticates through an interactive Sigstore OIDC browser flow, so run `gitsign-credential-cache &` once in that clone rather than authenticating per commit. If the flow cannot complete, stop -- do not disable signing to get a mark through, because an unsigned mark is indistinguishable later from the 115 already in history. |
| 4c | medium | sonnet | none | Add the per-session recipe to `docs/development.md`, in the review tracking section that already documents `tools/review-tracking.sh`. It must cover: pull on a clean tree, then `./tools/review-tracking.sh prune`; pick files from the current tranche rather than from `next` (which chooses at random and would scatter the order this plan sets); read and mark in weAudit; then `./tools/review-tracking.sh stamp`, `git add .vscode/*.weaudit* REVIEWS.md`, and a **signed** commit. Give the one-liner that lists a tranche's outstanding files, so a session does not re-derive it: `./tools/review-tracking.sh status \| grep 'never reviewed' \| sed 's/.*: //' \| grep '^kerbside/'` with the prefix swapped per tranche. State that the commit must be signed and how to check (`git log --format='%h %G? %s' -1`; `N` means it landed unsigned). Cross-link the upstream workflow doc rather than restating it -- `docs/code-review-tracking.md` in shakenfist/development is the authority. Keep to the repository's 80-column wrap. |

Each step is its own commit:

- 4a: `Name every tracked file in review scope.`
- 4b: no commit, and no change in this clone
- 4c: `Document the review session recipe.`

## The review sessions

Six tranches, ordered so the sessions most likely to find
something come first. Counts are from the survey and assume 4a
has landed; each session re-derives its own list with the
one-liner from step 4c, because the numbers move as marks land
and as `prune-reviews` runs.

| # | Tranche | Files | Why here |
|---|---------|-------|----------|
| 1 | Application code: `kerbside/*.py`, `kerbside/sources/`, `kerbside/rpc/` | 7 | `api.py` (875 lines), `main.py` (530), `proxy_supervisor.py`, `sf_token.py`, `sources/ovirt.py`, `sources/static.py`, `rpc/contract.py`. Every open security issue against this repository points somewhere in here. Highest yield per line, and the sketch already nominated four of the seven. |
| 2 | The web surface: `kerbside/api/templates/`, `kerbside/rpc/kerbside.proto` | 12 | New to scope under decision 3. Autoescape and `\|safe` live here; #319, #132 and #134 are all rendered by these files. Read immediately after the code that renders them, while it is fresh. |
| 3 | Configuration that is executable in practice | ~25 | The rest of what 4a brings in, plus `.pre-commit-config.yaml` and the two remaining workflows. `.gitleaks.toml` first: it decides what the credential scan catches, so an over-broad allow rule there is a hole that hides holes. |
| 4 | `tools/` | 17 | Shell and Python that CI executes, several with repository write access. `gitleaks-scan.sh`, `flake8wrap.sh` and `shellcheck-wrap.sh` gate other checks; a bug in one of those is a silently disabled check, which is exactly what phase 2 found in `flake8wrap.sh`. |
| 5 | Prose: repository root, `docs/`, `.claude/` | ~27 | `AGENTS.md`, `ARCHITECTURE.md`, `README.md`, `PLAN-TEMPLATE.md`, `PUSH-AUDIT.md`, `RELEASE-SETUP.md`, the `docs/` guides and the skills. Drift here is real but it is not exploitable, and reading it after the code means the reader can tell when a document has gone stale. |
| 6 | `docs/spice/` and the unit tests | 24 | 9 protocol documents (`channel-protocols.md` alone is 952 lines) and 15 test modules. The protocol documents have never been read end to end and that is worth fixing, but they describe an external protocol rather than this repository's decisions. The tests are read constantly in the course of other work. Last, honestly. |

**The backlog came out at 104, not the 112 this plan predicted, and
the eight are already read.** The projection assumed every file
entering scope was unreviewed. Eight were not: `demo/Dockerfile`,
`demo/kerbside.ini`, `demo/kerbside-demo-env`,
`demo/spice-target/Dockerfile`, `etc/kerbside.conf.example`,
`loadtests/latency/Dockerfile`, `rust/kerbside-proxy/Dockerfile` and
`tools/run-tempest-tests` all carry stamps dated 2026-08-06, 08-08 or
08-18. They were read and marked in weAudit during earlier sessions
*while out of scope*, the stamps persisted in the sidecar, and
widening `include` made those reviews count. Tranche 3 is smaller by
seven and tranche 4 by one as a result.

This is worth knowing beyond the arithmetic: weAudit will mark any
file, in scope or not, and the sidecar keeps the stamp. So a reviewer
who reads something the scope config does not cover is not wasting
the effort -- it banks against the day the file comes into scope.

The backlog is still above the threshold at the end of tranche 5,
with the 24 files of tranche 6 outstanding, so tranche 6 is what
closes #227 -- there is no short cut that skips it. At a realistic 800 to 1,200 lines of
genuine whole-file reading per hour, 23,000 lines is **eight to
twelve two-hour sessions**. That is the number to plan against;
a phase that pretends it is three will stall and look stuck.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Scope is widened to satisfy `review-scope-completeness` and the review-coverage number gets worse, so the next reader thinks phase 4 went backwards. | Stated in decision 1 and carried into the master plan and the index row as part of the planning commit, with both before and after numbers. The Definition of done names 227 in scope and 112 needing review as the *expected* post-4a state, so hitting it reads as success rather than regression. |
| The grind stalls after two sessions and the phase sits `In progress` for months with no way to tell progress from abandonment. | The tranche table is the progress meter: `status` filtered by tranche prefix says exactly where the phase is, and step 4c writes that one-liner into `docs/development.md` so any session can answer the question in one command. A stalled phase is a legitimate outcome to report; an unmeasurable one is not. |
| A session marks files reviewed on a tree with uncommitted edits, so the mark attests to content that was never committed. | The upstream workflow's first rule, restated in 4c: mark only on a clean tree. `prune` catches it after the fact -- the blob SHA will not match at HEAD -- but after the fact means the reading has to be redone. |
| gitsign cannot complete its browser flow in a headless or remote session, and signing gets quietly turned off to unblock a commit. | 4b makes this a stop-and-report condition rather than a judgement call, but only for the commits it applies to. An unsigned mark is worse than a delayed one: it is indistinguishable, later, from the 115 already in history. |
| Signing configuration is set in a development clone, where `commit.gpgsign true` makes git attempt to sign every ordinary commit and ordinary work blocks on a Sigstore login for no benefit. | Decision 6. The configuration belongs in the review account's clone only. This phase set it here during implementation and reverted it; the four settings are unset in both local and global scope, which is the state a development clone should be in. |
| An agent is asked to "help with" a review session and ends up producing the reading the mark attests to. | Decision 5, and the step table stops at 4c for exactly this reason. There is no sub-agent step in this phase that reads a file under review. |
| `review-scope-completeness` files an issue against kerbside between now and 4a landing, and it looks like the phase missed it. | Expected, not a problem. The check fails today and the issue is overdue; when it appears it is the same finding this phase is already fixing, and it closes on the audit run after 4a merges. |

## Definition of done

- [ ] `./tools/review-tracking.sh scope-orphans` prints *every
      tracked file is either in scope or explicitly excluded* and
      exits zero.
- [x] `./tools/review-tracking.sh status` reports **227 in-scope
      files** immediately after 4a. The backlog came out at **104**,
      not the predicted 112, because eight of the files entering
      scope already carried stamps from earlier sessions; the
      discrepancy is explained under the tranche table rather than
      tuned away.
- [ ] Every `exclude` entry added by 4a has a reason in the
      comment block that says what the file is, not merely that
      it is excluded.
- [ ] `tests/fixtures/README.md` is still in scope after 4a.
- [ ] The **review account's clone** signs, confirmed against the
      first mark-adding commit rather than against its config. No
      signing configuration is set in a development clone; the 4a and
      4c commits are correctly unsigned, adding no review mark.
- [ ] `docs/development.md` gives a session recipe that a reader
      can follow without opening the upstream document, and the
      tranche one-liner in it runs and produces file paths.
- [ ] `pre-commit run --all-files` is clean.
- [ ] The audit's `review-scope-completeness` check passes for
      kerbside.
- [ ] `review-coverage` reports fewer than 5 files needing review
      and issue #227 closes from a passing audit run.

The last two are on different clocks. `review-scope-completeness`
passes as soon as 4a merges. `review-coverage` needs the tranches
done, so this phase stays `In progress` across many sessions by
design -- which is what the phase is, not a sign it is stuck.

## Back brief

The thing to notice about this phase is that its measured
numbers get worse first. 4a takes the backlog from 77 to 112 and
that is the correct outcome; a phase report that leads with "112
files need review" without leading with why is going to read as
a failure.

Two gates:

**Gate 1, before 4a is committed.** The scope config decides what
is subject to human review for the life of the repository, and
the argument for including a file type is easier to make now than
to revisit later. Show the proposed `include` and `exclude` lists
with the reason for each addition and the resulting numbers, and
wait. In particular the three that are genuine judgement calls
rather than bookkeeping: the Jinja templates and SVG icons
(in, decision 3), the ignore files (`.dockerignore` decides what
ships in an image, so in), and `AUTHORS`/`LICENSE` (out, not
authored here).

**Gate 2, after the first review session.** One session against
tranche 1 is enough to calibrate the 800-1,200 lines per hour
estimate the tranche table is built on. If the real rate is half
that, the plan is a twenty-session plan and the tranche
boundaries should move before another ten sessions are spent
against the wrong shape. Report the actual rate, not a
reassurance.
