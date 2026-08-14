# sfui conversion phase 6: sessions, sources and audit

This is a phase plan under `PLAN-sfui-conversion.md`; read that
master plan's Prompt, Agent guidance and Administration sections
first — they apply here and are not repeated. This phase was
planned at **medium effort**, as the master plan recommends: the
patterns were established by phases 4 and 5, and this phase
applies them to the three remaining pages.

## Situation

Phases 4 and 5 delivered `base-sfui.html`, the converted login
and consoles pages, the inline theme-aware icon convention, and
the two-step terminate confirmation. Three templates still
extend the old Bootstrap `base.html`:

- `sessions.html` — a Bootstrap accordion of proxy sessions,
  one duplicate `id="data"` table per session, and an axios GET
  for session terminate.
- `sources.html` — a table whose CA certificate column is the
  last HTML-in-attribute Bootstrap popover.
- `audit.html` — a per-console event table with a stray
  `</td>`, unclosed `<tr>`s, and a `total_events` context value
  it never renders.

This phase converts all three, after which no template extends
`base.html` and nothing loads Bootstrap, jQuery or axios — the
old static tree becomes fully dead code, though its deletion
deliberately stays in phase 9 with the rest of the teardown.

## What the survey found

The master plan's phase 6 section makes four factual claims and
the survey confirmed all four against the tree — a real result
worth stating plainly:

- The duplicate ids: `sessions.html:25` emits
  `id="data"` inside the per-session loop, so every session
  after the first duplicates it. (`sources.html:4` and
  `audit.html:6` carry single `id="data"`s — not duplicates,
  but equally dead; phase 5 dropped the consoles one.)
- The sources CA popover: `sources.html:24-29`, complete with
  `data-bs-html="true"` and a `<pre>` inside an attribute.
- The audit markup defects: `audit.html:26` closes a `</td>`
  that was never opened, and no row in the loop closes its
  `<tr>`.
- `total_events`: passed by the view (`api.py:331`), rendered
  nowhere. The phase 1 smoke test even annotates it
  ("Phase 6 of the master plan will start using it",
  `test_api_html.py:186-188`).

Findings beyond the master plan's text:

1. **No canonical sfui work is needed.** Every primitive this
   phase wants already exists in the vendored `sf.css` at
   `f17a74c`: `.sf-disclosure--panel` (sf.css:589 — the comment
   says "a stack of these is an accordion"), `.sf-code`
   (sf.css:504, pre-wrap, scrolling, monospaced — made for a PEM
   blob), `.sf-empty` (557), `.sf-footnote` (566), the badge
   family (446-497), `.sf-section-header` (275), and the
   `.sf-num` / `.sf-push-right` utilities (663-671). This is the
   first template phase with no sfui-repo step and no re-vendor;
   `.sfui-commit` must be untouched at the end.
2. **The sources HTML context is the sharpest secret risk in
   the whole conversion.** The JSON twin deletes `password`
   before serialising (`api.py:816-819`); the HTML branch
   (`api.py:808-814`) passes `db.get_sources()` rows unfiltered,
   and `Source.export()` (`db.py:88-103`) includes `password`,
   `url`, `username`, `project_name`, `user_domain_id` and
   `project_domain_id`. The template must name exactly the six
   rendered columns and nothing else. The phase 1 smoke test
   already asserts `sekrit-source-password` never renders
   (`test_api_html.py:178`).
3. **The audit page's console context carries `ticket`.** The
   view passes `db.get_console(source, uuid)` (`api.py:330`),
   whose non-detailed export (`db.py:199-211`) includes
   `ticket` and `host_subject`. The current smoke test mocks a
   console fixture *with* the sekrit ticket but never asserts
   its absence from the audit page — step 6e closes that gap.
4. **The sessions context can have holes.** `db.get_sessions()`
   (`db.py:543-575`) returns a dict keyed by session id; a
   channel whose session has no console token gets
   `session_consoles.get(session_id, {})` (`db.py:565`), i.e. an
   entry with `channels` but no `name` or `source`. The current
   template survives because Jinja's default undefined renders
   as empty; the conversion must preserve that tolerance (no
   `.strftime()` or filters on possibly-missing values).
5. **The sessions terminate axios call dies here, not in
   phase 9.** `sessions.html:54-62` is the last axios use; the
   consoles two-step pattern replaces it, and
   `SessionTerminate.get` already 302s back to `/session` for
   HTML Accepts (`api.py:792-793`), so plain navigation works
   with no script beyond the shared confirmation handler.
6. **A defect from the master plan's Situation catalogue was
   already fixed and should not be re-fixed:** the audit page's
   nav highlight (`get_nav_items('Audit')` naming a nonexistent
   tab) was corrected to `'Consoles'` by phase 4 (`ad0465f`;
   today `api.py:333`).
7. **Kerbside PR #292 is in the merge queue** and rewrites
   `AGENTS.md`, `ARCHITECTURE.md` and
   `tools/preview-templates.py` (removing historical phase
   references from the source tree). This branch was cut from
   `bce2717`, before it merged. The management session rebases
   this branch onto `develop` after #292 lands and **before any
   implementation step runs** — the overlap is exactly the
   files step 6e edits.

The master plan's phase 6 section was accurate, so no
corrections were needed at source; an "Amended in phase 6
planning" note was added there recording the two additions
(template-only phase, and the shared terminate include), as this
plan's registration commit.

## Mission

Convert `sessions.html`, `sources.html` and `audit.html` onto
`base-sfui.html`, fixing the catalogued markup defects, retiring
the last popover, the last axios call and the duplicate ids, and
finally rendering `total_events` — without changing
`kerbside/api.py` at all, and leaving every JSON twin
byte-identical.

## Design decisions

### 1. The sessions accordion is a stack of `.sf-disclosure--panel`

One `<details class="sf-disclosure sf-disclosure--panel">` per
session, the first `open` (matching today's expanded first
panel), with `id="session-{{ session_id }}"` so phase 7's
morphdom preserves open state — same id discipline as phase 5.
The summary keeps today's sentence ("Session X for name in
source"). The body is the two-step terminate button followed by
the channels table (`.sf-table sf-table--striped` in
`.sf-table-scroll`). sf.css built this exact shape: the panel
variant's comment says a stack of them is an accordion.
Bootstrap's collapse JS, `loop.index` ids and
`aria-expanded` bookkeeping all go; `<details>` does it natively.

### 2. The two-step terminate script becomes a shared include

`consoles.html:210-268`'s delegated handler is already fully
generic — it keys on `data-kb-terminate-url` and touches nothing
page-specific. It moves **verbatim** to
`kerbside/api/templates/includes/two-step-terminate.html`
(script tag and all), and both `consoles.html` and
`sessions.html` render it with `{% include %}` in their scripts
block. One copy means phase 8 rewrites one line to a fetch POST
in one file. The include stays inline in the page (no new
static file): the master plan keeps inline scripts until the
Future-work CSP lands, `static/js/` is a Bootstrap graveyard
phase 9 deletes wholesale, and `templates/icons/` already set
the include-subdirectory precedent. The reasoning a reviewer is
most likely to test: duplication across only two pages was the
alternative, and it loses because phase 8 must edit every copy
of the fire line and a missed copy is a silent CSRF regression.

### 3. Sources: CA disclosure, badges for state

The certificate column becomes
`<details class="sf-disclosure" id="cacert-{{ source.name }}">`
(source names are unique keys — `db.py:149-155` queries by
name), summary "Certificate", body the PEM in
`<pre class="sf-code">` — which caps its own height and scrolls,
so a full chain cannot stretch the row. A source with no
`ca_cert` (the column is nullable) gets
`<span class="sf-badge sf-badge--dim">none</span>` instead of a
disclosure over an empty pre, mirroring phase 5's dim zero. The
`errored` column stops printing raw `True`/`False` and renders
`<span class="sf-badge sf-badge--red">errored</span>` or
`<span class="sf-badge sf-badge--green">ok</span>`. This is the
decision most likely to be argued with as scope creep; it is
in scope because the master plan's mission for these pages is a
style pass and a raw Python boolean in a table cell is exactly
the kind of thing the pass exists to fix. `last_seen` is
formatted `%Y-%m-%d %H:%M:%S` like every timestamp since
phase 5, guarded for None (renders a dim "never").

### 4. Audit heading: section header, identifiers not uppercased

The `<h3>` becomes the `.sf-section-header` idiom:
`<h2>Audit events for {{ console.source }} {{ console.name }}</h2>`
with the UUID pushed right in
`<span class="sf-status-line sf-push-right">{{ console.uuid }}</span>`.
The constraint that drove this shape: `.sf-section-header h2`
applies `text-transform: uppercase`, and a UUID is an identifier
an operator may read back or copy — it must sit outside the h2
so it is never case-mangled. (The name and source in the h2 do
get the uppercase treatment; they are labels, not identifiers,
and appear in their true case in the table and elsewhere.)

### 5. `total_events` renders as a footnote

Under the audit table:
`<p class="sf-footnote">Showing the {{ events|length }} most
recent of {{ total_events }} events.</p>`, and when
`total_events > events|length` it carries a
`<a href="?limit=200">See more events</a>` link — the same
limit the consoles page's audit disclosure already links to.
Pagination proper stays in the master plan's Future work.

### 6. Tables and ids

All three pages: `.sf-table sf-table--striped` inside
`.sf-table-scroll`, `id="data"` dropped, every `<tr>` closed,
the stray `</td>` gone, timestamps strftime'd. Numeric columns
(`PID`, `Connection ID`, client port) may take `.sf-num`; the
implementer judges per column.

### 7. Empty states

A sessions page with no sessions currently renders a blank
region. It becomes
`<p class="sf-empty">There are no active sessions.</p>`. The
sources and audit tables keep their headers when empty,
consistent with the consoles page.

## Key facts front-loaded for the sub-agents

- Conventions: local classes are `kb-` in `{% block styles %}`;
  no color literals, no `!important`, no id selectors in CSS;
  Python single quotes, 120-char wrap. Read `base-sfui.html`,
  `consoles.html` and `login.html` before writing anything.
- Context shapes: sessions is a **dict** keyed by session id,
  each value `{name, source, channels: [...]}` with `name` and
  `source` possibly absent (survey finding 4); channels rows
  carry `node, pid, created, client_ip, client_port,
  connection_id, channel_type` (plus unrendered ids —
  `db.py:481-494`). Sources rows carry the six rendered fields
  **plus the secrets in survey finding 2**. The audit page gets
  `console` (with `ticket` — render only `source`, `name`,
  `uuid`), `events` (fields as today's columns), and
  `total_events`.
- Nav context: sessions passes `get_nav_items('Sessions')`,
  sources `'Sources'`, audit `'Consoles'` — all correct today;
  the templates just inherit the base nav.
- All three views pass `refresh=True` and `when`; the base
  renders the meta refresh and footer from them. Nothing to do
  in the pages.
- `kerbside/api.py` is not edited in this phase. At all.
- The smoke tests (`kerbside/tests/unit/test_api_html.py`) are
  marker-based, never markup-based; keep that discipline for
  new assertions. Fixtures: `SESSIONS`, `SOURCE`, `CONSOLE`,
  `AUDIT_EVENT` at the top of the file.
- Preview tool: `tools/preview-templates.py <page> <dir>` run
  with `.tox/py3/bin/python`; PAGES entries document their own
  shape (`tools/preview-templates.py:53-96`). Serve over HTTP,
  not file://; headless Chromium is dark by default,
  `--blink-settings=preferredColorScheme=2` for light.

## Repository and branch logistics

- Worktree `../kerbside-wt-pages`, branch
  `sfui-conversion-phase-06` (naming matches phase 5's
  `sfui-conversion-phase-05`), cut from `develop` at `bce2717`.
- **Before step 6a runs:** kerbside PR #292 (docs overhaul,
  in the merge queue at planning time) must have merged and
  this branch been rebased onto the resulting `develop`. The
  management session does the rebase; expected conflicts: none
  in this plan's files, since #292 does not touch
  `docs/plans/`.
- No sfui checkout is needed; nothing under
  `kerbside/api/static/sfui/` changes.

## Execution

One commit per step, subjects as given. All work by sub-agents
in the phase worktree; the management session reviews actual
files and commits.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | low | sonnet | none | In the kerbside worktree: move the two-step terminate script out of `kerbside/api/templates/consoles.html` (`{% block scripts %}`, the whole `<script>...</script>` element, lines 210-268 today) **verbatim** into a new file `kerbside/api/templates/includes/two-step-terminate.html`, and make consoles.html's scripts block exactly `{% include 'includes/two-step-terminate.html' %}`. Byte-for-byte script move: do not reword comments, rename variables or reindent. No other file changes. Verify with `.tox/py3/bin/python tools/preview-templates.py consoles <dir>` before and after: the two rendered pages must be identical except for insignificant whitespace (diff them). `tox -eflake8` and `tox -epy3` must pass unedited. Commit subject: `Share the two-step terminate confirmation.` |
| 6b | medium | sonnet | none | In the kerbside worktree: rewrite `kerbside/api/templates/sessions.html` to extend `base-sfui.html`, implementing design decisions 1, 2 and 7 of `docs/plans/PLAN-sfui-conversion-phase-06-remaining-pages.md` — read that file first, then `base-sfui.html`, `consoles.html` and sf.css's disclosure/table/button sections. `{% block title %}Kerbside sessions{% endblock %}`. Structure: if the `sessions` dict is empty, `<p class="sf-empty">There are no active sessions.</p>`; otherwise one `<details class="sf-disclosure sf-disclosure--panel" id="session-{{ session_id }}">` per session, first one `open` (`loop.first`), summary preserving today's sentence (`sessions.html:13-14`), body containing a two-step terminate button (`<button type="button" class="sf-btn sf-btn--sm sf-btn--danger" data-kb-terminate-url="/session/{{ session_id }}/terminate">Terminate session</button>` — the pattern from `consoles.html`) and then the channels table: `.sf-table-scroll` wrapping `.sf-table sf-table--striped`, no `id`, the same five columns rendering the same fields as today (`sessions.html:27-43`), every `<tr>` closed. The context dict may lack `name`/`source` for orphan channels — plain `{{ }}` interpolation only, no filters or method calls on those two. Scripts block: `{% include 'includes/two-step-terminate.html' %}`. The axios function and the old accordion go entirely; `grep -n axios kerbside/api/templates/` must return nothing. Local styling if needed is `kb-` classes; likely none is needed. `kerbside/api.py` and all other templates byte-unchanged. `tox -eflake8` and `tox -epy3` must pass with existing tests unedited. Commit subject: `Convert the sessions page to sfui.` |
| 6c | medium | sonnet | none | In the kerbside worktree: rewrite `kerbside/api/templates/sources.html` to extend `base-sfui.html`, implementing design decisions 3 and 6 of `docs/plans/PLAN-sfui-conversion-phase-06-remaining-pages.md` — read that file first, then `consoles.html` for the conventions. `{% block title %}Kerbside sources{% endblock %}`. **The context rows are unfiltered `db.get_sources()` exports carrying `password`, `url`, `username` and project fields; render exactly these six columns and nothing else: `name`, `type`, `last_seen`, `seen_by`, `errored`, `ca_cert`.** The smoke test asserts `sekrit-source-password` never renders. Table: `.sf-table-scroll` wrapping `.sf-table sf-table--striped`, no `id`, rows closed. `last_seen`: `{{ source.last_seen.strftime('%Y-%m-%d %H:%M:%S') }}` when set, else `<span class="sf-badge sf-badge--dim">never</span>`. `errored`: `sf-badge--red` "errored" when true, `sf-badge--green` "ok" when false. `ca_cert`: when set, `<details class="sf-disclosure" id="cacert-{{ source.name }}">` with summary `Certificate` and body `<pre class="sf-code">{{ source.ca_cert }}</pre>`; when not, a dim `none` badge. No scripts block. `kerbside/api.py` and all other templates byte-unchanged; `tox -eflake8` and `tox -epy3` pass with existing tests unedited. Commit subject: `Convert the sources page to sfui.` |
| 6d | medium | sonnet | none | In the kerbside worktree: rewrite `kerbside/api/templates/audit.html` to extend `base-sfui.html`, implementing design decisions 4, 5 and 6 of `docs/plans/PLAN-sfui-conversion-phase-06-remaining-pages.md` — read that file first, then `consoles.html`. `{% block title %}Kerbside console audit{% endblock %}`. **The `console` context dict carries the secret `ticket`; render only `console.source`, `console.name` and `console.uuid`.** Heading per decision 4: `<div class="sf-section-header"><h2>Audit events for {{ console.source }} {{ console.name }}</h2><span class="sf-status-line sf-push-right">{{ console.uuid }}</span></div>` — the UUID must not sit inside the h2, whose uppercase transform would mangle it. Table: `.sf-table-scroll` wrapping `.sf-table sf-table--striped`, no `id`, today's six columns and fields (`audit.html:8-25`), timestamps as `{{ event.timestamp.strftime('%Y-%m-%d %H:%M:%S') }}`, every `<tr>` closed and the stray `</td>` gone. After the table, decision 5's footnote: `<p class="sf-footnote">Showing the {{ events|length }} most recent of {{ total_events }} events.{% if total_events > events|length %} <a href="?limit=200">See more events</a>.{% endif %}</p>`. No scripts block. `kerbside/api.py` and all other templates byte-unchanged; `tox -eflake8` and `tox -epy3` pass with existing tests unedited. Commit subject: `Convert the audit page to sfui.` |
| 6e | medium | sonnet | none | In the kerbside worktree, after 6a-6d: (1) Add `sessions`, `sources` and `audit` entries to `PAGES` in `tools/preview-templates.py` per its documented shape, each patching `kerbside.api.verify_jwt_in_request` to return `(None, {})` plus its db calls: sessions patches `kerbside.api.db.get_sessions` with a deepcopy of the test module's `SESSIONS`; sources patches `kerbside.api.db.get_sources` with two rows — a deepcopy of `SOURCE`, and a variant named differently with `errored: True` and `ca_cert: None` so one screenshot shows both badge states and the no-cert path; audit (route `/console/sf1/u-1234/audit`) patches `kerbside.api.db.get_console` (deepcopy of `CONSOLE`), `kerbside.api.db.count_audit_events` (return 42) and `kerbside.api.db.get_audit_events` (a few `AUDIT_EVENT` deepcopies). (2) In `kerbside/tests/unit/test_api_html.py`, marker-based only, without editing existing tests: extend nothing — **add** a test asserting the audit page never renders `sekrit-hypervisor-ticket` (the fixture already carries it) and renders the `total_events` value (use a distinctive count like 4242); a test that an empty sessions dict renders the no-active-sessions message; and a test that a source with `ca_cert: None` renders (200, name present). (3) Docs: `docs/development.md`'s preview section lists the three new page arguments; `ARCHITECTURE.md`'s template notes now say all five pages extend `base-sfui.html` and `base.html` is unreferenced pending phase 9's deletion; `AGENTS.md`'s conversion status note likewise. Respect however PR #292 restructured those files — update the current text, do not restore old phrasing. `tox -eflake8` and `tox -epy3` pass. Commit subject: `Add previews, tests and docs for phase 6 pages.` |

## Verification

Run by the management session after 6e, before the outcome
commit:

1. `tox -eflake8 && tox -epy3` — all tests pass.
2. `git diff develop --stat -- kerbside/api.py
   kerbside/api/static/` is empty: no API change, no static
   change, `.sfui-commit` untouched.
3. Mechanical greps over `kerbside/api/templates/`:
   `extends "base.html"` → nothing; `axios` → nothing;
   `data-bs-` → nothing; `id="data"` → nothing;
   `armedTerminate` → only in
   `includes/two-step-terminate.html`; color literals
   (`#[0-9a-f]`, `rgba(`) → nothing.
4. Render all three pages with `tools/preview-templates.py`
   into a scratch dir; screenshot each in both palettes
   (port 8098; light needs
   `--blink-settings=preferredColorScheme=2`) and actually
   look at them: accordion first-open, badges, CA disclosure
   open state, footnote, section header with the UUID in true
   case, 400px-wide narrow render of the sessions page.
5. Re-run the phase 5 DOM-event harness approach against the
   rendered sessions page: rewrite `data-kb-terminate-url` to
   `#fired`, drive real clicks in headless Chromium, assert
   arm → confirm fires, cross-arm restores, focusout disarms.
6. Rendered-page hygiene: `<tr>` open/close counts match on
   all three pages; the secrets (`sekrit-source-password`,
   `sekrit-hypervisor-ticket`) absent from their rendered
   fixtures (the tests also assert this).

## Success criteria

* `kerbside/api.py`, `base.html`, `base-sfui.html`,
  `login.html`, `consoles.html` (after 6a) and everything
  under `kerbside/api/static/` byte-unchanged from step 6a's
  completion onward; JSON twins therefore untouched.
* No template extends `base.html`; Bootstrap classes, popover
  attributes, axios and duplicate/dead `id="data"` are absent
  from `kerbside/api/templates/`.
* Exactly one copy of the two-step terminate script exists,
  in `includes/two-step-terminate.html`, included by exactly
  two pages.
* `total_events` is rendered by the audit page and asserted by
  a test; the ticket-absence and password-absence assertions
  cover audit and sources.
* All three pages render correctly in both palettes from the
  preview tool, and the sessions two-step terminate passes the
  DOM-event assertions.
* `tox -eflake8` and `tox -epy3` pass; the daily `sfui-vendor`
  audit stays green (nothing vendored changed).

## Risks

- **PR #292 lands mid-phase and 6e edits stale docs.** The
  logistics section gates all implementation on rebasing after
  it merges; the management session owns the rebase and
  re-checks `git log origin/develop..HEAD` shows only phase
  commits.
- **The include extraction (6a) subtly changes the consoles
  page.** Mitigated by the before/after rendered diff written
  into the step brief, and by the phase 5 DOM harness re-run
  in verification.
- **A secret leaks into a converted template.** Two layers:
  the briefs name the exact renderable fields, and the smoke
  tests assert the sentinel secrets are absent (6e adds the
  missing audit-page assertion).
- **The section header's uppercase transform mangles the
  UUID.** Decision 4 places it outside the `<h2>`; the
  reviewer checks the screenshot for true-case rendering.
- **Orphan-session entries crash the accordion.** The brief
  forbids filters/method calls on `name`/`source`; Jinja's
  default undefined then renders empty, as today.

## Future work recorded here

- The pluralisation wart family grows: "There are 1 active
  authentication tokens" (phase 5's Outcome) is joined by any
  singular counts on these pages. A single wording pass over
  all five converted pages once phase 9 lands.
- Audit pagination UI (the master plan already lists it): the
  footnote's `?limit=200` link is a stopgap, not paging.
- `.sf-status-line` gets its first real use here (the audit
  UUID); the polling status line phase 7 adds will be its
  second — check they coexist visually in phase 7.

## Back brief

Before executing any step of this plan, back brief the operator
on the plan and how the intended work aligns with it. No step
in this phase is expensive to redo, so no additional gate
beyond the standing back brief is required; the one sequencing
hard-stop is the PR #292 rebase in the logistics section.

## Outcome

Executed 2026-08-14. PR #292 had merged, so the branch was
rebased onto `develop` at `98bef5c` (clean, no conflicts)
before any implementation ran; the plan commit is `498f5ff`.
All five steps landed, each as its own commit after
management-session review of the actual files:

- 6a `e0256f6` — the two-step terminate script moved verbatim
  into `includes/two-step-terminate.html` (extraction verified
  byte-for-byte; the rendered consoles page differed only in
  the dynamic footer timestamp).
- 6b `0edecc4` — sessions page: disclosure-panel accordion,
  first open, stable `session-` ids, shared terminate include,
  tolerant interpolation for orphan sessions.
- 6c `d4fdf29` — sources page: named-fields-only table over
  the secret-carrying context, red/green errored badges,
  guarded `last_seen`, CA disclosure with `sf-code` body,
  dim `none`/`never` badges.
- 6d `7901fa8` — audit page: section header with the UUID
  outside the uppercase h2, defects fixed, `total_events`
  footnote with the `?limit=200` link.
- 6e `0f1779f` — preview entries for all three pages, three
  new marker-based smoke tests (audit ticket-absence and
  total_events, empty sessions, no-CA source), docs updated
  in the post-#292 voice.

### Corrections to this plan, found while executing it

- The verification grep "`axios` → nothing over
  `kerbside/api/templates/`" cannot hold until phase 9: the
  deliberately untouched `base.html` still loads axios. The
  greps were scoped to exclude `base.html`; every converted
  template and the include are clean.
- The 6b brief under-specified design decision 6: it did not
  name the Created column's strftime. Caught in management
  review; the ProxyChannel constructor always stamps
  `created`, so the unguarded call matches the consoles-page
  precedent.

### Verification

All items from the Verification section ran clean:
`tox -eflake8` and `tox -epy3` (131 tests, up from 128);
`kerbside/api.py` and `kerbside/api/static/` byte-identical
to `develop`; mechanical greps clean (no `extends
"base.html"`, no `data-bs-`, no `id="data"`, one
`armedTerminate` copy, no color literals); all three pages
rendered and eyeballed in both palettes plus the forced-open
CA disclosure and a 400px sessions render (the channels
table scrolls inside its panel); rendered `<tr>` counts
balanced and both secret sentinels absent; and the DOM-event
harness against the rendered sessions page passed all five
assertions (arm, focusout disarm, re-arm, fire on second
activation via the rewritten `#fired` URL, no spurious
disarm before the fire).
