# Systematic whole-codebase review tracking

## Prompt

Before responding to questions or discussion points in this document,
ground yourself in the referenced tooling and repositories. Read the
weAudit source (especially its serialization types) rather than relying
on its README, inspect the actual `.weaudit` state files produced during
the Phase 0 trial, and look at how `divergulent-reviews` structures its
signed review ledger before proposing changes to review-state storage.
Where a question touches on external concepts (git signing, crev-style
review proofs), research as needed. Flag uncertainty explicitly rather
than guessing.

## Situation

The consistency audits (see `PLAN-consistency-audits-v2.md` and
`audits/`) catch *mechanical* drift across Shaken Fist projects --
missing files, wrong configuration, packaging issues. What they do not
catch is drift *within* the code itself: inconsistencies in style,
architecture, and approach that creep into a codebase over years of
incremental change. Catching those requires a human actually reading
the code, file by file.

The problem is that the codebases are now large enough that a full
read-through cannot happen in one sitting. That creates a tracking
problem: which files have been reviewed, when, at what version of the
code, and with what notes? Without tracking, partial reviews are wasted
effort because you cannot resume them, and completed reviews silently
go stale as files change.

The desired workflow, as discussed:

* Review one file at a time inside the editor, with normal
  cross-referencing (go-to-definition etc.) available for context.
* Mark each file as reviewed, with optional notes.
* Have each review "signed" in the divergulent sense -- an
  attestation of who reviewed what, when, at which content version.
* Have reviews automatically flagged as stale when the underlying
  file changes, so re-review is a mechanical query rather than a
  judgement call.
* Be able to ask an LLM questions during review (Claude Code in the
  integrated terminal covers this with no integration work).

A strong constraint: **avoid writing new software where possible**.
The personal backlog is already long; the design below deliberately
composes existing tools (weAudit, git) with at most one small script.

## Analysis and recommendations

### Tool selection: weAudit

[weAudit](https://github.com/trailofbits/vscode-weaudit) is the VSCode
extension Trail of Bits uses for security audits, and audit work has
exactly this shape (large codebase, many sittings, coverage tracking).
It provides:

* "Mark File as Reviewed" / "Mark Region as Reviewed" commands, with a
  tick in the file explorer and highlighting so unreviewed code is
  visually obvious while browsing.
* Findings and notes attached to code regions.
* A daily log of files and lines reviewed per day.
* State stored as JSON in `.vscode/<username>.weaudit` (per user),
  which can be committed and shared.

Alternatives considered and rejected for now:

* **Writing our own extension** -- rejected for backlog reasons; only
  reconsider if Phase 0 shows weAudit is unsuitable.
* **Forking weAudit** (Apache-2.0) to add hash-keyed staleness --
  fallback option, not the starting point.
* **crev / cargo-vet style signed review proofs** -- the right *data
  model* (review proof = path + content hash + reviewer + date +
  notes + signature) but heavyweight to adopt directly. We get an
  equivalent binding from signed git commits (below). Keep as a
  reference model if the git-based approach proves insufficient.

### What weAudit does and does not record

Verified against `src/types.ts` in the weAudit repository (July 2026):

* An audited-file entry is `{path, author}` -- **no per-file hash, no
  timestamp**. (`PartiallyAuditedFile` adds `startLine`/`endLine`.)
* The state file has a single workspace-level `gitSha`, not a per-mark
  one.
* weAudit has **no concept of a stale review**: a file keeps its tick
  after it changes, until the mark is removed from the JSON.

So the JSON carries almost no provenance. That is fine: git carries
it instead.

### Attestation via signed git commits

Commit each update to `.vscode/<user>.weaudit` as a **signed commit**.
The signature covers the whole commit tree, which contains both the
review mark *and* the exact content of the reviewed source file. So
"Michael reviewed `foo.py` as it existed at blob X, on date D" is
implicit in the signed commit -- author, date, identity, and content
binding all come from git for free, with nothing added to the JSON.

Rules required to keep that binding honest:

1. **Mark reviews only on a clean working tree.** If a file has
   uncommitted local edits when it is marked reviewed, the committed
   tree will not match what was actually read.
2. **Commit review marks at the end of each session** (or more often),
   before pulling or rebasing, so the marking commit's tree reflects
   the reviewed state.
3. **Protect the branch** carrying review state from force-pushes and
   rebases; rewriting history rewrites the attestation trail.
4. Verification is `git verify-commit` on the marking commits.

Important corollary: **the state file must live in the same repository
as the code it reviews.** A central reviews repository (the
`divergulent-reviews` pattern) would break the tree binding -- the
signed commit would no longer contain the reviewed content. The
divergulent case is different because its ledger reviews *external*
artifacts (Debian patches) that cannot live in the same repo.
Per-repo `.vscode/<user>.weaudit` is also what weAudit supports
natively.

### Staleness via stamped blob SHAs and git hooks

Phase 0 confirmed the gap: weAudit has no idea when a reviewed file
changes. The revised design (July 2026, after trialling weAudit)
solves this entirely client-side with two hooks and one small script,
replacing the history-walking approach originally planned (preserved
below as a fallback).

**Stamp at commit time (pre-commit hook).** When a commit includes
review marks, record the blob SHA of each reviewed file: for every
`path` in `auditedFiles` that does not yet have a stamp, store the
staged blob SHA (`git rev-parse :"<path>"` -- the index version,
which is what lands in the commit tree). Compare content in git
terms, never timestamps (mtimes do not survive clones, and touch-only
commits would false-positive).

**Store stamps in a sidecar file, not inside the weAudit JSON.**
Verified against `src/codeMarker.ts` (July 2026): weAudit
reconstructs its save data from only the fields it knows about
(`{path, author}` for audited files; findings are field-by-field
mapped), so any extra fields we inject are dropped the next time
weAudit saves -- which happens on every mark. Losing a stamp is the
dangerous failure mode: re-stamping an old review at the *current*
SHA would silently refresh a stale review. A sidecar
(`.vscode/<user>.weaudit-shas.json`, mapping path to blob SHA and
review date) is never touched by weAudit and cannot be clobbered.
The pre-commit hook only ever stamps entries *absent* from the
sidecar, and drops
sidecar entries whose paths have left `auditedFiles` (unmarked
files).

The hook should also warn when a newly-stamped file has staged
content changes in the same commit -- that violates the clean-tree
rule above (the mark would attest to content the reviewer may not
have read).

**Prune when history moves (post-merge / post-checkout /
post-rewrite hooks).** After a pull, checkout, or rebase, staleness
is an O(1) check per file with no history archaeology: for each
sidecar entry, compare the stored SHA to `git rev-parse
HEAD:"<path>"`. On mismatch (or the path no longer existing --
covers renames, since blob SHAs follow content not paths), remove
the entry from both `auditedFiles` and the sidecar, and print what
was pruned. Partially-audited (region) marks are pruned wholesale on
the same trigger: line ranges shift as files change, so any change
to a file removes its region marks just like a whole-file mark.
The `pre-commit` framework supports all three hook types, and
`default_install_hook_types` in
`.pre-commit-config.yaml` makes a plain `pre-commit install` set
them all up.

The pruned state files are left modified in the working tree and get
committed (signed) at the end of the next session like any other
review-state change -- the prune is authored by the reviewer, so the
signed-mutations-only property is preserved.

Two operational notes:

* weAudit does **not** watch its state file for external changes
  (verified against `codeMarker.ts`), so a prune while VSCode has
  the workspace open needs a window reload or weAudit refresh before
  the ticks update. Pruning fires on pull, which usually precedes
  opening the editor; the session-start habit covers the rest.
* Hooks are per-clone: a fresh clone without `pre-commit install`
  silently shows stale ticks. Acceptable for a single reviewer;
  the optional CI backstop below covers it if it ever matters.

**Attestation is unchanged, and slightly strengthened.** The signed
commit tree remains the real proof (it contains both the mark and
the content); the sidecar stamp additionally makes the reviewed blob
SHA an *explicit* signed assertion rather than one derived from the
tree.

**Fallback: deriving staleness from history.** If stamps are ever
missing (pre-sidecar marks, forensic verification of old reviews),
staleness can still be derived by walking the state file's history:
find the commit **R** where a path entered `auditedFiles` by parsing
`git show <rev>:.vscode/<user>.weaudit` at each revision (note `git
log -S"<path>"` is unreliable -- findings in the same JSON also
contain paths), then test `git diff --quiet R HEAD -- <path>` and
optionally `git verify-commit R`. This is not planned to be built
unless needed.

### Review scope: direct code only

Reviews cover direct code. Generated artifacts (protobuf output and
similar), vendored code, and test data are out of scope; whether
unit tests are in scope is a per-repo call (see open questions).
Each adopted repo carries a small scope configuration the hooks
read -- include/exclude globs, e.g. `.vscode/review-scope.toml`.
The scope config gives coverage reporting a meaningful denominator
("N of M in-scope files reviewed") and lets the stamp hook warn
when an out-of-scope file is marked.

### Surfacing reviews: a generated REVIEWS.md

Review marks buried in `.vscode/*.weaudit*` are invisible to anyone
who does not know to look for them. Each adopted repo therefore
carries a `REVIEWS.md` at its root -- generated, never hand-edited
-- built from the weAudit state plus the sidecar.

The file opens with a header for someone encountering it cold: a
short explanation of what the file is (periodic whole-file human
review, distinct from PR review), a pointer to this tooling, a note
that the file is generated and how, and a coverage summary ("N of M
in-scope files reviewed"). Then the table: one row per reviewed
file with path, reviewer, review date, and the stamped (short) blob
SHA; partial reviews listed with their line ranges. Output is
deterministic (sorted by path) so its diffs read as "these reviews
were added, these were obsoleted".

Both hooks regenerate it whenever they change the sidecar: the
stamp hook after adding stamps, so `REVIEWS.md` lands in the same
signed commit as the marks it describes, and the prune hooks after
removing stale marks. A `REVIEWS.md` was prototyped by hand for
ryll during Phase 0; the generated file replaces it.

### Session driver: pick the next file to review

With a scope config in place, the helper can also answer "what
should I review now": a `next` subcommand picks a random in-scope
file with no current review mark (never-reviewed and pruned-stale
files land in the same pool, which is the right behaviour) and
opens it in VSCode (`code <path>`), printing the path for
non-editor use. Random choice is deliberate -- it avoids both
alphabetical ruts and cherry-picking easy files -- and it removes
the "where do I start today" friction from a session. Combined with
the automatic prune on pull, a session becomes: pull, `next`,
review, mark, repeat, commit signed.

### New code required

One script with `stamp` and `prune` subcommands (invoked by the
pre-commit and post-merge/checkout/rewrite hooks respectively),
each regenerating `REVIEWS.md` whenever the sidecar changes; a
standalone `regen` subcommand for bootstrap and repair; and the
interactive `next` subcommand above.
Target repos should not carry a copy: the `pre-commit` framework
supports remote hook repositories, so the script and its
`.pre-commit-hooks.yaml` live once in this repository
(`shakenfist/development`, decided -- it is already the home of the
plan, the audits, and the shared review tooling) and each project
adds a few lines to its existing `.pre-commit-config.yaml`.

CI drift detection (the previously-planned scheduled workflow
following the `consistency-audit.yml` pattern) is now an **optional
backstop** rather than the mechanism: with stamps in the sidecar, a
CI check reduces to the same O(1) comparison and needs no history
walking. Defer until there is a demonstrated need (e.g. multiple
clones or reviewers) -- and if built, CI reports only and never
edits the state files, since a bot commit would interleave unsigned
mutations with human attestations.

## Open questions

Resolved so far (decisions are recorded in the analysis above): the
hook script lives in `shakenfist/development`; `.gitignore` gets a
`!.vscode/*.weaudit*` exception per repo at adoption (already done
for ryll); scope is direct code only, excluding generated and
vendored artifacts; region marks are pruned wholesale like file
marks; reviews are surfaced via a generated `REVIEWS.md` opening
with an explanatory header and coverage summary; a `next`
subcommand picks a random unreviewed in-scope file and opens it in
VSCode.

Still open:

* **Are unit tests in scope?** Direct code is in and generated code
  is out (decided); unit tests sit in between. Probably a per-repo
  call recorded in that repo's scope config.
* **Should REVIEWS.md link to findings?** The header and coverage
  summary are decided; whether rows should also reference weAudit
  findings/notes for the file is not.
* **Multi-reviewer**: weAudit state is per-user
  (`<username>.weaudit`). For now there is one reviewer; the design
  extends naturally (one sidecar per state file) but the hook script
  should not assume a single state file, and `REVIEWS.md` must merge
  all reviewers' state.

## Execution

### Phase 0: Trial weAudit -- UNDERWAY

The point of this phase is to find out whether the core review loop
feels good *before* investing in any automation. Course-correct the
rest of this plan based on the outcome.

*Outcome so far (July 2026)*: trialling in `shakenfist/ryll`, with a
couple of files reviewed with weAudit. The review loop itself works;
the confirmed big gap is that weAudit does not notice when a reviewed
file later changes. That finding produced the stamped-SHA / git-hook
design now in the analysis section, replacing the original
history-walking approach. ryll remains the proving ground through
Phases 1 and 2; the tooling itself lands in `development` once solid.

1. Install the weAudit extension from the VSCode marketplace
   (`trailofbits.weaudit`).
2. Pick a trial codebase (see open questions) and do several real
   review sessions across multiple days.
3. Evaluate specifically:
   * Does the mark-as-reviewed / explorer-tick workflow suit?
   * Are the state-file diffs reviewable and merge-friendly enough to
     commit per session?
   * Is the daily log useful for pacing?
   * Do findings/notes cover the "notes with the signature" need, or
     is something more needed?
   * Any friction using Claude Code in the integrated terminal
     alongside it?
4. Decision point: continue with weAudit, fork it, or revisit the
   build-vs-buy question with what was learned.

### Phase 1: Conventions for signed review state

1. ~~Confirm `.vscode/*.weaudit` is committable in the trial repo
   (adjust `.gitignore` if needed).~~ Done for ryll; every other
   repo needs the same `.gitignore` exception as part of adoption
   (captured in the Phase 4 template).
2. ~~Adopt the session discipline: clean tree before marking,
   signed commit of the state file per session.~~ Done, and
   stronger than planned: reviews happen from a dedicated user
   account on the review machine whose clones never carry
   development edits, making the clean-tree rule structural. That
   account signs with gitsign (Sigstore keyless; example: ryll
   commit 755a3cc "review: glz.rs"), which also records every
   attestation in the Rekor transparency log. The conventions doc
   records setup and verification.
3. ~~Document the conventions in `docs/code-review-tracking.md` in
   this repository (workflow, signing, verification commands).~~
   Done.
4. ~~Confirm branch protection on the target repo prevents history
   rewrites on the branch carrying review state.~~ Done. It was
   initially absent (ryll had only a tag ruleset restricting
   releases); Mikal created the "Protect default branch history"
   ruleset (`deletion` + `non_fast_forward` on `~DEFAULT_BRANCH`)
   on 2026-07-09 using the command now recorded in the conventions
   doc. shakenfist/shakenfist's "Develop branch" ruleset is a
   stricter superset and also satisfies the requirement; the
   possible audit item for this is listed under future work.

*Phase 1 note (2026-07-09)*: Phase 0 prototyping happens in a
dedicated review account on the same host (invisible from the
development account, and initially mistaken for another machine).
The `.gitignore` exception and `.vscode/mikal.weaudit` are already
pushed to ryll's `origin/develop`; the hand-written draft
`REVIEWS.md` is local to the review account and will be replaced
by the generated file in Phase 2 (agreed). ryll's pushed exception
covers `!.vscode/*.weaudit` only -- the sidecar needs an
additional `!.vscode/*.weaudit-shas.json` line when Phase 2 wires
the hooks up.

### Phase 2: Stamp and prune hooks

1. ~~Write the review script in this repository with `stamp`,
   `prune`, `regen`, and `next` subcommands as described in the
   analysis -- `stamp` and `prune` regenerate `REVIEWS.md` whenever
   they change the sidecar -- plus tests against a fixture repo.~~
   Done: `scripts/review-tracking.py`, with ten fixture-repo tests
   in `scripts/test_review_tracking.py`.
2. ~~Define the per-repo scope config and wire it into the script:
   out-of-scope warnings at stamp time, the coverage denominator
   for the `REVIEWS.md` header, and the candidate pool for
   `next`.~~ Done: `.vscode/review-scope.toml`, fnmatch
   include/exclude lists; the tracking machinery itself
   (`.vscode/*`, `REVIEWS.md`) is always excluded.
3. ~~Add `.pre-commit-hooks.yaml` to this repository exposing the
   hooks (stamp at the `pre-commit` stage; prune at `post-merge`,
   `post-checkout`, and `post-rewrite`).~~ Done, and the whole
   loop validated through `pre-commit try-repo` against a fixture:
   stamp (fails asking for a re-stage, then passes), upstream
   change, prune at post-merge discarding the mark from the
   weAudit state, the sidecar, and `REVIEWS.md`.
4. ~~Wire the trial repo's `.pre-commit-config.yaml` to the hooks,
   including `default_install_hook_types`, and re-run
   `pre-commit install`.~~ Prepared and staged in the primary
   ryll clone (2026-07-09), pinned to development rev `0957d15`;
   awaiting Mikal's review and commit. Each clone (including the
   review account's) then needs `pre-commit install` re-run.
5. ~~Bootstrap: stamp the files already marked reviewed during
   Phase 0 after eyeballing that they are unchanged since review,
   and replace ryll's hand-maintained `REVIEWS.md` with the
   generated one.~~ Folded into the same staged ryll change, which
   is essential: the stamp hook is `always_run`, so once the
   wiring lands, the next commit by anyone would stamp any
   unstamped mark at the file's *current* content -- discovered
   when a scoped pre-commit run during preparation auto-stamped
   the stale mark. The eyeball check found `glz.rs` unchanged
   since its signed review (stamped at `6600fc53`, dated
   2026-07-05, the true review date) and `byte_bounded_lru.rs`
   changed by two post-review "Lint fix." commits (the review
   commit itself also modified the file -- the exact clean-tree
   violation the stamp hook now warns about), so its mark was
   removed; it returns via `next`. The bootstrap-with-wiring
   requirement is now documented as adoption step 6 in the
   conventions doc.
6. Validate the loop end to end in ryll: mark, commit (stamp lands
   and `REVIEWS.md` updates), change the file upstream, pull
   (prune fires and `REVIEWS.md` updates), confirm the tick
   disappears after a weAudit refresh, re-review, re-commit. *The
   hook-level loop is already validated against the fixture; this
   is the live confirmation from the review account.*

### Phase 3: Optional CI backstop (deferred)

Only if a demonstrated need appears (multiple clones or reviewers,
hooks not installed somewhere):

1. Add a scheduled workflow (pattern: `consistency-audit.yml`) that
   fresh-clones each participating repo and compares sidecar stamps
   to HEAD blob SHAs -- report only, never edit state files.
2. Consider adding never-reviewed coverage reporting ("N of M files
   reviewed") here, which may justify the workflow on its own.

### Phase 4: Documentation and rollout as a consistency audit

Once the ryll prototype is solid, this stops being an experiment and
becomes yet another thing the consistency audits check for:

1. Update this repository's `README.md` to describe the review
   tracking system alongside the consistency audits.
2. Add `audits/code-review-tracking.md` following the modular audit
   pattern (what we check, per-project status table), and a
   `templates/code-review-tracking/` directory containing the
   `.pre-commit-config.yaml` stanza, a starter scope config, and
   setup instructions (`default_install_hook_types`, the
   `.gitignore` exception for `.vscode/*.weaudit*`).
3. Extend `scripts/audit-check.py` with the mechanical check (hook
   config present in `.pre-commit-config.yaml`), so drift is caught
   by the existing daily `consistency-audit.yml` run and issue
   automation.
4. Roll out to further codebases as first passes are scheduled.
5. Revisit the open questions above with real experience.

## Administration and logistics

### Success criteria

We will know this plan has been successfully implemented when:

* A large codebase can be reviewed across many sittings without
  losing track of what has been covered.
* Every "reviewed" mark is attributable to a signed commit binding
  reviewer, date, and exact file content.
* "What needs (re-)review" is answered mechanically -- stale marks
  are pruned automatically by git hooks when history moves, rather
  than remembered.
* The in-editor view (weAudit ticks) never shows a stale review for
  longer than the gap between a pull and the next weAudit refresh.
* Anyone browsing an adopted repo can see what has been reviewed,
  by whom, and when, from a generated `REVIEWS.md` that is never
  out of step with the review state it is committed alongside.
* The amount of newly-written software is one small script plus
  pre-commit configuration.

### Future work

* Fork weAudit (or upstream a patch) to record a per-file blob hash
  at mark time and display staleness natively, removing the need for
  the prune step.
* Integrate review coverage into a cross-project dashboard alongside
  the consistency audit status.
* Consider whether LLM-assisted pre-review (Claude flagging candidate
  inconsistencies for the human pass) is worth adding once the human
  workflow is established.

* A `default-branch-protection` consistency audit item, surfaced by
  Phase 1: nothing currently audits branch protection (the
  github-security audit covers Dependabot, secret scanning, and
  CodeQL only), and ryll's `develop` turned out to have no branch
  ruleset at all. Every project's default branch should carry at
  least the `deletion` and `non_fast_forward` rules, checkable
  mechanically via `gh api repos/<org>/<repo>/rulesets` in
  `audit-check.py`'s existing gh-based style. This is general repo
  hygiene rather than review-specific, so it can proceed
  independently of this plan.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
