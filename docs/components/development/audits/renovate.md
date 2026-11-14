# Audit: Renovate for dependency bumps

## What we check

* `.github/workflows/renovate.yml` exists -- runs renovate hourly on a
  self-hosted runner. `RENOVATE_AUTODISCOVER_FILTER` is
  `${{ github.repository }}`, supplied by the runtime, so the file is
  copied verbatim rather than edited per repository.
* `renovate.yml` does not run `renovatebot/github-action`, which needs
  a Docker daemon on the runner.
* `renovate.yml` matches the template's apart from the versions
  Renovate bumps: the value of each `PINNED_*_VERSION` variable
  (`PINNED_RENOVATE_VERSION` and `PINNED_HASHIN_VERSION`) and the ref
  of each action it uses.
* `renovate.json` exists. Nothing is checked about what it holds
  beyond the two entries below, so a repository that keeps the fleet
  policy locally rather than extending the preset still passes; the
  preset is the convention, not a requirement this check enforces. It
  is not expected to be byte-identical to the template's copy.
* `renovate.json` extends `customManagers:githubActionsVersions`, so
  the Renovate version pinned in `renovate.yml` is bumped. Naming it
  directly passes, and so does extending the fleet preset, which
  names it.
* `renovate.json` enables the `pre-commit` manager, where the
  repository has remote pre-commit hooks to manage. Enabling it
  directly passes, and so does extending the fleet preset, which
  enables it.

### The central runner

Everything above describes the arrangement the criterion checks: a
copy of `renovate.yml` in each repository, running Renovate over that
repository alone. The fleet is moving to a single workflow that runs
Renovate over every repository from one place, and that workflow is
already in this repository as `.github/workflows/renovate-fleet.yml`.
It has no schedule yet, and runs only when it is dispatched by hand,
with a Renovate `dryRun` mode and an autodiscover filter as inputs, so
a pass can be read-only or confined to one repository. A schedule here
while every repository still runs its own hourly copy would have two
Renovate instances working the same repository, racing on branch
creation to produce duplicate or half-written pull requests, and the
runner's concurrency group cannot prevent that because the two runs
are different workflows in different repositories. Turning the
schedule on is therefore a step of the cutover rather than part of
adding the workflow. It will be hourly at minute 23 rather than on the
hour, because GitHub delays and sometimes drops scheduled events at
the top of the hour and a dropped run here would delay every
repository at once. It installs Renovate from npm in a step that holds no
secret, exactly as the template does, for the reasons given under
[Renovate without Docker](#renovate-without-docker) and in the
template README.

Its global configuration is `renovate-runner.json` at the root of this
repository. That file decides two things. Which repositories are
discovered: autodiscovery over `shakenfist/*`, with `requireConfig`
set to `required`, so Renovate manages a repository that carries a
Renovate configuration and ignores one that does not, and with
onboarding off, so it opens no pull request to add a configuration
where there is none. And one temporary `packageRules` entry that
disables every dependency in `.github/workflows/renovate.yml` and
`templates/renovate/renovate.yml`. Matching the files rather than the
packages covers everything in them at once, whichever of the Renovate
pin, the actions or the container action a given copy carries, and the
rule is to be removed when the per-repository copies are. The file is
validated by its own hook in default mode, which accepts global
options, while every other Renovate configuration is validated with
`--no-global`; `scripts/tests/test_hooks.py` holds each configuration
in the tree to exactly one of the two.

The job runs on an ephemeral VM, `[self-hosted, vm, debian-13, s]`,
and names the `renovate-write` environment, not the static pool the
per-repository copies use. `RENOVATE_TOKEN` pushes branches to every
repository the runner manages, and the static runners share one user
and one home directory between jobs, so code another job ran could
leave a `.pth` file or a `~/.gitconfig` hook there for this job to run
with that token. A VM starts from the image and is thrown away after
one job, which is the same reasoning `consistency-audit.yml` applies
to its token-holding jobs. The environment is where that token is meant to be
held, with deployment restricted to `main`, so that a branch which has
edited the workflow cannot dispatch itself and run with the
organisation's push credential. That restriction is a repository
setting rather than anything in the tree, and naming an environment
does not create it: a workflow referencing one that does not exist
gets an environment created for it, with no protection rules and no
secrets. So the protection is real only once somebody has configured
it, which is a thing to verify out of band before the cutover rather
than something this file can promise. The workflow's own check of the
ref is the weaker half of the pair -- a branch can edit a condition
out of its own copy -- and it makes a branch dispatch show as skipped
rather than failed. The cost is that a VM pays the whole
install on every run, with no cache to reuse.

Before Renovate starts, every run probes the token with
`scripts/renovate-check-push.py`. For each unarchived repository in
the organisation whose default branch carries a `renovate.json` it
asks GitHub whether the token has push permission, and the job fails
naming every repository where it does not, not only the first. The
probe derives the set the way `requireConfig` does, so it stays right
as repositories are added and retired. It reads `renovate.json` alone,
though Renovate accepts other file names, because this criterion is
what requires `renovate.json`. It also refuses to pass on a managed set
smaller than the floor it is given, because a token that quietly lost
sight of most of the organisation would otherwise shrink the set and
the probe and autodiscovery would agree on the smaller fleet. Without
the probe, a repository the token cannot write is one Renovate reads
and plans updates for, then fails to act on every hour with nothing
but a line in a log to say so.

What the probe cannot see is whether the token may write a workflow
file. Push permission is not the same capability: a classic personal
access token needs the `workflow` scope and a fine-grained one needs
Workflows: write, and without it GitHub refuses a push whose diff
touches `.github/workflows/`. Much of what Renovate proposes in this
fleet is exactly that -- action references, and the Renovate pin a
custom manager reads from a version comment -- so the probe can report
that the token reaches every repository while every workflow branch
still fails. The cutover's canary is what establishes that capability,
by watching a workflow-file update land.

The per-repository copies of `renovate.yml` are still present and
still running. Disabling them, deleting them, and changing this
criterion to match are later work, not part of adding the runner, and
"What we check" above is accurate for as long as they exist.

### Renovate without Docker

`renovatebot/github-action` runs Renovate as a container. The static
runner pool is gaining ephemeral pods on a Kubernetes cluster, and
they have no Docker daemon, so the action fails on every run a pod
takes. The template now sets up Node and runs a pinned Renovate from
npm, which works on a VM and a pod alike. A workflow step that still
uses the action is a finding. The action named in a comment is not.

The template pins Renovate behind a `# renovate:` comment, which only
the built-in `customManagers:githubActionsVersions` preset reads. A
copy of the new workflow without that preset reaching `renovate.json`,
directly or through the fleet preset, runs a Renovate that never bumps
itself, so that is a finding too.
`regexManagers:githubActionsVersions`, the preset's name before
Renovate renamed it, also passes.

Both findings go in the one issue, so a repository still on the old
copy is told to change both files at once. The template README covers
what else changes when Renovate leaves its container. The one that
matters is lock files, which Renovate now refreshes with the runner's
own tools. The template provides npm always, a Rust toolchain when
the repository has a `Cargo.lock`, and `hashin` when it has a
`requirements*.txt`, so every repository can copy it unmodified.

### The workflow is the template's

`renovate.yml` is copied verbatim, so any difference from
`templates/renovate/renovate.yml` is drift, except the versions
Renovate bumps in each copy on its own schedule: the value of each
`PINNED_*_VERSION` variable, and the ref after `@` on each `uses:` line
(with the version comment digest pinning leaves after it). A copy
whose Renovate or whose actions are ahead of or behind the template's
still matches; the action's name still has to. `node-version` is not
among them: the fleet preset stops Renovate bumping it, and it is the
line the check exists to hold.

The drift that prompted this was Node. Renovate proposed Node 26 for
the workflow's `setup-node` step, the pull request merged in eleven
repositories, and each one's Renovate, which needs Node `^24.11.0`,
then failed on every hourly run. The fleet preset now stops Renovate
proposing that bump, but a copy can still drift by hand, and the
workflow is where Node has to match the Renovate it runs.

The finding names each place the copy differs, up to three, with the
copy's line and the template's, and counts the rest. A copy still on
the container action gets only that finding, which already says to
copy the template. If the audit cannot read its own template, it
reports an error rather than a verdict.

### The fleet preset

The policy every repository shares lives once, in `renovate-fleet.json`
at the root of this repository, and each repository's `renovate.json`
extends it with `"extends": ["local>shakenfist/development:renovate-fleet"]`.
A policy copied into every repository only reaches the ones that are
re-synced, so a schedule or a rule changed after a repository was
onboarded stayed behind; extended, a change reaches the fleet once.

The preset holds the author and assignee Renovate uses, the dependency
dashboard, rollback pull requests, a three day `minimumReleaseAge`,
the `pre-commit` manager, `customManagers:githubActionsVersions`, the
`Australia/Sydney` timezone, and the cadence: ordinary updates
`on saturday`, majors and the CI tooling group `on the first day of the
month`, with at most 2 pull requests an hour and 10 open at once
(Renovate's own defaults, written down rather than inherited). Minor,
patch and digest updates are never merged automatically. The CI
tooling group puts GitHub Actions and pre-commit hook revisions, every
update type, digests included, into one monthly pull request. The two
rules that keep `node-version` and the pinned Renovate in step, and the
`shellcheck-py/shellcheck-py` versioning rule, are in it too. It sets
no `vulnerabilityAlerts`: Renovate's defaults already drop the
schedule, `minimumReleaseAge` and grouping for a security fix, so a
fix for a vulnerability Dependabot reports arrives at once rather than
waiting for Saturday. One rule is an exception to that. A `packageRules`
entry with `enabled: false` suppresses even a vulnerability fix, and
the preset's Node-major disable rule is one, so Node majors in the
Renovate workflows are the one update type the immediate security path
does not reach.

Renovate reads a `local>` preset from the default branch of the
repository that holds it, so a change to `renovate-fleet.json` cannot
be tried from a pull request branch: it takes effect for the whole
fleet the moment it merges. The only check before that is
pre-commit's `renovate-config-validator`. Its default `files:`
pattern matches `renovate.json` but not `renovate-fleet.json`, and a
hook that silently skips the file it was added for still passes, so
the hook's pattern is overridden with one that covers any Renovate
configuration or `renovate-*.json` preset in any directory, rather
than the files that happen to exist; `scripts/tests/test_hooks.py`
holds it to every such file in the tree. It also runs with
`--no-global`: given a file as an argument the validator treats it as
a global self-hosted configuration, which accepts a self-hosted-only
key such as `onboarding` in a file that is meant to be a repository
preset.

Two of the checks above, the `pre-commit` manager and the version
comments, accept a repository that extends the preset. Each one reads
the parsed configuration through a helper that knows nothing about any
preset, and a single predicate beside them recognises the `extends`
string; a check passes when either says so. Keeping the name out of
the helpers is what stops a repository being passed on a string alone.
That is still trust by name, and it holds only while the preset keeps
its promises, so the tests load the real `renovate-fleet.json` and
assert that each helper accepts it on its own. A commit that removed
the manager or the version comment extend from the preset would
otherwise leave every repository in the fleet passing on a promise
nothing kept.

What stays local to a repository is what only that repository needs:
`development`'s `templates/` patterns below, the Python constraints,
package grouping and range strategy sections after them.

### The pre-commit manager

Renovate's `pre-commit` manager is opt-in: cargo, dockerfile,
github-actions and the Python managers are on by default, but
`.pre-commit-config.yaml` is not read at all unless the config says so.
A repository can therefore look fully renovate-managed while its hook
revisions age untouched. That matters more than the usual stale
dependency: pre-commit hooks are the linters gating every commit, so an
unwatched pin means the thing judging everything else is itself
unjudged. `instar` was four months behind on `actionlint` while its
cargo, dockerfile and github-actions dependencies were current.

Any of renovate's three enabling forms passes, as does extending the
fleet preset, which enables the manager for the repository:

```json
{"pre-commit": {"enabled": true}}
{"enabledManagers": ["pre-commit", "..."]}
{"extends": [":enablePreCommit"]}
```

The check applies only when there is something to bump: a repository
with no `.pre-commit-config.yaml`, or one whose hooks are all
`repo: local`, passes without the manager. `sfui`'s
`"pre-commit": {"enabled": true}` is the form repositories carried
before the preset.

Enabling the manager is not enough for the shellcheck hook:
shellcheck-py's four-part tags need the fleet preset's regex versioning
rule, or the lookup fails on the dependency dashboard and the hook is
never bumped. This check does not look for that rule; see
[the template README](https://github.com/shakenfist/development/blob/main/templates/renovate/README.md#shellcheck-pys-four-part-tags).

### Repositories that ship workflow templates

Renovate's `github-actions` manager only reads
`.github/workflows`, `.github/actions` and `workflow-templates`. A
repository that keeps workflows anywhere else -- `development` ships
the fleet's templates from `templates/` -- gets its own workflows
bumped while the copies it hands out stay behind, so every repository
onboarded after a bump starts life a version stale.

Extending the manager's file patterns brings them into view. The
patterns extend the defaults rather than replacing them, so only the
extra path is named:

```json
{
  "github-actions": {
    "managerFilePatterns": ["/^templates/.+\\.ya?ml$/"]
  }
}
```

This applies to `development` today, and to any repository that grows
a second home for workflow files. It stays in `development`'s own
`renovate.json` rather than the fleet preset, because it only applies
here.

`customManagers:githubActionsVersions` has the same blind spot, and
its file patterns cannot be extended from outside the preset. So
`development` also carries a `customManagers` entry with the preset's
match string and `templates/` as its only file pattern. That keeps the
template's Renovate pin moving with the copy the repository runs.

### Python version constraints

Projects supporting multiple Linux distributions set
`constraints.python` to the oldest Python they support, so renovate
stops proposing updates the oldest distribution cannot install:

```json
{"constraints": {"python": ">=3.8"}}
```

Currently required for: agent-python, occystrap.

The value matches `requires-python` in `pyproject.toml` -- both derive
from the system Python of the oldest supported distribution. Where a
project has a supported platforms matrix that table lives in
`ARCHITECTURE.md`, and both files carry a comment pointing back to it,
so dropping a distribution means three edits. CI should test on the
oldest supported Python, so a bump that breaks it fails there rather
than on a user's machine.

### Package grouping and range strategy

Tightly coupled dependencies (the grpc stack, for instance) are grouped
so they bump together:

```json
{
  "packageRules": [
    {
      "description": "Group grpc packages together",
      "matchPackagePatterns": [
        "^grpcio",
        "^googleapis-common-protos",
        "^protobuf"
      ],
      "groupName": "grpc packages"
    }
  ]
}
```

Where a family is released in lockstep rather than merely coupled --
the oslo libraries cut coordinated releases, so four pull requests
arrive the same evening for one upstream event -- grouping stops being
a preference and becomes an audited requirement. See
[renovate-lockstep-groups.md](/components/development/audits/renovate-lockstep-groups/), which is
the criterion that measures it; this section stays the general advice.

Server projects (shakenfist, kerbside) pin exactly (`==`) and use
renovate's default range strategy, which bumps those pins on every
release. That is right for software running on infrastructure we
control.

Client and library projects (agent-python, client-python,
client-python-k3s, clingwrap, occystrap) constrain loosely (`>=`) so
they install across a wide range of distributions and Python versions.
For those the grpc group adds `"rangeStrategy": "widen"`, so renovate
only opens a pull request when a new major version falls outside the
existing range.

Without it renovate raises the floor of every `>=` constraint on every
minor release, which is churn -- and worse than churn on the newest
distributions. Fedora 43 ships Python 3.14, and older grpcio releases
have no wheels for it; a loose constraint lets pip choose a version
that does, while a raised floor or an exact pin sends it to a source
build that fails wherever a C++ compiler is missing. Nothing is given
up by staying loose: the gRPC wire protocol is stable across minor
versions, and proto3 serialization is stable within a major version.

## Template

Template: `templates/renovate/`
See: `templates/renovate/README.md`

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#renovate).
