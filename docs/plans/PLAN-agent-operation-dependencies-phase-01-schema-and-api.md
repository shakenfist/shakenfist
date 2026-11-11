# Dependency-aware agent operations phase 1: schema, object and API

## Prompt

This phase gives an agent operation somewhere to record its
dependency edges, teaches the object the one new terminal state the
cascade needs, and puts the edges on the three creating endpoints
behind a capability token with submission-time validation. It writes
no evaluator: nothing in this phase reads an edge and acts on it, and
an operation created with edges in this phase executes exactly as it
does today. Phase 2 is what makes the edges mean something.

Ground every change in the tree rather than in the master plan's
prose. The pattern this phase follows almost exactly is
`PLAN-agent-operation-deadlines` phase 2, whose commit
`02cfb79b48` ("Add agent operation deadline columns") added two
static and two attribute columns to the same two tables with
additive `ADD COLUMN IF NOT EXISTS` migrations, and whose phase 3
declared the REST parameters on the same three endpoints. Read that
commit and
[its phase plan](PLAN-agent-operation-deadlines-phase-02-schema.md)
before starting: its decisions 2, 4, 7 and 8 are reused here by
reference rather than re-argued.

The files this phase touches are
`shakenfist/schema/agentoperation_data.py`,
`shakenfist/schema/agentoperation_attributes.py`, a new
`shakenfist/schema/agentoperation_dependency.py`,
`shakenfist/mariadb.py`, `protos/database.proto`,
`shakenfist/daemons/database/main.py`,
`shakenfist/operations/agentoperation.py`,
`shakenfist/external_api/base.py`,
`shakenfist/external_api/instance.py`,
`shakenfist/external_api/app.py`,
`docs/developer_guide/state_machine.md`,
`docs/operator_guide/database.md` and
`docs/developer_guide/api_reference/`.

**Planning effort:** high. The master plan does not specify a review
effort for this phase; review at high effort anyway, because the
`abort` state and the submission-time namespace check are the two
places where a wrong answer is expensive to detect.

## Scope

In:

- Two JSON static columns on `agent_operations`, `depends_on` and
  `runs_after`, and one JSON attribute column on
  `agent_operation_attributes`, `dependency_failure`. Both tables'
  versions bump and both migrations are additive.
- A typed edge model with its own permitted-target set, per decision
  5 of the master plan, and a per-edge settle delay, per decision 3
  of its "Decisions already made".
- `abort` in `AgentOperation.TERMINAL_STATES` and in
  `state_targets`, in one commit, per decision 3 of the master plan.
- The three creating endpoints gaining the two parameters, their
  declarations, a capability token, and submission-time validation
  per decision 6.
- `depends_on` and `runs_after` on `external_view()`.
- A live-MariaDB migration test, and documentation of the columns,
  the state and the parameters.

Out:

- All evaluation. No dispatcher change, no cascade, no settle-delay
  enforcement, no dependency-wait budget, no configuration option,
  and no `database_load_budget.yaml` amendment. Those are phase 2,
  and decisions 1, 2, 8, 9 and 10 of the master plan belong to it.
- `dependency_failure` on `external_view()`. The column lands here
  because this is the schema phase; the only writer is the cascade,
  so the field appears on the wire in phase 2 (decision 1 below).
- Any client change. `client-python` is phase 3.
- The developer-guide model page and the example index. Phase 4.
- Mixed-type edges with cluster operations, declined by the master
  plan's decision 4 and filed as
  [#4824](https://github.com/shakenfist/shakenfist/issues/4824).

## What the survey found

Phase 0 verified this plan's claims about the tree six weeks of
commits ago. The survey re-checked the ones phase 1 depends on. The
master plan's description of the work is substantially right; the
corrections below are to its scope boundaries and to stale line
references, and one of them moves work from phase 2 into this phase.

1. **The phase 1 prerequisite is met.** `client-python-k3s` learned
   `abort` in PR
   [#164](https://github.com/shakenfist/client-python-k3s/pull/164)
   (`539521411c`, "Learn the aborted agent operation ending"), which
   is an ancestor of both `v0.4.0` and `v0.4.1`.
   `AGENT_OP_FAILED_STATES = ('error', 'expired', 'abort')` is at
   `shakenfist_client_k3s/cluster.py:138`, and
   `exceptions.py:1275` gives an aborted operation its own
   explanation ("the operation was aborted because something it
   depended on failed"). So this repository may emit `abort`.
2. **The attributes table is already at version 4, not 3.** The
   deadlines plan's phase 2 took it to 3;
   `expiry_reason` took it to 4 afterwards. The bumps in this phase
   are therefore `agent_operations` 3 -> 4
   (`shakenfist/mariadb.py:493`) and
   `agent_operation_attributes` 4 -> 5 (`:494`). A brief written
   from the master plan's prose would have got this wrong.
3. **`BaseOperation.STATE_ABORT` already exists**
   (`shakenfist/operations/baseoperation.py:53`), and
   `AgentOperation` inherits it. No new constant is needed — only
   the `state_targets` edges and the `TERMINAL_STATES` membership,
   which is what makes decision 3 of the master plan a two-line
   change with a wedged-queue failure mode if it is split across
   two commits.
4. **The cluster-operation precedent gives no column precedent.**
   `cluster_operations` is a hand-written table whose only
   variable-shape column is `metadata_json`
   (`shakenfist/mariadb.py:2308-2343`), so a cluster operation's
   `depends_on` rides inside that blob and never has a column of
   its own. `agent_operations` is generated from its pydantic model
   by `pydantic_to_sqlalchemy_table` (`:19749`), where a `list`
   annotation becomes a JSON column (`schema/sqlalchemy.py:338-341`,
   `:382`). The precedent to follow is therefore `commands`, which
   is `list[dict]` on the model and `_json_dumps(data.commands)` at
   the direct layer (`:19944`), not the cluster `dependency` model.
5. **`VALID_CLUSTER_OPERATIONS` still has exactly one consumer**, the
   `dependency.validate_op_type` validator at
   `shakenfist/schema/operations/baseclusteroperation.py:117`, as
   phase 0 found. Nothing else reads it, so decision 5's "leave it
   alone and give the agent edge its own set" costs nothing.
6. **Several line references in the master plan and in the phase 0
   plan have drifted.** The three creating endpoints are now at
   `shakenfist/external_api/instance.py:2383`, `:2450` and `:2516`
   (phase 0 recorded `:2334`, `:2401`, `:2467`); the array-of-object
   type tokens are at `shakenfist/external_api/base.py:1038-1039`
   (recorded as `:821-829`, which is now the middle of
   `NETWORKSPEC_SCHEMA`); `Instance.agent_operation_next` is at
   `shakenfist/instance.py:3279` (recorded as `:3164`). The master
   plan's design-sketch reference is **corrected at source** by this
   commit; the phase 0 plan is a historical record and is left
   alone.
7. **Issue #4826 has been fixed since phase 0 filed it.** The
   master plan's "Bugs found during this work" reads as though the
   always-empty `agent_operations_queue` view field is outstanding.
   It landed in PR #4832 (merge `29b9a16226`): there is now an
   `Instance.agent_operations_queued()` accessor
   (`shakenfist/instance.py:3261`) which is the single reader of the
   `queue` key, and `external_view()` goes through it
   (`:844-851`). **Corrected at source** — the entry now records
   that it was fixed and where. This matters to phase 1 only as a
   warning: an instance's external view embeds each queued
   operation's full `external_view()`, so the two fields this phase
   adds to that view appear in the instance view too.
8. **A new structured parameter spec is not automatically covered by
   the documentation consistency test, and there is already one
   gap.** `shakenfist/tests/external_api/test_api_reference_specs.py`
   checks the published key list of each structured spec against the
   API reference, but `SPEC_SECTIONS` (`:60`) is a hand-written
   three-entry dict — `diskspec`, `networkspec`, `videospec`.
   `shapespec`, added to `ARGTYPES` at
   `shakenfist/external_api/base.py:1098` by
   `PLAN-placeable-capacity` phase 2, is in neither that dict nor the
   API reference: `grep -rn shapespec docs/ --include=*.md` finds only
   that plan. This phase adds its own spec to `SPEC_SECTIONS` and
   documents it (decision 7 below). The `shapespec` gap is **not
   fixed here and no issue is filed**: it belongs to
   `PLAN-placeable-capacity`, which is in flight and whose own
   documentation work is unfinished, so the durable record is a note
   against that plan rather than a duplicate issue.
9. **The Agent Operations state diagram currently matches
   `state_targets` exactly.** Every edge in the mermaid block at
   `docs/developer_guide/state_machine.md:48-84` has a counterpart in
   `AgentOperation.state_targets`
   (`shakenfist/operations/agentoperation.py:65-86`) and vice versa,
   checked edge by edge. Nothing enforces that — `check-doc-anchors`
   checks links, not diagrams — so the agreement is luck, and this
   phase is about to add a state. Decision 11 below closes it.
10. **`agent_operation_timing()` is the convention for a
    pre-creation refusal**, at `shakenfist/external_api/base.py:441`.
    It returns `(value, None)` or `(None, <400 response>)` and its
    docstring states the convention explicitly; all three endpoints
    call it before creating any object or event. The submission-time
    validation of decision 6 gets the same shape, for the same
    reason.

Nothing else in the master plan's phase 1 row was wrong.

## Decisions

1. **All three columns land in this phase; only two of them reach
   the external view.** The master plan puts "the failed dependency
   on the wire in `external_view()`" in phase 2, which reads as
   though the column arrives then too. Splitting it that way would
   give phase 2 a second table migration and a second version bump
   on a table this phase has just migrated, for one nullable column.
   Instead `dependency_failure` is added here, with nothing writing
   it, exactly as the deadlines plan's phase 2 added `last_progress`
   and `attempts` and said "nothing reads any of these values yet".
   `external_view()` gains `depends_on` and `runs_after` here,
   because a client which cannot read back what it submitted has no
   way to confirm an edge was recorded; `dependency_failure` joins
   the view in phase 2, which is the first moment it can be
   anything but null, following the `expiry_reason` precedent
   (#4075). **The master plan's phase 2 row is amended at source**
   to say the column already exists.
2. **`AgentOperation.current_version` stays at 3. No object version
   bump, no `_upgrade_step_3_to_4`.** This is decision 2 of the
   deadlines plan's phase 2, adopted by reference and for its
   reasons: a bump makes every not-yet-rolled node raise on any
   operation an already-rolled API node created, for the length of
   the rollout, and agent operations are the one object type whose
   writer and readers are reliably different processes on different
   machines. There is also nothing to migrate — an absent edge list
   is an empty list, not a gap. The fact a deployment needs is "does
   this database have the columns?", and that is the table version,
   which `sf-database` refuses to start behind.
3. **The stored shape is `list[dict]`, and the typed model is a
   validator over it.** `depends_on` and `runs_after` are
   `list[dict]` on `AgentOperationData`, mirroring `commands`, so the
   direct layer writes `_json_dumps(...)` and the gRPC layer carries
   a JSON string, both unchanged in kind from what they already do.
   Type safety comes from a pydantic `field_validator` on
   `AgentOperationData` which constructs the edge model from every
   entry and raises if it will not build, so a malformed or
   mixed-type edge cannot be stored *or read back* unnoticed. The
   alternative — `Optional[list[agent_dependency]]` on the frozen
   model — would be more idiomatic pydantic and would force a
   `model_dump` at the direct layer, a `model_validate` at the read,
   and a decision about what `AgentOperationData` means when it
   crosses gRPC as a string. The validator buys the same guarantee
   at the same boundary for less machinery.
4. **The edge model is `agent_dependency` in a new module,
   `shakenfist/schema/agentoperation_dependency.py`, with
   `VALID_DEPENDENCY_OPERATIONS = frozenset([ObjectType.AGENTOPERATION])`.**
   The cluster `dependency` model and `VALID_CLUSTER_OPERATIONS` are
   not touched. This is decision 5 of the master plan, and the point
   of it is that the edge is admitted in one direction only: a
   cluster operation still cannot name an agent operation, so the
   mixed-type edges declined by decision 4 stay out by construction
   rather than by anyone remembering. A new module rather than a
   second model in `baseclusteroperation.py`, because that file is
   the cluster vocabulary and the name would then be a lie in the
   other direction. It sits in `shakenfist/schema/` rather than in
   `shakenfist/schema/operations/`, which holds cluster-operation
   schema models exclusively, beside the agent operation's own two
   schema modules, because that is where a reader looks for one.
5. **`op_type` is on the model with a default and is not a published
   API field.** The model carries `op_type: ObjectType =
   ObjectType.AGENTOPERATION` so the persisted edge dict has the
   same shape as a cluster `dependency` — an evaluator written later
   reads `{'op_type': ..., 'op_uuid': ...}` either way — and the
   validator refuses any other value. The API object a caller sends
   is `{"op_uuid": ..., "settle_seconds": ...}` and nothing else,
   with `additionalProperties: False`. Publishing a field with
   exactly one legal value would be API noise, and a caller who
   sent the wrong one would earn a 400 for a constant.
   **This is the decision most likely to be argued with**: the
   counter-argument is that omitting `op_type` makes widening to
   #4824 a published-shape change rather than a new enum value. That
   is true, and it is the right trade, because #4824 is declined
   rather than deferred to a known date, and a field nobody may vary
   is a worse cost paid now than a parameter change paid never.
6. **`settle_seconds` is declared `minimum: 0, maximum:
   config.AGENT_OPERATION_MAX_DEADLINE`, and no new configuration
   option is introduced.** That ceiling already exists and already
   means "the longest a caller may park an instance's executor
   slot"; the declaration precedent is `deadline_seconds` at
   `shakenfist/external_api/instance.py:2314-2316`. The honest
   consequence, which phase 4 documents rather than designs around:
   once phase 2 lands its 300-second dependency-wait budget, a
   settle longer than the budget guarantees the dependent operation
   is aborted. Tying the declared maximum to the budget's config
   value instead would mean introducing a configuration option in
   the phase that cannot enforce it, which is the "documenting a
   lie" failure the deadlines plan's phase 2 step 2e named.
7. **Two type tokens, `agentdependency` and
   `arrayofagentdependency`, and the single form goes in
   `SPEC_SECTIONS`.** Only the array form is used by an endpoint, as
   is true of `diskspec`; the single form exists because
   `test_api_reference_specs.py` resolves a documented section
   through `ARGTYPES[token]['properties']` and so keys on the object
   schema, not the array. Adding both means the existing
   documentation consistency test covers the new spec with no edit
   to the test's own logic, which is the difference between a
   documented spec and survey finding 8's `shapespec`.
8. **Submission validation is one helper, and it does not disclose
   existence.** `api_base.agent_operation_dependencies(depends_on,
   runs_after, caller_namespace)` lives beside
   `agent_operation_timing()` and returns
   `((depends_on, runs_after), None)` or `(None, <400>)`. For each
   named edge it reads the operation once and refuses if it does not
   exist, is not an agent operation, or is in a namespace the caller
   cannot read — `request_namespace() not in (dep.namespace,
   'system')`, the test `requires_instance_ownership` performs at
   `shakenfist/external_api/base.py:2000`. Master plan decision 6
   says 400 for all three, and that is kept, but the *message* is
   the same for all three, so a caller cannot use the refusal to
   learn that an operation they may not see exists. The repository's
   instinct elsewhere is a 404 for exactly this reason; a 400 is
   right here because the caller sent a bad parameter rather than
   addressed a missing resource, and the non-disclosure that the 404
   was buying is bought by the shared message instead.
9. **Namespace is checked once, at submission, and never again.**
   Master plan decision 6, restated here because it is the kind of
   thing a later reviewer tries to "fix": re-checking at evaluation
   would let a namespace change break a chain already in flight.
10. **`abort` gains edges from `initial`, `preflight` and `queued`,
    and `abort -> deleted`. Not from `executing`.** Only
    `queued -> abort` is reachable by phase 2's evaluator, which runs
    on the head `agent_operation_next()` returned and so only ever
    sees a queued operation. The other two are declared for
    completeness, exactly as `expired`'s `initial` edge was and for
    the reason its comment gives. `executing -> abort` is
    deliberately absent: aborting work already running is agent-side
    cancellation, which is a non-goal of this plan, and declaring
    the edge would invite a future site to take it.
    `abort -> deleted` is not optional — `delete()` removes the
    rows and *then* assigns `deleted`
    (`shakenfist/operations/agentoperation.py:553-561`), so without
    it a user deleting an aborted operation raises
    `InvalidStateException`.
11. **A unit test compares the Agent Operations mermaid block to
    `state_targets`.** Survey finding 9 found the two agree today by
    luck. A test which parses the `## Agent Operations` section's
    `stateDiagram-v2` block and compares its edge set to
    `AgentOperation.state_targets` — mapping `[*] --> x` to the
    `None` key and `x --> [*]` to a `None` value — makes the
    documentation obligation falsifiable rather than a review item.
    It is in scope because this phase is the one adding a state, and
    it is the only new test in this phase that is not about the
    phase's own code.
12. **Dependency edges are not recorded as object references.**
    `external_view()` already carries `references_to` and
    `references_from` from the `object_references` machinery, and an
    edge is arguably a reference. It is not registered as one: the
    reference tables exist for blob and artifact reference counting
    and drive hard-delete safety, and adding an edge there would
    make a dependency keep its dependent's row alive, which is
    precisely the opposite of the sweep behaviour decision 9 of the
    master plan relies on.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | **The edge model, the three columns, both migrations and the direct layer.** Read decisions 2, 3, 4 and 5 of this plan and commit `02cfb79b48` first; that commit is the shape of this one. Create `shakenfist/schema/agentoperation_dependency.py`: a module docstring saying why this model exists separately from `dependency` in `baseclusteroperation.py` (decision 4 — the edge is admitted in one direction only, so a cluster operation cannot name an agent operation and mixed-type edges stay out by construction, see issue #4824); `VALID_DEPENDENCY_OPERATIONS: frozenset[ObjectType] = frozenset([ObjectType.AGENTOPERATION])`; and `class agent_dependency(BaseModel)` with `op_type: ObjectType = ObjectType.AGENTOPERATION`, `op_uuid: UUID4`, `settle_seconds: Optional[float] = None`. Give it a `field_validator('op_type')` refusing anything outside `VALID_DEPENDENCY_OPERATIONS` with a message naming the valid set, and a `field_serializer('op_type')` returning `str(op_type.value)` — copy both from `dependency` at `shakenfist/schema/operations/baseclusteroperation.py:113-125`, including the lower-case class name, which is that file's convention and not a mistake. Add a `settle_seconds` validator refusing a negative. In `shakenfist/schema/agentoperation_data.py`, add `depends_on: list[dict] = []` and `runs_after: list[dict] = []` after `commands` and before `deadline`, with comments stating: fate-sharing versus ordering-only, that an entry is an `agent_dependency` serialised to a dict, that `settle_seconds` is "satisfied this many seconds after the dependency completed" and that nothing in this phase evaluates either list. Extend the class docstring's Attributes block. Add one `field_validator('depends_on', 'runs_after')` which builds `agent_dependency(**entry)` for every entry and re-raises as a `ValueError` naming the index — decision 3's whole point is that this bites on the read path as well as the write path, so do not put the check in the API layer instead. In `shakenfist/schema/agentoperation_attributes.py`, add `dependency_failure: Optional[dict] = None` with a comment saying it holds the `op_type`, `op_uuid` and `state` of the failed `depends_on` edge which cascaded this operation to `abort`, that it is written once by phase 2's cascade and is NULL otherwise, and that it exists now because this is the schema phase (decision 1). In `shakenfist/mariadb.py`: bump `AGENT_OPERATIONS_VERSION` 3 -> 4 at `:493` and `AGENT_OPERATION_ATTRIBUTES_VERSION` 4 -> 5 at `:494` — `EXPECTED_SCHEMA_VERSIONS` at `:603-604` is derived and needs no edit, and `EXPECTED_TABLE_NAMES` in `shakenfist/tests/test_mariadb_schema_concurrency.py:154` needs none because no table is added; confirm both by reading rather than assuming. Add a `if current_ver < AGENT_OPERATIONS_VERSION:` block to `_ensure_agent_operations_schema` (`:19775`) after the existing deadline-columns block at `:19803-19836` and before the return, modelled on it exactly: the `LOG.info`, the `for column in (...)` loop issuing `ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column}`, the `try/except (IntegrityError, OperationalError)` with a `LOG.debug`, then `_set_table_version`. The columns are `depends_on LONGTEXT NULL` and `runs_after LONGTEXT NULL` — LONGTEXT, not JSON, because `schema/sqlalchemy.py:269-270` records that this repository stores JSON as `mysql.LONGTEXT` deliberately; read that comment and match what `create_all` produces for a `list` field, since a divergence between the migration and a fresh create is what the live test in 1f exists to catch. Do the same in `_ensure_agent_operation_attributes_schema` (`:19847`) for `dependency_failure LONGTEXT NULL`. The two edge lists are `NOT NULL DEFAULT '[]'` and `dependency_failure` is nullable, and no new column is indexed. The nullability is not a free choice: `pydantic_to_sqlalchemy_table` sets `nullable = is_optional and not is_pk` (`shakenfist/schema/sqlalchemy.py:461`), so a non-`Optional` `list[dict]` field generates `LONGTEXT NOT NULL` on a fresh create and the migration has to match it or produce exactly the divergence step 1f exists to catch. The `DEFAULT '[]'` is what existing rows get, chosen so the backfill does not depend on `sql_mode`, and is the `attempts` pattern. Then the direct layer: `_direct_create_agent_operation` (`:19928`) adds `depends_on=_json_dumps(data.depends_on), runs_after=_json_dumps(data.runs_after)` to its `insert().values()`, mirroring `commands` at `:19946` (find these by grep rather than trusting the exact line, which drifts); `_direct_get_agent_operation` (`:19963`) parses both back with the same `isinstance(x, str)` / `json.loads` dance `commands` uses at `:19985-19987` and passes `depends_on=... or []`; `_direct_create_agent_operation_attributes` (`:20032`) and `_direct_get_agent_operation_attributes` (`:20058`) do the equivalent for `dependency_failure` (JSON dict, `None` when absent, so do **not** coerce a NULL to `{}` — NULL means no cascade happened and `{}` would be a cascade with no detail); and `_agent_operation_attributes_column_values` (`:20093`) gains `'dependency_failure': data.dependency_failure` in its `all_values` dict, because that dict is the field mask's vocabulary and a field missing from it cannot be written at all. Do not touch the gRPC layer, the proto, the servicer, the object or the API — those are 1b, 1c, 1d and 1e. Commit subject: `Add agent operation dependency columns.` |
| 1b | medium | sonnet | none | **Carry the three new fields across the gRPC boundary.** In `protos/database.proto`, add to `AgentOperationStaticData` (`:2298`, fields 1-7 used) `string depends_on_json = 8;` and `string runs_after_json = 9;`, and to `AgentOperationAttributesProto` (`:2334`) an `optional string dependency_failure_json` at the next free field number. Comment them in the style of the existing `commands_json` and of the presence comment above `AgentOperationStaticData`: the two static fields are plain strings because an empty list is a legitimate value and `'[]'` says so, while `dependency_failure` uses proto3 explicit presence because absent means SQL NULL — no cascade happened — and that is different from a cascade which recorded nothing. Regenerate with `tox -e genprotos`; **never** invoke `grpc_tools.protoc` directly, and commit the regenerated `shakenfist/protos/` files in the same commit. In `shakenfist/mariadb.py`, set the new fields on the request in `_grpc_create_agent_operation` (`:20171`) and `_grpc_create_agent_operation_attributes` (`:20239`), and read them back in `_grpc_get_agent_operation` (`:20195`) and `_grpc_get_agent_operation_attributes` (`:20260`), copying the `HasField` idiom used for `expiry_reason` for the attribute field only. `_grpc_update_agent_operation_attributes` (`:20289`) already forwards `fields`, and must now also populate `dependency_failure_json` on the message it sends, because the mask names columns and the servicer reads their values off the message. Note that a proto3 `optional` field cannot be assigned `None` in Python: build the message and assign only when the value is not None. In `shakenfist/daemons/database/main.py`, extend `_agentop_from_proto` (`:4688`), `_agentop_to_proto` (`:4706`), `_agentop_attrs_from_proto` (`:4821`) and `_agentop_attrs_to_proto` (`:4838`) with the same JSON encode/decode and presence handling. Register no new metrics counters — these are existing RPCs. Read `shakenfist/tests/mock_mariadb.py`'s agent operation mocks (`_mariadb_update_agent_operation_attributes` and its neighbours) and confirm they store and mask the pydantic objects whole, so new model fields flow through with no edit; say in your report whether that held. Commit subject: `Carry dependency edges over gRPC.` |
| 1c | high | opus | none | **The object layer's static values.** In `shakenfist/operations/agentoperation.py`: give `new()` (`:167`) `depends_on=None, runs_after=None` keyword arguments after `progress_timeout`, defaulting to `None` so the three call sites in `shakenfist/external_api/instance.py` (`:2383`, `:2450`, `:2516`) are untouched by this step, and thread them into the `_db_create` metadata dict as `depends_on or []`. Extend its docstring to say what an edge is, that an edge may only name an agent operation which already exists (which is why the graph is a DAG by construction — master plan decision 6), and that nothing evaluates them yet. In `_db_create` (`:106`), pass `depends_on=metadata.get('depends_on', [])` and `runs_after=metadata.get('runs_after', [])` into the `AgentOperationData(...)` construction, and leave the initial `AgentOperationAttributesData(uuid=..., results={})` alone so `dependency_failure` takes its model default. In `_db_get` (`:134`), add both to the result dict. In `__init__` (`:86`), store them as private attributes alongside `__commands`, and add read-only `depends_on` and `runs_after` properties in the "Static values" block after `commands` (`:236`), each returning the list — note the convention recorded on `commands` at `:237-243`: static values are returned by reference and a consumer takes its own copy. **Do not change `current_version` and do not add an upgrade step**: see decision 2, and point a reviewer who expects a bump at it. Add `depends_on` and `runs_after` to `external_view()` (`:197`) from the static values; do **not** add `dependency_failure` (decision 1), and do not add a second attributes read — the attributes row is already read once into `attrs` at `:202`. Tests, in `shakenfist/tests/test_instance.py` beside `AgentOperationQueueTestCase` or in a new module if that reads better: `AgentOperation.new()` with no dependency arguments stores `[]` for both and `external_view()` reports `[]`; an operation created with one `depends_on` edge reads it back with `op_type` serialised to `'agentoperation'`; a `settle_seconds` of 30 survives the round trip; an edge naming a non-agent-operation `op_type` raises at model construction (this is the decision 3 guarantee, and the test is what proves the validator is on the model and not only in the API layer); and a negative `settle_seconds` raises. Commit subject: `Record dependency edges on an agent operation.` |
| 1d | high | opus | none | **The `abort` state, in one commit with its documentation.** Read decision 10 of this plan and decision 3 of the master plan first; the failure mode if `TERMINAL_STATES` and `state_targets` are split across two commits is a permanently wedged instance queue, so they go together. In `shakenfist/operations/agentoperation.py`: add `BaseOperation.STATE_ABORT` to `TERMINAL_STATES` (`:50`) — the constant already exists at `shakenfist/operations/baseoperation.py:53`, do not define a new one — and in `state_targets` (`:65`) add `STATE_ABORT` to the target tuples of `STATE_INITIAL`, `STATE_PREFLIGHT` and `STATE_QUEUED`, and a `BaseOperation.STATE_ABORT: (dbo.STATE_DELETED,)` entry. Do **not** add it to `STATE_EXECUTING`'s targets, and write a comment saying why not (aborting running work is this plan's agent-side-cancellation non-goal). Write a comment on the `initial` and `preflight` edges in the manner of the existing one about `initial -> expired` at `:60-64`: declared for completeness because only `queued -> abort` is reachable by the evaluator phase 2 adds, and declaring them costs nothing. Note what you are **not** adding: no `cascade_abort()` method and no writer of any kind — phase 2 owns that — so after this step `abort` is reachable only through a direct `state` assignment, which nothing performs. In `docs/developer_guide/state_machine.md`, add an `abort` bullet to the Agent Operations list after `expired` (`:34-40`), saying that it means something this operation declared a `depends_on` dependency on failed, that it is deliberately distinct from both `error` (the operation itself failed) and `expired` (a timing budget ran out), that the failed dependency is recorded on the operation, and that like `expired` it is swept for hard deletion because it is in `constants.FINAL_OBJECT_STATES`; and add the four new edges to the mermaid block (`:48-84`). Then add the test decision 11 asks for — put it in `shakenfist/tests/test_agentoperation_state_doc.py` or beside the existing agent operation tests, your call, and say which: parse the `stateDiagram-v2` block inside the `## Agent Operations` section of that file into a set of `(from, to)` pairs, build the same set from `AgentOperation.state_targets` with `[*] --> x` mapping to the `None` key and `x --> [*]` to a `None` value, and assert the two sets are equal, with a failure message naming the edges in one and not the other. The parse is the part most likely to rot, so assert the section was found and the edge set is non-empty before comparing, the way `test_api_reference_specs.test_every_spec_section_was_found` does. **Mutation-test it before you finish**: remove one edge from the diagram and confirm the test fails naming that edge, then remove one from `state_targets` and confirm the same, then restore — restore with the inverse edit or from a saved copy, never with `git checkout`. Commit subject: `Teach agent operations the abort state.` |
| 1e | high | opus | none | **The API surface.** Read decisions 5, 6, 7 and 8 of this plan first, and `shakenfist/external_api/instance.py:2298-2400` (the put endpoint) in full as the pattern for all three. In `shakenfist/external_api/base.py`: add `AGENTDEPENDENCYSPEC_SCHEMA` beside `VIDEOSPEC_SCHEMA` (`:831`) with `'type': 'object'`, `'additionalProperties': False`, `'required': ['op_uuid']`, and two properties — `op_uuid` (`'type': 'string'`, `'format': 'uuid'` if that is the token the `_FORMATS` table in `validation.py:432` publishes for a UUID; read it rather than guessing) and `settle_seconds` (`'type': 'number'`, `'minimum': 0`, `'maximum': config.AGENT_OPERATION_MAX_DEADLINE`), each with a description. The schema deliberately does not publish `op_type`: say so in a comment, with decision 5's reasoning in one sentence. Add `'agentdependency': AGENTDEPENDENCYSPEC_SCHEMA` beside `'videospec'` (`:1112`) and `'arrayofagentdependency': {'type': 'array', 'items': AGENTDEPENDENCYSPEC_SCHEMA}` in the array block (`:1038-1039`), with a comment noting the single form exists for the documentation test rather than for an endpoint (decision 7). Add the validation helper beside `agent_operation_timing()` (`:441`), following its return convention exactly and its docstring style: for each entry of each list, read the named operation once with `AgentOperation.from_db()`, and return a 400 if it is absent, if its `object_type` is not `AGENTOPERATION`, or if `request_namespace() not in (dep.namespace, 'system')` — **the same message for all three** (decision 8), something like `depends_on[1]: no such agent operation`. On success return the two lists with each entry normalised to the stored dict shape, by constructing `agent_dependency(**entry)` and `model_dump()`ing it, so `op_type` is filled in and the handler never writes a caller's raw dict. Then the three endpoints — put (`:2298`), get (`:2401`), execute (`:2462`) — each gain `('depends_on', 'body', 'arrayofagentdependency', <description>, False)` and the same for `runs_after` in their `swagger_helper()` parameter lists, the two kwargs on the `post()` signature, and a call to the new helper placed immediately after the `agent_operation_timing()` call and before anything is created, passing the result into `AgentOperation.new()`. Extend each endpoint's 400 response description to mention an invalid dependency. All three take both parameters: unlike `progress_timeout_seconds`, there is no endpoint for which an edge is meaningless. Add one capability token `'agentoperation-dependencies'` to the `instances` family in `shakenfist/external_api/app.py` (`:441`, beside `'agentoperation-deadlines'` at `:508-516`) with a comment in that token's style: it names the two parameters on all three endpoints, and it is also what makes the new `abort` terminal state safe, because an operation can only reach `abort` if a client which declared a dependency created it. Add the token to `shakenfist/tests/external_api/test_root.py:66`'s expectations, and entries for the new structured parameter to `STRUCTURED_PARAMETERS` in `shakenfist/tests/external_api/test_openapi_spec.py:109` — one per endpoint and parameter, and read `test_every_published_structure_or_bound_is_registered()` to see how many it wants. Tests go in `shakenfist/tests/external_api/test_agent_operation_parameters.py`, whose fixture already stands up the three endpoints with a ready agent: an edge naming an existing operation in the caller's namespace is stored with `op_type` filled in; an edge naming a UUID which does not exist is a 400 and creates nothing (assert no `AgentOperation` was created, as the timing tests do); an edge naming an operation in another namespace is a 400 **with the same message** as the missing case, asserted literally equal, because that equality is the non-disclosure guarantee; a `system` caller may name an operation in another namespace; an undeclared key inside an edge object is a 400 from the nested schema; a `settle_seconds` above the ceiling is a 400; both lists omitted stores `[]` for both; and `runs_after` works on all three endpoints. Commit subject: `Accept dependency edges on agent operations.` |
| 1f | high | opus | none | **The live migration test.** Add the new columns to `shakenfist/tests/test_mariadb_agent_operations_live.py`, which the deadlines plan's phase 2 wrote for exactly this purpose — read it first, including its `@unittest.skipUnless(os.environ.get('SF_MARIADB_TEST_DSN'), ...)` gate and its DESTRUCTIVE docstring. Extend it, do not write a new module: the rewind-and-replay shape is already there. The test must build the current schema via the public `ensure_schema()` path, rewind to a pre-phase-1 deployment (`ALTER TABLE ... DROP COLUMN` for `depends_on`, `runs_after` and `dependency_failure`, and `_set_table_version` to 3 and 4 respectively), insert a row into each table using only the pre-phase-1 columns, run `ensure_schema()` again, and assert: `agent_operations` reports version 4 and `agent_operation_attributes` version 5; all three columns exist in `information_schema.columns`; **each new column's type matches what `create_all` produces on a fresh database** — query `information_schema.columns` for both the migrated table and a freshly created one and compare, because a migration which adds `JSON` where `create_all` adds `LONGTEXT` is the divergence the live test exists to find and nothing else in the repository can see it; the pre-existing `agent_operations` row reads back `[]` for both new columns, which is what their `DEFAULT '[]'` backfills and is the assertion that would catch a migration adding them without it; both are `NOT NULL`, matching a fresh create; the pre-existing attributes row reads back NULL for `dependency_failure`; a JSON edge list written through `mariadb.create_agent_operation()` reads back equal through `get_agent_operation()`; and a third `ensure_schema()` is a clean no-op leaving the versions where they are. **Mutation-test every assertion before you are done**: drop one `ADD COLUMN` from the migration, then leave one version un-bumped, then change one column's type to `JSON`, and confirm the test fails for the right reason each time — then restore with the inverse edit, not `git checkout`. A live test which passes against a broken migration is worse than no test. Run it the way CI does: `SF_MARIADB_TEST_DSN=... stestr run --serial 'shakenfist\.tests\.test_mariadb_agent_operations_live\.'` against a disposable MariaDB (`tools/ci-install-mariadb.sh` with `tools/bootstrap-mariadb.sql` stands one up). Confirm by reading that `tools/ci-enum-widening-test.sh` already matches any `test_mariadb_*_live.py` module, so no workflow edit is needed, and say so in your report. Commit subject: `Test the dependency column migration.` |
| 1g | medium | sonnet | none | **Documentation of what landed, and nothing more.** In `docs/operator_guide/database.md`, extend the `agent_operations` row (`:1396`) to list `depends_on (JSON list)`, `runs_after (JSON list)` and the `agent_operation_attributes` row (`:1419`) to list `dependency_failure (nullable)`, matching the terse column-list style of the surrounding rows. Add a short paragraph in that section's prose stating once: an entry in either list is `{"op_type": "agentoperation", "op_uuid": "...", "settle_seconds": N}`; `depends_on` is fate-sharing and `runs_after` is ordering-only; an edge can only name an operation which already existed when this one was created, so the graph is a DAG by construction; and `dependency_failure` is NULL unless a `depends_on` dependency failed and cascaded this operation to `abort`. That last fact is the one most likely to be restated wrongly later, so it wants exactly one home. In `docs/developer_guide/api_reference/instances.md`, add a `### agentdependency` section in the style of `### videospec` (`:195`) documenting both keys with the `* key (type, required): prose` bullet shape `test_api_reference_specs.py`'s `_BULLET` regex requires, and add `'agentdependency': 'agentdependency'` to `SPEC_SECTIONS` in `shakenfist/tests/external_api/test_api_reference_specs.py:60` — the test then covers the new spec with no change to its logic (decision 7). Document the two new body parameters on the three agent endpoints in the same file, beside `deadline_seconds`. In `docs/developer_guide/api_reference/agentoperations.md`, add `depends_on` and `runs_after` to the example response body so it matches what `external_view()` now returns, and do **not** add `dependency_failure`, which the view does not carry until phase 2. Do not document the dependency-wait budget, the settle-delay enforcement, the cascade, or the `abort` state's cause beyond the state-machine bullet 1d wrote: none of it exists yet, and documenting it now would be documenting a lie. `AGENTS.md` and `ARCHITECTURE.md` are not touched — no convention changes and no component is added or moved. Commit subject: `Document agent operation dependency columns.` |

Each step is its own commit. 1a and 1b both edit `shakenfist/mariadb.py`
and go in that order; 1c depends on both; 1d is independent of 1a-1c and
could go first, but goes here so the `abort` commit lands after the
columns it will eventually be explained by; 1e depends on 1a and 1c; 1f
depends on 1a only; 1g last, so it describes what actually landed.

## Risks and mitigations

- **The `abort` state wedges an instance queue if the commit is
  split.** `agent_operation_next()` breaks out of its loop on a state
  it has not been taught, leaving the entry at the head forever
  (`shakenfist/instance.py:3406-3415`). A commit which adds the
  `state_targets` edge without the `TERMINAL_STATES` membership is
  that bug. *Mitigation:* step 1d does both in one commit and the
  brief says so; the management session checks the diff of that
  commit specifically, before running anything.
- **A migration which adds a different column type from what a fresh
  `create_all` produces.** `schema/sqlalchemy.py` maps a `list` field
  to `mysql.LONGTEXT` deliberately, so an `ADD COLUMN ... JSON` would
  leave a migrated cluster and a fresh cluster with different schemas
  and nothing in the unit suite could tell: it mocks MariaDB
  completely. *Mitigation:* step 1f asserts the migrated column type
  equals the freshly created one, and mutation-tests that assertion
  by deliberately introducing the divergence.
- **The validator in decision 3 runs on every read.** Every
  `_direct_get_agent_operation` constructs an `AgentOperationData`,
  which now builds an `agent_dependency` per edge. For an operation
  with no edges that is two empty-list iterations, which is free; for
  one with edges it is a handful of pydantic constructions on a path
  the sidechannel dispatcher polls per ready instance every five
  seconds. *Mitigation:* the lists are expected to hold one or two
  entries, and the dispatcher reads an operation it was already
  reading. If a reviewer thinks this is material, the measurement to
  ask for is phase 2's, which is where the per-pass read cost is
  budgeted (`shakenfist/data/database_load_budget.yaml`); flag it
  rather than optimising blind.
- **A client sends an edge against a server which has the parameter
  but no evaluator.** Between this phase and phase 2 landing, a
  declared dependency is accepted, stored, visible on the view, and
  silently ignored at dispatch — the operation runs in queue order
  as it does today. *Mitigation:* the capability token is the signal,
  and it is added in this phase, so a client which checks it will
  believe dependencies work. That is the real exposure, and the
  mitigation is that both phases land in the same release cycle and
  the token's own comment says what it promises. If the two phases
  look likely to be separated by a release, the token should move to
  phase 2 instead; that is a decision for the operator at the back
  brief, not for the implementer.
- **A namespace check written against the wrong direction.** The
  idiom is `request_namespace() not in (dep.namespace, 'system')`,
  which admits a caller in the dependency's namespace *and* a caller
  in `system`. Inverting it would let any caller name any operation,
  or lock `system` out. *Mitigation:* step 1e's test list includes
  both a cross-namespace refusal and a `system` caller succeeding,
  which fail in opposite directions if the test is inverted.

## Definition of done

Each item is checkable; the command is given where there is one.

- `shakenfist/schema/agentoperation_dependency.py` exists
  and defines `VALID_DEPENDENCY_OPERATIONS` containing exactly
  `ObjectType.AGENTOPERATION`. Check: `grep -n
  'VALID_DEPENDENCY_OPERATIONS' -r shakenfist/` shows the definition
  and its validator and nothing else.
- `VALID_CLUSTER_OPERATIONS` is unchanged. Check: `git diff develop
  -- shakenfist/schema/operations/baseclusteroperation.py` is empty.
- `AGENT_OPERATIONS_VERSION` is 4 and
  `AGENT_OPERATION_ATTRIBUTES_VERSION` is 5, and
  `AgentOperation.current_version` is still 3. Check: `grep -n
  'AGENT_OPERATIONS_VERSION =\|AGENT_OPERATION_ATTRIBUTES_VERSION ='
  shakenfist/mariadb.py` and `grep -n 'current_version'
  shakenfist/operations/agentoperation.py`.
- `AgentOperation.TERMINAL_STATES` contains `abort`, and
  `state_targets` has `abort` as a target of `initial`, `preflight`
  and `queued`, an `abort -> deleted` entry, and **no**
  `executing -> abort` edge.
- The Agent Operations state diagram in
  `docs/developer_guide/state_machine.md` and
  `AgentOperation.state_targets` describe the same edge set, asserted
  by a test rather than by eye, and that test has been mutation
  tested in both directions (an edge removed from the diagram, and an
  edge removed from `state_targets`) with the failures recorded in
  this plan's execution notes.
- The three creating endpoints each declare `depends_on` and
  `runs_after`. Check: `grep -n 'arrayofagentdependency'
  shakenfist/external_api/instance.py` returns six lines.
- `agentoperation-dependencies` is advertised and asserted. Check:
  `grep -rn 'agentoperation-dependencies' shakenfist/external_api/app.py
  shakenfist/tests/external_api/test_root.py` returns at least one
  line from each.
- A request naming a non-existent dependency and a request naming a
  dependency in a namespace the caller cannot read are refused with
  **byte-identical** messages, asserted by a test using
  `assertEqual` on the two response bodies rather than two separate
  message assertions.
- A refused request creates nothing: the tests for both refusal cases
  assert no `AgentOperation` was created, in the manner of
  `test_a_negative_deadline_is_refused_and_creates_nothing`.
- `external_view()` carries `depends_on` and `runs_after` and does
  **not** carry `dependency_failure`. Check: `grep -n
  'dependency_failure' shakenfist/operations/agentoperation.py`
  returns nothing.
- The live migration test asserts that each new column's type on a
  migrated database equals its type on a freshly created one, and
  that assertion has been mutation tested by changing one column's
  migration type, with the result recorded in the execution notes.
- The live test passes against a real MariaDB, and the command and
  its output are recorded in the execution notes. A skipped live
  test is not a pass.
- `docs/developer_guide/api_reference/instances.md` has an
  `### agentdependency` section whose key list the existing
  `test_api_reference_specs.py` compares against `ARGTYPES`, because
  `agentdependency` has been added to its `SPEC_SECTIONS`.
- No fact about an edge is stated differently on two pages: the edge
  dict's shape, the `depends_on`-versus-`runs_after` distinction and
  the DAG-by-construction claim each appear in exactly one of
  `docs/operator_guide/database.md` and
  `docs/developer_guide/api_reference/instances.md`, with the other
  linking to it if it needs it.
- Nothing in this phase documents the cascade, the settle delay's
  enforcement, the dependency-wait budget or a configuration option.
  Check: `grep -rni 'dependency.wait\|settle.*budget'
  docs/operator_guide/ docs/developer_guide/` returns nothing outside
  `docs/plans/`.
- The master plan's phase 2 row says the `dependency_failure` column
  already exists, its design-sketch line references are current, and
  its #4826 entry records that the bug was fixed (all three done in
  this phase's planning commit, see survey findings 6, 7 and
  decision 1).
- `pre-commit run --all-files` passes.

## Back brief

Before step 1a runs, back brief the operator on the understanding of
this plan. Two items are gates: do not proceed on either without
explicit agreement.

- **Decision 5, that `op_type` is not a published API field.** It is
  the decision a reviewer is most likely to disagree with, and
  changing it later is a published-shape change rather than an
  internal one.
- **The capability token's timing**, raised under Risks: the token
  lands in this phase and promises a behaviour phase 2 delivers. If
  phase 1 and phase 2 might be separated by a release, the token
  belongs in phase 2 and this phase's endpoints accept the
  parameters undeclared-by-capability, which is a different and
  worse shape. The operator decides.

Surface as information rather than as questions: decision 1, which
moves the `dependency_failure` column from phase 2 into this phase
and amends the master plan to say so; decision 11, which adds a test
this phase did not strictly need; and survey finding 8's `shapespec`
documentation gap, which belongs to `PLAN-placeable-capacity` and is
recorded there rather than fixed or filed here.

## Execution notes

- **2026-11-11 (AEDT). The back brief was answered, all three gates as
  the plan proposed.** Decision 5 stands: the edge object a caller
  sends carries no `op_type`, and widening to #4824 is accepted as a
  published-shape change. The `agentoperation-dependencies` capability
  token stays in phase 1 rather than moving to phase 2, on the
  understanding that both phases land in the same release cycle. This
  session may push the branch and open the pull request once the
  definition of done passes.
- **Departure, taken before step 1a ran: the edge model's module moved
  from `shakenfist/schema/operations/` to `shakenfist/schema/`.**
  `schema/operations/` holds cluster-operation schema models and
  nothing else, while the agent operation's own `agentoperation_data.py`
  and `agentoperation_attributes.py` sit one level up, so a reader
  looking for an agent operation schema would look in the wrong place
  under the original path. Corrected at source in decision 4, in the
  file list in the prompt, in step 1a's brief and in the definition of
  done.
- **Two survey premises re-checked against the tree before 1a was
  briefed.** A `list` annotation maps to `mysql.LONGTEXT()`
  (`shakenfist/schema/sqlalchemy.py:391`), so the migration's
  `LONGTEXT` is what `create_all` produces; and the comment at
  `:265-283` independently records the same migrated-versus-fresh
  divergence hazard that step 1f's column-type equality assertion
  exists to catch, which is worth citing in that step's commit.
  `_direct_create_agent_operation`'s JSON dump is at `mariadb.py:19946`
  rather than the `:19944` first written.
- **2026-11-11 (AEDT). Step 1a found step 1a's own brief wrong in three
  places, all three corrected at source.** First, the brief said to add
  a second `if current_ver < AGENT_OPERATIONS_VERSION:` block; that
  would be dead code, because the existing block already sets
  `current_ver` to the target, and the attributes function already
  carries the single-block pattern with a comment explaining it, added
  by the `expiry_reason` work. The brief also described a
  `try/except (IntegrityError, OperationalError)` which no longer
  exists -- a failing `ALTER` is now deliberately allowed to propagate.
  Both errors came from briefing off `02cfb79b48`'s historical diff
  rather than the current function. Second, the brief said
  `depends_on LONGTEXT NULL`, which contradicts its own instruction to
  match `create_all`: a non-`Optional` `list` field generates
  `LONGTEXT NOT NULL`, so the columns are `NOT NULL DEFAULT '[]'`.
  Step 1f's expectations are corrected with it. Third, writing
  `dependency_failure` raw would have put a dict in a LONGTEXT column,
  so it is `_json_dumps`ed when set and NULL when not.
- **The `flake8` pre-commit hook does not see uncommitted work.**
  `tools/flake8wrap.sh` lints `git diff --name-only HEAD~1`, so the
  "Style check with flake8" hook reports on the previous commit and
  passes vacuously for work in the working tree. Every step from here
  runs `flake8 --max-line-length=120` by hand over the files it
  touched, and its brief says so. Step 1a's five files were checked
  that way in the management session and are clean.
- **2026-11-11 (AEDT). Step 1b found pre-existing drift in the
  generated gRPC stubs, and it is regenerated in a commit of its
  own.** Five of the six `*_pb2_grpc.py` files carried
  `GRPC_GENERATED_VERSION = '1.84.0'` while `pyproject.toml` pins
  `grpcio-tools==1.84.1` -- and says in a comment that the pin exists
  so generated stubs match the runtime pins -- and
  `database_pb2_grpc.py` was already at 1.84.1 because the last
  person to touch the proto regenerated it. Nothing checks this:
  there is no proto-staleness job in CI and no pre-commit hook for
  it. The regeneration is therefore a real fix rather than churn, but
  it is unrelated to dependency edges, so it lands as its own commit
  ahead of the edge work.
- **An unset proto3 string reads back as the empty string, which step
  1b guarded without being asked to.** `depends_on_json` and
  `runs_after_json` are plain string fields rather than optional ones,
  because the columns are `NOT NULL DEFAULT '[]'` and `'[]'` is an
  unambiguous "no edges". A message from a server which predates the
  fields carries `''`, so both readers test the string before
  `json.loads` and fall back to `[]`. Verified in the management
  session by serialising and re-parsing: absent `dependency_failure`
  round-trips as NULL, an empty dict round-trips as `{}`, and the two
  stay distinct, which is the distinction phase 2's cascade depends
  on.
- **2026-11-11 (AEDT). Step 1d's `abort` state has one wire-visible
  consequence beyond the state machine, and two stale enumerations were
  corrected with it.** Adding a member to `TERMINAL_STATES` changes
  every reader of that tuple, and all five were checked. `expire()` and
  `fail()` gain an aborted operation as another state they no-op from,
  and `agent_operation_next()` retires an aborted head, which are both
  the point. `Instance.external_view()` skips terminal operations when
  it renders `agent_operations_queue`, so an aborted operation drops
  off that list -- correct, since the field reports what has not
  finished, and newly observable because that field read the wrong
  attribute key and was empty for every instance until #4826 was fixed.
  `_abort_commands_if_terminal()` in the sidechannel tests `error` and
  `expired` by name rather than the tuple, so its behaviour genuinely
  does not change; its docstring reasons about which members of the
  tuple it omits, and that enumeration plus the one in
  `agent_operation_next()`'s terminal branch would both have gone stale,
  so both gained a clause. Neither is a logic change.
- **The diagram consistency test was mutation tested in the management
  session as well as by the step.** Removing `queued -> abort` from
  `state_targets` fails
  `test_the_diagram_is_the_state_machine` with "Only in state_targets:
  none. Only in the diagram: queued --> abort.", and the tree was
  restored from a copy rather than with `git checkout`, with the test
  green again afterwards.
- **The `abort` documentation was tightened in review.** The step's
  bullet said the failed dependency "is recorded on the operation",
  which promises something a developer cannot see: the column exists
  but nothing writes it and it is not on the external view until phase
  2. It now says plainly that nothing moves an operation into the state
  yet. The adjacent `expired` bullet's "only `expired` is currently
  swept for hard deletion" was reworded to "of the two", because beside
  a new bullet which says an aborted operation is swept too it read as
  a contradiction.
