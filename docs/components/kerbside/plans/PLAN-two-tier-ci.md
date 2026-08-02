# Two-tier CI: smoke gates on PRs, full clouds in the merge queue

## Prompt

Before responding to questions or discussion points in this
document, explore the kerbside codebase thoroughly. Read
relevant source files, understand existing patterns (the
Rust SPICE proxy in `rust/kerbside-proxy/` and the gRPC
control contract in `kerbside/rpc/`, the source driver
abstraction in `kerbside/sources/`, the REST API in
`kerbside/api.py`, the .vv file generation path, and the
CI workflows in `.github/workflows/`). Ground your answers
in what the code actually does today. Do not speculate
about the codebase when you could read it instead. Where a
question touches on external concepts (GitHub merge
queues, `merge_group` events, oVirt's SPICE proxy
mechanism, the ovirt-engine console API), research as
needed to give a confident answer. Flag any uncertainty
explicitly rather than guessing.

Key cross-repo references:

- `shakenfist/actions` — the reusable CI actions and
  ansible used by all lanes (`setup-kerbside-environment`,
  `build-smoke-cluster`, `deploy-kerbside-on-shakenfist`,
  `kerbside-single-node.yml`). Most tier-split work
  touches this repo as well as kerbside.
- `shakenfist/shakenfist` — the reference two-tier CI
  layout this plan mirrors.
- `shakenfist/kerbside-patches` — the OpenStack/Kolla
  integration; its own nightly CI catches upstream Kolla
  churn independently of this repo's lanes.
- `shakenfist/ryll` — the Rust SPICE client; its headless
  mode drives automated console checks.

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be
named for the master plan, in the same directory as the
master plan, and simply have `-phase-NN-descriptive`
appended before the `.md` file extension. Tracking of
these sub-phases should be done via a table in this master
plan under the Execution section.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Every pull request against develop currently runs (via
`functional-tests.yml` plus sibling workflows):

- **sanity_checks** — flake8, unit tests, coverage
  (~10 minutes, m runner).
- **ovirt_matrix** — builds a complete oVirt 4.5
  environment on Rocky 8 from RPMs, boots a SPICE test
  VM, and checks console connectivity with
  `tools/test-ovirt-console.py` (up to 120 minutes,
  m runner).
- **openstack_matrix** — builds Kolla container images,
  deploys all-in-one OpenStack with the PR's kerbside
  wheel injected, and runs Tempest (up to 120 minutes,
  m runner). This is the most expensive lane and today
  the only cloud lane that deploys the PR's code.
- **Direct-qemu functional test** — the PR's proxy against
  a real qemu/KVM SPICE server (l runner, ~60 minutes).
- **Rust proxy** — fmt/clippy/test/build, path-filtered to
  `rust/**` and the proto.
- **CodeQL**, **automated_reviewer**.

Meanwhile `sf-e2e-functional.yml` (single-node Shaken
Fist, deploys the PR's kerbside and proxy wheel, drives
the happy path and the adversarial matrix) is nightly and
dispatch only — the phase 9 decision (PLAN-kerbside-vdi-
tokens-phase-09-e2e.md) deliberately kept it off PRs
"until it is shown stable". As of 2026-08-01 it has very
few recorded runs (one nightly failure on 2026-07-31,
green nightly on 2026-08-01, ~16 minute runtime when
green).

The cost problem is concrete: a burst of Renovate PRs
queues an oVirt build plus a Kolla build per dependency
bump, roughly four machine-hours of cloud construction per
PR, on lanes whose failure modes are dominated by upstream
environment churn rather than kerbside regressions.

Two defects shape the sequencing:

1. **The oVirt lane does not test kerbside.** Verified
   against `functional-tests.yml` (2026-08-02): the job
   builds the oVirt environment and tests SPICE console
   connectivity *directly against the hypervisor*; it
   never deploys the PR's kerbside, never exercises
   `kerbside/sources/ovirt.py` against the engine, and
   never relays a console through the proxy. As a PR gate
   it validates the environment, not the change. Moving it
   to a less frequent tier loses almost nothing today —
   but it should also be fixed to actually deploy and
   drive kerbside, otherwise the merge tier inherits a
   gate that still doesn't test our code.
2. **sf-e2e is not yet trusted enough to be the smoke
   gate.** Promotion work is in progress in a separate
   session; this plan treats "sf-e2e is PR-ready" as a
   hard precondition rather than re-planning that work
   here.

### The oVirt architecture question

oVirt's native remote-console story for off-network
clients is an HTTP CONNECT proxy (conventionally squid),
configured engine-wide (`engine-config -s
SpiceProxyDefault=...`), per cluster, or per VM pool. When
set, the engine writes a `proxy=` line into the .vv files
*it* hands out, and remote-viewer tunnels to the
hypervisor's SPICE port through that proxy. (Semantics
from oVirt documentation; re-verify during phase
planning.)

Kerbside is not in that chain and does not need it.
`kerbside/sources/ovirt.py` discovers consoles via the
engine API and yields the *hypervisor's* SPICE endpoint
directly (`console.address`, `console.port`,
`console.tls_port` — `ovirt.py:108-117`), acquires the
ticket via the graphics-console service
(`get_console_for_vm`), and pins the host certificate
subject. A kerbside-brokered session is therefore already
`client -> kerbside -> hypervisor` — one proxy layer. The
feared `client -> kerbside -> squid -> hypervisor` chain
only arises if kerbside were pointed at engine-generated
.vv files, which it never uses.

So "swap squid for kerbside" is really a deployment-model
statement, with three candidate architectures:

- **(a) Kerbside as the VDI front door (recommended).**
  Users obtain consoles from kerbside; `SpiceProxyDefault`
  is simply never configured and squid is not deployed.
  Engine-portal consoles remain available only where the
  hypervisor network is directly reachable (admin use), or
  are disabled. Zero new code; full SPICE firewall,
  session tracking, and audit apply. The layer count is
  already minimal.
- **(b) Kerbside impersonates the squid.** Set the
  cluster/engine SPICE proxy to a kerbside HTTP CONNECT
  listener, so engine-portal .vv files route through
  kerbside. Neat operationally (a stock oVirt config
  knob, no engine patches), but architecturally weak: the
  client authenticates to the hypervisor with oVirt's
  ticket end-to-end, and secure channels are TLS
  client-to-hypervisor with the engine CA and
  host-subject pinning — kerbside cannot terminate or
  inspect them without an untenable MITM. Kerbside would
  degrade to an opaque tunnel with target-address policy
  and connection metadata only, losing the
  inspection-first firewall that justifies its existence.
- **(c) Patch ovirt-engine to hand out kerbside .vv
  files** — the kerbside-patches analogue for oVirt.
  Deep surgery in the engine's Java .vv generation path,
  against an upstream on life support. Not proposed.

Recommendation: (a) is the supported architecture and
what the fixed CI lane should deploy; document it in
`docs/console-sources.md`. (b) is at most a future
compatibility shim and should be recorded as rejected
unless a concrete deployment needs portal-initiated
consoles through kerbside.

## Mission and problem statement

Adopt the shakenfist/shakenfist two-tier CI flow: a fast,
indicative smoke tier on every PR push, and the full
multi-cloud suite in the merge queue. Concretely:

- **Smoke tier (pull_request):** sanity_checks,
  Direct-qemu, Rust proxy (path-filtered), CodeQL, and —
  once promoted — single-node sf-e2e. This still deploys
  the PR's code against a real cloud and relays real SPICE
  traffic through the real proxy.
- **Merge tier (merge_group):** oVirt (fixed to actually
  deploy kerbside) and Kolla/OpenStack; the future
  multinode Shaken Fist and Proxmox lanes land here too.

The trade accepted: a cloud-specific breakage surfaces in
the merge queue rather than on the PR, blocking the queue
and costing a rerun. Given those lanes' failure modes are
dominated by upstream churn (which kerbside-patches'
nightly CI catches independently), this is a good trade.

## Preconditions

- **sf-e2e PR-readiness** (in progress elsewhere). This
  plan does not start execution until sf-e2e meets the
  promotion criteria in Open questions below. Until then
  the current PR gates stay as they are — we do not
  weaken PR coverage before its replacement is trusted.

## Open questions

1. **sf-e2e promotion criteria.** Proposal: N consecutive
   green scheduled runs (suggest N=10, bumping the
   schedule to more than nightly to accumulate signal
   faster), with runtime consistently under ~30 minutes.
   To be confirmed against whatever is blocking it in the
   other session.
2. **Does the oVirt lane belong in the merge tier or
   nightly?** Once fixed to deploy kerbside it becomes a
   genuine integration gate and the merge tier is
   defensible. If flake from the RPM-built environment
   dominates, demote it to schedule-only like sf-e2e was.
3. **Renovate interaction.** Renovate PRs are the bulk of
   PR volume. Confirm renovate's automerge/rebase flow
   drives the merge queue correctly (memory:
   close/reopen is the reliable retrigger for stale
   rollups), and that queue entries batch rather than
   serialise four-hour lanes per dependency bump.
4. **Merge queue mechanics.** Branch protection on
   develop must switch to a merge queue with per-tier
   required checks; workflow `concurrency:` groups must
   key sanely for `merge_group` refs; the
   `automated_reviewer` job currently `needs:` the cloud
   matrices and must be re-anchored to smoke-tier jobs
   only. Repository-settings changes are manual GitHub UI
   work (cf. PLAN-consistency-audit.md).
5. **What does "oVirt CI uses kerbside" drive?** Minimum:
   deploy the PR's kerbside (wheel built as in the other
   lanes) against the engine, register the ovirt source,
   confirm discovery populates consoles, and relay a
   console session through the proxy (ryll headless, as
   the direct-qemu and sf-e2e lanes do). Reuse of the
   sf-e2e drive tooling should be assessed in phase
   planning.

## Execution

Phases will get detailed plan files as each is started.

| Phase | Plan | Status |
|-------|------|--------|
| 1. oVirt lane deploys kerbside | PLAN-two-tier-ci-phase-01-ovirt-kerbside.md | Not started |
| 2. Promote sf-e2e to PR smoke gate | PLAN-two-tier-ci-phase-02-sf-e2e-promotion.md | Blocked on precondition |
| 3. Merge queue adoption and tier split | PLAN-two-tier-ci-phase-03-merge-queue.md | Not started |
| 4. Documentation | PLAN-two-tier-ci-phase-04-docs.md | Not started |

- **Phase 1** is independent of the precondition and can
  start first: extend the oVirt lane so the environment
  build is followed by a kerbside deploy and a
  proxied-console check (open question 5). This settles
  the architecture (a) decision in practice; the
  operator-facing documentation of that architecture is
  phase 4's deliverable. Touches `shakenfist/actions` as
  well as this repo.
- **Phase 2** flips `sf-e2e-functional.yml` to also run on
  pull_request once the promotion criteria are met, and
  removes the phase-9 "not a PR gate" caveat.
- **Phase 3** moves ovirt_matrix and openstack_matrix out
  of `functional-tests.yml` into a merge-tier workflow
  triggered on `merge_group`, re-anchors
  automated_reviewer onto smoke jobs, adds `merge_group`
  triggers to the smoke workflows so queue entries re-run
  them, and documents the manual branch-protection
  changes. Ordering: only after phases 1 and 2, so PR
  coverage is never weakened first.
- **Phase 4** updates `docs/testing.md`,
  `.claude/CLAUDE.md`'s CI workflow list, AGENTS.md, and
  ARCHITECTURE.md as needed. It also delivers the
  recommended-oVirt-deployment documentation (probably in
  `docs/console-sources.md`, or a dedicated page if it
  outgrows the source-options format that file uses
  today), covering at minimum:
  - the front-door architecture decision (option (a)
    above) and why the engine's SPICE proxy (squid) is
    not deployed alongside kerbside — including a record
    of option (b) as considered and rejected;
  - engine-side configuration: leave `SpiceProxyDefault`
    and per-cluster SPICE proxy unset; the account and
    permissions the source user needs for console
    discovery and ticket acquisition; guest expectations
    (qemu-guest-agent, spice-vdagent);
  - network prerequisites: kerbside needs direct L3
    reachability to every hypervisor's SPICE port range —
    the reachability squid normally provides in a stock
    oVirt deployment;
  - the user interaction model: consoles are obtained
    from kerbside (`.vv` download via the API/UI), and
    the engine portal's own console buttons are for
    admins on the management network or disabled.

  Phase 1's fixed CI lane is a worked example of a
  correct deployment; phase 4 should distil that ansible
  into these docs rather than leaving the knowledge
  implicit in CI.

  Phase 4's oVirt page should be written as the first
  "use case" page — value proposition, how it works,
  how to set it up, status — in the format defined by
  PLAN-use-case-docs.md, which tracks the wider
  per-deployment-permutation documentation suite (Shaken
  Fist, OpenStack, multi-cloud, static, and eventually
  Proxmox) as follow-on work outside this plan's scope.

Future work, explicitly out of scope here but shaping the
design: a multinode Shaken Fist lane and a Proxmox source
plus lane, both landing directly in the merge tier. On
Proxmox feasibility (researched 2026-08-02): PVE has had
an official automated installer since 8.2
(`proxmox-auto-install-assistant`, answer.toml via
ISO/partition/HTTP with DHCP or DNS discovery, first-boot
hooks since 8.3), but the better CI fit is the officially
supported install-on-Debian path — PVE 9.x on a debian:13
base VM via apt, mirroring how the oVirt lane layers
ovirt-engine onto rocky:8. On the future source's
architecture (researched 2026-08-02): Proxmox's
`spiceproxy` is not squid — it is a purpose-built
pve-manager daemon on port 3128 that verifies a ticket
embedded in the HTTP CONNECT pseudo-hostname
(`pvespiceproxy:<ticket>:<vmid>:<node>:...`) against the
cluster auth key and routes to the right node, then
relays bytes with no SPICE awareness. Because Proxmox
binds qemu's SPICE listener to loopback with mandatory
TLS, the oVirt answer (kerbside dials the hypervisor
port directly) does NOT carry over: kerbside's backend
leg should instead issue the ticketed CONNECT to
spiceproxy and run its normal SPICE-client handshake
through the tunnel — kerbside remains the protocol-aware
middle, so full inspection is preserved. The proxy's
backend dialer will need HTTP CONNECT support; assess
that when planning the source.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in
the management session. The management session is reserved
for planning, review, and decision-making.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the plan, at the recommended effort
   level and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's
   summary describes what it intended, not necessarily
   what it did.
4. **Fix or retry** if the output is wrong. Diagnose
   whether the brief was insufficient (improve it) or the
   model was too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied
   with the result.

Use `isolation: "worktree"` for sub-agents when the change
is risky or experimental. CI workflow changes are hard to
test locally; prefer small commits that can be validated
by dispatch runs, and lean on `ci-status` to watch them.

### Planning effort

Phases 1 and 3 should be planned at high effort: phase 1
requires understanding the oVirt engine API, the existing
ansible in shakenfist/actions, and the .vv exchange path;
phase 3 requires getting merge-queue event semantics,
concurrency groups, and required-check wiring right, where
mistakes silently stop CI from gating merges. Phases 2 and
4 are mechanical and can be planned at medium effort.
