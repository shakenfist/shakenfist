# Phase 8: OpenStack CI lane disposition + oVirt provisioning flake

Part of [PLAN-test-harness.md](/components/kerbside/plans/PLAN-test-harness/). The disposition
decision and documentation land in kerbside; the root-cause fix lands
in `shakenfist/actions`. Per the master plan's single-home rule, the
plan file lives here in `docs/plans/`.

## Goal

The master plan framed phase 8 as deciding the fate of the heavyweight
"Test cloud compatibility" CI lane (oVirt + kolla-OpenStack) now that
the direct-qemu lane covers the spine. That decision is now made (see
Decisions): **keep the lane per-PR, both legs blocking, for now.**

With the disposition settled as status quo, the substance of phase 8
becomes making that per-PR gate *trustworthy*, because a gate the team
merges past is worse than no gate. Two concrete problems block that:

1. **The oVirt leg has a real, intermittent provisioning flake.** On
   `pull_request` runs (the actual merge gate) the oVirt leg failed
   genuinely on 2026-06-11 with `ssh: connect to host 10.0.2.2 port 22:
   Connection refused` immediately after the provisioning playbook's
   SSH wait had already passed. Root cause confirmed against the code
   (see Situation): the wait gate checks only that port 22 is listening
   and presents an OpenSSH banner — it does not wait for cloud-init to
   finish, so sshd's host-key-regen restart (or a not-yet-ready guest
   on a busy hypervisor) drops the next real SSH. This is the bug that
   already trained us to merge past red once (the phase 6→7 smoke-client
   version-pin failure merged on a red lane).

2. **The lane shows spurious oVirt red on `workflow_dispatch` runs.**
   Manual/develop-branch dispatches default `target` to kolla-only, and
   the oVirt job's "Filter workflow_dispatch runs" step calls
   `core.setFailed('Target skipped')` — marking the deliberately-skipped
   target as a *failure* rather than a clean skip. Its `always()`
   artifact step then times out SSHing a guest that was never
   provisioned. This is the cosmetic red that made the lane look broken
   on develop and obscured the real PR-gate health.

This phase is scope-bounded to:

- A root-cause fix to the oVirt (and kolla — same playbook) provisioning
  readiness gate in `shakenfist/actions`.
- A GitHub issue in `shakenfist/actions` tracking the flake and its
  diagnosis (none exists today; the plan template now asks us to file
  one).
- A workflow fix in kerbside so unselected `workflow_dispatch` targets
  skip cleanly instead of reporting red.
- A documented disposition record (keep per-PR; the criteria under which
  we would later demote to a schedule) and the master-plan status bump.

Out of scope for phase 8:

- **Demoting the lane to a schedule.** Explicitly decided against for
  now (see Decisions). The revisit criteria are recorded so a future
  phase can pick it up.
- **Retiring the oVirt or kolla leg.** Both stay per-PR and blocking.
- **Any change to the direct-qemu lane.** It is green and untouched.
- **A second runner shape or a CI matrix expansion.**
- **Broader `shakenfist/actions` provisioning rework.** The fix is
  surgical: the readiness gate, nothing else in the provisioning flow.
- **Deduplicating the oVirt vs kolla provisioning paths.** The kolla
  leg provisions via `setup-kerbside-environment` and the oVirt leg via
  a direct `kerbside-single-node.yml` invocation; both share the same
  wait gate, so the fix covers both, but unifying the two entry points
  is separate work.

## Decisions baked into this plan

These are judgment calls made while drafting, surfaced explicitly so
they can be challenged before code lands.

- **Disposition: keep the cloud-compat lane per-PR, both legs blocking,
  for now.** The operator's call (2026-06-20): Kerbside PRs are frequent
  and getting Nova reliable with Kerbside is the primary focus, so the
  per-PR Nova coverage is worth its cost. A future demotion to a
  schedule is foreseeable but not yet. Phase 8 therefore makes no
  cadence change; it records the decision and the revisit criteria.
  *Superseded 2026-08-09 by two-tier CI phase 3
  ([PLAN-two-tier-ci-phase-03-merge-queue.md](/components/kerbside/plans/PLAN-two-tier-ci-phase-03-merge-queue/)):
  the cloud matrices moved to the merge-queue tier. The half of this
  disposition that says both legs stay blocking survives — they now
  block merges via the queue rather than PRs — but the per-PR cadence
  does not, for a different reason than the revisit criteria below
  anticipated (tier split, not flake or cost).*
- **Revisit-to-schedule criteria (for a future phase, not now):** demote
  the heavy legs to nightly + `workflow_dispatch` when any of — the
  Nova+Kerbside integration has been stable for a sustained period and
  the per-PR signal stops catching regressions; PR volume drops enough
  that per-PR cost outweighs value; or runner capacity becomes a binding
  constraint. Recorded in the master plan's Future work.
- **Root-cause the flake rather than route around it.** The operator
  chose this over making oVirt non-blocking or accepting the red. The
  diagnosis is confirmed and the fix is small, so this is the right
  trade: fix the actual bug, keep the gate honest.
- **The fix is `wait_for_connection` + `cloud-init status --wait`, not a
  bigger `wait_for` timeout.** Bumping the existing `wait_for` timeout
  would not help: the wait already *passes* (the banner is present) and
  the failure happens *after* it, when sshd bounces during cloud-init.
  The correct gate waits for cloud-init to actually finish. See the
  fix sketch in the Execution table.
- **Keep the cheap banner `wait_for` as an early gate; add the real
  readiness gate after it.** The existing `wait_for port:22
  search_regex:OpenSSH` is harmless and gives a fast early failure if
  the guest never boots at all. Rather than replace it, add a follow-on
  readiness play targeting the provisioned hosts. Minimal blast radius.
- **The cosmetic target-skip becomes a job-level `if:`, not a
  `setFailed` step.** Replacing `core.setFailed('Target skipped')` with
  a job-level condition (`github.event_name != 'workflow_dispatch' ||
  contains(inputs.target, matrix.test.name)`) makes an unselected target
  *skip* (neutral grey) instead of *fail* (red). The `always()` artifact
  step then never runs against a non-existent guest.
- **Verification of the provisioning fix is CI-based and may need
  iteration.** The SF fabric provisioning cannot be exercised locally,
  and the flake is intermittent (~10% on the pre-fix gate), so a single
  green run does not prove the fix. Confidence comes from the mechanism
  (`cloud-init status --wait` closes the exact window that was failing)
  plus several consecutive green oVirt runs. Like phase 5, CI iteration
  is part of finishing the step, not a new phase.
- **Fable is not used in this phase.** Phase 7 was the deliberate Fable
  experiment. Phase 8's steps are a well-understood ansible fix, a small
  workflow edit, and documentation; opus and sonnet cover them. Noted so
  the model choice is a decision, not an omission.

## Situation

### What the cloud-compat lane is, today

`.github/workflows/functional-tests.yml` ("Test cloud compatibility")
runs three jobs, triggered on `pull_request` to develop and on
`workflow_dispatch`:

- `sanity_checks` — lint, unit tests, coverage. Fast, reliable.
- `ovirt_matrix` ("oVirt 4.5 on Rocky 8") — provisions a Rocky 8 guest
  on the SF fabric, installs ovirt-engine, creates a SPICE test target,
  and checks console connectivity.
- `openstack_matrix` ("OpenStack via Kolla-Ansible master on Debian 12")
  — provisions a guest, builds kolla images, deploys all-in-one
  OpenStack, and runs the kerbside tempest plugin.

The kolla leg was red until `shakenfist/kerbside-patches` PR #1306
merged (2026-06-12); since then it has been green on every `pull_request`
run. The oVirt leg is the remaining trouble.

### Measured lane health (as of 2026-06-20)

Distinguishing trigger type matters and was initially missed:

- On **`pull_request`** runs (the real merge gate), oVirt was green on
  roughly 12 of the last ~14 runs, with two genuine failures on
  2026-06-11 and none since 2026-06-13.
- On **`workflow_dispatch`** runs (manual, develop branch), the oVirt
  job is reported as *failed* on every run — but cosmetically: those
  dispatches default `target` to kolla-only, and the oVirt job's filter
  step calls `core.setFailed('Target skipped')`. This is not the infra
  flake; it is the workflow marking a deliberately-skipped target as a
  failure.

So the genuine flake is real but low-rate (~10% on the gate, none in the
last week), and the dramatic "always red on develop" appearance was the
cosmetic target-skip.

### Root cause of the genuine oVirt flake (confirmed)

The provisioning playbook `shakenfist/actions`
`ansible/kerbside-single-node.yml` ends with:

```yaml
- name: Wait for all instances to present an "OpenSSH" prompt
  wait_for:
    port: 22
    host: "{{hostvars[item]['ansible_ssh_host']}}"
    search_regex: OpenSSH
    delay: 60
    timeout: 300
  with_items: "{{ groups['allsf'] }}"
```

This waits only until port 22 is open and emits an OpenSSH banner. It
does **not** authenticate and does **not** wait for cloud-init to
finish. The instance is created (in `kerbside-create-instance.yml`) with
`await: true` (SF agent up), added to the `allsf` group with
`ansible_ssh_user: cloud-user`, and the runner then SSHes to it directly
from the kerbside workflow ("Prepare /srv on target" and later steps).

Primary evidence, oVirt job on run 27335927162 (2026-06-11):

- `10:10:08  TASK [Wait for all instances to present an "OpenSSH" prompt]`
- the wait **passed** (the playbook step "Build infrastructure" did not
  fail; only later steps did, and the timing is far too early for a
  300 s wait timeout)
- `10:11:56  ssh: connect to host 10.0.2.2 port 22: Connection refused`
  at the next workflow step ("Prepare /srv on target")

"Connection **refused**" (not "timed out") means the host was routable
but nothing was listening on port 22 at that instant — sshd had bounced.
The signature is cloud-init: sshd starts and presents a banner early
(the `wait_for` passes), then cloud-init regenerates host keys and
restarts sshd, and the runner's next real SSH lands in that window. On a
busier hypervisor the gap between "banner present" and "cloud-init done"
widens, which is why the failure rate tracks fabric load.

### The cosmetic target-skip

Both `ovirt_matrix` and `openstack_matrix` begin with:

```yaml
- name: Filter workflow_dispatch runs
  if: github.event_name == 'workflow_dispatch' && ! contains(inputs.target, matrix.test.name)
  uses: actions/github-script@v7
  with:
    script: |
        core.setFailed('Target skipped')
```

On a `workflow_dispatch` run with `target` set to one matrix entry, the
*other* job's filter step fails the job. The downstream `Gather
artifacts` / `Collect logs` steps run `if: always()` and then time out
SSHing a guest that was never created. The whole job shows red for a
target that was simply not selected.

### Bug tracker scan (per template guidance)

- `shakenfist/kerbside` open issues: none relate to the cloud-compat
  lane, oVirt, or CI flakiness (the open set is workflow-standards #59,
  security-settings #58, ryll-decoupling #15, install-guide #3).
- `shakenfist/actions` open issues: none match ssh / cloud-init / wait /
  flake / provisioning.

So no existing issue tracks this; phase 8 files one (step 8a).

## Mission and problem statement

After phase 8:

- `shakenfist/actions` `ansible/kerbside-single-node.yml` gates instance
  readiness on cloud-init completion, not just an open SSH port. The
  oVirt (and kolla) leg no longer fails with post-wait "connection
  refused". Confirmed by several consecutive green oVirt `pull_request`
  runs.
- A `shakenfist/actions` issue records the diagnosis and links the fix.
- `kerbside` `.github/workflows/functional-tests.yml` skips unselected
  `workflow_dispatch` targets cleanly (neutral), so manual/develop runs
  no longer show spurious oVirt red.
- The master plan carries the disposition decision (keep per-PR; revisit
  criteria) and the phase 8 row reads "Implementation complete; PR
  pending operator".
- `pre-commit run --all-files` and `actionlint` clean on the kerbside
  commits; `ansible-lint` / `yamllint` clean (as the actions repo
  requires) on the provisioning commit.

## Open questions

These do not block writing this plan but must be resolved during
implementation:

- **Is `cloud-init` present and is `cloud-init status --wait` available
  to `cloud-user` on both base images** (Rocky 8 for oVirt, Debian 12
  for kolla)? Expected yes on standard cloud images; verify in step 8b,
  and if `--wait` needs root, run it with `become: true`.
- **Does `wait_for_connection` work against the `allsf` hosts as added
  to the inventory** (it uses the `ansible_ssh_*` facts set in
  `kerbside-create-instance.yml`)? Expected yes; confirm the new
  readiness play targets `hosts: allsf` and inherits those connection
  vars.
- **Should the kolla leg's `setup-kerbside-environment` path also be
  audited?** The kolla leg provisions through that composite action, not
  a bare `kerbside-single-node.yml` call. Confirm whether it routes
  through the same playbook (and therefore the same wait gate) or has
  its own wait; if separate, the fix may need mirroring. Resolve early
  in step 8b.
- **How many consecutive green oVirt runs constitute "fixed"** given the
  ~10% pre-fix rate? Provisional bar: the mechanism plus ≥3 consecutive
  green oVirt `pull_request` runs. The operator may want more.

## Execution

Each step is one logical change. Kerbside commits land on the branch
`test-harness-phase-8` (this plan file travels with them). The
`shakenfist/actions` fix lands as its own commit in that repo; per the
master plan, the two repos do not share git operations. The operator
opens all PRs.

| Step | Repo | Effort | Model | Isolation | Brief for sub-agent |
|------|------|--------|-------|-----------|---------------------|
| 8a. File the tracking issue | shakenfist/actions | low | (management session) | none | Not a sub-agent coding task — the management session files a GitHub issue on `shakenfist/actions` titled for the oVirt provisioning readiness flake. Body: the confirmed diagnosis (the `wait_for port:22 search_regex:OpenSSH` gate in `ansible/kerbside-single-node.yml` returns as soon as sshd presents a banner, before cloud-init finishes; sshd's host-key-regen restart then drops the next real SSH with "connection refused"; rate tracks hypervisor load). Cite the 2026-06-11 evidence (run 27335927162: wait passed at 10:10:08, "connection refused" at 10:11:56 on the next step). State the intended fix (cloud-init readiness gate) so the issue and the fix PR cross-link. Mark it found during kerbside test-harness phase 8. |
| 8b. Fix the provisioning readiness gate | shakenfist/actions | medium | opus | worktree | Requires a `shakenfist/actions` checkout (the operator must confirm one is available; clone if not). In `ansible/kerbside-single-node.yml`, after the existing `Wait for all instances to present an "OpenSSH" prompt` task, add a readiness gate that waits for cloud-init to finish, not just for the SSH port. Recommended shape — a follow-on play (or delegated tasks) targeting `hosts: allsf` so it uses the per-instance `ansible_ssh_host` / `ansible_ssh_user: cloud-user` / key facts set in `kerbside-create-instance.yml`: `wait_for_connection: {delay: 0, timeout: 300}` to establish a real authenticated SSH connection (retries through an sshd bounce), then `command: cloud-init status --wait` (`changed_when: false`, `become: true` if `--wait` needs root on the image) to block until cloud-init is done. Keep the existing `wait_for` banner check as the cheap early gate. First, verify the open questions: that the kolla leg (`setup-kerbside-environment`) routes through the same playbook/gate (mirror the fix if not), that `cloud-init` is present on both Rocky 8 and Debian 12 base images, and that `cloud-init status --wait` is available to the SSH user. Do NOT broaden scope to other provisioning tasks. Verify locally with `ansible-lint` and `yamllint` (or whatever the actions repo's pre-commit runs) and a `--syntax-check`; note in the commit body that full verification is CI-only (the SF fabric cannot be exercised locally) and that the real proof is consecutive green oVirt runs. One commit. The operator opens the actions PR; expect to watch several oVirt runs before declaring it fixed. |
| 8c. Fix the cosmetic target-skip false-red | kerbside | low | sonnet | none | On `test-harness-phase-8`. In `.github/workflows/functional-tests.yml`, for BOTH the `ovirt_matrix` and `openstack_matrix` jobs: remove the "Filter workflow_dispatch runs" step (the `core.setFailed('Target skipped')` github-script step) and instead add a job-level condition so unselected `workflow_dispatch` targets skip cleanly: `if: github.event_name != 'workflow_dispatch' || contains(inputs.target, matrix.test.name)`. This makes a non-selected target neutral-skip (grey) rather than red, and stops the `always()` artifact/log steps from running against a guest that was never provisioned. Confirm the jobs still run on every `pull_request` (the condition is true when the event is not `workflow_dispatch`) and that a `workflow_dispatch` with a given `target` runs only the matching job. Verify with `actionlint` and `pre-commit run --all-files`. One commit. |
| 8d. Disposition record + docs + status | kerbside | low | sonnet | none | On `test-harness-phase-8`. Update `docs/plans/PLAN-test-harness.md`: set the phase 8 row status to "Implementation complete; PR pending operator"; in Future work, record the disposition decision (keep the cloud-compat lane per-PR, both legs blocking, for now) and the revisit-to-schedule criteria (Nova+Kerbside integration stable and per-PR signal no longer catching regressions; PR volume drops; or runner capacity becomes constraining); add the two bugs to the master plan's "Bugs fixed during this work" (the oVirt readiness-gate flake fixed in shakenfist/actions, and the cosmetic target-skip false-red). If `README.md` / `AGENTS.md` / `ARCHITECTURE.md` describe the cloud-compat lane's reliability or trigger behaviour, add a one-line note that unselected `workflow_dispatch` targets skip cleanly and that instance readiness now gates on cloud-init. `pre-commit run --all-files` clean. One commit. |

### Sequencing notes

- 8a (file the issue) first, so 8b's fix PR can reference it.
- 8b is the long pole: the real fix, cross-repo, CI-verified, likely
  several oVirt runs before it is trusted. It is independent of 8c/8d
  and can proceed in parallel with them.
- 8c and 8d are small kerbside-side changes; they can land before 8b is
  confirmed green, since they do not depend on the provisioning fix.
- The operator opens the `shakenfist/actions` PR (8b) and the kerbside
  PR (8c+8d). The phase is "done" once the actions fix is merged and the
  oVirt leg has been green across several consecutive `pull_request`
  runs.

### Branch and PR shape

- **kerbside**: new branch `test-harness-phase-8` from `develop`. Steps
  8c and 8d land here, plus this plan file. One PR.
- **shakenfist/actions**: a single commit on its own branch for step 8b.
  Separate PR. The plan file stays in kerbside per the single-home rule.

## Agent guidance

This phase plan follows the conventions in `PLAN-TEMPLATE.md` at the
kerbside repo root. The execution model, effort levels, brief-writing
standards, and management-session review checklist apply unchanged.

Notes specific to phase 8:

- **The fix target is `shakenfist/actions`, a third repo.** As with the
  ryll phases, the management session must confirm the operator has that
  repo checked out and brief the sub-agent that the plan lives in
  `shakenfist/kerbside/docs/plans/` even though the commit lands
  elsewhere. Use a worktree for 8b so the experimental ansible change is
  isolated.
- **Do not "fix" the flake by enlarging the existing `wait_for`
  timeout.** The wait already passes; the failure is after it. Anyone
  reaching for a bigger timeout has misread the root cause — point them
  at the Situation section.
- **The provisioning fix cannot be proven locally.** The SF fabric is
  not reproducible on the dev host. Verification is `ansible-lint` /
  syntax-check plus CI observation across several runs. Resist any
  sub-agent claiming the fix is "verified" off a single green run, given
  the ~10% pre-fix rate.
- **Keep the cosmetic and the real fix in separate commits and repos.**
  The target-skip edit (kerbside) and the readiness gate (actions) are
  unrelated causes that happened to both show as "oVirt red"; do not
  conflate them.
- **Do not change the lane's cadence.** The disposition is keep-per-PR.
  No `schedule:` trigger, no demotion. If a sub-agent proposes one,
  that is out of scope — it belongs to a future phase guided by the
  recorded revisit criteria.

### Back brief

Before executing any step of this plan, please back brief the operator
as to your understanding of the step and how the work you intend to do
aligns with that step's brief.

## Administration and logistics

### Success criteria

Phase 8 is done when:

- `shakenfist/actions` `ansible/kerbside-single-node.yml` gates on
  cloud-init completion (`wait_for_connection` + `cloud-init status
  --wait`, or equivalent) after the existing banner wait, and the change
  is `ansible-lint` / `yamllint` clean.
- A `shakenfist/actions` issue records the diagnosis and is linked from
  the fix PR.
- The oVirt `pull_request` leg is green across several consecutive runs
  after the fix merges (provisional bar: ≥3).
- `kerbside` `.github/workflows/functional-tests.yml` skips unselected
  `workflow_dispatch` targets cleanly; `actionlint` and `pre-commit run
  --all-files` are clean.
- The master plan records the disposition decision and revisit criteria,
  lists both fixed bugs, and marks the phase 8 row "Implementation
  complete; PR pending operator".

### Future work

Items deliberately deferred from phase 8:

- **Demote the heavy legs to a schedule.** Recorded in the master plan's
  Future work with its revisit criteria. Not now.
- **Unify the oVirt and kolla provisioning entry points.** They share
  the wait gate but enter through different paths
  (`kerbside-single-node.yml` directly vs `setup-kerbside-environment`);
  consolidating is separate work.
- **Retry wrappers on the kerbside workflow's direct SSH steps.** A
  belt-and-braces complement to the readiness gate. If the gate fix
  proves insufficient, add `ssh ... || retry` backoff to the
  "Prepare /srv on target" and sibling steps. Not needed if 8b closes
  the window.
- **Audit other `shakenfist/actions` playbooks for the same port-only
  wait pattern.** The multi-node playbooks likely share it.

### Bugs fixed during this work

- **oVirt provisioning readiness gate waited for an open
  SSH port, not cloud-init completion** (`shakenfist/actions`
  `kerbside-single-node.yml`). The `wait_for port:22
  search_regex:OpenSSH` task returned on the sshd banner;
  cloud-init then restarted sshd (host-key regen) and the
  runner's next real SSH was refused. Fixed by adding a
  `wait_for_connection` + `cloud-init status --wait`
  readiness gate (with `failed_when: false` so it waits
  without asserting cloud-init's exit code). Confirmed by
  inspection that the kolla all-in-one leg routes through
  the same playbook, so one fix covers both legs; the
  multi-node playbooks carry the same pattern and are
  logged as future work. Tracked as `shakenfist/actions`
  issue #2.
- **Cloud-compat lane reported unselected
  `workflow_dispatch` targets as red** (kerbside
  `functional-tests.yml`). The `core.setFailed('Target
  skipped')` filter step marked a deliberately-unselected
  matrix target as failed, and the `always()` artifact
  steps then timed out against a never-provisioned guest.
  Fixed by replacing the step with a job-level `if:` so
  unselected targets skip cleanly (the target name is a
  literal because the matrix context is unavailable in a
  job-level `if:`).
