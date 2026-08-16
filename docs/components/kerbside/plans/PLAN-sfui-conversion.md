# Convert the kerbside admin UI to sfui

## Prompt

Before responding to questions or discussion points in this
document, explore the kerbside codebase thoroughly. Read the
relevant source files, understand existing patterns (the
Flask app and every route in `kerbside/api.py`, the Jinja
templates in `kerbside/api/templates/`, the static assets in
`kerbside/api/static/`, the data shapes returned by
`kerbside/db.py`, and the CI lane scripts under
`tools/direct-qemu/` that exercise the JSON side of the web
routes). Ground your answers in what the code actually does
today. Do not speculate about the codebase when you could
read it instead. Flag any uncertainty explicitly rather than
guessing.

Key cross-repo references:

- `shakenfist/sfui` — the canonical design system this plan
  adopts: `README.md` is the contract (tokens, theming, the
  component rules, the vendoring mechanics via
  `tools/vendor.sh`), and `docs/plans/PLAN-sfui-spinout.md`
  records why the repository exists and names kerbside as
  step 5.
- `shakenfist/private-ci` — the first sfui consumer. The
  conductor dashboard (`conductor/templates/dashboard.html`)
  is the reference for the brand header treatment, the theme
  toggle wiring, and the morphdom polling pattern
  (`conductor/static/morphdom-umd.js`).
  `docs/plans/PLAN-sfui-theming.md` there records the
  theming decisions and sketches this conversion in its
  "Later phases" section.
- `shakenfist/development` — carries the `sfui-vendor`
  consistency audit (`audits/sfui-vendor.md`,
  `scripts/audit-check.py`). Kerbside is in the daily audit
  matrix, so the moment a `.sfui-commit` lands here the
  vendored copy is checked automatically: verbatim at the
  recorded commit, and not behind canonical HEAD.

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be
named for the master plan, in the same directory as the
master plan, and simply have `-phase-NN-descriptive`
appended before the `.md` file extension. Tracking of these
sub-phases should be done via a table in this master plan
under the Execution section.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Kerbside's admin UI is a small server-rendered Flask
application: one module (`kerbside/api.py`) and six Jinja
templates totalling 390 lines (`base.html`, `login.html`,
`consoles.html`, `sessions.html`, `sources.html`,
`audit.html`), all extending `base.html`. It is styled with
vendored Bootstrap 5.3 and uses axios for the login,
logout and terminate XHRs. The vendored payload is ~8.5 MB
on disk, of which exactly three files are loaded:
`bootstrap.min.css`, `bootstrap.bundle.min.js` and
`axios.min.js` (~370 KB). `jquery-3.7.0.min.js` and the
other 40-odd Bootstrap variants are dead weight, as are two
of the eight icon files.

The UI predates any Shaken Fist design language and shows
its age:

- Refresh is a `<meta http-equiv="refresh" content="30">`
  full-page reload on every page except login.
- Rich detail (recent audit events, token counts, CA
  certificates) is delivered as multi-line HTML — including
  a Jinja `for` loop — stuffed into `data-bs-content`
  attributes and rendered by Bootstrap popovers with
  `data-bs-html="true"`.
- The markup has accumulated real defects: every
  `consoles.html` and `audit.html` table row is an unclosed
  `<tr>`, `login.html` closes a `<p>` with `</span>` and
  still uses `<font color="red">` and Bootstrap 4 spacing
  utilities that are no-ops in Bootstrap 5, `base.html` has
  a stray empty `<link href="">`, no charset, no viewport
  and no `lang`, `sessions.html` emits a duplicate
  `id="data"` per session, and the audit page never
  highlights a nav tab because `get_nav_items('Audit')`
  names a tab that does not exist.
- The navbar hardcodes the brand teal
  (`style="background-color: #1d6d87;"`) — the color that
  became sfui's `--sf-brand` token.
- Destructive actions (console and session terminate) are
  plain GET links and GET XHRs with no confirmation, which
  is also a blind-CSRF surface (issue #133).

There is no UI test coverage of any kind: the only API-layer
unit test (`kerbside/tests/unit/test_api.py`) always sends
`Accept: application/json`, so no test has ever rendered a
template. Every HTML page shares its URL with a JSON twin
via `Accept`-header content negotiation, and the CI lanes
(`tools/direct-qemu/`, the tempest plugin) depend on the
JSON side.

Meanwhile the design system this UI was always waiting for
now exists. sfui lives canonically at
https://github.com/shakenfist/sfui: design tokens with light
and dark palettes behind `data-theme`, a flash-free theme
boot script (`sf-theme.js`) with a cookie-remembered
preference, Lit components (`sf-tabs`, `sf-theme-toggle`)
with a strict data-in/events-out contract, the Shaken Fist
globe mark, and a vendoring script whose drift is checked
daily by shakenfist/development's `sfui-vendor` consistency
audit. private-ci's conductor dashboard is the first
consumer and the reference brand treatment. Kerbside is the
named second consumer in every one of those documents, and
issue #244 ("The web admin interface could do with a rewrite
/ style pass") is precisely this work: the "web style" and
"library of common elements" it asks for became sfui, and
this plan is the "bring the admin UI here into line" part.

## Mission and problem statement

Convert the kerbside admin UI into the second sfui consumer:
vendor sfui into `kerbside/api/static/sfui/`, rebuild the
six templates on tokens and sfui components, and retire
Bootstrap, jQuery and axios entirely — without changing the
JSON API twins, the `.vv` download flows, or the auth
mechanics that the CI lanes and real deployments depend on.

Concretely, the converted UI has:

- The sfui head order (`sf-theme.js` then `tokens.css`),
  light and dark themes with the cookie-remembered
  preference, and an `<sf-theme-toggle>` in the chrome.
- The reference brand treatment: the globe mark beside the
  page heading and `--sf-brand` confined to the chrome, in
  place of the hardcoded teal navbar.
- An sfui tab strip for the Sources / Consoles / Sessions
  navigation, with the audit page highlighting correctly.
  **Amended in phase 4:** the navigation is anchors styled by
  `sf.css`'s `.sf-nav`, not `<sf-tabs>`, because the
  component's arrow-key selection would navigate and its
  `role="tab"` buttons are not links. See phase 4's design
  decision 1.
- Native `<details>` disclosures replacing the
  HTML-in-attribute popovers.
- morphdom-based polling replacing the meta-refresh reload,
  following the conductor dashboard pattern.
- Valid, accessible markup: the unclosed rows, mismatched
  tags, deprecated elements, duplicate ids, keyboard-dead
  logout link and missing charset/viewport/lang all fixed.
- A template smoke-test suite so this conversion — and every
  future template change — has a safety net.

Done means the sfui audit greps come back clean, the daily
`sfui-vendor` consistency audit reports kerbside verbatim at
canonical HEAD, and issue #244 closes.

## Non-goals

- No changes to the JSON responses, the `.vv` generation
  path, or route URLs. The `Accept`-negotiation branches in
  each view stay exactly as they are (except where an Open
  question below is decided otherwise).
- No auth-flow rework: Keystone remains the only backend,
  and the login-page-only-on-`/` behaviour (other pages
  error rather than redirect when the session expires)
  stays. Both are noted in Future work.
- No CSP or security-header work (Future work; today there
  are none, so inline scripts remain possible during the
  conversion).
- No `url_for()` / sub-path-mounting conversion; asset URLs
  stay root-relative absolute paths, matching both the
  current templates and the sfui README's examples.
- No in-browser console viewer. Kerbside hands off to a
  native SPICE client via `.vv` files, and that is out of
  scope here.

## Open questions

All five were decided by the operator on 2026-08-08:

1. **What replaces the popovers?** Three popovers exist:
   recent audit events and active-token details on the
   consoles table, and the CA certificate on the sources
   table. **Decided: native `<details>` disclosures** in
   the table cells — zero dependencies, zero positioning
   code, keyboard support for free, and appropriate for an
   admin-only UI that does not have to be fancy. The
   popover *content* moves out of attribute values and
   into real markup, which kills the ugliest pattern in
   the current templates. (An `sf-popover` Lit component
   over the native HTML Popover API remains a future
   option if `<details>` ever proves clunky.)
2. **Where does morphdom live?** private-ci vendors
   `morphdom-umd.js` in conductor's static assets, outside
   the sfui set. **Decided: promote into sfui's vendored
   dependencies** — one canonical copy, flowing to
   consumers via `vendor.sh`, documented beside lit-core
   in the README. private-ci deletes its local copy at its
   next re-vendor.
3. **Where does shared page styling live?** sfui today
   ships tokens and components but no page-level CSS; the
   conductor dashboard styles its tables and panels in its
   own page `<style>`. **Decided: converged styling** — a
   shared tokens-based stylesheet (e.g. `sf.css`) added to
   the sfui distributable set, adopted by kerbside now and
   by the dashboard opportunistically, reducing duplicated
   effort across the two UIs. This adds a small sfui-side
   phase before the kerbside pages convert.
4. **Do the destructive GETs become POSTs in this plan?**
   Issue #133 documents the CSRF exposure: terminate
   actions are GET, and flask-jwt-extended's cookie CSRF
   protection does not cover GET. **Decided: yes, convert
   them** (phase 8): browser actions become POST (`fetch`
   with the CSRF double-submit header), the JSON API moves
   to POST with it, and the out-of-browser callers plus any
   docs examples are updated in the same phase. Closes
   #133.

   **Corrected in phase 8 planning**, against the tree at
   `aa7e5da`: the callers are
   `tools/direct-qemu/verify-terminate-live.sh`,
   `tools/sf-e2e/drive-happy-path.py:253` and
   `tools/ovirt-e2e/drive-console.py:494` — the latter two
   were missing from this list. `lane-up.sh` only mints the
   JWT and contains no terminate call, and the tempest
   plugin never touches these endpoints; both were named
   here in error. Forms were dropped as an option because
   `JWT_CSRF_CHECK_FORM` defaults to `False`. Issue #133
   also covers a *third* destructive GET this phase does
   not convert — `ConsolesProxyVirtViewer` writes a ticket
   and mints a token while returning a `.vv` file — which
   is mitigated by a `SameSite=Lax` cookie and tracked
   separately as #319.
5. **Confirmation on terminate?** **Decided: a lightweight
   two-step** ("Terminate → Confirm?" state on the button
   itself) rather than a modal; it fixes the unconfirmed
   destructive click without new components. One day this
   may be replaced by a notification-toast pattern instead
   (see Future work).

## Execution

Each phase gets its own plan file before implementation
starts, per the Prompt section. Phases 4–7 each end with a
visual check of the affected pages in both themes against a
locally-running kerbside.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Template smoke tests | PLAN-sfui-conversion-phase-01-smoke-tests.md | Done |
| 2. Vendor sfui + static plumbing | PLAN-sfui-conversion-phase-02-vendoring.md | Done |
| 3. sfui canonical additions (shakenfist/sfui repo) | PLAN-sfui-conversion-phase-03-sfui-canonical.md | Done |
| 4. New base + login page | PLAN-sfui-conversion-phase-04-base-login.md | Done |
| 5. Consoles page | PLAN-sfui-conversion-phase-05-consoles.md | Done |
| 6. Sessions, sources and audit pages | PLAN-sfui-conversion-phase-06-remaining-pages.md | Done |
| 7. morphdom polling | PLAN-sfui-conversion-phase-07-polling.md | Done |
| 8. Terminate actions to POST (#133) | PLAN-sfui-conversion-phase-08-terminate-post.md | Done |
| 9. Teardown, docs and issue closure | PLAN-sfui-conversion-phase-09-teardown.md | Not started |

Phase notes, dependencies and recommended planning effort:

1. **Template smoke tests** (plan at medium effort). The
   safety net comes first, while the templates are still
   the old ones: extend `kerbside/tests/unit/test_api.py`
   (or a sibling `test_api_html.py`) with one test per
   HTML route sending `Accept: text/html` against the
   mocked-`db` test client, asserting 200 and a
   characteristic content marker per page. These tests are
   deliberately marker-based, not markup-snapshot-based, so
   they survive the conversion and then guard it.
2. **Vendor sfui + static plumbing** (plan at medium
   effort). Run sfui's `tools/vendor.sh
   kerbside/api/static/sfui`; extend
   `.pre-commit-config.yaml`'s trailing-whitespace /
   end-of-file exclude from
   `^kerbside/api/static/(css|js)/` to cover the sfui path;
   add the path to `.vscode/review-scope.toml`'s vendored
   excludes; verify the wheel actually contains the new
   files (`python -m build` then `unzip -l` — packaging
   relies on setuptools' implicit `include-package-data`
   plus the setuptools_scm file finder, so this is the
   phase that proves it). No template references yet; the
   daily `sfui-vendor` audit starts covering kerbside from
   this commit.
3. **sfui canonical additions** (plan at high effort; work
   lands in the shakenfist/sfui repository, then re-vendor
   here and in private-ci). Per the decided open
   questions: the shared tokens-based stylesheet, and
   morphdom promotion (README "Vendored dependencies"
   entry plus the `files` list in `tools/vendor.sh`,
   deleting private-ci's local copy at its re-vendor).
4. **New base + login** (plan at high effort — this phase
   sets the visual language for everything after it).
   Introduce `base-sfui.html` alongside the old base
   (strangler pattern: every phase leaves all pages
   working): correct head (`charset`, `viewport`, `lang`,
   sfui script/stylesheet order), brand header per the
   reference treatment with the sfui globe mark (retiring
   kerbside's Inkscape `logo.svg`), a nav strip wired to the
   `navitems` context (fixing the audit-page highlight and
   the keyboard-dead logout link),
   `<sf-theme-toggle>`, and the refresh footer. Convert
   `login.html` onto it, fixing the mismatched tags,
   `<font>`, invalid input type, missing autocomplete
   attributes and dead Bootstrap 4 utilities, and dropping
   axios for `fetch`. Decide here whether the login page
   should stop rendering the nav entirely (it currently
   shows links to protected pages while unauthenticated).
   **Decided in phase 4:** the nav is `.sf-nav` anchors
   rather than `<sf-tabs>`, which adds a small canonical
   sfui step, and the login page renders no nav and no
   logout control (the theme toggle stays).
5. **Consoles page** (plan at high effort — the largest and
   densest page: popover replacement, two dropdown
   button-groups, icons). Convert `consoles.html` onto the
   new base: fix the unclosed `<tr>`s, move audit/token
   detail into `<details>` disclosures, rebuild the
   connect and terminate button groups (terminate gains
   the two-step confirmation), and
   re-cut the Material Symbols icons to
   `fill="currentColor"` so they follow the theme (updating
   `static/icons/README.md` provenance notes; delete the
   two dead icons).
   **Amended in phase 5 planning:** an SVG loaded via
   `<img>` resolves `currentColor` in its own isolated
   document and can never follow the page theme, so the
   icons become inline Jinja includes under
   `templates/icons/` and `static/icons/` retires; and the
   connect dropdown flattens to two visible anchor-buttons
   rather than being rebuilt (sfui deliberately has no
   dropdown primitive), which pulls one small canonical
   sfui addition — `.sf-btn` suppressing the base-layer
   link underline so anchors can be buttons.
6. **Sessions, sources and audit** (plan at medium effort —
   the patterns are established by now). Native `<details>`
   accordion for sessions (fixing the duplicate ids),
   shared table styling throughout, the sources CA
   disclosure, the audit page's stray `</td>`/unclosed
   `<tr>`, and make use of `total_events` on the audit
   page.
   **Amended in phase 6 planning:** this phase is
   template-only — every primitive it needs already exists
   in the vendored `sf.css`, so there is no canonical sfui
   step and no re-vendor; and the two-step terminate script
   moves out of `consoles.html` into a shared
   `templates/includes/two-step-terminate.html` here, so
   phase 8's POST conversion edits one copy.
7. **morphdom polling** (plan at medium effort, following
   the dashboard pattern). Replace the meta refresh: fetch
   the current URL with `Accept: text/html`, parse, and
   morph the content container only (`data-theme` lives on
   `<html>`, which the morph never touches); update the
   "Content refreshed at" stamp from the poll. Ensure open
   `<details>` disclosures survive a poll.
   **Corrected in phase 7 planning:** morphdom does *not*
   preserve the `open` attribute natively — its attribute
   sync would clobber operator state in both directions — so
   the poll supplies an `onBeforeElUpdated` hook that copies
   the live open state onto the incoming element (and skips
   an armed terminate button entirely); the phase 6 stable
   ids are what make morphdom match the right elements.
8. **Terminate actions to POST** (plan at high effort).
   Routes, templates, CSRF double-submit wiring, the three
   out-of-browser callers, docs; closes #133. See the
   correction under open question 4: not the tempest
   plugin, not `lane-up.sh`, and the `.vv` ticket write is
   mitigated rather than converted (#319).
9. **Teardown, docs and issue closure** (plan at medium
   effort). Delete the old `base.html` (renaming
   `base-sfui.html` into place), Bootstrap (all 40+ files),
   jQuery, axios and `logo.svg`; update
   `docs/development.md`'s "Vendored web assets" section,
   `ARCHITECTURE.md`'s directory notes and `AGENTS.md`;
   prune the pre-commit exclude back down; run the sfui
   README audit greps over the finished templates; close
   #244.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean
and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the plan, at the recommended effort
   level and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's
   summary describes what it intended, not necessarily
   what it did.
4. **Fix or retry** if the output is wrong. Diagnose
   whether the brief was insufficient (improve it) or the
   model was too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied
   with the result.

This applies to all steps, including high-effort ones. If
a sub-agent can't succeed even with a detailed brief and
the right model, that's a signal the brief needs
improving, not that the management session should do the
implementation itself.

Use `isolation: "worktree"` for sub-agents when the change
is risky or experimental. The worktree is discarded if the
output is unsatisfactory. For safe, well-understood
changes, sub-agents can work directly in the main tree.

One conversion-specific caution for every brief: each HTML
view shares its URL with a JSON twin behind an `Accept`
check, and the HTML paths receive **unfiltered** dicts from
`kerbside/db.py` (the JSON paths strip `ticket` and
`password`; the template context does not). Briefs for
template work must name the exact fields to render — never
iterate dict keys generically.

### Planning effort

The master plan itself should always be created at **high
effort** — it requires broad codebase understanding,
cross-referencing multiple source files, and making
judgment calls about scope and sequencing.

Each phase plan should specify the recommended effort
level for planning that phase, as listed in the phase
notes above. Phases that set visual language or touch API
routes and CI tooling (4, 5, 8, and the canonical additions
in 3) are planned at high effort; the mechanical and
pattern-following phases (1, 2, 6, 7, 9) at medium.

### Step-level guidance

Each phase plan should include a table like this:

```
| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 1a   | medium | sonnet | none      | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree  | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**
- **high** — Requires reading multiple files, making
  judgment calls, understanding non-obvious invariants
  (the Accept-negotiation twins, the unfiltered template
  context, CSRF token lifecycle, what the CI lane scripts
  curl), or researching external references. The sub-agent
  needs to think carefully about edge cases.
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief. May need to read a
  few files but the approach is well-defined.
- **low** — Purely mechanical changes (rename, reformat,
  delete dead files). The brief is a complete instruction.

**Model choice:** The planner should recommend which
model is best suited for each step. This is a judgment
call, not a rigid rule — the right model depends on what
the step requires, not on whether it's "planning" or
"implementation".

- **opus** — Best for steps that require deep reasoning,
  cross-file architectural understanding, or subtle
  correctness judgment: the base/login phase that sets the
  visual language, the consoles-page rework, the POST/CSRF
  phase.
- **sonnet** — Good default for well-briefed
  implementation work. Faster and cheaper than opus.
  Works well when the plan front-loads the research and
  the brief is detailed enough that the agent doesn't
  need to make broad judgment calls.
- **haiku** — Suitable for purely mechanical tasks:
  deleting the dead Bootstrap variants, running the audit
  greps, running commands. The brief must be a
  near-complete instruction.

The model choice interacts with effort level and brief
quality. A detailed brief compensates for a lighter model
— sonnet at medium effort with a thorough brief often
matches opus at medium effort with a vague brief. The
planner's job is to write briefs good enough that the
recommended model can succeed.

Note: the model also determines the context window (opus
has 1M tokens, sonnet and haiku have 200K). Steps that
require holding many files in context simultaneously may
need opus for that reason alone, even if the reasoning
itself is straightforward.

**When in doubt, skew to the more capable model.** Saving
money only matters if the outcome is still acceptable. A
failed or low-quality implementation wastes more time
(and therefore more money) than using a heavier model
would have cost. Only recommend a lighter model when you
are confident the brief is detailed enough for it to
succeed.

**Brief for sub-agent:** This is the key field. Write it
as if briefing a colleague who has never seen the
codebase. Include: what to change, which files to touch,
what patterns to follow, and any non-obvious constraints.
The better the brief, the lower the effort level needed
and the lighter the model that can succeed.

A good brief front-loads the research the planner already
did. For example, instead of "convert the sessions page",
write "rewrite `kerbside/api/templates/sessions.html` to
extend `base-sfui.html`, replacing the Bootstrap accordion
(lines 5–52) with one native `<details class='sf-accordion'>`
per session, first session `open`, unique ids derived from
`session.session_id`; the terminate button posts via the
pattern established in `consoles.html`; table markup uses
the shared classes from sfui's `sf.css`; render exactly the
fields the current template renders — the context dicts are
unfiltered `db.get_sessions()` rows."

### Management session review checklist

After a sub-agent completes, the management session
should verify:

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code passes `tox -eflake8` and `tox -epy3`,
      including the phase-1 template smoke tests.
- [ ] For template phases: the page renders correctly in
      **both themes** in a real browser against a locally
      running kerbside, and the sfui audit greps stay
      clean (no `rgba()` or hex literals outside `var()`
      fallbacks; `--sf-brand` only in chrome).
- [ ] For any phase touching `kerbside/api/static/sfui/`:
      `tools/vendor.sh --check` from a canonical sfui
      checkout passes — the copy is never edited in place.
- [ ] The JSON twins are byte-identical for the touched
      routes (the CI lanes depend on them).
- [ ] The changes match the intent of the brief — not
      just syntactically correct but semantically right.
- [ ] Commit message follows project conventions
      (including the `Co-Authored-By` line with model,
      context window, effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully
implemented because the following statements will be true:

* The code passes `tox -eflake8` and `tox -epy3`, and the
  new template smoke tests render every HTML page.
* All five pages (login, consoles, sessions, sources,
  audit) are fully readable in both light and dark themes;
  the preference survives reloads with no flash of the
  wrong theme; auto follows the operating system.
* Bootstrap, jQuery and axios are gone from
  `kerbside/api/static/`, along with the dead icons and
  the old logo; the only vendored JS is the sfui set
  (which now includes morphdom).
* The sfui README audit greps come back clean over
  `kerbside/api/templates/`, and `tools/vendor.sh --check`
  passes against the vendored copy.
* The daily `sfui-vendor` consistency audit in
  shakenfist/development reports kerbside's copy verbatim
  at canonical HEAD.
* The JSON twins and `.vv` flows are unchanged, except the
  terminate routes which move to POST exactly as phase 8
  specifies, and the direct-qemu, sf-e2e and tempest lanes
  are green.
* The markup defects catalogued in the Situation section
  are all fixed, and the templates parse as valid HTML.
* `python -m build` produces a wheel containing the sfui
  static assets.
* `docs/development.md` ("Vendored web assets"),
  `ARCHITECTURE.md` and `AGENTS.md` describe the new
  static-asset reality; issues #244 and #133 are closed.

### Future work

* CSP and security headers for the admin UI. Today there
  are none; the conversion keeps inline scripts, so a CSP
  would need nonces or extracted files. Worth doing once
  the UI settles.
* Session-expiry UX: every page except `/` errors rather
  than redirecting to the login page when the JWT expires.
* Login UX for deployments without Keystone (static /
  oVirt / Shaken Fist sources): today the Keystone form
  renders unconditionally and login 500s.
* `url_for()` conversion / sub-path mounting support.
* Audit-page pagination: the route accepts `?limit=` and
  the view computes `total_events`, but the UI exposes
  neither.
* Restricting `/console/direct` to admins (issue #134)
  intersects with the connect dropdown but is an API-side
  authorisation change, out of scope here.
* Replace the two-step terminate confirmation with a
  notification-toast pattern (act immediately, toast the
  outcome, perhaps with an undo window) once sfui grows a
  toast affordance; noted at decision time as the likely
  eventual shape.
* A `flasgger` decision: it is a declared dependency but
  is never imported — drop it or wire it up (independent
  of this plan; noticed during the survey).
* An `sf-poll.js` page-infrastructure helper in sfui,
  wrapping the fetch-and-morph polling loop. Deliberately
  not built in phase 3: the dashboard's loop is entangled
  with its per-panel change detection and kerbside's will
  be a simpler whole-container morph, and there is no way
  to tell which parts are genuinely common until both
  exist. Revisit after phase 7.
* A `tools/vendor.sh --check` step in kerbside's own CI.
  Considered and deliberately not built in phase 2: it
  would clone sfui on every run to check what the daily
  `sfui-vendor` audit in shakenfist/development already
  checks, and its staleness verdict would depend on
  whatever sfui HEAD happened to be that minute. Worth
  revisiting if drift ever reaches `develop` unnoticed.

### Bugs fixed during this work

To be filled in as phases execute. Known intersecting
issues going in:

* #244 — the admin UI rewrite/style pass: resolved by this
  plan as a whole.
* #133 — destructive admin actions on GET (blind CSRF):
  fixed by phase 8 for both terminate routes, which are now
  POST behind the `X-CSRF-TOKEN` double submit. The third
  destructive GET it names, the `.vv` ticket write, cannot
  become a POST without replacing a browser-native download
  on the console handoff path; it is mitigated by the
  `SameSite=Lax` cookie phase 8 sets, and its residual is
  #319.
* The markup defect catalogue in the Situation section
  (unclosed `<tr>`s, `</span>` closing a `<p>`, `<font>`,
  duplicate `id="data"`, dead Bootstrap 4 utilities, the
  `get_nav_items('Audit')` mismatch, the stray
  `<link href="">`, missing charset/viewport/lang).

### Documentation index maintenance

`docs/plans/index.md` has a row for this plan in the
Master plans table; keep its status and phase links
current as phase plans are created and completed, and mark
it *Complete* when all phases are done.

### Back brief

Before executing any step of this plan, please back
brief the operator as to your understanding of the plan
and how the work you intend to do aligns with that plan.
