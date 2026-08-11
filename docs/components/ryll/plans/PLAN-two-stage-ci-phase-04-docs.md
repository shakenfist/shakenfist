# Two-stage CI phase 4: documentation

Phase 4 of [PLAN-two-stage-ci.md](/components/ryll/plans/PLAN-two-stage-ci/). Phases
1 to 3 built the two tiers, added the Windows cross-check, and
turned on the merge queue. This phase makes the result
discoverable: what runs where, how to read a failure that no
longer appears on the pull request, and which invariants an
agent editing `ci.yml` has to preserve.

## A new page, not a longer section

The master plan left this open ("`docs/development.md`, or a new
`docs/ci.md` if the CI material has outgrown it"). It has
outgrown it. The material that now needs writing down — two
tiers, three gate jobs, the `check_paths` fast path, queue
mechanics and ejections, artifact provenance, the ruleset and
its bypass actor, the `prune-reviews` bot — is a reference, and
most of it is of no interest to somebody who just wants to build
ryll.

So `docs/ci.md` is new and carries the reference, including the
workflow inventory table that used to live in
`development.md`. `development.md` keeps a short summary aimed
at a contributor: the two tiers exist, merging enqueues rather
than merges, the merge tier reports on the queue's run, and the
Linux commands CI runs are the local ones. It links out for the
rest.

## What the pass found stale

`AGENTS.md` still claimed review-only changes skip CI "via
`paths-ignore`" and that "the supply-chain content scanners
still run on them". Phase 1 replaced the `paths-ignore` with the
`check_paths` job and folded the scanners into `ci.yml`, so both
halves were wrong: the scanners skip too. `codeql-analysis.yml`
does still use `paths-ignore`, which is presumably where the
claim survived from.

`docs/development.md` said the merge tier runs "on
`merge_group`", which is true but says nothing to a reader who
has not met a merge queue.

`ARCHITECTURE.md` describes CI only as it stood at phase 7 of
the web-frontend work, as a record of that phase. It is not a
current-state description and was left alone.

`docs/releasing.md` needed nothing: release artifacts come from
`release.yml` on a tag and never came from a push to `develop`.

## The merge-queue material worth writing down

Three things are non-obvious enough to be the reason this page
exists:

1. **Merge-tier failures are invisible on the pull request.**
   They ran against `gh-readonly-queue/develop/pr-N-<sha>`, so
   the pull request shows only a timeline event saying it was
   removed from the queue. You have to go to the merge group's
   run in the Actions tab.
2. **Skipped counts as success, deliberately.** Both gates map
   each dependency to "success or skipped", and the gate that
   does not apply to an event is itself skipped. That is what
   lets `Can merge` be required without blocking pull requests,
   and what lets review-only changes through without running a
   single build. It reads like a bug if you meet it cold.
3. **A job nothing needs is not required.** The ruleset names
   three checks, none of which is a real job. Adding a job to
   `ci.yml` without adding it to a gate's `needs` produces a job
   that can fail without blocking a merge.

## Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 4a | medium | opus | none | Write `docs/ci.md`; move the workflow inventory into it; trim `development.md`'s CI section to a summary plus a link; add the page to `docs/index.md`. |
| 4b | low | opus | none | Fix the stale `paths-ignore` claim in `AGENTS.md` and add a "two CI tiers and the merge queue" section to its CI conventions, covering gate membership, the shared `if:` shape, skipped-as-success, `workflow_dispatch` running both tiers, and not pushing to `develop`. |
| 4c | low | opus | none | Annotate `PLAN-ci-platform-matrix.md`: its future runtime smokes are merge-tier work, preferably as steps in the existing `build` matrix, and any new job needs a gate. |
| 4d | low | opus | none | Update the master plan's phase table and open questions, and `docs/plans/index.md`. |

Implementation ran in the management session rather than in
sub-agents, contrary to the master plan's execution model: the
work is prose about material the session had just built and
measured, and the plan's own guidance rates docs work as
well-briefed and bounded.

## Validation

* `pre-commit run --all-files` passes.
* Every relative link in the new and edited pages resolves
  (`docs/ci.md` → `plans/…`, `development.md`, `releasing.md`;
  `docs/plans/PLAN-ci-platform-matrix.md` → `../ci.md`).
* The job tables in `docs/ci.md` match `ci.yml` job for job,
  including runner labels.
* Merging this branch also closes the last phase 3 validation
  gap. Phase 3 could not prove that `prune-reviews` pushes
  successfully *under* the active ruleset: the run after the
  first queued merge found nothing to prune and exited before
  `git push`. This branch changes `AGENTS.md`, which is a
  reviewed file, so the post-merge prune has real work to do and
  exercises the bot's push through the team bypass actor for
  real.

## Follow-ups this phase does not do

* A review-only pull request (touching only `REVIEWS.md`) has
  not been exercised since the checks became required. The path
  is the same one this branch's `check_paths` job takes, but the
  gate-passes-on-all-skipped case specifically has only been
  observed while the checks were advisory.
* `shakenfist/actions`' `export-repo-config` workflow exports
  `"bypass_actors": []` even when a ruleset has one — kerbside's
  export shows the same, so the audit trail is missing the
  bypass fleet-wide. Worth raising there.
* `tools/ci-prune-reviews.sh` pushes to `develop` whatever ref
  it was run against. ryll now guards the workflow, but the
  script mirrors one in shakenfist/development and kerbside
  carries the same hazard.
* Nothing in the repository runs actionlint, despite
  `.github/actionlint.yaml` existing. Raised twice during this
  work and by the automated reviewer on PR #257; still open, and
  possibly a fleet-wide consistency-audit item.
