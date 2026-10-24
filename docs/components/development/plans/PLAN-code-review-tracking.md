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

*Corrected on 2026-10-24*: the hooks described in this section and in
"New code required" below were removed in `cde84bb` (#9,
2026-07-18), and `.pre-commit-hooks.yaml` went with them. Review state
changing in the middle of unrelated git operations confused more than
it helped. `stamp` and `prune` are now run by hand through a
per-repository wrapper. Since `PLAN-review-coverage.md`, `prune` and
`import` also run from each adopter's `prune-reviews` workflow on its
own default branch. The stamped-SHA sidecar, the O(1) staleness
comparison and the reasons for both still stand. This section is
kept as the record of the design, and `docs/code-review-tracking.md`
is the authority on how the pieces run today.

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

*Corrected on 2026-10-24*: two of the resolutions above did not
last. The hooks are gone (see the note at the top of "Staleness via
stamped blob SHAs and git hooks"). Scope is no longer direct code
only: `ed10382` made code plus the prose that documents it the
recommended default, and `9c4821a` added YAML and other declarative
configuration. The
current rule is the scope config entry under "The pieces" in
`docs/code-review-tracking.md`.

The three questions below were left open in July. Practice has
answered each, which is what Phase 4 step 5 asked for:

* **Are unit tests in scope?** Direct code is in and generated code
  is out (decided); unit tests sit in between. Probably a per-repo
  call recorded in that repo's scope config. *Settled as a per-repo
  call*, which is how `docs/code-review-tracking.md` states it, and
  each adopter's `.vscode/review-scope.toml` records the choice.
* **Should REVIEWS.md link to findings?** The header and coverage
  summary are decided; whether rows should also reference weAudit
  findings/notes for the file is not. *Settled as no, by default.*
  The rows carry path, reviewer, date, blob SHA and, since
  `PLAN-review-import.md`, the source of an imported review. No
  findings link was built and nobody has asked for one. Findings
  stay in the weAudit state, which is committed beside
  `REVIEWS.md`.
* **Multi-reviewer**: weAudit state is per-user
  (`<username>.weaudit`). For now there is one reviewer; the design
  extends naturally (one sidecar per state file) but the hook script
  should not assume a single state file, and `REVIEWS.md` must merge
  all reviewers' state. *Settled as designed.* `review-tracking.py`
  reads every `.vscode/*.weaudit` file. `REVIEWER_IDENTITIES` maps
  each reviewer to the signing identity that `import` verifies. The
  imports file is a reviewer-shaped sidecar of its own. There is
  still only one human reviewer, so the multi-reviewer path has had
  no real use.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 0. Trial weAudit | Complete | nothing in this repository; the trial ran in ryll |
| 1. Conventions for signed review state | Complete | `d180c39` |
| 2. Stamp and prune hooks | Complete | `0957d15`, `9f8d47f`, `4eae793`, `ee6d4ea`; ryll `7775679e` (#144), `ef94c321` (#146) |
| 3. Optional CI backstop | Superseded | nothing; replaced by `PLAN-review-coverage.md` |
| 4. Documentation and rollout as a consistency audit | Complete | accreted over many landings; reconstructed in Phase 5 |
| 5. Push audit | Complete | `c39b06e` (#314) |

This table was added on 2026-10-24, when Phases 0--4 were closed
out together. Until then the plan carried no status table and every
phase section still read as if work were outstanding. The `Merged`
cells for Phases 1 and 2 come from the reconstruction that
`PLAN-push-audit-phase` added to Phase 5. Phase 4's landings were
never recorded, and Phase 5 reconstructs them.

### Phase 0: Trial weAudit

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
   *Closed out on 2026-10-24 without being run.* The hooks this step
   would have checked were removed nine days after they landed, in
   `cde84bb` (#9, 2026-07-18), and the script has been run by hand
   ever since. The same loop running from CI was confirmed in
   production by `PLAN-review-coverage.md` phase 5.

### Phase 3: Optional CI backstop (deferred)

*Superseded on 2026-08-02 by `PLAN-review-coverage.md`*, which built
both halves of this phase in a different shape. Each adopting
repository prunes its own default branch from CI, rather than one
scheduled workflow comparing stamps across the fleet. Never-reviewed
coverage is reported by the daily audit's `review-coverage`
criterion.

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

*What actually landed (survey of 2026-10-24).* This phase was never
run as a unit. Its work arrived in pieces between July and October,
some as unplanned landings and some under later plans, and none of it
was recorded here at the time. Phase 5's survey reconstructs those
landings. Taking the steps in turn:

1. Done. `README.md` describes review tracking among its
   documentation links. It first did so in Phase 2, and was reworded
   by #9 and `b51f550`.
2. Done in a different shape. There is no
   `audits/code-review-tracking.md`. The criteria are
   `docs/audits/review-coverage.md` (backlog against HEAD, from
   `PLAN-review-coverage.md`), `docs/audits/review-scope-completeness.md`
   (nothing leaves scope silently, #72), and the
   `review-marks-pre-commit` check in
   `docs/audits/workflow-standards.md` (#14, #15). The template
   directory is `templates/review-tracking/`, not
   `templates/code-review-tracking/`. It holds the prune workflow,
   its script and the by-hand wrapper (`PLAN-review-import.md`), not
   a `.pre-commit-config.yaml` stanza: that stanza went when the hooks
   did.
3. No longer applicable. There is no hook configuration to check
   for. The checks that do exist are listed under step 2.
4. Done. Besides ryll, the adopters are kerbside (kerbside#214),
   actions (actions#24), hunkydory (hunkydory#8), sfui (sfui#39),
   kerbside-client (kerbside-client#10) and this repository (#36). The
   `review-tracking-adoption` skill (`b51f550`) is the procedure for
   adopting a repository.
5. Settled in practice and now recorded under "Open questions" above.

### Phase 5: Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of every phase in
this plan against `main`, not over the last phase's diff alone --
the interactions between phases are most of what a whole-plan
audit is for. Findings land as their own pull request. The plan is
not complete until each finding is resolved or declined in writing,
with the reason recorded here. If the audit finds nothing, say so in
one sentence: that is a real result, and a run of them is the
evidence for making this phase conditional rather than mandatory.

**Planning effort:** high. Every phase of this plan merged between
two and fifteen weeks ago, and none of Phase 4 was recorded as it
landed. Nearly all of the judgement here is about which landings
belong to this plan and which tree to report against. Getting either
wrong produces a confident audit of nothing. **Review effort:** the
runbook's own settings, raised to high for 2a and 2b. Fourteen ranges
have to be mapped onto a tree that has since been restructured, and
that mapping is where a lighter run would go wrong.

In scope:

* running `PUSH-AUDIT.md` over the fourteen ranges in D5.1;
* the consumer check D5.6 adds for the interfaces this plan removed;
* writing the Outcome into this section;
* closing the plan.

Out of scope:

* fixing anything the audit finds, which lands as its own pull
  request;
* re-auditing work that other plans own and have audited (D5.2);
* re-auditing the adoption pull requests in other repositories
  (D5.4);
* the template drift in sfui and kerbside-client, which the survey
  found and which is filed rather than fixed (D5.5);
* development#22 (Dockerfiles in human review scope), a per-repository
  scope decision that this plan's machinery already supports;
* the `default-branch-protection` criterion, which stays under Future
  work.

#### What the survey found

**The plan's status record was wrong in every phase.** Phase 0
still read "UNDERWAY", Phase 2 step 6 and every Phase 4 step were
still open, and there was no Execution table. `4b15e15` closes out
Phases 0--4. `9acc3bc`, the commit that wrote this plan, corrects the
false claims where they are made, not by rewriting the history: a dated
note at the top of "Staleness via stamped blob SHAs and git hooks",
another under "Open questions", the answers to the three open
questions, and a step-by-step "What actually landed" under Phase 4.
`cde84bb` deliberately left this file as "a historical record" when
it removed the hooks, and `PLAN-review-coverage.md` phase 5 made the
same call. The notes respect both decisions, so a later step need not
redo them.

**Phase 4 landed in fourteen pieces that nobody recorded**, besides
the work later plans did under their own names. The ranges below are
every first-parent landing on `main` that touched the review tracking
paths and is not claimed by another plan's Execution record.
`scripts/review-tracking.py`, `scripts/*test_review_tracking.py`,
`docs/code-review-tracking.md`, `templates/review-tracking/`,
`.claude/skills/review-tracking-adoption/`,
`scripts/audit/checks/review.py`, the two review criterion specs,
`tools/*review*.sh` and `.github/workflows/prune-reviews.yml` were
searched with `git log --first-parent main`. Every landing was then
checked for single or double parents with `git log -1 --format=%p`.
These are the diffstats the audit must reproduce:

| # | Range | Landed | What | Files | Diffstat |
|---|-------|--------|------|-------|----------|
| 1 | `d180c39^..d180c39` | 2026-07-09 direct | Phase 1 conventions | 5 | +244 -6 |
| 2 | `0957d15^..ee6d4ea` | 2026-07-09 direct | Phase 2 script, hooks, bootstrap rule, `next` workspace, derived dirs | 8 | +854 -60 |
| 3 | `a3b7f50^1..a3b7f50` | #9, 2026-07-18 | Hooks removed; run by hand | 7 | +93 -130 |
| 4 | `ed10382^..ed10382` | 2026-07-24 direct | Code plus docs as the default scope | 1 | +24 -7 |
| 5 | `079e34e^1..079e34e` | 2026-08-05 local merge | YAML into review scope | 1 | +25 -12 |
| 6 | `ba4a6a7^..ba4a6a7` | 2026-08-07 direct | Scope example: JavaScript and protobuf | 1 | +16 -7 |
| 7 | `b51f550^..b51f550` | 2026-08-08 direct | `review-tracking-adoption` skill | 4 | +110 -0 |
| 8 | `ccaac6e^1..ccaac6e` | #14, 2026-08-11 | Review marks exempt from rewriting hooks; the `review-marks-pre-commit` check | 5 | +209 -0 |
| 9 | `127079e^1..127079e` | #15, 2026-08-11 | Narrow that exclude | 4 | +143 -43 |
| 10 | `a33e694^1..a33e694` | #36, 2026-08-21 | This repository adopted | 7 | +172 -0 |
| 11 | `44748d4^1..44748d4` | #56, 2026-08-27 | `stamp` announces out-of-scope reviews | 3 | +171 -10 |
| 12 | `0ba8920^1..0ba8920` | #62, 2026-08-29 | Stale-stamp CI gate dropped | 3 | +19 -43 |
| 13 | `39f8d5b^1..39f8d5b -- ':!.vscode' ':!REVIEWS.md'` | #72, 2026-08-31 | `review-scope-completeness` and `scope-orphans` | 11 | +515 -20 |
| 14 | `f624d26^1..f624d26` | #236, 2026-10-09 | `next` picks from a subtree, in scope order | 3 | +204 -7 |

The total is 63 file-diffs, +2,799 -345. Ranges 1 and 2 repeat the
reconstruction that `PLAN-push-audit-phase` wrote here in August,
which this section's earlier text recorded as four direct commits.
They are written as one `first^..last` range because the four commits
are consecutive on `main`. Range 5 is a local merge with two parents,
so `^1` reads it whole, and it is the same diff as its only branch
commit `9c4821a`. Range 3 is #9's merge commit, and its only branch
commit is `cde84bb`, the one the correction notes cite. Range 13 is a
review-session pull request that also carried a criterion. The
pathspec removes its marks, which record a review session rather than
the plan's work: 15 files and +771 -42 unscoped.

**Landings that touched these paths and are deliberately not in the
table**, with whose record covers each:

* `PLAN-review-coverage.md`: #11 (`b677b61`), #139, #146. Its
  phase 6 audited them.
* `PLAN-review-import.md`: #177, #186, #203, #249, #251, #254, and
  the re-syncs in actions, hunkydory, kerbside and ryll. Its phase 5
  audited them.
* `PLAN-audit-scripts-restructure.md`: #79, which moved every check
  out of `audit-check.py`.
* `PLAN-review-unit-size.md`: #160.
* `PLAN-consistency-audits-v2.md`: #47 (`e3d1333`) as a whole,
  including `ba3876a`. That commit taught `stamp` to refuse a stale
  stamp, and it is the one review-tracking change inside a PR that
  another plan has recorded. The cav2 `fix` PRs (#266 to #285) are
  also that plan's.
* Not review-tracking work: #17's fixture git-environment scrub
  (`4697409`), `805cb21`'s lint pass and #167's test-suite move
  (`b9e8b6c`). Each touched these files only as one of many.
* Review sessions (#47, #52, #72, #167, #277, #292, #296, #301,
  #303) commit marks, and that is attestation, not plan work.

**The tree has moved under every range.** Map each finding to a
current path before reporting it (D5.3):

* The check functions that #14, #15 and #72 added to
  `scripts/audit-check.py` are now the `ReviewMarksPreCommit`,
  `ReviewCoverage` and `ReviewScopeCompleteness` classes. They are
  at `scripts/audit/checks/review.py:130`, `:172` and `:247`, and
  registered at `scripts/audit/registry.py:86-88`.
* `scripts/test_audit_check.py` is gone. The criteria's tests are
  `scripts/tests/test_review.py`.
* `scripts/test_review_tracking.py` is now
  `scripts/tests/test_review_tracking.py` (`b9e8b6c`). This corrects
  `PLAN-review-coverage.md`'s phase 6 survey, which was true when
  written.
* `audits/workflow-standards.md` is now
  `docs/audits/workflow-standards.md`.
* `.pre-commit-hooks.yaml` is gone (range 3). Its hook ids were
  `review-stamp` and `review-prune`.

**Every file the audit would report against carries a current human
review mark.** For each live file the ranges touched, the `REVIEWS.md`
stamp equals `git rev-parse HEAD:<path>` at `aa8d259`. That holds for
`scripts/review-tracking.py`, `scripts/audit/checks/review.py`, both
test modules, `docs/code-review-tracking.md`, the two criterion specs,
`docs/audits/workflow-standards.md`, the adoption skill, both `tools/`
scripts, `prune-reviews.yml`, `ci.yml`, `.pre-commit-config.yaml`,
`README.md`, `AGENTS.md` and `ARCHITECTURE.md`. All were reviewed
between 2026-09-27 and 2026-10-21. `.vscode/review-scope.toml` is
tracking machinery and never carries a mark. A person has therefore
read every file in its present form. That shapes D5.3 and D5.7: what
whole-file review does not do is read a diff for what it removed, who
consumed it, or how two landings interact.

**sfui and kerbside-client carry stale copies of the review tracking
CI.** `templates/review-tracking/README.md` says all three files
"must stay byte-identical to the template in every adopted
repository". Blob hashes fetched on 2026-10-24 with
`gh api repos/shakenfist/<repo>/contents/<path>` show this:

| Repository | `prune-reviews.yml` | `ci-prune-reviews.sh` | `review-tracking.sh` |
|------------|---------------------|-----------------------|----------------------|
| actions, hunkydory, kerbside, ryll | identical | identical | identical |
| sfui, kerbside-client | differs | differs | identical |

Both adopted on 2026-09-27 (sfui#39, kerbside-client#10). The
template directory then took eleven commits on 2026-10-13 and
2026-10-14, `99a2783` to `467b09a`. Several of those isolate the
prune job from shared runner state: `9556eba`, `014f82e`, `ba549e4`
and `130d700`. The
re-sync wave that `PLAN-review-import.md` phase 5 audited covered the
four older adopters only. No criterion measures the byte-identity
the README requires, which is why nothing has reported this. D5.5
files it as development#307.

**`PUSH-AUDIT.md` agrees with the tree** on every point this phase
leans on: `AUDIT_RANGE` throughout, the six-file rule pointing at
`docs/consistency-audits.md`, and the suites 2b names, all of which
now sit under `scripts/tests/`. Nothing else disagreed.

**Review marks this phase can stale: none.** The phase edits only
this plan, and `.vscode/review-scope.toml` excludes `docs/plans/*`.

#### Decisions

**D5.1. The audit reads the fourteen ranges in the survey table,
each separately.** They are not joined into one span, because
fifteen weeks of unrelated work sits between range 1 and range 14.
Each agent runs every check once per range. Each reports a totals
table, one row per range, with files, insertions and deletions. A
report whose totals do not match the survey's is re-run, not
triaged, as `PUSH-AUDIT.md` requires. Each brief below carries the
ranges literally, not as `$AUDIT_RANGE`.

**D5.2. Work another plan owns is cited, not re-audited.** This is
the same rule `plan-push-audit-phase` gives for cross-repository
landings, applied to cross-plan ones. `PLAN-review-coverage.md` and
`PLAN-review-import.md` are both `Complete`, and their audits
covered the steady-state machinery and the template. Reading those
ranges again would double the cost and turn up findings that already
carry dispositions there. The exclusion list in the survey is the
boundary. An agent that finds a defect it can only attribute to an
excluded range reports it as outside this plan, so the management
session can check it against that plan's Outcome before deciding.

**D5.3. Findings are reported against today's tree, and the ranges
are read for intent.** This is `PLAN-review-coverage.md` D6.3,
adopted unchanged, and the reasoning is stronger here. A finding is
only actionable where a fix would land, and for most of these ranges
that is a different path. The survey also shows a person has read
every live file in its current form. Each agent therefore states,
for each finding, the current path and line, or that the code no
longer exists. A finding that later work already resolved is
reported as resolved, with the commit, not dropped.

**D5.4. The adoption pull requests in other repositories are listed,
not re-audited.** The block expects each cross-repository landing to
carry its own audit and be cited. None of these did, because all of
them predate the block or adopted a template whose own audit was
cited:

* ryll#144, #146, #170;
* kerbside#214, #219, #254;
* actions#24, #49;
* hunkydory#8;
* sfui#39;
* kerbside-client#10.

What they added falls into three kinds, and none needs a fresh
audit:

* **Hook wiring.** ryll#170 removed it, the hooks are gone, and
  D5.6 checks that nothing still references them.
* **Copies of the CI template.** These are byte-identical in four
  repositories, and `PLAN-review-import.md` phase 5 audited the
  copies. The two that are not identical are D5.5's subject.
* **Per-repository scope configs and `.gitignore` lines.** The
  daily `review-scope-completeness` and `review-marks-pre-commit`
  criteria measure these, and they are policy rather than code.

kerbside#254 (skipping functional tests on review-only pull
requests) is the one piece of real CI logic among them. The
`review-tracking-adoption` skill re-verifies it. It is named here so
the gap is stated rather than silent.

**D5.5. The sfui and kerbside-client drift is filed, not fixed or
audited here.** Re-syncing two repositories is a fix with its own
review, and the audit's own findings land as their own pull request.
The missing measurement belongs to a criterion, and a criterion has
a blast radius this phase should not take on. A new
`review-tracking-template` check would newly fail exactly those two
repositories today, and probably none elsewhere. It belongs in the
issue, with that count. It was filed during planning, as
development#307, so the Outcome can cite it.

**D5.6. One extra check: nothing still consumes what the plan
removed.** `plan-push-audit-phase` item 4 is not in this
repository's `PUSH-AUDIT.md`, which never had to ask it. This plan
removed several things:

* a whole published interface, `.pre-commit-hooks.yaml` and its hook
  ids `review-stamp` and `review-prune`;
* a test assertion (range 12);
* a set of `audit-check.py` functions, which #79 moved.

A repository still pinning the hooks would fail on its next
`pre-commit autoupdate`, not today. Nothing in the fleet would
report it. The check is a grep across every fleet clone, and the
greps are stated in the report.

**D5.7. Wave 2 runs in full, at the runbook's settings, raised to
high for 2a and 2b.** This is the decision most likely to be argued
with. Every live file has had a whole-file human read since these
ranges landed. Later plans' push audits read most of the steady
state. The case for spending four agents on July's diffs is weak if
the question is "is this code good". It is not the question. Each
wave 2 brief below is aimed at what neither of those reads covered:

* what a range removed;
* whether a later range silently undid an earlier one, as range 12
  undid part of `ba3876a`'s guard;
* whether the documentation still tells one story across fourteen
  edits;
* the token and runner surface that range 10 introduced to this
  repository.

Skipping a dimension because the code "has been reviewed" would
also be the first audit to argue its way out of the mandatory phase.
That argument belongs in `plan-push-audit-phase`, with evidence, not
in one plan's execution. 2a and 2b go to high because they carry the
path mapping. 2c and 2d keep the runbook's settings.

**D5.8. The phase closes the plan.** Where the audit finds nothing,
step 5h sets Phase 5 and the index row to `Complete` in this
phase's own pull request, and records no `Merged` cell. Where it
finds something, the findings pull request is the carrier, as
`plan-phase-landing` requires.

#### Step plan

Steps 5b to 5f are independent, and run in parallel once 5a's totals
are in hand. Every brief below must carry the fourteen ranges
literally, copied from the survey table with their pathspecs, and
the totals the agent must reproduce. Every agent reports and does not
fix, and none prunes, regenerates or commits `REVIEWS.md`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | Wave 1 of `PUSH-AUDIT.md`, in this worktree, which differs from `origin/main` only in this plan. Run `git fetch origin`, then `pre-commit run --all-files` once on a clean tree. Then run every grep in the wave 1 block once per range, for the fourteen ranges in the survey table, by exporting `AUDIT_RANGE` to each in turn. For range 13, append the pathspec `':!.vscode' ':!REVIEWS.md'` to each command's own pathspec. First run `git diff --shortstat` per range and confirm it matches the table's files and diffstat. Stop and report if any does not, because a mismatch means the range was read wrong. Report every hit with a verdict: accepted or blocking, and why. Expect and explain, rather than ignore: paths under `audits/` and `scripts/test_audit_check.py` in ranges 8, 9 and 13 predate the restructure; `REVIEWS.md` moves inside range 10, which is adoption working; and the plan-reference grep excludes `*.md`, so a hit in `docs/code-review-tracking.md` means its pathspec was dropped. No commit. |
| 5b | high | sonnet | none | Wave 2a, code quality, per `PUSH-AUDIT.md`, over the fourteen ranges, taking 5a's report as input. Apply D5.3: read each range for intent, then resolve the code to its current home using the survey's path map before writing a finding. Three questions matter most. (1) **Did a later range undo an earlier one without saying so?** Range 12 (`0ba8920`) deleted a test that asserted every stamp matches its content. `ba3876a`, inside #47 and outside this audit, added that test as one of two guards. Confirm that the other guard, `stamp` refusing a stale stamp, still exists in `scripts/review-tracking.py` at HEAD, and say where. (2) **`stamp`'s out-of-scope announcement (range 11) and `scope-orphans` (range 13) both decide what is in scope.** Do they use one matcher, or two that could disagree? (3) **The `next` subtree and order options (range 14)**: what happens with a subtree that matches no in-scope file, or a scope file whose include order lists a pattern twice? Apply the comment-proportion and source-file-size blocks to `scripts/review-tracking.py` at HEAD, and give its line count. Report findings with the current file and line, blocking or advisory, and the range that introduced each. |
| 5c | high | sonnet | none | Wave 2b, test review, per `PUSH-AUDIT.md`, over the fourteen ranges, applying D5.3. Tests now live in `scripts/tests/test_review_tracking.py` and `scripts/tests/test_review.py`. Ranges 8, 9 and 13 wrote theirs into `scripts/test_audit_check.py`, which is gone, so the first question is whether every assertion those ranges added survived the moves. Name any that did not. For each subcommand and check the ranges introduced or changed, find a test exercising each status it can return: `next` with a subtree and with ordering; `stamp`'s out-of-scope path; `scope-orphans`; `ReviewMarksPreCommit` pass, fail and not_applicable; `ReviewScopeCompleteness` pass, fail and not_applicable. Name the gaps. Range 12 removed a test. Say whether anything still proves a committed stamp matches the content it names, and if nothing does, whether the `review-coverage` criterion makes that redundant. Name the test that would fail if prune stopped running, or say there is none. Report grouped by file. |
| 5d | medium | sonnet | none | Wave 2c, documentation review, per `PUSH-AUDIT.md`, over the fourteen ranges. `docs/code-review-tracking.md` was edited by every one of them. Check the following: (1) no page outside `docs/plans/` describes the removed hooks as live, so grep `docs/`, `README.md`, `AGENTS.md`, `ARCHITECTURE.md`, `templates/` and `.claude/skills/` for `pre-commit-hooks`, `review-stamp`, `review-prune`, `post-merge`, `post-checkout` and `default_install_hook_types`, and judge each hit; (2) the default scope (code plus docs plus YAML, from ranges 4 to 6) is stated once, and agrees between `docs/code-review-tracking.md`, the adoption skill and this repository's own `.vscode/review-scope.toml` comments; (3) the template README's claim that copies "must stay byte-identical" is not contradicted by any page that tells an adopter to edit a copy; (4) the adoption procedure in the doc and in `.claude/skills/review-tracking-adoption/SKILL.md` lists the same steps in the same order. The skill was added in range 7 and edited since. Also check that the two `*Corrected on 2026-10-24*` notes in this plan's analysis are accurate against the doc. Report as a bullet list. |
| 5e | high | opus | none | Wave 2d, security, per `PUSH-AUDIT.md`, over the fourteen ranges. Read the actual code at HEAD. The surface this plan introduced to this repository is range 10 (`a33e694`). It added `.github/workflows/prune-reviews.yml` and `tools/ci-prune-reviews.sh`, which push to `main` with a bot token. Both have since been replaced by the template copies, which `PLAN-review-import.md` phase 5 audited. So the question is narrow: did range 10 add anything to `ci.yml` (12 lines) or elsewhere that the template replacement left behind and that still runs? Read `ci.yml` at HEAD for it. Then the path surface: `scripts/review-tracking.py` builds paths from `.vscode/review-scope.toml`, from weAudit JSON and from `next`'s subtree argument (range 14). Apply the path-traversal block to each, bearing in mind that the first two need a merged pull request to change and the third is a local argument. Then `stamp` (ranges 2 and 11), which shells out to `git` with paths from weAudit JSON: is any of them passed where git would read it as an option? Report severity, current file and line, class, and fix. |
| 5f | medium | sonnet | none | The removed-interface consumer check, D5.6. Fetch every Shaken Fist clone beside this one under `src/shakenfist/`, skipping `-wt-` worktrees. Use `gh repo list shakenfist --limit 200` to find any repository with no local clone, and use `gh search code --owner shakenfist` for those. Grep each default branch (`git grep <pattern> origin/HEAD`) for: `review-stamp`, `review-prune`, `pre-commit-hooks.yaml` together with `development`, `repo: https://github.com/shakenfist/development` in any `.pre-commit-config.yaml`, `test_every_stamp_matches`, `test_audit_check`, `check_review_coverage` and `check_review_marks`. State every command run and the repositories covered, and count the repositories searched. A hit in a plan file or a changelog is history, not a consumer, so say which each hit is. No commit, no issue, no edits in any repository. |
| 5g | high | opus | none | Management triage, in the management session. Read all five reports and check each one's totals table against the survey's. Work the management checklist at the end of `PUSH-AUDIT.md`. Decide each finding blocking, advisory or declined. Check every finding attributed to an excluded range against the owning plan's Outcome (D5.2). Write an **Outcome** subsection here: the ranges and totals seen, what wave 1 found, what each of 5b to 5f found, and for every finding where it will be fixed or why it was declined. Cite development#307, and record D5.4's list as not audited, with the reason. |
| 5h | low | sonnet | none | Close the plan, per D5.8. If 5g recorded no findings to fix, set Phase 5's row and the plan's row in `docs/plans/index.md` to `Complete`, leave the `Merged` cell empty, and update the index Intent to say the push audit ran and found nothing. Otherwise leave both rows `In progress` and say in the Outcome that the findings pull request carries the close-out: it sets both to `Complete` and fills the `Merged` cell with this phase's merge commit. Fill the `Bugs fixed during this work` section from what the plan's landings fixed, at least the auto-stamp of a stale mark that Phase 2 step 5 records, or say none were recorded. Run `pre-commit run --all-files`. Commit subject: `Record the review tracking push audit.` |

#### Risks and mitigations

* **An agent reads the wrong range and reports a clean audit.**
  This is the characteristic failure of auditing merged work. Every
  brief carries the ranges literally, and every report carries a
  totals table. In 5g, the management session compares each table
  to the survey's, row by row, before reading any findings.
* **Range 13 is read without its pathspec**, and the review marks
  in it get audited as code. The unscoped total is +771 -42 and the
  scoped total is +515 -20, so the totals check catches it.
* **A finding is reported against a path that no longer exists.**
  D5.3 and the survey's path map cover this. 5g rejects any finding
  without a current path or a "no longer exists" statement.
* **Two plans audit the same diff and disagree.** D5.2's exclusion
  list is explicit. 5g checks findings against excluded ranges
  against the owning plan's Outcome before deciding them.
* **The consumer grep misses a repository with no local clone.** 5f
  reconciles against `gh repo list` and states its count. The
  management session checks that count against the organisation's
  repository count.
* **The issue D5.5 files reads as a fix request, and someone
  re-syncs without the criterion.** The drift then recurs at the
  next template change. The issue asks for both, and says the
  criterion is what prevents recurrence.

#### Definition of done

* The Outcome's totals table matches the survey's on every row. This
  prints fourteen labelled lines that must equal the table's Files
  and Diffstat columns. Range 13 prints last, because it needs its
  pathspec:

  ```
  for r in d180c39^..d180c39 0957d15^..ee6d4ea a3b7f50^1..a3b7f50 \
      ed10382^..ed10382 079e34e^1..079e34e ba4a6a7^..ba4a6a7 \
      b51f550^..b51f550 ccaac6e^1..ccaac6e 127079e^1..127079e \
      a33e694^1..a33e694 44748d4^1..44748d4 0ba8920^1..0ba8920 \
      f624d26^1..f624d26; do echo "$r $(git diff --shortstat $r)"; done
  echo "39f8d5b^1..39f8d5b $(git diff --shortstat 39f8d5b^1..39f8d5b -- ':!.vscode' ':!REVIEWS.md')"
  ```

* Every finding in the Outcome has a current path and line or a "no
  longer exists", and a disposition: fixed in a named pull request,
  or declined with a reason.
* 5f's report names every repository searched, and their count equals
  `gh repo list shakenfist --limit 200 --json name --jq length`,
  archived repositories included. Searching those as well costs
  nothing and leaves no gap to name.
* The Outcome links development#307.
* Phase 5's row and the plan's index row agree with the Outcome. If
  it records findings to fix, both read `In progress`, and
  `grep -c '^| 5. Push audit | In progress | |'
  docs/plans/PLAN-code-review-tracking.md` returns 1. If it records
  none, both read `Complete` and the `Merged` cell is empty. The
  anchor matters: without it the grep also matches this bullet,
  which quotes the row.
* `pre-commit run --all-files` passes, and `git diff origin/main
  --stat` on the branch lists only this plan and `docs/plans/index.md`.

#### Back brief

Before running 5a, back brief Mikal: the fourteen ranges, the
exclusion list, and D5.7. If the attribution of landings in the
survey looks wrong to him, that is the cheapest point to correct it.
Every brief inherits it.

#### Outcome

The audit ran on 2026-10-24 (AEDT), in the order the step table
gives. 5a ran first and alone. 5b to 5f then ran in parallel as
read-only reporters, and 5g's triage below is the management
session's. The back brief was taken first, and Mikal confirmed the
attribution of landings in the survey unchanged.

**Ranges.** All five reporting agents reproduced the survey table
exactly, every one of the fourteen rows, including range 13 with its
pathspec. The management session re-ran ranges 2 and 13 by hand. No
report was re-run.

**Wave 1 (5a): clean.** `pre-commit run --all-files` passed. The only
grep hits were these, all accepted:

* stdlib imports in range 2;
* one plan citation in range 2's `.pre-commit-hooks.yaml`, which
  range 3 deleted with the file;
* two files over the size threshold at HEAD,
  `scripts/review-tracking.py` (1,560 lines) and
  `scripts/tests/test_review_tracking.py` (2,135);
* paths that predate the restructure.

**Wave 2.**

* 5b (code quality): ten advisory findings, none blocking.
* 5c (tests): no assertion lost in either move, 162 tests passing.
  35 mutants ran under `timeout`, and four survived.
* 5d (documentation): no gaps found. One imprecision, in this plan's
  own correction note, is fixed in `8e1b850`.
* 5e (security): one medium finding, two low and two informational,
  with four proofs of concept. There is no option injection: every
  path from weAudit or the scope config reaches git prefixed with `:`
  or `HEAD:`, so none can start with `-` (PoC 3). `shell=True`
  appears nowhere in `scripts/` or `tools/`.
* 5f (consumers of removed interfaces): no live consumer across the
  organisation's 40 repositories. 26 were searched by fetched clone
  and 14 by `gh`; 10 of the 14 are archived.

Every finding below was checked against the tree at `aa8d259` by the
management session. F1 and F2 were reproduced by running the code,
and the rest by reading it.

##### Findings to fix

These land as one follow-up pull request against `main` after this
phase merges. That pull request sets this phase's `Status` to
`Complete` and fills its `Merged` cell, and sets the index row to
`Complete`, as `plan-phase-landing` requires. Until then both rows
read `In progress`. development#315 tracks the findings, so a slip in
the follow-up stays visible outside this plan.

* **F1 (medium, security; range 2, reached through #11).**
  `load_json()` at `scripts/review-tracking.py:283` opens with no
  symlink, containment or size check. `status` and `scope-orphans`
  run it inside every audited repository's checkout, from
  `ReviewCoverage` (`scripts/audit/checks/review.py:200`) and
  `ReviewScopeCompleteness`. A merged `.vscode/x.weaudit -> /dev/zero`
  in any adopter used 1 GiB in under half a second (5e PoC 2,
  re-run here against a copy of HEAD's script). The runner is the
  shared `[self-hosted, static]` pool, and the 60-second timeout
  does not bound memory. The audit's own guarded
  `Repo.read()` (`scripts/audit/repo.py`, `READ_MAX_BYTES`) refuses
  the same link, so this sidesteps a guard the package already
  has. No content disclosure was achieved. `PLAN-review-coverage.md`'s
  audit recorded the timeout case only, so this is new. Fix: route
  every read through one guarded opener: realpath within the
  toplevel, regular files only, `O_NOFOLLOW`, and a size cap
  matching `READ_MAX_BYTES`.
* **F2 (low, security; range 2).** `write_json()` at `:291` and the
  `REVIEWS.md` write at `:551` follow symlinks. A newline in a
  weAudit path injects a line into the target (5e PoC 4). This needs
  a merged pull request, by someone who can usually already run code
  on the runners, so it is low. Fix: the same opener for writes, and
  reject control characters in marked paths.
* **F3 (low, integrity; range 2).** `stamp` resolves
  `blob_sha(':%s' % path)` (`:633`, `:645`). A weAudit path `/x`
  becomes `:/x`, git's commit-message search, and stamps a commit
  SHA. It heals itself at prune. Fix: `':0:%s'`.
* **F4 (low, latent false pass; range 8, widened by 9).**
  `pre_commit_excludes_review_marks()` at
  `scripts/audit/checks/review.py:64` accepts any `exclude:` line in
  the file:
  * a YAML block scalar (`exclude: |`) captures `|`, which matches
    every path (5b reproduced it);
  * an exclude on an unrelated hook counts for the rewriting hook,
    which it does not protect (5c).

  Either way `review-marks-pre-commit` passes a repository whose
  rewriting hook still rewrites the marks. None of the six local
  adopter clones trips it today. Fix: reject a block-scalar
  indicator or an empty value, and require the matching exclude on
  the rewriting hook or at the top level. Add tests for both.
* **F5 (advisory, robustness; range 2).** `cmd_prune` indexes
  `stamps[path]['sha']` (`scripts/review-tracking.py:696`, `:705`).
  For a sidecar entry with no sha it raises `KeyError`, right after
  `stamp` has told the user to run `prune`. Fix: `.get('sha')`.
* **F6 (test gaps; ranges 8 and 11).** Three mutants survived:
  dropping `mixed-line-ending`, `pretty-format-json` or
  `file-contents-sorter` from `FILE_REWRITING_HOOK_IDS`
  (`review.py:42`). So did one ignoring partial marks in
  `stamp`'s out-of-scope detection (`review-tracking.py:628`). Fix:
  one test per hook id (or a parameterised one), and a partial-mark
  out-of-scope case.
* **F7 (advisory, small; ranges 2 and 11).** The `--help` text
  reflows the module docstring into one paragraph (`:1530`): use
  `RawDescriptionHelpFormatter`. The out-of-scope banner says
  "excluded by review-scope.toml" (`:587`) for files that are only
  unmatched: say "not in scope".
* **F8 (informational; range 13, and #11).** Both review checks run
  the child without `environment_without_credentials()`
  (`review.py:200`, `:285`). `SfuiVendor` uses it. The child runs no
  repository-chosen code today. Fix: pass the scrubbed environment,
  in the same PR as F1.

##### How the findings were resolved

The findings pull request fixed every item above, in three commits,
each through a sub-agent and reviewed in the management session:

| Finding | Commit | Proof |
|---------|--------|-------|
| F1, F2, F8 | `30bf5aa` Guard every review state read and write. | 11 mutants killed; 5e PoC 2 re-run, refused with exit 2 |
| F3, F5, F7, F6 (partial marks) | `a914f48` Fix four small review-tracking.py defects. | 5 mutants killed |
| F4, F6 (hook ids) | `fc52a33` Judge review-mark excludes per rewriting hook. | 19 mutants killed; every adopter's verdict unchanged |

F1, F2 and F8 share one commit because they share the new I/O
helper's hunks and the tests that drive the child.

The pull request's automated review found F2 only partly fixed. The
first commit guarded marked paths, but the reviewer name, stamp dates
and shas, imported fields and line numbers also reach a `REVIEWS.md`
cell. `00d4425` guards every cell, table-escapes `|`, refuses before
writing in `stamp`, `prune` and `import`, and refuses a malformed mark
entry without a traceback (21 mutants). `aeb765e` refuses malformed
JSON and format characters, and stops dropping an import whose date
is null (5 mutants).

##### Findings declined, with reasons

* **The `next` wording in both wrapper copies is stale**
  (`tools/review-tracking.sh:11` and its template): it says
  "random", but range 14 added subtree and `listed` order. This is
  not fixed alone, because the template copies must stay
  byte-identical and an edit here means a re-sync of six
  repositories. It goes with the re-sync development#307 already
  asks for, and is noted there.
* **`.vscode/review-scope.toml` is in `ci.yml`'s `paths-ignore`**
  (`.github/workflows/ci.yml:39`; 5b and 5e F5). A scope-only pull
  request skips lint, the smoke run and the automated reviewer,
  although the scope file is policy rather than review state. This
  is not a defect in this repository's copy. It is the fleet
  convention in adoption step 8 (`docs/code-review-tracking.md:318`)
  and the adoption skill, carried by seven repositories. Changing it
  is a convention decision for Mikal, and the findings pull request
  either takes it or records why not. *Declined there, on
  2026-10-24, and filed as development#316.* The change touches step
  8, the adoption skill, any criterion asserting the four-path set and
  seven repositories' `ci.yml`, so it needs its own change and review
  rather than being folded into security fixes.
* **The three review checks repeat the scope-file skip in `run()`**
  instead of `applies()` (`review.py:154`, `:189`, `:280`). Verdicts
  are unaffected. This is a style point outside any range's intent,
  and it belongs to the next change that touches them.
* **The 3.11 floor** (`tomllib`, `review-tracking.py:237`) is
  undocumented for by-hand use. The audit runner is guarded
  (`ci.yml:97`). The failure is a clear `ImportError`, and no
  by-hand user is known to be on an older interpreter, so this is
  declined until one is.
* **Comment proportion**: the `report_out_of_scope` and
  `scope_orphans` docstrings narrate more than they need
  (`:556-573`, `:1393-1413`). Advisory. Both carry their reasons,
  and a person read them in full on 2026-10-16.
* **Source file size**: `scripts/review-tracking.py` is 1,560 lines,
  over the 1,500 that wants a stated reason. About 1,000 of them are
  the import machinery from `PLAN-review-import.md`, outside this
  plan. Its seam is clean (the imports file, the history walk,
  `verify_commit`, `cmd_import`), and splitting it is recorded under
  Future work rather than done in an audit.
* **Smaller gaps, declined for their size, as advisories:**
  * `next` ignores a mistyped path among valid ones;
  * `scope-orphans` has no assertion on its zero-orphan message;
  * `prune-reviews.yml`'s triggers are pinned only by byte
    comparison with the template;
  * range 13 did not state its blast radius, a historical record
    that the compliance page now carries.

##### What the audit did not do

* It did not audit the adoption pull requests in other
  repositories, per D5.4. They are listed there, with kerbside#254
  named as the unaudited CI logic.
* It did not read `import` (`review-tracking.py` around
  `:781-1300`), which belongs to `PLAN-review-import.md`.
* It did not re-review the template copies of `prune-reviews.yml`
  and `ci-prune-reviews.sh`, which that plan's audit read.
* The 14 uncloned repositories in 5f were covered by `gh search
  code`, which only indexes default branches and can lag. 5f gives
  their result as indicative.

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
* A criterion holding every adopter's review tracking CI copies
  byte-identical to `templates/review-tracking/`, as that template's
  README requires. development#307 records the two repositories that
  have drifted and asks for both the re-sync and the criterion.
* Split the import machinery out of `scripts/review-tracking.py`,
  which is 1,560 lines, about 1,000 of them `import`. The push
  audit found the seam already clean.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed.

* A scoped pre-commit run while the ryll wiring was being prepared
  auto-stamped a stale mark at the file's current content. The
  bootstrap rule in `9f8d47f` made bootstrap land with the wiring,
  and is now adoption step 6 (Phase 2 step 5).
* weAudit records derived directory entries in `auditedFiles`, and
  the script treated them as files (`ee6d4ea`).
* `stamp` skipped an already-stamped file whose content had since
  changed, silently and with exit zero, so a stale mark could reach
  the default branch. Fixed in `ba3876a`, which landed under #47 and
  is recorded in `PLAN-consistency-audits-v2.md`.

* Review state was read with a bare `open()`. The daily audit's
  `status` and `scope-orphans` runs, inside each audited checkout,
  would follow a committed symlink out of the clone or into
  `/dev/zero`. Writes followed symlinks, and a newline in a marked
  path could inject a line into `REVIEWS.md`. Fixed in `30bf5aa`,
  which also runs both review criteria without credentials, and
  completed in `00d4425` and `aeb765e`, which guard every other
  `REVIEWS.md` cell.
* `stamp` read a marked path beginning with `/` as git's
  commit-message search, and `prune` raised `KeyError` on a stamp
  with no sha. Fixed in `a914f48`, with two smaller wording fixes.
* `review-marks-pre-commit` passed a block-scalar `exclude: |`, and
  an exclude on an unrelated hook. Fixed in `fc52a33`.

The push audit found all three (Phase 5 Outcome, F1 to F8), and
development#315 tracked them.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
