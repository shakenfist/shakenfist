# Phase 9: Documentation sweep

## Context

Phases 1–8 each ended with a focused documentation update
covering the change shipped in that phase. As a result, the
project-level documents (`ARCHITECTURE.md`, `AGENTS.md`,
`docs/developer_guide/network_dispatcher.md`) are already in good
shape — they were never allowed to drift more than one phase out
of date. Phase 9 is therefore *not* the rewrite the master plan
implied; it is a focused finalisation pass.

The scope of Phase 9 is the work that genuinely was deferred:

* The REST API reference docs under
  `docs/developer_guide/api_reference/` were only partially
  updated. Phase 7 rewrote `clusteroperations.md` from scratch,
  but `networks.md` still carries a `delete_network` example that
  predates the 202+poll contract — the example manually polls
  `get_network` in a `while` loop, which is exactly the code the
  new client library wraps. That example needs updating.
* `ARCHITECTURE.md` and `AGENTS.md` have accumulated a sequence
  of "Phase N — <title>" subsections (Phase 7, Phase 8 in
  ARCHITECTURE.md; Phases 6, 7, 8 in AGENTS.md). The
  phase-narrative format was useful while phases were landing
  one-at-a-time; in the merged final state it produces a
  document organised around the order work happened in rather
  than the architecture itself. A light consolidation pass folds
  these into final-state descriptions while keeping the
  rationale intact.
* The master plan and the eight sub-phase plans need a final
  cross-check: any "future work" items that shipped during the
  project should be removed; any references to the
  `/cluster_operations/` URL prefix (renamed during Phase 7
  review) should be `/clusteroperations/`; any references to
  "per-method migration flags" (documented during Phase 8 as
  inaccurate) should be removed.

A handful of other docs were inspected during Phase 9 planning
and confirmed to need no changes:

* `README.md` — no network-facade-relevant content; nothing to
  update.
* `docs/developer_guide/state_machine.md` — the Network state
  values (`initial`, `created`, `delete-wait`, `deleted`,
  `error`) and their transitions are unchanged by the facade
  refactor. The doc is about *which* states exist, not *how*
  transitions are driven.
* `docs/operator_guide/networking/overview.md` — describes the
  resulting host network configuration (interfaces, bridges,
  VXLAN topology) rather than the orchestration mechanism. No
  facade-specific updates needed.
* `docs/developer_guide/network_dispatcher.md` — already
  comprehensive. The chronological "Phase 6", "Phase 7", "Phase
  8" sections in this doc are intentional and stay: this is the
  deep-dive technical narrative for contributors who want to
  understand how the architecture came to be, and the
  chronology is the point. Final-state summaries live in
  `ARCHITECTURE.md` instead.
* Existing tests are already consistent with the post-Phase-8
  state. Phase 8a already converted the lock-related assertions;
  Phase 6c added explicit `InvalidStateForTask` tests for the
  retired handlers. No test-cleanup pass needed.

## What Phase 9 ships

**1. API reference doc updates.**

* `docs/developer_guide/api_reference/networks.md` — the
  "Python API client: delete a network" example block (around
  lines 59–94 of the current file) currently shows:

  ```python
  n = sf_client.delete_network(n['uuid'])
  while n['state'] != 'deleted':
      print('Waiting...')
      time.sleep(1)
      n = sf_client.get_network(n['uuid'])
  ```

  Replace with an example that reflects the new client API:
  `delete_network` with `wait=True` (the default) returns the
  cluster operation's final view once it reaches a terminal
  state. The `while` loop disappears. Add a brief paragraph
  explaining that `wait=False` returns the op handle if the
  caller wants to do something else while the deletion happens.
  Reference the
  `docs/developer_guide/api_reference/clusteroperations.md`
  page for the polling contract.

* `docs/developer_guide/api_reference/networks.md` — the
  "REST API calls" entry list near the top mentions
  `DELETE /networks` and `DELETE /networks/{network_ref}` but
  doesn't note that they return HTTP 202. Add a brief
  parenthetical to each entry stating the response shape.

* Sweep the rest of `docs/developer_guide/api_reference/` for
  any other stale references to the old synchronous-delete
  contract. The phase 7 rewrite of `clusteroperations.md` is
  current and should not need further edits; if anything is
  found, fix in place.

**2. ARCHITECTURE.md consolidation.**

The "Network Operation Error Handling" section currently has
ten subsections, two of which (`#### Phase 7 — REST contract`
and `#### Phase 8 — NodeLock removal`) are phase-narrative
holdovers. Fold their content into the surrounding final-state
descriptions:

* The Phase 7 subsection covers the 202+poll contract, the
  cluster-operation discovery endpoints, and the
  `redirect_to_network_node` removal. Most of this belongs
  alongside the existing `### Network Operation Queue Families`
  section as a "REST API surface" subsection describing the
  final shape.
* The Phase 8 subsection is a single paragraph noting the
  NodeLock removal. Fold into the
  `#### BridgedVXLanNetwork — worker-only mutation surface`
  subsection, where it belongs architecturally — the
  worker-only mutation surface is the *reason* the NodeLocks
  could be removed.

After the consolidation, neither subsection retains a "Phase
N — title" header. The information is preserved; the
phase-narrative chrome is gone.

**3. AGENTS.md consolidation.**

AGENTS.md currently has phase-narrative subsections for Phase
6 (maintain), Phase 7 (REST contract), and Phase 8 (NodeLock
removal). The doc's purpose is to brief a fresh agent on the
codebase as it stands now — phase numbers are noise in that
context. Replace the three subsections with a single
"Network facade architecture" subsection summarising the final
state in three or four short paragraphs:

* The `BridgedVXLanNetwork` worker class is the only place
  that mutates host network state. The single-threaded
  net-worker dispatcher (`shakenfist/daemons/network/workitem.py`)
  is the only caller. Mention the cancellation check on
  dequeue and the exponential back-off map; point at the
  single-worker safety comment in workitem.py.
* `Network` methods enqueue cluster operations rather than
  mutating state directly. Maintain (`shakenfist/daemons/network/maintain.py`)
  is discovery-only with a five-guard pipeline.
* The REST API surface — the two delete endpoints return 202
  with an op handle; the two discovery endpoints
  (`/clusteroperations/<uuid>/chain` and
  `/clusteroperations?target_*=`) are available; the only
  surviving `@redirect_to_network_node` is on
  `NetworkPingEndpoint.get`.
* Error handling: `ErrorReport` is the on-the-wire shape;
  errors are data, not rehydrated exception types.

Keep the section concise — AGENTS.md is supposed to brief in
minutes, not hours.

**4. Master plan and sub-phase plan audit.**

* `docs/plans/PLAN-network-facade.md` — audit the body
  (especially the "Mission", "Design decisions", and "Open
  questions" sections) for any remaining
  "TODO" / "future work" / "Phase N will..." items that have
  shipped. Remove the obsolete ones, leave the genuinely
  deferred ones (e.g. the queue-based ping endpoint, the
  `redirect_instance_request` / `redirect_to_eventlog_node`
  decorator migrations) with a note that they remain deferred
  beyond the master plan.
* The eight sub-phase plans (`PLAN-network-facade-phase-0[1-8]-*.md`)
  stay as historical record. No edits needed unless a
  cross-reference between them is broken.
* `docs/plans/PLAN-recurring-operations.md` — confirm it still
  reflects the right scope (was set aside during Phase 1
  planning as a separate future plan).

**5. Status flip.**

After steps 1–4 land and CI is green, flip Phase 9 in the
master plan execution table to `Complete`. With Phase 9
complete, the entire network-facade master plan is done.

## What Phase 9 does **not** do

* **No structural changes to source files.** Phase 9 is docs
  only. The single test-file touched in Phase 8a already
  converted lock-related assertions; no further test cleanup
  is needed.
* **No README.md changes.** The README has no
  network-facade-relevant content. If we wanted a one-line
  pointer to the dispatcher dev guide, it could go here, but
  that's a different conversation.
* **No state-machine doc changes.** Network states haven't
  changed.
* **No operator-guide changes.** The operator's
  Shaken-Fist-deployment view of networking is unchanged.
* **No new doc files.** All Phase 9 edits are to existing
  files.
* **No `mkdocs.yml.tmpl` changes.** No new pages, no
  reorganisation.
* **No restructuring of `network_dispatcher.md`.** Its
  chronological phase-by-phase narrative is the right format
  for that doc.

## Key references

* `docs/developer_guide/api_reference/networks.md` — the file
  with the most concretely-broken example.
* `docs/developer_guide/api_reference/clusteroperations.md` —
  reference target for the new polling contract.
* `ARCHITECTURE.md` — sections to consolidate at lines 320
  (Phase 7 subsection) and 410 (Phase 8 subsection).
* `AGENTS.md` — three Phase subsections to consolidate; check
  the existing structure first to land the new section in the
  right place.

## Success criteria

* `docs/developer_guide/api_reference/networks.md` `delete_network`
  example uses the new client API; no manual polling loop.
* `ARCHITECTURE.md` "Network Operation Error Handling" section
  has no "Phase N — title" subsection headers; information is
  preserved in final-state subsections.
* `AGENTS.md` has a single "Network facade architecture"
  subsection rather than three phase-numbered subsections.
* `docs/plans/PLAN-network-facade.md` execution table shows
  every phase as `Complete`.
* `grep -rn '/cluster_operations/' docs/ shakenfist/ ARCHITECTURE.md AGENTS.md`
  returns zero hits (no underscored URL references remaining).
* `pre-commit run --all-files` passes.
* `mkdocs build` does not warn about broken links.

## Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a. API reference doc updates | low | sonnet | none | Edit `docs/developer_guide/api_reference/networks.md`. Replace the "Python API client: delete a network" example block (currently shows manual `while n['state'] != 'deleted'` polling against `get_network`) with an example using the new client API: `n = sf_client.delete_network(uuid)` returns the op view once terminal; no polling loop needed. Add a brief paragraph after the example noting that `wait=False` returns the raw op handle if the caller wants fire-and-forget semantics, and pointing readers at `docs/developer_guide/api_reference/clusteroperations.md` for the polling contract details. Also: the "REST API calls" entry list near the top of the file lists `DELETE /networks` and `DELETE /networks/{network_ref}` — add a short parenthetical to each entry indicating they return HTTP 202 with an op-handle response body. Sweep the rest of `docs/developer_guide/api_reference/` (`agentoperations.md`, `instances.md`, `artifacts.md`, `interfaces.md`, etc.) for any remaining stale references to a synchronous-network-delete contract or to `/cluster_operations/<...>` URLs (the underscored form). If found, fix in place. Run `pre-commit run --all-files` (which includes the doc-related linters); fix any issues. Commit message subject: `docs: api_reference updates for phase 9.` The commit body should explain the example replacement, note that `clusteroperations.md` was already updated in Phase 7 so this commit completes the API reference sweep, and reference the master plan / phase 9 plan. |
| 9b. ARCHITECTURE.md and AGENTS.md consolidation | medium | sonnet | none | Edit `ARCHITECTURE.md`'s "Network Operation Error Handling" section (starts around line 228). It currently contains ten subsections, two of which (`#### Phase 7 — REST contract` around line 320, and `#### Phase 8 — NodeLock removal` around line 410) are phase-narrative holdovers. **Phase 7 content** (the 202+poll delete contract, the two discovery endpoints, the `redirect_to_network_node` removal) should be folded into a new subsection after the existing `### Network Operation Queue Families` section, titled something like `### REST API surface` or `### External API contract`, describing the final shape (not "what Phase 7 shipped"). **Phase 8 content** (single paragraph noting the NodeLock removal) belongs in the existing `#### BridgedVXLanNetwork — worker-only mutation surface` subsection — fold it in as a closing paragraph explaining *why* the worker-only mutation surface lets cross-daemon serialisation be queue-based rather than lock-based. After folding, neither "Phase 7" nor "Phase 8" subsection header should remain; the information is preserved without phase-narrative chrome. Edit `AGENTS.md`: it currently has three phase-numbered subsections (Phase 6 maintain, Phase 7 REST contract, Phase 8 NodeLock removal). Replace them with a single "Network facade architecture" subsection summarising the final state in three or four short paragraphs (see the master plan's "AGENTS.md consolidation" body for the structure). Keep the section concise — AGENTS.md is for fresh-agent orientation in minutes, not deep technical history (that lives in `network_dispatcher.md`). For both files, **preserve information**, don't lose any architectural detail in the consolidation. Read each subsection thoroughly before consolidating. Run `pre-commit run --all-files`; confirm `mkdocs build` does not warn. Commit message subject: `docs: consolidate phase narrative into final-state.` Body should list each file touched and what was folded into what, and note that the deep-dive chronological narrative still lives in `docs/developer_guide/network_dispatcher.md`. |
| 9c. Master plan audit | low | sonnet | none | Audit `docs/plans/PLAN-network-facade.md`. Read the "Mission", "Design decisions", "Open questions", and "Future work" sections (or whatever the equivalent named sections are) and identify any "TODO" / "future work" / "Phase N will..." / "deferred to Phase N" items. For each, decide whether it shipped during phases 1-8 (in which case remove the deferred-work marker) or whether it remains genuinely outside the master plan's scope (e.g. the queue-based ping endpoint, the `redirect_instance_request` / `redirect_to_eventlog_node` decorator migrations — these are real future work, not phase 9 cleanups). For items that shipped, fold the description into past-tense final-state language. Also: `grep -rn '/cluster_operations/' docs/ shakenfist/ ARCHITECTURE.md AGENTS.md` should return zero hits after this step (the URL was renamed during Phase 7 review; any remaining references in the master plan or sub-phase plans are stale). Skim the eight sub-phase plans (`PLAN-network-facade-phase-0[1-8]-*.md`) for broken cross-references between them, but **do not** edit the sub-phase plans' content — those stay as historical record. Skim `docs/plans/PLAN-recurring-operations.md` and confirm its scope reflects what was deferred during Phase 1 planning; no edits expected. Run `pre-commit run --all-files`. Commit message subject: `plans: phase 9 master plan finalisation.` Body should list each removed-as-shipped item, list each retained-as-deferred item, and confirm the grep result. |
| 9d. Mark phase 9 complete | low | sonnet | none | Edit `docs/plans/PLAN-network-facade.md`. In the execution table, flip Phase 9's status from `Planning` to `Complete`. The row description `Documentation and tests` can stay as-is (the tests scope turned out to be already-handled, but the description is fine). Add a one-paragraph note at the top of the master plan or in a "Status" section noting that with Phase 9 complete, the entire network-facade master plan has landed. Use the same phrasing pattern as the earlier "plans: mark phase N of network facade complete" commits (`82d0af2f` for Phase 7, `90f6f3c9` for Phase 8). The commit body should reference the four phase-9 sub-commits, note that this is the final commit of the project, and (optionally) note what comes next (the ping-endpoint queue migration and the remaining redirect decorators are the natural follow-ons but are not part of this plan). Commit message subject: `plans: mark phase 9 of network facade complete.` |

## Step ordering and dependencies

* 9a (API reference) is self-contained and lands first.
* 9b (project doc consolidation) is independent of 9a; can land in either order. Either order is fine; 9b is the most substantive Phase 9 commit and benefits from going second so any 9a feedback can be folded in.
* 9c (master plan audit) depends on 9a and 9b having landed because the audit cross-checks against the consolidated final-state.
* 9d (status flip) lands last, after CI confirms 9a–9c.

Recommended landing order: 9a → 9b → 9c → CI green → 9d.

## Back brief

Before executing 9b, the implementing sub-agent must back brief the management session. Confirm:

* The information preservation invariant. Consolidating phase-narrative subsections into final-state descriptions must not drop any architectural detail. Reading each subsection slowly before consolidating is the load-bearing step; if a paragraph's content doesn't have a natural home in the final-state structure, **stop and report** rather than silently dropping it.

* The audience distinction. ARCHITECTURE.md is the final-state reference; AGENTS.md is the fresh-agent orientation; `network_dispatcher.md` is the deep-dive chronological narrative. Each audience benefits from a different structure. Phase 9 leaves `network_dispatcher.md` alone — phase narrative is the *right* format for that file.

* The "Phase X subsection header" rule applies only to the phase-narrative subsections in ARCHITECTURE.md and AGENTS.md. Other phase references in those files (e.g. "Phase 6 retired the network_deploy handler" stated as historical fact) can stay — they're descriptive, not structural.

Before executing 9c, the implementing sub-agent must confirm:

* The retained-as-deferred list. The queue-based ping endpoint and the two non-network redirect decorators (`redirect_instance_request`, `redirect_to_eventlog_node`) are explicitly out of scope for the network-facade master plan and remain genuine future work. **Do not** describe these as "shipped" or remove them from the master plan's deferred-work list.

* Sub-phase plans stay as-is. Only the master plan body and execution table are touched. The eight sub-phase plans are historical record of how the work was scoped and executed.

## Review checklist for the management session

After 9a:
- [ ] `networks.md` `delete_network` example uses the new client API; no `while` polling loop.
- [ ] `networks.md` REST entry list notes 202 on the two delete endpoints.
- [ ] No other API reference page has a stale synchronous-delete reference.
- [ ] `pre-commit run --all-files` passes.

After 9b:
- [ ] No `#### Phase N — title` subsections remain in `ARCHITECTURE.md`'s "Network Operation Error Handling" section.
- [ ] `AGENTS.md` has a single "Network facade architecture" subsection rather than three phase-numbered ones.
- [ ] Architectural detail from the consolidated subsections is preserved; agent's commit message lists what was folded into what.
- [ ] `mkdocs build` does not warn.

After 9c:
- [ ] `grep -rn '/cluster_operations/' docs/ shakenfist/ ARCHITECTURE.md AGENTS.md` returns zero hits.
- [ ] Master plan body has no "TODO" / "future work" markers for items that shipped during phases 1–8.
- [ ] The retained-as-deferred items (ping endpoint, two non-network redirect decorators) remain documented as such.
- [ ] Sub-phase plans untouched.

After 9d:
- [ ] Phase 9 status is `Complete` in the master plan execution table.
- [ ] All nine rows now show `Complete`.
- [ ] CI passes on the phase 9 PR.
