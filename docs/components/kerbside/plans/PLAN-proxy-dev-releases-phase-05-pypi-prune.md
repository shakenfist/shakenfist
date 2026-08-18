# Proxy dev releases phase 5: bounding the dev release set

Master plan: `PLAN-proxy-dev-releases.md`. Phases 1-4 made unreleased
kerbside installs resolve a working proxy binary from PyPI by
publishing rolling dev wheels. This phase stops that stream from
growing without bound, and — because the research below shows the
obvious mechanism is not available — settles what "bounded" can
honestly mean.

Planning effort: medium (per the master plan), with the research step
done first and reported in full below.

## Scope

In scope:

* An automated, credential-free monitor of the `kerbside-proxy` PyPI
  project's storage and dev-release count, which files (and updates) a
  GitHub issue when a threshold is crossed.
* A reduction in publish inflow: dependency-lockfile-only merges stop
  triggering a dev release.
* A documented manual pruning runbook for when the monitor fires, and
  the recorded reasoning for why pruning is not automated.
* The master plan corrections this phase's survey forced (already
  applied in this planning commit — see "What the survey found").

Out of scope: automating deletion of PyPI releases (decision 1 —
declined, with reasons); yanking (decision 2 — declined); changing the
release (`v*` tag) lane in any way; the phase 4 post-merge tail
(patch175's PR and the Gerrit recheck), which is tracked on phase 4.

## What the survey found

Verified 2026-08-17 against `develop` at `eb14012`, the live PyPI JSON
API, and PyPI/Warehouse documentation.

**The master plan's volume assumption was optimistic.** The sketch said "Quota headroom is years even without
pruning … this phase is about hygiene, not urgency". Measured against
the *merged* workflow's actual path filter (`rust/**`,
`kerbside/rpc/kerbside.proto`, `tools/build-proxy-wheel.sh`,
`tools/stamp-dev-proxy-version.sh`, `tools/gen-protos.sh`, and the
workflow file), **42 of the 217 first-parent `develop` merges in the 42
days since the Rust tree was created (2026-07-06 … 2026-08-17) would
have triggered a publish** — about 30/month, 365/year. At 5.80 MB per
publish (two wheels: 2.82 MB aarch64 + 2.97 MB x86_64) that is
**~2.1 GB/year against a 10 GB project limit: about 4.7 years of
headroom.** Still years, as the sketch said — but a finite and
forecastable runway rather than an open-ended one, and one worth
knowing the size of before choosing what to build. Corrected at source in the master
plan's phase 5 sketch as part of this planning commit.

**76% of that inflow is dependency bumps that cannot change anything
the handshake protects.** Of the 42 triggering merges, **32 touch only
`rust/kerbside-proxy/Cargo.lock` and/or `Cargo.toml`** (Renovate), and
**18 touch `Cargo.lock` alone**. Only 10 touch proxy source, `build.rs`,
the proto, or the build tooling. None of the 32 can alter the gRPC
contract hash, so none of them can cause the phase 3 startup refusal
that promptness exists to avoid.

**PyPI offers no supported way to delete or yank programmatically.**
`docs.pypi.org/api/` documents Index, JSON, Upload, Integrity, Stats,
BigQuery, RSS and Secret-Reporting APIs; none has a delete or yank verb.
The Index and JSON APIs are read-only; the Upload API is publish-only.
This is not an oversight we could work around with the right token:
Warehouse issue **#12810, "Warehouse API to delete old .dev wheels
(nightly builds)" — our exact use case — is open and labelled
"Blocked"** (see also #11397). Deletion and yanking are web-UI actions
only.

**Trusted publishing cannot do it either.** Per
`docs.pypi.org/trusted-publishers/internals/`, the OIDC exchange mints a
15-minute project-scoped API token that "behaves exactly like a normal
project-scoped API token" — and since no delete endpoint exists for any
credential, the question of scope is moot.

**`pypi-cleanup` works by driving the web login form.** It is maintained
(v0.1.10, 2026-02-26; last commit 2026-03-20) and does support 2FA — by
requiring **the account password *and* the TOTP secret** as environment
variables. Its own README leads with "THIS UTILITY IS DESTRUCTIVE AND
CAN POTENTIALLY WRECK YOUR PROJECT RELEASES AND MAKE THE PROJECT
INACCESSIBLE ON PYPI". No maintained alternative was found.

**Deletion is irreversible in a way that matters here.**
`pypi.org/help/#file-name-reuse`: "PyPI does not allow for a filename to
be reused, even once a project has been deleted and recreated" and
"Deleted files cannot be re-uploaded." Deleting a release does not free
its version number either. Because our dev versions are
setuptools_scm commit counts, a pruned `0.4.1.devN` can never be
republished from that commit — a re-run of the bootstrap-style
`workflow_dispatch` on an older commit would fail permanently.

**Yanking is the wrong tool.** PEP 592 yanking makes resolvers skip a
release (which is what we want for old dev wheels anyway — the floor
already picks the newest), but it **frees no storage**, so it does
nothing for the constraint that actually binds. It is also UI-only.

**There is nothing to prune today.** The project holds 2 final releases
(0.3.0, 0.4.0) and 1 dev release (0.4.1.dev184): 17.8 MB, **0.18% of the
10 GB limit**. This phase is therefore entirely preventive.

**The data a monitor needs is available without any credential.** The
JSON API (`pypi.org/pypi/kerbside-proxy/json`) returns per-file `size`
and `upload_time` and answered in 0.05 s unauthenticated. PyPI exposes
no usage endpoint (`docs.pypi.org/project-management/storage-limits/`
points at the web UI; current defaults **100 MB per file, 10 GB per
project**), so summing JSON API sizes is the normal client-side
approach.

**The repo already has the exact automation pattern this needs.**
`tools/file-nightly-failure-issue.sh` files or updates a GitHub issue,
deduplicating on exact title via `gh issue list --search` plus a `jq`
filter and commenting on the existing issue rather than filing a
duplicate. It is called from `direct-qemu-functional.yml:294-310` and
`sf-e2e-functional.yml:221-237` from a **dedicated job** gated on
`github.event_name == 'schedule'` holding `permissions: issues: write`,
deliberately isolated from the lane job that runs PR code.

**Credential posture to preserve.** No PyPI password or token exists
anywhere in this repo: `release.yml` and `dev-proxy-wheel.yml` both
publish purely via OIDC trusted publishers. The only secrets are
`GITHUB_TOKEN`, `RENOVATE_TOKEN` and `DEPENDENCIES_TOKEN`. Note also
that publishing runs on **self-hosted** runners.

## Decisions

1. **Do not automate deletion.** This is the decision most likely to be
   argued with, because the master plan's open question 6 chose "automate
   it … rather than deferring". The master plan also anticipated this
   outcome, offering option (b) "degrade gracefully to a monitoring
   workflow" if automation proved "too fragile or policy-risky", and the
   research says it is both. Automating deletion would mean storing a
   PyPI **account password and TOTP seed** as repository secrets on
   **self-hosted** runners, to drive a **web login form** that PyPI does
   not offer as an API — trading a zero-credential OIDC posture for an
   account-wide credential that can destroy the project's final releases,
   in order to reclaim space we will not need for years, with every
   deletion **permanently burning that version number**. Storing the TOTP
   seed beside the password also defeats the 2FA it satisfies. The
   honest read of Warehouse #12810 being open-and-blocked is that the
   platform does not support this workflow yet; the right response is to
   wait for it rather than to automate around it with credentials.
2. **Do not yank either.** It frees no storage, so it does not address
   the binding constraint, and it is UI-only so it is no more automatable
   than deletion. It would also be redundant: the committed floor already
   resolves the newest wheel.
3. **Bound the set primarily by reducing inflow.** Drop
   `rust/kerbside-proxy/Cargo.lock` from the publish trigger via a
   negative path pattern. A lockfile-only change moves transitive pins;
   it cannot change the proto, the contract hash, or the binary's
   interface, so a dev wheel that lags one lockfile bump is still a
   correct, contract-compatible binary — which is all a dev wheel
   promises. This removes 18 of 42 measured triggers (43%), taking the
   runway from ~4.7 to ~8.3 years, and it costs nothing but a two-line
   path filter. **`Cargo.toml` deliberately stays a trigger**: it carries
   direct dependency versions and crate features, which can change
   behaviour in ways the contract hash would not catch. Excluding it too
   would remove 32 of 42 and buy ~20 years, and is the obvious "why not
   go further?" — declined because the saving is not needed and the
   safety argument is materially weaker.
4. **Automate the *watching*, not the deleting.** A weekly workflow runs
   a credential-free check of the PyPI JSON API and files/updates a
   GitHub issue when either threshold is crossed: **total project storage
   ≥ 50% of 10 GB**, or **dev release count ≥ 300**. This is what
   discharges open question 6's actual concern ("an unautomated pruning
   chore would be forgotten until something breaks") — the chore is
   remembered by automation even though its execution is manual. The two
   thresholds fire on very different timescales at the measured rate: the
   300-release count arrives first, after roughly 17 months of
   accumulation, while storage is still only about a sixth of the limit,
   and the 50%-of-quota alarm is around four years out. That ordering is
   intended — the count is the tidiness axis and the bytes are the axis
   that actually binds, and neither is urgent when it fires.
5. **The runbook lives in `RELEASE-SETUP.md`.** That file already owns
   release infrastructure operations end to end (setup, how releases
   work, verifying, troubleshooting), so a "Pruning dev releases" section
   belongs there; `docs/proxy-architecture.md`'s packaging section gets
   one sentence and a link, per phase 4's one-page-owns-each-fact rule.
   The runbook must state the irreversibility rule prominently, because
   an operator pruning by hand in the UI can otherwise burn a version
   number without realising it.
6. **Amend the master plan's success criterion.** It currently reads "A
   merge to develop that touches `rust/**` … publishes a dev wheel", which
   decision 3 deliberately narrows. The criterion is updated in step 5b's
   commit rather than silently contradicted.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | Build the credential-free PyPI storage monitor. (1) `tools/check-pypi-storage.py`: follow the conventions of `tools/check-wheel.py` — `#!/usr/bin/env python3`, module docstring explaining rationale, `argparse`, `REPO_ROOT` via `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`, `if __name__ == '__main__': sys.exit(main())`. It fetches `https://pypi.org/pypi/<project>/json` (default project `kerbside-proxy`) with `urllib.request` (stdlib only — no venv, no third-party deps; the endpoint needs no auth), sums every file's `size`, counts releases whose version contains `.dev` separately from final releases, and compares against `--max-bytes-pct` (default 50, of a `--limit-bytes` default of 10 GB) and `--max-dev-releases` (default 300). Print a short human-readable report to stdout always (totals, percentages, counts, oldest/newest dev version). Exit 0 when under both thresholds, 1 when either is crossed. Add `--input-file PATH` to read the JSON from a file instead of the network — this is what makes it testable. Wrap lines at 120 chars, single quotes except docstrings (repo style; `flake8` runs on `tools/` via pre-commit). (2) `tools/file-pypi-storage-issue.sh`: copy the structure of `tools/file-nightly-failure-issue.sh` exactly — `#!/bin/bash`, `set -e`, usage header comment, fixed title (use `kerbside-proxy PyPI storage threshold crossed`), dedupe via `gh issue list --state open --search "in:title \"${title}\"" --json number,title --jq` exact-title filter, `gh issue comment` if found else `gh issue create`. Take the report text file and the run URL as positional args. (3) `.github/workflows/pypi-storage-check.yml`: `schedule: - cron: '0 6 * * 1'` (weekly Monday) plus `workflow_dispatch`; top-level `permissions: {}`; a `check` job (`runs-on: [self-hosted, static]`, `permissions: contents: read`) that checks out and runs the script, capturing stdout to a file and its exit status; and a **separate** `threshold_issue` job holding `permissions: contents: read, issues: write` with `GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}` that runs only when the check reported a crossing — this job separation is the established pattern (see `direct-qemu-functional.yml:294-310`), do not merge the two jobs. Falsifiable verification to run and record before finishing: craft two fixture JSON files and run the script against both with `--input-file`, proving exit 1 with the expected message when over threshold and exit 0 when under; then run it for real against live PyPI and confirm it reports ~17.8 MB / 0.18% / 1 dev release and exits 0. `pre-commit run --all-files` (actionlint + shellcheck + flake8) passes. Commit subject: "Watch the kerbside-proxy PyPI storage budget." |
| 5b | medium | sonnet | none | Reduce publish inflow. In `.github/workflows/dev-proxy-wheel.yml`, add `- '!rust/kerbside-proxy/Cargo.lock'` to the `push:` `paths:` list, immediately after `- 'rust/**'` (GitHub evaluates include/exclude patterns in order, so the negation must follow the inclusion it narrows; a merge touching the lockfile *and* any other matching path still publishes, which is the intent). Extend the workflow's header comment with a short paragraph explaining why: a lockfile-only bump moves transitive pins and cannot change the proto, the contract hash, or the binary's interface, so the dev wheel may lag one such bump without breaking the promise a dev wheel makes; this measurably removes over 40% of triggers (42 measured in 42 days, 18 of them lockfile-only). State explicitly that `Cargo.toml` is deliberately still a trigger because it carries direct dependency versions and crate features. Then sync every prose copy of the path list: `docs/proxy-architecture.md` (the "How the binary gets there: packaging" section lists the filter), `RELEASE-SETUP.md`'s "Dev releases" section, and `docs/plans/PLAN-proxy-dev-releases-phase-01-publish-workflow.md` if it restates it — find them with `grep -rn 'stamp-dev-proxy-version.sh' --include='*.md' .`. Finally, in `PLAN-proxy-dev-releases.md`, amend the first success criterion (currently "A merge to develop that touches `rust/**` or `kerbside/rpc/kerbside.proto` publishes a `kerbside-proxy` dev wheel … and a Python-only merge publishes nothing") to exclude lockfile-only merges, and note the phase 5 decision that narrowed it. Do not touch the `workflow_dispatch` escape hatch — a forced publish must still be possible. `pre-commit run --all-files` passes. Commit subject: "Stop lockfile bumps from publishing dev wheels." |
| 5c | medium | sonnet | none | Document the pruning position. In `RELEASE-SETUP.md`, add a `## Pruning dev releases` section after "Dev releases". It must say: (i) PyPI has **no API** for deleting or yanking — Warehouse issue #12810 requests exactly this for nightly/dev wheels and is open and blocked, so deletion is a manual web-UI action at `pypi.org/manage/project/kerbside-proxy/releases/`; (ii) **deletion is irreversible** — `pypi.org/help/#file-name-reuse` says a filename can never be reused "even once a project has been deleted and recreated", and because dev versions are setuptools_scm commit counts a pruned `0.4.1.devN` can never be republished from that commit, so never prune a version a running CI job might still resolve; (iii) yanking is not used, because it frees no storage; (iv) the monitor (`tools/check-pypi-storage.py`, run weekly by `pypi-storage-check.yml`) is what tells you pruning is due, and the thresholds are 50% of the 10 GB project limit or 300 dev releases; (v) the manual procedure: keep all final releases and the newest ~20 dev releases, delete older dev releases oldest-first, and re-run the monitor afterwards to confirm. Also record why automation was declined (an account password plus TOTP seed as repository secrets on self-hosted runners, to drive a login form, for space not needed for years). Then add ONE sentence plus a link in `docs/proxy-architecture.md`'s "How the binary gets there: packaging" section noting that dev releases accumulate and are pruned manually per `RELEASE-SETUP.md` — do not restate the mechanism there. Falsifiable check: `grep -rn 'pypi-cleanup' --include='*.md' .` returns hits only in `docs/plans/`, since the tool is discussed as a rejected option in planning, not recommended in operator docs. `pre-commit run --all-files` passes. Commit subject: "Document how and when to prune dev releases." |

## Risks and mitigations

* **The monitor silently stops working** (PyPI changes the JSON schema,
  the network fails) and the alarm never fires — the classic failure of
  a watchdog nobody watches. The original mitigation claimed here — that
  the script exits non-zero and so "surfaces in the normal
  failed-workflow path" — was wrong, and review caught it: the normal
  failed-workflow path is a red entry in the Actions tab, which is
  precisely the alerting mechanism this workflow's own header says
  alerts nobody. It bites harder here than for the nightly lanes,
  because those are expected green daily and a week of red gets noticed,
  whereas this check is designed to report nothing for years, so silence
  from a broken monitor is indistinguishable from silence from a healthy
  one. Actually mitigated by a `broken_monitor` job that files a
  separately-titled tracking issue whenever a scheduled run fails, and
  by the committed tests that prove the alarm fires at all.
* **Thresholds chosen once and never revisited.** 300 dev releases at
  the measured rate is reached after roughly 17 months, with storage
  still around a sixth of the limit; the 50%-of-quota alarm is roughly
  four years out. (This bullet previously said 50% of quota arrived at
  17 months, contradicting decision 4 — the count threshold is the one
  that fires first, and by a wide margin.) The issue body includes the
  current numbers so whoever reads it can judge urgency rather than
  trusting the threshold.
* **Inflow reduction hides a needed rebuild.** A Rust dependency security
  fix landing as a lockfile-only bump will not reach dev wheels until the
  next substantive change. Accepted: dev wheels are CI artifacts, releases
  always rebuild from scratch, and `workflow_dispatch` can force a publish
  at any time — which the 5b brief preserves deliberately.
* **A future operator prunes by hand and burns a version number**, or
  deletes a wheel an in-flight CI run is resolving. Mitigated by making
  irreversibility and the "newest ~20" floor the loudest part of the
  runbook (5c).
* **This phase closes the master plan without ever pruning anything.**
  That is the intended outcome and is stated as such, so a later reader
  does not mistake the absence of a pruning workflow for an oversight.

## Definition of done

* `tools/check-pypi-storage.py` exits 1 on a fixture that crosses each
  threshold and 0 on one that does not; run live against PyPI it reports
  the real numbers and exits 0. Pinned by
  `kerbside/tests/unit/test_check_pypi_storage.py` rather than left as
  one-time manual fixture runs — a run recorded at review proves the
  code worked once, which is not what a watchdog needs. The suite covers
  both boundaries in both directions (the comparison is `>=`), the
  dev/final split, ordering by `upload_time` rather than version string,
  and the three-way exit contract end to end through `main()`.
* Exit 1 is reachable only by a genuine threshold crossing. That is
  enforced structurally, by a guard around `main()`'s body rather than
  only at the call sites that use `fail()`: an exception nobody
  anticipated otherwise reaches the interpreter and exits 1, which is
  the code the workflow reads as "file an alarm", and the alarm would
  carry an empty report. Two such cases were found in review and are
  pinned as regression tests — a `releases` value that is not a mapping,
  and a file entry with a null `size`.
* `pypi-storage-check.yml` grants `issues: write` only to the job that
  files the issue, and no job in it holds any PyPI credential — there is
  still no PyPI password or token anywhere in the repo
  (`grep -rn 'PYPI' .github/workflows/ | grep -i 'password\|token'`
  returns nothing beyond OIDC usage).
* A lockfile-only change to `rust/kerbside-proxy/Cargo.lock` does not
  match `dev-proxy-wheel.yml`'s push filter, while a change to
  `Cargo.toml` or `src/**` still does; the reasoning is in the workflow
  header, and no `.md` file outside `docs/plans/` still lists the old
  path set.
* `RELEASE-SETUP.md` states that deletion is manual, irreversible, and
  UI-only, names the thresholds, and gives the oldest-first procedure;
  `docs/proxy-architecture.md` links to it without restating it.
* The master plan's first success criterion no longer claims every
  `rust/**` merge publishes, and its Execution table and the `index.md`
  row are current (done in this planning commit).
* `pre-commit run --all-files` passes for each of 5a, 5b, 5c.

## Back brief

Before executing any step, back brief the operator on this plan and how
the intended work aligns with it. The specific thing to confirm is
decision 1: the master plan's open question 6 asked for automated
pruning, and this plan declines to build it, substituting a monitor plus
a manual runbook. If the operator wants deletion automated despite the
credential cost, that is their call to make — but it should be made
explicitly before step 5a is spawned, because it changes the shape of
every step here.
