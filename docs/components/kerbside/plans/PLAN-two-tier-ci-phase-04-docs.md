# Two-tier CI phase 4: documentation

Phase 4 of [PLAN-two-tier-ci.md](/components/kerbside/plans/PLAN-two-tier-ci/). Read that
master plan first: it holds the prompt, the tier split, the oVirt
front-door architecture decision, and the agent guidance this
phase inherits.

This phase writes down what phases 1-3 built. It changes no
workflow, no ruleset, and no code. It has two halves that are
only loosely related, and they are kept separate deliberately:

- **The CI half.** Phases 1-3 moved the cloud matrices to the
  merge tier and promoted sf-e2e to a PR gate. `docs/testing.md`
  still describes the lanes as though every one of them ran on
  every PR, and `.claude/CLAUDE.md`'s workflow list is a flat
  inventory that says nothing about tiers and is missing three
  workflows.
- **The oVirt half.** Phase 1's lane is a working deployment of
  the front-door architecture (master plan option (a)), and that
  knowledge currently exists only as CI glue. The master plan
  assigns phase 4 the job of distilling it into an
  operator-facing page, written as the **first** use-case page
  in the format defined by [PLAN-use-case-docs.md](/components/kerbside/plans/PLAN-use-case-docs/).

## Situation (grounded, 2026-08-10)

Read before writing, not assumed:

- `docs/testing.md` describes the direct-qemu lane, ryll, the
  oVirt console probe, the phase 1 oVirt kerbside lane, the
  Tempest plugin, and the loadtest images. It mentions the tier
  split exactly once, in a subordinate clause inside the Tempest
  section ("the cloud matrices moved from per-PR to the merge
  tier in two-tier CI phase 3"). There is no description of what
  a PR runs versus what the merge queue runs, no mention of the
  gate jobs, and no mention of the required status checks.
- `AGENTS.md` is in better shape: phase 3 updated the
  direct-qemu, sf-e2e, and "Test Locations" sections with the
  tier facts and the required-check names. It does not need
  re-stating, only a pointer to the operator-facing page and to
  `docs/testing.md` as the single description of the tiers.
- `.claude/CLAUDE.md`'s "CI Workflows" list names eight
  workflows with one-line descriptions. `.github/workflows/`
  contains thirteen. Missing: `sf-e2e-functional.yml`,
  `prune-reviews.yml`, `pin-indirect-dependencies.yml`. The
  entry for `functional-tests.yml` still reads "Lint, unit
  tests, oVirt/OpenStack integration" as if it were one tier.
- `docs/console-sources.md`'s oVirt section is a six-row option
  table plus a note about the CA equality check. It does not
  mention that `url` must not carry an `/api` suffix (kerbside
  appends it; `tools/ovirt-e2e/gen-sources.py` guards against
  the mistake explicitly because it is easy to make), and says
  nothing about what the engine account needs to be able to do.
- `docs/index.md` has Operator / Developer / Architecture /
  protocol sections. There is no use-case section to add a page
  to; one has to be created.
- `README.md` carries a nine-line paragraph describing the
  sf-e2e lane, sitting under `## Installation`. It is test
  detail in the install section, which the readme-discipline
  policy says belongs in `docs/`.

### The oVirt facts this page rests on

From `kerbside/sources/ovirt.py` (read 2026-08-10), the driver
makes exactly these calls against the engine:

| Call | Purpose |
|------|---------|
| `GET /services/pki-resource?resource=ca-certificate` | CA fetch, unauthenticated, compared for equality against the configured `ca_cert` |
| `vms_service().list()` | enumerate VMs |
| `hosts_service().list(search='id=<id>')` | the host's `certificate.subject`, which becomes the pin |
| `graphics_consoles_service().list(current=True)` | the console's `address`, `port`, `tls_port` |
| `console_service(<id>).ticket()` | the per-connection ticket, acquired at connect time |

It never reads an engine-generated `.vv` file. That is the
single fact the whole architecture argument rests on, and it is
why `SpiceProxyDefault` is irrelevant to kerbside rather than
hostile to it — a distinction the master plan's prose blurs and
this page must not.

External semantics confirmed 2026-08-10 against oVirt and Red
Hat documentation:

- The engine-wide SPICE proxy is
  `engine-config -s SpiceProxyDefault=protocol://host:port`,
  followed by an `ovirt-engine` restart. It can be overridden
  per cluster, and disabled per VM.
- Acquiring a graphics-console ticket (`SetVmTicket`) requires
  the `RECONNECT_TO_VM` action group on the VM.

### The least-privilege question, answered honestly

The lane runs as `admin@internal`, which is `SuperUser`. Listing
hosts is an admin-scope operation, so the account needs more
than a portal user role; `RECONNECT_TO_VM` on the VMs covers the
ticket. A minimal custom role has **not** been constructed or
tested. The page will state what the driver calls, state that
only `SuperUser` has been exercised, and stop there rather than
inventing a role that has never been tried.

## Mission

1. A new `docs/use-cases/ovirt.md` covering the four sections
   PLAN-use-case-docs.md mandates, with the master plan's phase
   4 content bullets as its minimum contents.
2. `docs/testing.md` gains a tier section that is the one place
   describing what runs where.
3. Everything else — `.claude/CLAUDE.md`, `AGENTS.md`,
   `ARCHITECTURE.md`, `README.md`, `docs/index.md`,
   `docs/console-sources.md` — points at those two rather than
   restating them.

## Decisions

1. **`docs/use-cases/` as a subdirectory.** Seven pages are
   planned. A flat `docs/` already carries eleven files; adding
   seven more use-case pages to it would bury the reference
   material. `docs/spice/` and `docs/plans/` establish the
   subdirectory precedent. The page is `docs/use-cases/ovirt.md`.
2. **The index gets a "Use Cases" section**, listed before
   Operator Documentation, because it is what a prospective
   operator reads first. PLAN-use-case-docs.md said the pages
   join the Operator section; that was written before the
   subdirectory existed and the placement reads better this way.
   Recorded here as a deliberate departure.
3. **`SpiceProxyDefault` is described as irrelevant, not
   forbidden.** Setting it changes only the `.vv` files the
   engine's own portal hands out. Kerbside never reads those, so
   a deployment that keeps portal consoles working for admins
   *and* runs kerbside is perfectly coherent. The recommendation
   is to not deploy squid because nothing in the kerbside path
   needs it — not because it breaks anything.
4. **Option (b) is recorded as rejected in the operator page**,
   not just in the plan. An operator evaluating kerbside against
   oVirt will ask "why not just point SpiceProxyDefault at
   kerbside?", and the answer (kerbside would become an opaque
   tunnel, losing the firewall that justifies it) is the clearest
   short statement of what kerbside is for.
5. **The README's sf-e2e paragraph moves to `docs/testing.md`.**
   It is test detail under `## Installation`. The doc-link list
   is being touched anyway for the new page, which is one of the
   three conditions under which readme-discipline permits a
   README edit.

## Steps

| # | Step | Files |
|---|------|-------|
| 1 | Write the oVirt use-case page | `docs/use-cases/ovirt.md` (new) |
| 2 | Document the CI tiers | `docs/testing.md` |
| 3 | Consolidate the workflow list | `.claude/CLAUDE.md` |
| 4 | Point at the new pages, do not restate | `AGENTS.md`, `ARCHITECTURE.md` |
| 5 | Index the new page; oVirt source cross-links and gotchas | `docs/index.md`, `docs/console-sources.md` |
| 6 | Doc links, and move the sf-e2e paragraph out | `README.md` |
| 7 | Close the phase out | `PLAN-two-tier-ci.md`, `PLAN-use-case-docs.md` |

### Step 1: `docs/use-cases/ovirt.md`

Four sections, in the PLAN-use-case-docs.md order.

**Value proposition.** oVirt's own remote-console answer for
off-network clients is an HTTP CONNECT proxy (squid) that
relays bytes without understanding them. Kerbside replaces that
with a protocol-aware front door: SPICE firewall on by default,
session tracking, audit events, API-driven termination, and one
console entry point across oVirt and any other source the same
kerbside brokers.

**How it works.** The discovery/ticket/connection flow, with the
call table above as its backbone, plus a diagram in the style of
`docs/index.md`'s. Both legs get named: client-to-kerbside
(kerbside's own TLS and token), kerbside-to-hypervisor (engine
CA, `NEED_SECURED` escalation to `tls_port`, host-subject
pinning). State plainly that this is one proxy layer, not two —
the "kerbside plus squid" chain people expect never forms.

**How to set it up.** Engine side: an account (with the
least-privilege caveat as written above), guest expectations
(`qemu-guest-agent`, `spice-vdagent` — the lane's Debian 12
GNOME image ships both), and the `SpiceProxyDefault` position
per decision 3. Kerbside side: the `sources.yaml` block, the
`/api` suffix trap, the CA equality check and what makes it
fail. Network: kerbside needs L3 reachability to every
hypervisor's SPICE port range (5900+ and the TLS ports) — this
is the reachability squid otherwise provides, and it is the
prerequisite most likely to be missed. Worked example: point at
`tools/ovirt-e2e/` and the phase 1 plan, and describe the CI
topology honestly, including that kerbside runs off-box from
oVirt there for a Python-version reason that will not apply to a
real deployment.

**Status and limitations.** Proven in CI every merge-queue entry
since phase 1 (discovery, scrape, TLS escalation with pinning,
relay, terminate). Not proven: multi-host clusters (the lane is
single-host, so host-subject pinning is exercised against
exactly one certificate), least-privilege accounts, oVirt
versions other than 4.5, live migration during a session.
Record option (b) as considered and rejected, and (c) as not
proposed.

### Step 2: `docs/testing.md`

A new "## CI tiers" section, immediately after the intro, giving
a table of workflow → trigger → tier → required check, and short
prose on: the gate-job pattern (skipped required checks satisfy
the ruleset, so one required-check list serves both refs); which
five checks the develop ruleset requires; the consequence that
renaming a gate job blocks all merges; and that `rust.yml` is
advisory. Then fold the README's sf-e2e paragraph in as the
sf-e2e lane description, since the file has sections for the
other two e2e lanes but not that one.

### Step 3: `.claude/CLAUDE.md`

Replace the flat inventory with a tiered one, add the three
missing workflows, and link to `docs/testing.md` for the detail.
Add the oVirt page to the Documentation section.

### Step 4: `AGENTS.md`, `ARCHITECTURE.md`

`AGENTS.md`: a pointer from the oVirt lane section to the
operator page, and a pointer from "Test Locations" to
`docs/testing.md`'s tier section instead of the current
inline summary. Do not duplicate the tier table.

`ARCHITECTURE.md`: the source-abstraction section's oVirt row
gains a link to the use-case page, and the directory listing
gains `docs/use-cases/`. Nothing else.

### Step 5: `docs/index.md`, `docs/console-sources.md`

Index: a "Use Cases" section with the oVirt page, and a note
that the remaining pages are tracked in PLAN-use-case-docs.md.

Console sources: link the oVirt option table to the use-case
page, and add the two things the table cannot express — the
`/api` suffix trap and the account requirements.

### Step 6: `README.md`

Add the oVirt use-case link to the curated list; delete the
sf-e2e paragraph from `## Installation` (it lands in
`docs/testing.md` in step 2). No new bullets for features.

### Step 7: close out

Mark phase 4 complete in the master plan's execution table with
a link to this file. In PLAN-use-case-docs.md, record that the
format is settled by the oVirt page and the remaining pages are
now unblocked, and note decision 2's departure on placement.

## Validation

Documentation has no test suite, so validate what can be
mechanically checked and be explicit that the rest is review:

1. **Every relative link resolves.** Script it over the changed
   files rather than eyeballing; a `docs/use-cases/` page has a
   different relative depth to the rest of `docs/`, which is
   exactly where link rot starts.
2. **Every factual claim about the code traces to a file.**
   Ports, the call table, the `/api` trap, the CA check, the
   required-check names, the workflow triggers: each was read
   during planning and must be re-checked against the tree at
   write time, not copied from this plan.
3. **The workflow inventory is complete.** Diff the
   `.claude/CLAUDE.md` list against `ls .github/workflows/`.
4. **The required-check names match the exported ruleset.**
   `sanity_checks` already has a "Verify required-check names
   against the exported ruleset" step; the names written into
   the docs must match the same source.
5. `pre-commit run --all-files` clean.

## Out of scope

- The remaining six use-case pages (PLAN-use-case-docs.md).
- Any change to workflows, rulesets, or code.
- The phase 3 plan's step 4 caveat that human members of the
  bypass team silently skip the merge queue — that belongs in
  the phase 3 plan and is tracked there, not here.
- The oVirt lane's `Build infrastructure` capacity failures.
