# sfui conversion phase 9: teardown, docs and issue closure

Planning effort: **medium**, as the master plan specifies. The
judgement in this phase is about what to delete and what the
documentation should say afterwards, not about behaviour: no
handler, no route and no rendered page changes. The one
non-mechanical decision is the base template rename, and it is
argued in decision 1.

## Situation

Phases 1 to 8 rebuilt every page of the admin UI on sfui. All
five templates extend `base-sfui.html`, the polling loop is a
fetch-and-morph cycle, and both terminate actions are POST
behind a CSRF header. What remains is the corpse of the old UI:
a `base.html` nothing extends, 8.6 MB of Bootstrap, jQuery and
axios that nothing loads, a `logo.svg` superseded by the sfui
brand asset, and documentation and tooling configuration that
still describe all of it as live.

This is the last phase. When it merges, the master plan is
complete and #244 -- "The web admin interface could do with a
rewrite / style pass" -- can be closed.

## What the survey found

The master plan's phase 9 section (`PLAN-sfui-conversion.md`
lines 371-379) is mostly accurate. Three corrections, and one
addition it does not mention at all.

**1. AGENTS.md needs no change.** The master plan says to update
it. It does not mention `base.html`, Bootstrap, jQuery, axios or
`logo.svg` -- PR #318 (`llm-doc-structure`) moved that material
out into `docs/` on 2026-08-16. What AGENTS.md still carries is
the "never edit `kerbside/api/static/sfui/` in place" rule at
`AGENTS.md:133-141`, which this phase does not make false. The
claim is corrected at source in the master plan's phase section
as part of the planning commit, so this does not need
rediscovering.

**2. `.vscode/review-scope.toml` also needs pruning, and the
master plan does not mention it.** Its exclude list carries
`kerbside/api/static/js/*` (`:32`) and its comment at `:20-24`
names "bootstrap, jquery, axios" as the vendored third-party
JavaScript being excluded from whole-file review. Once that
directory is gone the pattern matches nothing and the comment
describes files that do not exist. The `.weaudit` review marks
themselves are clean -- a grep for the doomed paths in
`.vscode/*.weaudit` returns nothing -- so no mark is orphaned by
the deletion and the `prune-reviews` workflow has nothing to do.

**3. The sfui README audit greps already pass, except for one
match inside the file being deleted.** The audit rules are at
`kerbside/api/static/sfui/README.md:302-338`. Run over
`kerbside/api/templates/` today:

- `grep -rn 'rgba(\|#[0-9a-f]\{6\}'` returns exactly one hit,
  `base.html:22` (`style="background-color: #1d6d87;"`). Deleting
  the file is the fix; there is no separate remediation to do.
- Every `var(--...)` reference in the templates is
  `--sf-font-sans`, defined at `sf.css:96`. Nothing typo'd.

So the audit is a verification step in this phase, not work. It
is written into the Verification section rather than given a step
of its own.

**4. Nothing outside `base.html` references the doomed assets.**
`grep -rn "/static/"` across the tree, excluding the static
directory itself, returns: `base.html:6,7,8,26` (the Bootstrap
CSS, the Bootstrap bundle, axios and `logo.svg`), `base-sfui.html`
(sfui paths only), `sf-theme.js`'s own docstring, the pre-commit
comment and `review-scope.toml`. No test, no `tools/` script, no
workflow, no `demo/` file and no `docs/` page loads them.

**5. Packaging needs no change.** `pyproject.toml:139` lists
`packages = ["kerbside", "kerbside.rpc"]` and there is no
`MANIFEST.in`; static assets reach the wheel through the
setuptools_scm git file finder. Deleting tracked files simply
stops them shipping. (That the file finder is the only thing
shipping them is issue #326, which is a real packaging bug and
explicitly out of scope here -- fixing it would change what is in
the wheel, which is not a thing to do inside a deletion commit.)

**6. Confirmed as the master plan states:** `base.html` is
extended by nothing (`grep -rn 'extends' kerbside/api/templates/`
returns `base-sfui.html` five times); `static/css/` holds 32
files and `static/js/` 14, plus `logo.svg`, 8.6 MB in total;
`docs/development.md` has "Bootstrap CSS" (`:163-167`) and
"Axios" (`:169-172`) subsections under "Vendored web assets";
`ARCHITECTURE.md:366-374` calls `base.html` "unreferenced,
pending deletion"; the two rewriting pre-commit hooks exclude
`^(kerbside/api/static/(css|js|sfui)/|...)` at `:51` and `:53`.

## Mission

Delete the Bootstrap-era UI, rename the base template to the
name it deserves now that it is the only one, and leave every
document, comment and tooling exclusion describing the tree that
actually exists. Close #244.

## Design decisions

### 1. `base-sfui.html` is renamed to `base.html`

The master plan calls for it and I am keeping it, but this is
the decision a reviewer is most likely to argue with, so here is
the case on both sides.

Against: it churns five `{% extends %}` lines, four documentation
references and two test comments for no functional gain, and it
makes every historical phase plan's mention of "base-sfui.html"
read as a file that never existed.

For: `-sfui` was a transitional qualifier that existed to
distinguish two coexisting bases. With one base left, the
qualifier is not merely redundant, it is actively misleading --
it implies a non-sfui base is still somewhere in the tree, which
is exactly the state this phase ends. Historical plans are
point-in-time records and are excluded from review scope
(`review-scope.toml:27`); we do not rewrite them, and phase 8
already established that precedent when its own commit message
outlived the file it described.

The delete and the rename go in **one commit**, with `git mv`
after `git rm`, so that git records a rename of the surviving
file rather than an unrelated add and delete.

### 2. The deletion is one commit, mechanically verifiable

`static/css/`, `static/js/`, `static/logo.svg` and the old
`base.html` all die together. They are one thing -- the
Bootstrap-era UI -- and splitting them across commits would
produce an intermediate state where a template references a
deleted stylesheet. The rename in decision 1 rides in the same
commit for the same reason.

### 3. A regression test that static references resolve

Pure deletion leaves nothing behind to stop the next person
reintroducing a 404. This phase adds a test that renders every
page, extracts every `src=` and `href=` beginning `/static/`,
and asserts each resolves to a file that exists under
`kerbside/api/static/`.

This needs a word of justification, because
`test_api_html.py`'s class docstring says assertions "must only
ever look for fixture data markers ..., never for markup". That
rule exists because the sfui conversion was going to rewrite
every template, and a markup-coupled test would have broken on
each rewrite. The new test does not assert *which* markup is
present -- it asserts a referential invariant between whatever
markup exists and the filesystem, which survives any rewrite.
It goes in its own class with a docstring saying so, and the
original class docstring's now-past-tense claim ("The sfui
conversion is going to rewrite every template") is corrected in
the same commit.

### 4. Documentation says what is true now, not what was removed

`docs/development.md`'s "Bootstrap CSS" and "Axios" subsections
are deleted outright rather than rewritten into a "we used to
use Bootstrap" note. Git history is the record of what was
removed; a documentation page is a description of the current
system. The one place a backward reference earns its keep is
the Page polling section (`:216-239`), which explains *why* the
morph cycle exists by contrasting it with the old meta refresh
-- that sentence stays, with the file it names updated for the
rename.

### 5. The vendored sfui copy is not re-synced here

Issue #296 reports the vendored copy is two commits behind
canonical sfui. Re-vendoring is the sanctioned fix, but it
belongs in its own change: it rewrites files under
`static/sfui/` that this phase must leave alone, and mixing a
vendored-copy bump into a deletion commit makes both harder to
review. Out of scope, and noted in Future work.

### 6. #244 is closed by hand at the end, not by the commit

The closing comment should summarise what the nine phases
actually delivered, which is a thing to write once the branch is
green rather than a `Fixes #244` trailer buried in a
documentation commit. The final step drafts the comment; posting
it and closing waits for Michael, per the standing rule that PR
and issue actions are his.

## Key facts front-loaded for the sub-agents

- Five templates extend the base: `login.html`, `consoles.html`,
  `sessions.html`, `sources.html`, `audit.html`, each at line 1.
- `base-sfui.html` is 159 lines; the doomed `base.html` is 77.
- Files to delete: `kerbside/api/static/css/` (32 files),
  `kerbside/api/static/js/` (14 files),
  `kerbside/api/static/logo.svg`, and
  `kerbside/api/templates/base.html`.
- Live references to `base-sfui.html` outside `docs/plans/`:
  `ARCHITECTURE.md:366` and `:371`, `docs/development.md:219`
  and `:255`, `kerbside/tests/unit/test_api_html.py:72` and
  `:88`, and `tools/preview-templates.py:17`. That last one is
  outside `kerbside/`, so the step 9a grep is widened to
  `tools/` to catch it.
- Live references to the old `base.html` outside `docs/plans/`:
  `ARCHITECTURE.md:369`, `docs/development.md:223`.
- `docs/plans/` is a point-in-time record and is **not** updated
  for the rename. Neither is any commit message already in
  history.
- Nothing under `kerbside/api/static/sfui/` may change. It is a
  verbatim vendored copy stamped in `.sfui-commit`; the daily
  `sfui-vendor` audit compares it against canonical sfui.
- The repo check is `pre-commit run --all-files`, which runs
  actionlint, shellcheck, the hygiene hooks, flake8 and the unit
  tests.

## Repository and branch logistics

Worktree `../kerbside-wt-teardown`, branch
`sfui-conversion-phase-09`, rebased onto `develop` at `9f9a36d`
after the survey (it was originally cut at `eb14012`). Develop
reflowed `ARCHITECTURE.md` in between, so every line number this
plan cites for that file was re-checked against `9f9a36d`; the
content is unchanged. This plan file lives on that branch and
lands with the code.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | low | haiku | none | In the `kerbside-wt-teardown` worktree, delete the Bootstrap-era UI and rename the base template, in one commit. `git rm -r kerbside/api/static/css kerbside/api/static/js`; `git rm kerbside/api/static/logo.svg kerbside/api/templates/base.html`; then `git mv kerbside/api/templates/base-sfui.html kerbside/api/templates/base.html`. Update the `{% extends "base-sfui.html" %}` line (line 1) in each of `login.html`, `consoles.html`, `sessions.html`, `sources.html` and `audit.html` to `{% extends "base.html" %}`, and the two in-comment mentions of `base-sfui.html` at `consoles.html:8` and `login.html:8`. Also fix `tools/preview-templates.py`'s module docstring (`:17`), which says "Only pages that have actually been converted onto base-sfui.html are listed in PAGES below -- this script must never invent fixtures for a page that has not been converted yet": the base is now `base.html` and there is no unconverted page left, so say the five pages in PAGES are every page the app renders and that a page added later needs its fixtures added here. It goes in this commit, not a later one, so that no commit in history names a template that does not exist. Change nothing else in any template -- not the markup, not the scripts, not `includes/`. Nothing under `kerbside/api/static/sfui/` may be touched. Afterwards `grep -rn 'base-sfui' kerbside/ tools/` must return nothing and `grep -rn '/static/\(css\|js\)/\|/static/logo.svg' kerbside/` must return nothing. Run `pre-commit run --all-files`; the unit tests render all five pages and must pass. Commit subject: `Delete the Bootstrap era admin UI.` |
| 9b | medium | sonnet | none | Add a regression test to `kerbside/tests/unit/test_api_html.py` that would have caught a dangling static reference. New class `StaticAssetReferenceTestCase`, after the existing classes, with a docstring explaining why it is exempt from the markup rule in `LoginPageTestCase`'s docstring (`:43-51`): it asserts a referential invariant between whatever markup exists and the filesystem, not the presence of particular markup, so it survives template rewrites. It should render each page the existing tests render -- follow their `api.app.test_client()` and `mock` setup exactly, reusing the module-level `CONSOLE`/`SESSIONS`/`SOURCE`/`AUDIT_EVENT` fixtures -- extract every `src="/static/..."` and `href="/static/..."` value with a regex over the response body, assert the set is non-empty (a page that loads no assets would otherwise pass vacuously), and assert each path resolves to an existing file under `kerbside/api/static/`, locating that directory relative to `kerbside.api.__file__` rather than the test's cwd. In the same commit, fix `LoginPageTestCase`'s docstring: "The sfui conversion is going to rewrite every template" is now past tense -- say that the conversion did rewrite every template and the rule stands for future ones. Python style: single quotes except docstrings, 120 columns, no trailing whitespace. Verify the test fails if you temporarily point a template at `/static/nope.css`, then put the template back. Commit subject: `Test that static asset references resolve.` |
| 9c | medium | sonnet | none | Update the documentation for a tree with no Bootstrap in it. In `docs/development.md`: delete the "### Bootstrap CSS" (`:163-167`) and "### Axios" (`:169-172`) subsections entirely, leaving "## Vendored web assets" with "### sfui" and "### Page polling" under it; in Page polling, `:219` and `:223` refer to `base-sfui.html` and to "The old `base.html`" -- the file is now called `base.html` and the old one no longer exists, so rewrite that contrast to say the page previously used a `<meta http-equiv="refresh">` without implying a second file is still present; fix the `base-sfui.html` mention at `:255`. In `ARCHITECTURE.md`, rewrite the `templates/` directory note (`:366-374`): `base.html` is the sfui base extended by all five pages, and the "base.html (the old Bootstrap base) is unreferenced, pending deletion" sentence goes; update the `static/` note (`:386`) which says "CSS, JS (static/icons/ retired...)" -- the only thing left under `static/` is `sfui/`. Do **not** edit `AGENTS.md`: it mentions none of this (verified), and its sfui vendoring rule stays true. Do **not** edit anything under `docs/plans/`. Afterwards, `grep -rn -i 'bootstrap\|jquery\|axios' --exclude-dir=plans docs/ ARCHITECTURE.md AGENTS.md README.md` must return nothing but `RELEASE-SETUP.md`-style unrelated uses of the word "bootstrap" (there are none in these files today; `demo/Dockerfile` and `RELEASE-SETUP.md` are out of scope and use the word in its ordinary sense). Commit subject: `Document the admin UI without Bootstrap.` |
| 9d | low | haiku | none | Prune the tooling exclusions that name the deleted directories. In `.pre-commit-config.yaml`, both `trailing-whitespace` (`:51`) and `end-of-file-fixer` (`:53`) exclude `^(kerbside/api/static/(css|js|sfui)/|\.vscode/.*\.weaudit)`; narrow both to `^(kerbside/api/static/sfui/|\.vscode/.*\.weaudit)`. Update the comment at `:29-32` which says "Vendored Bootstrap assets under kerbside/api/static/{css,js}/ and the vendored sfui copy ... are excluded" so it describes only the sfui copy. Leave the whole `.vscode/.*\.weaudit` half of both patterns and its long comment block (`:34-47`) exactly as they are. In `.vscode/review-scope.toml`, remove `'kerbside/api/static/js/*'` from the `exclude` list (`:32`) and rewrite the comment at `:20-24` so it explains only the sfui exclusion -- the sentence naming "bootstrap, jquery, axios" as vendored third-party JavaScript describes files that no longer exist. Run `pre-commit run --all-files`: it must pass, and it must not now want to rewrite anything it previously skipped (if it does, that is a real finding -- report it rather than committing a mass whitespace change). Commit subject: `Prune the Bootstrap tooling exclusions.` |
| 9e | medium | sonnet | none | Close the phase out. Set phase 9 to `Done` in the Execution table of `docs/plans/PLAN-sfui-conversion.md` (`:261`) and mark the master plan itself complete in its status line and in the `docs/plans/index.md` row for the sfui conversion -- status `In progress` becomes `Complete`, and the phase 9 entry gets its one-line outcome summary in the same style as phases 1-8. Append an Outcome section to `docs/plans/PLAN-sfui-conversion-phase-09-teardown.md` recording what was deleted (file counts and megabytes), what the rename touched, and anything the survey got wrong. Then draft -- do **not** post -- a closing comment for issue #244 summarising the nine phases, and put it in the phase plan's Outcome section for Michael to post. Do not use `gh issue close`, do not add a `Fixes #244` trailer. Commit subject: `Record the phase 9 outcome.` |

## Verification

Run in the worktree after 9d, before 9e:

```shell
# Nothing references the deleted assets or the old name.
grep -rn 'base-sfui' kerbside/ tools/ docs/ ARCHITECTURE.md \
    AGENTS.md
grep -rn '/static/css/\|/static/js/\|/static/logo.svg' \
    --exclude-dir=plans kerbside/ docs/ .vscode/ \
    .pre-commit-config.yaml

# The sfui audit greps, over the finished templates.
grep -rn 'rgba(\|#[0-9a-f]\{6\}' kerbside/api/templates/
grep -rhno 'var(--[a-z0-9-]*' kerbside/api/templates/*.html \
    kerbside/api/templates/includes/*.html | sed 's/.*var(//' \
    | sort -u

# The vendored copy is untouched.
git diff --stat develop -- kerbside/api/static/sfui/

# What is left under static/.
find kerbside/api/static -maxdepth 1

pre-commit run --all-files
```

Expected: the first two greps silent except for `docs/plans/`
hits, which are excluded above; the hex/rgba grep silent; the
token list exactly `--sf-font-sans`; the sfui diffstat empty;
`find` showing only `static/` and `static/sfui/`; pre-commit
green.

The one check a grep cannot make is that the pages still *look*
right. Render them with the preview procedure in
`docs/development.md`'s "Previewing templates" section and
confirm the five pages, both palettes, still have their styling
-- the failure this phase could plausibly cause is deleting an
asset a page quietly depended on, and 9b's test catches a
dangling reference but not a missing rule.

## Success criteria

* `kerbside/api/static/` contains `sfui/` and nothing else.
* No file outside `docs/plans/` and outside git history
  mentions Bootstrap, jQuery, axios or `base-sfui.html` --
  including `tools/`, which the phase 8 era greps did not
  cover.
* The base template is `kerbside/api/templates/base.html` and
  all five pages extend it; `git log --follow` on it reaches the
  sfui base's history, confirming git recorded a rename.
* A template referencing a non-existent static file fails the
  unit tests (demonstrated in 9b, not merely asserted).
* The sfui README audit greps over `kerbside/api/templates/`
  return no hex or `rgba()` literals and no undefined token.
* `git diff develop -- kerbside/api/static/sfui/` is empty, so
  the daily `sfui-vendor` audit is unaffected.
* `pre-commit run --all-files` passes, and the hygiene hooks
  report no newly-in-scope files needing rewrites.
* The master plan and `docs/plans/index.md` both say Complete.
* A closing comment for #244 is drafted for Michael to post.

## Risks

- **A page silently loses styling.** The templates were
  converted phases ago and load only sfui assets, but a
  hand-verified render is cheap insurance and no test can
  replace it. Mitigation: the preview render in Verification,
  checked by the management session, both palettes.
- **The rename churns a reference nobody greps for.** The greps
  in Verification cover the tree; the residual risk is a
  reference in a place greps exclude, which is `docs/plans/`
  and is deliberate.
- **Pre-commit rewrites the newly-in-scope files.** Narrowing
  the exclude in 9d brings nothing new into scope, because the
  directories are gone -- but if the hooks do want to rewrite
  something, it means the exclude was hiding a file we do own.
  9d's brief says to report that rather than commit it.
- **The deletion is hard to review as a diff.** 47 deleted
  vendored files will swamp `gh pr diff`. Mitigation: they are
  one commit with nothing else in it, so a reviewer can check
  the commit's file list rather than its contents, and the
  functional change in that commit is five `{% extends %}` lines
  plus a rename.

## Future work recorded here

* Re-vendor sfui to pick up the two commits the copy is behind
  (issue #296). Deliberately not done here -- see decision 5.
* Issue #326: static assets and subpackages ship only via the
  setuptools_scm git file finder, so a git-less build produces a
  broken install. Adjacent to this phase's tree but a packaging
  bug, not a teardown item.
* Issue #319: the `.vv` GET that mints a token, mitigated with
  `SameSite=Lax` in phase 8 rather than converted.
* The `sf-poll.js` question the master plan defers: whether the
  fetch-and-morph loop becomes a shared sfui helper now that
  both the dashboard's loop and kerbside's exist. This is work
  in shakenfist/sfui, not kerbside, and it outlives this plan.

## Back brief

Before executing any step, back brief the operator on the
understanding of this plan and how the intended work aligns with
it.

One gate: **stop after 9a and show the file list before
continuing.** The deletion is the irreversible-feeling step and
its shape should be agreed before four more commits land on top
of it. The rename in decision 1 is the specific thing to confirm
-- it is cheap to abandon at that point and expensive to unpick
afterwards.

## Outcome

Executed 2026-08-20 on branch `sfui-conversion-phase-09`. Five
commits, one more than the plan's five steps, because 9a was
split in two -- see below.

**What was deleted.** 47 files, 8.4 MB: `static/css/` (32 files
of Bootstrap 5.3), `static/js/` (14 files -- Bootstrap bundles,
jQuery 3.7.0, axios 1.6.5), `static/logo.svg`, and the 77-line
`base.html` nothing extended. `kerbside/api/static/` now holds
`sfui/` and nothing else. The vendored sfui copy is byte for
byte unchanged, so the daily `sfui-vendor` audit is unaffected.

**What the rename touched.** Five `{% extends %}` lines, two
in-template comments, two comments in `test_api_html.py`, one
docstring in `tools/preview-templates.py`, two directory notes
in `ARCHITECTURE.md` and two references in `docs/development.md`.
Nothing under `docs/plans/`, deliberately.

**Where the plan was wrong.**

1. **The single-commit shape defeated its own success
   criterion.** Decision 2 put the delete and the rename in one
   commit, with `git mv` after `git rm`, expecting git to record
   a rename. It does not: because the old `base.html` was
   deleted and the new one moved onto that same path in the same
   commit, git recorded `D base-sfui.html` plus `M base.html`,
   and `git log --follow base.html` walked the Bootstrap base's
   history back to the initial commit instead of reaching the
   sfui base's. At the plan's review gate the step was split in
   two -- delete everything old, then rename -- which records a
   true `R100`. The intermediate state is sound: after the first
   commit all five pages still extend `base-sfui.html`, which
   still exists, so nothing dangles. Decision 2's reasoning was
   right about not splitting the *asset* deletion from the
   `{% extends %}` update; it just did not follow that a
   same-path swap is not a rename to git.

2. **The survey missed `tools/preview-templates.py`.** Its
   module docstring named `base-sfui.html` and described a world
   with unconverted pages left in it. The survey's grep for live
   references covered `kerbside/`, `docs/`, `ARCHITECTURE.md`
   and `AGENTS.md` but not `tools/`. Found during the plan's
   recovery from a crashed session, folded into 9a, and the
   greps widened.

3. **8.6 MB was `du` block-rounding.** The real figure is 8.4 MB
   of file content.

Everything else the survey found held: `AGENTS.md` needed no
change, `.vscode/review-scope.toml` did need pruning, no
`.weaudit` mark referenced a deleted path, and the sfui README
audit greps pass over the finished templates -- no hex or
`rgba()` literals, and `--sf-font-sans` is the only token
referenced.

**Verification.** `pre-commit run --all-files` green at every
commit. The new `StaticAssetReferenceTestCase` was proved to
fail by pointing `base.html` at `/static/nope.css`, which failed
all five of its tests. All five pages were rendered through
`tools/preview-templates.py` and screenshotted in both palettes:
brand chrome, theme toggle, tab strip, tables, disclosures, the
two-step terminate control and the poll footer all present and
styled.

### Drafted closing comment for #244

Not posted. For Michael to post, or to edit first.

> Closed by the sfui conversion, which landed over nine phases.
>
> The admin UI is now built on [sfui](https://github.com/shakenfist/sfui),
> the Shaken Fist design system, making kerbside its second
> consumer after the private-ci conductor dashboard. What
> changed:
>
> - **All five pages rebuilt** on sfui tokens and components --
>   login, consoles, sessions, sources and audit -- with a
>   shared base template, brand chrome, a tab-strip nav and a
>   three-way theme toggle that follows the system palette by
>   default.
> - **Bootstrap, jQuery and axios are gone**, 8.4 MB across 47
>   vendored files, along with the last hand-rolled markup
>   defects the conversion surfaced.
> - **Reloads replaced by polling**: pages that used a
>   `<meta http-equiv="refresh">` now fetch and morph their
>   content every 30 seconds, so scroll position, focus, an open
>   disclosure and a half-confirmed terminate survive a tick.
>   A failed poll reports staleness rather than throwing away a
>   readable page.
> - **Destructive actions are POSTs** behind a CSRF
>   double-submit header (#133). One `.vv` GET that mints a
>   token could not convert; it is mitigated with a
>   `SameSite=Lax` cookie and tracked separately as #319.
> - **A safety net that did not exist before**: HTML smoke
>   tests over every page, asserting on fixture data rather than
>   markup so they survive future rewrites, plus a test that
>   every `/static/` reference a page makes resolves to a file
>   that is actually there.
>
> The plan and its nine phase documents are in `docs/plans/`,
> starting at `PLAN-sfui-conversion.md`.
