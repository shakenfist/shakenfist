# Advisory ignore review

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly. Read the relevant
files and ground your answers in what they actually say. Do not
speculate about the repository when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

There is no application code here. The artifacts are the audit
specifications in `docs/audits/`, the tooling in `scripts/` that
measures them, the templates in `templates/` that the rest of the
fleet copies, and the workflows that run all of it every week
against every Shaken Fist repository.

Consult `AGENTS.md` for the conventions and the invariants that
are not visible in the code, and `ARCHITECTURE.md` for the shape
of the system. `docs/consistency-audits.md` is the reference for
what a run does, how to add a criterion, how to bring a
repository into scope, and how to test a change before it reaches
the fleet -- read it before changing anything under `scripts/` or
`docs/audits/`. `docs/code-review-tracking.md` covers the review
tooling, and `PUSH-AUDIT.md` is the pre-push review runbook that
every plan's final phase runs.

Two things make planning here different from planning in a
repository that holds a product, and both should shape any plan
written from this template:

* **The blast radius is other people's repositories.** The weekly
  workflow files and closes GitHub issues fleet-wide. A change
  that is merely wrong does not produce a red build; it produces
  issues in ten repositories, or silently closes ones that should
  have stayed open. Always pass `--dry-run` when running
  `audit-manage-issues.py` by hand.
* **This repository is in its own audit matrix.** A standard we
  exempt ourselves from is a standard we stop noticing the cost
  of. A change to a criterion is a change we are measured against
  on the next run, so a plan should say how many repositories --
  including this one -- it newly fails.

<!-- shared-block: plan-file-conventions v2 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section. `Merged` is last, and the push audit is the last row,
  for the reasons given in `plan-push-audit-phase`:

  | Phase | Plan | Status | Merged |
  |-------|------|--------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started | |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started | |
  | 3. Push audit | - | Not started | |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

**In this repository.** Plans here keep their phases as sections
inside the master plan rather than as separate phase files, and
the Execution table's `Plan` column is dropped accordingly;
`docs/plans/index.md` says so. The shared convention above is the
fleet default, and a plan large enough to want phase files should
use them rather than argue with the block.

## Situation

Three repositories in the audit matrix (the `matrix.repo` list in
`.github/workflows/consistency-audit.yml`; `images` and
`private-ci` are narrowed to a few checks by `REPO_OVERRIDES` and
would not run this one) scan their Rust dependencies against the
RustSec advisory database: ryll, andris and teneral. ryll and
andris run `cargo deny` (policy in `deny.toml`) and `cargo audit`
(policy in `.cargo/audit.toml`) on every pull request, and both
keep an `[advisories].ignore` list in each file. teneral, which
joined the matrix on 2026-11-27, runs `cargo deny` alone, has an
empty ignore list, and does not set `unused-ignored-advisory`.
uncalibrated-sextant and visual-digest-rust carry a `Cargo.lock`
but neither scanner. No repository in the matrix ignores a Python
or npm advisory. The matrix count is not stated here because it
moves: it went from 24 to 25 while this plan was in review.

Nothing gives an ignore an end date, and nothing asks whether one
still does anything. On 2026-11-25 every ignore was removed from
both files in ryll and andris and the scanners were run again
(cargo-deny 0.22.1, cargo-audit 0.23.0, in a `rust:latest`
container):

| Repository | Ignores | Needed by `cargo deny` | Needed by `cargo audit` | Suppressing nothing |
|------------|---------|------------------------|-------------------------|---------------------|
| ryll | 10 | RUSTSEC-2023-0071, RUSTSEC-2025-0134 | RUSTSEC-2023-0071 | 8 |
| andris | 2 | RUSTSEC-2023-0071 | RUSTSEC-2023-0071 | 1 (RUSTSEC-2025-0134) |

Nine of the fleet's twelve ignores suppressed nothing. Every one
of the nine is an unmaintained or unsound advisory. cargo-deny
0.22 checks those advisory kinds only for the workspace's direct
dependencies unless `unmaintained` and `unsound` are widened, and
`cargo audit` exits non-zero only for vulnerabilities, printing
everything else as a warning. So an ignore for an unmaintained
transitive crate suppresses nothing in either scanner, while
reading as a gate that holds something back. cargo-deny already
reports every such ignore as `advisory-not-detected`, but only as
a warning in a job that passes, so nobody reads it. ryll has
reaped its stale ignores by hand three times: one in July 2026,
four in September and eight in November (shakenfist/ryll#805),
each found by someone happening to look.

The ignores that do suppress something have no review clock
either. ryll and andris have ignored RUSTSEC-2023-0071 (the `rsa`
Marvin attack) since their scanners landed, and nothing asks
whether a fix has shipped since.

cargo-deny 0.22 already has the mechanisms. Read from the 0.22.1
source (`src/advisories/cfg.rs`, `src/advisories/diags.rs`,
`src/advisories.rs`, `src/cargo-deny/check.rs`) and its changelog;
the file and line for each claim are recorded so that a later
reader can tell a renamed key from a mis-specified rule:

- An ignore entry may be a table, `{ id = "...", reason = "...",
  expiry = "..." }` (`IgnoreId`, `cfg.rs:187-224`). `expiry` is
  either an absolute date, `YYYY-MM-DD`, or an ISO 8601 duration
  starting `P` (`parse_expiry`, `cfg.rs:149`). Once the run date is
  past it, the ignore no longer applies and the advisory fails the
  run at deny level, as `advisory-ignore-expired`
  (`diags.rs:194-216`).
- The comparison is `date > expiry` (`diags.rs:200`), so an
  absolute expiry is valid through that day, and the run date is
  the current date in UTC, not the runner's local time
  (`check.rs:591-593`).
- A duration is measured from the advisory's *publication* date
  (`md.date.checked_add(dur)`, `diags.rs:197`), and so is the
  global `ignore-expiry` default. For an advisory published in
  2023, any sensible duration has already run out, so neither works
  as a review clock. The absolute date does.
- `unused-ignored-advisory` (`cfg.rs:438`) sets the level of the
  `advisory-not-detected` diagnostic, and of `yanked-not-detected`
  for a yanked-crate ignore (`advisories.rs:222-240`). It defaults
  to `warn`; `deny` fails the run on any ignore that suppresses
  nothing.
- The same `ignore` list also suppresses yanked crates. An entry
  is an advisory when it is a string that contains a `-` and whose
  text before the first `-` is all uppercase ASCII -- so `"FOO"` is a
  package spec, `"A1-x"` is one too, and `"-x"` is an advisory,
  because an empty prefix passes vacuously -- or a table with an
  `id` key;
  anything else is parsed as a package spec, a bare
  `"crate@version"` or `{ crate = "...", reason = "..." }`, and
  goes to a separate yanked list (`cfg.rs:349-415`). A package spec
  cannot carry an `expiry`.
- `unmaintained` and `unsound` are both scopes (`cfg.rs:259-262`),
  defaulting to `workspace`.
- Per-entry `expiry` arrived in 0.21.0 (EmbarkStudios/cargo-deny#904)
  and `unused-ignored-advisory` in 0.19.0
  (EmbarkStudios/cargo-deny#823). An older cargo-deny does not
  silently ignore an `expiry` key: 0.20.2's `IgnoreId` calls
  `finalize` on the table, which rejects unknown keys, so the
  configuration fails to load and the job goes red.
- `EmbarkStudios/cargo-deny-action@v2`, the action ryll's and
  andris's CI uses, runs cargo-deny 0.22.1 at the time of
  writing. Both call it with `command: check`, which runs every
  check including advisories, from the `cargo-deny` job in
  `ci.yml` (skipped in the merge queue and on pull requests that
  change no code) and from the weekly `supply-chain.yml` cron.
  teneral does not use the action: its `Dockerfile` runs an
  unpinned `cargo install --locked cargo-deny` when the build image
  is made, and `make deny` runs `cargo deny --locked check all`
  from `supply-chain.yml`, on a weekly cron and on pull requests.
  That job is deliberately in no `needs:` list, so in teneral an
  expired or dead ignore reddens a job that blocks no merge.
- Without `--config`, cargo-deny uses the first of `deny.toml`,
  `.deny.toml`, `.cargo/deny.toml` and `.config/deny.toml` it
  finds, nearest directory first, walking up from the manifest
  (`default_config_path`, `src/cargo-deny/common.rs:168-174`). All
  three repositories keep `deny.toml` at the root, and none passes
  `--config`.

cargo-audit 0.23 has no expiry on its ignores.

The fleet's other ignore files, `.gitleaksignore` (four
repositories) and `.gitleaks.toml` allowlists (six), record
fingerprints of false positives. A false positive does not go
stale the way an accepted advisory does, so they are out of scope.

## Mission and problem statement

Make every RustSec ignore in the fleet either do something and be
on a review clock, or be gone. Enforcement is split along what
each layer can see:

- **Each repository's own CI** enforces the clock and the
  liveness, because only it has the toolchain and the advisory
  database. That is cargo-deny's per-entry `expiry`, which fails
  the run once the date passes, and `unused-ignored-advisory =
  "deny"`, which fails it on an ignore that suppresses nothing.
- **The weekly consistency audit** makes sure that configuration
  is in place, and warns before an expiry breaks CI. That is a new
  criterion, `advisory-ignore-review`, which applies to any
  repository that tracks a cargo-deny configuration or a
  `.cargo/audit.toml`. The cargo-deny configuration is the first of
  `deny.toml`, `.deny.toml`, `.cargo/deny.toml` and
  `.config/deny.toml`, relative to the repository root, that is
  tracked -- cargo-deny's own search order (see *Situation*) -- so
  that the cheapest rename cannot move a repository out of scope;
  below, `deny.toml` means whichever of those it is, and findings
  name the actual path. Rules 1 to 4 read `deny.toml` alone; rule 5 is the only
  one that reads `.cargo/audit.toml`, whose ignores are bare ID
  strings with no `expiry` and so could never satisfy rule 2. A
  repository with `.cargo/audit.toml` and no `deny.toml` is measured
  by rule 5 alone: it has no cargo-deny configuration to carry rule
  1's key, so rule 1 does not apply to it, and with an empty ignore
  list it passes, since nothing is on no clock. The criterion fails
  when:
  1. `deny.toml` has no `[advisories]` table, or that table does not
     set `unused-ignored-advisory = "deny"`;
  2. an advisory entry in `deny.toml`'s `[advisories].ignore` is not a
     table with an `id`, a non-empty `reason`, and an `expiry` written
     as an absolute date rather than a duration; or a yanked-crate entry
     is not a table with a non-empty `reason`. The check tells the two
     apart the way cargo-deny does (see *Situation*). A yanked-crate
     entry is exempt from the `expiry` half of this rule and from rules
     3 and 4, because cargo-deny cannot attach an expiry to one;
     `unused-ignored-advisory = "deny"` still fails it once the yanked
     version leaves the lockfile, so it has liveness without a clock;
  3. an `expiry` is more than 180 days after the run date;
  4. an `expiry` is 14 days or fewer after the run date, or already past
     -- review is due, and the issue names each ID and its absolute
     date, never a count of days remaining: the weekly run rewrites the
     issue body only when it differs, so a countdown would notify every
     watcher every week while a date stays put until the verdict moves.
     Past means `expiry` is before the run date, which is today's date
     in UTC: the same inclusive comparison and the same clock as
     cargo-deny, so the audit never calls an entry expired on a day CI
     still honours it. The finding says which of the two it is,
     because they mean different things: a due entry ("review due for
     RUSTSEC-2023-0071 by 2027-03-01") leaves CI green, while a past
     one ("review overdue for RUSTSEC-2023-0071, expired 2027-03-01")
     means cargo-deny is already failing every run that reaches it;
  5. an ID in `.cargo/audit.toml`'s ignore list, where that file
     exists, is not also in `deny.toml`'s -- including when there
     is no `deny.toml` at all. cargo-audit cannot carry a date, so
     its ignores ride on the clock of the matching cargo-deny
     entry, and a repository with only `.cargo/audit.toml` has
     ignores on no clock. The reverse is not required: `cargo
     audit` fails on fewer advisories than `cargo deny` does, so
     its list is legitimately shorter (shakenfist/ryll#805 made it
     so).

Rules 3 and 4 make the verdict a function of the run date: an
ignore reviewed to 90 days out passes for 76 days and then fails,
on a repository nobody touched. That is the mechanism, not a side
effect -- the issue is the review prompt, and the weekly run
rewrites its body, so the IDs and dates stay current. It is not
new: the distribution end-of-life checks in
`scripts/audit/checks/distros.py` already move with the calendar.

The criterion assumes what it cannot see: that the repository's CI
runs cargo-deny's advisories check, at 0.21.0 or later. All three
repositories in scope do (see *Situation*), and running a version
older than 0.21.0 fails closed rather than open, because such a
binary rejects the `expiry` key. What would pass unnoticed is a
repository that keeps a `deny.toml` but stops running the
advisories check, or one whose CI points cargo-deny somewhere the
search above does not reach -- `--config` to another path, or a
workspace below the repository root with its configuration beside
it. The second is quieter than the first: if no configuration is
left at any of the four paths, the repository becomes
`not_applicable` and its open issue closes, which reads as
compliance rather than as absence. Checking workflows for a scanner invocation is a
different criterion, with different applicability, and it is
already in *Future work* as "whether every Rust repository should
run cargo-deny"; step 1d records the invocation in each repository
in the meantime, and the specification states the assumption.

The specification sets conventions the check does not try to
enforce, because it cannot see an entry's history. A new or
reviewed ignore expires 90 days out by default, and up to 180 days
for an advisory where no upstream movement is expected (an
archived crate with no successor). Reviewing an ignore is a pull
request that moves the date and rewrites `reason` to say what was
checked ("dav-server still at 0.12.0, which requires `^0.16.3`").
The first dates written are staggered, so that a repository's
ignores do not all come due in the same week.

With this criterion, ryll and andris newly fail, because neither
sets the lint level or any expiry, and teneral newly fails rule 1
alone. This repository is not applicable, because it has no
configuration for either scanner.

Rules 3 and 4 mean a repository on a healthy clock spends about
two weeks in every ninety showing `fail` on
`docs/audits/compliance.md`, beside repositories that never set
the lint level. That periodic red is the expected steady state,
not a regression, and the specification says so. The `fail()`
detail is what tells the two apart, so it leads with which rules
fired: "review due for 1 ignore" reads differently from
"deny.toml does not set unused-ignored-advisory", and both read
differently from "review overdue for 1 ignore", which is the one
state in which the repository's own CI is already red.

Not covered: the gitleaks files above; advisory ignores for other
ecosystems, since none exist yet (see *Future work*); whether
every Rust repository should run cargo-deny at all; and paying
down the debt the ignores record, which stays with each
repository's own plans.

## Open questions

1. **An expiry breaks every pull request in that repository that
   changes code, and the weekly scanner run, from the day after
   it.** This is the same failure mode as a newly
   published advisory, which is exactly what happened to ryll on
   2026-11-24 with RUSTSEC-2026-0596. By default the plan accepts
   that: the criterion's 14-day warning means at least two weekly
   audit runs file an issue first, and a hard stop is what
   "insists" means. The alternative is to put the date in
   `reason` only and enforce it in the audit alone, which never
   breaks CI but also never stops a repository that ignores the
   issue.
2. **`cargo audit` duplicates `cargo deny`'s advisory check from
   the same database, and its ignores cannot expire.** By default
   both scanners stay and rule 5 ties them together. Dropping
   `cargo audit` from ryll's and andris's CI would remove a file
   and a rule; that is a separate decision, recorded in *Future
   work*.
3. **The warning window.** Default 14 days: two weekly runs.
4. **`unused-ignored-advisory = "deny"` makes a dependency bump
   break its own pull request.** When a bump fixes an advisory,
   the ignore for it suppresses nothing, and the bump's pull
   request fails `cargo deny`. That pull request is usually
   Renovate's, which cannot edit `deny.toml`, so a human adds a
   commit removing the ignore before it can land. By default the
   plan accepts that: the commit is the same reap ryll has done by
   hand three times, made at the moment it is due instead of
   months later, and it is a one-line edit the failure names. The
   alternative is to leave the lint at `warn`, which is today's
   behaviour, and the audit cannot make up for it: telling a live
   ignore from a dead one needs the lockfile resolved against the
   advisory database, which the audit does not have. The weekly
   `supply-chain.yml` cron has the same exposure with no pull
   request attached: an advisory withdrawn upstream, or a yanked
   version republished, turns a live ignore dead and fails a
   scheduled run on a day nobody touched the repository. GitHub
   sends that failure to whoever last edited the cron line, and
   the `scheduled-workflow-health` criterion will not report it,
   since it only fails a workflow that has never succeeded; the
   next pull request that runs `cargo deny` fails the same way,
   and that is where a human reliably sees it. Phase 2's pull
   requests should say both in each repository's scanner policy,
   so the first red Renovate pull request, or the first red
   Monday, is not a surprise.

## Execution

The phases are sections below.

| Phase | Status | Merged |
|-------|--------|--------|
| 1. The criterion and its specification | Not started | |
| 2. Adopt in ryll, andris and teneral | Not started | see section |
| 3. Push audit | Not started | |

<!-- shared-block: plan-status-vocabulary v1 -->
Plan status vocabulary (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`):

A status cell -- in the master plan's own Execution phase table, and
in the row `docs/plans/index.md` carries for the plan -- holds
exactly one of these terms and nothing else:

- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something outside the plan
  changes. Say what, in the plan.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan, which the plan names.

The term is the whole cell. No dates, no phase arithmetic, no
parenthetical qualifiers, no summary of what happened: a status is
read to decide whether a plan still wants attention, and prose in
that column has repeatedly grown until it could no longer be read
either by a person scanning the table or by tooling. Detail belongs
in the plan file, and a one-line summary belongs in the index's own
Intent column.

Matching is case-insensitive, so `In Progress` is accepted, but the
spelling above is the one to write.
<!-- shared-block-end -->

**In this repository.** The same term is written twice: once in
this plan's own phase table, and once in the row the plan carries
in `docs/plans/index.md`. The index row is the whole-plan status,
so it only reaches `Complete` once every phase has been
completed, abandoned or superseded. The `plan-index` criterion
reads that table, and this repository is inside its own audit
matrix, so a status that drifts out of the vocabulary fails our
own tooling before it fails anybody else's.

<!-- shared-block: plan-push-audit-phase v4 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did. What is done instead is at
  least the checklist below, applied to the recorded ranges: it is
  a floor, not a substitute for a runbook, and a plan adds the
  questions its own repository's blast radius raises. Each item
  gets a verdict, or a sentence saying why it does not apply.
  Do not borrow another repository's runbook instead: a runbook is
  written for one repository's blast radius.
  1. Every recorded landing takes one of the forms above: a merge
     commit with two parents, whose diff is read against its first
     parent; every commit of a direct landing, or its `first..last`
     range; a landing in another repository, whose own audit is
     cited; or a statement that no range is recoverable, naming the
     paths the audit read.
  2. Anything the diff added that runs unattended -- cron, a timer,
     a scheduled workflow, a self-update -- reports its own failure
     somewhere a human sees, or is named as not doing so.
  3. No secret or token entered any repository, and no private
     hostname or IP address entered a repository more public than
     the one it came from.
  4. Anything the diff removed or renamed has no consumer left,
     checked by grep across every repository that could consume it
     rather than by assumption, with the greps stated.
  5. Where the repository publishes something -- an image, a
     release, a package -- a broken change fails closed: one that
     publishes nothing is recoverable, one that publishes something
     wrong is not.
<!-- shared-block-end -->

**In this repository.** `PUSH-AUDIT.md` exists at the repository
root and is referenced from `AGENTS.md`, so the final phase runs
it rather than explaining its absence. Note that every diff
command in it is written against `main...HEAD`: a stale local
`main` silently widens the audit to unrelated history, so fetch
before starting, or read it as `origin/main...HEAD`.

<!-- shared-block: plan-phase-landing v1 -->
Phase landing (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-phase-landing.md`):

A plan's status and a repository's review state both live in files
that every branch would otherwise rewrite. Left alone, that turns
each of them into a merge-conflict hot spot, and it spends a pull
request and a full CI run on a change that is entirely prose.
Three rules keep them out of the way.

- **A phase is closed out in the first commit of the next phase,
  not in a pull request of its own.** By the time the next phase
  branches, the previous one has merged, so its merge commit is
  known and its `Merged` cell can record the thing the push-audit
  phase actually needs. This is the only ordering that works: a
  phase cannot record its own merge commit, and a separate
  close-out pull request buys that record at the price of a round
  trip. The close-out sets the finished phase's `Status` and
  `Merged` cells and the plan's row in `docs/plans/index.md`, and
  it is committed before the next phase's own work, so that the
  branch never claims the plan is further along than the default
  branch is.

- **The last phase closes itself out.** The push-audit phase is
  the last row of every plan, so no next phase will carry its
  close-out. Where the audit raises findings, the plan is not
  complete until they are resolved or declined, and those land as
  their own pull request after the audit phase has merged -- so
  that pull request is the carrier, and it can record the audit
  phase's merge commit, which by then is known. Where the audit
  finds nothing there is no carrier, and no follow-up pull
  request is opened for the sake of one cell: the phase sets its
  own `Status`, and the plan's index row, to `Complete` in its
  own pull request, and records no `Merged` cell. It is the only
  row permitted to omit one. The column exists so that the
  push-audit phase can reconstruct what to audit; the audit phase
  is last, so nothing ever reads its own row.

- **`REVIEWS.md` is not pruned or regenerated in a pull request
  that changes code or documentation.** Editing a reviewed file
  stales its mark, and adding or removing an in-scope file moves
  the header count, but neither is the landing pull request's
  business. `prune` regenerates the file whether or not it dropped
  anything, so the `prune-reviews` workflow heals both on the next
  push to the default branch. Pruning from a branch is also wrong
  more often than it is right, though not for the reason it first
  appears: `prune` compares each stamp against `HEAD`, which on a
  branch is the branch tip, so it drops the marks for the files the
  pull request itself touched while keeping marks the default
  branch has already pruned. Committing that state merges a review
  file computed from a stale tree, and can resurrect marks
  `prune-reviews` has already removed. Accumulated staleness is
  reported by the `review-coverage` audit, which recomputes
  coverage against `HEAD` and raises an issue once the backlog is
  worth a review session.

  **A review session is the exception**, and it is not optional
  tidiness: `stamp` regenerates `REVIEWS.md` as well as writing the
  marks, and the rows, the sidecars and the marks are committed
  together (see `docs/code-review-tracking.md`). Where a repository
  requires a pull request to reach its default branch, that is how
  a review session lands, so "not in a pull request" is about the
  kind of change, not the mechanism.

These rules assume phases land one after another. Where two phase
branches are open at once, each closes out only the phase it
directly follows.
<!-- shared-block-end -->

**In this repository.** The plan index is `docs/plans/index.md`.
`review-tracking-tests` deliberately does not assert the `REVIEWS.md`
header count, so a phase that adds or removes an in-scope file needs
no regeneration commit; `prune-reviews` corrects the count on the
next push to main.

### Phase 1: the criterion and its specification

Effort: high, because a new criterion changes what the fleet is
measured against.

#### Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 1a | high | opus | none | Add `scripts/audit/checks/supply_chain.py`, a new family module (`packaging.py` and `ci_workflows.py` are both close to their size-ratchet baselines), holding one `Check` subclass with `id = 'advisory-ignore-review'`, `issue_title = 'Advisory ignore review'`, `spec = 'docs/audits/advisory-ignore-review.md'` and `template = None`; the issue title is the fleet-wide idempotency key, so it is fixed here rather than chosen during implementation. The class takes no constructor argument: `test_only_three_checks_take_a_constructor_argument` in `scripts/tests/test_metadata.py` pins that set. `applies(repo)` returns None, meaning run, when any of the four cargo-deny configuration paths *Mission* lists, or `.cargo/audit.toml`, is tracked, and otherwise the skip reason `'No cargo-deny or cargo-audit configuration'`, which becomes the `not_applicable` details on the compliance page; see the `applies()` docstring in `scripts/audit/check.py` and `ScheduledWorkflowHealth.applies()` in `scripts/audit/checks/ci_workflows.py` for the shape. Test tracking with `require_tracked_paths()` from `audit.files`, called once with all five paths as pathspecs (`require_tracked_paths(repo.path, 'deny.toml', '.deny.toml', '.cargo/deny.toml', '.config/deny.toml', '.cargo/audit.toml')`), not once per path: each call is two git subprocesses, and `applies()` is meant to be the cheap question. It raises `ToolFailure` when git cannot list the index, which is what it is for -- `tracked_paths()` returning None and being read as "nothing tracked" would make an unlistable repository `not_applicable` and close its issue -- and it means the criterion joins the set `test_an_empty_directory_is_a_tool_failure_exactly_where_expected` pins (1b). The listing comes back in git's index order, which is bytewise and puts `.deny.toml` before `deny.toml`, so choose the configuration by walking the check's own tuple of the four cargo-deny paths in cargo-deny's order and taking the first that is in the listing, never by taking the listing's first element. `run(repo)` takes the first tracked cargo-deny path in cargo-deny's order (*Mission*) the same way, from one listing of its own, names that path in every finding about it, and reads it and `.cargo/audit.toml`, whichever are tracked, through `repo.read()` and parses them with `tomllib`, as `packaging.py` does; `scripts/tests/test_containment.py` fails any check that opens a file another way, and 1b gives it something to open. Three ways a tracked file yields no configuration, and each is a `fail` naming why, never a raise and never a skip or a pass: a file `repo.read()` will not return (a link out of the clone, a directory, a dangling link, over the read cap), reported with `unreadable_file_detail(repo, name)`, because treating it as absent would close the repository's issue on the strength of a file nobody read; a file `tomllib` cannot parse; and a value of the wrong TOML type. Every value read out of the TOML is type-checked before use: `[advisories]` and `ignore` must be a table and a list, each entry a string or a table, `id`, `reason` and `crate` strings, and `expiry` a string -- an unquoted `expiry = 2027-03-01` parses to a `datetime.date` and a TOML datetime to a `datetime.datetime`, and either is a rule 2 finding naming the entry, not an exception from string handling. A string `expiry` is valid only if it matches `^\d{4}-\d{2}-\d{2}$` and is a real calendar date; anything else -- a duration (leading `P`), `"soon"`, `"2027-13-45"`, a truncated `"2027-03"`, or a wider ISO form that `datetime.date.fromisoformat` accepts and cargo-deny's `parse_expiry` does not (`"20270301"`, `"2027-W09-1"`, `"2027-03-01T00:00:00"`) -- is a rule 2 finding naming the entry and the value. Do not call `fromisoformat` on an unmatched string, and catch its `ValueError` on a matched one (`"2027-02-30"`). The general rule: nothing in the check may raise on content read out of the audited files, because `registry.run_check()` reports an exception as `error`, which means the audit's own tooling failed, not that the audited repository is wrong. A `reason` that is empty or whitespace-only is missing. Classify each `ignore` entry as cargo-deny does (*Situation*: a string containing `-` whose prefix is uppercase ASCII, or a table with `id`, is an advisory, anything else a yanked-crate spec). Test the prefix with `all('A' <= c <= 'Z' for c in prefix)`, not `str.isupper()`, which disagrees with cargo-deny in both directions: it is false for an empty prefix and true for `"A1"`. It implements rules 1 to 5 from *Mission*, each against the file *Mission* names, and reports every finding, not just the first: one line per finding, naming each advisory ID or crate and, for rules 3 and 4, its absolute expiry date, in the `findings` result key, with a one-line summary as the `fail()` detail that leads with which rules fired ("review due for 1 ignore", "review overdue for 1 ignore", "deny.toml does not set unused-ignored-advisory"), since that detail is all `docs/audits/compliance.md` shows and a periodic rule 4 red must read differently from a configuration defect. Rule 4 words a due entry and a past one differently, in both `findings` and the detail (*Mission*, rule 4). No count of days appears in `findings` or the detail (*Mission*, rule 4). Rule 5 compares IDs as exact strings. `findings` is one of the two per-item keys `build_issue_body` in `scripts/audit-manage-issues.py` renders through `render_issue_items` (the other is `missing`; `details` goes through `render_details`), and `test_every_extra_result_key_is_one_the_issue_renders` in `scripts/tests/test_metadata.py` fails any other. The run date is injected the way `scripts/audit/checks/distros.py` does it: a module-level `scan(repo, today=None)` does the work and returns the findings, `run(repo)` calls it with no `today`, and the date-dependent tests call `scan()` directly, as `scripts/tests/test_distros.py` does. Follow distros for the shape but not for the clock: `retired_releases()` defaults to `datetime.date.today()`, the runner's local date, and the runners are self-hosted, so the default here is `datetime.datetime.now(datetime.timezone.utc).date()`, matching cargo-deny. An entry is past only when `expiry < run_date`. Append the instance at the end of `CHECKS` in `scripts/audit/registry.py`, as a new family, since the results JSON's ordering is published (the module docstring says why), and add `supply_chain` to its `from audit.checks import (...)` list. Do not mention this plan or its phases in the module's comments or docstrings: `plan-history-in-source` fails source that cites a plan's phases, steps or decisions, and `plan-source-references` fails a plan reference that does not resolve. First, as its own commit, move `unreadable_file_detail` from `scripts/audit/checks/plans.py` to `scripts/audit/files.py` beside `require_tracked_paths`, and re-point `plans.py`'s call sites: every shared helper lives in `audit/check.py`, `audit/repo.py`, `audit/files.py` or `audit/text/`, and the only import between modules in `audit/checks/` today is within one family. Check that the move introduces no import cycle (`files.py` does not import `audit.repo` today). |
| 1b | high | opus | none | `scripts/tests/test_supply_chain.py`, following the `CheckTestCase` convention in `docs/consistency-audits.md`; the date-dependent cases call `scan(repo, today=...)` directly (1a). Cover: not applicable with no cargo-deny or cargo-audit configuration, asserting the reason text; applicable, and read, with the configuration at each of `.deny.toml`, `.cargo/deny.toml` and `.config/deny.toml` instead of `deny.toml`, each finding naming the actual path; with both `deny.toml` and `.deny.toml` tracked, `deny.toml` is the one read; a passing configuration; each of rules 1 to 5 failing alone; a `deny.toml` with no `[advisories]` table, which fails rule 1; `unused-ignored-advisory` set to `"warn"` and to `"allow"`, each failing rule 1 as distinct from the key being absent, since `"warn"` is the default ryll and andris run under today, and set to `true` and to a table, each a rule 1 finding rather than an `error`; a string-form advisory entry; a duration-form `expiry`; a malformed `expiry` string (`"soon"`, `"2027-13-45"`, `"2027-02-30"`, `"2027-03"`) and a wider ISO form cargo-deny rejects (`"20270301"`, `"2027-03-01T00:00:00"`), each a rule 2 finding naming the value rather than an `error`; a `reason` that is empty, one that is whitespace-only, and one that is not a string, each a rule 2 finding; an ignore table carrying both `id` and `crate`, which classifies as an advisory, as cargo-deny does; an unquoted TOML date and a TOML datetime as `expiry`, a non-list `ignore`, an entry that is neither string nor table, and a non-string `id`, each a finding naming the entry rather than an `error`; an expiry exactly 14 and exactly 15 days out, and exactly 180 and 181; an expiry equal to the run date, which is due but not past, and one day before it, which is past; a yanked-crate `{ crate, reason }` entry with no `expiry`, which passes, and a bare `"crate@version"` string, which fails rule 2; the classification boundary, from cargo-deny's own rule (*Situation*): `"FOO"`, with no `-`, and `"A1-x"`, whose prefix is not all uppercase letters, are each a bare package spec and fail rule 2 as one, while `"-x"`, whose prefix is empty, is a string-form advisory; a `.cargo/audit.toml` ID missing from `deny.toml`, and the reverse passing; a `.cargo/audit.toml` with ignores and no `deny.toml`, which fails rule 5 and nothing else; a `.cargo/audit.toml` with an empty ignore list and no `deny.toml`, which passes; an unparseable `deny.toml`; a tracked `deny.toml` that `repo.read()` will not return, which fails saying so; the same two for `.cargo/audit.toml`, each a `fail` naming that file, so rule 5 can never pass on a file nobody read; a `.cargo/audit.toml` whose `[advisories]` is not a table, whose `ignore` is not a list, or with a non-string entry, each a `fail` naming that file rather than an `error`; an `[advisories]` table with no `ignore` key, which passes as long as rule 1 holds; that every finding arrives in `findings`, not only the first; that rule 3 and 4 findings carry the absolute date and no day count; that a due entry and a past one are worded differently in both `findings` and the `fail()` detail; and that `scan()`'s default date is the UTC date, not the local one. Add the `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` lines in `scripts/tests/test_metadata.py`, with the literals 1a names, and add `advisory-ignore-review` to the sorted list in `test_an_empty_directory_is_a_tool_failure_exactly_where_expected` in the same file, because `applies()` asks git for the index (1a); its docstring stays true. Add a minimal `deny.toml` and `.cargo/audit.toml` to `OVERLAY` in `scripts/tests/test_containment.py`, whose comment says it exists for ecosystems this repository lacks: without them the containment suite sees the criterion as `not_applicable` and never drives its reads through the link-out-of-clone, dangling-link and directory shapes. Keep the new file under the 1,500 lines `tools/source-size-ratchet.py` allows a new file; if it cannot be, the `tools/source-size-baseline` entry is a hand edit with its reason in the pull request description. |
| 1c | medium | opus | none | `docs/audits/advisory-ignore-review.md`, following the structure in `docs/audits/README.md`: what is checked, why the work splits between cargo-deny and the audit (the *Situation* table is the evidence), the conventions the check does not enforce (90 days by default, 180 at most, what a review pull request says, staggering), what it does not cover, what it assumes (that CI runs cargo-deny's advisories check at 0.21.0 or later, and why an older binary fails closed; that cargo-deny finds its configuration by its default search, and that `--config` elsewhere or a workspace below the root would drop the repository out of scope silently), why yanked-crate entries have liveness but no clock, and whether the `{ crate, reason }` form was exercised against the binary in phase 2's verification or only read from the source, so a reader knows which half of rule 2 is evidence; that the verdict moves with the calendar by design, so a periodic red during the review window is the expected steady state and not a regression, and `compliance.md#advisory-ignore-review` under `## Projects`. The page is documentation content, so `docs-line-references` and `plan-phase-references` measure it: cite cargo-deny by symbol and diagnostic name (`IgnoreId`, `parse_expiry`, `advisory-ignore-expired`, `advisory-not-detected`, `unused-ignored-advisory`), or by a GitHub permalink pinned to a full commit sha (a tag is not enough for `LINE_ANCHOR_RE`), never as `file:line`, and do not name this plan's phases by number. Record the cargo-deny version the criterion was measured against (0.22.1, unless 1d finds it has moved), and say that a cargo-deny major version bump is a trigger to re-read the criterion, since the action tracks the tool and Renovate bumps only the action. Add its line to the criterion index in `docs/audits/README.md`. |
| 1d | medium | opus | none | Management session, inline. Run `scripts/audit-check.py` against fresh clones of ryll, andris, teneral and this repository, and `audit-manage-issues.py --dry-run` over the result. Record each verdict in this plan. Expected: ryll and andris fail rules 1 and 2, teneral fails rule 1 alone, and this repository is not applicable. Any other verdict is either a check bug or a premise this plan got wrong. Re-confirm the cargo-deny version that `EmbarkStudios/cargo-deny-action@v2` runs, and that ryll's and andris's `ci.yml` and `supply-chain.yml` still invoke it with `command: check` (or `check advisories`), and teneral's `make deny` still runs `check all`; record the version teneral's build image installs, too. None of the three may pass `--config`. If the version has moved past 0.22.1, re-read the source paths cited in *Situation* and record whether the key and diagnostic names, the `date > expiry` comparison and the UTC clock still hold. Run `pre-commit run --all-files` before proposing the commit. |

#### Definition of done

- All six of the criterion's files are in step, as *Adding a
  criterion* in `docs/consistency-audits.md` lists them.
- The dry run names ryll, andris and teneral and no other
  repository.
- The specification says what each layer enforces and why, and a
  reader who was not here can follow the 2026-11-25 measurement it
  rests on.

### Phase 2: adopt in ryll, andris and teneral

Merged: one line per repository, as `<repo> <sha> (#pr)`.

Each repository gets one pull request, which closes the issue
that phase 1's first real run files there with `Fixes #N`. The
audit's cron runs on Sundays; to file the issues without waiting
for it, dispatch `consistency-audit.yml` by hand once phase 1 has
merged. ryll's builds on shakenfist/ryll#805, which removed the
eight ignores that suppressed nothing and merged on 2026-11-26.

Each pull request runs that repository's own `PUSH-AUDIT.md` over
its branch before it lands, and records the result in its
description; all three repositories have one. Phase 3 cites those
audits rather than re-running them, per the push-audit block
above.

Every expiry written in this phase falls between 60 and 90 days
after the day the pull request is written. The criterion passes
only expiries 15 to 180 days out (rules 3 and 4), and 90 days is
the default convention; an expiry inside 14 days would fail the
criterion the day it lands. Stagger within that range, so that a
repository's ignores do not all come due in the same week.

#### Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 2a | medium | opus | worktree | In ryll: set `unused-ignored-advisory = "deny"` under `[advisories]` in `deny.toml`. Convert each ignore to `{ id, reason, expiry }`, carrying the comment's rationale into `reason`, with the expiries staggered between 60 and 90 days out (see above). Update the ignore policy in `docs/ci.md` and the *Situation* of `docs/plans/PLAN-supply-chain-followups.md` to describe the review clock, and to say that a dependency bump which fixes an ignored advisory now fails `cargo deny` until the same pull request removes the ignore, and that the weekly cron can now go red for a change to the advisory database rather than to the repository (open question 4). Run ryll's `PUSH-AUDIT.md` over the branch and record the result in the pull request description. |
| 2b | medium | opus | worktree | In andris: the same, after removing RUSTSEC-2025-0134 from both files, since it suppresses nothing there (cargo-deny reports it as `advisory-not-detected`, and `cargo audit` only warns). Read andris's own docs for the place that states its scanner policy, rather than assuming ryll's layout. Run andris's `PUSH-AUDIT.md` over the branch and record the result in the pull request description. |
| 2c | low | sonnet | worktree | In teneral: set `unused-ignored-advisory = "deny"` under `[advisories]` in `deny.toml`; the ignore list is empty, so there is nothing to convert. Its `deny.toml` comment already says each entry carries why it is accepted and what would remove it; extend it to say each entry is a `{ id, reason, expiry }` table with an absolute expiry 60 to 90 days out, and that a dead ignore now fails the run. Record the same, and that the advisory job gates no merge and so an expiry reddens a job nobody is blocked by, wherever teneral's docs state its scanner policy (`supply-chain.yml`'s header comment at least). Run teneral's `PUSH-AUDIT.md` over the branch and record the result in the pull request description. |
| 2d | medium | opus | none | Management session, inline, for each repository: run its scanners as its CI does (ryll and andris: both in a `rust:latest` container; teneral: `make deny`) and confirm they pass. Then prove that each mechanism fails, on a scratch copy and not on the branch: an expiry set to yesterday must fail `cargo deny` with `advisory-ignore-expired`, and a bogus ignore ID must fail it with `advisory-not-detected`. On one scratch copy, also add a yanked-crate `{ crate = "...", reason = "..." }` ignore and confirm cargo-deny loads it and reports it as `yanked-not-detected` rather than rejecting the key, since no repository carries that form and rule 2's yanked-crate half is otherwise read from the source alone; record the result for step 1c's page. Re-run phase 1's check against each branch and confirm it passes. |

#### Definition of done

- All three pull requests have merged, each with its own push
  audit recorded, and the next weekly run closes all three issues,
  or a hand-dispatched run does.
- Both mechanisms were shown to fail, not just to pass, and the
  yanked-crate form was shown to load.

### Phase 3: push audit

Run this repository's `PUSH-AUDIT.md` over phase 1's merge commit,
read against its first parent. Phase 2's landings in ryll, andris
and teneral are not re-run here: each was audited by that
repository's own runbook as part of the pull request that landed
it (steps 2a to 2c), and this phase cites those three results. The
question only this phase can answer is what the two halves did to
each other: that the criterion's verdict on each repository, after
phase 2, is a pass.

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

**In this repository.** High effort is anything that changes what
a criterion means, anything that touches the scheduler in
`audit-check.py`, and anything that reaches
`audit-manage-issues.py` -- those decide what the fleet is held
to and what lands in other people's issue trackers. Medium effort
covers adding a criterion that follows the shape of an existing
one, a documentation sweep, or a template change with a worked
example already in the tree.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

**In this repository.** A worked brief: instead of "add a check
that plans are indexed", write "add a `PlanIndex` class to
`scripts/audit/checks/plans.py` declaring the id `plan-index`, its
`spec` and its `issue_title` as class attributes and implementing
`run(repo)`, register the instance in `CHECKS` in
`scripts/audit/registry.py` beside the other plan checks, write
`docs/audits/plan-index.md` following the structure in
`docs/audits/README.md` and linking to `compliance.md#plan-index`,
add the file to `docs/audits/README.md`, add its lines to
`FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in
`scripts/tests/test_metadata.py`, and add tests to
`scripts/tests/test_plans.py` covering pass, fail and
not-applicable." A brief that names five of the criterion's six
files is the characteristic defect here, and the frozen tables are
the one most often left out.

A check that does not apply reports
`not_applicable` with a reason rather than being omitted, because
an omitted check renders as `unknown`.

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

**In this repository.** The project-specific checks referred to
above are:

- [ ] `pre-commit run --all-files` passes. It runs actionlint,
      shellcheck, flake8, skillsaw and all five test suites, and
      `ci.yml` runs the same command on every pull request.
- [ ] `python3 scripts/audit-check.py --repo-path . --repo-name
      development` still reports what it reported before the
      change, or the plan says why the verdict moved.
- [ ] If the change touches issue filing, it was exercised with
      `--dry-run` only.

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `pre-commit run --all-files` passes.
* `scripts/audit-check.py` run against this repository reports no
  new failures, or the plan states which verdicts moved and why.
* Any new or changed criterion has all six of its files in step:
  the `Check` subclass in `scripts/audit/checks/<family>.py`, its
  registration in `CHECKS` in `scripts/audit/registry.py`, its
  tests in `scripts/tests/test_<family>.py`, the
  specification under `docs/audits/`, its row in
  `docs/audits/README.md`, and its frozen lines in
  `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in
  `scripts/tests/test_metadata.py` -- plus a
  `FROZEN_COLUMN_NAMES` line where a spec page carries more than
  one check. `AUDIT_METADATA`, `ISSUE_TITLES` and `COLUMN_NAMES`
  are derived from the registry rather than tables anybody edits;
  the issue title is the fleet-wide idempotency key, so renaming
  one orphans every open issue for that check across the fleet.
* No `consistency-audit` marker block has been added to a
  criterion specification by hand, and the compliance tables in
  `docs/audits/compliance.md` have not been hand-edited.
* Any entry added to `REPO_OVERRIDES` carries a stated reason.
* Anything under `templates/` is judged as the code it will
  become in ten other repositories: placeholders consistent, no
  reference to paths that only exist here, and the README beside
  it saying what to substitute.
* Python is wrapped at 120 characters, single quotes for strings
  and double quotes for docstrings, and no script has grown a
  dependency outside the standard library.
* Documentation in `docs/` describes any user-visible change.
  `AGENTS.md` changes only if a convention changed;
  `ARCHITECTURE.md` only if the shape of the system changed;
  `README.md` only if the pitch, the install story or the
  documentation links changed.

### Documentation index maintenance

When creating a new master plan from this template, add one row to
the table in `docs/plans/index.md`: the date the plan was written,
a link to it, a one-line intent, and its status from the
vocabulary above. Rows run oldest first. One row per master plan,
never one per phase -- the phases are tracked in the plan's own
Execution table, and duplicating them in the index is how the two
drift apart.

There is no phase-arithmetic column and no `order.yml` here; both
belong to repositories whose documentation is published through a
generated navigation. The `plan-index` criterion checks the
columns this index actually has.

The index row carries the whole-plan status, so it only reaches
`Complete` once every phase has been completed, abandoned or
superseded. Update it as the plan progresses, not only at the end.

Once a plan has been finished for 90 days it is retired as
`docs/plan-retirement.md` describes: its reasoning is moved into
docs/ and its *Future work* is filed as issues. Write both so they
survive being read by someone who was not here.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

- uncalibrated-sextant and visual-digest-rust carry a `Cargo.lock`
  and run neither scanner. Whether every Rust repository should run
  cargo-deny is a criterion of its own.
- Drop `cargo audit` where `cargo deny` already runs (open question
  2). That would remove `.cargo/audit.toml` and rule 5 with it.
- Extend the review clock to other ecosystems when the first
  advisory ignore appears in one. osv-scanner's `ignoreUntil` is
  the equivalent of `expiry`; pip-audit has none.
- visual-digest-rust depends on `rqrr` 0.10, which pulls in `lru`
  0.16.4 (RUSTSEC-2026-0253, unsound). `rqrr` 0.11 moves to `lru`
  0.18, which carries the fix. That is not an ignore, but it was
  found while measuring the *Situation* table.
- Whether the fleet's `deny.toml` files should widen `unmaintained`
  and `unsound` from `workspace` to `all`. Today a transitive
  unmaintained or unsound crate is not reported at all, which is
  why nine of the twelve ignores suppressed nothing, and why the
  `rqrr` finding above was found by hand rather than by CI. Widening
  is a separate decision with its own backlog: it would make the
  deleted ignores live again. The two are coupled, though: until it
  happens, `unused-ignored-advisory = "deny"` forbids recording an
  accepted transitive unmaintained or unsound crate as an ignore.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

- shakenfist/ryll#802: RUSTSEC-2026-0596, an out-of-bounds read in
  `etherparse` 0.21.0, was failing `cargo audit` and `cargo deny`
  on every ryll pull request. Fixed with a lockfile bump to 0.21.1.
  It was first misread as the `lru` advisory, which is what started
  this plan.
- shakenfist/ryll#805: removed ryll's eight ignores that suppressed
  nothing, and stopped `.cargo/audit.toml` from mirroring
  `deny.toml`. Merged 2026-11-26; phase 2 builds on it.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
