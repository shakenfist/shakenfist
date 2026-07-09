# Code review tracking

This documents the conventions for tracking whole-codebase human
review: periodically reading a repository file by file to catch the
inconsistencies in style, architecture, and approach that creep in
over years of incremental change. It is complementary to both PR
review (which only ever examines deltas) and the consistency audits
(which catch mechanical drift like missing files -- see `audits/`).
The full design rationale is in
`docs/plans/PLAN-code-review-tracking.md`.

The automation lives in this repository:
`scripts/review-tracking.py` (subcommands `stamp`, `prune`, `regen`,
and `next`) exposed as pre-commit hooks by `.pre-commit-hooks.yaml`
at the repository root, with tests in
`scripts/test_review_tracking.py`.

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
  written by the `review-stamp` hook, never by weAudit) records the
  blob SHA and date of each review so staleness is a mechanical
  check, and a generated **`REVIEWS.md`** surfaces the review state
  to people who do not know to look in `.vscode/`.
* **A scope config** (`.vscode/review-scope.toml`) defines which
  files count as reviewable: `include` and `exclude` lists of
  fnmatch patterns matched against repo-relative paths (`*` matches
  across directory separators; an empty or absent `include` means
  all tracked files). The tracking machinery (`.vscode/*`,
  `REVIEWS.md`) is always excluded. Generated and vendored code
  should be excluded here; whether unit tests are in scope is a
  per-repo decision.

## Adopting a repository

1. Ensure the review state files are committable. If `.gitignore`
   excludes `.vscode/`, add exceptions:

   ```
   !.vscode/*.weaudit
   !.vscode/*.weaudit-shas.json
   !.vscode/review-scope.toml
   ```

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

4. Write `.vscode/review-scope.toml`, for example:

   ```toml
   # Which files are subject to whole-file review.
   include = []                 # empty: all tracked files
   exclude = ['*_pb2.py', 'vendor/*']
   ```

5. Wire the repo's `.pre-commit-config.yaml` to the shared hooks
   and make plain `pre-commit install` set up all the needed hook
   types:

   ```yaml
   default_install_hook_types:
     - pre-commit
     - post-merge
     - post-checkout
     - post-rewrite

   repos:
     - repo: https://github.com/shakenfist/development
       rev: <pinned commit or tag>
       hooks:
         - id: review-stamp
         - id: review-prune
   ```

   Then re-run `pre-commit install` in each clone (including the
   review account's).

6. Bootstrap existing review marks, if any were made before the
   hooks were wired up -- and do it **in the same commit as the
   wiring**. The stamp hook is `always_run`: once active, the next
   commit by *anyone* stamps every unstamped mark at the file's
   current content, whether or not that content is what was
   reviewed. A stale pre-existing mark would be silently blessed.
   So, as part of the wiring commit:

   * For each pre-existing mark, check the file is unchanged since
     its signed review commit; unmark any that changed (they are
     stale, and will come back around via `next`).
   * Run the stamp by hand, and correct the recorded dates to the
     true review dates (a fresh stamp records today):

     ```
     <development-clone>/scripts/review-tracking.py stamp
     # fix dates in .vscode/<user>.weaudit-shas.json if needed
     <development-clone>/scripts/review-tracking.py regen
     ```

   * Delete any hand-maintained REVIEWS.md; the generated file
     replaces it.
   * Commit the wiring, corrected weAudit state, sidecar, and
     REVIEWS.md together (signed).

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

1. `git pull` on a clean tree. The prune hook fires, discards
   marks for files changed since their review, and regenerates
   `REVIEWS.md`. Anything it pruned is a good candidate work
   queue for the session. If VSCode was already open, reload the
   window (or toggle the weAudit tree view) so the ticks refresh
   -- weAudit does not watch its state file for external changes.
2. Pick a file:

   ```
   ~/src/shakenfist/development/scripts/review-tracking.py next
   ```

   picks a random unreviewed in-scope file and opens it in VSCode
   (`--no-open` to just print it).
3. Read it. weAudit's explorer ticks show what is already done;
   Claude Code in the integrated terminal for questions.
4. Mark it reviewed (`weAudit: Mark File as Reviewed`), attach
   findings or notes as needed.
5. Repeat from 2. At the end of the session:

   ```
   git add .vscode/*.weaudit
   git commit
   ```

   The stamp hook records each newly reviewed file's blob SHA and
   date in the sidecar, regenerates `REVIEWS.md`, and fails this
   first commit attempt asking you to stage those updates; do the
   `git add` it suggests and re-run the commit. The (signed)
   commit that lands contains the marks, the stamps, and the
   regenerated `REVIEWS.md` together.

## Staleness

A review applies to the file content that was read, not the path:
once the file changes, that review is stale and the file should be
treated as unreviewed. weAudit does not track this -- a stale tick
looks identical to a fresh one.

The hooks make staleness automatic: `review-stamp` records each
reviewed file's blob SHA in the sidecar at commit time, and
`review-prune` (at the post-merge, post-checkout, and post-rewrite
stages) discards any mark -- whole-file or region -- whose stamped
SHA no longer matches `HEAD`, regenerating `REVIEWS.md` to match.
Region marks are pruned wholesale with the file: line ranges shift
as files change, so a partial review of a changed file is not
trusted either. See the plan for the full design, including why
the stamps live in a sidecar rather than in weAudit's own JSON.

Two behaviours worth knowing about:

* The prune hook fires on *any* checkout in a clone with the hook
  types installed -- including switching to an old branch in a
  development clone, where files legitimately differ from their
  stamped SHAs. The prune is correct there too (those reviews do
  not apply to that content), but if it surprises you, `git
  restore .vscode/ REVIEWS.md` puts the state back.
* A stamped entry is never re-stamped while it exists: if a
  reviewed file changes, the only path forward is prune then
  re-review. This is what prevents a stale review being silently
  refreshed at the file's current content.
* When every file in a directory is reviewed, weAudit adds a
  derived *directory* entry to `auditedFiles` alongside the
  per-file entries. The hooks treat these as pure UI state: they
  are never stamped or listed in `REVIEWS.md`, and prune removes
  them when a file inside stops being reviewed (mirroring what
  weAudit itself does when a file is unmarked in its UI).
