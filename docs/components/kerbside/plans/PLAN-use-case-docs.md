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
| Standalone / static source | The static driver (`kerbside/sources/static.py`) for labs, demos, and direct-qemu style fleets. **`docs/installation.md` owns the demo mechanics** — the commands, in order, with their real output — per PLAN-demo-install.md decision 2. This page owns the framing: why you would run a static source, how it works, what it cannot do, linking to the installation demo rather than restating it |
| Proxmox | Deferred until the source exists; architecture notes already captured in PLAN-two-tier-ci.md's future-work section |

`docs/index.md`'s introduction slims down to the generic
broker model and links to these pages; the pages join the
Operator Documentation section of the index. README.md is
only touched if the curated doc links change (per the
readme-discipline policy).

## Status

The oVirt page landed 2026-08-10 as PLAN-two-tier-ci.md
phase 4's deliverable, and settles the format. The
remaining six pages are unblocked.

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
