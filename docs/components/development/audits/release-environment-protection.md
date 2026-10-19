# Audit: Release environment protection

## What we check

A repository that publishes through `.github/workflows/release.yml`
gates its publishing jobs behind a GitHub environment, conventionally
`release`. That environment is the only thing between somebody
pushing a tag and a signed release on PyPI, so every environment a
job in `release.yml` names must:

* **exist.** GitHub creates an environment that a job names but that
  does not exist, with no protection rules at all, the first time the
  job runs. A missing environment is therefore not a configuration
  error that stops a release, but a release that nobody approves.
* **have a required reviewer.** Its `protection_rules` include a
  `required_reviewers` rule naming at least one reviewer.
* **deploy only from tags.** Its `deployment_branch_policy` is set,
  with `custom_branch_policies` true, and every one of its deployment
  policies is of type `tag`, with a pattern narrower than `*` or
  `**`, which match every tag. Without a policy any branch's workflow
  can name the environment and read its secrets; with
  `protected_branches` instead, a branch rather than a tag can
  deploy; and a `branch` policy beside the tag one reopens the same
  door.

A repository without `release.yml` reports `not_applicable`. A
`release.yml` that names no environment fails, because nothing in it
waits for a human.

The check reads `GET /repos/shakenfist/<repo>/environments/<name>`
and, where the environment has custom policies,
`GET .../environments/<name>/deployment-branch-policies`. A 404 on
the environment is the missing-environment failure above. Any other
error from either call is a check error rather than a failure: it
says the audit could not ask, not that the environment is wrong.

An environment named by an expression (`environment: ${{ ... }}`) is
resolved at run time, so the audit cannot tell which environment the
job will use or whether it exists. It is reported as a failure that
asks for the name to be written literally, rather than passed: the
point of the criterion is that a release is known to wait for a
reviewer, and an unresolvable name is exactly the case where nobody
knows.

### Required, but confirmed by a reviewer

Three further properties of the environment are deliberately not
measured.

* **The reviewers can actually approve.** The check counts the
  entries in the `required_reviewers` rule, not the people behind
  them. A team with no members, or a user who has since lost access,
  satisfies the check, but leaves nobody who can approve a release,
  or leaves only an administrator through `can_admins_bypass`.
  Telling those cases apart needs team membership, which is more
  calls and wider permissions than the read token has. A reviewer
  confirms the list names people who can approve.

* **`prevent_self_review` stays off.** The fleet has one maintainer
  with the right to approve a release. Turning self-review prevention
  on would mean the person who pushes a tag can never approve it, so
  no release could ever be published. Whether it is right depends on
  who is in the reviewer list, which is a judgement about people
  rather than a setting a check can compare to a constant.
* **`can_admins_bypass`** lets an organisation administrator deploy
  without waiting for approval. Whether that is acceptable depends on
  who the administrators are, which the API cannot judge; where they
  are the same people as the reviewers, it changes nothing about who
  can release. A reviewer confirms it is a deliberate choice for the
  repository.

## Template

No template -- this is a one-time configuration change, described
step by step in `templates/release-automation/RELEASE-SETUP.md`.

To inspect the current configuration:

```bash
gh api repos/shakenfist/<repo>/environments/release \
    --jq '{protection_rules, deployment_branch_policy}'
gh api repos/shakenfist/<repo>/environments/release/deployment-branch-policies \
    --jq '.branch_policies[] | {name, type}'
```

To create or repair the environment from the CLI, with a reviewer and
a single `v*` tag policy:

```bash
gh api -X PUT repos/shakenfist/<repo>/environments/release --input - <<'EOF'
{"reviewers": [{"type": "User", "id": <reviewer user id>}],
 "prevent_self_review": false, "wait_timer": 0,
 "deployment_branch_policy": {"protected_branches": false,
                              "custom_branch_policies": true}}
EOF
gh api -X POST repos/shakenfist/<repo>/environments/release/deployment-branch-policies \
    -f name='v*' -f type=tag
```

Read both back afterwards.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#release-environment-protection).
