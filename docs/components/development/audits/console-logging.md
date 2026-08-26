# Audit: Console script logging setup

## What we check

Every file named as an entry point in `pyproject.toml` -- by
`[project.scripts]`, `[project.gui-scripts]` or
`[project.entry-points."console_scripts"]` -- that calls
`shakenfist_utilities.logs.setup_console()` must also:

* call `logging.basicConfig()`; and
* stop its own logger propagating into the root handler.

```python
LOG = logs.setup_console(__name__)
logging.basicConfig(level=logging.INFO)
logging.getLogger(__name__).propagate = False
```

The `propagate` assignment is matched against the entry point's *own*
logger -- the name bound to `setup_console()`'s return, or
`getLogger()` called with the same argument it was given. A line
silencing an unrelated third-party logger does not satisfy it, because
the entry point still emits every one of its own lines twice. Where a
file makes several `setup_console()` calls, the one given `__name__`
is the file configuring itself, and is the one that decides this.

All three matches are made against the file's *code*: comments and
string literals are blanked first. A commented-out
`logging.basicConfig()` is the state of anything somebody was
debugging and is exactly the misconfiguration this exists to catch, so
counting it as a call would pass the file for the defect it has. The
`audit-ok` marker is read from a complementary view in which string
bodies are blanked and comments survive, because a marker *is* a
comment: reading it from the whole file let a docstring that merely
mentioned the marker exempt the module that mentioned it.

`setup_console()` raises the root logger's level to INFO but attaches
its handler to the *named* logger only. Records from every other
module therefore propagate up to a root logger with no handler on it
and are dropped -- so without `basicConfig()` the entry point sees its
own INFO messages and nothing else. Once root does have a handler, the
entry point's own records reach both it and the handler
`setup_console()` installed, which is what `propagate = False`
prevents.

Only declared entry points are examined. occystrap calls
`logs.setup_console(__name__)` at the top of all 24 of its modules,
and only `occystrap/main.py` is an entry point; a call anywhere else
is a module getting a logger, not a console script setting up logging.
A repository that declares no console scripts, or whose entry points
do not use the helper, is not applicable -- this is a rule about how
the helper is used, not a requirement to use it.

An entry point is resolved to a file by trying `pkg/mod.py`,
`pkg/__init__.py` and both of those under `src/`. A repository whose
layout is none of those -- `lib/`, say -- is reported as having
declared entry points that did not resolve, naming them, rather than
as having declared none: the two are different facts, and the second
is a clean bill for a file nobody looked at. A declaration that is
malformed rather than merely unresolvable -- `scripts` given as a
string, a target that is not one -- is named the same way, and does
not stop the rest of the repository being audited.

An unresolved declaration is named in every outcome, not only when
nothing resolved at all. A repository with a mixed layout collected a
pass on the entry points that were found while the one that was not
went unmentioned, which is the same clean bill for a file nobody
opened. A pass is a statement about every entry point the repository
declares, so one that resolved to nothing withholds it: the ones that
did resolve are reported as compliant, but the criterion has not been
assessed.

A file that genuinely should not configure logging -- because
something else in the process already has -- carries an
`audit-ok: console-logging` comment, ideally with a reason. The marker
is read per file rather than per line: the finding is about the file's
logging setup as a whole, so there is no single line for it to sit on.

### Verbose handling

Part of the standard, and reviewed rather than measured. When a
`--verbose` or `--debug` flag is handled, the root handler level has
to move too, or raising `LOG`'s level alone changes nothing:

```python
if verbose:
    logging.root.setLevel(logging.DEBUG)
    for handler in logging.root.handlers:
        handler.setLevel(logging.DEBUG)
    LOG.setLevel(logging.DEBUG)
```

This is not checked because there is no reliable signal for "this
entry point has a verbosity flag" that does not also match parsers
that pass the value straight through. occystrap's `main.py` is the
worked example of the shape.

## Template

No template -- this is a code-level pattern.

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | non-compliant | shakenfist/agent-python#128 |
| client-python | non-compliant | shakenfist/client-python#371 |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#124 |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3909 |

Details for non-compliant projects:

- **agent-python** (Status): 1 of 1 console entry point(s) calling setup_console() do not configure the root logger -- shakenfist_agent/main.py: missing logging.basicConfig() (INFO from every other module reaches a root logger with no handler and is dropped); propagate = False on its own logger (its own lines are emitted twice once root has a handler)
- **client-python** (Status): 1 of 1 console entry point(s) calling setup_console() do not configure the root logger -- shakenfist_client/main.py: missing logging.basicConfig() (INFO from every other module reaches a root logger with no handler and is dropped); propagate = False on its own logger (its own lines are emitted twice once root has a handler)
- **occystrap** (Status): 1 of 1 console entry point(s) calling setup_console() do not configure the root logger -- occystrap/main.py: missing logging.basicConfig() (INFO from every other module reaches a root logger with no handler and is dropped); propagate = False on its own logger (its own lines are emitted twice once root has a handler)
- **shakenfist** (Status): 2 of 2 console entry point(s) calling setup_console() do not configure the root logger -- shakenfist/client/backup.py: missing logging.basicConfig() (INFO from every other module reaches a root logger with no handler and is dropped); propagate = False on its own logger (its own lines are emitted twice once root has a handler); shakenfist/client/ctl.py: missing logging.basicConfig() (INFO from every other module reaches a root logger with no handler and is dropped); propagate = False on its own logger (its own lines are emitted twice once root has a handler)
<!-- consistency-audit:end -->
