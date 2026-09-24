# Use case documentation

## Situation

The kerbside docs are reference-heavy: `installation.md`,
`configuration.md`, and `console-sources.md` document the
knobs, and the `spice/` tree documents the protocol. What
is missing is use case documentation — a clear page per
deployment permutation that says "this is the value
proposition for this type of deployment, this is how it
works, this is how to set it up". The value-proposition
material that exists today is scattered through
`docs/index.md`'s introduction (the broker model, the
OpenStack spice-direct story, the Bumblebee comparison)
rather than organised by the decision a prospective
operator is actually making.

Identified during two-tier CI planning
(PLAN-two-tier-ci.md, 2026-08-02); tracked here as a
standalone plan because the suite is broader than that
plan's mission.

## Mission

One page per deployment permutation, each following the
same structure:

1. **Value proposition** — who this deployment is for and
   what kerbside adds over the platform's native console
   story.
2. **How it works** — the broker/token/connection flow
   for this platform, with a diagram.
3. **How to set it up** — platform-side configuration,
   kerbside-side configuration, network prerequisites,
   and a pointer to a worked deployment (CI ansible or
   kerbside-patches) where one exists.
4. **Status and limitations** — what is proven, what is
   experimental, what is not yet implemented.

Proposed pages:

| Page | Notes |
|------|-------|
| Shaken Fist VDI | Broker embedded in SF; Ed25519 VDI console tokens (PLAN-kerbside-vdi-tokens.md); the sf-e2e lane is the worked example |
| OpenStack | Nova 2025.1 spice-direct; Kolla-Ansible deployment via kerbside-patches; much of docs/index.md's OpenStack section moves here |
| oVirt | Front-door architecture per PLAN-two-tier-ci.md; written by that plan's phase 4 as the FIRST page, establishing the format |
| Multi-cloud aggregation | One kerbside brokering several sources at once — the distinctive value proposition. Resurveyed 2026-09-22 and corrected: it is no longer one sentence. It is the `docs/index.md` Use Cases row plus a "One entry point across clouds" bullet on each of the three cloud pages — `ovirt.md:40`, `shakenfist.md:63` and `openstack.md:65` — and the last of those has grown to nine lines carrying the substantive material phase 2 found: several OpenStack clouds may be configured at once, a presented token is offered to each in `sources.yaml` order until one validates it, and the coupling that follows. The page therefore decides which of four places owns each fact rather than writing on a blank sheet. Covers the consequences: users keep one console entry point while workloads move between providers (cloud migration without retraining or re-plumbing client access), and multiple clouds in different regions present as a single VDI estate |
| Placement topologies | The inverse of aggregation: kerbside instances placed by user population rather than by cloud — e.g. a kerbside per regional office, close to its users, so SPICE over the WAN is exactly the kerbside-to-hypervisor backend leg: firewall-inspected, audited, a single controllable egress point at the office edge, and — conditionally — TLS'd. Corrected 2026-09-22, having said "TLS'd (with host-subject pinning)" unconditionally: `rust/kerbside-proxy/src/backend.rs:92` dials the insecure port first and escalates only when the hypervisor rejects plaintext with NEED_SECURED *and* a secure port is configured, and `:198-211` maps an empty `host_subject` or `ca_cert` to `None`, so pinning happens only when the source supplied a subject. OpenStack never does — `kerbside/api.py:665-671` is the only `add_console()` on that path and passes neither field. Multiple kerbsides against one cloud is natural for scraped sources (SF, oVirt); the OpenStack flow assumes one kerbside URL per Nova deployment, so per-group placement there needs the broker to route — document as a caveat |
| Standalone / static source | The static driver (`kerbside/sources/static.py`) for labs, demos, and direct-qemu style fleets. **`docs/installation.md` owns the demo mechanics** — the commands, in order, with their real output — per PLAN-demo-install.md decision 2, delivered 2026-08-22 as that plan's phase 5 (`docs/installation.md` "Try it: the demo stack"). This page owns the framing: why you would run a static source, how it works, what it cannot do, linking to the installation demo rather than restating it |
| Proxmox | Deferred until the source exists; the design record is [PLAN-proxmox-source.md](/components/kerbside/plans/PLAN-proxmox-source/), whose phase 6 is this page |

`docs/index.md`'s introduction slims down to the generic
broker model and links to these pages; the pages join the
Operator Documentation section of the index. README.md is
only touched if the curated doc links change (per the
readme-discipline policy).

## Status

All six writable pages exist. The oVirt page landed
2026-08-10 as PLAN-two-tier-ci.md phase 4's deliverable, and
settles the format; `shakenfist.md` followed 2026-09-18 as
phase 1 (`2f0e526`), `openstack.md` 2026-09-20 as phase 2
(`a7df5e5`), `standalone.md` 2026-09-21 as phase 3
(`28efa6c`), and `multi-cloud.md` with `placement.md`
2026-09-22 as phase 4 (`8c5c042`). All six carry identical
section headings, so the format is a convention rather than
a coincidence. What remains is phase 5, the index slim-down
and closeout, and phase 6, the push audit. Proxmox is still
blocked and still has no source driver (`kerbside/sources/`
holds `base.py`, `ovirt.py`, `shakenfist.py` and `static.py`
and nothing else, rechecked 2026-09-23).

One fact about the backend leg cost four phases to settle
and is now guarded rather than remembered. Phases 1, 2 and 3
each stated backend TLS or host-subject pinning more
strongly than `rust/kerbside-proxy/src/backend.rs` supports,
and each was corrected at review. Phase 4 stopped correcting
instances and swept the class: six pages had it wrong, the
sixth (`docs/proxy-architecture.md`) is filed as #472, and
`tools/check-backend-tls-claims.py` now fails CI on an
unconditional claim in `docs/use-cases/` or the Use Cases
table. A page in this plan may not say the leg is encrypted
or pinned without saying when.

A third thing landed since this plan was written, and the
plan did not know it: **the index scaffolding already
exists.** `docs/index.md` carries a `### Use Cases` heading
with a seven-row table — every proposed page including
Proxmox — each with a description and a "Tested in Kerbside
CI" column, and the note that scenarios without a link are
planned rather than written. Each page phase therefore
*links an existing row* rather than adding one, and must
agree with that row's description and CI claim or change
it.

Two things it decided that the rest should follow:

- **The pages live in `docs/use-cases/`, not flat in
  `docs/`.** Seven pages would have buried the reference
  material; `docs/spice/` and `docs/plans/` set the
  subdirectory precedent.
- **They are indexed under their own "Use Cases" heading
  in `docs/index.md`, listed before Operator
  Documentation**, rather than joining the Operator
  section as this plan originally proposed. A prospective
  operator reads "is this for me?" before "what are the
  config keys?".

One thing worth imitating: the oVirt page's "Status and
limitations" section is a table of what is *not* proven,
each row naming why. That is more useful than a prose
paragraph and much harder to let quietly rot, because a
row either still applies or gets deleted.

## Execution

Promoted from a standalone plan on 2026-09-18, when phase 1
was planned: six remaining pages cannot be tracked by a
single status cell. Promotion brings the
`plan-push-audit-phase` obligation from `PLAN-TEMPLATE.md`
with it, which is phase 6 and is not optional. Because that
audit runs over the accumulated diff of every phase, each
row records the merge commit that landed it as it lands —
the range is not reliably reconstructable afterwards.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Shaken Fist | [PLAN-use-case-docs-phase-01-shaken-fist.md](/components/kerbside/plans/PLAN-use-case-docs-phase-01-shaken-fist/) | Complete | 2f0e526 |
| 2. OpenStack | [PLAN-use-case-docs-phase-02-openstack.md](/components/kerbside/plans/PLAN-use-case-docs-phase-02-openstack/) | Complete | a7df5e5 |
| 3. Standalone / static source | [PLAN-use-case-docs-phase-03-standalone.md](/components/kerbside/plans/PLAN-use-case-docs-phase-03-standalone/) | Complete | 28efa6c |
| 4. Multi-cloud aggregation and placement topologies | [PLAN-use-case-docs-phase-04-multi-cloud.md](/components/kerbside/plans/PLAN-use-case-docs-phase-04-multi-cloud/) | Complete | 8c5c042 |
| 5. Index slim-down and closeout | [PLAN-use-case-docs-phase-05-index-slimdown.md](/components/kerbside/plans/PLAN-use-case-docs-phase-05-index-slimdown/) | In progress | |
| 6. Push audit | | Not started | |

The oVirt page is not a phase: it landed 2026-08-10 as
`PLAN-two-tier-ci-phase-04-docs.md`'s deliverable, and is
audited by that plan rather than this one.

Proxmox is not a phase either. It stays deferred until a
source driver exists, and acquires a phase then.

Phases 2 to 4 group the remaining five pages. OpenStack
and the static source each get their own phase because
each has a reference page and a worked example to reconcile
with; multi-cloud aggregation and placement topologies
share one because they are the same architectural argument
read forwards and backwards, neither has CI coverage, and
writing them apart would duplicate the reasoning. Phase 5
is the index work the Mission describes — slimming the
introduction once the OpenStack page exists to receive
`### Implementation in OpenStack` and `### What About
Bumblebee?` — plus the README collapse that phase 1's risk
table flags: one link to the Use Cases section rather than
a bullet per page.
