# Phase 5: CI workflow MariaDB install — restoring green

Parent plan: [PLAN-byo-mariadb.md](PLAN-byo-mariadb.md).

## Prompt

Before responding, read these files so you understand
the current CI shape and what phase 4 broke:

- `.github/workflows/functional-tests.yml` — the
  pull-request and merge-queue functional-test
  workflows. Four `Run getsf installer on primary`
  steps live here: at approximately lines 352, 867,
  1397, 1616. Each one ssh-invokes
  `/tmp/getsf-wrapper` on the primary node.
- `.github/workflows/scheduled-tests.yml` — the
  scheduled-build workflow. One more
  `Run getsf installer on primary` step at
  approximately line 148.
- `tools/bootstrap-mariadb.sql` — the operator-applied
  SQL snippet shipped in phase 4 step 1. CI runs
  this snippet against an apt-installed MariaDB to
  satisfy the BYO contract.
- `examples/mariadb-tuning.cnf` — the SF-recommended
  drop-in. CI installs it for parity with the old
  bundled-install path.
- The `shakenfist/actions/setup-test-environment`
  action is a separate repo this workflow uses. The
  `/tmp/getsf-wrapper` script comes from there. We
  do **not** modify that repo in this phase; instead
  we pass `GETSF_MARIADB_*` env vars through the
  SSH invocation of the wrapper, which inherits
  them into getsf's prompt-suppression checks.

The point of this phase is narrow: restore green CI
that phase 4 deliberately broke. Phase 6 adds N>1
sf-database test coverage on top of the now-restored
single-instance pipeline.

One commit per step. Each commit must pass
`pre-commit run --all-files` (which includes
`actionlint` on the workflow YAML). Functional CI
will pass only after step 2 of this phase lands.

## Context

Phase 4 deleted `shakenfist/deploy/ansible/roles/mariadb/`
and stopped `deploy.py` from generating a MariaDB
password. The deployer now demands an operator-
provided MariaDB host and password; without them
`deploy.py` `SystemExit`s with a clear error message
pointing at `tools/bootstrap-mariadb.sql`.

The CI workflows currently:

1. Spin up VM(s) on the CI rig (the `Build infrastructure`
   step invokes ansible playbooks from
   `shakenfist/actions`).
2. Copy a `tools/` directory and a `getsf-wrapper`
   script to the primary VM.
3. SSH into the primary VM and run
   `/tmp/getsf-wrapper`, which non-interactively
   invokes `getsf`.

Step 3 fails after phase 4 because the wrapper does
not supply `GETSF_MARIADB_HOST` /
`GETSF_MARIADB_PASSWORD` (those env vars did not
exist before phase 4). `deploy.py` aborts. No
schema gets created. Every functional test fails
on the upgrade-of-test-env step.

Phase 5 fixes this by:

1. Shipping `tools/ci-install-mariadb.sh`, a small
   helper that apt-installs `mariadb-server`,
   applies `tools/bootstrap-mariadb.sql` with a
   given password, and drops in the recommended
   tuning. The script runs on the target box; CI
   scp-s it across to the primary VM and invokes
   it over SSH. (The script is also genuinely
   useful for developers spinning up a single-box
   dev deploy.)
2. Adding a `Install BYO MariaDB on primary`
   workflow step before each of the five
   `Run getsf installer on primary` sites, and
   updating each `Run getsf installer` step to
   pass `GETSF_MARIADB_HOST` / `GETSF_MARIADB_PORT`
   / `GETSF_MARIADB_USER` / `GETSF_MARIADB_PASSWORD`
   / `GETSF_MARIADB_DATABASE` env vars to the
   wrapper's SSH invocation.

After this phase lands:

- Functional CI is green again.
- The BYO contract is exercised end to end on every
  PR: the `bootstrap-mariadb.sql` snippet, the
  `getsf` prompts (consumed non-interactively via
  env vars), and the new operator-driven schema
  initialisation in `sf-ctl ensure-mariadb-schema`.
- A regression in any of those pieces shows up as a
  CI failure on the next PR rather than at an
  operator's deploy time.
- Phase 6 can now layer the N>1 sf-database CI
  shape on top of the working baseline.

## Decisions (phase-local)

1. **`tools/ci-install-mariadb.sh` runs on the
   target VM**, not on the GitHub Actions runner.
   The CI step SCPs the script + the SQL snippet
   + the tuning file to the primary VM and SSH-
   invokes it. Running on the target lets the same
   script support single-box dev installs without
   modification.

2. **The script takes three arguments**: a path to
   the SQL snippet, a path to the tuning file
   (optional — pass an empty string to skip), and
   the MariaDB password. It does not generate the
   password itself; the caller decides. CI passes
   a fixed string (`citestpw`); developers running
   it locally pass whatever they choose.

3. **Hardcoded CI password (`citestpw`).** CI is
   ephemeral; the database is reachable only inside
   the test VM. There is no value in randomising
   the password and no value in stashing it as a
   GitHub Actions secret. A fixed string keeps the
   workflow diff readable.

4. **The script installs the tuning .cnf by
   default.** The tuning matters for performance,
   and CI's functional tests stress sf-database
   enough that the difference is observable.
   Skipping the tuning is an opt-out, not an
   opt-in.

5. **Env vars are passed through the SSH `command`
   string** rather than via `ssh -o SendEnv` or
   similar. The `setup-test-environment` action's
   SSH-config is not under our control. Passing
   `GETSF_FOO=bar /tmp/getsf-wrapper` over the
   wire is the most portable shape: the remote
   shell sets the env var before invoking the
   wrapper, the wrapper inherits it, and getsf's
   `if [ -z "${GETSF_FOO}" ]; then ...` checks
   skip the prompt.

6. **Workflow YAML duplication is accepted.** Five
   invocation sites get the same new step inserted
   verbatim. GitHub Actions composite actions are
   the principled answer to this duplication, but
   introducing one for this single use case is
   yak-shaving that this phase does not justify.
   The duplicated YAML is fine.

7. **No changes to the external
   `shakenfist/actions` repository.** Touching
   that repo would couple this phase to a release
   in another tree. The phase fits cleanly in
   this repo by adding a new step before the
   existing wrapper invocation.

## Steps

Two sequential steps. Step 1 ships the helper
script; step 2 wires it into CI. The tree is
buildable after step 1 but CI is still red. CI
goes green after step 2.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | low | sonnet | none | Create `tools/ci-install-mariadb.sh`. Self-contained bash script that runs on a Debian/Ubuntu target. Takes three positional args: `$1` = path to bootstrap SQL snippet, `$2` = path to tuning .cnf (empty string to skip), `$3` = MariaDB password. Script body: (a) `apt-get update`; (b) retry-on-lock `apt-get install -y mariadb-server` (use the pattern from the deleted `shakenfist/deploy/ansible/roles/mariadb/tasks/bootstrap.yml`: retry until "Failed to lock apt" goes away or 100 retries elapse — a `while ! apt-get install -y mariadb-server; do sleep 5; done` is the simplest equivalent in bash); (c) wait for the systemd unit to be `is-active`; (d) `sed "s/__REPLACE_ME__/$3/" "$1" | mysql -u root` to apply the snippet; (e) if `$2` is non-empty: `cp "$2" /etc/mysql/mariadb.conf.d/` then `systemctl restart mariadb` then re-check `is-active`. Add a leading comment block explaining: the script's three args, that it runs on the target box (not the CI runner), that the same script works for single-box developer installs, that it expects the caller to have already SCPed the SQL and tuning paths next to it if running over SSH from CI. Add `set -euo pipefail` at the top for safer bash. Make the script executable (`chmod +x` in the same commit; git tracks the exec bit). Verify with `bash -n tools/ci-install-mariadb.sh` (syntax-check). `pre-commit run --all-files`. One commit. |
| 2 | medium | opus | worktree | Wire the new install step into every CI invocation of `getsf-wrapper`. Five sites total: `.github/workflows/functional-tests.yml` lines ~352, ~867, ~1397, ~1616 (PR localhost, merge localhost, slim-primary PR, slim-primary merge), and `.github/workflows/scheduled-tests.yml` line ~148. For each site, immediately BEFORE the existing `- name: Run getsf installer on primary` step, insert a new step structured like this (adjusting `matrix.pr` vs `matrix.merge` vs `matrix.os` to whatever the surrounding matrix variable is at that invocation site): ```yaml      - name: Install BYO MariaDB on primary        run: |          set -e          . ${GITHUB_WORKSPACE}/ci-environment.sh          scp -i /srv/github/id_ci -o StrictHostKeyChecking=no \              -o UserKnownHostsFile=/dev/null \              ${GITHUB_WORKSPACE}/tools/bootstrap-mariadb.sql \              ${GITHUB_WORKSPACE}/tools/ci-install-mariadb.sh \              ${GITHUB_WORKSPACE}/examples/mariadb-tuning.cnf \              ${{ matrix.pr.base_image_user }}@${primary}:/tmp/          ssh -i /srv/github/id_ci -o StrictHostKeyChecking=no \              -o UserKnownHostsFile=/dev/null \              ${{ matrix.pr.base_image_user }}@${primary} \              'sudo bash /tmp/ci-install-mariadb.sh \                   /tmp/bootstrap-mariadb.sql \                   /tmp/mariadb-tuning.cnf \                   citestpw' ``` Then update the existing `Run getsf installer on primary` step's SSH command. Change ```ssh ... ${user}@${primary} /tmp/getsf-wrapper``` to ```ssh ... ${user}@${primary} \    "GETSF_MARIADB_HOST=127.0.0.1 \     GETSF_MARIADB_PORT=3306 \     GETSF_MARIADB_USER=shakenfist \     GETSF_MARIADB_PASSWORD=citestpw \     GETSF_MARIADB_DATABASE=shakenfist \     /tmp/getsf-wrapper"``` at each of the five sites. Path resolution note: `${GITHUB_WORKSPACE}/tools/bootstrap-mariadb.sql` is in the SF repo's working copy on the runner (which is what `${GITHUB_WORKSPACE}` points to). The `actions/` subdir below `${GITHUB_WORKSPACE}` is the external `shakenfist/actions` repo — its `tools/` is a different `tools/` and is NOT the one we want; we want the SF repo's own `tools/`. Same for `examples/`. After making the changes: run `pre-commit run --all-files` (actionlint will catch yaml-syntax issues and undocumented step keys). If any of the five matrix-variable contexts is different (`matrix.merge` vs `matrix.pr` vs `matrix.os`), make sure each insertion uses the right one — copy-paste-and-rename is the failure mode. Worktree isolation because the workflow YAML is load-bearing for every PR; a typo breaks CI for everyone, not just this branch. One commit. |

## Validation

- `pre-commit run --all-files` passes after each step.
  Actionlint covers workflow YAML syntax; flake8 /
  unit-tests / mypy do not exercise CI files.
- After step 1: `bash -n tools/ci-install-mariadb.sh`
  passes; the script is executable; manual smoke
  test against a local Debian VM (if available)
  produces a usable MariaDB.
- After step 2: the next PR's functional-tests
  workflow run reaches green. CI was red between
  phase 4 landing and step 2 of this phase; that
  is expected.
- After step 2: the `Install BYO MariaDB on
  primary` step appears in the GitHub Actions
  log for the PR's functional-tests run,
  showing the apt-install, the SQL snippet
  application, and the tuning drop-in.
- The `Run getsf installer on primary` step's
  command line in the GitHub Actions log shows
  the five `GETSF_MARIADB_*` env vars set; the
  step completes without `deploy.py` aborting
  on missing-required-field.

## Risks

- **Matrix-variable mismatch across the five
  insertion sites.** Each site has its own matrix
  context (`matrix.pr`, `matrix.merge`,
  `matrix.os`). Copy-paste-and-forget-to-rename is
  the most likely failure mode for step 2. The
  brief tells the sub-agent to verify each
  insertion against the matrix variable used by
  its surrounding step.
- **The `getsf-wrapper` might unset or override
  env vars it inherits.** Phase plan assumes the
  wrapper passes through `GETSF_*` vars from its
  own environment to `getsf`. If the wrapper
  explicitly `unset`s or rewrites them, the new
  CI step's env-var passing has no effect and
  `deploy.py` still fails. Mitigation: the
  workflow log will show this on the first CI
  run after step 2; if it happens, the immediate
  fix is to modify the wrapper directly on the
  primary VM via an additional SSH step that
  appends `export GETSF_MARIADB_*` lines to
  `/tmp/getsf-wrapper` before invoking it. That
  fallback path is documented here so the
  management session can apply it without
  spawning another planning round.
- **apt lock contention during VM boot.** Cloud-
  init or unattended-upgrades may hold the apt
  lock when CI's install step runs. The brief
  for step 1 tells the sub-agent to use a
  retry-on-lock loop. If the loop doesn't help,
  a step-1 follow-up adds a pre-step that waits
  for cloud-init to finish (`cloud-init status
  --wait`).
- **Tuning .cnf parsing**: the SF tuning sets
  `innodb_buffer_pool_size = 1G`. CI's VM may
  have less than that available, in which case
  mariadb fails to start. The brief tells the
  sub-agent to verify the tuning install
  succeeds (the systemd `is-active` check after
  the restart catches this). If 1G is too
  aggressive for the CI VM, the tuning install
  becomes optional / skipped in CI, and a
  parallel issue gets filed for tuning-the-
  tuning. Phase 6's N>1 test rig may want a
  different tuning anyway.

## Out of scope

- N>1 sf-database CI shape and a functional test
  for the LB path — phase 6.
- Modifying the `shakenfist/actions` repo —
  explicitly deferred to keep the phase
  in-tree.
- Documentation about the CI install pattern
  beyond what `tools/ci-install-mariadb.sh`'s
  own comment block says — operator-facing
  docs were updated in phase 4 step 5 and
  point operators at the SQL snippet, which is
  the operator-facing artefact. The CI helper
  is a sibling.
- Cluster_ci local dev-rig tooling (if any
  exists under that name) — searched, none
  found in this repo as of phase 4 landing.
- `etcd_master` ansible group rename — PLAN-
  remove-primary phase 7.

## Back brief

Before executing this phase, please back brief
the operator on:

- The two steps and the file boundaries for each.
- The deliberate CI-red window between phase 4
  landing and step 2 of this phase landing.
  Confirm step 2 should land immediately after
  step 1.
- The decision to keep the helper script
  on-target (runnable for single-box dev installs
  too) rather than runner-only.
- The decision not to touch the external
  `shakenfist/actions` repo. The env-var
  passing happens via the SSH command string.
- The fallback path if the wrapper turns out to
  unset inherited env vars: append exports to
  `/tmp/getsf-wrapper` from a new SSH step.
- The CI password (`citestpw`) is hardcoded;
  CI is ephemeral and there is no value in
  randomising it or using a GitHub secret.
