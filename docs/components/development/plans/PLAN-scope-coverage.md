# Plan: a `scope-coverage` audit, reconciling the audit lists against the organisation

## Prompt

Before executing any part of this plan, read what it changes.
`docs/consistency-audits.md` is the reference for what a daily run
does, how a criterion is added, and how a repository is brought into
scope -- read it first. Then `AGENTS.md` for the invariants that are
not visible in the code, and `ARCHITECTURE.md` for how the pieces fit.
`PUSH-AUDIT.md` is the pre-push runbook this plan's last phase runs.

The two standing constraints from `PLAN-TEMPLATE.md` bind here. The
blast radius is other people's repositories: bringing three
repositories into the matrix files 43 issues on the next morning's
run, on repositories whose owners did not ask for them today. And this
repository is inside its own audit matrix, so the criterion added here
measures the repository it is added to, from the first run after it
merges.

Every count in this plan was measured on 2026-09-04 against
`gh repo list shakenfist --limit 200`. The organisation moves; re-derive
before relying on a number.

## Situation

Audit scope is written down in three places -- the `repo:` matrix in
`.github/workflows/consistency-audit.yml`, the in-scope list in
`docs/audits/README.md`, and the excluded list on the same page --
and `AuditScopeIsStatedOnceTest` in `scripts/tests/test_registry.py`
holds those three in agreement with each other.

Nothing compares any of them to the organisation. A repository in
none of the three is not audited, is not documented as excluded, and
produces no finding anywhere. The failure is silent by construction:
the only signal is a repository missing from a list nobody diffs.
This is issue #40.

Measured today, the organisation has 38 repositories. Five are in
neither list:

| Repository | Last push | Note |
|------------|-----------|------|
| divergulent-reviews | 2026-09-03 | review-tracking sidecar for divergulent |
| homebrew-tap | 2026-07-24 | packaging tap |
| kerbside-client | 2024-03-29 | one commit, 2024; never revisited |
| uncalibrated-sextant | 2026-08-16 | actively developed |
| visual-digest-rust | 2026-07-24 | actively developed |

The same blindness runs the other way: the excluded list names
`imago-testdata`, `imago-testdata-quarantine` and `occystrap-testdata`,
none of which exist under `shakenfist` any more. The imago test data
now lives on the private GitLab as `instar-testdata`. An exclusion for
a repository that does not exist is harmless in itself; it is the same
missing check.

`kerbside-client` was investigated rather than assumed. Its code did
not move: `shakenfist/kerbside` has no client package, nothing in this
repository references it, and the repository is a single "Initial
commit" from 2024-03-29 carrying real code -- `apiclient.py` (7.5KB)
and `main.py` (14KB) -- with an empty test tree. It was pushed once and
left.

## Mission and problem statement

Make the scope decidable against reality in both directions, and make
the five undecided repositories decided in writing.

Two properties, checked every morning:

* Every repository in the organisation is either in the audit matrix
  or on the excluded list.
* Every name in the matrix or on the excluded list still resolves to a
  repository in the organisation.

Neither is a judgement about whether a repository *should* be audited.
The check cannot make that call and does not try; what it removes is
the third state, where nobody made it either.

## Decisions

### D1. A registered `Check`, scoped to `development`

`scope-coverage` is an ordinary `Check` subclass whose `applies(repo)`
returns a skip reason unless `repo.name == 'development'`. The lists
live in this repository's clone, this repository is already in the
matrix and audits itself deliberately, and the audit step already
carries `GH_TOKEN: ${{ secrets.AUDIT_TOKEN }}`.

That buys the whole lifecycle from machinery that already exists:
`audit-manage-issues.py` files one `consistency` issue on `development`
when the lists drift and closes it when they do not, and the criterion
gets a section on the compliance page like any other.

The issue proposed two alternatives, and both were rejected:

* **A separate job in `consistency-audit.yml`.** It would need its own
  issue filing, closing and idempotency logic -- the part of
  `audit-manage-issues.py` that is easy to get subtly wrong -- and it
  would appear nowhere in the compliance tables.
* **A unit test calling the API.** `pre-commit` and the pull request
  gate have no token, so the test would either be skipped where it
  matters or make the gate depend on the network.

The precedent for a check that is N/A nearly everywhere is
`sfui-vendor`, which is real for the repositories that vendor sfui and
skipped for the rest.

### D2. No `isArchived` filter

Every archived repository in the organisation -- `ansible-modules`,
`client-go`, `client-js`, `deploy`, `jenkins-private`, `loadtest`,
`ostrich`, `symbolicmode`, `terraform-provider-shakenfist`, `website`
-- is already on the excluded list. So the strict reading costs nothing
to adopt: every repository appears in one of the lists, archived or
not, and there is no filter to write and no exemption to explain.

The issue floated `isArchived` as the obvious filter and named
`kerbside-client` as the case it would have got wrong -- dormant since
2024 and not archived. Requiring a decision for every repository
removes the class of problem rather than that one instance of it.

### D3. The five repositories, decided

| Repository | Decision | Reason |
|------------|----------|--------|
| divergulent-reviews | Excluded | A review-tracking sidecar, not a project in the sense the criteria mean |
| homebrew-tap | Excluded | A packaging tap; nothing to package, document or release |
| kerbside-client | In scope | Real client code held to the standard, not an archive |
| uncalibrated-sextant | In scope | Actively developed |
| visual-digest-rust | In scope | Actively developed |

The excluded list's rationale sentence says exclusions are "internal
only tooling or historical archive repositories". A review sidecar and
a packaging tap are neither, so the sentence widens to cover
repositories that are not projects in the sense the criteria mean.

Onboarding costs, from a dry run of `scripts/audit-check.py` against
each clone on 2026-09-04:

| Repository | pass | fail | n/a |
|------------|------|------|-----|
| uncalibrated-sextant | 9 | 19 | 21 |
| visual-digest-rust | 9 | 15 | 25 |
| kerbside-client | 5 | 9 | 35 |

43 issues on the first run after the matrix change. That is the point
of onboarding rather than a reason not to: they are the criteria the
rest of the fleet already meets. `standards-alignment` is the skill
for working the backlog down, and this plan does not do it.

### D4. One parser, not two

The parse of the three scope lists lives in
`AuditScopeIsStatedOnceTest` today: literal start and end phrases, a
bullet prefix, and a `REPO_NAME` guard that notices a parse which has
started collecting prose. The check needs exactly that parse.

It moves to `scripts/audit/scope.py` and the test imports it. A second
copy in the check would let the test and the check disagree about what
the lists say, which is the failure this criterion exists to prevent,
one level up.

The phrase-anchoring assertions move with it. They are the reason the
parse is trustworthy: a start phrase that is reworded away raises, and
an end phrase that is reworded away silently runs the block to the end
of the file.

### D5. `develop` branches before the matrix, not after

`uncalibrated-sextant` and `visual-digest-rust` both defaulted to
`main`, which `default-branch-naming` fails. Renaming after they enter
the matrix would file an issue on each and close it the next morning.

Both were renamed through the GitHub rename endpoint rather than
create-and-delete: it moves the default branch and retargets open pull
requests in one operation. Neither had open pull requests or rulesets,
and both now have `develop` as their only branch. `kerbside-client`
already defaulted to `develop`.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. `develop` branches for the onboarding repositories | Complete | n/a -- GitHub settings, no commit |
| 2. Reconcile the scope lists | Complete | |
| 3. Lift the scope parsing into `audit/scope.py` | Complete | |
| 4. Add the `scope-coverage` check | Complete | |
| 5. Push audit | Not started | |

Phases 2 to 4 ship as a single pull request, so the `Merged` record
will be the same merge commit for all three.

### 1. `develop` branches for the onboarding repositories

`uncalibrated-sextant` and `visual-digest-rust` renamed `main` to
`develop` on GitHub, which moved the default branch with it. Local
clones updated: `develop` created tracking `origin/develop`,
fast-forwarded, and `origin/HEAD` re-pointed. No commit in this
repository.

Stale remote-tracking refs for branches deleted upstream before today
were left in place in both clones; pruning them is unrelated hygiene.

### 2. Reconcile the scope lists

One commit, and it lands before the check so that the check passes on
its first run rather than failing on the state it was written to
detect.

* `.github/workflows/consistency-audit.yml`: add `kerbside-client`,
  `uncalibrated-sextant` and `visual-digest-rust` to the matrix, in
  alphabetical order.
* `docs/audits/README.md`: the same three onto the in-scope list; add
  `divergulent-reviews` and `homebrew-tap` to the excluded list; remove
  `imago-testdata`, `imago-testdata-quarantine` and
  `occystrap-testdata`; widen the excluded list's rationale sentence
  per D3.
* Check `REPO_OVERRIDES` in `scripts/audit/repo.py` needs nothing for
  the three: they are ordinary Python and Rust repositories on
  `develop`, with no exemption to state. An override added here would
  be an exemption written for a repository nobody has tried to fix yet.

`python3 -m unittest tests.test_registry` is the gate: it compares all
three lists against each other and is the reason this phase is
separable at all.

### 3. Lift the scope parsing into `audit/scope.py`

One commit, no behaviour change. `matrix_repos()`,
`documented_in_scope()`, `documented_excluded()`, `bulleted_block()`
and `REPO_NAME` move out of `AuditScopeIsStatedOnceTest` into
`scripts/audit/scope.py`, taking a repository root rather than reading
`REPO_ROOT` from the test base. The test imports them and keeps its own
tests of the guards -- those are tests of the parser, and they follow
it.

The module raises rather than asserts: `unittest` assertions in
production code are a test framework leaking into the runner, and the
check has to turn a failed parse into a `fail()` result rather than a
traceback. `AuditScopeIsStatedOnceTest` keeps its assertion messages by
catching and re-raising, or by asserting on the exception text.

### 4. Add the `scope-coverage` check

One commit.

* `ScopeCoverage` in `scripts/audit/checks/github_config.py`:
  `id = 'scope-coverage'`, `spec = 'docs/audits/scope-coverage.md'`,
  `template = None`, an issue title, `applies()` per D1, and `run()`
  comparing the organisation listing against the two lists. Registered
  in `CHECKS` in `scripts/audit/registry.py` beside the rest of the
  `github_config` family.
* Both failure directions in one result, with the repository names in
  `missing=` so `audit-manage-issues.py` renders them as bullets.
* `docs/audits/scope-coverage.md`, following the structure in
  `docs/audits/README.md`, plus its line in that index table.
* The frozen lines in `scripts/tests/test_metadata.py` -- adding a
  criterion adds a line to `FROZEN_METADATA` and
  `FROZEN_ISSUE_TITLES`. The issue title is the idempotency key for
  filing and closing; choose it once.
* Tests in `scripts/tests/test_github_config.py` on `FakeGitHub`: a
  clean scope passes, an unlisted repository fails, a listed name that
  does not resolve fails, a truncated listing is caught, and a
  repository that is not `development` skips without an API call.

### 5. Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of phases 2 to 4 against
`main`, using the merge commit recorded in the Execution table.
Findings land as their own pull request.

## Risks and mitigations

* **`gh repo list` truncates at 30 by default.** The organisation has
  38 repositories, so the default limit silently loses eight and the
  check reports them as unlisted. Pass an explicit high limit, or
  paginate `orgs/<org>/repos`. Covered by a test that scripts a
  listing at the limit.
* **A token that cannot see private repositories.** `performance`,
  `private-ci` and `jenkins-private` are private and on the excluded
  list. A listing without them would report three dead exclusions
  every morning, and the fix that implies -- deleting the exclusions
  -- would be actively harmful. Planned as a heuristic (fail if the
  listing contains no private repositories at all); built instead as
  evidence, because the heuristic is wrong in both directions in an
  organisation that has none. Every name missing from the listing is
  resolved directly against the API, which answers the question
  rather than guessing at it.
* **A renamed repository.** It appears in the listing under its new
  name and in the matrix under its old one. Resolving the old name
  follows the rename redirect, so the finding names the new name and
  says what to write down, rather than reporting a repository that
  does not exist. This is the same trap `gh_canonical_repo()` exists
  for: the API follows a rename, issue listing and search do not.
* **43 issues on the first run.** Expected, per D3. Worth saying out
  loud in the pull request so it is not read as the audit having
  broken.

## Administration and logistics

### Success criteria

* Every one of the 38 repositories in the organisation is in exactly
  one of the two lists, and the three dead exclusions are gone.
* `scope-coverage` passes on `development` on the first run after
  merge, and fails if a repository is added to the organisation
  without a decision.
* `python3 -m unittest discover -s scripts -t scripts` and
  `pre-commit run --all-files` both pass.
* The parse of the scope lists exists once.

### Documentation index maintenance

`docs/audits/README.md` gains the criterion's index line in phase 4 and
its scope changes in phase 2. `docs/consistency-audits.md` needs no
change: "Adding a criterion" and "Bringing a repository into scope"
both already describe what these phases do. `AGENTS.md` and
`ARCHITECTURE.md` are unchanged -- no convention moves and the shape of
the system does not change, a criterion is added to a registry that
already exists.

### Future work

* The dry runs measured `uncalibrated-sextant` and `visual-digest-rust`
  on feature branches rather than their default branch, and without
  `skillsaw` installed, so `llm-context-lint` was N/A in both. The
  first real run is the authoritative count.
* The 43 issues are a backlog for `standards-alignment`, not for this
  plan.
* Nothing checks the *private* GitLab projects the same way. The same
  blindness exists there and the fix does not transfer, since the
  audit only knows about GitHub.

### Bugs fixed during this work

To be filled in as the work proceeds.

### Back brief

Before phase 2 begins, confirm the D3 table is still what Mikal wants:
it is the only part of this plan that cannot be derived from the
repository, and every later phase assumes it.
