# Title for the plan

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read relevant
source files, understand existing patterns (object lifecycle, state
machines, database access via etcd/MariaDB, gRPC microservices,
daemon architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do not
speculate about the codebase when you could read it instead. Where
a question touches on external concepts (KVM/libvirt, VXLAN
networking, etcd, MariaDB/Galera, gRPC/protobuf), research as
needed to give a confident answer. Flag any uncertainty explicitly
rather than guessing.

Consult `ARCHITECTURE.md` for the system architecture overview,
object types, and daemon structure. Consult `CLAUDE.md` for build
commands, project conventions, database access patterns, and the
MariaDB migration approach. Consult `GOALS.md` for current
development priorities.

When we get to detailed planning, I prefer a separate plan file
per detailed phase. These separate files should be named for the
master plan, in the same directory as the master plan, and simply
have `-phase-NN-descriptive` appended before the `.md` file
extension. Tracking of these sub-phases should be done via a table
like this in this master plan under the Execution section:

```
| Phase | Plan | Status |
|-------|------|--------|
| 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
| 2. gRPC endpoints | PLAN-thing-phase-02-grpc.md | Not started |
| ...   | ...  | ...    |
```

I prefer one commit per logical change, and at minimum one commit
per phase. Do not batch unrelated changes into a single commit.
Each commit should be self-contained: it should build, pass tests,
and have a clear commit message explaining what changed and why.

## Situation

...

## Mission and problem statement

...

## Open questions

...

## Execution

...

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (flake8, stestr
  unit tests, and mypy type checking).
* New code follows existing patterns: object lifecycle in
  `baseobject.py`, MariaDB access via the three-layer pattern
  (direct/gRPC/public), Pydantic schemas in `shakenfist/schema/`.
* There are unit tests for core logic and preferably functional
  test coverage as well.
* Lines are wrapped at 120 characters, single quotes for strings,
  double quotes for docstrings.
* gRPC proto changes (if any) have been regenerated with
  `tox -e genprotos`.
* Documentation in `docs/` has been updated to describe any new
  features, commands, or database changes.
* `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` have been
  updated if the change adds or modifies modules, daemons, or
  object types.

### Future work

We should list obvious extensions, known issues, unrelated bugs
we encountered, and anything else we should one day do but have
chosen to defer to here so that we don't forget them.

...

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
