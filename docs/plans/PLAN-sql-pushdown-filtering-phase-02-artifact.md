# Phase 2 — Artifact pushdown

Master plan: [PLAN-sql-pushdown-filtering.md](PLAN-sql-pushdown-filtering.md).
Phase 1: [PLAN-sql-pushdown-filtering-phase-01-infrastructure.md](PLAN-sql-pushdown-filtering-phase-01-infrastructure.md).

Planning effort: **medium** (sonnet). The primitive from
phase 1 already exists and is tested; this phase is a
targeted wiring change.

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly with
particular attention to `shakenfist/baseobject.py` (the
generic `from_db_by_ref`, `namespace_filter`, `state_filter`,
and the `ACTIVE_STATES` conventions), `shakenfist/artifact.py`
(the Artifact class, its `filter()` override, and its
`ACTIVE_STATES`), and `shakenfist/mariadb.py` (the
`find_artifacts` public wrapper added in phase 1). Ground any
claim in what the code does today. Flag uncertainty
explicitly.

## Goal

Replace the Python-side scan in `Artifact.from_db_by_ref`
with a single call to `mariadb.find_artifacts(criteria)` so
that every name-based REST lookup
(`shakenfist/external_api/artifact.py:62`) executes exactly
one indexed SQL query that filters on state, namespace, and
name simultaneously.

Non-goals for this phase:

* Changing `Instance.from_db_by_ref` or
  `Network.from_db_by_ref` — that is phase 3.
* Changing `Artifact.filter()` or the `Artifacts` iterator —
  phase 4 handles iterators.
* Deleting `Artifact.filter()`. The master plan keeps the
  predicate API as a documented fallback.
* Ad-hoc bulk scans elsewhere in `artifact.py` — phase 5.

## Design

### Override `Artifact.from_db_by_ref`

Add a classmethod on `Artifact` that supersedes the base
`DatabaseBackedObject.from_db_by_ref`:

```python
@classmethod
def from_db_by_ref(
        cls, object_ref: Union[str, uuid_mod.UUID],
        namespace: Optional[str] = None) -> 'Artifact | None':
    """Look up an artifact by UUID or by name within a namespace.

    UUID lookups short-circuit to from_db. Name lookups push
    state + namespace + name down to a single indexed SQL
    query via mariadb.find_artifacts.
    """
    if object_ref and util_general.valid_uuid4(object_ref):
        return cls.from_db(object_ref)

    # namespace='system' or namespace=None means "look across
    # all namespaces" — preserve that by omitting the namespace
    # filter. Matches baseobject.namespace_filter semantics.
    criteria_namespace = (
        namespace if namespace and namespace != 'system' else None)

    criteria = ObjectFilterCriteria(
        states=list(cls.ACTIVE_STATES),
        namespace=criteria_namespace,
        name=object_ref,
    )
    matches = mariadb.find_artifacts(criteria)

    if not matches:
        return None
    if len(matches) > 1:
        raise exceptions.MultipleObjects(
            f'multiple artifacts have the name "{object_ref}"'
            f' in namespace "{namespace}"')
    return cls(matches[0])
```

Rationale:

* UUID short-circuit preserves identical behaviour to the
  base class for the UUID path.
* `states=list(ACTIVE_STATES)` — `ObjectFilterCriteria.states`
  is typed `list[str]`, and `ACTIVE_STATES` on `Artifact` is
  a set of string constants. The cast is deterministic.
* Namespace handling mirrors `baseobject.namespace_filter`:
  when `namespace == 'system'`, the filter returns `True`
  for all objects (i.e. no namespace constraint). Pushing
  `WHERE namespace = 'system'` would be incorrect — we want
  "no WHERE clause on namespace". The ternary collapses
  `None` and `'system'` to `None` on the criteria, which the
  phase-1 primitive translates to "no namespace filter".
* `MultipleObjects` behaviour is preserved. The base class
  iterates all matches and raises if it saw two; we get the
  full match list from SQL and count. The error message
  format matches the base class.
* The reply is hydrated by `Artifact(data)` because the
  existing constructor already accepts `ArtifactData`
  Pydantic models (see `shakenfist/artifact.py:62-81`).

### Do not delete `Artifact.filter()`

Per the master plan, `Artifact.filter()` stays as a
documented fallback for callers that pass arbitrary Python
predicates. No production call site uses it after this phase
(verified by step 2a) but keeping it:

* Keeps behaviour parity with `Instance.filter()` and
  `Network.filter()` until their respective phases.
* Gives future callers (debug tools, one-off scripts) an
  escape hatch for predicate-based filtering.

### No schema or index changes

Phase 1 already added the `idx_<table>_name` index and the
JOIN query. The phase-1 primitive already emits SQL of the
form:

```sql
SELECT a.*
  FROM artifacts a
  JOIN object_states s
    ON s.object_uuid = a.uuid
   AND s.object_type = 'ARTIFACT'
 WHERE s.state_value IN ('initial', 'created', 'error')
   AND a.namespace = :namespace      -- when non-None
   AND a.name      = :name           -- when non-None
```

MariaDB will use `idx_object_states_type_state` on the join
and the `name` / `namespace` indexes on the WHERE clauses,
yielding an index-only plan in the common case.

## Steps

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 2a   | medium | sonnet | none      | Add the `from_db_by_ref` override to `Artifact` in `shakenfist/artifact.py`, per design above. Import `ObjectFilterCriteria` from `shakenfist.schema.object_filter` alongside existing schema imports. Do NOT remove `Artifact.filter()`. Before editing, confirm (by grep) that the only in-tree call site of `Artifact.filter()` is `DatabaseBackedObject.from_db_by_ref` in `baseobject.py` — report the grep result in the back brief. If any other caller exists, surface it before making the change. |
| 2b   | medium | sonnet | none      | Unit tests in a new `shakenfist/tests/test_artifact_from_db_by_ref.py`. Mock `mariadb.find_artifacts` (not the underlying engine — we're testing the override, not the SQL). Cover: (1) UUID input short-circuits to `cls.from_db` and does NOT call `find_artifacts`; (2) non-UUID name with specific namespace calls `find_artifacts` with `states=ACTIVE_STATES`, `namespace=<that>`, `name=<ref>`; (3) non-UUID name with namespace=`'system'` calls `find_artifacts` with `namespace=None`; (4) non-UUID name with namespace=`None` calls `find_artifacts` with `namespace=None`; (5) zero matches returns `None`; (6) one match returns an `Artifact` instance built from the returned `ArtifactData`; (7) two matches raises `MultipleObjects` with the expected message. Match test style of existing shakenfist/tests/test_*.py files. |
| 2c   | medium | sonnet | none      | Functional test in `shakenfist/deploy/cluster_ci/test_artifacts.py` (or the closest existing file — confirm by inspection). Create two artifacts with the same name in *different* namespaces, confirm each namespace resolves its own by name. Then create two artifacts with the same name in the *same* namespace (via the admin path that bypasses the REST uniqueness check, if there is one — otherwise skip this half with a note). Confirm the double-match case returns the expected API error. Keep the test under 60 seconds of wall time. |
| 2d   | low    | haiku  | none      | Run `pre-commit run --all-files` at the repo root. If flake8 / mypy / stestr report anything, fix and rerun. Do not commit. |

## Back brief

Before executing any step, the sub-agent must back brief
the operator with:

* Files it intends to change and the specific functions
  it will touch.
* Confirmation of the `Artifact.filter` call-site grep
  from step 2a.
* Any design decision not explicit in this plan.

## Management session review checklist

After each step:

- [ ] Files changed match the brief. No unrelated edits.
- [ ] `Artifact.from_db_by_ref` override lives adjacent to
      `from_db` for readability.
- [ ] `Artifact.filter()` is unchanged.
- [ ] `pre-commit run --all-files` passes (flake8, stestr,
      mypy).
- [ ] The new unit tests mock `mariadb.find_artifacts`
      rather than `_get_engine`, because this phase is
      about wiring, not SQL.
- [ ] Functional test confirms end-to-end behaviour.
- [ ] Commit message follows project conventions,
      including the Co-Authored-By line with model,
      context window, effort level.

## Success criteria for phase 2

* `Artifact.from_db_by_ref` is overridden in
  `shakenfist/artifact.py`.
* A name lookup makes exactly one call to
  `mariadb.find_artifacts` (verified via mocked
  assertion in step 2b).
* UUID lookups continue to use `from_db` (no regression).
* `namespace='system'` and `namespace=None` behave
  identically for name lookups (no namespace filter).
* `MultipleObjects` is raised when the name is ambiguous
  within the scope.
* Pre-commit clean.
* The existing `cluster_ci` artifact tests that were
  timing out because of the pre-phase-1 bug still pass (a
  sanity check that we haven't regressed the fix that
  shipped as `915de7e7`).

## Open questions for this phase

1. **Functional test coverage (step 2c).** Can an admin
   API path create two artifacts with the same name in the
   same namespace, or does the creation path already de-dup
   on `(namespace, name)`? If the latter, the
   MultipleObjects half of step 2c is purely theoretical
   and can be dropped with a note in the test file. The
   step-2c sub-agent should check this by reading
   `shakenfist/external_api/artifact.py` and the MariaDB
   unique-constraint definition on the `artifacts` table;
   report findings in the back brief before writing the
   test.

2. **Return type annotation.** The existing base-class
   `from_db_by_ref` returns `Artifact | None` for Artifact's
   call. Should we tighten the override's annotation to
   match? Leaning yes — match the type narrowing the
   concrete override gives us. Worth a 30-second check of
   mypy coverage on this symbol before writing the code.
