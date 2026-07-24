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
  in `shakenfist/deploy/cluster_ci`? Shakenfist prefers
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

- `README.md` reflects any new features, changed usage, or
  updated project structure.
- `ARCHITECTURE.md` reflects any new or modified modules,
  daemons, object types, or gRPC services.
- `AGENTS.md` reflects any new dependencies, build
  commands, or conventions.
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
