# Audit: Bypass actor identity

## What we check

A default-branch ruleset that requires a pull request, status checks
and the merge queue is only as strong as the list of actors allowed to
bypass it. The one automation identity that may bypass is a GitHub App
whose private key lives in a `review-marks` environment, and whose
deployment branch policy admits only the default branch. That way a job
on any other ref -- a pull request that edits the review-marks workflow,
a tag pushed at an edited commit -- cannot read the key, so it cannot
mint a token that bypasses the ruleset. A long-lived personal access
token, held as a repository or organisation secret and readable from any
branch, cannot have that property, which is why the bypass moved to the
App.

This criterion measures the half of that arrangement the audit's
read-only token can see: the `review-marks` environment and its
deployment branch policy. The actor list on the ruleset, and the
secrets a repository holds, are confirmed by a reviewer against the
table below, because the read token sees neither (GitHub omits a
ruleset's `bypass_actors` for a caller that cannot edit the ruleset, and
does not let that token list a repository's secrets).

Where a `review-marks` environment exists, its deployment branch policy
must be exactly this, and nothing looser:

* `deployment_branch_policy` is
  `{"protected_branches": false, "custom_branch_policies": true}`.
* `.../deployment-branch-policies` holds exactly one entry.
* that entry is `type: branch`, its `name` is the repository's default
  branch, and the name contains no wildcard character (`*`, `?`, `[`,
  `]`).

"Admits the default branch" is not the test. A `protected_branches:
true` policy admits every protected branch; a branch name is a glob, so
`develop*` matches more than `develop`; and a policy can be `type: tag`,
so a set such as {`develop`, tag `*`} admits the default branch and also
lets any writer push a tag at a commit with an edited workflow and read
the key. Any of these fails: an extra policy, a tag policy, a wildcard
name, `protected_branches` true, `custom_branch_policies` false, or no
branch policy at all.

A repository with no pull-request ruleset on its default branch and no
`review-marks` environment reports `not_applicable`: there is nothing to
bypass and no key to protect. A repository that has the environment is
judged on the policy above whether or not it also has a pull-request
ruleset, because the key is worth protecting wherever it is held.

A repository with a pull-request ruleset and no `review-marks`
environment passes: no environment is no key, so no branch can reach
one, and a review-marks push falls back to `github.token`, which that
ruleset refuses. The state is fail-closed, which is why it is a pass
rather than a failure. It is still drift in a repository that runs
`prune-reviews`, because that workflow names the environment and
GitHub recreates it with no policy the first time a job references it
-- so review marks stop landing there until an operator restores the
environment with its policy. The loud signal for that is the failing
push in the repository itself, not this criterion.

The check reads the default branch name, then
`GET /repos/shakenfist/<repo>/environments/review-marks` and, where the
environment exists,
`GET .../environments/review-marks/deployment-branch-policies`. Only
where the environment is absent does it read
`GET /repos/shakenfist/<repo>/rules/branches/<branch>`, to decide
between a pass and `not_applicable`: a repository with a compliant
environment never reads the rules at all, so a hiccup on that endpoint
cannot turn its verdict into an error. It never asks for
`bypass_actors`: `rules/branches/<branch>` returns effective
rules and carries no bypass list, and the actor rules are the
reviewer's half below.

A 404 on the environment is ambiguous -- GitHub answers it both for an
environment that does not exist and for one the token may not read --
and the two mean opposite things here, so the check resolves it against
`GET /repos/shakenfist/<repo>/environments`. A listing that answers
without naming `review-marks` is absence. A listing that names it, or
that does not answer at all, is a check error: a token whose
environment read had regressed would otherwise report every repository
in the fleet compliant at once. Any other error from any call, or a null
where a value is required -- a deployment policy with no name or no
type, a policy list that is not a list, a `total_count` past the page
-- is likewise a check error rather than a failure: it says the audit
could not ask, not that the configuration is wrong.

### Required, but confirmed by a reviewer

Two properties cannot be measured with the audit's read-only token, and
are confirmed by a reviewer instead.

**Every bypass actor on a default-branch ruleset is an identity only
reviewed code can use.** The read token cannot see `bypass_actors`, so a
reviewer reads each ruleset's bypass list (with a token that can edit
the ruleset) and judges every actor by its type:

| `actor_type` | Verdict |
|--------------|---------|
| `Integration` (a GitHub App) | Passes only for the review-marks App, `shakenfist-review-marks`, actor id `5538020`. Any other App fails: it is an automation identity whose key could bypass the ruleset. |
| `RepositoryRole` | Passes only for the admin role, actor id `5`. Any other repository role fails. |
| `OrganizationAdmin` | Passes. |
| `Team` | Fails: a team bypass belongs to whichever accounts are in the team, including automation accounts, and the identity cannot be pinned to reviewed code. |
| `DeployKey` | Fails: a deploy key with write access is an automation credential, like a personal access token. |
| any other value | An error, never a pass: an actor type the audit does not recognise has not been judged. |

**One known team bypass.** `sf-can-skip-merge-queue` (`Team`, actor id
`11722172`) is a bypass actor on every one of the organisation's
pull-request rulesets except `actions`' `21132503`, so by the table
above all of those fail this half today. `actions` is the exception
because its ruleset carries the review-marks App as its only bypass
actor; it gained its pull-request rule on 2026-11-11. Stated without a
count deliberately: the previous wording here named a number, which
went stale as repositories were standardised and then read as a verdict
about the ones it no longer covered. (Checked across the organisation
on 2026-11-11. Three private repositories answer 403 to the rulesets
endpoint and hold no rulesets at all.)

The team's automation member was removed on 2026-11-08, leaving
`mikalstill` and `sfconductor`. `sfconductor`'s bypasses are pull
request merges that skip the queue rather than pushes to a default
branch; `mikalstill` is a person holding admin, whose bypasses the
history counts without classifying. The rule-suite history read on
2026-10-29 covered the seven repositories `PLAN-bot-bypass-identity.md`
names rather than the whole organisation, and covers the month before
that read and so the period before the removal above; it records
`sfconductor` bypassing in five of those seven (shakenfist 52,
kerbside 7, client-python-k3s 2, ryll 1 and instar 1) and `mikalstill`
in all seven. That read is
`repos/<org>/<repo>/rulesets/rule-suites?time_period=month&rule_suite_result=bypass`
per repository, counted by each entry's `actor_name`, with the
merge-rather-than-push classification coming from resolving an entry's
`after_sha` to the commit it produced. GitHub keeps about a month of
that history, so the figures above are the record of that read rather
than something a later reader can re-run; the query is named so the
next one can be made the same way. A reviewer records the team as a
known exception, with what `sfconductor` merges with still to be
settled, rather than as a new finding; the exception ends when the team
stops being a bypass actor.

The admin-role and `OrganizationAdmin` passes rest on no automation
identity holding an organisation-owner or repository-admin role -- in
particular on any repository where the review-marks App is installed,
where an admin could widen the environment's policy. The audit cannot
see role membership, so a reviewer confirms it.

The admin role's actor id is `5`, from GitHub's documented set of
predefined repository base-role ids (read `1`, triage `2`, write `3`,
maintain `4`, admin `5`); no live ruleset bypasses the admin role, so
the id is taken from the documentation rather than read off a ruleset.

**The App's key is only ever an environment secret.**
`REVIEW_MARKS_APP_KEY` must appear in no repository's
`actions/secrets` and in no organisation secret, only as a secret of the
`review-marks` environment. `secrets.REVIEW_MARKS_APP_KEY` resolves at
all three levels, and the workflow's mint-when-present guard would run
just as quietly with a key any branch could read, which would defeat the
whole arrangement. The read token cannot list secrets, so a reviewer
confirms the key is held only in the environment.

**One accepted residual, on hunkydory.** The runner-controller App
`shakenfist-k3s-ci-arc` holds `administration: write` on hunkydory,
which edits rulesets and environments there. Whoever holds that App's
key could widen hunkydory's `review-marks` deployment branch policy, read
the review-marks key from a branch workflow, and mint push tokens for
every repository the review-marks App is installed on. The residual
stands until the runner-controller App's permission is narrowed to the
runner registration it actually needs; until then a reviewer accepts it
knowingly rather than the audit reporting it clean.

Since 2026-11-11 that residual reaches further than the product
repositories. `PLAN-workflow-ref-trust.md` step 2a installed the
review-marks App on `shakenfist/actions`, which serves the fleet's
composite actions and reusable workflows at `@main`, so the same chain
now ends in a push to the repository whose code runs inside every other
repository's jobs -- and in a bypass of the pull-request ruleset that
step created. Nothing about the chain changed; what changed is what sits
at the end of it. A reviewer accepting the residual should know that is
the reach.

## Template

No template -- this is a one-time ruleset and environment configuration
change.

To inspect the current configuration:

```bash
gh api repos/shakenfist/<repo>/environments/review-marks \
    --jq '{deployment_branch_policy}'
gh api repos/shakenfist/<repo>/environments/review-marks/deployment-branch-policies \
    --jq '.branch_policies[] | {name, type}'
gh api repos/shakenfist/<repo>/rulesets/<id> --jq '.bypass_actors'
```

To create or repair the environment from the CLI, with the
default-branch policy (the default branch is `develop` everywhere except
`actions` and `development`, where it is `main`):

```bash
gh api -X PUT repos/shakenfist/<repo>/environments/review-marks --input - <<'EOF'
{"deployment_branch_policy": {"protected_branches": false,
                              "custom_branch_policies": true}}
EOF
gh api -X POST repos/shakenfist/<repo>/environments/review-marks/deployment-branch-policies \
    -f name='<default branch>' -f type=branch
```

A `PUT` replaces the environment's configuration rather than merging
into it, so anything omitted from the body is cleared: an environment
that holds required reviewers or a wait timer loses them. `review-marks`
deliberately holds neither -- its protection is the branch policy alone
-- so the body above is complete for it, but any environment that does
hold a protection rule must carry it in the body too.

Read both back afterwards. To add the review-marks App as a bypass
actor, fetch the ruleset, append the App to `bypass_actors` preserving
every other field, and PUT it back:

```bash
gh api repos/shakenfist/<repo>/rulesets --jq \
    '.[] | select(.target == "branch") | {id, name}'
# The guard is inside the transform rather than before it. GitHub
# omits bypass_actors for a credential it does not show it to, so
# appending to a field that is not there would drop every actor the
# ruleset has. error() says so on stderr and exits non-zero, which a
# check pasted in as a separate command would not. The map() drops
# any existing entry for the App first, so running this twice leaves
# one entry rather than two.
gh api repos/shakenfist/<repo>/rulesets/<id> | jq \
    '{name, target, enforcement, conditions, rules,
      bypass_actors: (if has("bypass_actors")
                      then [.bypass_actors[] | select(.actor_id != 5538020)]
                           + [{actor_id: 5538020, actor_type: "Integration",
                               bypass_mode: "always"}]
                      else error("bypass_actors not visible to this credential: "
                             + "use one that can edit the ruleset") end)}' > /tmp/ruleset.json
gh api -X PUT repos/shakenfist/<repo>/rulesets/<id> --input /tmp/ruleset.json
```

GitHub refuses an App that is not installed on the repository as a
bypass actor, so a PUT that is rejected means the installation is
missing rather than the request being wrong.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#bypass-actor-identity).
