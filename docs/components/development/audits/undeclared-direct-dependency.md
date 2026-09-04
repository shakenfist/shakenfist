# Audit: Imports satisfied only by a transitive pin

## Who this applies to

Python projects with a generated indirect dependency block -- the
entries between `# START_OF_INDIRECT_DEPS` and `# END_OF_INDIRECT_DEPS`
in `pyproject.toml`, maintained by
`tools/pin-indirect-dependencies.sh`. See
[pin-indirect-dependencies.md](/components/development/audits/pin-indirect-dependencies/) for who
has one and why.

A project without that block is not applicable. The question this
criterion asks is whether an import is resting on a pin nothing
declared, and without a generated block there is no such pin to rest
on.

## What we check

Nothing the project imports is declared only inside the generated
block.

## Why

A package a project imports but never declares resolves anyway, for
exactly as long as something else happens to require it. That is not a
dependency, it is a coincidence, and it ends on the day the
intermediate library drops the requirement.

This is not hypothetical. `shakenfist` imported `oslo_concurrency` in
its CI harness for years without declaring it, and the import worked
because `shakenfist-utilities` declared `oslo.concurrency` -- a
dependency `shakenfist-utilities` never used and has now removed. The
moment that removal reached a release, the reconciler would have
dropped the pin and the harness would have failed at import time, in a
repository that had changed nothing.

The generated block is where the coincidence is visible. Every name in
it is there because something resolved to it, and the reconciler
removes it as soon as that stops being true. So an import of a name in
that block is not a style problem. It is a breakage with a date on it,
and the date is set by somebody else's dependency list.

It is also the counterpart of
[unused-declared-dependency.md](/components/development/audits/unused-declared-dependency/), which
deliberately does not read the generated block: there, "is this
declared thing used?"; here, "is this used thing declared?".

## The fix

Declare the package above the `# START_OF_INDIRECT_DEPS` marker, with
the version it currently resolves to. The reconciler drops the
generated copy on its next run, the way `pbr` is already handled in
`shakenfist` -- it leaves out anything already declared above the
marker -- so the two never fight over the same name.

Do not remove the generated pin by hand and stop there. That block is
regenerated, so a hand edit to it survives exactly until the next
reconcile.

## False positives, and why there are few

A module that a *directly declared* distribution could equally have
provided is not evidence about the transitive one, and is skipped.
Namespace packages are the reason: `protobuf` and
`googleapis-common-protos` both install into `google`, so
`import google.protobuf` would otherwise report whichever of the two
happened to land in the generated block.

A subdirectory carrying its own `pyproject.toml` is not read either.
It is a separate distribution that happens to share a repository, and
it declares its own dependencies. kerbside is the worked example: its
`tempest-plugin/` imports `oslo_config` and declares `oslo.config` in
`tempest-plugin/pyproject.toml`, and reporting that as an undeclared
dependency of kerbside itself was a finding whose only honest remedy
was to ignore it. The root is never pruned -- only subdirectories are
tested -- so a checkout is always read against its own manifest.

Import names are derived from distribution names the same way as in
[unused-declared-dependency.md](/components/development/audits/unused-declared-dependency/), and
the same masking and directory rules apply -- comments and string
literals do not count as imports, and `build/`, `dist/`, `.tox/` and
`.venv/` are not read, along with the rest of the list that page gives
and explains.

There is deliberately no escape hatch. Unlike a dependency that is
installed but not imported, which has several legitimate explanations,
an import with no declaration has one fix and it is always the same
one.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#undeclared-direct-dependency).
