# Proxy dev releases phase 1: the publish workflow

Master plan: `PLAN-proxy-dev-releases.md`. This phase builds the
path-filtered GitHub Actions workflow that publishes PEP 440 dev
releases of the `kerbside-proxy` wheel to PyPI, plus the version
stamping helper and the one-time setup documentation. Phases 2-5
(committed dev specifier, contract handshake, docs/downstream,
pruning) are out of scope here.

Planning effort: high (per the master plan) — PyPI trusted
publishing, maturin version semantics and workflow security posture
all involve judgment calls. Review effort for implementation steps:
per the step table below.

## Scope

In scope:

* `.github/workflows/dev-proxy-wheel.yml` — the publish workflow.
* `tools/stamp-dev-proxy-version.sh` — dev version stamping helper.
* RELEASE-SETUP.md and AGENTS.md updates for the new workflow and
  its one-time setup (GitHub `dev-release` environment, PyPI
  trusted publisher registration).
* Pre-merge verification that the stamped wheel carries the
  expected PEP 440 dev version (dockerized native build).

Out of scope (explicitly):

* The committed `kerbside-proxy>=0.4.0.dev0` specifier in
  `pyproject.toml` and the stamp-script changes — phase 2. Until
  phase 2 lands, dev wheels published by this phase are resolvable
  but nothing depends on them.
* The contract handshake — phase 3.
* Any change to `tools/build-proxy-wheel.sh`, `release.yml`, or
  `tools/stamp-proxy-version.sh` (release behaviour is unchanged).
* Pruning — phase 5.

## What the survey found

No prior phase exists; this is phase 1. The master plan's phase 1
section was verified against the tree on 2026-08-14 and **no false
claims were found**. Specific verifications, so the reader can
check:

* Version derivation: `git describe --dirty --tags --match 'v*'
  --first-parent` (the exact command configured in
  `[tool.setuptools_scm]`, pyproject.toml:135) yields
  `v0.4.0-159-g<sha>` on the planning branch, so setuptools_scm
  derives `0.4.1.dev159+g<sha>` and stripping the local segment
  gives the monotonic `0.4.1.devN` scheme the master plan assumes.
* `rust/kerbside-proxy/Cargo.lock` exists and is the only
  Cargo.lock in the tree, so the `rust/**` path filter covers both
  Cargo files; no separate filter entries are needed.
* `rust/kerbside-proxy/pyproject.toml:31` is `dynamic =
  ["version"]` — the line decision 1 replaces at stamp time.
* `tools/build-proxy-wheel.sh` takes `x86_64`, `aarch64` or
  `--native`, writes wheels to `${WHEEL_OUT:-dist/proxy-wheels}`,
  and its prerequisites (maturin, ziglang, rustup) are installed by
  the caller — matching the release workflow's wheel matrix
  (`release.yml:98-109`), which this workflow mirrors.
* The release wheel matrix runs on `[self-hosted, vm, debian-12,
  xl]` (`release.yml:78`); no new runner class is required, and
  aarch64 is zig-cross-compiled so no aarch64 runner is needed.
* `RELEASE-SETUP.md` has a "One-Time Setup Steps" section (line 17)
  where the dev-release environment and trusted publisher
  registration steps belong.
* `.github/workflows/rust.yml:12-22` models the path set (`rust/**`
  plus `kerbside/rpc/kerbside.proto` plus the workflow file).
* Branch/PR structure precedent: both prior master plans landed
  with their phase 1 plan stacked as consecutive commits on the
  same branch (sfui-conversion: 88c8ba7 then c998858; two-tier-ci:
  14f4f80 then 9c2d46e). This plan follows that precedent — see
  decision 2.

Since nothing was false, no corrections to the master plan's phase
section or `index.md` description were needed.

## Decisions

1. **Wheel dev versions come from a static `[project] version` in
   the crate's pyproject.toml, stamped by a new
   `tools/stamp-dev-proxy-version.sh`, not from Cargo semver
   pre-release translation.** The script derives the version via
   setuptools_scm, strips the local segment (`0.4.1.dev159+g<sha>`
   → `0.4.1.dev159`), and replaces `dynamic = ["version"]` in
   `rust/kerbside-proxy/pyproject.toml` with `version =
   "0.4.1.dev159"`. Reasoning: the version is then exact PEP 440
   by construction, with no reliance on maturin's semver→PEP 440
   translation of a `0.4.1-dev.159` Cargo pre-release; Cargo.toml
   is untouched (its `0.1.0` placeholder stays); and the release
   path (`stamp-proxy-version.sh`, which stamps Cargo.toml) is
   entirely unaffected. **This is the decision a reviewer is most
   likely to argue with**, because it assumes maturin prefers a
   static `[project] version` over the Cargo version when
   `dynamic` is absent. That assumption is verified before
   anything reaches PyPI: step 1a's done-check builds a wheel in
   Docker and asserts the filename carries the dev version.
2. **The phase plan and implementation stack on the existing
   `proxy-dev-releases` branch and worktree** instead of a new
   worktree cut from develop. The master plan commit (bdcd8dc)
   exists only on this branch, so a develop-cut worktree would not
   contain the plan this phase executes; stacking matches the
   repository's established first-phase precedent (survey, last
   bullet). Later phases, planned after this branch merges, should
   revert to per-phase branches (`proxy-dev-releases-phase-NN-*`).
3. **Workflow shape**: two jobs. `build` is a `[x86_64, aarch64]`
   matrix on `[self-hosted, vm, debian-12, xl]` with
   `fail-fast: false`, mirroring `release.yml`: checkout with
   `fetch-depth: 0`, install maturin+ziglang in a venv, run
   `tools/stamp-dev-proxy-version.sh`, run
   `tools/build-proxy-wheel.sh <arch>`, upload the wheel artifact.
   `publish` downloads both artifacts, generates build provenance
   attestations, and publishes with
   `pypa/gh-action-pypi-publish@release/v1` and
   `skip-existing: true`.
4. **Triggers**: `push` to develop filtered to the decision-7
   paths, and `workflow_dispatch` with a `dry_run` boolean input
   **defaulting to true**. A dry run builds both wheels but skips
   the publish job, so workflow testing can never publish by
   accident; the bootstrap publish is an explicit
   `dry_run: false` dispatch. There is deliberately NO
   `pull_request` trigger: PR-triggered runs would execute
   untrusted forks' code in a workflow whose identity a PyPI
   trusted publisher accepts. (The publish job's environment gate
   also protects this, but not having the trigger is the simpler
   invariant.)
5. **Trust boundaries**: only the `publish` job runs in the
   `dev-release` environment and only it gets `id-token: write` +
   `attestations: write`; build jobs run with `contents: read`.
   The `dev-release` environment has no required reviewers but is
   restricted to the develop branch, so a compromised feature
   branch cannot dispatch a publishing run.
6. **Concurrency**: group `dev-proxy-wheel`,
   `cancel-in-progress: false`, so racing merges serialise.
   Versions are per-commit distinct (commit-count-based), and
   `skip-existing: true` makes any replay idempotent.
7. **Path filter** (push trigger): `rust/**`,
   `kerbside/rpc/kerbside.proto`, `tools/build-proxy-wheel.sh`,
   `tools/stamp-dev-proxy-version.sh`, `tools/gen-protos.sh`
   (added for symmetry during PR #314 review),
   `.github/workflows/dev-proxy-wheel.yml`. This is the master
   plan's list plus the scripts whose changes alter the built
   artifact or the publish behaviour. (Superseded in phase 5:
   `rust/kerbside-proxy/Cargo.lock` was later excluded from
   `rust/**`, because lockfile-only bumps were 18 of the 42
   measured triggers and cannot change the binary's interface.
   The workflow file itself is the authority on the current
   filter.)
8. **Operator setup happens before merge**: the `dev-release`
   environment (no reviewers, develop-branch-restricted) and the
   PyPI trusted publisher for `dev-proxy-wheel.yml` +
   `dev-release` environment on the `kerbside-proxy` project are
   created while the PR is in review, so the first real triggering
   merge publishes rather than failing. If setup slips, the
   failure mode is a loud red run, not silence.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Write `tools/stamp-dev-proxy-version.sh` modelled on `tools/stamp-proxy-version.sh` (same header-comment style, `set -euo pipefail`, repo-root derivation). It must: derive the version with `python3 -m setuptools_scm` (document that the caller needs setuptools_scm importable and full git history), strip the PEP 440 local segment (`${version%%+*}`), validate the result matches `^[0-9]+\.[0-9]+\.[0-9]+\.dev[0-9]+$` (refuse final release versions — this tool is the dev-side mirror of stamp-proxy-version.sh's refusal of dev versions), and replace the `dynamic = ["version"]` line in `rust/kerbside-proxy/pyproject.toml` with `version = "<ver>"`, erroring if the line is absent (already-stamped trees must fail loudly, not double-stamp). Also accept an explicit version as `$1` for testability. shellcheck (via pre-commit) must pass. |
| 1b | high | opus | none | Write `.github/workflows/dev-proxy-wheel.yml` implementing decisions 3-7 exactly. Mirror `release.yml`'s build-proxy-wheels job for the build matrix (checkout `fetch-depth: 0`, venv with maturin+ziglang, `dtolnay/rust-toolchain@stable`, apt prerequisites) plus `pip install setuptools_scm` for the stamp step, and its publish jobs for attestation + `pypa/gh-action-pypi-publish@release/v1` with `skip-existing: true`. Header comment must state: why there is no pull_request trigger (untrusted code vs trusted publisher identity), why dev wheels are attested but not tag-signed (no tag exists), and that the `dev-release` environment + trusted publisher are documented in RELEASE-SETUP.md. The publish job condition: run when the event is a push, or a dispatch with `dry_run == 'false'` (dispatch inputs are strings). actionlint (via pre-commit) must pass. |
| 1c | medium | sonnet | none | Update RELEASE-SETUP.md "One-Time Setup Steps": a new subsection for dev releases covering creating the `dev-release` GitHub environment (no required reviewers, deployment branch restricted to develop) and registering a second trusted publisher on the PyPI `kerbside-proxy` project (workflow `dev-proxy-wheel.yml`, environment `dev-release`), and extend "How Releases Work" with a short "Dev releases" paragraph (when they publish, version scheme, attestation-but-no-tag-signing). Update AGENTS.md's release/build notes to mention the dev release lane and `tools/stamp-dev-proxy-version.sh`. Keep both documents' existing tone and heading style. |
| 1d | medium | sonnet | none | Verification, pre-merge, per the operator's no-native-Rust preference: inside the official `rust` Docker image (mounting the repo, matching the documented local-build pattern), pip-install maturin + setuptools_scm in a venv, run `tools/stamp-dev-proxy-version.sh`, then `tools/build-proxy-wheel.sh --native`, and assert the produced wheel filename matches `kerbside_proxy-[0-9.]*\.dev[0-9]*-py3-none-.*\.whl`. Report the exact filename. Then `git checkout -- rust/kerbside-proxy/pyproject.toml` to unstamp. This is the check that retires decision 1's maturin-precedence risk before any PyPI interaction. |

Each step is its own commit (1d amends nothing — it is evidence,
recorded in the PR description, not a tree change).

## Risks and mitigations

* **Maturin ignores or rejects a static `[project] version` for
  bin bindings.** Retired by step 1d before merge (management
  session checks the reported wheel filename). Fallback if it
  fails: stamp a Cargo semver pre-release (`X.Y.Z-dev.N`) instead
  and verify maturin's translation the same way; the workflow
  interface does not change either way.
* **A newly added workflow may not be dispatchable until it exists
  on the default branch** (GitHub limitation). Mitigation: the
  dry-run dispatch happens after merge; pre-merge confidence comes
  from step 1d plus actionlint. The bootstrap publish was always
  going to be post-merge.
* **Trusted publisher not registered when the first push trigger
  fires.** Mitigated by decision 8 (setup during PR review); the
  residual failure mode is a red publish job, which
  `skip-existing`-safe re-running (or a dispatch) recovers once
  registration lands. The management session checks the first
  triggered run.
* **Publishing runs on self-hosted runners.** Already the accepted
  posture for real releases (`release.yml` publishes from
  `[self-hosted, static]` with the same OIDC action); dev wheels
  add no new runner exposure. Noted for the reviewer rather than
  mitigated further.
* **Two merges racing to publish.** Serialised by the concurrency
  group; distinct versions per commit; `skip-existing` makes
  replays harmless.

## Definition of done

* `tools/stamp-dev-proxy-version.sh` exists; `pre-commit run
  --all-files` passes (shellcheck, actionlint included).
* Running the stamp script twice in a row fails the second time
  with a message naming the already-stamped line.
* Step 1d's dockerized build produced a wheel whose filename
  matches `kerbside_proxy-*.dev*-py3-none-*.whl`, and the filename
  is recorded in the PR description.
* `.github/workflows/dev-proxy-wheel.yml`'s push paths are
  exactly the decision-7 list (`git diff`-able against this plan),
  it has no `pull_request` trigger, and only the publish job
  references the `dev-release` environment.
* RELEASE-SETUP.md documents the `dev-release` environment and the
  second trusted publisher; AGENTS.md mentions the dev release
  lane.
* Post-merge, in order: a `dry_run: true` dispatch builds both
  wheels and skips publish; a `dry_run: false` dispatch (the
  bootstrap) publishes, after which `pip index versions
  kerbside-proxy --pre` lists a `0.4.1.devN` version; and the
  next Python-only merge to develop does NOT trigger the workflow
  (`gh run list --workflow=dev-proxy-wheel.yml` shows no run for
  that merge SHA).
* The master plan's Execution table and `docs/plans/index.md`
  reflect this phase as planned (done in the planning commit).

## Back brief

Before executing any step of this plan, back brief the operator on
the plan and how the intended work aligns with it. Additional
gate: after step 1b lands in review, pause for the operator to
perform the decision-8 one-time setup (GitHub environment + PyPI
trusted publisher) before merge; steps 1c/1d do not depend on that
setup and may proceed meanwhile.
