# Scheduler reservations phase 4b: client support for claims

## Prompt

Before responding to questions or discussion points in this
document, explore both the shakenfist and client-python
codebases thoroughly. Read relevant source files, understand
existing patterns (`apiclient`'s verb shape and its
status-code-to-exception mapping, the `sf-client` click groups,
the functional suite's relationship to a deployed cluster), and
ground your answers in what the code actually does today. Do not
speculate about the codebase when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

The client library lives in the `shakenfist/client-python`
repository, checked out beside this one. Consult its `AGENTS.md`
and `CLAUDE.md` for its own conventions, which are not identical
to this repository's.

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

## Planning effort

Planned at high effort. The implementation is mechanical -- five
verbs over an API that already exists -- but the phase turns on a
premise that has been repeated in four places and is false, and
on how a 720-line functional test that asserts status codes moves
onto a surface that raises exceptions. Both are judgement calls
that are expensive to get wrong and cheap to decide here.

## Situation

Phase 4 shipped the namespace capacity claims API. The client
library has no verbs for it, so the only way to reach it is to
build requests by hand, which is what this repository's own
functional coverage does: `test_namespace_claims.py` drives the
endpoints through `apiclient.Client._request_url()`, with a
docstring explaining that this is deliberate and asking the next
reader not to "fix" it onto verbs until a client release exists.

That constraint came from phase 4's decision D7, is restated in
the master plan's stub for this phase, is restated again in
client-python#364, and is restated a fourth time in the test's
own docstring. It says: the collection installs
`shakenfist-client` from PyPI, so a test written against new
`apiclient` methods cannot pass in CI until a client release
exists, and no server pull request can produce one.

The survey below establishes that this is not true, and has not
been true since 2026-06-24. Cluster CI does not install the
released client. It builds a wheel from a `client-python`
checkout at `develop` and installs that. A verb merged to the
client's `develop` is in this repository's cluster CI on the next
merge-queue run, with no release involved.

This phase therefore has less of a dependency than it was
written to have, and one real one it was not written to have: the
conductor in phase 4c *does* run a released client from PyPI, so
a release still has to be cut -- just not before the functional
coverage can move.

## Mission and problem statement

Give the claims API a client surface worth defending, move this
repository's functional coverage onto it, and cut the release
that phase 4c depends on.

Correct the false premise at each of the four places it is
written down, so that the next person to touch claims does not
inherit a constraint that was already gone when it was recorded.

## Scope

In scope:

- `apiclient` verbs for the five claim endpoints, in
  `shakenfist/client-python`.
- A typed exception for HTTP 503, which the claims API uses for
  both of its retryable refusals.
- `sf-client namespace claim` CLI verbs.
- Moving
  `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_namespace_claims.py`
  onto the verbs.
- Cutting a client release, because phase 4c needs one.
- Correcting the CI premise in the four places it is stated, and
  documenting how cluster CI actually obtains its client.

Out of scope:

- **Any server change.** The API is shipped and advertised; this
  phase consumes it.
- **A cluster-wide capacity view.** `sf-client namespace claim
  list` cannot tell an operator how much unclaimed room is left,
  because no endpoint publishes `cluster_capacity`. That is
  phase 5's "admin capacity view" call site, and inventing an
  endpoint here would duplicate it.
- **Anything about enforcement.** Exceeding a claim is recorded,
  not refused, in this release. Help text must not promise
  otherwise; see D4.
- **The conductor.** That is phase 4c, which this phase unblocks.

## What the survey found (2026-08-27)

Surveyed against shakenfist `45332ff81` and client-python
`8e04857`. The first finding is the reason this phase's scope
changed.

**1. Cluster CI does not install the released client.** The
deploy path is: `functional-tests.yml`'s `(collection)` matrix →
the reusable smoke-cluster workflow →
`shakenfist/actions`'s `build-smoke-cluster` action →
`tools/deploy-collection.sh`, which invokes the collection's
example playbook with `sf_build_local_wheels=true`,
`repo_path=${GITHUB_WORKSPACE}/shakenfist` and
`client_repo_path=${GITHUB_WORKSPACE}/client-python`. Play 0 of
`examples/_shared/site.yml` builds both wheels from those
checkouts, play 1 copies them to every node, and the node role
installs the wheel paths instead of the PyPI names
(`roles/node/tasks/bootstrap.yml:150-192`). The checkouts come
from `shakenfist/actions`'s `setup-test-environment`, which
checks out `shakenfist/client-python` with no `ref` -- so the
repository's default branch, `develop`.

The local-wheel mechanism landed on 2026-06-24 (`05751666e`,
"Add example consumer playbooks for the collection"), which is
seven weeks before phase 4 wrote D7 around the assumption that
it did not exist. The premise was stale when it was written, not
made stale afterwards.

Two caveats worth stating precisely, because "CI uses develop"
is easy to over-read. The default for a real operator deploy is
still the PyPI name, so an unreleased verb reaches CI and
nothing else. And the conductor (phase 4c) installs a released
client, so its dependency on a release is real and unaffected by
this finding.

**2. The functional test asserts status codes, not results.**
`test_namespace_claims.py` is 720 lines built on `_claim_api()`,
which calls `_request_url()`, catches the typed exception, and
returns a `(status, body)` pair -- because for most of this
file the *status code* is the assertion (`409` for a duplicate
claim, `507` for one the cluster cannot promise, `503` for
accounting not yet built, `404` for a claim read through the
wrong namespace). Two retry wrappers,
`_claim_api_awaiting_accounting()` and
`_claim_api_awaiting_headroom()`, sit on top of that pair shape
via `shakenfist_ci/retries.py`. A naive "move onto verbs" that
replaced `_claim_api()` with direct verb calls would have to
rewrite every assertion in the file and would break both retry
wrappers. D3 decides how this actually happens.

**3. `retries.py` is deliberately import-free.** It was added
six days ago by the #3907 fix and its docstring says it is kept
free of imports from the rest of the suite *and* of
`shakenfist_client`, so the unit tests in `shakenfist/tests` can
load it by path and drive it with a fake clock
(`test_ci_claims_headroom.py`). Any design that made the retry
loop catch `apiclient` exceptions would break that property and
the unit test that depends on it.

**4. An unmapped status already raises a usable exception.**
`_actual_request_url()` guards its lookup: `if r.status_code in
STATUS_CODES_TO_ERRORS` raises the typed class, and anything
else outside `[200, 202]` raises a bare `APIException`
(`apiclient.py:328-337`). `APIException` carries `status_code`
and `text` (`apiclient.py:41-47`). So a 503 today is
distinguishable by attribute, not only by message.

*Correction at source:* the phase 4c plan's finding 3 says a
caller "must string-match to tell 'retry in a moment' from a
durable error". That overstates it, and is corrected in that
plan as part of this planning commit. The recommendation to add
the mapping is unchanged -- catching `ServiceUnavailable` reads
better than inspecting an attribute on a generic exception, and
phase 4c's decision E6 branches on exactly that distinction --
but it is ergonomics rather than a gap.

**5. Everything else the phase stub asserts is true.** The
capability string `namespace-claims` is advertised
(`external_api/app.py:304-311`, checked by
`tests/external_api/test_root.py`), and `check_capability()`
exists in the client (`apiclient.py:253`) for a caller that
wants to feature-detect. Issue client-python#364 is open and
carries an accurate description of the API, including the `PUT`
field-mask semantics and the `state` versus `coverage_state`
distinction. The five endpoints are routed as the issue
describes (`external_api/app.py:435-438`).

**6. The client has a natural home for CLI verbs.**
`shakenfist_client/commandline/namespace.py` is a click group
with sixteen subcommands including key and trust management, and
unit tests live beside it in
`shakenfist_client/tests/`.

**7. Releases are tag-driven and Michael controls them.**
`RELEASE-SETUP.md` describes PyPI trusted publishing from a
`release.yml` workflow gated on a GitHub environment with
required reviewers, triggered by a `v*` tag. The most recent tag
is `v0.8.3`. The master plan's phase stub calls the release
"outside this repository's control", which is true of *this*
repository but reads as though it were outside the project's
control; it is one tag and one approval.

### Corrections made at source

As part of the planning commit:

- The master plan's phase 4b stub no longer says the phase
  depends on a client release before its functional coverage can
  move, and says what CI actually installs.
- The phase 4c plan's finding 3 is corrected per finding 4
  above.

Three things are deliberately left to the steps that touch
their files rather than done here: client-python#364's "Why
this needs an issue rather than just happening" section (step
5), `test_namespace_claims.py`'s docstring (step 4), and the
new `docs/developer_guide/ci.md` subsection (step 5). The last
of those is not a correction at all -- nothing in that file is
wrong -- but a missing fact that has now cost the project one
deliberate design compromise and four repetitions of a false
constraint.

## Decisions

**D1. The functional coverage moves before the release, not
after.** Finding 1 removes the ordering constraint everyone has
been working around. The only ordering that remains is between
repositories: the client change must be merged to
client-python's `develop` before the shakenfist pull request
enters the merge queue, because that is when the cluster job
builds the wheel. Not before the shakenfist PR is *opened* --
cluster tests are skipped on `pull_request` and run on
`merge_group`.

**D2. CLI verbs are in scope, though nothing requires them.**
Issue #364 scopes them out as "a natural companion but not
required by anything", and that is true of the code. It is not
true of the phase after next: phase 8 writes an operator guide
for claims, and an operator guide whose worked examples are
`curl` invocations would be a poor outcome for a surface that is
otherwise complete. A release is also a heavyweight event -- a
tag plus a human approval -- so the marginal cost of shipping
the CLI in the same one is close to zero, and the cost of a
second release later is not. This is the decision most likely to
be argued with, since it widens a phase whose master-plan stub
mentions only `apiclient` verbs.

**D3. The functional test keeps `_claim_api()` and puts the
verbs underneath it.** Not a rewrite of every assertion. The
adapter changes from "call `_request_url()`, catch the typed
exception, return `(status, body)`" to "call the verb, catch the
typed exception, return `(status, body)`", reading the pair off
`APIException.status_code` and `.text` per finding 4. Three
things follow, and all three are why this is the right shape:
the status-code assertions that are the point of the file
survive unchanged; `retries.py` keeps the pair contract and its
import-free property (finding 3), so
`test_ci_claims_headroom.py` keeps passing; and the verbs are
genuinely exercised, because every request in the file now goes
through one.

The alternative -- assert on exception classes directly -- loses
resolution rather than gaining it: `409` covers `exists`,
`below_usage` and `not_active`, which the file distinguishes
today and would then have to distinguish by message anyway.

**D4. The CLI says what this release actually does.** Exceeding
a claim is recorded, not refused, until phase 5 flips
`CLAIM_ENFORCEMENT_HARD`. Help text that says a claim
"reserves" or "guarantees" capacity would be wrong in a way an
operator would only discover by being surprised. The verbs
describe a claim as a declared ceiling that the cluster
*accounts against*, and `claim show` prints `coverage_state`
beside `state` rather than merging them into one status column
(#364 asks for this explicitly, and it is the client half of a
distinction the server was careful about).

**D5. No feature detection in the verbs.** `check_capability()`
exists and a caller may use it, but the verbs do not call it:
against a server without claims the request 404s, which is a
clear enough answer, and a verb that silently no-oped on an old
server would be worse than one that failed. The conductor's own
version gate (phase 4c step 0) is where this actually matters.

**D6. One typed exception, not a family.** Add
`ServiceUnavailableException` for 503. Do not add
claim-specific exception classes for the individual refusal
reasons: the reason is in the message, the status is on the
exception, and a class per refusal reason would be a vocabulary
the server does not itself have.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent | Status |
|------|--------|-------|-----------|---------------------|--------|
| 1 | medium | sonnet | worktree | (client-python) The five verbs and the exception. In `shakenfist_client/apiclient.py`, add `get_namespace_claims(namespace)`, `get_namespace_claim(namespace, claim_uuid)`, `create_namespace_claim(namespace, limit_cpus, limit_memory_mb, limit_disk_gb, expires_in_seconds)`, `update_namespace_claim(namespace, claim_uuid, limit_cpus=None, limit_memory_mb=None, limit_disk_gb=None, expires_in_seconds=None)` and `delete_namespace_claim(namespace, claim_uuid)`, beside the namespace key and trust verbs at `:1245-1290` and following their shape exactly (build the path by concatenation, `self._request_url(...)`, return `r.json()`). Two things are not boilerplate. `update_namespace_claim` sends **only** the keyword arguments the caller passed -- the server treats the body as a field mask, so sending all four with values read from a previous GET turns a re-date into a resize race; build the data dict from the non-`None` arguments and let the server reject an empty one. And `expires_in_seconds` is a duration against the cluster's clock, so pass it through and do not convert a datetime. Add `ServiceUnavailableException(APIException)` and map `503` to it in `STATUS_CODES_TO_ERRORS` (`:118-127`); read `_actual_request_url()` at `:328-337` first to confirm the guarded lookup means this changes an exception's class and nothing else. Unit tests in `shakenfist_client/tests/test_client_apiclient.py`, following that file's `_request_url` mocking pattern -- cover each verb's method and path, that `update_namespace_claim` omits unpassed fields, and that a 503 now raises the new class. Read client-python#364 for the API detail. Commit subject: `Add namespace capacity claim verbs.` | Not started |
| 2 | medium | sonnet | worktree | (client-python) CLI verbs, per D2 and D4. Add a `claim` subgroup to the `namespace` click group in `shakenfist_client/commandline/namespace.py`, following the key and trust subcommands' patterns for arguments, `shell_complete` and output formatting: `namespace claim list <namespace>`, `claim show <namespace> <uuid>`, `claim create <namespace> --cpus --memory-mb --disk-gb --expires-in`, `claim update <namespace> <uuid> [--cpus] [--memory-mb] [--disk-gb] [--expires-in]` and `claim delete <namespace> <uuid>`. `update` passes through only the options the operator supplied, for the field-mask reason in step 1 -- click's default of `None` for an unsupplied option is what makes that natural, so do not give them defaults. Output must print `coverage_state` beside `state` as two columns and never merge them (D4). Help text describes a claim as capacity the cluster accounts against, not capacity it reserves or guarantees, because exceeding a claim is recorded rather than refused in this release. Do not add a "how much room is left" display: no endpoint publishes cluster totals, and that view is phase 5's. Tests beside `shakenfist_client/tests/test_client_commandline_instance.py`, mirroring its approach. Commit subject: `Add namespace claim commands to sf-client.` | Not started |
| 3 | n/a | management session | none | (client-python) Merge steps 1 and 2 to `develop`, then cut a release: tag `v0.8.4` and approve the `release` environment per `RELEASE-SETUP.md`. The release is not needed for step 4 (finding 1) but is needed by phase 4c, and cutting it here means one release rather than two. Confirm the release appears on PyPI before phase 4c's step 0 gate is evaluated. | Not started |
| 4 | high | opus | worktree | (shakenfist) Move the functional coverage onto the verbs, per D3. In `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_namespace_claims.py`, change `_claim_api()` (`:180-204`) to dispatch to the client verbs instead of `_request_url()`, reading the returned `(status, body)` pair off `APIException.status_code` and `.text` for the failure path and returning `(200, result)` for the success path. Keep the method's signature and contract, so `_claim_api_awaiting_accounting()`, `_claim_api_awaiting_headroom()` and every assertion in the file are untouched -- and so `shakenfist_ci/retries.py` keeps its pair contract and its freedom from `shakenfist_client` imports, which `shakenfist/tests/test_ci_claims_headroom.py` asserts by loading it by path. `_claims_url()` and `_claim_url()` become namespace and uuid arguments rather than paths; keep or remove them as the dispatch makes natural, but do not leave a helper that builds a URL nothing uses. Then rewrite the file's "Why this file reaches past the client library" docstring section: it currently explains a constraint that no longer exists, and should instead say that the verbs are what an operator uses and so what this file defends, and note that cluster CI builds the client from a `develop` checkout rather than installing the release. This step cannot be verified on a `pull_request` run -- the `(collection)` matrix is skipped there and runs on `merge_group` (`docs/developer_guide/coding_rules.md:341-352`) -- so drive the changed helper against a real cluster before proposing the commit, per that same rule. Requires step 1 merged to client-python's `develop` first. Commit subject: `tests: drive claims through the client verbs.` | Not started |
| 5 | medium | sonnet | worktree | (shakenfist) The documentation half. Add a short subsection to `docs/developer_guide/ci.md` saying how cluster CI obtains its code: the `(collection)` matrix deploys through `shakenfist/actions`, which checks out `shakenfist`, `client-python` and `agent-python` (the triggering repository at its ref, the others at `develop`), and `tools/deploy-collection.sh` passes `sf_build_local_wheels=true` so the collection builds and installs wheels from those checkouts rather than the PyPI packages an operator would get. Say the consequence plainly, because it is the part that was missed for two months: an unreleased client change is available to cluster CI as soon as it merges to the client's `develop`. Then close the loop on client-python#364 -- comment correcting its "Why this needs an issue rather than just happening" section and close it if steps 1 and 2 satisfy it. Commit subject: `docs: say where CI gets its client from.` | Not started |
| 6 | low | sonnet | worktree | (shakenfist) Close-out. Set the phase 4b row to `Complete` in the master plan Execution table and confirm `docs/plans/index.md`'s arithmetic. Check the phase 4c plan's step 0 gate now reads true (a released client carrying the verbs) and say so in that plan rather than leaving the reader to check PyPI. Commit subject: `scheduler: close out phase 4b.` | Not started |

## Risks and mitigations

**The two-repository ordering goes wrong.** A shakenfist pull
request that reaches the merge queue before the client change is
on client-python's `develop` fails its cluster jobs, and the
failure looks like a claims bug rather than an ordering mistake.
Mitigated by D1 stating the ordering explicitly and by step 4's
brief naming step 1 as a prerequisite. Checked by the management
session before the step 4 branch is enqueued.

**Step 4 cannot be verified on a pull request.** The
`(collection)` matrix is skipped on `pull_request`, so a green
PR says nothing about whether the rewritten helper works -- the
exact trap `coding_rules.md` documents with the federation test
that died in `setUp` for four commits. Mitigated by the step's
brief requiring the helper to be driven against a real cluster
before the commit is proposed, which is cheap: the file's own
helpers are importable given a scratch venv, and sfcbr is
available.

**Cluster CI is exposed to the client's `develop`.** Finding 1
cuts both ways: an unrelated regression merged to client-python
breaks this repository's cluster CI with no change here. That is
already true today and is not introduced by this phase, but it
is now written down, which makes it something someone can decide
about later. Recorded as future work rather than addressed.

**The release stalls.** Step 3 needs a human approval, and phase
4c's step 0 gates on it. If the release does not happen, phase
4b's own work is still complete and phase 4c waits -- which is
the honest outcome and does not block phases 5 through 9.

## Definition of done

- [ ] No *call* to `_request_url` remains in
      `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_namespace_claims.py`.
      The name still appears once, in the docstring explaining what the
      file used to do and why that reasoning was wrong, which is worth
      keeping.
- [ ] `shakenfist/tests/test_ci_claims_headroom.py` still passes,
      and `shakenfist_ci/retries.py` still imports nothing from
      `shakenfist_client`.
- [ ] The `(collection)` matrix passes with the rewritten test,
      observed in a merge-queue run rather than inferred from a
      pull request.
- [ ] `sf-client namespace claim show` prints `state` and
      `coverage_state` as separate values.
- [ ] `update_namespace_claim` with one keyword argument sends a
      body with one key, asserted by a unit test.
- [ ] A `shakenfist-client` release carrying the verbs is on
      PyPI, and phase 4c's step 0 gate reads true.
- [ ] No document in either repository still says a client
      release is required before functional coverage can use the
      verbs: the master plan stub, the test docstring, and
      client-python#364 are each corrected or closed.
- [ ] `docs/developer_guide/ci.md` says where cluster CI gets its
      client from.
- [ ] `pre-commit run --all-files` passes in each repository.

## What the implementation established

Recorded here as it was found, because two of these were open questions
the plan asked to have answered before code was written.

**An over-large claim on a claim-free namespace answers 507**, as
`InsufficientResourcesException`, with a per-dimension body: `the
cluster does not have the capacity to promise this claim: cpus (limit
234, used 114, requested 100000)`. Checked against sfcbr on 2026-08-27.
The same request against a namespace which *already* holds a claim
answers 409, because the `exists` branch is evaluated first -- which is
what the phase 4a soak recorded, and why its "impossible claim is
refused" line said 409 rather than 507. This answers phase 4c's step 2
open question directly: the conductor creates its claim on a namespace
it has just made, so the refusal it must handle is 507, and E6's first
branch catches `InsufficientResourcesException`.

**Every dispatch path of the rewritten `_claim_api()` behaves against a
real cluster.** Collection and single `GET`, `POST`, a refused second
`POST`, a `PUT` naming no fields (400), a `PUT` naming one dimension
(the others unmoved), a cross-namespace read (404, not disclosed),
`DELETE`, and `DELETE` of a claim already gone (404). D3's claim that no
assertion in the file needed to change held.

**The headroom-tolerant wiring is real, not decorative.** Routing
`_create_claim` off `_claim_api_awaiting_headroom` fails exactly one
test in `test_ci_claims_headroom.py`, so the assertion added by the
issue-3907 fix does what it says.

## Future work

- **Pinning the client for cluster CI.** Building from
  `develop` gives fast feedback and couples two repositories'
  CI. Whether that trade is right is a question this phase
  raises and does not answer.
- **A cluster capacity endpoint**, so `claim list` can show an
  operator how much unclaimed room is left. Phase 5's admin
  capacity view is where this belongs.
- **Client-side claim helpers for the conductor's shape** --
  "create a claim sized to this footprint, or tell me it was
  refused" -- if phase 4c finds itself writing the same wrapper
  twice.

## Back brief

Before implementation starts, the implementing session states
back to the management session:

1. The exact `(status, body)` pair `_claim_api()` will return
   for each of the five verbs' success and failure paths,
   demonstrated against a real cluster for at least one refusal,
   so D3's claim that no assertion in the file needs to change
   is checked rather than assumed.
2. The `update_namespace_claim` body for a call passing only
   `expires_in_seconds`, as JSON.
3. Whether `_claims_url()` and `_claim_url()` survive the
   rewrite, and if so what they return.

Step 2's CLI shape -- the subgroup name, the option names, and
the columns `claim list` prints -- is worth agreeing before it
is built, being cheap to propose and tedious to redo once tests
and documentation reference it.
