# sfui conversion phase 3: shared styling and morphdom

Master plan: `PLAN-sfui-conversion.md`. Planned at high
effort per the master plan's phase notes, because it decides
the shape of a design-system API that two repositories will
then be pinned to.

**Most of the work in this phase happens in the
shakenfist/sfui repository, not in kerbside.** Kerbside's
share is a re-vendor. private-ci gets a commit too.

## Situation

Master-plan open questions 2 and 3 were decided on
2026-08-08: morphdom is promoted into sfui's vendored
dependencies rather than copied per consumer, and page
styling converges in a shared tokens-based stylesheet rather
than being written per consumer. This phase does both, and
it must do them before phase 4 converts a single kerbside
page, because phase 4 onwards consumes what lands here.

What exists today:

* sfui ships tokens, a theme boot script, a logo, the Lit
  runtime and two components. It ships **no page-level
  CSS**, so both consumers style their own pages.
* The conductor dashboard's `<style>` block
  (`dashboard.html:16-828`, ~810 lines) is the only body of
  page styling in the design system's world, and it is
  already fully token-clean: a grep for `rgba(` and hex
  colours across that entire file returns nothing. So this
  is a de-duplication exercise, not a colour cleanup. What
  it *does* duplicate is structural — the same table style
  written four times (`:278`, `:503`, `:693`, and the
  repo-activity table), the same link treatment four times
  (`:309`, `:529`, `:600`, `:720`, `:784`), the mono font
  stack seven times, five distinct hardcoded radii across
  twelve rules.
* Kerbside is about to lose Bootstrap entirely and has
  essentially no styling of its own: one inline `<style>`
  rule and some `style=` attributes. It needs tables, form
  controls, buttons, disclosures, cards and page chrome
  from somewhere.
* morphdom 2.7.7 (MIT, vendored unmodified from
  `https://unpkg.com/morphdom@2.7.7/dist/morphdom-umd.js`)
  lives at `conductor/static/morphdom-umd.js` in private-ci
  and is referenced by `dashboard.html:1014`,
  `ARCHITECTURE.md:44` and `ARCHITECTURE.md:903-904`.
  Kerbside's phase 7 needs the same library.

## Mission

sfui gains two distributable additions — a shared stylesheet
`sf.css` and the vendored morphdom — designed so that
kerbside can build every page in phases 4 to 6 on them, and
so that the dashboard can adopt them incrementally, one
primitive at a time, without a flag day and without any
visual change it did not ask for. Both consumers are then
re-vendored, and private-ci's duplicate morphdom is deleted.

## Design decisions

These are the decisions the phase is really about. They are
recorded here because once two repositories vendor `sf.css`,
renaming anything in it is a three-repository change plus
template edits.

### 1. Opt-in classes in a cascade layer

`sf.css` ships **`.sf-*` classes inside `@layer sfui`**, with
element-level base rules gated behind an `sf-page` class on
`<body>` and wrapped in `:where()`.

The alternative — classless CSS styling bare `table`,
`button` and `a` — would be cheaper for kerbside, whose
Bootstrap classes are about to be deleted anyway. It is
rejected for two reasons. It contradicts sfui's naming
discipline, where elements and events are already `sf-*`.
And it makes dashboard adoption all-or-nothing: the moment
`dashboard.html` linked the stylesheet, every table, button
and link emitted by its ~1800 lines of JavaScript string
concatenation would restyle at once, with no test coverage
to catch what broke.

The layer is what makes incremental adoption safe.
Unlayered rules beat layered ones regardless of specificity,
so **a page's own `<style>` block automatically wins over
`sf.css`** with no `!important` and no specificity contest.
Sub-layers `sfui.reset`, `sfui.base`, `sfui.components`,
`sfui.utilities` are declared in one `@layer` statement at
the top of the file.

The `sf-page` gate matters because this phase re-vendors
`sf.css` into private-ci *before* the dashboard is ready to
adopt anything. Without the gate, `* { margin: 0; padding:
0 }` would silently flatten any page that linked the
stylesheet. With it, linking `sf.css` changes nothing until
the body carries the class.

Naming rules, all mechanically checkable:

* Single-class `.sf-*` component classes, BEM-style
  modifiers with `--`: `.sf-btn`, `.sf-btn--danger`,
  `.sf-btn--sm`. Modifiers never appear alone.
* A class must never share a name with an sfui custom
  element. `.sf-tabs` and `.sf-theme-toggle` are reserved
  and forbidden as class names — differing from the element
  by only a leading dot is a readability trap.
* Element-level base rules are `:where(.sf-page) :where(a)`
  form, i.e. zero specificity, so even a bare `a {}` in a
  page overrides them.
* No `!important`, no id selectors, no colour outside
  `var(--sf-*)` and `color-mix()`.
* Per-page tuning is by documented custom-property knob
  first, unlayered override second.

### 2. The first cut, and what it deliberately omits

Small enough to review in one sitting; everything in it is
either needed by both consumers or needed by kerbside on
every page. Sources cited as `dashboard.html` line numbers.

*Base and chrome:* `.sf-page` (reset, sans stack, bg/text,
page padding, the <700px reduction) `:24-34, :817-819`;
`.sf-container` with `--sf-container-max` `:36-39`;
`.sf-header` plus its `h1`, `img`, `p` and
`sf-theme-toggle` placement `:41-78, :823-826`;
`.sf-page sf-tabs` spacing `:22`; `.sf-footer` `:380-387`;
`.sf-status-line` `:80-84` (which declassifies the
id-selected `#update-status`); and the accent link
treatment `:529-537`.

*Content primitives:* `.sf-section` and
`.sf-section-header` `:127-146`; `.sf-push-right` utility;
`.sf-table` with `th`/`td` `:503-527`; `.sf-table--striped`
and a row-hover rule; `.sf-table-scroll`; `.sf-num`
`:730-733`; `.sf-btn` factored into a base plus
`--primary`, `--danger`, `--sm` `:323-371`; `.sf-btn-group`;
`.sf-badge` plus token-semantic variants `:186-218`;
`.sf-card` `:93-98`; `.sf-code` `:262-276`; `.sf-banner`
with `--warn`/`--error`/`--info` `:800-811`; `.sf-empty`
`:373-378`; `.sf-footnote` `:675-679`.

*New, with no dashboard precedent, because kerbside needs
them:* `.sf-disclosure` on `<details>` plus its `summary`,
and `.sf-disclosure--panel` for the sessions accordion;
`.sf-field`, `.sf-label`, `.sf-input`, `.sf-form-error`.

Three of those deserve their reasoning recorded, because
each is a place where "wait until a page needs it" was the
tempting answer:

* **`.sf-table--striped` and row hover.** All four kerbside
  tables use Bootstrap's `table-striped` today and the
  consoles table is ten columns wide; zebra and hover are
  what make a table that dense readable. Deferring means
  phase 5 discovers the gap and forces a re-vendor.
* **`.sf-btn-group`.** Kerbside phase 5 needs it for the
  connect and terminate clusters. Pay once now rather than
  a canonical change plus two re-vendors later.
* **Form controls.** They serve exactly one page, so they
  fail the "both consumers" test. They are in anyway
  because without them the login page has *no* styling at
  all the moment Bootstrap goes, and it is four small
  selectors.

Deliberately deferred, to be promoted only when a page
demonstrably needs them: `.sf-menu`/`.sf-menu-item`
(unnecessary if phase 5 keeps the connect and terminate
lists inline in a `<details>` rather than as floating
overlays, which is the recommendation — it also avoids
needing elevation at all); `.sf-card--interactive`;
`.sf-card-grid`; the `.sf-stat*` KPI tile set;
`.sf-dot`; `.sf-chip` (the dashboard's `.meta-tag` at
`:227-238` differs from `.status-badge` on five properties,
so folding them would be a visible change to worker cards);
`.sf-badge--outline`; `.sf-tooltip`; text-colour utilities;
and elevation.

### 3. Non-colour custom properties live in `sf.css`

`sf.css` defines `--sf-font-sans`, `--sf-font-mono`,
`--sf-radius-sm|--sf-radius|--sf-radius-lg` (4px / 6px /
8px, matching what the dashboard and the components already
use), and the knobs `--sf-container-max`, `--sf-page-pad`,
`--sf-code-max-height`, `--sf-table-cell-pad`.

They do **not** go in `tokens.css`. That file's stated remit
is every colour, and its central invariant — every token
defined in both palettes — is meaningless for a font stack.
Adding palette-independent values there would weaken an
audit rule that currently has teeth.

This has a consequence that must be handled in the same
commit, or the README's audit section starts producing false
positives (`README.md:150-176`):

1. "No `var(--)` references to tokens that `tokens.css` does
   not define" becomes "…that `tokens.css` **or `sf.css`**
   does not define" — otherwise `--sf-font-mono` reads as a
   typo'd token.
2. The hex/`rgba()` greps extend their target to `sf.css`,
   which should contain zero of either.
3. The `--sf-brand`-only-in-chrome rule has to accept
   `sf.css`'s `.sf-header` as chrome by definition.

### 4. Components consume the radius properties

`sf-tabs.js` hardcodes `8px` (`:71`) and `8px 14px` (`:58`);
`sf-theme-toggle.js` hardcodes `6px` (`:55`). Shadow DOM
means `sf.css` cannot reach inside them, so once `sf.css`
owns the radius scale the components would silently drift
from it.

Fix it here, in its own commit: components reference
`var(--sf-radius-lg, 8px)` and friends, with fallbacks
matching today's literal values exactly — the same pattern
the colour tokens already use. A page that loads the
components without `sf.css` renders exactly as it does
today, so this change carries no risk, and it keeps the
geometry coherent for pages that do load it.

### 5. A demo page, in canonical sfui only

`demo.html` at the sfui repository root: every first-cut
primitive rendered once, with the theme toggle wired, so the
stylesheet can be looked at in both palettes without booting
a consumer.

This is not a nice-to-have. Designing a stylesheet nobody
has ever rendered and then vendoring it into two
repositories is how you discover, in phase 5, that the
disclosure marker is invisible on the light palette. sfui's
own spin-out plan already lists a demo page as future work;
this is the phase where it pays for itself.

It is **not** part of the distributable set — like `docs/`
and `tools/`, consumers do not carry it, and it must not be
added to `vendor.sh`'s `files` array. Because the components
are ES modules, `file://` will not load them: the demo needs
`python3 -m http.server` from the repository root, which
`AGENTS.md` should say.

### 6. morphdom is promoted as-is, with no polling helper

morphdom moves into sfui as a vendored dependency alongside
`lit-core.min.js`, unmodified, keeping its existing header
comment recording version 2.7.7, the MIT licence and the
unpkg URL it came from.

What is **not** built here is an `sf-poll.js` page-infrastructure
helper wrapping the fetch-and-morph loop. It is tempting —
both consumers will run that loop — but the dashboard's
version is entangled with its per-panel change detection and
kerbside's will be a much simpler whole-container morph.
There is no way to tell which parts are genuinely common
until both exist. Revisit after phase 7; recorded in the
master plan's future work.

## Key facts front-loaded for the sub-agents

* The canonical sfui checkout is at
  `/srv/kasm_profiles/mikal/vscode/src/shakenfist/sfui`, on
  `develop`, currently at `5949092`. It has no CI workflows
  and its pre-commit config runs shellcheck on `tools/`
  only, so nothing lints CSS — care in review substitutes.
* The distributable set is the `files=()` array in
  `tools/vendor.sh:23` plus `components/`. Adding `sf.css`
  and `morphdom-umd.js` means editing that array **and**
  the Layout list in `README.md:19-30`. A file added to one
  and not the other is the classic mistake here.
* Every colour must come from the thirteen existing tokens
  plus `color-mix()`. The analysis confirmed no new colour
  token is required for the first cut. Established
  conventions to follow rather than reinvent: text on a
  coloured fill is `var(--sf-bg)` (`dashboard.html:335`) or
  `color-mix(in srgb, var(--sf-bg) 70%, transparent)`
  (`:455-457`); tinted fills are `color-mix(… 15%,
  transparent)` (`:196-218`); the header's brand rule is
  `color-mix(in srgb, var(--sf-brand) 45%, var(--sf-border))`.
* Where the two consumers genuinely differ, the shared rule
  takes kerbside's side and the dashboard keeps a local
  override, because the dashboard's overrides are unlayered
  and therefore win automatically: the header lockup is
  **left-aligned** (the dashboard centres it), and
  `--sf-container-max` defaults to `1100px` with kerbside
  setting it full-bleed.
* Four one-property deltas are known and accepted for the
  eventual dashboard adoption, and should be written into
  the sfui README or the private-ci plan rather than
  discovered later: queue-table links gain the
  `--sf-accent-hover` shift on hover (`:314` versus
  `:534`); `.clear-btn`'s `0.72rem` becomes `.sf-btn--sm`;
  `.console-output`'s `max-height: 180px` becomes an
  explicitly-set knob; `.container` and header centring stay
  as local overrides.
* private-ci is at `master`, clean, with its sfui copy
  stamped `5949092`. Its morphdom references to update are
  `dashboard.html:1014` (the script tag),
  `ARCHITECTURE.md:44` (the static tree) and
  `ARCHITECTURE.md:903-904` (the prose naming the path).
  `AGENTS.md:501` mentions morphdom without a path and needs
  no change.
* After the sfui commits land, both consumers are stale
  until re-vendored. Kerbside is in the daily audit's scope,
  private-ci is not.

## Repository and branch logistics

Work lands in three repositories, so this needs settling
before execution rather than mid-flight:

* **sfui**: the initial import was committed directly to
  `develop`. **Decided otherwise (2026-08-10):** every
  repository in this phase gets a branch and a pull
  request, because the consumers have GitHub-hosted CI
  that only a pull request triggers, and a change that
  reaches a default branch without a PR never gets tested.
  sfui's branch is `page-styles`.
* **kerbside**: the re-vendor commit goes on the existing
  `sfui-conversion` branch in the
  `kerbside-wt-sfui` worktree, alongside the rest of the
  conversion.
* **private-ci**: branch `morphdom-from-sfui`, for the same
  reason; the earlier sfui work landed straight on `master`,
  which is the habit this phase stops. Note the
  session-memory warning about concurrent sessions sharing
  that clone — check
  `git status` immediately before staging, and stop if the
  working tree changes unexpectedly.

## Execution

Five commits across three repositories, in this order.
Steps 3a to 3d are sfui; 3e and 3f are the consumers.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | low | haiku | none | In the sfui repository, promote morphdom: copy `conductor/static/morphdom-umd.js` from private-ci to the sfui root unmodified (keep its header comment recording version 2.7.7, MIT, and the unpkg source URL), add `morphdom-umd.js` to the `files=()` array in `tools/vendor.sh`, add it to the Layout list in `README.md`, and add an entry to the README's "Vendored dependencies" section in the same style as the `lit-core.min.js` entry, noting that consumers use it directly for poll-and-morph page refresh. Do not touch anything else. Run `pre-commit run --all-files`. |
| 3b | high | opus | none | In the sfui repository, write `sf.css` implementing exactly the first-cut selector list in the "Design decisions" section of `docs/plans/PLAN-sfui-conversion-phase-03-sfui-canonical.md` (in the kerbside repository — read it first, in full). Follow the layer/gate/naming architecture in decision 1 and the custom-property decisions in decision 3 exactly. Every rule should be derived from the cited `dashboard.html` line ranges in private-ci where one is given, so that the dashboard's eventual adoption is a class swap rather than a redesign. No colour literals, no `!important`, no id selectors. Also update `README.md`: add `sf.css` to the Layout list, add a section documenting the stylesheet (the layer, the `sf-page` gate, the class convention, the knobs), and make the three amendments to the "Auditing for consistency" section listed in decision 3. Add `sf.css` to the `files=()` array in `tools/vendor.sh`. |
| 3c | medium | sonnet | none | In the sfui repository, add `demo.html` at the repository root: every primitive `sf.css` defines, rendered once with realistic content, plus `<sf-tabs>` and `<sf-theme-toggle>` wired to `sf-theme.js` so both palettes can be inspected. It must link `sf-theme.js`, `tokens.css` and `sf.css` in the documented head order and put `sf-page` on the body. Do **not** add it to `tools/vendor.sh` — it is not distributable. Note in `README.md` and `AGENTS.md` that it is served with `python3 -m http.server` from the repository root, because the components are ES modules and `file://` will not load them. |
| 3d | medium | sonnet | none | In the sfui repository, make the two components consume the radius custom properties `sf.css` defines: `components/sf-tabs.js` (the `8px` at line ~71 and `8px 14px` at line ~58) and `components/sf-theme-toggle.js` (the `6px` at line ~55) become `var(--sf-radius-lg, 8px)` and `var(--sf-radius, 6px)` forms, with fallbacks matching the current literals **exactly** so a page without `sf.css` renders identically. Note the pattern in the README's component contract alongside the colour-fallback rule. Verify against `demo.html` in both themes. |
| 3e | low | haiku | none | In the kerbside worktree at `/srv/kasm_profiles/mikal/vscode/src/shakenfist/kerbside-wt-sfui`, re-vendor: run `tools/vendor.sh <worktree>/kerbside/api/static/sfui` from the updated, clean sfui checkout, and stage the result including the updated `.sfui-commit`. Mind that `.sfui-commit` is a dotfile that shell globs skip — add the directory, not a glob. No other file changes; no template touches. Confirm `tools/vendor.sh --check` then exits zero. |
| 3f | medium | sonnet | none | In private-ci on `master`, adopt sfui's morphdom and drop the duplicate: re-vendor with sfui's `tools/vendor.sh conductor/static/sfui`, delete `conductor/static/morphdom-umd.js`, repoint the script tag at `conductor/templates/dashboard.html:1014` to `/static/sfui/morphdom-umd.js`, and update `ARCHITECTURE.md:44` (the static tree) and `ARCHITECTURE.md:903-904` (the prose naming the path). `AGENTS.md:501` mentions morphdom without a path and needs no change. The dashboard must not adopt `sf.css` in this change — it does not link it and its body carries no `sf-page` class, so the new file is inert. Verify the dashboard still refreshes correctly in a browser before reporting. |

Management-session review for this phase, beyond the
standard checklist:

* Read `sf.css` in full against decision 2's list — this is
  the artifact two repositories get pinned to, and it is
  the one thing in this phase that no linter checks.
* `grep -n 'rgba(\|#[0-9a-f]\{3,8\}\|!important' sf.css`
  must return nothing.
* Every `var(--sf-*)` in `sf.css` must resolve to something
  `tokens.css` or `sf.css` itself defines.
* Open `demo.html` and look at every primitive in both
  palettes, and at the components after step 3d.
* `tools/vendor.sh --check` passes for both consumer copies
  after their re-vendors, and both `.sfui-commit` files
  match the new canonical HEAD.

## Success criteria

* sfui ships `sf.css` and `morphdom-umd.js` as
  distributables: both in `tools/vendor.sh`'s `files` array
  and in the README's Layout list, with morphdom documented
  under Vendored dependencies and `sf.css` documented in its
  own section.
* `sf.css` implements the first-cut list, contains no colour
  literals, no `!important` and no id selectors, and every
  token reference resolves.
* The README's audit section is amended so its greps stay
  true with a second stylesheet in the tree.
* `demo.html` renders every primitive and both components,
  correctly, in both palettes.
* A page that loads the components without `sf.css` is
  visually unchanged by step 3d.
* Kerbside's vendored copy is verbatim at the new canonical
  HEAD, with no template or Python change in that commit.
* private-ci has exactly one morphdom, under `sfui/`, and
  the dashboard still polls and morphs correctly, visually
  unchanged.
* The daily `sfui-vendor` audit reports kerbside verbatim at
  canonical HEAD once these land on the respective default
  branches.

## Risks

* **The naming is load-bearing and expensive to change.**
  Once two repositories vendor `sf.css`, renaming a class is
  a canonical change plus two re-vendors plus template
  edits. This is why the first cut is small and why the
  naming rules are written down before any CSS is.
* **Nothing lints CSS in sfui.** No CI, and pre-commit only
  covers shell. The demo page and the management-session
  read-through are the entire safety net; do not skip
  either.
* **The reset is the sharpest edge.** `* { margin: 0;
  padding: 0 }` shipping in a stylesheet that private-ci
  links but has not planned for would flatten the dashboard.
  The `sf-page` gate exists precisely for that, and must not
  be dropped as unnecessary indirection.
* **Kerbside loses Bootstrap behaviour, not just styling** —
  dropdown positioning, popover placement, `fixed-bottom`,
  button-group joining. The first cut replaces some of that
  deliberately (inline `<details>` instead of overlays,
  in-flow footer instead of fixed). Phases 4 and 5 should
  confirm the reduced set is acceptable rather than growing
  `sf.css` under pressure mid-conversion.
* **The dashboard has no template tests**, and its markup is
  built by string concatenation across a dozen render
  functions. Its adoption of `sf.css` should be one
  primitive at a time, and private-ci should get a smoke
  test first. Not this phase's work, but this phase should
  not make it harder.

## Future work recorded here

* The dashboard's incremental adoption of `sf.css`: the
  straight swaps are `.reliability-table`/`.cost-table`/the
  repo-activity table to `.sf-table`, `.cancel-btn` to
  `.sf-btn--danger`, `.status-badge` to `.sf-badge`,
  `.console-output` to `.sf-code`, `.startup-banner` to
  `.sf-banner--warn`, `.empty-state` to `.sf-empty`,
  `.category`/`.category-header` to
  `.sf-section`/`.sf-section-header`, `.table-footnote` to
  `.sf-footnote`, `.cost-num` to `.sf-num`, plus the body,
  header and footer chrome. The four accepted deltas are
  listed above. `.meta-tag` and `.size-badge` need a design
  decision first, not a swap.
* An `sf-poll.js` helper, once both consumers' polling loops
  exist (after phase 7).
* Text-colour utilities, which would remove nine inline
  `style="color:var(--sf-…)"` attributes from the dashboard.
* Elevation (`--sf-shadow-1`) if an overlay pattern ever
  lands — noting it would be the first non-colour-valued
  entry in `tokens.css`, which that file's prose currently
  rules out.

## Outcome

Done, 2026-08-10, in five commits across three
repositories as planned:

* shakenfist/sfui, on `develop`: `a6e2587` promotes
  morphdom, `221dcfa` adds `sf.css` and `demo.html`
  (steps 3b and 3c together, since the demo is how the
  stylesheet gets reviewed), `83ad8ce` moves the
  components onto the radius properties.
* kerbside, on `sfui-conversion`: the re-vendor at
  `83ad8ce`, verbatim per `tools/vendor.sh --check`.
* private-ci, on `master`: `9d90d22` re-vendors, drops
  the duplicate morphdom and repoints the script tag.

`sf.css` came out at 611 lines. It contains no colour
literal, no `!important` and no id selector, every
`var(--sf-*)` in it resolves to something `tokens.css` or
`sf.css` defines, and its braces balance. Packaging in
kerbside needed no change and was checked rather than
assumed: `python -m build` ships all ten vendored files,
`sf.css` and `morphdom-umd.js` included, in both the
wheel and the sdist.

### The demo page paid for itself immediately

`.sf-table-scroll` could not scroll. `.sf-table` is
`width: 100%`, so inside the wrapper it shrank to fit and
wrapped every cell instead of overflowing -- a
ten-column table squeezed into 900px of unreadable
two-line cells, which is exactly what kerbside's consoles
table would have become in phase 5. Fixed by sizing the
contained table to its content
(`.sf-table-scroll > .sf-table { min-width: max-content }`).
Nothing but rendering the page would have caught this;
the CSS is entirely valid and reads correctly.

The plan's other worry, that the native `<details>`
marker might be invisible on one palette, turned out
fine: it inherits `currentColor`, and both palettes were
screenshotted to confirm it.

### Corrections to this plan, found while executing it

* **Decision 4 overcounted the radius literals.**
  `sf-tabs.js:58` is `padding: 8px 14px`, not a radius.
  Only two literals existed -- the tab badge's `8px` and
  the theme toggle's `6px` -- and spacing stays local to
  a component under rule 3 of the component contract.
  Step 3d was therefore a two-line change, done in this
  session rather than by a sub-agent.
* **The tint convention was internally inconsistent.**
  The key facts state 15%, citing the badges, but
  `.sf-banner`'s cited source uses 12%. Converged on 15%
  so the stylesheet has one tint strength rather than two
  and the next primitive has nothing to choose.
* **The management-session grep contradicted itself.**
  `grep '!important' sf.css` "must return nothing", but
  documenting the no-`!important` rule in the file header
  trips it. The header says "important flags" instead;
  the README, which is not a grep target, spells it out.
* `--sf-code-max-height` had no specified default. It is
  `24rem`; the dashboard sets `180px` locally when it
  adopts `.sf-code`.
* `.sf-badge` variants are named for tokens
  (`--green`, `--amber`, `--red`, `--purple`, `--pink`,
  `--accent`, `--dim`) rather than for states, because
  what a state means is host-page policy under the
  component contract, and a partial set would force a
  re-vendor the first time a page needs another colour.

### The accepted dashboard deltas are seven, not four

Beyond the four the plan lists, adopting `sf.css` in the
dashboard will also change: table rows gain a hover
highlight (the plan mandates the rule but did not list
its consequence); `.cancel-btn` and `.clear-btn` change
typeface, because they set no `font-family` today and so
render in the browser's button font while `.sf-btn` is
`font-family: inherit`; and `.startup-banner` loses its
centring and shifts 12% to 15%. All are single-property
differences and none needs a canonical change.

Also worth knowing for that work: `.sf-status-line`
declassifies `#update-status`, but the dashboard selects
that element by id in JavaScript, so adoption there means
*adding* the class, not replacing the id.

### Verification

Both palettes of `demo.html` were rendered and read.
Headless Chromium defaults to `prefers-color-scheme:
dark`; `--blink-settings=preferredColorScheme=2` forces
light, which exercises the same auto path `sf-theme.js`
resolves, so no cookie is needed.

The no-visual-change criterion for step 3d was proved
rather than asserted: rendering both components on a page
with `tokens.css` but no `sf.css`, before and after the
change, produced byte-identical screenshots, and so did
`demo.html` with `sf.css` loaded.

private-ci was verified by running the conductor and
loading the dashboard, not by reading the diff: the new
path serves, the old one 404s, and the rendered page has
painted panels with a live timestamp, which only happens
if `paintPanel()` reached `morphdom()` without throwing.
`sf.css` is present but inert there -- no `<link>`, no
`sf-page` class.

### Still to do

Three pull requests, one per repository, none of them yet
pushed:

| Repository | Branch | Commits |
|------------|--------|---------|
| shakenfist/sfui | `page-styles` | `a6e2587`, `221dcfa`, `83ad8ce` |
| shakenfist/kerbside | `sfui-conversion` | the conversion so far, `88c8ba7`..`1353b34` |
| shakenfist/private-ci | `morphdom-from-sfui` | `9d90d22` |

**sfui merges first.** Both consumers' `.sfui-commit`
names `83ad8ce`, which does not exist on canonical
`develop` until that pull request lands, and the daily
`sfui-vendor` audit checks the recorded commit against
canonical HEAD. Kerbside's own CI does not check the
vendored copy -- phase 2 deliberately rejected a
`vendor.sh --check` CI step -- so its pull request can be
raised and tested in parallel; only the audit is affected.

Nothing breaks if they merge out of order. Each consumer
carries its own copy of every file it serves, so
private-ci's dashboard keeps working from
`conductor/static/sfui/morphdom-umd.js` whatever sfui's
branch is doing; the only cost of merging a consumer first
is a provenance stamp that briefly points at a commit
canonical sfui has not published, and private-ci is not in
the audit's matrix at all.

### The merge commit makes the stamp stale

sfui's pull request merged as a merge commit, so canonical
`develop` is `494ea9e` and the `83ad8ce` both consumers
recorded is an ancestor of it rather than HEAD. No file
changed -- `git diff 83ad8ce 494ea9e` is empty -- but the
`sfui-vendor` audit fails on *behind canonical HEAD* as
well as on edited content, so kerbside would have been
flagged for a stamp that describes byte-identical files.

Kerbside's branch was re-vendored at `494ea9e` before its
pull request merged. The diff is one line, `.sfui-commit`
alone, and `tools/vendor.sh --check` then passes. private-ci
still records `83ad8ce`; it is not in the audit's matrix, so
its stamp gets corrected by the next re-vendor rather than
by a commit of its own.

The rule for phases 4 onwards: **vendor from canonical
`develop` after the sfui change has merged**, never from
the sfui feature branch, or every consumer lands one merge
commit behind.

## Back brief

Before executing this phase, back brief the operator on the
intended work and how it aligns with this plan and the
master plan — including the sfui branch question in
"Repository and branch logistics", which needs an answer
before anything is pushed.
