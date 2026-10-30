# Scheduler reservations phase 8: documentation and the client release

## Prompt

Before responding to questions or discussion points in this document,
read the master plan
([PLAN-scheduler-reservations.md](PLAN-scheduler-reservations.md)),
its phase 8 stub and status notes, the phase 0 decisions document
(D8, D10, D11, D16, D17) and the phase 7b plan in client-python
(`docs/plans/PLAN-scheduler-reservations-phase-07b-client-views.md` on
that repository's `develop`). Then read the documentation this phase
changes as it stands today, rather than as the stub remembers it:
`docs/operator_guide/scheduler.md`, `docs/operator_guide/database.md`,
`docs/developer_guide/subsystem_internals.md`,
`docs/developer_guide/database_internals.md`,
`docs/developer_guide/standards.md`, `docs/user_guide/affinity.md`,
`docs/release_notes/v07-v08.md`, `docs/glossary.md` and
`ARCHITECTURE.md`. Ground every claim about what the documentation
says in the file, with a line number.

All planning documents live in `docs/plans/`. This file follows the
conventions in `PLAN-TEMPLATE.md`.

## Planning effort

Planned at **high** effort in the management session, because the
survey had to decide what of the stub's scope still exists to be
done, and because one step is a release, which cannot be undone.
Review at **medium**: the decisions below are narrow and each step is
a self-contained documentation change with a falsifiable check.

## Scope

The master plan's stub for this phase was written on 2026-05-22 and
amended on 2026-08-29 (the release) and 2026-10-20 (read through 7b's
commands). It describes four deliverables: an operator guide for
claims and capacity, a developer-guide write-up of the guarded-UPDATE
idiom (D10), user-facing affinity migration notes, and one
client-python release covering phases 4b to 7b. The survey below found
that the first and third already exist, written by the phases that
built the features, and that the stub's real remainder is narrower
and more specific than it reads.

**In scope:**

- The client-python release the stub assigns here, and the sentence
  in the operator guide that names the client version an operator
  needs.
- Correcting `docs/operator_guide/scheduler.md` where it says the
  client cannot do what it has been able to do since 4b and 7b, and
  leading its claims and diagnosis sections with the commands rather
  than the endpoints they read.
- The D10 write-up: the guarded-counter idiom as a pattern a future
  finite-resource claim can copy, which nothing in `docs/` is today.
- Release notes for what phases 3 to 7b changed, which
  `docs/release_notes/v07-v08.md` does not mention at all.
- Glossary entries for the vocabulary the guide uses and the glossary
  does not define: *capacity claim*, *coverage state*, *capacity
  ledger*, *scheduler capacity reconciler* and *guarded UPDATE*.
- Bookkeeping that the documentation audit in phase 9 should find
  already done: the master plan's success criteria, which still
  describe a `node_reservations` table phase 0 decided against and a
  batch-create API D8 deferred; and two open issues this plan's
  earlier phases resolved without closing.

**Out of scope, and why:**

- **Phase 5's documentation.** The flip from advisory to hard
  enforcement changes what every "advisory this release" sentence
  says. Phase 5 owns that delta. This phase records where those
  sentences are (*Definition of done*, item 12) so phase 5 can find
  them.
- **A server release.** The server is at `v0.8.0-rc4`, and the
  operator guide is published from `develop`. The stub commits this
  phase to one client release only, and a server release has its own
  process (`docs/developer_guide/release_process.md`) and its own
  timing.
- **Changes to client-python's own pages.** 7b wrote
  `docs/cluster-capacity.md` and `docs/namespace-claims.md` there;
  this phase links them and does not restate them (D3). A fact found
  wrong in them is fixed there as a one-line pull request, not here.
- **sfui.** No capacity view exists in the web UI. 7b's *Future
  work* records it.
- **Fixing #3882** (the reconciler does not log the drift it
  corrects). Step 6 verifies the claim against the code and either
  closes the issue with evidence or leaves it open with a note. A fix
  is a code change and belongs to its own branch.
- **The conductor's documentation of claims.** Phase 4c retired the
  definition-of-done item that wanted it; private-ci's own plan owns
  that repository.

## What the survey found

Surveyed on 2026-10-30 against shakenfist `61a14af571` and
client-python `44696dd` (both `develop`). The stub's four
deliverables, checked one at a time:

**F1. The operator guide exists and is substantial.**
`docs/operator_guide/scheduler.md` is 1152 lines. It carries the
placement pipeline (`:12`), the guarded admission (`:342`), namespace
claims with their advisory status, drawdown semantics, peak, growth
and expiry, the two states, the cross-namespace listing and the
refusal codes (`:431-696`), the configuration reference (`:697`),
placement diagnosis from audit events (`:832`), the cluster-wide
ledger and the exclusion reasons in `/admin/resources` (`:945`,
`:1005`), affinity debugging (`:1038`) and mixed-version behaviour
(`:1140`). The reconciler is documented for operators in
`docs/operator_guide/database.md:738-930`. The stub's "operator guide
for claims and capacity (including the two service classes and the
reconciler)" was written as though none of this existed; phases 3, 4,
4a, 6, 7 and 7a each wrote their part as they landed. The stub is
corrected at source in this phase's planning commit.

**F2. The guide contradicts the client three times.** Each of these
was true when written and is false now:

- `:444`: "`sf-client` has no claim verbs yet, so for now a claim is
  created with an HTTP request rather than a command." The verbs
  landed in client-python#375 (phase 4b, 2026-08).
- `:495`: "admin-gated like the claim verbs themselves since
  `sf-client` has no events verb for either yet", followed by two
  `GET` URLs. `namespace events` and `namespace claim events` landed
  in client-python#442 (phase 7b).
- `:668`: "`sf-client` has no claim verbs, so there is no quick
  interactive way to confirm that the claim pathway works". Same
  correction as `:444`.

Beyond the three false sentences, the guide never names `sf-client
node capacity`, `sf-client namespace claim list --all` or the
`--coverage` filter (`grep -c 'node capacity'` is 0; the only
`sf-client` commands it quotes are `instance events`, `instance show`
and `instance list`). Its cross-namespace listing section (`:621`)
and its whole diagnosis section (`:916-1037`) are written against the
endpoints. That is the stub's 2026-10-20 amendment, "read through
phase 7b's commands rather than `curl`", and it is the operator-guide
work that remains.

**F3. The "two service classes" are described but never named.** The
claims section explains that unclaimed placements are admitted only
into what active claims have not spoken for (`:466`, `:965`), which is
D17's claimed-versus-best-effort split, but neither "service class"
nor "best-effort" appears in `docs/`. An operator cannot search for
the concept. Step 2 names it once, where the behaviour is explained.

**F4. The affinity migration notes exist.**
`docs/user_guide/affinity.md:216-242` ("The weighted form is
deprecated") explains the mapping from weights to `prefer_with_tag` /
`prefer_without_tag`, that magnitude is discarded, when ordering can
change, and the deprecation event; `scheduler.md:1084-1138` carries a
script that finds instances still on the weighted form. Phase 6 wrote
both. Nothing remains for this phase except a release-notes entry
(F6).

**F5. The D10 write-up does not exist as a pattern.** The scheduler's
own instance of the idiom is documented thoroughly:
`docs/developer_guide/subsystem_internals.md:6-420` (the admission
transaction, the demand clause, the mirrored limit arithmetic, the
peak high-water mark) and the ER_CHECKREAD rule at
`docs/developer_guide/standards.md:91`. But D10's purpose was that
"future finite-resource claims (per-session floating IPs, etc.) can
copy the pattern", and no page says what the pattern is independent
of the scheduler: when it applies and when a `UNIQUE` constraint or
the IPAM reservation path is the right tool instead; the three parts
(materialised counters, a guarded `UPDATE` as the transaction's first
statement, a reconciler that corrects drift and is the only creator
of rows); the rules phase 0's benchmark and the later phases
established (no per-decision rows, canonical write order, which
MariaDB errors to retry, never an aggregate-guard
`INSERT ... SELECT`); and how a read of the counters reports a
degraded or absent row. `database_internals.md` already has one such
pattern section, "SQL Filter-Pushdown Discipline" (`:158`), and the
reconciler's description sits oddly inside "Cluster Operation
Storage and Work Queues" (`:497-530`, within `:403-539`), which is
where `ARCHITECTURE.md:204-206` sends a reader looking for "the
capacity tables and their reconciler".

**F6. The release notes do not mention any of it.**
`docs/release_notes/v07-v08.md` has a section for phase 00a ("Load-aware
scheduling and per-node resource reservations", `:1291`) and nothing
for guarded admission, namespace claims, the advisory ceiling, the
capacity tables and their schema step, the affinity constraint form
and the deprecation of weights, the exclusion reasons, the cluster
ledger, the cross-namespace listing, or any of the client commands.
`grep -c` for `prefer_with_tag`, `namespace claim`, `node capacity`,
`cluster_capacity`, `coverage_state` and `/admin/claims` is 0 for each.
The only affinity entries (`:342-349`) are the validation fixes.

**F7. The glossary defines none of the vocabulary.** Its thirty-odd
entries include `claim` only in the OIDC sense (bound claims on a
mapping rule), which is the overload the glossary exists to
disambiguate. *Capacity claim*, *coverage state*, the *capacity
ledger* (`cluster_capacity`), the *reconciler* and *guarded UPDATE*
have no entry.

**F8. The client release is overdue by the stub's own rule.**
client-python's newest tag is `v0.8.3` (2026-07-24). Forty-two pull
requests have merged since, including the claim verbs (#375), the
transient-refusal retry (#399), agent-operation deadlines (#380), the
7a verb (#438) and the 7b views (#442). The release mechanism is one
`v*` tag plus one approval in the `release` environment; since #432
(2026-10) the workflow's `ci-gate` job requires the tagged commit's
merge-queue `functional-tests.yml` run to have succeeded, and no tag
has been pushed since that gate landed, so this is its first real
run. `docs/development.md` in client-python documents the procedure
and the recovery table. No commit subject since `v0.8.3` names a
removal or rename of a command or option; the one "drop" is a CI
action change.

**F9. The master plan's success criteria describe a design phase 0
rejected.** `PLAN-scheduler-reservations.md:1283-1322` still says
"The `node_reservations` table is the single source of truth for
pending capacity claims", "The new batch-create API exists, is
documented, and is used end-to-end in at least one functional test"
and "The existing in-process `Scheduler()` callers are gone". Phase 0
replaced the reservations table with materialised counters (the
Execution preamble says so), D8 deferred batch creates out of the
plan, and the callers are phase 5's. Phase 9 audits the plan against
its criteria, so the criteria should be true before it does.

**F10. Two issues this plan resolved are still open.**

- #4391, "No API exposes `cluster_capacity`", filed 2026-09-30, carries
  an automated-fix comment deferring it to phase 5. Phase 7a published
  the singleton as the `cluster_capacity` key of `/admin/resources`
  (#4654, documented at `scheduler.md:945`), and 7b renders it as the
  cluster claims ledger in `node capacity` (client-python#442). The
  issue's title is false and nothing closed it.
- #3882, "Scheduler capacity reconciler does not log the drift it
  corrects", filed 2026-08-24, has no comments. The master plan's phase
  2 note says that until phase 4a's close-out "the pass logged
  membership and timing only"; whether the pass logs corrections now
  is a question for the code, and no `drift` string appears in
  `mariadb.py`'s reconcile path. Step 6 answers it rather than this
  survey guessing.

**Corrected at source in this planning commit:** the phase 8 stub
in the master plan now carries a *Corrected by the phase 8 survey*
paragraph recording F1, F4 and F5; the `docs/plans/index.md` row's
arithmetic moves to 13 of 16 with 7b's close-out. The success
criteria (F9) are corrected by step 6, not here, because that is a
change to what the plan promises and deserves its own commit.

## Decisions

**D1. This is a gap-closing phase, not the writing phase the stub
describes.** F1 and F4 found the operator guide and the migration
notes already written. Rewriting a 1152-line guide that is mostly
right would churn text phases 3 to 7a reviewed carefully, invalidate
anchors other pages link, and make phase 9's audit harder to read.
Step 2 therefore edits the sections F2 names and leaves the rest
alone, and the definition of done is phrased as greps against the
specific defects, not as "the guide is good".

**D2. The release is `v0.8.4`, it is step 1, and the documentation
pull request does not merge until PyPI serves it.** The stub's
obligation is that "the guide must not merge claiming a command an
operator cannot install". The cheapest way to honour it is to cut the
release first and let the docs catch up, since the release's latency
is a human approval and the docs' latency is writing. Patch-level
because every client tag since `v0.8` has been patch-level whatever
it carried (`v0.8.3` shipped the VDI console tokens), nothing since
`v0.8.3` removed or renamed a command or option (F8), and the new
commands refuse cleanly against an older server through the
capability tokens, so no operator is forced to upgrade the server to
keep a working client. The tagged commit is client-python `develop`'s
head at the time of tagging, which the management session confirms
has a successful merge-queue `functional-tests.yml` run before
Michael pushes the tag (the gate has no override). A
`workflow_dispatch` dry run of `release.yml` precedes the tag, because
this is the first release through the gate (F8). Michael pushes the
tag and approves the environment; nothing here can or should do
either for him. This is the decision most likely to be argued with:
one could hold the release until the docs are reviewed so the two
land together, or call forty-two merges a minor version. The first
costs a round trip for no reader's benefit, since the guide is
published from `develop` either way; the second contradicts the
repository's own tagging history.

**D3. The operator guide links client-python's pages and does not
duplicate their output.** 7b's plan and the master plan's 7b stub
both say the client page is what "phase 8's operator guide links
rather than duplicates". The guide keeps its field tables, which
document the API and are what a script reads; it gains the command
to run and a link to the client page for what the command prints.
Reproducing `node capacity`'s table here would put the format under
two owners, and it is 7b's. Links use the full GitHub URL to the
client page on `develop`, which is the form `docs/cluster-capacity.md`
already uses in the other direction; nothing in `docs/` links a
client-python page today, so there is no precedent to match and the
anchor checker only follows relative links.

**D4. The idiom write-up is a pattern section in
`database_internals.md`, not a new page, and `subsystem_internals.md`
keeps the scheduler's instance.** F5 found the pattern slot already
exists: "SQL Filter-Pushdown Discipline" is a pattern section in the
same file, and `ARCHITECTURE.md` already sends readers to
`database_internals.md` for the capacity tables. A new page would
need a nav entry, an `AGENTS.md` row and a reason to be found; a
section beside the pushdown discipline is found by the reader who is
already looking for database patterns. The section says what to copy
and links the scheduler's instance for how it was done; it does not
move or repeat `subsystem_internals.md:6-420`. `ARCHITECTURE.md:204-206`
is repointed at the new section, which is where "the capacity tables
and their reconciler" are now explained as a thing rather than as a
paragraph inside the work-queue section.

**D5. Three release-notes sections, written for an upgrader.** Each
says what changed, what an operator must do (`sf-ctl
ensure-mariadb-schema` before rolling daemons, for the tables), what
they will see that is new (events, refusals, the advisory warning),
and where the guide is. They go after "Load-aware scheduling and
per-node resource reservations" (`:1291`), which is the same plan's
first section, and before "Agent operations are bounded by a
deadline" (`:1345`). Three rather than one because they are read by
different people: *Atomic admission and namespace capacity claims* by
an operator of a shared cluster, *An operator-readable capacity
surface* by anyone who has typed `curl` against `/admin/resources`,
and *Affinity constraints, and the deprecation of weights* by a user
who writes affinity metadata. The notes link the guide and the client
pages for detail and do not restate the field tables.

**D6. Five glossary entries, and the OIDC overload is named.** The
glossary's own rule is that an overloaded word names its sibling
meaning. *Capacity claim* therefore carries a "Not to be confused
with" pointing at the bound claims of a mapping rule, and the mapping
rule entry gains the reverse pointer. Each new entry carries a
"Defined in" pointer into the guide or the code, matching the
existing entries' form.

**D7. The master plan's success criteria are corrected in this
phase.** They are documentation of the plan, this is the plan's
documentation phase, and phase 9 reads them as the standard it audits
against. Each false criterion is rewritten to what the plan actually
decided, with the decision number, rather than deleted: a reader of
the plan's history should see that batch creates were promised and
then deferred.

**D8. Issue hygiene is a step, with evidence in the closing
comment.** #4391 is closed citing the merge that published the
singleton and the client that renders it. #3882 is answered from the
code: if the reconcile pass records what it corrected, the issue is
closed with the line that does it; if not, the issue gets a comment
saying so and stays open, and the operator guide's reconciler section
gains one sentence saying corrections are not logged, so an operator
does not look for a log line that does not exist.

**D9. Nothing in `docs/` names a phase number.** The
`check-plan-phase-references` pre-commit hook refuses "phase 7b" in
documentation outside `docs/plans/`. Release notes say what landed and
link the master plan; they do not say which phase landed it. Every
brief below repeats this because it is the error a sub-agent writing
from this plan is most likely to make.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | n/a | none (management session with Michael) | none | **Cut client-python `v0.8.4` (D2).** Not sub-agent work: it is one tag and one approval, both Michael's. The management session does the checks and hands him the two actions. (a) `cd` to the client-python checkout, `git fetch origin`, and record `origin/develop`'s head SHA. (b) Confirm its merge-queue run passed: `gh run list --repo shakenfist/client-python --workflow functional-tests.yml --event merge_group --limit 10 --json headSha,conclusion,databaseId` and match the SHA, remembering that the queue tests a temporary merge commit, so if no row matches read `docs/development.md`'s *Releasing* section in client-python for how `require-ci-success` resolves it, and if in doubt run the dry run first and let the gate say. (c) Dry run: `gh workflow run release.yml --repo shakenfist/client-python --ref develop`, wait for it, and confirm `build` passed and no publishing job ran. (d) Give Michael the two lines to run, `git tag v0.8.4 <sha> && git push origin v0.8.4`, and ask him to approve the `release` environment when the workflow pauses. Use `AskUserQuestion` for the version number and the go-ahead together, once, before (d); the tag is the irreversible act. (e) After the workflow completes, verify from outside the repository: `curl -s https://pypi.org/pypi/shakenfist-client/json \| jq -r .info.version` prints `0.8.4`, and in a fresh venv `pip install shakenfist-client==0.8.4 && sf-client node capacity --help` exits 0. Record the SHA, the run id and the PyPI timestamp in this plan's Execution notes. If the gate fails, follow the recovery table `docs/development.md` links, and record what happened. Steps 2 to 6 do not wait for this step; the pull request's merge does. |
| 2 | high | opus | none | **Correct `docs/operator_guide/scheduler.md` and lead with the commands (F2, F3, D3).** Read the file first; the line numbers here are against `61a14af571`. Make these changes and no others. (i) `:444`: replace the "has no claim verbs yet" sentence with one saying claims are administered with `sf-client namespace claim` (`create`, `show`, `list`, `update`, `delete`), that the REST surface is at `/auth/namespaces/<namespace>/claims` for scripts, and link client-python's `docs/namespace-claims.md` at `https://github.com/shakenfist/client-python/blob/develop/docs/namespace-claims.md` for the commands' options. State the minimum client version in that sentence: `shakenfist-client` 0.8.4. (ii) `:495`: replace "since `sf-client` has no events verb for either yet" and the two `GET` URLs with `sf-client namespace claim events <namespace> <claim>` and `sf-client namespace events <namespace>`, keeping the paragraph's point that the namespace trail outlives the claim and keeping the URLs as the REST equivalent in one clause. (iii) `:621` "Listing every namespace's claims": open with `sf-client namespace claim list --all`, and `--coverage active` / `--coverage expired` for the filter, then keep the existing `GET /admin/claims` text as the API behind it. Check the option names against client-python `develop`'s `shakenfist_client/commandline/namespace.py` (`claim list` is at about `:346`) rather than trusting this brief. (iv) `:668` "Verifying claims on a cluster": replace the false premise with a sentence saying the quick interactive check is `namespace claim create` followed by `node capacity`, and keep the exerciser paragraph as the thorough check. (v) `:466` or `:965`, whichever reads better: name the two service classes once, in one sentence: a namespace with an active claim is *claimed* and its capacity is honoured absolutely against everyone else, a namespace without one is *best-effort* and competes for what claims have not spoken for. Do not add a section. (vi) `:916-1037` "Diagnosing a placement decision" and its two subsections: add, at the top of the `/admin/resources` paragraph at `:916`, that `sf-client node capacity` is the command that renders this response with the two ledgers side by side, and that `sf-client node capacity <node>` cuts it to one node; at `:945` say the cluster claims ledger is the last table `node capacity` prints; at `:1005` say the exclusion reasons appear in `node capacity`'s not-reported list. Keep every field table. Link `https://github.com/shakenfist/client-python/blob/develop/docs/cluster-capacity.md` once, where the command is first named, for what the output means. Do not paste the command's output (D3). **Constraints:** no phase numbers anywhere (D9); no new H2 sections; existing anchors other pages link (`#namespace-capacity-claims`, `#admission-is-a-guarded-capacity-claim`, `#the-cluster-wide-ledger-in-the-resources-response`, `#system-reservations`) keep their headings; wrap at the file's existing width; `grep -n 'has no claim verbs\|has no events verb' docs/operator_guide/scheduler.md` must return nothing when you are done. Run `python3 tools/check-doc-anchors.py` and `python3 tools/check-plan-phase-references.py` before reporting. **Back brief gate:** before editing, return the new text of (i), (ii) and (v) verbatim, and a one-line summary of each other edit, for the management session to agree. |
| 3 | high | opus | none | **Write the guarded-counter pattern into `docs/developer_guide/database_internals.md` (F5, D4), and repoint `ARCHITECTURE.md`.** Add a new H2 section titled `## Guarded counters for finite resources` immediately after "SQL Filter-Pushdown Discipline" (`:158-184`) and before "gRPC Reliability" (`:185`). It is a pattern, written for a developer about to add a second finite-resource claim, and it must stand without the scheduler: name the scheduler only as the worked instance, with links to `subsystem_internals.md#the-claim-admission-transaction` and `#scheduler-and-node-capacity-metrics` for how it was done. Cover, in this order and in about 80 to 120 lines: (a) when it applies: a shared pool with an aggregate limit that concurrent writers draw down, which a `UNIQUE` constraint cannot express (VXLAN ids are a `UNIQUE` case; IPAM has its own reservation rows; say both so a reader does not reach for this where a simpler tool fits). (b) The three parts: a materialised counter row per pool (`used_*` beside `limit_*`), admission as a single guarded `UPDATE ... SET used = used + :x WHERE used + :x <= limit` whose row count is the answer, and a reconciler that recomputes the counters from ground truth on a cadence and is the only writer that *creates* rows, so an absent row means "not yet reconciled" and the guard must treat it as a refusal or an unguarded admission by explicit choice, never as zero. (c) The rules, each with its reason: the guarded `UPDATE` is the transaction's first statement (link `standards.md#a-guarded-update-must-be-the-transactions-first-statement` for ER_CHECKREAD and do not restate it); no per-decision reservation rows, because the placed object is the drawdown record and a row per decision is a reaper's job to clean; multi-row admissions update rows in one canonical order inside one database-daemon RPC and retry on MariaDB 1213, 1205 and 1020; never guard with an aggregate subquery or `INSERT ... SELECT`, because phase 0's benchmark (link `PLAN-scheduler-reservations-phase-00-findings.md`) found it silently overshoots under READ COMMITTED and livelocks under REPEATABLE READ; counters are allocation ledgers, not measurements, so a powered-off instance holds its reservation, and an admission that must compare against a measurement reads it inside the same transaction; drawdown and release are the admission RPC's job, and the reconciler corrects drift between passes rather than being the only writer. (d) What a read reports: `row_present` and `degraded` as separate flags so an absent row and a failed read are never a zero, as `/admin/resources` does (`scheduler.md#the-cluster-wide-ledger-in-the-resources-response`). (e) One paragraph on where the scheduler's instance lives: the three tables (`scheduler_node_capacity`, `namespace_claims`, `cluster_capacity`), `AdmitInstancePlacement` / `ReleaseInstancePlacement`, `ReconcileSchedulerCapacity`, and the reconciler description at `:497-530`, which you leave where it is. Verify every claim in (c) against `shakenfist/mariadb.py` before writing it: find `AdmitInstancePlacement`'s guard, the retry error list, and the canonical order comment, and cite the function names, not line numbers. Then in `ARCHITECTURE.md:204-206` change the link text and anchor so "the capacity tables and their reconciler" points at the new section. Add one sentence to `subsystem_internals.md`'s opening paragraph (`:6-13`) pointing at the pattern section for the general form. **Constraints:** no phase numbers (D9); `python3 tools/check-doc-anchors.py` passes; do not touch `AGENTS.md` (its row 23 already covers this file). |
| 4 | medium | opus | none | **Three release-notes sections in `docs/release_notes/v07-v08.md` (F6, D5).** Insert them after the "Load-aware scheduling and per-node resource reservations" section ends (just before `## Agent operations are bounded by a deadline, not a fixed backstop`, about `:1345`) in this order, matching the file's existing voice and bullet style (read `:1291-1344` first). **(1) `## Atomic admission and namespace capacity claims`:** placement is now admitted by a guarded capacity claim in the same transaction as the placement write, so two creates racing the last slot admit exactly once, and RAM and disk are protected as CPU is; three new tables (`scheduler_node_capacity`, `namespace_claims`, `cluster_capacity`) created by `sf-ctl ensure-mariadb-schema`, which must run before daemons are rolled after this upgrade; a reconciler on the elected cluster node rebuilds them every five minutes; namespace capacity claims (`sf-client namespace claim`, `/auth/namespaces/<ns>/claims`), admin-only, cluster-wide not per-node; **advisory this release**: exceeding a claim is recorded as the `placement admitted over namespace capacity claim` event and not refused, while an active claim does reserve capacity from namespaces without one; the new refusal codes (507 / 503 / 409) on claim create; the D13 demand clause and `SCHEDULER_DEMAND_PER_VCPU` only if the config key is user-facing (check `scheduler.md#configuration-reference`). Link `/operator_guide/scheduler/#namespace-capacity-claims` and `/operator_guide/database/`. **(2) `## An operator-readable capacity surface`:** `/admin/resources` now carries `cluster_capacity` (the ledger admission compares against) and `excluded` (why a hypervisor is missing from `per_node`, with the four reasons); `GET /admin/claims` lists every namespace's claims with a `coverage_state` filter; capability tokens `cluster-capacity`, `cluster-resources-excluded` and the claims-listing token (read their exact names from `shakenfist/external_api/base.py` or wherever `capabilities` is assembled; grep for `cluster-resources-excluded`); and in the client, `sf-client node capacity`, `namespace claim list --all`, `namespace events`, `namespace claim events`, `peak_used_*` on `claim show`, and `node resources` fixed. Say the client commands need `shakenfist-client` 0.8.4. Link the two client-python pages by full GitHub URL. **(3) `## Affinity constraints, and the deprecation of weights`:** the constraint form (`prefer_with_tag`, `prefer_without_tag`, `require_with_tag`, `require_without_tag`); hard constraints are a filter stage that can leave no candidate; weights still work, map onto the preference form, magnitude is discarded, ordering can change when magnitudes differed; a deprecation event is recorded when a weighted specification is accepted; the script in the operator guide finds instances still carrying weights. Link `/user_guide/affinity/` and `/operator_guide/scheduler/#finding-instances-that-still-use-weighted-affinity` (verify the anchor exists). **Constraints:** every fact above is a pointer, not a source: verify each against `scheduler.md`, `affinity.md`, `database.md` and the code before writing it, and omit anything you cannot find. No phase numbers (D9); no "we" narrative about how it was built; no field tables. Links in this file are site-absolute (`/operator_guide/...`) like its neighbours; `python3 tools/check-doc-anchors.py` must pass. |
| 5 | medium | sonnet | none | **Glossary entries (F7, D6).** In `docs/glossary.md`, add five entries in alphabetical position, each in the file's exact form (`**term**{#anchor} -- definition. ... Defined in ...`; read `:18-30` for the form and an existing entry such as `mapping rule` for the "Not to be confused with" style). **capacity claim** -- a namespace's reservation of aggregate cluster vCPU, memory and disk, admin-created, at most one active per namespace, advisory this release; Defined in `operator_guide/scheduler.md#namespace-capacity-claims`; Not to be confused with the bound claims of a [mapping rule](#mapping-rule), which are fields of an OIDC identity token. **coverage state** -- whether a capacity claim is currently covering placements (`active` or `expired`), carried beside and never merged with the object `state`; Defined in `operator_guide/scheduler.md#the-two-states-a-claim-carries`. **capacity ledger** -- the `cluster_capacity` singleton row of totals, claimed and unclaimed-used counters that both cluster-wide admission guards test against, published as `cluster_capacity` in `/admin/resources` and rendered by `sf-client node capacity`; Defined in `operator_guide/scheduler.md#the-cluster-wide-ledger-in-the-resources-response`. **guarded UPDATE** -- a single `UPDATE` whose `WHERE` clause carries the admission test, so that the row count is the decision and two concurrent writers serialise on the row; Defined in `developer_guide/database_internals.md#guarded-counters-for-finite-resources` (step 3's anchor; confirm it) and `developer_guide/standards.md#a-guarded-update-must-be-the-transactions-first-statement`. **scheduler capacity reconciler** -- the five-minute pass on the elected cluster node that rebuilds the three capacity tables from ground truth, expires claims, and is the only writer that creates capacity rows; Defined in `operator_guide/database.md` (find the reconciler heading's anchor). Then add the reverse pointer to the **mapping rule** entry: a "Not to be confused with" clause naming [capacity claim](#capacity-claim). Keep each definition to three or four lines; the glossary defines, the guides explain. **Constraints:** entries alphabetical (the file says so at `:16`); anchors are the lower-cased hyphenated term; no phase numbers (D9); `python3 tools/check-doc-anchors.py` passes. |
| 6 | medium | sonnet | none | **Bookkeeping: the success criteria (F9, D7) and the two issues (F10, D8).** (a) In `docs/plans/PLAN-scheduler-reservations.md`, under `### Success criteria` (about `:1283-1322`), rewrite three bullets. "The `node_reservations` table is the single source of truth..." becomes: the three materialised capacity tables are the single source of truth for admission, and drift between them and ground truth is corrected by the reconciler without operator intervention (phase 0 replaced the per-decision reservations table with counters; see the Execution preamble). "The new batch-create API exists, is documented..." becomes: a batch-create API was deferred out of this plan by D8, and the "schedule N, fail on N-1" pattern is addressed by atomic per-create admission plus a namespace claim covering the batch, which is what the conductor does (phase 4c). "The existing in-process `Scheduler()` callers are gone..." stays as written but gains "(phase 5)". Keep every other bullet. This file is under `docs/plans/`, so phase numbers are allowed here. (b) Close #4391: `gh issue close 4391 --comment` with a comment that says the singleton is published as the `cluster_capacity` key of `/admin/resources` since #4654, documented in the scheduler guide's cluster-wide ledger section, and rendered by `sf-client node capacity` since client-python#442; quote the `curl`-free command. (c) #3882: read `ReconcileSchedulerCapacity`'s implementation in `shakenfist/mariadb.py` and the cluster daemon's call site, and determine whether a pass records the corrections it made (a log line, an event, or a counter with the delta). If it does, close the issue with the function and the log or event name quoted; if it does not, comment on the issue with what the pass does record and leave it open, and add one sentence to `docs/operator_guide/database.md`'s reconciler paragraph (`:738-751`) saying that a correction leaves no log line, so a changed counter is found by comparing `node capacity` before and after a pass. Report which branch you took. **Constraints:** do not edit anything else in the master plan; do not touch the Execution table; no phase numbers in `database.md` (D9). |
| 7 | medium | sonnet | none | **Close-out.** Write this plan's `## Outcome`: what each step did, the release SHA, run id and PyPI timestamp from step 1, #4391's closing comment URL, which branch step 6(c) took, and every *Definition of done* item run with its result in a table, in the style of `PLAN-transient-capacity-refusals-phase-06-docs.md`'s Outcome. Populate item 12's list of "advisory" sentences by running its grep and pasting the file:line pairs into this plan, so phase 5 inherits it. Fill the `## Execution notes` with any departure from a brief. Do not set the master plan's Execution row for this phase to `Complete` and do not fill its `Merged` cell: the next phase's close-out does that (`plan-phase-landing`). Run `pre-commit run --all-files` last and record that it passed. |

Steps 2 to 6 are independent of each other and of step 1's
completion, but they all run `pre-commit` in the same tree, so run
them one at a time in this worktree rather than in parallel. Step 7
runs last. The pull request opens after step 7, and merges after step
1's verification in (e) has been recorded.

## Risks and mitigations

- **The first release through `ci-gate` fails for a reason that has
  nothing to do with the code.** The gate requires a merge-queue run
  on the tagged commit, and nothing has exercised it since #432.
  Mitigation: step 1(c)'s `workflow_dispatch` dry run, which builds
  without publishing, and the recovery table `docs/development.md`
  links. A failed gate signs and publishes nothing, so the failure
  mode is a delay, not a bad release. The management session reads
  the gate's output; Michael decides whether to move the tag.
- **A docs sub-agent "improves" text outside its brief.** Step 2's
  brief lists its edits by line and forbids others; the management
  session reviews `git diff --stat` and the hunk count against the
  list, and rejects a diff that touches a section not named.
- **A brief's fact is wrong.** Steps 2, 3 and 4 each say to verify
  against the file or the code before writing, and the definition of
  done checks the result, not the brief. The management session
  spot-checks two facts per step against the source after the step,
  as the review checklist says.
- **Phase 5 later changes the enforcement and misses an "advisory"
  sentence.** Item 12 of the definition of done makes this phase
  record every such sentence in its Outcome, so phase 5's survey has
  a list rather than a grep to invent.
- **A phase number leaks into `docs/`.** The pre-commit hook catches
  it at commit time, and each brief repeats D9. Step 6(a) is the one
  step that legitimately writes phase numbers, and only into a file
  under `docs/plans/`.
- **The release and the docs drift apart.** The guide names
  `shakenfist-client` 0.8.4 as the minimum; if step 1 ends up tagging
  a different version, step 7 changes that string before the pull
  request opens, and the definition of done's item 2 compares the
  guide's version with PyPI's rather than with this plan.

## Definition of done

Each item is a check the management session runs, and the Outcome
records the result.

1. `curl -s https://pypi.org/pypi/shakenfist-client/json | jq -r
   .info.version` prints `0.8.4` (or the version Michael chose), and
   `pip install shakenfist-client==<that> && sf-client node capacity
   --help` exits 0 in a fresh venv. The release run id is in the
   Outcome.
2. `grep -o 'shakenfist-client[^.]*[0-9]\.[0-9]\.[0-9]'
   docs/operator_guide/scheduler.md` names the version PyPI serves,
   and nothing older.
3. `grep -c 'has no claim verbs\|has no events verb'
   docs/operator_guide/scheduler.md` is 0. It is 3 at `61a14af571`.
4. `grep -c 'sf-client node capacity' docs/operator_guide/scheduler.md`
   is at least 3, `grep -c 'namespace claim list --all'` at least 1,
   and `grep -c 'namespace claim events'` at least 1. All three are 0
   at `61a14af571`.
5. `grep -c 'best-effort' docs/operator_guide/scheduler.md` is
   exactly 1. It is 0 at `61a14af571`.
6. `grep -c '^## Guarded counters for finite resources'
   docs/developer_guide/database_internals.md` is 1, and
   `grep -c 'guarded-counters-for-finite-resources' ARCHITECTURE.md`
   is 1. Both are 0 at `61a14af571`.
7. In `docs/release_notes/v07-v08.md`, `grep -c` for each of
   `prefer_with_tag`, `namespace claim`, `node capacity`,
   `cluster_capacity`, `ensure-mariadb-schema` and `advisory` is at
   least 1. Each is 0 at `61a14af571` except `ensure-mariadb-schema`,
   which may already appear elsewhere; for that one the check is that
   it appears inside the new admission section.
8. `grep -c '{#capacity-claim}\|{#coverage-state}\|{#capacity-ledger}\|{#guarded-update}\|{#scheduler-capacity-reconciler}'
   docs/glossary.md` is 5, and the `mapping rule` entry links
   `#capacity-claim`.
9. `grep -c 'node_reservations. table is the single source'
   docs/plans/PLAN-scheduler-reservations.md` is 0 and
   `grep -c 'deferred out of this plan by D8'` in the same file is at
   least 1.
10. `gh issue view 4391 --json state --jq .state` prints `CLOSED`, and
    `gh issue view 3882 --json comments --jq '.comments | length'` is
    at least 1.
11. `pre-commit run --all-files` passes, which includes
    `check-doc-anchors`, `check-plan-phase-references` and
    `check-plan-status`.
12. The Outcome carries the output of
    `grep -rn -i 'advisory' docs ARCHITECTURE.md --include='*.md' | grep -v '^docs/plans/'`
    as the list phase 5 inherits, and every line in it says the
    ceiling is advisory *this release* or equivalent; none says it is
    enforced.
13. No fact about the client's commands is stated differently in
    `scheduler.md`, the release notes and the glossary: the management
    session reads the three mentions of `claim list --all` and the
    three of `node capacity` side by side and records that they agree.
14. The master plan's Execution row for this phase still reads `In
    progress` with `—` in `Merged` on the branch, and the index row
    reads `13 of 16`.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.

**Gates beyond the back brief.** Step 1 asks Michael, once, for the
version number and the go-ahead before any tag is pushed; the dry run
may run before that question is asked. Step 2 returns the replacement
text for its edits (i), (ii) and (v) before editing, because that
text is what an operator searching for the commands will read first
and it is cheaper to agree on a paragraph than to re-review a diff of
a 1152-line file.

## Future work

- **An `sf-client` page in the server's own documentation.** The
  guide links client-python's pages by GitHub URL because the server
  site does not import them. If the docs sync that builds
  `docs/components/` ever imports client-python, those links should
  become site-relative; the sync is `tools/sync_component_docs.py` in
  shakenfist/actions and is not this plan's.
- **A "copy this pattern" worked example.** Step 3 describes the
  guarded-counter idiom; a second consumer (D10 names per-session
  floating IPs) would be the test of whether the description is
  enough. Until one exists, the section is reviewed only by reading.

## Execution notes

Implementation began on 2026-10-30 (Australia/Sydney).

**Back brief answers (2026-10-30).**
- Steps 2 to 6 run one at a time in this worktree, through sub-agents,
  because every step runs `pre-commit` in the same tree; step 7 last.
  Step 1 runs alongside them.
- The release is `v0.8.4`, and the dry run could start at once.
- Step 6 may post to GitHub: close #4391 and comment on or close #3882.
- This session may push the branch and open the pull request once the
  definition of done passes, noting in the body that the merge waits on
  PyPI serving the release.

**Step 1 so far.** client-python `develop`'s head is
`44696ddaf059ff1c8ed6e5ab0235a6366f1d0fc7` (#443). Its merge-queue
`functional-tests.yml` run, 40744116587 on 2026-10-28, succeeded. The
`workflow_dispatch` dry run of `release.yml`, run 41018325275 on
2026-10-30, succeeded: the build job passed and every publishing job
was skipped, as designed. The tag command was handed to Michael; the
tag and the environment approval are his.

**Step 2.** The gate text was agreed with one correction to the brief:
a claimed namespace is not "honoured absolutely" while ceilings are
advisory, so the service-class sentence describes what the ledger keeps
from others instead. The management session added one qualifier the
agent's draft lacked: naming a node drops the ledger and the
not-reported list only in table and simple output, and JSON output
keeps the whole response (`node_capacity()` in client-python's
`commandline/node.py`).

## Outcome

Not yet written.
