# Phase 8: smoke clusters as an ecosystem facility

This is a sub-plan of [PLAN-remove-primary.md](PLAN-remove-primary.md).
Phase 6 authored the reusable `smoke-cluster.yml` workflow in
`shakenfist/actions` and cut shakenfist's own CI over to it. Phase 8
turns the smoke cluster into a facility any ecosystem repo can use to
answer one question cheaply: *have I completely and hilariously broken
things?* — and retires the last copies of the getsf-era cluster CI.

## Context

The master plan scoped phase 8 as replacing each downstream repo's
copy-pasted cluster-build workflow with a few-line `workflow_call`,
naming `client-python`, `library-python` and `kerbside`. Surveying the
ecosystem while drafting found a smaller migration but a broader
design question.

**The landscape:**

1. **client-python** is the only downstream repo with a copy-pasted
   nested-SF-cluster CI. Its `functional_matrix` (debian-12,
   `localhost`, `cluster-ci.conf`) has been **hard-broken** since phase
   6 deleted getsf; its getsf `ansible-modules` job was removed in
   phase 6 step 5.
2. **library-python does not exist**; the master plan mention is stale.
3. **kerbside / kerbside-patches** use SF as under-cloud infrastructure
   via bespoke tooling (already collection-migrated during the phase
   6/7 fallout fixes). They have no nested-SF-cluster CI to migrate —
   but kerbside has SF-facing code (VDI console integration) that a
   cheap SF smoke check would protect.
4. **agent-python** has no cluster CI. Note for later: the getsf-era
   agent-from-checkout deploy path (`GETSF_AGENT_PACKAGE`) did not
   survive into the collection — guests get their agent from the CI
   images — so agent-python smoke CI would not currently exercise an
   agent PR's code. Restoring that path is a prerequisite for
   meaningful agent-python adoption and is out of scope here.
5. No other org repo has cluster-shaped CI.

**The design question: there are two kinds of consumer.**

- Repos whose "not broken" check *is* SF's own smoke suite with their
  component swapped into the cluster. client-python is the archetype:
  `setup-test-environment` checks out the *triggering* repo at the PR
  sha and the others at develop, and the collection deploy builds the
  client wheel from that checkout — so a client PR is deployed into
  the cluster and the standard smoke suite exercises it. The
  `workflow_call` form fits these directly.
- Repos whose "not broken" check is *their own tests against a live SF
  cluster*. kerbside is the archetype. A reusable **workflow** cannot
  serve them: it runs as a separate job, and cluster access (the
  under-cloud namespace, inventory, ssh key, `/etc/sf` credentials)
  does not transfer between jobs. What they need is a **composite
  action** that deploys the smoke cluster *inside their own job* and
  leaves it usable by subsequent steps.

Both modes should share one deploy implementation, or we recreate the
copy-paste drift this phase exists to end.

**One reusable-workflow wart to fix before rollout:** every bundle
uploads under the fixed name `bundle-functional-cluster-smoke`, so a
multi-entry run produces indistinguishable artifacts.

## Decisions (phase-local)

- **Extract the deploy into a `build-smoke-cluster` composite action.**
  It performs: under-cloud topology → BYO MariaDB → Loki → generated
  inventory → collection deploy (server/client wheels from the
  checkouts) → wait-schedulable → base image import, and exposes the
  cluster's coordinates (primary address, inventory path, namespace)
  as action outputs. `smoke-cluster.yml` becomes a consumer of the
  action (deploy via the action, then its stestr/module-test/teardown
  steps as today), so there is exactly one deploy implementation.
- **`workflow_call` consumers use the smoke defaults.** For a
  did-I-break-it check the right shape is `tier: smoke`, `localhost`
  topology, `smoke-ci.conf` — what shakenfist's own PR gate uses.
  client-python's old `cluster-ci.conf` over-paid; the server repo
  already runs the full suites on its own merges.
- **client-python adopts the workflow; kerbside gets the action.**
  client-python's caller replaces its broken job (keeping the
  `functional_matrix` job id; the operator flips the required status
  check at merge). A kerbside SF-integration smoke leg via the action
  is designed here but lands as its own kerbside PR when wanted — the
  facility existing is this phase's deliverable, the kerbside test
  content is kerbside's.
- **Artifact names become `bundle-<component>-<tier>[-<key>]`.**
- **Delete the getsf-era dead topologies** (`ci-topology-localhost-
  released.yml`, `-upgrade.yml`, `slim-primary-released.yml`):
  referenced by no workflow in any org repo, and they write wrappers
  for an installer deleted in phase 6. Released-version testing, if
  wanted later, is a fresh design against the collection.
- **Document the recipe.** A short "adding SF smoke CI to your repo"
  section in the actions README covering both modes, so the next repo
  is a copy of six lines rather than six hundred.

## Steps

| # | Repo | Description |
|---|------|-------------|
| 1 | this | **Plan.** This document. |
| 2 | actions | **Extract `build-smoke-cluster`.** New composite action containing the deploy sequence currently inlined in `smoke-cluster.yml`; the workflow consumes it. Outputs: primary address, inventory path, under-cloud namespace. Artifact-name parameterisation and the component-selection documentation fix ride along. Validated by a shakenfist workflow_dispatch run (behaviourally unchanged, distinct artifact names). |
| 3 | client-python | **Adopt the workflow.** Replace the broken `functional_matrix` body with a `uses: shakenfist/actions/.github/workflows/smoke-cluster.yml@main` caller: `component: client-python`, `component_ref: ${{ github.sha }}`, smoke defaults otherwise, `secrets: inherit`. Un-breaks the repo's CI. Prepared as a branch; operator pushes, PRs, and updates the required status check (new context: "functional_matrix / Smoke tests (collection)"). |
| 4 | actions | **Kerbside-mode worked example.** Add the README recipe for both modes, including a concrete build-smoke-cluster example job (deploy, then run-your-own-tests against the outputs). Coordinate with kerbside on an actual SF-integration smoke leg as a follow-on kerbside PR (out of this phase's gate). |
| 5 | actions | **Delete the dead getsf-era topologies** after a grep gate across all org checkouts. |
| 6 | this | **Bookkeeping.** Flip the phase 8 row in `PLAN-remove-primary.md` and correct its stale repo list; code review of the phase and address findings. |

## Risks

- **Deploy extraction regression.** Moving ~120 lines of workflow steps
  into a composite action risks subtle env/step-context differences
  (`$GITHUB_ENV` propagation, `inputs.*` vs `${{ inputs }}` scoping).
  Mitigated by validating shakenfist's own CI on the extracted form
  before any downstream adoption.
- **Required-check rename in client-python.** Merge-then-flip sequence
  documented in step 3; the repo has no merge queue, so it is one
  setting.
- **Cross-repo skew.** A client-python PR depending on an unmerged
  shakenfist change cannot pass (cluster deploys develop server) — the
  pre-existing land-in-order policy, newly visible after the CI dark
  period.
- **Composite-action mode tempts long-lived clusters.** The action
  deploys into the caller's job on a shared under-cloud; the recipe
  must state the cluster's lifetime is the job and teardown is the
  under-cloud reaper's, mirroring today's behaviour.

## Validation

- shakenfist workflow_dispatch run green on the extracted action, with
  per-job distinct bundle names.
- client-python PR run green end-to-end (develop server + PR client,
  smoke suite) via the reusable workflow.
- The README recipe's example job deploys and reaches the cluster in a
  scratch run.
- Grep gates: no getsf reference in actions outside git history; no org
  repo references the deleted topologies.

## Out of scope

- agent-python adoption (needs an agent-from-checkout deploy path in
  the collection first — recorded above).
- The kerbside SF-integration smoke leg's test content (kerbside's own
  PR, using the facility this phase provides).
- Collection publication to galaxy; the phase 7 deferred-removal PR;
  the node-lifecycle in-guest-kill flake.
- Released-version / upgrade testing redesign.

## Cross-repo / operator actions

- Push actions/main for steps 2, 4 and 5.
- Push and PR the client-python branch (step 3), updating its required
  status check at merge time.
- After phase 8, the remove-primary master plan is complete; consider a
  v0.8 release to start the phase-7 deprecation clock.
