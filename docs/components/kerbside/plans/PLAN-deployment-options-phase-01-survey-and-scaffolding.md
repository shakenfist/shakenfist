# Deployment options phase 1: survey, matrix and scaffolding

Master plan: [PLAN-deployment-options.md](/components/kerbside/plans/PLAN-deployment-options/).

## Planning effort

Planned at high effort, as the master plan asks: the index
table's shape and the boundaries between pages are inherited by
every later phase. Review effort: medium. There is one small code
change (the guard's scope), and the rest is a table and two
issues.

## Scope

In scope:

- A `### Deployment Options` table in `docs/index.md`, with every
  row marked planned.
- Extending the backend-TLS claim guard to `docs/deployment/`
  before any page exists there.
- Filing the first-party container image issue in this
  repository, and an issue in `kerbside-patches` recording the
  Kolla split decided in master plan question 1.
- Settling master plan questions 2 and 4, and writing down the
  page conventions phases 2 to 5 follow.

Out of scope:

- **Any page under `docs/deployment/`.** The directory is created
  by whichever page phase lands first (decision 3).
- `installation.md`'s "Deploying for real" table, which phase 6
  owns.
- The OpenStack row of the Use Cases table, which still says
  "deployed alongside the cluster with Kolla-Ansible via
  kerbside-patches". Phase 3 rewrites it alongside
  `openstack.md`'s Deployment section, which says the same thing.
- Closing the three Kolla flake issues. The survey suggests they
  are stale, but closing them is triage that belongs in a
  `merge-ci-triage` pass, not in this phase. Recorded under
  Risks.

## What the survey found

Every claim the master plan's phase 1 section and its Situation
make was checked against the tree, Shaken Fist's `develop`, and
GitHub on 2026-10-10. Three were wrong and have been corrected at
source in the master plan, in the same commit as this file, so
later phases need not redo the corrections.

1. **The Kolla lane is not flaky now.** The master plan said the
   lane "is already the source of three open flake issues (#293,
   #308, #312)". The issues exist and are open, but all three were
   filed on 2026-08-13 or 2026-08-14, and none has been updated
   since 2026-09-20. Across the 40 most recent `merge_group` runs
   of `functional-tests.yml` (2026-09-28 to 2026-10-09), the Kolla
   job succeeded 32 times and failed 0 times. The other 8 runs
   never started it: 4 were documentation-only, so path filtering
   skipped the test matrix; 3 were superseded queue entries
   cancelled within a minute, before the matrix started; and in 1
   (run 36797038722) the job sat queued for 24 hours without a
   runner and was cancelled. That one is a runner-starvation
   timeout, not a lane failure, since the job never ran. By the
   same count the oVirt lane failed 3 times. This settles master
   plan question 2 (decision 5).
2. **The Shaken Fist role is about 2000 lines, not 1000.** The
   earlier count covered only `tasks/` and `templates/`. All files
   under `shakenfist/deploy/collection/roles/kerbside/` on
   `develop` come to 1983 lines, including four helper scripts in
   `files/` and the argument specs. The single-source claim holds:
   `templates/sources.yaml` builds a one-element list with
   `'type': 'shakenfist'`.
3. **The role move now has an owner and a release dependency.**
   The amendment the master plan proposed was accepted by the
   session running `PLAN-kerbside-deployer.md`, as a rewritten
   phase 5, `PLAN-kerbside-deployer-phase-05-role-to-kerbside.md`.
   Mikal approved it the same day, and it is committed as shakenfist
   431d6a269 on shakenfist#4479's branch. It needs that
   plan's phase 4 to merge, and a Kerbside release containing
   #536 (the fix for #533, timestamp columns stored as
   single-precision `FLOAT`). #536 merged on 2026-10-08, and the
   newest release, v0.6.0, is from 2026-09-07, so **a Kerbside
   release is now on the role move's critical path.**

Three findings were not in the master plan at all, and each shapes
a decision below:

4. **A new docs directory needs an `order.yml`.**
   `docs/development.md` "Documentation site navigation" (line
   109) explains that `docs/` is synced to shakenfist.com by
   shakenfist/actions' `tools/sync_component_docs.py`.
   `docs/spice/` and `docs/use-cases/` each carry an `order.yml`
   that orders their pages in the site navigation. The section
   label is the directory name title-cased, so `deployment/`
   shows as "Deployment", which is fine and cannot be overridden
   anyway. An `order.yml` entry naming a page that does not exist
   prints `Warning: order.yml entry not found, skipping` from the
   sync script (around line 262).
5. **The claim guard's scope is a literal that another file
   copies.** `tools/check-backend-tls-claims.py:126` is `DOC_PATHS
   = ('docs/use-cases/*.md', 'docs/index.md')`. The same string
   appears verbatim as the search text of the "index.md dropped
   from the scanned set" mutation in
   `tools/mutate-backend-tls-claims.py` (around line 50). Changing
   `DOC_PATHS` without changing the mutation leaves a mutation
   whose search text no longer matches, which the mutation tool
   reports as a failure, not a pass. The guard runs in the
   ungated `docs_checks` job (`functional-tests.yml`, step "Check
   the backend TLS claims in the documentation"), so it does run
   on a documentation-only pull request.
6. **No issue exists for a first-party container image.** No open
   issue title mentions an image, container, Helm, Kubernetes,
   systemd, or packaging. `kerbside/tests/unit/test_docs_links.py`
   checks every tracked `.md` file, so new pages are covered
   without any change.

The rest of the master plan held: Kerbside tracks no `.service`
file, the Kolla image is the only published one, and nothing for
Kubernetes exists.

## Decisions

1. **The index table carries the matrix. There is no separate
   grid.** A grid of use case against mechanism would be mostly
   "yes" in the by-hand row and mostly "no" elsewhere. What a
   reader needs is which sources each mechanism can configure,
   which fits in a column. So `### Deployment Options` has four
   columns: `Mechanism | Description | Sources it configures |
   Tested in Kerbside CI`. **This is the decision most likely to
   be argued with.** The case for a grid is that it makes the
   front-door message visible at a glance. The case against is
   that the message is better carried by one sentence above the
   table, which phase 1 writes.
2. **Four rows, all planned, in this order:** by hand; the
   ansible role; Kolla-Ansible; containers. The order runs from
   most general to most specific, which puts the front-door
   default first and Kolla third. Initial cell contents:

   | Mechanism | Sources it configures | Tested in Kerbside CI |
   |-----------|-----------------------|-----------------------|
   | By hand: pip, venv and systemd | Any | Every lane, but by CI scripts rather than as a documented procedure |
   | Ansible role | Shaken Fist only today; any, if the role move lands | `sf-e2e`, smoke tier and nightly, through `tools/sf-e2e/deploy-kerbside.sh` rather than the role, until the deployer plan's phase 5 |
   | Kolla-Ansible (kerbside-patches) | OpenStack | `openstack_matrix`, merge tier |
   | Containers | Static (the compose demo) | `demo-compose`, advisory, path-filtered |

   The CI column says what is true on the day it is written. The
   ansible row in particular will change when the deployer plan's
   phase 5 lands.
3. **No `docs/deployment/` directory in this phase.** Git cannot
   track an empty directory, and an `order.yml` listing four
   pages that do not exist prints a warning on every sync. The
   first page phase creates the directory and an `order.yml`
   holding only its own page, and each later page phase appends
   its entry. The page filenames are fixed now, so the index rows
   and later phases agree: `manual.md`, `ansible.md`,
   `kolla-ansible.md`, `containers.md`. The `order.yml` order
   matches the table.
4. **The guard covers `docs/deployment/*.md` from this phase
   on.** A glob over a directory that does not exist yet matches
   nothing, so this is safe before any page exists. Extending
   it then is the whole point: the first deployment page meets
   the guard rather than a review round.
5. **Master plan question 2: the Kolla lane stays in the merge
   queue.** It passed in all 32 of the last 40 merge runs that
   started it, with no failures, and it is the only end-to-end
   OpenStack coverage Kerbside has. Revisit only if a stale `kerbside-patches` rebase starts
   failing merges. If that happens, the remedy is to rebase, not
   to demote the lane.
6. **Master plan question 4: one `containers.md` page.** This
   confirms the master plan's recommendation; nothing in the
   survey argued against it.
7. **The page convention for phases 2 to 5.** Four sections,
   under exactly these headings: `## Who this is for`, `## What it
   deploys`, `## How to use it`, `## Status and limitations`. The
   last is a table with a `Not covered | Why` shape, as the
   use-case pages use. Each page links to the use-case pages for
   *why*, and to `installation.md` for the list of pieces, and
   restates neither.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Extend the backend-TLS claim guard to the deployment pages. In `tools/check-backend-tls-claims.py:126`, change `DOC_PATHS` to `('docs/use-cases/*.md', 'docs/deployment/*.md', 'docs/index.md')`, and update the comment block above it (lines ~112-125) with one sentence on why deployment pages are in scope: they describe TLS material and are where an unconditional backend-TLS claim would reappear. In `tools/mutate-backend-tls-claims.py` (~line 50), the "index.md dropped from the scanned set" mutation's search text is the old literal. Update its search text to the new tuple, and its replacement to the new tuple minus `'docs/index.md'`. Add a sibling mutation, "deployment pages dropped from the scanned set", that removes `'docs/deployment/*.md'`. In `kerbside/tests/unit/test_check_backend_tls_claims.py`, add a test, near `test_the_use_cases_pages_and_the_index_are_covered` (~line 211), proving that a file under `docs/deployment/` is scanned. No such file exists in the repository yet, so build a temporary tree (`tempfile.TemporaryDirectory`) holding `docs/deployment/x.md` with an unconditional claim, and point the guard at it. Read how `default_paths()` and `repository_root()` resolve the root, and how the neighbouring test that `chdir`s works (~line 200), and use whichever seam already exists rather than adding one. Run `tox -epy3 -- kerbside.tests.unit.test_check_backend_tls_claims`, `tox -eflake8`, and `tools/mutate-backend-tls-claims.py` (which needs `.tox/py3`). Every mutation, including the new one, must be reported as caught. Commit subject: "Scan the deployment pages for backend TLS claims." |
| 1b | medium | sonnet | none | Add a `### Deployment Options` section to `docs/index.md`, immediately after the Use Cases section (after the paragraph ending "`verify-rust-proxy.sh`.") and before `### Operator Documentation`. Open with two or three sentences: use cases say what Kerbside fronts, and this section says how it is installed and run; the default worth reaching for is a Kerbside deployed in front of the clouds rather than by one of them, because aggregation and placement both assume one; link `use-cases/multi-cloud.md`. Then the table from decision 2 of `docs/plans/PLAN-deployment-options-phase-01-survey-and-scaffolding.md`, with columns `Mechanism \| Description \| Sources it configures \| Tested in Kerbside CI`. Write one-line descriptions in the noun-phrase style of the Use Cases rows. No row is a link yet. Close with the sentence "Mechanisms without a link are planned rather than written; see", followed by a relative markdown link to `plans/PLAN-deployment-options.md`, mirroring the closing line of the Use Cases section. Do not claim the backend leg is encrypted or pinned anywhere: the guard from 1a scans this file. Run `tools/check-backend-tls-claims.py`, `tox -epy3 -- kerbside.tests.unit.test_docs_links`, and `pre-commit run --all-files`. Commit subject: "Index the deployment options." |
| 1c | low | sonnet | none | In `docs/development.md` "Documentation site navigation" (~line 123), the sentence names `docs/spice/` and `docs/use-cases/` as the directories carrying an `order.yml`, and says a new page in either must be added to it. Add one sentence saying that `docs/deployment/`, once it exists, carries one too and is under the same rule. Do not create the directory or the file. Commit subject: "Note that deployment pages need an order.yml entry." |
| 1d | low | (management session) | n/a | File two issues; these are outward-facing, so the management session files them after confirming the wording with Mikal. **kerbside:** "Publish a first-party container image". Kolla's image (kolla 975495) is the only published one, and it is built around `kolla_start` and `config.json`. `demo/Dockerfile` builds locally. Production compose and any Kubernetes or Helm support depend on such an image. Link `PLAN-deployment-options.md`. **kerbside-patches:** "Record the enablement / deployment split for upstream Kolla-Ansible work". Name 967801 and the Keystone service account as enablement (keep pushing gently), and 976889, 988189, 988913 and 989614 as deployment (rebase only), per master plan question 1. Then put both issue numbers into the master plan's Out of scope list. Commit subject: "Record the deployment options gap issues." |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The index table states CI coverage that changes under it. The ansible row will be wrong as soon as the deployer plan's phase 5 lands. | The deployer plan's phase 5 already edits this repository's docs, so its review should catch the row. Phase 5 here re-checks the row in any case. |
| The guard's new test passes without proving anything, for example by asserting on the pattern string instead of scanning a file. | The new mutation in step 1a, "deployment pages dropped", has to be caught by that test. If the mutation tool reports it as surviving, the test is wrong. The management session runs the mutation tool, not just the unit test. |
| #293, #308 and #312 stay open while the evidence says the lane is green, so the next reader assumes Kolla is unreliable. | Out of scope here, and recorded here. Raise with Mikal as a candidate `merge-ci-triage` pass at the end of this phase. |
| The role move stalls on a Kerbside release that nobody has scheduled. | Recorded in the master plan's Dependencies section as on the critical path, and raised with Mikal in this phase's report. |

## Definition of done

- [x] `grep -n "docs/deployment/\*.md" tools/check-backend-tls-claims.py`
      matches `DOC_PATHS`.
- [x] `tools/mutate-backend-tls-claims.py` reports every mutation
      caught, including one named "deployment pages dropped from
      the scanned set".
- [x] `tox -epy3` and `tox -eflake8` pass.
- [x] `docs/index.md` has a `### Deployment Options` heading
      between `### Use Cases` and `### Operator Documentation`,
      with four rows, none a link, in decision 2's order. The
      closing sentence says which column carries the link
      (review round 2 of #555).
- [x] `tools/check-backend-tls-claims.py` exits 0.
- [x] `git ls-files docs/deployment` prints nothing (decision 3).
- [x] Both issues from step 1d exist, and their numbers are in
      the master plan.
- [x] `pre-commit run --all-files` passes.

## Back brief

Before executing any step of this plan, back brief the operator
on your understanding of the plan and how the work you intend to
do aligns with it. Step 1d's issue wording is the one gate: it is
published outside the repository, so it is shown to Mikal before
it is filed.
