Thanks for your work on this. I appreciate it. Some final
checks before I push.

## How to use this template

The pre-push audit splits into two waves:

**Wave 1 — mechanical.** Build verification, lint, unit and
functional test suites, and the parts of style conformance
that grep can answer. Always run wave 1 first; wave 2 is
only worth spending on if wave 1 passes.

**Wave 2 — judgment.** Code-quality, test-coverage,
documentation, and security review. Some of this is
mechanical (TODO/FIXME grep, dead-code detection, new
dependencies) and the rest needs sub-agents to read code
and apply judgment. The four judgment agents are
independent and can be spawned in parallel.

The management session reviews all findings, fixes any
issues, and confirms the push.

## Wave 1: Mechanical checks

Run the following, stopping on the first failure:

```
pre-commit run --all-files
tox
```

`pre-commit` runs flake8, stestr unit tests, and mypy. `tox`
runs the full test matrix including any additional envs
configured (e.g. `genprotos`). If proto files are in the
diff, also confirm the generated stubs are up to date:

```
tox -e genprotos
git diff --exit-code shakenfist/protos
```

Then a few grep-level style checks on the diff against
`develop`:

```
git diff develop...HEAD -- '*.py' | grep -nE '^\+[^+].{120,}'  # lines > 120 chars
git diff develop...HEAD -- '*.py' | grep -nE '^\+[^+].*\bprint\('  # stray print()s in new code
git diff develop...HEAD -- '*.py' | grep -nE '^\+[^+].*\betcd\b'  # new etcd references
git diff develop...HEAD -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'  # new bulk-scan pushdown violations
```

Exit condition: wave 1 passes when pre-commit, tox, proto
regeneration (if applicable), and the style greps all come
back clean. If anything fails, fix the cause and re-run
before spending on wave 2.

### Style conformance — judgment portion

The commands above cover what grep can prove. The remaining
style questions need a sub-agent to read code:

| Setting | Value |
|---------|-------|
| Model | sonnet |
| Effort | low |

**Brief for sub-agent (only if wave 1 passes):**

Check `git diff develop...HEAD` for adherence to project
conventions in `CLAUDE.md` and `AGENTS.md`:

- Python conventions: import ordering, logging via the
  `shakenfist_utilities.logs` pattern, single quotes for
  strings and double quotes for docstrings, 120-char lines.
- Object lifecycle conventions: state machine transitions
  via `state_targets`, `hard_delete()` cleanup, event
  logging via `shakenfist.eventlog.add_event` with the
  correct `EVENT_TYPE_*` constant.
- Database access conventions: every new MariaDB function
  should follow the three-layer pattern — `_direct_*`,
  `_grpc_*`, and a public wrapper that chooses between
  them via `_use_database_service()`. Direct access from
  non-database-daemon code paths is a bug.
- SQL-pushdown discipline (blocking): does any new code
  materialise a full object list with `get_all_*()` and then
  filter in Python where a `WHERE` clause on an indexed
  column could have done the work at the database? New
  `mariadb.get_all_*(` call sites must either route through
  a `find_*` primitive or carry a `# nopushdown: <reason>`
  trailing comment. See
  [docs/operator_guide/database.md](docs/operator_guide/database.md).
- gRPC conventions: proto edits in
  `shakenfist/protos/database.proto`; counter registered
  in the Monitor operations list in
  `shakenfist/daemons/database/main.py`; stubs regenerated
  with `tox -e genprotos`.
- Field rename / unit-change discipline: did any field
  silently change units (e.g. seconds → ms, bytes → KiB)
  without a rename or doc comment?

Report a short list of any violations found. If none, say
"Style checks passed."

## Wave 2: Deeper review

Only run wave 2 after wave 1 passes.

Start with the mechanical sweep on the diff:

```
# TODO / FIXME / HACK / XXX in changed files
git diff develop...HEAD -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'

# New `# noqa`, `# type: ignore`, or `pragma: no cover`
git diff develop...HEAD -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'

# New test functions vs files changed (sanity ratio)
git diff develop...HEAD --stat | tail -1
git diff develop...HEAD -- '*.py' | grep -cE '^\+\s*def test_'

# Documentation files touched (warns if none — the diff may have merited doc updates)
git diff develop...HEAD --name-only -- 'docs/*' '*.md'

# New direct subprocess / shell calls — might need sanitisation review
git diff develop...HEAD -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'
```

These report only — they do not block. Treat the output as
input to the judgment agents below.

Then spawn the judgment agents. They are independent and
can run in parallel.

### 2a. Code quality

| Setting | Value |
|---------|-------|
| Model | sonnet |
| Effort | medium |

**Brief for sub-agent:**

The mechanical sweep has already extracted TODO/FIXME
comments, new `# noqa` / `# type: ignore`, and subprocess
calls. Take that report as input.

Add the judgment-level review on the diff
(`git diff develop...HEAD`):

- **Duplicated code:** Are there significant blocks of
  duplicated logic that the mechanical scan can't see?
  Look for copy-paste patterns across MariaDB accessors,
  REST handlers, or daemon run loops.
- **Missed abstractions:** Should any new code be extracted
  into a shared module? Look for logic a second object type
  or daemon would likely need.
- **SQL pushdown (blocking):** Any new `mariadb.get_all_*(`
  call that is not on an existing line and not tagged with
  a `# nopushdown: <reason>` trailing comment must be
  rewritten as a `find_*` call (or as a scoped helper with
  explicit justification). The SQL-pushdown rule is
  blocking, not advisory — the wave-1 mechanical grep
  flags the diff-level occurrences; this brief covers the
  judgment-level cases the grep misses (for example,
  callers that reach through a helper function that itself
  scans). See
  [docs/operator_guide/database.md](docs/operator_guide/database.md)
  for when each entry point applies.
- **Cached FK list pattern (blocking):** Any new
  `list[str]` / `list[UUID4]` field on a
  `shakenfist/schema/*_attributes.py` model — and any new
  `add_*` / `remove_*` mutator pair on the owning object
  that appends to or removes from it — should prompt the
  reviewer to ask: *is this a list of child-object UUIDs
  that a `WHERE <fk> = ?` query against the child table
  could provide live?* If yes, the property must be
  query-backed and the cache column must not exist. Phase
  7 of the SQL-pushdown plan removed two such
  caches (`network_attributes.networkinterfaces`,
  `instance_attributes.interfaces`); legitimate non-FK
  list fields (e.g. `node_attributes.daemons`,
  `namespace_attributes.trust`) are not flagged. See
  [docs/plans/PLAN-sql-pushdown-filtering-phase-07-denorm-lists.md](docs/plans/PLAN-sql-pushdown-filtering-phase-07-denorm-lists.md).
- **Three-layer pattern:** Does every new MariaDB function
  have the direct/gRPC/public trio, with the corresponding
  counter registered and proto definition (if needed)?
- **Triage the mechanical findings:** for each
  TODO / noqa / type:ignore the sweep flagged, say
  blocking or advisory and why. Skip ones inside test
  modules unless they disable coverage on production code.

<!-- shared-block: comment-proportion v1 -->
Comment proportion (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/comment-proportion.md`):

- A comment or docstring earns its length by saying what the code
  cannot: the contract, the units, the failure modes, the reason a
  surprising choice is correct. Restating the code in prose is not
  documentation.
- Treat as candidates any added comment or docstring that is longer
  than the code it documents, and any comment block over roughly
  fifteen lines attached to a body under ten. These are candidates,
  not verdicts -- a subtle algorithm, a public API contract, or a
  hard-won bug explanation can justify the length.
- Where the length is not justified the finding is advisory, and
  the fix is to cut the restatement rather than delete the comment:
  keep the why, drop the line-by-line narration of the what.
- Prose that documents user-visible behaviour rather than the
  implementation usually belongs in `docs/`, with the comment
  reduced to a pointer.
<!-- shared-block-end -->

Report findings as a bullet list. For each finding, state
the file, line, and whether it's blocking (must fix before
push) or advisory (can address later).

### 2b. Test review

| Setting | Value |
|---------|-------|
| Model | sonnet |
| Effort | medium |

**Brief for sub-agent:**

Review the diff (`git diff develop...HEAD`) for test
coverage:

- Does every new public function or significant code path
  have unit test coverage?
- For anything behavioural (lifecycle transitions, cluster
  operations, REST endpoints), is there functional coverage
  in `shakenfist/deploy/shakenfist_ci/cluster_ci_tests`? Shakenfist prefers
  functional tests to unit tests when we can only have
  one.
- Do the tests include adversarial cases (malformed input,
  empty namespaces, duplicate UUIDs, state transitions from
  terminal states, concurrent deletion)?
- Are there any assertions that test implementation details
  rather than behaviour (fragile tests that will break on
  refactors)?
- Are there any new modules or functions with zero test
  coverage that should have at least basic tests?

Also verify:
- All existing tests still pass (wave 1 already confirmed
  this, so just check the wave 1 result).
- If the change touches instance lifecycle, networking, or
  the database daemon, note which `cluster_ci` tests in
  particular would exercise the new behaviour and whether
  they should be run before push.

Report findings as a bullet list grouped by file.

### 2c. Documentation review

| Setting | Value |
|---------|-------|
| Model | sonnet |
| Effort | medium |

**Brief for sub-agent:**

Check that documentation matches the current code state.
Read the diff (`git diff develop...HEAD`) and verify:

<!-- shared-block: readme-discipline v1 -->
README discipline (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/readme-discipline.md`):

- New user-visible features are documented in `docs/` (and
  `ARCHITECTURE.md` / `AGENTS.md` where appropriate), not by
  adding bullets to `README.md`.
- `README.md` is a pitch: what the project is, who it is for,
  minimal installation instructions, a small number of usage
  examples, and curated absolute links into `docs/`. It only
  changes when the pitch, the install story, or the
  documentation links change.
- README growth is itself a finding: if the diff adds README
  content that belongs in `docs/`, flag it as blocking and
  move it.
<!-- shared-block-end -->

<!-- shared-block: llm-doc-discipline v1 -->
AGENTS.md and ARCHITECTURE.md discipline (shared block; do not
edit -- the canonical copy lives in shakenfist/development at
`templates/shared-blocks/llm-doc-discipline.md`):

- `AGENTS.md` is a working guide: the conventions, invariants and
  gotchas an agent cannot infer by reading the code, plus curated
  links into `docs/`. It is loaded into every session, so every
  line costs context on every task.
- `ARCHITECTURE.md` is a map: the component inventory, how data
  moves between components, and why the shape is the way it is.
  A deep dive on one subsystem belongs in `docs/`, where humans
  benefit from it too.
- One canonical home per fact. If `docs/` covers it, link to it
  instead of restating it -- and the same rule applies between
  `AGENTS.md` and `ARCHITECTURE.md`.
- Neither file is a reference manual, a runbook, or a changelog.
  CLI flags, configuration keys, wire protocols, step-by-step
  procedures and plan history go to `docs/`.
- Growth in either file is itself a finding: if the diff adds
  content that belongs in `docs/`, flag it as blocking and move
  it.
<!-- shared-block-end -->

- In this project, the structural changes that reach
  `ARCHITECTURE.md` are new or modified modules, daemons,
  object types and gRPC services; the conventions that reach
  `AGENTS.md` are new dependencies, build commands and
  invariants.
- `docs/` content is in sync — in particular,
  `docs/operator_guide/database.md` for schema changes,
  `docs/operator_guide/` for new operator-visible
  behaviour, and `docs/developer_guide/` for new internal
  patterns.
- State machine docs match the code: if the diff changes any
  object's `state_targets` map (a state added or removed, or a
  transition added or removed), verify
  `docs/developer_guide/state_machine.md` still matches. Each
  object's state list and its mermaid diagram must reflect
  exactly the states and transitions in the corresponding
  `state_targets` dict — including the creation transition
  (the `None` key) and any recovery edges. This audit is
  worth a quick pass even when the diff does not touch
  `state_targets`, as the diagrams have drifted from the maps
  before.

<!-- shared-block: plan-phase-references v1 -->
Plan phase references (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-phase-references.md`):

- Documentation outside plans directories describes the current
  state of the software, not the history of how it was built. Do
  not write "implemented in phase 5" or "since phase 3 of the
  two-tier CI plan": a reader wants to know whether a feature
  exists, not which phase of which plan delivered it.
- If a documented behaviour is implemented, describe it plainly.
  If it is planned but not yet implemented, link to the master
  plan in `docs/plans/` instead of citing a phase number.
- Reserve the word "phase" for plan documents. A procedural
  document describing a live multi-stage process (a release
  runbook, say) should call its stages "steps" or "stages", so
  that a phase reference in `docs/` is always a plan smell.
- The consistency audit greps `README.md` and `docs/` (excluding
  plans directories) for "phase <number>". Append
  `<!-- audit-ok: phase-reference -->` to a line only when the
  reference is genuinely not about an implementation plan.
<!-- shared-block-end -->

- Plan files in `docs/plans/` are up to date — completed
  phases marked complete, deferred items listed, and the
  *Plan Status* table in `docs/plans/index.md` reflects
  reality.
- If database schema changed, verify there is migration
  guidance (either in-tree migration code or a documented
  upgrade path).

Report findings as a bullet list. "No documentation gaps
found" is a valid answer.

### 2d. Security review

| Setting | Value |
|---------|-------|
| Model | opus |
| Effort | high |

**Brief for sub-agent:**

Security review of the diff (`git diff develop...HEAD`).
This requires careful judgment — read the actual code, not
just the diff summary.

Check for:

- **Authentication and authorisation:** Does every new
  REST endpoint and gRPC method enforce JWT validation
  and namespace authorisation? Is the
  `@external_api.base.requires_namespace*` decorator (or
  equivalent) applied? Can a caller in namespace A read or
  mutate objects owned by namespace B?
- **Input validation:** Are API inputs (namespace names,
  instance metadata, source URLs, artifact names) validated
  before use? Could malformed input cause unhandled
  exceptions or bypass checks? Watch for `.get()` with
  implicit default where a required field is expected.
- **SQL injection:** All MariaDB access should go through
  SQLAlchemy parameterised queries. Any f-string or
  string-concatenated SQL is a finding. Any use of
  `text()` with interpolated user input is a finding.
- **Shell and subprocess safety:** Are user-controlled
  values passed to subprocess calls, `os.system`, or shell
  templates without sanitisation? Any new `shell=True` is
  a finding unless the command string is a constant.
- **Credential and secret handling:** Are passwords, JWT
  secrets, or database credentials logged, persisted to
  events, or returned in API responses? The event log is
  particularly dangerous because it is broadly readable.
- **Resource exhaustion:** Could a malicious caller cause
  unbounded memory growth (e.g. via a very large artifact
  list), file descriptor leaks, or daemon CPU spin? Look
  for unpaginated `get_all_*` calls in hot paths and
  missing timeouts on external operations.
- **Concurrency:** Are there new shared-state patterns or
  lock acquisitions? Could they deadlock with existing
  locks? Is lock ordering documented or obvious?

Report findings with severity (critical / high / medium /
low / informational). For each finding, state the file,
line, the vulnerability class, and a recommended fix.

## Management session checklist

After all agents complete, the management session should:

- [ ] Wave 1 passed (pre-commit, tox, proto stubs fresh,
      style greps clean).
- [ ] Wave 2 findings reviewed.
- [ ] Any blocking findings from 2a/2b/2c have been fixed
      and re-verified.
- [ ] Any security findings from 2d have been assessed —
      critical and high must be fixed before push.
- [ ] The commit history is clean (no fixup commits that
      should be squashed, no accidental files, no WIP
      messages).
- [ ] The branch is up to date with the target branch
      (rebase if needed).
- [ ] Ready to push.
