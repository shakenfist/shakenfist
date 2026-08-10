# sfui conversion phase 2: vendor sfui and its plumbing

Master plan: `PLAN-sfui-conversion.md`. This phase was
planned at medium effort per the master plan's phase notes.

## Situation

sfui lives canonically at https://github.com/shakenfist/sfui
and is consumed by copying its distributable files into a
consumer's static assets. This phase makes kerbside a
consumer: the files arrive, the repository's tooling learns
to leave them alone, the packaging is proven to ship them,
and the documentation says where they came from.

Nothing in the UI changes. No template references the new
directory yet, so the running application is byte-identical
in behaviour before and after this commit. That is
deliberate: the plumbing is the risk here, and it is worth
isolating in a commit that cannot break a page.

Grounded facts about what is being vendored (sfui at
`5949092`, the current canonical HEAD):

    README.md              10 KB
    tokens.css            3.4 KB
    sf-theme.js           3.0 KB
    shakenfist-logo.svg    22 KB
    lit-core.min.js        16 KB
    components/sf-tabs.js         5.3 KB
    components/sf-theme-toggle.js 4.3 KB
    .sfui-commit          (the source sha, written by the script)

About 65 KB in total, against the ~8.5 MB of Bootstrap,
jQuery and axios that phase 9 deletes.

## Mission

`kerbside/api/static/sfui/` exists as a verbatim vendored
copy stamped with its source commit; kerbside's tooling
(pre-commit, review scope) treats it as vendored rather than
as ours to edit; the wheel demonstrably ships it; and the
docs record how to update it and the rule that it is never
edited in place.

## Why this path

`kerbside/api/static/sfui/`, served at `/static/sfui/...`.
The sfui README's documented `<head>` snippet is

    <script src="/static/sfui/sf-theme.js"></script>
    <link rel="stylesheet" href="/static/sfui/tokens.css">

and private-ci serves its copy from the same URL shape. The
Flask app already serves `kerbside/api/static/` at `/static`
(`kerbside/api.py:43-46`), so this path needs no server
change at all.

## Key facts front-loaded for the sub-agents

**The vendoring is a script run, never a copy by hand.**
From a clean checkout of shakenfist/sfui:

    tools/vendor.sh <kerbside-worktree>/kerbside/api/static/sfui

The script (`tools/vendor.sh` in sfui) copies the
distributable set, `rsync -a --delete`s `components/`, and
writes `git rev-parse HEAD` into `<target>/.sfui-commit`. It
warns if the sfui tree is dirty, because the stamp would
then describe contents that do not exist in any commit. So:
`git pull --ff-only` and confirm `git status` is clean in
the sfui checkout *before* running it. The local sfui
checkout is at
`/srv/kasm_profiles/mikal/vscode/src/shakenfist/sfui`.

**`.sfui-commit` is a dotfile, and shell globs skip
dotfiles.** `git add kerbside/api/static/sfui/*` would
silently omit the provenance stamp, leaving a copy that the
drift audit cannot verify. Add the directory
(`git add kerbside/api/static/sfui`), and check `git status`
lists `.sfui-commit` before committing. A missing
`.sfui-commit` is exactly the failure that made the private-ci
vendoring commit need repair.

**pre-commit must not touch the vendored files.**
`.pre-commit-config.yaml:35-38` excludes
`^kerbside/api/static/(css|js)/` from `trailing-whitespace`
and `end-of-file-fixer`. Extend that to
`^kerbside/api/static/(css|js|sfui)/` and extend the comment
above it (lines 30-31) to say sfui is vendored too.

Be honest about why: every current sfui file already ends in
a newline and has no trailing whitespace, so the hooks would
not modify anything today. The exclude is prophylactic. It
matters because a hook that reformats a vendored file
creates drift that reads, to the audit and to
`vendor.sh --check`, as "somebody edited the vendored copy",
and phase 3 adds files (a minified morphdom, a stylesheet)
whose hygiene we do not control. Encoding the invariant now
is consistent with how the Bootstrap exclude is already
justified in that comment.

`check-added-large-files --maxkb=1024` is not a problem: the
largest vendored file is the 22 KB logo.

**The review queue must not swallow the vendored copy.**
`.vscode/review-scope.toml` includes `*.js` and `*.md` in
whole-file review and excludes
`kerbside/api/static/js/*` as vendored third-party code
(with the reasoning in the header comment, lines 20-23). Add
`kerbside/api/static/sfui/*` to the `exclude` list — one
pattern covers the components, the theme script, lit-core
and the vendored README, since `*` matches across directory
separators per the file's own header. Extend the header
comment to mention sfui. This also keeps ~35 KB of
third-party JavaScript out of the human-review backlog that
the `review-coverage` consistency audit measures (issue #227
is already open against kerbside for that backlog).

**Packaging works today by implication, and must be proven
for the new directory.** `pyproject.toml:133-134` declares
`packages = ["kerbside", "kerbside.rpc"]` with no
`package-data` and no `include-package-data`; assets ship
because setuptools' pyproject mode defaults
`include-package-data` to true and the setuptools_scm file
finder enumerates git-tracked files. That this works is not
speculation — the wheel pip built into `.tox/py3` contains
`kerbside/api/static/{css,icons,js,logo.svg}` and its
`RECORD` lists 54 `api/static` entries. `kerbside/api/` has
no `__init__.py`, so these ship as package data of
`kerbside`, which is fine.

What is untested is a *new* subdirectory containing a
dotfile. Verify it, do not assume it. If the check shows a
gap, the fix is an explicit `[tool.setuptools.package-data]`
entry; if there is no gap, do not add one — implicit
inclusion is already doing the job and an unnecessary
declaration is one more thing to keep in sync.

**Documentation that becomes wrong the moment this lands.**
Each of these describes the static assets as they are today
and must describe them as they will be after this commit,
without pre-announcing phase 9's deletions:

* `docs/development.md:99-110`, the "Vendored web assets"
  section (currently Bootstrap 5.3, jQuery 3.7.0, axios
  1.6.5). Add sfui: what it is, that it is vendored from
  shakenfist/sfui, the never-edit-in-place rule, and the
  exact re-vendor command. This is the human-facing home
  for the instructions, per the docs-over-AGENTS.md policy.
* `AGENTS.md` (see its static-assets mentions around lines
  16 and 58): a brief agent-facing rule — the sfui directory
  is a vendored copy, change canonical sfui and re-vendor,
  with a pointer to `docs/development.md`. Brief, not a
  duplicate of the docs page.
* `ARCHITECTURE.md:393-396`, the directory-structure comment
  (`static/ # CSS, JS, icons`) — mention the sfui directory.

`README.md` is not touched: it is a pitch, and a vendored
asset directory is not a pitch item.

**Not doing: a `vendor.sh --check` step in kerbside CI.**
The sfui README offers it and it is tempting, but it would
mean cloning sfui on every CI run to check something the
daily `sfui-vendor` consistency audit in
shakenfist/development already checks — including the
staleness dimension, which a CI check pinned to whatever
sfui HEAD happens to be that minute would report
inconsistently. Recorded in the master plan's Future work
rather than built here.

## Sequencing consequence worth knowing

From the moment this commit reaches `develop`, the daily
`sfui-vendor` audit covers kerbside: it verifies the copy is
verbatim at the recorded commit, and that the recorded
commit is canonical HEAD. Phase 3 then changes canonical
sfui (adding the shared stylesheet and morphdom), which
makes this copy stale by exactly one commit until it is
re-vendored.

That is not a reason to reorder the phases — proving the
plumbing in a commit that cannot break a page is worth more
than avoiding one re-run of a one-line script, and phase 3
names the re-vendor as an explicit step. But note the
consequence: the audit reads each repository's default
branch, so if all of these phases land on this branch and
merge as one pull request, the intermediate staleness is
never visible. If phase 2 merges to `develop` on its own and
phase 3 follows later, expect the audit to file a "behind
canonical" issue against kerbside in between, which closes
itself on the re-vendor.

## Execution

One commit for the phase: the copy, the tooling that keeps
it verbatim, and the docs are one logical change, and
landing the copy without the pre-commit exclude would be a
trap for the next person to run the hooks.

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 2a   | medium | sonnet | none      | In the kerbside worktree, vendor sfui and make the repository treat it as vendored: run sfui's `tools/vendor.sh` into `kerbside/api/static/sfui` from a clean, up-to-date sfui checkout (never copy files by hand, never edit a vendored file); extend the `trailing-whitespace`/`end-of-file-fixer` excludes in `.pre-commit-config.yaml` to cover the sfui path and update the comment above them; add `kerbside/api/static/sfui/*` to `exclude` in `.vscode/review-scope.toml` and extend its header comment; update `docs/development.md`'s "Vendored web assets" section, the static-asset mentions in `AGENTS.md`, and the directory-structure comment in `ARCHITECTURE.md`. Do not touch any template — the UI must be unchanged by this phase. Mind the `.sfui-commit` dotfile when staging. All details, including the exact paths and line numbers, are in `docs/plans/PLAN-sfui-conversion-phase-02-vendoring.md`; read it first. |
| 2b   | medium | sonnet | none      | Prove the vendored assets ship in the built distribution: run `python -m build` in the kerbside worktree (output is gitignored) and report whether `kerbside/api/static/sfui/` — every file including `components/` and the `.sfui-commit` dotfile — appears in both the wheel and the sdist (`unzip -l dist/*.whl`, `tar tzf dist/*.tar.gz`). If anything is missing, diagnose and propose the narrowest `[tool.setuptools.package-data]` fix; do not add one if nothing is missing. Report the file list either way, then delete `dist/` and any `*.egg-info` the build left behind. Do not commit. |

Management-session review, beyond the master plan's standard
checklist:

* Run `tools/vendor.sh --check
  <kerbside-worktree>/kerbside/api/static/sfui` from the
  sfui checkout — it must exit zero.
* Confirm `.sfui-commit` is staged and its contents match
  `git -C <sfui> rev-parse HEAD`.
* Run `pre-commit run --all-files` and confirm it reports no
  modifications to anything under
  `kerbside/api/static/sfui/`.
* Confirm `git diff --stat` shows no template changes.

## Success criteria

* `kerbside/api/static/sfui/` contains the seven
  distributable files plus `components/` and
  `.sfui-commit`, all byte-identical to sfui at the recorded
  commit, and `tools/vendor.sh --check` exits zero.
* `pre-commit run --all-files` does not modify any vendored
  file, and the review-scope exclude keeps them out of the
  review queue.
* The built wheel and sdist contain the vendored directory.
* `tox -epy3` and `tox -eflake8` still pass, and the phase 1
  smoke tests are untouched and green — the UI is unchanged.
* `docs/development.md`, `AGENTS.md` and `ARCHITECTURE.md`
  describe the new directory and the never-edit-in-place
  rule; `README.md` is untouched.
* No template or Python source file changed.

## Outcome

Complete. `kerbside/api/static/sfui/` holds the seven
distributable files plus `components/` and `.sfui-commit`
recording `5949092`, vendored by sfui's `tools/vendor.sh`
from a clean checkout. `tools/vendor.sh --check` exits zero
and the stamp matches sfui HEAD, both verified in the
management session rather than taken from the sub-agent's
report. No template or Python file changed; `tox -epy3`
still passes 110 tests.

Packaging: proven, and no `[tool.setuptools.package-data]`
entry was needed. `python -m build` produced a wheel and an
sdist each containing all eight files, the `.sfui-commit`
dotfile and the nested `components/` directory included. The
check was meaningful rather than accidentally passing: the
directory exists only in the index at that point, not in
`HEAD`, so the setuptools_scm file finder demonstrably
enumerated the new content. Implicit inclusion is doing the
job and an explicit declaration would just be another thing
to keep in sync.

Two review corrections to the sub-agent's work:

* The vendored-sfui rule was moved out of AGENTS.md's "Key
  Files to Understand" table into "Common Pitfalls". The
  table is for files an agent reads to understand the
  system, and a never-edit-in-place rule is a trap to avoid;
  as a table row it also pushed `db.py` and `main.py` down
  the list it was second in.
* A repetitive sentence in `.vscode/review-scope.toml`'s
  header comment was tightened into the existing sentence
  about vendored JavaScript.

The plan's line-number citations for `AGENTS.md` (16 and 58)
were stale — that file has no static-asset mentions at all —
which the sub-agent correctly flagged rather than forcing an
edit at the cited lines. The `ARCHITECTURE.md` citation was
a few lines out but pointed at the right block.

## Back brief

Before executing this phase, back brief the operator on the
intended work and how it aligns with this plan and the
master plan.
