# Audit: Fuzz nightly reporting

## What we check

Fuzzing is a discovery activity with no natural end. A fuzz target
does not pass or fail in the way a unit test does — it runs until you
stop it, and what it produces is a crash nobody knew about. That makes
it valuable, and it makes it the wrong shape for a merge gate.

Three things follow, and a repository with fuzz targets is checked for
all of them.

"Fuzz targets" here means cargo-fuzz: a `fuzz_targets/` directory
anywhere in the repository, or a cargo-fuzz subcommand or a
`make *fuzz*` target in a workflow. Nothing else is detected yet, so a
project fuzzing with atheris, Go's native `FuzzXxx`, or libFuzzer
directly reads `not_applicable` with "No fuzz targets" — which means
the audit cannot see its fuzzing, not that it has none. If that is
your repository, say so and the detection can grow.

The search for `fuzz_targets/` does not descend into build output,
vendored trees or virtualenvs — `target/`, `build/`, `dist/`,
`node_modules/`, `vendor/`, `third_party/`, `.tox/`, `.venv/`,
`venv/`. A cargo-fuzz corpus lands under `target/`, and a
`fuzz_targets/` inside any of them belongs to a dependency rather
than to the repository being audited.

A repository whose workflows already run the targets is never
searched: the workflow read is cached and the walk is not, so the
cheap question is asked first, and the only finding that has to name
where the targets are is the one where no workflow runs them.

**The fuzz targets must run on a schedule.** A `schedule:` trigger on
a workflow that invokes the targets. Fuzzing that only ever happens
when someone remembers to dispatch it is fuzzing that does not happen;
a nightly run is what turns the targets from a directory of code into
a thing that finds bugs. The trigger may sit on a caller instead: a
`workflow_call` workflow that runs the targets counts as scheduled
when some workflow with a `schedule:` trigger `uses:` it. Both
triggers are read in block form and in flow form (`on:
[workflow_call]`), and both may carry a trailing comment — a trigger
line that is explained is still a trigger.

The caller has to name this repository's own copy of the callee:
`uses: ./.github/workflows/fuzz-run.yml`, or the full
`owner/repo/.github/workflows/fuzz-run.yml@ref` form where the owner
and repository are this one. A caller that schedules a *different*
project's workflow that happens to share the file name has not
scheduled anything here.

**The scheduled run must report what it finds as GitHub issues.** This
is the requirement that is easiest to skip and most expensive to skip.
A failing pull request check is impossible to miss — it is standing
between someone and their merge. A failing scheduled workflow is a red
mark on the Actions tab that nobody is looking at, and GitHub's only
notification for it is an email to whoever pushed last, which at 04:00
UTC is nobody's inbox in particular. So a scheduled fuzz lane has to
carry its own route to a human: `issues: write`, and something that
files an issue when a target crashes.

Every scheduled lane that runs the targets is held to this, not just
one of them per repository. A second campaign that crashes where
nobody hears it is the failure this criterion exists to prevent, and
nothing in a workflow file separates that from a corpus-minimisation
lane with nothing to report — so the strict reading is the one that
holds, and a lane that genuinely has nothing to say either files
nothing because it finds nothing, or carries the permission and the
call it never reaches.

What that last one is measured by is a call to `gh issue create`, a
`gh api` call against an issues endpoint, an `issues.create` through
`actions/github-script` or Octokit, or the
`peter-evans/create-issue-from-file` action — in the workflow, or in a
`.sh` or `.py` script under the repository that the workflow names,
which is followed one level. Where the nightly is split across a
caller and a `workflow_call` callee, either side may hold the
permission and either may make the call — the callee fuzzing and
uploading while the caller inspects and files is as good a split as
the reverse.

`gh issue create` is recognised however a script spells the argv —
`['gh', 'issue', 'create', ...]` through `subprocess` is the same
call — and a Python client's own `create_issue` counts, because a
reporter in a script is the shape this criterion recommends and
recognising only the shell spelling would fail a repository that took
the advice.

Comments do not count on either side, and that includes a comment at
the end of a line of code: `- run: echo done  # TODO: gh issue
create` describes reporting rather than doing it. The same rule runs
in the other direction when deciding whether a repository fuzzes at
all — `- run: make build  # replaces the old make fuzz-all target` is
not an invocation, and reading it as one would pull a project that
has never fuzzed anything into scope and then fail it. A `#` only
opens a comment where it follows whitespace and sits outside quotes,
so `${#crashes[@]}` and `--title "crash # 3"` survive.
The permission is looked for anywhere in the workflow, which does not
model GitHub replacing a workflow-level `permissions:` block wholesale
when a job declares its own — a fuzz job that narrows its own
permissions can pass this and still fail at runtime.

**The fuzz lane must not gate the merge queue.** No `merge_group`
trigger on a workflow that runs fuzz targets. A short build-and-smoke
on `pull_request` or on `push` to the default branch is good practice
and is not what this forbids — catching a fuzz target that stopped
compiling is worth ten seconds of a PR. What it forbids is putting the
fuzz lane in the merge queue's path, where its cost is charged against
the queue's timeout.

Building the targets rather than running them does not exempt a lane
from that. What the queue pays for is runners held while its clock
runs, and the lane that evicted ryll's pull requests three times was
`make fuzz-build-*` and `make fuzz-smoke-*` — its own comment calls it
"a build-and-doesn't-panic gate, not a real fuzz campaign". The cost
was four self-hosted runners, not the fuzzing.

A repository whose merge queue needs a fuzz status check to *report*
does not need the fuzz job itself in the queue. The queue requires the
named check to report on the `merge_group` event, which the fleet's
aggregate gate job does — ryll's `Can merge` runs on `merge_group` and
treats a skipped dependency as success — leaving the expensive job on
`pull_request` where it is not on the queue's clock.

## Why the merge queue in particular

Merge queues time out. GitHub's `check_response_timeout_minutes` caps
at 360, it is a wall clock from when the merge group forms, and it does
not distinguish a job that is running slowly from one that has not been
given a runner yet. A fuzz matrix asking a shared self-hosted pool for
several runners at the moment a merge group forms is therefore a bet
that the pool is free, settled against a six-hour deadline, with the
PR's place in the queue as the stake.

ryll lost that bet three times in eight days
([shakenfist/ryll#329](https://github.com/shakenfist/ryll/issues/329)).
Its four `Fuzz (*)` jobs were `merge_group`-only, so they asked the
six-worker `l` pool for four runners that its pull request CI never
requested. On the third occurrence the jobs waited six hours, got
runners, and passed — twenty minutes after the queue had already
evicted the PR, leaving a merge group whose run was entirely green and
a pull request that had silently failed to merge. The failure mode is
not "fuzzing found a bug"; it is "fuzzing was queued behind someone
else's build".

## The reference implementation

instar is the worked example, in
[`.github/workflows/coverage-fuzz.yml`](https://github.com/shakenfist/instar/blob/develop/.github/workflows/coverage-fuzz.yml)
and
[`tools/ci/report-fuzz-crash.sh`](https://github.com/shakenfist/instar/blob/develop/tools/ci/report-fuzz-crash.sh).
It fuzzes forty targets nightly against a tiered time budget, a single
target for ten seconds on pull requests, and every target for fifteen
seconds after a merge. It has no `merge_group` trigger.

Four things in it are worth copying and are not obvious:

- **Deduplicate before filing.** A crash that recurs every night must
  become one issue with comments on it, not one issue per night. instar
  keys on the target plus a normalized panic location and message, with
  the numbers in the message collapsed — a panic that interpolates the
  fuzz-derived values that provoked it produces a different string
  every night for one bug.
- **A failure to report must not end the run.** The remaining targets
  still have to be fuzzed. Count the reporting failures and fail the
  job at the end instead.
- **Decide deliberately what turns the run red.** For a campaign that
  reports crashes, red on a crash that was successfully filed is wrong:
  the issue has already reached a human, and going red as well leaves
  the nightly permanently red for as long as any known crash is open —
  which makes it a thing nobody reads, the exact failure this criterion
  exists to prevent, reintroduced one step further along. instar
  therefore fails only on crashes it could not report. A
  build-and-smoke gate is the other case: its failures are "this stopped
  compiling", they get fixed rather than accumulating, so red on the
  failure itself is useful and ryll's `fuzz.yml` does that. What is not
  optional either way is that a failure nobody could file an issue about
  fails the run.
- **Persist the work before failing.** The corpus push and the artifact
  upload run before the step that fails the job. When issue filing is
  the thing that broke, the uploaded artifacts are how the crash
  reaches a human, so they must not be collateral damage.

The reporting logic belongs in a script under `tools/`, not inline in
the workflow YAML. instar's is there because the inline version broke
the nightlies silently for a month: a large crash input made a
`jq --arg` invocation exceed `MAX_ARG_STRLEN`, the step ran under
`bash -e`, and the run aborted at the first crash — no issue filed,
half the targets never fuzzed, corpus push skipped. A script is
testable; `tools/ci/test-report-fuzz-crash.sh` now covers that case.

## Exceptions

A repository whose fuzz lane must run in the merge queue takes an
`audit-ok: fuzz-in-merge-queue` comment in the workflow, ideally with
a reason. Anywhere in the file works; its own comment line above the
trigger is where it belongs, and is where a reader will look for it.

Repositories with no fuzz targets are not applicable.

## Template

No template. The workflow is too shaped by what is being fuzzed to
generalize usefully; copy instar's `coverage-fuzz.yml` and
`tools/ci/report-fuzz-crash.sh` and cut them down. The crash reporter
is the part that generalizes most directly — its input is a target
name, a crash artifact and a log.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#fuzz-nightly-reporting).
