# sfui conversion phase 4: the new base and the login page

Master plan: `PLAN-sfui-conversion.md`. Planned at high effort
per the master plan's phase notes, because this phase sets the
visual language, the head order, the chrome and the local CSS
conventions that phases 5 to 7 copy without re-deciding.

**One master-plan decision is amended here** (design decision
1: the navigation is links, not `<sf-tabs>`), and that pulls a
small canonical addition into sfui, so this phase touches two
repositories like phase 3 did.

## Situation

Phases 1 to 3 built the safety net and the materials. What
exists today:

* Six templates, all extending `kerbside/api/templates/base.html`
  (77 lines): a hardcoded teal Bootstrap navbar, `axios`,
  `bootstrap.bundle.min.js`, a `<style>` block widening
  popovers, a stray `<link href="">`, a meta refresh, and a
  Bootstrap popover initialiser.
* `kerbside/api/static/sfui/` is a verbatim vendored copy of
  canonical sfui, carrying `tokens.css`, `sf.css`,
  `sf-theme.js`, the globe mark, `lit-core.min.js`,
  `morphdom-umd.js` and two components. Nothing in the
  templates references any of it yet, and `<body>` carries no
  `sf-page` class, so all of it is inert.
* `sf.css` already ships every content primitive this phase
  needs -- `.sf-page`, `.sf-container`, `.sf-header`,
  `.sf-footer`, `.sf-status-line`, `.sf-card`, `.sf-btn`,
  `.sf-field`, `.sf-label`, `.sf-input`, `.sf-form-error` --
  and reserves `.sf-tabs` and `.sf-theme-toggle` as class
  names.
* `kerbside/tests/unit/test_api_html.py` renders every HTML
  route and asserts on fixture-data markers only, never on
  markup. It is the safety net for everything below.

Three defects in the current chrome are in scope because they
live in the base template or the context that feeds it:

* **`<title>` is always empty.** `base.html:5` renders
  `{{ title }}`, and no view passes `title` -- a grep for
  `title=` across `kerbside/api.py` finds only the `.vv` file
  field at `:358`. Every page in the admin UI has a blank
  browser tab today.
* **The audit page highlights nothing.** `api.py:333` passes
  `get_nav_items('Audit')`, but `base_navitems`
  (`api.py:91-107`) contains only Sources, Consoles and
  Sessions, so no item is ever marked active on that page.
* **Logout is keyboard-dead.** `base.html:43` is an `<a>` with
  an `onclick` and no `href`, so it is not focusable and not
  activatable by keyboard. The active nav item is also
  `href="#"` (`base.html:36`), which discards the URL.

And one that is a policy question rather than a defect: the
login page renders the full navigation, offering links to
three protected pages to a user who has not authenticated.

## Mission

Introduce `base-sfui.html` beside the old base and convert
`login.html` onto it, so that kerbside has one page rendered
entirely in sfui -- correct head, brand chrome, both themes,
no Bootstrap, no axios -- while the other four pages keep
working unchanged on the old base. The chrome this phase
builds is the chrome phases 5 and 6 inherit, so it is
finished, not sketched: the title, the navigation, the theme
toggle, the logout control, the footer and the local CSS
conventions are all decided here.

## Design decisions

### 1. The navigation is links, not `<sf-tabs>`

The master plan says "`<sf-tabs>` for the Sources / Consoles /
Sessions navigation". Reading the component's implementation
against that use makes it the wrong choice, on four counts
(**amendment approved by the operator on 2026-08-11**, and
carried back into the master plan's mission and phase notes):

* **Arrow keys would navigate.** `sf-tabs._onKeydown`
  (`components/sf-tabs.js`) treats ArrowLeft/ArrowRight as
  selection, and `_select` fires `sf-tab-selected` for each
  one. A page that maps selection onto navigation would issue
  a page load per arrow keypress -- a keyboard trap on the
  primary navigation of the app. The ARIA automatic-activation
  tab pattern is fine when selection swaps a panel in the same
  document; it is hostile when selection is a page load.
* **`role="tablist"` / `role="tab"` is a lie here.** Those
  roles promise tab panels within this document. Site sections
  are links, and a screen reader should hear them as links.
* **It would stop being navigation.** Buttons in shadow DOM
  cannot be middle-clicked into a new tab, copied as a link,
  or opened by a keyboard user's "open in new tab", and they
  do nothing at all without JavaScript. The current template,
  for all its faults, uses real anchors.
* **It would need the nav marshalled into JavaScript.** The
  component contract puts complex values in properties, not
  attributes, so the Jinja `navitems` list would have to be
  serialised into an inline script to set `.tabs` -- where
  today it is a four-line `{% for %}` over anchors.

So the navigation stays anchors, and what it needs from sfui
is a stylesheet class, not a component: **`.sf-nav`**, added
to `sf.css` in the `sfui.components` layer, visually identical
to `<sf-tabs>` so the design system has one tab-strip look
whichever mechanism produces it. The active item is marked
with `aria-current="page"` and keeps its real `href`.

`<sf-tabs>` remains exactly right for what the conductor
dashboard uses it for -- switching panels inside one document
-- and this phase does not change it.

Two consequences worth stating:

* The class/element naming rule in sfui's README reserves
  `.sf-tabs` and `.sf-theme-toggle` as classes because they
  name elements. `.sf-nav` needs the inverse reservation:
  because navigation is deliberately CSS-only, there must
  never be an `<sf-nav>` element. That goes in the README with
  the rule it mirrors.
* `sf.css` gives every link in the page the accent colour and
  a hover underline (`:where(.sf-page) :where(a)`, in the
  `sfui.base` layer). `.sf-nav a` is in `sfui.components`,
  which is a later layer, so it wins regardless of specificity
  -- but it must then set `text-decoration: none` on hover
  explicitly, or nav items will underline.

### 2. The login page renders no navigation

Decided: no navigation and no logout control on the login
page. Offering three links that will 401, plus a logout for a
session that does not exist, is misleading. The theme toggle
stays -- it is a preference, not a privilege, and the login
page is exactly where a user first sees the colours.

The mechanism is the context, not the template: `Root.get()`
passes `navitems=[]` instead of `get_nav_items(None)`, and
`base-sfui.html` renders the nav row and the logout button
only `{% if navitems %}`. The alternative -- a separate
`authenticated` flag -- means editing five render calls to
express something the existing context already says. The nav
strip and the logout button are one thing, session chrome, and
`navitems` is the honest signal for it because the login page
is the only unauthenticated page in the app (the master plan's
non-goals keep it that way).

### 3. `<title>` becomes a template block

`base-sfui.html` renders `{% block title %}Kerbside{% endblock
%}` and each page overrides it; `login.html` sets "Kerbside
login". The `title` context variable is not resurrected -- no
view has ever passed it, and a page's own name is presentation
that belongs in the template. The convention for phases 5 and
6 is `Kerbside <section>`, lowercased: "Kerbside consoles",
"Kerbside sessions", "Kerbside sources", "Kerbside audit".

### 4. The header carries a control cluster, and local CSS is `kb-`

`sf.css` positions `.sf-header sf-theme-toggle` absolutely at
the top right. Kerbside needs the logout button beside it, so
the two go in a flex cluster and the toggle's positioning is
neutralised locally:

    .kb-header-controls {
        position: absolute;
        top: 0;
        right: 0;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .kb-header-controls sf-theme-toggle { position: static; }

Unlayered page rules beat every `sfui.*` layer whatever their
specificity, which is the mechanism `sf.css` was designed
around, so this needs no `!important` and no specificity
contest. The narrow-screen behaviour in `sf.css`'s
`max-width: 700px` block must be mirrored for the cluster, or
the toggle sits on top of the heading on a phone.

This also settles the local naming convention kerbside will
need through phases 5 and 6: **local classes are `kb-`**,
never `sf-`, so that a reader can tell at a glance whether a
class comes from the design system or from this app, and so
that no local class can ever collide with a future sfui one.

Logout is a `<button class="sf-btn sf-btn--sm">`, not an
anchor: it changes server state (`DELETE /auth`) and must not
be reachable by a GET.

`.kb-header-controls` is a plausible future `sf.css`
primitive; it is not promoted now because one consumer is not
evidence of a shared pattern. Recorded under Future work.

### 5. The refresh notice moves to the footer

The old base puts "Content refreshed at ..." in a
`fixed-bottom` div. It becomes a `.sf-footer`, rendered only
when `refresh` is true, so the login page has no empty
bordered footer. `{{ when }}` currently stringifies a
`datetime` with microseconds; the footer renders
`{{ when.strftime('%H:%M:%S') }}` instead, because the date is
noise on a page that reloads every 30 seconds.

`.sf-status-line` is deliberately left unused. It is the right
home for phase 7's "updated 12s ago", and claiming it now
would pre-empt that decision.

The `{% if refresh %}<meta http-equiv="refresh" content="30">`
carries over unchanged. Phase 7 replaces it with morphdom
polling; phase 4 is not the place to change refresh
behaviour, and `login.html` passes `refresh=False` anyway.

### 6. What `base-sfui.html` drops, and what it keeps

Dropped: `bootstrap.min.css`, `bootstrap.bundle.min.js`,
`axios.min.js`, the popover-width `<style>` block, the
Bootstrap popover initialiser, the `<link href="">`, and the
`navbar-brand` link to `https://shakenfist.com` -- an admin UI
does not send its operator to the marketing site, and the
reference treatment (the conductor dashboard) does not link
its lockup either.

Kept: the `{% block content %}` and `{% block scripts %}`
contract, so the unconverted pages' blocks keep meaning what
they mean when they move over. Added: `{% block title %}` and
`{% block styles %}` (in `<head>`, after the sfui
stylesheets), which phases 5 and 6 need for page-local CSS.

`kerbside/api/static/logo.svg`, `css/` and `js/` all stay in
the tree: the four unconverted pages still load them through
the old base. Phase 9 deletes them.

`base.html` itself is not touched. Two bases coexist for
phases 4 to 6; phase 9 deletes the old one and renames the new
one.

### 7. `login.html`: a real form, `fetch`, and an announced error

The current page is a Bootstrap card with an `onclick`
handler, a document-level `keypress` listener to make Enter
work, `<font color="red">` for errors, `type="username"`
(invalid, so it behaves as `text` by accident), no
`autocomplete` attributes, a `</span>` closing a `<p>`
(`login.html:9`), a stray `is-invalid` on a container
(`:26`), and Bootstrap 4 margin utilities (`ml-4`, `mr-4`)
that Bootstrap 5 does not implement -- so they have never done
anything.

The converted page is a `<form>` with a `type="submit"`
button, which makes the Enter-key listener unnecessary rather
than reimplementing it: the form's `submit` event handler
calls `preventDefault()` and does the fetch. Fields become
`.sf-field` / `.sf-label` / `.sf-input` inside a `.sf-card`,
with `type="text"` plus `autocomplete="username"` and
`type="password"` plus `autocomplete="current-password"`, both
`required`. The error region is a `.sf-form-error` with
`role="alert"`, so a failed login is announced rather than
silently painted red.

axios goes; `fetch` replaces it, preserving the existing
status-to-message mapping exactly (400 "Bad request.", 401
"Unauthorized.", 500 "Server error.", anything else "Unknown
error: <body>(<status>)", a rejected promise "Request
error.") and the redirect to `/source` on success.

## Key facts front-loaded for the sub-agents

* **The Accept-negotiated twins.** Every HTML view shares its
  URL with a JSON twin behind an `Accept` check, and the HTML
  branches receive **unfiltered** `kerbside/db.py` dicts --
  the JSON branches strip `ticket` and `password`, the
  template context does not. This phase's page has no such
  context, but the rule stands: name the fields you render,
  never iterate a context dict's keys.
* **`/auth` tolerates a missing content type.** The kwargs of
  `Auth.post` are injected by `shakenfist_utilities.api`'s
  `flask_get_post_body()`, which calls
  `flask.request.get_json(force=True)`. So a `fetch` that
  forgets `Content-Type: application/json` still works, and no
  test would catch the omission -- send the header anyway.
* **`Auth.delete` has no `@verify_token`,** so
  flask-jwt-extended's cookie CSRF protection does not apply
  to it and logout needs no CSRF header. Phase 8 owns CSRF;
  do not start it here.
* **`window.sfTheme` is available synchronously.**
  `sf-theme.js` is a classic script in `<head>`, so an inline
  script may read `sfTheme.preference` immediately. Setting
  `.preference` on `<sf-theme-toggle>` before its module has
  upgraded the element is safe -- Lit replays instance
  properties set pre-upgrade -- and this is exactly what
  `conductor/templates/dashboard.html:2820-2827` in private-ci
  does. Copy that wiring.
* **The nav markup that replaces `base.html:32-45`:**

      <nav class="sf-nav" aria-label="Sections">
        {% for navitem in navitems %}
        <a href="{{ navitem.href }}"
           {% if navitem.active %}aria-current="page"{% endif %}
           >{{ navitem.name }}</a>
        {% endfor %}
      </nav>

  Note the active item keeps its real `href`, unlike today's
  `href="#"`.
* **Asset URLs stay root-relative absolute paths**
  (`/static/sfui/...`). No `url_for()` -- a master-plan
  non-goal.
* **The smoke tests must keep passing untouched.** They assert
  fixture markers, not markup; "Username" and "Password"
  survive the login rewrite. If a smoke test needs editing to
  pass, something is wrong with the conversion, not the test.

## Repository and branch logistics

Two repositories, as in phase 3:

* **sfui**: branch `nav-links`, then a pull request. The
  consumers' CI only runs on a pull request, and a design
  system change that reaches `develop` untested is the habit
  phase 3 stopped.
* **kerbside**: branch `sfui-conversion-phase-04` in the
  `kerbside-wt-sfui` worktree, off `develop`.

**The re-vendor in step 4b may only run once step 4a has
merged into sfui's `develop`** -- vendoring from the branch is
exactly the mistake that made the last two re-vendors
necessary, and the rule is now written down in sfui's README.
The kerbside template steps (4c, 4d) do not have to wait for
it: they consume `.sf-nav` by name, and the class exists in
the vendored copy the moment 4b lands. Sequence them 4c/4d
while the sfui pull request is in review, then land 4b first
in the branch's history so no commit references a class the
tree does not contain.

There is also a re-vendor already pushed and awaiting a pull
request on `sfui-revendor-readme` (sfui's README text, from
the phase 3 tail). If that has not merged by the time 4b runs,
4b's re-vendor subsumes it and that branch should be dropped
rather than merged twice.

Note the session-memory warning about concurrent sessions
sharing these clones: check `git status` immediately before
staging, and stop if the working tree changes unexpectedly.

## Execution

Five steps, five commits, in this order. All work is done by
sub-agents; the management session reviews the files
themselves, not the sub-agent's summary.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | In the sfui repository on branch `nav-links`, add a `.sf-nav` primitive to `sf.css` in the `sfui.components` layer: a flex row of anchors with a bottom border, the active item marked `aria-current="page"` taking the accent colour and a 2px accent bottom border. **Derive every metric from `components/sf-tabs.js`'s `nav` and `button` styles** (gap, padding, font size, border widths, the dim-to-bright hover) so the two tab strips are pixel-identical, and add a comment in each file saying they must stay in step. Remember `:where(.sf-page) :where(a:hover)` adds an underline that `.sf-nav a:hover` must switch off. No colour literals, no `!important`, no id selectors; tokens or `color-mix()` of tokens only. Document the class in the README's "Page styles" section, including why navigation is CSS-only rather than a component (see design decision 1 of `docs/plans/PLAN-sfui-conversion-phase-04-base-login.md` in the kerbside repository -- read it first) and add the inverse naming reservation: `.sf-nav` is a class, so there must never be an `<sf-nav>` element. Add a nav strip to `demo.html` beside the existing `<sf-tabs>` so the two can be compared in both palettes. Run `pre-commit run --all-files`. |
| 4b | low | haiku | none | In the kerbside worktree at `/srv/kasm_profiles/mikal/vscode/src/shakenfist/kerbside-wt-sfui`, re-vendor sfui: run `tools/vendor.sh <worktree>/kerbside/api/static/sfui` from a clean sfui checkout of `develop` **after step 4a has merged there**, and stage the whole directory -- `.sfui-commit` is a dotfile that shell globs skip, so add the directory, not a glob. No other file changes; no template touches. Confirm `tools/vendor.sh --check` then exits zero. |
| 4c | high | opus | none | In the kerbside worktree, add `kerbside/api/templates/base-sfui.html` implementing design decisions 1 to 6 of `docs/plans/PLAN-sfui-conversion-phase-04-base-login.md` exactly. Do not modify `base.html` or any other template: nothing extends the new base yet, and that is deliberate. Correct head (`<html lang="en">`, `charset`, `viewport`, `{% block title %}`, then `sf-theme.js` as a classic script, `tokens.css`, `sf.css`, then `components/sf-theme-toggle.js` as a module, then `{% block styles %}`); `<body class="sf-page">` wrapping a `.sf-container`; `.sf-header` with the globe mark from `/static/sfui/shakenfist-logo.svg` (`alt=""` -- the heading text carries the name) and the `.kb-header-controls` cluster; the `.sf-nav` row and the logout button both gated `{% if navitems %}`; `{% block content %}`; `{% block scripts %}`; the `.sf-footer` refresh notice; and inline scripts wiring the theme toggle (copy the pattern from `conductor/templates/dashboard.html:2820-2827` in private-ci) and the logout `fetch('/auth', {method: 'DELETE'})`. Do not load `sf-tabs.js` -- nothing on the page uses it. Do not load Bootstrap or axios. |
| 4d | high | opus | none | In the kerbside worktree, convert `kerbside/api/templates/login.html` to extend `base-sfui.html`, per design decision 7 of the phase 4 plan: a real `<form>` with a submit button and a `submit` handler (no document-level keypress listener), `.sf-card` / `.sf-field` / `.sf-label` / `.sf-input` / `.sf-btn--primary`, valid input types with `autocomplete` and `required`, a `role="alert"` `.sf-form-error` region, and `fetch` in place of axios preserving the existing status-to-message mapping and the `/source` redirect exactly. Set `{% block title %}Kerbside login{% endblock %}`. Then two `kerbside/api.py` changes: `Root.get()` (`:140`) passes `navitems=[]` so the login page renders no navigation, and `ConsolesAudit.get()` (`:333`) passes `get_nav_items('Consoles')` instead of the never-matching `'Audit'`, which fixes the audit page's highlight on the old base immediately. Nothing else in `api.py`. `tox -eflake8` and `tox -epy3` must pass with the phase 1 smoke tests unedited. |
| 4e | medium | sonnet | none | In the kerbside worktree, the tests and docs for this phase. Add to `kerbside/tests/unit/test_api_html.py` a test that the unauthenticated login page offers no navigation: assert `/console` and `/session` do not appear in the body, and comment that `/source` is deliberately not asserted because the login script redirects there on success. Add a unit test for `get_nav_items` asserting that the named section is the only active one -- a pure function test, no markup. Add `tools/preview-templates.py`: a small helper that renders a named template through the Flask test client into an output directory and symlinks `kerbside/api/static` beside it, so a converted page can be served and screenshotted without a deployed kerbside (login needs no fixtures or auth mocking; later phases extend it as they convert their pages). Document it in `docs/development.md` under a new "Previewing templates" heading, with the `python3 -m http.server` and headless-Chromium recipe for both palettes from the Verification section of the phase 4 plan. Update `ARCHITECTURE.md:395`'s template tree for `base-sfui.html` and note in `AGENTS.md`, beside the existing vendored-sfui entry, that templates converted to sfui extend `base-sfui.html` while the rest still extend `base.html` until phase 9. Do not change any template or `api.py`. |

Management-session review for this phase, beyond the standard
checklist in the master plan:

* Read `base-sfui.html` in full. It is the file phases 5 and 6
  copy their conventions from, and no linter looks at it.
* `grep -n 'rgba(\|#[0-9a-f]\{3,8\}\|!important' base-sfui.html
  login.html` returns nothing, and `--sf-brand` appears only
  in chrome.
* Nothing under `kerbside/api/static/sfui/` was edited in
  place: `tools/vendor.sh --check` exits zero.
* `base.html` and the four pages that still extend it are
  byte-unchanged, and all five smoke tests pass unedited.
* The login page in both palettes, at desktop and at 400px
  wide, per the recipe below.

## Verification

The visual check the master plan requires for phases 4 to 7
does not need a deployed kerbside. Render the page through the
test client, serve the result beside the static tree, and
screenshot it in both palettes:

    python3 tools/preview-templates.py login /tmp/preview
    (cd /tmp/preview && python3 -m http.server 8099) &

    chromium --headless --disable-gpu --no-sandbox \
        --hide-scrollbars --window-size=1280,1000 \
        --virtual-time-budget=4000 \
        --screenshot=/tmp/login-dark.png \
        http://localhost:8099/login.html

    chromium --headless --disable-gpu --no-sandbox \
        --hide-scrollbars --window-size=1280,1000 \
        --virtual-time-budget=4000 \
        --blink-settings=preferredColorScheme=2 \
        --screenshot=/tmp/login-light.png \
        http://localhost:8099/login.html

Headless Chromium reports `prefers-color-scheme: dark` by
default, so the plain run exercises the dark palette;
`preferredColorScheme=2` is the only value that gives light.
Serve over HTTP, not `file://`: the components are ES modules.
Then actually look at both PNGs.

The interactive paths -- submitting the form, a wrong
password, the theme toggle, logout -- still need a browser
against a running kerbside, or a hand-check of the fetch
calls. The login POST cannot be exercised without Keystone, so
the error mapping is verified by reading it against the
current implementation line by line.

## Success criteria

* `base-sfui.html` exists, `login.html` extends it, and
  `base.html` plus the other four templates are unchanged.
* The login page renders correctly in both palettes with no
  flash of the wrong theme, and carries no navigation and no
  logout control.
* Every other page still renders on the old base, with the
  audit page now highlighting Consoles.
* Both `<title>`s are non-empty -- `base-sfui.html`'s default
  and `login.html`'s override.
* No Bootstrap, axios or jQuery is referenced by
  `base-sfui.html` or `login.html`; all four files still exist
  under `kerbside/api/static/` for the unconverted pages.
* Logout and the theme toggle are keyboard-operable, and a
  failed login is announced by a `role="alert"` region.
* `sf.css` ships `.sf-nav`, documented in the README with the
  reason navigation is CSS-only and the `<sf-nav>` element-name
  reservation, and demonstrated in `demo.html`.
* `tools/vendor.sh --check` exits zero and `.sfui-commit`
  matches canonical `develop`'s head.
* `tox -eflake8` and `tox -epy3` pass, with the phase 1 smoke
  tests unedited plus the two new tests.
* `tools/preview-templates.py` and its `docs/development.md`
  section let a reviewer screenshot a converted page in both
  palettes without deploying kerbside.

## Risks

* **The amended master-plan decision was agreed before step
  4a** (2026-08-11) and is not revisitable cheaply afterwards,
  because phases 5 and 6 copy whichever chrome this phase
  ships. Had `<sf-tabs>` been kept, 4a would disappear, 4b
  would become optional, and 4c would marshal `navitems` into
  an inline script instead.
* **A merge round trip sits in the middle of the phase.** 4b
  cannot run until 4a is merged into sfui's `develop`. Doing
  the kerbside template work first keeps the stall off the
  critical path, but the commits must still be ordered so no
  commit references a class its tree lacks.
* **Nothing lints templates or CSS.** flake8 and the smoke
  tests cover Python; the smoke tests deliberately assert no
  markup. The management-session read-through and the
  screenshots are the whole safety net for the chrome.
* **The theme toggle's pre-upgrade property assignment is
  subtle.** It works because Lit replays instance properties,
  and it is proven in the conductor dashboard -- but a
  sub-agent that "improves" it into an `await
  customElements.whenDefined` dance is adding a failure mode.
  Copy the working pattern.
* **`login.html` is the pattern for four more pages.** A
  shortcut taken here (a stray `style=` attribute, a colour
  literal, a `kb-` class that should have been a primitive)
  gets copied four times before phase 9 notices.

## Future work recorded here

* **`.kb-header-controls` may belong in `sf.css`** as a
  header control cluster, once a second consumer wants one.
  The conductor dashboard has a single control in its header
  and does not need it yet.
* **`.sf-status-line` is unclaimed** until phase 7 decides
  what the polling status line says.
* **The old base's popover initialiser** dies with the last
  page that needs it, in phase 6 or 9, not here.
* **Session expiry still errors rather than redirecting** on
  the four protected pages -- a master-plan non-goal, and the
  reason `navitems` can stand in for "authenticated" today. If
  that ever changes, revisit design decision 2.

## Outcome

Done, 2026-08-11, across two repositories, all five steps as
planned plus a second sfui round trip for the button size the
screenshots turned up:

* shakenfist/sfui, on `nav-links`: `e372c91` adds `.sf-nav`,
  its README rationale and the `demo.html` strip (step 4a).
  Merged to `develop` as `042939e`. Then, on
  `form-scale-button`: `89ed7e4` adds `.sf-btn--lg`, merged as
  `c199966`.
* kerbside, on `sfui-conversion-phase-04`: `271bd86`
  re-vendors at `042939e` (4b), `07db4ac` is this plan,
  `e94616d` adds `base-sfui.html` (4c), `ad0465f` converts
  the login page and fixes the two `api.py` context calls
  (4d), `e112cb2` adds the two tests, the preview tool and
  the docs (4e). `f5828b4` re-vendors again at `c199966`, and
  the commit carrying this outcome puts `.sf-btn--lg` on the
  login button.

The merge round trip in the middle of the phase went as the
risks section described. The four kerbside commits were
written first against the old `develop`, then the branch was
reset to a `develop` that had moved on by six commits, the
re-vendor committed, and the four cherry-picked back on top.
Only `docs/plans/index.md` conflicted, because two-tier CI
phase 4 had landed a row of its own in the meantime.

`tools/vendor.sh --check` exits zero, `.sfui-commit` is
canonical `develop`'s head, and the re-vendor is a
two-merge jump: kerbside's pending `sfui-revendor-readme`
branch, which would have made the intermediate hop, is
subsumed and should be deleted unmerged.

### Corrections to this plan, found while executing it

* **The verification recipe did not work as written.**
  `python3 tools/preview-templates.py` fails on a host
  without kerbside's dependencies installed, which is every
  host that has only ever run `tox`. The recipe now uses
  `.tox/py3/bin/python`, and the script's import of
  `kerbside.api` is guarded so the failure names the
  interpreter rather than `flask`.
* **The port in the recipe is an example, and saying so
  matters.** 8099 was already taken on the development host,
  and a stale `http.server` left over from an earlier
  preview serves convincing 404s from a directory that has
  since been deleted -- which looks exactly like a broken
  template.

### Verification

Both palettes were rendered at 1280px and read, and the
narrow layout at 400px: the `.kb-header-controls` cluster
leaves its absolute corner and sits under the title, as
design decision 4 intended, and nothing overflows. The login
page carries no navigation and no logout control, and the
theme toggle is present, per design decision 2.

The management-session greps come back clean: no colour
literal, important flag or id selector in either template,
and no reference to Bootstrap, axios or jQuery, while all
four remain under `kerbside/api/static/` for the pages that
still need them. `git diff origin/develop..HEAD --
kerbside/api/templates/` touches exactly two files, so
`base.html` and the four pages on it are byte-unchanged.
`tox -eflake8` and `tox -epy3` pass with the phase 1 smoke
tests unedited.

### The login button was undersized, and the fix was canonical

`.sf-btn` is `font-size: 0.78rem; padding: 0.15rem 0.6rem`,
sized for the dashboard's table-row actions, while
`.sf-input` is `0.9rem; 0.4rem 0.6rem`. On a form the primary
button therefore read as smaller than the fields above it.
The rendered screenshots made this obvious in a way the
stylesheet had not, and it was fixed inside the phase rather
than deferred, because phases 5 and 6 copy this page.

The fix is canonical, not a `kb-` override that four more
pages would copy: sfui `89ed7e4` adds `.sf-btn--lg`, which
takes `.sf-input`'s padding and font size rather than a
scaled-up `.sf-btn`, so the control is exactly as tall as its
fields. Merged as `c199966`, re-vendored in kerbside by
`f5828b4`, and used on the login button by the commit that
records this outcome.

Three decisions inside that change are worth carrying into
later phases. Size and colour are separate modifiers, so a
form's primary control is
`.sf-btn .sf-btn--primary .sf-btn--lg` -- making `--primary`
imply a size would be the wrong axis, since a primary action
in a table row is still a table-row action. The base size was
left alone, because rescaling `.sf-btn` and pushing the small
size onto the table rows would repaint every existing button
in both consumers to fix one page. And sfui's own `demo.html`
had the identical defect in its cancel form, unnoticed since
phase 3: a gallery only pays off when someone renders it and
looks, which is the same lesson `.sf-table-scroll` taught.

## Back brief

Given on 2026-08-11, before any step ran, and approved:

* Design decision 1 amends the master plan and pulls in a
  canonical sfui change. Approved; the master plan's mission
  bullet and phase-4 note now record the amendment.
* Design decision 2 answers the master plan's open question
  about the login page's navigation: it renders none.
* Steps 4a and 4b straddle a pull request in sfui, so the
  kerbside commits are written first and the re-vendor is
  reordered to the front of the branch once sfui's `develop`
  carries `.sf-nav` -- `git reset --hard origin/develop`, the
  re-vendor commit, then cherry-pick the held commits back on
  top. The pending `sfui-revendor-readme` re-vendor is
  subsumed by 4b if it has not merged by then.
