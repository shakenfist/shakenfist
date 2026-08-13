# sfui conversion phase 5: the consoles page

Master plan: `PLAN-sfui-conversion.md`. Planned at high effort
per the master plan's phase notes, because this is the largest
and densest page: the popover replacement, both dropdown
button-groups, the icon set and the two-step terminate
confirmation all land here, and phase 6 copies whatever this
phase decides.

**Two master-plan claims are amended here** (see What the
survey found): the icons cannot merely be re-cut with
`fill="currentColor"` — an SVG loaded through `<img>` never
inherits the page's color, so following the theme requires
inlining them into the template — and the connect dropdown is
not rebuilt as a dropdown at all. One small canonical addition
is pulled into sfui (`.sf-btn` must suppress the base-layer
link underline so anchors can be buttons), so this phase
touches two repositories like phases 3 and 4 did.

## Situation

Phases 1 to 4 built the safety net, the materials and the
chrome. What exists today:

* `base-sfui.html` carries the converted head, brand header,
  `.sf-nav`, theme toggle, logout button and refresh footer,
  with `{% block title %}`, `{% block styles %}`,
  `{% block content %}` and `{% block scripts %}` hooks and
  the `kb-` local-class convention. Only `login.html` extends
  it.
* `consoles.html` (102 lines) still extends `base.html`. It
  renders one table row per console from the **unfiltered**
  `db.get_consoles()` dicts, with four action cells: an audit
  popover, a connect dropdown, a token popover and a terminate
  dropdown.
* The vendored `sf.css` already ships every primitive this
  page needs: `.sf-table` (+ `--striped`, `.sf-table-scroll`),
  `.sf-btn` (+ `--primary`, `--danger`, `--sm`, `--lg`),
  `.sf-badge` and its colors, and `.sf-disclosure`
  (+ `--panel`). sfui deliberately has no dropdown-menu
  primitive.
* `kerbside/tests/unit/test_api_html.py` renders the consoles
  page against a fixture and doubles as a leak guard: the
  fixture carries `'ticket': 'sekrit-hypervisor-ticket'` and
  the test asserts it never appears in the body.
* `tools/preview-templates.py` renders converted pages for
  screenshotting; its `PAGES` table has only `login` and
  documents exactly how an authenticated page joins it.

The defect catalogue for this page, all confirmed in the
survey:

* **Every data row is an unclosed `<tr>`**
  (`consoles.html:18`; the loop ends at `:99` without one),
  and the audit popover's `<li>` at `:42` is never closed
  either.
* **Both popovers stuff multi-line HTML into
  `data-bs-content`** — the audit one (`:32-53`) includes a
  Jinja `for` loop and a "See more events" link; the token
  one (`:67-83`) is explanation prose with `<br/>` line
  breaks.
* **Both action groups are Bootstrap dropdowns** (`:56-64`,
  `:86-97`), so they die with `bootstrap.bundle.min.js`. The
  terminate items are plain GET links (issue #133 — phase 8's
  problem, not this one).
* **The icons are `<img>` references to
  `/static/icons/*.svg`** with hardcoded black glyphs (none
  of the six SVGs carries any `fill` attribute), invisible
  against the dark palette. `bug.svg` and `change.svg` are
  referenced by no template at all.

## What the survey found

The master plan's phase 5 section was checked claim by claim
against `develop` (`893ee42`). Most of it holds: the unclosed
`<tr>`s, the two popovers, the two dropdowns, the two dead
icons and `static/icons/README.md` are all exactly where it
says. Three findings correct or sharpen it, and the first two
are now amended in the master plan's phase 5 note as part of
this planning commit:

1. **"Re-cut the Material Symbols icons to
   `fill="currentColor"`" cannot work as written.** All four
   live icons are loaded via `<img src="/static/icons/...">`
   (`consoles.html:52,58,82,88`), and an SVG document loaded
   through `<img>` is an isolated context: `currentColor`
   inside it resolves against the SVG's own root, not the
   embedding page, so it renders black regardless of theme.
   Following the theme requires the markup to be part of the
   page. Decision 5 below inlines them as Jinja includes and
   retires `static/icons/` entirely.
2. **"Rebuild the connect and terminate button groups"
   undersells a decision.** sfui has no dropdown primitive
   (deliberately — see `sf.css`'s component notes), and the
   connect dropdown hides exactly two always-available
   actions behind a click. Decisions 1 and 3 flatten connect
   into two visible anchor-buttons and turn terminate into a
   `.sf-disclosure`, which is the pattern the master plan
   already chose for every other popover.
3. **`.sf-btn` on an anchor hover-underlines.** The base
   layer gives every `.sf-page` link an underline and accent
   color on hover (`sf.css:126-134`), and `.sf-btn` never
   declares `text-decoration`, so the connect links styled as
   buttons would underline on hover. `.sf-nav a`
   (`sf.css:249-254`) shows the canonical way out. This is a
   design-system gap, not a kerbside quirk, and follows the
   `.sf-btn--lg` precedent from phase 4: the fix is canonical
   (decision 2), not a `kb-` override.

Beyond those, the survey pinned down facts the briefs need:
the HTML view (`api.py:275-284`) passes `db.get_consoles()`
unfiltered, and `Console.export()` (`db.py:199-211`) includes
`ticket` and `host_subject`, so the template must render
exactly the fields it renders today and no more; `api.py`
needs **no changes at all** this phase (the view already
passes `navitems`, `refresh` and `when`); and nothing outside
`consoles.html` references `/static/icons/` or the table's
`id="data"`, so both can go.

## Mission

Convert `consoles.html` onto `base-sfui.html`: a valid,
theme-following table with the audit and token detail in
native disclosures, visible connect actions, a terminate
disclosure with the decided two-step confirmation, and inline
theme-aware icons — leaving the JSON twin byte-identical, the
other four pages untouched, and the smoke tests passing
unedited.

## Design decisions

### 1. The connect dropdown becomes two visible anchor-buttons

The dropdown hides two always-available downloads ("via
proxy", "directly") behind an icon-only toggle. Flattened,
they are two compact real anchors styled
`.sf-btn .sf-btn--sm` with the text "Proxy" and "Direct" —
real links because they are real GET downloads of `.vv`
files, keeping middle-click, "copy link address" and
operation without JavaScript. The connect icon goes with the
dropdown: two labelled buttons need no disambiguating glyph.

### 2. `.sf-btn` suppresses the link underline, canonically

`sf.css` gains `text-decoration: none` on `.sf-btn` in the
`sfui.components` layer, with the same comment pattern
`.sf-nav a` uses (the base layer underlines every page link
on hover; this layer is later, so it wins with no
specificity contest). The README's button section gains a
sentence saying `.sf-btn` works on anchors and when an
anchor-button is appropriate (a genuine navigation or
download), and `demo.html` gains one anchor-button beside
the existing buttons so both palettes show it. Small, but
canonical for the same reason `.sf-btn--lg` was: the second
consumer needing it proves it is not page styling.

### 3. The terminate dropdown becomes a disclosure of two-step buttons

The terminate cell becomes a `.sf-disclosure` whose
`<summary>` carries the session count and the sessions icon
(the affordance the current button shows), rendered only when
`console.sessions` is non-empty; with no sessions the cell
shows a dim `0` (`.sf-badge .sf-badge--dim`) and nothing to
disclose — replacing today's `disabled` dropdown toggle.

Inside, one button per action, exactly the actions the
dropdown offers today: "Terminate all sessions", then one per
session id. Each is a `<button type="button"
class="sf-btn sf-btn--sm sf-btn--danger"
data-kb-terminate-url="...">` implementing the decided
two-step: the first activation arms the button (its label
becomes "Confirm?"), the second navigates to the existing GET
terminate URL via `window.location`. Leaving the armed button
(blur or pointer leaving it) disarms it. Buttons, not
anchors, for two reasons: an armed/disarmed control is not a
link, and phase 8 converts the navigation to a `fetch` POST
by changing only the handler's last line.

The handler is a single delegated listener on the content
container in `{% block scripts %}`, not per-button listeners:
it survives phase 7's morphdom replacement of the rows
without re-wiring, and it is one place for phase 8 to edit.
Terminate stays a GET this phase — phase 8 owns the
POST/CSRF conversion, and starting it here would drag the CI
lane scripts and tempest plugin into a template phase.

### 4. The popovers become disclosures with stable ids

Both popovers move into `.sf-disclosure` elements in their
cells, closed by default:

* **Audit**: the summary is the audit icon plus a visible
  label; the body is the paragraph, the (now correctly
  closed) `<ul>` of up to 20 events, and the existing "See
  more events" link to
  `/console/{{ source }}/{{ uuid }}/audit?limit=200`.
  Event lines render `timestamp`, optional `session_id`,
  optional `channel` and `message` — the same four fields as
  today. Timestamps render
  `{{ event.timestamp.strftime('%Y-%m-%d %H:%M:%S') }}`:
  the microseconds are noise, but unlike the footer stamp
  these can span days, so the date stays.
* **Tokens**: the summary is the count plus the key icon;
  the body is the current explanation prose as real
  paragraphs (dropping the `<br/>` line breaks — the
  disclosure panel wraps text properly, which the popover's
  fixed width could not).

Every disclosure gets a stable id derived from the row —
`audit-{{ console.source }}-{{ console.uuid }}` and
`tokens-{{ console.source }}-{{ console.uuid }}` — so phase
7's morphdom preserves the `open` attribute on matched nodes
and an operator reading events does not have them slam shut
on the next poll. This is the id discipline the master plan's
phase 7 note asks phase 6 to establish; it starts here.

### 5. Icons inline as Jinja includes, and `static/icons/` retires

The four live icons (`audit`, `connect`, `sessions`,
`tokens`) move to `kerbside/api/templates/icons/*.svg`, each
gaining `fill="currentColor"` on the `<svg>` root,
`width="21" height="21"` (the size the page uses today,
replacing the per-`<img>` attributes) and
`aria-hidden="true"` (every icon in this page sits beside
text or inside a labelled control, so announcing it would
duplicate the label — the same reasoning as the base
template's empty logo alt). Templates use
`{% include 'icons/audit.svg' %}` where they used `<img>`.
Flask's template loader serves anything under `templates/`,
and an include is verbatim markup, so the SVGs need no Jinja
syntax.

`static/icons/README.md` moves with them to
`templates/icons/README.md`, keeping the Material Symbols
provenance links for the four survivors, dropping the entries
for `bug` and `change` (deleted; referenced by no template),
and gaining a paragraph on why the icons are inline template
includes rather than static files (the `<img>`/`currentColor`
finding above). `static/icons/` is then deleted entirely in
the conversion commit — the converted `consoles.html` was its
last referrer.

`connect.svg` does not move: decision 1 drops its only use,
and an icon with no referrer is exactly the dead weight this
conversion exists to remove. It is deleted alongside `bug`
and `change` — three icons move, three delete — with its
provenance recorded under Future work in case a connect
glyph is ever wanted again.

### 6. The table is `.sf-table--striped` in a scroll wrapper

The table becomes `class="sf-table sf-table--striped"` inside
a `.sf-table-scroll` wrapper — ten columns of ids, ports and
counts is precisely the monospaced, striped, scroll-don't-
squash case those primitives exist for. The `colspan="4"`
"Admin" header survives as-is over the four action columns.
The `id="data"` is dropped: nothing references it (the smoke
tests are marker-based and no script uses it), and phase 7
will pick its morph container on the base template, not on a
per-page table.

### 7. This phase re-vendors both consumers

Step 5a lands a canonical sfui commit, and the daily
`sfui-vendor` audit in shakenfist/development checks every
consumer's `.sfui-commit` against canonical HEAD — so the
moment 5a merges, kerbside **and private-ci** are both one
commit stale, and private-ci files an issue at the next 06:00
UTC run. Step 5b therefore re-vendors both, exactly as the
phase 4 tail did (private-ci `fd964ef` is the precedent).
The private-ci re-vendor is a one-commit push to its
`master`; the kerbside re-vendor lands in this phase's
branch.

## Key facts front-loaded for the sub-agents

* **The Accept-negotiated twins.** `Consoles.get()`
  (`kerbside/api.py:275-298`) shares `/console` between HTML
  and JSON on the `Accept` header. The HTML branch passes
  `db.get_consoles()` **unfiltered**: each dict carries
  `ticket` (the hypervisor auth secret) and `host_subject`
  alongside the rendered fields. Render exactly: `name`,
  `source`, `uuid`, `hypervisor`, `hypervisor_ip`,
  `insecure_port`, `secure_port`, `token_count`,
  `sessions` (a list of session-id strings), and per audit
  event `timestamp`, `session_id`, `channel`, `message`.
  Never iterate a context dict's keys. The smoke test
  asserts `sekrit-hypervisor-ticket` never renders.
* **`api.py` does not change in this phase.** The view
  already passes `navitems=get_nav_items('Consoles')`,
  `refresh=True` and `when`; the JSON twin must stay
  byte-identical, and the cheapest proof is an untouched
  `api.py`.
* **The smoke tests must keep passing untouched.** They
  assert fixture markers (`testvm`, absence of the ticket),
  never markup. If one needs editing to pass, the conversion
  is wrong, not the test.
* **Terminate URLs stay exactly**
  `/console/{{ console.source }}/{{ console.uuid }}/terminate`
  and `/session/{{ session_id }}/terminate`, both GET, both
  redirecting back to `/console` (`test_api_html.py:186-196`
  proves the redirect). Phase 8 owns changing the verb.
* **The `kb-` convention.** Local classes are `kb-`, never
  `sf-` (`base-sfui.html:22-33`). Page styles go in
  `{% block styles %}`, page scripts in
  `{% block scripts %}`. No color literals, no `!important`,
  no id selectors — tokens or `color-mix()` of tokens only.
* **The vendored copy is read-only.** Nothing under
  `kerbside/api/static/sfui/` is ever edited in place;
  `tools/vendor.sh --check` from a canonical checkout must
  exit zero after every step except between 5a's merge and
  5b's re-vendor.
* **The preview tool's extension point.**
  `tools/preview-templates.py:59-74` documents the shape: a
  `consoles` entry is `'route': '/console'` with two
  patches — `kerbside.api.verify_jwt_in_request` returning
  `(None, {})` and `kerbside.api.db.get_consoles` returning
  fixtures. Reuse the test module's `CONSOLE` fixture
  (import `kerbside.tests.unit.test_api_html`) and add a
  second console with `sessions: []` and `token_count: 0` so
  both terminate states render in one screenshot.

## Repository and branch logistics

Two repositories, as in phases 3 and 4:

* **sfui**: branch `btn-anchor-underline`, then a pull
  request. Design-system changes reach `develop` via review,
  never directly.
* **kerbside**: branch `sfui-conversion-phase-05` in the
  `kerbside-wt-consoles` worktree, off `develop` at
  `893ee42`.
* **private-ci**: no branch; one re-vendor commit to
  `master` in step 5b, matching how every private-ci
  re-vendor has landed.

**Step 5b may only run once 5a has merged into sfui's
`develop`** — vendoring from a feature branch is the mistake
the sfui README now forbids. Steps 5c and 5d do not wait for
it: 5d consumes no new class by name (the `.sf-btn`
underline fix changes behaviour, not names), but sequence 5b
before 5d in the branch's history anyway so the converted
page never ships against a copy that hover-underlines its
buttons.

Note the session-memory warning about concurrent sessions
sharing these clones: check `git status` immediately before
staging, and stop if the working tree changes unexpectedly.

## Execution

Five steps. The kerbside commits land in this order on
`sfui-conversion-phase-05`; 5a is an sfui pull request and 5b
also touches private-ci. All work is done by sub-agents; the
management session reviews the files themselves, not the
sub-agent's summary.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | In the sfui repository on branch `btn-anchor-underline`: add `text-decoration: none;` to the `.sf-btn` rule in `sf.css`'s `sfui.components` layer, with a comment in the `.sf-nav a` style explaining that the base layer underlines every page link on hover and this later layer wins without a specificity contest (read `sf.css:245-254` for the exact precedent). In the README's button documentation, add that `.sf-btn` works on `<a>` as well as `<button>`, and when each is right: an anchor when the action is a real navigation or download, a button when it changes state. Add one anchor-button (`<a class="sf-btn sf-btn--sm" href="#">`) to `demo.html`'s button row so both palettes render it. No other changes; no color literals; run `pre-commit run --all-files` if the repo has hooks, otherwise eyeball the diff for trailing whitespace. |
| 5b | low | haiku | none | **Only after 5a has merged to sfui `develop`.** From a clean sfui checkout of `develop`: run `tools/vendor.sh /srv/kasm_profiles/mikal/vscode/src/shakenfist/kerbside-wt-consoles/kerbside/api/static/sfui` and stage the whole directory in the kerbside worktree (`.sfui-commit` is a dotfile shell globs skip — add the directory, not a glob). Then run `tools/vendor.sh /srv/kasm_profiles/mikal/vscode/src/shakenfist/private-ci/conductor/static/sfui` and stage the same way there (private-ci commits directly to `master`; check `git status` first, another session may share the clone). Confirm `tools/vendor.sh --check <target>` exits zero for both. No template changes anywhere. |
| 5c | low | sonnet | none | In the kerbside worktree: create `kerbside/api/templates/icons/` containing `audit.svg`, `sessions.svg` and `tokens.svg` copied from `kerbside/api/static/icons/`, each edited so the `<svg>` root carries `fill="currentColor"`, `width="21"`, `height="21"` (replacing `height="48"`/`width="48"`) and `aria-hidden="true"`, keeping the `viewBox` untouched. Write `kerbside/api/templates/icons/README.md`: the Material Symbols provenance list from `static/icons/README.md` reduced to these three, plus a paragraph explaining the icons are inline Jinja includes because an SVG loaded via `<img>` resolves `currentColor` in its own isolated document and can never follow the page theme. Do not touch `static/icons/` yet — `consoles.html` still references it until 5d. No template changes. |
| 5d | high | opus | none | In the kerbside worktree: rewrite `kerbside/api/templates/consoles.html` to extend `base-sfui.html`, implementing design decisions 1, 3, 4, 5 and 6 of `docs/plans/PLAN-sfui-conversion-phase-05-consoles.md` exactly — read that file first, then the current `consoles.html`, `base-sfui.html` and `login.html` (the conventions), and `sf.css`'s table/button/badge/disclosure sections. `{% block title %}Kerbside consoles{% endblock %}`. Structure: `.sf-table-scroll` wrapping a `.sf-table sf-table--striped` (no id), same columns as today including the `colspan="4"` Admin header, every `<tr>` and `<li>` closed. Cells render exactly the fields the current template renders — the context dicts are unfiltered `db.get_consoles()` rows carrying secrets (`ticket`), and the smoke test will catch a leak. Audit and token cells per decision 4 (stable disclosure ids; strftime the audit timestamps `%Y-%m-%d %H:%M:%S`); connect cell per decision 1 (two `.sf-btn sf-btn--sm` anchors, "Proxy" and "Direct", to the existing `.vv` URLs); terminate cell per decision 3 (disclosure with summary count + sessions icon when sessions exist, `.sf-badge sf-badge--dim` zero otherwise; two-step buttons carrying `data-kb-terminate-url`; one delegated click-and-blur handler in `{% block scripts %}` arming, disarming and navigating — keyboard-operable: Enter twice on a focused button must terminate). Icons via `{% include 'icons/....svg' %}`. Any local styling is `kb-` classes in `{% block styles %}`; no color literals, no `!important`, no id selectors. Delete `kerbside/api/static/icons/` entirely in this commit (all six SVGs and the README — 5c relocated the survivors; `connect`, `bug` and `change` die per decision 5) and verify `grep -rn 'static/icons' kerbside/` returns nothing. `base.html`, the other four templates and `kerbside/api.py` must be byte-unchanged. `tox -eflake8` and `tox -epy3` must pass with the smoke tests unedited. |
| 5e | medium | sonnet | none | In the kerbside worktree: (1) Add a `consoles` entry to `PAGES` in `tools/preview-templates.py` per the documented shape (`tools/preview-templates.py:59-74`): route `/console`, patching `kerbside.api.verify_jwt_in_request` to return `(None, {})` and `kerbside.api.db.get_consoles` to return two consoles — `copy.deepcopy` of `kerbside.tests.unit.test_api_html.CONSOLE`, plus a variant with `sessions: []`, `token_count: 0` and a different name/uuid — so one screenshot shows both terminate states. (2) Add one smoke test to `kerbside/tests/unit/test_api_html.py`, marker-based like its neighbours: a console with no sessions renders (200, its name present, ticket absent). Do not edit existing tests. (3) Docs: in `docs/development.md`'s "Previewing templates" section note that `consoles` is now a valid page argument; in `ARCHITECTURE.md`, update the template tree/description for `consoles.html` extending `base-sfui.html` and `templates/icons/`; in `AGENTS.md`, update the sfui-conversion note (consoles converted; sessions/sources/audit still on `base.html` until phase 6). `tox -eflake8` and `tox -epy3` must pass. |

Management-session review for this phase, beyond the standard
checklist in the master plan:

* Read the rewritten `consoles.html` in full — it is the file
  phase 6 copies, and no linter looks at it.
* `grep -nE 'rgba\(|#[0-9a-f]{3,8}|!important|data-bs-|btn-|dropdown|popover' kerbside/api/templates/consoles.html`
  returns nothing (the `#` grep tolerates only `href`
  fragments if any; there should be none).
* In the rendered preview output, `<tr` and `</tr` counts are
  equal, and `<li` and `</li` counts are equal — the two
  defect classes this phase fixes, checked mechanically.
* `sekrit-hypervisor-ticket` and `host_subject`'s value do
  not appear in the rendered preview body.
* Nothing under `kerbside/api/static/sfui/` was edited in
  place: `tools/vendor.sh --check` exits zero (kerbside and
  private-ci both, after 5b).
* `git diff develop..HEAD --stat` touches only
  `consoles.html`, `templates/icons/`, the deleted
  `static/icons/`, the re-vendored `static/sfui/`,
  `tools/preview-templates.py`, `test_api_html.py`, the three
  doc files and the plan files.
* The two-step terminate: Tab to a terminate button, Enter,
  label reads "Confirm?", Enter again navigates; Tab away
  disarms. Checked in a real browser session, not just read.
* Both palettes at 1280px and the narrow layout at 400px,
  per the recipe below, for a fixture with sessions and one
  without.

## Verification

The visual check follows the phase 4 recipe, now with real
fixtures behind it:

    .tox/py3/bin/python tools/preview-templates.py consoles /tmp/preview
    (cd /tmp/preview && python3 -m http.server 8098) &

    chromium --headless --disable-gpu --no-sandbox \
        --hide-scrollbars --window-size=1280,1000 \
        --virtual-time-budget=4000 \
        --screenshot=/tmp/consoles-dark.png \
        http://localhost:8098/consoles.html

    chromium --headless --disable-gpu --no-sandbox \
        --hide-scrollbars --window-size=1280,1000 \
        --virtual-time-budget=4000 \
        --blink-settings=preferredColorScheme=2 \
        --screenshot=/tmp/consoles-light.png \
        http://localhost:8098/consoles.html

(8098, not 8099: the phase 4 outcome notes a stale
`http.server` on a reused port serving convincing 404s. Pick
a fresh port and kill the server afterwards.) Headless
Chromium reports dark by default; `preferredColorScheme=2` is
light. Serve over HTTP — the components are ES modules. The
disclosures render closed in a static screenshot, so also
screenshot once with `open` added by hand via a second
preview run or DevTools, or accept the interactive check in a
real browser as covering the open state.

The interactive paths — disclosure toggling, the two-step
arm/disarm/fire, both connect downloads — need a browser
against a running kerbside or the preview page (the terminate
navigation will 404 against `http.server`, which is fine: the
assertion is that the *second* activation navigates and the
first does not).

## Success criteria

* `consoles.html` extends `base-sfui.html`; `base.html`,
  `sessions.html`, `sources.html`, `audit.html`, `login.html`
  and `kerbside/api.py` are byte-unchanged.
* The management-session greps above return nothing, and the
  rendered page balances `<tr>`/`</tr>` and `<li>`/`</li>`.
* The page renders correctly in both palettes, including the
  icons following the theme (visibly not black-on-dark), with
  disclosures closed by default and openable.
* A console with no sessions shows a dim zero and no
  terminate control; one with sessions shows the count and
  the two-step buttons; the two-step is keyboard-operable and
  disarms on blur.
* `kerbside/api/static/icons/` no longer exists;
  `kerbside/api/templates/icons/` holds exactly three SVGs
  (each `fill="currentColor"`) and a README with provenance
  and the inline-not-img rationale;
  `grep -rn 'static/icons' kerbside/` finds nothing.
* sfui `develop` ships the `.sf-btn` underline suppression,
  documented and demonstrated; both consumers' `.sfui-commit`
  records that commit and `tools/vendor.sh --check` passes
  for both.
* `tox -eflake8` and `tox -epy3` pass; the pre-existing smoke
  tests are unedited; the new no-sessions smoke test passes.
* `tools/preview-templates.py consoles` renders both fixture
  consoles, and both palette screenshots were produced and
  actually looked at.
* The daily `sfui-vendor` audit stays green for kerbside and
  private-ci on the first run after 5b lands.

## Risks

* **The `currentColor` fix renders wrong in some other way**
  (wrong size, misaligned baseline) once inline. Mitigation:
  the visual check is per-palette and per-state, and the
  icons keep their 21px box; the management session reads
  the screenshots, not the diff, for this.
* **The two-step confirmation is fiddly state machinery in
  a delegated handler** — arm, disarm-on-blur, fire — and a
  bug here terminates a session on a single click.
  Mitigation: the handler is small and reviewed line by
  line; the review checklist includes the keyboard path and
  the disarm path in a real browser; and until phase 8 the
  action is still a plain GET a reviewer can see in the URL
  bar before it fires. Residual risk accepted: there is no
  JS unit-test harness in kerbside and building one for
  this is out of proportion.
* **Disclosures in table cells may interact badly with the
  striped/hover rows** (a tall open disclosure makes one
  zebra stripe huge). Accepted: it reads fine or it does
  not, which is what the both-palettes screenshot review is
  for; if it is ugly, the fallback is moving the disclosure
  body content below the row — a phase-6 pattern decision to
  take only if forced.
* **Concurrent sessions share these clones** (private-ci
  especially). Mitigation: `git status` immediately before
  every staging step, stop on anything unexpected.
* **5b lands and private-ci's push is forgotten**, leaving
  the audit to file an issue at 06:00 UTC. Mitigation: 5b's
  brief does both consumers in one step, and the success
  criteria pin the audit staying green.

## Future work recorded here

* **A connect glyph**, if the flattened Proxy/Direct buttons
  ever want an icon again: the deleted `connect.svg`'s
  provenance (Material Symbols "computer") stays recorded in
  `templates/icons/README.md`'s history and this plan.
* **An `sf-icon` treatment in sfui** (inline SVG conventions,
  size classes) if a third consumer starts inlining icons;
  two data points are not yet a pattern, same rule as
  `.kb-header-controls` in phase 4.
* **The disclosure-in-table-row layout** may want a canonical
  opinion in `sf.css` once phase 6 puts disclosures in three
  more pages; watch whether the `kb-` styling this phase
  writes gets copied.
* **JS test coverage for the admin UI**: the two-step
  handler is the second inline script with real logic (after
  the login fetch). If phase 7's polling adds a third, a
  minimal JS test harness stops being out of proportion.

## Back brief

Before executing any step of this plan, back brief the
operator on your understanding of the plan and how the work
you intend to do aligns with it. Step 5d additionally gets
its own gate: before the sub-agent runs, restate the cell
structure (four action columns and what each contains) and
the two-step state machine to the operator, because 5d is
cheap to brief and expensive to redo.

## Outcome

All five steps landed as planned: 5a merged to sfui as
`f17a74c` (pull request #5), 5b re-vendored both consumers
(`ac9504b` here; private-ci `80af1aa`, pushed to master the
same hour so the 06:00 UTC audit never saw a stale copy), 5c
is `2529bfe`, 5d is `b6870eb`, 5e is `efe81dc`.

### Corrections to this plan, found while executing it

* **`.sf-btn-group` already existed** (`sf.css:435`), so the
  connect pair needed no `kb-` CSS at all — the plan's "if
  the primitives already look right bare, add no styles"
  hope came true for that cell.
* **`pointerleave` does not bubble**, so the delegated
  disarm listens for `pointerout` instead, guarded to
  `pointerType === 'mouse'`: a touch pointer is destroyed
  the instant the tap ends, and disarming on it would make
  the second tap impossible. The buttons have no child
  elements, so `pointerout` on them only ever means the
  pointer actually left.
* **The number-only summaries needed accessible names.**
  Review caught that the tokens and terminate summaries
  exposed a bare number once the icons went `aria-hidden`
  (the old markup's `alt` text had carried the meaning), so
  both gained an `aria-label`. The label matches the
  existing "1 active authentication tokens" pluralization
  bug in the visible prose rather than silently fixing one
  of the two; a wording pass is deliberately not this
  phase's business.

### Verification

Both palettes were rendered at 1280px from the new
`tools/preview-templates.py consoles` fixtures and read,
plus a forced-open variant and the 400px narrow layout: the
icons follow the theme in both palettes, an open audit
disclosure widens the table into its scroll wrapper exactly
as the accepted risk described (and reads fine), and the
narrow layout drops the header cluster into flow with the
table scrolling in place.

The two-step state machine was exercised with real DOM
events in headless Chromium against the rendered page, with
the terminate URL rewritten to a fragment so the navigation
is observable: arm relabels to "Confirm?", arming a second
button restores the first, `focusout` disarms, and only the
second activation of the same button navigates. All five
assertions passed.

The mechanical checks all come back clean: `<tr>`/`</tr>`
and `<li>`/`</li>` balance in the rendered page, the ticket
and `host_subject` never render, no `static/icons` reference
survives, the management-session grep finds no color
literal, Bootstrap residue or important flag, and
`tools/vendor.sh --check` passes for both consumers at sfui
HEAD `f17a74c`. `tox -epy3` runs 128 tests including the
new no-sessions smoke test; the pre-existing smoke tests
are byte-unedited.
