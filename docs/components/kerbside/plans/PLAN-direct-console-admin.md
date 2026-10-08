# Restrict /console/direct to admin users (issue #134)

## Prompt

Before responding to questions or discussion points in this
document, explore the kerbside codebase thoroughly. Read
relevant source files, understand existing patterns (the
Rust SPICE proxy in `rust/kerbside-proxy/` and the gRPC
control contract in `kerbside/rpc/`, the source driver
abstraction in `kerbside/sources/`, the REST API in
`kerbside/api.py`, the SQLAlchemy/alembic data model in
`kerbside/db.py` and `kerbside/migrations/`, Pydantic-based
config in `kerbside/config.py`, audit logging, and the .vv file
generation path). Ground your answers in what the code
actually does today. Do not speculate about the codebase
when you could read it instead. Where a question touches on
external concepts (SPICE protocol, QXL, vdagent, libvirt
graphics, OpenStack Nova consoles, oVirt console API,
Shaken Fist), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than
guessing.

Consult `ARCHITECTURE.md` for the overall proxy
architecture, channel model, and connection lifecycle.
Consult `AGENTS.md` for build commands, project
conventions, key file map, and code organisation. The
`docs/` tree contains protocol documentation derived from
upstream SPICE sources and verified against this
implementation — `protocol-overview.md`,
`channel-protocols.md`, `compression-protocols.md`,
`spice-link-protocol.md`, `vd-agent-protocol.md`,
`scancodes.md`, `usb-redirection.md`,
`console-sources.md`, `proxy-architecture.md`, and
`capabilities.md`. Key cross-repo references:

- `shakenfist/ryll` — the Rust SPICE client; the headless
  mode and (eventually) its control socket are the
  primary automated test driver
- `shakenfist/uncalibrated-sextant` — UEFI Rust test
  target guest with on-screen QR digest and serial event
  drain; the assertion oracle for automated SPICE tests
- `shakenfist/kerbside-patches` — patches applied to
  upstream components (Nova, libvirt, etc.) for full
  Kerbside integration; relevant when changes touch the
  hypervisor-facing contract
- `shakenfist/shakenfist` — the Shaken Fist cloud, one of
  the supported source hypervisors
- External: the SPICE protocol sources at
  `gitlab.freedesktop.org/spice/spice-protocol` and the
  reference C client at `spice-gtk`

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

## Situation

`GET /console/direct/<source>/<uuid>/console.vv` is served by
`ConsolesDirectVirtViewer.get` in `kerbside/api.py`. It returns a
virt-viewer file that points the client at the hypervisor itself
(`hypervisor`, falling back to `hypervisor_ip`, with the
hypervisor's own SPICE ports) and embeds the hypervisor ticket as
the password: the persisted ticket for a static source, and for an
oVirt source a fresh ticket acquired from the engine on every
request. A client holding that file never touches the Kerbside
proxy, so the L0/L1 firewall, the size and rate caps, session
bookkeeping, termination and the audit trail all stop applying.

The handler carries only `@verify_token`. That decorator calls
`verify_jwt_in_request` and checks nothing but the signature and
expiry: there is no role, group or ownership notion anywhere in
the API. The session JWT minted by `Auth.post` carries `identity`
(the username), `iss` and `openstack_token`, and login requires
membership of exactly one Keystone group, `KEYSTONE_ACCESS_GROUP`.
So every user who can log in can obtain a proxy-bypassing
credential for every console. The consoles page renders a "Direct"
button beside "Proxy" for every row
(`kerbside/api/templates/consoles.html`).

The maintainer decision recorded on issue #134 is that direct
access is a legitimate feature for administrators, and must be
restricted to them.

Things checked while writing this plan:

- Nothing in CI or `tools/` calls `/console/direct`. The callers
  are the template button and the unit tests in
  `kerbside/tests/unit/test_api.py`, which assert that the
  handler does not log the source password or console ticket.
  Those tests will need an admin session.
- `kerbside/tests/unit/test_conf_example.py` asserts that every
  `Config` field appears in `etc/kerbside.conf.example`, so a new
  setting must be added there in the same commit.
- PR #535 (issue #131, the `AUTH_SECRET_SEED` startup guard) also
  edits the top of `kerbside/api.py`, `kerbside/config.py` and the
  conf example. The overlap is textual, not semantic; whichever
  lands second rebases. #131 matters here because an admin claim
  is only as trustworthy as the signing key: with a forgeable JWT
  anyone can assert admin. #535 should land first or alongside.
- `PLAN-proxmox-source-phase-03a-connect-time-minting.md` lists
  #134 under future work and notes that its decision 4 bounds the
  lifetime of a direct ticket. That narrows the exposure but does
  not close it.

## Mission and problem statement

Make `/console/direct` refuse every caller who is not an
administrator, before it reads the console or acquires a ticket,
and make the set of administrators an explicit operator choice
that is empty unless configured.

Out of scope: per-console ownership (any logged-in user can still
get a *proxied* token for any console — that is the access model
today and changing it is a separate design), non-Keystone login
(issue #300), and the CSRF shape of the proxy `.vv` GET (issue
#319).

## Open questions

These are decisions for the operator. Each has a recommendation;
the plan below assumes the recommendations. Questions 2 (default
empty, so direct access is off until configured) and 5 (audit both
issuance and refusal) were confirmed by the operator on 2026-10-08.

1. **Where does "admin" come from?** Recommendation: a new
   `KEYSTONE_ADMIN_GROUP` setting, checked at login exactly as
   `KEYSTONE_ACCESS_GROUP` is, with the result stamped into the
   session JWT as a boolean `kerbside_admin` claim. Alternatives
   considered: a Keystone *role* (Kerbside's login already speaks
   groups, and the service user can check group membership without
   a project scope, so a role adds a second mechanism for no gain);
   a plain on/off switch for the endpoint (does not meet "restricted
   to admin users").

2. **What is the default?** Recommendation: empty, meaning nobody
   is an administrator and `/console/direct` is refused for
   everyone. This is a behaviour change for deployments that use
   the Direct button today; they must set the group to keep it.
   That is the safe direction for a credential-bypass feature, and
   the change is called out in the PR description and in
   `docs/configuration.md`. The alternative, defaulting to the
   access group, would preserve today's exposure by default.

3. **A configured admin group that does not exist in Keystone.**
   Recommendation: log an error naming the group and let the login
   succeed without admin. Admin is an extra privilege, so failing
   closed means withholding it rather than refusing ordinary access.
   (Contrast the access group, whose absence already fails login.)

4. **Refusal status and ordering.** Recommendation: 403, returned
   before any source is consulted, so no oVirt ticket is ever
   acquired on a non-admin's behalf. The console row is read only to
   decide whether to audit (question 5); the response does not
   depend on it, and every logged in user can already list consoles.

5. **Auditing.** Recommendation: record an audit event against the
   console both when a direct credential is issued and when one is
   refused, naming the user. Today the handler only logs. Issuing a
   proxy-bypassing credential is exactly the security-sensitive
   operation `AGENTS.md` asks to be audited, and the console's audit
   trail is otherwise blind to direct sessions because they never
   reach the proxy. A refusal is audited only when the named console
   exists: nothing reaps `audit_events` (see the comment on the
   Shaken Fist token exchange in `kerbside/api.py`), so auditing
   whatever `(source, uuid)` a caller names would let any logged in
   user write orphan rows without bound.

6. **Staleness.** The claim lives as long as the session JWT
   (`API_TOKEN_DURATION`). Removing a user from the admin group
   takes effect at their next login, not immediately. Recommendation:
   accept and document it; it matches how removal from the access
   group already behaves.

## Execution

One pull request, one commit per step.

| Step | Change | Status | Merged |
|------|--------|--------|--------|
| 1 | Admin group setting and login claim | Complete | |
| 2 | Gate and audit `/console/direct` | Complete | |
| 3 | Hide the Direct button from non-admins | Complete | |
| 4 | Documentation | Complete | |
| 5 | Push audit over the branch | Complete | |

### Step 1: admin group setting and login claim

- `kerbside/config.py`: add `KEYSTONE_ADMIN_GROUP: str`, default
  `''`, beside `KEYSTONE_ACCESS_GROUP`, with a description saying
  empty means no administrators.
- `etc/kerbside.conf.example`: add it as a documented default
  (`# keystone_admin_group =`), which `test_conf_example.py`
  requires.
- `kerbside/api.py` `Auth.post`: after the access-group check, if
  `KEYSTONE_ADMIN_GROUP` is set, find the group in the list already
  fetched and call `check_in_group`; `NotFound` means not an admin,
  a missing group logs an error (question 3). Add
  `'kerbside_admin': <bool>` to `additional_claims`. Reuse the one
  `groups.list()` call rather than listing twice.
- Add a small helper in `api.py`, `session_is_admin()`, that reads the
  verified claims with `flask_jwt_extended.get_jwt()` and returns
  `claims.get('kerbside_admin') is True`, so a pre-upgrade token
  without the claim is not an admin.
- Unit tests, alongside the existing mocked-Keystone login test:
  the claim is true for a member, false for a non-member, false
  and no `check_in_group` call when the setting is empty, and false
  with an error logged when the configured group is missing.

### Step 2: gate and audit `/console/direct`

- `ConsolesDirectVirtViewer.get`: first statement after the
  decorator checks `session_is_admin()`. On failure, add an audit event
  (`'Refused direct console credential to non-admin user <name>'`)
  and return `sf_api.error(403, 'direct console access requires an
  administrator')`. On success, add an audit event
  (`'Issued direct hypervisor credential to <name>'`) next to the
  existing log line. The username comes from `get_jwt_identity()`.
- Unit tests: give the existing direct tests an admin session (a
  helper that mints a JWT with the claim, next to whatever the
  suite already uses to authenticate); add a non-admin test
  asserting 403, that the oVirt source class is never constructed,
  and that the refusal is audited for a real console and not for an
  unknown one; add a test that a token with no `kerbside_admin` claim
  is refused; assert the issuance event on the success path.

### Step 3: hide the Direct button from non-admins

- `Consoles.get` passes `is_admin=session_is_admin()` to the template, and
  `consoles.html` renders the Direct anchor only when it is true.
  This is presentation only; step 2 is the control. The page is
  polled (`refresh=True`), but the button is static markup in the
  fetched content, so no listener concerns apply.
- Unit test: the rendered page contains the direct href for an
  admin and not for a non-admin.

### Step 4: documentation

- `docs/configuration.md`: a row for `KEYSTONE_ADMIN_GROUP`
  stating the default, that it gates `/console/direct`, the
  upgrade behaviour change, and the staleness from question 6.
- Wherever the `.vv` endpoints are described for operators (check
  `ARCHITECTURE.md`'s endpoint list and `docs/installation.md`),
  say that the direct endpoint is admin-only and bypasses the
  proxy. `ARCHITECTURE.md` changes only if it already lists the
  endpoint.
- Leave `docs/plans/PLAN-proxmox-source-phase-03a-...` as written;
  historical plans are not edited.

### Step 5: push audit

Run `PUSH-AUDIT.md` over `origin/develop...HEAD` for this branch,
record the findings and their disposition in *Outcome* below, and
fix or decline each in writing before asking for review.

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

In this repository the Execution phases are the table each master
plan carries, and `Merged` is its last column, after `Status` --
last because a row that omits it must still reach `Status`. Phases
here land as pull requests, so the cell normally holds one merge
commit.

### Phase landing

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

### Phase status

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

!!! note "In this project"

    Phases involving deep protocol research (SPICE channel
    semantics, vdagent behaviour, hypervisor console quirks),
    database schema changes, or architectural decisions should
    be planned at high effort. Phases that are mechanical or
    follow well-established patterns can be planned at medium
    effort.

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

!!! note "In this project"

    The non-obvious invariants that push a step to high effort
    are SPICE channel ordering, capability negotiation, ticket
    and token lifecycle, DB migration safety, and audit log
    guarantees.

    A worked brief for this codebase: instead of "add an
    endpoint for X", write "add a new endpoint `POST /api/v1/X`
    in `kerbside/api.py` alongside the existing `POST
    /api/v1/consoles` handler at line ~210. Use the same
    `pydantic` request-model pattern, the same auth decorator
    (`@requires_auth`), and persist via the `Source` model in
    `kerbside/db.py`. Add an audit event of type `X_CREATED`
    matching the convention at line ~340."

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

!!! note "In this project"

    The project-specific checks referred to above are:

    - [ ] The code passes `tox -eflake8` and `tox -epy3`.
    - [ ] If a DB migration was added, the alembic upgrade and
          downgrade paths were both exercised.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully
implemented because the following statements will be true:

* The code passes `tox -eflake8` and `tox -epy3`.
* Coverage from `tox -ecover` does not regress
  meaningfully for touched modules.
* New code follows existing patterns: pydantic config,
  SQLAlchemy session usage, the audit logging convention,
  the existing `kerbside/sources/base.py::BaseSource`
  interface for new source backends, and the Rust proxy's
  SPICE handling in `rust/kerbside-proxy/`.
* There are unit tests for new logic, and the existing
  tests still pass. Integration coverage in the tempest
  plugin has been extended where the change is
  user-visible.
* Lines are wrapped at 120 characters; Python strings use
  single quotes except for docstrings (which use double
  quotes); trailing whitespace is removed.
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` have
  been updated if the change adds or modifies endpoints,
  hypervisor backends, configuration knobs, DB schema,
  or the SPICE proxy contract.
* Documentation in `docs/` has been updated to describe
  any new features or configuration options. If protocol
  documentation in `docs/channel-protocols.md`,
  `docs/spice-link-protocol.md`, or related files is
  affected, it has been updated and cross-checked against
  the implementation.
* If the changes affect the hypervisor-facing contract,
  the relevant patches in `shakenfist/kerbside-patches`
  have been reviewed and updated if needed.
* The `PUSH-AUDIT.md` audit has been run over the plan's
  accumulated diff, and every finding it raised has been
  fixed or declined in writing in the plan.

### Documentation index maintenance

When creating a new master plan from this template,
update the following file in `docs/plans/`:

* **`index.md`** — add a row to the *Master plans* table
  with the creation date, a link to the plan, a one-line
  intent summary, the initial status, and links to each
  phase plan file. Keep the table in chronological
  order.

When all phases of a plan are complete, update the
status column in `index.md` to *Complete*.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

- Per-console ownership: any logged-in user can still obtain a
  proxied token for any console.
- Issue #300: login is Keystone-only, so deployments without
  Keystone have no administrators and no direct access.
- Issue #319: the proxied `.vv` GET still mints a token.
- Keystone groups are matched by name across every domain, last
  match wins, for both the access and admin groups. Scoping the
  lookup to a configured domain would stop a same-named group in
  another domain being chosen.
- A functional check of the real-Keystone path (admin and
  non-admin users, `KEYSTONE_ADMIN_GROUP`, then 200 or 403 on
  `/console/direct`). It is unit-tested against mocks only; the
  sf-e2e or kolla lane is the natural home.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

- Issue #134, which this plan closes. Related: #131 (PR #535),
  which makes the signing key behind the new claim trustworthy.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->

## Outcome

Steps 1 to 4 landed as planned, with one refinement recorded under
questions 4 and 5: a refusal is audited only against a console that
exists.

The push audit (step 5) ran over `origin/develop...HEAD`. Wave 1 passed
with nothing to report. The judgment reviews raised no blocking
findings. Their findings, and what was done with each:

| Review | Finding | Disposition |
|--------|---------|-------------|
| Code quality | The new `is_admin()` and `username()` helpers shared names with locals in `Auth.post` | Fixed: renamed to `session_is_admin()` and `session_username()` |
| Code quality | One line over 80 columns | Fixed |
| Code quality | The admin `check_in_group` block repeats the access-group block | Declined: two copies, and a helper would hide the different failure handling (a missing access group refuses login; a missing admin group does not) |
| Code quality | A missing admin group is logged on every login | Declined: intended, so a misconfiguration stays visible |
| Tests | No test of an SSL error on the admin group check | Fixed |
| Tests | No test of an admin requesting an unknown console | Fixed |
| Tests | The claim name was never exercised with a real signed token | Fixed: a round-trip test mints and verifies a real JWT |
| Tests | Exact audit strings in assertions | Declined: the wording is the audited behaviour |
| Tests | No functional-lane coverage of the real Keystone path | Deferred to future work |
| Docs | No upgrade note for the behaviour change | Fixed: `docs/installation.md` |
| Docs | `docs/use-cases/openstack.md` should say the direct download is admin-only | Declined: that passage describes the proxied download, which mints a token; the direct one does not |
| Security | The admin claim is only as strong as `AUTH_SECRET_SEED` | Accepted: #131 (PR #535) must land first or alongside |
| Security | `get_console` ignores the source, so audit rows could be filed under a source the caller typed | Fixed: the handler treats a source mismatch as not found, before the gate |
| Security | A refusal still writes one audit row per authenticated request | Accepted: the same as the proxied download; the comment no longer claims more |
| Security | Groups are matched by name across Keystone domains | Deferred to future work; the access group has the same pattern |
| Security | The docs said revocation took effect "at next login" | Fixed: it takes effect when the current session expires |
| Security | Hypervisor addresses remain readable; the gate protects the ticket, not the network path | Declined: unchanged by this plan, and hypervisor SPICE ports already need network controls |
