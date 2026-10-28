# Deployment options

## Prompt

Before acting on this plan, read `docs/installation.md` (in
particular "What a running Kerbside needs" and "Deploying for
real"), the six pages under `docs/use-cases/`, the Use Cases
table in `docs/index.md`, `demo/`, and the CI deployment
scripts `tools/sf-e2e/deploy-kerbside.sh`,
`tools/ovirt-e2e/deploy-kerbside.sh` and
`tools/direct-qemu/start-kerbside.sh`. Cross-repository
references:

- `shakenfist/shakenfist`: the `kerbside` role at
  `shakenfist/deploy/collection/roles/kerbside/`, and
  `docs/plans/PLAN-kerbside-deployer.md`, which is building it.
- `shakenfist/kerbside-patches`: the Kolla and Kolla-Ansible
  patch set, and its `README.md` and `docs/`.
- The `spice-direct-consoles` topic on the OpenStack Gerrit.

The claims below were read on 2026-10-10. The Shaken Fist
deployer plan in particular is moving week to week, so
re-check its Execution table before acting on anything here
that depends on it. Ground every claim in the code rather
than in this document, and flag uncertainty rather than
guessing.

## Situation

Issue #546 asks for a deployment options section, organised
the way `docs/use-cases/` is. Writing that section starts from a
change of emphasis, recorded here because every page in this
plan follows from it.

### Use case and deployment mechanism are separate questions

The use-case pages answer *what Kerbside sits in front of*: a
Shaken Fist cluster, an OpenStack cloud, oVirt, static targets,
several of these at once (`multi-cloud.md`), or a population of
users (`placement.md`). They do not answer *how Kerbside gets
installed and run*. Today the docs merge the two questions.
`docs/installation.md`'s "Deploying for real" table is keyed by
cloud, and its OpenStack row answers "a sample Kolla-Ansible
deployment lives in kerbside-patches".

That merging works against Kerbside's most distinctive value
proposition. Once Kerbside is deployed by one cloud's deployer:

- its upgrade schedule follows that cloud;
- its configuration is shaped around that cloud's one source;
- adding a second cloud means a Kerbside that the first cloud's
  deployer owns has to learn about a cloud it knows nothing of.

Aggregation (`multi-cloud.md`) and placement (`placement.md`)
both assume a Kerbside deployed *in front of* the clouds rather
than *inside* one. That makes the front-door deployment the
default worth documenting. Even an operator with one cloud today
keeps the option of a second one without redeploying.

### What exists today

| Mechanism | State on 2026-10-10 | Where |
|-----------|---------------------|-------|
| By hand: pip, a venv, two processes | The pieces are documented; turning them into durable services is not. Kerbside ships no systemd units. (Since #580 the role, now in this repository, carries both units as Jinja templates; phase 2 found this and ships static copies.) | `docs/installation.md` "What a running Kerbside needs"; the CI scripts above are the nearest thing to a recipe |
| Shaken Fist's ansible collection | A `kerbside` role exists and is the most complete deployer Kerbside has: about 2000 lines, two systemd units, validation plays, and co-located or dedicated hosts. It writes exactly one `type: shakenfist` source (`templates/sources.yaml`). It also has a bring-your-own-Kerbside path: set `kerbside_url` with an empty `kerbside` group, and Shaken Fist is configured for a Kerbside it does not deploy. | `shakenfist/deploy/collection/roles/kerbside/`; being built by `PLAN-kerbside-deployer.md`, phases 1 to 3 merged, phase 4 open as shakenfist#4479 |
| Kolla-Ansible | The Kolla image build merged upstream (kolla 975495). The deployment (kolla-ansible 976889) and four CI and enablement changes are still open. | `kerbside-patches`; `docs/use-cases/openstack.md` "Deployment" |
| Docker compose | Demo only, and labelled that way. Builds its image locally. | `demo/`; `installation.md` "Try it: the demo stack" |
| A first-party container image | None. Kolla publishes `kerbside-api` and `kerbside-proxy` images on quay.io (`openstack.kolla`), built from Kerbside's `develop` branch rather than a release, and started through Kolla's `config.json` and `kolla_start` conventions. They are the only published Kerbside images. (Phase 4's survey corrected this row, which had not said where they are published or what they are built from.) | - |
| Kubernetes / Helm | Nothing. | - |

### The Kolla-Ansible patch set changes role

A lot of effort has gone into upstreaming the Kolla-Ansible
deployment, and that effort has not been met by review effort
upstream. Given the framing above, Kolla-Ansible is one
deployment option among several, and not the one most operators
should reach for. The patch set will be kept alive by regular
rebasing in `kerbside-patches`, and active upstream pushing
stops.

The patch set holds two different kinds of change, and the
split matters:

- **OpenStack-side enablement**: the SPICE options (967800 and
  967802, merged), the routable console address `spice-direct`
  needs (967801, open), and the Keystone service account
  Kerbside validates tokens with (part of 976889). These are
  needed *whenever* Kerbside fronts an OpenStack cloud, however
  Kerbside itself is deployed.
- **Deploying Kerbside**: 976889 and the CI changes built on it
  (988189, 988913, 989614). These are needed only when Kolla
  runs Kerbside as one of its own control-plane services.

The front-door story needs the first group and not the second.
Open question 1 asks whether that is the line between "keep
pushing gently" and "rebase only".

"Kept alive by rebase" has a running cost that should be
explicit. Kerbside's Kolla lane deploys from `kerbside-patches`
(`.github/workflows/functional-tests.yml`, `openstack_matrix`), so
a stale rebase breaks it. When this plan was written the lane ran
in the merge queue, where that would have blocked Kerbside's own
merges. Since #605 (2026-10-25) it runs nightly, and a stale
rebase files a nightly-failure issue instead; phase 3's survey
corrected this. The lane itself was reliable: phase 1 counted 32
successes and no failures across the 40 most recent merge runs;
the other 8 never started the job.

### Gaps the pages will expose

These problems exist whatever the deployment mechanism, but a
deployment page cannot honestly avoid them:

- **#300: login is Keystone-only.** A Kerbside in front of
  anything other than OpenStack has nowhere to send an
  interactive login.
- **No CI lane runs two Kerbsides** (`placement.md`), so
  redundancy and per-site placement are untested.
- **No CI lane runs two sources** (`multi-cloud.md`), so the
  front-door deployment this plan recommends is itself untested
  end to end.

## Mission and problem statement

A `docs/deployment/` section with one page per deployment
mechanism, a `### Deployment Options` table in `docs/index.md`
showing which sources each mechanism configures and what CI
proves, and an `installation.md` that hands off to that section
instead of indexing deployment by cloud.

Each page follows one structure, mirroring the use-case pages:

1. **Who this is for**: the operator this mechanism suits, and
   the ones it does not.
2. **What it deploys**: processes, units or containers,
   database, TLS material, sources, and what it leaves to the
   operator.
3. **How to use it**: the steps, or a pointer to where the
   steps live when another repository owns them. Link, do not
   duplicate.
4. **Status and limitations**: a table of what is not proven,
   with each row saying why, as the use-case pages do.

This is a documentation plan. Engineering gaps it finds are
filed as issues or recorded against the plan that owns them.
They are not absorbed here: a docs spike that quietly becomes a
deployer project finishes neither. One exception is open, which
is shipping example systemd units (open question 3).

### Out of scope, and where it lives instead

- **Moving the `kerbside` role into Kerbside.** It belongs to
  `PLAN-kerbside-deployer.md` in shakenfist/shakenfist, which is
  still building the role. Accepted there as a rewritten phase
  5; see "Dependencies on other plans".
- **Publishing a first-party container image.** Filed in phase
  1 as #553. Compose-for-production and Kubernetes both
  depend on it, so a Helm chart is not written ahead of it.
- **Upstream Kolla-Ansible work.** Tracked in `kerbside-patches`.
  The enablement / deployment split from question 1 is recorded
  there as kerbside-patches#1859.

## Open questions

### 1. Where is the line on upstream Kolla-Ansible work?

Options:

- (a) Freeze all upstream activity, and rebase only.
- (b) Keep gently pushing the OpenStack-side enablement changes
  (967801, and a Keystone account that does not require
  Kerbside to be Kolla-deployed), and freeze the
  Kerbside-deployment changes (976889 onwards).

**Decided 2026-10-10: (b).** The enablement changes are smaller
and easier for the Kolla team to accept, and they are what the
front-door deployment actually needs from Kolla. Phase 3
documents the split, and `kerbside-patches` should record it
too.

Phase 3's survey found that the line cannot take effect as the
changes stand. 967801 is stacked on 976889 in Gerrit, so it
cannot merge first until it is restacked onto master. No change
exists yet for the Keystone account. kerbside-patches#1859 is
still open.

### 2. Does the Kolla lane stay in Kerbside's merge queue?

It is the only end-to-end OpenStack coverage Kerbside has. When
this plan was written it was believed to be the flakiest lane,
on the strength of three open flake issues (#293, #308, #312),
and it now depends on a patch set that is rebased rather than
actively developed. Moving it to nightly would keep the coverage
and stop a stale rebase from blocking unrelated merges. Phase
1's survey found the flakiness belief out of date.

**Decided in phase 1: it stays.** It passed in all 32 of the 40
most recent merge runs (2026-09-28 to 2026-10-09) that started
it, with no failures; phase 1's survey accounts for the other 8.
If a stale rebase ever breaks it, the remedy is to rebase, not
to demote the lane.

**Overtaken by #605 (2026-10-25).** Both cloud lanes moved to
nightly. The reasons were runner capacity and duplicate runs
dispatched from kerbside-patches, not flakiness. An OpenStack
regression now surfaces the morning after it merges, unless the
author dispatches the lane on the pull request. Phase 3's survey
recorded this.

### 3. Does Kerbside ship example systemd units?

`PLAN-kerbside-deployer.md` open question 2 already says that
the long-term home of `kerbside-api.service` and
`kerbside-daemon.service` is Kerbside, and that the collection
should switch to them once Kerbside ships them. Shipping them in
`etc/systemd/`, next to `etc/kerbside.conf.example`, would:

- give the by-hand page something to install rather than prose
  to transcribe;
- remove one coupling before the role moves.

**Recommendation: yes, in phase 2.** Start from the collection's
units, which already encode two hard-won lessons: `Restart=always`
because of #313, and `WantedBy=multi-user.target` rather than
Shaken Fist's target.

**Decided in phase 2: yes, as static files held to the role's
templates.** By then the role had moved into this repository
(#580), so the coupling this question set out to remove no longer
existed, and the units already lived here as Jinja templates under
`ansible/roles/kerbside/templates/`. Phase 2 ships rendered copies
in `etc/systemd/` for operators who do not run Ansible, with a unit
test that holds their directives to the templates. #313 is closed.
`Restart=always` stands for the reason the templates now give:
Kerbside never exits on purpose.

### 4. One container page, or an index row only?

With no first-party image, a "Containers" page would mostly say
"not yet". **Decided in phase 1: one short page.** Operators
searching for Kubernetes deserve a direct answer, and the page
is where the image issue gets linked from.

### 5. Does the backend-TLS claim guard cover the new pages?

`tools/check-backend-tls-claims.py` checks only
`docs/use-cases/*.md` and `docs/index.md` (`DOC_PATHS`).
Deployment pages will describe TLS material and are exactly
where an unconditional "the backend leg is encrypted" claim
would creep back in. **Recommendation: yes.** Extend
`DOC_PATHS` in phase 1, before any page exists.

## Execution

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Survey, matrix and scaffolding | [PLAN-deployment-options-phase-01-survey-and-scaffolding.md](/components/kerbside/plans/PLAN-deployment-options-phase-01-survey-and-scaffolding/) | Complete | 200d6b3 |
| 2. By hand: pip, venv and systemd | [PLAN-deployment-options-phase-02-manual.md](/components/kerbside/plans/PLAN-deployment-options-phase-02-manual/) | Complete | b10f2ac |
| 3. OpenStack: Kolla-Ansible, and Kerbside in front of OpenStack | [PLAN-deployment-options-phase-03-openstack.md](/components/kerbside/plans/PLAN-deployment-options-phase-03-openstack/) | Complete | d159d17 |
| 4. Containers: compose beyond the demo, and Kubernetes | [PLAN-deployment-options-phase-04-containers.md](/components/kerbside/plans/PLAN-deployment-options-phase-04-containers/) | In progress | |
| 5. Ansible: the `kerbside` role | | Not started | |
| 6. Installation hand-off and closeout | | Not started | |
| 7. Push audit | | Not started | |

**Phase 1: survey, matrix and scaffolding.** Re-check the
inventory above against the tree and against the Shaken Fist
deployer plan. Settle the page format and fix the page
filenames; the first page phase creates `docs/deployment/` and
its `order.yml`. Add a `### Deployment Options` table to
`docs/index.md`, beside Use Cases: one row per mechanism, rows
without a page marked as planned (the convention the Use Cases
table set), and a "Tested in Kerbside CI" column. Phase 1's
decision 1 settled that this table carries the matrix, as a
"Sources it configures" column, with no separate use case by
mechanism grid. Extend the TLS claim guard (question 5). File
the image issue. Settle question 2.
Planned at high effort: the matrix and the page boundaries are
judgment calls that every later phase inherits.

**Phase 2: by hand.** `docs/deployment/manual.md`: turn
`installation.md`'s pieces into durable services, covering
units, a service user, the configuration layout, the database,
TLS material, and upgrades. Kerbside keeps no state on disk and
logs to syslog, so there are no state or log directories to
create; phase 2's survey corrected this. Ships the example units
(question 3). `installation.md` keeps acquisition and the list of
pieces, and this page must not restate them. Planned at medium
effort.

**Phase 3: OpenStack.** `docs/deployment/kolla-ansible.md`:
what `kerbside-patches` gives you, its upstream status, and the
maintained-by-rebase stance stated plainly. The phase also
documents the front-door path, Kerbside deployed by any other
mechanism in front of OpenStack. That path covers the Nova
configuration `spice-direct` needs, the routable console address,
and the Keystone service account an operator creates by hand when
Kolla is not doing it. Phase 3 puts the front-door path in
`use-cases/openstack.md`'s "How to set it up", because it is the
same however Kerbside is deployed. `openstack.md` has no
"Deployment" section; its **Deployment** paragraph and upstream
status table move to the new page, leaving a link. Phase 3's
survey corrected this. Planned at high effort: the front-door path
has never been written down, and nothing in CI exercises it.

**Phase 4: containers.** `docs/deployment/containers.md`: what
`demo/` would need to become production-shaped, why Kolla's
image is not a general-purpose image, and the status of
Kubernetes. Links the image issue. Planned at medium effort.

**Phase 5: Ansible.** `docs/deployment/ansible.md`. It describes
the role wherever it lives at that point, and links to Shaken
Fist's operator guide for the parts Shaken Fist owns. It also
covers the bring-your-own-Kerbside path, which is how a Shaken
Fist cluster joins a front-door Kerbside. The role move was
accepted, so this phase documents a Kerbside-owned role that
accepts several sources. The move has landed (see Dependencies
on other plans), so this phase is unblocked. Phase 2 found that
the role installs no client library but `shakenfist-client`, so it
cannot yet deploy a working oVirt or OpenStack source or a Keystone
login (#602), and the index's sources cell for the role says so;
this page states the role's state on the day and updates that
cell. Planned at medium effort.

**Phase 6: installation hand-off and closeout.**
`installation.md`'s "Deploying for real" becomes a pointer to
`docs/deployment/` rather than a table keyed by cloud. Reconcile
`docs/index.md`, and touch README.md only if its curated links
change. Check whether ARCHITECTURE.md or AGENTS.md need anything
(probably not: no component or convention changes). Planned at
medium effort.

Phases 2, 3 and 4 are independent, and can run in any order or
in parallel once phase 1 has merged. Phase 5 waits on another
repository. If that wait is long, phase 6 may run before phase
5, with the Ansible row in the index still marked planned.

<!-- shared-block: plan-push-audit-phase v3 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did.
<!-- shared-block-end -->

## Dependencies on other plans

- **shakenfist/shakenfist `PLAN-kerbside-deployer.md`** owns the
  `kerbside` role. Phases 1 to 3 have merged, and phase 4 is
  shakenfist#4479 (open on 2026-10-10). Its phase 5 already makes
  Kerbside's sf-e2e lane deploy through the collection, and its
  open question 2 already names Kerbside as the long-term home
  of the systemd units.

  **Amendment accepted 2026-10-10** by the session running that
  plan, as a rewritten phase 5,
  `PLAN-kerbside-deployer-phase-05-role-to-kerbside.md`, committed
  as shakenfist 431d6a269 on shakenfist#4479's branch. The move
  needs that plan's phase 4 to merge, and a Kerbside release
  containing #536 (the fix for #533). The newest release, v0.6.0,
  predates it, **so a Kerbside release is on the critical path.**
  It rewrites its phase 5 so that the role moves into this
  repository, as an ansible collection, rather than Kerbside's
  lane reaching into Shaken Fist's collection. The changes are:

  - The role's single hard-coded Shaken Fist source becomes a
    `kerbside_sources` list. Shaken Fist's `site.yml` computes its
    own entry, and an operator can add others.
  - Shaken Fist's collection depends on Kerbside's.
  - Kerbside's sf-e2e lane exercises the role directly.

  Kerbside's CI runs faster than Shaken Fist's, so iterating on
  the role here should be quicker. Shaken Fist's phase 4 merge
  lane keeps proving the integration from the other side. The
  cost is a collection dependency across repositories and a
  release step for it, which that phase has to design. This
  plan's phase 5 waits on the move.

  **The move landed on 2026-10-22.**
  - Release: v0.8.0 published the role on Galaxy as the
    `shakenfist.kerbside` collection.
  - Shaken Fist: it deploys Kerbside from that collection since
    shakenfist#4591 (merge commit 48849831), as a soft dependency
    pinned in `examples/_shared/requirements.yml`.
  - This repository: the sf-e2e lane deploys through the role
    since #587 (merge commit 89f1994).
  - Actions: shakenfist/actions#195 (merge commit 31139125)
    removed `deploy-kerbside-on-shakenfist`.

  Phase 5 can start.

- **`PLAN-use-case-docs.md`** (Complete) set the page format,
  the planned-row index convention, and the backend-TLS claim
  guard this plan reuses.
- **`PLAN-demo-install.md`** (Complete) owns `installation.md`'s
  demo walkthrough. Phase 4 links it and does not restate it.

## Agent guidance

Phases follow `PLAN-TEMPLATE.md`'s sub-agent execution model:
implementation by sub-agents, and review and commits in the
management session. Every page states backend TLS conditionally,
as `rust/kerbside-proxy/src/backend.rs` behaves. Four phases of
the use-case plan each got this wrong before the guard existed.
Claims about another repository's deployer are checked in that
repository on the day, not taken from this plan.

## Future work

- A first-party container image, then a Helm chart or an
  operator if anyone asks for one.
- A CI lane that runs one Kerbside in front of two sources. It is
  the deployment this plan recommends, and nothing tests it.
- #300: a login path that is not Keystone.

## Back brief

Before executing any step of this plan, back brief the
operator on your understanding of the plan and how the work
you intend to do aligns with it.
