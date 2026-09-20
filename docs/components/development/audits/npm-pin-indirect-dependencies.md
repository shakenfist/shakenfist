# Audit: Pinning indirect npm dependencies

## Who this applies to

Every project with a `package.json` at the root of the repository, and
which declares at least one dependency in it. Everything else is not
applicable, which today is every repository in the fleet except
`hunkydory`.

A project whose lockfile is `yarn.lock`, `pnpm-lock.yaml` or
`bun.lockb` rather than an npm one is reported not applicable with
that reason. Those files pin a tree too, and reading them is work
nobody in the fleet needs yet; the criterion says so rather than
failing a project for using a package manager it does not parse.

`npm-shrinkwrap.json` is not one of those. It is npm's own format --
the same schema as `package-lock.json`, installed exactly by `npm ci`
-- and it is the publishable spelling, which npm reads in preference
when a project has both. A project pinning its tree that way is doing
what this criterion asks, so it is read as the lockfile rather than
reported as a package manager we do not parse.

## What we check

* `package-lock.json`, or `npm-shrinkwrap.json`, exists.
* It is committed. A lockfile that only ever existed in the working
  copy that generated it pins nothing for anybody else.
* Its `lockfileVersion` is 2 or later.
* No workflow installs the project's dependencies with `npm install`,
  or either of its aliases, `npm i` and `npm add`.

## Why this is shaped differently to the Python criterion

[pin-indirect-dependencies.md](/components/development/audits/pin-indirect-dependencies/) asks a
Python project to run a reconciler, keep a generated block of
transitive pins in `pyproject.toml`, and open a pull request when that
block changes. None of that is needed here, because npm already does
it.

`package-lock.json` records every package in the transitive closure
with the exact version it resolved to and an integrity hash for the
artefact, and `npm ci` installs precisely that tree -- failing rather
than resolving if the lockfile and the manifest disagree. The pinning
story is satisfied by construction, so asking for a reconciler on top
of it would be inventing a rule npm does not have.

What is *not* satisfied by construction is that the mechanism is
present and honoured. That is what the four points above measure, and
each of them is a way a project can have a lockfile and still install
a tree nobody chose.

**A lockfile that was never committed.** `.gitignore` entries for
`package-lock.json` are common advice for libraries, on the reasoning
that a consumer resolves their own tree. That reasoning does not reach
CI, which resolves a fresh tree on every run and therefore tests a
different set of packages to the one the last run tested.

**lockfileVersion 1.** Written by npm 6 and before. It predates the
`packages` map, and records the tree in a nested form that npm 7 and
later rewrite on first contact. A v1 file in the tree is a lockfile
that the next `npm install` will replace, so the pins in it are a
snapshot rather than a contract.

**`npm install` in CI.** This is the one that undoes the rest. `npm
ci` installs the lockfile and refuses to modify it; `npm install`
resolves the ranges in `package.json` afresh, writes any newer
versions it finds back into the lockfile, and carries on. A workflow
doing that tests a tree that nothing pinned and nobody reviewed, which
is the exact failure this criterion exists to prevent, reached from
the other direction.

`npm i` and `npm add` are the same command under npm's own aliases,
and are read as `npm install` wherever it is read. Spelling the
resolving install differently does not make it a different install,
and a criterion that only knew the long form would be evaded by a
habit rather than by a decision.

`npm install -g` is not that. A global install puts a tool on the
runner's path beside the project -- the way several workflows in the
fleet install the Claude Code CLI -- and touches no lockfile, so it is
not reported.

Neither is `npm install --package-lock-only`, which resolves and
rewrites the lockfile without installing anything. That is how a
lockfile is deliberately refreshed, and nothing is tested against the
tree it produces until the resulting lockfile is reviewed and merged.

Neither, finally, is the phrase appearing somewhere that is not a
command. Only the body of a `run:` is read, and the command has to
start there or follow a `|`, `&&` or `;`. A step called "never use npm
install here", an `echo` saying the same thing, and a comment at the
end of a `npm ci` line are a workflow describing the mistake rather
than making it, and failing a compliant repository for saying so is
the expensive direction to be wrong in.

## Workspace roots

Unlike its two siblings --
[npm-unused-declared-dependency.md](/components/development/audits/npm-unused-declared-dependency/)
and
[npm-undeclared-direct-dependency.md](/components/development/audits/npm-undeclared-direct-dependency/),
which report a workspace root not applicable because the dependencies
of such a tree are spread across several manifests -- this criterion
applies to one. A workspace root has exactly one lockfile, at the
root, pinning the whole tree, and a workflow that installs with `npm
install` undoes that pinning for every workspace at once. The
questions this criterion asks are all answerable from the root, so it
asks them.

## What we deliberately do not check

Whether the lockfile is *in step* with `package.json`. Answering that
means resolving the declared ranges, which means reaching the registry,
and the audit reads checkouts offline. `npm ci` already fails loudly on
exactly that condition, so the answer arrives from CI on the next run
rather than from here.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#npm-pin-indirect-dependencies).
