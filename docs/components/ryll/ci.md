# Continuous integration

Ryll's CI runs in two tiers, and `develop` is behind a merge
queue. The short version: a pull request runs the cheap, fast
checks on our own hardware, and the expensive cross-platform
builds run exactly once, against the commit that is about to
land.

This page describes what runs where, how to read a failure, and
how the pieces fit together. For building and testing locally
see [development.md](/components/ryll/development/).

## Why two tiers

Every ryll change used to run about fifteen jobs, including four
fuzz builds and four cross-platform builds. The common failure
was a cheap job failing — most often `cargo deny` on advisory
drift — while eight heavy jobs ran to completion anyway. Most of
the compute in a failed run was wasted, and the slowest jobs sat
directly on pull request feedback latency.

The two-tier scheme is modelled on
`shakenfist/shakenfist`'s `functional-tests.yml`, which in turn
implements the [merge queue gate pattern described by
boinkor.net](https://boinkor.net/2023/11/neat-github-actions-patterns-for-github-merge-queues/).

## The two tiers

All of CI lives in `.github/workflows/ci.yml`.

The **smoke tier** runs on `pull_request` and gates the `Can
enqueue` status check:

| Job | Runner | What it does |
|-----|--------|--------------|
| `Lint` | self-hosted `l` | `make lint` (rustfmt + clippy) |
| `Cross-check Windows` | self-hosted `l` | `make check-windows` |
| `Build (Linux x86_64)` | self-hosted `l` | `make release`, both `--web` smoke tests, `make test`, `.deb` and `.rpm` |
| `cargo audit` | self-hosted `s` | RustSec advisory check |
| `cargo deny` | self-hosted `s` | Licence, ban, and advisory policy (`deny.toml`) |
| `gitleaks` | self-hosted `s` | Secret scanning over full history |
| `shellcheck` | self-hosted `s` | `tools/run-shellcheck.sh`, then `tools/audit/test-audit-range.sh`, `tools/test-report-fuzz-failure.sh`, `tools/test-report-fuzz-run.sh` and `tools/test-fuzz-targets.sh` |
| `bidi and zero-width` | self-hosted `s` | `tools/check-bidi.sh` |
| `skillsaw` | self-hosted `s` | `pre-commit run skillsaw` over the agent context |

The **merge tier** runs on `merge_group` and gates `Can merge`:

| Job | Runner | What it does |
|-----|--------|--------------|
| `Build (Linux aarch64)` | `ubuntu-24.04-arm` | Build, test, `--web` smokes, `.deb`, `.rpm` |
| `Build (macOS aarch64)` | `macos-latest` | Build, test, tarball |
| `Build (Windows x86_64)` | `windows-latest` | Build, test, zip (`--no-default-features`) |
| `Build (Windows aarch64)` | `windows-11-arm` | Build, test, zip (`--no-default-features`) |

In practice the smoke tier finishes in about ten minutes, paced
by the Linux build, and the merge tier in about fifteen, paced
by the Windows x86_64 build.

Fuzzing is not a tier. It runs nightly from `fuzz.yml`; see
[the nightly fuzz lane](#the-nightly-fuzz-lane) below.

`workflow_dispatch` deliberately runs **both** tiers, which is
what makes `@shakenfist-bot please retest` a full retest.
Fuzzing is not included in that: a retest runs both tiers and
the fuzz lane is neither, so a retest neither builds nor
format-checks the fuzz workspace. Exercise that with
`gh workflow run fuzz.yml`.

### The Windows cross-check is a proxy

`make check-windows` cross-compiles the
`x86_64-pc-windows-gnu` triple from the Linux devcontainer. It
is a cheap stand-in for the merge tier's real Windows builds,
not a replacement: it catches `cfg(windows)` and windows-sys
breakage, which is what actually breaks in practice, but not
`target_env = "msvc"` differences, link failures, or anything
aarch64-specific. The msvc triple cannot be checked from Linux
without an MSVC toolchain, because `cargo check` still runs
build scripts and `aws-lc-sys` compiles vendored BoringSSL C for
the target. See
[PLAN-two-stage-ci.md](/components/ryll/plans/PLAN-two-stage-ci/).

## The nightly fuzz lane

`.github/workflows/fuzz.yml` builds and smoke-runs every
`cargo-fuzz` target in `shakenfist-spice-protocol/fuzz` at
12:00 UTC daily, and on `workflow_dispatch`. It is not part of
either tier and gates nothing. The targets are read from the
`[[bin]]` tables in `fuzz/Cargo.toml` rather than listed in the
workflow, so a new one is fuzzed because it exists rather than
because somebody remembered to add it. That manifest and not a
glob over `fuzz_targets/`, because it is what `cargo fuzz build
<name>` itself resolves against: a glob would pick up a helper
module dropped in the directory, and would miss a `[[bin]]` whose
`path` points elsewhere.

The extraction lives in `tools/fuzz-targets.sh` rather than
inline in the workflow. It decides which targets get fuzzed at
all, which makes its failure mode silence — a target it drops is
never built, never fails, and so is never reported — and nothing
in this repository lints or tests a workflow `run:` block, since
`tools/run-shellcheck.sh` globs `scripts/` and `tools/` and no
job invokes `actionlint`. As a script it gets both, the second
through `tools/test-fuzz-targets.sh`.

The extraction is an `awk` pattern, not a TOML parser, so it can
be shown valid TOML it does not understand — `name="x"` without
the spaces is the practical one, and `cargo fuzz` accepts it. The
zero-target guard only catches losing *every* target, so the
script counts `[[bin]]` tables independently and fails if that
count and the number of names parsed disagree. The count
deliberately tolerates leading whitespace the parser does not: a
guard blind in the same places as the thing it guards would agree
with it and say nothing. The name is taken as the whole remainder
of its line rather than as `awk`'s third field, so a name
containing a space arrives whole and is rejected by the
`[A-Za-z0-9_-]` check; reading the third field would hand back
its first word, which looks like a clean shorter name, agrees
with the table count, and fuzzes a target that does not exist.

A quoted value is matched and unwrapped whole, in both of TOML's
string spellings, rather than having its quotes stripped and a
trailing `#` comment cut off afterwards. That order is the point.
Cutting at the first `#` first would turn `name = "fuzz#target"`
into `fuzz` — a plausible-looking shorter name that passes the
charset check and fuzzes a target that does not exist, which is
the same silent shortening every other guard here exists to
prevent. Unwrapping first means a `#` inside the literal survives
to be rejected loudly, and a `#` after it is never part of the
value at all. Both spellings and the trap are fixtures in
`tools/test-fuzz-targets.sh`.

It is one job, not a matrix leg per target. Every leg of the
matrix it replaced spent 255 of its 340 seconds on `ensure-cache
fuzz-devcontainer` — the same image build, four times, on four
separate runners, ahead of 78 seconds of work that actually
differed. Sharing it trades wall-clock the nightly does not need
(roughly 570s serial against 340s parallel) for the thing that
is actually scarce, which is `l` runners.

It used to be the merge tier's other half, running on
`merge_group` beside the cross-platform builds. That asked the
shared `l` pool for four runners at the moment a merge group
formed — the largest single request ryll makes of a pool six
workers wide across every repository — and when the pool was
starved the queue did not merely run slowly. GitHub's
`check_response_timeout_minutes` caps at 360, it is a wall clock
from when the merge group forms, and it does not distinguish a
job that is running slowly from one that has not been given a
runner. Three times in eight days
([#329](https://github.com/shakenfist/ryll/issues/329)) the fuzz
jobs waited out the full six hours and the pull request was
evicted from the queue. On the third occasion they got runners
and passed twenty minutes *after* the eviction, so the merge
group's run was entirely green and the PR had silently failed to
merge.

The fuzz crate's format check came along with them, and it is
worth saying why it did not stay behind in the smoke tier
instead. The fuzz crate is a detached workspace, so `cargo fmt
--all --check` in the `lint` job does not reach it, and a format
check is otherwise exactly the sort of cheap, deliver-it-early
job the smoke tier is for. It is not cheap here: `make
fuzz-fmt-check` depends on `ensure-cache fuzz-devcontainer`,
which is 255 of the 340 seconds a fuzz leg used to take, and it
needs an `l` runner to do it. Paying that on every push to every
pull request would spend more of the scarce pool than moving the
fuzz jobs off the merge queue gives back, so formatting drift in
`shakenfist-spice-protocol/fuzz` is caught by the nightly along
with everything else.

Within the nightly the format check is tolerant rather than
fatal, and reports as its own issue through the reporter's
`--fmt-failure` mode. Formatting drift here is both the most
likely non-target failure in this lane and the least urgent one,
and a check that aborted the job would mean a target that had
genuinely stopped compiling stayed invisible for as long as the
format nit went unfixed — inverting the priority the lane exists
to serve. Its marker lives in `fuzz-logs/fmt/` so the
`fuzz-logs/*.failed` glob the report job walks cannot mistake it
for a target.

The `fuzz-devcontainer` build is the one thing that does abort
the job, in a step of its own ahead of both. Everything below it
needs that image, so a broken build is not a per-target failure:
letting it through would fail every target and file an issue
about each of them for one problem. Failing early leaves no
markers at all, which the report job reads as a run that died
before the targets and files exactly one issue about.

A smoke run that panics leaves the input that produced it in
`shakenfist-spice-protocol/fuzz/artifacts/<target>/`, and a step
before the upload copies that into `fuzz-logs/artifacts/` so it
travels with the logs. The 40-line tail in the issue body
usually carries libFuzzer's base64 line, but that is a fallback
rather than a guarantee, and a crash found a day later against
develop is worth more to reproduce than one that was in front of
a human immediately. The copy is deliberate rather than a second
`path:` on the upload step: a second path moves the artifact's
root up to the workspace, which would nest `fuzz-logs/` a
directory deeper than `tools/report-fuzz-run.sh` walks and turn
every failing night into a `--no-artifact` issue.

The job's `timeout-minutes: 90` covers every target end to end.
It was previously per matrix leg, i.e. per target. Four targets
finish inside ten minutes so there is a great deal of headroom,
but the work is serial now and `tools/fuzz-targets.sh` exists
precisely so the target count can grow without anyone editing
the workflow, so the budget is worth revisiting when it does.
A timeout truncates the loop, and the targets never reached
write no marker and are indistinguishable from passing ones;
only `targets-ran.txt`'s absence catches that, as a single
run-level issue that cannot name them.

The `report` job runs only on `develop`. `workflow_dispatch` is
the documented way to exercise this lane on demand, and without
that guard a dispatch from a scratch branch would file genuine
`bug`-labelled issues — which the real nightly would then dedup
onto, commenting on somebody's test issue instead of filing its
own. A branch dispatch still runs the fuzz job and still uploads
its logs; it just does not file.

The trade is deliberate: a fuzz target that stops building is
now caught within a day rather than before the change lands.
That is acceptable here because this is a build-and-doesn't-panic
gate rather than a real fuzz campaign — long-running
coverage-guided fuzzing is [#135](https://github.com/shakenfist/ryll/issues/135) —
and because nothing else in the merge tier depended on its result.

### Failures are issues, not a red workflow

Nobody reads a scheduled workflow's result. A failing pull
request check stands between someone and their merge; a failing
nightly is a mark on the Actions tab, and GitHub's only
notification for it is an email to whoever pushed last, which at
12:00 UTC is nobody's inbox in particular.

So the `report` job files a GitHub issue per failing target, and
*that* is the notification. Two scripts do it:
`tools/report-fuzz-run.sh` walks the marker files the fuzz job
left behind and decides what gets reported, and
`tools/report-fuzz-failure.sh` writes each issue. Both are
scripts rather than workflow `run:` blocks for the reason given
above for `tools/fuzz-targets.sh`: nothing lints or tests a
`run:` block, and every one of these three has silence as its
failure mode. `tools/test-report-fuzz-run.sh` stubs the reporter
and asserts which invocations the walk chooses, which is the part
the reporter's own test cannot see.

Six details in the arrangement are load-bearing:

- The fuzzing step exits 0 whatever the target does, recording
  the verdict as a marker file. A step that aborted the job would
  take the log upload and the report job with it. The markers
  carry text rather than being `touch`ed empty: a zero-byte
  file's survival through `upload-artifact` and back is an
  assumption, and a marker that went missing would read to the
  report job as a run that failed outside the targets — an
  actively wrong diagnosis, filed under the wrong title.
- The log is uploaded before anything fails, because when issue
  filing is the thing that broke, the artifact is how the failure
  reaches a human.
- `fuzz-logs/` is created in a step of its own, immediately
  after `actions/checkout` and ahead of everything else that can
  fail, and the report job's `download-artifact` is
  `continue-on-error`. A job that died in the devcontainer build
  or the format check writes no marker and no log; an upload of
  an empty path creates no artifact, and a download of a missing
  artifact is a hard error that would take the report job — and
  with it the whole notification — down with the fuzz job. The
  step sits *ahead* of the cargo cache rather than after it
  because the two run-level modes below exist to be told apart:
  behind the cache restore, a cache failure wrote no run marker
  and so filed `--no-artifact`, whose body sends the reader to
  the upload and download steps — the wrong first move for a job
  that never got as far as having anything to upload.
- The target step writes `fuzz-logs/targets-ran.txt` *after* the
  loop, and the walk keys its run-level branch on that file's
  absence. A passing target writes no marker, so an empty marker
  set on its own is ambiguous between "every target passed" and
  "the loop never ran" — and the loop does abort, at an
  `exit 1`, when `tools/fuzz-targets.sh` rejects the manifest.
  Inferring the answer from the absence of *other* markers is
  what a second failure on the same night can mask: a formatting
  drift and an unreadable manifest together used to file the
  formatting issue and say nothing at all about no target having
  been fuzzed. A positive completion marker says which happened
  directly, so the two cannot interfere.
- The report job never finishes having reported nothing. It only
  runs when the fuzz job did not succeed, so a marker set that
  accounts for no failure is itself a failure worth hearing
  about — the run died before the loop, the loop was cut short,
  or something after it (the artifact upload, say) broke. Which
  of those gets said depends on `fuzz-logs/run-info.txt`, written
  immediately after checkout. Present, the artifact round-tripped
  and the failure really is in the run: `--run-failure`. Absent,
  either checkout itself failed — it is the one step ahead of the
  marker, and the `--no-artifact` body names it first — or the
  logs never arrived at all; both leave the fuzz job's own
  failure unread, so both are `--no-artifact`. The
  modes are separate because they are different bugs with
  different first moves, and because they carry separate titles
  none of them can dedup on top of another and bury it.
- Reporting runs on the static runner, where the rest of this
  repository's `gh` calls run — `release.yml`'s version-mismatch
  issue is the precedent. The `debian-12-docker` image is not
  known to carry the CLI. A failure to report is counted rather
  than thrown, and fails the job at the end: one target nobody
  could file about must not stop the rest being filed.

Recurrences comment on the open issue for that target rather
than filing a duplicate: a target that stops compiling stays
broken until someone fixes it, and without dedup the nightly
would file one issue per target per night. The dedup lookup
matches on issue title alone and deliberately not on the `bug`
label the reporter applies, because a label stripped during
triage would silently switch dedup back off.

A lookup that fails falls through to filing — a duplicate issue
is a much smaller problem than a failure nobody hears about —
but it says so first. The `gh issue list` call and the `jq` that
reads it are separate steps with their statuses checked, and a
missing `jq`, a non-zero `gh`, or output `jq` cannot parse each
emit a `::warning::` before falling through. Without that,
a permanently broken lookup and a genuine first failure produce
the same empty answer, and the nightly quietly refiles the same
issue every night — which reads as ordinary nightly noise rather
than as the reporter being broken.

Because dedup keys on an *open* issue, closing one is part of
the fix. An issue left open after the target is repaired turns
every later failure of the same thing into a "Failed again"
comment on a thread people have stopped reading, rather than
into new work. Nothing closes them automatically — the fuzz job
runs on `debian-12-docker`, which is not known to carry `gh`,
and giving it `issues: write` to close what the static runner
filed would spread the credential across both — so every issue
this lane files says so in its own body, where the person doing
the fixing will read it.

The reporter is the only channel this lane has, so its failure
mode is silence — and silence is invisible until a fuzz target
happens to break. `tools/test-report-fuzz-failure.sh` pins its
behaviour against that: the excerpt bounds, the UTF-8 and NUL
scrubbing, the markdown fence, the `--run-failure`,
`--no-artifact` and `--fmt-failure` bodies, and the argument
contract, all through `--dry-run` so it needs no network and no
`GH_TOKEN`.

Dedup is covered too, and that part cannot go through `--dry-run`
— a dry run returns before the reporter talks to GitHub at all,
which would leave the search qualifier, the `--json` field set
and the jq exact-title match untested. Those are the pieces a
`gh` or API change breaks *quietly*: a lookup that starts
returning nothing does not error, it just files a fresh issue
every night. So the reporter takes its `gh` command from `$GH`
and the test stubs it, driving the recurrence, first-failure,
near-miss-title, unparseable-response and failing-`gh` paths
against canned responses — the last two also asserting the
warning, which is the only thing separating a broken lookup from
a first failure.

The recurrence case asserts the recorded `gh` argv and not only
the outcome. The stub answers any `issue list` from its canned
response whatever it was asked, so an assertion that only reads
the result holds just as well for a lookup that has lost
`in:title` or asks for a field set the jq cannot read. The
qualifier, the `--state open` filter and the exact
`--json number,title` field set are each pinned directly, the
last of them as a substring with a trailing space so that a
widened field set does not satisfy it.

It runs in pre-commit and in `ci.yml`'s `shellcheck` job, beside
the audit-range test, `tools/test-report-fuzz-run.sh` and
`tools/test-fuzz-targets.sh`, which exist for the same reason.

Two of its assertions depend on tools the test does not itself
need — `iconv` for the UTF-8 scrub, `jq` for the dedup lookup —
and skip when they are absent. That is right in pre-commit, where
a missing tool must not block an unrelated commit, and wrong in
CI, where an image change that dropped `jq` would quietly delete
the most valuable coverage in the file and leave the check green.
So `ci.yml` sets `FUZZ_REPORTER_TEST_STRICT=1` and the skips
become failures there — and, because that turns the `jq` skip
into a hard failure, the job installs `jq` alongside `shellcheck`
rather than assuming the `debian-12` image carries it. Every
other `jq` user in this repository runs on the static runner, so
nothing had established that it does.

### What the reporting does not cover

Everything above protects against a *fuzz* failure going
unheard. It does not protect against the notification channel
itself being what broke, and that residual hole is worth naming
rather than leaving a reader to infer that the lane is
airtight.

If `gh` is missing from the static runner, the `GITHUB_TOKEN` is
under-scoped or expired, the `bug` label has been deleted, or
`tools/report-fuzz-run.sh` exits 1 on failures it counted but
could not file, the outcome is exactly the one this section
opens by rejecting: a red scheduled run and no issue. The tests
above make that unlikely for reasons inside the scripts —
argument handling, body construction, the marker walk, the dedup
lookup — but nothing exercises the live path, because the report
job only runs on `develop` and only when a fuzz job has already
failed. The first real execution of the filing path is the first
night something genuinely breaks.

Two things bound it in practice. The reporter fails the report
job rather than swallowing an error, so the run is at least red
on the Actions tab; and the fuzz job's own logs survive for 30
days in the `fuzz-logs` artifact, which is the fallback the
"upload before anything fails" ordering exists to guarantee. If
a stronger guarantee is ever wanted, the shape that would give
it is a low-frequency canary — a monthly dispatch that files a
known issue and immediately closes it — which detects a dead
reporter without waiting for a real failure to do it.

This shape is the fleet-wide standard, generalized from instar's
`coverage-fuzz.yml`; the criterion is
[fuzz-nightly-reporting](https://github.com/shakenfist/development/blob/main/docs/audits/fuzz-nightly-reporting.md)
in shakenfist/development.

## The three gates

The `develop` ruleset requires exactly three status checks, and
none of them builds anything — they are aggregators over the
jobs that do:

* **`Can see status`** runs `true` on every event. It exists so
  the ruleset always has at least one check it can see, on both
  pull requests and merge groups.
* **`Can enqueue`** depends on every smoke-tier job and runs
  only when the event is not `merge_group`.
* **`Can merge`** depends on every merge-tier job and runs only
  when the event *is* `merge_group`.

Each gate uses `if: always()` so it still runs when a dependency
failed or was skipped, and then evaluates a jq expression over
the `needs` context that maps each dependency to "success or
skipped" and requires all of them:

```bash
jq '. | to_entries
      | map([.value.result == "success",
             .value.result == "skipped"] | any)
      | all'
```

Treating a skipped dependency as success is what makes the
review-only fast path work — see below. A failed or cancelled
dependency fails the gate.

The gate that does not apply to a given event is itself skipped,
and GitHub treats a skipped required check as satisfied. That is
why `Can merge` being skipped does not block a pull request, and
`Can enqueue` being skipped does not block a merge group.

!!! warning "Adding a job means editing a gate"

    A new job is not really required until it is in a gate's
    `needs` list. Add smoke-tier jobs to `can_enqueue` (and, if
    the automated reviewer should wait for them, to
    `automated_reviewer`); add merge-tier jobs to `can_merge`. A
    job that no gate depends on can fail without blocking
    anything.

## The life of a pull request

1. You push a branch and open a pull request against `develop`.
2. The smoke tier runs. The automated reviewer runs once every
   smoke job has passed.
3. `Can enqueue` goes green. `Can merge` shows as skipped.
4. You merge the pull request. GitHub does not merge it
   immediately — it adds it to the merge queue.
5. The queue creates a `gh-readonly-queue/develop/pr-N-<sha>`
   ref containing your change merged onto the current `develop`,
   and CI runs on it with the `merge_group` event. Only the
   merge tier runs; every smoke-tier job is skipped.
6. `Can merge` goes green and the queue moves `develop` to the
   merge commit it just tested.

The queue is configured with ALLGREEN grouping and
`max_entries_to_build: 1`, which deliberately disables
speculative stacking: one entry builds at a time. For a
single-developer project on a loaded CI cluster that trades peak
throughput for never wasting a run on a speculative build that
gets ejected and rebuilt.

## Reading a merge queue ejection

If a merge-tier job fails, `Can merge` fails, and GitHub removes
the pull request from the queue. This is the part that surprises
people:

**The failing checks do not appear in the pull request's checks
list.** They ran against the merge group ref, not against your
branch, so the pull request shows only a timeline event saying
it was removed from the merge queue.

To find out what happened:

* Follow the link in that timeline event, or
* go to **Actions → CI** and look for the run whose branch is
  `gh-readonly-queue/develop/pr-<your PR>-<sha>`.

An ejection means one of two things. Either your change really
does break a platform the smoke tier cannot see — the usual
suspects are the msvc Windows builds and anything that only
compiles on aarch64 — or the merge tier hit infrastructure
flakiness. Push a fix and merge again, or just re-queue the
unchanged pull request if you believe it was flaky.

A queued entry is also rebuilt when `develop` moves underneath
it, which the `prune-reviews` bot does after most merges. With
one entry at a time this is usually invisible, but it is the
thing to look at if you see queue churn.

## Review-only changes

Changes that touch only the code-review artefacts —
`REVIEWS.md`, `.vscode/*.weaudit`, `.vscode/*.weaudit-shas.json`
and `.vscode/review-scope.toml` — cannot affect the build, so
the `check_paths` job skips every tier job for them. Both gates
still pass, because their jq counts a skipped dependency as
success, so such a pull request goes through the queue without
running a single build.

`check_paths` uses `dorny/paths-filter` with
`predicate-quantifier: 'every'`. That matters: with the default
quantifier a file matches if it matches *any* pattern, so `'**'`
would match everything and silently defeat the `!REVIEWS.md`
exclusions. Keep its skip list in sync with the `.vscode`
whitelist in `.gitignore` and with `codeql-analysis.yml`.

## Retesting

Commenting `@shakenfist-bot please retest` on a pull request
runs `gh workflow run ci.yml` against the branch. Because
`workflow_dispatch` runs both tiers, this exercises the merge
tier on the branch — useful for confirming a Windows or macOS
fix before queueing, rather than discovering it by ejection.

A dispatch run does **not** report into a queued entry. To
retest something already in the queue, remove it from the queue
and add it again.

## Where binaries come from

There is no longer a `push: branches: [develop]` trigger on
`ci.yml`. The merge queue already tests the exact commit that
lands, so a push-triggered run would only repeat it.

| You want | Look at |
|----------|---------|
| Binaries for a pull request | The `Build (Linux x86_64)` job's artifacts on the pull request's CI run (`.deb` and `.rpm`, 30-day retention) |
| Binaries for a `develop` SHA | The `merge_group` CI run that landed it — all four platform builds attach their artifacts there |
| Binaries for an arbitrary branch | Run `manual-build.yml` (Actions → Manual build) and pick the platforms |
| Release binaries | `release.yml`, triggered by a `v*` tag — see [releasing.md](/components/ryll/releasing/) |

## Branch protection and the bot

The `develop` ruleset ("Develop branch") requires a pull
request, enables the merge queue, requires the three gate
checks, and blocks deletion and non-fast-forward pushes.

The one thing that still pushes directly to `develop` is the
`prune-reviews` workflow, which drops review marks invalidated
by whatever just merged. It authenticates as `shakenfist-bot`
using the `DEPENDENCIES_TOKEN` secret; the bot is a member of
the "SF Can Skip Merge Queue" team, which is the ruleset's
bypass actor. GitHub does not accept the built-in Actions app as
a bypass actor at all, so the token is load-bearing rather than
a preference. That team also contains a human, which is the
escape hatch if the ruleset ever wedges.

A push made with a personal access token retriggers workflows,
where a `GITHUB_TOKEN` push does not, so `prune-reviews`
triggers itself once. That is safe rather than a loop: the
second run finds nothing to prune and exits before committing.

The workflow is guarded to `refs/heads/develop`, because
`tools/ci-prune-reviews.sh` rebases onto `develop` and pushes to
`develop` whatever ref was checked out — dispatching it on a
branch would otherwise push that branch's unmerged commits
straight to `develop`.

Ruleset changes are captured under `.github/exported-config/`
by `export-repo-config.yml`, which runs daily and on demand.

## Reproducing CI locally

Every Linux x86_64 job runs inside the devcontainer via the
Makefile, so the local commands are the ones CI runs:

```bash
make lint            # rustfmt + clippy, as the Lint job
make check-windows   # the Windows cross-check
make test            # the unit test suite
make web-smoke       # --web startup and shutdown
make web-smoke-tls   # the same, with TLS
```

The merge tier cannot be reproduced locally — we own no macOS,
Windows, or aarch64 Linux hardware, which is also why those jobs
use GitHub-hosted runners and carry `audit-ok:
github-hosted-runner` markers for the workflow-standards
consistency audit.

## Workflow inventory

| Workflow | Purpose |
|----------|---------|
| `ci.yml` | Smoke tier and merge tier, the three gates, and the automated PR review |
| `manual-build.yml` | On-demand binary builds of arbitrary branches |
| `release.yml` | Build and publish release artifacts |
| `codeql-analysis.yml` | CodeQL security scanning |
| `supply-chain.yml` | Weekly advisory drift against develop (cargo-audit, cargo-deny); the PR-time scanners live in `ci.yml` |
| `fuzz.yml` | Nightly `cargo-fuzz` build and smoke run against develop; failures filed as issues |
| `renovate.yml` | Automated dependency updates (hourly) |
| `export-repo-config.yml` | Daily repository configuration export |
| `pr-re-review.yml` | Bot-triggered PR re-review (`@shakenfist-bot please re-review`) |
| `pr-retest.yml` | Bot-triggered CI re-run (`@shakenfist-bot please retest`) |
| `prune-reviews.yml` | Prune stale review marks after each push to develop |

## Concurrency

Every job a pull request or PR comment can trigger must declare
a job-level `concurrency:` block that cancels superseded runs.
The self-hosted fleet runs `MAX_WORKERS = 6` across every
Shaken Fist repository, and the `l` pool is its scarcest
resource: without a concurrency group a superseded run can hold
an `l` slot for its full 45-minute timeout while its replacement
queues behind it.

Use the job-level form rather than the workflow-level one, so
that unrelated jobs in the same workflow do not cancel each
other:

```yaml
jobs:
  my-job:
    runs-on: [self-hosted, vm, debian-12, s]
    concurrency:
      group: ${{ github.workflow }}-${{ github.ref }}-my-job
      cancel-in-progress: true
```

Comment-triggered workflows (`pr-retest`, `pr-re-review`) need a
different group key:

```yaml
      group: pr-retest-${{ github.event.issue.number }}
```

`github.ref` points at the default branch for `issue_comment`
events, so it does not distinguish one pull request from
another. The PR number does.

Merge queue jobs need a different key again. On `merge_group`,
`github.ref` is the per-attempt queue branch
`gh-readonly-queue/develop/pr-<N>-<SHA>`, and GitHub mints a
fresh SHA every time it rebuilds the group — which it does on
every push to `develop`. A group keyed on it is therefore unique
per rebuild, `cancel-in-progress` never matches, and superseded
merge groups run to completion holding runners the whole fleet
shares. Branch the key on the event:

```yaml
      group: >-
        ${{ github.workflow }}-my-job-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
```

The `merge_group-` prefix keeps a queue run from sharing a group
with a `workflow_dispatch` run on `develop`, whose `github.ref`
is the same string.

Cancelling a merge group is only safe because the queue is
serial: the develop ruleset sets `max_entries_to_build: 1`, so
the queue builds one entry at a time and any other in-flight
`merge_group` run is by definition superseded, its queue branch
already abandoned by GitHub. That setting and this key have to
move together. See
[shakenfist/kerbside#284](https://github.com/shakenfist/kerbside/issues/284)
for what the unfixed version cost, and the fleet audit
[merge-group-cancellation](https://github.com/shakenfist/development/blob/main/audits/merge-group-cancellation.md).

Scheduled, push-to-default, and release workflows must **not**
enable `cancel-in-progress`. Cancelling a release mid-publish,
or a renovate run mid-PR-creation, leaves partial state behind.

`fuzz.yml` is the awkward case: it is scheduled, but it also
takes a `workflow_dispatch`, and a manual run *should* supersede
an in-flight nightly rather than queue behind it. It resolves
that by making the flag an expression rather than choosing one
answer for both events:

```yaml
      cancel-in-progress: ${{ github.event_name == 'workflow_dispatch' }}
```

A flat `true` there is the general rule's case exactly. The job
waits on the same starved `l` pool the lane exists to get out of
the way of, so a nightly can still be sitting unstarted when the
next one is created, and `cancel-in-progress` cancels *pending*
members of a group as well as running ones. Night N would be
cancelled by night N+1; the report job skips `cancelled`; and
the lane goes silent every night the pool stays starved — in
precisely the condition it was written for. Left to queue, night
N is failed by GitHub after 24 hours and correctly files
`--no-artifact`. The only cancellation left is a dispatch
superseding a nightly, which is the one the report job is right
to ignore.

## Build network isolation

Cargo runs a dependency's `build.rs` as ordinary code at compile
time, so a compromised crate can execute during a plain `cargo
build` — before any ryll code runs, and on every job that merely
compiles. To contain that, the Makefile splits the build in two:

- `make fetch` (`cargo fetch`) downloads every crate named in
  `Cargo.lock` but compiles nothing, so no build script runs. It
  is the only build step allowed network access.
- the targets that compile the workspace — `build`,
  `build-tokio-console`, `release`, `check-windows`, `test`, `lint`
  and `lint-fix` — then run in the devcontainer with `--network
  none` and the cargo cache mounted read-only. A malicious build
  script cannot reach a C2 or exfiltrate secrets (its download call
  fails and the build aborts loudly), and it cannot poison the
  cache for the rest of the job.

This is the same reason docs.rs builds every crate offline, and is
what would have turned the 2026-08-20 `arrayref` / `proc-macro1`
build-script dropper (reported as rustsec/advisory-db#3161; no
`RUSTSEC-YYYY-NNNN` id had been assigned at the time of writing)
into a loud build failure rather than a silent compromise.

Three targets still compile with the network up, and both of the
lanes they serve are real: `fuzz` runs nightly and
`publish-crates` on every release.

- `fuzz-build-%` and `fuzz-smoke-%` build the detached fuzz
  workspace (see `shakenfist-spice-protocol/fuzz/Cargo.toml`'s
  `[workspace]` table), which `make fetch` does not populate, so
  they must still resolve and download at compile time. Isolating
  them needs a second `cargo fetch` against that workspace first,
  tracked in shakenfist/ryll#306.
- `publish-crates` runs `cargo publish`, which builds each crate as
  part of its verify step and genuinely needs the network to
  upload. This one cannot be isolated.

(`fuzz-fmt-check` and `fuzz-fmt` also run networked, but they
only run `cargo fmt` and compile nothing. So do `deb`, `rpm` and
the `web-smoke` targets, which repackage or run the binary
`release` already produced.)

Outside the Makefile entirely: release's `build-ryll-wheels` job
runs `tools/build-ryll-wheel.sh`, which builds ryll with maturin
inside `quay.io/pypa/manylinux_2_28_*` rather than the
devcontainer, with network and without `--frozen`. That is a
shipped artifact — it is what `pip install ryll` gets — so it is
the most significant gap here. Closing it needs the same
fetch/compile split inside the manylinux image, tracked in
shakenfist/ryll#305; until then the wheel is built on the same
terms as before this change.

Because those jobs write the cargo cache with the network up,
they save it under their own `actions/cache` key prefix
(`fuzz-cargo-cache`, `publish-cargo-cache`) rather than the shared
one. Otherwise a networked, writable-cache job could hand the next
run's isolated build the very cache the read-only mount exists to
protect — the mount stops poisoning within a job, not across them.

What the isolation does not buy: the checkout stays mounted
read-write, because the build has to write `target/`. A build
script running offline can therefore still modify the source tree,
`tools/*.sh` and the build output — the defence stops exfiltration
and cache poisoning, not tampering. That matters because networked
steps run afterwards in the same workspace (`make deb`, `make
rpm`, `make web-smoke`, and the release job's upload of
`target/release/ryll` as a shipped artifact), against a tree an
earlier build script could have touched.

The download cache lives in `.cargo-cache` and is persisted across
runs by an `actions/cache` step, keyed on `Cargo.lock`, inserted
after `actions/checkout` — whose default `clean: true` runs `git
clean -ffdx` and would otherwise delete the gitignored cache every
run. Point `CARGO_CACHE` at a path outside the checkout to
relocate it. (The cross-platform merge-tier jobs build with a
natively installed cargo rather than in the devcontainer and use
`Swatinem/rust-cache`; the `--network none` isolation applies only
to the containerised Linux builds.)

Severing the network namespace leaves the container with only
`lo`, which the WebRTC tests notice: the default UDP bind policy
excludes loopback, so on such a host it correctly resolves to
nothing and refuses to build a peer connection (see
`shakenfist-spice-webrtc/src/bind_addrs.rs`). Tests go through
`bind_addrs_for_tests`, `bind_policy_for_tests` and
`WebrtcBridgeConfig::for_tests` instead, which fall back to
binding loopback when the host offers nothing else — the peers
they connect are in the same process, so a loopback candidate
serves. The production default is deliberately left alone: a
server that quietly bound loopback would advertise candidates no
browser could reach, which is what `--web-media-addr 127.0.0.1`
exists to make a deliberate choice.

The trade-off to know about: under isolation those tests no
longer exercise binding a real interface address, which is the
failure a wrong bind address produces. Nothing in CI covers that
— it is what the browser session in the webrtc-rs 0.20 upgrade's
soak phase is for. The inversion is worth stating too: every CI
run of the WebRTC suite now takes the loopback fallback, so the
branch that picks a real interface address runs only on developer
machines.

## Supply-chain policy

The scanner jobs above enforce policy that lives in files at the
repository root, and changing that policy has rules the files
themselves do not state:

- **Ignoring a RustSec advisory** requires adding the advisory
  ID to *both* `deny.toml` and `.cargo/audit.toml`, with an
  inline comment on each entry giving the rationale. Both
  scanners run on every pull request and both must pass, so the
  two ignore lists have to stay in sync — editing only
  `deny.toml` produces a red `cargo audit` job. Ignores are debt
  and should not accumulate silently.
- **Allowing a new licence** means adding a permissive SPDX
  identifier to `deny.toml`'s `licenses.allow` array. The
  `licenses.exceptions` array is for a narrower case: a single
  crate declaring a licence that is not on the general allowlist,
  scoped so the grant does not apply repository-wide. The
  `epaint_default_fonts` / `Ubuntu-font-1.0` entry is the
  canonical example.
- **Suppressing a gitleaks false positive** goes in
  `.gitleaksignore`, with a comment explaining the pattern and
  why it is safe. ryll runs the upstream gitleaks binary
  directly rather than `gitleaks-action`, which requires a paid
  licence for organisation repositories.
