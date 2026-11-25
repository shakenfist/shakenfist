# Audit: Renovate for dependency bumps

## What we check

Renovate reaches every managed repository from one workflow in this
repository, the central runner below, and the policy it applies
lives once, in the fleet preset below. This criterion checks that a
repository is set up to be governed by both. It reports every finding
in the one issue, so that one change closes it.

* `renovate.json` exists and is a JSON object. The runner manages a
  repository only when it carries a Renovate configuration, so a
  repository without one gets no dependency updates at all, and the
  runner's push probe looks for `renovate.json` by that name alone. A
  file that is there but does not parse to an object is a finding of
  its own rather than an abort of the run, since Renovate cannot read
  it either.
* `.github/workflows/renovate.yml` does not exist. The runner manages
  the repository, and a per-repository Renovate workflow, enabled
  again, races it: two Renovate instances over one repository race on
  branch creation and produce duplicate or half-written pull requests.
  A disabled copy fails too, since it is one click from running again.
  The check does not look inside the file: whatever it runs, the
  container action included, it should not be there.
* `renovate.json` extends `local>shakenfist/development:renovate-fleet`,
  as an entry of an `extends` list; an `extends` that is not a list is
  named as the reason. Without the preset a configuration still
  validates and Renovate still runs, on its own defaults rather than
  the fleet's policy: among other things, with no weekly window and no
  `pre-commit` manager.
* `renovate.json` sets no top-level key that the preset also sets,
  other than `$schema`, `extends` and `packageRules`. Every such key is
  named in the one finding. Keys the preset does not set --
  `constraints`, `lockFileMaintenance`, `customManagers`, a manager's
  own block such as `github-actions` -- pass.

The set of keys the last rule forbids is read, not listed: the check
loads `renovate-fleet.json` from the audit's own checkout and takes
its top level. A key added to the preset is therefore protected from
the next run, and a repository that already sets it starts failing at
that run, which is worth knowing before adding one. If the audit
cannot read or parse its own copy of the preset, it reports an error
rather than a verdict: that is our outage, not the repository's.

### Why a key the preset sets is a failure

A repository's own configuration is resolved after the presets it
extends, so a key it sets at its top level replaces the preset's value
outright. When the fleet came to move onto the preset, every managed
repository carried `"schedule": ["after 9pm"]` from the days when each
kept the whole policy locally, and that silently beat the preset's
Saturday window. Nothing about it looks wrong: the configuration
validates, Renovate runs, and the cadence simply does not change. A
check that accepted it would report a repository as on the preset
while the preset governed nothing there. The other keys the
repositories carried -- `assignees`, `dependencyDashboard`,
`gitAuthor`, `minimumReleaseAge`, `rollbackPrs` and a `pre-commit`
block -- restated the preset's values, which is harmless only until
the preset changes and the local copy does not.

The three exceptions are not overrides. `$schema` only names the
schema, `extends` is how a repository reaches the preset at all, and
Renovate appends a repository's `packageRules` to the preset's rather
than replacing them.

This forbids a repository from deliberately setting, say, its own
`minimumReleaseAge`. A repository that genuinely needs a different
policy can still have one, in a `packageRules` entry scoped to the
packages or files it is about, which is narrower and is reviewed as a
rule rather than hidden in a top-level value; the top level is the
fleet's.

Before the central runner, this criterion required a per-repository
workflow copied verbatim from a template, and checked that
`renovate.json` enabled the `pre-commit` manager, extended
`customManagers:githubActionsVersions` and carried a versioning rule
for `shellcheck-py/shellcheck-py`. The workflow and its template are
gone. The other three are properties of the preset now, which a
repository that extends it has by construction; see
[The fleet preset](#the-fleet-preset) for what holds the preset to
them.

### The central runner

The fleet runs Renovate from a single workflow in this repository,
`.github/workflows/renovate-fleet.yml`, which works over every managed
repository in one pass. The conductor's periodic tier in
shakenfist/private-ci dispatches it about once an hour. The workflow
has no `schedule:` trigger, because GitHub's own cron delivered
roughly one hourly event in seven: over 35.6 hours of an hourly
schedule it started 5 runs, created at whatever minute it chose. The
conductor sends no inputs, so every scheduled run gets the dispatch
defaults: a real write pass over the whole fleet, logging at `info`.
By hand it can be dispatched with a Renovate `dryRun` mode, an
autodiscover filter that confines the pass to one repository, and a
`debug` log level for a deliberate one-off look at what Renovate is
doing; this repository is public, and so are its run logs. The
conductor's own documentation is private-ci's `docs/periodic-ci.md`.

The consequence is a dependency: if the conductor is down the runner
stops, and with it dependency updates and security pull requests for
the whole fleet. There is no fallback schedule, deliberately, since one
would hide an outage behind a cadence that merely looked poor.

No repository runs its own Renovate workflow. Each used to carry a
copy of one, running Renovate over that repository alone; the copies
were disabled before the runner was dispatched on a recurring basis,
because the runner's concurrency group cannot keep it from racing
them -- the two runs are different workflows in different
repositories. The copies are being deleted, and the criterion fails a
repository that still carries one.

Its global configuration is `renovate-runner.json` at the root of this
repository. That file decides which repositories are discovered:
autodiscovery over `shakenfist/*`, with `requireConfig` set to
`required`, so Renovate manages a repository that carries a Renovate
configuration and ignores one that does not, and with onboarding off,
so it opens no pull request to add a configuration where there is
none. It also holds one temporary `packageRules` entry, which disables
every dependency in `.github/workflows/renovate.yml`, so that a
repository whose disabled copy has not yet been deleted is proposed no
updates for a file that no longer runs. Matching the file rather than
the packages covers everything in it at once, whichever of the
Renovate pin, the actions or the container action a given copy
carries. The rule is removed when the fleet's last copy is. The file
is validated by its own hook in default mode, which accepts global
options, while every other Renovate configuration is validated with
`--no-global`; `scripts/tests/test_hooks.py` holds each configuration
in the tree to exactly one of the two.

The job runs on an ephemeral VM, `[self-hosted, vm, debian-13, s]`,
and names the `renovate-write` environment, rather than running on the
static pool. `RENOVATE_TOKEN` pushes branches to every repository the
runner manages, and the static runners share one user and one home
directory between jobs, so code another job ran could leave a `.pth`
file or a `~/.gitconfig` hook there for this job to run with that
token. A VM starts from the image and is thrown away after one job,
which is the same reasoning `consistency-audit.yml` applies to its
token-holding jobs. The environment is where that token is meant to be
held, with deployment restricted to `main`, so that a branch which has
edited the workflow cannot dispatch itself and run with the
organisation's push credential. That restriction is a repository
setting rather than anything in the tree, and naming an environment
does not create it: a workflow referencing one that does not exist
gets an environment created for it, with no protection rules and no
secrets. So the protection is real only once somebody has configured
it, which is a thing to verify out of band rather than something the
workflow can promise. The workflow's own check of the ref is the
weaker half of the pair -- a branch can edit a condition out of its
own copy -- and it makes a branch dispatch show as skipped rather than
failed. The cost is that a VM pays the whole install on every run,
with no cache to reuse. It needs outbound access to GitHub
(`setup-node` downloads Node from its `actions/node-versions`
releases), the npm registry, PyPI and Rust's distribution server, and
no Docker daemon.

Before anything is installed, every run probes the token with
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

The checkout is of this repository alone, for `renovate-runner.json`
and the probe, and sets `persist-credentials: false`. Renovate clones
each repository it manages itself, with `RENOVATE_TOKEN`, into
`RENOVATE_BASE_DIR`, a directory under `runner.temp`, so no job
credential is left in a `.git/config` anywhere. Renovate's lookup
cache lives there too, so it does not survive between runs and every
pass repeats its datasource lookups against GitHub and the registries.
If that ever shows up as rate limiting, an `actions/cache` of the
cache directory would bring persistence back.

#### Renovate without Docker

The runner does not use `renovatebot/github-action`. That action runs
Renovate as the `ghcr.io/renovatebot/renovate` container, so it needs
a Docker daemon on the runner, which the VM does not have. Instead the
workflow installs Node with `actions/setup-node`, installs a pinned
Renovate with `npm`, and runs it. The rest of this section is what
follows from Renovate leaving its container.

#### Lock files

The container installed tools such as `cargo` on demand to refresh
lock files (Renovate's `binarySource=install`). Outside the container
Renovate uses whatever is on the runner's `PATH`
(`binarySource=global`), so the workflow has to provide each tool a
lock file needs, or Renovate's pull requests carry an "artifact update
problem" instead of a refreshed lock file. For the lock files the
fleet has:

| Lock file | Tool Renovate runs | Provided by |
|-----------|--------------------|-------------|
| `package-lock.json` | `npm` | `actions/setup-node` |
| `Cargo.lock` | `cargo update` | `dtolnay/rust-toolchain`, pinned to a commit |
| `requirements*.txt` | `hashin`, for a file with `--hash` lines | A venv under `runner.temp` |

The Rust and `hashin` steps run on every pass, because the
repositories being managed are not in the workspace to be looked at:
Renovate clones them later, so `hashFiles()` has nothing to test. Both
are `continue-on-error`, since they run for the whole fleet: a rustup
or PyPI outage should cost the repositories that need the tool an
artifact update problem, not cost every repository its pass.

The Rust step installs stable with rustup's minimal profile (`rustc`,
`cargo` and the standard library, no clippy, rustfmt or docs) and
makes it rustup's default. It sets no override, so a repository with a
`rust-toolchain.toml` still gets the toolchain it names: rustup reads
that file from the directory Renovate runs `cargo` in, and installs
the toolchain on first use. The action is pinned to a commit rather
than to `@stable`, which is a branch, so the code running in the job
that holds the token is not whatever that ref points at on the day.
The step gives `toolchain: stable` explicitly, because the action
takes its input's default from the ref name, and a bare sha would
otherwise work only by virtue of which branch it happens to sit on.

A requirements file without `--hash` lines needs no tool: Renovate
edits the pins as text. One with them needs `hashin`, which Renovate
runs to replace the old version's hashes with every hash PyPI
publishes for the new one. andris's `.devcontainer/wheel/requirements.txt`
was the first such file in the fleet, and its first bump
(shakenfist/andris#69) carried the new pin with the old hashes and an
artifact update problem whose stderr was empty: Renovate runs its
tools without a shell, so a missing `hashin` fails to spawn rather
than printing "not found". Only the `hashin` script is put on `PATH`,
through a directory of its own, so the venv's `python3` and `pip` do
not shadow the runner's for the other tools Renovate runs.

`hashin` is pinned with a `# renovate:` comment like Renovate itself,
so it is bumped the same way. Its install step holds no secret, and
Renovate passes its tools only an allowlist of environment variables
(`PATH`, `HOME`, proxy and certificate settings), not
`RENOVATE_TOKEN`, so `hashin` and its dependencies never see the
token. Those dependencies (`packaging` and `pip`) are resolved within
their declared ranges on each run, as Renovate's npm tree is.

A repository that adds another kind of lock file -- `uv.lock`,
`poetry.lock`, `go.sum`, `pnpm-lock.yaml` and so on -- needs a step of
the same shape in the runner's workflow: unconditional, and
`continue-on-error`.

#### The version pin

The Renovate version is pinned exactly, in an environment variable
of the "Install Renovate" step, with a comment Renovate reads:

```yaml
# renovate: datasource=npm depName=renovate
PINNED_RENOVATE_VERSION: 44.257.5
```

`customManagers:githubActionsVersions` is a preset built into
Renovate, and the fleet preset extends it. It matches that comment
above any `*_VERSION` key in `.github/workflows`, so Renovate bumps
its own pin like any other dependency. Without the preset nothing
reads the comment and the pin ages silently. The variable is not named
`RENOVATE_*`, because Renovate reads those as its own configuration.

An exact pin trades a floating major tag, which picked up every
release unasked, for a pull request. It pins Renovate itself, not its
dependency tree: with no lockfile, `npm` resolves Renovate's
dependencies within their declared ranges on each fresh install.

`node-version` follows Renovate's `engines.node` (`^24.11.0` for
44.x), and the fleet preset keeps the two in step with two
`packageRules` entries.

- **Renovate majors carry a note.** A Renovate major that raises
  `engines.node` needs `node-version` raised in the same pull request,
  but the bump itself only touches the pin, so the rule's
  `prBodyNotes` puts that check in front of the reviewer.
- **Node majors for the runner are never proposed.** Renovate's
  github-actions manager proposes Node majors for `setup-node`, and
  one taken before the pinned Renovate's `engines.node` accepts it
  fails every run with "Unsupported node environment detected". In
  October 2026 a Node 26 bump did that to nine repositories at once.
  The rule disables Node majors in
  `.github/workflows/renovate-fleet.yml` alone, a file only this
  repository has; Node in any other workflow is still proposed as
  usual.

#### Installing without the token

`RENOVATE_TOKEN` can write to every repository Renovate manages, so
the workflow keeps it out of the install. Two steps hold it: the push
probe, which runs a standard-library Python script before anything is
installed, and the Renovate run itself. The "Install Renovate" step
holds no secret: it runs `npm install --ignore-scripts` into a
directory under `runner.temp`, so no package in the freshly resolved
tree runs an install script at all, and then `npm rebuild re2`. `re2`
is the one native dependency Renovate uses. It is Renovate's RE2
regular expression engine, and without its build Renovate falls back
to `RegExp` and warns on every run. The rebuild runs only that
package's scripts, still without the token. The "Self-hosted
Renovate" step holds the token and runs the installed binary, so it
fetches nothing from the registry.

This was once `npx --yes renovate@<pin>` in one step that held the
token (shakenfist/development#322). An install script anywhere in the
tree, from a dependency Renovate pulls in within its declared ranges,
would have run with write access to the fleet on the next hourly run.

The split closes install time, not run time. Code from the tree that
Renovate imports still runs in the step that holds the token, so a
compromised dependency release can still reach it that way until the
tree is fixed by a lockfile. Nor does a step-level split protect
against anything earlier in the same job, which can write
`GITHUB_PATH` or `GITHUB_ENV` or leave a shim in the workspace for the
token-holding step to run; the pinned actions and the ephemeral VM
are what bound that. `minimumReleaseAge` does not help here either: it
governs the pull requests Renovate opens, not what `npm` installs.

### The fleet preset

The policy every repository shares lives once, in `renovate-fleet.json`
at the root of this repository, and each repository's `renovate.json`
extends it with `"extends": ["local>shakenfist/development:renovate-fleet"]`.
A policy copied into every repository only reaches the ones that are
re-synced, so a schedule or a rule changed after a repository was
onboarded stayed behind; extended, a change reaches the fleet once.

The preset holds the author and assignee Renovate uses, the dependency
dashboard, rollback pull requests, a three day `minimumReleaseAge`,
the `pre-commit` manager, `customManagers:githubActionsVersions`,
`helpers:pinGitHubActionDigests`, the `Australia/Sydney` timezone, and
the cadence: ordinary updates `on saturday`, majors and the CI tooling
group `on the first day of the month`, with at most 2 pull requests an
hour and 10 open at once (Renovate's own defaults, written down rather
than inherited). Minor, patch and digest updates are never merged
automatically. The CI tooling group puts GitHub Actions and pre-commit
hook revisions, every update type, digests included, into one monthly
pull request. The two rules that keep the runner's `node-version` and
its pinned Renovate in step, and the `shellcheck-py/shellcheck-py`
versioning rule, are in it too.

`helpers:pinGitHubActionDigests` makes Renovate rewrite a third-party
`uses:` reference to a full commit sha with a trailing comment naming
the ref it replaced, and then keep that sha current. A tag is a
pointer its owner can move, so a workflow that trusts one runs
whatever the tag points at on the morning it runs; a sha is the only
reference that cannot change underneath. The CI tooling group matches
by manager and sets no `matchUpdateTypes`, so the pins are expected to
arrive inside it, costing one monthly pull request rather than one per
release; the first pin pull request is what confirms that a pin obeys
that schedule. Pinning reaches whatever a repository's
`github-actions` manager scans, which in this repository means
`templates/` as well as `.github/`, so a template and its deployed
twin are pinned in the same pull request.

The fleet's own actions are the exception, carried as a `packageRules`
entry. `shakenfist/**` references are not pinned: every repository
consumes them at `@main` on purpose, so that a fix to a shared
composite action or reusable workflow reaches the fleet without a pull
request in each repository, and that branch is protected by a ruleset
requiring review instead of by a sha. The exception names both the
`action` and `workflow` dep types, because Renovate calls a job-level
reusable workflow a `workflow` while every other `uses:` reference is
an `action`. Both names, and the helper's own definition, are read
from Renovate's source at 44.227.4, and re-read unchanged at
44.251.0:
the helper is the `pinGitHubActionDigests` entry in
[`lib/config/presets/internal/helpers.preset.ts`](https://github.com/renovatebot/renovate/blob/83794a8993ff455b106ddf1e23ce4deca3f5277d/lib/config/presets/internal/helpers.preset.ts#L118-L126),
and the dep type a reference is given is chosen by
`extractRepositoryAction` in
[`lib/modules/manager/github-actions/extract.ts`](https://github.com/renovatebot/renovate/blob/83794a8993ff455b106ddf1e23ce4deca3f5277d/lib/modules/manager/github-actions/extract.ts#L78-L111),
which says `workflow` when the reference's sub-path matches
`reusableWorkflowPathRe` and `action` otherwise. Both links are pinned
to the commit 44.227.4 was tagged from.

The preset sets no `vulnerabilityAlerts`: Renovate's defaults
already drop the schedule, `minimumReleaseAge` and grouping for a
security fix, so a fix for a vulnerability Dependabot reports arrives
at once rather than waiting for Saturday. One rule is an exception to
that. A `packageRules` entry with `enabled: false` suppresses even a
vulnerability fix, and
the preset's Node-major disable rule is one, so Node majors in the
runner's workflow are the one update type the immediate security path
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

The criterion trusts the preset by name: it requires the `extends`
string and does not look at what the preset carries. That holds only
while the preset keeps its promises, so the tests load the real
`renovate-fleet.json` and assert that it enables the `pre-commit`
manager, extends the preset that reads version comments, and carries
the `shellcheck-py/shellcheck-py` versioning rule. Each assertion uses
a predicate that reads the parsed configuration and knows nothing
about the preset's name, so the preset passes on its contents rather
than on a string. A commit that removed one of the three from the
preset would otherwise leave every repository in the fleet passing on
a promise nothing kept.

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
cargo, dockerfile and github-actions dependencies were current. The
fleet preset enables the manager, so every repository that extends it
has it.

Enabling the manager is not enough for the shellcheck hook, which
needs a `versioning` rule before Renovate can order its tags. The
preset carries that rule too, and the rule is inert in a repository
that does not use the hook. See
[the template README](https://github.com/shakenfist/development/blob/main/templates/renovate/README.md#shellcheck-pys-four-part-tags)
for why the rule is needed.

`images` and `private-ci` are scoped out of this criterion by their
`only_checks` entries, so no finding reports whether they extend the
preset, and a hook pin that ages there goes unreported. Widening
either entry is a decision about that exemption rather than about
this check.

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
its file patterns cannot be extended from outside the preset. No
template under `templates/` carries a `# renovate:` version comment
today, so `development` needs nothing for it. A template that gains
one needs a local `customManagers` entry with the preset's match
string and `templates/` as its file pattern, or its pin is never
bumped.

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
