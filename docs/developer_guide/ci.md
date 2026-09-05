# Continuous integration

How Shaken Fist's CI is put together: the workflows, the merge queue,
the automated jobs that open their own pull requests, and the bot
commands available on a PR.

## GitHub Actions workflows

Every workflow in `.github/workflows/`:

| Workflow | Purpose | Trigger |
|----------|---------|---------|
| `functional-tests.yml` | Main CI: lint, unit tests, functional tests, credential scanning, and the automated reviewer, delinter and exception fixer jobs. The functional jobs deploy nested test clusters via the `shakenfist.shakenfist` Ansible collection (`shakenfist/deploy/collection/`), driven by the reusable `smoke-cluster` workflow in the `shakenfist/actions` repository | PR, merge_group |
| `docs-tests.yml` | Build and test documentation | PR touching `docs/**` or `mkdocs.yml` |
| `code-formatting.yml` | Whole-tree formatting sweep | Daily schedule, manual, self-test PR |
| `codeql-analysis.yml` | CodeQL static analysis | Push, PR, weekly schedule |
| `pin-indirect-dependencies.yml` | Reconcile pinned indirect dependencies, adding new ones and removing obsolete ones (runs `tools/pin-indirect-dependencies.sh`) | Daily schedule, PR self-test |
| `renovate.yml` | Self-hosted Renovate dependency updates | Hourly schedule, manual |
| `export-repo-config.yml` | Export GitHub repo settings to version control, via a shared reusable workflow in the `actions/` repository | Daily schedule |
| `pr-re-review.yml` | Re-review PR on bot command | `@shakenfist-bot please re-review` |
| `pr-address-comments.yml` | Address review comments on bot command | `@shakenfist-bot please address comments` |
| `pr-fix-tests.yml` | Fix test failures on bot command | `@shakenfist-bot please attempt to fix` |
| `test-drift-fix.yml` | Unit test fixer (called by `pr-fix-tests.yml`) | workflow_call, workflow_dispatch |
| `issue-fix.yml` | Triage open issues, propose a fix as a draft PR | workflow_dispatch |
| `merge-failure-triage.yml` | Triage a failed merge queue run: PR-caused or systemic, record the occurrence, post a verdict | workflow_run (a failed `Functional tests` merge group run), workflow_dispatch |
| `scheduled-tests.yml` | Longer-running test sweep (schedule currently disabled) | workflow_dispatch |
| `publish-website.yml` | Publish the mkdocs site | Push to `develop`, manual |
| `refresh-website.yml` | Trigger a GitHub Pages rebuild | Daily schedule, manual |
| `sync-external-docs.yml` | Import the sibling repositories' documentation into `docs/components/` | Hourly schedule, manual |
| `release.yml` | Build and publish a release | Tag push, manual |

## Where the functional jobs get their code

The functional jobs do not install released packages. `shakenfist/actions`'s
`setup-test-environment` checks out three repositories --
`shakenfist/shakenfist`, `shakenfist/client-python` and
`shakenfist/agent-python` -- with the repository that triggered the workflow
at its triggering ref and **the others at `develop`**. Its
`tools/deploy-collection.sh` then runs the collection's example playbook with
`sf_build_local_wheels=true`, `repo_path` and `client_repo_path` pointing at
those checkouts. Play 0 of `examples/_shared/site.yml` builds a server wheel
and a client wheel, play 1 copies them to every node, and the node role
installs the wheel paths in place of the PyPI names it would otherwise use
(`shakenfist/deploy/collection/roles/node/tasks/bootstrap.yml`).

The consequence is worth stating plainly, because it was missed for two
months: **an unreleased client change is available to the functional jobs as
soon as it merges to `client-python`'s `develop`.** No PyPI release is
involved. A test in this repository may be written against a new `apiclient`
verb the moment that verb merges next door.

Two things this does not mean. An operator deploying the collection normally
gets the PyPI packages, since `sf_build_local_wheels` defaults to false -- so
an unreleased verb reaches CI, and no operator. And the coupling runs both
ways: a regression merged to `client-python`'s `develop` breaks this
repository's functional jobs with no change here.

The private CI conductor is on the same footing, not the opposite one. Its
deployment playbook pip-installs `client-python@develop` into the conductor
virtualenv with `state: latest`, overriding the unpinned `shakenfist-client`
its `requirements.txt` resolves from PyPI, and has since 2026-07-12 -- for
the same reason: `develop` moves API contracts faster than releases do, and
a released client that was behind a server contract once wedged the
conductor's main loop overnight. So the conductor picks up a client change
on its next deploy, and a release is not what gets a verb to it.

That leaves a released client mattering to exactly one audience -- operators
-- which is worth knowing before treating a release as a prerequisite for
anything internal.

Phase 4 of `docs/plans/PLAN-scheduler-reservations.md` reasoned from the
opposite belief and deliberately wrote its functional coverage against
`apiclient.Client._request_url()` to work around a constraint which had
already been gone for seven weeks. Its phase 4b then made the same mistake
about the conductor, in the same document that corrected the first one --
because it checked this repository's install path and took the conductor's
from another plan. If you find yourself about to do something similar, check
this section first, and check the install path itself rather than a
description of it.

## Running commands from the functional suite

`shakenfist_ci/process.py` is how the functional suite shells out. It is
`subprocess.run()` with an acceptable-exit-code list, and it replaced
`oslo_concurrency.processutils.execute()` -- which the harness was importing
without declaring, and which nothing else in this repository used.

Three things about it are load-bearing, and are the reason it is a module
rather than ten inline `subprocess` calls:

* **Output is returned unstripped, and decoded with `os.fsdecode`.** Callers
  split on newlines and parse JSON, so a trailing newline is data. `ip`, `ssh`
  and the client run against real hosts and occasionally emit a byte that is
  not UTF-8; `fsdecode` maps it to a surrogate instead of losing the test to a
  `UnicodeDecodeError`.
* **stdin is `/dev/null`.** `ssh` reads stdin, and a command that inherited
  the test runner's would block rather than fail.
* **`ProcessTimeoutError` subclasses `ProcessExecutionError`.** A node that
  accepts an ssh connection and then wedges is exactly as untestable as one
  that refuses it, so `_require_node_exec()` skips on both without being
  taught the difference.

### Credentials go through the environment, not argv

`_exec()` in the command-line tests writes every command it runs to stderr,
and the harness prints it again on failure. So the namespace key reaches
`sf-client` as `SHAKENFIST_KEY` in the child's environment -- along with
`SHAKENFIST_NAMESPACE` and `SHAKENFIST_API_URL`, which the client has honoured
since v0.2.5 -- and never appears on a command line at all.

`process.mask_secrets()` exists as well, and rewrites the value of
`--key`-shaped flags to `***` before a command or its output goes into an
exception. It is a backstop and not the protection. Name-based redaction is
the technique this repository already abandoned once in the API layer, for the
reason recorded under "Credential-carrying routes are not logged, not
redacted" in `coding_rules.md`: it silently starts leaking the day somebody
adds a name it has not heard of. It cannot match a short option, a positional
secret, or a flag nobody thought of. Keep the credential out of the string.

### Node-exec commands have a timeout

`_node_exec()` passes `SF_CI_NODE_EXEC_TIMEOUT` (seconds, default 300) to
every command it runs, local or over ssh. These are introspection commands --
`ip`, `iptables`, `virsh` -- so one of them taking minutes means the node is
wedged, which is a state a test may well have just put it in. ssh's
`ConnectTimeout=10` bounds the TCP connect only, and does nothing for a session
that connects and then stops responding; without this the job would stall
until the CI runner's own timeout killed it, with no indication of which test
was to blame.

## CI headroom instrumentation

Phase 1 of `docs/plans/PLAN-ci-cloud-sizing.md` (see
`docs/plans/PLAN-ci-cloud-sizing-phase-01-headroom-probe.md` for the
decisions behind it) added two data-gathering instruments to every
functional cluster job, so that later phases can size CI's clouds from
a distribution instead of the handful of hand-collected numbers the
plan started from. Neither instrument gates anything itself -- see
"Nothing here is a quality gate" below -- but the poller's own traffic
does interact with a check that gates, which is the one reason a
reader troubleshooting a CI failure might need this section; see "The
probe's traffic is exempted from the idle-load check". Otherwise it
matters when reading a job's bundle afterwards, or when working on the
sizing plan itself.

### Two instruments, not one

**The headroom series** is a poll. `tools/ci_headroom_probe.py` (in
this repository) runs in the background on the cluster primary for the
whole functional test step, sampling `GET /admin/resources` and the
node roster (`GET /nodes`) every 15 seconds and appending one JSON
record per sample to a JSONL file.

**The refusal census** is a scrape. After the tests finish,
`tools/ci_headroom_collect.sh` (in `shakenfist/actions`) runs a
filtered Loki query for every scheduler admission event the run
produced. `Scheduler._log_and_raise_on_error()` logs one at *every*
stage, whether or not candidates survived, so a green run's refusals
are on the record too, not only a failing one's.

The two are not substitutes for each other. A fifteen-second poll
cannot see a refusal, which begins and ends between samples; a census
cannot see a cloud sitting half empty for an hour, which is the
oversizing case the whole plan exists to catch. Reporting one number
derived from both would hide which of the two produced it, so they
are built, collected and reported separately.

### Where the output lands

Both instruments write into `/srv/ci/traces/` on the cluster primary:
`headroom.jsonl` (the series) and `headroom-census.json` (the raw
Loki `query_range` response the census reads). Neither gets its own
`upload-artifact` step -- the existing `Gather logs` step already
scp's the whole of `/srv/ci/traces/` into the job's 90-day artifact
bundle, so a reader looking at a bundle finds both files sitting
beside the other logs, with no separate download to go and find.

`tools/ci_headroom_report.py` (also in this repository) turns the two
into a printed summary at the end of the job log: p90 and peak
committed vCPU and memory, cluster-wide and per node, both absolute
and as a fraction of the admission ledger, plus the refusal census
broken out by stage.

### The series record format

Phase 2 parses `headroom.jsonl` as a contract, so treat the shape
below as load-bearing rather than as prose to paraphrase; the tool's
own docstring is the source of truth if the two ever disagree. Each
line is one JSON object. A successful sample carries:

* `sampled_at` -- float, unix epoch seconds, wall clock at sample
  start.
* `resources` -- the verbatim `/admin/resources` payload
  (`client.get_cluster_resources()`).
* `nodes` -- the node roster at the time of the sample, reduced to
  `uuid`, `fqdn`, `is_hypervisor`, `is_network_node` and
  `is_database_node` for each entry.

A failed sample carries only `sampled_at` and `error` (the exception
text) -- `resources` and `nodes` are absent, not null. The probe never
raises: a failure just writes an error record and the polling loop
continues, so one bad sample never costs the run the rest of the
series.

The roster is recorded on *every* sample, not once at the start of the
run, and that is deliberate. `summarize_resources()` silently omits
from `per_node` any node that is not a hypervisor, whose metrics are
older than 120 seconds, or whose queue is unreasonably long, and does
not say which of those applied. A node missing from a given sample's
`per_node` therefore has several possible explanations, and only a
roster captured in that same sample can tell "not a hypervisor" apart
from "metrics gone stale" or "the node did not exist yet" -- a roster
fetched once at the top of the run cannot, because cluster membership
itself can change mid-run.

### Four capacity stages, and a naming trap

The scheduler's admission checks that can refuse a candidate node for
being full are `sufficient_idle_cpu`, `sufficient_idle_memory`,
`sufficient_free_disk` and `sufficient_idle_disk`. The last two are
easy to swap: `sufficient_free_disk` is disk *space*, but
`sufficient_idle_disk` is disk *bandwidth* -- a rate predicate on
disk-busy delta, not a capacity check at all, and not something that
more or bigger disks in the same shape would fix. The cloud-sizing
plan itself named the wrong one as "disk" before this phase's survey
caught the mistake, which is why it is worth calling out here: read a
`sufficient_idle_disk` row in a census as a disk I/O problem, never as
evidence the cloud needs more disk capacity.

### Nothing here is a quality gate

Every workflow step this phase added is `continue-on-error`, and
`ci_headroom_report.py` always exits 0 whatever it finds -- even an
internal error in the report is printed, not raised. The band verdict
it prints (committed vCPU as a fraction of the admission ledger,
against bounds of 0.35 and 0.70) is explicitly labelled PROVISIONAL:
phase 0 set those bounds with no distribution to check them against,
phase 2 replaces or defends them, and any enforcement is phase 5's to
add. No verdict this instrumentation prints can fail a job.

That is not the same as the instrumentation being invisible to the
checks that do gate, which is what this section used to say and what
issue 3975 disproved -- see below.

### The probe's traffic is exempted from the idle-load check

The probe polls, and `test_no_unbudgeted_fixed_rate_database_polling`
in the functional suite exists to notice polling. Each sample's `GET
/nodes` hydrates each node from the iterator, which is one `GetNode`
each, then runs `Node.external_view()` per node, which is one
`GetNodeAttributes` and one `GetAllNodeDaemonStates` each, and its
`GET /admin/resources` reads node metrics per node, which is one
`GetNodeMetrics` each. On an N node cluster at the default 15 second
interval that is N/15 per second for all four, from the `api` caller,
flat across windows and indifferent to what the suite is doing --
which is exactly the signature of the server-side polling loop the
check is built to catch. It clears the unbudgeted ceiling, `max(0.25,
0.05N)` per second, from four nodes upwards; it was found on the six
node merge queue cluster, where three of the pairs read 0.3997/s
against 0.30/s and failed the build (issue 3975). The fix written for
that issue exempted only the three RPCs its body named, so the
`GetNode` the iterator issues came back as issue 4028 -- which of the
probe's pairs clears the check's activity-spread bar varies run to
run, and on those runs `GetNode` cleared it alone.

Those four `(operation, caller)` pairs are therefore listed in
`HARNESS_DRIVEN_PAIRS` in
`shakenfist/deploy/shakenfist_ci/load_budget.py`, alongside the events
reads the suite's own await helpers make. The budget file is
deliberately not the home for them: no deployed cluster runs the
probe, so a budget entry would model load that exists nowhere outside
a CI job, and it would have to be a per-node term, raising every real
cluster's ceiling in proportion to its size.

Two consequences worth knowing:

* **The exemption costs coverage.** CI can no longer see a *new*
  fixed-rate poll of node state made through sf-api, whatever its
  rate. The blind spot is CI's alone -- none of the four pairs is
  budgeted for the `api` caller, so the
  `ShakenFistUnbudgetedDatabasePolling` alert, which reads its
  exclusions from the budget file rather than from that set, still
  watches all four at the unbudgeted ceiling on every real cluster.
  The pairs also stay visible in a run's `harness_driven` list rather
  than being dropped from the report.
* **Retiring the probe means trimming the exemption.** It outlives
  its justification otherwise, and no test can catch that on its own:
  the launcher (`ci_headroom_launch.sh`) and the workflow steps live
  in `shakenfist/actions`, so a decommission done there stops the
  probe without touching anything in this repository.
  `test_the_suite_still_probes_cluster_headroom` covers what it can --
  it fails if the probe is deleted here, stops sampling on a timer,
  stops reading one of the two endpoints, or loses the note in its
  docstring that records this obligation -- but a probe that is simply
  never launched again looks identical to a running one from here.

An absent census is not zero refusals. When the Loki query failed or
log shipping was unhealthy, the report says so explicitly rather than
printing "0 refusals", which would look exactly like a run that
refused nothing. Read a bundle's `headroom-census.json` the same way
by hand: missing or empty means "unknown", never "clean".

A *short* census is not zero refusals either. The query carries an
entry limit -- 5000, which is both Loki's default
`max_entries_limit_per_query` and what `ci_headroom_collect.sh` asks
for -- and Loki gives no signal when it cuts a response off at that
limit. A response holding exactly the limit is indistinguishable from
a complete one, and the scheduler emits an event per stage per
schedule, so a run creating a few hundred instances can reach it. The
report is told the limit via `--census-limit` and prints `CENSUS MAY
BE TRUNCATED` when the count reaches it; every figure below that line
is a lower bound.

One trap is worth stating because it has already been fallen into. Do
not add a `|= "Added event"` line filter to the census query. That is
the message `eventlog.add_event_multi` logs under, but pylogrus'
`JsonFormatter` merges the caller's fields over the record last and
one of those fields is `message` -- so the shipped JSON's `message` is
the *event's* message, and `Added event` never appears in the line at
all. Such a filter matches nothing, and the empty census that results
is reported honestly as "no schedule stage events at all", which reads
like an idle cluster rather than a broken query.

## Coverage the functional suite does not have

### Affinity test skips are topology dependent

`cluster_ci_tests/test_scheduler.py`'s `test_affinity` skips rather
than passes on two degeneracies, because in both the assertion could
not have failed and a pass would be a false green. The two carry
different messages, and which one is expected depends on the
topology -- so a permanently skipping run cannot be read as a healthy
one:

- **`only N candidates`** -- the scorer considered fewer than two
  nodes, so affinity was never consulted. Expected on any cluster
  with fewer than three hypervisors, where the test's own
  `len(nodes) < 3` guard usually fires first.
- **`affine node not a candidate`** -- the node the test is affine to
  was ejected by an admission filter (CPU, memory or disk) before
  scoring. This is issue #3565's real mechanism and is not an
  affinity defect. **Expected on `slim-tier` until
  `PLAN-ci-cloud-sizing` lands**, since that topology runs the same
  suite on roughly half the resources and the admission filters bind
  there.

Neither skip is expected on `slim-primary`. A `slim-primary` run
reporting either one is a real signal: the cluster is smaller than it
should be, or the scheduler is ejecting nodes it should not. Treat it
as a failure to investigate rather than a pass.

This expectation is **documentation, not enforcement**, and that is a
deliberate trade rather than an oversight. A skip is green, so nothing
in CI fails if `slim-primary` starts skipping too -- the "does not skip
on a healthy three-node run" check was made once by hand. The obvious
automation, failing rather than skipping when `get_nodes()` returns
three or more, would fail every `slim-tier` run, and `slim-tier` is
expected to skip until `PLAN-ci-cloud-sizing` lands. Once it does, and
neither topology is expected to skip, that guard becomes worth adding:
fail rather than skip on any cluster with three or more hypervisors.

`test_binary_affinity_prefers_the_tagged_node` shares both skips and
reads the same way, since it asserts the binary model's soft half
through the same helper.
`test_unsatisfiable_require_with_tag_is_refused` shares neither: it
asserts a refusal, needs no successful create and no candidate count,
and so is expected to run on every topology including a single-node
one. A skip there is a bug in the test.

### Upgrade data verification

Every functional job deploys a fresh cluster, so nothing in the suite
observes an upgraded one. `cluster_ci_tests/test_upgrades.py` used to
look like coverage for this: it asserted that a namespace called
`upgrade` still held its networks, its instances and their connectivity
after an upgrade, guarded by a `skipTest()` for clusters which have no
such namespace. Nothing has ever built that namespace, and the guard was
in any case written as `'upgrade' not in client.get_namespaces()` --
a name against a list of dicts, which is always true -- so the test
would have skipped even on a cluster which did have one. It was removed
in the change which fixed that comparison across the suite, rather than
left to report as a skip in every run's summary.

Reinstating it means building the `upgrade` namespace and its objects
before the upgrade, in a job which then upgrades in place. Until that
job exists, upgrade-data verification is unimplemented, and the nearest
thing the suite does have is the `Schema ENUM widening` job in
`functional-tests.yml`, which covers exactly one upgrade hazard (ENUM
columns frozen at `CREATE TABLE` time) on a purpose-built database
rather than a deployed cluster.

## Merge Queue Pattern

The CI uses a two-stage merge queue pattern (see [this blog post](https://boinkor.net/2023/11/neat-github-actions-patterns-for-github-merge-queues/)):

1. **`Can enqueue`** - Runs on `pull_request` events, gates entry to merge queue
2. **`Can merge`** - Runs on `merge_group` events, gates the actual merge

**Important**: Only `Can see status` and `Can enqueue` are required status checks
in branch protection. `Can merge` is evaluated by the merge queue itself, not as
a required check.

A failed `Can merge` ejects the pull request from the queue, and
[merge failure triage](#merge-failure-triage) then classifies the failure
automatically.

### Superseded merge groups are cancelled

Every job that can run in the queue and holds a scarce runner carries a
`concurrency:` block whose key is merge-group aware — the four
`functional_matrix_merge_collection` entries and `ansible_modules_collection`
through the shared `smoke-cluster.yml`, and `node_lifecycle_collection`,
`schema_enum_widening` and `automated_delinter` in this repository.

`github.ref` is the obvious key and is right on every other event. On
`merge_group` it is the per-attempt queue branch
`gh-readonly-queue/develop/pr-<N>-<SHA>`, and GitHub mints a fresh SHA every
time it rebuilds the group — which it does on every push to `develop`. Keyed
on that, every rebuild lands in a concurrency group of its own,
`cancel-in-progress` never matches, and superseded merge groups run to
completion. Each one deploys several nested clusters onto the sfcbr
under-cloud that every Shaken Fist repository shares, so the cost lands on
the neighbours as well as here: `shakenfist/kerbside#284` records a kerbside
merge group failing to place a 12 vCPU instance while two superseded
shakenfist merge groups held the capacity. So the key branches on the event
and uses `github.event.merge_group.base_ref` in the queue, with a
`merge_group-` prefix so a queue run does not share a group with a
`workflow_dispatch` run on `develop`.

Cancelling is only safe because the queue is serial: the develop ruleset sets
`max_entries_to_build: 1`, so any other in-flight `merge_group` run is by
definition superseded and its queue branch already abandoned by GitHub.
Raising that setting while keeping this key would cancel a live queue entry,
which reports a failed required check and ejects the pull request — the two
have to move together. This is audited fleet-wide; see
[merge-group-cancellation](https://github.com/shakenfist/development/blob/main/audits/merge-group-cancellation.md).

## Exported Repository Configuration

Repository settings (rulesets, branch protection, merge queue config) are
exported to `.github/exported-config/` for version control and audit purposes:

- `repository-settings.json` - Repo-level settings
- `rulesets-summary.json` - List of all rulesets
- `ruleset-*.json` - Full details for each ruleset

If the `export-repo-config` workflow creates a PR, it means GitHub UI settings
have changed and should be reviewed.

## Credential scanning

The `credential_scan` job in `functional-tests.yml` runs
`tools/gitleaks-scan.sh`, which scans every commit reachable from `HEAD`
for leaked credentials. On a pull request that is the whole of `develop`
plus the branch under test: about three seconds over five and a half
thousand commits. It is one of `Can enqueue`'s dependencies, so a
credential cannot be merged, and unlike most jobs it is not skipped for
documentation-only changes -- a credential pasted into a code sample is
still a credential, and the one real key secret this scan found in our
own history had been published in the user guide.

The scan is scoped to `HEAD` rather than every ref because `gh-pages`
carries the built documentation site, whose search index is a single
enormous JSON blob quoting every code sample we have. Scanning it takes
five minutes instead of three seconds, produces around a hundred and
fifty findings which are all duplicates of source files already
scanned, and -- in gitleaks 8.16 -- attributes them to unrelated
`develop` merge commits, so they cannot even be triaged by commit.

The scan carries a positive control: it plants a key secret and an SSH
private key in a scratch directory and fails if gitleaks does not report
both. An empty result is otherwise indistinguishable from a broken
scanner, and the allowlists described below could in principle grow
until they forgive everything.

To reproduce a CI failure, run `tools/gitleaks-scan.sh` yourself, passing
`--gitleaks PATH` if the available binary is not the pinned 8.16.0. It
does not matter which directory you run it from: it changes to the top of
the working tree first, because both `.gitleaks.toml` and
`.gitleaksignore` are resolved relative to the working directory, and
from a subdirectory the ignore file would be missed silently and the
three accepted historical findings reported as new. It does need a full
clone -- a shallow one cannot see the history the scan claims to cover,
so the script says so and exits rather than passing over a fraction of
it.

Two rules are ours rather than upstream's:

* `shakenfist-key-secret` matches the `sfk_` credential format. Unit
  tests in `shakenfist/tests/test_credentials.py` read the rule's regex
  out of `.gitleaks.toml` and assert it matches what
  `credentials.generate()` actually produces, so the format and the
  scanner cannot drift apart silently.
* `shakenfist/tests/test_no_committed_credentials.py` walks the working
  tree for the same format but *verifies the checksum*, which
  distinguishes a real credential from a documented example. It runs in
  the unit suite and needs no allowlist at all.

### Accepting a finding

There are two places to record a finding you have decided to accept, and
they are not interchangeable.

Content which will recur -- a documentation placeholder, a test fixture,
an upstream default -- goes in the `[allowlist]` `regexes` list in
`.gitleaks.toml`, keyed on the text itself. Editing the paragraph around
a placeholder creates a new finding in a new commit, so anything keyed
on a commit would need replacing every time. Do not use `paths` for
this: blinding a whole file also blinds a real credential added to it
later. Note that 8.16 matches these regexes against the whole match
rather than the secret alone, so anchoring one with `^...$` quietly
stops it matching.

A specific historical event goes in `.gitleaksignore` as a
`commit:path:rule-id:line` fingerprint, which forgives that one
occurrence and nothing else -- the same secret in a new commit fails the
scan again. History cannot be rewritten to make such an entry
unnecessary: this repository is public, so anything committed here has
been world-readable since the day it landed. An entry therefore asserts
that the credential has been dealt with *where it was trusted*, not that
it has been tidied out of sight. Write down which credential, and what
was done. A unit test enforces that every entry is well formed and
carries a comment.

## Automated CI Jobs

`functional-tests.yml` carries three jobs which act on the pull request
themselves rather than only reporting on it. A fourth piece of automation,
merge failure triage, is a separate workflow for the reasons given below.

### Automated Delinter

When flake8 fails, the `automated_delinter` job runs Claude Code to fix lint
errors automatically. It skips if the last commit was from the bot to prevent
loops.

### Automated Exception Fixer

When functional tests detect exceptions in logs, the `automated_exception_fixer`
job downloads the test bundles and runs Claude Code to analyze and fix the
issues.

### Automated Reviewer

After successful tests, the `automated_reviewer` job calls the shared
`shakenfist/actions/.github/workflows/pr-auto-review.yml@main` reusable
workflow, which reviews the PR with the `review-pr-with-claude` action.
All the gating other than "CI passed" lives in that shared workflow: the
runner, the 60 minute timeout, the pull-request-event and
same-repository restrictions, its own concurrency group, and the
bot-commit check which keeps a bot push from triggering a review which
triggers another bot push. What this repository supplies is the `needs:`
list naming the test jobs and the token `permissions`, which a
cross-repository reusable workflow cannot grant itself.

The `@shakenfist-bot please re-review` command in `pr-re-review.yml`
still uses the `shakenfist/actions/review-pr-with-claude@main` action
directly, because it deliberately passes `force` to review a PR the bot
has already reviewed.

The reviewer produces structured JSON reviews, creates GitHub issues for
actionable items, and embeds the JSON in the PR comment for automation.

### Merge failure triage

When `Functional tests` fails on a `merge_group` event, GitHub ejects the pull
request from the merge queue and somebody has to decide whether the pull
request broke something or whether the test cloud fell over again.
`merge-failure-triage.yml` does that first pass automatically, following the
same steps a maintainer follows by hand: find the first failing step (later
failures are usually cascade), classify the failure as caused by the pull
request or systemic, search for an existing tracking issue and record the
occurrence on it — or file one — and recommend re-queueing or fixing first. The
verdict is posted as a comment on the ejected pull request.

Merge groups here launch several nested test clusters at once, so merge CI is
far more sensitive to under-cloud capacity than pull request CI is, and
historically most merge failures have been environmental rather than code
regressions. That prior is in the prompt, which is why the evidence rules below
matter: a model given no evidence and that prior will happily conclude
"systemic, re-queue".

Most of the work is in `tools/merge-ci-triage.sh`, which gathers the evidence,
runs the model, and publishes the result; `tools/merge-triage.py` parses,
validates and renders the verdict.

#### Why it is a separate workflow

The obvious implementation is another job in `functional-tests.yml`, gated on
`can_merge` having failed. It races the queue: once `Can merge` reports its
failure the queue ejects the pull request and tears down the
`gh-readonly-queue` ref the run is on, and a triage job which takes minutes
sits in exactly that window. Moving the triage into an earlier *step* of
`can_merge` avoids the race but holds the merge queue open while a model reads
logs. A job reachable on `merge_group` would also fall under the
[merge-group-cancellation](https://github.com/shakenfist/development/blob/main/audits/merge-group-cancellation.md)
audit, whose correct behaviour — be cancelled when a newer merge group
supersedes you — is the opposite of what a triage job wants.

The workflow's `permissions:` block names `actions: read`, which is not
optional: naming any scope at all sets every unnamed scope to `none`, and every
piece of evidence the triage gathers is an Actions API read.

`workflow_run` runs after the merge group run has finished, on the default
branch, in the base repository context, and none of that can cancel it. The
trigger carries a `branches: ['gh-readonly-queue/**']` filter so the workflow is
only invoked for merge group runs; without it every pull request run of the
test suite would create a skipped run here. `workflow_run` has no conclusion
filter, so the "did it actually fail?" half of the gate is the job's `if:`.

The workflow never checks out or runs the code it is triaging. It reads the
failed run through `gh` and checks out the default branch, which is what makes
a writable token safe in a workflow that reads a pull request diff.

#### The verdict document

Each triage produces one JSON document matching
`tools/merge-triage-schema.json`. It is published twice: as the
`merge-triage-<run id>` build artifact, and embedded in a collapsed `<details>`
section of the pull request comment, the same way the automated reviewer embeds
its review. The private-ci conductor consumes these to track which merge
failures have been triaged and which of them blamed the pull request, so the
fields that matter to a consumer are:

| Field | Meaning |
|-------|---------|
| `verdict` | `pr_caused`, `systemic`, `ambiguous`, or `unknown` |
| `recommendation` | `requeue`, `fix_first`, or `investigate` |
| `failure_signature` | Short stable string for grouping recurrences |
| `tracking_issue` | Issue recording a systemic failure, or null |
| `tracking_issue_action` | `commented`, `created`, or `none` |
| `run_id`, `pull_request` | What was triaged |

Four properties of that document are worth knowing before consuming it:

- **A document is always written.** A model that answers in prose, a run that
  cannot be read, a document that fails its own schema — each yields
  `verdict: unknown` with an `error` explaining why, never a missing file. The
  guarantee is kept by an `EXIT` trap over an envelope written before anything
  can fail, so it holds for the paths that fall over early too. A missing
  document is indistinguishable from a triage that never ran; an `unknown` one
  is not. The single exception is a run that was not a failed merge group run
  at all: nothing was triaged, so there is nothing to publish a verdict about.
  A document that failed the schema is uploaded alongside the replacement as
  `triage.invalid.json`, since that is a bug in the tooling rather than a
  triage outcome and the discarded document is the only evidence of it.
- **No evidence means no verdict.** If neither the failed job list nor the
  failed step logs can be read, no model is run: the document says so and the
  verdict is `unknown`. Anything else that could not be gathered — the sibling
  runs, the pull request diff — is named in `evidence`, so a verdict always
  carries its own caveats rather than reading as though the model saw
  everything.
- **The envelope is not model output.** The repository, run id and pull request
  number are written from what GitHub said and overwrite whatever the model put
  in those fields, so a verdict cannot be filed against the wrong failure. Only
  the fields in `MODEL_FIELDS` are taken from the response at all.
- **A cited tracking issue has been checked.** A consumer can treat a non-null
  `tracking_issue` as evidence the occurrence really was recorded — but only
  when `tracking_issue_action` is `commented` or `created`, and those two are
  the claims that get verified. The issue has to exist and the issue or one of
  its comments has to carry the failed run's URL; a claim that does not check
  out is downgraded to an action of `none`, keeping the number as a reference
  and losing only the assertion, with the reason appended to `evidence`. An
  issue that cannot be read at all is dropped outright, number and all. A
  citation whose action is already `none` is not checked for the run URL —
  nothing was written to it, so it will not reference this run, and checking
  anyway would drop every reference-only citation ever made. A dry run forces
  `none` unconditionally, whatever the model reports, because nothing was
  written to GitHub on that path; the verification still runs against what the
  model *claimed*, so that path is exercised outside production too.

The comment is only posted if the body could be neutralised —
`tools/neutralise-pr-body.sh` rewrites it in place, and a failure inside it
would otherwise leave the un-neutralised model prose where `gh pr comment`
reads it from. A triage nobody sees is a smaller problem than one that fires an
@mention on publication, and the verdict is still in the artifact.

An `unknown` verdict is posted on the pull request like any other. It is a thin
comment, but the alternative is silence, and silence on an ejected pull request
reads as "triage never ran" — the same ambiguity the always-write-a-document
rule exists to remove, moved from the artifact to the thread. The comment names
why triage reached nothing, which is what tells a maintainer whether to look at
the failed run or at this workflow.

Re-triaging the same run does not double-post: the comment carries an invisible
`<!-- merge-triage run:<id> -->` marker which a later run recognises.

#### What the model can and cannot touch

The model runs with `--dangerously-skip-permissions` and the checkout as its
working directory, so the job stages everything it executes into `runner.temp`
before the model starts: `merge-ci-triage.sh` itself, `merge-triage.py`, its
schema, `neutralise-pr-body.sh` and `claude-model-fallback.sh`. `issue-fix.yml`
stages the same set for the same two reasons. Bash reads a script lazily as it
executes, so an edit to the running driver would corrupt it mid-run; and the
extractor and the neutraliser run *after* the model has exited, so reading them
from the workspace would mean parsing and defusing model output with a copy the
model could have rewritten. With the staging in place a workspace write has no
effect at all — the checkout is discarded with the runner and is never pushed.

The failed step logs are cut to a byte budget before they reach the prompt, and
both ends are kept with the tail given the larger share. Keeping only the head
is the obvious implementation and it is wrong here: a single Ansible
cluster-build step routinely exceeds the whole budget in progress output, and
the message saying what actually broke is the last thing it emits. The elision
is marked inline with the number of bytes dropped.

### Developer Automation (Bot Commands)

Authorized users can trigger automation by commenting on PRs:

- **`@shakenfist-bot please re-review`** - Triggers a fresh automated
  review of the PR using the shared review action.
- **`@shakenfist-bot please address comments`** - Runs Claude Code to
  address actionable items from the automated review. Uses
  `tools/address-comments-with-claude.sh` with dual-checkout security
  (trusted tools from base branch, PR code separately).
- **`@shakenfist-bot please attempt to fix`** - Runs Claude Code to fix
  unit test failures (`tox -ecover`). Uses `test-drift-fix.yml` with
  structured commit summaries.

`issue-fix.yml` is required to check a proposed fix against the plans
in `docs/plans/` before writing any code. Triage skims
`docs/plans/index.md` and deprioritises issues an unlanded plan
already owns; the fix job reads the plan files covering the code it
means to change, follows the pattern established by phases which have
landed, and declines with `NO_FIX` when an outstanding phase is the
proper home for the fix. The plans are read from the checkout at run
time rather than summarised into the workflow, because they change
constantly. This exists because one-off automated fixes had been
landing across partially implemented plans and having to be unpicked
(see the step 3 note in
`docs/plans/PLAN-scheduler-reservations-phase-01-node-metrics-columns.md`).

`issue-fix.yml` asks the model for two marker delimited blocks on
stdout: a commit message and a pull request description. The commit
message is pushed and its first line becomes the pull request title;
the description is published as the pull request body, so an automated
fix arrives explaining its own root cause, what it deliberately did not
do, and any judgement call a reviewer might make differently, rather
than as a diffstat under boilerplate.

Neither block is required. A missing description falls back to the
commit message body, and a missing commit message to a generic note
telling the reviewer to read the diff -- a correct fix is worth
publishing even when the prose is lost. Both cases are logged rather
than fatal, because the run cannot be retried to recover them.

The parsing lives in `tools/extract-model-block.sh` rather than inline
in the workflow, and is covered by
`shakenfist/tests/test_extract_model_block.py`. It takes only the first
complete block, requires both markers (an unterminated block would
otherwise run to the end of the captured output and swallow the block
after it), treats a marker as a terminator only when it is the entire
line, and strips a code fence only when one wraps the whole block --
never globally, because a description may legitimately quote code.

The description is model output, so the workflow assembles the pull
request body into a file and passes it with `gh pr create --body-file`.
It must never be interpolated into a shell string.

It is also run through `tools/neutralise-pr-body.sh` before
publication, which drops the `@` from a mention and separates an
issue-closing keyword from its reference. Both of those are things
GitHub acts on rather than renders: a mention notifies a real person
the instant the draft is created, and a closing keyword closes an
unrelated issue on merge. The prompt forbids both, and a side effect
which fires automatically and cannot be undone should not rest on the
model having complied. Fenced code is passed through untouched --
GitHub does not linkify inside a fence, and a description quoting a
decorator or an email address is a normal description.
`shakenfist/tests/test_neutralise_pr_body.py` covers both halves.

`issue-fix.yml` runs its fix attempt through
`tools/claude-model-fallback.sh`, which takes a comma-separated
preference list (`--models`, default `claude-fable-5,claude-opus-5`) and
moves to the next model when one reports its subscription credit is
exhausted. That case arrives as an HTTP 429 in the `--output-format json`
payload (`api_error_status`), which the claude CLI's own
`--fallback-model` flag does not handle -- it only covers overloaded or
unavailable models. A refused request is free, so the wrapper attempts
the real job rather than paying for a pre-flight probe.

## CI Caching

Workflows that download packages use environment variables to route
traffic through local caches:

- **HTTP proxy**: `http_proxy`/`https_proxy` set to
  `http://192.168.1.15:3128` (Squid cache) for apt, curl, and
  general HTTP downloads.
- **PyPI mirror**: `PIP_INDEX_URL` set to
  `https://devpi.home.stillhq.com/root/pypi/+simple/` (devpi) for
  pip package installs.
- **uv mirror**: `uv` does not read pip's `PIP_*` variables, so
  workflows that resolve with `uv` must also set `UV_INDEX_URL` (and
  `UV_EXTRA_INDEX_URL` if a fallback index is wanted) to the same
  values. Setting only `PIP_INDEX_URL` silently sends the uv resolve
  straight to pypi.

CI VMs provisioned by the `shakenfist/actions` Ansible playbooks also
get system-level config files (`/etc/apt/apt.conf.d/01proxy` and
`/etc/pip.conf`) so that the collection deploy and other tools use the
caches.
- **Proxy bypass**: `no_proxy`/`NO_PROXY` set to
  `localhost,127.0.0.1,10.0.0.0/8` to prevent local service traffic from
  being routed through the proxy.

## Branch Protection

The develop branch uses:
- Required status checks: `Can see status`, `Can enqueue`
- Merge queue with ALLGREEN grouping strategy
- Configuration exported to `.github/exported-config/`
