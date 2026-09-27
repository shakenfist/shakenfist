# Code review tracking

This documents the conventions for tracking whole-codebase human
review: periodically reading a repository file by file to catch the
inconsistencies in style, architecture, and approach that creep in
over years of incremental change. It is complementary to both PR
review (which only ever examines deltas) and the consistency audits
(which catch mechanical drift like missing files -- see `docs/audits/`).
The full design rationale is in
`docs/plans/PLAN-code-review-tracking.md`.

The automation lives in this repository:
`scripts/review-tracking.py` (subcommands `stamp`, `prune`, `regen`,
`next`, `status`, `scope-orphans`, and `import`), with tests in
`scripts/tests/test_review_tracking.py`. In a developer's clone it is
run by hand -- deliberately not from git hooks. An earlier iteration
wired `stamp` and `prune` into the pre-commit, post-merge,
post-checkout, and post-rewrite hooks, but review state silently
changing in the middle of unrelated git operations proved more
confusing than helpful. That objection does not apply to CI running
against a repository's own default branch, so in steady state four
subcommands also run automatically: `prune` then `import`, in that
order, from an adopting repo's `prune-reviews` workflow -- copied
from `templates/review-tracking/`, see "Adopting a repository"
below -- on every push to the default branch and once a day, and
`status` and `scope-orphans` from the consistency audit's
`review-coverage` and `review-scope-completeness` checks (see
"Steady state" below).
Target repositories carry a thin wrapper (for example ryll's
`tools/review-tracking.sh`) that locates a local clone of this
repository and passes through to the script.

## The pieces

* **weAudit** (`trailofbits.weaudit` on the VSCode marketplace)
  provides the in-editor workflow: mark files or regions as
  reviewed, attach notes and findings, see progress. Its state
  lives in `.vscode/<username>.weaudit`, one JSON file per
  reviewer, committed to the repository being reviewed.
* **Signed git commits** of that state file provide attestation.
  The signature covers the commit tree, which contains both the
  review mark and the exact content of the reviewed file -- so
  "who reviewed what, when, at which content version" needs
  nothing beyond git.
* **A sidecar file** (`.vscode/<username>.weaudit-shas.json`,
  written by the `stamp` subcommand, never by weAudit) records the
  blob SHA and date of each review so staleness is a mechanical
  check, and a generated **`REVIEWS.md`** surfaces the review state
  to people who do not know to look in `.vscode/`.
* **An imports file** (`.vscode/imports.weaudit-shas.json`, written
  by the `import` subcommand, never by weAudit or a human) records
  full-file reviews carried in from this repository's own review
  history rather than made in this clone -- see "Importing reviews
  from this repository" under "Steady state".
* **A scope config** (`.vscode/review-scope.toml`) defines which
  files count as reviewable: `include` and `exclude` lists of
  fnmatch patterns matched against repo-relative paths (`*` matches
  across directory separators; an empty or absent `include` means
  all tracked files). An `exclude` entry beginning with `!` is a
  re-include, so a directory can be excluded except for one file
  without naming every other file by hand; it cannot put the
  tracking machinery (`.vscode/*`, `REVIEWS.md`) back, which is
  always excluded, since those files describe the reviews and can
  never attest to themselves. Scope should cover the
  executable artifacts (source code in every language the repo
  uses, plus shell scripts), the declarative configuration that is
  executable in practice (CI workflows, container and deployment
  manifests, Ansible playbooks, tool and packaging config -- the
  YAML that decides what runs, with which permissions, against
  which secrets, and which is where a supply-chain change is most
  likely to hide), and the prose that documents them (READMEs,
  `ARCHITECTURE`/`AGENTS`, and `docs/` guides): prose drifts out of
  sync just as quietly as code and benefits from the same periodic
  re-reading. Generated and vendored code should be
  excluded, as should ephemeral working documents like a
  `docs/plans/` archive -- point-in-time records of intended work,
  not living artifacts, and numerous enough to swamp the queue.
  Whether unit tests are in scope is a per-repo decision. An
  optional `import-exclude` list names files that must never be
  marked reviewed by import (see "Importing reviews from this
  repository" under "Steady state").

## Adopting a repository

1. Ensure the review state files are committable. If `.gitignore`
   excludes `.vscode/`, add exceptions:

   ```
   !.vscode/*.weaudit
   !.vscode/*.weaudit-shas.json
   !.vscode/review-scope.toml
   ```

   The ignore rule must then be `.vscode/*` rather than `.vscode/`:
   git cannot re-include a file whose parent directory is excluded,
   so exceptions under a bare `.vscode/` do nothing. The
   `prune-reviews` workflow fails the run if the imports file it
   writes is ignored, rather than importing the same reviews every
   day and committing none of them.

   Exceptions rather than un-ignoring `.vscode/` wholesale, because
   weAudit also writes `.vscode/.weauditdaylog`, a log of which files
   each session opened. Nothing reads it, it is not attestation, and
   it churns every session -- so if the repository does *not* ignore
   `.vscode/` it needs the opposite entry, or the day log rides along
   in every review commit and triggers the expensive CI lane that
   step 8's `paths-ignore` block exists to skip:

   ```
   .vscode/.weauditdaylog
   ```

   Then, *if the repository runs a pre-commit hook that rewrites the
   files it is given* -- `end-of-file-fixer`, `trailing-whitespace`
   and friends -- exempt the review marks from those hooks:

   ```yaml
   - id: end-of-file-fixer
     exclude: ^\.vscode/.*\.weaudit
   - id: trailing-whitespace
     exclude: ^\.vscode/.*\.weaudit
   ```

   Without it, `end-of-file-fixer` rewrites the weAudit file on every
   `pre-commit run --all-files`, because the generator emits no
   trailing newline -- and reports a failure that cannot usefully be
   fixed, since committing the newline only means the next regen drops
   it again. The pattern deliberately covers the `.weaudit-shas.json`
   sidecar as well as the weAudit file itself.

   Scope the exclude to those hooks rather than putting it at the top
   level of `.pre-commit-config.yaml`. A top-level exclude is shorter,
   but it hides the review marks from *every* hook, including content
   scanners -- and review notes are prose, so a blanket exclude stops
   gitleaks and the bidi/zero-width check from reading exactly the
   kind of human-written text a secret or a smuggled character would
   land in. This is the same reasoning that keeps content scanners out
   of the `paths-ignore` block in step 8; it applies just as much to a
   local hook as to a CI workflow. A repository that runs no rewriting
   hook at all (ryll, today) needs no exclude and should not add one.

   The consistency audit's `review-marks-pre-commit` check enforces
   this, and reports not applicable where no rewriting hook is
   configured.

2. Ensure commit signing is configured for the clone(s) reviews
   will be made from (see below).

3. Protect the branch that will carry review state (normally the
   default branch) from history rewrites: a repository ruleset
   with the `non_fast_forward` and `deletion` rules is the
   minimum. For example:

   ```
   gh api -X POST repos/shakenfist/<repo>/rulesets --input - <<'EOF'
   {
     "name": "Protect default branch history",
     "target": "branch",
     "enforcement": "active",
     "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"],
                                 "exclude": []}},
     "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]
   }
   EOF
   ```

   (`~DEFAULT_BRANCH` tracks whatever the default branch is, so the
   same command works on every repo.)

   (shakenfist/shakenfist's "Develop branch" ruleset is a stricter
   superset -- PRs, merge queue, status checks -- and also
   satisfies this.)

4. Write `.vscode/review-scope.toml`. Cover code, YAML
   configuration, and docs across the languages the repo uses, and
   exclude generated, vendored, and ephemeral files. For example
   (ryll's config; adjust the language patterns to the repo):

   ```toml
   # Source in every language the repo uses, plus shell scripts,
   # protobuf definitions, the YAML that configures CI and
   # deployment, and the Markdown documentation.
   include = ['*.rs', '*.sh', '*.py', '*.pyi', '*.js', '*.proto', '*.md', '*.yml', '*.yaml']
   # docs/plans/ is a point-in-time archive, not living artifacts;
   # protoc output is generated, so reviewing it would attest to the
   # generator rather than to anything a human wrote.
   exclude = [
       'docs/plans/*',
       '*_pb2.py',
       '*_pb2.pyi',
       '*_pb2_grpc.py',
       '*_pb2_grpc.pyi',
   ]
   ```

   An empty `include` means all tracked files, which is a fine
   starting point for a small repo -- but then lean on `exclude` to
   keep generated code (`*_pb2.py` and friends), vendored trees
   (`vendor/*`, minified third-party JavaScript), and ephemeral
   archives out of the queue.

   Whichever you choose, every tracked file has to end up either in
   scope or named by an `exclude` entry: the
   `review-scope-completeness` audit fails on a file that is out of
   scope only because `include` does not name it. Enumerating
   extensions and leaving `include` empty are both compliant, and
   they differ in which way a new file type fails. With an empty
   `include` it silently joins the review queue and somebody excludes
   it if that was wrong; with a list it fails the audit and somebody
   decides. Run `review-tracking.py scope-orphans` after writing the
   config to see which files it leaves out.

   Prefix an `exclude` entry with `!` to re-include something the
   pattern above it takes away:

   ```toml
   exclude = [
       'docs/audits/*',
       '!docs/audits/README.md',
   ]
   ```

   Reach for this when a directory is excluded because its files are
   machine-rewritten and one file in it is not. Prefer it to naming
   the keepers individually, which is a list that has to be edited on
   the day a file changes category and therefore will not be. Do not
   reach for it to carve out several files from a directory that is
   excluded for a reason those files will grow into -- a spec awaiting
   its check has no generated table today and will have one the day
   the check lands, so re-including it buys a review that the next
   audit run invalidates.

   Widening scope in a repo that has already been adopted needs no
   migration -- the newly in-scope files simply have no review mark,
   so they enter the `next` queue like any other unreviewed file.
   Expect the `review-coverage` audit to open an issue until the
   backlog has been worked off, since a repo's worth of workflow
   files arrives at once.

5. Copy `tools/review-tracking.sh` byte-for-byte from
   `templates/review-tracking/review-tracking.sh` -- verify with
   `git hash-object` against the template rather than trusting the
   copy. It is the thin wrapper: it locates a local clone of this
   repository -- `$SHAKENFIST_DEVELOPMENT`, then a sibling
   `../development`, then `~/src/shakenfist/development` -- and
   passes its arguments through to `scripts/review-tracking.py`.
   Changes to the wrapper go to the template, not to a repository's
   copy of it.

   This repository is the exception: its own wrapper does not go
   looking, because `scripts/review-tracking.py` is in the tree
   beside it. The search order above would find a *sibling* clone
   and run that one's copy of the script, which is the wrong answer
   in the one repository where the right answer is certain.

6. Bootstrap existing review marks, if any were made before the
   tooling was adopted. A stale pre-existing mark must not be
   blessed: `stamp` records whatever content the file has *now*,
   whether or not that is what was reviewed. So:

   * For each pre-existing mark, check the file is unchanged since
     its signed review commit; unmark any that changed (they are
     stale, and will come back around via `next`).
   * Run the stamp by hand, and correct the recorded dates to the
     true review dates (a fresh stamp records today):

     ```
     ./tools/review-tracking.sh stamp
     # fix dates in .vscode/<user>.weaudit-shas.json if needed
     ./tools/review-tracking.sh regen
     ```

   * Delete any hand-maintained REVIEWS.md; the generated file
     replaces it.
   * Commit the wrapper, corrected weAudit state, sidecar, and
     REVIEWS.md together (signed).

7. Copy `.github/workflows/prune-reviews.yml` and
   `tools/ci-prune-reviews.sh` byte-for-byte from
   `templates/review-tracking/` -- again verify with `git
   hash-object`. This is the steady-state prune-and-import
   automation (see "Steady state" below): it runs `prune` then
   `import` on every push to the default branch, once a day, and on
   manual dispatch, and lands the result as one commit. See
   [templates/review-tracking/README.md](https://github.com/shakenfist/development/blob/main/templates/review-tracking/README.md)
   for what else the workflow needs (the `.gitignore` exception,
   `.vscode/review-scope.toml`, and the `paths-ignore` block from
   step 8) and for gitsign and the development clone it makes. A
   repository whose default-branch ruleset requires a pull request
   needs a `DEPENDENCIES_TOKEN` secret, since GitHub will not accept
   the built-in Actions app as a bypass actor and shakenfist-bot has
   to push with a token instead; a repository without that
   requirement pushes with `GITHUB_TOKEN`. Changes to the workflow
   or script go to the template, not to a repository's copy of them.
   The consistency audit's `review-coverage` check needs no per-repo
   setup -- it notices the scope config and starts checking the repo
   automatically.

8. Teach the repository's build and analysis workflows to ignore
   review-only changes, so a review session (or a bot prune) does
   not burn a CI run on files no build reads:

   ```yaml
   on:
     pull_request:
       branches: [develop]
       paths-ignore:
         - 'REVIEWS.md'
         - '.vscode/*.weaudit'
         - '.vscode/*.weaudit-shas.json'
         - '.vscode/review-scope.toml'
   ```

   Apply it to the code-shaped workflows (unit tests, lint, CodeQL,
   functional lanes) but *not* to content scanners like gitleaks or
   the bidi/zero-width check: review notes are prose, and prose is a
   place secrets or Unicode smuggling could land. This is only safe
   while no skipped workflow is a required status check -- a skipped
   required check sits "expected" forever and blocks the merge.

   Which workflows count as "code-shaped" differs per project, so
   verifying this step is judgment work rather than a deterministic
   audit: the `review-tracking-adoption` Claude skill (in this
   repository's `.claude/skills/`) carries the verification
   procedure. Re-run it when new workflows land in an adopted repo.

## The review account

Reviews are performed from a dedicated user account on the review
machine, with its own clones of the repositories under review.
This is deliberate isolation: the review clones never contain
in-flight development changes, so the clean-tree rule below holds
structurally rather than by discipline, and there is no risk of
attesting to code mid-edit. Development happens in the primary
account's clones; review marks are only ever made and committed
from the review account.

## Commit signing

Review-state commits must be signed; the simplest way to guarantee
that is to sign all commits made from the review account. The
convention is **gitsign** (Sigstore keyless signing, matching the
Sigstore use in our release automation): the signing certificate
is issued for `mikal@stillhq.com` via GitHub OAuth at commit time,
and every signature is recorded in the Rekor transparency log --
which gives each review attestation an independent public
timestamp as a side effect.

Setup in the review account:

```
git config --global commit.gpgsign true
git config --global gpg.format x509
git config --global gpg.x509.program gitsign
```

Verification, from any account with gitsign installed:

```
gitsign verify \
    --certificate-identity=mikal@stillhq.com \
    --certificate-oidc-issuer=https://github.com/login/oauth \
    <sha>
git log --show-signature -- .vscode/   # with the config above
```

`import` performs the same check, and needs to know which identity
each reviewer signs as: `REVIEWER_IDENTITIES` in
`scripts/review-tracking.py` maps a reviewer name (the `<username>`
of `.vscode/<username>.weaudit`) to their certificate identity.
When a new reviewer starts signing review-state commits here, add
an entry for them; until then `import` cannot verify their reviews
and skips each one with a warning naming them.

Note that GitHub's web UI shows gitsign commits as "Unverified"
(reason `bad_cert`): GitHub cannot validate Fulcio's short-lived
certificates. This is expected -- the trust path is gitsign
verification against Fulcio and Rekor, not GitHub's badge. An
example review attestation: shakenfist/ryll commit `755a3cc`
("review: glz.rs").

## Session discipline

The signed-commit attestation is only as good as the tree it
covers, which imposes three rules:

1. **Mark reviews only on a clean working tree.** If a file has
   uncommitted edits when it is marked reviewed, the committed
   tree will not match what was actually read. The dedicated
   review account makes this structural: its clones never carry
   development edits.
2. **Commit review state at the end of every session**, before
   pulling or rebasing, so the marking commit's tree reflects the
   reviewed state:

   ```
   git add .vscode/*.weaudit*
   git commit    # signed, with a "reviewed N files" style message
   ```

3. **Never rewrite history** on the branch carrying review state
   (enforced by the ruleset above).

A session therefore looks like:

1. `git pull` on a clean tree, then:

   ```
   ./tools/review-tracking.sh prune
   ```

   discards marks for files changed since their review and
   regenerates `REVIEWS.md`. Anything it pruned is a good
   candidate work queue for the session. If VSCode was already
   open, reload the window (or toggle the weAudit tree view) so
   the ticks refresh -- weAudit does not watch its state file for
   external changes. Note the pull is load-bearing, not just
   hygiene: the `prune-reviews` workflow commits prunes to the
   default branch between sessions, and marking reviews on a
   clone that has not picked those up risks a review-state
   commit that conflicts on push. The local prune usually finds
   nothing left to do.
2. Pick a file:

   ```
   ./tools/review-tracking.sh next
   ```

   picks a random unreviewed in-scope file and opens it in VSCode
   (`--no-open` to just print it). A file imported from this
   repository's own review history (see "Importing reviews from
   this repository" under "Steady state") shows no tick in weAudit
   and is not offered here; reviewing it by hand supersedes the
   import.
3. Read it. weAudit's explorer ticks show what is already done;
   Claude Code in the integrated terminal for questions.
4. Mark it reviewed (`weAudit: Mark File as Reviewed`), attach
   findings or notes as needed.
5. Repeat from 2. At the end of the session:

   ```
   ./tools/review-tracking.sh stamp
   git add .vscode/*.weaudit* REVIEWS.md
   git commit
   ```

   The stamp records each newly reviewed file's blob SHA and date
   in the sidecar and regenerates `REVIEWS.md`, printing exactly
   what to `git add`. The (signed) commit that lands contains the
   marks, the stamps, and the regenerated `REVIEWS.md` together.

### Reviewing a file that is out of scope

`stamp` ends with a banner naming any marked file the scope config
excludes, and exits non-zero. It is deliberately the loudest thing
the tool prints, because the mistake is otherwise close to
undetectable from the outside: an out-of-scope review *does* get a
row in the `REVIEWS.md` table, so it looks recorded, but the
coverage count above that table only counts in-scope files and does
not move. `status` cannot see it either, so the `review-coverage`
audit goes on reporting the file as outstanding, and `next` never
offered it in the first place. The reviewer reads a file carefully
and the number they are trying to move stays where it was.

The banner repeats on every run, not just the one that first stamps
the file. A mark noticed once and left alone is precisely the case
that needs saying again, and the second run is when the reviewer is
looking for confirmation that the count moved.

Two ways out, and which one is right is a judgement call:

* If reviewing the file was a mistake, un-mark it in weAudit and
  re-run `stamp`, which drops the stamp along with the mark.
* If the file should have been in scope, widen the include or
  exclude patterns in `.vscode/review-scope.toml` and say why in
  the commit message. The exclusions in that file are argued rather
  than incidental -- each one carries a comment explaining what
  would go wrong if the file were in the queue -- so an addition
  that does not engage with the argument is likely to be reverted
  by whoever wrote it.

## Staleness

A review applies to the file content that was read, not the path:
once the file changes, that review is stale and the file should be
treated as unreviewed. weAudit does not track this -- a stale tick
looks identical to a fresh one.

The tooling makes staleness a mechanical check: `stamp` records
each reviewed file's blob SHA in the sidecar, and `prune` discards
any mark -- whole-file or region -- whose stamped SHA no longer
matches `HEAD`, regenerating `REVIEWS.md` to match. Region marks
are pruned wholesale with the file: line ranges shift as files
change, so a partial review of a changed file is not trusted
either. On the default branch the `prune-reviews` workflow does
this automatically after every push (see "Steady state" below); in
clones, pruning remains part of the session discipline: run it
after every pull (and after any merge or rebase in a clone
carrying review state). See the plan for the full design,
including why the stamps live in a sidecar rather than in
weAudit's own JSON.

Four behaviours worth knowing about:

* Prune compares against whatever `HEAD` currently is -- run it
  with an old branch checked out and it will (correctly, but
  perhaps surprisingly) discard reviews of files that differ
  there. If that was not what you meant, `git restore .vscode/
  REVIEWS.md` puts the state back.
* A stamped entry is never re-stamped while it exists: if a
  reviewed file changes, the only path forward is prune then
  re-review. This is what prevents a stale review being silently
  refreshed at the file's current content. `stamp` reports such a
  file and exits non-zero rather than passing over it, which is the
  last chance anything gets to say so: `stamp` runs by hand and not
  from a hook, so nothing downstream will stop the commit. Until
  this was checked it skipped the file in silence, and because a
  review-only commit is exempt from CI, the mark then survived to
  the default branch where `prune-reviews` deleted it -- discarding
  the review rather than the staleness, which is the wrong half.
* A stamp going stale is not a CI failure. Editing a reviewed
  file is the ordinary case rather than a mistake, and the
  change that does it is often not a change that can
  re-review anything -- a Renovate action bump touching
  stamped workflow files least of all. Staleness is handled
  after the merge instead of gated before it: `prune-reviews`
  discards the mark on the next push to the default branch,
  and the `review-coverage` audit recomputes coverage against
  `HEAD` and raises an issue once the backlog reaches five
  files. This repository briefly asserted the opposite at
  commit time, and every dependency bump touching a stamped
  workflow failed CI until somebody re-stamped by hand.
* When every file in a directory is reviewed, weAudit adds a
  derived *directory* entry to `auditedFiles` alongside the
  per-file entries. The tooling treats these as pure UI state:
  they are never stamped or listed in `REVIEWS.md`, and prune
  removes them when a file inside stops being reviewed (mirroring
  what weAudit itself does when a file is unmarked in its UI).

## Steady state

Once a repository approaches full coverage, the interesting
questions change: reviews go stale as PRs merge, and someone needs
to notice when enough staleness has accumulated to be worth a
session. Two pieces of automation cover this.

One consequence of `REVIEWS.md` being generated is worth stating
before either of them: the header count is a property of the whole
tree, so adding or removing *any* in-scope file changes it -- on
whatever branch happens to do so. That is why a pull request does
not regenerate it. A generated file that every branch has to
rewrite is a merge-conflict hot spot, and the conflicts are
semantic rather than textual: two branches each adding an in-scope
file regenerate to the same header text, merge cleanly, and leave
a count that is wrong by one. Resolving that costs a rebase and a
full CI run for a change that is entirely prose, which on a small
CI cluster is a real tax on forward progress.

Nothing is needed to make this safe. `prune` regenerates
`REVIEWS.md` whether or not it pruned anything, so the
`prune-reviews` workflow corrects a moved count on the next push
to the default branch, in the same commit it would have made
anyway. The `review-coverage` audit does not read the count at
all: `review-tracking.py status` recomputes coverage against
`HEAD`, precisely so that a missed regeneration cannot inflate it.

This repository does assert at commit time
(`review-tracking-tests`) that `REVIEWS.md` is reproducible from
the committed state, because a file that is not is how a missing
stamp sidecar hides -- the count trusts marks rather than stamps,
so a commit that lands the marks and forgets the sidecar reports
the right number while every Date and Blob SHA cell renders as
`-`. That assertion compares every row but skips the count line,
so it catches the sidecar and hand-edited rows without making the
file a hot spot. An adopting repository that copies that hook
inherits the check; one that does not, does not.

**Automatic pruning and importing.** Each adopting repository
carries a `prune-reviews` workflow, copied byte-for-byte from
`templates/review-tracking/` (step 7 of "Adopting a repository"
above), that runs on every push to its default branch, once a day,
and on manual dispatch -- the push is the only event that can
create staleness there, and the daily and dispatch runs are what
pick up new reviews imported from this repository even when the
target repository itself is quiet. It clones this repository for
the script, runs `prune` then `import`, and commits the updated
review state and regenerated `REVIEWS.md` whenever either changed
-- including a regeneration that pruned nothing, which is what
corrects a moved header count -- directly back to that branch as
shakenfist-bot, in one commit, `Prune and import review marks.`.
It lands that commit by regenerating rather than rebasing: each of
up to three attempts fetches the branch, resets to its tip, runs
`prune` and `import` there, and pushes. Everything in the commit is
derived from the tree beneath it, so a merge that lands mid-run
costs a retry, never a conflict, and the retry also prunes the marks
that merge made stale. A concurrency group serialises the
workflow's own runs, and the loop terminates either way:
a repository that can push with the default `GITHUB_TOKEN` never
retriggers the workflow at all, while one whose ruleset forces a
PAT instead -- as ryll's does, since develop requires a pull
request before merging and GitHub will not accept the built-in
Actions app as a bypass actor, so shakenfist-bot pushes with
`DEPENDENCIES_TOKEN` -- retriggers exactly once, into a run that
finds nothing left to do (observed 2026-09-11 04:57:20 UTC, before
ryll carried the merged workflow, `Prune stale review marks.`,
which committed nothing).

The bot's commit is not signed, and does not need to be for the
prune half of it: prune can only *remove* marks, never add or
refresh them ('a stamped entry is never re-stamped while it
exists', above, is enforced by `stamp`, which the automation never
runs). The attestations live in the signed review-state commits
already in history, and verifying a native mark means verifying the
signed commit that *introduced* its stamp -- an unsigned later
commit that deletes marks weakens nothing. Removing a mark is
always safe; it merely queues the file for re-review.

`import` also runs unsigned from the bot, in the same commit as
prune, and it adds marks, so it needs its own argument rather
than inheriting prune's. An imported
entry is not itself an attestation: it is a pointer to one --
the repository, path, and introducing commit of a signed
review-state commit already made in this repository (see
"Importing reviews from this repository" below). Trusting the entry
means verifying that commit, which `import` does at the moment it
writes the entry, not later and not by anyone downstream. This is a
genuinely new trust surface -- for the first time, an unsigned bot
commit can cause a file to read as reviewed -- and verification is
what bounds it: the bot can only relay an attestation a human has
already signed, and an entry it cannot verify is skipped and
reported rather than written.

**Importing reviews from this repository.** Much of what an adopted
repository has to review is not its own code: workflow templates,
helper scripts, and exported GitHub configuration are written and
reviewed here, then copied out byte-for-byte by
`standards-alignment` rollouts and reviewed again, file by file, in
every repository that received them. A review mark attests to a
blob SHA, not a path, and two files with the same blob SHA have the
same bytes -- so a review of a blob here is equally a review of an
identical blob anywhere else. `review-tracking.py import`, run in an
adopted repository, marks as reviewed every in-scope file whose
`HEAD` blob matches one this repository has fully reviewed.

Only full-file marks count, and only from this repository's own
sidecar history. `stamp` also stamps files carrying partial (region)
marks, so `import` walks every `.vscode/*.weaudit-shas.json` in this
repository's history, oldest commit first, and credits a
`(reviewer, blob)` pair only where the same commit's
`.vscode/<reviewer>.weaudit` has a full-file `auditedFiles` entry for
that path. Where a blob was reviewed more than once, the earliest
qualifying commit wins, since that is the attestation to point at.
Any path is eligible, not only `templates/`: identical bytes need no
context the file does not carry, and a whole-file review never
attested to context either -- a native mark does not go stale when a
script the file calls changes. History matters because a target's
copy often lags a template this repository has since changed and
re-reviewed: the copy's blob then matches an older stamp, which only
the sidecar's history still records, not `HEAD`.

The imported marks live only in `.vscode/imports.weaudit-shas.json`,
a sidecar-shaped file with no `.weaudit` file beside it, and
`import` never writes a `.weaudit` file or a human reviewer's own
sidecar. This is deliberately narrower than it could be: weAudit
loads every `.vscode/*.weaudit` file and decorates a file as audited
from its path alone, whatever file or author the entry came from
(`src/codeMarker.ts:626-628` in trailofbits/vscode-weaudit), so a
`.weaudit` imports file would show ticks in VSCode for files nobody
read in this clone. Worse, weAudit writes an `auditedFiles` change
back to `<author>.weaudit` (`codeMarker.ts:559-586`, `:931`), so an
imported entry authored by the original reviewer would, once
un-ticked, be written into that reviewer's own state file in this
repository, mixing imported reviews into a human's record. weAudit only globs `*.weaudit`, so it never reads the
sidecar-shaped imports file, and the name is already covered by the
`.gitignore` exception and the `paths-ignore` entry
(`.vscode/*.weaudit-shas.json`) that step 8 of "Adopting a
repository" puts in every adopted repository, so an import commit
needs no new exception and triggers none of the expensive CI lanes.

Each entry records where the attestation lives, not the attestation
itself:

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

`date` is the source stamp's date, not the day of the import,
because it records when the content was actually read. `verified`
records whether the introducing commit's signature was checked --
see the attestation argument above for why `import` checks it before
trusting the entry, rather than leaving verification to a later
reader. A commit that fails verification is skipped with a warning
naming it, as is a review by a reviewer with no entry in
`REVIEWER_IDENTITIES` (see "Commit signing"). `status` does not
verify an imported entry's provenance, any more than it verifies a
native mark's signature: it only checks that the blob SHA still
matches `HEAD`, the same as it does for a native mark.

`--no-verify` is the only way to import without verification. It
disables the check outright, every run under it says so on stderr,
and what it imports is recorded with `verified: false` and shown in
`REVIEWS.md` with an `(unverified)` suffix on its Source cell,
rather than passed off as checked. A run without gitsign on `PATH`
does not fall back to that: it imports nothing, leaves the entries
already recorded alone, ends with a warning banner on stderr saying
so and pointing at `--no-verify`, and still exits zero, so that a
CI job on a runner without gitsign neither fails nor marks anything
reviewed. It still removes entries, which needs no verification.

A file whose native mark has gone stale is not imported until
`prune` has removed the mark, which is why the workflow will run
`prune` first; until then the file's own review history says it
needs a human. An imported entry that has itself gone stale -- its
blob is no longer the file's content at `HEAD` -- is replaced if the
new content was reviewed here too and can be imported, and
otherwise removed by `import` itself, with the same message `prune`
gives, so that `REVIEWS.md` never shows it as reviewed in the
meantime. `import` regenerates `REVIEWS.md` when it changes
anything, as `stamp` and `prune` do.

A native review always supersedes an import: if a reviewer marks a
file that already carries an imported entry, `import` removes its
own entry on the next run, so `REVIEWS.md` shows one row per file and
the human's review is the one kept.

An import can be rejected the same way scope itself is narrowed.
`.vscode/review-scope.toml` gains an `import-exclude` list of
fnmatch patterns with the same semantics as `exclude` (`!`
re-includes), and, like the other exclusions in that file, an entry
should say why:

```toml
# Byte-identical to the template today, but this file is genuinely
# per-repository and a shared review would hide that it needs its
# own read.
import-exclude = ['config/local-overrides.yml']
```

`import` never adds an entry matching `import-exclude`, and removes
any existing imported entry that starts matching one.

`REVIEWS.md`'s reviewed-files table gains a `Source` column: `-` for
a native review, `development@<12-char commit>` for an imported one
(with ` (unverified)` appended if it was imported under `--no-verify`),
with the `Reviewer` cell taken from the entry's own provenance rather
than from a `.weaudit` file in this clone.

This repository does not import from itself. A template and this
repository's own instance of it could in principle cross-import, but
the case is rare enough that `import` run against this repository,
whether this clone or a worktree of it, is a no-op: it prints
`review-import: nothing to do; shakenfist/development is the source
of imported reviews`, writes nothing, and exits zero, so that the
shared `prune-reviews` workflow can run it here unchanged.

**Backlog alerting.** The daily consistency audit runs a
`review-coverage` check (`docs/audits/review-coverage.md`) against
every repository in its matrix. Repositories without a
`.vscode/review-scope.toml` are reported as not applicable, so
adopting the tooling automatically opts a repository in. The check
runs `review-tracking.py status`, which recomputes coverage against
HEAD -- which marks are still valid, which files are stale or never
reviewed -- rather than trusting the committed `REVIEWS.md`, so
alerting stays honest even if the prune automation breaks. When 5 or
more in-scope files need review (`REVIEW_BACKLOG_THRESHOLD` in
`scripts/audit/checks/review.py`), the audit files a `Consistency:
Human review coverage` issue on the repository listing the files
needing review, and closes it once a session brings the backlog back
under the threshold. That list is a snapshot, not a queue that stays
current: it is written once, when the issue is filed, and
`scripts/audit-manage-issues.py` creates, dedupes and closes such
issues but never edits one that is already open, so a long-lived
issue understates a growing backlog and any link it carries to a
criterion spec rots if that spec moves (shakenfist/development#138).
Expect the issue to open and close routinely: a single feature PR
can touch five in-scope files, and the issue is a standing nudge
rather than an alarm.

**Scope alerting.** The same audit runs a
`review-scope-completeness` check
(`docs/audits/review-scope-completeness.md`), which measures the
scope config rather than the backlog. It fails when a tracked file
is out of scope only because no `include` pattern names it, as
opposed to because an `exclude` entry says it should not be
reviewed. The two checks fail in opposite directions and the gap
between them matters: narrowing `include` is the cheapest way to
make a `review-coverage` issue close, and without this check
nothing notices a repository that reaches full coverage by
shrinking what counts. It runs `review-tracking.py scope-orphans`,
and the issue it files lists the unnamed files.

The case it was written for was not adversarial.
`templates/renovate/renovate.json` in this repository is a template
copied across the fleet -- by this repository's own scope config the
most consequential kind of file in it -- and it sat outside review
for as long as the `include` list had no JSON pattern, because JSON
had simply never come up. No issue could have been filed about it:
from `review-coverage`'s perspective the repository was fully
measured.

`status` and `scope-orphans` are also useful interactively: run
`./tools/review-tracking.sh status` in any clone to see effective
coverage at that clone's HEAD, or
`./tools/review-tracking.sh scope-orphans` to see what the scope
config is silently leaving out. Neither touches any state.
