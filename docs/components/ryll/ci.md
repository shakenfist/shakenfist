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
| `shellcheck` | self-hosted `s` | `tools/run-shellcheck.sh`, then `tools/audit/test-audit-range.sh` |
| `bidi and zero-width` | self-hosted `s` | `tools/check-bidi.sh` |
| `skillsaw` | self-hosted `s` | `pre-commit run skillsaw` over the agent context |

The **merge tier** runs on `merge_group` and gates `Can merge`:

| Job | Runner | What it does |
|-----|--------|--------------|
| `Fuzz (×4)` | self-hosted `l` | Build and smoke-run each `cargo-fuzz` target |
| `Build (Linux aarch64)` | `ubuntu-24.04-arm` | Build, test, `--web` smokes, `.deb`, `.rpm` |
| `Build (macOS aarch64)` | `macos-latest` | Build, test, tarball |
| `Build (Windows x86_64)` | `windows-latest` | Build, test, zip (`--no-default-features`) |
| `Build (Windows aarch64)` | `windows-11-arm` | Build, test, zip (`--no-default-features`) |

In practice the smoke tier finishes in about ten minutes, paced
by the Linux build, and the merge tier in about fifteen, paced
by the Windows x86_64 build.

`workflow_dispatch` deliberately runs **both** tiers, which is
what makes `@shakenfist-bot please retest` a full retest.

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
lanes they serve are real: `fuzz` runs on every merge-group entry
and `publish-crates` on every release.

- `fuzz-build-%` and `fuzz-smoke-%` build the detached fuzz
  workspace (see `shakenfist-spice-protocol/fuzz/Cargo.toml`'s
  `[workspace]` table), which `make fetch` does not populate, so
  they must still resolve and download at compile time. Isolating
  them needs a second `cargo fetch` against that workspace first,
  tracked in shakenfist/ryll#306.
- `publish-crates` runs `cargo publish`, which builds each crate as
  part of its verify step and genuinely needs the network to
  upload. This one cannot be isolated.

(`fuzz-fmt-check` also runs networked, but it only runs `cargo
fmt --check` and compiles nothing. So do `deb`, `rpm` and the
`web-smoke` targets, which repackage or run the binary `release`
already produced.)

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
