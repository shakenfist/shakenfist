# Title for the plan

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts
(KVM/libvirt, VXLAN networking, MariaDB/Galera, gRPC/protobuf),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/baseobject.py` (object lifecycle and state
machine), `shakenfist/mariadb.py` (three-layer database
access pattern), `shakenfist/schema/` (Pydantic models), and
`shakenfist/daemons/database/main.py` (gRPC database daemon).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via a table like this in this master plan under the
Execution section:

```
| Phase | Plan | Status |
|-------|------|--------|
| 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
| 2. gRPC endpoints | PLAN-thing-phase-02-grpc.md | Not started |
| ...   | ...  | ...    |
```

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

...

## Mission and problem statement

...

## Open questions

...

## Execution

...

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean and
avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the plan, at the recommended effort level
   and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's summary
   describes what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether
   the brief was insufficient (improve it) or the model was
   too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied with
   the result.

This applies to all steps, including high-effort ones. If a
sub-agent can't succeed even with a detailed brief and the
right model, that's a signal the brief needs improving, not
that the management session should do the implementation
itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental. The worktree is discarded if the
output is unsatisfactory. For safe, well-understood changes,
sub-agents can work directly in the main tree.

### Planning effort

The master plan itself should always be created at **high
effort** — it requires broad codebase understanding,
cross-referencing multiple source files, and making judgment
calls about scope and sequencing.

Each phase plan should specify the recommended effort level
for planning that phase. Phases involving schema design,
cross-daemon coordination, protocol changes, or subtle
correctness questions (locking, consistency, migration
safety) should be planned at high effort. Phases that are
mechanical or follow well-established patterns (adding a new
MariaDB accessor that mirrors an existing one, for example)
can be planned at medium effort.

### Step-level guidance

Each phase plan should include a table like this:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**
- **high** — Requires reading multiple files, making judgment
  calls, understanding non-obvious invariants, or researching
  external references. The sub-agent needs to think carefully
  about edge cases. Typical examples: new object type
  lifecycle, cross-daemon protocol changes, migration logic.
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief. May need to read a few
  files but the approach is well-defined. Typical examples:
  adding a new gRPC endpoint parallel to an existing one,
  adding a new MariaDB accessor.
- **low** — Purely mechanical changes (rename, reformat, add
  a log line, regenerate proto stubs). The brief is a
  complete instruction.

**Model choice:** The planner should recommend which model is
best suited for each step. This is a judgment call, not a
rigid rule — the right model depends on what the step
requires, not on whether it's "planning" or "implementation".

- **opus** — Best for steps that require deep reasoning,
  cross-daemon architectural understanding, subtle
  correctness judgment (locking, state machines, migration),
  or complex protocol research. Also appropriate for
  intricate implementation where getting it wrong would be
  costly to debug.
- **sonnet** — Good default for well-briefed implementation
  work. Faster and cheaper than opus. Works well when the
  plan front-loads the research and the brief is detailed
  enough that the agent doesn't need to make broad judgment
  calls.
- **haiku** — Suitable for purely mechanical tasks:
  search-and-replace, regenerating proto stubs, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

The model choice interacts with effort level and brief
quality. A detailed brief compensates for a lighter model —
sonnet at medium effort with a thorough brief often matches
opus at medium effort with a vague brief. The planner's job
is to write briefs good enough that the recommended model
can succeed.

Note: the model also determines the context window (opus has
1M tokens, sonnet and haiku have 200K). Steps that require
holding many files in context simultaneously — especially
sweeping changes through `mariadb.py` or multi-daemon
coordination — may need opus for that reason alone, even if
the reasoning itself is straightforward.

**When in doubt, skew to the more capable model.** Saving
money only matters if the outcome is still acceptable. A
failed or low-quality implementation wastes more time (and
therefore more money) than using a heavier model would have
cost. Only recommend a lighter model when you are confident
the brief is detailed enough for it to succeed.

**Brief for sub-agent:** This is the key field. Write it as
if briefing a colleague who has never seen the codebase.
Include: what to change, which files to touch, what patterns
to follow, and any non-obvious constraints. The better the
brief, the lower the effort level needed and the lighter the
model that can succeed.

A good brief front-loads the research the planner already
did, so the implementing agent doesn't repeat it. For
example, instead of "add MariaDB functions for the new
object", write "add direct, gRPC, and public wrappers for
`get_widget` in `shakenfist/mariadb.py`, mirroring the
`get_artifact` trio at lines 11129/11797/11811. The direct
path uses `_get_widgets_table()`; the gRPC wrapper goes
through `GetWidget` on the database daemon; register the
counter in the Monitor operations list in
`shakenfist/daemons/database/main.py`."

### Management session review checklist

After a sub-agent completes, the management session should
verify:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code passes `pre-commit run --all-files` (flake8,
      stestr unit tests, mypy).
- [ ] If proto files changed, stubs were regenerated with
      `tox -e genprotos` and committed.
- [ ] The changes match the intent of the brief — not just
      syntactically correct but semantically right.
- [ ] Commit message follows project conventions (including
      the Co-Authored-By line with model, context window,
      effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (flake8,
  stestr unit tests, and mypy type checking).
* New code follows existing patterns: object lifecycle in
  `baseobject.py`, MariaDB access via the three-layer pattern
  (direct/gRPC/public), Pydantic schemas in
  `shakenfist/schema/`.
* Object or attribute filtering is pushed down to the
  MariaDB SQL layer where indexes can make it faster, rather
  than materialising everything and filtering in Python.
* There are unit tests for core logic and preferably
  functional test coverage as well (see
  `shakenfist/deploy/cluster_ci`).
* Lines are wrapped at 120 characters, single quotes for
  strings, double quotes for docstrings.
* gRPC proto changes (if any) have been regenerated with
  `tox -e genprotos`.
* Documentation in `docs/` has been updated to describe any
  new features, commands, or database changes. In particular
  `docs/operator_guide/database.md` has been updated for
  schema changes.
* `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` have been
  updated if the change adds or modifies modules, daemons,
  or object types.

### Future work

We should list obvious extensions, known issues, unrelated
bugs we encountered, and anything else we should one day do
but have chosen to defer to here so that we don't forget
them.

...

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add a row to the *Plan Status* table
  with a link to the plan, its phase breakdown, initial
  status, and a one-line description. Keep entries grouped
  by master plan.
* **`order.yml`** — add an entry for the new master plan so
  it appears in the documentation navigation in the
  intended order. Phase files should *not* be added to
  `order.yml`; they are linked from the master plan's
  Execution table and from `index.md` only.

The site navigation in `mkdocs.yml` is produced from
`mkdocs.yml.tmpl` by the docs-sync workflow, which consumes
`order.yml`. You do not need to edit `mkdocs.yml` by hand.

When all phases of a plan are complete, update the status
column in `docs/plans/index.md`.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
