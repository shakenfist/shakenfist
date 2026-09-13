# Audit: npm imports satisfied only by a transitive dependency

## Who this applies to

Every project with a `package.json` and a readable `package-lock.json`
-- or `npm-shrinkwrap.json`, the same file under the name npm prefers
when a project has both -- whose lockfile resolves at least one package
the manifest does not declare, and which has JavaScript or TypeScript
source outside its build directories.

A workspace root is not applicable, for the reason
[npm-unused-declared-dependency.md](/components/development/audits/npm-unused-declared-dependency/)
gives: the dependencies of such a tree are spread across several
manifests and this criterion reads only the root one.

## What we check

Nothing the project imports is a package that only appears in
`package-lock.json`.

## Why

npm installs a flat tree. A package pulled in by a dependency of a
dependency is unpacked into `node_modules/` beside the ones the project
asked for, and importing it works exactly as well as importing one it
declared. It keeps working for as long as the intermediate package
continues to require it -- and on the day it stops, nothing in this
repository has changed and the build breaks anyway.

That is not a dependency. It is a coincidence, and the date it ends is
set by somebody else's dependency list.

The Python half of the fleet has the worked example, on
[undeclared-direct-dependency.md](/components/development/audits/undeclared-direct-dependency/):
`shakenfist` imported `oslo_concurrency` for years on an edge that
existed only because `shakenfist-utilities` declared a dependency it
never used. npm's flat tree makes that arrangement both easier to reach
and harder to see, because there is no generated block of pins to read
-- the whole resolved tree is in the lockfile, and it all looks the
same from an import statement.

The lockfile is where the coincidence is visible. Every package in it
that `package.json` does not declare is there because something
resolved to it, and the next `npm install` after that stops being true
drops it.

It is also the counterpart of
[npm-unused-declared-dependency.md](/components/development/audits/npm-unused-declared-dependency/):
there, "is this declared thing used?"; here, "is this used thing
declared?".

## The fix

Declare the package in `package.json`, at the version the lockfile
already resolves, and reinstall so the lockfile records it as a direct
dependency. There is deliberately no escape hatch. Unlike a dependency
that is installed but not imported, which has several legitimate
explanations, an import with no declaration has one fix and it is
always the same one.

## False positives, and why there are few

Four kinds of import are not packages, and all four appear in
`hunkydory` today:

* **Node builtins, in both spellings.** `node:fs` needs no table -- npm
  forbids a colon in a package name, so a specifier carrying a scheme
  is never a package. The bare spelling (`fs`, `path`) is matched
  against a list of builtin module names. That list is applied *after*
  the declared dependencies are subtracted, because npm really does
  carry packages called `path`, `process` and `events`: a project that
  declares one has declared it, and one that does not has imported the
  builtin.
* **Modules the host injects.** A VS Code extension imports `vscode`,
  which the extension host provides and which must never be declared --
  declaring it installs a placeholder package and breaks the build. The
  signal is `engines`: that field is the manifest saying which hosts
  the package runs inside, so a module named there is a module its host
  may provide.
* **Relative imports.** `./diff` and `../src/recount` are files, not
  packages, and neither are `#subpath` imports.
* **Anything already declared**, in any of the four dependency maps. A
  `peerDependencies` or `optionalDependencies` entry is a deliberate
  statement about a package even though it is not installed as a direct
  dependency.

Imports that resolve to *nothing* -- not declared, not in the lockfile
at all -- are deliberately not reported. That is a build failure rather
than a latent one, the compiler already says so, and the honest
candidates for it are a `paths` alias in `tsconfig.json` or a workspace
sibling, which are resolver configuration this scan does not read.

Comments are stripped before imports are read, and build output is not
read at all, with the same rules and for the same reasons as
[npm-unused-declared-dependency.md](/components/development/audits/npm-unused-declared-dependency/).
A commented-out import counting as a use would be a false *failure*
here rather than a false pass, which is why the scanner understands
string and regular expression literals well enough to know which `/`
starts a comment.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#npm-undeclared-direct-dependency).
