# Audit: npm dependencies declared but never used

## Who this applies to

Every project with a `package.json` at the root of the repository which
declares something in `dependencies` or `devDependencies`.

A workspace root -- a `package.json` carrying a `workspaces` field --
is not applicable. The dependencies of such a tree are spread across
several manifests, and this criterion reads only the root one, so it
would report a package the root declares on behalf of a member as
unused.

A project with no readable `package-lock.json` -- or
`npm-shrinkwrap.json`, the same file under the name npm prefers when a
project has both -- is not applicable
either. The lockfile is where the command names a package installs are
recorded -- it is how `typescript` is known to be what puts `tsc` on
the path -- so without it every tool invoked from a `scripts` entry
would look unused. A project in that state is already being told about
it by
[npm-pin-indirect-dependencies.md](/components/development/audits/npm-pin-indirect-dependencies/).

`peerDependencies` and `optionalDependencies` are not read. Both are
statements about what a *consumer* will provide, so "does this project
import it" is the wrong question to ask about them.

## What we check

Every package declared in `dependencies` or `devDependencies` is
imported, run, named in configuration, or annotated with a reason it is
installed anyway.

## Why

A dependency nobody uses is not free, and the npm ecosystem is where
that is least free of all. A package carries its own transitive closure
into every install, and for npm that closure is routinely measured in
hundreds rather than the dozen a Python distribution brings. Every
package in it is another Renovate pull request to review, another
integrity hash, and another supply chain to trust, for a project that
would behave identically without any of it.

[unused-declared-dependency.md](/components/development/audits/unused-declared-dependency/) has the
worked example from the Python half of the fleet: one unused
`oslo.concurrency` line in `library-utilities` put twelve packages into
every `shakenfist` install and accounted for fourteen percent of its
dependency bumps for a year.

Unlike the Python criterion, this one reads development dependencies as
well as runtime ones. The Python argument for leaving tooling alone is
that `optional-dependencies` holds things meant to be *run* rather than
imported, and flagging them would flag all of tox, stestr and flake8 at
once. npm's `devDependencies` holds the same kind of thing, but it is
installed by `npm ci` on every CI run and bumped by Renovate like
everything else -- and in a TypeScript project it is where dead weight
actually accumulates, because the build tooling is the part that gets
replaced. The answer is not to skip the section but to be generous
about what counts as use, which is the next part of this page.

## What counts as use

All of these, and the list is deliberately long:

* **An import.** `import`, `import type`, a side-effect `import 'x'`,
  `export ... from 'x'`, `require()` and a dynamic `import()` all
  count, from any `.ts`, `.tsx`, `.mts`, `.cts`, `.js`, `.jsx`, `.mjs`
  or `.cjs` file in the repository. A subpath import (`import 'x/y'`)
  counts as a use of `x`.
* **A command in `scripts`.** A package named in a `scripts` entry is
  being used by that script.
* **A binary a `scripts` entry runs.** `typescript` puts `tsc` on the
  path, and `"build": "tsc -p ."` never names the package. The command
  names a package installs are read out of the lockfile, so the
  mapping is measured rather than guessed from a table that would be
  right about `typescript` and wrong about the next tool anybody adds.
  A package installing a single command records it as a bare path, and
  npm names that command after the package rather than after the file.
* **A name in configuration.** Any dotfile, or any `.json`, `.js`,
  `.cjs`, `.mjs`, `.ts`, `.mts`, `.cts`, `.yaml`, `.yml` or `.toml`
  file, at the root of the repository or one level inside `.config/`,
  and any workflow under `.github/workflows/`. This is how an eslint
  config in an `extends` array, a postcss plugin keyed by name, and a
  tool a workflow invokes are all counted. `.config/` is read because
  it is the alternative home eslint documents for a flat config and
  other tools have followed it, and a config file that is not read is
  the only mention of the plugin it names -- which is exactly what
  this criterion would then report as unused. Nothing deeper is read,
  or the audit would start counting mentions in whatever a project
  keeps as test fixtures. The dependency maps in `package.json` itself
  are excluded before the manifest is read, for the obvious reason:
  they name every declared dependency.
* **Being a `@types/` package.** These are consumed by the TypeScript
  compiler on the strength of their name, and nothing ever imports
  them. `@types/vscode` describes an API the extension host injects,
  and the package it describes is not a dependency of the project at
  all.

Generosity is the deliberate choice, and it is the same one the Python
criterion makes about deriving import names. A spurious candidate can
only make a dependency look used, and this criterion files an issue
when one looks unused: a false pass costs a finding that the next sweep
gets anyway, while a false failure sends somebody to justify a
dependency that was never in question.

Comments do not count. A commented-out import is the precise shape this
criterion exists to find, so reading one as a use would report the
deadest dependency in the tree as the one still in use. Build output is
not read either -- `node_modules/`, `dist/`, `build/`, `out/`,
`coverage/` and whatever `tsconfig.json` names as its `outDir` -- because
a compiled copy of a source file says what the source said at the last
build, which is exactly what makes a deleted import look alive.

## Recording a dependency that is used without being named

package.json is JSON and cannot carry the `# not-imported:` comment the
Python criterion reads, so the annotation is a field. npm ignores
fields it does not know:

```json
"shakenfistAudit": {
  "notImported": {
    "autoprefixer": "named as a plugin in postcss.config.js"
  }
}
```

The reason is required, for the same reason it is required there: an
unexplained exception is indistinguishable from silencing a finding,
and the reason is the thing a future reader actually needs. Whether the
dependency can go is a question about how it is used, and by the time
anyone asks, whoever knew has forgotten.

Reach for it rarely. Everything in the list above is already counted,
so a package that needs an annotation is one that is genuinely invisible
-- loaded by a resolver from a string this scan cannot see.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#npm-unused-declared-dependency).
