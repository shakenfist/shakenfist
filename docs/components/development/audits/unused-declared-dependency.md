# Audit: Dependencies declared but never imported

## Who this applies to

Every Python project in the fleet: anything with a `pyproject.toml`
whose Python is not incidental, and which has Python source outside its
build and environment directories.

Only `[project] dependencies` is read. An `optional-dependencies` group
is test and build tooling -- tox, stestr, coverage, flake8, mkdocs --
which is meant to be run rather than imported, so reading it would flag
all of it.

The block between `# START_OF_INDIRECT_DEPS` and
`# END_OF_INDIRECT_DEPS` is skipped. Those entries are transitive by
construction: `tools/pin-indirect-dependencies.sh` regenerates them
from what the direct dependencies resolve to, and asking whether the
project imports them is the wrong question about a line no human wrote.
See [pin-indirect-dependencies.md](/components/development/audits/pin-indirect-dependencies/).

## What we check

Every declared dependency is either imported somewhere in the
repository's Python, or annotated with a reason it is installed anyway.

## Why

A dependency nobody imports is not free, and it is not merely
untidy. It carries its own transitive closure into every install, and
each package in that closure is another stream of Renovate pull
requests -- reviewed, merged and CI-tested -- for a project that would
behave identically without any of it.

`library-utilities` is the worked example. It declares
`oslo.concurrency` in its `[project] dependencies` and imports it in no
file. Because every other Python project in the fleet depends on
`shakenfist-utilities`, that one line is what puts `oslo.concurrency`,
`oslo.config`, `oslo.i18n`, `oslo.utils`, `debtcollector`, `fasteners`,
`iso8601`, `netaddr`, `pyparsing`, `rfc3986`, `stevedore` and `wrapt`
into their installs: twelve packages, fifteen percent of shakenfist's
81-package dependency closure, and thirty-four of the two hundred and
forty-eight dependency bumps merged into shakenfist in the year to
September 2026.

Nothing else in the fleet's graph is close. The next largest node,
`requests`, brings three packages and is imported everywhere.

## Recording a dependency that is used without being imported

Plenty of dependencies are legitimately installed and never imported: a
command-line tool invoked as a subprocess, a database driver selected
by connection string, a gunicorn worker class named in a config file, a
transitive package pinned directly to hold a version floor. The
criterion cannot tell those from a dead line, so it asks for the reason
to be written down where the dependency is declared:

```toml
dependencies = [
    # not-imported: uv -- invoked as a subprocess by the image fetcher
    "uv>=0.8.0",
]
```

The marker is a comment anywhere inside the `[project] dependencies`
array; putting it on the line above the dependency keeps both within
the 120 character wrap. It names the distribution, and the reason after
`--` is required. An unexplained exception is indistinguishable from
silencing a finding, and the reason is the thing a future reader
actually needs: whether the dependency can go is a question about how
it is used, and by the time anyone asks, whoever knew has forgotten.

The annotation sits beside `# never-pin:`, which
`tools/pin-indirect-dependencies.sh` reads from the same array.

## How a dependency is matched to an import

The audit runs against checkouts it does not install, and the module a
wheel unpacks is recorded in the wheel rather than in its name. So the
import name is derived: the name itself, the name with `-` and `.`
turned into `_`, and the name with a `python` or `py` affix removed, so
that `PyYAML` finds `yaml` and `python-magic` finds `magic`.
`IMPORT_NAME_ALIASES` in `scripts/audit/checks/packaging.py` carries
the rest -- `protobuf` to `google`, `grpcio` to `grpc`, `mysqlclient`
to `MySQLdb`.

Deriving generously is deliberate. A spurious candidate can only make a
dependency look used, and this criterion files an issue when one looks
unused: a false pass costs a finding that the next sweep gets anyway,
while a false failure sends somebody to justify a dependency that was
never in question. When a new distribution's import name cannot be
derived, add it to the alias table rather than annotating every
consumer.

A subdirectory carrying its own `pyproject.toml` is not read: it is a
separate distribution declaring its own dependencies, so an import
there does not vouch for a declaration here. kerbside is the only
repository in the fleet shaped that way today, carrying a tempest
plugin and a Rust proxy alongside the package itself.

Comments and string literals are masked before imports are read. A
commented-out import is the precise shape this criterion exists to
find, so counting one as a use would report the deadest dependency in
the tree as the one still in use. `build/`, `dist/`, `cover/`, `.tox/`,
`.venv/` and `node_modules/` are not read: a copy of the source is what
makes a deleted import look alive, and shakenfist's `.tox` holds 23,286
Python files against the 502 it wrote.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#unused-declared-dependency).
