# Phase 1 — terminology and glossary

Parent plan:
[PLAN-auth-federation.md](PLAN-auth-federation.md).

## Scope

Phase 1 produces the project glossary: a single authoritative
page defining the authentication vocabulary this plan and
`PLAN-oidc-authentication.md` depend on, plus the other
overloaded terms the codebase and documentation already use.
After this phase ships, every later phase (and every future
doc edit) has one place to point at when it says "namespace
key" or "trust", and the docs navigation carries a Glossary
entry.

The phase covers:

- A terminology survey across `docs/`, `ARCHITECTURE.md`,
  `README.md`, `AGENTS.md`, and the `sf-client` command
  surface, cataloguing terms that are overloaded, colliding,
  or used inconsistently.
- `docs/glossary.md` — the glossary itself.
- Navigation wiring in `mkdocs.yml.tmpl` and cross-links
  from the three authentication guides and
  `docs/user_guide/objects.md`.
- A bounded consistency pass fixing places where existing
  docs contradict the agreed definitions.
- An `AGENTS.md` note telling future work (human or agent)
  to keep the glossary current when concepts are added or
  renamed.

Out of scope (deferred):

- Renaming anything in code, CLI, or API — this phase
  documents reality, it does not change it. (The
  "service account token" rename is the sibling OIDC
  plan's question; artifact/label surface changes belong
  to `PLAN-artifact-ux-rework.md`.)
- The authentication guide rewrites (phase 4) — phase 1
  adds glossary links to those guides, nothing more.
- Component-level terminology in other repositories
  (kerbside, ryll, the CI conductor).

## Design decisions

**Location (resolves master plan open question 8).** One
page at `docs/glossary.md`, navigation entry in
`mkdocs.yml.tmpl` as a top-level item after "Features".
Per `docs/developer_guide/updating_docs.md`, the deployed
`mkdocs.yml` is produced from the template by the docs-sync
workflow, so only the template is edited by hand.

**Entry format.** Alphabetical. Each entry is: the term in
bold; a one-to-three-sentence definition; a "Defined in"
pointer to the authoritative code and/or doc; and, where a
term is overloaded, an explicit "not to be confused with"
note naming the sibling meaning and linking its entry.
Definitions describe what the code does today, in the
glossary's own words — other documents link here rather
than re-defining.

**Planned terms are marked.** The federation vocabulary
pinned in the master plan (identity token, trusted issuer,
mapping rule, scope/capability) describes objects that do
not exist until phase 3. These entries are included now —
pinning the vocabulary before implementation is the point —
but carry a marker of the form
`(planned — see PLAN-auth-federation)`, linking to the
master plan, so the glossary never lies to a user about
what is real. Phase 4 removes the markers as the features
land. (The marker is quoted here in backticks rather than
as live markdown because this page renders under
`docs/plans/`, where the glossary's relative link target
would not resolve.)

**Consistency with sibling plans.** The
blob/artifact/label/snapshot/upload definitions must match
the Situation section of `PLAN-artifact-ux-rework.md`
(docs/plans/PLAN-artifact-ux-rework.md lines 51–70), which
already states them crisply, and the by-reference lookup
semantics must match `docs/user_guide/objects.md`. When
that plan later changes the surface, it owns updating the
affected glossary entries (noted in its Future work by the
consistency pass, step 1d).

**Seed term list.** From the master plan (authentication):
identity token *(planned)*, trusted issuer *(planned)*,
mapping rule *(planned)*, namespace key, access token,
scope *(planned)*, nonce, trust. From the
preliminary survey done while planning this phase
(collisions already known): **key** (namespace key vs the
inter-node `_service_key*` pattern vs SSH keys in deploy
docs), **token** (access token vs the GitHub runner
registration token in CI docs vs identity token),
**trust** (namespace trust vs "trusted issuer"),
**artifact / blob / label / snapshot / upload** (the
cluster PLAN-artifact-ux-rework.md exists to fix),
**namespace**, **object / by reference** (objects.md),
**node** and node roles, **agent / agent operation / side
channel**, **instance**, **state machine states**
(created/deleted/error and friends), **event / audit
event**. Step 1a's survey is expected to extend this list,
not replace it.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Terminology survey. Sweep `docs/**/*.md`, `ARCHITECTURE.md`, `README.md`, `AGENTS.md`, and the command/help surface of `client-python/shakenfist_client/commandline/` (read the click command and option help strings; the repo is checked out as a sibling of this one — if absent, survey the API reference pages under `docs/developer_guide/api_reference/` instead) for the seed terms listed in the phase plan's "Seed term list" and for any other term used with two or more distinct meanings. For each candidate term report: the meanings found, two or three representative quotes with file:line, and whether the collision is internal to shakenfist or with an external vocabulary (e.g. GitHub Actions "artifact"). Do not edit any file. Return the report as your final message, grouped as: (i) seed terms confirmed with evidence, (ii) new collisions discovered, (iii) terms checked and found unambiguous (list only). The management session will review the report with the operator and fix the final term list before step 1b. |
| 1b | high | opus | none | Write `docs/glossary.md` following the "Entry format" and "Planned terms are marked" decisions in `docs/plans/PLAN-auth-federation-phase-01-glossary.md`, covering exactly the term list approved after step 1a (the management session will paste it into this brief). Authentication definitions come from the master plan's phase 1 section (`docs/plans/PLAN-auth-federation.md`, "Authentication terms to pin") and must be kept semantically identical; storage-cluster definitions must match `PLAN-artifact-ux-rework.md` lines 51–70 and `docs/user_guide/objects.md`. Every entry gets a "Defined in" pointer (code path, or doc page, or both). Where the survey found a collision, write the "not to be confused with" note. Wrap lines at the width used by neighbouring docs pages. Commit message subject: "docs: add the project glossary." |
| 1c | low | sonnet | none | Wire the glossary in. (i) Add `- Glossary: glossary.md` to the nav in `mkdocs.yml.tmpl` immediately after the `- Features: features.md` line (~line 83); do not edit `mkdocs.yml` (generated by docs-sync). (ii) Add a one-line pointer to the glossary near the top of `docs/user_guide/authentication.md`, `docs/developer_guide/authentication.md`, `docs/operator_guide/authentication.md`, and `docs/user_guide/objects.md` — one sentence, e.g. "Terms used here are defined in the [glossary](../glossary.md)." with the relative path correct for each page's directory depth. (iii) Add a bullet to `AGENTS.md`'s documentation conventions section: when adding or renaming a user-visible concept, update `docs/glossary.md` in the same change. Commit message subject: "docs: wire the glossary into navigation and guides." |
| 1d | medium | sonnet | none | Consistency pass. For each contradiction catalogued by step 1a (the management session will enumerate them in this brief after reviewing the survey), either (a) adjust the existing doc's wording to match the glossary definition, or (b) where the loose usage is embedded in the surface itself (command names, API paths) and cannot change here, add the glossary's "not to be confused with" note instead — never rewrite behaviour descriptions, only terminology. If a contradiction implicates `PLAN-artifact-ux-rework.md`'s scope, do not fix it: append it to that plan's Future work section with a one-line description. Keep the diff strictly terminological; if a fix feels like it changes meaning, stop and report instead. Commit message subject: "docs: align existing pages with glossary terminology." |

After 1d the management session runs:

- `pre-commit run --all-files` (must be clean).
- `mkdocs build` (or `mkdocs serve` per
  `docs/developer_guide/updating_docs.md`) against a locally
  rendered `mkdocs.yml` to confirm the nav entry and all new
  relative links resolve. Since `mkdocs.yml` is generated,
  temporarily apply the same nav edit locally for the check
  and discard it.
- A read of `git diff` on the guide pages to confirm the
  consistency pass stayed terminological.
- Confirmation that the master plan's open question 8 is
  marked resolved and the Execution table / `index.md`
  status for phase 1 is updated.

## Risks and mitigations

- **Risk:** The glossary drifts from the code as concepts
  evolve.
  **Mitigation:** every entry carries a "Defined in"
  pointer, so staleness is checkable; the `AGENTS.md`
  convention added in 1c makes updating the glossary part
  of any renaming change; phases 2–4 of this plan already
  include glossary updates in their scope.

- **Risk:** Defining planned terms (mapping rule, trusted
  issuer) confuses users into thinking the features exist.
  **Mitigation:** the *(planned)* marker with a link to the
  master plan, removed by phase 4. The marker convention is
  itself documented at the top of the glossary.

- **Risk:** The consistency pass (1d) grows into a general
  docs rewrite.
  **Mitigation:** the brief constrains it to enumerated
  contradictions from 1a, terminology-only edits, and an
  explicit stop-and-report rule; artifact-surface issues
  are diverted to `PLAN-artifact-ux-rework.md`'s Future
  work rather than fixed.

- **Risk:** Glossary definitions pre-empt decisions the
  sibling plans have not made (e.g. artifact UX rework
  renames "label").
  **Mitigation:** definitions describe today's behaviour
  only; sibling plans own updating entries they change,
  and the 1d rule records rather than resolves anything in
  their scope.

## Definition of done

- [ ] `docs/glossary.md` exists, alphabetical, every entry
      with a definition and a "Defined in" pointer; planned
      terms carry the marker.
- [ ] The eight master-plan authentication terms appear
      with definitions semantically identical to the master
      plan's.
- [ ] Glossary reachable from the mkdocs nav and linked
      from the three authentication guides and
      `objects.md`; all links verified by a local mkdocs
      build.
- [ ] `AGENTS.md` carries the keep-the-glossary-current
      convention.
- [ ] Contradictions found by the survey are either fixed,
      noted in the glossary, or recorded against
      `PLAN-artifact-ux-rework.md` — none silently dropped.
- [ ] `pre-commit run --all-files` is clean.
- [ ] Master plan open question 8 marked resolved; phase 1
      status updated in the master plan Execution table and
      `docs/plans/index.md`.
- [ ] Each step is a single self-contained commit; commit
      messages follow project conventions including the
      Prompt paragraph and Co-Authored-By line with model
      and effort.

## Back brief

Before executing any step of this phase, the implementing
sub-agent should back-brief the management session on its
understanding of the brief and the surrounding context, and
the management session must review the step 1a survey with
the operator before cutting the 1b term list.
