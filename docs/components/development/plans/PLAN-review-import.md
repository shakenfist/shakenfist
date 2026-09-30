# Review import

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly. Read the relevant
files and ground your answers in what they actually say. Do not
speculate about the repository when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

There is no application code here. The artifacts are the audit
specifications in `docs/audits/`, the tooling in `scripts/` that
measures them, the templates in `templates/` that the rest of the
fleet copies, and the workflows that run all of it every morning
against every Shaken Fist repository.

Consult `AGENTS.md` for the conventions and the invariants that
are not visible in the code, and `ARCHITECTURE.md` for the shape
of the system. `docs/consistency-audits.md` is the reference for
what a daily run does, how to add a criterion, how to bring a
repository into scope, and how to test a change before it reaches
the fleet -- read it before changing anything under `scripts/` or
`docs/audits/`. `docs/code-review-tracking.md` covers the review
tooling, and `PUSH-AUDIT.md` is the pre-push review runbook that
every plan's final phase runs.

Two things make planning here different from planning in a
repository that holds a product, and both should shape any plan
written from this template:

* **The blast radius is other people's repositories.** The daily
  workflow files and closes GitHub issues fleet-wide. A change
  that is merely wrong does not produce a red build; it produces
  issues in ten repositories, or silently closes ones that should
  have stayed open. Always pass `--dry-run` when running
  `audit-manage-issues.py` by hand.
* **This repository is in its own audit matrix.** A standard we
  exempt ourselves from is a standard we stop noticing the cost
  of. A change to a criterion is a change we are measured against
  the next morning, so a plan should say how many repositories --
  including this one -- it newly fails.

<!-- shared-block: plan-file-conventions v1 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section:

  | Phase | Plan | Status |
  |-------|------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started |

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

Whole-file human review (`docs/code-review-tracking.md`) is
deployed in five repositories: this one, actions, hunkydory,
kerbside and ryll. Much of what churns the review queue in the
other four is not their own code. It is files this repository
manages -- workflow templates, helper scripts, the exported GitHub
configuration -- which are written and reviewed here, copied out
by `standards-alignment` rollouts, and then have to be reviewed
again, file by file, in every repository that received them. A
template change touching five files costs five re-reviews here and
up to twenty more across the fleet, of content that is often
byte-for-byte what was already read here.

A review mark attests to a blob SHA, not a path, and two files
with the same blob SHA have the same bytes. So a review of a blob
here is equally a review of an identical blob anywhere else. The
tooling does not know that: `prune` in a target repository only
ever compares a target's own stamps against its own `HEAD`.

Measured on 2026-09-24 against local clones (one to five days
old), counting target files whose blob at `HEAD` matches a blob
this repository has stamped as fully reviewed at any point in the
sidecar's history:

| Repository | In-scope files needing review | Byte-identical to a reviewed blob here |
|------------|-------------------------------|----------------------------------------|
| actions | 14 | 0 |
| hunkydory | 2 | 5 |
| kerbside | 140 | 4 |
| ryll | 37 | 4 |

The matches are `export-repo-config.yml` (three repositories),
`.github/exported-config/repository-settings.json` (three),
`tools/mermaid-lint.sh` (two), and one each of
`codeql-analysis.yml`, `pr-re-review.yml`, `pr-retest.yml`,
`mermaid-lint.yml` and `pin-indirect-dependencies.sh`. Counting
only the stamps current at this repository's `HEAD` drops ryll
from 4 to 2 and kerbside from 4 to 3: a target copy that lags a
template this repository has since changed and re-reviewed is the
common case during a rollout, and only the sidecar's history still
records the review of the older blob.

Import on its own therefore buys a handful of files per repository
today. The near misses are the larger opportunity. Most
template-derived files have drifted from their template, and the
drift is of four kinds, which want different treatment:

* **Deliberate parameters.** `templates/renovate/renovate.yml`
  carries `{{GITHUB_REPO_NAME}}` in `RENOVATE_AUTODISCOVER_FILTER`,
  so every copy differs by one line by construction, although
  `${{ github.repository }}` would supply the same value at run
  time. `mermaid-lint.yml` expects a repository with its own
  exclusions to add a `!path/**` pair, and says so.
* **Copies lagging the template.** kerbside's and ryll's
  `mermaid-lint` files are blob-identical to older versions of the
  template; `pr-re-review.yml` in three repositories predates the
  template's merge-ref hardening. (A local clone of ryll also
  showed a runner label lagging, and Renovate pins can drift the
  same way, but step 3a's fresh clones found neither.)
* **Local improvements never upstreamed.** actions'
  `renovate.yml` has a `concurrency` block the template lacks;
  ryll's `codeql-analysis.yml` has the review-state `paths-ignore`
  block that `docs/code-review-tracking.md` step 8 asks every
  adopted repository to carry.
* **Genuinely per-repository files.** `release.yml` (155 to 529
  differing lines) and the review automation workflows (240 and
  more) are adapted substantially per repository, and may be
  better not thought of as copies at all.

The review tracking CI machinery is itself in the second and
third categories without being a template: `prune-reviews.yml`,
`tools/ci-prune-reviews.sh` and `tools/review-tracking.sh` exist
in four divergent hand-made copies. actions' `ci-prune-reviews.sh`
has a three-attempt landing retry and a `git status --porcelain`
check the other three lack; the workflows differ in default branch
(`main` against `develop`), in whether they carry the
`workflow_dispatch` ref guard (kerbside does not), and in how
ryll's checkout authenticates (`DEPENDENCIES_TOKEN`, because its
ruleset requires a pull request and the Actions app cannot bypass
it).

Two constraints the design has to respect:

* **The prune commit's attestation argument.** The bot's
  `Prune stale review marks.` commits are unsigned, and
  `docs/code-review-tracking.md` justifies that on the grounds
  that prune can only remove marks. An import adds marks from an
  unsigned bot commit, so the argument has to be rebuilt rather
  than inherited: an imported mark must point at the signed commit
  in this repository that introduced the stamp, so that it is a
  pointer to an attestation rather than an attestation.
* **Amplification.** Today an unsigned commit that forges a stamp
  on this repository's `main` marks files reviewed in this
  repository only. With import it marks them reviewed in five.
  The import is therefore the right place to check signatures
  rather than merely record where they are.

## Mission and problem statement

Make a review done here count wherever the same bytes appear in
another adopted repository, and make the same bytes appear more
often.

When this plan is complete:

1. `review-tracking.py import`, run in an adopted repository,
   marks as reviewed every in-scope file whose blob at `HEAD` this
   repository has stamped under a full-file mark in a signed
   commit, recording in the target's sidecar where the attestation
   lives. `REVIEWS.md` shows which reviews were imported and from
   where.
2. The review tracking CI machinery is a template in
   `templates/review-tracking/`, copied byte-for-byte into every
   adopted repository, running `prune` and then `import` on every
   push to the default branch and daily.
3. The templates whose copies have drifted from them in the
   adopted repositories have been triaged, the drift parameterised
   away or upstreamed where that is possible, and the copies
   re-synchronised.
4. `docs/code-review-tracking.md` describes import and its
   attestation argument.

It deliberately does not cover shared blocks. They are spliced
into files such as `AGENTS.md`, so the enclosing file's blob never
matches anything reviewed here, and a partial-file import would
need region marks that survive edits elsewhere in the file, which
the tooling deliberately does not trust. Nor does it cover moving
templates to reusable workflows, or an audit criterion enforcing
template conformance; both are recorded as future work.

## Open questions

Each question carries the default the plan takes if nobody
answers.

1. **Where do imported marks live?** *Decided 2026-09-24:* only
   in `.vscode/imports.weaudit-shas.json`, a sidecar-shaped file
   with no `.weaudit` file beside it, and never in a human
   reviewer's state file. The boundary between what a human did in
   this repository and what an automated process brought in is
   then explicit, and a reviewer working in VSCode is never shown
   a tick for a file they have not read here.

   A `.weaudit` file was the first choice and step 1a ruled it out.
   weAudit loads every `.vscode/*.weaudit` file and decorates a
   file as audited from its path alone, whatever file or author the
   entry came from (`src/codeMarker.ts:626-628` in
   trailofbits/vscode-weaudit), and hiding another file's marks is
   a per-session toggle that is not persisted and only works when
   the entries' `author` equals the file's basename. Worse, weAudit
   writes an `auditedFiles` change to `<author>.weaudit`
   (`codeMarker.ts:559-586`, `:931`), so an imported entry authored
   by the original reviewer would, once un-ticked, be written into
   that reviewer's own state file. weAudit only globs `*.weaudit`,
   so it never reads the sidecar-shaped file.

   The name also matches the `.gitignore` exception and the
   `paths-ignore` entry (`.vscode/*.weaudit-shas.json`) that step 8
   of the adoption procedure already put in every adopted
   repository, so an import commit neither needs a new exception
   nor triggers the expensive CI lanes.
2. **What triggers an import after a review lands here?** Default:
   add a daily `schedule` trigger to `prune-reviews.yml`. A rollout
   usually reaches the targets before the template is re-reviewed
   here, and a push-only trigger would then never look again. The
   alternative -- this repository dispatching `prune-reviews` in
   every adopted repository when its own review state changes --
   imports within minutes but needs a token with `actions: write`
   across the fleet.
3. **Should the source be restricted to `templates/`?** Default:
   no. Any path fully reviewed here is eligible, because identical
   bytes need no context the file does not carry, and whole-file
   review never attested to context anyway: a native mark does not
   go stale when a script the file calls changes either. The
   matches above show why the restriction would cost something:
   ryll's `tools/mermaid-lint.sh` matches this repository's
   `tools/mermaid-lint.sh`, not the template.
4. **Should this repository import from itself?** Default: no.
   Identical copies within this repository (a template and this
   repository's own instance of it) could cross-import, but the
   case is rare, and running import against its own clone is a
   no-op worth making explicit rather than an edge worth
   supporting.
5. **Is gitsign available on the static runners?** *Answered by
   step 2a, 2026-09-24:* no. The static runners are provisioned by
   `mach33labs/33fl/static_runner.yml` with a minimal base package
   set. The workflow downloads a pinned `sigstore/gitsign` release
   binary and checks it against the release's `checksums.txt`, the
   same way other workflows fetch pinned release tarballs; baking
   it into the runner image can follow once the workflow has proven
   out. Verification also needs egress to Rekor
   (`rekor.sigstore.dev`) and Sigstore's TUF root, which nothing in
   the fleet yet proves the runners have; the first scheduled run
   in phase 2 is the test. If verification cannot run there, import
   records provenance without verifying it and says so on stderr on
   every run. *Settled 2026-09-30:* the first runs after the
   rollout imported 16 entries across the four repositories (actions
   3, hunkydory 6, kerbside 4, ryll 3), every one `verified: true`,
   so the static runners reach Rekor and the TUF root.
6. **The checkout token.** *Answered by step 2a, 2026-09-24:*
   `DEPENDENCIES_TOKEN` is a per-repository secret, present in
   exactly the three adopted repositories whose `develop` ruleset
   requires a pull request (hunkydory, kerbside, ryll) and absent
   from the two whose `main` rulesets do not (actions, this
   repository). The template's checkout uses
   `${{ secrets.DEPENDENCIES_TOKEN || github.token }}`, which
   reproduces today's behaviour in all five from one file.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. `import` subcommand | Complete | 54c7ecc (#177) |
| 2. Review tracking CI template and rollout | Complete | 02d6dbb (#186); actions 74a33c9 (#111); hunkydory f24d413 (#57); kerbside 62ccbcb (#504); ryll ce3ebb3 (#418) |
| 3. Template convergence | In progress | |
| 4. Verification | Not started | |
| 5. Push audit | Not started | |

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

<!-- shared-block: plan-push-audit-phase v3 -->
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
  untriggered for as long as it did.
<!-- shared-block-end -->

**In this repository.** `PUSH-AUDIT.md` exists at the repository
root and is referenced from `AGENTS.md`, so the final phase runs
it rather than explaining its absence. Note that every diff
command in it is written against `main...HEAD`: a stale local
`main` silently widens the audit to unrelated history, so fetch
before starting, or read it as `origin/main...HEAD`.

Phases 2 and 3 land partly in actions, hunkydory, kerbside and
ryll. Each of those pull requests is audited against its own
repository's default branch as it lands, and phase 5 cites those
audits.

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

One exception matters to this plan. Phase 1 changes how
`REVIEWS.md` is rendered, and `review-tracking-tests` asserts that
every row of this repository's `REVIEWS.md` is reproducible from
the committed state. Phase 1's pull request therefore has to
regenerate this repository's `REVIEWS.md` in the same commit as
the rendering change, or its own CI fails. That is a rendering
change, not a prune, and does not break the rule above.

### Phase 1: `import` subcommand

Planning effort: high. This phase decides what an imported mark
means and how it is verified.

Add an `import` subcommand to `scripts/review-tracking.py`, kept
separate from `prune` so that `prune` stays remove-only -- the
property the existing attestation argument rests on -- and the one
subcommand that adds marks without a human can be read, tested
and switched off on its own.

**Source.** The source is the clone of this repository the script
itself lives in (the parent of `scripts/`), which is where the
target's wrapper already found it. `import` refuses to run when
the target's top level is that same clone (open question 4).

**What counts as a reviewed blob.** Walk the history of every
`.vscode/*.weaudit-shas.json` in the source, oldest first. At each
commit, a sidecar entry `path -> sha` counts only if the same
commit's `.vscode/<reviewer>.weaudit` has a full-file
`auditedFiles` entry for `path` that is not a derived directory
entry. The sidecar alone is not enough: `stamp` also stamps files
that carry only partial (region) marks, and a partial review must
never be imported as a full one. The first commit at which a
`(reviewer, sha)` pair qualifies is the commit that introduced the
attestation; record it. Build a map from blob SHA to
`(reviewer, source path, introducing commit, stamp date)`. Where a
blob was reviewed more than once, prefer the earliest.

**Verification.** For each introducing commit that is about to be
used, verify its signature with gitsign against the identity and
issuer in `docs/code-review-tracking.md` ("Commit signing"). A
commit that fails verification is skipped with a warning naming
it. `--no-verify` disables this, and when verification is disabled
or gitsign is not installed, every run says so on stderr. Record
the verification outcome per import in the run's output.

**What gets imported.** For each in-scope tracked file in the
target (existing `load_scope()`, `in_scope()`, `tracked_files()`)
whose `HEAD` blob is in the map, which does not already carry a
valid full-file mark in a reviewer's state file, and which no
`import-exclude` pattern matches (below), add an entry to
`.vscode/imports.weaudit-shas.json` (open question 1):

```json
{
  "version": 1,
  "files": {
    "<target path>": {
      "sha": "<blob sha>",
      "date": "<source stamp date>",
      "imported": {
        "repo": "shakenfist/development",
        "reviewer": "<reviewer>",
        "path": "<source path>",
        "commit": "<introducing commit>",
        "verified": true
      }
    }
  }
}
```

`import` writes nothing else: no `.weaudit` file, and never a
human reviewer's state file or sidecar. The `date` is the source
stamp's date, not today, because it records when the content was
read. `verified` records whether the introducing commit's
signature was checked.

A native review supersedes an import: when a reviewer's state file
carries a valid full-file mark for a file the imports file also
lists, `import` removes its own entry, so `REVIEWS.md` shows one
row per file and the human's is the one kept.

A file carrying a stale native mark is not a candidate until
`prune` has removed that mark, which is why the CI script runs
`prune` first. A file carrying only a partial native mark can be
imported; the partial row stays as well.

**Rejecting an import.** An import has no tick in weAudit, so
there is nothing there to un-tick, and deleting the entry by hand
would only see it re-imported on the next scheduled run. So
`.vscode/review-scope.toml` gains an optional `import-exclude`
list of fnmatch patterns with the same semantics as `exclude`
(including `!` re-includes). `import` never adds a matching file
and removes any existing imported entry that matches. Like the
other exclusions in that file, an entry should say why in a
comment.

**Interaction with the other subcommands.** The imports file is a
source of marks for every read path, and is a separate code path
from `state_files()`, which keeps globbing `*.weaudit` only.

* `prune` drops an imported entry whose sha no longer matches
  `HEAD`, exactly as it does a native stamp; the blob SHA is what
  the review attests to. It removes the imports file when it
  empties.
* `stamp` ignores the imports file entirely. Nothing in it was
  marked in this clone.
* `status` counts an imported entry whose sha matches `HEAD` as a
  valid review. It does not verify provenance: native marks are
  not signature-checked by `status` either, and the audit
  workflow's depth-1 checkout of this repository has no history to
  check against. `status --json` gains an `imported` count so the
  audit can report it.
* `next` treats imported files as reviewed and does not offer
  them.
* `render_reviews_md()` counts imported files toward the header's
  reviewed count and gives them rows in the reviewed-files table,
  with a new `Source` column: `-` for a native review,
  `development@<12-char commit>` for an imported one, and the
  `Reviewer` cell from the entry's `imported.reviewer`. Regenerate
  this repository's `REVIEWS.md` in the same commit (see the
  landing note above).

**Output and exit status.** Print one line per import naming the
target path, the source path and the short commit, and a summary
count. Exit zero whether or not anything was imported; the CI
script decides whether to commit by looking at the tree, as it
does for `prune`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Before any code, read the weAudit extension source (`trailofbits.weaudit`, on GitHub) and report only: whether it shows audited-file ticks from a `.vscode/*.weaudit` file that is not the current user's (here `.vscode/imports.weaudit`), whether that can be turned off in the UI or settings, whether it ever writes to a state file other than the current user's, and which top-level keys a state file needs for weAudit to load it without error. Settles the part of open question 1 that is still open. |
| 1b | high | opus | worktree | Implement `import` in `scripts/review-tracking.py` exactly as the phase 1 section of `docs/plans/PLAN-review-import.md` describes: history walk with full-mark filtering, gitsign verification with `--no-verify`, candidate selection, writes confined to `.vscode/imports.weaudit-shas.json` (no `.weaudit` file is ever written or read for imports), native marks superseding imports, the `import-exclude` list in `load_scope()`'s config, the self-import refusal, `prune`, `stamp`, `status`, `next` and `render_reviews_md()` treating the imports file as described, `status --json` `imported` count, and the `Source` column in `render_reviews_md()`. Follow the file's existing style (module-level helpers, `git()` wrapper, `load_json`/`write_json` preserving trailing newlines, single quotes, 120 columns). Update the module docstring's subcommand list. Regenerate this repository's `REVIEWS.md`. |
| 1c | high | opus | none | Tests in `scripts/tests/test_review_tracking.py`, following its existing fixture repos: a source fixture with signed-commit verification stubbed out, covering full-mark import, partial-mark-only source not imported, earliest review preferred, lagging copy imported from history, already-marked target untouched, stale target mark not replaced before prune, out-of-scope target file ignored, nothing ever written to any `.weaudit` file or a human reviewer's sidecar, `prune` dropping a stale import and removing an emptied imports file, native mark superseding an import, `import-exclude` preventing and removing an import, self-import refused, `stamp` ignoring the imports file, `next` not offering an imported file, `status` counting imports, and the `Source` column. |
| 1d | medium | sonnet | none | Document `import` in `docs/code-review-tracking.md`: a new subsection under "Steady state" covering what is imported, why imports live in a sidecar-shaped file weAudit never reads (with the weAudit behaviour from open question 1 that ruled out a `.weaudit` file), rejecting an import with `import-exclude`, the provenance record, why verification happens at import time (amplification), and a rewrite of the "prune only removes marks" argument so it is scoped to prune and a parallel argument covers import. Update the subcommand list near the top of the file. |

### Phase 2: review tracking CI template and rollout

Planning effort: medium. The pattern is established; the work is
reconciling four copies.

Create `templates/review-tracking/` holding `prune-reviews.yml`,
`ci-prune-reviews.sh` and `review-tracking.sh`, with a README in
the shape of the other template directories. Each must be copyable
byte-for-byte, so that the copies are themselves importable:

* The default branch comes from
  `${{ github.event.repository.default_branch }}` in the workflow
  (including the ref guard) and is passed to the script in the
  environment, instead of being written in.
* The script takes actions' landing retry and `git status
  --porcelain` check, which are improvements the other three lack.
* The script runs `prune`, then `import`, and commits both with a
  message that names both (`Prune and import review marks.`).
* The workflow gains a daily `schedule` trigger (open question 2),
  a `timeout-minutes`, clones this repository with full history
  (it is 16 MB) instead of `--depth 1`, and installs or locates
  gitsign (open question 5).
* The checkout token follows open question 6.
* The comments are rewritten for the template rather than
  inherited from ryll's, and link to
  `docs/code-review-tracking.md` by absolute URL.

Replace this repository's own `prune-reviews.yml` and
`ci-prune-reviews.sh` with copies. Its `review-tracking.sh` stays
different -- `docs/code-review-tracking.md` step 5 explains why it
must not search for a sibling clone -- and the README says so.

Then, one pull request per repository, replace the copies in
actions, hunkydory, kerbside and ryll, and check each repository's
first scheduled run. Update step 7 of "Adopting a repository" to
point at the template, and the `review-tracking-adoption` skill to
match.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | Survey and report only: is gitsign installable on the `[self-hosted, static]` runners (check the image definitions in shakenfist/images and any existing gitsign use in the fleet), and is `DEPENDENCIES_TOKEN` an organisation secret visible to actions, hunkydory, kerbside and ryll (`gh secret list --org shakenfist` and per repo). Settles open questions 5 and 6. |
| 2b | high | opus | none | Write `templates/review-tracking/` as the phase 2 section describes, starting from actions' `tools/ci-prune-reviews.sh` and ryll's `.github/workflows/prune-reviews.yml`, and replace this repository's two copies. Run `actionlint` and `shellcheck` via `pre-commit run --all-files`. |
| 2c | medium | sonnet | none | Update `docs/code-review-tracking.md` step 7 and the steady-state description of the workflow, and `.claude/skills/review-tracking-adoption/SKILL.md`, to point at the template. |
| 2d | medium | sonnet | worktree | In each of actions, hunkydory, kerbside and ryll, one branch and pull request per repository replacing the three files with byte-identical copies of the template; confirm with `git hash-object` that each copy matches. Do not merge. |

**Outcome.** All four rollout pull requests merged on 2026-09-28,
and each repository's copies match the template. Two things came up
that the steps above did not anticipate. actions carried a test
(`PruneGuardTest`) asserting the old workflow's literal
`refs/heads/main` guard, updated in its rollout pull request. And
hunkydory had never done adoption step 8: its expensive workflows
ran in full on review-only changes, and since `Build, test and
lint`, `Pre-commit hooks` and `Analyze` are required checks there,
its rollout pull request added a job-level `check_paths` skip rather
than a trigger-level `paths-ignore`. Step 8 and the adoption skill
still describe only the trigger-level form, and phase 3 picks that
up.

### Phase 3: template convergence

Planning effort: high. Each template needs a judgement about which
kind of drift it has, and some of the answers change the template
every other repository receives.

For each template directory under `templates/` with a copy in an
adopted repository, classify the difference between template and
copy into the four kinds in "Situation", and act:

* **Parameters** are replaced with values the runtime supplies
  (`${{ github.repository }}` for renovate's autodiscover filter)
  where that is possible, so the file can be copied verbatim. A
  parameter that cannot be removed stays, and the README says the
  copy is not expected to be importable.
* **Lagging copies** are re-synchronised.
* **Local improvements** are upstreamed into the template, then
  every adopted copy re-synchronised -- including in repositories
  outside the review-tracking set when `standards-alignment` next
  runs there; this phase does not chase them.
* **Genuinely per-repository files** are recorded as such in the
  template's README, so that nobody spends effort converging them.

Record the classification as a table in this section: template,
repositories, kind, action taken. The survey in "Situation" is the
starting point but was taken from local clones, so re-measure.

**What the survey found (step 3a, re-run 2026-09-30).** The
2026-09-24 survey had drifted in six days. That is the argument
for this phase in miniature: copies change under the plan as well
as under the template. Re-measured against the default branches of
all five repositories:

* **5 of 29 copies are byte-identical, down from 6.** The
  denominator excludes `review-tracking` (phase 2 made all 12 of
  its copies identical) and `release-automation` (per-repository;
  counting it gives 5 of 34).
  * pr-re-review in kerbside and ryll was re-synchronised by other
    work on 2026-09-27: +2.
  * Template commit 488a2b0 (2026-09-25) added a comment to
    `export-repo-config.yml`, so the kerbside and ryll copies now
    lag: -2.
  * hunkydory#57 gave hunkydory's `codeql-analysis.yml` a job-level
    review-only skip, so it no longer matches: -1.
* **Four claims in the earlier table were false by 2026-09-30:**
  * actions' `pr-re-review.yml` already has
    `persist-credentials: false`. Its only divergence is two
    comments that say `pr-auto-review.yml` is "alongside this
    file".
  * kerbside's and ryll's `pr-retest.yml` already have the bot
    guard, and all four copies carry the current template body.
    What remains is the dispatched-workflow parameter.
  * The template's `renovate.json` no longer says "Automatically
    merge" (fixed in 488a2b0). The hunkydory and ryll copies still
    do.
  * `export-repo-config.yml` is identical in only one of three
    repositories, not three.
* **Four claims still hold:**
  * The mermaid-lint copies in kerbside and ryll lag.
  * actions' `renovate.yml` has `timeout-minutes` and
    `concurrency` that the template lacks.
  * kerbside's and ryll's CodeQL copies still check out `HEAD^2`.
  * kerbside's `pin-indirect-dependencies.yml` adds apt packages.
* **Three things the plan did not know:**
  * kerbside's `pr-retest.yml` notes that dispatching
    `functional-tests.yml` runs the merge tier, not the lanes that
    gate a pull request.
  * ryll's CodeQL cancels in-progress push and scheduled runs as
    well as pull request runs.
  * actions' CodeQL `paths-ignore` lacks the review paths, so it
    misses adoption step 8.
* **Decision 3 as first written counted only the five
  review-tracking repositories**, which made `ci.yml` look like the
  majority default; across the fleet it is `functional-tests.yml`.
* **Required checks:** only hunkydory has status checks CodeQL
  would affect (`Analyze`). kerbside and ryll require only their
  merge-queue gates; actions and this repository require none.
* **Audit criteria:** none of those under `scripts/audit/checks/`
  inspects anything the actions below change. `CiReviewAutomation`
  does require pr-re-review's `rev-parse HEAD^2` confirm step and
  the fork gates on both review automation files, and the template
  change keeps both.
* **Step 8 of "Adopting a repository"** and the
  `review-tracking-adoption` skill describe only the trigger-level
  `paths-ignore`. Both say a skipped required check "sits expected
  forever", which is true of a workflow that never starts but not
  of a job skipped by `if:`, which satisfies the check. Neither
  offers the job-level skip that hunkydory, kerbside and actions
  already use for required checks.

This section's table and decisions are corrected to match. The
"Situation" measurements are left as the 2026-09-24 baseline that
phase 4 re-measures against.

**Classification and actions**, in order of copies gained per unit
of work:

| Order | Template | Kind | Action | Copies gained |
|-------|----------|------|--------|---------------|
| 1 | `mermaid-lint` (`.sh` and `.yml`) | lagging | Re-sync kerbside and ryll verbatim; no template change | 4 |
| 2 | `export-repo-config.yml` | lagging | Re-sync kerbside and ryll verbatim; the template README says actions' file at that path is the reusable workflow, not a copy (likewise `issue-link-check.yml`) | 2 |
| 3 | `renovate/renovate.yml` | parameter, local improvement | Filter becomes `${{ github.repository }}`; upstream actions' `timeout-minutes` and `concurrency`; re-sync all four and this repository | 4 |
| 4 | `ci-review-automation/pr-retest.yml` | parameter | Dispatch `${{ vars.RETEST_WORKFLOW \|\| 'functional-tests.yml' }}` with neutral wording; re-sync all four and this repository; actions, development, hunkydory and ryll set `RETEST_WORKFLOW=ci.yml` | 4 |
| 5 | `ci-review-automation/pr-re-review.yml` | parameter (comment only) | Neutral wording for where `pr-auto-review.yml` lives; re-sync actions | 1 |
| 6 | `codeql/codeql-analysis.yml` | local improvement, lagging | Job-level review-only skip (decision 2) and the rest of decision 2; re-sync hunkydory and ryll; kerbside and actions stay per-repository | 2 |
| 7 | `pin-indirect-dependencies/pin-indirect-dependencies.yml` | parameter, local improvement | `{{PROJECT_NAME}}` goes: the checkout moves to the workspace root (not `${{ github.event.repository.name }}`, which is empty on the daily `schedule` run); extra apt packages move to a tracked file the workflow reads if present; re-sync kerbside | 1 |
| -- | `release-automation/*`, `renovate/renovate.json` | per-repo | Record as per-repo in the READMEs; upstream kerbside's `vm` sign-tag label; fix the "Automatically merge" description in the hunkydory and ryll copies | 0 |

That takes the identical count from 5 to 23 of 29.

**Decisions.**

1. **The denominator is 29.** `review-tracking` is phase 2's and
   already converged; `release-automation` is per-repository.
   Phase 4 reports both totals.
2. **CodeQL takes hunkydory's job-level skip.** This settles the
   earlier "decision needed before item 5". The skip works whatever
   a repository requires, is proven in hunkydory, and a trigger-level
   filter cannot be safe in a template some copies have as a
   required check. Around it:
   * the filter also excludes `docs/**`, as three of four copies do;
   * a fork guard;
   * actions' pull-request-only `cancel-in-progress`, which fixes
     ryll cancelling push and scheduled runs;
   * `timeout-minutes`;
   * `branches: [main, develop]`, which names branches but stays
     identical across the fleet, the mermaid-lint precedent;
   * the obsolete `HEAD^2` checkout goes.

   kerbside pins `languages` because autobuild fails on its C file,
   and actions runs a language matrix. Both stay documented
   per-repository copies rather than growing a parameter for two
   repositories.
3. **pr-retest reads its target from a repository variable.** The
   default is `functional-tests.yml`: it is the fleet majority (nine
   repositories, against six on `ci.yml` and one on
   `unit-tests.yml`) and what the template dispatched before, so most
   repositories are unaffected on their next sync. The variable is
   set to `ci.yml` in actions, development, hunkydory and ryll; each
   is a change to that repository's settings made by Michael, and
   development's must be set before this phase's pull request merges.
   kerbside needs none. kerbside's caveat -- the dispatched workflow
   is not what gates a pull request there -- is a design question
   about the retest bot, recorded as future work rather than solved
   here.
4. **This repository's own copies converge with the fleet's**
   where a template change makes that possible: renovate.yml,
   pr-retest.yml and codeql-analysis.yml.
5. **Step 8 and the adoption skill document the job-level skip**
   as the answer for a required check, and correct the "sits
   expected forever" wording. actions' CodeQL gains the review paths
   in its re-sync pull request, closing its step 8 gap.
6. **Pull requests and review.** The template changes land in this
   repository first, as one pull request with one commit per
   template. The copies follow as one pull request per repository
   after it merges, so that each copy matches what landed here. A
   copy is only importable once the changed template has been
   re-reviewed and stamped here, so the re-review is part of the
   phase, not an afterthought.

The decision most open to argument is 2's handling of kerbside and
actions. A `languages` parameter (`${{ vars.CODEQL_LANGUAGES }}`,
empty meaning autodetect) would make kerbside's copy importable
too, at the cost of every repository's CodeQL depending on an
unset variable behaving like an absent input. That is not verified,
and verifying it on a security scanner is the wrong place to learn
it.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | Done 2026-09-30; the findings are above. |
| 3b | medium | sonnet | none | Items 1, 2 (README only), 3, 5 and 7 and the per-repo README notes. Per template, one commit in this repository: change the template as the table says, update its README, and where this repository carries its own copy (`.github/workflows/renovate.yml`) re-sync it. Keep the `CiReviewAutomation` properties (`rev-parse HEAD^2` confirm step, fork gates; see `scripts/audit/checks/ci_workflows.py:932`). For item 7, the apt list is a tracked `tools/pin-indirect-dependencies-apt.txt`, read only if present. Run `pre-commit run --all-files` and `python3 scripts/audit-check.py --repo-path . --repo-name development`. |
| 3c | high | opus | none | Items 4 and 6: pr-retest's `vars.RETEST_WORKFLOW` and the CodeQL template of decision 2, starting from hunkydory's `codeql-analysis.yml` (origin/develop, commit cd098f5) and actions' fork guard and concurrency. Walk every event (review-only pull request, code pull request, fork pull request, push, schedule, dispatch) and state each job's outcome in the commit message. Re-sync this repository's copies. Keep `test_registry.py`'s ci-review-automation tests passing. |
| 3d | medium | sonnet | none | Rewrite step 8 of "Adopting a repository" in `docs/code-review-tracking.md` and steps 2-3 of `.claude/skills/review-tracking-adoption/SKILL.md` to give the job-level `check_paths` skip as the answer for a required check, with hunkydory's shape as the worked example. |
| 3e | medium | sonnet | worktree | After the 3b-3d pull request merges: one branch and pull request each in actions, hunkydory, kerbside and ryll re-syncing that repository's copies from the table, verified with `git hash-object` (for kerbside's pin-indirect-dependencies that is the workflow *and* the new `tools/pin-indirect-dependencies-apt.sh`, plus a `tools/pin-indirect-dependencies-apt.txt` carrying its mysqlclient build dependencies), plus the renovate.json description fix (hunkydory, ryll) and the review paths in actions' CodeQL. Do not push or open pull requests until asked. `RETEST_WORKFLOW=ci.yml` is set by Michael in actions, hunkydory and ryll before each pull request merges (development's is set before the 3b-3d pull request merges); kerbside needs none. |

**Risks.**

* *A converged template breaks a copy that relied on its local
  difference.* kerbside's CodeQL and pr-retest are the known cases,
  and both are handled above. Each 3e pull request runs that
  repository's own CI, and the management session reads the diff
  of every copy against its old version before opening it.
* *The CodeQL skip hides a real change.* The filter fails open
  (`|| 'true'`, `!cancelled()`), and step 3c's event walk is the
  check.
* *The imports this phase is for do not arrive.* A re-synchronised
  copy imports only after the new template is reviewed here.
  Michael re-reviews the changed templates before 3e opens its
  pull requests; phase 4 counts the result.

**Definition of done.**

* For every row in the table above, `git hash-object` of each copy
  on each repository's default branch equals the template's, or the
  template's README names that copy as per-repository. A script run
  by phase 4 checks it; step 3a's survey is its first draft.
* `grep -n 'sits "expected" forever' docs/code-review-tracking.md
  .claude/skills/review-tracking-adoption/SKILL.md` finds nothing,
  and both describe `check_paths`.
* A review-only pull request in hunkydory merges with `Analyze`
  reported as skipped.
* `pre-commit run --all-files` passes here, and `audit-check.py`
  reports no new failure for any of the five repositories.

**Back brief.** Before 3c edits anything, the management session
agrees the CodeQL template's event table with Michael. It is the
one change here that alters a security scanner's coverage in every
copy.

### Phase 4: verification

Planning effort: medium.

Once phases 2 and 3 have merged in every adopted repository, and
the converged templates have been re-reviewed and stamped here:

* Record the imports each repository's first runs made, and the
  per-repository counts in the "Situation" table re-measured, so
  the plan says what the work actually bought.
* Pick one imported mark in each repository and verify it by hand
  from the review account, following the procedure the phase 1
  documentation describes, to confirm the documentation is
  sufficient.
* Confirm `prune` removes an imported mark when the target file
  changes, by watching it happen on the next ordinary change to an
  imported file.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Collect the `prune-reviews` run logs since phase 2 landed in each adopted repository (`ci-status`), extract the import lines, re-run the "Situation" measurement against fresh clones, and report. |

### Phase 5: push audit

Run `PUSH-AUDIT.md` over the merge commits recorded for phases 1
to 4 in this repository, and cite the per-repository audits for
the pull requests that landed elsewhere. Findings land as their
own pull request.

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

**In this repository.** The phase sections above carry the step
tables. Two facts every brief in this plan depends on: `stamp`
stamps files with partial marks as well as full ones
(`scripts/review-tracking.py`, `cmd_stamp`), so a sidecar entry
alone never proves a full review; and `review-tracking-tests`
compares every row of this repository's `REVIEWS.md` against a
fresh render, so any change to `render_reviews_md()` regenerates
the file in the same commit.

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
* `prune` still only ever removes marks, and `import` is the only
  code path by which a mark is added without a human. It writes
  only to `.vscode/imports.weaudit-shas.json`; no automated
  process writes to any `.weaudit` file or a reviewer's sidecar.
* Every imported mark names a signed commit in this repository
  that introduced a full-file review of the same blob, or the
  run that imported it said on stderr that it could not verify.
* The three review tracking CI files are byte-identical across the
  four adopted repositories (and this repository, for the two it
  shares).
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

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

...

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

...

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->

**In this repository.** Future work, as of writing:

* **A `verify` subcommand** that walks every mark in a repository
  to the signed commit that introduced its stamp -- following an
  imported mark's provenance into this repository -- and checks
  each signature. Today verification of native marks is a manual
  procedure too; this plan does not change that, but import makes
  it more worth automating.
* **Reusable workflows.** Moving templates such as
  `export-repo-config.yml` to `workflow_call` workflows would
  remove the copies altogether, leaving small identical callers.
  It changes the trust model (a caller pinned by SHA, bumped by
  Renovate) and is a bigger decision than this plan should make.
* **A template conformance criterion.** Phase 3 converges the
  copies once; without enforcement they will drift again. Revisit
  after phase 4 shows whether import makes the drift visible
  enough on its own.
* **Shared blocks.** See the mission statement for why they are
  out of scope.
* **Upgrading unverified imports.** An entry imported with
  `"verified": false` (gitsign absent, or `--no-verify`) is never
  re-checked. If phase 2 finds the runners cannot reach Rekor, those
  entries stay unverified after the runners are fixed; `import`
  should re-verify them when it can, and drop any that fail.
* **A failed verification hides later attestations.** The history
  walk records only the earliest commit that attests to a blob. If
  that commit's signature does not verify, the blob is skipped even
  when a later, signed commit attests to the same blob. Rare, since
  review commits are signed, but falling back to the next
  attestation would be more correct.
  The same applies when the earliest attestation is by a reviewer
  with no entry in `REVIEWER_IDENTITIES`.
* **Split `scripts/review-tracking.py`.** Phase 1 took it from 636
  to about 1,230 lines, past the ~800-line mark in
  `templates/shared-blocks/source-file-size.md` that this repository
  publishes to the fleet. The file is under whole-file review, so
  every change to it costs a re-read of all of it. Raised by the
  automated review of #177 and deliberately not done there, where
  it would have buried the logic change in the diff.
* **Automate the gitsign bump.** `GITSIGN_VERSION` and
  `GITSIGN_SHA256` in `templates/review-tracking/ci-prune-reviews.sh`
  are bumped by hand. A Renovate regex manager can bump the version
  but not the binary's digest, so a half-automated bump would fail
  the checksum check; this wants either a Renovate post-upgrade
  task or a small scheduled job. Raised by the automated review of
  #186.
* **Stagger the daily run.** Every adopted repository runs the
  template's `23 4 * * *` schedule at the same minute on the shared
  static runners. They queue rather than fail, so this is left
  alone unless phase 4 shows the queue matters; a per-repository
  cron would break byte-identity for that file. Raised by the
  automated review of #186.
* **What the retest bot should dispatch.** kerbside's
  `pr-retest.yml` notes that dispatching `functional-tests.yml` runs
  the merge tier, not the lanes that gate a pull request there.
  Phase 3 makes the target a repository variable; whether the bot
  should instead re-run a pull request's own gating lanes is a
  change to the review automation template, not to convergence.

Related issues: shakenfist/development#173 is this repository's
own review-coverage issue; import does not affect it, because this
repository does not import from itself.
