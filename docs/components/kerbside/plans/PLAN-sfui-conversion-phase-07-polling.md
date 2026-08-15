# sfui conversion phase 7: morphdom polling

This is a phase plan under `PLAN-sfui-conversion.md`; read that
master plan's Prompt, Agent guidance and Administration sections
first — they apply here and are not repeated. This phase was
planned at **medium effort**, as the master plan recommends,
though its single implementation step is briefed at high effort:
the polling loop has three state-preservation hazards (open
disclosures, the armed terminate button, unchanged-content
identity) that are cheap to get subtly wrong.

## Situation

Every authenticated page still refreshes with
`<meta http-equiv="refresh" content="30">` — a full page reload
every 30 seconds that closes any disclosure an operator opened,
disarms a half-confirmed terminate, resets scroll, and drops
text selection. Phases 5 and 6 laid the groundwork to fix this:
every disclosure carries a stable, row-derived id, and every
listener on rendered content is delegated (document-level for
the two-step terminate), so DOM nodes can be replaced or
preserved under the page scripts without rewiring. morphdom
2.7.7 is already vendored in the sfui set. This phase replaces
the meta refresh with a fetch-and-morph poll.

## What the survey found

One load-bearing correction and several structural facts:

1. **The master plan's phase 7 claim that "morphdom preserves
   the `open` attribute on matched nodes" is false.** morphdom
   2.7.7 has no special-casing of `<details>` at all (its only
   special cases are form-field state); its attribute sync
   copies the incoming node's attributes over the live node's,
   which would *close* every disclosure the operator opened and
   *re-open* the server-rendered defaults (the sessions page
   renders its first panel `open` on every response). The
   preservation the master plan wants requires an
   `onBeforeElUpdated` hook (`morphdom-umd.js:344,513`) copying
   the live `open` state onto the incoming element before the
   sync. Corrected at source in the master plan's phase 7 note
   as part of this planning commit.
2. **There is no content wrapper to morph.** `base-sfui.html:97`
   renders `{% block content %}` bare inside `.sf-container`,
   between the nav and the footer. The morph needs a stable
   target element wrapping the block.
3. **The morph region is script-free on all four polling
   pages.** consoles, sessions, sources and audit all keep their
   scripts in `{% block scripts %}` outside the content block
   (verified by grep), so morphing the content wrapper can never
   duplicate or destroy a live script.
4. **The armed terminate button is a real morph hazard.** The
   two-step include's state (`armedTerminate`, showing
   "Confirm?") lives in the DOM node's text. A morph would
   restore the incoming label while `armedTerminate` still
   points at the node — the button would *look* unarmed but fire
   on the next click. The hook must skip the armed node.
5. **The reference pattern is adjacent but not identical.** The
   conductor dashboard (`private-ci
   conductor/templates/dashboard.html:1115-1126,2646-2705,2921`)
   polls a JSON endpoint and repaints per-panel; kerbside's
   pages are single server-rendered documents, so the simpler
   shape is: fetch the page's own URL with `Accept: text/html`,
   parse with `DOMParser`, morph the one content wrapper. The
   directly transferable pieces are the unchanged-content
   short-circuit (compare the last-applied HTML string and skip
   the morph — `paintPanel`'s `lastPanelHtml`), morph-not-
   innerHTML, and the status-line failure text ("Update failed,
   retrying...").
6. **The dashboard's reload-after-auth-failures behaviour does
   not transfer.** conductor reloads after three auth-shaped
   failures to bounce through SSO. Kerbside has no SSO bounce:
   an expired JWT on these routes returns a JSON 401 error, so a
   reload would replace a readable (if stale) page with an error
   body. The master plan's Non-goals defer session-expiry UX;
   the poll therefore just reports staleness and keeps retrying.
7. **A base-sfui comment is now stale.** `base-sfui.html:99-102`
   says `.sf-status-line` is "deliberately left unused: it is
   the home for the polling status line a later phase adds" —
   phase 6 gave it a first use (the audit page UUID). The
   comment is updated by 7a when the footer is touched; the
   phase 6 plan's future-work note to check the two uses coexist
   visually is folded into this phase's verification.
8. morphdom is vendored at 2.7.7, unmodified, exposing a global
   `morphdom` (UMD), documented in the sfui README's vendored
   dependencies (`kerbside/api/static/sfui/README.md:344-348`);
   nothing sfui-side changes in this phase and `.sfui-commit`
   must be untouched.

## Mission

Replace the meta-refresh full-page reload with a 30-second
fetch-and-morph poll on every page rendered with `refresh=True`,
preserving operator state (open and closed disclosures, the
armed terminate button, scroll, selection) across polls, and
reporting staleness instead of failing loudly — without touching
`kerbside/api.py`, the JSON twins, or anything vendored.

## Design decisions

### 1. Morph target: a `<main id="kb-content">` wrapper

`base-sfui.html` wraps `{% block content %}` in
`<main id="kb-content">`. `<main>` is the semantically correct
landmark for the page's unique content (one per page, and it
also improves the AT landmark story for free). The poll morphs
this element with `childrenOnly: true`, so the wrapper itself —
and every listener delegated above it — is never replaced. The
login page gets the wrapper too (harmless, consistent markup);
it simply never polls because `refresh` is falsy there.

### 2. The poll is a Jinja include, not a static file

`templates/includes/poll.html` carries the whole
`<script>...</script>`, included from `base-sfui.html` gated on
`{% if refresh %}` — exactly the two-step terminate include
precedent, and for the same reasons: inline scripts are the
status quo until the future CSP work, `static/js/` is a
Bootstrap graveyard phase 9 deletes, and one copy serves every
page. The master plan's future-work entry for a canonical
`sf-poll.js` in sfui explicitly waits until kerbside's loop and
the dashboard's both exist; this include is kerbside's half of
that evidence.

### 3. The loop: fetch own URL, parse, short-circuit, morph

Every 30 seconds (matching the meta refresh it replaces), and
skipping ticks while `document.hidden`: fetch
`window.location.href` with `Accept: text/html` (same-origin,
cookies flow by default); on a non-OK or non-HTML response or a
rejected fetch, mark the page stale and try again next tick; on
success, extract `#kb-content` from a `DOMParser` document,
compare its innerHTML string against the last one applied, skip
the morph entirely when unchanged (most ticks — this is the
dashboard's `lastPanelHtml` trick, and it means an idle page
never churns), and otherwise morph with the hook in decision 4.

### 4. One `onBeforeElUpdated` hook, two preservations

```javascript
onBeforeElUpdated: function (fromEl, toEl) {
    if (fromEl === window.armedTerminate) {
        return false;  // never repaint a half-confirmed button
    }
    if (fromEl.tagName === 'DETAILS') {
        if (fromEl.open) { toEl.setAttribute('open', ''); }
        else { toEl.removeAttribute('open'); }
    }
    return true;
}
```

- The armed-button skip closes the hazard from survey finding 4:
  a repaint must never make an armed button look unarmed while
  it would still fire on the next click. Skipping the node keeps
  the "Confirm?" label and the arm state consistent; if the
  session vanishes server-side the whole disclosure is removed
  as an unmatched node, which leaves `armedTerminate` pointing
  at a detached element — every path from there (disarm on
  focusout, a later arm of another button) is harmless.
- The `<details>` clause copies the *live* open state onto the
  incoming element in both directions, so an operator's opened
  disclosure stays open and their deliberately closed first
  sessions panel stays closed, whatever the server's default.
  It applies to all details, id'd or not; the stable ids from
  phases 5-6 are what make morphdom match the right elements
  in keyed lists.

### 5. The footer stamp becomes the poll's status line

The footer keeps its server-rendered initial text but the
timestamp moves into a span the poll owns:
`Content refreshed at <span class="sf-status-line"
id="kb-refresh-status">{{ when.strftime('%H:%M:%S') }}</span>.`
On each successful poll the script writes the client-clock time
into the span; after a failed poll it writes "stale since" plus
the last success time, and keeps retrying silently (survey
finding 6: no reload, no error page). `.sf-status-line` gives
the digits the monospace non-jitter treatment it was built for,
and the stale base-sfui comment about it being unused is
rewritten to point here.

### 6. The meta refresh dies in the same commit

`base-sfui.html:18-20` goes. No page may carry both mechanisms
even transiently: a meta reload racing the poll would discard
the state the poll exists to preserve. `refresh=True` context
now means "this page polls" — `kerbside/api.py` is not touched.

## Key facts front-loaded for the sub-agents

- Files: `base-sfui.html` (wrapper, footer span, drop meta
  refresh, morphdom script tag, include the poll, fix the
  stale comment), new `templates/includes/poll.html`. Nothing
  else changes in 7a.
- Load `morphdom-umd.js` with a classic
  `<script src="/static/sfui/morphdom-umd.js"></script>` in the
  head next to the theme script only when `refresh` (no module
  loader; it is UMD and defines a global).
- `window.armedTerminate` is a top-level var in
  `includes/two-step-terminate.html` — a bare script, so it is
  a global; pages without the terminate include (sources,
  audit) simply have it undefined and the hook's first test is
  always false there.
- The rendered previews are static files: in a preview, the
  poll fetches the page's own static URL, gets identical
  content, and short-circuits — harmless by construction, but
  worth one sentence in `docs/development.md`.
- Conventions: `kb-` ids/classes; no color literals; Python
  files untouched, so flake8 is trivially green; tests must
  stay green unedited except where 7b adds new ones.
- The smoke-test discipline is markers-not-markup; the two
  assertions 7b adds (no `http-equiv="refresh"` anywhere in a
  rendered polling page; present on none of login's renders
  either) are behavioural absences that survive any future
  markup change, which is why they are acceptable.

## Repository and branch logistics

- Worktree `../kerbside-wt-polling`, branch
  `sfui-conversion-phase-07`, cut from `develop` at `8fd908c`
  (the phase 6 merge).
- No sfui checkout needed; nothing vendored changes.
- Merge CI note: issues #308 (Kolla lane broken by upstream
  neutron packaging) and #309 (oVirt host-install flake) were
  open at planning time; if still open when this phase's PR
  queues, expect the same triage outcome.

## Execution

One commit per step. All work by sub-agents in the phase
worktree; the management session reviews actual files and
commits.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | high | opus | none | In the kerbside worktree: implement design decisions 1-6 of `docs/plans/PLAN-sfui-conversion-phase-07-polling.md` exactly — read that file in full first, then `kerbside/api/templates/base-sfui.html`, `includes/two-step-terminate.html`, and private-ci's `conductor/templates/dashboard.html` lines 1090-1130 and 2640-2705 (at `/srv/kasm_profiles/mikal/vscode/src/shakenfist/private-ci`) for the reference idioms. Changes: (1) `base-sfui.html` — wrap the content block in `<main id="kb-content">`, delete the `{% if refresh %}` meta-refresh block, add the morphdom classic script tag in the head gated on `refresh`, convert the footer timestamp into the `kb-refresh-status` span per decision 5, rewrite the now-stale `.sf-status-line` comment (base-sfui.html:99-102) to describe both real uses, and add `{% if refresh %}{% include 'includes/poll.html' %}{% endif %}` after the existing scripts. (2) New `kerbside/api/templates/includes/poll.html` — one script implementing decision 3's loop and decision 4's hook verbatim, with comments in the established voice explaining the short-circuit, the two preservations, and why failure means stale-not-reload. Constraints: `kerbside/api.py`, all five page templates, the terminate include and everything vendored byte-unchanged; no listeners attached to content nodes; `tox -eflake8` and `tox -epy3` pass with tests unedited. Commit subject: `Replace the meta refresh with morphdom polling.` |
| 7b | medium | sonnet | none | In the kerbside worktree, after 7a: (1) Add two smoke tests to `kerbside/tests/unit/test_api_html.py` without editing existing ones: a rendered `refresh=True` page (reuse the consoles fixtures/mocks) contains no `http-equiv="refresh"` and does contain `kb-refresh-status`; the login render contains neither `http-equiv="refresh"` nor `kb-refresh-status` (it does not poll). (2) Docs: `docs/development.md` — in the preview section, one sentence noting that previewed pages carry the poll script and it harmlessly short-circuits against static files; `ARCHITECTURE.md` — the template notes now describe the polling include and the kb-content morph target; `AGENTS.md` — the conversion-status note records that pages poll via morphdom instead of meta refresh. Keep the current (post-#292) voice; AGENTS/ARCHITECTURE must not duplicate docs/. `tox -eflake8` and `tox -epy3` pass. Commit subject: `Test and document the polling conversion.` |

## Verification

Run by the management session after 7b, before the outcome
commit. The DOM harness is the heart of it — polling cannot be
verified by reading code:

1. `tox -eflake8 && tox -epy3`.
2. `git diff develop --stat -- kerbside/api.py
   kerbside/api/static/` is empty.
3. Render the sessions and consoles pages with
   `tools/preview-templates.py`; serve over HTTP.
4. Harness, against the rendered sessions page in headless
   Chromium (the phase 5/6 technique: append a script, drive
   real events, assert via `document.title`): stub
   `window.fetch` to return a handcrafted second document in
   which the first session panel is server-default `open`, a
   new session row exists, and the armed button's label
   differs; then (a) open a disclosure the server renders
   closed, close the server-open first panel, arm a terminate
   button; (b) let one poll tick run (call the poll function
   directly rather than waiting 30s); assert: the new row
   appeared (morph ran), the operator-opened disclosure is
   still open, the operator-closed panel is still closed, the
   armed button still reads "Confirm?" and `armedTerminate`
   still points at it, and `kb-refresh-status` was rewritten.
   (c) Make the stub fetch reject; tick; assert the status
   span reports staleness and the content is untouched. (d)
   Tick with identical content; assert a marker property set
   on a live node beforehand survives (the short-circuit
   skipped the morph).
5. Screenshot the audit page in both palettes: the UUID
   status-line and the footer status-line coexist without
   visual collision (the phase 6 future-work check).
6. Mechanical: `grep -rn 'http-equiv' kerbside/api/templates/`
   returns only `base.html` (untouched until phase 9);
   `morphdom` referenced only from `base-sfui.html`'s script
   tag and the vendored file itself.

## Success criteria

* The meta refresh is gone from `base-sfui.html`; every
  `refresh=True` page polls every 30 seconds and login does
  not; `kerbside/api.py` and everything vendored are
  byte-unchanged.
* All four harness assertions in Verification 4 pass: morph
  applies changes, both disclosure states survive, the armed
  button survives untouched, failure reports staleness without
  destroying content, and unchanged content skips the morph.
* The two new smoke tests pass; existing tests pass unedited.
* The daily `sfui-vendor` audit stays green (nothing vendored
  changed).

## Risks

- **The hook silently not running** (a typo'd option name means
  morphdom ignores it and everything *looks* fine until an
  operator loses an open disclosure). Caught by harness
  assertions (b) — they fail if the hook is inert.
- **Duplicate listeners accumulating across polls.** Prevented
  structurally: all page listeners are delegated on `document`
  and the poll include runs once; the wrapper is never
  replaced (`childrenOnly`). The harness's repeated ticks
  would surface double-fire on the terminate path.
- **Session expiry turning the page into a silent zombie.**
  Accepted and explicit: the status line pins the staleness
  time; real expiry UX is the master plan's recorded future
  work.
- **Preview screenshots hitting the poll.** Short-circuits by
  construction (static file fetch returns identical content);
  documented in 7b.

## Future work recorded here

- The canonical `sf-poll.js` decision (already in the master
  plan): with kerbside's loop and the dashboard's both live,
  the commonality is now observable — revisit after this phase
  merges.
- Session-expiry UX (already in the master plan): the stale
  status line is the interim behaviour.
- A `Retry-After`-style backoff on repeated failures if the
  30s hammer ever bothers a struggling daemon; not worth
  complexity now.

## Back brief

Before executing any step of this plan, back brief the operator
on the plan and how the intended work aligns with it. Step 7a
is the phase; if its shape needs arguing (the wrapper element,
the include placement, the hook semantics), argue before it
runs — the harness in Verification 4 is deliberately specified
first so the implementation has an executable target.

## Outcome

Executed 2026-08-14. Both steps landed after management-session
review of the actual files:

- 7a `649f0f6` — the polling implementation, exactly per design
  decisions 1-6, with two accepted implementer refinements:
  `window.kbPoll` returns its fetch chain so a harness can await
  a tick, and a fetched document without `#kb-content` counts as
  a failed poll rather than a silent no-op.
- 7b `784257f` — the two behavioural smoke tests (133 tests
  total) and the docs updates.

### Verification

All items ran clean: `tox -eflake8` and `tox -epy3`;
`kerbside/api.py` and everything under `kerbside/api/static/`
byte-identical to `develop`; `http-equiv` remains only in the
untouched `base.html`; morphdom referenced only from
`base-sfui.html` and the poll include. The DOM harness against
the rendered sessions page passed all eleven assertions: morph
applies a new panel, live node identity is kept, the status
stamp updates, an operator-closed server-open panel stays
closed, an operator-opened server-closed panel stays open, the
armed terminate button survives a morph untouched while a
neighbouring cell in the same panel updates (proving the morph
genuinely ran around the skip), unchanged content short-circuits
(a marker property on a live node survives), and a failed poll
reports "stale since" while leaving content intact. The audit
page was screenshotted in both palettes: the UUID status-line
and the footer stamp coexist without collision, and the stamp
showed live client-clock time — the poll running for real
against the static preview and short-circuiting as documented.

One harness note for honesty's sake: the first harness run
passed its preservation assertions vacuously because a
management-session bug served the same variant twice and the
short-circuit skipped the morph; the `neighbour-morphed`
assertion existed precisely to catch that, failed, and the
fixed harness then passed everything with a real morph. The
implementing agent's own independent 13-assertion harness had
already passed beforehand.
