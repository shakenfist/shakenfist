# Audit: Release artifacts exclude docs/plans

## What we check

`docs/plans/` is working history: the plans, their phases, and the
audit notes and diffs filed under them. It stays in git, where somebody
asking why the code is shaped the way it is can find it, but it is not
source, and nobody who installs a release needs it. Every packaging
tool the fleet releases with defaults to taking what git tracks (or
everything git does not ignore) from the directory its manifest sits
in, so a manifest at the repository root ships `docs/plans/` unless it
is told not to.

This was found when client-python-k3s's sdist size gate tripped in
October 2026: the sdist was 2.3MB, and 0.9MB of it was plans. Pruning
`docs/plans` took it to 1.4MB without changing a line of what it
builds. client-python, divergulent, kerbside, library-utilities,
occystrap and shakenfist were shipping their plans the same way.

For a repository that tracks files under `docs/plans/`, each release
artifact built from the repository root (or from `docs/`) must exclude
that directory with its own mechanism:

| Artifact | Built from | Passes when |
|----------|------------|-------------|
| sdist, setuptools with a git file finder (setuptools_scm, setuptools-git, pbr) | `pyproject.toml` or `setup.py` | `MANIFEST.in` has `prune docs/plans` (or `prune docs`), not undone by a later `graft`, `include` or `recursive-include` that adds a plan back |
| sdist, plain setuptools | `pyproject.toml` or `setup.py` | `MANIFEST.in` does not add `docs/plans` back in |
| sdist, hatchling | `pyproject.toml` | `[tool.hatch.build.targets.sdist]` `exclude` covers `docs/plans` (it wins over an include list), or an `include`/`only-include` list does not select it |
| crate | `Cargo.toml` with a `[package]` that is published | `exclude` covers `docs/plans`, or an `include` list does not select it, either set directly or inherited from `[workspace.package]` |
| npm package | `package.json` that is not `private` | a `files` list does not select `docs/plans`, or `.npmignore` covers it |
| Ansible collection | `galaxy.yml` | a `build_ignore` entry matches `docs/plans` or `docs` as a path from the collection root, or the `manifest` directives prune it |

Wheels are not checked: they carry the import package, not the
repository. A crate published with `publish = false`, a virtual Cargo
workspace, a private npm package, and any manifest in a subdirectory
(client-python-k3s's `collection/`, ryll's crates) do not apply, since
none of them can reach `docs/plans/`.

A `pyproject.toml` whose build backend is none of the above is reported
rather than passed: a check that cannot measure an artifact must not
claim it is clean. The fix there is to confirm by building the sdist,
and to teach the check about the backend.

## What this does not cover

Where a pattern names files rather than a whole directory, the check
judges it against the files git tracks under `docs/plans/`, so an
include of `*.txt` ships plans only if a `.txt` note is tracked there.
A pattern rooted inside `docs/plans` -- `docs/plans/README.md`,
`recursive-include docs/plans *.png` -- ships plans whether or not a
file matching it is tracked yet, since anything that lands there later
will be one.

Exclusions are judged the other way: one only counts when it covers the
whole of `docs/plans`, so that the next plan, or its audit notes, is
excluded too. That means a pattern naming `docs/plans`, a directory
above it, or everything inside one (`docs/plans/*`, `docs/**`), in
`.npmignore`, Cargo and hatch `exclude`, and galaxy `build_ignore`
alike. An exclusion of one file type or name, such as `*.md` or
`docs/plans/PLAN-*`, is not credited, even when every plan tracked
today matches it.

Gitignore-shaped lists (npm `files` and `.npmignore`, Cargo and hatch
`include` and `exclude`) are matched the gitignore way: a leading `/`
anchors a pattern, a trailing `/` limits it to directories, and `*`
stays within one path segment, so `docs/*.md` names only the Markdown
files directly in `docs/`. An npm `files` entry is matched at any
depth, which can only report more than npm ships, not less. The tools
disagree about `!` lines: npm, like git, cannot re-include a file below
an excluded directory, while Cargo and hatch let a pattern matching a
file beat one matching its directory, whatever their order. So an
ignore list with a `!` line naming `docs/plans`, a directory above it,
or anything inside it is not credited, wherever that line sits; drop
the negation, or move what it protects out of `docs/plans`. In an
include list, a `!` line takes the plans back out only when it selects
all of `docs/plans`, comes after every entry reaching it, and names it
at least as closely: `"docs/", "!docs/plans"` passes, and
`"docs/plans/*.md", "!docs/plans"` does not, since Cargo ships the
plans from it. That rule is Cargo's and hatch's, applied to npm too, so
a `**` or bare `*` entry reaches inside the plans and no `!` line takes
them back: `"docs/**", "!docs/plans"` and `"*", "!docs"` are reported
even though npm, which applies `files` in order, would probably leave
the plans out. List what ships instead (`"docs/"` rather than
`"docs/**"`).

galaxy's `build_ignore` is not gitignore-shaped: ansible-galaxy
fnmatches it against each path relative to the collection root, and
the check matches it the same way, so `*` crosses `/` there, and
`plans`, `/docs/plans` and `docs/plans/` exclude nothing, and do not
pass.

`MANIFEST.in` (and a galaxy `manifest`) is evaluated with setuptools'
own globbing. Directory arguments are globs; `include` and `graft` glob
without recursion, so `include docs/**` takes only the files directly
in `docs/`, while `recursive-include` and `global-include` read a `**`
path segment as any number of directories. A path starting with `/` is
absolute and names nothing, and a `prune .` or `recursive-exclude . *`
does nothing, because setuptools matches the `.` literally. The only
exclusions credited are `prune` and `recursive-exclude <dir> *` of
`docs/plans` or a directory above it: an `exclude` or `global-exclude`
that would remove the plans one file type or name at a time is not.
Use `prune docs/plans`:

```
graft docs
exclude docs/plans/*.md    # fails: removes today's plans, not the directory
```

```
graft docs
prune docs/plans           # passes
```

A galaxy `manifest` that keeps the default
directives is treated as shipping the plans, since those defaults take
`.txt`, `.json` and `.yml` files from `docs/`; add `prune docs/plans`
to its directives. The `galaxy.yml` reader handles block and one-line
flow lists, but not a flow-style `manifest: {...}` mapping, which is
reported as unreadable rather than guessed at.

Docker images are not checked. The build context is chosen by the
command that builds the image rather than by a file in the repository,
so there is no manifest to read; a `COPY . .` from the repository root
would ship plans, and a `.dockerignore` entry is the fix.

Release tarballs assembled by hand in a workflow (instar's staging
directory, ryll's binary tarball) list their contents explicitly, and
are not checked.

## Template

No template. For the common case, a setuptools_scm (or pbr) project,
add this to `MANIFEST.in` at the repository root:

```
prune docs/plans
```

and confirm with `python3 -m build --sdist` and
`tar tzf dist/*.tar.gz | grep docs/plans` printing nothing.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#release-artifacts-exclude-plans).
