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
| Multi-cloud aggregation | One kerbside brokering several sources at once — the distinctive value proposition; today implicit everywhere and stated nowhere. Covers the consequences: users keep one console entry point while workloads move between providers (cloud migration without retraining or re-plumbing client access), and multiple clouds in different regions present as a single VDI estate |
| Placement topologies | The inverse of aggregation: kerbside instances placed by user population rather than by cloud — e.g. a kerbside per regional office, close to its users, so SPICE over the WAN is exactly the kerbside-to-hypervisor backend leg: TLS'd (with host-subject pinning), firewall-inspected, audited, and a single controllable egress point at the office edge. Multiple kerbsides against one cloud is natural for scraped sources (SF, oVirt); the OpenStack flow assumes one kerbside URL per Nova deployment, so per-group placement there needs the broker to route — document as a caveat |
| Standalone / static source | The static driver (`kerbside/sources/static.py`) for labs, demos, and direct-qemu style fleets |
| Proxmox | Deferred until the source exists; architecture notes already captured in PLAN-two-tier-ci.md's future-work section |

`docs/index.md`'s introduction slims down to the generic
broker model and links to these pages; the pages join the
Operator Documentation section of the index. README.md is
only touched if the curated doc links change (per the
readme-discipline policy).

## Status

Not started, except that the oVirt page is scheduled as
PLAN-two-tier-ci.md phase 4's deliverable. Do not start
the remaining pages before that page has settled the
format.
